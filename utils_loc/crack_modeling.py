"""Crack modeling helpers shared by cube and component pipelines."""

import random

import rhinoscriptsyntax as rs


def _poly_inward_direction(curve_id):
    """Approximate inward direction using curve normal oriented toward origin."""
    normal = rs.CurveNormal(curve_id)
    if not normal:
        return None

    center_data = rs.CurveAreaCentroid(curve_id)
    if not center_data:
        return None
    centroid = center_data[0]

    to_origin = rs.VectorCreate((0, 0, 0), centroid)
    dot = rs.VectorDotProduct(normal, to_origin)
    if dot < 0:
        normal = rs.VectorReverse(normal)

    normal = rs.VectorUnitize(normal)
    return normal


def _coerce_ids(items):
    obj_ids = []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, (list, tuple)):
            obj_ids.extend(_coerce_ids(item))
            continue
        obj_id = getattr(item, "Id", item)
        obj_id = rs.coerceguid(obj_id, False)
        if obj_id:
            obj_ids.append(obj_id)
    return obj_ids


def _assign_layer(obj_ids, layer_name):
    if not layer_name or not rs.IsLayer(layer_name):
        return
    for obj_id in _coerce_ids(obj_ids):
        if rs.IsObject(obj_id):
            rs.ObjectLayer(obj_id, layer_name)


def create_crack(
    crack_polys,
    crack_inside_polys,
    base_poly,
    offset_poly,
    diff_polys,
    inward_dir=None,
    d1_range=(0.5, 2.5),
    delta_depth_range=(10.0, 30.0),
    layer_crack_extrusion="crack_extrusion",
    layer_parent_surface="cube",
    cleanup_inputs=True,
    rng=None,
):
    """Create crack geometry from projected polygon curves.

    Args:
        crack_polys: Crack boundary curve ids.
        crack_inside_polys: Inner (hole) curve ids.
        base_poly: Base crack footprint curve id.
        offset_poly: Outer crack area curve id.
        diff_polys: Difference polygons used to patch parent surface.
        inward_dir: Optional explicit inward vector.
        d1_range: (min, max) shallow inward depth.
        delta_depth_range: (min, max) extra depth after d1.
        layer_crack_extrusion: Layer for generated crack solids/surfaces.
        layer_parent_surface: Layer for helper fills on parent surface.
        cleanup_inputs: Delete input/helper curves after creation.
    """
    crack_polys = [cid for cid in crack_polys or [] if cid and rs.IsPolyline(cid)]
    inside_polys = [cid for cid in crack_inside_polys or [] if cid and rs.IsPolyline(cid)]
    diff_polys = [cid for cid in diff_polys or [] if cid and rs.IsPolyline(cid)]

    if not crack_polys or not base_poly or not offset_poly or not rs.IsPolyline(offset_poly):
        print("create_crack: crack_polys, base_poly, and offset_poly must be valid polylines.")
        return None

    direction = inward_dir or _poly_inward_direction(base_poly)
    if not direction:
        print("create_crack: failed to determine inward direction.")
        return None

    d1_min, d1_max = float(d1_range[0]), float(d1_range[1])
    if d1_min > d1_max:
        d1_min, d1_max = d1_max, d1_min
    depth_extra_min, depth_extra_max = float(delta_depth_range[0]), float(delta_depth_range[1])
    if depth_extra_min > depth_extra_max:
        depth_extra_min, depth_extra_max = depth_extra_max, depth_extra_min

    rng = random if rng is None else rng
    d1 = rng.uniform(d1_min, d1_max)
    d2 = d1 + rng.uniform(depth_extra_min, depth_extra_max)
    vec_d1 = rs.VectorScale(direction, d1)
    vec_delta = rs.VectorScale(direction, d2 - d1)

    cleanup_ids = list(crack_polys) + list(inside_polys) + [base_poly, offset_poly] + list(diff_polys)

    diff_surfaces = []
    for diff_poly in diff_polys:
        shifted_curve = rs.CopyObject(diff_poly, vec_d1)
        if not shifted_curve:
            continue
        cleanup_ids.append(shifted_curve)
        surfaces = rs.AddPlanarSrf(shifted_curve) or []
        diff_surfaces.extend(_coerce_ids(surfaces))

    base_bottom_curve = rs.CopyObject(base_poly, vec_d1)
    if not base_bottom_curve:
        print("create_crack: failed to offset base curve.")
        return None
    cleanup_ids.append(base_bottom_curve)

    if not rs.CurveDirectionsMatch(offset_poly, base_bottom_curve):
        rs.ReverseCurve(base_bottom_curve)
    seam_param = rs.CurveClosestPoint(base_bottom_curve, rs.CurveStartPoint(offset_poly))
    if seam_param is not None:
        rs.CurveSeam(base_bottom_curve, seam_param)

    loft_ids = _coerce_ids(rs.AddLoftSrf([offset_poly, base_bottom_curve]) or [])

    extrusions = []
    bottom_caps = []
    for crack_poly in crack_polys:
        deep_poly = rs.CopyObject(crack_poly, vec_d1)
        if not deep_poly:
            continue
        start = rs.CurveStartPoint(deep_poly)
        end = rs.PointAdd(start, vec_delta)
        extrusion = rs.ExtrudeCurveStraight(deep_poly, start, end)
        extrusions.extend(_coerce_ids([extrusion]))

        bottom_curve = rs.CopyObject(deep_poly, vec_delta)
        cleanup_ids.append(bottom_curve)
        cap_ids = rs.AddPlanarSrf(bottom_curve) if bottom_curve else []
        bottom_caps.extend(_coerce_ids(cap_ids))

        if rs.IsObject(deep_poly):
            rs.DeleteObject(deep_poly)

    inside_extrusions = []
    inside_caps = []
    helper_curves = []
    for sub_poly in inside_polys:
        shifted = rs.CopyObject(sub_poly, vec_d1)
        if not shifted:
            continue
        helper_curves.append(shifted)
        start = rs.CurveStartPoint(shifted)
        end = rs.PointAdd(start, vec_delta)
        inside_extrusion = rs.ExtrudeCurveStraight(shifted, start, end)
        inside_extrusions.extend(_coerce_ids([inside_extrusion]))
        inside_caps.extend(_coerce_ids(rs.AddPlanarSrf(shifted) or []))
    cleanup_ids.extend(helper_curves)

    crack_geometry_ids = []
    crack_geometry_ids.extend(loft_ids)
    crack_geometry_ids.extend(extrusions)
    crack_geometry_ids.extend(bottom_caps)

    parent_fill_ids = []
    parent_fill_ids.extend(diff_surfaces)
    parent_fill_ids.extend(inside_extrusions)
    parent_fill_ids.extend(inside_caps)

    _assign_layer(crack_geometry_ids, layer_crack_extrusion)
    _assign_layer(parent_fill_ids, layer_parent_surface)

    if cleanup_inputs:
        for cid in set(_coerce_ids(cleanup_ids)):
            if cid and rs.IsObject(cid):
                rs.DeleteObject(cid)

    return {
        "loft": loft_ids,
        "extrusions": extrusions,
        "bottom_caps": bottom_caps,
        "parent_fills": parent_fill_ids,
        "d1": d1,
        "d2": d2,
    }
