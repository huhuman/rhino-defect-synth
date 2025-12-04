"""Rendering outputs for color, depth, normal, and masks."""


def render_image(view=None, out_path=None, preset=None, width=None, height=None):
    """
    Render the active/named view to an image file.

    Args:
        view: optional view name or handle.
        out_path: filepath for the color render.
        preset: render preset name.
        width: optional override width.
        height: optional override height.
    """
    raise NotImplementedError("Implement color rendering to file.")


def render_depth(view=None, out_path=None):
    """Render a depth pass for the view."""
    raise NotImplementedError("Implement depth rendering to file.")


def render_normal(view=None, out_path=None):
    """Render a normal pass for the view."""
    raise NotImplementedError("Implement normal rendering to file.")


def render_mask(view=None, out_path=None):
    """Render an object mask pass for the view."""
    raise NotImplementedError("Implement mask rendering to file.")


def render_all_outputs(view=None, out_dir=None, basename="frame"):
    """
    Convenience helper to render color, depth, normal, and mask in one call.
    """
    raise NotImplementedError("Implement combined rendering workflow.")

