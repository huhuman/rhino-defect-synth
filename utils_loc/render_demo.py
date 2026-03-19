"""Render demo helpers extracted from render.py."""

import math
import os
import random

import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.lighting import set_skylight, setup_face_lights, setup_sun
from utils_loc.outputs import _capture_bitmap, _save_bitmap

import importlib
render_core = importlib.import_module("utils_loc.render")

SUPPORTED_DEMO_TYPES = ("camera", "material", "lighting")
_DEFAULT_FACE_LIGHT_FACES = ["+x", "-x", "+y", "-y", "+z", "-z"]
_SUPPORTED_CAMERA_DEMO_MODES = ("show", "capture")


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


def _to_non_negative_int(value, default=0):
    if value is None:
        return max(0, int(default))
    return max(0, int(value))


def _normalize_vec3(vec):
    x, y, z = (float(v) for v in vec)
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-12:
        return 0.0, 0.0, 1.0
    inv = 1.0 / length
    return x * inv, y * inv, z * inv


def _lerp_vec3(a, b, t):
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * float(t),
        float(a[1]) + (float(b[1]) - float(a[1])) * float(t),
        float(a[2]) + (float(b[2]) - float(a[2])) * float(t),
    )


def _distance_vec3(a, b):
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    dz = float(b[2]) - float(a[2])
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _distance_sq_vec3(a, b):
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    dz = float(b[2]) - float(a[2])
    return dx * dx + dy * dy + dz * dz


def _is_layer_or_sublayer(layer_name, prefix):
    if not layer_name or not prefix:
        return False
    if layer_name == prefix:
        return True
    return str(layer_name).startswith(str(prefix) + "::")


def _collect_scene_object_ids(exclude_layer_prefixes=None):
    obj_ids = rs.AllObjects(
        select=False,
        include_lights=False,
        include_grips=False,
    ) or []
    if not exclude_layer_prefixes:
        return list(obj_ids)

    prefixes = [str(name).strip() for name in exclude_layer_prefixes if str(name).strip()]
    if not prefixes:
        return list(obj_ids)

    filtered_ids = []
    for obj_id in obj_ids:
        layer_name = rs.ObjectLayer(obj_id) or ""
        if any(_is_layer_or_sublayer(layer_name, prefix) for prefix in prefixes):
            continue
        filtered_ids.append(obj_id)
    return filtered_ids


def _compute_aabb_from_objects(object_ids):
    if not object_ids:
        return None
    bbox_pts = rs.BoundingBox(object_ids)
    if not bbox_pts:
        return None

    xs = [float(pt.X) for pt in bbox_pts]
    ys = [float(pt.Y) for pt in bbox_pts]
    zs = [float(pt.Z) for pt in bbox_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    center = (
        (max_x + min_x) * 0.5,
        (max_y + min_y) * 0.5,
        (max_z + min_z) * 0.5,
    )
    lengths = (
        max(max_x - min_x, 1e-6),
        max(max_y - min_y, 1e-6),
        max(max_z - min_z, 1e-6),
    )
    return {
        "center": center,
        "lengths": lengths,
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "min_z": min_z,
        "max_z": max_z,
    }


def _sort_vertices_clockwise(vertices, center_xy, start_vertex=None):
    cx, cy = (float(center_xy[0]), float(center_xy[1]))
    ordered = sorted(
        vertices,
        key=lambda pt: math.atan2(float(pt[1]) - cy, float(pt[0]) - cx),
        reverse=True,
    )
    if not ordered or start_vertex is None:
        return ordered

    start_idx = min(
        range(len(ordered)),
        key=lambda idx: _distance_sq_vec3(ordered[idx], start_vertex),
    )
    return ordered[start_idx:] + ordered[:start_idx]


def _pose_from_position(position, target):
    pos = tuple(float(v) for v in position)
    tgt = tuple(float(v) for v in target)
    direction = _normalize_vec3((tgt[0] - pos[0], tgt[1] - pos[1], tgt[2] - pos[2]))
    return {
        "position": pos,
        "target": tgt,
        "direction": direction,
    }


def _resolve_camera_demo_mode(params, camera_cfg):
    raw_mode = params.get("camera_demo_mode")
    if raw_mode is None and isinstance(camera_cfg, dict):
        raw_mode = camera_cfg.get("demo_mode")
    if raw_mode is None and isinstance(camera_cfg, dict):
        raw_mode = (camera_cfg.get("component") or {}).get("demo_mode")

    mode = str(raw_mode or "show").strip().lower()
    aliases = {
        "preview": "show",
        "show_camera": "show",
        "render": "capture",
    }
    mode = aliases.get(mode, mode)
    if mode not in _SUPPORTED_CAMERA_DEMO_MODES:
        raise ValueError(
            "camera demo mode must be one of "
            f"{_SUPPORTED_CAMERA_DEMO_MODES}, got '{raw_mode}'."
        )
    return mode


def _component_demo_cfg(camera_cfg):
    component_cfg = camera_cfg.get("component")
    if isinstance(component_cfg, dict):
        return component_cfg
    return {}


def _resolve_component_demo_value(camera_cfg, key, default=None, aliases=None):
    aliases = list(aliases or [])
    component_cfg = _component_demo_cfg(camera_cfg)
    for candidate in [key] + aliases:
        if candidate in component_cfg:
            return component_cfg[candidate]
    for candidate in [key] + aliases:
        if candidate in camera_cfg:
            return camera_cfg[candidate]
    return default


def _scale_vertices_about_center(vertices, center, scale_xy):
    cx, cy, cz = (float(v) for v in center)
    factor = max(float(scale_xy), 1e-6)
    scaled = []
    for vx, vy, vz in vertices:
        scaled.append(
            (
                cx + (float(vx) - cx) * factor,
                cy + (float(vy) - cy) * factor,
                cz + (float(vz) - cz),
            )
        )
    return scaled


def _resolve_component_mid_slice_path(camera_cfg, camera_gizmo_layer):
    object_ids = _collect_scene_object_ids(exclude_layer_prefixes=[camera_gizmo_layer])
    aabb = _compute_aabb_from_objects(object_ids)
    if aabb is None:
        raise ValueError("render_demo camera mode could not find any scene geometry for AABB path.")

    center = aabb["center"]
    mid_z = center[2]
    raw_vertices = [
        (aabb["min_x"], aabb["min_y"], mid_z),
        (aabb["max_x"], aabb["min_y"], mid_z),
        (aabb["max_x"], aabb["max_y"], mid_z),
        (aabb["min_x"], aabb["max_y"], mid_z),
    ]
    vertices = _sort_vertices_clockwise(
        raw_vertices,
        center_xy=(center[0], center[1]),
        start_vertex=raw_vertices[0],
    )
    bbox_scale = float(
        _resolve_component_demo_value(
            camera_cfg,
            "bbox_scale",
            default=1.5,
            aliases=["bbox_scale_xy", "path_bbox_scale"],
        )
    )
    vertices = _scale_vertices_about_center(vertices, center=center, scale_xy=bbox_scale)

    smooth = _to_bool(
        _resolve_component_demo_value(
            camera_cfg,
            "smooth_path",
            default=False,
            aliases=["smooth"],
        )
    )
    base_transition = _to_non_negative_int(
        _resolve_component_demo_value(
            camera_cfg,
            "transition_frames",
            default=0,
            aliases=["transition_frame"],
        ),
        default=0,
    )
    loop_vertices = list(vertices)
    if len(loop_vertices) > 1:
        loop_vertices.append(loop_vertices[0])

    if not smooth or base_transition <= 0:
        return {
            "aabb": aabb,
            "vertices": vertices,
            "poses": [_pose_from_position(vertex, center) for vertex in loop_vertices],
            "smooth_path": smooth,
            "base_transition": base_transition,
            "segment_transition_frames": [],
            "bbox_scale": bbox_scale,
        }

    min_side = max(min(aabb["lengths"][0], aabb["lengths"][1]), 1e-6)
    segment_transition_frames = []
    poses = [_pose_from_position(loop_vertices[0], center)]

    for idx in range(len(loop_vertices) - 1):
        start = loop_vertices[idx]
        end = loop_vertices[idx + 1]
        edge_len = _distance_vec3(start, end)
        step_count = int(math.ceil(float(base_transition) / min_side * edge_len))
        step_count = max(0, step_count)
        segment_transition_frames.append(step_count)

        for step in range(1, step_count + 1):
            t = step / float(step_count + 1)
            poses.append(_pose_from_position(_lerp_vec3(start, end, t), center))
        poses.append(_pose_from_position(end, center))

    return {
        "aabb": aabb,
        "vertices": vertices,
        "poses": poses,
        "smooth_path": smooth,
        "base_transition": base_transition,
        "segment_transition_frames": segment_transition_frames,
        "bbox_scale": bbox_scale,
    }


def _build_component_defect_demo_context(base_out_dir, params, camera_cfg):
    render_params = _resolve_camera_demo_render_params(params)
    render_params["output_dir"] = base_out_dir
    render_params["camera"] = dict(camera_cfg or {})
    try:
        return render_core.build_render_context(render_params)
    except ValueError as exc:
        message = str(exc)
        if "requires camera.component.defects" in message:
            print(
                "render_demo(camera): component defect seeds not found; "
                "using bbox path only."
            )
            return None
        raise


def _build_camera_capture_context(base_out_dir, params, camera_cfg):
    outputs_cfg = params.get("outputs") or {}
    scene_cfg = outputs_cfg.get("scene") or {}
    mask_cfg = outputs_cfg.get("mask") or {}
    return {
        "base_out_dir": base_out_dir,
        "lens": camera_cfg.get("lens"),
        "width": params.get("width"),
        "height": params.get("height"),
        "max_length": params.get("max_length"),
        "match_viewport_aspect": bool(params.get("match_viewport_aspect", True)),
        "output_basename_pattern": params.get("output_basename_pattern"),
        "output_basename_prefix": params.get("output_basename_prefix"),
        "output_index_offset": _to_non_negative_int(params.get("output_index_offset", 0)),
        "model_iter": params.get("model_iter"),
        "render_iter": params.get("render_iter"),
        "scene_only_layers": scene_cfg.get("only_layers"),
        "scene_hide_layers": scene_cfg.get("hide_layers"),
        "mask_only_layers": mask_cfg.get("only_layers"),
        "mask_hide_layers": mask_cfg.get("hide_layers"),
        "smooth_path": False,
        "transition_frames": 0,
    }


def _build_capture_basename_fallback(output_idx, context):
    idx = int(output_idx) + int(context.get("output_index_offset", 0))
    pattern = context.get("output_basename_pattern")
    if pattern:
        return str(pattern).format(
            output_idx=idx,
            model_iter=context.get("model_iter"),
            render_iter=context.get("render_iter"),
        )

    prefix = context.get("output_basename_prefix")
    if prefix is not None:
        return f"{prefix}{idx}"
    return f"view_{idx:03d}"


def _predict_capture_color_paths(context, frame_count):
    basename_builder = getattr(render_core, "_build_capture_basename", None)
    predicted = []
    for idx in range(int(frame_count)):
        if basename_builder:
            basename = basename_builder(idx, context)
        else:
            basename = _build_capture_basename_fallback(idx, context)
        predicted.append(os.path.abspath(os.path.join(context["base_out_dir"], f"color/{basename}.png")))
    return predicted


def _predict_capture_frame_count(poses, context):
    pose_count = len(poses or [])
    if pose_count <= 1:
        return pose_count
    if not bool(context.get("smooth_path")):
        return pose_count
    transition_frames = _to_non_negative_int(context.get("transition_frames", 0))
    if transition_frames <= 0:
        return pose_count
    return pose_count + (pose_count - 1) * transition_frames


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
    for key in (
        "lighting",
        "background_wallpaper_dir",
        "width",
        "height",
        "max_length",
        "match_viewport_aspect",
        "outputs",
        "output_basename_pattern",
        "output_basename_prefix",
        "output_index_offset",
        "model_iter",
        "render_iter",
    ):
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

    camera_cfg = dict(params.get("camera") or {})
    if not camera_cfg:
        raise ValueError("render_demo camera mode requires params['camera'].")

    camera_demo_mode = _resolve_camera_demo_mode(params, camera_cfg)
    camera_strategy = str(camera_cfg.get("strategy") or "").strip().lower()

    setup_env = getattr(render_core, "_setup_render_environment", None)
    if setup_env is None:
        setup_env = render_core.setup_render_environment

    poses = []
    lengths_for_gizmos = (1.0, 1.0, 1.0)
    capture_context = None
    strategy_tag = camera_strategy or "unknown"

    if camera_strategy == "component":
        component_path = _resolve_component_mid_slice_path(camera_cfg, camera_gizmo_layer)
        bbox_path_poses = list(component_path["poses"])
        poses = list(bbox_path_poses)
        lengths_for_gizmos = component_path["aabb"]["lengths"]
        capture_context = _build_camera_capture_context(base_out_dir, params, camera_cfg)
        strategy_tag = "component-aabb-mid-slice"

        defect_render_context = _build_component_defect_demo_context(
            base_out_dir,
            params,
            camera_cfg,
        )
        defect_poses = []
        if defect_render_context is not None:
            defect_poses = list(render_core.generate_render_poses(defect_render_context) or [])
            capture_context = defect_render_context
            capture_context["smooth_path"] = False
            capture_context["transition_frames"] = 0
            poses.extend(defect_poses)
            if defect_poses:
                strategy_tag = "component-aabb-mid-slice-plus-defects"

        if component_path["smooth_path"]:
            print(
                "render_demo(camera): component path interpolation "
                f"base_transition={component_path['base_transition']}, "
                f"per_segment={component_path['segment_transition_frames']}."
            )
        print(
            "render_demo(camera): component bbox path "
            f"bbox_scale={component_path['bbox_scale']:g}, "
            f"corner_count={len(component_path['vertices'])}, "
            f"pose_count={len(bbox_path_poses)}."
        )
        if defect_poses:
            print(
                "render_demo(camera): component defect camera poses "
                f"count={len(defect_poses)}, "
                f"combined_pose_count={len(poses)}."
            )

    else:
        cam_params = _resolve_camera_demo_render_params(params)
        cam_params["output_dir"] = base_out_dir
        camera_render_context = render_core.build_render_context(cam_params)
        if camera_render_context is None:
            raise ValueError(
                "render_demo camera mode could not build camera context. "
                "Ensure scene geometry exists or provide component defects in camera config."
            )
        poses = render_core.generate_render_poses(camera_render_context)
        lengths_for_gizmos = camera_render_context["lengths"]
        capture_context = camera_render_context
        strategy_tag = camera_render_context["camera_strategy"]

    if camera_demo_mode == "show":
        if poses:
            render_core.preview_camera_gizmos(
                poses,
                lengths_for_gizmos,
                layer_name=camera_gizmo_layer,
            )
            print(
                "render_demo(camera): drew "
                f"{len(poses)} camera poses using strategy '{strategy_tag}'."
            )
        else:
            print("render_demo(camera): no camera poses were generated.")

        context["rhino_view"].Redraw()
        basename = f"0000_demo-camera_{_sanitize_token(strategy_tag)}_cams-{len(poses)}"
        captured_paths = [_capture_current_view(context, basename)]
        print(
            "render_demo: "
            f"type=camera, mode=show, captured {len(captured_paths)} images "
            f"to '{context['base_out_dir']}'."
        )
        return captured_paths

    if not poses:
        print("render_demo(camera): no camera poses were generated; skipping capture.")
        return []

    env_params = _resolve_camera_demo_render_params(params)
    setup_env(env_params)
    render_core.redraw_views()

    render_core.capture_pose_sequence(poses, capture_context)
    expected_frame_count = _predict_capture_frame_count(poses, capture_context)
    captured_paths = _predict_capture_color_paths(capture_context, expected_frame_count)
    print(
        "render_demo: "
        f"type=camera, mode=capture, captured {len(captured_paths)} frames "
        f"to '{context['base_out_dir']}' using strategy '{strategy_tag}'."
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
      - camera_demo_mode (str): camera route mode: show | capture
      - camera.component.smooth_path / transition_frames: component bbox-path interpolation
      - camera.component.bbox_scale: XY scale factor applied to the scene bbox path (default 1.5)
      - camera.component.defects or cached Rhino defect metadata: optional defect-focused poses
      - cleanup_camera_gizmos (bool), camera_gizmo_layer (str),
        delete_camera_gizmo_layer_on_cleanup (bool)
      - width, height, max_length (int): optional capture sizing
      - outputs/match_viewport_aspect/output_basename_*: optional capture overrides
      - max_cases (int): optional case cap (material/lighting demos)
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
