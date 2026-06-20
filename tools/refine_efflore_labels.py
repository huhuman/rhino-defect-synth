"""Refine efflorescence labels: the mask labels the FULL efflore geometry footprint, but
the deposit is semi-transparent and doesn't fill it. This post-step shrinks the efflore
label to the actually-visible white deposit, using the color render.

Per frame: take the rough efflore region from the mask (white-deposit classes only —
CS2 + CS3::inner; the CS3::outer rust halo is left as-is). Within that region, classify
each pixel as deposit (whitish/bright) vs bare concrete showing through (darker), via a
per-region Otsu threshold on lightness. Non-deposit pixels are relabelled to the nearest
surrounding surface class (nearest-neighbour fill), so the refined mask = the real deposit.

method=color (default): fast, no deps beyond numpy/scipy/PIL.
method=sam (optional): plug in SAM here if the color split is insufficient (stub provided).

Usage:
    python tools/refine_efflore_labels.py --run <run_dir>          # dry-run stats
    python tools/refine_efflore_labels.py --run <run_dir> --apply  # write mask_refined/
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
from PIL import Image
from scipy import ndimage

WHITE_EFFLORE_LAYERS = ("defect::efflore::CS2", "defect::efflore::CS3::inner")


def _otsu(values):
    """Otsu threshold on a 1-D array of 0..255 lightness values."""
    if values.size == 0:
        return 128.0
    hist, _ = np.histogram(values, bins=256, range=(0, 255))
    total = values.size
    sum_all = np.dot(np.arange(256), hist)
    w_b = 0.0
    sum_b = 0.0
    best_t, best_var = 128.0, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, float(t)
    return best_t


def refine_one(mask_rgb, color_rgb, white_colors, min_region=50):
    """Return (refined_mask, region_px, deposit_px). Mask unchanged if no efflore."""
    h, w = mask_rgb.shape[:2]
    region = np.zeros((h, w), bool)
    for (r, g, b) in white_colors:
        region |= (mask_rgb[:, :, 0] == r) & (mask_rgb[:, :, 1] == g) & (mask_rgb[:, :, 2] == b)
    region_px = int(region.sum())
    if region_px < min_region:
        return mask_rgb, region_px, region_px

    lightness = color_rgb.max(axis=2).astype(np.float32)  # white deposit is bright
    thr = _otsu(lightness[region])
    deposit = region & (lightness >= thr)
    # Morphological cleanup: close interior texture holes (efflore isn't uniformly bright)
    # and drop isolated speckles, so the label is a contiguous deposit, not noise.
    struct = ndimage.generate_binary_structure(2, 2)
    deposit = ndimage.binary_closing(deposit, structure=struct, iterations=3) & region
    deposit = ndimage.binary_opening(deposit, structure=struct, iterations=1) & region
    remove = region & ~deposit
    if not remove.any():
        return mask_rgb, region_px, int(deposit.sum())

    # Relabel non-deposit region pixels to the nearest NON-efflore-region pixel's color.
    _, (iy, ix) = ndimage.distance_transform_edt(region, return_indices=True)
    refined = mask_rgb.copy()
    refined[remove] = mask_rgb[iy[remove], ix[remove]]
    return refined, region_px, int(deposit.sum())


def main():
    ap = argparse.ArgumentParser(description="Refine efflore labels to the visible deposit.")
    ap.add_argument("--run", required=True)
    ap.add_argument("--color-json", default="configs/component_layer_color.json")
    ap.add_argument("--method", choices=["color", "sam"], default="color")
    ap.add_argument("--apply", action="store_true", help="write refined masks to mask_refined/")
    args = ap.parse_args()

    if args.method == "sam":
        raise SystemExit("SAM method is a stub — wire your SAM predictor in refine_one(). "
                         "Use --method color for the dependency-free refiner.")

    colors = json.load(open(args.color_json))
    white_colors = [tuple(int(v) for v in colors[name][:3]) for name in WHITE_EFFLORE_LAYERS if name in colors]
    masks = sorted(glob.glob(os.path.join(args.run, "mask", "*.png")))
    if not masks:
        print("No masks in", os.path.join(args.run, "mask"))
        return

    out_dir = os.path.join(args.run, "mask_refined")
    if args.apply:
        os.makedirs(out_dir, exist_ok=True)

    n_eff = 0
    tot_region = 0
    tot_deposit = 0
    for m in masks:
        base = os.path.splitext(os.path.basename(m))[0]
        color_path = os.path.join(args.run, "color", base + ".png")
        if not os.path.isfile(color_path):
            continue
        mask_rgb = np.array(Image.open(m).convert("RGB"), np.uint8)
        color_rgb = np.array(Image.open(color_path).convert("RGB"), np.uint8)
        refined, region_px, deposit_px = refine_one(mask_rgb, color_rgb, white_colors)
        if region_px >= 50:
            n_eff += 1
            tot_region += region_px
            tot_deposit += deposit_px
        if args.apply:
            Image.fromarray(refined, "RGB").save(os.path.join(out_dir, base + ".png"))

    if n_eff:
        keep = 100.0 * tot_deposit / max(tot_region, 1)
        print("frames with efflore: %d" % n_eff)
        print("efflore label pixels: %d -> %d  (deposit is %.0f%% of the old footprint; %.0f%% trimmed)"
              % (tot_region, tot_deposit, keep, 100 - keep))
    else:
        print("no efflore regions found")
    if args.apply:
        print("wrote refined masks to", out_dir)
    else:
        print("(dry run; pass --apply to write mask_refined/)")


if __name__ == "__main__":
    main()
