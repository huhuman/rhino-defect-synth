# rhino-defect-synth
Rhino Python workflow for synthetic defect generation and multi-pass rendering.

Current implemented path is cube-based crack modeling + configurable camera sweep rendering
(cube grid, cube spherical, and component defect-point driven) with color/depth/normal/mask outputs.

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

Nested generation script: `main_nested.py` (model loop x render loop).

```python
import main_nested
main_nested.run(
    config_name="cube_render.yaml",
    renders_per_model=4,   # optional; falls back to config nested_loop.renders_per_model
    start_face_index=0,
    faces_per_model=6,
    seed=42,
    show_cameras=False,
    print_timings=True,
)
```

Demo runner script: `main_demo.py` (material demo, lighting demo, camera-placement demo).
These helpers run only the `demo` stage by default so you can run preparation/modeling separately via `main.py`.

```python
import main_demo

main_demo.run_material_demo(
    config_name="cube_render.yaml",
    demo_params={"layer_name": "cube"},
)

main_demo.run_lighting_demo(
    config_name="cube_render.yaml",
    demo_params={
        "layer_name": "cube",
        "max_cases": 8,  # repeats lighting route; randomness comes from env/face lights per run
        # Optional face-light overrides (passed to utils_loc.lighting.setup_face_lights):
        # "face_lights": {"faces": ["+x", "+z"], "intensities": [0.5, 0.35]},
    },
)

main_demo.show_camera_placement(
    config_name="cube_render.yaml",
)

# Cleanup camera gizmos later (layer-based).
main_demo.delete_camera_models(layer_name="demo_camera_gizmos", delete_layer=True)
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
- `camera`

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
- `camera` (common):
  - `strategy`: `cube` or `component`
  - `lens` (optional)
  - `smooth_path` (bool)
  - `transition_frames` (int)

Camera placement strategies:

1) `camera.strategy: cube`
- `camera.cube.arrangement`: `grid` or `spherical`
- `camera.cube.distance_multiplier_min` / `camera.cube.distance_multiplier_max`
  - random scale applied to scene bounding-box lengths before placement
- jitter keys (both arrangements):
  - `camera.cube.direction_jitter_degrees`
  - `camera.cube.position_jitter` (absolute, optional)
  - `camera.cube.position_jitter_scale` (used when `position_jitter: null`)
- arrangement-specific keys:
  - grid:
    - `camera.cube.points_per_side` (>=2)
  - spherical:
    - `camera.cube.sample_count` (>=1)
    - `camera.cube.sphere_angle_jitter_degrees`

2) `camera.strategy: component`
- `camera.component.defects`: list of defect seeds
  - each item: `{ point: [x,y,z], normal: [nx,ny,nz] }`
- `camera.component.cameras_per_defect`: number of cameras sampled per defect
- `camera.component.distance_min` / `camera.component.distance_max`
- defect-oriented randomness:
  - `camera.component.normal_jitter_degrees`
  - `camera.component.tangent_jitter`
  - `camera.component.target_jitter`
- optional final jitter pass:
  - `camera.component.direction_jitter_degrees`
  - `camera.component.position_jitter` or `camera.component.position_jitter_scale`

Note:
- Grid placement already existed before this update (`generate_box_camera_grid`).

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
    strategy: cube
    cube:
      arrangement: grid
      points_per_side: 2
      sample_count: 24
      distance_multiplier_min: 1.5
      distance_multiplier_max: 2.5
      sphere_angle_jitter_degrees: 0.0
      direction_jitter_degrees: 25.0
      position_jitter: null
      position_jitter_scale: 0.25
    transition_frames: 5
    smooth_path: false
```

Component example (`configs/component_render.yaml`):

```yaml
extends: component_base.yaml
modeling:
  strategy: component
rendering:
  output_dir: "C:/.../component_rendered"
  camera:
    strategy: component
    component:
      cameras_per_defect: 3
      distance_min: 100.0
      distance_max: 200.0
      normal_jitter_degrees: 12.0
      tangent_jitter: 20.0
      target_jitter: 5.0
      direction_jitter_degrees: 2.0
      position_jitter: null
      position_jitter_scale: 0.0
      defects:
        - point: [0.0, 0.0, 0.0]
          normal: [0.0, 0.0, 1.0]
```

### 4) `nested_loop` (for `main_nested.py`)
Optional config section to drive nested dataset generation.

```yaml
nested_loop:
  renders_per_model: 4
  seed: 42
  layer_material_choices:
    cube: ["Concrete light", "Concrete rusty"]
    crack_extrusion: ["Concrete rusty", "Concrete light"]
  rendering_sampler:
    camera:
      cube:
        points_per_side: [2, 3]
        distance_multiplier_min: { min: 1.4, max: 1.8 }
        distance_multiplier_max: { min: 2.2, max: 2.8 }
        direction_jitter_degrees: { min: 10.0, max: 35.0 }
    lighting:
      sun:
        time_of_day: { min: 5.0, max: 19.0 }
        intensity: { min: 0.15, max: 0.5 }
      skylight:
        intensity: [0.1, 0.2, 0.35]
```

Notes:
- `main_nested.py` iterates models in chunks of 6 face maps (`start_face_index += 6`).
- Any leftover maps not forming a full chunk of 6 are skipped.
- Per render, it applies random materials from `layer_material_choices`, then samples overrides from `rendering_sampler`.
- Output basenames are formatted as `{model_iter}-{render_iter}-{output_idx}`.

## Output Structure
For each rendered camera pose, outputs are written under:

`<output_dir>/`
- `color/view_XXX.png`
- `depth/view_XXX.png`
- `normal/view_XXX.png`
- `mask/view_XXX.png`
- `depth_buffer/view_XXX.pfm`
- `normal_buffer/view_XXX.pfm`

When using `main_nested.py`, `view_XXX` is replaced by:
- `<model_iter>-<render_iter>-<output_idx>`

Notes:
- `depth_buffer` / `normal_buffer` are captured immediately after color capture in the same run.
- Channel capture uses the active Rhino view via `CaptureRenderChannels`.
- If viewport aspect ratio differs from output aspect ratio, channels can appear to cover a wider/narrower scene.

## Render Demo Utility
There is also a simplified render-demo helper that captures PNGs from the current active view:
- `utils_loc.pipeline.run_render_demo(base_out_dir, params=None)`
- backed by `utils_loc.render_demo.render_demo(...)`

Useful keys for `params`:
- `demo_type` (`camera` | `material` | `lighting`) (required)
  - `camera`: draw camera gizmos from configured camera strategy, then capture one image from the current active view (single capture)
  - `material`: iterate materials while holding lighting at the first configured values
  - `lighting`: keep the first sampled material, then for each iteration run render environment setup and `setup_face_lights(...)` before capture
- `layer_name` (required for `material` and `lighting`)
- `material_names` or `sample_material_count`
- `sun_times`, `sun_intensities`, `skylight_intensities`
  - `material`: first value of each list is used
  - `lighting`: first value of each list is reused each iteration; use `max_cases` to control iteration count while randomness comes from environment / face lights
- `face_lights` (optional): args forwarded to `utils_loc.lighting.setup_face_lights(...)` for `lighting` demo
- `camera` for `demo_type: camera`
- `background_wallpaper_dir` (optional): used by `lighting` demo via render environment setup
- `cleanup_camera_gizmos` (default `true`, clears old camera gizmos before drawing)
- `camera_gizmo_layer` (default `demo_camera_gizmos`)
- `delete_camera_gizmo_layer_on_cleanup` (default `false`)
- `width`, `height`, `max_length`
- `max_cases`, `seed`
  - `max_cases` limits material iterations, controls lighting repeat count, and is ignored by camera capture count (camera always captures one image)

Example (`lighting` demo, randomized per run with `max_cases`):

```python
from utils_loc.pipeline import run_render_demo

run_render_demo(
    base_out_dir="C:/path/to/demo_outputs/lighting_demo",
    params={
        "demo_type": "lighting",
        "layer_name": "cube",
        "sample_material_count": 3,   # first sampled material is used
        "max_cases": 12,              # number of repeated captures
        "sun_times": [17.5],          # first value reused each run
        "sun_intensities": [0.2],
        "skylight_intensities": [0.08],
        "background_wallpaper_dir": "C:/path/to/wallpapers",
        "face_lights": {
            "faces": ["+x", "-x", "+z"],
            "intensities": [0.35, 0.25, 0.45],
            "light_type": "directional",
        },
    },
)
```

Example (`camera` demo):

```python
from utils_loc.pipeline import run_render_demo

run_render_demo(
    base_out_dir="C:/path/to/demo_outputs",
    params={
        "demo_type": "camera",
        "camera": {
            "strategy": "cube",
            "cube": {
                "arrangement": "spherical",
                "sample_count": 24,
                "distance_multiplier_min": 1.5,
                "distance_multiplier_max": 2.5,
            },
        },
        # Optional capture sizing and gizmo cleanup controls:
        "max_length": 1600,
        "cleanup_camera_gizmos": True,
    },
)
```

## Project Layout
- `main.py`: stage runner.
- `main_demo.py`: demo runner for material/lighting/camera-placement sweeps.
- `configs/`: YAML configs.
- `utils_loc/pipeline.py`: orchestration (`prepare`, `create_model`, `run_render`, `run_render_demo`).
- `utils_loc/cube_modeling.py`: cube crack-map to geometry.
- `utils_loc/crack_modeling.py`: crack extrusion generation.
- `utils_loc/render.py`: camera generation, lighting setup, render orchestration.
- `utils_loc/outputs.py`: per-pass capture + channel export.
- `utils_loc/lighting.py`, `utils_loc/camera.py`: helpers.
- `rhino_channels_plugin/`: C# Rhino command plugin for linear depth/normal channels.
- `utils_loc/defect_modeling.py`, `utils_loc/environment.py`: placeholders (not production-wired yet).
