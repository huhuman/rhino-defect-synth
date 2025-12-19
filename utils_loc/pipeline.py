"""Simple entry point orchestrating material, modeling, and rendering steps."""

import math
import random

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import import_materials, import_Vray_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.lighting import setup_sun, set_random_wallpaper
from utils_loc.camera import (
    animate_camera_path,
    generate_box_camera_grid,
    jitter_camera_poses,
)
from utils_loc.outputs import render_all_outputs


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


def render(params):
    """Render the model based on the provided parameters.

    Args:
        params (dict): Dictionary containing rendering parameters.
    """
    print("-------- Environment Setup -------")
    set_random_wallpaper(params["background_wallpaper_dir"])
    sun_time = random.uniform(5.0, 19.0)
    setup_sun(time_of_day=sun_time)
    print("-------- Environment Setup Finished -------")

    print("-------- Camera Path Preview -------")
    bbox_pts = rs.BoundingBox(
        rs.AllObjects(
            select=False,
            include_lights=False,
            include_grips=False,
        )
    )

    camera_cfg = params["camera"]
    points_per_side = int(camera_cfg["points_per_side"])
    if bbox_pts:
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
        multiple = 1.5
        lengths = (x_length * multiple, y_length * multiple, z_length * multiple)

        points_per_side = max(2, points_per_side)
        poses = generate_box_camera_grid(center, lengths, points_per_side)
        position_jitter = min(lengths) / (points_per_side - 1) * 0.1
        direction_jitter_degrees = 10.0
        poses = jitter_camera_poses(
            poses,
            position_jitter=position_jitter,
            direction_jitter_degrees=direction_jitter_degrees,
        )

        diag = math.sqrt(sum(l * l for l in lengths)) or 1.0
        distance = camera_cfg.get("distance")
        distance = float(distance) if distance is not None else diag * float(camera_cfg.get("distance_scale", 1.25))

        animate_camera_path(
            poses,
            view_name=camera_cfg.get("view_name"),
            distance=distance,
            dwell_ms=camera_cfg.get("dwell_ms", 300),
            lens=camera_cfg.get("lens"),
        )
    else:
        print("No geometry found for camera path generation; skipping camera move preview.")
    
    # render_all_outputs()
