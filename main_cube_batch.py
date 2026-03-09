#! python3
"""Nested modeling/render driver for multi-variant dataset generation."""

import copy
import importlib
import os
import random
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.config import load_config
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare

render = importlib.import_module("utils_loc.render")


def _reset_scene_objects():
    """Delete all objects from the active document."""
    objs = rs.AllObjects(
        select=False,
        include_lights=True,
        include_grips=True,
    )
    if objs:
        rs.DeleteObjects(objs)


def _normalize_layer_name_set(layer_names):
    if layer_names is None:
        return None
    if isinstance(layer_names, (str, bytes)):
        return {str(layer_names)}
    return {str(name) for name in layer_names if str(name).strip()}


def _layer_matches(layer, names):
    if not names:
        return False
    layer_name = getattr(layer, "Name", None)
    full_path = getattr(layer, "FullPath", None)
    if layer_name in names or full_path in names:
        return True
    if full_path:
        for name in names:
            if full_path.startswith(f"{name}::"):
                return True
        tail = full_path.split("::")[-1]
        if tail in names:
            return True
    return False


def _setup_render_view(cfg=None):
    """Set active view mode and configure layer visibility."""
    render_view = sc.doc.Views.ActiveView
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    cfg = cfg or {}
    view_setup_cfg = cfg.get("view_setup") or {}
    only_set = _normalize_layer_name_set(view_setup_cfg.get("only_layers"))
    hide_set = _normalize_layer_name_set(view_setup_cfg.get("hide_layers"))

    if not only_set and not hide_set:
        # Backward-compatible default behavior for existing batch presets.
        for layer in sc.doc.Layers:
            if layer.Name:
                layer.IsVisible = "CS" not in layer.Name
        return

    for layer in sc.doc.Layers:
        if not layer.Name:
            continue
        if only_set:
            layer.IsVisible = _layer_matches(layer, only_set)
        else:
            layer.IsVisible = True
        if hide_set and _layer_matches(layer, hide_set):
            layer.IsVisible = False


def _list_json_files(folder_path):
    if not folder_path:
        raise ValueError("modeling.cube.cube_map_dir is required.")
    if not os.path.isdir(folder_path):
        raise ValueError(f"cube_map_dir does not exist: '{folder_path}'")
    return sorted(
        filename for filename in os.listdir(folder_path) if filename.lower().endswith(".json")
    )


def _compute_model_start_indices(total_files, start_face_index, faces_per_model):
    start = max(0, int(start_face_index))
    step = max(1, int(faces_per_model))
    if step != 6:
        print(f"Warning: expected faces_per_model=6 for cube workflow, got {step}.")

    if start >= total_files:
        return [], 0

    remaining = total_files - start
    model_count = remaining // step
    stop = start + model_count * step
    leftovers = remaining - model_count * step
    return list(range(start, stop, step)), leftovers


def _sample_value(spec, rng):
    """Sample one value from a sampler spec."""
    if isinstance(spec, (list, tuple)):
        if not spec:
            raise ValueError("Sampler list cannot be empty.")
        return copy.deepcopy(rng.choice(list(spec)))

    if isinstance(spec, dict):
        if "min" in spec and "max" in spec and len(spec) <= 3:
            min_value = float(spec["min"])
            max_value = float(spec["max"])
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            sampled = rng.uniform(min_value, max_value)
            if spec.get("type") == "int":
                return int(round(sampled))
            return sampled
        return {key: _sample_value(value, rng) for key, value in spec.items()}

    return copy.deepcopy(spec)


def _deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def _sample_rendering_params(base_rendering, rendering_sampler, rng):
    params = copy.deepcopy(base_rendering or {})
    if not rendering_sampler:
        return params

    sampled_overrides = _sample_value(rendering_sampler, rng)
    if not isinstance(sampled_overrides, dict):
        raise ValueError("nested_loop.rendering_sampler must resolve to a dict.")
    return _deep_update(params, sampled_overrides)


def _count_render_frames(poses, smooth_path, transition_frames):
    pose_count = len(poses or [])
    if pose_count <= 0:
        return 0
    if bool(smooth_path) and int(transition_frames) > 0:
        return pose_count + (pose_count - 1) * int(transition_frames)
    return pose_count


def _run_render_with_frame_count(params, show_cameras=False):
    """Run render stage and return (captured_frame_count, preview_mode_used)."""
    render.setup_render_environment(params)
    context = render.build_render_context(params)
    if context is None:
        return 0, False

    render.redraw_views()
    poses = render.generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        render.preview_camera_gizmos(poses, context["lengths"])
        return 0, True

    render.capture_pose_sequence(poses, context)
    frame_count = _count_render_frames(
        poses=poses,
        smooth_path=context.get("smooth_path", False),
        transition_frames=context.get("transition_frames", 0),
    )
    return frame_count, False


def run(
    config_name="cube_render.yaml",
    renders_per_model=None,
    max_iter=None,
    start_face_index=0,
    faces_per_model=6,
    seed=None,
    show_cameras=False,
    print_timings=True,
):
    """
    Run nested loops:
      model_iter over cube face-map chunks
      render_iter over random render variants per model
    """
    stage_times = []
    timer_start = perf_counter()

    cfg = load_config(config_name)
    nested_cfg = cfg.get("nested_loop", {})

    if seed is None:
        seed = nested_cfg.get("seed")
    if seed is not None:
        rng = random.Random(int(seed))
        print(f"Random seed: {int(seed)}")
    else:
        rng = random.Random()

    if renders_per_model is None:
        renders_per_model = int(nested_cfg.get("renders_per_model", 1))
    renders_per_model = max(1, int(renders_per_model))

    if max_iter is None:
        max_iter = nested_cfg.get("max_iter")
    if max_iter is not None:
        max_iter = max(0, int(max_iter))

    rendering_sampler = nested_cfg.get("rendering_sampler", {})
    preparation_params = dict(cfg.get("preparation") or {})

    modeling_params = dict(cfg.get("modeling", {}))
    if not modeling_params:
        raise ValueError("Config must include a 'modeling' section.")
    if modeling_params.get("strategy") != "cube":
        raise ValueError("Nested runner currently supports only modeling.strategy='cube'.")
    cube_params = dict(modeling_params.get("cube") or {})
    if not cube_params:
        raise ValueError("Nested runner requires modeling.cube for cube strategy.")

    base_rendering = dict(cfg.get("rendering", {}))
    if not base_rendering:
        raise ValueError("Config must include a 'rendering' section.")

    json_files = _list_json_files(cube_params.get("cube_map_dir"))
    model_starts, leftovers = _compute_model_start_indices(
        total_files=len(json_files),
        start_face_index=start_face_index,
        faces_per_model=faces_per_model,
    )
    total_model_iters = len(model_starts)
    if max_iter is not None:
        model_starts = model_starts[:max_iter]

    if leftovers > 0:
        print(
            f"Skipping trailing {leftovers} face maps because each model iteration needs "
            f"{int(faces_per_model)} maps."
        )

    print(
        f"Nested run: models={len(model_starts)}, renders_per_model={renders_per_model}, "
        f"total_renders={len(model_starts) * renders_per_model}"
    )
    if max_iter is not None:
        print(
            f"Iteration cap: max_iter={max_iter}, available_iters={total_model_iters}, "
            f"using_iters={len(model_starts)}"
        )

    next_output_index = int(nested_cfg.get("output_index_start", 0))
    stop_after_preview = False

    for model_iter, face_idx in enumerate(model_starts):
        print(f"=== model_iter={model_iter}, start_face_index={face_idx} ===")

        time_model_stage_start = perf_counter()
        _reset_scene_objects()
        clear_imported_materials_from_doc()
        prepare(dict(preparation_params))
        _setup_render_view(cfg=cfg)

        model_params = dict(modeling_params)
        model_params["start_face_index"] = int(face_idx)
        create_model(model_params)
        stage_times.append(
            (f"reset->modeling[{model_iter}]", perf_counter() - time_model_stage_start)
        )

        for render_iter in range(renders_per_model):
            time_render_stage_start = perf_counter()
            clear_imported_materials_from_doc()
            prepare(dict(preparation_params))
            _setup_render_view(cfg=cfg)

            render_params = _sample_rendering_params(
                base_rendering=base_rendering,
                rendering_sampler=rendering_sampler,
                rng=rng,
            )
            render_params["model_iter"] = model_iter
            render_params["render_iter"] = render_iter
            render_params["output_index_offset"] = next_output_index

            captured_count, preview_used = _run_render_with_frame_count(
                params=render_params,
                show_cameras=show_cameras,
            )
            next_output_index += int(captured_count)
            if captured_count > 0:
                print(
                    f"render_iter={render_iter}: captured_views={captured_count}, "
                    f"next_view_id={next_output_index}"
                )
            stage_times.append(
                (
                    f"preparation->rendering[{model_iter},{render_iter}]",
                    perf_counter() - time_render_stage_start,
                )
            )

            if preview_used:
                # Camera preview mode intentionally exits after first render pass.
                stop_after_preview = True
                break
        if stop_after_preview:
            break

    if print_timings:
        total = perf_counter() - timer_start
        print("======== Nested Stage Timings ========")
        for name, duration in stage_times:
            print(f"{name}: {duration:.2f}s")
        print(f"total: {total:.2f}s")

    return stage_times


if __name__ == "__main__":
    run(
        config_name="cube.local.yaml",
        renders_per_model=None,
        max_iter=None,
        start_face_index=0,
        faces_per_model=6,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
