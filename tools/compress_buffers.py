"""One-time / per-batch converter: depth_buffer + normal_buffer PFM (uncompressed 32-bit
float, ~8 MB / ~24 MB per frame) -> compressed .npz (float16 by default).

Rendered surfaces are mostly flat, so depth/normal compress enormously: a 100-model run's
~490 GB of PFM shrinks to ~5 GB (fp16) / ~21 GB (fp32, lossless). fp16 precision loss is
tiny: normals max ~2e-4 (unit vectors), depth sub-cm in the defect range (<1500 cm; the only
larger error is at the far background, which is irrelevant).

Output: depth_buffer/<name>.npz with key 'depth', normal_buffer/<name>.npz with key 'normal'
(np.load(path)['depth'] -> HxW or H*W float array; shape preserved). The original .pfm is
deleted only after the .npz is written AND read back & shape-verified (unless --keep-pfm).
Idempotent: a frame whose .npz already exists is skipped, so it is safe to re-run (e.g. once
mid-way and again after the batch finishes).

Run OFFLINE in WSL (needs numpy); do NOT run concurrently with an active render on the same
disk (I/O contention slows the run). Example:
    python tools/compress_buffers.py /path/to/runs/component/<ts> --dtype fp16
    python tools/compress_buffers.py /path/to/runs/component/<ts> --verify-only   # dry stats
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np


def read_pfm(path):
    """Read a .pfm (PF=3ch, Pf=1ch) -> (float32 ndarray HxWxC or HxW, (h,w,c))."""
    with open(path, "rb") as f:
        header = f.readline().strip()
        if header not in (b"PF", b"Pf"):
            raise ValueError("not a PFM: %s" % path)
        ch = 3 if header == b"PF" else 1
        dims = f.readline().split()
        w, h = int(dims[0]), int(dims[1])
        scale = float(f.readline())
        data = np.frombuffer(f.read(w * h * ch * 4), dtype="<f4" if scale < 0 else ">f4")
    # The C# writer emits scanlines top-to-bottom (y=0..height, y=0=top), so a plain reshape
    # already matches the image/label rasters and the existing PFM loader (verified by
    # image-vs-depth edge correlation 2026-06-25: as-is=0.32 vs flipud=-0.06). Do NOT flip.
    return np.array(data, dtype=np.float32).reshape(h, w, ch) if ch == 3 else np.array(data, dtype=np.float32).reshape(h, w)


def _max_finite_err(a32, a_lo):
    """Max abs error of the low-precision round-trip over finite, in-defect-range values."""
    a = a32[np.isfinite(a32)]
    if a.size == 0:
        return 0.0, 0.0
    rt = a_lo[np.isfinite(a32)].astype(np.float32)
    err = np.abs(a - rt)
    # defect-range mask for depth-like magnitudes (ignore far background > 1500)
    rng = a[(np.abs(a) > 0) & (np.abs(a) < 1500.0)]
    rng_rt = rt[(np.abs(a) > 0) & (np.abs(a) < 1500.0)]
    rng_err = float(np.abs(rng - rng_rt).max()) if rng.size else 0.0
    return float(err.max()), rng_err


def _file_kind(path):
    """Peek the first bytes: 'pfm' = raw PFM data (Pf/PF header), 'npz' = real (zip) .npz,
    'other' = anything else. The plugin writes raw PFM but NAMES the file .npz, so a .npz
    on disk may actually be uncompressed PFM — this lets us detect and compress it."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
    except Exception:
        return "other"
    if head in (b"Pf", b"PF"):
        return "pfm"
    if head == b"PK":  # zip local-file-header magic => a real numpy .npz
        return "npz"
    return "other"


def convert_dir(run_dir, dtype="fp16", verify_only=False, keep_pfm=False):
    totals = {"pfm_bytes": 0, "npz_bytes": 0, "n": 0, "skipped": 0, "max_err": 0.0, "max_range_err": 0.0, "failed": 0}
    # Recurse so flat run dirs (depth_buffer/normal_buffer/*) AND nested datasets both work.
    # Match BOTH *.pfm and *.npz: the render plugin writes uncompressed PFM into files it NAMES
    # .npz, so we route by CONTENT (header), not extension. PFM channel count -> key:
    # 3-channel (PF) = normal, 1-channel (Pf) = depth.
    candidates = sorted(
        glob.glob(os.path.join(run_dir, "**", "*.pfm"), recursive=True)
        + glob.glob(os.path.join(run_dir, "**", "*.npz"), recursive=True)
    )
    for src in candidates:
        kind = _file_kind(src)
        if kind == "npz":
            # already a real compressed .npz -> nothing to do (idempotent, safe to re-run)
            totals["skipped"] += 1
            continue
        if kind != "pfm":
            continue  # not a buffer we understand; leave it alone
        # Target: a .npz path. For a *.pfm source that's a sibling .npz (delete pfm after).
        # For a *.npz source that IS raw PFM, we rewrite the SAME file in place (compressed).
        is_npz_named = src.lower().endswith(".npz")
        npz = src if is_npz_named else os.path.splitext(src)[0] + ".npz"
        if (not is_npz_named) and os.path.exists(npz) and _file_kind(npz) == "npz" and not verify_only:
            totals["skipped"] += 1
            continue
        try:
            arr = read_pfm(src)
        except Exception as exc:
            print("  ! read failed %s: %s" % (os.path.basename(src), exc))
            totals["failed"] += 1
            continue
        key = "normal" if arr.ndim == 3 else "depth"
        # normal -> fp16 (unit vectors, ~2e-4 error). depth -> fp32: lossless AND avoids fp16
        # overflow to +inf for far-background depth values > 65504 cm.
        out_dtype = np.float16 if key == "normal" else np.float32
        lo = arr.astype(out_dtype)
        me, re = _max_finite_err(arr, lo)
        totals["max_err"] = max(totals["max_err"], me)
        totals["max_range_err"] = max(totals["max_range_err"], re)
        totals["pfm_bytes"] += os.path.getsize(src)
        totals["n"] += 1
        if verify_only:
            import io
            b = io.BytesIO(); np.savez_compressed(b, **{key: lo}); totals["npz_bytes"] += b.tell()
            continue
        tmp = npz + ".tmp"
        # write to a file OBJECT so numpy doesn't append a second ".npz" to the tmp name
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **{key: lo})
        # read-back verify before replacing/deleting the source
        try:
            with np.load(tmp) as chk:
                ok = chk[key].shape == lo.shape
        except Exception:
            ok = False
        if not ok:
            print("  ! verify failed, keeping source: %s" % os.path.basename(src))
            if os.path.exists(tmp):
                os.remove(tmp)
            totals["failed"] += 1
            continue
        os.replace(tmp, npz)  # in-place when src IS the .npz; else writes sibling .npz
        totals["npz_bytes"] += os.path.getsize(npz)
        if (not keep_pfm) and (not is_npz_named) and os.path.exists(src):
            os.remove(src)  # only a real .pfm source; the .npz-named case was overwritten in place
    return totals


def main():
    ap = argparse.ArgumentParser(description="Compress depth/normal PFM buffers to fp16/fp32 npz.")
    ap.add_argument("run_dir")
    ap.add_argument("--dtype", choices=["fp16", "fp32"], default="fp16")
    ap.add_argument("--verify-only", action="store_true", help="report sizes/precision, write/delete nothing")
    ap.add_argument("--keep-pfm", action="store_true", help="keep the .pfm after writing .npz")
    args = ap.parse_args()

    t = convert_dir(args.run_dir, dtype=args.dtype, verify_only=args.verify_only, keep_pfm=args.keep_pfm)
    mode = "VERIFY" if args.verify_only else ("CONVERT (kept pfm)" if args.keep_pfm else "CONVERT (deleted pfm)")
    pfm_gb = t["pfm_bytes"] / 1e9
    npz_gb = t["npz_bytes"] / 1e9
    print("[compress_buffers] %s dtype=%s" % (mode, args.dtype))
    print("  frames: %d converted, %d skipped(existing), %d failed" % (t["n"], t["skipped"], t["failed"]))
    print("  size: PFM %.1f GB -> npz %.2f GB (%.0f%%)" % (pfm_gb, npz_gb, (100 * npz_gb / pfm_gb) if pfm_gb else 0))
    print("  fp%s round-trip max err: %.4f (in-defect-range <1500: %.4f cm)" % (
        "16" if args.dtype == "fp16" else "32", t["max_err"], t["max_range_err"]))


if __name__ == "__main__":
    main()
