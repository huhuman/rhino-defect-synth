"""Simple entry point orchestrating material, modeling, and rendering steps."""

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import create_materials_from_texture_dir, import_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.component_modeling import create_bridge_component
from utils_loc.damage_modeling import apply_damage_pipeline

import importlib
render = importlib.import_module("utils_loc.render")
render_demo = importlib.import_module("utils_loc.render_demo")

_LAST_MODEL_RESULT = None


def prepare(params=None):
    """Prepare the environment by importing materials and creating layers.
    Args:
        params (dict): Dictionary containing preparation parameters.
    """
    params = params or {}
    colors = params.get("colors", {})
    materials = params.get("materials", {})
    texture_materials = params.get("texture_materials", {})

    # Materials
    import_materials()
    # import_Vray_materials()

    # Optional: build materials from a user texture directory.
    texture_root_dir = texture_materials.get("texture_root_dir")
    if texture_root_dir:
        create_materials_from_texture_dir(
            texture_root_dir,
            recursive=texture_materials.get("recursive", True),
        )
    
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

    global _LAST_MODEL_RESULT

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

        model_result = {"strategy": "cube", "crack_faces": crack_faces}
        damage_cfg = params.get("damage") or {}
        if damage_cfg and bool(damage_cfg.get("enabled", True)):
            print("-------- Start Damage Placement -------")
            damage_result = apply_damage_pipeline(damage_cfg, model_result=model_result)
            model_result["damage"] = damage_result
            print(
                "-------- Damage Placement Complete ------- "
                "(total: {}, crack: {}, efflore: {}, exposed_rebar: {})".format(
                    damage_result.get("summary", {}).get("total", 0),
                    damage_result.get("summary", {}).get("crack", 0),
                    damage_result.get("summary", {}).get("efflore", 0),
                    damage_result.get("summary", {}).get("exposed_rebar", 0),
                )
            )
        _LAST_MODEL_RESULT = model_result
        return model_result

    elif strategy == "component":
        print("-------- Start Component Modeling -------")
        component_cfg = dict(params.get("component", {}))
        if not component_cfg:
            component_cfg = {
                key: value
                for key, value in params.items()
                if key not in ("strategy", "start_face_index")
            }

        result = create_bridge_component(component_cfg)
        damage_cfg = params.get("damage") or {}
        if damage_cfg and bool(damage_cfg.get("enabled", True)):
            print("-------- Start Damage Placement -------")
            damage_result = apply_damage_pipeline(damage_cfg, model_result=result)
            result["damage"] = damage_result
            print(
                "-------- Damage Placement Complete ------- "
                "(total: {}, crack: {}, efflore: {}, exposed_rebar: {})".format(
                    damage_result.get("summary", {}).get("total", 0),
                    damage_result.get("summary", {}).get("crack", 0),
                    damage_result.get("summary", {}).get("efflore", 0),
                    damage_result.get("summary", {}).get("exposed_rebar", 0),
                )
            )
        print(
            "-------- Component Modeling Complete ------- "
            "(surfaces: {}, polylines: {}, solids: {}, reference_points: {})".format(
                len(result.get("surfaces", [])),
                len(result.get("polylines", [])),
                len(result.get("solids", [])),
                len(result.get("reference_points", [])),
            )
        )
        _LAST_MODEL_RESULT = result
        return result
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def run_render(params, show_cameras=False):
    """Pipeline render stage."""
    params = dict(params or {})
    camera_cfg = dict(params.get("camera") or {})
    if camera_cfg.get("strategy") == "component":
        component_cfg = dict(camera_cfg.get("component") or {})
        has_defects = bool(component_cfg.get("defects"))
        has_record = bool(component_cfg.get("defect_record_path") or params.get("defect_record_path"))
        if not has_defects and not has_record:
            last_damage = ((_LAST_MODEL_RESULT or {}).get("damage") or {})
            if last_damage.get("camera_defects"):
                component_cfg["defects"] = last_damage["camera_defects"]
                camera_cfg["component"] = component_cfg
                params["camera"] = camera_cfg

    render.setup_render_environment(params)
    context = render.build_render_context(params)
    if context is None:
        return

    render.redraw_views()
    poses = render.generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        render.preview_camera_gizmos(poses, context["lengths"])
        return

    return render.capture_pose_sequence(poses, context)


def run_render_demo(base_out_dir, params=None):
    """Pipeline demo stage for camera/material/lighting visualization."""
    captured_paths = render_demo.render_demo(base_out_dir=base_out_dir, params=params)
    print(f"run_render_demo: captured {len(captured_paths)} images to '{base_out_dir}'.")
    return captured_paths
