# Crack shape-library generation requirements (for the library side)

Context: the Rhino pipeline loads each crack as a polygon outline and carves a thin groove
**point-by-point**. The `long_units_v2` library hung model-0 modeling because its outlines were
~2000–5300 vertices (10× v1) and projected cracks reached ~10 m. These are the requirements to
fix it **at the source** so the pipeline runs cleanly. (The pipeline also has a 500-pt decimation
backstop, but the library should not rely on it.)

## 1. Point count per polygon — THE root fix
- **Cap each crack outline at ≤ 300 vertices** (v1 ran ~200, max 334, and was fine).
- Decimate at generation with **Douglas–Peucker** (shape-preserving), tolerance ≈ 0.3–0.5 × the
  crack width in px — this drops the dense collinear points along the length while keeping the
  jagged turns. Do NOT uniform-subsample so aggressively that corners round off.
- Target ~150–300 pts; hard ceiling 300.

## 2. Length / aspect — second root fix
- The pipeline's rendered crack **length = (config crack width) × aspect**, where
  `aspect = span_px / ref_width_px`. Config widths are CS2≈0.03 cm, CS3≈0.13 cm.
  So `aspect = 7980` → a ~10 m crack: it won't fit on most bridge components (piers/beams flanges)
  and is slow to carve.
- **Cap aspect at ≈ 2000** (→ ≤ ~2.5 m at CS3, well within slab/beam/parapet spans), and spread
  aspect across roughly **[200, 2000]** for variety (short hairlines → long structural cracks).
  Avoid the >3000 tail entirely.

## 3. Polygon geometry validity
- Each outline must be a **closed, simple (non-self-intersecting) polygon**. Self-intersections
  break the Rhino groove boolean. Validate + repair before export.
- ≥ 3 vertices; consistent winding; the loop traces BOTH sides of the crack (so it has a real
  width, not a zero-area line).
- Coordinates in **pixels**, origin anywhere (the pipeline re-centers using width_px/height_px).

## 4. CSV + JSON schema (keep — this is what the loader reads)
CSV (`crack_summary.csv`), one row per crack:
- `instance_id`
- `instance_polygon_path` — path to the polygon JSON, **relative to the CSV's directory**
  (e.g. `crack_polygon/longvec_cs2_000000.json`).
- `cs` — `CS1` / `CS2` / `CS3` (drives severity; pipeline prefers this column).
- `ref_width_px` — the **actual crack WIDTH in px** (NOT the long span). The pipeline uses this as
  the scale reference (`metric_scale = config_width_cm / ref_width_px`). Getting this wrong
  rescales the whole crack — keep it the true local width.
- `span_px`, `aspect`, `est_length_m`, `bbox_w`, `bbox_h`, `source`, `seed` — kept for reference.

Polygon JSON: `{"polygons": [[[x,y],[x,y],...]], "width_px": <int>, "height_px": <int>, ...}`.
`polygons` is a list (usually 1) of vertex lists. `width_px`/`height_px` are the outline's bbox
(used for re-centering), **not** the crack width.

## 5. CS distribution (optional)
- v2 is CS2/CS3 only (500 each); config `cs_weights:[0,1,1]` matches. If hairline CS1 is wanted,
  add CS1 instances (thin, short).

## Quick acceptance check before handing over a new library
- `max vertices over all polygons ≤ 300`
- `max aspect ≤ 2000`
- all `instance_polygon_path` resolve + every polygon is closed & simple
- spot-render 5 cracks: jagged, correct width, length ≤ ~2.5 m, no carve hang.
