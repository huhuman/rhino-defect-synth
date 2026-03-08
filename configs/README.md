# Config Reference (`configs/`)

[English](README.md) | [繁體中文](README.zh-TW.md)

This document is table-first: each parameter is mapped to runtime behavior.

## Load and Priority

| Item | Runtime mechanism |
|---|---|
| `extends` | Recursively loaded and deep-merged by `utils_loc.config.load_config`. Supports `string` or `list[string]`. |
| Default composition | Use explicit `extends` to compose `*_defaults.yaml`, `*_defect_defaults.yaml`, and `*_render.yaml` into base configs. |
| Conflict priority | `current config > later extends entries > earlier extends entries`. |

## Config File Roles

| File | Purpose |
|---|---|
| `cube_base.yaml` | Cube base composition (`cube_defaults + cube_defect_defaults + cube_render`) plus `preparation`. |
| `cube_render.yaml` | Cube render/view block only. |
| `cube_render.local.yaml` | Local machine override for cube. |
| `component_base.yaml` | Component base composition (`component_defaults + component_defect_defaults + component_render`) plus `preparation`. |
| `component_render.yaml` | Component render/view block only. |
| `component.local.yaml` | Local machine override for component. |
| `cube_defaults.yaml` | Cube modeling defaults (`modeling.cube`). |
| `component_defaults.yaml` | Component modeling defaults (`modeling.component`). |
| `component_defect_defaults.yaml / cube_defect_defaults.yaml` | Defect config blocks (`modeling.defect`); runtime placement is currently component-only. |

## Top-level Parameters

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `extends` | `string | list[string]` | Parent config(s) to merge before current config. | none | Paths are resolved under `configs/`. |
| `view_setup.only_layers` | `string | list[string]` | If set, only matched layers are visible in `main.setup_render_view`. | none | Supports hierarchical matching like `a::b`. |
| `view_setup.hide_layers` | `string | list[string]` | Hides layers after visibility pass. | none | Applied in addition to `only_layers`. |
| `preparation.materials` | `dict[str,str]` | Layer-to-render-material mapping in `pipeline.prepare`. | base config | Used by `create_layers`. |
| `preparation.colors` | `dict[str,str]` | Layer color mapping in `pipeline.prepare`. | base config | Required by layer creation. |
| `preparation.texture_materials.texture_root_dir` | `string | null` | If set, imports texture materials from this folder. | none | Optional convenience. |
| `preparation.texture_materials.recursive` | `bool` | Recursive texture scan toggle. | `true` | Used only when texture root is set. |
| `modeling` | `dict` | Passed to `pipeline.create_model`. | config | Must include `strategy`. |
| `rendering` | `dict` | Passed to `pipeline.run_render`. | config | Required for render stage. |

## Modeling: Common

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `modeling.strategy` | `cube | component` | Selects branch in `pipeline.create_model`. | none | Required. |
| `modeling.defect` | `dict` | Used by component branch to run `apply_defect_pipeline`; cube branch currently ignores this block. | `component_defect_defaults.yaml` + overrides | `cube_defect_defaults.yaml` is kept for config composition/compatibility. |

## Modeling: Cube

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `modeling.cube.cube_map_dir` | `string` | Input folder for cube contour/crack maps. | `cube_defaults.yaml` | Required for cube. |
| `modeling.cube.start_face_index` | `int` | Face offset used by cube modeling. | `cube_defaults.yaml` | Can be overridden by `main.run(start_face_index=...)`. |
| `modeling.start_face_index` | `int` | Optional runtime override consumed by pipeline for cube branch. | `main.run` argument | If set, takes precedence over `modeling.cube.start_face_index`. |

## Modeling: Component (`modeling.component`)

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `seed` | `int | null` | Local RNG seed for component generation. | `component_defaults.yaml` | `null` = non-deterministic. |
| `delete_centerline_curve` | `bool` | Deletes helper centerline object after build. | `component_defaults.yaml` | Geometry cleanup toggle. |
| `convert_polygons_to_surfaces` | `bool` | Converts generated polygons to surfaces. | `component_defaults.yaml` | Affects result object types. |
| `keep_polygon_curves` | `bool` | Keeps source polyline curves when surfaces are created. | `component_defaults.yaml` | Useful for debug. |
| `centerline.*` | mixed | Controls centerline shape and station generation. | `component_defaults.yaml` | Includes `span`, `num_base_pts`, `theta*`, etc. |
| `slab.*` | mixed | Controls slab dimensions/slope. | `component_defaults.yaml` | Width/thickness/cross-slope. |
| `parapet.*` | mixed | Enables and sizes parapet profile. | `component_defaults.yaml` | `enabled` gate + profile dimensions. |
| `beam.enabled` | `bool` | Enables beam generation branch. | `component_defaults.yaml` | If disabled, downstream uses slab-based fallback points. |
| `beam.num_lines` | `int` | Number of beam lines across deck width. | `component_defaults.yaml` | Affects bearing count. |
| `beam.section_key` | `string` | Picks beam section profile. | `component_defaults.yaml` | Can be extended by `section_library_inch`. |
| `beam.section_library_inch` | `dict` | Custom beam section catalog override. | `component_defaults.yaml` | Merged with built-in catalog. |
| `bearing.*` | mixed | Builds layered bearing solids under beam lines. | `component_defaults.yaml` | Dimension/scaling controls. |
| `pier.enabled` | `bool` | Enables pier generation branch. | `component_defaults.yaml` | If false, no piers are built. |
| `pier.type` | `hammerhead | m_column` | Selects pier geometry function. | `component_defaults.yaml` | Unsupported values raise error. |
| `pier.count` | `int` | Auto-selects number of pier anchor stations. | `component_defaults.yaml` | Used only when `anchor_indices` has no valid entries. |
| `pier.anchor_indices` | `list[int] | int | null` | Explicit pier station anchors (highest priority). | `component_defaults.yaml` | Negative indices supported. |
| `pier.use_internal_stations_only` | `bool` | Limits auto candidates to internal stations when possible. | `component_defaults.yaml` | Impacts `count` behavior. |
| `pier.H / V / W` | `float` | Core pier dimensions consumed by selected pier type. | `component_defaults.yaml` | Shared by pier builders. |
| `pier.hammerhead.*` | mixed | Hammerhead-specific shape controls. | `component_defaults.yaml` | Used only when type is `hammerhead`. |
| `pier.m_column.*` | mixed | M-column-specific shape controls. | `component_defaults.yaml` | Used only when type is `m_column`. |
| `layers.{slab,parapet,beam,bearing,pier}` | `string` | Layer name mapping for generated objects. | `component_defaults.yaml` | Can use hierarchical paths. |
| `reference_points.*` | mixed | Surface sampling for defect placement seeds. | `component_defaults.yaml` | Controls extraction density and conversion behavior. |

## Modeling: Defect (`modeling.defect`)

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `enabled` | `bool` | Optional global gate for component defect placement. | `component_defect_defaults.yaml` | If false, component defect stage is skipped. |
| `seed` | `int | null` | Local RNG seed for defect placement. | `component_defect_defaults.yaml` | Isolated RNG state. |
| `record_output_path` | `string | null` | Optional JSON output path for records. | `component_defect_defaults.yaml` | Creates parent dir automatically. |
| `target_layers` | `list[str] | null` | Limits candidate surfaces by layer. | `component_defect_defaults.yaml` | `null` means no layer filtering. |
| `max_attempts_per_instance` | `int` | Retry budget per defect instance. | `component_defect_defaults.yaml` | Prevents infinite placement loops. |
| `reference.*` | mixed | Candidate point extraction controls. | `component_defect_defaults.yaml` | Includes boundary distance threshold. |
| `random.*` | mixed | Shared transform randomization bounds. | `component_defect_defaults.yaml` | Scale/orientation/margin/offset. |
| `shape_library.*` | mixed | Global fallback shape-template loading controls. | `component_defect_defaults.yaml` | Used when defect-specific overview CSV is not set. |
| `layers.seeds` | `string` | Seed marker layer. | `component_defect_defaults.yaml` | Auto-created if missing. |
| `layers.geometry.*` | `dict[str,string]` | Geometry output layers by defect type. | `component_defect_defaults.yaml` | Auto-created if missing. |
| `layers.mask.*` | `dict[str,string]` | Mask output layers by defect type. | `component_defect_defaults.yaml` | Auto-created if missing. |
| `crack.overview_csv_path` | `string | null` | Reads crack overview rows and resolves per-instance polygon JSON from `instance_mask_path`. | `component_defect_defaults.yaml` | Supports `units -> polygon` path rewrite. |
| `crack.target_width_cm` | `float` | Pixel-to-world baseline using `width_px`. | `component_defect_defaults.yaml` | Final scale also multiplies `random.scale_*`. |
| `crack.*` | mixed | Crack-specific geometry/severity controls. | `component_defect_defaults.yaml` | Includes `d1_range`, `delta_depth_range`, and CS1/CS2/CS3 mapping. |
| `efflore.*` | mixed | Component-only efflore controls. | `component_defect_defaults.yaml` | Uses CS2/CS3 only (no CS1). |
| `spalling.*` | mixed | Component-only spalling/rebar controls. | `component_defect_defaults.yaml` | Uses CS2/CS3 only (no CS1). |
| Cube defect scope | literal | Cube modeling currently generates cracks directly from six face maps and does not invoke `modeling.defect`. | `cube_defaults.yaml` | `cube_defect_defaults.yaml` is retained for compatibility/config composition. |

## Rendering Parameters

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `output_dir` | `string` | Root output directory for all rendered channels. | none | Required for render stage. |
| `width`, `height` | `int | null` | Explicit capture resolution. | viewport size | If unset, viewport dimensions are used. |
| `max_length` | `int | null` | Longest-side output constraint (preserve aspect). | none | Used when width/height are unset. |
| `background_wallpaper_dir` | `string | null` | Random wallpaper selection before rendering. | none | Optional. |
| `output_basename_pattern` | `string | null` | Python format pattern for frame naming. | none | Supports `{output_idx}`, `{model_iter}`, `{render_iter}`. |
| `output_basename_prefix` | `string | null` | Prefix naming fallback. | none | Used when pattern is unset. |
| `output_index_offset` | `int` | Adds offset to frame index. | `0` | Useful in batched runs. |
| `model_iter`, `render_iter` | any | Metadata for basename pattern substitution. | none | Optional. |
| `outputs.scene.only_layers` | `string | list[string]` | Scene visibility whitelist for color/depth/normal capture. | none | Hierarchical match supported. |
| `outputs.scene.hide_layers` | `string | list[string]` | Scene visibility blacklist for color/depth/normal capture. | none | Applied after whitelist/default pass. |
| `outputs.mask.only_layers` | `string | list[string]` | Mask visibility whitelist. | none | If set, only these layers are visible in mask pass. |
| `outputs.mask.hide_layers` | `string | list[string]` | Mask visibility blacklist. | none | Applied after mask visibility pass. |
| `lighting.sun.enabled` | `bool` | Enables sun setup call. | `true` | If disabled, sun is not reconfigured. |
| `lighting.sun.time_of_day` | `float | null` | Sun time; random when null. | random `5.0..19.0` | Passed to Rhino sun setup. |
| `lighting.sun.date/latitude/longitude/timezone/intensity/north` | mixed | Direct pass-through sun parameters. | runtime defaults | Optional. |
| `lighting.skylight.enabled` | `bool` | Enables skylight. | `true` | Passed to skylight setup. |
| `lighting.skylight.intensity` | `float` | Skylight intensity. | `0.25` | |

## Rendering Camera: Common

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `camera.strategy` | `cube | component` | Selects camera generation branch. | none | Required. |
| `camera.lens` | numeric | Passed to `set_camera`. | current view/rhino default | Optional. |
| `camera.transition_frames` | `int` | In-between frames when `smooth_path=true`. | `0` | |
| `camera.smooth_path` | `bool` | Enables interpolated capture path. | `false` | |

## Rendering Camera: Cube (`camera.cube`)

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `arrangement` | `grid | spherical` | Chooses cube camera sampler. | none | Required for cube camera. |
| `points_per_side` | `int` | Grid sampler resolution per edge. | `2` | Grid mode only. |
| `sample_count` | `int` | Number of spherical camera samples. | `24` | Spherical mode only. |
| `distance_multiplier_min/max` | `float` | Scene bbox scale multiplier range. | `1.5 / 2.5` | Order auto-corrected if min>max. |
| `sphere_angle_jitter_degrees` | `float` | Angle jitter for spherical sampling. | `0.0` | Spherical mode only. |
| `direction_jitter_degrees` | `float` | Look-direction jitter post sampling. | `10.0` | Applied by `jitter_camera_poses`. |
| `position_jitter` | `float | null` | Absolute position jitter. | none | If null, scale-based jitter is used. |
| `position_jitter_scale` | `float` | Position jitter scale vs pose spacing. | `0.25` | Used when `position_jitter` is null. |

## Rendering Camera: Component (`camera.component`)

| Parameter path | Type | Runtime mechanism | Default source | Notes |
|---|---|---|---|---|
| `defects` | `list[{point,normal}]` | Direct defect seeds for camera generation. | none | One of `defects` or record path is required. |
| `defect_record_path` | `string | null` | Loads defects from saved defect record. | none | Optional if `defects` provided. |
| `defect_types` | `list[str] | str | null` | Filters loaded defects by type. | none | Used with record-path loading. |
| `cameras_per_defect` | `int` | Number of poses per defect seed. | `1` | Minimum clamped to 1. |
| `distance_min/max` | `float` | Camera distance range from defect. | scene-scale `0.10 / 0.20` | Order auto-corrected if min>max. |
| `normal_jitter_degrees` | `float` | Normal-direction angular jitter. | `10.0` | |
| `tangent_jitter` | `float` | Tangential offset magnitude. | `0.0` | |
| `target_jitter` | `float` | Target point jitter. | `0.0` | |
| `direction_jitter_degrees` | `float` | Final look-direction jitter. | `0.0` | |
| `position_jitter` | `float | null` | Absolute pose-position jitter. | none | If null, scale-based jitter is used. |
| `position_jitter_scale` | `float` | Position jitter scale vs spacing. | `0.0` | Used when `position_jitter` is null. |

## Pipeline Fallbacks and Convenience

| Scenario | Runtime mechanism |
|---|---|
| Component camera has neither `defects` nor record path | `pipeline.run_render()` tries latest `modeling.defect.camera_defects` in memory. |
| `modeling.defect` omitted entirely | Component defect stage does not run. |
| Layer names with `::` | Treated as hierarchical layers in layer creation and matching logic. |

## Minimal runnable examples

### Cube

```yaml
extends: cube_base.yaml
modeling:
  strategy: cube
  cube:
    cube_map_dir: "C:/path/to/crack_cube_maps"
rendering:
  output_dir: "C:/path/to/out"
  camera:
    strategy: cube
    cube:
      arrangement: grid
```

### Component

```yaml
extends: component_base.yaml
modeling:
  strategy: component
  component: {}
rendering:
  output_dir: "C:/path/to/out"
  camera:
    strategy: component
    component:
      defects:
        - point: [0, 0, 0]
          normal: [0, 0, 1]
```
