"""Camera positioning helpers."""

import math
import random
from typing import Iterable, List, Mapping, Sequence, Tuple

import rhinoscriptsyntax as rs
import scriptcontext as sc

Vec3 = Tuple[float, float, float]


def _resolve_view_name(view_name=None):
    """Return a view name usable by rhinoscriptsyntax calls."""
    if view_name:
        rhino_view = sc.doc.Views.Find(view_name, False)
        if rhino_view is None:
            raise ValueError(f"View not found: {view_name}")
        return rhino_view.ActiveViewport.Name

    rhino_view = sc.doc.Views.ActiveView
    if rhino_view is None:
        raise ValueError("No active view is available.")
    return rhino_view.ActiveViewport.Name


def _normalize(vec: Sequence[float]) -> Vec3:
    """Normalize a 3D vector; fall back to (0,0,1) if length is zero."""
    x, y, z = (float(v) for v in vec)
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return 0.0, 0.0, 1.0
    inv = 1.0 / length
    return x * inv, y * inv, z * inv


def _lerp(a: Sequence[float], b: Sequence[float], t: float) -> Vec3:
    """Linear interpolation between two 3D vectors."""
    return tuple(float(a[i]) + (float(b[i]) - float(a[i])) * t for i in range(3))


def _centroid(points: Sequence[Sequence[float]]) -> Vec3:
    """Compute centroid of a collection of 3D points."""
    if not points:
        return 0.0, 0.0, 0.0
    sx = sy = sz = 0.0
    count = 0
    for pt in points:
        px, py, pz = (float(v) for v in pt)
        sx += px
        sy += py
        sz += pz
        count += 1
    inv = 1.0 / float(count)
    return sx * inv, sy * inv, sz * inv


def _linspace(start: float, stop: float, n: int) -> List[float]:
    """Simple linspace without numpy."""
    if n <= 1:
        return [start]
    step = (stop - start) / float(n - 1)
    return [start + i * step for i in range(n)]


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
    if position is None or target is None:
        raise ValueError("Both position and target are required to set the camera.")

    resolved_view = _resolve_view_name(view_name)
    rs.ViewCameraTarget(resolved_view, position, target)
    if up is not None:
        rs.ViewCameraUp(resolved_view, up)
    if lens is not None:
        rs.ViewCameraLens(resolved_view, lens)
    sc.doc.Views.Redraw()
    return position, target


def set_camera_target(position, target, up=None):
    """Convenience wrapper for setting camera and target together."""
    return set_camera(position=position, target=target, up=up)


def move_camera(position: Iterable[float], direction: Iterable[float], view_name=None, distance=10.0, up=None, lens=None):
    """
    Move the camera to a position and orient it along a direction vector.

    Args:
        position: camera location (x, y, z).
        direction: direction vector to look toward.
        view_name: optional Rhino view name.
        distance: distance along the direction vector to place the target.
        up: optional up vector override.
        lens: optional lens length / FOV override.
    """
    pos = tuple(float(p) for p in position)
    dir_vec = _normalize(direction)
    tgt = tuple(pos[i] + dir_vec[i] * float(distance) for i in range(3))
    return set_camera(view_name=view_name, position=pos, target=tgt, up=up, lens=lens)


def generate_box_camera_grid(center: Iterable[float], lengths: Iterable[float], n: int) -> List[Mapping[str, Vec3]]:
    """
    Generate camera positions around a box (6 faces, n x n points per face).

    Args:
        center: box center (x, y, z).
        lengths: box side lengths (lx, ly, lz).
        n: number of camera points per edge (>=2).

    Returns:
        List of dicts with position, target (center), and direction (toward center).
    """
    if n < 2:
        raise ValueError("n must be >= 2 to form a grid.")

    cx, cy, cz = (float(v) for v in center)
    lx, ly, lz = (float(v) for v in lengths)
    hx, hy, hz = lx * 0.5, ly * 0.5, lz * 0.5

    xs = _linspace(cx - hx, cx + hx, n)
    ys = _linspace(cy - hy, cy + hy, n)
    zs = _linspace(cz - hz, cz + hz, n)

    faces = [
        ([(cx + hx, y, z) for y in ys for z in zs]),  # +X
        ([(cx - hx, y, z) for y in ys for z in zs]),  # -X
        ([(x, cy + hy, z) for x in xs for z in zs]),  # +Y
        ([(x, cy - hy, z) for x in xs for z in zs]),  # -Y
        ([(x, y, cz + hz) for x in xs for y in ys]),  # +Z
        ([(x, y, cz - hz) for x in xs for y in ys]),  # -Z
    ]

    poses = []
    seen = set()
    center_pt: Vec3 = (cx, cy, cz)

    for pts in faces:
        for pt in pts:
            key = tuple(round(c, 6) for c in pt)
            if key in seen:
                continue
            seen.add(key)
            dir_vec = _normalize(center_pt[i] - pt[i] for i in range(3))
            poses.append(
                {
                    "position": pt,
                    "target": center_pt,
                    "direction": dir_vec,
                }
            )
    return poses


def jitter_camera_poses(poses: List[Mapping[str, Vec3]], position_jitter=0.0, direction_jitter_degrees=0.0) -> List[Mapping[str, Vec3]]:
    """
    Apply small random offsets to camera positions and directions.

    Args:
        poses: list from `generate_box_camera_grid`.
        position_jitter: max absolute positional offset per axis (model units).
        direction_jitter_degrees: max angular jitter for the look direction.
    """
    pos_j = float(position_jitter)
    dir_j = float(direction_jitter_degrees)

    def jitter_dir(dir_vec: Vec3) -> Vec3:
        if dir_j <= 0:
            return dir_vec
        # Perturb direction by adding a small random vector and renormalizing.
        max_delta = math.tan(math.radians(dir_j))
        delta = (
            random.uniform(-max_delta, max_delta),
            random.uniform(-max_delta, max_delta),
            random.uniform(-max_delta, max_delta),
        )
        perturbed = tuple(dir_vec[i] + delta[i] for i in range(3))
        return _normalize(perturbed)

    jittered = []
    for pose in poses:
        pos = pose["position"]
        dir_vec = pose["direction"]
        jittered_pos = tuple(
            float(pos[i]) + (random.uniform(-pos_j, pos_j) if pos_j > 0 else 0.0)
            for i in range(3)
        )
        jittered_dir = jitter_dir(dir_vec)
        jittered.append(
            {
                "position": jittered_pos,
                "target": pose.get("target"),
                "direction": jittered_dir,
            }
        )
    return jittered


def sort_poses_topdown_circular(poses: List[Mapping[str, Vec3]], center: Vec3 = None, z_bin_tol: float = 1e-3) -> List[Mapping[str, Vec3]]:
    """Order poses from highest Z to lowest, and circularly within each layer.

    Args:
        poses: list of pose dicts (expects a "position" entry).
        center: optional reference point for angle sorting; defaults to centroid of positions.
        z_bin_tol: tolerance for grouping poses into the same Z layer.
    """
    if not poses:
        return poses

    positions = [p["position"] for p in poses]
    cx, cy, cz = center if center is not None else _centroid(positions)
    z_tol = max(1e-9, float(z_bin_tol))

    # Group poses by Z layers using a tolerance bin.
    layers = {}
    for pose in poses:
        z = float(pose["position"][2])
        key = round(z / z_tol)
        if key not in layers:
            layers[key] = {"z": z, "items": []}
        layers[key]["items"].append(pose)

    # Sort layer keys from top (largest Z) to bottom.
    sorted_layers = sorted(layers.values(), key=lambda entry: entry["z"], reverse=True)

    ordered: List[Mapping[str, Vec3]] = []
    for layer in sorted_layers:
        items = layer["items"]
        # Sort clockwise around center (atan2 gives -pi..pi). Adjust to start at +X and go CCW.
        def angle(p):
            x, y, _ = (float(v) for v in p["position"])
            return math.atan2(y - cy, x - cx)

        ordered.extend(sorted(items, key=angle))

    return ordered


def _prepare_pose_for_animation(pose: Mapping[str, Vec3], fallback_distance: float) -> Mapping[str, Vec3]:
    """Ensure every pose has an explicit target for animation."""
    pos = tuple(float(v) for v in pose["position"])
    target = pose.get("target")
    direction = pose.get("direction")

    if target is None:
        if direction is None:
            raise ValueError("Each pose must define a target or direction.")
        dir_vec = _normalize(direction)
        target = tuple(pos[i] + dir_vec[i] * fallback_distance for i in range(3))
    else:
        target = tuple(float(v) for v in target)

    return {"position": pos, "target": target, "direction": direction}


def _interpolate_camera_path(resolved_poses: List[Mapping[str, Vec3]], frames_between: int) -> List[Mapping[str, Vec3]]:
    """Insert evenly spaced poses between each provided pose."""
    if frames_between <= 0 or len(resolved_poses) < 2:
        return resolved_poses

    frames: List[Mapping[str, Vec3]] = [resolved_poses[0]]
    steps = int(frames_between)

    for idx in range(len(resolved_poses) - 1):
        start = resolved_poses[idx]
        end = resolved_poses[idx + 1]

        for step in range(1, steps + 1):
            t = step / float(steps + 1)
            interp_pos = _lerp(start["position"], end["position"], t)
            # Keep the target fixed during interpolation to avoid zooming toward the midpoint.
            interp_tgt = start["target"]
            frames.append({"position": interp_pos, "target": interp_tgt, "direction": end.get("direction")})

        frames.append(end)

    return frames


def animate_camera_path(poses: List[Mapping[str, Vec3]], view_name=None, distance=None, dwell_ms=300, up=None, lens=None, transition_frames=0):
    """
    Move the Rhino camera through a list of poses to visualize motion.

    Args:
        poses: list containing position/target/direction entries.
        view_name: optional Rhino view name.
        distance: optional override for direction-based target distance. Defaults to 10 units when None.
        dwell_ms: pause in milliseconds between frames.
        up: optional up vector override.
        lens: optional lens length / FOV override.
        transition_frames: number of linearly interpolated poses to insert between each input pose.
    """
    if not poses:
        return

    resolved_view = _resolve_view_name(view_name)
    sleep_ms = max(0, int(dwell_ms))
    target_distance = 10.0 if distance is None else float(distance)

    resolved_poses = [_prepare_pose_for_animation(p, target_distance) for p in poses]
    frames = _interpolate_camera_path(resolved_poses, transition_frames)

    for pose in frames:
        pos = pose["position"]
        target = pose["target"]

        set_camera(view_name=resolved_view, position=pos, target=target, up=up, lens=lens)

        if sleep_ms:
            rs.Sleep(sleep_ms)


def animate_camera_path_jump(poses: List[Mapping[str, Vec3]], **kwargs):
    """Animate by jumping directly to each pose (backwards compatible helper)."""
    return animate_camera_path(poses, transition_frames=0, **kwargs)


def animate_camera_path_transition(poses: List[Mapping[str, Vec3]], transition_frames=5, **kwargs):
    """Animate by linearly interpolating poses before moving the camera."""
    return animate_camera_path(poses, transition_frames=transition_frames, **kwargs)


def spin_camera_around_bbox(bbox, step_degrees=15, distance_scale=0.25):
    """
    Generate camera poses around a bounding box for turntable renders.

    Args:
        bbox: bounding box tuple (max_x, min_x, max_y, min_y, max_z, min_z).
        step_degrees: degrees to rotate per step.
        distance_scale: relative distance multiplier from bbox extents.
    """
    max_x, min_x, max_y, min_y, max_z, min_z = bbox
    center = ((max_x + min_x) * 0.5, (max_y + min_y) * 0.5, (max_z + min_z) * 0.5)
    radius = max(max_x - min_x, max_y - min_y) * distance_scale
    z_height = center[2] + (max_z - min_z) * distance_scale

    poses = []
    angle = 0.0
    while angle < 360.0:
        rad = math.radians(angle)
        pos = (
            center[0] + radius * math.cos(rad),
            center[1] + radius * math.sin(rad),
            z_height,
        )
        dir_vec = _normalize(center[i] - pos[i] for i in range(3))
        poses.append({"position": pos, "target": center, "direction": dir_vec})
        angle += step_degrees
    return poses
