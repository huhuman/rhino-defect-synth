# rhino-defect-synth
Rhino Python workflow for synthetic defect generation and multi-pass rendering.

Current implemented path is cube-based crack modeling + camera sweep rendering with color/depth/normal/mask outputs.

## Requirements
- Rhino 8 (Windows) with Python scripting enabled.
- Python modules available in Rhino:
  - `PyYAML` (used by `utils_loc/config.py`)
  - `numpy` (used by `utils_loc/cube_modeling.py`)
- `rhino_channels_plugin` from this repo if you run the `rendering` stage.
  - Current render pipeline calls `-CaptureRenderChannels` unconditionally for linear depth/normal `.pfm`.
  - Plugin build/install instructions: `rhino_channels_plugin/README.md`.
  - If you modify plugin C# code, rebuild and reload the plugin in Rhino before testing.

## Entry Point
Primary script: `main.py`.

Run from Rhino Python:

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

## Pipeline Stages
Defined in `main.py`:
- `reset`
- `load_config`
- `preparation`
- `view_setup`
- `modeling`
- `rendering`

Aliases:
- `prepare` / `prep` -> `preparation`
- `view` / `setup_view` -> `view_setup`
- `model` -> `modeling`
- `render` -> `rendering`

Dependencies:
- `preparation`, `modeling`, and `rendering` require `load_config`.

## Config System
Configs live under `configs/`.  
Loaded via `utils_loc.config.load_config(config_name)`.

`extends` is supported (example: `cube_render.yaml` extends `cube_base.yaml`).

Important merge behavior:
- Merge is **shallow** at top level.
- If child config defines `preparation`, it replaces the full `preparation` block from base (no deep merge).

## Main Config Sections
### 1) `preparation`
Used by `utils_loc/pipeline.py::prepare()`:
- imports materials from Rhino local Render Content
- recreates layers and applies optional material assignments

Example:

```yaml
preparation:
  materials:
    cube: Concrete light
    crack_extrusion: Concrete rusty
  colors:
    cube: Black
    crack_extrusion: Red
    crack_CS1: Green
    crack_CS2: Yellow
    crack_CS3: Orange
```

### 2) `modeling`
Used by `utils_loc/pipeline.py::create_model()`.

Implemented strategy:
- `strategy: cube`
- `cube_map_dir`: directory containing crack-map JSON files

`start_face_index` is injected by `main.run(...)` and shifts which 6 JSON files are used.

Expected JSON keys (from `utils_loc/cube_modeling.py`):
- `pixel_size_cm`
- `width_px` or `height_px`
- `contours`
- `severities`
- `expanded_contours`
- `base_contours`
- `difference_contours`

### 3) `rendering`
Used by `utils_loc/pipeline.py::run_render()` and `utils_loc/render.py`.

Required:
- `output_dir`
- `camera` with at least:
  - `points_per_side`
  - `distance_multiplier_min`
  - `distance_multiplier_max`

Supported keys:
- `output_dir` (str): output root.
- `background_wallpaper_dir` (str): random wallpaper source dir.
- `width` / `height` (int): explicit output size.
- `max_length` (int): longest side while preserving active view aspect ratio.
  - Applied only when `width` and `height` are not set.
- `lighting.sun`:
  - `enabled` (bool)
  - `time_of_day` (float or `null` for random 5-19)
  - `date`, `latitude`, `longitude`, `timezone`, `intensity`, `north`
- `lighting.skylight`:
  - `enabled` (bool)
  - `intensity` (float)
- `camera`:
  - `points_per_side` (int, >= 2)
  - `distance_multiplier_min` (float)
  - `distance_multiplier_max` (float)
  - `lens` (optional)
  - `direction_jitter_degrees` (float)
  - `position_jitter` (float, absolute model units; optional)
  - `position_jitter_scale` (float; used when `position_jitter` is `null`)
  - `smooth_path` (bool)
  - `transition_frames` (int)

Current default example (`configs/cube_render.yaml`):

```yaml
extends: cube_base.yaml
modeling:
  strategy: cube
  cube_map_dir: "C:/.../crack_cube_maps"
rendering:
  output_dir: "C:/.../rendered"
  max_length: 1920
  background_wallpaper_dir: "C:/.../concrete_background"
  lighting:
    sun:
      enabled: true
      time_of_day: null
      intensity: 0.25
  camera:
    points_per_side: 2
    distance_multiplier_min: 1.5
    distance_multiplier_max: 2.5
    direction_jitter_degrees: 25.0
    position_jitter: null
    position_jitter_scale: 0.25
    transition_frames: 5
    smooth_path: false
```

## Output Structure
For each rendered camera pose, outputs are written under:

`<output_dir>/`
- `color/view_XXX.png`
- `depth/view_XXX.png`
- `normal/view_XXX.png`
- `mask/view_XXX.png`
- `depth_buffer/view_XXX.pfm`
- `normal_buffer/view_XXX.pfm`

Notes:
- `depth_buffer` / `normal_buffer` are captured immediately after color capture in the same run.
- Channel capture uses the active Rhino view via `CaptureRenderChannels`.
- If viewport aspect ratio differs from output aspect ratio, channels can appear to cover a wider/narrower scene.

## Render Demo Utility
There is also a render-parameter sweep helper:
- `utils_loc.pipeline.run_render_demo(base_out_dir, params=None)`
- backed by `utils_loc.render.render_demo(...)`

Useful keys for `params`:
- `layer_name`
- `material_names` or `sample_material_count`
- `sun_times`, `sun_intensities`, `skylight_intensities`
- `use_wallpaper`, `background_wallpaper_dir`
- `width`, `height`, `max_length`
- `max_cases`, `seed`

## Project Layout
- `main.py`: stage runner.
- `configs/`: YAML configs.
- `utils_loc/pipeline.py`: orchestration (`prepare`, `create_model`, `run_render`, `run_render_demo`).
- `utils_loc/cube_modeling.py`: cube crack-map to geometry.
- `utils_loc/crack_modeling.py`: crack extrusion generation.
- `utils_loc/render.py`: camera generation, lighting setup, render orchestration.
- `utils_loc/outputs.py`: per-pass capture + channel export.
- `utils_loc/lighting.py`, `utils_loc/camera.py`: helpers.
- `rhino_channels_plugin/`: C# Rhino command plugin for linear depth/normal channels.
- `utils_loc/defect_modeling.py`, `utils_loc/environment.py`: placeholders (not production-wired yet).

## Troubleshooting
### `AttributeError: 'function' object has no attribute ...` for `render`
Rhino may be using stale module state from an older session.  
Restart Rhino (or reload modules) after refactors that rename exports/imports.

### `CaptureRenderChannels command failed`
Plugin is not loaded or command unavailable. Build/install `rhino_channels_plugin` and retry.

### `depth_buffer` / `normal_buffer` looks wider than color
This is usually an aspect-ratio mismatch between:
- live viewport size (camera frustum source for plugin sampling), and
- configured output size (`rendering.width`/`rendering.height`).

Fix:
- Make viewport ratio match output ratio before running, or
- use `rendering.max_length` only (lets output follow viewport ratio).

The pipeline also prints a warning when viewport/output aspect ratios differ.

### After plugin code changes, output still looks old
Rhino is still using an old plugin build.

Fix:
1. Rebuild `rhino_channels_plugin`.
2. Reload/reinstall the new plugin DLL in Rhino.
3. Re-run the pipeline.

### PFM appears mirrored/upside-down in custom loaders
PFM orientation depends on writer/reader conventions.

For this plugin output, validate orientation against the color image and adjust loader transforms (`flipud`/`fliplr`) accordingly.

### Camera randomness seems too small
Increase in config:
- `rendering.camera.direction_jitter_degrees`
- `rendering.camera.position_jitter` or `position_jitter_scale`
