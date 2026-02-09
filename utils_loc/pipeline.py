"""Simple entry point orchestrating material, modeling, and rendering steps."""

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import import_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
import utils_loc.render as render_steps


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
    """Pipeline render stage."""
    render_steps.setup_render_environment(params)
    context = render_steps.build_render_context(params)
    if context is None:
        return

    render_steps.redraw_views()
    poses = render_steps.generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        render_steps.preview_camera_gizmos(poses, context["lengths"])
        return

    return render_steps.capture_pose_sequence(poses, context)


def render_demo(base_out_dir, params=None):
    """Pipeline demo stage for sweeping render settings."""
    context = render_steps.build_render_demo_context(base_out_dir=base_out_dir, params=params)
    captured_paths = []
    try:
        for case_idx, case in enumerate(render_steps.iterate_render_demo_cases(context)):
            if render_steps.should_stop_render_demo(case_idx, context):
                break
            captured_paths.append(render_steps.capture_render_demo_case(case_idx, case, context))
    finally:
        render_steps.restore_render_demo_context(context)

    print(f"render_demo: captured {len(captured_paths)} images to '{context['base_out_dir']}'.")
    return captured_paths
