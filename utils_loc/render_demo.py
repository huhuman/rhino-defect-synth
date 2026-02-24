"""Render demo helpers extracted from render.py."""

import os
import random

import scriptcontext as sc

from utils_loc.lighting import set_skylight, setup_face_lights, setup_sun
from utils_loc.outputs import _capture_bitmap, _save_bitmap

import importlib
render_core = importlib.import_module("utils_loc.render")

SUPPORTED_DEMO_TYPES = ("camera", "material", "lighting")
_DEFAULT_FACE_LIGHT_FACES = ["+x", "-x", "+y", "-y", "+z", "-z"]


def _as_list(value, default):
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _to_optional_float(value):
    if value is None:
        return None
    return float(value)


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _sanitize_token(value):
    text = str(value)
    chars = []
    for char in text:
        if char.isalnum() or char in ("-", "_", "."):
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    safe = "".join(chars).strip("-_.")
    return safe or "na"


def _require_demo_type(params):
    demo_type = params.get("demo_type")
    if demo_type not in SUPPORTED_DEMO_TYPES:
        raise ValueError(
            "render_demo requires params['demo_type'] with one of: "
            "camera, material, lighting."
        )
    return demo_type


def _require_active_view():
    rhino_view = sc.doc.Views.ActiveView
    if rhino_view is None:
        raise ValueError("No active Rhino view available for render demo.")
    return rhino_view


def _find_layer_by_name(layer_name):
    for layer in sc.doc.Layers:
        if layer.Name == layer_name:
            return layer
    raise ValueError(f"Layer not found for render demo: '{layer_name}'")


def _resolve_demo_materials(params):
    render_materials = [mat for mat in sc.doc.RenderMaterials]
    if not render_materials:
        raise ValueError("No render materials found in the current Rhino document.")

    material_names = params.get("material_names")
    if material_names:
        names = [str(name) for name in _as_list(material_names, [])]
        selected = []
        missing = []
        for name in names:
            match = next((mat for mat in render_materials if mat.DisplayName == name), None)
            if match is None:
                missing.append(name)
            else:
                selected.append(match)
        if missing:
            raise ValueError(
                "render_demo could not find material(s): "
                + ", ".join(repr(name) for name in missing)
            )
        return selected

    count = max(1, min(int(params.get("sample_material_count", 5)), len(render_materials)))
    rng = random.Random(params.get("seed", None))
    indices = list(range(len(render_materials)))
    rng.shuffle(indices)
    return [render_materials[idx] for idx in indices[:count]]


def _resolve_common_context(base_out_dir, params, demo_type):
    os.makedirs(base_out_dir, exist_ok=True)

    lighting_cfg = params.get("lighting") or {}
    sun_cfg = lighting_cfg.get("sun") or {}
    skylight_cfg = lighting_cfg.get("skylight") or {}

    max_cases = params.get("max_cases")
    max_cases = None if max_cases is None else max(1, int(max_cases))

    return {
        "base_out_dir": base_out_dir,
        "demo_type": demo_type,
        "rhino_view": _require_active_view(),
        "width": params.get("width"),
        "height": params.get("height"),
        "max_length": params.get("max_length"),
        "max_cases": max_cases,
        "sun_cfg": sun_cfg,
        "skylight_enabled": bool(skylight_cfg.get("enabled", True)),
        "sun_times": [
            _to_optional_float(v)
            for v in _as_list(params.get("sun_times"), [sun_cfg.get("time_of_day", None)])
        ],
        "sun_intensities": [
            float(v)
            for v in _as_list(params.get("sun_intensities"), [sun_cfg.get("intensity", 1.0)])
        ],
        "skylight_intensities": [
            float(v)
            for v in _as_list(params.get("skylight_intensities"), [skylight_cfg.get("intensity", 0.25)])
        ],
    }


def _capture_current_view(context, basename):
    out_path = os.path.join(context["base_out_dir"], f"{basename}.png")
    bitmap = _capture_bitmap(
        context["rhino_view"],
        width=context["width"],
        height=context["height"],
        max_length=context["max_length"],
    )
    _save_bitmap(bitmap, out_path)
    return out_path


def _apply_basic_lighting(context, sun_time, sun_intensity, skylight_intensity):
    sun_cfg = context["sun_cfg"]
    if sun_cfg.get("enabled", True):
        setup_sun(
            time_of_day=sun_time,
            date=sun_cfg.get("date"),
            latitude=sun_cfg.get("latitude"),
            longitude=sun_cfg.get("longitude"),
            timezone=sun_cfg.get("timezone"),
            intensity=sun_intensity,
            north=sun_cfg.get("north", 0.0),
        )
    set_skylight(
        intensity=skylight_intensity,
        enabled=bool(context.get("skylight_enabled", True)),
    )


def _iter_material_cases(context, materials):
    sun_time = context["sun_times"][0]
    sun_intensity = context["sun_intensities"][0]
    skylight_intensity = context["skylight_intensities"][0]
    for idx, material in enumerate(materials):
        if context["max_cases"] is not None and idx >= context["max_cases"]:
            break
        yield idx, {
            "material": material,
            "sun_time": sun_time,
            "sun_intensity": sun_intensity,
            "skylight_intensity": skylight_intensity,
        }


def _iter_lighting_cases(context):
    repeat_count = context["max_cases"] or 1
    sun_time = context["sun_times"][0]
    sun_intensity = context["sun_intensities"][0]
    skylight_intensity = context["skylight_intensities"][0]

    for case_idx in range(repeat_count):
        yield case_idx, {
            "sun_time": sun_time,
            "sun_intensity": sun_intensity,
            "skylight_intensity": skylight_intensity,
        }


def _build_case_lighting_params(params, case):
    lighting_cfg = dict(params.get("lighting") or {})
    sun_cfg = dict(lighting_cfg.get("sun") or {})
    skylight_cfg = dict(lighting_cfg.get("skylight") or {})

    sun_cfg["time_of_day"] = case["sun_time"]
    sun_cfg["intensity"] = case["sun_intensity"]
    skylight_cfg["intensity"] = case["skylight_intensity"]

    lighting_cfg["sun"] = sun_cfg
    lighting_cfg["skylight"] = skylight_cfg

    env_params = dict(params)
    env_params["lighting"] = lighting_cfg
    return env_params


def _apply_face_lights(params):
    face_cfg = dict((params.get("face_lights") or ((params.get("lighting") or {}).get("face_lights"))) or {})
    if _to_bool(face_cfg.pop("enabled", True)) is False:
        return {}

    faces = _as_list(face_cfg.pop("faces", None), _DEFAULT_FACE_LIGHT_FACES)

    return setup_face_lights(
        bbox_pts=face_cfg.pop("bbox_pts", None),
        faces=faces,
        distance_factor=float(face_cfg.pop("distance_factor", 0.35)),
        intensities=face_cfg.pop("intensities", None),
        light_type=face_cfg.pop("light_type", "directional"),
        replace_existing=_to_bool(face_cfg.pop("replace_existing", True)),
        spot_hotspot=float(face_cfg.pop("spot_hotspot", 0.6)),
        spot_falloff=float(face_cfg.pop("spot_falloff", 55.0)),
    )


def _require_layer_for_demo(params, demo_type):
    layer_name = params.get("layer_name")
    if not layer_name:
        raise ValueError(f"{demo_type} demo requires params['layer_name'].")
    return _find_layer_by_name(layer_name)


def _resolve_camera_demo_render_params(params):
    camera_cfg = params.get("camera")
    if not camera_cfg:
        raise ValueError("render_demo camera mode requires params['camera'].")

    render_params = {"camera": camera_cfg}
    for key in ("lighting", "width", "height", "max_length"):
        if key in params:
            render_params[key] = params[key]
    return render_params


def _run_material_demo(base_out_dir, params):
    context = _resolve_common_context(base_out_dir, params, demo_type="material")
    target_layer = _require_layer_for_demo(params, "material")
    materials = _resolve_demo_materials(params)

    captured_paths = []
    for case_idx, case in _iter_material_cases(context, materials):
        target_layer.RenderMaterial = case["material"]
        _apply_basic_lighting(
            context,
            sun_time=case["sun_time"],
            sun_intensity=case["sun_intensity"],
            skylight_intensity=case["skylight_intensity"],
        )
        context["rhino_view"].Redraw()

        sun_time_tag = "random" if case["sun_time"] is None else f"{case['sun_time']:g}"
        basename = (
            f"{case_idx:04d}"
            "_demo-material"
            f"_mat-{_sanitize_token(case['material'].DisplayName)}"
            f"_sunT-{sun_time_tag}"
            f"_sunI-{case['sun_intensity']:g}"
            f"_sky-{case['skylight_intensity']:g}"
        )
        captured_paths.append(_capture_current_view(context, basename))

    print(
        "render_demo: "
        f"type=material, captured {len(captured_paths)} images "
        f"to '{context['base_out_dir']}'."
    )
    return captured_paths


def _run_lighting_demo(base_out_dir, params):
    params["background_wallpaper_dir"] = None
    context = _resolve_common_context(base_out_dir, params, demo_type="lighting")
    target_layer = _require_layer_for_demo(params, "lighting")
    materials = _resolve_demo_materials(params)
    material = materials[0]
    target_layer.RenderMaterial = material

    # Use the same environment setup used by the render stage, then layer on face lights.
    setup_env = getattr(render_core, "_setup_render_environment", None)
    if setup_env is None:
        setup_env = render_core.setup_render_environment

    captured_paths = []
    for case_idx, case in _iter_lighting_cases(context):
        setup_env(_build_case_lighting_params(params, case))
        _apply_face_lights(params)
        context["rhino_view"].Redraw()

        sun_time_tag = "random" if case["sun_time"] is None else f"{case['sun_time']:g}"
        basename = (
            f"{case_idx:04d}"
            "_demo-lighting"
            f"_mat-{_sanitize_token(material.DisplayName)}"
            f"_sunT-{sun_time_tag}"
            f"_sunI-{case['sun_intensity']:g}"
            f"_sky-{case['skylight_intensity']:g}"
        )
        captured_paths.append(_capture_current_view(context, basename))

    print(
        "render_demo: "
        f"type=lighting, captured {len(captured_paths)} images "
        f"to '{context['base_out_dir']}'."
    )
    return captured_paths


def _run_camera_demo(base_out_dir, params):
    context = _resolve_common_context(base_out_dir, params, demo_type="camera")

    cleanup_camera_gizmos = _to_bool(params.get("cleanup_camera_gizmos", True))
    camera_gizmo_layer = params.get("camera_gizmo_layer", render_core.CAMERA_GIZMO_LAYER_DEFAULT)
    delete_camera_gizmo_layer_on_cleanup = _to_bool(
        params.get("delete_camera_gizmo_layer_on_cleanup", False)
    )

    if cleanup_camera_gizmos:
        render_core.delete_camera_gizmo_layer(
            layer_name=camera_gizmo_layer,
            delete_layer=delete_camera_gizmo_layer_on_cleanup,
        )

    cam_params = _resolve_camera_demo_render_params(params)
    cam_params["output_dir"] = base_out_dir
    camera_render_context = render_core.build_render_context(cam_params)
    if camera_render_context is None:
        raise ValueError(
            "render_demo camera mode could not build camera context. "
            "Ensure scene geometry exists or provide component defects in camera config."
        )

    poses = render_core.generate_render_poses(camera_render_context)
    if poses:
        render_core.preview_camera_gizmos(
            poses,
            camera_render_context["lengths"],
            layer_name=camera_gizmo_layer,
        )
        print(
            "render_demo(camera): drew "
            f"{len(poses)} camera poses using strategy "
            f"'{camera_render_context['camera_strategy']}'."
        )
    else:
        print("render_demo(camera): no camera poses were generated.")

    context["rhino_view"].Redraw()
    basename = f"0000_demo-camera_cams-{len(poses)}"
    captured_paths = [_capture_current_view(context, basename)]

    print(
        "render_demo: "
        f"type=camera, captured {len(captured_paths)} images "
        f"to '{context['base_out_dir']}'."
    )
    return captured_paths


def render_demo(base_out_dir, params=None):
    """
    Run one render demo route and capture PNGs from the current active view.

    Params keys:
      - demo_type (str): required, one of camera | material | lighting
      - layer_name (str): required for material/lighting demos
      - material_names (list[str]) or sample_material_count (int)
      - lighting (dict): base sun/skylight config
      - sun_times (list[float|None]): demo overrides; None randomizes sun time per case
      - sun_intensities (list[float])
      - skylight_intensities (list[float])
      - face_lights (dict): optional args for utils_loc.lighting.setup_face_lights (lighting demo)
      - camera (dict): required for camera demo
      - cleanup_camera_gizmos (bool), camera_gizmo_layer (str),
        delete_camera_gizmo_layer_on_cleanup (bool)
      - width, height, max_length (int): optional capture sizing
      - max_cases (int): optional case cap (camera route always captures one image)
      - seed (int): deterministic random material sampling seed
    """
    params = params or {}
    demo_type = _require_demo_type(params)

    if demo_type == "material":
        return _run_material_demo(base_out_dir, params)
    if demo_type == "lighting":
        return _run_lighting_demo(base_out_dir, params)
    if demo_type == "camera":
        return _run_camera_demo(base_out_dir, params)

    raise ValueError(f"Unsupported render_demo demo_type '{demo_type}'.")
