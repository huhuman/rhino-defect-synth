"""Lighting presets and setup helpers."""


def setup_lighting(preset="studio", intensity=None, direction=None):
    """
    Create or configure lights for the scene.

    Args:
        preset: logical preset name (e.g., 'studio', 'sun', 'hdr').
        intensity: optional override for light strength.
        direction: optional light direction vector.
    """
    raise NotImplementedError("Implement lighting creation for the chosen preset.")


def clear_lighting():
    """Remove or reset custom lights to the document defaults."""
    raise NotImplementedError("Implement light cleanup/reset.")

