"""Render stage helpers used by the pipeline orchestrator."""

import itertools
import math
import os
import random

import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.camera import (
    generate_box_camera_grid,
    jitter_camera_poses,
    set_camera,
    sort_poses_topdown_circular,
)
from utils_loc.lighting import set_random_wallpaper, set_skylight, setup_sun
from utils_loc.outputs import _capture_bitmap, _save_bitmap, render_all_outputs


def _setup_render_environment(params):
    """Set wallpaper and lighting before generating renders."""
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


def _build_render_context(params):
    """Collect scene/camera information required for rendering."""
    bbox_pts = rs.BoundingBox(
        rs.AllObjects(
            select=False,
            include_lights=False,
            include_grips=False,
        )
    )
    if not bbox_pts:
        print("No geometry found for camera path generation; skipping render.")
        return None

    camera_cfg = params["camera"]
    xs = [pt.X for pt in bbox_pts]
    ys = [pt.Y for pt in bbox_pts]
    zs = [pt.Z for pt in bbox_pts]
    center = (
        (max(xs) + min(xs)) * 0.5,
        (max(ys) + min(ys)) * 0.5,
        (max(zs) + min(zs)) * 0.5,
    )
    x_length = max(xs) - min(xs)
    y_length = max(ys) - min(ys)
    z_length = max(zs) - min(zs)
    distance_multiplier_min = camera_cfg["distance_multiplier_min"]
    distance_multiplier_max = camera_cfg["distance_multiplier_max"]
    multiple = random.uniform(distance_multiplier_min, distance_multiplier_max)
    lengths = (x_length * multiple, y_length * multiple, z_length * multiple)

    base_out_dir = params["output_dir"]
    os.makedirs(base_out_dir, exist_ok=True)

    return {
        "center": center,
        "lengths": lengths,
        "camera_cfg": camera_cfg,
        "base_out_dir": base_out_dir,
        "lens": camera_cfg.get("lens"),
        "transition_frames": int(camera_cfg.get("transition_frames", 0)),
        "smooth_path": bool(camera_cfg.get("smooth_path", False)),
        "width": params.get("width"),
        "height": params.get("height"),
        "max_length": params.get("max_length"),
    }


def _generate_render_poses(context):
    """Generate and order camera poses around the scene."""
    camera_cfg = context["camera_cfg"]
    center = context["center"]
    lengths = context["lengths"]

    points_per_side = max(2, int(camera_cfg["points_per_side"]))
    poses = generate_box_camera_grid(center, lengths, points_per_side)

    grid_spacing = min(lengths) / float(points_per_side - 1)
    position_jitter_override = camera_cfg.get("position_jitter")
    if position_jitter_override is None:
        # Scale-based jitter keeps behavior proportional to scene/camera grid size.
        position_jitter_scale = float(camera_cfg.get("position_jitter_scale", 0.25))
        position_jitter = max(0.0, grid_spacing * position_jitter_scale)
    else:
        # Absolute jitter in model units takes precedence when explicitly configured.
        position_jitter = max(0.0, float(position_jitter_override))

    direction_jitter_degrees = float(camera_cfg.get("direction_jitter_degrees", 10.0))
    poses = jitter_camera_poses(
        poses,
        position_jitter=position_jitter,
        direction_jitter_degrees=direction_jitter_degrees,
    )
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


def _add_camera_gizmo(idx, pose, scale):
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

    rs.AddPolyline(base_corners + [base_corners[0]])
    for corner in base_corners:
        rs.AddLine(corner, tip)
    rs.AddTextDot(f"cam {idx}", pos)
    if tgt:
        rs.AddLine(pos, tgt)


def _preview_camera_gizmos(poses, lengths):
    """Draw camera gizmos in Rhino for visual debugging."""
    gizmo_scale = min(lengths) * 0.08
    for idx, pose in enumerate(poses):
        _add_camera_gizmo(idx, pose, gizmo_scale)
    rs.Redraw()


def _capture_pose(idx, pose, base_out_dir, lens, width=None, height=None, max_length=None):
    set_camera(position=pose["position"], target=pose["target"], lens=lens)
    basename = f"view_{idx:03d}"
    render_all_outputs(
        out_dir=base_out_dir,
        basename=basename,
        width=width,
        height=height,
        max_length=max_length,
    )


def _capture_pose_sequence(poses, context):
    """Capture all frames based on smooth/direct path settings."""
    base_out_dir = context["base_out_dir"]
    lens = context["lens"]
    smooth_path = context["smooth_path"]
    transition_frames = context["transition_frames"]
    width = context["width"]
    height = context["height"]
    max_length = context["max_length"]

    if smooth_path and transition_frames > 0:
        frame_idx = 0
        for i, pose in enumerate(poses[:-1]):
            next_pose = poses[i + 1]
            _capture_pose(frame_idx, pose, base_out_dir, lens, width=width, height=height, max_length=max_length)
            frame_idx += 1

            for step in range(1, transition_frames + 1):
                t = step / float(transition_frames + 1)
                interp_pos = (
                    pose["position"][0] + (next_pose["position"][0] - pose["position"][0]) * t,
                    pose["position"][1] + (next_pose["position"][1] - pose["position"][1]) * t,
                    pose["position"][2] + (next_pose["position"][2] - pose["position"][2]) * t,
                )
                interp_pose = {"position": interp_pos, "target": pose["target"], "direction": pose.get("direction")}
                _capture_pose(
                    frame_idx,
                    interp_pose,
                    base_out_dir,
                    lens,
                    width=width,
                    height=height,
                    max_length=max_length,
                )
                frame_idx += 1

        _capture_pose(frame_idx, poses[-1], base_out_dir, lens, width=width, height=height, max_length=max_length)
        return

    for idx, pose in enumerate(poses):
        _capture_pose(idx, pose, base_out_dir, lens, width=width, height=height, max_length=max_length)


def setup_render_environment(params):
    """Public helper for render stage environment setup."""
    return _setup_render_environment(params)


def build_render_context(params):
    """Public helper for collecting camera/output context."""
    return _build_render_context(params)


def generate_render_poses(context):
    """Public helper for camera pose generation."""
    return _generate_render_poses(context)


def preview_camera_gizmos(poses, lengths):
    """Public helper for drawing camera previews."""
    return _preview_camera_gizmos(poses, lengths)


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


def _as_list(value, default):
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


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


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _find_layer_by_name(layer_name):
    for layer in sc.doc.Layers:
        if layer.Name == layer_name:
            return layer
    raise ValueError(f"Layer not found for render demo: '{layer_name}'")


def _resolve_demo_materials(material_names=None, sample_count=5, seed=0):
    render_materials = [mat for mat in sc.doc.RenderMaterials]
    if not render_materials:
        raise ValueError("No render materials found in the current Rhino document.")

    if material_names:
        selected = []
        wanted = [str(name).strip().lower() for name in material_names]
        for wanted_name in wanted:
            match = None
            for mat in render_materials:
                if mat.DisplayName and mat.DisplayName.strip().lower() == wanted_name:
                    match = mat
                    break
            if match is None:
                raise ValueError(f"Material not found for render demo: '{wanted_name}'")
            selected.append(match)
        return selected

    count = max(1, min(int(sample_count), len(render_materials)))
    rng = random.Random(seed)
    indices = list(range(len(render_materials)))
    rng.shuffle(indices)
    return [render_materials[idx] for idx in indices[:count]]


def build_render_demo_context(base_out_dir, params=None):
    """Prepare parsed render-demo options and current-document state."""
    params = params or {}
    os.makedirs(base_out_dir, exist_ok=True)

    rhino_view = sc.doc.Views.ActiveView
    if rhino_view is None:
        raise ValueError("No active Rhino view available for render demo.")

    layer_name = params.get("layer_name", "cube")
    target_layer = _find_layer_by_name(layer_name)

    materials = _resolve_demo_materials(
        material_names=params.get("material_names"),
        sample_count=params.get("sample_material_count", 5),
        seed=params.get("seed", 0),
    )
    sun_times = [float(v) for v in _as_list(params.get("sun_times"), [8.0, 12.0, 17.0])]
    sun_intensities = [float(v) for v in _as_list(params.get("sun_intensities"), [0.25, 1.0])]
    skylight_intensities = [float(v) for v in _as_list(params.get("skylight_intensities"), [0.25])]
    wallpaper_flags = [_to_bool(v) for v in _as_list(params.get("use_wallpaper"), [False])]
    wallpaper_dir = params.get("background_wallpaper_dir")

    if any(wallpaper_flags) and not wallpaper_dir:
        raise ValueError("render_demo requested wallpaper but no background_wallpaper_dir was provided.")

    width = params.get("width")
    height = params.get("height")
    max_length = params.get("max_length")
    max_cases = params.get("max_cases")
    max_cases = None if max_cases is None else max(1, int(max_cases))

    return {
        "base_out_dir": base_out_dir,
        "rhino_view": rhino_view,
        "target_layer": target_layer,
        "materials": materials,
        "sun_times": sun_times,
        "sun_intensities": sun_intensities,
        "skylight_intensities": skylight_intensities,
        "wallpaper_flags": wallpaper_flags,
        "wallpaper_dir": wallpaper_dir,
        "width": width,
        "height": height,
        "max_length": max_length,
        "max_cases": max_cases,
        "prev_wallpaper_file": rhino_view.ActiveViewport.WallpaperFilename,
        "prev_layer_material": target_layer.RenderMaterial,
    }


def iterate_render_demo_cases(context):
    """Yield cartesian-product demo cases from parsed context."""
    return itertools.product(
        context["materials"],
        context["sun_times"],
        context["sun_intensities"],
        context["skylight_intensities"],
        context["wallpaper_flags"],
    )


def should_stop_render_demo(case_idx, context):
    """Return True when case limit is reached."""
    max_cases = context["max_cases"]
    return max_cases is not None and case_idx >= max_cases


def capture_render_demo_case(case_idx, case, context):
    """Apply a single demo case and capture one output image."""
    material, sun_time, sun_intensity, skylight_intensity, use_wallpaper = case
    rhino_view = context["rhino_view"]
    target_layer = context["target_layer"]

    target_layer.RenderMaterial = material
    setup_sun(time_of_day=sun_time, intensity=sun_intensity)
    set_skylight(intensity=skylight_intensity, enabled=skylight_intensity > 0.0)

    wallpaper_tag = "none"
    if use_wallpaper:
        wallpaper_path = set_random_wallpaper(context["wallpaper_dir"], view=rhino_view.ActiveViewport.Name)
        wallpaper_tag = _sanitize_token(os.path.splitext(os.path.basename(wallpaper_path))[0])
    else:
        rhino_view.ActiveViewport.SetWallpaper("", False)

    rhino_view.Redraw()

    basename = (
        f"{case_idx:04d}"
        f"_mat-{_sanitize_token(material.DisplayName)}"
        f"_sunT-{sun_time:g}"
        f"_sunI-{sun_intensity:g}"
        f"_sky-{skylight_intensity:g}"
        f"_bg-{wallpaper_tag}"
    )
    out_path = os.path.join(context["base_out_dir"], f"{basename}.png")
    bitmap = _capture_bitmap(
        rhino_view,
        width=context["width"],
        height=context["height"],
        max_length=context["max_length"],
    )
    _save_bitmap(bitmap, out_path)
    return out_path


def restore_render_demo_context(context):
    """Restore modified material/wallpaper state after demo capture."""
    context["target_layer"].RenderMaterial = context["prev_layer_material"]
    context["rhino_view"].ActiveViewport.SetWallpaper(context["prev_wallpaper_file"] or "", False)
    context["rhino_view"].Redraw()


def render_demo(base_out_dir, params=None):
    """
    Sweep combinations of render settings and capture demo images.

    Params keys:
      - layer_name (str): layer receiving material swaps. Default: "cube"
      - material_names (list[str]): exact material names to use
      - sample_material_count (int): used when material_names is omitted. Default: 5
      - sun_times (list[float]): default [8.0, 12.0, 17.0]
      - sun_intensities (list[float]): default [0.25, 1.0]
      - skylight_intensities (list[float]): default [0.25]
      - use_wallpaper (list[bool]): default [False]
      - background_wallpaper_dir (str): required when any use_wallpaper=True
      - width, height (int): optional capture size overrides
      - max_length (int): optional longest-side resolution preserving viewport aspect ratio
      - max_cases (int): optional cap on number of combinations
      - seed (int): seed for deterministic random material sampling
    """
    context = build_render_demo_context(base_out_dir=base_out_dir, params=params)

    captured_paths = []
    try:
        for case_idx, case in enumerate(iterate_render_demo_cases(context)):
            if should_stop_render_demo(case_idx, context):
                break
            captured_paths.append(capture_render_demo_case(case_idx, case, context))
    finally:
        restore_render_demo_context(context)

    print(f"render_demo: captured {len(captured_paths)} images to '{context['base_out_dir']}'.")
    return captured_paths
