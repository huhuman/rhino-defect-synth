"""Render stage helpers used by the pipeline orchestrator."""

import gc
import math
import os
import random

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.camera import (
    generate_box_camera_spherical,
    generate_box_camera_grid,
    generate_defect_camera_poses,
    jitter_camera_poses,
    set_camera,
    sort_poses_topdown_circular,
)
from utils_loc.lighting import (
    create_targeted_light,
    delete_named_lights,
    random_natural_light_color,
    set_random_wallpaper,
    set_skylight,
    setup_sun,
)
from utils_loc.defect_placement import defects_from_record_payload
from utils_loc.defect_record_store import load_defect_record_payload_from_document
from utils_loc.outputs import render_all_outputs

CAMERA_GIZMO_LAYER_DEFAULT = "demo_camera_gizmos"
_COMPONENT_RENDER_LAYER_PREFIXES = ("component", "defect")
_HELPER_LIGHT_PREFIXES = ("face_light", "defect_light")


def _to_non_negative_int(value, default=0):
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(0, parsed)


def _run_capture_gc():
    try:
        gc.collect()
    except Exception:
        pass
    try:
        import System

        System.GC.Collect()
        wait_fn = getattr(System.GC, "WaitForPendingFinalizers", None)
        if callable(wait_fn):
            wait_fn()
    except Exception:
        pass


def _stabilize_after_capture_frame(frame_idx_1based, context):
    wait_after_frame_ms = _to_non_negative_int(context.get("capture_wait_after_frame_ms", 0))
    gc_every_frames = _to_non_negative_int(context.get("capture_gc_every_frames", 0))

    try:
        Rhino.RhinoApp.Wait()
    except Exception:
        pass
    if wait_after_frame_ms > 0:
        rs.Sleep(wait_after_frame_ms)
    if gc_every_frames > 0 and int(frame_idx_1based) % gc_every_frames == 0:
        _run_capture_gc()


def _setup_render_environment(params):
    """Set wallpaper and lighting before generating renders."""
    delete_named_lights(_HELPER_LIGHT_PREFIXES)

    if params.get("background_wallpaper_dir"):
        set_random_wallpaper(params["background_wallpaper_dir"])

    lighting_cfg = params.get("lighting", {})
    sun_cfg = lighting_cfg.get("sun", {})
    if sun_cfg.get("enabled", True):
        setup_sun(
            time_of_day=sun_cfg.get("time_of_day", random.uniform(5.0, 19.0)),
            date=sun_cfg.get("date"),
            latitude=sun_cfg.get("latitude"),
            longitude=sun_cfg.get("longitude"),
            timezone=sun_cfg.get("timezone"),
            intensity=sun_cfg.get("intensity", 1.0),
            north=sun_cfg.get("north", 0.0),
        )

    skylight_cfg = lighting_cfg.get("skylight", {})
    set_skylight(
        intensity=float(skylight_cfg.get("intensity", 0.25)),
        enabled=bool(skylight_cfg.get("enabled", True)),
    )


def _coerce_vec3(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a 3-item list/tuple.")
    return tuple(float(v) for v in value)


def _safe_lengths(lengths, min_value):
    min_len = float(min_value)
    return tuple(max(abs(float(length)), min_len) for length in lengths)


def _bbox_center_lengths():
    obj_ids = rs.AllObjects(
        select=False,
        include_lights=False,
        include_grips=False,
    )
    if not obj_ids:
        return None

    bbox_pts = rs.BoundingBox(obj_ids)
    if not bbox_pts:
        return None

    xs = [pt.X for pt in bbox_pts]
    ys = [pt.Y for pt in bbox_pts]
    zs = [pt.Z for pt in bbox_pts]
    center = (
        (max(xs) + min(xs)) * 0.5,
        (max(ys) + min(ys)) * 0.5,
        (max(zs) + min(zs)) * 0.5,
    )
    lengths = _safe_lengths(
        (
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ),
        min_value=1e-3,
    )
    return {"center": center, "lengths": lengths}


def _center_lengths_from_points(points, min_length=1.0):
    if not points:
        return (0.0, 0.0, 0.0), (float(min_length), float(min_length), float(min_length))

    xs = [float(pt[0]) for pt in points]
    ys = [float(pt[1]) for pt in points]
    zs = [float(pt[2]) for pt in points]
    center = (
        (max(xs) + min(xs)) * 0.5,
        (max(ys) + min(ys)) * 0.5,
        (max(zs) + min(zs)) * 0.5,
    )
    lengths = _safe_lengths(
        (
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ),
        min_value=min_length,
    )
    return center, lengths


def _normalize_camera_strategy(camera_cfg):
    if "strategy" not in camera_cfg:
        raise ValueError("camera.strategy is required and must be 'cube' or 'component'.")
    strategy = camera_cfg["strategy"]
    if strategy not in ("cube", "component"):
        raise ValueError(
            f"Unsupported camera.strategy='{strategy}'. "
            "Expected exactly one of: cube, component."
        )
    return strategy


def _normalize_cube_camera_cfg(camera_cfg):
    cube_cfg = camera_cfg.get("cube")
    if not isinstance(cube_cfg, dict):
        raise ValueError("camera.cube is required and must be a dict when camera.strategy='cube'.")
    if "arrangement" not in cube_cfg:
        raise ValueError(
            "camera.cube.arrangement is required and must be 'grid' or 'spherical'."
        )
    arrangement = cube_cfg["arrangement"]
    if arrangement not in ("grid", "spherical"):
        raise ValueError(
            f"Unsupported cube camera arrangement='{arrangement}'. "
            "Expected exactly one of: grid, spherical."
        )
    return dict(cube_cfg)


def _normalize_component_camera_cfg(camera_cfg):
    component_cfg = camera_cfg.get("component")
    if not isinstance(component_cfg, dict):
        raise ValueError(
            "camera.component is required and must be a dict when camera.strategy='component'."
        )
    return dict(component_cfg)


def _load_defects_from_document(include_defect_types=None):
    payload = load_defect_record_payload_from_document()
    if not payload:
        return []
    return defects_from_record_payload(payload, include_defect_types=include_defect_types)


def _normalize_defects(raw_defects):
    if raw_defects is None:
        return []
    if not isinstance(raw_defects, (list, tuple)):
        raise ValueError("camera.component.defects must be a list.")

    defects = []
    for idx, defect in enumerate(raw_defects):
        if not isinstance(defect, dict):
            raise ValueError(
                "Each defect entry must be a dict with keys: point, normal."
            )
        point_raw = defect.get("point")
        normal_raw = defect.get("normal")

        if point_raw is None or normal_raw is None:
            raise ValueError(
                f"Defect entry #{idx} must include both 'point' and 'normal'."
            )

        normalized = {
            "point": _coerce_vec3(point_raw, f"defects[{idx}].point"),
            "normal": _coerce_vec3(normal_raw, f"defects[{idx}].normal"),
        }
        for key, value in defect.items():
            if key not in ("point", "normal"):
                normalized[key] = value
        defects.append(normalized)

    return defects


def _normalize_component_defect_light_cfg(lighting_cfg):
    cfg_raw = (lighting_cfg or {}).get("defect_lights")
    if not isinstance(cfg_raw, dict):
        return {"enabled": False}
    cfg = dict(cfg_raw)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "light_type": str(cfg.get("light_type") or "point").strip().lower() or "point",
        "intensity": float(cfg.get("intensity", 0.5)),
        "spot_hotspot": float(cfg.get("spot_hotspot", 0.6)),
        "spot_falloff": float(cfg.get("spot_falloff", 55.0)),
    }


def _build_component_pose_light(pose, light_cfg):
    if not light_cfg.get("enabled", False):
        return None
    return {
        "position": tuple(float(v) for v in pose["position"]),
        "target": tuple(float(v) for v in pose.get("target", pose["position"])),
        "light_type": light_cfg.get("light_type", "point"),
        "intensity": float(light_cfg.get("intensity", 0.5)),
        "color": random_natural_light_color(),
        "name": "defect_light",
        "spot_hotspot": float(light_cfg.get("spot_hotspot", 0.6)),
        "spot_falloff": float(light_cfg.get("spot_falloff", 55.0)),
    }


def _apply_pose_light(pose):
    light = pose.get("light")
    if not isinstance(light, dict):
        return None

    delete_named_lights(light.get("name", "defect_light"))
    return create_targeted_light(
        position=light["position"],
        target=light.get("target"),
        light_type=light.get("light_type", "point"),
        intensity=light.get("intensity", 0.5),
        color=light.get("color"),
        name=light.get("name"),
        spot_hotspot=light.get("spot_hotspot", 0.6),
        spot_falloff=light.get("spot_falloff", 55.0),
    )


def _resolve_component_defects(component_camera_cfg):
    include_defect_types = component_camera_cfg.get("defect_types")

    defects = _normalize_defects(component_camera_cfg.get("defects"))
    if defects:
        print(
            "Component render: using {} inline defect seeds from config.".format(
                len(defects),
            )
        )
        return defects

    defects = _normalize_defects(
        _load_defects_from_document(include_defect_types=include_defect_types)
    )
    if defects:
        print(
            "Component render: loaded {} defect seeds from Rhino document metadata.".format(
                len(defects),
            )
        )
    return defects


def _resolve_position_jitter(cfg, spacing, default_scale=0.25):
    position_jitter_override = cfg.get("position_jitter")
    if position_jitter_override is None:
        position_jitter_scale = float(cfg.get("position_jitter_scale", default_scale))
        return max(0.0, float(spacing) * position_jitter_scale)
    return max(0.0, float(position_jitter_override))


def _default_component_render_layers(layer_names):
    if layer_names is None:
        return list(_COMPONENT_RENDER_LAYER_PREFIXES)
    if isinstance(layer_names, (list, tuple)):
        return list(layer_names)
    return layer_names


def _build_render_context(params):
    """Collect scene/camera information required for rendering."""
    camera_cfg = params["camera"]
    strategy = _normalize_camera_strategy(camera_cfg)
    outputs_cfg = params.get("outputs") or {}
    mask_cfg = outputs_cfg.get("mask") or {}
    scene_cfg = outputs_cfg.get("scene") or {}
    channel_cfg = params.get("channel") or {}
    lighting_cfg = params.get("lighting") or {}
    bbox_data = _bbox_center_lengths()
    cube_camera_cfg = {}
    component_camera_cfg = {}
    defects = []

    if strategy == "cube":
        if bbox_data is None:
            print("No geometry found for cube camera path generation; skipping render.")
            return None

        cube_camera_cfg = _normalize_cube_camera_cfg(camera_cfg)
        distance_multiplier_min = float(
            cube_camera_cfg.get("distance_multiplier_min", 1.5)
        )
        distance_multiplier_max = float(
            cube_camera_cfg.get("distance_multiplier_max", 2.5)
        )
        if distance_multiplier_min > distance_multiplier_max:
            distance_multiplier_min, distance_multiplier_max = (
                distance_multiplier_max,
                distance_multiplier_min,
            )
        multiple = random.uniform(distance_multiplier_min, distance_multiplier_max)
        center = bbox_data["center"]
        lengths = tuple(length * multiple for length in bbox_data["lengths"])

    else:
        component_camera_cfg = _normalize_component_camera_cfg(camera_cfg)
        defects = _resolve_component_defects(component_camera_cfg)
        if not defects:
            raise ValueError(
                "camera.strategy='component' requires camera.component.defects "
                "(list of point/normal entries) or cached defect metadata in the Rhino document."
            )

        if bbox_data is None:
            center, lengths = _center_lengths_from_points(
                [item["point"] for item in defects],
                min_length=1.0,
            )
        else:
            center = bbox_data["center"]
            lengths = bbox_data["lengths"]

    base_out_dir = params["output_dir"]
    os.makedirs(base_out_dir, exist_ok=True)

    if strategy == "component":
        scene_only_layers = _default_component_render_layers(scene_cfg.get("only_layers"))
        mask_only_layers = _default_component_render_layers(mask_cfg.get("only_layers"))
    else:
        scene_only_layers = scene_cfg.get("only_layers")
        mask_only_layers = mask_cfg.get("only_layers")

    return {
        "camera_strategy": strategy,
        "center": center,
        "lengths": lengths,
        "camera_cfg": camera_cfg,
        "cube_camera_cfg": cube_camera_cfg,
        "component_camera_cfg": component_camera_cfg,
        "defects": defects,
        "base_out_dir": base_out_dir,
        "lens": camera_cfg.get("lens"),
        "transition_frames": int(camera_cfg.get("transition_frames", 0)),
        "smooth_path": bool(camera_cfg.get("smooth_path", False)),
        "width": params.get("width"),
        "height": params.get("height"),
        "max_length": params.get("max_length"),
        "match_viewport_aspect": bool(params.get("match_viewport_aspect", True)),
        "output_basename_pattern": params.get("output_basename_pattern"),
        "output_basename_prefix": params.get("output_basename_prefix"),
        "output_index_offset": int(params.get("output_index_offset", 0)),
        "model_iter": params.get("model_iter"),
        "render_iter": params.get("render_iter"),
        "scene_only_layers": scene_only_layers,
        "scene_hide_layers": scene_cfg.get("hide_layers"),
        "mask_only_layers": mask_only_layers,
        "mask_hide_layers": mask_cfg.get("hide_layers"),
        "channels": dict(channel_cfg) if isinstance(channel_cfg, dict) else {},
        "capture_gc_every_frames": _to_non_negative_int(
            params.get("capture_gc_every_frames", 0)
        ),
        "capture_wait_after_frame_ms": _to_non_negative_int(
            params.get("capture_wait_after_frame_ms", 0)
        ),
        "defect_light_cfg": _normalize_component_defect_light_cfg(lighting_cfg)
        if strategy == "component"
        else {"enabled": False},
    }


def _generate_render_poses(context):
    """Generate and order camera poses around the scene."""
    center = context["center"]
    lengths = context["lengths"]
    camera_strategy = context["camera_strategy"]

    if camera_strategy == "cube":
        cube_camera_cfg = context["cube_camera_cfg"]
        arrangement = cube_camera_cfg["arrangement"]

        if arrangement == "grid":
            points_per_side = max(2, int(cube_camera_cfg.get("points_per_side", 2)))
            poses = generate_box_camera_grid(center, lengths, points_per_side)
            spacing = min(lengths) / float(points_per_side - 1)
        elif arrangement == "spherical":
            sample_count = max(1, int(cube_camera_cfg.get("sample_count", 24)))
            poses = generate_box_camera_spherical(
                center,
                lengths,
                sample_count,
                angle_jitter_degrees=float(
                    cube_camera_cfg.get("sphere_angle_jitter_degrees", 0.0)
                ),
            )
            radius = 0.5 * math.sqrt(
                lengths[0] * lengths[0]
                + lengths[1] * lengths[1]
                + lengths[2] * lengths[2]
            )
            spacing = math.sqrt((4.0 * math.pi * radius * radius) / float(sample_count))
        else:
            raise ValueError(f"Unsupported cube camera arrangement: {arrangement}")

        position_jitter = _resolve_position_jitter(
            cube_camera_cfg,
            spacing,
            default_scale=0.25,
        )
        direction_jitter_degrees = float(
            cube_camera_cfg.get("direction_jitter_degrees", 10.0)
        )
        poses = jitter_camera_poses(
            poses,
            position_jitter=position_jitter,
            direction_jitter_degrees=direction_jitter_degrees,
        )
        return sort_poses_topdown_circular(poses, center=center)

    component_cfg = context["component_camera_cfg"]
    defects = context["defects"]
    default_radius_min = 120.0
    default_radius_max = 220.0
    radius_min = float(
        component_cfg.get(
            "radius_min",
            component_cfg.get("distance_min", default_radius_min),
        )
    )
    radius_max = float(
        component_cfg.get(
            "radius_max",
            component_cfg.get("distance_max", default_radius_max),
        )
    )
    if radius_min > radius_max:
        radius_min, radius_max = radius_max, radius_min

    poses = generate_defect_camera_poses(
        defects=defects,
        cameras_per_defect=max(1, int(component_cfg.get("cameras_per_defect", 1))),
        radius_min=radius_min,
        radius_max=radius_max,
        target_jitter=float(component_cfg.get("target_jitter", 0.0)),
    )

    spacing = max(abs(radius_max - radius_min), 0.5 * (radius_min + radius_max), 1e-3)
    position_jitter = _resolve_position_jitter(
        component_cfg,
        spacing,
        default_scale=0.0,
    )
    direction_jitter_degrees = float(component_cfg.get("direction_jitter_degrees", 0.0))
    poses = jitter_camera_poses(
        poses,
        position_jitter=position_jitter,
        direction_jitter_degrees=direction_jitter_degrees,
    )
    light_cfg = context.get("defect_light_cfg") or {}
    if light_cfg.get("enabled", False):
        poses = [
            dict(pose, light=_build_component_pose_light(pose, light_cfg))
            for pose in poses
        ]
    return sort_poses_topdown_circular(poses, center=center)


def _normalize_vec(vec):
    x, y, z = (float(v) for v in vec)
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return 0.0, 0.0, 1.0
    inv = 1.0 / length
    return x * inv, y * inv, z * inv


def _dot(a, b):
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a, b):
    ax, ay, az = (float(v) for v in a)
    bx, by, bz = (float(v) for v in b)
    return (
        ay * bz - az * by,
        az * bx - ax * bz,
        ax * by - ay * bx,
    )


def _camera_basis(dir_vec):
    up_guess = (0.0, 0.0, 1.0)
    if abs(_dot(dir_vec, up_guess)) > 0.9:
        up_guess = (0.0, 1.0, 0.0)
    right = _normalize_vec(_cross(up_guess, dir_vec))
    true_up = _normalize_vec(_cross(dir_vec, right))
    return right, true_up


def _ensure_layer(layer_name):
    if not layer_name:
        return None
    if rs.IsLayer(layer_name):
        return layer_name
    created = rs.AddLayer(layer_name)
    return created or layer_name


def _add_camera_gizmo(idx, pose, scale, layer_name=None):
    pos = tuple(float(v) for v in pose["position"])
    tgt = pose.get("target")
    dir_vec = pose.get("direction")
    if dir_vec is None and tgt is not None:
        dir_vec = tuple(tgt[i] - pos[i] for i in range(3))
    dir_vec = _normalize_vec(dir_vec or (1.0, 0.0, 0.0))

    right, up_vec = _camera_basis(dir_vec)
    body_len = scale * 1.4
    tip = tuple(pos[i] + dir_vec[i] * body_len for i in range(3))
    base_center = tuple(pos[i] - dir_vec[i] * scale * 0.35 for i in range(3))
    half_w = scale * 0.35

    base_corners = [
        tuple(base_center[i] + right[i] * half_w + up_vec[i] * half_w for i in range(3)),
        tuple(base_center[i] - right[i] * half_w + up_vec[i] * half_w for i in range(3)),
        tuple(base_center[i] - right[i] * half_w - up_vec[i] * half_w for i in range(3)),
        tuple(base_center[i] + right[i] * half_w - up_vec[i] * half_w for i in range(3)),
    ]

    obj_ids = []
    poly_id = rs.AddPolyline(base_corners + [base_corners[0]])
    if poly_id:
        obj_ids.append(poly_id)
    for corner in base_corners:
        line_id = rs.AddLine(corner, tip)
        if line_id:
            obj_ids.append(line_id)
    dot_id = rs.AddTextDot(f"cam {idx}", pos)
    if dot_id:
        obj_ids.append(dot_id)
    if tgt:
        look_line_id = rs.AddLine(pos, tgt)
        if look_line_id:
            obj_ids.append(look_line_id)

    if layer_name:
        for obj_id in obj_ids:
            if rs.IsObject(obj_id):
                rs.ObjectLayer(obj_id, layer_name)

    return obj_ids


def _preview_camera_gizmos(poses, lengths, layer_name=CAMERA_GIZMO_LAYER_DEFAULT):
    """Draw camera gizmos in Rhino for visual debugging."""
    use_layer = _ensure_layer(layer_name)
    gizmo_scale = max(min(lengths), 1e-3) * 0.08
    created_ids = []
    for idx, pose in enumerate(poses):
        created_ids.extend(_add_camera_gizmo(idx, pose, gizmo_scale, layer_name=use_layer))
    rs.Redraw()
    return created_ids


def delete_camera_gizmo_layer(layer_name=CAMERA_GIZMO_LAYER_DEFAULT, delete_layer=True):
    """Delete camera gizmo objects created on the given layer."""
    if not layer_name or not rs.IsLayer(layer_name):
        return 0

    obj_ids = rs.ObjectsByLayer(layer_name, select=False) or []
    if obj_ids:
        rs.DeleteObjects(obj_ids)

    if delete_layer and rs.IsLayer(layer_name):
        try:
            rs.DeleteLayer(layer_name)
        except Exception:
            pass

    rs.Redraw()
    return len(obj_ids)


def _build_capture_basename(output_idx, context):
    idx = int(output_idx) + int(context.get("output_index_offset", 0))
    pattern = context.get("output_basename_pattern")
    if pattern:
        try:
            return str(pattern).format(
                output_idx=idx,
                model_iter=context.get("model_iter"),
                render_iter=context.get("render_iter"),
            )
        except Exception as exc:
            raise ValueError(
                f"Invalid output_basename_pattern='{pattern}'. "
                "Expected Python format placeholders such as {output_idx}."
            ) from exc

    prefix = context.get("output_basename_prefix")
    if prefix is not None:
        return f"{prefix}{idx}"
    return f"view_{idx:03d}"


def _capture_pose(idx, pose, context):
    _apply_pose_light(pose)
    set_camera(position=pose["position"], target=pose["target"], lens=context["lens"])
    basename = _build_capture_basename(idx, context)
    render_all_outputs(
        out_dir=context["base_out_dir"],
        basename=basename,
        width=context["width"],
        height=context["height"],
        max_length=context["max_length"],
        match_viewport_aspect=context.get("match_viewport_aspect", True),
        scene_only_layers=context.get("scene_only_layers"),
        scene_hide_layers=context.get("scene_hide_layers"),
        mask_only_layers=context.get("mask_only_layers"),
        mask_hide_layers=context.get("mask_hide_layers"),
        channels=context.get("channels"),
    )


def _capture_pose_sequence(poses, context):
    """Capture all frames based on smooth/direct path settings."""
    smooth_path = context["smooth_path"]
    transition_frames = context["transition_frames"]

    try:
        if smooth_path and transition_frames > 0:
            frame_idx = 0
            for i, pose in enumerate(poses[:-1]):
                next_pose = poses[i + 1]
                _capture_pose(frame_idx, pose, context)
                _stabilize_after_capture_frame(frame_idx + 1, context)
                frame_idx += 1

                for step in range(1, transition_frames + 1):
                    t = step / float(transition_frames + 1)
                    interp_pos = (
                        pose["position"][0] + (next_pose["position"][0] - pose["position"][0]) * t,
                        pose["position"][1] + (next_pose["position"][1] - pose["position"][1]) * t,
                        pose["position"][2] + (next_pose["position"][2] - pose["position"][2]) * t,
                    )
                    interp_pose = {
                        "position": interp_pos,
                        "target": pose["target"],
                        "direction": pose.get("direction"),
                        "light": pose.get("light"),
                    }
                    _capture_pose(frame_idx, interp_pose, context)
                    _stabilize_after_capture_frame(frame_idx + 1, context)
                    frame_idx += 1

            _capture_pose(frame_idx, poses[-1], context)
            _stabilize_after_capture_frame(frame_idx + 1, context)
            return

        for idx, pose in enumerate(poses):
            _capture_pose(idx, pose, context)
            _stabilize_after_capture_frame(idx + 1, context)
    finally:
        delete_named_lights("defect_light")


def setup_render_environment(params):
    """Public helper for render stage environment setup."""
    return _setup_render_environment(params)


def build_render_context(params):
    """Public helper for collecting camera/output context."""
    return _build_render_context(params)


def generate_render_poses(context):
    """Public helper for camera pose generation."""
    return _generate_render_poses(context)


def preview_camera_gizmos(poses, lengths, layer_name=CAMERA_GIZMO_LAYER_DEFAULT):
    """Public helper for drawing camera previews."""
    return _preview_camera_gizmos(poses, lengths, layer_name=layer_name)


def capture_pose_sequence(poses, context):
    """Public helper for final render capture loop."""
    return _capture_pose_sequence(poses, context)


def redraw_views():
    """Public helper for Rhino view redraw."""
    sc.doc.Views.Redraw()


def render(params, show_cameras=False):
    """Run the full render stage from env setup to output capture."""
    setup_render_environment(params)
    context = build_render_context(params)
    if context is None:
        return

    redraw_views()
    poses = generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        preview_camera_gizmos(poses, context["lengths"])
        return

    capture_pose_sequence(poses, context)
