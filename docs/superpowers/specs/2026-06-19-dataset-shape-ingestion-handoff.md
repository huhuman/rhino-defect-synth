# Handoff: dacl10k / S2DS → shape-candidate ingestion

**For the OTHER codebase** (where the defect-shape extraction lives). This spec defines the
**exact output contract** the Rhino pipeline (`rhino-defect-synth`) consumes, so you can generate
compatible shape candidates from public datasets. Hand this file to a fresh session there.

Datasets (see `docs/research/2026-06-19-defect-shape-datasets.md`): **dacl10k** (polygons, has
spalling + efflorescence, CC BY-NC 4.0) is the primary source; **S2DS** (pixel masks, academic-only)
secondary. Both non-commercial/research only.

---

## Goal
For each real spalling / efflorescence / crack instance in the dataset, emit:
1. one row in a per-type **overview CSV**, and
2. one (or more) **polygon JSON** file(s),
following the schema below. The Rhino pipeline points `defect.{type}.overview_csv_path` at the CSV.

## Directory layout the pipeline expects
```
<root>/
  crack_summary.csv          spalling_summary.csv          efflore_summary.csv
  crack_units/    <id>.png    spalling_units/  <id>.png     efflore_units/  <id>.png   (instance mask PNGs)
  crack_polygon/  <id>.json   spalling_polygon/<id>.json    efflore_polygon/<id>.json  (+ <id>_expand.json for efflore CS3)
```
Polygon path is derived from `instance_mask_path` by swapping `_units/`→`_polygon/` and `.png`→`.json`
(or set an explicit `polygon_json_path` column). Efflore outer/halo = same name + `_expand.json`.

## Polygon JSON schema (all types)
```json
{
  "instance_id": "<matches CSV instance_id>",
  "width_px":  <bbox width in px>,
  "height_px": <bbox height in px>,
  "polygons": [ [[x,y],[x,y], ... >=3 pts], ... ],
  "source_mask": "<original image filename, optional>"
}
```
- Coordinates are **pixels, relative to the instance bbox top-left (0,0)**; `0<=x<width_px`,
  `0<=y<height_px`. The pipeline centres (subtract bbox/2) and scales to cm itself — **do NOT
  pre-scale or centre.** Each polygon ≥3 points; closure optional. The largest polygon is used as
  the primary outline.

## Overview CSV columns
Common to all: `instance_id, instance_mask_path, bbox_x, bbox_y, bbox_w, bbox_h` (bbox in px of the
source image). Per type, add the **reference metric** (px) used to scale to real-world cm:

| type | extra column | meaning | how to compute from a mask |
|---|---|---|---|
| crack | `width_px` | crack width (px) | median width = 2·(area / skeleton_length), or distance-transform peak |
| spalling | `diameter_px` | pit diameter (px) | equivalent diameter = 2·sqrt(area/π) |
| efflore | (uses max(bbox_w,bbox_h)) | span (px) | bbox longest edge; optional `expand_ratio` |

The pipeline maps `metric_px` → `target_metric_cm` by sampling a real-world size (crack width t1/t2
thresholds; spalling diameter_threshold 15 cm; efflore span_range 30–150 cm) and computing
`scale = target_cm / metric_px`. So the metric just needs to be **internally consistent in px**.

## Condition-state (CS) conventions
- **Crack:** CS encoded in the `instance_id`/filename suffix — `_skeleton`→CS1, `_erodex1`→CS2,
  none→CS3. To span CS1–CS3, emit multiple polygon variants per crack (skeletonised, eroded×1, raw).
- **Spalling:** CS chosen by the pipeline from depth/diameter sampling — you don't set it. Just emit
  the outline.
- **Efflorescence:** **CS2 = inner polygon only; CS3 = inner + `_expand.json` outer/halo polygon.**
  To get CS3 samples, also emit an `_expand` polygon (a dilated/halo outline) for that instance.

## Minimal examples
Crack `crack_summary.csv`:
```
instance_id,instance_mask_path,bbox_x,bbox_y,bbox_w,bbox_h,width_px
img007_c0,crack_units/img007_c0.png,320,175,37,225,31.6
img007_c0_skeleton,crack_units/img007_c0_skeleton.png,320,175,37,225,10.0
```
Efflore `efflore_summary.csv` + `efflore_polygon/img007_e0.json` (+ `_expand.json` for CS3):
```
instance_id,instance_mask_path,bbox_x,bbox_y,bbox_w,bbox_h,expand_ratio
img007_e0,efflore_units/img007_e0.png,404,417,121,226,0.05
```

## Conversion outline (dacl10k / S2DS → contract)
1. Load each image's annotations; for **spalling/efflorescence/crack** classes, get the per-instance
   polygon (dacl10k) or connected component of the class mask (S2DS).
2. Compute the instance bbox; translate polygon points to **bbox-local px**; write `width_px`/
   `height_px` = bbox dims.
3. Compute the reference metric per the table above (crack width via skeleton; spalling equiv-diameter;
   efflore = bbox longest edge).
4. Write the unit mask PNG (cropped to bbox), the polygon JSON, and the CSV row.
5. Efflore CS3: also emit a dilated `_expand` polygon. Crack CS1/CS2: emit `_skeleton`/`_erodex1`
   variants.
6. Keep the dataset attribution; non-commercial use only.

## Validation before handing back to Rhino
- Every CSV `instance_mask_path` resolves; its derived polygon JSON exists.
- Polygon points are bbox-local px, ≥3 pts, within `[0,width_px]×[0,height_px]`.
- Reference-metric column present and >0.
- (Authoritative source: `utils_loc/defect_placement_templates.py` `_load_overview_rows`,
  `_resolve_polygon_path_from_row`, `_build_shape_from_overview_row`.)
