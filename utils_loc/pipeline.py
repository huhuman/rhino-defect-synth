"""Simple entry point orchestrating material, modeling, and rendering steps."""

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import create_materials_from_texture_dir, import_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.component_modeling import create_bridge_component
from utils_loc.defect_placement import apply_defect_pipeline

import importlib
render = importlib.import_module("utils_loc.render")
render_demo = importlib.import_module("utils_loc.render_demo")

_LAST_MODEL_RESULT = None


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _should_run_defect_pipeline(defect_cfg):
    if not isinstance(defect_cfg, dict) or not defect_cfg:
        return False

    # Keep supporting the legacy/global switch when it is explicitly set.
    if "enabled" in defect_cfg and not bool(defect_cfg.get("enabled", True)):
        return False

    for defect_type in ("crack", "efflore", "spalling", "exposed_rebar"):
        type_cfg = defect_cfg.get(defect_type)
        if not isinstance(type_cfg, dict):
            continue
        if not bool(type_cfg.get("enabled", True)):
            continue
        if _to_int(type_cfg.get("count"), 0) > 0:
            return True
    return False


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
        cube_cfg = dict(params.get("cube") or {})
        if not cube_cfg:
            raise ValueError("modeling.cube is required when modeling.strategy='cube'.")
        print ("-------- Start Cube Modeling -------")
        crack_faces = create_cube(
            cube_map_dir=cube_cfg["cube_map_dir"],
            start_face_index=params.get("start_face_index", cube_cfg.get("start_face_index", 0)),
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

        # Cube workflow uses face crack maps directly; no secondary defect placement stage.
        model_result = {"strategy": "cube", "crack_faces": crack_faces}
        _LAST_MODEL_RESULT = model_result
        return model_result

    elif strategy == "component":
        print("-------- Start Component Modeling -------")
        component_cfg = dict(params.get("component", {}))
        if not component_cfg:
            raise ValueError("modeling.component is required when modeling.strategy='component'.")

        result = create_bridge_component(component_cfg)
        defect_cfg = params.get("defect") or {}
        if _should_run_defect_pipeline(defect_cfg):
            print("-------- Start Defect Placement -------")
            defect_result = apply_defect_pipeline(defect_cfg, model_result=result)
            result["defect"] = defect_result
            print(
                "-------- Defect Placement Complete ------- "
                "(total: {}, crack: {}, efflore: {}, spalling: {}, exposed_rebar: {})".format(
                    defect_result.get("summary", {}).get("total", 0),
                    defect_result.get("summary", {}).get("crack", 0),
                    defect_result.get("summary", {}).get("efflore", 0),
                    defect_result.get("summary", {}).get("spalling", 0),
                    defect_result.get("summary", {}).get("exposed_rebar", 0),
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
            last_defect = (_LAST_MODEL_RESULT or {}).get("defect") or {}
            if last_defect.get("camera_defects"):
                component_cfg["defects"] = last_defect["camera_defects"]
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
