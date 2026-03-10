"""Crack modeling helpers shared by cube and component pipelines."""

import random

import rhinoscriptsyntax as rs


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


def _delete_objects(obj_ids):
    ids = list(set(_coerce_ids(obj_ids)))
    if ids:
        rs.DeleteObjects(ids)


def create_crack(
    crack_polys,
    crack_inside_polys,
    base_poly,
    offset_poly,
    diff_polys,
    inward_dir=None,
    d1_range=(0.5, 2.5),
    delta_depth_range=(10.0, 30.0),
    layer_crack_extrusion="geometry::crack",
    layer_erosion="cube::erosion",
    layer_parent_surface="geometry::cube",
    cleanup_inputs=True,
    rng=None,
    disable_redraw=True,
):
    """Create crack geometry from projected polygon curves.

    Args:
        crack_polys: Crack boundary curve ids.
        crack_inside_polys: Inner (hole) curve ids.
        base_poly: Base crack footprint curve id.
        offset_poly: Outer crack area curve id.
        diff_polys: Difference polygons used to patch parent surface.
        inward_dir: Required inward vector from sampled surface normal.
        d1_range: (min, max) shallow inward depth.
        delta_depth_range: (min, max) extra depth after d1.
        layer_crack_extrusion: Layer for crack solids/surfaces.
        layer_erosion: Layer for erode-poly loft shell geometry.
        layer_parent_surface: Layer for helper fills on parent surface.
        cleanup_inputs: Delete input/helper curves after creation.
        disable_redraw: Temporarily disable viewport redraw for faster batch ops.
    """
    crack_polys = [cid for cid in crack_polys or [] if cid and rs.IsPolyline(cid)]
    inside_polys = [cid for cid in crack_inside_polys or [] if cid and rs.IsPolyline(cid)]
    diff_polys = [cid for cid in diff_polys or [] if cid and rs.IsPolyline(cid)]

    if (
        not crack_polys
        or not base_poly
        or not offset_poly
        or not rs.IsPolyline(base_poly)
        or not rs.IsPolyline(offset_poly)
    ):
        print("create_crack: crack_polys, base_poly, and offset_poly must be valid polylines.")
        return None

    direction = rs.VectorUnitize(inward_dir) if inward_dir is not None else None
    if not direction:
        print("create_crack: inward_dir is required and must be a valid vector.")
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
    vec_d2 = rs.VectorAdd(vec_d1, vec_delta)

    cleanup_ids = list(crack_polys) + list(inside_polys) + [base_poly, offset_poly] + list(diff_polys)
    redraw_was_enabled = False

    try:
        if disable_redraw:
            redraw_was_enabled = bool(rs.EnableRedraw(False))

        diff_surfaces = []
        for diff_poly in diff_polys:
            surfaces = _coerce_ids(rs.AddPlanarSrf(diff_poly) or [])
            if not surfaces:
                continue
            rs.MoveObjects(surfaces, vec_d1)
            diff_surfaces.extend(surfaces)

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
            start = rs.CurveStartPoint(crack_poly)
            if not start:
                continue
            end = rs.PointAdd(start, vec_delta)
            extrusion_ids = _coerce_ids([rs.ExtrudeCurveStraight(crack_poly, start, end)])
            if extrusion_ids:
                rs.MoveObjects(extrusion_ids, vec_d1)
                extrusions.extend(extrusion_ids)

            cap_ids = _coerce_ids(rs.AddPlanarSrf(crack_poly) or [])
            if cap_ids:
                rs.MoveObjects(cap_ids, vec_d2)
                bottom_caps.extend(cap_ids)

        inside_extrusions = []
        inside_caps = []
        for sub_poly in inside_polys:
            start = rs.CurveStartPoint(sub_poly)
            if start:
                end = rs.PointAdd(start, vec_delta)
                inside_extrusion_ids = _coerce_ids([rs.ExtrudeCurveStraight(sub_poly, start, end)])
                if inside_extrusion_ids:
                    rs.MoveObjects(inside_extrusion_ids, vec_d1)
                    inside_extrusions.extend(inside_extrusion_ids)
            cap_ids = _coerce_ids(rs.AddPlanarSrf(sub_poly) or [])
            if cap_ids:
                rs.MoveObjects(cap_ids, vec_d1)
                inside_caps.extend(cap_ids)

        crack_geometry_ids = []
        crack_geometry_ids.extend(extrusions)
        crack_geometry_ids.extend(bottom_caps)

        parent_fill_ids = []
        parent_fill_ids.extend(diff_surfaces)
        parent_fill_ids.extend(inside_extrusions)
        parent_fill_ids.extend(inside_caps)

        _assign_layer(loft_ids, layer_erosion or layer_crack_extrusion)
        _assign_layer(crack_geometry_ids, layer_crack_extrusion)
        _assign_layer(parent_fill_ids, layer_parent_surface)

        return {
            "loft": loft_ids,
            "extrusions": extrusions,
            "bottom_caps": bottom_caps,
            "parent_fills": parent_fill_ids,
            "d1": d1,
            "d2": d2,
        }
    finally:
        if cleanup_inputs:
            _delete_objects(cleanup_ids)
        if disable_redraw and redraw_was_enabled:
            rs.EnableRedraw(True)
