#!/usr/bin/env python3
"""Audit defect visibility / wasted-modeling for a component render run.

For a run's mask + camera channels, this reports — per defect CLASS and per defect
INSTANCE — how well each modeled defect was actually captured, so you can see before a
long run whether defects are rendering too small, getting fully missed (modeled but no
usable frame = wasted modeling+render), or producing junk frames (camera buried in a
surface / occluded).

It is Rhino-free (numpy + Pillow only) and reads:
  <run>/mask/*.png       flat layer-colour semantic masks
  <run>/camera/*.json    per-frame camera pose (location/target)

Defect vs surface colours are derived from the layer→colour map
(configs/component_layer_color.json): keys under `defect::` are defects, everything
else (component::, debug::, ...) is background/surface.

Usage:
  python tools/audit_defect_visibility.py --run <run_dir> [--color-map <json>]
         [--size-gate 0.0008] [--downsample 1] [--json out.json]

A defect INSTANCE is approximated by clustering frames whose camera TARGET coincides
(defect-focused poses look at the defect point), rounded to --cluster-cm.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COLOR_MAP = os.path.join(REPO_ROOT, "configs", "component_layer_color.json")


def load_color_sets(color_map_path):
    """Return (defect_colors:set[tuple], class_of:dict[tuple->str]) from the layer map.

    Keys beginning with `defect::` are defect classes (we collapse to the family token,
    e.g. defect::crack::CS3 -> 'crack'); all other keys are surface/background.
    """
    with open(color_map_path) as fh:
        raw = json.load(fh)
    defect_colors = set()
    class_of = {}
    for layer, rgb in raw.items():
        if not isinstance(rgb, (list, tuple)) or len(rgb) < 3:
            continue
        key = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        if str(layer).startswith("defect::"):
            parts = str(layer).split("::")
            family = parts[1] if len(parts) > 1 else "defect"
            defect_colors.add(key)
            class_of[key] = family
    return defect_colors, class_of


def _frame_stats(mask_path, defect_colors, class_of, downsample):
    im = Image.open(mask_path).convert("RGB")
    if downsample and downsample > 1:
        im = im.resize((im.width // downsample, im.height // downsample), Image.NEAREST)
    arr = np.asarray(im).reshape(-1, 3)
    total = arr.shape[0]
    cols, counts = np.unique(arr, axis=0, return_counts=True)
    per_class = {}
    defect_px = 0
    for col, n in zip(cols, counts):
        key = (int(col[0]), int(col[1]), int(col[2]))
        if key in defect_colors:
            n = int(n)
            defect_px += n
            fam = class_of.get(key, "defect")
            per_class[fam] = per_class.get(fam, 0) + n
    dominant = max(per_class, key=per_class.get) if per_class else None
    return total, defect_px, per_class, dominant


def audit(run_dir, color_map_path, size_gate, downsample, cluster_cm):
    defect_colors, class_of = load_color_sets(color_map_path)
    mask_dir = os.path.join(run_dir, "mask")
    cam_dir = os.path.join(run_dir, "camera")
    masks = sorted(glob.glob(os.path.join(mask_dir, "*.png")))
    if not masks:
        raise SystemExit("no masks under {}".format(mask_dir))

    rows = []  # (base, dist, total, defect_px, per_class, dominant, target)
    for mp in masks:
        base = os.path.basename(mp)[:-4]
        cj = os.path.join(cam_dir, base + ".json")
        dist = None
        target = None
        if os.path.exists(cj):
            cam = json.load(open(cj)).get("camera", {})
            loc = cam.get("location")
            tgt = cam.get("target")
            if loc and tgt:
                loc = np.asarray(loc, dtype=float)
                tgt = np.asarray(tgt, dtype=float)
                dist = float(np.linalg.norm(loc - tgt))
                target = tuple(tgt)
        total, dpx, per_class, dominant = _frame_stats(mp, defect_colors, class_of, downsample)
        rows.append((base, dist, total, dpx, per_class, dominant, target))

    n = len(rows)
    res = {
        "run": run_dir,
        "frames": n,
        "size_gate_ratio": size_gate,
        "models": len(sorted({r[0].split("_r")[0] for r in rows})),
    }

    # ---- frame-level junk ----
    zero = sum(1 for r in rows if r[3] == 0)
    below = sum(1 for r in rows if r[2] and (r[3] / r[2]) < size_gate)
    res["junk_zero_defect_px"] = [zero, round(zero / n * 100, 1)]
    res["junk_below_size_gate"] = [below, round(below / n * 100, 1)]

    # ---- per-class ----
    by_class = {}
    for r in rows:
        if r[5] is None:
            by_class.setdefault("NONE", []).append(0)
        else:
            by_class.setdefault(r[5], []).append(r[4][r[5]])
    res["per_class"] = {}
    for cl, vals in sorted(by_class.items()):
        a = np.array(vals)
        res["per_class"][cl] = {
            "frames": int(len(a)),
            "median_px": int(np.median(a)),
            "mean_px": int(a.mean()),
            "n_below_500px": int((a < 500).sum()),
            "n_below_100px": int((a < 100).sum()),
            "max_px": int(a.max()),
        }

    # ---- per-instance coverage (cluster by camera target) ----
    clusters = {}
    for r in rows:
        if r[6] is None:
            continue
        key = tuple(round(c / cluster_cm) * cluster_cm for c in r[6])
        clusters.setdefault(key, []).append(r)
    missed = partial = full = 0
    for key, frames in clusters.items():
        usable = [f for f in frames if f[2] and (f[3] / f[2]) >= size_gate]
        if not usable:
            missed += 1
        elif len(usable) < len(frames):
            partial += 1
        else:
            full += 1
    res["instances"] = {
        "distinct_defect_targets": len(clusters),
        "fully_captured": full,
        "partial": partial,
        "missed_no_usable_frame": missed,
        "missed_pct": round(missed / max(1, len(clusters)) * 100, 1),
    }

    # ---- crack camera-distance distribution ----
    crack_d = [r[1] for r in rows if r[5] == "crack" and r[1] is not None]
    if crack_d:
        a = np.array(crack_d)
        res["crack_distance_cm"] = {
            "n": int(len(a)),
            "min": int(a.min()),
            "p25": int(np.percentile(a, 25)),
            "median": int(np.median(a)),
            "p75": int(np.percentile(a, 75)),
            "max": int(a.max()),
        }
    return res


def _print(res):
    print("run    : {}".format(res["run"]))
    print("models : {}   frames: {}".format(res["models"], res["frames"]))
    z = res["junk_zero_defect_px"]
    b = res["junk_below_size_gate"]
    print("junk   : 0-defect-px {}/{} ({}%)   below-size-gate {}/{} ({}%)".format(
        z[0], res["frames"], z[1], b[0], res["frames"], b[1]))
    inst = res["instances"]
    print("defects: {} targets -> {} full / {} partial / {} MISSED ({}% wasted modeling)".format(
        inst["distinct_defect_targets"], inst["fully_captured"], inst["partial"],
        inst["missed_no_usable_frame"], inst["missed_pct"]))
    print("per-class defect pixels:")
    print("  {:10s}{:>8s}{:>11s}{:>11s}{:>10s}{:>10s}".format(
        "class", "frames", "median_px", "mean_px", "<500px", "<100px"))
    for cl, s in res["per_class"].items():
        print("  {:10s}{:>8d}{:>11d}{:>11d}{:>10d}{:>10d}".format(
            cl, s["frames"], s["median_px"], s["mean_px"], s["n_below_500px"], s["n_below_100px"]))
    if "crack_distance_cm" in res:
        d = res["crack_distance_cm"]
        print("crack camera distance (cm): min {min} | p25 {p25} | median {median} | p75 {p75} | max {max}".format(**d))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run directory (contains mask/ and camera/)")
    ap.add_argument("--color-map", default=DEFAULT_COLOR_MAP, help="layer->colour JSON")
    ap.add_argument("--size-gate", type=float, default=0.0008,
                    help="min defect-px / total-px for a frame to count as usable (default 0.0008)")
    ap.add_argument("--downsample", type=int, default=1,
                    help="NEAREST mask downsample factor for speed (e.g. 4). Default 1 = full res")
    ap.add_argument("--cluster-cm", type=float, default=5.0,
                    help="round camera target to this many cm to group poses into one defect")
    ap.add_argument("--json", help="also write the full result dict to this path")
    args = ap.parse_args()

    res = audit(args.run, args.color_map, args.size_gate, args.downsample, args.cluster_cm)
    _print(res)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2)
        print("wrote {}".format(args.json))


if __name__ == "__main__":
    main()
