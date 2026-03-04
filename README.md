# rhino-defect-synth

[English](README.md) | [繁體中文](README.zh-TW.md)

Rhino Python workflow for synthetic defect generation and multi-pass rendering.

## Current Status
Implemented and wired in the main pipeline:
- `cube` modeling from contour JSON + crack geometry generation.
- `component` (bridge) modeling with configurable slab/parapet/beam/bearing/pier generation.
- Unified damage placement pipeline for `crack`, `efflore`, and `exposed_rebar` (spall + rebar).
- Camera generation for both `cube` and `component` strategies.
- Multi-pass output capture: color, depth, normal, mask, plus linear depth/normal `.pfm` channels.

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

### `main_nested.py`
Nested loop dataset generation (`model loop x render loop`).
Current limitation: only supports `modeling.strategy: cube`.

```python
import main_nested
main_nested.run(
    config_name="cube_render.yaml",
    renders_per_model=4,
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
- `preparation`, `modeling`, `rendering` require `load_config`.

## Config System
Configs live in `configs/`, loaded via `utils_loc.config.load_config(config_name)`.

`extends` is supported (for example `cube_render.yaml` extends `cube_base.yaml`).

Merge behavior:
- Top-level merge is shallow.
- Nested blocks are replaced when overridden in child config.

Component-modeling defaults:
- `utils_loc/component_modeling.py::create_bridge_component()` first loads `configs/component_defaults.yaml`.
- Then it deep-merges `modeling.component` overrides on top.
- In other words, only keys you define in `modeling.component` override the defaults.

Damage-modeling defaults:
- `utils_loc/damage_modeling.py::apply_damage_pipeline()` first loads `configs/damage_defaults.yaml`.
- Then it deep-merges `modeling.damage` overrides on top.

## Main Config Sections
### 1) `preparation`
Used by `utils_loc/pipeline.py::prepare()`:
- imports render materials
- optionally creates texture-based materials
- recreates layers and applies layer material/color assignments

### 2) `modeling`
Used by `utils_loc/pipeline.py::create_model()`.

Supported strategies:
- `strategy: cube`
  - required: `cube_map_dir`
  - optional: `start_face_index` (injected by `main.run`)
  - optional: `damage` block (unified damage placement)
- `strategy: component`
  - uses `component` block handled by `utils_loc/component_modeling.py`
  - optional: `damage` block (unified damage placement)

#### Component modeling highlights
`utils_loc/component_modeling.py::create_bridge_component()` supports:
- centerline controls (`span`, `theta`, `use_curve`, etc.)
- slab/parapet parameters
- beam section library + beam counts
- bearing and pier generation (`hammerhead` or `m_column`)
- conversion of generated polygons to surfaces
- reference-point extraction for defect placement
- optional cap: `reference_points.max_num_surfaces` (`0` = unlimited)

Useful component keys:
- `pier.anchor_indices`: explicit station indices for pier placement (supports negative indices)
- `reference_points.max_num_surfaces`: limit sampled surfaces during reference-point extraction

Returned model result includes:
- `surfaces`, `polylines`, `solids`
- `objects_by_component`
- sampled `reference_points`, `reference_sizes`, `reference_normals`

#### Unified damage modeling (`modeling.damage`)
`utils_loc/damage_modeling.py::apply_damage_pipeline()` supports:
- damage types:
  - `crack`
  - `efflore`
  - `exposed_rebar` (modeled as `spall + rebar`)
- shared shape library loading (cube contour JSON and simple polygon JSON)
- candidate generation from surfaces via:
  - `utils_loc.defect_modeling.get_surfaces`
  - `utils_loc.defect_modeling.get_reference_points`
- optional cap: `reference.max_num_surfaces` (`0` = unlimited)
- boundary-aware random scaling/orientation per candidate
- per-instance records + optional JSON export (`record_output_path`)
- camera seed extraction (`camera_defects`) for component camera strategy
- local RNG seeding (`seed`) without mutating Python global random state

`crack` generation is shared through `utils_loc/crack_modeling.py::create_crack()` and now takes configurable depth ranges/layers/cleanup.

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
- or load from damage records:
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
- If `camera.strategy=component` and config does not provide defects or record path, `pipeline.run_render()` can auto-use defects from the most recent `modeling.damage` result in-memory.

#### Mask layer controls
`rendering.outputs.mask` supports:
- `only_layers`: render mask with only selected layers visible
- `hide_layers`: hide selected layers when capturing mask

### 4) `nested_loop` (for `main_nested.py`)
Optional section to drive randomized render variants per generated cube model:
- `renders_per_model`
- `seed`
- `layer_material_choices`
- `rendering_sampler`

## Output Structure
For each camera pose, outputs are saved under:

`<output_dir>/`
- `color/<basename>.png`
- `depth/<basename>.png`
- `normal/<basename>.png`
- `mask/<basename>.png`
- `depth_buffer/<basename>.pfm`
- `normal_buffer/<basename>.pfm`

By default, `basename` is `view_XXX`. Nested runs can override basename patterns.

## Layering Notes
- Hierarchical layer paths (for example `defects::mask::crack`) are supported and auto-created.
- Damage pipeline separates:
  - geometry layers (`defects::geometry::*`)
  - mask layers (`defects::mask::*`)
  - seed markers (`defects::seeds`)
- This makes hide/show-based mask annotation capture easier during rendering.

## Example Configs
- Cube render: `configs/cube_render.yaml`
- Component render (+ optional damage pipeline): `configs/component_render.yaml`
- Component defaults (loaded by component modeling): `configs/component_defaults.yaml`
- Damage defaults (loaded by damage modeling): `configs/damage_defaults.yaml`
- Base layer/material setup:
  - `configs/cube_base.yaml`
  - `configs/component_base.yaml`

## Project Layout
- `main.py`: stage runner
- `main_nested.py`: nested model/render dataset loop (cube)
- `main_demo.py`: demo utilities
- `configs/`: YAML configs
- `utils_loc/pipeline.py`: orchestration (`prepare`, `create_model`, `run_render`, `run_render_demo`)
- `utils_loc/cube_modeling.py`: cube geometry + contour mapping
- `utils_loc/component_modeling.py`: configurable bridge component modeling
- `utils_loc/damage_shapes.py`: shared shape parsing/loading
- `utils_loc/damage_modeling.py`: unified defect placement + records
- `utils_loc/crack_modeling.py`: shared crack geometry generation
- `utils_loc/defect_modeling.py`: surface/reference-point helpers
- `utils_loc/render.py`: camera generation + render-stage orchestration
- `utils_loc/outputs.py`: color/depth/normal/mask/channel capture
- `utils_loc/layers.py`, `utils_loc/lighting.py`, `utils_loc/camera.py`: utilities
- `rhino_channels_plugin/`: Rhino command plugin for linear depth/normal export
