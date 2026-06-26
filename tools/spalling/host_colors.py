#!/usr/bin/env python3
"""Offline builder for the spalling host-colour table.

Computes a representative concrete colour (+ a few interior variants) for each
component-surface material, so the runtime can colour spall cavities to match the
host they broke out of. Pure numpy/PIL/yaml — runs in WSL, no Rhino.

See docs/superpowers/specs/2026-06-25-spalling-host-color-design.md
    docs/superpowers/plans/2026-06-26-spalling-host-color.md
"""
import glob  # noqa: F401  (kept for ad-hoc use; os.walk is the primary scanner)
import os

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Colour extraction
# ---------------------------------------------------------------------------

def representative_color(image_path, trim_pct=10.0):
    """Trimmed-mean RGB of an image: drop the top/bottom `trim_pct` luminance
    percentiles (kills highlights/shadows), average the rest. Returns (r,g,b) ints."""
    img = Image.open(image_path).convert("RGB")
    arr = np.asarray(img, dtype=np.float64).reshape(-1, 3)
    lum = arr @ np.array([0.299, 0.587, 0.114])
    lo, hi = np.percentile(lum, [trim_pct, 100.0 - trim_pct])
    keep = arr[(lum >= lo) & (lum <= hi)]
    if keep.size == 0:
        keep = arr
    mean = keep.mean(axis=0)
    return tuple(int(round(c)) for c in mean)


def make_variants(base, count=3, darker_factor=0.85, desaturate=0.15):
    """Return `count` colour variants of base (r,g,b): [exact, darker, desaturated, ...].
    Broken interior concrete reads darker/dustier than the weathered face."""
    r, g, b = base
    out = [(int(r), int(g), int(b))]
    if count >= 2:
        out.append(tuple(int(round(c * darker_factor)) for c in base))
    if count >= 3:
        grey = (r + g + b) / 3.0
        out.append(tuple(int(round(c + (grey - c) * desaturate)) for c in base))
    while len(out) < count:
        f = darker_factor ** (len(out))
        out.append(tuple(int(round(c * f)) for c in base))
    return [tuple(max(0, min(255, c)) for c in v) for v in out[:count]]


# ---------------------------------------------------------------------------
# Host name -> texture file -> table
# ---------------------------------------------------------------------------

_COLOR_SUFFIXES = ("_Color", "_color", "_COL", "_albedo", "_Albedo", "_diffuse", "")
_IMG_EXTS = (".png", ".jpg", ".jpeg")


def find_base_texture(material_name, texture_root):
    """Find the base-colour texture file for a material stem under texture_root
    (recursive). Prefer a *_Color-style companion; fall back to the bare stem."""
    candidates = []
    for root, _dirs, files in os.walk(texture_root):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in _IMG_EXTS:
                continue
            for suf in _COLOR_SUFFIXES:
                if stem == material_name + suf:
                    candidates.append((_COLOR_SUFFIXES.index(suf), os.path.join(root, f)))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])  # lowest suffix index = most preferred
    return candidates[0][1]


def build_color_table(material_names, texture_root, variants=3,
                      darker_factor=0.85, desaturate=0.15, trim_pct=10.0):
    """Map each resolvable host material name to a list of colour variants.
    Unresolvable names are omitted (runtime falls back to the gravel material)."""
    table = {}
    for name in material_names:
        path = find_base_texture(name, texture_root)
        if not path:
            continue
        try:
            base = representative_color(path, trim_pct=trim_pct)
        except Exception:
            continue
        table[name] = make_variants(base, count=variants,
                                    darker_factor=darker_factor, desaturate=desaturate)
    return table


def host_material_names_from_config(component_base_path, exclude_layers=None):
    """Collect every material name listed under component::* in the materials map,
    skipping any layer in `exclude_layers` (e.g. non-concrete component::bearing,
    whose dark rubber/plastic colours would make near-black spall patches)."""
    import yaml
    exclude = set(str(x) for x in (exclude_layers or []))
    cfg = yaml.safe_load(open(component_base_path))
    # The materials map lives under `preparation:` in component_base.yaml, with a
    # top-level fallback for other layouts.
    mats = (cfg.get("preparation") or {}).get("materials") or cfg.get("materials") or {}
    names = set()
    for layer, lst in mats.items():
        if str(layer) in exclude:
            continue
        if str(layer).startswith("component::") and isinstance(lst, list):
            names.update(str(n) for n in lst)
    return sorted(names)


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Build spalling host-colour table.")
    ap.add_argument("--component-config", default="configs/component_base.yaml")
    ap.add_argument("--texture-root", required=True)
    ap.add_argument("--out", default="configs/spalling_host_colors.json")
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--darker-factor", type=float, default=0.85)
    ap.add_argument("--desaturate", type=float, default=0.15)
    ap.add_argument("--trim-pct", type=float, default=10.0)
    ap.add_argument("--exclude-layers", nargs="*", default=["component::bearing"],
                    help="component layers to skip (non-concrete hosts)")
    a = ap.parse_args(argv)
    names = host_material_names_from_config(a.component_config, exclude_layers=a.exclude_layers)
    table = build_color_table(names, a.texture_root, variants=a.variants,
                              darker_factor=a.darker_factor, desaturate=a.desaturate,
                              trim_pct=a.trim_pct)
    with open(a.out, "w") as fh:
        json.dump(table, fh, indent=2, sort_keys=True)
    print("wrote {} ({} hosts resolved of {} listed)".format(a.out, len(table), len(names)))


if __name__ == "__main__":
    main()
