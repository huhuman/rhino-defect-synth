# Depth realism: oblique camera (#2/#3) + host-tinted gravel spall material (#4) — design

Date: 2026-06-26
Status: approved (design), pending implementation plan
Branch: `overnight/leak-fix-and-realism`

## Problem

Spall cavities and crack grooves render **visually flat** — their depth is not legible —
for two compounding reasons:

1. **Camera is head-on.** `generate_defect_camera_poses` places the camera at
   `defect_point + surface_normal * distance`, looking back down the normal, with only a
   ±5° symmetric jitter (`direction_jitter_degrees: 5`). Viewing a cavity/groove straight
   down its axis hides the side walls — no parallax, no depth. It also makes every model's
   viewpoints feel the same (issue #3: poses are all "normal ± 5°").

2. **Spall material is flat colour.** The host-colour feature renders spall cavities as a
   pure `DiffuseColor` / PBR base colour with **no normal or roughness texture** — a
   perfectly smooth tint. No aggregate, no micro-relief, so even an oblique camera would see
   little surface depth, and it reads as a fake flat patch.

Depth perception needs BOTH an oblique viewing angle AND surface relief. This spec fixes
both together. Spall SHAPE (real dacl10k+s2ds polygon libraries) and DEPTH (1.3–5 cm
extrusion) are fine and unchanged.

## Goal

- **#2/#3:** Most defect camera poses view the defect **obliquely** (20–50° off the surface
  normal, random azimuth), with a minority kept near head-on for variety. Reveals cavity/
  groove depth via parallax and diversifies per-model viewpoints.
- **#4:** Spall cavities are rendered with a **host-tinted gravel** material — a real gravel
  texture (keeping its normal + roughness maps for relief) whose albedo is recoloured toward
  the host surface's colour. Aggregate feel + micro-relief + host-matched tone (no clash).

Hard constraint: preserve the long-run pipeline — occlusion culling, per-type framing,
size-filter, mask/label (layer colour), material-cleanup/reset, layer-reuse all unchanged.

---

## Part A — Oblique camera (`utils_loc/camera.py`)

### Change

`generate_defect_camera_poses` currently gets each candidate direction from
`_direction_on_normal_hemisphere(normal, jitter_deg)` (normal ± small jitter). Replace that
single call with a new `_sample_view_direction(normal, cfg, rng)` that, per pose:

- With probability `head_on_fraction` (default 0.25): return the current near-normal
  direction (`_direction_on_normal_hemisphere(normal, jitter_deg)`) — documentation-style.
- Otherwise (oblique): sample a tilt `θ ~ U[oblique_min, oblique_max]` (default 20–50°) and
  azimuth `φ ~ U[0, 360°)`. Build the direction by rotating the normal by θ about a tangent
  axis chosen at azimuth φ in the normal's tangent plane (reuse `_orthonormal_basis(normal)`
  for the tangent frame). Then apply the existing small `jitter_deg` spread on top.

The returned direction is still on the outward hemisphere (θ ≤ 50° < 90°, so the camera
stays in front of the surface). Everything downstream is unchanged:

- camera position = `point + direction * radius` (existing).
- **occlusion test + retry loop** (existing) re-tests each oblique candidate; oblique views
  are more likely occluded, and the existing resample-until-clear / skip logic handles it.
- framing/distance (`_distance_samples_for_defect`), size-filter (`min_visible_size_ratio`),
  all pose fields — unchanged.

### Config (`rendering.camera.component`)

```yaml
      oblique_angle_range: [20.0, 50.0]   # degrees off-normal for oblique poses
      head_on_fraction: 0.25              # fraction of poses kept near head-on
```

Absent/empty `oblique_angle_range` → current behaviour (all near-normal), so the change is
opt-in and revertible. `render.py` reads these from `component_cfg` and passes them through
to `generate_defect_camera_poses` (alongside the existing `direction_jitter_degrees`,
`framing_factor_by_type`, etc.).

### Notes

- Per-type obliqueness (e.g. cracks more oblique than spalls) is **out of scope** — uniform
  range for all defect types. Can be added later if needed.
- Foreshortening: an oblique view makes the defect's apparent size a bit smaller; the
  existing size-filter may drop a few more poses. Acceptable; no framing compensation.

---

## Part B — Host-tinted gravel spall material

### B1. Offline texture generation (extend `tools/spalling/`)

New generator (e.g. `tools/spalling/tinted_gravel.py`, reusing `host_colors.py`'s
`representative_color` + the host list) producing, per host material, a tinted-gravel PBR set:

- Input: a neutral grey **gravel base** with a full PBR set (verified available: Gravel01/02/03_2K,
  GreyRock01/02_2K all have `_BaseColor` + `_Normal` + `_Roughness` + `_AO` + `_Height`).
- Recolour the base **albedo** toward the host's representative colour by **ratio scaling**
  (preserves the gravel's per-pixel luminance/chroma variation, i.e. the aggregate detail —
  only the mean tone moves):
  `out = clip(gravel_rgb * (host_rgb / gravel_mean_rgb), 0, 255)`.
- **Copy the gravel's `_Normal` / `_Roughness` (and `_AO` / `_Height`) maps unchanged** —
  these carry the surface relief that makes depth legible.
- Output a pipeline-suffix-named set per host into `Textures/spall_host_proc/`:
  `spallhost_<hostStem>_BaseColor.png`, `_Normal.png`, `_Roughness.png`, … so the existing
  importer (`materials.find_texture_bitmaps`) auto-wires the channels on import.
- One base gravel by default (config-selectable); optional multiple bases later for variety.
- Re-runnable offline in WSL (numpy/PIL). Records the chosen gravel base + host colour source.

### B2. Runtime assignment (`utils_loc/spalling_host_color.py`)

Replace the flat-colour material factory with a **tinted-gravel material** per host:

- `get_or_create_color_material(rgb, roughness)` → `get_or_create_host_material(host_name)`:
  resolve the host's generated texture set under `Textures/spall_host_proc/`
  (`spallhost_<hostStem>_*`); build a textured material via the existing
  `build_material_from_texture_bitmaps(find_texture_bitmaps(base_color_path), name)`; add it
  to the doc; return its basic-material index. Shared by host (one material per distinct host
  per model), cached, cleared on reset (existing `reset_material_cache`).
- Assignment to spall cavity objects is the **already-proven** per-object
  `rs.ObjectMaterialSource(oid, 1)` + `rs.ObjectMaterialIndex(oid, idx)` path (unchanged).
- Mapping host_layer → host material name uses `selected_materials[record["surface_layer"]]`
  and the raw records from `defect_placement.get_last_placed_records()` (unchanged — both
  already working from the host-colour feature).
- Fallback when a host's tinted set is missing → leave the existing curated gravel material.

`configs/spalling_host_colors.json` (host → representative colour) becomes the **input to the
offline tint step**; the runtime now selects tinted-gravel textures instead of building a flat
colour. The `roughness` config knob is superseded by the gravel's roughness map.

### Config (`preparation.spalling_host_color`)

```yaml
  spalling_host_color:
    enabled: true
    proc_texture_dir: "C:/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/spall_host_proc"
    gravel_base: "Gravel02_2K"        # base whose relief is reused (offline + record-keeping)
    # color_table_path still drives the offline tint (host representative colours)
    color_table_path: "configs/spalling_host_colors.json"
```

---

## Interaction & why both

Oblique camera (A) without relief (B) → still flat (smooth tint). Relief (B) without oblique
camera (A) → head-on hides it. Together: oblique view + micro-relief → cavity walls and
aggregate read as 3-D. They are designed and verified together.

## Error handling / fallback

- Camera: bad/empty `oblique_angle_range` → near-normal (current behaviour); never raise.
- Material: missing `proc_texture_dir` or a host's set → that spall keeps the curated gravel
  material (existing fallback); per-spall failure skips that spall; whole step in try/except
  so it never breaks the render loop. Log `spalling host-color: N recoloured, M fell back`.

## Memory / performance

- Camera: no cost change (same pose count; oblique may drop a few more to occlusion/size).
- Material: a handful of textured materials per model (one per distinct host), imported under
  the existing `material_reuse` + texture-downsampling regime — same class of cost as the
  component materials already imported. The tinted sets are generated once offline (disk:
  ~one PBR set per host under spall_host_proc).

## Testing / verification (needs a Rhino run from a FRESH session)

Offline (WSL, TDD-able):
1. Ratio-scale tint: a synthetic gravel tinted to a target colour has mean ≈ target but
   retains its luminance variance (not flattened to a solid colour).
2. Generator emits, per host, a `spallhost_<host>_BaseColor/_Normal/_Roughness` set; normal
   map is byte-identical to the gravel base (relief preserved).

In Rhino (short run, fresh session):
3. Camera: defect poses are visibly oblique (cavity walls / crack groove sides visible);
   per-model viewpoints differ; `head_on_fraction` of poses still near-normal; occlusion
   culling + size-filter still drop junk frames (no blank/occluded frames).
4. Material: `spalling host-color: N recoloured` with N>0; spall cavities show aggregate
   texture + relief tinted to the host tone (not a flat patch, not clashing gravel); masks
   still Gold/DarkOrange (labels unchanged); `basic_materials` bounded.
5. Revert: `oblique_angle_range` absent → head-on; `spalling_host_color.enabled: false` →
   old look. Both revert paths work.

## Out of scope / deferred

- Per-defect-type oblique ranges.
- Multiple gravel bases per host for extra variety (single base first).
- Any change to spall shape/depth or crack geometry.
