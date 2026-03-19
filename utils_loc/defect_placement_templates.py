"""Shape-template and overview-row loading helpers for defect placement."""

import csv
import json
import math
import os
import re


def _normalize_path(path):
    return str(path or "").replace("\\", "/")


def _resolve_path_with_base(path, base_dir=None):
    text = str(path or "").strip()
    if not text:
        return None
    norm = os.path.normpath(text)
    if os.path.isabs(norm):
        return os.path.abspath(norm)
    if base_dir:
        return os.path.abspath(os.path.normpath(os.path.join(str(base_dir), norm)))
    return os.path.abspath(norm)


def _load_overview_rows(csv_path):
    if not csv_path:
        return []
    abs_path = os.path.abspath(str(csv_path))
    if not os.path.isfile(abs_path):
        print("Defect overview: CSV file not found: '{}'".format(abs_path))
        return []

    csv_dir = os.path.dirname(abs_path)
    rows = []
    with open(abs_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if not isinstance(raw, dict):
                continue
            row = {}
            for key, value in raw.items():
                if key is None:
                    continue
                row[str(key).strip()] = value
            if any(str(value).strip() for value in row.values()):
                row["__overview_dir"] = csv_dir
                row["__overview_csv_path"] = abs_path
                rows.append(row)
    return rows


def _sample_rows(rows, count, rng):
    if not rows or count <= 0:
        return []
    if count <= len(rows):
        return rng.sample(rows, count)
    return [rng.choice(rows) for _ in range(count)]


def _crack_row_tokens(row):
    row = row or {}
    tokens = set()
    for key in ("instance_id", "instance_mask_path", "polygon_json_path", "polygon_path"):
        raw = row.get(key)
        if raw is None:
            continue
        text = _normalize_path(raw).lower()
        if text:
            tokens.update(re.findall(r"[a-z0-9]+", text))
        base = os.path.basename(text)
        stem, _ext = os.path.splitext(base)
        if stem:
            tokens.update(re.findall(r"[a-z0-9]+", stem))
    return tokens


def _classify_crack_row_condition_state(row):
    tokens = _crack_row_tokens(row)
    if "skeleton" in tokens:
        return "CS1"
    if "erodex1" in tokens:
        return "CS2"
    return "CS3"


def _crack_base_instance_id(row):
    instance_id = str((row or {}).get("instance_id") or "").strip()
    if instance_id.endswith("_skeleton"):
        return instance_id[: -len("_skeleton")]
    if instance_id.endswith("_erodex1"):
        return instance_id[: -len("_erodex1")]
    return instance_id


def _to_polygon_json_path(instance_mask_path):
    if not instance_mask_path:
        return None
    path = _normalize_path(instance_mask_path)
    for src, dst in (
        ("/crack_units/", "/crack_polygon/"),
        ("/spalling_units/", "/spalling_polygon/"),
        ("/efflore_units/", "/efflore_polygon/"),
        ("/units/", "/polygon/"),
        ("_units/", "_polygon/"),
    ):
        path = path.replace(src, dst)
    root, _ext = os.path.splitext(path)
    return os.path.normpath(root + ".json")


def _resolve_polygon_path_from_row(row):
    row = row or {}
    base_dir = row.get("__overview_dir")
    direct = row.get("polygon_json_path") or row.get("polygon_path")
    if direct:
        return _resolve_path_with_base(direct, base_dir=base_dir)
    polygon_path = _to_polygon_json_path(row.get("instance_mask_path"))
    return _resolve_path_with_base(polygon_path, base_dir=base_dir)


def _resolve_expand_polygon_path_from_row(row):
    polygon_path = _resolve_polygon_path_from_row(row)
    if not polygon_path:
        return None
    root, ext = os.path.splitext(str(polygon_path))
    if root.endswith("_expand"):
        return os.path.abspath(root + ext)
    return os.path.abspath(root + "_expand.json")


def _normalize_polygon(points):
    out = []
    for point in points or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        out.append((float(point[0]), float(point[1])))
    return out


def _center_polygon(points, width_px, height_px):
    cx = 0.5 * float(width_px)
    cy = 0.5 * float(height_px)
    return [(float(x) - cx, float(y) - cy) for x, y in points]


def _scale_polygon(points, scale):
    s = float(scale)
    return [(float(x) * s, float(y) * s) for x, y in points]


def _load_polygon_payload(runtime, polygon_path):
    if not polygon_path:
        return None
    abs_path = os.path.abspath(str(polygon_path))
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return None
    raw_polygons = data.get("polygons") or []
    polygons = []
    for raw in raw_polygons:
        poly = _normalize_polygon(raw)
        if len(poly) >= 3:
            polygons.append(poly)
    if not polygons:
        return None
    width_px = runtime._to_float(data.get("width_px"), 0.0)
    height_px = runtime._to_float(data.get("height_px"), 0.0)
    if width_px <= 0.0:
        width_px = runtime._to_float(data.get("bbox_w"), 0.0)
    if height_px <= 0.0:
        height_px = runtime._to_float(data.get("bbox_h"), 0.0)
    if width_px <= 0.0 and height_px <= 0.0:
        xs = [point[0] for poly in polygons for point in poly]
        ys = [point[1] for poly in polygons for point in poly]
        width_px = max(xs) - min(xs) if xs else 1.0
        height_px = max(ys) - min(ys) if ys else 1.0
    if width_px <= 0.0:
        width_px = height_px
    if height_px <= 0.0:
        height_px = width_px
    return {
        "path": abs_path,
        "instance_id": data.get("instance_id"),
        "width_px": float(width_px),
        "height_px": float(height_px),
        "polygons": polygons,
    }


def _resolve_reference_metric_px(runtime, defect_type, row, polygon_payload):
    row = row or {}
    polygon_payload = polygon_payload or {}

    if defect_type == "crack":
        ref = runtime._to_optional_float(row.get("width_px"))
        if ref and ref > 0.0:
            return ref
    elif defect_type == "spalling":
        ref = runtime._to_optional_float(row.get("diameter_px"))
        if ref and ref > 0.0:
            return ref
    elif defect_type == "efflore":
        bw = runtime._to_optional_float(row.get("bbox_w"))
        bh = runtime._to_optional_float(row.get("bbox_h"))
        if bw is not None or bh is not None:
            return max(float(bw or 0.0), float(bh or 0.0), 1.0)
        ref = runtime._to_optional_float(row.get("diameter_px"))
        if ref and ref > 0.0:
            return ref

    width_px = runtime._to_float(polygon_payload.get("width_px"), 1.0)
    height_px = runtime._to_float(polygon_payload.get("height_px"), 1.0)
    return max(width_px, height_px, 1.0)


def _sanitize_reference_metric_px(runtime, defect_type, metric_px, row, polygon_payload):
    try:
        metric = float(metric_px)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(metric) or metric <= 0.0:
        return None

    row = row or {}
    polygon_payload = polygon_payload or {}
    bbox_w = runtime._to_optional_float(row.get("bbox_w"))
    bbox_h = runtime._to_optional_float(row.get("bbox_h"))
    payload_w = runtime._to_optional_float(polygon_payload.get("width_px"))
    payload_h = runtime._to_optional_float(polygon_payload.get("height_px"))

    long_side_candidates = [value for value in (bbox_w, bbox_h, payload_w, payload_h) if value and value > 0.0]
    short_side_candidates = [value for value in (bbox_w, bbox_h) if value and value > 0.0]
    bbox_long = max(long_side_candidates) if long_side_candidates else None
    bbox_short = min(short_side_candidates) if short_side_candidates else bbox_long

    if defect_type == "crack":
        if bbox_long is not None and metric > max(float(bbox_long), float(bbox_short or bbox_long) * 4.0):
            return None
    elif defect_type == "efflore":
        if bbox_long is not None and metric > float(bbox_long) * 2.0:
            return None

    return metric


def _scale_payload_polygons(runtime, payload, metric_scale):
    polygons = []
    for poly in (payload or {}).get("polygons") or []:
        centered = _center_polygon(poly, payload["width_px"], payload["height_px"])
        scaled = _scale_polygon(centered, metric_scale)
        if len(scaled) >= 3:
            polygons.append(scaled)
    polygons.sort(key=lambda pts: abs(runtime._polygon_area(pts)), reverse=True)
    return polygons


def _resolve_efflore_polygons(runtime, polygons, expand_polygons=None):
    candidates = [list(poly) for poly in (polygons or []) if len(poly) >= 3]
    if not candidates:
        return [], None

    candidates.sort(key=lambda pts: abs(runtime._polygon_area(pts)), reverse=True)
    inner_poly = list(candidates[0])

    expand_candidates = [list(poly) for poly in (expand_polygons or []) if len(poly) >= 3]
    if not expand_candidates:
        return inner_poly, None

    expand_candidates.sort(key=lambda pts: abs(runtime._polygon_area(pts)), reverse=True)
    outer_poly = list(expand_candidates[0])
    if not outer_poly:
        return inner_poly, None
    return inner_poly, outer_poly


def _shape_radius_from_polygons(polygons):
    radius = 0.0
    for poly in polygons or []:
        for x, y in poly or []:
            radius = max(radius, math.sqrt(float(x) * float(x) + float(y) * float(y)))
    return radius


def _resolve_cs_weights(runtime, defect_cfg, expected_len, default_weights):
    raw = (defect_cfg or {}).get("cs_weights")
    if not isinstance(raw, (list, tuple)) or len(raw) < expected_len:
        raw = list(default_weights or [])
    weights = []
    for idx in range(expected_len):
        fallback = default_weights[idx] if idx < len(default_weights or []) else 1.0
        base = raw[idx] if idx < len(raw) else fallback
        weights.append(max(0.0, runtime._to_float(base, 1.0)))
    if sum(weights) <= 1e-12:
        return [1.0] * expected_len
    return weights


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


def _weighted_pick(options, weights, rng):
    options = list(options or [])
    if not options:
        return None
    if len(options) == 1:
        return options[0]
    resolved = list(weights or [])
    if len(resolved) < len(options):
        resolved.extend([1.0] * (len(options) - len(resolved)))
    total = sum(max(0.0, float(value)) for value in resolved[: len(options)])
    if total <= 1e-12:
        return rng.choice(options)
    target = rng.random() * total
    acc = 0.0
    for option, value in zip(options, resolved):
        acc += max(0.0, float(value))
        if target <= acc:
            return option
    return options[-1]


def _sample_crack_profile(runtime, defect_cfg, rng):
    t1, t2 = _resolve_crack_width_thresholds(runtime, defect_cfg)
    cs_weights = _resolve_cs_weights(runtime, defect_cfg, expected_len=3, default_weights=[1.0, 1.0, 1.0])
    cs_level = _weighted_pick(["CS1", "CS2", "CS3"], cs_weights, rng=rng)
    if cs_level == "CS1":
        width_cm = runtime._uniform_sample(rng, 0.5 * t1, t1)
    elif cs_level == "CS2":
        width_cm = runtime._uniform_sample(rng, t1, t2)
    else:
        width_cm = runtime._uniform_sample(rng, t2, 20.0 * t2)
    return {
        "condition_state": cs_level,
        "target_metric_cm": max(1e-6, float(width_cm)),
        "severity_t1_cm": float(t1),
        "severity_t2_cm": float(t2),
    }


def resolve_spalling_thresholds(runtime, spalling_cfg):
    spalling_cfg = spalling_cfg or {}
    depth_threshold = runtime._to_optional_float(spalling_cfg.get("depth_threshold"))
    diameter_threshold = runtime._to_optional_float(spalling_cfg.get("diameter_threshold"))
    if depth_threshold is None or depth_threshold <= 0.0:
        depth_threshold = 10.0
    if diameter_threshold is None or diameter_threshold <= 0.0:
        diameter_threshold = 15.0
    return float(depth_threshold), float(diameter_threshold)


def sample_spalling_profile(runtime, defect_cfg, rng):
    depth_threshold, diameter_threshold = resolve_spalling_thresholds(runtime, defect_cfg)
    cs_weights = _resolve_cs_weights(runtime, defect_cfg, expected_len=2, default_weights=[1.0, 1.0])
    cs_level = _weighted_pick(["CS2", "CS3"], cs_weights, rng=rng)
    if cs_level == "CS2":
        depth_cm = runtime._uniform_sample(rng, 0.5 * depth_threshold, depth_threshold)
        diameter_cm = runtime._uniform_sample(rng, 0.5 * diameter_threshold, diameter_threshold)
    else:
        depth_cm = runtime._uniform_sample(rng, depth_threshold, 2.0 * depth_threshold)
        diameter_cm = runtime._uniform_sample(rng, diameter_threshold, 2.0 * diameter_threshold)
    return {
        "condition_state": cs_level,
        "target_metric_cm": max(1e-6, float(diameter_cm)),
        "target_spall_depth_cm": max(1e-4, float(depth_cm)),
        "depth_threshold": float(depth_threshold),
        "diameter_threshold": float(diameter_threshold),
    }


def resolve_target_profile(runtime, defect_type, defect_cfg, rng):
    defect_cfg = defect_cfg or {}
    if defect_type == "crack":
        return _sample_crack_profile(runtime, defect_cfg, rng)
    if defect_type == "spalling":
        return sample_spalling_profile(runtime, defect_cfg, rng)

    span_min = None
    span_max = None
    span_range = defect_cfg.get("span_range_cm")
    if isinstance(span_range, (list, tuple)) and len(span_range) >= 2:
        span_min = runtime._to_optional_float(span_range[0])
        span_max = runtime._to_optional_float(span_range[1])
    if span_min is None:
        span_min = runtime._to_optional_float(defect_cfg.get("span_min_cm"))
    if span_max is None:
        span_max = runtime._to_optional_float(defect_cfg.get("span_max_cm"))
    if span_min is None and span_max is None:
        span_fixed = runtime._to_optional_float(defect_cfg.get("span_cm"))
        if span_fixed is None or span_fixed <= 0.0:
            span_fixed = 12.0
        span_min = span_fixed
        span_max = span_fixed
    elif span_min is None:
        span_min = span_max
    elif span_max is None:
        span_max = span_min
    span_cm = runtime._uniform_sample(rng, max(1e-6, float(span_min)), max(1e-6, float(span_max)))
    cs_weights = _resolve_cs_weights(runtime, defect_cfg, expected_len=2, default_weights=[1.0, 1.0])
    cs_level = _weighted_pick(["CS2", "CS3"], cs_weights, rng=rng)
    return {
        "condition_state": cs_level,
        "target_metric_cm": float(span_cm),
    }


def _build_shape_from_overview_row(runtime, defect_type, row, defect_cfg, rng, target_profile=None):
    polygon_path = _resolve_polygon_path_from_row(row)
    payload = _load_polygon_payload(runtime, polygon_path)
    if payload is None:
        print(
            "Defect {}: polygon json not found/invalid for row '{}': {}".format(
                defect_type,
                row.get("instance_id"),
                polygon_path,
            )
        )
        return None

    metric_px = _sanitize_reference_metric_px(
        runtime,
        defect_type,
        _resolve_reference_metric_px(runtime, defect_type, row, payload),
        row,
        payload,
    )
    if metric_px is None:
        print(
            "Defect {}: skipped row '{}' because metric_px is invalid for bbox (bbox_w={}, bbox_h={}, width_px={}).".format(
                defect_type,
                row.get("instance_id"),
                row.get("bbox_w"),
                row.get("bbox_h"),
                row.get("width_px"),
            )
        )
        return None
    if not isinstance(target_profile, dict):
        target_profile = resolve_target_profile(runtime, defect_type, defect_cfg, rng=rng)
    target_metric_cm = max(1e-6, runtime._to_float(target_profile.get("target_metric_cm"), 1.0))
    metric_scale = target_metric_cm / metric_px

    centered_polygons = _scale_payload_polygons(runtime, payload, metric_scale)
    if not centered_polygons:
        return None

    primary = centered_polygons[0]
    secondary = centered_polygons[1] if len(centered_polygons) > 1 else None
    shape = {
        "source_file": payload["path"],
        "source_index": 0,
        "severity": None,
        "instance_id": row.get("instance_id") or payload.get("instance_id"),
        "instance_mask_path": row.get("instance_mask_path"),
        "width_px": payload["width_px"],
        "height_px": payload["height_px"],
        "metric_px": float(metric_px),
        "target_metric_cm": float(target_metric_cm),
        "metric_scale": float(metric_scale),
        "polygons": centered_polygons,
        "primary_poly": primary,
        "secondary_poly": secondary,
        "row": dict(row),
        "condition_state": target_profile.get("condition_state"),
    }
    for key in (
        "target_spall_depth_cm",
        "depth_threshold",
        "diameter_threshold",
        "severity_t1_cm",
        "severity_t2_cm",
    ):
        if key in target_profile:
            shape[key] = target_profile.get(key)

    if defect_type == "crack":
        shape.update(
            {
                "offset_poly": list(primary),
                "base_poly": list(primary),
                "crack_polys": [list(poly) for poly in centered_polygons],
                "inside_polys": [],
                "diff_polys": [],
            }
        )
    elif defect_type == "spalling":
        shape.update(
            {
                "offset_poly": list(primary),
                "base_poly": list(primary),
                "spall_poly": list(primary),
            }
        )
    elif defect_type == "efflore":
        outer_payload = None
        outer_polygons = []
        sampled_cs = runtime._normalize_condition_state(target_profile.get("condition_state"), default="CS2")
        if sampled_cs == "CS3":
            outer_payload = _load_polygon_payload(runtime, _resolve_expand_polygon_path_from_row(row))
            if outer_payload is not None:
                outer_polygons = _scale_payload_polygons(runtime, outer_payload, metric_scale)
        inner_poly, outer_poly = _resolve_efflore_polygons(runtime, centered_polygons, expand_polygons=outer_polygons)
        shape.update(
            {
                "offset_poly": list(inner_poly),
                "base_poly": list(inner_poly),
                "efflore_inner_poly": inner_poly,
                "efflore_outer_poly": outer_poly,
                "efflore_outer_polygons": [list(poly) for poly in outer_polygons],
                "efflore_outer_source_file": (outer_payload or {}).get("path"),
                "secondary_poly": list(outer_poly) if outer_poly else None,
            }
        )

    shape_radius_polygons = list(centered_polygons)
    if defect_type == "efflore":
        shape_radius_polygons.extend(shape.get("efflore_outer_polygons") or [])
    shape["shape_radius"] = _shape_radius_from_polygons(shape_radius_polygons)
    return shape


def _load_shapes_from_overview_csv(runtime, defect_type, cfg, defect_cfg, count, rng):
    overview_cfg = runtime._deep_merge(cfg.get("overview") or {}, defect_cfg.get("overview") or {})
    csv_path = overview_cfg.get("csv_path") or defect_cfg.get("overview_csv_path")
    if not csv_path:
        return []

    rows = _load_overview_rows(csv_path)
    if not rows:
        print("Defect {}: overview CSV has no rows: '{}'".format(defect_type, csv_path))
        return []

    requested = max(0, runtime._to_int(overview_cfg.get("sample_count"), count))
    shapes = []
    if defect_type == "crack":
        crack_families = {}
        for row in rows:
            base_id = _crack_base_instance_id(row)
            if not base_id:
                continue
            family = crack_families.setdefault(base_id, {})
            family[_classify_crack_row_condition_state(row)] = row

        missing_cs_counts = {}
        for _idx in range(requested):
            target_profile = resolve_target_profile(runtime, defect_type, defect_cfg, rng=rng)
            cs_level = str(target_profile.get("condition_state") or "").strip().upper()
            if cs_level not in ("CS1", "CS2", "CS3"):
                cs_level = "CS1"
            eligible_families = [family for family in crack_families.values() if family.get(cs_level)]
            if not eligible_families:
                missing_cs_counts[cs_level] = missing_cs_counts.get(cs_level, 0) + 1
                continue
            family = rng.choice(eligible_families)
            geometry_row = family.get("CS1") or family.get("CS2") or family.get("CS3")
            if not geometry_row:
                missing_cs_counts[cs_level] = missing_cs_counts.get(cs_level, 0) + 1
                continue
            shape = _build_shape_from_overview_row(
                runtime,
                defect_type,
                geometry_row,
                defect_cfg,
                rng=rng,
                target_profile=target_profile,
            )
            if shape is not None:
                shape["overview_source_instance_id"] = (family.get(cs_level) or {}).get("instance_id")
                shape["geometry_source_instance_id"] = geometry_row.get("instance_id")
                shapes.append(shape)

        if missing_cs_counts:
            details = ", ".join("{}:{}".format(key, int(missing_cs_counts[key])) for key in sorted(missing_cs_counts))
            print("Defect crack: missing overview rows for sampled CS levels ({})".format(details))
    else:
        for row in _sample_rows(rows, requested, rng=rng):
            shape = _build_shape_from_overview_row(runtime, defect_type, row, defect_cfg, rng=rng)
            if shape is not None:
                shapes.append(shape)

    if len(shapes) < requested:
        print("Defect {}: only {} of {} overview rows were usable.".format(defect_type, len(shapes), requested))
    return shapes


def resolve_shapes_for_type(runtime, defect_type, cfg, defect_cfg, count, rng):
    overview_shapes = _load_shapes_from_overview_csv(runtime, defect_type, cfg, defect_cfg, count, rng)
    if overview_shapes:
        if len(overview_shapes) >= count:
            return overview_shapes[:count]
        expanded = list(overview_shapes)
        while len(expanded) < count:
            expanded.append(rng.choice(overview_shapes))
        return expanded
    return []


def pick_shape_points(runtime, shape):
    offset_poly = shape.get("offset_poly") or shape.get("base_poly") or []
    base_poly = shape.get("base_poly") or offset_poly
    crack_polys = shape.get("crack_polys") or ([base_poly] if base_poly else [])
    inside_polys = shape.get("inside_polys") or []
    diff_polys = shape.get("diff_polys") or []
    return offset_poly, base_poly, crack_polys, inside_polys, diff_polys


def select_surface_cut_points(runtime, shape, default_points=None):
    spall_poly = shape.get("spall_poly") or []
    if len(spall_poly) >= 3:
        return list(spall_poly)

    base_poly = shape.get("base_poly") or []
    offset_poly = shape.get("offset_poly") or []
    if len(base_poly) >= 3 and len(offset_poly) >= 3:
        base_key = (abs(runtime._polygon_area(base_poly)), runtime._polygon_perimeter(base_poly))
        offset_key = (abs(runtime._polygon_area(offset_poly)), runtime._polygon_perimeter(offset_poly))
        return list(base_poly if base_key <= offset_key else offset_poly)
    if len(base_poly) >= 3:
        return list(base_poly)
    if len(offset_poly) >= 3:
        return list(offset_poly)

    primary = shape.get("primary_poly") or []
    if len(primary) >= 3:
        return list(primary)

    crack_polys = [points for points in (shape.get("crack_polys") or []) if len(points) >= 3]
    if crack_polys:
        crack_polys.sort(key=lambda pts: (abs(runtime._polygon_area(pts)), runtime._polygon_perimeter(pts)), reverse=True)
        return list(crack_polys[0])
    return list(default_points or [])


def select_crack_surface_cut_points(runtime, shape, crack_polys, default_points=None):
    crack_candidates = [points for points in (crack_polys or []) if len(points) >= 3]
    if crack_candidates:
        crack_candidates.sort(key=lambda pts: (abs(runtime._polygon_area(pts)), runtime._polygon_perimeter(pts)), reverse=True)
        return list(crack_candidates[0])

    diff_candidates = [points for points in (shape.get("diff_polys") or []) if len(points) >= 3]
    if diff_candidates:
        diff_candidates.sort(key=lambda pts: (abs(runtime._polygon_area(pts)), runtime._polygon_perimeter(pts)), reverse=True)
        return list(diff_candidates[0])

    base_poly = shape.get("base_poly") or []
    if len(base_poly) >= 3:
        return list(base_poly)
    offset_poly = shape.get("offset_poly") or []
    if len(offset_poly) >= 3:
        return list(offset_poly)
    return list(default_points or [])
