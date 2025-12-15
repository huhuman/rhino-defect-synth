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


def create_crack(crack_polys, crack_inside_polys, base_poly, offset_poly, diff_polys, inward_dir=None):
    cleanup_ids = []
    crack_polys = [bp for bp in crack_polys or [] if bp and rs.IsPolyline(bp)]
    crack_inside_polys = [sp for sp in crack_inside_polys or [] if sp and rs.IsPolyline(sp)]
    if not crack_polys or not base_poly or not offset_poly or not rs.IsPolyline(offset_poly):
        print("create_crack: crack_polys, base_poly, and offset_poly must be polylines.")
        return None

    direction = inward_dir or _poly_inward_direction(base_poly)
    if not direction:
        print("create_crack: failed to determine inward direction.")
        return None
    d1 = random.uniform(0.5, 2.5)  # mm
    d2 = d1 + random.uniform(10, 30)  # mm

    vec_d1 = rs.VectorScale(direction, d1)
    vec_delta = rs.VectorScale(direction, d2 - d1)
    
    diff_surfaces = []
    for diff_poly in diff_polys:
        diff_curve = rs.CopyObject(diff_poly, vec_d1)
        diff_srf = rs.AddPlanarSrf(diff_curve)
        cleanup_ids.append(diff_curve)
        if diff_srf:
            diff_surfaces.append(diff_srf)
    cleanup_ids += diff_polys

    base_bottom_curve = rs.CopyObject(base_poly, vec_d1)
    cleanup_ids.append(base_poly)
    if not base_bottom_curve:
        print("create_crack: failed to offset base curve.")
        return None

    if not rs.CurveDirectionsMatch(offset_poly, base_bottom_curve):
        rs.ReverseCurve(base_bottom_curve)
    rs.CurveSeam(base_bottom_curve, rs.CurveClosestPoint(base_bottom_curve, rs.CurveStartPoint(offset_poly)))

    loft_ids = rs.AddLoftSrf([offset_poly, base_bottom_curve])
    cleanup_ids.append(base_bottom_curve)

    # Extrude each base poly individually
    extrusions = []
    bottom_caps = []
    for crack_poly in crack_polys:
        deep_poly = rs.CopyObject(crack_poly, vec_d1)
        if not deep_poly:
            continue
        start = rs.CurveStartPoint(deep_poly)
        end = rs.PointAdd(start, vec_delta)
        extrusion = rs.ExtrudeCurveStraight(deep_poly, start, end)
        if extrusion:
            extrusions.append(extrusion)
        bottom_cap = rs.AddPlanarSrf(rs.CopyObject(deep_poly, vec_delta))
        if bottom_cap:
            bottom_caps.append(bottom_cap)
        if rs.IsObject(deep_poly):
            rs.DeleteObject(deep_poly)

    # Extrude inner subtract curves to same depth and thickness.
    inner_extrusions = []
    helper_curves = []
    for sub_poly in crack_inside_polys:
        shifted = rs.CopyObject(sub_poly, vec_d1)
        if not shifted:
            continue
        helper_curves.append(shifted)
        s_pt = rs.CurveStartPoint(shifted)
        e_pt = rs.PointAdd(s_pt, vec_delta)
        inside_extrusion = rs.ExtrudeCurveStraight(shifted, s_pt, e_pt)
        if inside_extrusion:
            inner_extrusions.append(inside_extrusion)
        helper_curves.append(shifted)
        cap = rs.AddPlanarSrf(shifted)
        if cap:
            inner_extrusions.extend(cap)
    cleanup_ids += helper_curves
    cleanup_ids += crack_inside_polys
    
    # place geometry on crack_extrusion layer if it exists
    if rs.IsLayer("crack_extrusion"):
        targets = []
        targets.extend(loft_ids or [])
        if extrusion:
            targets.append(extrusion)
        if bottom_cap:
            targets.append(bottom_cap)
        for obj in targets:
            if not obj:
                continue
            obj_id = getattr(obj, "Id", obj)
            obj_id = rs.coerceguid(obj_id, False)
            if obj_id:
                rs.ObjectLayer(obj_id, "crack_extrusion")
    if rs.IsLayer("cube"):
        for obj in inner_extrusions:
            if not obj:
                continue
            obj_id = getattr(obj, "Id", obj)
            obj_id = rs.coerceguid(obj_id, False)
            if obj_id:
                rs.ObjectLayer(obj_id, "cube")
        for obj in diff_surfaces:
            if not obj:
                continue
            obj_id = getattr(obj, "Id", obj)
            obj_id = rs.coerceguid(obj_id, False)
            if obj_id:
                rs.ObjectLayer(obj_id, "cube")

    cleanup_ids.append(offset_poly)
    cleanup_ids = set(cleanup_ids)
    for cid in cleanup_ids:
        if cid and rs.IsObject(cid):
            rs.DeleteObject(cid)

    created = {"loft": loft_ids, "extrusion": extrusion}
    return created
