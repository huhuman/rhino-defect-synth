"""Utility modules for the Rhino defect synthesis workflow."""

__all__ = ["prepare", "create_model", "run_render", "run_render_demo"]


def __getattr__(name):
    # Lazy exports avoid importing Rhino-dependent modules at package import time.
    if name in __all__:
        from utils_loc.pipeline import prepare, create_model, run_render, run_render_demo

        exports = {
            "prepare": prepare,
            "create_model": create_model,
            "run_render": run_render,
            "run_render_demo": run_render_demo,
        }
        return exports[name]
    raise AttributeError(f"module 'utils_loc' has no attribute '{name}'")
