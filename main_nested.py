#! python3
"""Nested modeling/render driver for multi-variant dataset generation."""

import copy
import os
import random
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.config import load_config
from utils_loc.pipeline import create_model, prepare, run_render


def _reset_scene_objects():
    """Delete all objects from the active document."""
    objs = rs.AllObjects(
        select=False,
        include_lights=True,
        include_grips=True,
    )
    if objs:
        rs.DeleteObjects(objs)


def _setup_render_view():
    """Set active view to Rendered mode and hide crack section layers."""
    render_view = sc.doc.Views.ActiveView
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    for layer in sc.doc.Layers:
        if layer.Name:
            layer.IsVisible = "CS" not in layer.Name


def _list_json_files(folder_path):
    if not folder_path:
        raise ValueError("modeling.cube_map_dir is required.")
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


def _find_layer(layer_name):
    layer_index = sc.doc.Layers.FindByFullPath(str(layer_name), True)
    if layer_index < 0:
        raise ValueError(f"Layer not found: '{layer_name}'")
    return sc.doc.Layers[layer_index]


def _build_material_lookup():
    return {
        str(mat.DisplayName).strip().lower(): mat
        for mat in sc.doc.RenderMaterials
        if mat.DisplayName
    }


def _pick_and_assign_layer_materials(layer_material_choices, rng):
    """
    Randomly assign one material per target layer.

    Args:
        layer_material_choices (dict): layer_name -> material_name | [material_name, ...]
        rng: random provider exposing choice(...).
    """
    if not layer_material_choices:
        return {}

    material_lookup = _build_material_lookup()
    chosen = {}
    for layer_name, material_names in layer_material_choices.items():
        options = material_names if isinstance(material_names, (list, tuple)) else [material_names]
        options = [str(name) for name in options if str(name).strip()]
        if not options:
            raise ValueError(f"No material options configured for layer '{layer_name}'.")

        selected_name = str(rng.choice(options)).strip()
        key = selected_name.lower()
        if key not in material_lookup:
            raise ValueError(
                f"Material '{selected_name}' was not found in the Rhino document. "
                "Run preparation/import first and verify exact material names."
            )
        layer = _find_layer(layer_name)
        layer.RenderMaterial = material_lookup[key]
        chosen[str(layer_name)] = selected_name
    return chosen


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


def run(
    config_name="cube_render.yaml",
    renders_per_model=None,
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

    layer_material_choices = nested_cfg.get("layer_material_choices", {})
    rendering_sampler = nested_cfg.get("rendering_sampler", {})

    modeling_params = dict(cfg.get("modeling", {}))
    if not modeling_params:
        raise ValueError("Config must include a 'modeling' section.")
    if modeling_params.get("strategy") != "cube":
        raise ValueError("Nested runner currently supports only modeling.strategy='cube'.")

    base_rendering = dict(cfg.get("rendering", {}))
    if not base_rendering:
        raise ValueError("Config must include a 'rendering' section.")

    time_prep_start = perf_counter()
    prepare(cfg.get("preparation"))
    _setup_render_view()
    stage_times.append(("preparation", perf_counter() - time_prep_start))

    json_files = _list_json_files(modeling_params.get("cube_map_dir"))
    model_starts, leftovers = _compute_model_start_indices(
        total_files=len(json_files),
        start_face_index=start_face_index,
        faces_per_model=faces_per_model,
    )
    if leftovers > 0:
        print(
            f"Skipping trailing {leftovers} face maps because each model iteration needs "
            f"{int(faces_per_model)} maps."
        )

    print(
        f"Nested run: models={len(model_starts)}, renders_per_model={renders_per_model}, "
        f"total_renders={len(model_starts) * renders_per_model}"
    )

    for model_iter, face_idx in enumerate(model_starts):
        print(f"=== model_iter={model_iter}, start_face_index={face_idx} ===")

        time_model_start = perf_counter()
        _reset_scene_objects()
        _setup_render_view()

        model_params = dict(modeling_params)
        model_params["start_face_index"] = int(face_idx)
        create_model(model_params)
        stage_times.append((f"modeling[{model_iter}]", perf_counter() - time_model_start))

        for render_iter in range(renders_per_model):
            time_render_start = perf_counter()
            assigned = _pick_and_assign_layer_materials(layer_material_choices, rng)
            render_params = _sample_rendering_params(
                base_rendering=base_rendering,
                rendering_sampler=rendering_sampler,
                rng=rng,
            )
            render_params["model_iter"] = model_iter
            render_params["render_iter"] = render_iter
            render_params["output_basename_pattern"] = f"{model_iter}-{render_iter}-{{output_idx}}"

            if assigned:
                print(
                    f"render_iter={render_iter}: layer materials -> "
                    + ", ".join(f"{k}:{v}" for k, v in sorted(assigned.items()))
                )
            run_render(params=render_params, show_cameras=show_cameras)
            stage_times.append(
                (f"rendering[{model_iter},{render_iter}]", perf_counter() - time_render_start)
            )

            if show_cameras:
                # Camera preview mode intentionally exits after first render pass.
                break
        if show_cameras:
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
        config_name="cube_render.yaml",
        renders_per_model=None,
        start_face_index=0,
        faces_per_model=6,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
