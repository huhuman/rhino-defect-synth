"""Camera positioning helpers."""


def set_camera(view_name=None, position=None, target=None, up=None, lens=None):
    """
    Position a camera in the active or named view.

    Args:
        view_name: optional Rhino view name.
        position: iterable of 3 floats.
        target: iterable of 3 floats.
        up: optional up vector.
        lens: optional lens length / FOV override.
    """
    raise NotImplementedError("Implement camera placement with rs.ViewCameraTarget etc.")


def set_camera_target(position, target, up=None):
    """
    Convenience wrapper for setting camera and target together.
    """
    raise NotImplementedError("Implement camera + target setter.")


def spin_camera_around_bbox(bbox, step_degrees=15, distance_scale=0.25):
    """
    Generate camera poses around a bounding box for turntable renders.

    Args:
        bbox: bounding box tuple (max_x, min_x, max_y, min_y, max_z, min_z).
        step_degrees: degrees to rotate per step.
        distance_scale: relative distance multiplier from bbox extents.
    """
    raise NotImplementedError("Implement camera orbit generation around bbox.")

