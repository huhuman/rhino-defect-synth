"""Minimal preset containers for materials, rendering, and modeling."""

MATERIAL_PRESETS = {
    "default": {
        "import": ["vray_corpus"],
        "layers": "bridge_components",
    }
}

RENDER_PRESETS = {
    "turntable": {
        "step_degrees": 18,
        "frames": 20,
        "width": 1442,
        "height": 879,
    }
}

MODELING_PRESETS = {
    "strategy_a": {},
    "strategy_b": {},
}

