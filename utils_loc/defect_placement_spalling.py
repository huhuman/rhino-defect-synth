"""Spalling and exposed-rebar geometry helpers for defect placement."""

import math


def _build_spall_ring_points(runtime, vertices, centroid, inward_normal, t, depth, irregularity, rng, min_bottom_area_ratio=0.25):
    radial_scale = max(0.03, 1.0 - 0.92 * float(t))
    depth_base = float(depth) * float(t)
    vertex_count = len(vertices)
    count_ratio = max(0.35, min(1.0, math.sqrt(radial_scale)))
    if vertex_count <= 8:
        ring_vertices = list(vertices)
    else:
        target_count = max(6, min(vertex_count, int(round(vertex_count * count_ratio))))
        ring_vertices = runtime._resample_closed_polygon(vertices, target_count)
    min_bottom_radius_ratio = math.sqrt(max(0.0, float(min_bottom_area_ratio)))
    ring = []
    is_bottom_ring = t >= 0.999
    radial_jitter_ratio = max(0.0, min(0.15, float(irregularity) * 0.18 * (1.0 - 0.5 * float(t))))
    for point in ring_vertices:
        vec = runtime._sub(point, centroid)
        if is_bottom_ring:
            local_scale = max(min_bottom_radius_ratio, radial_scale)
            local_depth = float(depth)
        else:
            shrink_jitter = 1.0 - rng.uniform(0.0, radial_jitter_ratio)
            local_scale = max(0.01, radial_scale * shrink_jitter)
            local_depth = depth_base
        ring_pt = runtime._add(
            runtime._add(centroid, runtime._scale(vec, local_scale)),
            runtime._scale(inward_normal, local_depth),
        )
        ring.append(ring_pt)
    return runtime._ensure_closed(ring)


def _model_spall_from_polygon(
    runtime,
    polygon,
    candidate,
    depth,
    layer_name,
    rng,
    irregularity=0.2,
    min_bottom_area_ratio=0.25,
):
    vertices = runtime._unique_points(polygon)
    if len(vertices) < 3:
        return [], {}

    centroid = (
        sum(point[0] for point in vertices) / float(len(vertices)),
        sum(point[1] for point in vertices) / float(len(vertices)),
        sum(point[2] for point in vertices) / float(len(vertices)),
    )
    inward_normal = runtime._candidate_inward_normal(candidate)
    irregularity = max(0.0, min(0.9, float(irregularity)))

    ring_points = [runtime._ensure_closed(vertices)]
    for t in (1.0 / 3.0, 2.0 / 3.0, 1.0):
        ring_points.append(
            _build_spall_ring_points(
                runtime,
                vertices,
                centroid,
                inward_normal,
                t,
                depth,
                irregularity=irregularity,
                rng=rng,
                min_bottom_area_ratio=min_bottom_area_ratio,
            )
        )

    helper_curves = []
    for pts in ring_points:
        curve_id = runtime._add_polyline(pts)
        if not curve_id:
            runtime._delete_objects(helper_curves)
            return [], {}
        helper_curves.append(curve_id)

    geometry_ids = []
    bottom_cap_method = "none"
    bottom_cap_count = 0
    try:
        for idx in range(len(helper_curves) - 1):
            loft = runtime.rs.AddLoftSrf([helper_curves[idx], helper_curves[idx + 1]]) or []
            geometry_ids.extend(runtime._coerce_ids(loft))
        bottom_cap = runtime._coerce_ids(runtime.rs.AddPlanarSrf(helper_curves[-1]) or [])
        if bottom_cap:
            geometry_ids.extend(bottom_cap)
            bottom_cap_method = "planar"
            bottom_cap_count = len(bottom_cap)
        else:
            bottom_vertices = runtime._unique_points(ring_points[-1])
            if len(bottom_vertices) >= 3:
                center = (
                    sum(point[0] for point in bottom_vertices) / float(len(bottom_vertices)),
                    sum(point[1] for point in bottom_vertices) / float(len(bottom_vertices)),
                    sum(point[2] for point in bottom_vertices) / float(len(bottom_vertices)),
                )
                fan_caps = []
                for idx in range(len(bottom_vertices)):
                    p0 = bottom_vertices[idx]
                    p1 = bottom_vertices[(idx + 1) % len(bottom_vertices)]
                    tri = runtime.rs.AddSrfPt([p0, p1, center])
                    fan_caps.extend(runtime._coerce_ids([tri]))
                if fan_caps:
                    geometry_ids.extend(fan_caps)
                    bottom_cap_method = "fan"
                    bottom_cap_count = len(fan_caps)
    finally:
        runtime._delete_objects(helper_curves)

    runtime._orient_surfaces_to_normal(geometry_ids, runtime._candidate_outward_normal(candidate))
    runtime._assign_layer(geometry_ids, layer_name)
    return runtime._coerce_ids(geometry_ids), {
        "depth": float(depth),
        "ring_count": len(ring_points),
        "irregularity": irregularity,
        "bottom_area_ratio_min": float(min_bottom_area_ratio),
        "bottom_cap_method": bottom_cap_method,
        "bottom_cap_count": int(bottom_cap_count),
    }


def _rebar_line_positions(start, end, spacing, rng, padding=0.5):
    start = float(start)
    end = float(end)
    if end < start:
        start, end = end, start
    length = max(0.0, end - start)
    spacing = max(1e-4, float(spacing))
    if spacing > length:
        return [0.5 * (start + end)]

    center = 0.5 * (start + end) + rng.uniform(-0.35 * spacing, 0.35 * spacing)
    lower_limit = start - float(padding)
    upper_limit = end + float(padding)
    out = [center]

    cursor = center + spacing
    while cursor <= upper_limit:
        out.append(cursor)
        cursor += spacing

    cursor = center - spacing
    while cursor >= lower_limit:
        out.append(cursor)
        cursor -= spacing

    out.sort()
    return out or [0.5 * (start + end)]


def _make_rebar_pipe(runtime, start, end, radius):
    line_id = runtime.rs.AddLine(start, end)
    if not line_id:
        return []
    try:
        return runtime._coerce_ids(runtime.rs.AddPipe(line_id, 0.0, float(radius), cap=2) or [])
    finally:
        if runtime.rs.IsObject(line_id):
            runtime.rs.DeleteObject(line_id)


def _model_rebar_bars(runtime, candidate, polygon, spall_depth, rebar_cfg, layer_name, rng):
    u_axis, v_axis, _n_axis = runtime._candidate_axes(candidate)
    if runtime._dot(u_axis, (1.0, 0.0, 0.0)) < 0.0:
        u_axis = runtime._scale(u_axis, -1.0)
    if runtime._dot(v_axis, (0.0, 1.0, 0.0)) < 0.0:
        v_axis = runtime._scale(v_axis, -1.0)
    inward_normal = runtime._candidate_inward_normal(candidate)

    vertices = runtime._unique_points(polygon)
    if len(vertices) < 3:
        return [], {"bar_count": 0, "skipped_reason": "invalid_polygon"}

    local_uv = []
    for point in vertices:
        rel = runtime._sub(point, candidate["point"])
        local_uv.append((runtime._dot(rel, u_axis), runtime._dot(rel, v_axis)))
    us = [uv[0] for uv in local_uv]
    vs = [uv[1] for uv in local_uv]
    left, right = min(us), max(us)
    bottom, top = min(vs), max(vs)
    span_u = max(1e-4, right - left)
    span_v = max(1e-4, top - bottom)
    length_scale = max(0.2, runtime._to_float(rebar_cfg.get("length_scale"), 1.3))

    mid_u = 0.5 * (left + right)
    mid_v = 0.5 * (bottom + top)
    half_u = 0.5 * span_u * length_scale
    half_v = 0.5 * span_v * length_scale
    left_ext, right_ext = mid_u - half_u, mid_u + half_u
    bottom_ext, top_ext = mid_v - half_v, mid_v + half_v

    radius = runtime._sample_rebar_radius_cm(rebar_cfg, rng=rng)
    diameter = 2.0 * radius
    cover_depth = runtime._sample_rebar_cover_depth_cm(rebar_cfg, radius_cm=radius, rng=rng)
    if float(spall_depth) <= max(0.0, cover_depth - 0.5 * radius):
        return [], {"bar_count": 0, "skipped_reason": "spall_depth_not_enough"}

    spacing = runtime._sample_rebar_spacing_cm(
        rebar_cfg,
        diameter_cm=diameter,
        span_hint_cm=max(span_u, span_v),
        rng=rng,
    )

    place_probability = runtime._clamp(runtime._to_float(rebar_cfg.get("place_probability"), 0.75), 0.0, 1.0)
    padding = runtime._to_float(rebar_cfg.get("extent_padding"), 0.5)
    use_dual_direction = runtime._to_bool(rebar_cfg.get("dual_direction"), default=True)
    visible_length_factor = max(0.5, runtime._to_float(rebar_cfg.get("visible_length_min_diameter_factor"), 2.5))
    visible_length_min = max(0.5, runtime._to_float(rebar_cfg.get("visible_length_min_cm"), 3.0))
    secondary_offset = max(diameter, runtime._to_float(rebar_cfg.get("secondary_layer_offset"), diameter))

    xs = _rebar_line_positions(left, right, spacing, rng, padding=padding)
    ys = _rebar_line_positions(bottom, top, spacing, rng, padding=padding)

    local_polygon = [(float(u), float(v)) for u, v in local_uv]
    polygon_centroid = runtime._polygon_centroid(local_polygon)
    required_visible_length = max(visible_length_min, visible_length_factor * diameter)
    visible_candidates = []

    def _collect_visible_candidates(line_values, direction, center_depth, axis_range, line_axis):
        visibility_depth = max(0.0, float(center_depth) - 0.5 * diameter)
        if visibility_depth >= float(spall_depth):
            return
        depth_ratio = visibility_depth / max(1e-6, float(spall_depth))
        visible_poly = runtime._scale_polygon_about_centroid(
            local_polygon,
            runtime._spall_radial_scale_for_depth_ratio(depth_ratio),
            centroid=polygon_centroid,
        )
        if len(visible_poly) < 3:
            return
        for line_value in line_values:
            visibility = runtime._polygon_line_visibility(visible_poly, axis=line_axis, value=line_value)
            if visibility["total_length"] < required_visible_length:
                continue
            visible_candidates.append(
                {
                    "direction": direction,
                    "line_value": float(line_value),
                    "center_depth": float(center_depth),
                    "visible_length": float(visibility["total_length"]),
                    "best_interval": visibility["best_interval"],
                    "axis_range": axis_range,
                    "line_axis": line_axis,
                }
            )

    _collect_visible_candidates(
        xs,
        direction="v",
        center_depth=cover_depth,
        axis_range=(bottom_ext, top_ext),
        line_axis="x",
    )
    if use_dual_direction:
        _collect_visible_candidates(
            ys,
            direction="u",
            center_depth=cover_depth + secondary_offset,
            axis_range=(left_ext, right_ext),
            line_axis="y",
        )

    if not visible_candidates:
        return [], {
            "bar_count": 0,
            "candidate_count_total": int(len(xs) + (len(ys) if use_dual_direction else 0)),
            "candidate_count_visible": 0,
            "skipped_reason": "no_visible_candidates",
        }

    rng.shuffle(visible_candidates)
    selected_candidates = [
        item for item in visible_candidates
        if rng.random() < place_probability
    ]
    if not selected_candidates:
        return [], {
            "bar_count": 0,
            "candidate_count_total": int(len(xs) + (len(ys) if use_dual_direction else 0)),
            "candidate_count_visible": int(len(visible_candidates)),
            "candidate_count_selected": 0,
            "place_probability": float(place_probability),
            "skipped_reason": "no_candidates_selected",
        }

    created = []
    count_u = 0
    count_v = 0
    rib_count = 0
    curved_count = 0

    for item in selected_candidates:
        if item["direction"] == "v":
            lateral_axis = u_axis
            start = runtime._add(
                runtime._add(candidate["point"], runtime._scale(inward_normal, item["center_depth"])),
                runtime._add(runtime._scale(u_axis, item["line_value"]), runtime._scale(v_axis, bottom_ext)),
            )
            end = runtime._add(
                runtime._add(candidate["point"], runtime._scale(inward_normal, item["center_depth"])),
                runtime._add(runtime._scale(u_axis, item["line_value"]), runtime._scale(v_axis, top_ext)),
            )
        else:
            lateral_axis = v_axis
            start = runtime._add(
                runtime._add(candidate["point"], runtime._scale(inward_normal, item["center_depth"])),
                runtime._add(runtime._scale(u_axis, left_ext), runtime._scale(v_axis, item["line_value"])),
            )
            end = runtime._add(
                runtime._add(candidate["point"], runtime._scale(inward_normal, item["center_depth"])),
                runtime._add(runtime._scale(u_axis, right_ext), runtime._scale(v_axis, item["line_value"])),
            )

        centerline_points = runtime._build_rebar_centerline_points(
            start,
            end,
            radius_cm=radius,
            lateral_axis=lateral_axis,
            inward_normal=inward_normal,
            rebar_cfg=rebar_cfg,
            rng=rng,
        )
        if len(centerline_points) > 2:
            curved_count += 1

        curve_id = runtime._add_curve(centerline_points)
        if not curve_id:
            continue
        try:
            pipe_ids = runtime._coerce_ids(runtime.rs.AddPipe(curve_id, 0.0, float(radius), cap=2) or [])
        finally:
            if runtime.rs.IsObject(curve_id):
                runtime.rs.DeleteObject(curve_id)
        if not pipe_ids:
            continue

        rib_ids = runtime._build_rebar_rib_ids(
            centerline_points=centerline_points,
            radius_cm=radius,
            axis_range=item["axis_range"],
            visible_interval=item["best_interval"],
            rebar_cfg=rebar_cfg,
        )
        created.extend(pipe_ids + rib_ids)
        rib_count += len(rib_ids)
        if item["direction"] == "u":
            count_u += 1
        else:
            count_v += 1

    if not created:
        return [], {
            "bar_count": 0,
            "candidate_count_total": int(len(xs) + (len(ys) if use_dual_direction else 0)),
            "candidate_count_visible": int(len(visible_candidates)),
            "skipped_reason": "geometry_creation_failed",
        }

    runtime._assign_layer(created, layer_name)
    return created, {
        "bar_count": int(count_u + count_v),
        "bar_count_u": int(count_u),
        "bar_count_v": int(count_v),
        "bar_length": float(max(right_ext - left_ext, top_ext - bottom_ext)),
        "bar_length_u": float(right_ext - left_ext),
        "bar_length_v": float(top_ext - bottom_ext),
        "spacing": float(spacing),
        "diameter": float(diameter),
        "radius": float(radius),
        "cover_depth": float(cover_depth),
        "secondary_layer_offset": float(secondary_offset if use_dual_direction else 0.0),
        "dual_direction": bool(use_dual_direction),
        "candidate_count_total": int(len(xs) + (len(ys) if use_dual_direction else 0)),
        "candidate_count_visible": int(len(visible_candidates)),
        "candidate_count_selected": int(count_u + count_v),
        "place_probability": float(place_probability),
        "visible_length_threshold": float(required_visible_length),
        "curved_bar_count": int(curved_count),
        "rib_object_count": int(rib_count),
    }


def _resolve_rebar_cfg(spalling_cfg):
    return dict((spalling_cfg or {}).get("rebar") or {})


def _resolve_spalling_condition_state(runtime, spalling_cfg, diameter_cm, depth_cm):
    depth_threshold, diameter_threshold = runtime._resolve_spalling_thresholds(spalling_cfg)
    if float(depth_cm) >= depth_threshold and float(diameter_cm) >= diameter_threshold:
        return "CS3"
    return "CS2"


def _resolve_spalling_diameter_cm(runtime, shape):
    target = runtime._to_optional_float((shape or {}).get("target_metric_cm"))
    if target is not None and target > 0.0:
        return float(target)
    radius = max(1e-6, runtime._to_float((shape or {}).get("shape_radius"), 1.0))
    return 2.0 * radius


def _resolve_spalling_depth_cm(runtime, shape, spalling_cfg, rng):
    target_depth = runtime._to_optional_float((shape or {}).get("target_spall_depth_cm"))
    if target_depth is not None and target_depth > 0.0:
        return float(target_depth)

    sampled = runtime._sample_spalling_profile(spalling_cfg, rng)
    return float(sampled.get("target_spall_depth_cm", 1.0))


def model_spalling_instance(runtime, candidate, shape, transform, cfg, layer_map, rng, defect_cfg=None, debug_cfg=None):
    spalling_cfg = dict(defect_cfg or (cfg.get("spalling") or {}))
    offset_2d = shape.get("spall_poly") or shape.get("offset_poly") or shape.get("base_poly") or []
    polygon = runtime._project_points_to_surface(offset_2d, candidate, transform["angle_deg"], transform["normal_offset"])
    surface_cut_polygon = runtime._project_points_to_surface(
        offset_2d,
        candidate,
        transform["angle_deg"],
        normal_offset=0.0,
    )
    if len(polygon) < 4:
        return None

    spall_depth = max(0.1, _resolve_spalling_depth_cm(runtime, shape, spalling_cfg, rng=rng))
    diameter_cm = _resolve_spalling_diameter_cm(runtime, shape)
    shape_state = str(shape.get("condition_state") or "").strip().upper()
    if shape_state in ("CS2", "CS3"):
        condition_state = shape_state
    else:
        condition_state = _resolve_spalling_condition_state(
            runtime,
            spalling_cfg,
            diameter_cm=diameter_cm,
            depth_cm=spall_depth,
        )

    rebar_cfg = _resolve_rebar_cfg(spalling_cfg)
    place_rebar = runtime._to_bool(rebar_cfg.get("enabled"), default=True)
    spall_layer_name = runtime._geometry_layer_for_condition(layer_map, "spall", condition_state)
    spall_ids, spall_metrics = _model_spall_from_polygon(
        runtime,
        polygon,
        candidate,
        spall_depth,
        spall_layer_name,
        rng=rng,
        irregularity=runtime._to_float(spalling_cfg.get("depth_irregularity"), 0.2),
        min_bottom_area_ratio=max(0.0, runtime._to_float(spalling_cfg.get("min_bottom_area_ratio"), 0.25)),
    )
    if not spall_ids:
        return None

    rebar_ids = []
    rebar_metrics = {}
    if place_rebar:
        rebar_layer_name = runtime._geometry_layer_for_condition(
            layer_map,
            "exposed_rebar",
            condition_state,
            part="rebar",
        )
        rebar_ids, rebar_metrics = _model_rebar_bars(
            runtime,
            candidate,
            polygon,
            spall_depth=spall_depth,
            rebar_cfg=rebar_cfg,
            layer_name=rebar_layer_name,
            rng=rng,
        )

    # Exposed-rebar cavity is its OWN class (Goldenrod/Chocolate), distinct from plain
    # spalling (Gold/DarkOrange), so the mask preserves the difference and post-processing
    # can merge or split the two spalling kinds. Restores the re-layering c405566 removed
    # (user decision 2026-06-22); the has_exposed_rebar record flag below is kept too.
    defect_type = "exposed_rebar" if rebar_ids else "spalling"
    if rebar_ids:
        exposed_spall_layer_name = runtime._geometry_layer_for_condition(
            layer_map,
            "exposed_rebar",
            condition_state,
            part="spalling",
        )
        runtime._assign_layer(spall_ids, exposed_spall_layer_name)
    geometry_ids = runtime._coerce_ids(spall_ids + rebar_ids)
    if not geometry_ids:
        runtime._delete_objects(spall_ids + rebar_ids)
        return None

    record = runtime._record_common(defect_type, candidate, transform, shape)
    record["condition_state"] = condition_state
    record["geometry_ids"] = runtime._as_strings(geometry_ids)
    record["spall_geometry_ids"] = runtime._as_strings(spall_ids)
    record["rebar_geometry_ids"] = runtime._as_strings(rebar_ids)
    record["mask_ids"] = []
    record["surface_cut_polygon"] = [list(runtime._vec3(pt)) for pt in runtime._ensure_closed(surface_cut_polygon)]
    record["spall_metrics"] = dict(spall_metrics or {})
    record["spall_metrics"]["depth"] = float(spall_depth)
    record["spall_metrics"]["diameter_cm"] = float(diameter_cm)
    record["spall_metrics"]["has_rebar"] = bool(rebar_ids)
    depth_threshold, diameter_threshold = runtime._resolve_spalling_thresholds(spalling_cfg)
    record["spall_metrics"]["depth_threshold"] = float(shape.get("depth_threshold", depth_threshold))
    record["spall_metrics"]["diameter_threshold"] = float(shape.get("diameter_threshold", diameter_threshold))
    if rebar_metrics:
        record["rebar_metrics"] = rebar_metrics
    if rebar_ids:
        record["has_exposed_rebar"] = True
        record["exposed_rebar_geometry_ids"] = {
            "spalling": runtime._as_strings(spall_ids),
            "rebar": runtime._as_strings(rebar_ids),
        }
    runtime._attach_normal_debug(record, defect_type, candidate, debug_cfg)
    return record
