#! python3
"""Entry point for render demos only (material, lighting, camera placement)."""

import os
from time import perf_counter

from utils_loc.config import load_config
from utils_loc.logging_utils import install_timestamped_print
from utils_loc.pipeline import run_render_demo

install_timestamped_print()

import importlib
render = importlib.import_module("utils_loc.render")

DEMO_TYPES = ("material", "lighting", "camera")


def _resolve_demo_out_dir(cfg, demo_type, base_out_dir=None):
    if base_out_dir:
        return base_out_dir
    rendering_cfg = cfg.get("rendering", {})
    output_root = rendering_cfg.get("output_dir")
    if not output_root:
        raise ValueError("Config rendering.output_dir is required when base_out_dir is not provided.")
    return os.path.join(output_root, "demo", cfg["modeling"]["strategy"], demo_type)


def _build_demo_params(cfg, demo_type, demo_params=None):
    rendering_cfg = cfg.get("rendering", {})
    params = {"demo_type": demo_type}

    for key in ("lighting", "background_wallpaper_dir", "width", "height", "max_length"):
        if key in rendering_cfg:
            params[key] = rendering_cfg[key]

    if "face_lights" in rendering_cfg:
        params["face_lights"] = rendering_cfg["face_lights"]

    if demo_type == "camera":
        camera_cfg = rendering_cfg.get("camera")
        if not camera_cfg:
            raise ValueError("Config rendering.camera is required for camera demo.")
        params["camera"] = camera_cfg
        params["camera_gizmo_layer"] = render.CAMERA_GIZMO_LAYER_DEFAULT

    if demo_params:
        params.update(demo_params)

    if demo_type in ("material", "lighting") and "layer_name" not in params:
        raise ValueError(
            f"{demo_type} demo requires demo_params['layer_name'] to be set explicitly."
        )
    return params


def run(
    demo_type,
    config_name="cube_render.yaml",
    base_out_dir=None,
    demo_params=None,
    print_timings=True,
):
    """Run one demo type (demo stage only)."""
    if demo_type not in DEMO_TYPES:
        raise ValueError(f"demo_type must be one of {DEMO_TYPES}, got '{demo_type}'.")

    stage_times = []

    start = perf_counter()
    cfg = load_config(config_name)
    stage_times.append(("load_config", perf_counter() - start))

    demo_out_dir = _resolve_demo_out_dir(cfg, demo_type=demo_type, base_out_dir=base_out_dir)
    os.makedirs(demo_out_dir, exist_ok=True)
    params = _build_demo_params(cfg, demo_type=demo_type, demo_params=demo_params)

    start = perf_counter()
    demo_outputs = run_render_demo(base_out_dir=demo_out_dir, params=params)
    stage_times.append(("demo", perf_counter() - start))

    if print_timings:
        total = 0.0
        print("======== Demo Stage Timings ========")
        for name, duration in stage_times:
            total += duration
            print(f"{name}: {duration:.2f}s")
        print(f"total: {total:.2f}s")

    return {
        "stage_times": stage_times,
        "demo_outputs": demo_outputs,
    }


def run_material_demo(**kwargs):
    """Run material-iteration demo."""
    return run(demo_type="material", **kwargs)


def run_lighting_demo(**kwargs):
    """Run lighting-iteration demo (render env + face lights per case)."""
    return run(demo_type="lighting", **kwargs)


def show_camera_placement(clear_existing=True, delete_layer=True, **kwargs):
    """Run camera-placement visualization demo.

    Args:
        clear_existing: delete old camera gizmos before running.
        delete_layer: when clearing, also delete the gizmo layer itself first.
    """
    if clear_existing:
        demo_params = kwargs.get("demo_params") or {}
        layer_name = demo_params.get("camera_gizmo_layer", render.CAMERA_GIZMO_LAYER_DEFAULT)
        delete_camera_models(layer_name=layer_name, delete_layer=delete_layer)

    return run(demo_type="camera", **kwargs)


def delete_camera_models(layer_name=render.CAMERA_GIZMO_LAYER_DEFAULT, delete_layer=True):
    """Delete all camera gizmo objects from a dedicated layer."""
    deleted_count = render.delete_camera_gizmo_layer(layer_name=layer_name, delete_layer=delete_layer)
    print(
        f"delete_camera_models: deleted {deleted_count} objects "
        f"from layer '{layer_name}' (delete_layer={bool(delete_layer)})."
    )
    return deleted_count


if __name__ == "__main__":
    demo_params = {
        "layer_name": "cube",
        "max_cases": 5,
    }

    # Example usage: material demo.
    # run_material_demo(
    #     config_name="cube_render.local.yaml",
    #     demo_params=demo_params,
    #     print_timings=True,
    # )

    # Example usage: lighting demo (iterates render env, then setup_face_lights).
    # run_lighting_demo(
    #     config_name="cube_render.local.yaml",
    #     demo_params=demo_params,
    #     print_timings=True,
    # )

    # Example usage: camera placement demo.
    # show_camera_placement(
    #     config_name="cube_render.local.yaml",
    #     demo_params={},
    #     print_timings=True,
    # )
    
    # Example usage: capture for component drone-like camera setup.
    show_camera_placement(
        config_name="component.local.yaml",
        demo_params={
            "camera_demo_mode": "show",
        },
        print_timings=True,
    )
