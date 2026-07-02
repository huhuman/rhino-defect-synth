"""Crack modeling helpers shared by cube and component pipelines."""

import math
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


def _offset_ring_inward(ring_id, distance):
    """Offset a closed planar ring toward its interior by ``distance``.

    Returns a single new closed-curve id, or None when the offset cannot be made
    cleanly (thin crack collapses, self-intersects, or splits into several curves).
    Callers treat None as "skip the taper and keep a vertical wall".
    """
    if not ring_id or distance <= 0.0:
        return None
    centroid = rs.CurveAreaCentroid(ring_id)
    if not centroid:
        return None
    try:
        offset = rs.OffsetCurve(ring_id, centroid[0], distance)
    except Exception:
        offset = None
    ids = _coerce_ids(offset)
    if len(ids) != 1 or not rs.IsCurveClosed(ids[0]):
        _delete_objects(ids)
        return None
    return ids[0]


def _resolve_wall_slope(value, rng):
    """Resolve the wall_slope_deg config to a single clamped angle in degrees.

    Accepts a scalar (fixed slope) or a ``[min, max]`` range, which is sampled per
    crack so each crack gets its own taper for variety. Returns 0.0 (vertical) for
    None / empty / unparseable input.
    """
    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            lo, hi = float(value[0]), float(value[1])
            if lo > hi:
                lo, hi = hi, lo
            value = rng.uniform(lo, hi)
        elif len(value) == 1:
            value = value[0]
        else:
            return 0.0
    try:
        slope = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(60.0, slope))


def _resolve_taper_spec(wall_slope_deg, rng):
    """Decide how the deep cut tapers, from the wall_slope_deg config.

    Returns one of:
      None                 -> no taper (vertical walls)
      ("auto", lo, hi)     -> per-crack taper sized to each ring's own width; the
                              inward offset is a random fraction in [lo, hi] of the
                              ring's half-width, so no manual angle is needed and
                              every crack stays within its own feasible range.
      ("slope", angle)     -> fixed/explicit angle off vertical (legacy behaviour).
    """
    if wall_slope_deg is None:
        return None
    if isinstance(wall_slope_deg, str):
        return ("auto", 0.3, 0.8) if wall_slope_deg.strip().lower() == "auto" else None
    slope = _resolve_wall_slope(wall_slope_deg, rng)
    return ("slope", slope) if slope > 0.0 else None


def _make_bottom_ring(crack_poly, taper_spec, deep_span, rng):
    """Build the narrowed bottom ring for one outer crack ring, or None to stay vertical.

    For "auto" the target inward offset is derived from the ring's own characteristic
    width (2*area/perimeter) and then probed downward, so a thin neck only limits the
    taper instead of killing it outright.
    """
    if not taper_spec:
        return None
    mode = taper_spec[0]
    if mode == "slope":
        dist = deep_span * math.tan(math.radians(taper_spec[1]))
        return _offset_ring_inward(crack_poly, dist) if dist > 0.0 else None
    if mode == "auto":
        area = rs.CurveArea(crack_poly)
        length = rs.CurveLength(crack_poly)
        if not area or not length:
            return None
        char_width = 2.0 * abs(area[0]) / length
        target = rng.uniform(taper_spec[1], taper_spec[2]) * (char_width / 2.0)
        dist = target
        for _ in range(4):
            if dist <= 0.0:
                break
            ring = _offset_ring_inward(crack_poly, dist)
            if ring is not None:
                return ring
            dist *= 0.5
    return None


def create_crack(
    crack_polys,
    crack_inside_polys,
    base_poly,
    offset_poly,
    diff_polys,
    inward_dir=None,
    d1_range=(0.5, 2.5),
    delta_depth_range=(10.0, 30.0),
    wall_slope_deg=None,
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
        base_poly: Base crack outline curve id.
        offset_poly: Optional outer crack area curve id. When omitted, cracks extrude from the surface.
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

    has_offset_poly = bool(offset_poly and rs.IsPolyline(offset_poly))
    if not crack_polys:
        print("create_crack: crack_polys must contain valid polylines.")
        return None
    if has_offset_poly and (not base_poly or not rs.IsPolyline(base_poly)):
        print("create_crack: base_poly must be a valid polyline when offset_poly is provided.")
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
        loft_ids = []
        if has_offset_poly:
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
        crack_start_vector = vec_delta if has_offset_poly else vec_d2
        crack_move_vector = vec_d1 if has_offset_poly else None

        # Optional V/U cross-section: taper the deep cut to a narrower bottom ring so the
        # groove widens toward the surface. Only applied to hole-free cracks (annulus
        # cracks keep vertical walls so the taper can't interfere with the island), and
        # any ring whose inward offset collapses falls back to a vertical wall.
        deep_span = (d2 - d1) if has_offset_poly else d2
        taper_spec = _resolve_taper_spec(wall_slope_deg, rng) if not inside_polys else None

        tapered_bottom_rings = []
        for crack_poly in crack_polys:
            start = rs.CurveStartPoint(crack_poly)
            if not start:
                continue

            bottom_ring = _make_bottom_ring(crack_poly, taper_spec, deep_span, rng)
            if bottom_ring is not None:
                top_ring = rs.CopyObject(crack_poly)
                if crack_move_vector:
                    rs.MoveObject(top_ring, crack_move_vector)
                rs.MoveObject(bottom_ring, vec_d2)
                wall_ids = _coerce_ids(rs.AddLoftSrf([top_ring, bottom_ring]) or [])
                _delete_objects([top_ring])
                if wall_ids:
                    extrusions.extend(wall_ids)
                    tapered_bottom_rings.append(bottom_ring)
                    continue
                _delete_objects([bottom_ring])

            end = rs.PointAdd(start, crack_start_vector)
            extrusion_ids = _coerce_ids([rs.ExtrudeCurveStraight(crack_poly, start, end)])
            if extrusion_ids:
                if crack_move_vector:
                    rs.MoveObjects(extrusion_ids, crack_move_vector)
                extrusions.extend(extrusion_ids)

        # Bottom cap(s). With a taper the cap follows the narrowed bottom rings (already at
        # depth d2). Otherwise feed the inner (hole) rings to AddPlanarSrf together with the
        # outer rings so Rhino subtracts them by containment -> the cap is an annulus and the
        # enclosed island is left at the surface instead of being buried. A failed annular
        # cap falls back to a solid cap so the crack is not lost (reported for the generator).
        if tapered_bottom_rings:
            cap_ids = _coerce_ids(rs.AddPlanarSrf(tapered_bottom_rings) or [])
            _delete_objects(tapered_bottom_rings)
            bottom_caps.extend(cap_ids)
        else:
            cap_curves = list(crack_polys) + list(inside_polys)
            cap_ids = _coerce_ids(rs.AddPlanarSrf(cap_curves) or []) if cap_curves else []
            if not cap_ids and inside_polys:
                print("create_crack: annular bottom cap failed for {} hole(s); "
                      "carving solid cap (island NOT preserved).".format(len(inside_polys)))
                cap_ids = _coerce_ids(rs.AddPlanarSrf(crack_polys) or [])
            if cap_ids:
                rs.MoveObjects(cap_ids, vec_d2)
                bottom_caps.extend(cap_ids)

        inside_extrusions = []
        inside_caps = []
        for sub_poly in inside_polys:
            start = rs.CurveStartPoint(sub_poly)
            if start:
                inside_vector = vec_delta if has_offset_poly else vec_d2
                end = rs.PointAdd(start, inside_vector)
                inside_extrusion_ids = _coerce_ids([rs.ExtrudeCurveStraight(sub_poly, start, end)])
                if inside_extrusion_ids:
                    if has_offset_poly:
                        rs.MoveObjects(inside_extrusion_ids, vec_d1)
                    inside_extrusions.extend(inside_extrusion_ids)
            if has_offset_poly:
                cap_ids = _coerce_ids(rs.AddPlanarSrf(sub_poly) or [])
                if cap_ids:
                    rs.MoveObjects(cap_ids, vec_d1)
                    inside_caps.extend(cap_ids)

        crack_geometry_ids = []
        crack_geometry_ids.extend(extrusions)
        crack_geometry_ids.extend(bottom_caps)

        # diff_fills patch the transition band on the parent surface; island_fills are the
        # raised plugs that reconstruct the intact middle of an annulus (closed-loop) crack.
        # They are split so callers can keep islands while still discarding the diff patches.
        diff_fills = list(diff_surfaces)
        island_fills = list(inside_extrusions) + list(inside_caps)
        parent_fill_ids = diff_fills + island_fills

        _assign_layer(loft_ids, layer_erosion or layer_crack_extrusion)
        _assign_layer(crack_geometry_ids, layer_crack_extrusion)
        _assign_layer(parent_fill_ids, layer_parent_surface)

        return {
            "loft": loft_ids,
            "extrusions": extrusions,
            "bottom_caps": bottom_caps,
            "parent_fills": parent_fill_ids,
            "diff_fills": diff_fills,
            "island_fills": island_fills,
            "d1": d1 if has_offset_poly else 0.0,
            "d2": d2,
        }
    finally:
        if cleanup_inputs:
            _delete_objects(cleanup_ids)
        if disable_redraw and redraw_was_enabled:
            rs.EnableRedraw(True)
