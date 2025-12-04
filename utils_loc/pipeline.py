"""Simple entry point orchestrating material, modeling, and rendering steps."""

from utils_loc.materials import import_materials
from utils_loc.layers import (
    BRDIGE_COMPONENT_MATERIAL_DICT,
    BRIDGE_COMPONENT_COLOR_DICT,
    create_layers,
)
from utils_loc.strategy_a import run_strategy_a
from utils_loc.strategy_b import run_strategy_b
from utils_loc.lighting import setup_lighting
from utils_loc.outputs import render_all_outputs


def run(params=None):
    """
    Main driver for the Rhino plugin workflow.

    Args:
        params: optional dict for controlling the pipeline.
            keys:
                - strategy: "a" or "b" (default: "a")
                - strategy_params: dict forwarded to the modeling strategy
                - render: bool to toggle rendering
                - render_params: dict forwarded to render_all_outputs
    """
    params = params or {}
    strategy = params.get("strategy", "a")
    render_enabled = params.get("render", False)

    # Materials and layers
    import_materials()
    create_layers(
        component_material_dict=BRDIGE_COMPONENT_MATERIAL_DICT,
        component_color_dict=BRIDGE_COMPONENT_COLOR_DICT,
    )

    # Modeling (choose which strategy to run once implemented)
    if strategy == "a":
        # TODO: implement strategy A then enable call
        # run_strategy_a(params.get("strategy_params"))
        pass
    elif strategy == "b":
        # TODO: implement strategy B then enable call
        # run_strategy_b(params.get("strategy_params"))
        pass
    else:
        raise ValueError(f"Unknown strategy '{strategy}'")

    # Rendering pipeline
    if render_enabled:
        setup_lighting(params.get("lighting"))
        render_all_outputs(**(params.get("render_params") or {}))


if __name__ == "__main__":
    run()
