# Host-aware spalling colour (pure colour + roughness) — design

Date: 2026-06-25
Status: approved (design), pending implementation plan
Branch: `overnight/leak-fix-and-realism`

## Problem

Spall cavities currently take a material picked per-layer from a curated grey
exposed-aggregate/gravel list (`defect::spalling::*` → one material for ALL spalls in a
model). Even after the Tier-1a curation (removing saturated rocks, moving gravel-likes off
clean surfaces), a spall on a given host surface still gets a *random* gravel texture that
does not match the host concrete it broke out of — it reads as a foreign patch, not "this
same slab cracked open". The user wants the spall to derive its appearance from the host
surface's *selected material* so it reads as the same concrete.

After weighing realism vs asset/memory cost (we had just fixed a layer/material table
tombstone slowdown), the chosen approach is **pure colour + concrete roughness** (no
aggregate texture composite, no new texture files): the spall cavity is rendered in a colour
derived from its host material, with a matte concrete roughness. The 3D recessed geometry +
lighting already supplies form; the colour match removes the clash.

## Goal

Per-spall cavity render colour = a colour derived from that spall's host surface material,
chosen from a small per-host variant list, applied as a per-object material with concrete
roughness. **Masks/labels are unchanged** — the spall stays on its `defect::spalling` /
`defect::exposed_rebar::*::spalling` layer and the mask plugin paints the flat LAYER colour
(Gold / DarkOrange / Goldenrod / Chocolate), so the per-object render material does not
affect labels.

Non-goals: texture compositing, runtime bitmap blending, changing crack/efflore/rebar-rod
materials, changing the mask pipeline.

## Architecture

Two pieces: an **offline colour-table builder** and a **runtime post-placement assignment**.

### 1. Offline host→colour table (`tools/spalling/host_colors.py`, new)

- Input: the concrete texture set used by component surfaces (the `component::*` material
  lists in `configs/component_base.yaml`) under `texture_root_dir`.
- For each host material, load its base/albedo texture and compute a **representative
  colour** — a trimmed mean over the texture pixels (drop top/bottom luminance percentiles)
  to ignore highlight/shadow outliers.
- Derive a short **variant list** per host (the "混色列表"): e.g. `[exact, ~15% darker,
  slightly desaturated]` — broken interior concrete tends to read darker/dustier than the
  weathered face. Variant count + jitter factors are parameters.
- Output: `configs/spalling_host_colors.json` =
  `{ "<material_name>": [[r,g,b], [r,g,b], ...], ... }` (0–255 ints).
- One-time, re-runnable (regenerate when the host texture set changes). Pure offline
  numpy/PIL, runs in WSL — mirrors the efflore generator workflow.

### 2. Runtime post-placement assignment

Hook into the existing post-modeling step `_reapply_texture_mapping`
(`main_component_batch.py`), alongside `apply_spalling_texture_mapping`. New function
(in `utils_loc/texture_mapping.py` or a new `utils_loc/spalling_host_color.py`):

1. Load the colour table (cached once) + read this model's `selected_materials`
   (already available to the batch via `prepare_result["selected_materials"]`).
2. Read the cached defect records from the document (the same payload written by
   `pipeline._cache_defect_records_in_document` / `store_defect_record_payload`, read via
   the existing payload getter). Each spalling / exposed_rebar record carries
   `surface_layer` (host layer, from `_record_common`) and `spall_geometry_ids`.
3. For each spall record:
   - `host_layer = record["surface_layer"]`  (e.g. `component::slab`)
   - `host_material = selected_materials.get(host_layer)`
   - `variants = color_table.get(host_material)`; if missing → **fallback** (leave the
     existing curated gravel material untouched).
   - Pick one variant with a seeded RNG (reproducible per model/defect).
   - **get-or-create a pure-colour material keyed by the chosen colour** (base/diffuse =
     colour, roughness = configured concrete value, no colour texture). Keying by colour
     means all spalls sharing a colour share ONE material → only ~(#distinct host colours
     this model) ≈ 3–5 new materials per model.
   - Assign that material to `record["spall_geometry_ids"]` (the cavity objects). Rebar rods
     (`rebar_geometry_ids`) keep their rust material — untouched.
   - Objects stay on their defect layer → mask/label unaffected.

### Data flow

```
offline:  host textures ──▶ host_colors.py ──▶ configs/spalling_host_colors.json
runtime:  prepare() ─▶ selected_materials ┐
          create_model ─▶ cached defect records (surface_layer, spall_geometry_ids) ┐
          _reapply_texture_mapping ─▶ for each spall: host_layer→host_material→colour
                                       variant ─▶ per-colour material ─▶ cavity objects
```

## Config

Under `preparation` (component.local.yaml):

```yaml
  spalling_host_color:
    enabled: true
    color_table_path: "configs/spalling_host_colors.json"
    roughness: 0.9            # matte concrete
    # variant generation (used by the offline tool; recorded here for reproducibility)
    variants: 3
    darker_factor: 0.85
    desaturate: 0.15
```

Disabled (`enabled: false` or absent) → current behaviour (curated gravel per layer),
so the change is opt-in and fully revertible. Cube batch unaffected.

## Scope / relationship to Tier 1a

- Applies to `defect::spalling::CS2/CS3` cavity and
  `defect::exposed_rebar::CS2/CS3::spalling` cavity object ids.
- Supersedes the random gravel TEXTURE for spall cavities when a host colour is available;
  the Tier-1a curated gravel list stays as the **fallback** (host colour missing / disabled).
- Crack, efflore, and exposed-rebar rods are untouched.

## Error handling / fallback

- Missing colour table file, missing host material entry, or `enabled: false` → leave the
  spall's existing (gravel) material; never raise. Log a one-line summary
  (`spalling host-color: N cavities recoloured, M fell back`).
- Colour material creation failure for one spall → skip that spall (keep gravel), continue.

## Memory / performance

- Zero new texture files. ~3–5 pure-colour materials per model (shared by colour), created
  in the post step and cleared at reset like other materials. Negligible next to the ~17
  textured materials/model already imported. Compatible with the layer-reuse +
  material-cleanup constraints established in `project-status-2026-06`.

## Testing / verification (needs a Rhino run from a FRESH session)

1. Offline: `spalling_host_colors.json` colours are plausible concrete tones (not pure
   white/black), one entry per host material, `variants` colours per entry.
2. Short component run (max_iter ~5):
   - Spall cavities render in host-matched colour; visibly different colour for spalls on
     differently-textured components in the same model.
   - Masks still show spalling layer colours (Gold/DarkOrange/Goldenrod/Chocolate) — labels
     unchanged.
   - `basic_materials` growth bounded (no explosion from per-spall materials).
   - `enabled: false` reproduces the old gravel look (revert path works).

## Open items deferred

- Texture-composite blend (the higher-realism option) is explicitly deferred; this spec is
  the pure-colour route.
- If colour-only reads too flat in renders, a follow-up could add a shared low-contrast
  aggregate normal/bump (no colour texture) — out of scope here.
