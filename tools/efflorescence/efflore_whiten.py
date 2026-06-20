"""Route B: turn real "drip / lime-run" efflore PHOTOS (realistic morphology, but too
dark) into whitish efflorescence deposit textures, preserving the drip shape and adding
per-variation tint. Output matches the pipeline's texture-suffix convention
(_color/_opacity/_roughness) so it drops straight into the efflore material list, mixed
with the procedural set (tools/efflorescence/efflore_synth.py).

For each source photo we keep the drip MORPHOLOGY but recolor the deposit toward white:
- color: remap luminance to a near-white level (light/dark structure preserved) + a tint,
  with a touch of original chroma so it isn't a flat white.
- opacity: reuse the photo's own _Opacity (Leaking*) if present, else derive coverage from
  darkness (Grunge*: darker grunge = more deposit). This alpha is the deposit footprint and
  doubles as a rough label (SAM can refine it later).
- roughness: chalky/high (matte deposit).

Run offline (numpy/Pillow), e.g.:
    python tools/efflorescence/efflore_whiten.py \
        --texture-root "C:/.../DefectSynthetic/Textures" --variations 2
Prints the stems to add to the efflore material list.
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
from PIL import Image

COLOR_SUFFIXES = ("_color", "_basecolor", "_base_color", "_albedo", "_diffuse")
SOURCE_GLOBS = ("Leaking*", "GrungeWall*")
# (tint RGB, level_lo, level_hi, keep_chroma, gamma) per variation
VARIANTS = [
    ((255, 255, 252), 0.74, 1.00, 0.12, 0.90),
    ((250, 251, 255), 0.70, 0.98, 0.10, 1.05),
    ((247, 246, 242), 0.68, 0.96, 0.16, 1.15),
]


def _find_companion(color_path, kinds):
    base = color_path
    for suf in COLOR_SUFFIXES:
        i = base.lower().rfind(suf)
        if i >= 0:
            root = base[:i]
            break
    else:
        root = os.path.splitext(base)[0]
    for kind in kinds:
        for ext in (".png", ".jpg", ".jpeg"):
            for cand in (root + "_" + kind + ext, root + "_" + kind.capitalize() + ext):
                if os.path.isfile(cand):
                    return cand
    return None


def _norm(a):
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo + 1e-6)


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def whiten_color(rgb, tint, lo, hi, keep_chroma, gamma):
    a = rgb.astype(np.float32) / 255.0
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    lum = _norm(lum) ** gamma
    level = lo + (hi - lo) * lum
    white = np.array(tint, np.float32) / 255.0
    out = white[None, None, :] * level[..., None]
    out = (1.0 - keep_chroma) * out + keep_chroma * a
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def derive_opacity(rgb):
    """Coverage from darkness: darker (stained) -> more deposit. For sources w/o alpha."""
    a = rgb.astype(np.float32) / 255.0
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    cov = _smoothstep(_norm(1.0 - lum) * 1.2)
    return np.clip(cov * 255.0, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description="Whiten drip efflore photos into deposit textures.")
    ap.add_argument("--texture-root", required=True)
    ap.add_argument("--out-subdir", default="efflore_white")
    ap.add_argument("--variations", type=int, default=2)
    ap.add_argument("--prefix", default="ewhite")
    args = ap.parse_args()

    root = args.texture_root
    out_dir = os.path.join(root, args.out_subdir)
    os.makedirs(out_dir, exist_ok=True)

    color_paths = []
    for pat in SOURCE_GLOBS:
        for ext in ("png", "jpg", "jpeg"):
            for p in glob.glob(os.path.join(root, pat + "." + ext)) + glob.glob(os.path.join(root, pat)):
                if any(s in os.path.basename(p).lower() for s in COLOR_SUFFIXES):
                    color_paths.append(p)
    color_paths = sorted(set(color_paths))
    if not color_paths:
        print("No Leaking*/GrungeWall* color maps found under", root)
        return

    stems = []
    idx = 0
    for cp in color_paths:
        try:
            rgb = np.array(Image.open(cp).convert("RGB"), np.uint8)
        except Exception as e:
            print("skip", cp, e)
            continue
        opacity_src = _find_companion(cp, ("opacity", "alpha", "transparency", "mask"))
        opa = None
        if opacity_src:
            try:
                opa = np.array(Image.open(opacity_src).convert("L"), np.uint8)
            except Exception:
                opa = None
        if opa is None:
            opa = derive_opacity(rgb)
        n_var = max(1, min(args.variations, len(VARIANTS)))
        for v in range(n_var):
            tint, lo, hi, keep, gamma = VARIANTS[v]
            wc = whiten_color(rgb, tint, lo, hi, keep, gamma)
            stem = "%s_%03d" % (args.prefix, idx)
            Image.fromarray(wc, "RGB").save(os.path.join(out_dir, stem + "_color.png"))
            Image.fromarray(opa, "L").save(os.path.join(out_dir, stem + "_opacity.png"))
            Image.fromarray(np.full(wc.shape[:2], 235, np.uint8), "L").save(
                os.path.join(out_dir, stem + "_roughness.png")
            )
            stems.append(stem)
            idx += 1
        print("whitened", os.path.basename(cp), "->", n_var, "variant(s)")

    print("\nwrote %d whitened efflore stems to %s" % (len(stems), out_dir))
    print("add these to the efflore material list (mix with procedural efflore_*):")
    print(stems)


if __name__ == "__main__":
    main()
