"""Crack-specific geometry modeling helpers for defect placement."""


def _crack_tangent_3d(points):
    """Approximate the crack's LENGTH direction (unit 3D vector) from its surface-cut points via a
    2-pass diameter (point farthest from centroid -> farthest from that). Currently only used for a
    load-status diagnostic counter. Returns None when degenerate."""
    pts = []
    for p in points or []:
        if p is None:
            continue
        try:
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(pts) < 2:
        return None
    n = len(pts)
    c = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)

    def _d2(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2

    p1 = max(pts, key=lambda p: _d2(p, c))
    p2 = max(pts, key=lambda p: _d2(p, p1))
    t = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
    m = (t[0] ** 2 + t[1] ** 2 + t[2] ** 2) ** 0.5
    if m < 1e-9:
        return None
    return [t[0] / m, t[1] / m, t[2] / m]


def _resolve_crack_width_metrics(runtime, shape):
    width_px = runtime._to_optional_float((shape or {}).get("metric_px"))
    if width_px is None or width_px <= 0.0:
        width_px = runtime._to_optional_float((shape or {}).get("width_px"))
    if width_px is None or width_px <= 0.0:
        return None

    px_to_cm = runtime._to_optional_float((shape or {}).get("metric_scale"))
    if px_to_cm is None or px_to_cm <= 0.0:
        return None

    width_cm = float(width_px) * float(px_to_cm)
    return {
        "width_px": float(width_px),
        "px_to_cm": float(px_to_cm),
        "width_cm": float(width_cm),
    }


def _resolve_crack_width_thresholds(runtime, crack_cfg):
    crack_cfg = crack_cfg or {}
    t1 = runtime._to_optional_float(crack_cfg.get("t1"))
    t2 = runtime._to_optional_float(crack_cfg.get("t2"))
    if t1 is None:
        t1 = runtime._to_optional_float(crack_cfg.get("cs2_width_cm_threshold"))
    if t2 is None:
        t2 = runtime._to_optional_float(crack_cfg.get("cs3_width_cm_threshold"))
    if t1 is None:
        t1 = 0.1
    if t2 is None:
        t2 = 0.2
    t1 = max(0.0, float(t1))
    t2 = max(0.0, float(t2))
    if t1 > t2:
        t1, t2 = t2, t1
    return t1, t2


def _resolve_crack_condition_state(runtime, crack_cfg, crack_created, crack_width_metrics=None):
    crack_cfg = crack_cfg or {}
    width_cm = runtime._to_optional_float((crack_width_metrics or {}).get("width_cm"))
    if width_cm is not None:
        t1, t2 = _resolve_crack_width_thresholds(runtime, crack_cfg)
        if width_cm < t1:
            return "CS1"
        if width_cm < t2:
            return "CS2"
        return "CS3"

    d1 = runtime._to_optional_float((crack_created or {}).get("d1"), default=0.0) or 0.0
    cs2_threshold = runtime._to_float(crack_cfg.get("cs2_d1_threshold"), 1.0)
    cs3_threshold = runtime._to_float(crack_cfg.get("cs3_d1_threshold"), 2.0)
    if cs2_threshold > cs3_threshold:
        cs2_threshold, cs3_threshold = cs3_threshold, cs2_threshold
    if d1 >= cs3_threshold:
        return "CS3"
    if d1 >= cs2_threshold:
        return "CS2"
    return "CS1"


def model_crack_instance(runtime, candidate, shape, transform, cfg, layer_map, rng, defect_cfg=None, debug_cfg=None):
    offset_2d, base_2d, crack_2d, inside_2d, diff_2d = runtime._pick_shape_points(shape)
    cut_2d = runtime._select_crack_surface_cut_points(shape, crack_2d, default_points=base_2d or offset_2d)
    surface_cut_polygons = [
        runtime._project_points_to_surface(points, candidate, transform["angle_deg"], normal_offset=0.0)
        for points in crack_2d
        if len(points) >= 3
    ]
    offset_3d = runtime._project_points_to_surface(
        offset_2d,
        candidate,
        transform["angle_deg"],
        transform["normal_offset"],
    )
    base_3d = runtime._project_points_to_surface(
        base_2d,
        candidate,
        transform["angle_deg"],
        transform["normal_offset"],
    )
    surface_cut_polygon = runtime._project_points_to_surface(
        cut_2d,
        candidate,
        transform["angle_deg"],
        normal_offset=0.0,
    )

    crack_polys = []
    for points in crack_2d:
        poly = runtime._project_points_to_surface(points, candidate, transform["angle_deg"], transform["normal_offset"])
        curve_id = runtime._add_polyline(poly)
        if curve_id:
            crack_polys.append(curve_id)

    inside_polys = []
    for points in inside_2d:
        poly = runtime._project_points_to_surface(points, candidate, transform["angle_deg"], transform["normal_offset"])
        curve_id = runtime._add_polyline(poly)
        if curve_id:
            inside_polys.append(curve_id)

    diff_polys = []
    for points in diff_2d:
        poly = runtime._project_points_to_surface(points, candidate, transform["angle_deg"], transform["normal_offset"])
        curve_id = runtime._add_polyline(poly)
        if curve_id:
            diff_polys.append(curve_id)

    offset_curve = runtime._add_polyline(offset_3d)
    base_curve = runtime._add_polyline(base_3d)
    if not offset_curve or not base_curve or not crack_polys:
        for obj_id in runtime._coerce_ids(crack_polys + inside_polys + diff_polys + [offset_curve, base_curve]):
            if runtime.rs.IsObject(obj_id):
                runtime.rs.DeleteObject(obj_id)
        return None

    crack_layer_initial = runtime._geometry_layer_for_condition(layer_map, "crack", "CS1")
    crack_cfg = defect_cfg if isinstance(defect_cfg, dict) else (cfg.get("crack") or {})
    crack_created = runtime.create_crack(
        crack_polys,
        inside_polys,
        base_curve,
        offset_curve,
        diff_polys,
        inward_dir=runtime._candidate_inward_normal(candidate),
        d1_range=tuple(crack_cfg.get("d1_range", (0.5, 2.5))),
        delta_depth_range=tuple(crack_cfg.get("delta_depth_range", (10.0, 30.0))),
        wall_slope_deg=crack_cfg.get("wall_slope_deg"),
        layer_crack_extrusion=crack_layer_initial,
        layer_parent_surface=candidate.get("surface_layer"),
        cleanup_inputs=True,
        rng=rng,
    ) or {}
    outward_normal = runtime._candidate_outward_normal(candidate)
    runtime._orient_surfaces_to_normal(crack_created.get("extrusions") or [], outward_normal)
    runtime._orient_surfaces_to_normal(crack_created.get("bottom_caps") or [], outward_normal)
    runtime._orient_surfaces_to_normal(crack_created.get("island_fills") or [], outward_normal)
    crack_geometry = runtime._coerce_ids(
        (crack_created.get("loft") or [])
        + (crack_created.get("extrusions") or [])
        + (crack_created.get("bottom_caps") or [])
    )
    # Keep the middle islands of closed-loop (annulus) cracks as intact surface geometry
    # (already layered onto the parent surface); only discard the transition-band patches.
    island_fills = runtime._coerce_ids(crack_created.get("island_fills") or [])
    diff_fills = runtime._coerce_ids(crack_created.get("diff_fills") or [])
    runtime._delete_objects(diff_fills)
    if not crack_geometry:
        runtime._delete_objects(
            crack_polys
            + inside_polys
            + diff_polys
            + [offset_curve, base_curve]
            + island_fills
            + diff_fills
        )
        return None

    crack_width_metrics = _resolve_crack_width_metrics(runtime, shape)
    shape_cs_level = str(shape.get("condition_state") or "").strip().upper()
    if shape_cs_level in ("CS1", "CS2", "CS3"):
        cs_level = shape_cs_level
    else:
        cs_level = _resolve_crack_condition_state(
            runtime,
            crack_cfg,
            crack_created,
            crack_width_metrics=crack_width_metrics,
        )
    crack_layer = runtime._geometry_layer_for_condition(layer_map, "crack", cs_level)
    runtime._assign_layer(crack_geometry, crack_layer)

    record = runtime._record_common("crack", candidate, transform, shape)
    record["condition_state"] = cs_level
    record["geometry_ids"] = runtime._as_strings(crack_geometry)
    record["mask_ids"] = []
    record["surface_cut_polygon"] = [list(runtime._vec3(pt)) for pt in runtime._ensure_closed(surface_cut_polygon)]
    record["crack_tangent"] = _crack_tangent_3d(record["surface_cut_polygon"])
    record["surface_cut_polygons"] = [
        [list(runtime._vec3(pt)) for pt in runtime._ensure_closed(polygon)]
        for polygon in surface_cut_polygons
        if len(polygon) >= 4
    ]
    record["crack_metrics"] = {
        "d1": crack_created.get("d1"),
        "d2": crack_created.get("d2"),
    }
    if crack_width_metrics:
        t1, t2 = _resolve_crack_width_thresholds(runtime, crack_cfg)
        record["crack_metrics"].update(crack_width_metrics)
        record["crack_metrics"]["severity_t1_cm"] = float(shape.get("severity_t1_cm", t1))
        record["crack_metrics"]["severity_t2_cm"] = float(shape.get("severity_t2_cm", t2))
    runtime._attach_normal_debug(record, "crack", candidate, debug_cfg)
    return record
