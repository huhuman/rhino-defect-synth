"""Reference-point sampling, surface orientation, and normal-debug helpers."""

import math


def surface_curve_length(runtime, surface_id, direction, cross_param, sample_steps=24):
    sample_steps = max(4, runtime._to_int(sample_steps, 24))
    domain = runtime.rs.SurfaceDomain(surface_id, 0 if int(direction) == 0 else 1)
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
            point = runtime.rs.EvaluateSurface(surface_id, param, cross_param)
        else:
            point = runtime.rs.EvaluateSurface(surface_id, cross_param, param)
        if point is None:
            continue
        point = runtime._vec3(point)
        if prev is not None:
            total += runtime._distance(prev, point)
        prev = point
    return total if total > 1e-9 else None


def surface_uv_lengths(runtime, surface_id):
    if not surface_id or not runtime.rs.IsObject(surface_id) or not runtime.rs.IsSurface(surface_id):
        return 0.0, 0.0, None, None

    domain_u = runtime.rs.SurfaceDomain(surface_id, 0)
    domain_v = runtime.rs.SurfaceDomain(surface_id, 1)
    if not domain_u or not domain_v:
        return 0.0, 0.0, None, None

    u_cross_values = [
        float(domain_v[0]) + (float(domain_v[1]) - float(domain_v[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]
    v_cross_values = [
        float(domain_u[0]) + (float(domain_u[1]) - float(domain_u[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]

    u_lengths = [
        length
        for length in (surface_curve_length(runtime, surface_id, 0, value) for value in u_cross_values)
        if length is not None
    ]
    v_lengths = [
        length
        for length in (surface_curve_length(runtime, surface_id, 1, value) for value in v_cross_values)
        if length is not None
    ]
    length_u = sum(u_lengths) / float(len(u_lengths)) if u_lengths else 0.0
    length_v = sum(v_lengths) / float(len(v_lengths)) if v_lengths else 0.0
    return float(length_u), float(length_v), domain_u, domain_v


def uv_axis_boundary_distances_from_surface_data(runtime, uv, length_u, length_v, domain_u, domain_v):
    if not uv or len(uv) < 2 or not domain_u or not domain_v:
        return float("inf"), float("inf")

    u0 = float(domain_u[0])
    u1 = float(domain_u[1])
    v0 = float(domain_v[0])
    v1 = float(domain_v[1])
    u_span = abs(u1 - u0)
    v_span = abs(v1 - v0)
    if u_span <= 1e-9 or v_span <= 1e-9:
        return float("inf"), float("inf")

    u = float(uv[0])
    v = float(uv[1])
    u_ratio = min(abs(u - u0), abs(u1 - u)) / u_span
    v_ratio = min(abs(v - v0), abs(v1 - v)) / v_span
    return max(0.0, float(length_u) * u_ratio), max(0.0, float(length_v) * v_ratio)


def surface_axes(runtime, surface_id, point, normal):
    frame = None
    try:
        if surface_id and runtime.rs.IsObject(surface_id):
            uv = runtime.rs.SurfaceClosestPoint(surface_id, point)
            frame = runtime.rs.SurfaceFrame(surface_id, uv) if uv else None
    except Exception:
        frame = None

    if frame and hasattr(frame, "XAxis") and hasattr(frame, "YAxis"):
        u_axis = runtime._unit(frame.XAxis, fallback=(1.0, 0.0, 0.0))
        v_axis = runtime._unit(frame.YAxis, fallback=(0.0, 1.0, 0.0))
    else:
        n = runtime._unit(normal or (0.0, 0.0, 1.0), fallback=(0.0, 0.0, 1.0))
        ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
        u_axis = runtime._unit(runtime._cross(ref, n), fallback=(1.0, 0.0, 0.0))
        v_axis = runtime._unit(runtime._cross(n, u_axis), fallback=(0.0, 1.0, 0.0))
    n_axis = runtime._unit(normal or (0.0, 0.0, 1.0), fallback=runtime._cross(u_axis, v_axis))
    return u_axis, v_axis, n_axis


def surface_normal_at_point(runtime, surface_id, point, fallback=(0.0, 0.0, 1.0)):
    try:
        if surface_id and runtime.rs.IsObject(surface_id):
            uv = runtime.rs.SurfaceClosestPoint(surface_id, point)
            if uv:
                normal = runtime.rs.SurfaceNormal(surface_id, uv)
                if normal:
                    return runtime._unit(normal, fallback=fallback)
    except Exception:
        pass
    return runtime._unit(fallback, fallback=(0.0, 0.0, 1.0))


def surface_sample_point(runtime, surface_id):
    if not surface_id or not runtime.rs.IsObject(surface_id) or not runtime.rs.IsSurface(surface_id):
        return None
    try:
        centroid_data = runtime.rs.SurfaceAreaCentroid(surface_id)
        centroid = centroid_data[0] if isinstance(centroid_data, (list, tuple)) else centroid_data
        point = runtime._try_vec3(centroid)
        if point is not None:
            return point
    except Exception:
        pass

    try:
        domain_u = runtime.rs.SurfaceDomain(surface_id, 0)
        domain_v = runtime.rs.SurfaceDomain(surface_id, 1)
        if not domain_u or not domain_v:
            return None
        u = 0.5 * (float(domain_u[0]) + float(domain_u[1]))
        v = 0.5 * (float(domain_v[0]) + float(domain_v[1]))
        point = runtime.rs.EvaluateSurface(surface_id, u, v)
        return runtime._try_vec3(point)
    except Exception:
        return None


def normal_elevation_from_xy_deg(runtime, normal):
    n = runtime._unit(normal, fallback=(0.0, 0.0, 1.0))
    horizontal = math.sqrt(n[0] * n[0] + n[1] * n[1])
    return math.degrees(math.atan2(n[2], horizontal))


def resolve_efflore_z_threshold_deg(runtime, defect_cfg):
    eff_cfg = defect_cfg if isinstance(defect_cfg, dict) else {}
    z_threshold = runtime._to_optional_float(eff_cfg.get("z_threshold_deg"))
    if z_threshold is None:
        z_threshold = runtime._to_optional_float(eff_cfg.get("z_threshold"))
    if z_threshold is None:
        z_threshold = 5.0
    return abs(float(z_threshold))


def filter_surface_pool_for_type(runtime, surface_ids, defect_type, defect_cfg=None):
    if str(defect_type or "").strip().lower() != "efflore":
        return list(surface_ids or [])

    z_threshold = resolve_efflore_z_threshold_deg(runtime, defect_cfg)

    strict = []
    all_valid = []
    surface_debug = []
    for surface_id in surface_ids or []:
        if not surface_id or not runtime.rs.IsObject(surface_id) or not runtime.rs.IsSurface(surface_id):
            continue
        sample_point = surface_sample_point(runtime, surface_id)
        if sample_point is None:
            continue
        normal = surface_normal_at_point(runtime, surface_id, sample_point, fallback=(0.0, 0.0, 1.0))
        elevation = normal_elevation_from_xy_deg(runtime, normal)
        all_valid.append(surface_id)
        surface_debug.append(
            {
                "surface_id": str(surface_id),
                "layer": str(runtime.rs.ObjectLayer(surface_id) or ""),
                "normal": tuple(float(v) for v in normal),
                "elevation": float(elevation),
            }
        )
        if abs(elevation) <= z_threshold:
            strict.append(surface_id)
    print(
        "Defect efflore: surface filter summary total_input={} valid={} strict={} z_threshold={:.2f}".format(
            len(surface_ids or []),
            len(all_valid),
            len(strict),
            z_threshold,
        )
    )
    for item in surface_debug[:12]:
        normal = item["normal"]
        print(
            "Defect efflore: surface {} layer='{}' normal=({:.3f}, {:.3f}, {:.3f}) elevation_from_xy_deg={:.2f}".format(
                item["surface_id"],
                item["layer"],
                normal[0],
                normal[1],
                normal[2],
                item["elevation"],
            )
        )
    if len(surface_debug) > 12:
        print("Defect efflore: surface debug truncated (showing 12 of {}).".format(len(surface_debug)))
    if all_valid:
        print("Defect efflore: no surfaces matched z_threshold={:.2f}.".format(z_threshold))
    return strict


def flip_surface_if_conflicts_with_normal(runtime, surface_id, desired_normal, min_opposite_dot=0.25):
    if not surface_id or not runtime.rs.IsObject(surface_id) or not runtime.rs.IsSurface(surface_id):
        return False
    sample_point = surface_sample_point(runtime, surface_id)
    if sample_point is None:
        return False
    uv = runtime.rs.SurfaceClosestPoint(surface_id, sample_point)
    if uv is None:
        return False
    face_normal = runtime.rs.SurfaceNormal(surface_id, uv)
    face_normal = runtime._unit(face_normal, fallback=desired_normal)
    target = runtime._unit(desired_normal, fallback=(0.0, 0.0, 1.0))
    if runtime._dot(face_normal, target) >= -abs(float(min_opposite_dot)):
        return False
    try:
        runtime.rs.FlipSurface(surface_id)
        return True
    except Exception:
        return False


def orient_surfaces_to_normal(runtime, object_ids, desired_normal, min_opposite_dot=0.25):
    flipped = 0
    for obj_id in runtime._coerce_ids(object_ids):
        if flip_surface_if_conflicts_with_normal(
            runtime,
            obj_id,
            desired_normal,
            min_opposite_dot=min_opposite_dot,
        ):
            flipped += 1
    return flipped


def candidate_axes(runtime, candidate):
    u_axis = runtime._try_vec3((candidate or {}).get("u_axis"))
    v_axis = runtime._try_vec3((candidate or {}).get("v_axis"))
    n_axis = runtime._try_vec3((candidate or {}).get("n_axis"))
    if u_axis is not None and v_axis is not None:
        u_axis = runtime._unit(u_axis, fallback=(1.0, 0.0, 0.0))
        v_axis = runtime._unit(v_axis, fallback=(0.0, 1.0, 0.0))
        n_fallback = n_axis if n_axis is not None else runtime._cross(u_axis, v_axis)
        n_axis = runtime._unit(n_fallback, fallback=runtime._cross(u_axis, v_axis))
        return u_axis, v_axis, n_axis
    return surface_axes(
        runtime,
        (candidate or {}).get("surface_id"),
        (candidate or {}).get("point"),
        (candidate or {}).get("normal"),
    )


def candidate_outward_normal(runtime, candidate):
    c = candidate or {}
    cached = runtime._try_vec3(c.get("normal"))
    if cached is not None:
        return runtime._unit(cached, fallback=(0.0, 0.0, 1.0))
    return surface_normal_at_point(
        runtime,
        c.get("surface_id"),
        c.get("point"),
        fallback=c.get("normal") or (0.0, 0.0, 1.0),
    )


def candidate_inward_normal(runtime, candidate):
    return runtime._scale(candidate_outward_normal(runtime, candidate), -1.0)


def project_points_to_surface(runtime, points_2d, candidate, angle_deg, normal_offset=0.0):
    normal = candidate_outward_normal(runtime, candidate)
    origin = runtime._add(candidate["point"], runtime._scale(normal, normal_offset))
    u_axis, v_axis, _ = candidate_axes(runtime, candidate)
    angle = math.radians(float(angle_deg))
    cos_v = math.cos(angle)
    sin_v = math.sin(angle)
    out = []
    for x, y in points_2d or []:
        rx = (x * cos_v - y * sin_v)
        ry = (x * sin_v + y * cos_v)
        p = runtime._add(runtime._add(origin, runtime._scale(u_axis, rx)), runtime._scale(v_axis, ry))
        out.append(p)
    return runtime._ensure_closed(out)


def collect_reference_candidates(runtime, cfg, model_result=None, defect_type=None, defect_cfg=None, layer_map=None, debug_cfg=None):
    source_ids = runtime._collect_object_ids(model_result)
    ref_cfg = cfg.get("reference") or {}
    debug_efflore = str(defect_type or "").strip().lower() == "efflore"
    existing_ids_before = set(
        runtime.rs.AllObjects(select=False, include_lights=False, include_grips=False) or []
    )
    surface_ids = runtime.get_surfaces(
        object_ids=source_ids,
        layer_names=cfg.get("target_layers"),
        convert_polylines=True,
        explode_polysurfaces=True,
        keep_input=True,
    )
    temporary_surface_ids = [sid for sid in surface_ids if sid not in existing_ids_before]
    surface_ids = filter_surface_pool_for_type(runtime, surface_ids, defect_type, defect_cfg=defect_cfg)

    max_num_surfaces = max(0, runtime._to_int(ref_cfg.get("max_num_surfaces"), 0))
    if max_num_surfaces > 0:
        surface_ids = surface_ids[:max_num_surfaces]

    normal_debug_cfg = ref_cfg.get("normal_debug") or {}
    draw_normal_debug = bool(normal_debug_cfg.get("enabled", False))
    normal_debug_layer = str(normal_debug_cfg.get("layer") or "defects::normal_debug")
    normal_debug_length = max(1e-3, runtime._to_float(normal_debug_cfg.get("length"), 60.0))
    if draw_normal_debug:
        runtime.ensure_layer(normal_debug_layer)

    candidates = []
    seen_candidate_keys = set()
    su = max(1, runtime._to_int(ref_cfg.get("sample_count_u"), 2))
    sv = max(1, runtime._to_int(ref_cfg.get("sample_count_v"), 2))
    sample_edge_length_u = runtime._to_optional_float(ref_cfg.get("sample_edge_length_u"))
    sample_edge_length_v = runtime._to_optional_float(ref_cfg.get("sample_edge_length_v"))
    boundary_margin_ratio_u = max(0.0, runtime._to_float(ref_cfg.get("boundary_margin_ratio_u"), 0.25))
    boundary_margin_ratio_v = max(0.0, runtime._to_float(ref_cfg.get("boundary_margin_ratio_v"), 0.25))
    boundary_distance_u = max(0.0, (sample_edge_length_u or 0.0) * boundary_margin_ratio_u)
    boundary_distance_v = max(0.0, (sample_edge_length_v or 0.0) * boundary_margin_ratio_v)
    if debug_efflore:
        print(
            "Defect efflore: reference config sample_count_u={} sample_count_v={} sample_edge_length_u={} sample_edge_length_v={} boundary_distance_u={:.3f} boundary_distance_v={:.3f}".format(
                su,
                sv,
                sample_edge_length_u,
                sample_edge_length_v,
                boundary_distance_u,
                boundary_distance_v,
            )
        )

    stats = {
        "surface_count": 0,
        "sample_points_total": 0,
        "rejected_not_on_surface": 0,
        "rejected_boundary": 0,
        "rejected_duplicate": 0,
        "accepted": 0,
    }
    per_surface_logs = []
    surface_uv_cache = {}

    try:
        for surface_id in surface_ids:
            if not runtime.rs.IsObject(surface_id):
                continue
            stats["surface_count"] += 1
            points, sizes, normals, point_meta = runtime.get_reference_points(
                surface_id,
                sample_count_u=su,
                sample_count_v=sv,
                sample_edge_length_u=sample_edge_length_u,
                sample_edge_length_v=sample_edge_length_v,
                boundary_margin_ratio_u=boundary_margin_ratio_u,
                boundary_margin_ratio_v=boundary_margin_ratio_v,
                return_normals=True,
                return_metadata=True,
            )
            surface_layer = runtime.rs.ObjectLayer(surface_id)
            if surface_id not in surface_uv_cache:
                surface_uv_cache[surface_id] = surface_uv_lengths(runtime, surface_id)
            surface_length_u, surface_length_v, surface_domain_u, surface_domain_v = surface_uv_cache[surface_id]
            surface_stats = {
                "surface_id": str(surface_id),
                "layer": str(surface_layer or ""),
                "sampled": len(points),
                "accepted": 0,
                "rejected_not_on_surface": 0,
                "rejected_boundary": 0,
                "rejected_duplicate": 0,
                "boundary_min": None,
                "boundary_max": None,
            }
            for point, size, normal, meta in zip(points, sizes, normals, point_meta):
                stats["sample_points_total"] += 1
                skip_checks = bool((meta or {}).get("skip_checks"))
                point_3d = runtime._vec3(point)
                normal_3d = surface_normal_at_point(runtime, surface_id, point_3d, fallback=normal)
                u_axis, v_axis, n_axis = surface_axes(runtime, surface_id, point_3d, normal_3d)
                if not skip_checks and not runtime.rs.IsPointOnSurface(surface_id, point_3d):
                    stats["rejected_not_on_surface"] += 1
                    surface_stats["rejected_not_on_surface"] += 1
                    continue
                uv = (meta or {}).get("uv") or []
                boundary_dist_u, boundary_dist_v = (
                    (float("inf"), float("inf"))
                    if skip_checks else uv_axis_boundary_distances_from_surface_data(
                        runtime,
                        uv,
                        surface_length_u,
                        surface_length_v,
                        surface_domain_u,
                        surface_domain_v,
                    )
                )
                boundary_dist = min(boundary_dist_u, boundary_dist_v)
                if not skip_checks:
                    if surface_stats["boundary_min"] is None or boundary_dist < surface_stats["boundary_min"]:
                        surface_stats["boundary_min"] = float(boundary_dist)
                    if surface_stats["boundary_max"] is None or boundary_dist > surface_stats["boundary_max"]:
                        surface_stats["boundary_max"] = float(boundary_dist)
                if not skip_checks and (boundary_dist_u < boundary_distance_u or boundary_dist_v < boundary_distance_v):
                    stats["rejected_boundary"] += 1
                    surface_stats["rejected_boundary"] += 1
                    continue
                candidate_key = (str(surface_layer or ""), runtime._point_key(point_3d))
                if candidate_key in seen_candidate_keys:
                    stats["rejected_duplicate"] += 1
                    surface_stats["rejected_duplicate"] += 1
                    continue
                seen_candidate_keys.add(candidate_key)
                normal_line_id = None
                if draw_normal_debug:
                    end_pt = runtime._add(point_3d, runtime._scale(normal_3d, normal_debug_length))
                    normal_line_id = runtime.rs.AddLine(point_3d, end_pt)
                    if normal_line_id:
                        runtime._assign_layer([normal_line_id], normal_debug_layer)
                candidates.append(
                    {
                        "candidate_key": "{}|{:.4f}|{:.4f}|{:.4f}".format(
                            str(surface_layer or ""),
                            point_3d[0],
                            point_3d[1],
                            point_3d[2],
                        ),
                        "surface_id": surface_id,
                        "surface_layer": surface_layer,
                        "point": point_3d,
                        "normal": normal_3d,
                        "u_axis": u_axis,
                        "v_axis": v_axis,
                        "n_axis": n_axis,
                        "reference_size": float(size),
                        "boundary_dist": float(boundary_dist),
                        "boundary_dist_u": float(boundary_dist_u),
                        "boundary_dist_v": float(boundary_dist_v),
                        "skip_candidate_checks": skip_checks,
                        "sampling_mode": (meta or {}).get("sampling_mode"),
                        "uv": list((meta or {}).get("uv") or []),
                        "normal_debug_id": str(normal_line_id) if normal_line_id else None,
                    }
                )
                stats["accepted"] += 1
                surface_stats["accepted"] += 1
            if debug_efflore:
                per_surface_logs.append(surface_stats)
    finally:
        for sid in temporary_surface_ids:
            if sid and runtime.rs.IsObject(sid):
                runtime.rs.DeleteObject(sid)

    if debug_efflore:
        print(
            "Defect efflore: reference candidate summary surfaces={} sampled_points={} accepted={} rejected_boundary={} rejected_not_on_surface={} rejected_duplicate={}".format(
                stats["surface_count"],
                stats["sample_points_total"],
                stats["accepted"],
                stats["rejected_boundary"],
                stats["rejected_not_on_surface"],
                stats["rejected_duplicate"],
            )
        )
        for item in per_surface_logs[:20]:
            boundary_min = item["boundary_min"]
            boundary_max = item["boundary_max"]
            min_text = "n/a" if boundary_min is None else "{:.3f}".format(boundary_min)
            max_text = "n/a" if boundary_max is None else "{:.3f}".format(boundary_max)
            print(
                "Defect efflore: surface {} layer='{}' sampled={} accepted={} rejected_boundary={} rejected_not_on_surface={} rejected_duplicate={} boundary_range=[{}, {}]".format(
                    item["surface_id"],
                    item["layer"],
                    item["sampled"],
                    item["accepted"],
                    item["rejected_boundary"],
                    item["rejected_not_on_surface"],
                    item["rejected_duplicate"],
                    min_text,
                    max_text,
                )
            )
        if len(per_surface_logs) > 20:
            print("Defect efflore: reference debug truncated (showing 20 of {}).".format(len(per_surface_logs)))

    return candidates


def create_seed_marker(runtime, candidate, layer_name, radius_coef=0.04375, min_radius=0.625, axis_scale=1.6):
    runtime.ensure_layer(layer_name)
    if not runtime.rs.IsLayer(layer_name):
        return []
    point = candidate["point"]
    ref_size = max(1.0, runtime._to_float(candidate.get("reference_size"), 1.0))
    radius = max(float(min_radius), ref_size * float(radius_coef))
    line_half = radius * float(axis_scale)

    marker_ids = []
    sphere = runtime.rs.AddSphere(point, radius)
    if sphere:
        marker_ids.append(sphere)

    u_axis, v_axis, n_axis = candidate_axes(runtime, candidate)
    for axis in (u_axis, v_axis, n_axis):
        start = runtime._add(point, runtime._scale(axis, -line_half))
        end = runtime._add(point, runtime._scale(axis, line_half))
        line = runtime.rs.AddLine(start, end)
        if line:
            marker_ids.append(line)

    runtime._assign_layer(marker_ids, layer_name)
    return runtime._coerce_ids(marker_ids)


def basis_from_normal(runtime, normal):
    n = runtime._unit(normal, fallback=(0.0, 0.0, 1.0))
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
    x_axis = runtime._unit(runtime._cross(ref, n), fallback=(1.0, 0.0, 0.0))
    y_axis = runtime._unit(runtime._cross(n, x_axis), fallback=(0.0, 1.0, 0.0))
    return x_axis, y_axis, n


def add_direction_arrow(runtime, point, direction, layer_name, length, head_length_ratio=0.25, head_width_ratio=0.35):
    if not layer_name:
        return []
    runtime.ensure_layer(layer_name)
    if not runtime.rs.IsLayer(layer_name):
        return []

    origin = runtime._try_vec3(point)
    if origin is None:
        return []
    dir_vec = runtime._unit(direction, fallback=(0.0, 0.0, 1.0))
    if runtime._norm(dir_vec) <= 1e-12:
        return []

    length = max(1e-3, float(length))
    head_len = min(length * max(1e-3, float(head_length_ratio)), length * 0.9)
    head_width = max(1e-3, head_len * max(1e-3, float(head_width_ratio)))
    x_axis, y_axis, _ = basis_from_normal(runtime, dir_vec)

    shaft_end = runtime._add(origin, runtime._scale(dir_vec, length - head_len))
    tip = runtime._add(origin, runtime._scale(dir_vec, length))
    arrow_ids = []

    shaft_id = runtime.rs.AddLine(origin, shaft_end)
    if shaft_id:
        arrow_ids.append(shaft_id)

    for axis in (x_axis, runtime._scale(x_axis, -1.0), y_axis, runtime._scale(y_axis, -1.0)):
        wing_end = runtime._add(shaft_end, runtime._scale(axis, head_width))
        wing_id = runtime.rs.AddLine(tip, wing_end)
        if wing_id:
            arrow_ids.append(wing_id)

    runtime._assign_layer(arrow_ids, layer_name)
    return runtime._coerce_ids(arrow_ids)


def resolve_modeling_normal(runtime, defect_type, candidate):
    outward = candidate_outward_normal(runtime, candidate)
    if defect_type in ("crack", "spalling", "exposed_rebar"):
        return runtime._scale(outward, -1.0), "inward"
    return outward, "outward"


def attach_normal_debug(runtime, record, defect_type, candidate, debug_cfg):
    record = record if isinstance(record, dict) else {}
    normal_vec, normal_role = resolve_modeling_normal(runtime, defect_type, candidate)
    record["modeling_normal"] = [float(normal_vec[0]), float(normal_vec[1]), float(normal_vec[2])]
    record["modeling_normal_role"] = str(normal_role)

    cfg = (debug_cfg or {}).get("defect_normals") or {}
    if not bool(cfg.get("enabled", False)):
        return

    layer_name = str(cfg.get("layer") or "debug::normal")
    if bool(cfg.get("by_type", True)):
        layer_name = "{}::{}".format(layer_name, str(defect_type))

    debug_ids = add_direction_arrow(
        runtime,
        record.get("point") or (candidate or {}).get("point"),
        normal_vec,
        layer_name=layer_name,
        length=runtime._to_float(cfg.get("length"), 40.0),
        head_length_ratio=runtime._to_float(cfg.get("head_length_ratio"), 0.25),
        head_width_ratio=runtime._to_float(cfg.get("head_width_ratio"), 0.35),
    )
    if debug_ids:
        record["normal_debug_ids"] = runtime._as_strings(debug_ids)


def reference_points_debug_config(runtime, debug_cfg=None):
    debug_cfg = debug_cfg if isinstance(debug_cfg, dict) else {}
    ref_debug = debug_cfg.get("reference_points")
    legacy_seed_debug = debug_cfg.get("defect_seeds") or {}

    if isinstance(ref_debug, bool):
        return {
            "enabled": ref_debug,
            "layer": "debug::reference_points",
            "radius_coef": 0.04375,
            "min_radius": 0.625,
            "axis_scale": 1.6,
        }

    if isinstance(ref_debug, dict):
        return {
            "enabled": bool(ref_debug.get("enabled", True)),
            "layer": str(ref_debug.get("layer") or "debug::reference_points"),
            "radius_coef": max(0.0, runtime._to_float(ref_debug.get("radius_coef"), 0.04375)),
            "min_radius": max(0.0, runtime._to_float(ref_debug.get("min_radius"), 0.625)),
            "axis_scale": max(0.0, runtime._to_float(ref_debug.get("axis_scale"), 1.6)),
        }

    return {
        "enabled": bool(legacy_seed_debug.get("reference_points_enabled", False)),
        "layer": "debug::reference_points",
        "radius_coef": max(0.0, runtime._to_float(legacy_seed_debug.get("reference_radius_coef"), 0.04375)),
        "min_radius": max(0.0, runtime._to_float(legacy_seed_debug.get("reference_min_radius"), 0.625)),
        "axis_scale": max(0.0, runtime._to_float(legacy_seed_debug.get("reference_axis_scale"), 1.6)),
    }


def seed_layer_for_type(runtime, layer_map, defect_type):
    seeds = (layer_map or {}).get("seeds")
    if not isinstance(seeds, dict):
        return seeds
    if defect_type in ("spalling", "exposed_rebar"):
        key = "spalling"
    elif defect_type == "efflore":
        key = "efflore"
    else:
        key = "crack"
    return seeds.get(key) or seeds.get("default")


def draw_reference_points_debug(runtime, cfg, model_result=None, debug_cfg=None):
    ref_debug_cfg = reference_points_debug_config(runtime, debug_cfg)
    if not bool(ref_debug_cfg.get("enabled", False)):
        return 0

    ref_cfg = cfg.get("reference") or {}
    source_ids = runtime._collect_object_ids(model_result)
    existing_ids_before = set(
        runtime.rs.AllObjects(select=False, include_lights=False, include_grips=False) or []
    )
    surface_ids = runtime.get_surfaces(
        object_ids=source_ids,
        layer_names=cfg.get("target_layers"),
        convert_polylines=True,
        explode_polysurfaces=True,
        keep_input=True,
    )
    temporary_surface_ids = [sid for sid in surface_ids if sid not in existing_ids_before]
    max_num_surfaces = max(0, runtime._to_int(ref_cfg.get("max_num_surfaces"), 0))
    if max_num_surfaces > 0:
        surface_ids = surface_ids[:max_num_surfaces]

    layer_name = str(ref_debug_cfg.get("layer") or "debug::reference_points")
    runtime.ensure_layer(layer_name)
    if not runtime.rs.IsLayer(layer_name):
        return 0

    su = max(1, runtime._to_int(ref_cfg.get("sample_count_u"), 2))
    sv = max(1, runtime._to_int(ref_cfg.get("sample_count_v"), 2))
    sample_edge_length_u = runtime._to_optional_float(ref_cfg.get("sample_edge_length_u"))
    sample_edge_length_v = runtime._to_optional_float(ref_cfg.get("sample_edge_length_v"))
    boundary_margin_ratio_u = max(0.0, runtime._to_float(ref_cfg.get("boundary_margin_ratio_u"), 0.25))
    boundary_margin_ratio_v = max(0.0, runtime._to_float(ref_cfg.get("boundary_margin_ratio_v"), 0.25))
    radius_coef = max(0.0, runtime._to_float(ref_debug_cfg.get("radius_coef"), 0.04375))
    min_radius = max(0.0, runtime._to_float(ref_debug_cfg.get("min_radius"), 0.625))
    axis_scale = max(0.0, runtime._to_float(ref_debug_cfg.get("axis_scale"), 1.6))

    marker_count = 0
    seen_marker_keys = set()
    try:
        for surface_id in surface_ids:
            if not runtime.rs.IsObject(surface_id):
                continue
            points, sizes, normals, _point_meta = runtime.get_reference_points(
                surface_id,
                sample_count_u=su,
                sample_count_v=sv,
                sample_edge_length_u=sample_edge_length_u,
                sample_edge_length_v=sample_edge_length_v,
                boundary_margin_ratio_u=boundary_margin_ratio_u,
                boundary_margin_ratio_v=boundary_margin_ratio_v,
                return_normals=True,
                return_metadata=True,
            )
            for point, size, normal in zip(points, sizes, normals):
                point_3d = runtime._vec3(point)
                marker_key = (str(surface_id), runtime._point_key(point_3d))
                if marker_key in seen_marker_keys:
                    continue
                seen_marker_keys.add(marker_key)
                normal_3d = surface_normal_at_point(runtime, surface_id, point_3d, fallback=normal)
                u_axis, v_axis, n_axis = surface_axes(runtime, surface_id, point_3d, normal_3d)
                marker_ids = create_seed_marker(
                    runtime,
                    {
                        "surface_id": surface_id,
                        "point": point_3d,
                        "normal": normal_3d,
                        "u_axis": u_axis,
                        "v_axis": v_axis,
                        "n_axis": n_axis,
                        "reference_size": float(size),
                    },
                    layer_name,
                    radius_coef=radius_coef,
                    min_radius=min_radius,
                    axis_scale=axis_scale,
                )
                if marker_ids:
                    marker_count += 1
    finally:
        for sid in temporary_surface_ids:
            if sid and runtime.rs.IsObject(sid):
                runtime.rs.DeleteObject(sid)
    return marker_count
