"""Unified defect placement interfaces for crack/efflore/spalling/exposed-rebar."""

import copy
import math
import random
import sys

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc import defect_placement_crack
from utils_loc import defect_placement_efflore
from utils_loc import defect_placement_reference
from utils_loc import defect_placement_spalling
from utils_loc import defect_placement_templates
from utils_loc.defect_modeling import get_surfaces, subtract_surface


_DEFECT_MODELERS = {
    "crack": defect_placement_crack.model_crack_instance,
    "efflore": defect_placement_efflore.model_efflore_instance,
    "spalling": defect_placement_spalling.model_spalling_instance,
}


def _deep_merge(base, override):
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes", "y", "on"):
        return True
    if text in ("0", "false", "f", "no", "n", "off"):
        return False
    return bool(default)


def _to_optional_float(value, default=None):
    if value is None:
        return default
    text = str(value).strip() if isinstance(value, str) else value
    if text in ("", "none", "null"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _vec3(value):
    if hasattr(value, "X") and hasattr(value, "Y"):
        return float(value.X), float(value.Y), float(getattr(value, "Z", 0.0))
    if len(value) == 2:
        return float(value[0]), float(value[1]), 0.0
    return float(value[0]), float(value[1]), float(value[2])


def _add(a, b):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return ax + bx, ay + by, az + bz


def _sub(a, b):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return ax - bx, ay - by, az - bz


def _scale(v, scalar):
    x, y, z = _vec3(v)
    s = float(scalar)
    return x * s, y * s, z * s


def _lerp(a, b, t):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    ratio = float(t)
    return (
        ax + (bx - ax) * ratio,
        ay + (by - ay) * ratio,
        az + (bz - az) * ratio,
    )


def _cross(a, b):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx


def _dot(a, b):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return ax * bx + ay * by + az * bz


def _norm(v):
    x, y, z = _vec3(v)
    return math.sqrt(x * x + y * y + z * z)


def _unit(v, fallback=(0.0, 0.0, 1.0)):
    length = _norm(v)
    if length <= 1e-12:
        flen = _norm(fallback)
        if flen <= 1e-12:
            return 0.0, 0.0, 1.0
        return _scale(fallback, 1.0 / flen)
    return _scale(v, 1.0 / length)


def _distance(a, b):
    dx, dy, dz = _sub(a, b)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _point_key(point, precision=4):
    x, y, z = _vec3(point)
    p = max(0, _to_int(precision, 4))
    return (round(x, p), round(y, p), round(z, p))


def _ensure_closed(points):
    pts = [tuple(point) for point in points or []]
    if len(pts) < 3:
        return pts
    if _distance(pts[0], pts[-1]) > 1e-6:
        pts.append(pts[0])
    return pts


def _unique_points(points):
    unique = []
    for point in points or []:
        p = tuple(point)
        if not unique or _distance(unique[-1], p) > 1e-6:
            unique.append(p)
    if len(unique) > 1 and _distance(unique[0], unique[-1]) <= 1e-6:
        unique.pop()
    return unique


def _polygon_area(points):
    pts = [tuple(point) for point in points or []]
    if len(pts) < 3:
        return 0.0
    area2 = 0.0
    for idx in range(len(pts)):
        x1, y1 = pts[idx]
        x2, y2 = pts[(idx + 1) % len(pts)]
        area2 += x1 * y2 - x2 * y1
    return 0.5 * area2


def _polygon_perimeter(points):
    pts = _unique_points(points)
    if len(pts) < 2:
        return 0.0
    perimeter = 0.0
    for idx in range(len(pts)):
        perimeter += _distance(pts[idx], pts[(idx + 1) % len(pts)])
    return perimeter


def _resample_closed_polygon(points, target_count):
    pts = _unique_points(points)
    if len(pts) < 3:
        return pts

    target_count = max(3, _to_int(target_count, len(pts)))
    if len(pts) <= target_count:
        return pts

    cumulative = [0.0]
    for idx in range(len(pts)):
        segment_len = _distance(pts[idx], pts[(idx + 1) % len(pts)])
        cumulative.append(cumulative[-1] + segment_len)

    perimeter = cumulative[-1]
    if perimeter <= 1e-9:
        return pts[:target_count]

    sampled = []
    step = perimeter / float(target_count)
    segment_idx = 0
    for sample_idx in range(target_count):
        distance_along = step * float(sample_idx)
        while segment_idx + 1 < len(cumulative) and cumulative[segment_idx + 1] < distance_along:
            segment_idx += 1
        start = pts[segment_idx % len(pts)]
        end = pts[(segment_idx + 1) % len(pts)]
        seg_start = cumulative[segment_idx]
        seg_end = cumulative[segment_idx + 1]
        seg_len = max(1e-9, seg_end - seg_start)
        local_t = (distance_along - seg_start) / seg_len
        sampled.append(_lerp(start, end, local_t))
    return sampled


def _polygon_centroid(points):
    pts = [tuple(point) for point in points or []]
    if len(pts) < 3:
        return 0.0, 0.0
    area = _polygon_area(pts)
    if abs(area) <= 1e-9:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return sum(xs) / float(len(xs)), sum(ys) / float(len(ys))
    factor = 1.0 / (6.0 * area)
    cx = 0.0
    cy = 0.0
    for idx in range(len(pts)):
        x0, y0 = pts[idx]
        x1, y1 = pts[(idx + 1) % len(pts)]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return cx * factor, cy * factor


def _point_on_segment_2d(point, start, end, tolerance=1e-9):
    px, py = point
    x0, y0 = start
    x1, y1 = end
    cross = (px - x0) * (y1 - y0) - (py - y0) * (x1 - x0)
    if abs(cross) > tolerance:
        return False
    dot = (px - x0) * (x1 - x0) + (py - y0) * (y1 - y0)
    if dot < -tolerance:
        return False
    seg_len_sq = (x1 - x0) ** 2 + (y1 - y0) ** 2
    if dot - seg_len_sq > tolerance:
        return False
    return True


def _point_in_polygon_2d(point, polygon, tolerance=1e-9):
    pts = [tuple(p) for p in polygon or []]
    if len(pts) < 3:
        return False

    inside = False
    px, py = point
    for idx in range(len(pts)):
        x0, y0 = pts[idx]
        x1, y1 = pts[(idx + 1) % len(pts)]
        if _point_on_segment_2d((px, py), (x0, y0), (x1, y1), tolerance=tolerance):
            return True
        intersects = ((y0 > py) != (y1 > py))
        if not intersects:
            continue
        denom = (y1 - y0)
        if abs(denom) <= tolerance:
            continue
        x_cross = x0 + (py - y0) * (x1 - x0) / denom
        if x_cross >= px - tolerance:
            inside = not inside
    return inside


def _polygon_sample_point_3d(points):
    pts = _unique_points(points)
    if not pts:
        return None
    if len(pts) == 1:
        return tuple(pts[0])
    if len(pts) == 2:
        return _lerp(pts[0], pts[1], 0.5)

    origin = pts[0]
    normal = (0.0, 0.0, 0.0)
    for idx in range(len(pts)):
        normal = _add(normal, _cross(pts[idx], pts[(idx + 1) % len(pts)]))
    normal = _unit(normal, fallback=(0.0, 0.0, 1.0))
    ref = (1.0, 0.0, 0.0) if abs(_dot(normal, (1.0, 0.0, 0.0))) < 0.9 else (0.0, 1.0, 0.0)
    u_axis = _unit(_cross(ref, normal), fallback=(1.0, 0.0, 0.0))
    v_axis = _unit(_cross(normal, u_axis), fallback=(0.0, 1.0, 0.0))

    local = [
        (
            _dot(_sub(point, origin), u_axis),
            _dot(_sub(point, origin), v_axis),
        )
        for point in pts
    ]
    xs = [pt[0] for pt in local]
    ys = [pt[1] for pt in local]
    avg_uv = (
        sum(xs) / float(len(xs)),
        sum(ys) / float(len(ys)),
    )
    bbox_center = (
        0.5 * (min(xs) + max(xs)),
        0.5 * (min(ys) + max(ys)),
    )
    centroid_uv = _polygon_centroid(local)

    candidate_uvs = [centroid_uv, avg_uv, bbox_center]
    grid_count = 5
    if max(xs) - min(xs) > 1e-9 and max(ys) - min(ys) > 1e-9:
        for iy in range(grid_count):
            ty = (iy + 0.5) / float(grid_count)
            y = min(ys) + (max(ys) - min(ys)) * ty
            for ix in range(grid_count):
                tx = (ix + 0.5) / float(grid_count)
                x = min(xs) + (max(xs) - min(xs)) * tx
                candidate_uvs.append((x, y))

    seen = set()
    for uv in candidate_uvs:
        key = (round(float(uv[0]), 6), round(float(uv[1]), 6))
        if key in seen:
            continue
        seen.add(key)
        if not _point_in_polygon_2d(uv, local):
            continue
        return _add(origin, _add(_scale(u_axis, uv[0]), _scale(v_axis, uv[1])))

    return _add(origin, _add(_scale(u_axis, centroid_uv[0]), _scale(v_axis, centroid_uv[1])))


def _uniform_sample(rng, lo, hi):
    lo = float(lo)
    hi = float(hi)
    if lo > hi:
        lo, hi = hi, lo
    if abs(hi - lo) <= 1e-12:
        return lo
    return rng.uniform(lo, hi)


def _clamp(value, lo, hi):
    return max(float(lo), min(float(hi), float(value)))


def _numeric_choices(values):
    out = []
    for value in values or []:
        num = _to_optional_float(value)
        if num is None or not math.isfinite(num):
            continue
        out.append(float(num))
    return out


def _choice_weights(raw_weights, expected_len):
    if not isinstance(raw_weights, (list, tuple)):
        return [1.0] * max(0, int(expected_len))
    weights = []
    for idx in range(max(0, int(expected_len))):
        base = raw_weights[idx] if idx < len(raw_weights) else 1.0
        weights.append(max(0.0, _to_float(base, 1.0)))
    if sum(weights) <= 1e-12:
        return [1.0] * max(0, int(expected_len))
    return weights


def _sample_numeric_choice(choices, rng, weights=None, default=None):
    values = _numeric_choices(choices)
    if not values:
        return None if default is None else float(default)
    picked = _weighted_pick(values, _choice_weights(weights, len(values)), rng=rng)
    return float(picked if picked is not None else values[0])


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


def _sample_numeric_range(cfg, range_key, min_key=None, max_key=None, fixed_key=None, rng=None, default=None):
    rng = rng or random.Random()
    raw_range = cfg.get(range_key)
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
        lo = _to_optional_float(raw_range[0])
        hi = _to_optional_float(raw_range[1])
        if lo is not None or hi is not None:
            if lo is None:
                lo = hi
            if hi is None:
                hi = lo
            if lo is not None and hi is not None:
                return float(_uniform_sample(rng, lo, hi))

    lo = _to_optional_float(cfg.get(min_key)) if min_key else None
    hi = _to_optional_float(cfg.get(max_key)) if max_key else None
    if lo is not None or hi is not None:
        if lo is None:
            lo = hi
        if hi is None:
            hi = lo
        if lo is not None and hi is not None:
            return float(_uniform_sample(rng, lo, hi))

    if fixed_key:
        fixed = _to_optional_float(cfg.get(fixed_key))
        if fixed is not None:
            return float(fixed)
    return None if default is None else float(default)


def _spall_radial_scale_for_depth_ratio(depth_ratio):
    return max(0.03, 1.0 - 0.92 * _clamp(depth_ratio, 0.0, 1.0))


def _scale_polygon_about_centroid(points, scale, centroid=None):
    pts = [(float(x), float(y)) for x, y in _unique_points(points)]
    if len(pts) < 3:
        return pts
    if centroid is None:
        centroid = _polygon_centroid(pts)
    cx, cy = centroid
    out = []
    factor = float(scale)
    for x, y in pts:
        out.append((cx + (x - cx) * factor, cy + (y - cy) * factor))
    return out


def _polygon_line_intervals(points, axis, value, tol=1e-9):
    pts = [(float(x), float(y)) for x, y in _unique_points(points)]
    if len(pts) < 3:
        return []

    vertical = str(axis).strip().lower() == "x"
    intercepts = []
    for idx in range(len(pts)):
        x1, y1 = pts[idx]
        x2, y2 = pts[(idx + 1) % len(pts)]
        a1 = x1 if vertical else y1
        a2 = x2 if vertical else y2
        b1 = y1 if vertical else x1
        b2 = y2 if vertical else x2
        if abs(a2 - a1) <= tol:
            continue
        low = min(a1, a2)
        high = max(a1, a2)
        if float(value) < low or float(value) >= high:
            continue
        t = (float(value) - a1) / float(a2 - a1)
        intercepts.append(b1 + (b2 - b1) * t)

    intercepts.sort()
    intervals = []
    for idx in range(0, len(intercepts) - 1, 2):
        start = float(intercepts[idx])
        end = float(intercepts[idx + 1])
        if end - start > tol:
            intervals.append((start, end))
    return intervals


def _polygon_line_visibility(points, axis, value):
    intervals = _polygon_line_intervals(points, axis=axis, value=value)
    if not intervals:
        return {
            "intervals": [],
            "total_length": 0.0,
            "longest_length": 0.0,
            "best_interval": None,
        }

    total_length = 0.0
    best_interval = None
    longest = 0.0
    for start, end in intervals:
        seg_len = max(0.0, float(end) - float(start))
        total_length += seg_len
        if seg_len > longest:
            longest = seg_len
            best_interval = (float(start), float(end))
    return {
        "intervals": intervals,
        "total_length": float(total_length),
        "longest_length": float(longest),
        "best_interval": best_interval,
    }


def _polyline_length(points):
    pts = [tuple(_vec3(point)) for point in points or []]
    if len(pts) < 2:
        return 0.0
    total = 0.0
    for idx in range(len(pts) - 1):
        total += _distance(pts[idx], pts[idx + 1])
    return float(total)


def _point_at_polyline_distance(points, distance_along):
    pts = [tuple(_vec3(point)) for point in points or []]
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0]

    total = _polyline_length(pts)
    if total <= 1e-9:
        return pts[0]

    target = _clamp(distance_along, 0.0, total)
    walked = 0.0
    for idx in range(len(pts) - 1):
        start = pts[idx]
        end = pts[idx + 1]
        seg_len = _distance(start, end)
        if seg_len <= 1e-9:
            continue
        if walked + seg_len >= target:
            local = (target - walked) / seg_len
            return _lerp(start, end, local)
        walked += seg_len
    return pts[-1]


def _add_curve(points):
    cleaned = []
    for point in points or []:
        vec = _try_vec3(point)
        if vec is None:
            continue
        if not cleaned or _distance(cleaned[-1], vec) > 1e-6:
            cleaned.append(vec)
    if len(cleaned) < 2:
        return None
    if len(cleaned) == 2:
        try:
            return rs.AddLine(cleaned[0], cleaned[1])
        except Exception:
            return None
    try:
        curve_id = rs.AddInterpCurve(cleaned, degree=3)
    except TypeError:
        try:
            curve_id = rs.AddInterpCurve(cleaned)
        except Exception:
            curve_id = None
    except Exception:
        curve_id = None
    if curve_id:
        return curve_id
    try:
        return rs.AddPolyline(cleaned)
    except Exception:
        return None


def _sample_rebar_radius_cm(rebar_cfg, rng):
    radius = _sample_numeric_choice(
        rebar_cfg.get("radius_choices_cm") or rebar_cfg.get("radius_choices"),
        rng=rng,
        weights=rebar_cfg.get("radius_weights"),
    )
    if radius is not None and radius > 0.0:
        return float(radius)

    radius_min, radius_max = rebar_cfg.get("radius_range", [0.8, 2.5])
    radius_min = max(0.05, _to_float(radius_min, 0.8))
    radius_max = max(0.05, _to_float(radius_max, 2.5))
    if radius_min > radius_max:
        radius_min, radius_max = radius_max, radius_min
    return float(rng.uniform(radius_min, radius_max))


def _minimum_rebar_center_spacing_cm(diameter_cm, max_aggregate_size_cm=2.54):
    diameter_cm = max(0.1, float(diameter_cm))
    aggregate_cm = max(0.0, _to_float(max_aggregate_size_cm, 2.54))
    # Approximate AASHTO/Caltrans minimum spacing using clear spacing + bar diameter.
    min_clear_spacing = max(1.5 * diameter_cm, 1.5 * aggregate_cm, 3.81)
    return float(diameter_cm + min_clear_spacing)


def _sample_rebar_cover_depth_cm(rebar_cfg, radius_cm, rng):
    cover_depth = _sample_numeric_range(
        rebar_cfg,
        range_key="cover_depth_range",
        min_key="cover_depth_min",
        max_key="cover_depth_max",
        fixed_key="cover_depth",
        rng=rng,
        default=2.0,
    )
    return float(max(radius_cm + 0.25, float(cover_depth)))


def _sample_rebar_spacing_cm(rebar_cfg, diameter_cm, span_hint_cm, rng):
    min_center_spacing = _minimum_rebar_center_spacing_cm(
        diameter_cm,
        max_aggregate_size_cm=rebar_cfg.get("max_aggregate_size_cm", 2.54),
    )
    choices = _numeric_choices(
        rebar_cfg.get("spacing_choices_cm")
        or rebar_cfg.get("spacing_choices")
    )
    if choices:
        filtered = [value for value in choices if value >= min_center_spacing - 1e-6]
        if not filtered:
            filtered = [max(min_center_spacing, min(choices))]
        span_hint_cm = max(0.0, float(span_hint_cm))
        if span_hint_cm > 0.0:
            preferred = [
                value for value in filtered
                if value <= max(min_center_spacing, 1.35 * span_hint_cm)
            ]
            if preferred:
                filtered = preferred
        spacing = _sample_numeric_choice(
            filtered,
            rng=rng,
            weights=rebar_cfg.get("spacing_weights"),
            default=min_center_spacing,
        )
        return float(max(min_center_spacing, spacing))

    spacing = _to_optional_float(rebar_cfg.get("spacing"))
    if spacing is None:
        spacing = min_center_spacing if span_hint_cm <= 0.0 else min_center_spacing
    return float(max(min_center_spacing, float(spacing)))


def _build_rebar_centerline_points(start, end, radius_cm, lateral_axis, inward_normal, rebar_cfg, rng):
    if not _to_bool(rebar_cfg.get("curve_enabled"), default=True):
        return [tuple(_vec3(start)), tuple(_vec3(end))]

    curve_range = rebar_cfg.get("curve_offset_ratio_range")
    if not isinstance(curve_range, (list, tuple)) or len(curve_range) < 2:
        curve_range = [0.15, 0.45]
    ratio_min = max(0.0, _to_float(curve_range[0], 0.15))
    ratio_max = max(0.0, _to_float(curve_range[1], 0.45))
    if ratio_min > ratio_max:
        ratio_min, ratio_max = ratio_max, ratio_min

    diameter_cm = 2.0 * float(radius_cm)
    offset_mag = _uniform_sample(rng, ratio_min, ratio_max) * diameter_cm
    if offset_mag <= 1e-6:
        return [tuple(_vec3(start)), tuple(_vec3(end))]

    length = _distance(start, end)
    offset_mag = min(offset_mag, 0.08 * max(length, diameter_cm))
    if offset_mag <= 1e-6:
        return [tuple(_vec3(start)), tuple(_vec3(end))]

    normal_ratio = _clamp(_to_float(rebar_cfg.get("curve_normal_ratio"), 0.25), 0.0, 1.0)
    bend_axis = _unit(
        _add(_scale(lateral_axis, 1.0), _scale(inward_normal, normal_ratio)),
        fallback=lateral_axis,
    )
    sign = -1.0 if rng.random() < 0.5 else 1.0
    cp1 = _add(_lerp(start, end, 0.32), _scale(bend_axis, sign * offset_mag))
    cp2 = _add(_lerp(start, end, 0.68), _scale(bend_axis, -sign * offset_mag * rng.uniform(0.4, 0.9)))
    return [tuple(_vec3(start)), tuple(_vec3(cp1)), tuple(_vec3(cp2)), tuple(_vec3(end))]


def _visible_interval_distances(interval, axis_range, path_length):
    if not interval or not axis_range:
        return None
    axis_start, axis_end = axis_range
    span = float(axis_end) - float(axis_start)
    if abs(span) <= 1e-9 or path_length <= 1e-9:
        return None
    lo = (float(interval[0]) - float(axis_start)) / span
    hi = (float(interval[1]) - float(axis_start)) / span
    lo = _clamp(lo, 0.0, 1.0)
    hi = _clamp(hi, 0.0, 1.0)
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo <= 1e-6:
        return None
    return float(lo * path_length), float(hi * path_length)


def _build_rebar_rib_ids(centerline_points, radius_cm, axis_range, visible_interval, rebar_cfg):
    if not _to_bool(rebar_cfg.get("rib_enabled"), default=True):
        return []

    path_length = _polyline_length(centerline_points)
    distances = _visible_interval_distances(visible_interval, axis_range, path_length)
    if not distances:
        return []

    visible_start, visible_end = distances
    visible_length = max(0.0, visible_end - visible_start)
    if visible_length <= 1e-6:
        return []

    diameter_cm = 2.0 * float(radius_cm)
    spacing_factor = max(0.5, _to_float(rebar_cfg.get("rib_spacing_diameter_factor"), 1.2))
    band_factor = max(0.2, _to_float(rebar_cfg.get("rib_band_diameter_factor"), 0.5))
    height_ratio = max(0.01, _to_float(rebar_cfg.get("rib_height_ratio"), 0.08))
    max_count = max(0, _to_int(rebar_cfg.get("rib_max_count"), 8))
    if max_count <= 0:
        return []

    rib_spacing = max(0.5, diameter_cm * spacing_factor)
    rib_band = max(0.2, diameter_cm * band_factor)
    rib_radius = float(radius_cm) + max(0.02, min(0.2, float(radius_cm) * height_ratio))

    start = visible_start + 0.5 * rib_band
    end = visible_end - 0.5 * rib_band
    if end <= start:
        mid = 0.5 * (visible_start + visible_end)
        start = mid
        end = mid

    rib_ids = []
    center = float(start)
    while center <= end + 1e-6 and len(rib_ids) < max_count:
        seg_start = _point_at_polyline_distance(centerline_points, center - 0.5 * rib_band)
        seg_end = _point_at_polyline_distance(centerline_points, center + 0.5 * rib_band)
        if seg_start is not None and seg_end is not None and _distance(seg_start, seg_end) > 1e-3:
            rib_ids.extend(_make_rebar_pipe(seg_start, seg_end, rib_radius))
        center += rib_spacing

    if not rib_ids:
        mid = 0.5 * (visible_start + visible_end)
        seg_start = _point_at_polyline_distance(centerline_points, mid - 0.5 * rib_band)
        seg_end = _point_at_polyline_distance(centerline_points, mid + 0.5 * rib_band)
        if seg_start is not None and seg_end is not None and _distance(seg_start, seg_end) > 1e-3:
            rib_ids.extend(_make_rebar_pipe(seg_start, seg_end, rib_radius))
    return rib_ids


def _resolve_spalling_thresholds(spalling_cfg):
    return defect_placement_templates.resolve_spalling_thresholds(sys.modules[__name__], spalling_cfg)


def _sample_spalling_profile(defect_cfg, rng):
    return defect_placement_templates.sample_spalling_profile(sys.modules[__name__], defect_cfg, rng)


def _resolve_target_profile(defect_type, defect_cfg, rng):
    return defect_placement_templates.resolve_target_profile(sys.modules[__name__], defect_type, defect_cfg, rng)


def _coerce_ids(items):
    ids = []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, (list, tuple)):
            ids.extend(_coerce_ids(item))
            continue
        obj_id = getattr(item, "Id", item)
        obj_id = rs.coerceguid(obj_id, False)
        if obj_id:
            ids.append(obj_id)
    return ids


def _as_strings(ids):
    return [str(obj_id) for obj_id in _coerce_ids(ids)]


def _delete_objects(obj_ids):
    for obj_id in _coerce_ids(obj_ids):
        if rs.IsObject(obj_id):
            rs.DeleteObject(obj_id)


def _try_vec3(value):
    try:
        return _vec3(value)
    except (TypeError, ValueError, IndexError):
        return None


def _layer_path_parts(layer_name):
    return [part.strip() for part in str(layer_name).split("::") if part.strip()]


def ensure_layer(layer_name):
    if not layer_name:
        return None
    if rs.IsLayer(layer_name):
        return layer_name

    parts = _layer_path_parts(layer_name)
    if not parts:
        return None
    current = None
    for part in parts:
        full_path = part if current is None else "{}::{}".format(current, part)
        if rs.IsLayer(full_path):
            current = full_path
            continue
        created = rs.AddLayer(name=part, parent=current)
        current = created or full_path
    return current


def _assign_layer(obj_ids, layer_name):
    if not layer_name:
        return
    ensure_layer(layer_name)
    if not rs.IsLayer(layer_name):
        return
    for obj_id in _coerce_ids(obj_ids):
        if rs.IsObject(obj_id):
            rs.ObjectLayer(obj_id, layer_name)


def _collect_object_ids(model_result):
    if not isinstance(model_result, dict):
        return None
    object_ids = []
    for key in ("surfaces", "solids", "polylines"):
        object_ids.extend(_coerce_ids(model_result.get(key) or []))
    by_component = model_result.get("objects_by_component") or {}
    for ids in by_component.values():
        object_ids.extend(_coerce_ids(ids))
    if not object_ids:
        return None
    ordered = []
    seen = set()
    for obj_id in object_ids:
        if obj_id in seen:
            continue
        seen.add(obj_id)
        ordered.append(obj_id)
    return ordered


def _surface_curve_length(surface_id, direction, cross_param, sample_steps=24):
    return defect_placement_reference.surface_curve_length(
        sys.modules[__name__],
        surface_id,
        direction,
        cross_param,
        sample_steps=sample_steps,
    )


def _surface_uv_lengths(surface_id):
    return defect_placement_reference.surface_uv_lengths(sys.modules[__name__], surface_id)


def _uv_axis_boundary_distances_from_surface_data(uv, length_u, length_v, domain_u, domain_v):
    return defect_placement_reference.uv_axis_boundary_distances_from_surface_data(
        sys.modules[__name__],
        uv,
        length_u,
        length_v,
        domain_u,
        domain_v,
    )


def _surface_axes(surface_id, point, normal):
    return defect_placement_reference.surface_axes(sys.modules[__name__], surface_id, point, normal)


def _surface_normal_at_point(surface_id, point, fallback=(0.0, 0.0, 1.0)):
    return defect_placement_reference.surface_normal_at_point(
        sys.modules[__name__],
        surface_id,
        point,
        fallback=fallback,
    )


def _surface_sample_point(surface_id):
    return defect_placement_reference.surface_sample_point(sys.modules[__name__], surface_id)


def _resolve_efflore_z_threshold_deg(defect_cfg):
    return defect_placement_reference.resolve_efflore_z_threshold_deg(sys.modules[__name__], defect_cfg)


def _filter_surface_pool_for_type(surface_ids, defect_type, defect_cfg=None):
    return defect_placement_reference.filter_surface_pool_for_type(
        sys.modules[__name__],
        surface_ids,
        defect_type,
        defect_cfg=defect_cfg,
    )


def _flip_surface_if_conflicts_with_normal(surface_id, desired_normal, min_opposite_dot=0.25):
    return defect_placement_reference.flip_surface_if_conflicts_with_normal(
        sys.modules[__name__],
        surface_id,
        desired_normal,
        min_opposite_dot=min_opposite_dot,
    )


def _orient_surfaces_to_normal(object_ids, desired_normal, min_opposite_dot=0.25):
    return defect_placement_reference.orient_surfaces_to_normal(
        sys.modules[__name__],
        object_ids,
        desired_normal,
        min_opposite_dot=min_opposite_dot,
    )


def _candidate_axes(candidate):
    return defect_placement_reference.candidate_axes(sys.modules[__name__], candidate)


def _candidate_outward_normal(candidate):
    return defect_placement_reference.candidate_outward_normal(sys.modules[__name__], candidate)


def _candidate_inward_normal(candidate):
    return defect_placement_reference.candidate_inward_normal(sys.modules[__name__], candidate)


def _project_points_to_surface(points_2d, candidate, angle_deg, normal_offset=0.0):
    return defect_placement_reference.project_points_to_surface(
        sys.modules[__name__],
        points_2d,
        candidate,
        angle_deg,
        normal_offset=normal_offset,
    )


def _collect_reference_candidates(cfg, model_result=None, defect_type=None, defect_cfg=None, layer_map=None, debug_cfg=None):
    return defect_placement_reference.collect_reference_candidates(
        sys.modules[__name__],
        cfg,
        model_result=model_result,
        defect_type=defect_type,
        defect_cfg=defect_cfg,
        layer_map=layer_map,
        debug_cfg=debug_cfg,
    )


def _add_polyline(points, layer_name=None):
    cleaned = []
    for point in points or []:
        p = _try_vec3(point)
        if p is None:
            continue
        if not all(math.isfinite(v) for v in p):
            continue
        if not cleaned or _distance(cleaned[-1], p) > 1e-6:
            cleaned.append(p)

    if len(cleaned) > 1 and _distance(cleaned[0], cleaned[-1]) <= 1e-6:
        cleaned.pop()
    if len(cleaned) < 3:
        return None

    closed = list(cleaned)
    closed.append(cleaned[0])
    try:
        obj_id = rs.AddPolyline(closed)
    except Exception:
        return None
    if obj_id and layer_name:
        _assign_layer([obj_id], layer_name)
    return obj_id


def _add_mask_from_polygon(points, layer_name, as_surface=True):
    mask_ids = []
    curve_id = _add_polyline(points, layer_name=layer_name)
    if not curve_id:
        return mask_ids
    mask_ids.append(curve_id)
    if as_surface:
        surfaces = _coerce_ids(rs.AddPlanarSrf(curve_id) or [])
        if surfaces:
            _assign_layer(surfaces, layer_name)
            mask_ids.extend(surfaces)
    return mask_ids


def _resolve_random_value(defect_cfg, random_cfg, key, default):
    value = defect_cfg.get(key)
    if value is None:
        value = random_cfg.get(key, default)
    return value


def _normal_elevation_from_xy_deg(normal):
    return defect_placement_reference.normal_elevation_from_xy_deg(sys.modules[__name__], normal)


def _sample_transform(defect_type, defect_cfg, random_cfg, candidate, shape, rng):
    angle_min = _to_float(_resolve_random_value(defect_cfg, random_cfg, "orientation_min_deg", 0.0), 0.0)
    angle_max = _to_float(_resolve_random_value(defect_cfg, random_cfg, "orientation_max_deg", 360.0), 360.0)
    if angle_min > angle_max:
        angle_min, angle_max = angle_max, angle_min

    boundary_margin = _to_float(_resolve_random_value(defect_cfg, random_cfg, "boundary_margin", 0.9), 0.9)
    boundary_margin = max(0.0, min(1.0, boundary_margin))
    shape_radius = _to_float(shape.get("shape_radius"), 0.0)

    if shape_radius > 1e-9 and shape_radius > (float(candidate["boundary_dist"]) * boundary_margin):
        return None

    if str(defect_type or "").strip().lower() == "efflore":
        angle_deg = 0.0
    else:
        angle_deg = rng.uniform(angle_min, angle_max)
    normal_offset = _to_float(_resolve_random_value(defect_cfg, random_cfg, "normal_offset", 0.0), 0.0)

    return {
        "angle_deg": angle_deg,
        "normal_offset": normal_offset,
    }


def _resolve_shapes_for_type(defect_type, cfg, defect_cfg, count, rng):
    return defect_placement_templates.resolve_shapes_for_type(
        sys.modules[__name__],
        defect_type,
        cfg,
        defect_cfg,
        count,
        rng,
    )


def _pick_shape_points(shape):
    return defect_placement_templates.pick_shape_points(sys.modules[__name__], shape)


def _select_surface_cut_points(shape, default_points=None):
    return defect_placement_templates.select_surface_cut_points(
        sys.modules[__name__],
        shape,
        default_points=default_points,
    )


def _select_crack_surface_cut_points(shape, crack_polys, default_points=None):
    return defect_placement_templates.select_crack_surface_cut_points(
        sys.modules[__name__],
        shape,
        crack_polys,
        default_points=default_points,
    )


def _resolve_record_surface_cut_polygons(record):
    polygons = []
    for polygon in (record or {}).get("surface_cut_polygons") or []:
        pts = [list(_vec3(pt)) for pt in _ensure_closed(polygon or [])]
        if len(pts) >= 4:
            polygons.append(pts)
    if polygons:
        return polygons

    polygon = (record or {}).get("surface_cut_polygon") or []
    pts = [list(_vec3(pt)) for pt in _ensure_closed(polygon)]
    if len(pts) >= 4:
        return [pts]
    return []


def _surface_subtraction_strategy(record):
    defect_type = str((record or {}).get("type") or "").strip().lower()
    if defect_type in ("spalling", "exposed_rebar"):
        return "cavity"
    if defect_type == "crack":
        return "strip"
    return "generic"


def _surface_subtraction_record_priority(record):
    strategy = _surface_subtraction_strategy(record)
    if strategy == "cavity":
        return 0
    if strategy == "strip":
        return 1
    return 2


def _surface_subtraction_discard_points(record, polygons):
    if _surface_subtraction_strategy(record) != "cavity":
        return []
    discard_points = []
    for polygon in polygons or []:
        sample_point = _polygon_sample_point_3d(polygon)
        if sample_point is not None:
            discard_points.append(sample_point)
    return discard_points


def _object_key(value):
    text = str(value or "").strip()
    return text.lower() if text else ""


def _surface_point_distance(surface_id, point):
    if not surface_id or not rs.IsObject(surface_id) or not rs.IsSurface(surface_id):
        return None
    point_3d = _try_vec3(point)
    if point_3d is None:
        return None
    try:
        uv = rs.SurfaceClosestPoint(surface_id, point_3d)
    except Exception:
        uv = None
    if not uv:
        return None
    try:
        surface_point = rs.EvaluateSurface(surface_id, uv[0], uv[1])
    except Exception:
        surface_point = None
    if surface_point is None:
        return None
    return _distance(surface_point, point_3d)


def _surface_normal_alignment(surface_id, point, normal):
    if not surface_id or not rs.IsObject(surface_id) or not rs.IsSurface(surface_id):
        return 0.0
    point_3d = _try_vec3(point)
    normal_3d = _try_vec3(normal)
    if point_3d is None or normal_3d is None:
        return 0.0
    try:
        uv = rs.SurfaceClosestPoint(surface_id, point_3d)
    except Exception:
        uv = None
    if not uv:
        return 0.0
    try:
        surface_normal = rs.SurfaceNormal(surface_id, uv)
    except Exception:
        surface_normal = None
    if surface_normal is None:
        return 0.0
    return abs(_dot(_unit(surface_normal, fallback=normal_3d), _unit(normal_3d, fallback=surface_normal)))


def _resolve_surface_subtraction_target(record, target_by_key, by_layer_surfaces):
    target_id = target_by_key.get(_object_key(record.get("surface_id")))
    if target_id and rs.IsObject(target_id):
        return target_id

    layer_name = str(record.get("surface_layer") or "")
    candidates = [sid for sid in by_layer_surfaces.get(layer_name, []) if rs.IsObject(sid)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    point = _try_vec3(record.get("point"))
    if point is None:
        return candidates[0]

    normal = _try_vec3(record.get("normal")) or (0.0, 0.0, 1.0)
    best_id = None
    best_score = None
    for sid in candidates:
        on_surface = False
        try:
            on_surface = bool(rs.IsPointOnSurface(sid, point))
        except Exception:
            on_surface = False
        distance = _surface_point_distance(sid, point)
        if distance is None:
            distance = float("inf")
        alignment = _surface_normal_alignment(sid, point, normal)
        score = (
            1 if on_surface else 0,
            alignment,
            -distance,
        )
        if best_score is None or score > best_score:
            best_id = sid
            best_score = score

    return best_id


def _create_seed_marker(candidate, layer_name, radius_coef=0.04375, min_radius=0.625, axis_scale=1.6):
    return defect_placement_reference.create_seed_marker(
        sys.modules[__name__],
        candidate,
        layer_name,
        radius_coef=radius_coef,
        min_radius=min_radius,
        axis_scale=axis_scale,
    )


def _basis_from_normal(normal):
    return defect_placement_reference.basis_from_normal(sys.modules[__name__], normal)


def _add_direction_arrow(point, direction, layer_name, length, head_length_ratio=0.25, head_width_ratio=0.35):
    return defect_placement_reference.add_direction_arrow(
        sys.modules[__name__],
        point,
        direction,
        layer_name,
        length,
        head_length_ratio=head_length_ratio,
        head_width_ratio=head_width_ratio,
    )


def _resolve_modeling_normal(defect_type, candidate):
    return defect_placement_reference.resolve_modeling_normal(
        sys.modules[__name__],
        defect_type,
        candidate,
    )


def _attach_normal_debug(record, defect_type, candidate, debug_cfg):
    return defect_placement_reference.attach_normal_debug(
        sys.modules[__name__],
        record,
        defect_type,
        candidate,
        debug_cfg,
    )


def _record_common(defect_type, candidate, transform, shape):
    outward_normal = _candidate_outward_normal(candidate)
    return {
        "type": defect_type,
        "point": list(candidate["point"]),
        "normal": list(outward_normal),
        "surface_id": str(candidate["surface_id"]),
        "surface_layer": candidate.get("surface_layer"),
        "reference_size": float(candidate["reference_size"]),
        "boundary_dist": float(candidate["boundary_dist"]),
        "angle_deg": float(transform["angle_deg"]),
        "normal_offset": float(transform["normal_offset"]),
        "shape_source_file": shape.get("source_file"),
        "shape_source_index": int(shape.get("source_index", 0)),
        "severity": shape.get("severity"),
        "instance_id": shape.get("instance_id"),
        "instance_mask_path": shape.get("instance_mask_path"),
        "condition_state": shape.get("condition_state"),
        "metric_px": shape.get("metric_px"),
        "target_metric_cm": shape.get("target_metric_cm"),
        "metric_scale": shape.get("metric_scale"),
    }


def _normalize_condition_state(value, default="CS1"):
    text = str(value or "").strip().upper()
    if text in ("1", "CS1"):
        return "CS1"
    if text in ("2", "CS2"):
        return "CS2"
    if text in ("3", "CS3"):
        return "CS3"
    return str(default).upper()


def _geometry_layer_for_condition(layer_map, defect_key, condition_state, part=None):
    state = _normalize_condition_state(condition_state).lower()
    key = "{}_{}".format(defect_key, state)
    geometry_layers = (layer_map or {}).get("geometry") or {}
    part_key = str(part or "").strip().lower()
    if part_key:
        part_layer = geometry_layers.get("{}_{}".format(key, part_key))
        if part_layer:
            return str(part_layer)
    layer_name = geometry_layers.get(key)
    if not layer_name:
        raise ValueError("Missing defect geometry layer mapping: layers.geometry.{}".format(key))
    return str(layer_name)


def _resolve_spalling_cfg(cfg):
    return dict(cfg.get("spalling") or {})


def get_active_defect_requests(cfg):
    """Return enabled defect requests as (defect_type, defect_cfg, count)."""
    if not isinstance(cfg, dict) or not cfg:
        return []
    if not bool(cfg.get("enabled", True)):
        return []

    requests = []
    for defect_type in ("crack", "efflore"):
        defect_cfg = cfg.get(defect_type)
        if not isinstance(defect_cfg, dict):
            continue
        count = max(0, _to_int(defect_cfg.get("count"), 0))
        if bool(defect_cfg.get("enabled", True)) and count > 0:
            requests.append((defect_type, defect_cfg, count))

    if "spalling" in cfg:
        spalling_cfg = _resolve_spalling_cfg(cfg)
        count = max(0, _to_int(spalling_cfg.get("count"), 0))
        if bool(spalling_cfg.get("enabled", True)) and count > 0:
            requests.append(("spalling", spalling_cfg, count))
    return requests


def _resolve_layer_map(cfg, debug_cfg=None):
    layers_cfg = cfg.get("layers") or {}
    seed_debug_cfg = (debug_cfg or {}).get("defect_seeds") or {}
    default_seed_layer = str(seed_debug_cfg.get("layer") or "debug::seed")
    seed_by_type = bool(seed_debug_cfg.get("by_type", True))
    if seed_by_type:
        seed_layers = {
            "default": default_seed_layer,
            "crack": "{}::crack".format(default_seed_layer),
            "efflore": "{}::efflore".format(default_seed_layer),
            "spalling": "{}::spalling".format(default_seed_layer),
        }
    else:
        seed_layers = {
            "default": default_seed_layer,
            "crack": default_seed_layer,
            "efflore": default_seed_layer,
            "spalling": default_seed_layer,
        }
    layer_map = {
        "seeds": seed_layers,
        "geometry": dict((layers_cfg.get("geometry") or {})),
    }

    if bool(seed_debug_cfg.get("enabled", True)):
        for layer_name in layer_map["seeds"].values():
            ensure_layer(layer_name)
    for layer_name in layer_map["geometry"].values():
        ensure_layer(layer_name)
    return layer_map


def _reference_points_debug_config(debug_cfg=None):
    return defect_placement_reference.reference_points_debug_config(sys.modules[__name__], debug_cfg)

def _seed_layer_for_type(layer_map, defect_type):
    return defect_placement_reference.seed_layer_for_type(sys.modules[__name__], layer_map, defect_type)


def _draw_reference_points_debug(cfg, model_result=None, debug_cfg=None):
    return defect_placement_reference.draw_reference_points_debug(
        sys.modules[__name__],
        cfg,
        model_result=model_result,
        debug_cfg=debug_cfg,
    )


def _build_instance_records_for_type(
    defect_type,
    defect_cfg,
    count,
    cfg,
    candidates,
    shapes,
    layer_map,
    debug_cfg,
    rng,
    used_candidate_keys=None,
):
    if not shapes or count <= 0:
        return []
    if not candidates:
        print("Defect {}: no valid reference points were found.".format(defect_type))
        return []

    runtime = sys.modules[__name__]
    modeler = _DEFECT_MODELERS.get(defect_type)
    if modeler is None:
        print("Defect {}: no modeler is registered.".format(defect_type))
        return []

    random_cfg = cfg.get("random") or {}
    seed_cfg = (debug_cfg or {}).get("defect_seeds") or {}
    draw_seed_markers = bool(seed_cfg.get("enabled", True))
    seed_radius_coef = max(0.0, _to_float(seed_cfg.get("radius_coef"), 0.04375))
    seed_min_radius = max(0.0, _to_float(seed_cfg.get("min_radius"), 0.625))
    seed_axis_scale = max(0.0, _to_float(seed_cfg.get("axis_scale"), 1.6))
    max_attempts = max(1, _to_int(cfg.get("max_attempts_per_instance"), 40))
    records = []
    used_candidate_keys = used_candidate_keys if used_candidate_keys is not None else set()

    for instance_idx in range(count):
        shape = shapes[instance_idx % len(shapes)]
        placed = None
        available = [
            candidate for candidate in candidates
            if candidate.get("candidate_key") not in used_candidate_keys
        ]
        if not available:
            print("Defect {}: no unused reference points left.".format(defect_type))
            break

        rng.shuffle(available)
        for candidate in available[:max_attempts]:
            transform = _sample_transform(defect_type, defect_cfg, random_cfg, candidate, shape, rng=rng)
            if transform is None:
                continue
            placed = modeler(
                runtime,
                candidate,
                shape,
                transform,
                cfg,
                layer_map,
                rng=rng,
                defect_cfg=defect_cfg,
                debug_cfg=debug_cfg,
            )

            if placed is None:
                continue

            geometry_ids = _coerce_ids(placed.get("geometry_ids") or [])
            if not geometry_ids:
                _delete_objects(
                    (placed.get("geometry_ids") or [])
                    + (placed.get("spall_geometry_ids") or [])
                    + (placed.get("rebar_geometry_ids") or [])
                    + (placed.get("efflore_inner_geometry_ids") or [])
                    + (placed.get("efflore_outer_geometry_ids") or [])
                    + (placed.get("mask_ids") or [])
                )
                placed = None
                continue

            key = candidate.get("candidate_key")
            if key:
                used_candidate_keys.add(key)

            seed_ids = []
            if draw_seed_markers:
                seed_ids = _create_seed_marker(
                    candidate,
                    _seed_layer_for_type(layer_map, defect_type),
                    radius_coef=seed_radius_coef,
                    min_radius=seed_min_radius,
                    axis_scale=seed_axis_scale,
                )
            if seed_ids:
                placed["seed_id"] = str(seed_ids[0])
                placed["seed_ids"] = _as_strings(seed_ids)
            records.append(placed)
            break
        if placed is None:
            print("Defect {}: failed to place one instance within attempt budget.".format(defect_type))
    return records


def _json_ready_records(records):
    ready = []
    for idx, record in enumerate(records):
        item = dict(record)
        item.pop("surface_cut_polygon", None)
        item.pop("surface_cut_polygons", None)
        item["instance_index"] = idx
        ready.append(item)
    return ready


def _apply_surface_group_subtractions(records, cfg, model_result):
    records = list(records or [])
    if not records:
        return {"groups": 0, "cutters": 0, "targets": 0, "resolved_records": 0, "skipped_records": 0}

    source_ids = _collect_object_ids(model_result)
    if not source_ids:
        return {"groups": 0, "cutters": 0, "targets": 0, "resolved_records": 0, "skipped_records": 0}

    target_surfaces = get_surfaces(
        object_ids=source_ids,
        layer_names=cfg.get("target_layers"),
        convert_polylines=False,
        explode_polysurfaces=False,
        keep_input=True,
    )
    target_by_key = {}
    by_layer_surfaces = {}
    for sid in target_surfaces:
        if not sid or not rs.IsObject(sid):
            continue
        target_by_key[_object_key(sid)] = sid
        lname = rs.ObjectLayer(sid) or ""
        by_layer_surfaces.setdefault(lname, []).append(sid)

    sub_cfg = cfg.get("surface_subtraction") or {}
    normal_extrude_distance = max(
        0.0,
        _to_float(
            sub_cfg.get("normal_extrude_distance", sub_cfg.get("extrude_distance", 0.0)),
            0.0,
        ),
    )

    by_target_records = {}
    skipped_records = 0
    for record in records:
        if record.get("type") not in ("crack", "spalling", "exposed_rebar"):
            continue
        polygons = _resolve_record_surface_cut_polygons(record)
        if not polygons:
            continue
        target_id = _resolve_surface_subtraction_target(record, target_by_key, by_layer_surfaces)
        if not target_id:
            skipped_records += 1
            continue
        target_key = _object_key(target_id)
        group = by_target_records.get(target_key)
        if group is None:
            group = {
                "target_id": target_id,
                "layer_name": str(rs.ObjectLayer(target_id) or record.get("surface_layer") or ""),
                "records": [],
            }
            by_target_records[target_key] = group
        group["records"].append(record)

    groups = 0
    cutters = 0
    targets = 0
    resolved_records = 0
    for group in by_target_records.values():
        current_target_ids = [group.get("target_id")] if rs.IsObject(group.get("target_id")) else []
        if not current_target_ids:
            continue
        layer_name = str(group.get("layer_name") or "")
        layer_records = sorted(
            list(group.get("records") or []),
            key=_surface_subtraction_record_priority,
        )
        resolved_records += len(layer_records)

        group_cutters = 0
        applied_any = False
        for record in layer_records:
            polygons = _resolve_record_surface_cut_polygons(record)
            if not polygons:
                continue

            discard_points = _surface_subtraction_discard_points(record, polygons)
            cutter_ids = []
            helper_ids = []
            try:
                if not current_target_ids:
                    break

                normal_vec = _unit(record.get("normal") or (0.0, 0.0, 1.0), fallback=(0.0, 0.0, 1.0))
                offset_span = abs(_to_float(record.get("normal_offset"), 0.0))
                extrude_distance = max(normal_extrude_distance, normal_extrude_distance + offset_span)
                for polygon in polygons:
                    curve_id = _add_polyline(polygon)
                    if not curve_id:
                        continue
                    helper_ids.append(curve_id)
                    if normal_extrude_distance > 0.0:
                        anchor = _vec3(polygon[0])
                        start_pt = _add(anchor, _scale(normal_vec, -extrude_distance))
                        end_pt = _add(anchor, _scale(normal_vec, extrude_distance))
                        extruded = rs.ExtrudeCurveStraight(curve_id, start_pt, end_pt)
                        if extruded and rs.IsObject(extruded):
                            cutter_obj = extruded
                            try:
                                capped = rs.CapPlanarHoles(extruded)
                            except Exception:
                                capped = None
                            if capped and not isinstance(capped, bool) and rs.IsObject(capped):
                                cutter_obj = capped
                                if extruded != capped and rs.IsObject(extruded):
                                    rs.DeleteObject(extruded)
                            cutter_ids.append(cutter_obj)
                        else:
                            cutter_ids.append(curve_id)
                    else:
                        cutter_ids.append(curve_id)
                if not cutter_ids:
                    continue

                try:
                    split_ids = subtract_surface(
                        cutter_ids,
                        target_surfaces=current_target_ids,
                        delete_inputs=True,
                        discard_points=discard_points,
                    ) or []
                except TypeError as exc:
                    if "discard_points" not in str(exc):
                        raise
                    print("Surface subtraction: cached old subtract_surface() detected; retrying without discard_points. Restart Rhino to load the latest spalling cut logic.")
                    split_ids = subtract_surface(
                        cutter_ids,
                        target_surfaces=current_target_ids,
                        delete_inputs=True,
                    ) or []

                split_ids = [sid for sid in _coerce_ids(split_ids) if sid and rs.IsObject(sid)]
                if split_ids:
                    current_target_ids = _dedupe_ids(split_ids)
                    _assign_layer(current_target_ids, layer_name)
                    applied_any = True
                group_cutters += len(cutter_ids)
            finally:
                _delete_objects(helper_ids)
                _delete_objects(cutter_ids)

        if applied_any:
            groups += 1
            cutters += group_cutters
            targets += 1

    return {
        "groups": groups,
        "cutters": cutters,
        "targets": targets,
        "resolved_records": resolved_records,
        "skipped_records": skipped_records,
    }


def _extract_camera_defects(records):
    defects = []
    for record in records:
        point = record.get("point")
        normal = record.get("normal")
        if not point or not normal:
            continue
        defects.append(
            {
                "point": [float(point[0]), float(point[1]), float(point[2])],
                "normal": [float(normal[0]), float(normal[1]), float(normal[2])],
                "defect_type": record.get("type"),
                "instance_index": record.get("instance_index"),
            }
        )
    return defects


def defects_from_record_payload(payload, include_defect_types=None):
    """Convert defect records into camera defect seeds."""
    if not payload:
        return []
    if not isinstance(payload, dict):
        raise ValueError("Defect record payload must be a mapping/dict.")

    defects = payload.get("camera_defects")
    if defects is None:
        records = payload.get("records") or []
        if not isinstance(records, (list, tuple)):
            raise ValueError("Defect record payload field 'records' must be a list.")
        defects = _extract_camera_defects(records)
    elif not isinstance(defects, (list, tuple)):
        raise ValueError("Defect record payload field 'camera_defects' must be a list.")

    include = include_defect_types
    if not include:
        return list(defects)
    if isinstance(include, str):
        include = [include]
    allowed = {str(name) for name in include}
    filtered = []
    for item in defects:
        if not isinstance(item, dict):
            continue
        dtype = item.get("defect_type")
        if dtype in allowed:
            filtered.append(item)
    return filtered


def apply_defect_pipeline(params=None, model_result=None, debug_cfg=None):
    """Place crack/efflore/spalling defects on model surfaces."""
    cfg = params if isinstance(params, dict) else {}
    debug_cfg = debug_cfg if isinstance(debug_cfg, dict) else {}
    if not bool(cfg.get("enabled", True)):
        return {
            "enabled": False,
            "records": [],
            "camera_defects": [],
            "summary": {"total": 0},
            "layer_map": _resolve_layer_map(cfg, debug_cfg=debug_cfg),
        }

    active_requests = get_active_defect_requests(cfg)
    layer_map = _resolve_layer_map(cfg, debug_cfg=debug_cfg)
    if not active_requests:
        return {
            "enabled": True,
            "records": [],
            "camera_defects": [],
            "summary": {"total": 0},
            "layer_map": layer_map,
        }

    seed = cfg.get("seed")
    if seed is not None:
        rng = random.Random(_to_int(seed, 0))
    else:
        rng = random.Random()

    records = []
    used_candidate_keys = set()
    reference_marker_count = _draw_reference_points_debug(
        cfg,
        model_result=model_result,
        debug_cfg=debug_cfg,
    )
    if reference_marker_count:
        print("Defect reference_points: drew {} sampled reference markers.".format(reference_marker_count))
    for defect_type, defect_cfg, count in active_requests:
        shapes = _resolve_shapes_for_type(
            defect_type=defect_type,
            cfg=cfg,
            defect_cfg=defect_cfg,
            count=count,
            rng=rng,
        )
        if not shapes:
            print("Defect {}: skipped because no shape templates were resolved.".format(defect_type))
            continue
        candidates = _collect_reference_candidates(
            cfg,
            model_result=model_result,
            defect_type=defect_type,
            defect_cfg=defect_cfg,
            layer_map=layer_map,
            debug_cfg=debug_cfg,
        )
        if not candidates:
            print("Defect {}: skipped because no valid placement candidates were found.".format(defect_type))
            continue
        records.extend(
            _build_instance_records_for_type(
                defect_type,
                defect_cfg,
                count,
                cfg,
                candidates,
                shapes,
                layer_map,
                debug_cfg,
                rng=rng,
                used_candidate_keys=used_candidate_keys,
            )
        )

    subtraction = _apply_surface_group_subtractions(records, cfg, model_result)

    json_ready = _json_ready_records(records)
    camera_defects = _extract_camera_defects(json_ready)
    summary = {
        "total": len(json_ready),
        "crack": sum(1 for item in json_ready if item.get("type") == "crack"),
        "efflore": sum(1 for item in json_ready if item.get("type") == "efflore"),
        "spalling": sum(1 for item in json_ready if item.get("type") == "spalling"),
        "exposed_rebar": sum(1 for item in json_ready if item.get("type") == "exposed_rebar"),
    }

    payload = {
        "records": json_ready,
        "camera_defects": camera_defects,
        "summary": summary,
        "layer_map": layer_map,
        "surface_subtraction": subtraction,
    }

    return {
        "enabled": True,
        "records": json_ready,
        "camera_defects": camera_defects,
        "summary": summary,
        "layer_map": layer_map,
        "surface_subtraction": subtraction,
    }
