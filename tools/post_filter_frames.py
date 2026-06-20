"""Post-process filter for defect-focused renders: drop frames where the target
defect isn't actually visible (occluded / through-wall / too-small at scale).

Runs OFFLINE in CPython (numpy + Pillow) on a run output dir — NOT inside Rhino,
so it has no effect on the batch and no Rhino-runtime dependency. Reads each mask
PNG, counts defect-class pixels (via configs/component_layer_color.json), and for
frames below a pixel threshold moves the whole frame-set (color + mask + buffers +
camera) into <run>/_rejected/ (reversible) — or deletes with --delete.

Usage:
    python tools/post_filter_frames.py --run <run_dir> [--min-pixels 300] [--apply] [--delete]
Default is a DRY RUN (reports only). --apply moves rejects to _rejected/.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import shutil

import numpy as np
from PIL import Image

CHANNEL_SUBDIRS = {
    "color": ("color", ".png"),
    "mask": ("mask", ".png"),
    "depth": ("depth", ".png"),
    "normal": ("normal", ".png"),
    "depth_buffer": ("depth_buffer", ".pfm"),
    "normal_buffer": ("normal_buffer", ".pfm"),
    "camera": ("camera", ".json"),
}


def load_defect_class_colors(color_json_path):
    colors = json.load(open(color_json_path))
    cls = collections.defaultdict(list)
    for name, rgb in colors.items():
        if name.startswith("defect::"):
            cls[name.split("::")[1]].append(tuple(int(v) for v in rgb[:3]))
    return dict(cls)


def count_defect_pixels(mask_path, cls_colors):
    """Return (total_defect_px, {class: px})."""
    arr = np.array(Image.open(mask_path).convert("RGB"), dtype=np.uint8).reshape(-1, 3)
    per = {}
    total = 0
    for cls, cols in cls_colors.items():
        c = 0
        for (r, g, b) in cols:
            c += int(np.count_nonzero((arr[:, 0] == r) & (arr[:, 1] == g) & (arr[:, 2] == b)))
        per[cls] = c
        total += c
    return total, per


def frame_files(run_dir, basename):
    out = {}
    for ch, (sub, ext) in CHANNEL_SUBDIRS.items():
        p = os.path.join(run_dir, sub, basename + ext)
        if os.path.isfile(p):
            out[ch] = p
    return out


def main():
    ap = argparse.ArgumentParser(description="Filter defect renders by mask visibility.")
    ap.add_argument("--run", required=True)
    ap.add_argument("--color-json", default="configs/component_layer_color.json")
    ap.add_argument("--min-pixels", type=int, default=300,
                    help="frames with fewer total defect pixels are rejected")
    ap.add_argument("--apply", action="store_true", help="move rejects to <run>/_rejected/")
    ap.add_argument("--delete", action="store_true", help="delete instead of move (with --apply)")
    args = ap.parse_args()

    cls_colors = load_defect_class_colors(args.color_json)
    masks = sorted(glob.glob(os.path.join(args.run, "mask", "*.png")))
    if not masks:
        print("No masks found in", os.path.join(args.run, "mask"))
        return

    totals = []
    rejects = []
    cls_present = collections.Counter()
    for m in masks:
        base = os.path.splitext(os.path.basename(m))[0]
        total, per = count_defect_pixels(m, cls_colors)
        totals.append(total)
        for cls, c in per.items():
            if c > 0:
                cls_present[cls] += 1
        if total < args.min_pixels:
            rejects.append(base)

    totals = np.array(totals)
    n = len(totals)
    print(f"frames: {n}")
    print(f"zero-defect frames: {int((totals == 0).sum())} ({100*(totals==0).mean():.0f}%)")
    print("would-reject counts at thresholds:")
    for thr in (1, 100, 300, 1000, 3000):
        print(f"  < {thr:5} px : {int((totals < thr).sum())} ({100*(totals<thr).mean():.0f}%)")
    print("per-class frames-present:", dict(cls_present))
    print(f"\nthreshold={args.min_pixels} -> REJECT {len(rejects)}/{n} frames, KEEP {n-len(rejects)}")

    if not args.apply:
        print("\n(dry run; pass --apply to move rejects to _rejected/, or --delete to remove)")
        return

    rej_root = os.path.join(args.run, "_rejected")
    moved = 0
    for base in rejects:
        for ch, src in frame_files(args.run, base).items():
            if args.delete:
                os.remove(src)
            else:
                dst_dir = os.path.join(rej_root, CHANNEL_SUBDIRS[ch][0])
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, os.path.join(dst_dir, os.path.basename(src)))
            moved += 1
    print(f"{'deleted' if args.delete else 'moved to _rejected/'}: {moved} files across {len(rejects)} frames")


if __name__ == "__main__":
    main()
