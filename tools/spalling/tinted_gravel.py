#!/usr/bin/env python3
"""Offline: tint a gravel PBR albedo toward each host colour (keeping its normal/roughness
relief) so spall cavities read as host-toned aggregate, not a flat patch.

See docs/superpowers/specs/2026-06-26-depth-realism-oblique-camera-tinted-gravel-design.md
"""
import os
import shutil

import numpy as np
from PIL import Image


def ratio_tint(base_rgb, target_rgb):
    """Recolour an HxWx3 uint8 albedo so its MEAN becomes target_rgb while preserving the
    relative per-pixel variation (aggregate detail): out = base * (target / base_mean)."""
    arr = np.asarray(base_rgb, dtype=np.float64)
    mean = arr.reshape(-1, 3).mean(axis=0)
    mean = np.where(mean < 1.0, 1.0, mean)
    scale = np.asarray(target_rgb, dtype=np.float64) / mean
    out = np.clip(arr * scale, 0.0, 255.0)
    return out.astype(np.uint8)


_PBR_SUFFIXES = ("_BaseColor", "_Color", "_Normal", "_Roughness", "_AO", "_Height", "_Metallic")


def _find_companions(gravel_base, texture_root):
    """Return {suffix: path} for the gravel base's PBR maps found under texture_root."""
    found = {}
    for root, _dirs, files in os.walk(texture_root):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            for suf in _PBR_SUFFIXES:
                if stem == gravel_base + suf:
                    found.setdefault(suf, os.path.join(root, f))
    return found


def generate_tinted_sets(host_colors, gravel_base, texture_root, out_dir):
    """For each host -> first colour variant, write a tinted-gravel PBR set
    spallhost_<host>_<suffix>. Albedo is ratio-tinted; all other maps are copied verbatim."""
    comp = _find_companions(gravel_base, texture_root)
    albedo_suf = "_BaseColor" if "_BaseColor" in comp else ("_Color" if "_Color" in comp else None)
    if not albedo_suf:
        raise SystemExit("gravel base {} has no albedo map under {}".format(gravel_base, texture_root))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    base_albedo = np.asarray(Image.open(comp[albedo_suf]).convert("RGB"), dtype=np.uint8)
    written = 0
    for host, variants in host_colors.items():
        target = tuple(int(c) for c in variants[0])
        tinted = ratio_tint(base_albedo, target)
        stem = "spallhost_{}".format(host)
        Image.fromarray(tinted, "RGB").save(os.path.join(out_dir, stem + "_BaseColor.png"))
        for suf, path in comp.items():
            if suf in ("_BaseColor", "_Color"):
                continue
            shutil.copyfile(path, os.path.join(out_dir, stem + suf + os.path.splitext(path)[1]))
        written += 1
    return written


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Generate host-tinted gravel spall textures.")
    ap.add_argument("--color-table", default="configs/spalling_host_colors.json")
    ap.add_argument("--gravel-base", default="Gravel02_2K")
    ap.add_argument("--texture-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    host_colors = json.load(open(a.color_table))
    n = generate_tinted_sets(host_colors, a.gravel_base, a.texture_root, a.out_dir)
    print("wrote {} tinted-gravel sets to {}".format(n, a.out_dir))


if __name__ == "__main__":
    main()
