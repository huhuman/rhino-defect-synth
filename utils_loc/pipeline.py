"""Simple entry point orchestrating material, modeling, and rendering steps."""

import math
import os
import random

import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import import_materials, import_Vray_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.lighting import setup_sun, set_random_wallpaper, setup_face_lights, set_skylight
from utils_loc.camera import (
    animate_camera_path_transition,
    animate_camera_path,
    generate_box_camera_grid,
    jitter_camera_poses,
    sort_poses_topdown_circular,
    set_camera,
)
from utils_loc.outputs import (
    _save_bitmap,
    _capture_bitmap,
    render_all_outputs
)


def prepare(params=None):
    """Prepare the environment by importing materials and creating layers.
    Args:
        params (dict): Dictionary containing preparation parameters.
    """
    params = params or {}
    colors = params.get("colors", {})
    materials = params.get("materials", {})

    # Materials
    import_materials()
    # import_Vray_materials()
    
    # Layers
    create_layers(
        layer_material_dict=materials,
        layer_color_dict=colors,
    )


def create_model(params):
    """Create the model based on the provided parameters.
    Args:
        params (dict): Dictionary containing modeling parameters.
    """
    strategy = params["strategy"]

    if strategy == "cube":
        print ("-------- Start Cube Modeling -------")
        crack_faces = create_cube(
            cube_map_dir=params["cube_map_dir"],
            start_face_index=params.get("start_face_index", 0),
        )

        inward_dirs = {
            "+x": (-1, 0, 0),
            "-x": (1, 0, 0),
            "+y": (0, -1, 0),
            "-y": (0, 1, 0),
            "+z": (0, 0, -1),
            "-z": (0, 0, 1),
        }
        for face, crack_items in crack_faces.items():
            print(f"-------- Modeling cracks on face {face} -------")
            inward = inward_dirs.get(face)
            for item in crack_items:
                create_crack(
                    item.get("crack_polys"),
                    item.get("inside_polys"),
                    item.get("base_poly"),
                    item.get("offset_poly"),
                    item.get("diff_polys"),
                    inward_dir=inward,
                )

    elif strategy == "component":
        pass
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def render(params, show_cameras=False):
    """Render the model based on the provided parameters.

    Args:
        params (dict): Dictionary containing rendering parameters.
    """
    # print("-------- Environment Setup -------")
    if params.get("background_wallpaper_dir"):
        set_random_wallpaper(params["background_wallpaper_dir"])

    lighting_cfg = params.get("lighting", {})
    sun_cfg = lighting_cfg.get("sun", {})
    if sun_cfg.get("enabled", True):
        sun_kwargs = {
            "time_of_day": sun_cfg.get("time_of_day", random.uniform(5.0, 19.0)),
            "date": sun_cfg.get("date"),
            "latitude": sun_cfg.get("latitude"),
            "longitude": sun_cfg.get("longitude"),
            "timezone": sun_cfg.get("timezone"),
            "intensity": sun_cfg.get("intensity", 1.0),
            "north": sun_cfg.get("north", 0.0),
        }
        setup_sun(**sun_kwargs)
    # Fixed skylight for soft ambient fill.
    set_skylight(intensity=0.25, enabled=True)
    # print("-------- Environment Setup Finished -------")

    # Generate camera poses around the model.
    bbox_pts = rs.BoundingBox(
        rs.AllObjects(
            select=False,
            include_lights=False,
            include_grips=False,
        )
    )

    camera_cfg = params["camera"]
    points_per_side = int(camera_cfg["points_per_side"])
    if not bbox_pts:
        print("No geometry found for camera path generation; skipping render.")
        return

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

    # Face lights: pick random-but-reasonable values each render instead of config-driven.
    faces = ["+x", "-x", "+y", "-y", "+z", "-z"]
    light_intensities = [random.uniform(0.1, 0.8) for _ in faces]
    distance_factor = random.uniform(0.4, 0.55)
    spot_hotspot = random.uniform(0.4, 0.8)
    spot_falloff = random.uniform(40.0, 75.0)
    light_type = random.choice(["directional", "spot"])  # keep predictable set

    setup_face_lights(
        bbox_pts=bbox_pts,
        faces=faces,
        distance_factor=distance_factor,
        intensities=light_intensities,
        light_type=light_type,
        replace_existing=True,
        spot_hotspot=spot_hotspot,
        spot_falloff=spot_falloff,
    )

    sc.doc.Views.Redraw()
    return 

    points_per_side = max(2, points_per_side)
    poses = generate_box_camera_grid(center, lengths, points_per_side)
    position_jitter = min(lengths) / (points_per_side - 1) * 0.0
    direction_jitter_degrees = 10.0
    poses = jitter_camera_poses(
        poses,
        position_jitter=position_jitter,
        direction_jitter_degrees=direction_jitter_degrees,
    )

    poses = sort_poses_topdown_circular(poses, center=center)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")

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
            base_center = tuple(pos[idx] - dir_vec[idx] * scale * 0.35 for idx in range(3))
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

        gizmo_scale = min(lengths) * 0.08
        for idx, pose in enumerate(poses):
            _add_camera_gizmo(idx, pose, gizmo_scale)
        rs.Redraw()
        return

    # Render each camera pose to a single output folder with indexed filenames.
    base_out_dir = params.get("output_dir", "C:/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/rhino_modeling/rendered")
    os.makedirs(base_out_dir, exist_ok=True)
    lens = camera_cfg.get("lens")
    transition_frames = int(camera_cfg.get("transition_frames", 0))
    smooth_path = bool(camera_cfg.get("smooth_path", False))

    def capture_pose(idx, pose):
        set_camera(position=pose["position"], target=pose["target"], lens=lens)
        basename = f"view_{idx:03d}"
        render_all_outputs(out_dir=base_out_dir, basename=basename)

    if smooth_path and transition_frames > 0:
        # Build interpolated frames between poses for a smoother path.
        frame_idx = 0
        for i, pose in enumerate(poses[:-1]):
            next_pose = poses[i + 1]
            capture_pose(frame_idx, pose)
            frame_idx += 1

            for step in range(1, transition_frames + 1):
                t = step / float(transition_frames + 1)
                interp_pos = (
                    pose["position"][0] + (next_pose["position"][0] - pose["position"][0]) * t,
                    pose["position"][1] + (next_pose["position"][1] - pose["position"][1]) * t,
                    pose["position"][2] + (next_pose["position"][2] - pose["position"][2]) * t,
                )
                interp_pose = {"position": interp_pos, "target": pose["target"], "direction": pose.get("direction")}
                capture_pose(frame_idx, interp_pose)
                frame_idx += 1

        # Capture the final pose.
        capture_pose(frame_idx, poses[-1])
    else:
        for idx, pose in enumerate(poses):
            capture_pose(idx, pose)


def render_demo():
    base_out_dir = "C:/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/rhino_modeling/demo_screenshots"
    os.makedirs(base_out_dir, exist_ok=True)
    import scriptcontext as sc

    # ========== Lighting Demo ==========
    # render_view = sc.doc.Views.ActiveView
    # for time in range(0, 24, 1):
    #     setup_sun(time_of_day=time)
    #     out_path = os.path.join(base_out_dir, f"demo_sun_{time}.png")
    #     bitmap = _capture_bitmap(render_view)
    #     _save_bitmap(bitmap, out_path)
    
    # ========== Material Demo ==========
    render_materials = [mat for mat in sc.doc.RenderMaterials]
    rand_mat_indices = random.sample(range(len(render_materials)), 5)
    for layer in sc.doc.Layers:
        if layer.Name and layer.Name == "cube":
            for idx in rand_mat_indices:
                layer.RenderMaterial = render_materials[idx]
                mat_name = render_materials[idx].DisplayName
                sc.doc.Views.ActiveView.Redraw()
                out_path = os.path.join(base_out_dir, f"demo_material_{mat_name}.png")
                bitmap = _capture_bitmap(sc.doc.Views.ActiveView)
                _save_bitmap(bitmap, out_path)
