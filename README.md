# rhino-defect-synth

[English](README.md) | [繁體中文](README.zh-TW.md)

Rhino Python workflow for synthetic defect generation and multi-pass rendering.

## Current Status
Implemented and wired in the main pipeline:
- `cube` modeling from contour JSON + crack geometry generation.
- `component` (bridge) modeling with configurable slab/parapet/beam/bearing/pier generation.
- Unified defect placement pipeline for `crack`, `efflore`, and `exposed_rebar` (spall + rebar).
- Camera generation for both `cube` and `component` strategies.
- Multi-pass output capture: color, depth, normal, mask, plus linear depth/normal `.pfm` channels.

## Batch Stability Improvements
The current `main_cube_batch.py` path has been hardened for long-running Rhino sessions:
- heavy non-render document operations now suspend redraw and use a lighter working display mode before switching back to `Rendered` for capture
- batch runs can temporarily disable Rhino autosave and undo recording, then restore both settings on exit
- each timestamped batch folder now records both `batch_log.txt` and `batch_state.json`
- `batch_state.json` records current progress plus a safe resume point at completed model boundaries
- stability guards can stop early on memory/material/pass-count thresholds instead of letting Rhino drift into a hard crash
- nested-loop `seed` now drives batch-level random choices consistently within that Rhino run

## Requirements
- Rhino 8 (Windows) with Python scripting enabled.
- Python modules available in Rhino:
  - `PyYAML` (used by `utils_loc/config.py`)
  - `numpy` (used by cube modeling utilities)
- `rhino_channels_plugin` from this repo if you want linear depth/normal channel export:
  - `depth_buffer/*.pfm`
  - `normal_buffer/*.pfm`

Plugin instructions: `rhino_channels_plugin/README.md`.

## Entry Points
### `main.py`
Stage-based runner:

```python
import main
main.run(
    config_name="cube_render.yaml",
    stages=["load_config", "preparation", "view_setup", "modeling", "rendering"],
    skip=[],
    start_face_index=0,
    show_cameras=False,
    print_timings=True,
)
```

### `main_cube_batch.py`
Nested loop dataset generation (`model loop x render loop`).
Current limitation: only supports `modeling.strategy: cube`.

```python
import main_cube_batch
main_cube_batch.run(
    config_name="cube_render.yaml",
    renders_per_model=4,
    max_iter=3,
    start_face_index=0,
    faces_per_model=6,
    seed=42,
    show_cameras=False,
    print_timings=True,
)
```

### `main_demo.py`
Demo-only helpers for material, lighting, and camera placement visualization.

## Pipeline Stages (`main.py`)
Defined order:
- `reset`
- `load_config`
- `preparation`
- `view_setup`
- `modeling`
- `rendering`

Dependencies:
- `preparation`, `view_setup`, `modeling`, `rendering` require `load_config`.

## Config System
Configs live in `configs/`, loaded via `utils_loc.config.load_config(config_name)`.

`extends` supports both string and list:
- `extends: cube_base.yaml`
- `extends: [cube_defaults.yaml, cube_defect_defaults.yaml, cube_render.yaml]`

Merge behavior:
- `extends` merge is recursive (deep merge).
- Conflict priority is: current config > later extends entries > earlier extends entries.
- `load_config()` does not inject defaults automatically; defaults are composed explicitly via `extends`.

## Main Config Sections
### 1) `preparation`
Used by `utils_loc/pipeline.py::prepare()`:
- imports render materials
- optionally creates texture-based materials
- recreates layers and applies layer material/color assignments
- batch runs can temporarily disable Rhino autosave and undo recording via `preparation.autosave.disable_during_batch` and `preparation.undo.disable_during_batch`

### 2) `modeling`
Used by `utils_loc/pipeline.py::create_model()`.

Supported strategies:
- `strategy: cube`
  - required: `cube.cube_map_dir`
  - optional: `start_face_index` (injected by `main.run`)
  - crack geometry is generated directly from six face maps (no secondary defect-placement stage)
- `strategy: component`
  - uses `component` block handled by `utils_loc/component_modeling.py`
  - optional: `defect` block (unified defect placement)
  - optional: `debug` block (debug drawing controls)

#### Component modeling highlights
`utils_loc/component_modeling.py::create_bridge_component()` supports:
- centerline controls (`span`, `theta`, `use_curve`, etc.)
- slab/parapet parameters
- beam section library + beam counts
- bearing and pier generation (`hammerhead` or `m_column`)
- conversion of generated polygons to surfaces

Useful component keys:
- `pier.anchor_indices`: explicit station indices for pier placement (supports negative indices)

Returned model result includes:
- `surfaces`, `polylines`, `solids`
- `objects_by_component`

`modeling.debug` controls debug drawing:
- `surface_normals`: component surface normal arrows (`debug::normal::component::*`)
- `defect_normals`: defect modeling normal arrows (`debug::normal::*`)
- `defect_seeds`: defect seed markers (`debug::seed::*`)

#### Unified defect modeling (`modeling.defect`, component branch)
`utils_loc/defect_placement.py::apply_defect_pipeline()` supports:
- defect types:
  - `crack`
  - `efflore`
  - `exposed_rebar` (modeled as `spall + rebar`)
- condition-state notes:
  - `crack`: CS1/CS2/CS3
  - `efflore` and `spalling`: CS2/CS3 only
- shared shape library loading (cube contour JSON and simple polygon JSON)
- candidate generation from surfaces via:
  - `utils_loc.defect_modeling.get_surfaces`
  - `utils_loc.defect_modeling.get_reference_points`
- optional cap: `reference.max_num_surfaces` (`0` = unlimited)
- boundary-aware random scaling/orientation per candidate
- per-instance records + optional JSON export (`record_output_path`)
  - `records`/`summary` count only successfully generated defects (non-empty geometry)
- camera seed extraction (`camera_defects`) for component camera strategy
  - each item includes `point`, `normal`, `defect_type`, `instance_index`
- debug drawing is controlled by `modeling.debug` (not `modeling.defect`)
- local RNG seeding (`seed`) without mutating Python global random state
- shape-library parsing:
  - `file_format=auto` now detects cube vs simple JSON before parsing
  - cube contour arrays must have consistent lengths; mismatches raise explicit errors
  - shared point-set parsing is centralized in `utils_loc.defect_shapes.extract_point_sets()` (also used by `utils_loc.defect_modeling.py`)

`crack` generation is shared through `utils_loc/crack_modeling.py::create_crack()` and takes configurable depth ranges/layers/cleanup. It validates both `base_poly` and `offset_poly`, and applies cleanup in failure paths when `cleanup_inputs=True`.

### 3) `rendering`
Used by `utils_loc/pipeline.py::run_render()` and `utils_loc/render.py`.

Required:
- `output_dir`
- `camera`

Common keys:
- `width` / `height`
- `max_length` (when width/height not explicitly set)
- `background_wallpaper_dir`
- `lighting.sun` / `lighting.skylight`
- `camera.strategy`: `cube` or `component`
- `camera.lens`
- `camera.smooth_path`
- `camera.transition_frames`

#### Camera strategy: `cube`
- `camera.cube.arrangement`: `grid` or `spherical`
- `distance_multiplier_min` / `distance_multiplier_max`
- jitter controls:
  - `direction_jitter_degrees`
  - `position_jitter` or `position_jitter_scale`
- arrangement-specific:
  - grid: `points_per_side`
  - spherical: `sample_count`, `sphere_angle_jitter_degrees`

#### Camera strategy: `component`
- direct seed list:
  - `camera.component.defects: [{point: [x,y,z], normal: [nx,ny,nz]}, ...]`
- or load from defect records:
  - `camera.component.defect_record_path`
  - optional `camera.component.defect_types`
- sampling controls:
  - `cameras_per_defect`
  - `distance_min` / `distance_max`
  - `normal_jitter_degrees`
  - `tangent_jitter`
  - `target_jitter`
  - optional final jitter: `direction_jitter_degrees`, `position_jitter`, `position_jitter_scale`

Pipeline behavior:
- If `camera.strategy=component` and config does not provide defects or record path, `pipeline.run_render()` can auto-use defects from the most recent `modeling.defect` result in-memory.

#### Mask layer controls
`rendering.outputs.mask` supports:
- `only_layers`: render mask with only selected layers visible
- `hide_layers`: hide selected layers when capturing mask

### 4) `nested_loop` (for `main_cube_batch.py`)
Optional section to control batch iterations and per-model render variants:
- `renders_per_model`
- `camera_arrangements` (`grid` / `spherical`, supports list; runs full prep+render per arrangement)
- `max_iter` (caps model iterations; actual iterations = `min(max_iter, available_iters)`)
- `output_index_start` (starting index for `view_XXX` naming)
- `seed`
- `rendering_sampler`
- `stability.*` (wait/retry/GC/undo/memory guards)

Batch flow in `main_cube_batch.py`:
- Per model iteration: `reset -> preparation -> modeling`, with redraw suspended during heavy non-render document mutations.
- Per render iteration: one or more `clear_imported_materials_from_doc -> preparation -> view_setup -> rendering`
  passes (count controlled by `camera_arrangements`)
- Batch mode writes both `batch_log.txt` and `batch_state.json` inside the timestamped output folder.
- `batch_state.json` tracks current progress and a safe resume point at completed model boundaries; if a guard trips mid-model, the safe resume index intentionally points to the next known-clean model boundary.
- Nested-loop seed now drives batch-level random choices consistently across camera/lighting/render sampling within that Rhino run.
- Render view indices are continuous across iterations via `output_index_offset`, preventing filename overwrite.

## Output Structure
For each camera pose, outputs are saved under:

`<output_dir>/`
- `color/<basename>.png`
- `depth/<basename>.png`
- `normal/<basename>.png`
- `mask/<basename>.png`
- `depth_buffer/<basename>.pfm`
- `normal_buffer/<basename>.pfm`

For `main_cube_batch.py`, the timestamped run directory also contains:
- `batch_log.txt`
- `batch_state.json`

By default, `basename` is `view_XXX`. In batch mode, indices continue across iterations to avoid overwrite.

## Layering Notes
- Hierarchical layer paths (for example `defects::mask::crack`) are supported and auto-created.
- Cube workflow currently uses strict preparation-defined layers:
  - `cube::face`
  - `crack::CS1`, `crack::CS2`, `crack::CS3`
- In cube mode, mask-surface preparation code is kept in `utils_loc/cube_modeling.py` but intentionally disabled (commented) for now.
- Defect pipeline separates:
  - geometry layers (`defects::geometry::*`)
  - mask layers (`defects::mask::*`)
  - debug layers (`debug::normal::*`, `debug::seed::*`)
- This makes hide/show-based mask annotation capture easier during rendering.

## Example Configs
- Cube local (recommended runtime entry): `configs/cube.local.yaml`
- Component local (recommended runtime entry): `configs/component.local.yaml`
- Render blocks: `configs/cube_render.yaml`, `configs/component_render.yaml`
- Modeling defaults: `configs/cube_defaults.yaml`, `configs/component_defaults.yaml`
- Defect defaults: `configs/component_defect_defaults.yaml`
- Cube defect defaults (kept for config composition/compatibility): `configs/cube_defect_defaults.yaml`
- Base composition + preparation: `configs/cube_base.yaml`, `configs/component_base.yaml`

## Project Layout
- `main.py`: stage runner
- `main_cube_batch.py`: nested model/render dataset loop (cube)
- `main_demo.py`: demo utilities
- `configs/`: YAML configs
- `utils_loc/pipeline.py`: orchestration (`prepare`, `create_model`, `run_render`, `run_render_demo`)
- `utils_loc/cube_modeling.py`: cube geometry + contour mapping
- `utils_loc/component_modeling.py`: configurable bridge component modeling
- `utils_loc/defect_shapes.py`: shared shape parsing/loading
- `utils_loc/defect_placement.py`: unified defect placement + records
- `utils_loc/crack_modeling.py`: shared crack geometry generation
- `utils_loc/defect_modeling.py`: surface/reference-point helpers
- `utils_loc/render.py`: camera generation + render-stage orchestration
- `utils_loc/outputs.py`: color/depth/normal/mask/channel capture
- `utils_loc/layers.py`, `utils_loc/lighting.py`, `utils_loc/camera.py`: utilities
- `rhino_channels_plugin/`: Rhino command plugin for linear depth/normal export
