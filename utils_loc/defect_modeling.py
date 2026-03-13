
"""Helpers for defect placement references and component-surface preparation."""

import json
import math
import os

import rhinoscriptsyntax as rs
from utils_loc.defect_shapes import extract_point_sets


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _xyz(value):
    if value is None:
        return 0.0, 0.0, 0.0
    if hasattr(value, "X") and hasattr(value, "Y"):
        return float(value.X), float(value.Y), float(getattr(value, "Z", 0.0))
    if len(value) == 2:
        return float(value[0]), float(value[1]), 0.0
    return float(value[0]), float(value[1]), float(value[2])


def _distance(a, b):
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    dx = ax - bx
    dy = ay - by
    dz = az - bz
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _unit(v, fallback=(0.0, 0.0, 1.0)):
    vx, vy, vz = _xyz(v)
    norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if norm <= 1e-12:
        fx, fy, fz = _xyz(fallback)
        f_norm = math.sqrt(fx * fx + fy * fy + fz * fz)
        if f_norm <= 1e-12:
            return 0.0, 0.0, 1.0
        return fx / f_norm, fy / f_norm, fz / f_norm
    inv = 1.0 / norm
    return vx * inv, vy * inv, vz * inv


def _dedupe_ids(ids):
    ordered = []
    seen = set()
    for obj_id in ids:
        if not obj_id or obj_id in seen:
            continue
        seen.add(obj_id)
        ordered.append(obj_id)
    return ordered


def _normalize_layer_names(layer_names):
    if not layer_names:
        return None
    return {str(name) for name in _as_list(layer_names) if str(name).strip()}


def _object_in_layers(obj_id, layer_name_set):
    if layer_name_set is None:
        return True
    layer_name = rs.ObjectLayer(obj_id)
    return layer_name in layer_name_set


def _curve_to_surface_ids(curve_id):
    if not curve_id or not rs.IsCurve(curve_id) or not rs.IsCurveClosed(curve_id):
        return []

    surface_ids = rs.AddPlanarSrf(curve_id) or []
    if surface_ids:
        return list(surface_ids)

    pts = rs.CurvePoints(curve_id) or []
    vertices = []
    for pt in pts:
        p = _xyz(pt)
        if not vertices or _distance(vertices[-1], p) > 1e-6:
            vertices.append(p)
    if len(vertices) > 1 and _distance(vertices[0], vertices[-1]) <= 1e-6:
        vertices.pop()

    if len(vertices) == 4:
        srf_id = rs.AddSrfPt(vertices)
        if srf_id:
            return [srf_id]
    return []


def _sample_domain(domain, sample_count, margin):
    start = float(domain[0])
    end = float(domain[1])
    if sample_count <= 1:
        return [(start + end) * 0.5]

    margin = max(0.0, min(0.45, float(margin)))
    lo = start + (end - start) * margin
    hi = end - (end - start) * margin
    if lo > hi:
        mid = (start + end) * 0.5
        return [mid] * sample_count

    step = (hi - lo) / float(sample_count - 1)
    return [lo + idx * step for idx in range(sample_count)]


def _surface_domain_midpoint(domain):
    return 0.5 * (float(domain[0]) + float(domain[1]))


def _domain_param_from_distance(domain, total_length, distance):
    start = float(domain[0])
    end = float(domain[1])
    if total_length <= 1e-9:
        return _surface_domain_midpoint(domain)
    ratio = max(0.0, min(1.0, float(distance) / float(total_length)))
    return start + (end - start) * ratio


def _resolve_axis_sampling(domain, length, edge_length, fallback_count, center_param, boundary_margin_ratio=0.0):
    count = max(1, int(fallback_count))
    edge = None if edge_length is None else float(edge_length)
    if edge is None or edge <= 0.0:
        values = _sample_domain(domain, count, 0.0)
        return {
            "values": values,
            "sample_count": len(values),
            "centered": False,
            "insufficient": False,
            "boundary_distance": 0.0,
            "sample_margin": 0.0,
        }

    boundary_distance = max(0.0, edge * max(0.0, float(boundary_margin_ratio or 0.0)))
    usable_length = float(length) - 2.0 * boundary_distance
    if usable_length + 1e-9 < edge:
        return {
            "values": [float(center_param)],
            "sample_count": 1,
            "centered": True,
            "insufficient": True,
            "boundary_distance": boundary_distance,
            "sample_margin": 0.0 if length <= 1e-9 else boundary_distance / float(length),
        }

    cell_count = max(1, int(math.floor((usable_length + 1e-9) / edge)))
    occupied_length = float(cell_count) * edge
    leftover = max(0.0, usable_length - occupied_length)
    first_center_distance = boundary_distance + 0.5 * leftover + 0.5 * edge
    values = [
        _domain_param_from_distance(domain, length, first_center_distance + edge * float(idx))
        for idx in range(cell_count)
    ]
    return {
        "values": values,
        "sample_count": len(values),
        "centered": False,
        "insufficient": False,
        "boundary_distance": boundary_distance,
        "sample_margin": 0.0 if length <= 1e-9 else boundary_distance / float(length),
    }


def _surface_curve_length(surface_id, direction, cross_param, sample_steps=24):
    sample_steps = max(4, int(sample_steps))
    domain = rs.SurfaceDomain(surface_id, 0 if int(direction) == 0 else 1)
    if not domain:
        return None

    start = float(domain[0])
    end = float(domain[1])
    total = 0.0
    prev = None
    for idx in range(sample_steps + 1):
        t = float(idx) / float(sample_steps)
        param = start + (end - start) * t
        if int(direction) == 0:
            point = rs.EvaluateSurface(surface_id, param, cross_param)
        else:
            point = rs.EvaluateSurface(surface_id, cross_param, param)
        if not point:
            continue
        point = _xyz(point)
        if prev is not None:
            total += _distance(prev, point)
        prev = point
    return total if total > 1e-9 else None


def _surface_uv_lengths(surface_id, domain_u, domain_v):
    if not surface_id or not rs.IsObject(surface_id):
        return 0.0, 0.0

    u_cross_values = [
        float(domain_v[0]) + (float(domain_v[1]) - float(domain_v[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]
    v_cross_values = [
        float(domain_u[0]) + (float(domain_u[1]) - float(domain_u[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]

    u_lengths = [
        length for length in (_surface_curve_length(surface_id, 0, value) for value in u_cross_values)
        if length is not None
    ]
    v_lengths = [
        length for length in (_surface_curve_length(surface_id, 1, value) for value in v_cross_values)
        if length is not None
    ]
    if u_lengths and v_lengths:
        return sum(u_lengths) / float(len(u_lengths)), sum(v_lengths) / float(len(v_lengths))

    bbox = rs.BoundingBox(surface_id)
    if not bbox:
        return 0.0, 0.0
    xs = [pt.X for pt in bbox]
    ys = [pt.Y for pt in bbox]
    zs = [pt.Z for pt in bbox]
    lengths = sorted(
        [
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ],
        reverse=True,
    )
    if len(lengths) >= 2:
        return float(lengths[0]), float(lengths[1])
    if lengths:
        return float(lengths[0]), float(lengths[0])
    return 0.0, 0.0


def _resolve_surface_sampling(
    surface_id,
    domain_u,
    domain_v,
    sample_count_u,
    sample_count_v,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    boundary_margin_ratio_u=0.0,
    boundary_margin_ratio_v=0.0,
):
    center_uv = (_surface_domain_midpoint(domain_u), _surface_domain_midpoint(domain_v))
    length_u, length_v = _surface_uv_lengths(surface_id, domain_u, domain_v)
    u_sampling = _resolve_axis_sampling(
        domain_u,
        length_u,
        sample_edge_length_u,
        sample_count_u,
        center_uv[0],
        boundary_margin_ratio=boundary_margin_ratio_u,
    )
    v_sampling = _resolve_axis_sampling(
        domain_v,
        length_v,
        sample_edge_length_v,
        sample_count_v,
        center_uv[1],
        boundary_margin_ratio=boundary_margin_ratio_v,
    )
    both_centered = bool(u_sampling["insufficient"] and v_sampling["insufficient"])
    one_centered = bool(u_sampling["centered"] or v_sampling["centered"])
    return {
        "sample_count_u": int(u_sampling["sample_count"]),
        "sample_count_v": int(v_sampling["sample_count"]),
        "u_values": list(u_sampling["values"]),
        "v_values": list(v_sampling["values"]),
        "center_uv": center_uv,
        "center_u": bool(u_sampling["centered"]),
        "center_v": bool(v_sampling["centered"]),
        "sample_margin_u": float(u_sampling["sample_margin"]),
        "sample_margin_v": float(v_sampling["sample_margin"]),
        "skip_checks": both_centered,
        "sampling_mode": (
            "center_fallback" if both_centered else
            "axis_centered_grid" if one_centered else
            "edge_grid"
        ),
    }


def _size_hint_for_surface(surface_id, sample_count):
    area_data = rs.SurfaceArea(surface_id)
    area = 0.0
    if isinstance(area_data, (list, tuple)) and area_data:
        area = float(area_data[0])
    elif isinstance(area_data, (int, float)):
        area = float(area_data)
    if area > 0:
        return max(1e-3, math.sqrt(area / max(1, sample_count)))

    bbox = rs.BoundingBox(surface_id)
    if not bbox:
        return 1.0
    xs = [pt.X for pt in bbox]
    ys = [pt.Y for pt in bbox]
    zs = [pt.Z for pt in bbox]
    lengths = sorted(
        [
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ],
        reverse=True,
    )
    return max(1e-3, lengths[1] if len(lengths) > 1 else lengths[0])


def _reference_size_hint(
    surface_id,
    sample_count,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    sampling_mode=None,
):
    edge_lengths = []
    for value in (sample_edge_length_u, sample_edge_length_v):
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 1e-9:
            edge_lengths.append(numeric)

    if edge_lengths:
        return max(1e-3, min(edge_lengths))

    if sampling_mode == "center_fallback":
        domain_u = rs.SurfaceDomain(surface_id, 0)
        domain_v = rs.SurfaceDomain(surface_id, 1)
        if domain_u and domain_v:
            length_u, length_v = _surface_uv_lengths(surface_id, domain_u, domain_v)
            lengths = [length for length in (length_u, length_v) if length > 1e-9]
            if lengths:
                return max(1e-3, min(lengths))

    return _size_hint_for_surface(surface_id, sample_count)


def get_reference_points(
    surface,
    sample_count_u=3,
    sample_count_v=3,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    boundary_margin_ratio_u=0.0,
    boundary_margin_ratio_v=0.0,
    trim_margin=0.0,
    return_normals=False,
    return_metadata=False,
):
    """Sample candidate defect reference points on one surface.

    Args:
        surface: Rhino surface object id.
        sample_count_u: Number of samples along U domain.
        sample_count_v: Number of samples along V domain.
        sample_edge_length_u: Target surface sampling edge length along U in model units.
        sample_edge_length_v: Target surface sampling edge length along V in model units.
        boundary_margin_ratio_u: Inner sampling margin ratio relative to `sample_edge_length_u`.
        boundary_margin_ratio_v: Inner sampling margin ratio relative to `sample_edge_length_v`.
        trim_margin: Deprecated and ignored.
        return_normals: When True, also return sampled normals.
        return_metadata: When True, also return per-point metadata.

    Returns:
        Tuple of (points, sizes), (points, sizes, normals), or with metadata appended.
    """
    if not surface or not rs.IsObject(surface):
        if return_normals and return_metadata:
            return [], [], [], []
        if return_normals:
            return [], [], []
        if return_metadata:
            return [], [], []
        return [], []

    cleanup_ids = []
    if rs.IsPolysurface(surface):
        exploded = rs.ExplodePolysurfaces(surface, delete_input=False) or []
        if not exploded:
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []
        surface = exploded[0]
        cleanup_ids = exploded

    try:
        if not rs.IsSurface(surface):
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []

        domain_u = rs.SurfaceDomain(surface, 0)
        domain_v = rs.SurfaceDomain(surface, 1)
        if not domain_u or not domain_v:
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []

        sampling = _resolve_surface_sampling(
            surface,
            domain_u,
            domain_v,
            sample_count_u,
            sample_count_v,
            sample_edge_length_u=sample_edge_length_u,
            sample_edge_length_v=sample_edge_length_v,
            boundary_margin_ratio_u=boundary_margin_ratio_u,
            boundary_margin_ratio_v=boundary_margin_ratio_v,
        )
        u_values = list(sampling.get("u_values") or [sampling["center_uv"][0]])
        v_values = list(sampling.get("v_values") or [sampling["center_uv"][1]])

        points = []
        normals = []
        metadata = []
        size_hint = _reference_size_hint(
            surface,
            max(1, len(u_values) * len(v_values)),
            sample_edge_length_u=sample_edge_length_u,
            sample_edge_length_v=sample_edge_length_v,
            sampling_mode=sampling.get("sampling_mode"),
        )
        sizes = []

        for u in u_values:
            for v in v_values:
                point = rs.EvaluateSurface(surface, u, v)
                if not point:
                    continue
                point = _xyz(point)
                if not sampling["skip_checks"] and not rs.IsPointOnSurface(surface, point):
                    continue
                normal = rs.SurfaceNormal(surface, (u, v))
                normal = _unit(normal, fallback=(0.0, 0.0, 1.0))

                points.append(point)
                sizes.append(size_hint)
                normals.append(normal)
                metadata.append(
                    {
                        "uv": (float(u), float(v)),
                        "skip_checks": bool(sampling["skip_checks"]),
                        "sampling_mode": sampling["sampling_mode"],
                        "center_u": bool(sampling["center_u"]),
                        "center_v": bool(sampling["center_v"]),
                        "sample_margin_u": float(sampling.get("sample_margin_u", 0.0)),
                        "sample_margin_v": float(sampling.get("sample_margin_v", 0.0)),
                    }
                )
    finally:
        for obj_id in cleanup_ids:
            if obj_id and rs.IsObject(obj_id):
                rs.DeleteObject(obj_id)

    if return_normals and return_metadata:
        return points, sizes, normals, metadata
    if return_normals:
        return points, sizes, normals
    if return_metadata:
        return points, sizes, metadata
    return points, sizes


def get_surfaces(
    object_ids=None,
    layer_names=None,
    convert_polylines=True,
    explode_polysurfaces=False,
    keep_input=True,
):
    """Collect surface-like geometry suitable for defect placement.

    Args:
        object_ids: Optional object ids to inspect. Defaults to all doc objects.
        layer_names: Optional layer name or list of names for filtering.
        convert_polylines: Convert closed curves to surfaces when possible.
        explode_polysurfaces: Explode polysurfaces into individual surfaces.
        keep_input: Keep original objects when converting/exploding.

    Returns:
        List of Rhino surface object ids.
    """
    if object_ids is None:
        object_ids = rs.AllObjects(select=False, include_lights=False, include_grips=False) or []
    else:
        object_ids = _as_list(object_ids)

    layer_filter = _normalize_layer_names(layer_names)
    surfaces = []

    for obj_id in object_ids:
        if not obj_id or not rs.IsObject(obj_id):
            continue
        if not _object_in_layers(obj_id, layer_filter):
            continue

        if rs.IsSurface(obj_id):
            surfaces.append(obj_id)
            continue

        if rs.IsPolysurface(obj_id):
            if explode_polysurfaces:
                exploded = rs.ExplodePolysurfaces(obj_id, delete_input=not bool(keep_input)) or []
                for sid in exploded:
                    if sid and rs.IsSurface(sid):
                        surfaces.append(sid)
            else:
                surfaces.append(obj_id)
            continue

        if convert_polylines and rs.IsCurve(obj_id) and rs.IsCurveClosed(obj_id):
            created = _curve_to_surface_ids(obj_id)
            if created:
                layer_name = rs.ObjectLayer(obj_id)
                for sid in created:
                    if layer_name and rs.IsLayer(layer_name):
                        rs.ObjectLayer(sid, layer_name)
                    surfaces.append(sid)
                if not keep_input and rs.IsObject(obj_id):
                    rs.DeleteObject(obj_id)

    return _dedupe_ids(surfaces)


def create_curve_from_file(filename, close_curve=True, scale=1.0, z=0.0, layer_name=None):
    """Create one or more polylines from a JSON file.

    Supported JSON shapes:
      1) {"points": [[x, y], ...]}
      2) {"contours": [{"points": [[x, y], ...]}, ...]}
      3) [[x, y], ...]
      4) [[[x, y], ...], ...]
    """
    if not filename or not os.path.isfile(filename):
        raise IOError("File not found: {}".format(filename))

    with open(filename, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    curves = []
    for points in extract_point_sets(data):
        if not points:
            continue
        poly_pts = []
        for point in points:
            px, py, pz = _xyz(point)
            poly_pts.append((px * float(scale), py * float(scale), pz * float(scale) + float(z)))
        if close_curve and _distance(poly_pts[0], poly_pts[-1]) > 1e-6:
            poly_pts.append(poly_pts[0])

        curve_id = rs.AddPolyline(poly_pts)
        if not curve_id:
            continue
        if layer_name and rs.IsLayer(layer_name):
            rs.ObjectLayer(curve_id, layer_name)
        curves.append(curve_id)
    return curves


def subtract_surface(curves, target_surfaces=None, delete_inputs=False):
    """Subtract/split target surfaces using closed defect curves.

    This helper tries boolean difference first; if that fails it falls back to
    brep split and returns produced pieces.
    """
    curve_ids = [cid for cid in _as_list(curves) if cid and rs.IsObject(cid)]
    if not curve_ids:
        return []

    if target_surfaces is None:
        target_surfaces = get_surfaces()
    target_surfaces = [sid for sid in _as_list(target_surfaces) if sid and rs.IsObject(sid)]
    if not target_surfaces:
        return []

    cutter_ids = []
    generated_cutters = []
    for cid in curve_ids:
        if rs.IsSurface(cid) or rs.IsPolysurface(cid):
            cutter_ids.append(cid)
            continue
        if rs.IsCurve(cid) and rs.IsCurveClosed(cid):
            surfaces = _curve_to_surface_ids(cid)
            cutter_ids.extend(surfaces)
            generated_cutters.extend(surfaces)

    cutter_ids = _dedupe_ids(cutter_ids)
    if not cutter_ids:
        return target_surfaces

    output_ids = []
    for target in target_surfaces:
        if not rs.IsObject(target):
            continue

        try:
            diff = rs.BooleanDifference(target, cutter_ids, delete_input=False)
        except Exception:
            diff = None
        if diff:
            output_ids.extend(_as_list(diff))
            if delete_inputs and rs.IsObject(target):
                rs.DeleteObject(target)
            continue

        # RhinoScript SplitBrep expects a single cutter id (not a list), so
        # split sequentially with each cutter and keep the current piece set.
        pieces = [target]
        for cutter_id in cutter_ids:
            next_pieces = []
            for piece_id in pieces:
                if not piece_id or not rs.IsObject(piece_id):
                    continue
                try:
                    split = rs.SplitBrep(piece_id, cutter_id, delete_input=bool(delete_inputs))
                except Exception:
                    split = None
                if split:
                    next_pieces.extend(_as_list(split))
                else:
                    next_pieces.append(piece_id)
            pieces = _dedupe_ids(next_pieces)
            if not pieces:
                break

        if pieces:
            output_ids.extend(pieces)
        else:
            output_ids.append(target)

    if generated_cutters and delete_inputs:
        for cutter in generated_cutters:
            if rs.IsObject(cutter):
                rs.DeleteObject(cutter)

    return _dedupe_ids(output_ids)


def modeling_spall(reference_points, reference_sizes):
    """Placeholder output structure for spall modeling workflow."""
    if len(reference_points) != len(reference_sizes):
        raise ValueError("reference_points and reference_sizes must have the same length.")
    return [
        {"point": _xyz(point), "size": float(size)}
        for point, size in zip(reference_points, reference_sizes)
    ]


def modeling_rebar(left, right, top, bottom):
    """Placeholder descriptor for rebar extent boundaries."""
    return {
        "left": float(left),
        "right": float(right),
        "top": float(top),
        "bottom": float(bottom),
    }


def modeling_efflore(reference_points, reference_sizes):
    """Placeholder output structure for efflorescence workflow."""
    if len(reference_points) != len(reference_sizes):
        raise ValueError("reference_points and reference_sizes must have the same length.")
    return [
        {"point": _xyz(point), "thickness": max(0.1, float(size) * 0.01)}
        for point, size in zip(reference_points, reference_sizes)
    ]


if __name__ == "__main__":
    pass
