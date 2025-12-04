# rhino-defect-synth
Rhino Python scripts for parametric synthetic defect modeling and rendering. Current focus: keep the repo lightweight while separating materials, modeling, rendering, and a simple pipeline entry point.

## Layout
- `main.py` — runs the pipeline (`utils_loc.pipeline.run`).
- `utils_loc/` — all modules live here (no nested packages):
  - `materials.py` — import materials (V-Ray cosmos, local), helpers for future material creation.
  - `layers.py` — create/delete layers and assign render materials; bridge/cube layer color maps.
  - `render.py` — existing bbox/camera sweep and view capture prototype.
  - `cube_modeling.py` — JSON-driven cube face modeling utilities (contours → cutters → split).
  - `defect_modeling.py` — placeholders for defect creation (spall, rebar, efflorescence).
  - `camera.py`, `lighting.py`, `outputs.py`, `environment.py` — stubs for camera/lighting/render passes/doc setup.
  - `strategy_a.py`, `strategy_b.py` — stubs for two modeling strategies.
  - `config.py` — stubbed presets for materials, rendering, modeling.
  - `pipeline.py` — orchestrator: import materials, create layers, (future) run strategy, (future) render outputs.
  - `__init__.py` — exposes `run` from `pipeline`.

## Usage
Run from Rhino’s Python with the repo as working dir:
```python
import main  # or run main.py
```
Pipeline steps:
1) Imports materials and creates layers (bridge colors/material map).
2) (Todo) Choose a modeling strategy (`strategy_a`/`strategy_b`) and run it.
3) (Todo) Set up lighting/camera and render outputs (color/depth/normal/mask) from `outputs.py`.

## Next steps (planned)
- Implement camera/lighting/output rendering stubs.
- Implement strategy A/B and wire into `pipeline.run`.
- Flesh out `config.py` presets and document metadata/unit setup in `environment.py`.
