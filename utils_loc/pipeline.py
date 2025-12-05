"""Simple entry point orchestrating material, modeling, and rendering steps."""

from utils_loc.materials import import_materials, import_Vray_materials
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.lighting import setup_lighting
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
        create_cube(
            cube_map_dir=params["cube_map_dir"],
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
    setup_lighting()
    render_all_outputs(**params)
