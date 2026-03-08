
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


def get_reference_points(
    surface,
    sample_count_u=3,
    sample_count_v=3,
    trim_margin=0.1,
    return_normals=False,
):
    """Sample candidate defect reference points on one surface.

    Args:
        surface: Rhino surface object id.
        sample_count_u: Number of samples along U domain.
        sample_count_v: Number of samples along V domain.
        trim_margin: Domain margin ratio to avoid exact boundaries.
        return_normals: When True, also return sampled normals.

    Returns:
        Tuple of (points, sizes) or (points, sizes, normals).
    """
    if not surface or not rs.IsObject(surface):
        return ([], [], []) if return_normals else ([], [])

    cleanup_ids = []
    if rs.IsPolysurface(surface):
        exploded = rs.ExplodePolysurfaces(surface, delete_input=False) or []
        if not exploded:
            return ([], [], []) if return_normals else ([], [])
        surface = exploded[0]
        cleanup_ids = exploded

    try:
        if not rs.IsSurface(surface):
            return ([], [], []) if return_normals else ([], [])

        domain_u = rs.SurfaceDomain(surface, 0)
        domain_v = rs.SurfaceDomain(surface, 1)
        if not domain_u or not domain_v:
            return ([], [], []) if return_normals else ([], [])

        su = max(1, int(sample_count_u))
        sv = max(1, int(sample_count_v))
        u_values = _sample_domain(domain_u, su, trim_margin)
        v_values = _sample_domain(domain_v, sv, trim_margin)

        points = []
        normals = []
        size_hint = _size_hint_for_surface(surface, su * sv)
        sizes = []

        for u in u_values:
            for v in v_values:
                point = rs.EvaluateSurface(surface, u, v)
                if not point:
                    continue
                point = _xyz(point)
                if not rs.IsPointOnSurface(surface, point):
                    continue
                normal = rs.SurfaceNormal(surface, (u, v))
                normal = _unit(normal, fallback=(0.0, 0.0, 1.0))

                points.append(point)
                sizes.append(size_hint)
                normals.append(normal)
    finally:
        for obj_id in cleanup_ids:
            if obj_id and rs.IsObject(obj_id):
                rs.DeleteObject(obj_id)

    if return_normals:
        return points, sizes, normals
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

        diff = rs.BooleanDifference(target, cutter_ids, delete_input=False)
        if diff:
            output_ids.extend(_as_list(diff))
            if delete_inputs and rs.IsObject(target):
                rs.DeleteObject(target)
            continue

        split = rs.SplitBrep(target, cutter_ids, delete_input=False)
        if split:
            output_ids.extend(_as_list(split))
            if delete_inputs and rs.IsObject(target):
                rs.DeleteObject(target)
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
