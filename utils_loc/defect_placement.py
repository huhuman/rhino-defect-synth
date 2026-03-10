"""Unified defect placement interfaces for crack/efflore/spalling/exposed-rebar."""

import copy
import csv
import json
import math
import os
import random

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc.defect_shapes import load_shape_templates
from utils_loc.defect_modeling import get_reference_points, get_surfaces, subtract_surface


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


def _shape_radius_from_polygons(polygons):
    radius = 0.0
    for poly in polygons or []:
        for x, y in poly or []:
            radius = max(radius, math.sqrt(float(x) * float(x) + float(y) * float(y)))
    return radius


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


def _load_polygon_payload(polygon_path):
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
    width_px = _to_float(data.get("width_px"), 0.0)
    height_px = _to_float(data.get("height_px"), 0.0)
    if width_px <= 0.0:
        width_px = _to_float(data.get("bbox_w"), 0.0)
    if height_px <= 0.0:
        height_px = _to_float(data.get("bbox_h"), 0.0)
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


def _resolve_reference_metric_px(defect_type, row, polygon_payload):
    row = row or {}
    polygon_payload = polygon_payload or {}

    if defect_type == "crack":
        ref = _to_optional_float(row.get("width_px"))
        if ref and ref > 0.0:
            return ref
    elif defect_type == "spalling":
        ref = _to_optional_float(row.get("diameter_px"))
        if ref and ref > 0.0:
            return ref
    elif defect_type == "efflore":
        bw = _to_optional_float(row.get("bbox_w"))
        bh = _to_optional_float(row.get("bbox_h"))
        if bw is not None or bh is not None:
            return max(float(bw or 0.0), float(bh or 0.0), 1.0)
        ref = _to_optional_float(row.get("diameter_px"))
        if ref and ref > 0.0:
            return ref

    width_px = _to_float(polygon_payload.get("width_px"), 1.0)
    height_px = _to_float(polygon_payload.get("height_px"), 1.0)
    return max(width_px, height_px, 1.0)


def _resolve_target_metric_cm(defect_type, defect_cfg):
    defect_cfg = defect_cfg or {}
    if defect_type == "crack":
        default = 0.1
        keys = ("target_width_cm", "target_metric_cm", "target_size_cm")
    elif defect_type == "spalling":
        default = 15.0
        keys = ("target_diameter_cm", "target_metric_cm", "target_size_cm")
    else:
        default = 12.0
        keys = ("target_span_cm", "target_metric_cm", "target_size_cm")
    for key in keys:
        value = _to_optional_float(defect_cfg.get(key))
        if value is not None and value > 0.0:
            return value
    return default


def _build_shape_from_overview_row(defect_type, row, defect_cfg):
    polygon_path = _resolve_polygon_path_from_row(row)
    payload = _load_polygon_payload(polygon_path)
    if payload is None:
        print("Defect {}: polygon json not found/invalid for row '{}': {}".format(
            defect_type,
            row.get("instance_id"),
            polygon_path,
        ))
        return None

    metric_px = _resolve_reference_metric_px(defect_type, row, payload)
    if metric_px <= 0.0:
        metric_px = 1.0
    target_metric_cm = _resolve_target_metric_cm(defect_type, defect_cfg)
    metric_scale = target_metric_cm / metric_px

    centered_polygons = []
    for poly in payload["polygons"]:
        centered = _center_polygon(poly, payload["width_px"], payload["height_px"])
        scaled = _scale_polygon(centered, metric_scale)
        if len(scaled) >= 3:
            centered_polygons.append(scaled)
    if not centered_polygons:
        return None

    centered_polygons.sort(key=lambda pts: abs(_polygon_area(pts)), reverse=True)
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
    }

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
        inner_poly = list(secondary) if secondary else list(primary)
        outer_poly = list(primary) if secondary else None
        shape.update(
            {
                "offset_poly": list(inner_poly),
                "base_poly": list(inner_poly),
                "efflore_inner_poly": inner_poly,
                "efflore_outer_poly": outer_poly,
            }
        )

    shape["shape_radius"] = _shape_radius_from_polygons(centered_polygons)
    return shape


def _load_shapes_from_overview_csv(defect_type, cfg, defect_cfg, count, rng):
    overview_cfg = _deep_merge(cfg.get("overview") or {}, defect_cfg.get("overview") or {})
    csv_path = (
        overview_cfg.get("csv_path")
        or defect_cfg.get("overview_csv_path")
    )
    if not csv_path:
        return []

    rows = _load_overview_rows(csv_path)
    if not rows:
        print("Defect {}: overview CSV has no rows: '{}'".format(defect_type, csv_path))
        return []

    requested = _to_int(overview_cfg.get("sample_count"), count)
    requested = max(0, requested)
    sampled_rows = _sample_rows(rows, requested, rng=rng)
    shapes = []
    for row in sampled_rows:
        shape = _build_shape_from_overview_row(defect_type, row, defect_cfg)
        if shape is None:
            continue
        shapes.append(shape)

    if len(shapes) < requested:
        print(
            "Defect {}: only {} of {} overview rows were usable.".format(
                defect_type,
                len(shapes),
                requested,
            )
        )
    return shapes


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


def _duplicate_surface_borders(surface_id):
    borders = rs.DuplicateSurfaceBorder(surface_id) or []
    return _coerce_ids(borders)


def _distance_to_boundary(point, border_curves):
    if not border_curves:
        return float("inf")
    best = float("inf")
    for curve_id in border_curves:
        if not rs.IsObject(curve_id):
            continue
        param = rs.CurveClosestPoint(curve_id, point)
        if param is None:
            continue
        closest = rs.EvaluateCurve(curve_id, param)
        if closest is None:
            continue
        dist = _distance(point, _vec3(closest))
        if dist < best:
            best = dist
    return best


def _surface_axes(surface_id, point, normal):
    frame = None
    try:
        if surface_id and rs.IsObject(surface_id):
            uv = rs.SurfaceClosestPoint(surface_id, point)
            frame = rs.SurfaceFrame(surface_id, uv) if uv else None
    except Exception:
        frame = None

    if frame and hasattr(frame, "XAxis") and hasattr(frame, "YAxis"):
        u_axis = _unit(frame.XAxis, fallback=(1.0, 0.0, 0.0))
        v_axis = _unit(frame.YAxis, fallback=(0.0, 1.0, 0.0))
    else:
        n = _unit(normal or (0.0, 0.0, 1.0), fallback=(0.0, 0.0, 1.0))
        ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
        u_axis = _unit(_cross(ref, n), fallback=(1.0, 0.0, 0.0))
        v_axis = _unit(_cross(n, u_axis), fallback=(0.0, 1.0, 0.0))
    n_axis = _unit(normal or (0.0, 0.0, 1.0), fallback=_cross(u_axis, v_axis))
    return u_axis, v_axis, n_axis


def _surface_normal_at_point(surface_id, point, fallback=(0.0, 0.0, 1.0)):
    try:
        if surface_id and rs.IsObject(surface_id):
            uv = rs.SurfaceClosestPoint(surface_id, point)
            if uv:
                normal = rs.SurfaceNormal(surface_id, uv)
                if normal:
                    return _unit(normal, fallback=fallback)
    except Exception:
        pass
    return _unit(fallback, fallback=(0.0, 0.0, 1.0))


def _surface_sample_point(surface_id):
    if not surface_id or not rs.IsObject(surface_id) or not rs.IsSurface(surface_id):
        return None
    try:
        centroid_data = rs.SurfaceAreaCentroid(surface_id)
        centroid = centroid_data[0] if isinstance(centroid_data, (list, tuple)) else centroid_data
        point = _try_vec3(centroid)
        if point is not None:
            return point
    except Exception:
        pass

    try:
        domain_u = rs.SurfaceDomain(surface_id, 0)
        domain_v = rs.SurfaceDomain(surface_id, 1)
        if not domain_u or not domain_v:
            return None
        u = 0.5 * (float(domain_u[0]) + float(domain_u[1]))
        v = 0.5 * (float(domain_v[0]) + float(domain_v[1]))
        point = rs.EvaluateSurface(surface_id, u, v)
        return _try_vec3(point)
    except Exception:
        return None


def _flip_surface_if_conflicts_with_normal(surface_id, desired_normal, min_opposite_dot=0.25):
    if not surface_id or not rs.IsObject(surface_id) or not rs.IsSurface(surface_id):
        return False
    sample_point = _surface_sample_point(surface_id)
    if sample_point is None:
        return False
    uv = rs.SurfaceClosestPoint(surface_id, sample_point)
    if uv is None:
        return False
    face_normal = rs.SurfaceNormal(surface_id, uv)
    face_normal = _unit(face_normal, fallback=desired_normal)
    target = _unit(desired_normal, fallback=(0.0, 0.0, 1.0))
    # Only flip when the face is clearly opposite to the desired direction.
    if _dot(face_normal, target) >= -abs(float(min_opposite_dot)):
        return False
    try:
        rs.FlipSurface(surface_id)
        return True
    except Exception:
        return False


def _orient_surfaces_to_normal(object_ids, desired_normal, min_opposite_dot=0.25):
    flipped = 0
    for obj_id in _coerce_ids(object_ids):
        if _flip_surface_if_conflicts_with_normal(
            obj_id,
            desired_normal,
            min_opposite_dot=min_opposite_dot,
        ):
            flipped += 1
    return flipped


def _candidate_axes(candidate):
    u_axis = _try_vec3((candidate or {}).get("u_axis"))
    v_axis = _try_vec3((candidate or {}).get("v_axis"))
    n_axis = _try_vec3((candidate or {}).get("n_axis"))
    if u_axis is not None and v_axis is not None:
        u_axis = _unit(u_axis, fallback=(1.0, 0.0, 0.0))
        v_axis = _unit(v_axis, fallback=(0.0, 1.0, 0.0))
        n_fallback = n_axis if n_axis is not None else _cross(u_axis, v_axis)
        n_axis = _unit(n_fallback, fallback=_cross(u_axis, v_axis))
        return u_axis, v_axis, n_axis

    return _surface_axes(
        (candidate or {}).get("surface_id"),
        (candidate or {}).get("point"),
        (candidate or {}).get("normal"),
    )


def _candidate_outward_normal(candidate):
    c = candidate or {}
    cached = _try_vec3(c.get("normal"))
    if cached is not None:
        return _unit(cached, fallback=(0.0, 0.0, 1.0))
    return _surface_normal_at_point(
        c.get("surface_id"),
        c.get("point"),
        fallback=c.get("normal") or (0.0, 0.0, 1.0),
    )


def _candidate_inward_normal(candidate):
    return _scale(_candidate_outward_normal(candidate), -1.0)


def _project_points_to_surface(points_2d, candidate, scale, angle_deg, normal_offset=0.0):
    normal = _candidate_outward_normal(candidate)
    origin = _add(candidate["point"], _scale(normal, normal_offset))
    u_axis, v_axis, _ = _candidate_axes(candidate)
    angle = math.radians(float(angle_deg))
    cos_v = math.cos(angle)
    sin_v = math.sin(angle)
    out = []
    for x, y in points_2d or []:
        rx = (x * cos_v - y * sin_v) * float(scale)
        ry = (x * sin_v + y * cos_v) * float(scale)
        p = _add(_add(origin, _scale(u_axis, rx)), _scale(v_axis, ry))
        out.append(p)
    return _ensure_closed(out)


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


def _collect_reference_candidates(cfg, model_result=None):
    source_ids = _collect_object_ids(model_result)
    ref_cfg = cfg.get("reference") or {}
    existing_ids_before = set(
        rs.AllObjects(select=False, include_lights=False, include_grips=False) or []
    )
    surface_ids = get_surfaces(
        object_ids=source_ids,
        layer_names=cfg.get("target_layers"),
        convert_polylines=True,
        explode_polysurfaces=True,
        keep_input=True,
    )
    temporary_surface_ids = [sid for sid in surface_ids if sid not in existing_ids_before]

    max_num_surfaces = max(0, _to_int(ref_cfg.get("max_num_surfaces"), 0))
    if max_num_surfaces > 0:
        surface_ids = surface_ids[:max_num_surfaces]

    normal_debug_cfg = ref_cfg.get("normal_debug") or {}
    draw_normal_debug = bool(normal_debug_cfg.get("enabled", False))
    normal_debug_layer = str(normal_debug_cfg.get("layer") or "defects::normal_debug")
    normal_debug_length = max(1e-3, _to_float(normal_debug_cfg.get("length"), 60.0))
    if draw_normal_debug:
        ensure_layer(normal_debug_layer)

    candidates = []
    seen_candidate_keys = set()
    su = max(1, _to_int(ref_cfg.get("sample_count_u"), 2))
    sv = max(1, _to_int(ref_cfg.get("sample_count_v"), 2))
    trim_margin = _to_float(ref_cfg.get("trim_margin"), 0.1)
    min_boundary_distance = max(0.0, _to_float(ref_cfg.get("min_boundary_distance"), 1.0))

    try:
        for surface_id in surface_ids:
            if not rs.IsObject(surface_id):
                continue
            border_curves = _duplicate_surface_borders(surface_id)
            points, sizes, normals = get_reference_points(
                surface_id,
                sample_count_u=su,
                sample_count_v=sv,
                trim_margin=trim_margin,
                return_normals=True,
            )
            surface_layer = rs.ObjectLayer(surface_id)
            for point, size, normal in zip(points, sizes, normals):
                point_3d = _vec3(point)
                if not rs.IsPointOnSurface(surface_id, point_3d):
                    continue
                normal_3d = _surface_normal_at_point(surface_id, point_3d, fallback=normal)
                u_axis, v_axis, n_axis = _surface_axes(surface_id, point_3d, normal_3d)
                boundary_dist = _distance_to_boundary(point, border_curves)
                if boundary_dist < min_boundary_distance:
                    continue
                candidate_key = (
                    str(surface_layer or ""),
                    _point_key(point_3d),
                )
                if candidate_key in seen_candidate_keys:
                    continue
                seen_candidate_keys.add(candidate_key)
                normal_line_id = None
                if draw_normal_debug:
                    end_pt = _add(point_3d, _scale(normal_3d, normal_debug_length))
                    normal_line_id = rs.AddLine(point_3d, end_pt)
                    if normal_line_id:
                        _assign_layer([normal_line_id], normal_debug_layer)
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
                        "normal_debug_id": str(normal_line_id) if normal_line_id else None,
                    }
                )
            for curve_id in border_curves:
                if rs.IsObject(curve_id):
                    rs.DeleteObject(curve_id)
    finally:
        for sid in temporary_surface_ids:
            if sid and rs.IsObject(sid):
                rs.DeleteObject(sid)

    return candidates


def _resolve_random_value(defect_cfg, random_cfg, key, default):
    value = defect_cfg.get(key)
    if value is None:
        value = random_cfg.get(key, default)
    return value


def _sample_transform(defect_cfg, random_cfg, candidate, shape, rng):
    scale_min = _to_float(_resolve_random_value(defect_cfg, random_cfg, "scale_min", 0.6), 0.6)
    scale_max = _to_float(_resolve_random_value(defect_cfg, random_cfg, "scale_max", 1.4), 1.4)
    if scale_min > scale_max:
        scale_min, scale_max = scale_max, scale_min

    angle_min = _to_float(_resolve_random_value(defect_cfg, random_cfg, "orientation_min_deg", 0.0), 0.0)
    angle_max = _to_float(_resolve_random_value(defect_cfg, random_cfg, "orientation_max_deg", 360.0), 360.0)
    if angle_min > angle_max:
        angle_min, angle_max = angle_max, angle_min

    boundary_margin = _to_float(_resolve_random_value(defect_cfg, random_cfg, "boundary_margin", 0.9), 0.9)
    boundary_margin = max(0.0, min(1.0, boundary_margin))
    shape_radius = _to_float(shape.get("shape_radius"), 0.0)

    if shape_radius > 1e-9:
        boundary_upper = float(candidate["boundary_dist"]) * boundary_margin / shape_radius
        scale_upper = min(scale_max, boundary_upper)
    else:
        scale_upper = scale_max

    if scale_upper < scale_min:
        return None

    scale = rng.uniform(scale_min, scale_upper)
    angle_deg = rng.uniform(angle_min, angle_max)
    normal_offset = _to_float(_resolve_random_value(defect_cfg, random_cfg, "normal_offset", 0.0), 0.0)

    return {
        "scale": scale,
        "angle_deg": angle_deg,
        "normal_offset": normal_offset,
    }


def _resolve_shape_library(global_cfg, defect_cfg):
    shape_cfg = _deep_merge(global_cfg.get("shape_library") or {}, defect_cfg.get("shape_library") or {})
    return load_shape_templates(
        paths=shape_cfg.get("paths") or [],
        shape_dir=shape_cfg.get("shape_dir"),
        recursive=bool(shape_cfg.get("recursive", True)),
        pattern=shape_cfg.get("pattern", "*.json"),
        file_format=shape_cfg.get("file_format", "auto"),
    )


def _resolve_shapes_for_type(defect_type, cfg, defect_cfg, count, rng):
    overview_shapes = _load_shapes_from_overview_csv(
        defect_type=defect_type,
        cfg=cfg,
        defect_cfg=defect_cfg,
        count=count,
        rng=rng,
    )
    if overview_shapes:
        if len(overview_shapes) >= count:
            return overview_shapes[:count]
        expanded = list(overview_shapes)
        while len(expanded) < count:
            expanded.append(rng.choice(overview_shapes))
        return expanded

    shapes = _resolve_shape_library(cfg, defect_cfg)
    if not shapes:
        return []
    if count <= len(shapes):
        return rng.sample(shapes, count)
    sampled = list(shapes)
    sampled.extend(rng.choice(shapes) for _ in range(count - len(shapes)))
    return sampled


def _pick_shape_points(shape):
    offset_poly = shape.get("offset_poly") or shape.get("base_poly") or []
    base_poly = shape.get("base_poly") or offset_poly
    crack_polys = shape.get("crack_polys") or ([base_poly] if base_poly else [])
    inside_polys = shape.get("inside_polys") or []
    diff_polys = shape.get("diff_polys") or []
    return offset_poly, base_poly, crack_polys, inside_polys, diff_polys


def _create_seed_marker(candidate, layer_name, radius_coef=0.04375, min_radius=0.625, axis_scale=1.6):
    ensure_layer(layer_name)
    if not rs.IsLayer(layer_name):
        return []
    point = candidate["point"]
    ref_size = max(1.0, _to_float(candidate.get("reference_size"), 1.0))
    radius = max(float(min_radius), ref_size * float(radius_coef))
    line_half = radius * float(axis_scale)

    marker_ids = []
    sphere = rs.AddSphere(point, radius)
    if sphere:
        marker_ids.append(sphere)

    u_axis, v_axis, n_axis = _candidate_axes(candidate)
    for axis in (u_axis, v_axis, n_axis):
        start = _add(point, _scale(axis, -line_half))
        end = _add(point, _scale(axis, line_half))
        line = rs.AddLine(start, end)
        if line:
            marker_ids.append(line)

    _assign_layer(marker_ids, layer_name)
    return _coerce_ids(marker_ids)


def _basis_from_normal(normal):
    n = _unit(normal, fallback=(0.0, 0.0, 1.0))
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
    x_axis = _unit(_cross(ref, n), fallback=(1.0, 0.0, 0.0))
    y_axis = _unit(_cross(n, x_axis), fallback=(0.0, 1.0, 0.0))
    return x_axis, y_axis, n


def _add_direction_arrow(point, direction, layer_name, length, head_length_ratio=0.25, head_width_ratio=0.35):
    if not layer_name:
        return []
    ensure_layer(layer_name)
    if not rs.IsLayer(layer_name):
        return []

    origin = _try_vec3(point)
    if origin is None:
        return []
    dir_vec = _unit(direction, fallback=(0.0, 0.0, 1.0))
    if _norm(dir_vec) <= 1e-12:
        return []

    length = max(1e-3, float(length))
    head_len = min(length * max(1e-3, float(head_length_ratio)), length * 0.9)
    head_width = max(1e-3, head_len * max(1e-3, float(head_width_ratio)))
    x_axis, y_axis, _ = _basis_from_normal(dir_vec)

    shaft_end = _add(origin, _scale(dir_vec, length - head_len))
    tip = _add(origin, _scale(dir_vec, length))
    arrow_ids = []

    shaft_id = rs.AddLine(origin, shaft_end)
    if shaft_id:
        arrow_ids.append(shaft_id)

    for axis in (x_axis, _scale(x_axis, -1.0), y_axis, _scale(y_axis, -1.0)):
        wing_end = _add(shaft_end, _scale(axis, head_width))
        wing_id = rs.AddLine(tip, wing_end)
        if wing_id:
            arrow_ids.append(wing_id)

    _assign_layer(arrow_ids, layer_name)
    return _coerce_ids(arrow_ids)


def _resolve_modeling_normal(defect_type, candidate):
    outward = _candidate_outward_normal(candidate)
    if defect_type in ("crack", "spalling", "exposed_rebar"):
        return _scale(outward, -1.0), "inward"
    return outward, "outward"


def _attach_normal_debug(record, defect_type, candidate, debug_cfg):
    record = record if isinstance(record, dict) else {}
    normal_vec, normal_role = _resolve_modeling_normal(defect_type, candidate)
    record["modeling_normal"] = [float(normal_vec[0]), float(normal_vec[1]), float(normal_vec[2])]
    record["modeling_normal_role"] = str(normal_role)

    cfg = (debug_cfg or {}).get("defect_normals") or {}
    if not bool(cfg.get("enabled", False)):
        return

    layer_name = str(cfg.get("layer") or "debug::normal")
    if bool(cfg.get("by_type", True)):
        layer_name = "{}::{}".format(layer_name, str(defect_type))

    debug_ids = _add_direction_arrow(
        record.get("point") or (candidate or {}).get("point"),
        normal_vec,
        layer_name=layer_name,
        length=_to_float(cfg.get("length"), 40.0),
        head_length_ratio=_to_float(cfg.get("head_length_ratio"), 0.25),
        head_width_ratio=_to_float(cfg.get("head_width_ratio"), 0.35),
    )
    if debug_ids:
        record["normal_debug_ids"] = _as_strings(debug_ids)


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
        "scale": float(transform["scale"]),
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


def _mask_layer_for_state(layer_map, defect_key, condition_state):
    key = "{}_{}".format(defect_key, _normalize_condition_state(condition_state).lower())
    return (layer_map.get("mask") or {}).get(key) or (layer_map.get("mask") or {}).get(defect_key)


def _resolve_crack_width_metrics(shape, transform):
    shape = shape or {}
    transform = transform or {}

    width_px = _to_optional_float(shape.get("metric_px"))
    if width_px is None or width_px <= 0.0:
        width_px = _to_optional_float(shape.get("width_px"))
    if width_px is None or width_px <= 0.0:
        return None

    base_px_to_cm = _to_optional_float(shape.get("metric_scale"))
    if base_px_to_cm is None or base_px_to_cm <= 0.0:
        return None

    placement_scale = _to_optional_float(transform.get("scale"), default=1.0)
    if placement_scale is None or placement_scale <= 0.0:
        placement_scale = 1.0

    px_to_cm = float(base_px_to_cm) * float(placement_scale)
    width_cm = float(width_px) * float(px_to_cm)
    return {
        "width_px": float(width_px),
        "px_to_cm": float(px_to_cm),
        "width_cm": float(width_cm),
    }


def _resolve_crack_width_thresholds(crack_cfg):
    crack_cfg = crack_cfg or {}
    t1 = _to_optional_float(crack_cfg.get("t1"))
    t2 = _to_optional_float(crack_cfg.get("t2"))
    if t1 is None:
        t1 = _to_optional_float(crack_cfg.get("cs2_width_cm_threshold"))
    if t2 is None:
        t2 = _to_optional_float(crack_cfg.get("cs3_width_cm_threshold"))
    if t1 is None:
        t1 = 0.1
    if t2 is None:
        t2 = 0.2
    t1 = max(0.0, float(t1))
    t2 = max(0.0, float(t2))
    if t1 > t2:
        t1, t2 = t2, t1
    return t1, t2


def _resolve_crack_condition_state(crack_cfg, crack_created, crack_width_metrics=None):
    crack_cfg = crack_cfg or {}
    width_cm = _to_optional_float((crack_width_metrics or {}).get("width_cm"))
    if width_cm is not None:
        t1, t2 = _resolve_crack_width_thresholds(crack_cfg)
        if width_cm < t1:
            return "CS1"
        if width_cm < t2:
            return "CS2"
        return "CS3"

    # Backward-compatible fallback when width-based inputs are unavailable.
    d1 = _to_optional_float((crack_created or {}).get("d1"), default=0.0) or 0.0
    cs2_threshold = _to_float(crack_cfg.get("cs2_d1_threshold"), 1.0)
    cs3_threshold = _to_float(crack_cfg.get("cs3_d1_threshold"), 2.0)
    if cs2_threshold > cs3_threshold:
        cs2_threshold, cs3_threshold = cs3_threshold, cs2_threshold
    if d1 >= cs3_threshold:
        return "CS3"
    if d1 >= cs2_threshold:
        return "CS2"
    return "CS1"


def _resolve_efflore_condition_state(has_expand_polygon):
    # Efflore CS level follows expand-polygon usage:
    # - with expanded polygon: CS3
    # - without expanded polygon: CS2
    return "CS3" if bool(has_expand_polygon) else "CS2"


def _model_crack_instance(candidate, shape, transform, cfg, layer_map, rng, debug_cfg=None):
    offset_2d, base_2d, crack_2d, inside_2d, diff_2d = _pick_shape_points(shape)
    offset_3d = _project_points_to_surface(offset_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    base_3d = _project_points_to_surface(base_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    surface_cut_polygon = _project_points_to_surface(
        offset_2d,
        candidate,
        transform["scale"],
        transform["angle_deg"],
        normal_offset=0.0,
    )

    crack_polys = []
    for points in crack_2d:
        poly = _project_points_to_surface(points, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
        curve_id = _add_polyline(poly)
        if curve_id:
            crack_polys.append(curve_id)

    inside_polys = []
    for points in inside_2d:
        poly = _project_points_to_surface(points, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
        curve_id = _add_polyline(poly)
        if curve_id:
            inside_polys.append(curve_id)

    diff_polys = []
    for points in diff_2d:
        poly = _project_points_to_surface(points, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
        curve_id = _add_polyline(poly)
        if curve_id:
            diff_polys.append(curve_id)

    offset_curve = _add_polyline(offset_3d)
    base_curve = _add_polyline(base_3d)
    if not offset_curve or not base_curve or not crack_polys:
        for obj_id in _coerce_ids(crack_polys + inside_polys + diff_polys + [offset_curve, base_curve]):
            if rs.IsObject(obj_id):
                rs.DeleteObject(obj_id)
        return None

    mask_ids = []
    crack_created = create_crack(
        crack_polys,
        inside_polys,
        base_curve,
        offset_curve,
        diff_polys,
        inward_dir=_candidate_inward_normal(candidate),
        d1_range=tuple(cfg["crack"].get("d1_range", (0.5, 2.5))),
        delta_depth_range=tuple(cfg["crack"].get("delta_depth_range", (10.0, 30.0))),
        layer_crack_extrusion=layer_map["geometry"]["crack"],
        layer_parent_surface=candidate.get("surface_layer"),
        cleanup_inputs=True,
        rng=rng,
    ) or {}
    outward_normal = _candidate_outward_normal(candidate)
    _orient_surfaces_to_normal(crack_created.get("extrusions") or [], outward_normal)
    _orient_surfaces_to_normal(crack_created.get("bottom_caps") or [], outward_normal)
    _orient_surfaces_to_normal(crack_created.get("parent_fills") or [], outward_normal)
    crack_geometry = _coerce_ids(
        (crack_created.get("loft") or [])
        + (crack_created.get("extrusions") or [])
        + (crack_created.get("bottom_caps") or [])
    )
    parent_fills = _coerce_ids(crack_created.get("parent_fills") or [])
    if not crack_geometry:
        _delete_objects(
            crack_polys
            + inside_polys
            + diff_polys
            + [offset_curve, base_curve]
            + mask_ids
            + parent_fills
        )
        return None

    crack_cfg = cfg.get("crack") or {}
    crack_width_metrics = _resolve_crack_width_metrics(shape, transform)
    cs_level = _resolve_crack_condition_state(crack_cfg, crack_created, crack_width_metrics=crack_width_metrics)
    mask_layer = _mask_layer_for_state(layer_map, "crack", cs_level)
    mask_ids = _add_mask_from_polygon(offset_3d, mask_layer, as_surface=True)

    record = _record_common("crack", candidate, transform, shape)
    record["condition_state"] = cs_level
    record["geometry_ids"] = _as_strings(crack_geometry)
    record["mask_ids"] = _as_strings(mask_ids)
    record["surface_cut_polygon"] = [list(_vec3(pt)) for pt in _ensure_closed(surface_cut_polygon)]
    record["crack_metrics"] = {
        "d1": crack_created.get("d1"),
        "d2": crack_created.get("d2"),
    }
    if crack_width_metrics:
        t1, t2 = _resolve_crack_width_thresholds(crack_cfg)
        record["crack_metrics"].update(crack_width_metrics)
        record["crack_metrics"]["severity_t1_cm"] = float(t1)
        record["crack_metrics"]["severity_t2_cm"] = float(t2)
    _attach_normal_debug(record, "crack", candidate, debug_cfg)
    return record


def _model_efflore_instance(candidate, shape, transform, cfg, layer_map, rng, debug_cfg=None):
    eff_cfg = cfg.get("efflore") or {}
    outward_normal = _candidate_outward_normal(candidate)

    inner_2d = shape.get("efflore_inner_poly") or shape.get("offset_poly") or shape.get("base_poly") or []
    outer_2d = shape.get("efflore_outer_poly")
    if not outer_2d:
        polys = list(shape.get("polygons") or [])
        if len(polys) >= 2:
            outer_2d = polys[0]
            inner_2d = polys[1]
    if not inner_2d:
        return None

    def _extrude_polygon(polygon_points, thickness):
        curve_id = _add_polyline(polygon_points)
        if not curve_id:
            return []
        try:
            depth = max(1e-4, float(thickness))
            start = rs.CurveStartPoint(curve_id)
            if not start:
                return []
            start_pt = _vec3(start)
            end_pt = _add(start_pt, _scale(outward_normal, depth))
            extrusion = rs.ExtrudeCurveStraight(curve_id, start_pt, end_pt)
            geometry = _coerce_ids([extrusion])
            if not geometry:
                return []

            final_ids = []
            for gid in geometry:
                capped = None
                try:
                    capped = rs.CapPlanarHoles(gid)
                except Exception:
                    capped = None
                capped_ids = _coerce_ids([capped])
                if capped_ids:
                    final_ids.extend(capped_ids)
                    if gid not in capped_ids and rs.IsObject(gid):
                        rs.DeleteObject(gid)
                else:
                    final_ids.append(gid)
            return _coerce_ids(final_ids)
        finally:
            if rs.IsObject(curve_id):
                rs.DeleteObject(curve_id)

    inner_polygon = _project_points_to_surface(
        inner_2d,
        candidate,
        transform["scale"],
        transform["angle_deg"],
        transform["normal_offset"],
    )
    if len(inner_polygon) < 4:
        return None

    outer_polygon = []
    has_outer = bool(outer_2d and len(outer_2d) >= 3)
    if has_outer:
        outer_polygon = _project_points_to_surface(
            outer_2d,
            candidate,
            transform["scale"],
            transform["angle_deg"],
            transform["normal_offset"],
        )
        has_outer = len(outer_polygon) >= 4

    thickness = max(1e-4, _to_float(eff_cfg.get("fixed_thickness"), 0.1))
    uses_expand_polygon = bool(has_outer)
    cs_level = _resolve_efflore_condition_state(uses_expand_polygon)

    mask_layer = _mask_layer_for_state(layer_map, "efflore", cs_level)
    mask_source = outer_polygon if has_outer else inner_polygon
    mask_ids = _add_mask_from_polygon(mask_source, mask_layer, as_surface=True)

    inner_geometry = _extrude_polygon(inner_polygon, thickness)
    _orient_surfaces_to_normal(inner_geometry, outward_normal)

    inner_layer = layer_map["geometry"]["efflore"]
    _assign_layer(inner_geometry, inner_layer)

    outer_geometry = []
    if has_outer:
        outer_thickness = thickness
        outer_geometry = _extrude_polygon(outer_polygon, outer_thickness)
        _orient_surfaces_to_normal(outer_geometry, outward_normal)
        _assign_layer(outer_geometry, layer_map["geometry"]["efflore_outer"])

    geometry_ids = _coerce_ids(inner_geometry + outer_geometry)
    if not geometry_ids:
        _delete_objects(mask_ids + inner_geometry + outer_geometry)
        return None

    record = _record_common("efflore", candidate, transform, shape)
    record["condition_state"] = cs_level
    record["geometry_ids"] = _as_strings(geometry_ids)
    record["efflore_inner_geometry_ids"] = _as_strings(inner_geometry)
    record["efflore_outer_geometry_ids"] = _as_strings(outer_geometry)
    record["mask_ids"] = _as_strings(mask_ids)
    record["efflore_metrics"] = {
        "thickness": float(thickness),
        "outer_thickness": float(thickness) if has_outer else 0.0,
        "has_outer_layer": bool(outer_geometry),
        "mask_uses_outer": bool(has_outer),
        "uses_expand_polygon": bool(uses_expand_polygon),
    }
    _attach_normal_debug(record, "efflore", candidate, debug_cfg)
    return record


def _build_spall_ring_points(vertices, centroid, inward_normal, t, depth, irregularity, rng):
    radial_scale = max(0.03, 1.0 - 0.92 * float(t))
    depth_base = float(depth) * math.sin(0.5 * math.pi * float(t))
    deepest_idx = rng.randrange(len(vertices)) if t >= 0.999 and vertices else -1
    ring = []
    for idx, point in enumerate(vertices):
        vec = _sub(point, centroid)
        jitter = 1.0 + rng.uniform(-irregularity, irregularity) * (1.0 - 0.5 * t)
        local_scale = max(0.01, radial_scale * jitter)
        local_depth = depth_base
        if t > 0.0:
            local_depth = depth_base * (1.0 - irregularity * rng.uniform(0.0, 0.35))
        if idx == deepest_idx:
            local_depth = float(depth)
        ring_pt = _add(_add(centroid, _scale(vec, local_scale)), _scale(inward_normal, local_depth))
        ring.append(ring_pt)
    return _ensure_closed(ring)


def _model_spall_from_polygon(polygon, candidate, depth, layer_name, rng, irregularity=0.2):
    vertices = _unique_points(polygon)
    if len(vertices) < 3:
        return [], {}

    centroid = (
        sum(point[0] for point in vertices) / float(len(vertices)),
        sum(point[1] for point in vertices) / float(len(vertices)),
        sum(point[2] for point in vertices) / float(len(vertices)),
    )
    inward_normal = _candidate_inward_normal(candidate)
    irregularity = max(0.0, min(0.9, float(irregularity)))

    ring_points = [_ensure_closed(vertices)]
    for t in (0.35, 0.65, 1.0):
        ring_points.append(
            _build_spall_ring_points(
                vertices,
                centroid,
                inward_normal,
                t,
                depth,
                irregularity=irregularity,
                rng=rng,
            )
        )

    helper_curves = []
    for pts in ring_points:
        curve_id = _add_polyline(pts)
        if not curve_id:
            _delete_objects(helper_curves)
            return [], {}
        helper_curves.append(curve_id)

    geometry_ids = []
    try:
        for idx in range(len(helper_curves) - 1):
            loft = rs.AddLoftSrf([helper_curves[idx], helper_curves[idx + 1]]) or []
            geometry_ids.extend(_coerce_ids(loft))
        bottom_cap = rs.AddPlanarSrf(helper_curves[-1]) or []
        geometry_ids.extend(_coerce_ids(bottom_cap))
    finally:
        _delete_objects(helper_curves)

    _orient_surfaces_to_normal(geometry_ids, _candidate_outward_normal(candidate))
    _assign_layer(geometry_ids, layer_name)
    return _coerce_ids(geometry_ids), {
        "depth": float(depth),
        "ring_count": len(ring_points),
        "irregularity": irregularity,
    }


def _rebar_line_positions(start, end, spacing, rng, padding=0.5):
    start = float(start)
    end = float(end)
    if end < start:
        start, end = end, start
    length = max(0.0, end - start)
    spacing = max(1e-4, float(spacing))
    if spacing > length:
        center = 0.5 * (start + end)
        return [center * (0.8 + 0.4 * rng.random())]
    out = []
    x = start
    limit = end + float(padding)
    while x < limit:
        out.append(x)
        x += spacing
    return out or [0.5 * (start + end)]


def _make_rebar_pipe(start, end, radius):
    line_id = rs.AddLine(start, end)
    if not line_id:
        return []
    try:
        return _coerce_ids(rs.AddPipe(line_id, 0.0, float(radius), cap=2) or [])
    finally:
        if rs.IsObject(line_id):
            rs.DeleteObject(line_id)


def _model_rebar_bars(candidate, polygon, spall_depth, rebar_cfg, layer_name, rng):
    u_axis, v_axis, _n_axis = _candidate_axes(candidate)
    inward_normal = _candidate_inward_normal(candidate)
    cover_depth = max(0.0, _to_float(rebar_cfg.get("rebar_cover_depth"), 2.0))
    if float(spall_depth) <= cover_depth:
        return [], {"bar_count": 0, "skipped_reason": "spall_depth_not_enough"}

    vertices = _unique_points(polygon)
    if len(vertices) < 3:
        return [], {"bar_count": 0, "skipped_reason": "invalid_polygon"}

    local_uv = []
    for point in vertices:
        rel = _sub(point, candidate["point"])
        local_uv.append((_dot(rel, u_axis), _dot(rel, v_axis)))
    us = [uv[0] for uv in local_uv]
    vs = [uv[1] for uv in local_uv]
    left, right = min(us), max(us)
    bottom, top = min(vs), max(vs)
    span_u = max(1e-4, right - left)
    span_v = max(1e-4, top - bottom)
    length_scale = max(0.2, _to_float(rebar_cfg.get("rebar_length_scale"), 1.3))

    mid_u = 0.5 * (left + right)
    mid_v = 0.5 * (bottom + top)
    half_u = 0.5 * span_u * length_scale
    half_v = 0.5 * span_v * length_scale
    left_ext, right_ext = mid_u - half_u, mid_u + half_u
    bottom_ext, top_ext = mid_v - half_v, mid_v + half_v

    bar_count_min, bar_count_max = rebar_cfg.get("rebar_count_range", [1, 3])
    bar_count_min = max(1, _to_int(bar_count_min, 1))
    bar_count_max = max(1, _to_int(bar_count_max, 3))
    if bar_count_min > bar_count_max:
        bar_count_min, bar_count_max = bar_count_max, bar_count_min
    fallback_count = rng.randint(bar_count_min, bar_count_max)

    spacing = _to_optional_float(rebar_cfg.get("rebar_spacing"))
    if spacing is None:
        spacing = _to_optional_float(rebar_cfg.get("spacing"))
    if spacing is None:
        spacing = max(span_u, span_v) / float(max(1, fallback_count))
    spacing = max(1e-4, float(spacing))

    radius_min, radius_max = rebar_cfg.get("rebar_radius_range", [0.8, 2.5])
    radius_min = max(0.05, _to_float(radius_min, 0.8))
    radius_max = max(0.05, _to_float(radius_max, 2.5))
    if radius_min > radius_max:
        radius_min, radius_max = radius_max, radius_min
    radius = rng.uniform(radius_min, radius_max)
    diameter = 2.0 * radius

    keep_probability = max(0.0, min(1.0, _to_float(rebar_cfg.get("rebar_keep_probability"), 1.0)))
    padding = _to_float(rebar_cfg.get("rebar_extent_padding"), 0.5)
    use_dual_direction = _to_bool(rebar_cfg.get("rebar_dual_direction"), default=True)

    xs = _rebar_line_positions(left, right, spacing, rng, padding=padding)
    ys = _rebar_line_positions(bottom, top, spacing, rng, padding=padding)

    center_base = _add(candidate["point"], _scale(inward_normal, cover_depth))
    created = []
    count_u = 0
    count_v = 0

    for x in xs:
        if rng.random() > keep_probability:
            continue
        start = _add(_add(center_base, _scale(u_axis, x)), _scale(v_axis, top_ext))
        end = _add(_add(center_base, _scale(u_axis, x)), _scale(v_axis, bottom_ext))
        pipes = _make_rebar_pipe(start, end, radius)
        if pipes:
            count_v += 1
            created.extend(pipes)

    if use_dual_direction:
        cross_base = _add(candidate["point"], _scale(inward_normal, cover_depth + diameter))
        for y in ys:
            if rng.random() > keep_probability:
                continue
            start = _add(_add(cross_base, _scale(u_axis, left_ext)), _scale(v_axis, y))
            end = _add(_add(cross_base, _scale(u_axis, right_ext)), _scale(v_axis, y))
            pipes = _make_rebar_pipe(start, end, radius)
            if pipes:
                count_u += 1
                created.extend(pipes)

    _assign_layer(created, layer_name)
    return created, {
        "bar_count": int(count_u + count_v),
        "bar_count_u": int(count_u),
        "bar_count_v": int(count_v),
        "bar_length": float(max(right_ext - left_ext, top_ext - bottom_ext)),
        "bar_length_u": float(right_ext - left_ext),
        "bar_length_v": float(top_ext - bottom_ext),
        "spacing": float(spacing),
        "diameter": float(diameter),
        "cover_depth": float(cover_depth),
        "dual_direction": bool(use_dual_direction),
    }


def _resolve_spalling_cfg(cfg):
    spalling_cfg = dict(cfg.get("spalling") or {})
    legacy_cfg = dict(cfg.get("exposed_rebar") or {})

    if not spalling_cfg and legacy_cfg:
        spalling_cfg = dict(legacy_cfg)
        count = _to_int(legacy_cfg.get("count"), 0)
        spalling_cfg.setdefault("count", count)
        spalling_cfg.setdefault("rebar_enabled", True)
        if count > 0:
            spalling_cfg.setdefault("rebar_probability", 1.0)

    for key in ("spall_depth_range",):
        if key in legacy_cfg and key not in spalling_cfg:
            spalling_cfg[key] = legacy_cfg[key]
    return spalling_cfg


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

    if "spalling" in cfg or "exposed_rebar" in cfg:
        spalling_cfg = _resolve_spalling_cfg(cfg)
        count = max(0, _to_int(spalling_cfg.get("count"), 0))
        if bool(spalling_cfg.get("enabled", True)) and count > 0:
            requests.append(("spalling", spalling_cfg, count))
    return requests


def _resolve_rebar_cfg(cfg, spalling_cfg):
    rebar_cfg = _deep_merge(cfg.get("exposed_rebar") or {}, (spalling_cfg or {}).get("rebar") or {})
    for key in (
        "rebar_count_range",
        "rebar_radius_range",
        "rebar_length_scale",
        "rebar_cover_depth",
        "rebar_spacing",
        "spacing",
        "rebar_keep_probability",
        "rebar_extent_padding",
        "rebar_dual_direction",
    ):
        if key in (spalling_cfg or {}):
            rebar_cfg[key] = copy.deepcopy(spalling_cfg[key])
    return rebar_cfg


def _resolve_spalling_condition_state(spalling_cfg, diameter_cm, depth_cm):
    depth_threshold = _to_optional_float((spalling_cfg or {}).get("cs3_depth_threshold"))
    diameter_threshold = _to_optional_float((spalling_cfg or {}).get("cs3_diameter_threshold"))
    cs2_depth_threshold = _to_optional_float((spalling_cfg or {}).get("cs2_depth_threshold"))
    cs2_diameter_threshold = _to_optional_float((spalling_cfg or {}).get("cs2_diameter_threshold"))
    require_both = _to_bool((spalling_cfg or {}).get("cs3_requires_both"), default=False)

    if (
        depth_threshold is None
        and diameter_threshold is None
        and cs2_depth_threshold is None
        and cs2_diameter_threshold is None
    ):
        return "CS2"

    depth_hit = depth_threshold is not None and float(depth_cm) >= float(depth_threshold)
    diameter_hit = diameter_threshold is not None and float(diameter_cm) >= float(diameter_threshold)
    cs3 = (depth_hit and diameter_hit) if require_both else (depth_hit or diameter_hit)
    if cs3:
        return "CS3"

    depth_hit_cs2 = cs2_depth_threshold is not None and float(depth_cm) >= float(cs2_depth_threshold)
    diameter_hit_cs2 = cs2_diameter_threshold is not None and float(diameter_cm) >= float(cs2_diameter_threshold)
    cs2 = (depth_hit_cs2 and diameter_hit_cs2) if require_both else (depth_hit_cs2 or diameter_hit_cs2)
    if cs2:
        return "CS2"
    return "CS2"


def _resolve_spalling_diameter_cm(shape, transform):
    target = _to_optional_float((shape or {}).get("target_metric_cm"))
    if target is not None and target > 0.0:
        return float(target) * float(transform.get("scale", 1.0))
    radius = max(1e-6, _to_float((shape or {}).get("shape_radius"), 1.0))
    return 2.0 * radius * float(transform.get("scale", 1.0))


def _should_place_rebar(shape, spalling_cfg, condition_state, rng):
    row = (shape or {}).get("row") or {}
    for key in ("has_rebar", "exposed_rebar", "with_rebar"):
        if key in row:
            return _to_bool(row.get(key), default=False)

    if not _to_bool((spalling_cfg or {}).get("rebar_enabled"), default=True):
        return False
    if _to_bool((spalling_cfg or {}).get("rebar_only_cs3"), default=False) and condition_state != "CS3":
        return False
    if _to_bool((spalling_cfg or {}).get("force_rebar"), default=False):
        return True
    probability = _to_float((spalling_cfg or {}).get("rebar_probability"), 0.0)
    probability = max(0.0, min(1.0, probability))
    return rng.random() < probability


def _model_spalling_instance(candidate, shape, transform, cfg, layer_map, rng, spalling_cfg=None, debug_cfg=None):
    spalling_cfg = dict(spalling_cfg or _resolve_spalling_cfg(cfg))
    offset_2d = shape.get("spall_poly") or shape.get("offset_poly") or shape.get("base_poly") or []
    polygon = _project_points_to_surface(offset_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    surface_cut_polygon = _project_points_to_surface(
        offset_2d,
        candidate,
        transform["scale"],
        transform["angle_deg"],
        normal_offset=0.0,
    )
    if len(polygon) < 4:
        return None

    depth_range = spalling_cfg.get("depth_range") or spalling_cfg.get("spall_depth_range") or [5.0, 20.0]
    depth_min = max(0.1, _to_float(depth_range[0] if len(depth_range) > 0 else None, 5.0))
    depth_max = max(0.1, _to_float(depth_range[1] if len(depth_range) > 1 else None, 20.0))
    if depth_min > depth_max:
        depth_min, depth_max = depth_max, depth_min
    spall_depth = rng.uniform(depth_min, depth_max)
    diameter_cm = _resolve_spalling_diameter_cm(shape, transform)
    condition_state = _resolve_spalling_condition_state(spalling_cfg, diameter_cm=diameter_cm, depth_cm=spall_depth)

    place_rebar = _should_place_rebar(shape, spalling_cfg, condition_state, rng=rng)
    mask_ids = []
    spall_mask_layer = _mask_layer_for_state(layer_map, "spall", condition_state)
    mask_ids.extend(_add_mask_from_polygon(polygon, spall_mask_layer, as_surface=True))
    if place_rebar:
        exposed_mask_layer = _mask_layer_for_state(layer_map, "exposed_rebar", condition_state)
        mask_ids.extend(_add_mask_from_polygon(polygon, exposed_mask_layer, as_surface=False))

    spall_layer_name = layer_map["geometry"]["spall"]
    spall_ids, spall_metrics = _model_spall_from_polygon(
        polygon,
        candidate,
        spall_depth,
        spall_layer_name,
        rng=rng,
        irregularity=_to_float(spalling_cfg.get("depth_irregularity"), 0.2),
    )
    if not spall_ids:
        _delete_objects(mask_ids)
        return None

    rebar_ids = []
    rebar_metrics = {}
    if place_rebar:
        rebar_cfg = _resolve_rebar_cfg(cfg, spalling_cfg)
        rebar_ids, rebar_metrics = _model_rebar_bars(
            candidate,
            polygon,
            spall_depth=spall_depth,
            rebar_cfg=rebar_cfg,
            layer_name=(
                layer_map["geometry"].get("exposed_rebar")
                or layer_map["geometry"].get("rebar")
                or layer_map["geometry"]["spall"]
            ),
            rng=rng,
        )

    defect_type = "exposed_rebar" if rebar_ids else "spalling"
    geometry_ids = _coerce_ids(spall_ids + rebar_ids)
    if not geometry_ids:
        _delete_objects(mask_ids + spall_ids + rebar_ids)
        return None

    record = _record_common(defect_type, candidate, transform, shape)
    record["condition_state"] = condition_state
    record["geometry_ids"] = _as_strings(geometry_ids)
    record["spall_geometry_ids"] = _as_strings(spall_ids)
    record["rebar_geometry_ids"] = _as_strings(rebar_ids)
    record["mask_ids"] = _as_strings(mask_ids)
    record["surface_cut_polygon"] = [list(_vec3(pt)) for pt in _ensure_closed(surface_cut_polygon)]
    record["spall_metrics"] = dict(spall_metrics or {})
    record["spall_metrics"]["depth"] = float(spall_depth)
    record["spall_metrics"]["diameter_cm"] = float(diameter_cm)
    record["spall_metrics"]["has_rebar"] = bool(rebar_ids)
    if rebar_metrics:
        record["rebar_metrics"] = rebar_metrics
    _attach_normal_debug(record, defect_type, candidate, debug_cfg)
    return record


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
        "mask": dict((layers_cfg.get("mask") or {})),
    }

    configured_types = {name for name in ("crack", "efflore", "spalling") if name in cfg}
    if "exposed_rebar" in cfg:
        configured_types.add("spalling")

    if "crack" in configured_types:
        for key, value in {
            "crack": "defects::geometry::crack",
        }.items():
            layer_map["geometry"].setdefault(key, value)
        for key, value in {
            "crack_cs1": "defects::mask::crack::cs1",
            "crack_cs2": "defects::mask::crack::cs2",
            "crack_cs3": "defects::mask::crack::cs3",
        }.items():
            layer_map["mask"].setdefault(key, value)

    if "efflore" in configured_types:
        for key, value in {
            "efflore": "defects::geometry::efflore",
            "efflore_outer": "defects::geometry::efflore_outer",
        }.items():
            layer_map["geometry"].setdefault(key, value)
        for key, value in {
            "efflore_cs2": "defects::mask::efflore::cs2",
            "efflore_cs3": "defects::mask::efflore::cs3",
        }.items():
            layer_map["mask"].setdefault(key, value)

    if "spalling" in configured_types:
        for key, value in {
            "spall": "defects::geometry::spall",
            "exposed_rebar": "defects::geometry::exposed_rebar",
        }.items():
            layer_map["geometry"].setdefault(key, value)
        for key, value in {
            "spall_cs2": "defects::mask::spall::cs2",
            "spall_cs3": "defects::mask::spall::cs3",
            "exposed_rebar_cs2": "defects::mask::exposed_rebar::cs2",
            "exposed_rebar_cs3": "defects::mask::exposed_rebar::cs3",
        }.items():
            layer_map["mask"].setdefault(key, value)

    if bool(seed_debug_cfg.get("enabled", True)):
        for layer_name in layer_map["seeds"].values():
            ensure_layer(layer_name)
    for layer_name in layer_map["geometry"].values():
        ensure_layer(layer_name)
    for layer_name in layer_map["mask"].values():
        ensure_layer(layer_name)
    return layer_map



def _seed_layer_for_type(layer_map, defect_type):
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
            transform = _sample_transform(defect_cfg, random_cfg, candidate, shape, rng=rng)
            if transform is None:
                continue

            if defect_type == "crack":
                placed = _model_crack_instance(
                    candidate,
                    shape,
                    transform,
                    cfg,
                    layer_map,
                    rng=rng,
                    debug_cfg=debug_cfg,
                )
            elif defect_type == "efflore":
                placed = _model_efflore_instance(
                    candidate,
                    shape,
                    transform,
                    cfg,
                    layer_map,
                    rng=rng,
                    debug_cfg=debug_cfg,
                )
            else:
                placed = _model_spalling_instance(
                    candidate,
                    shape,
                    transform,
                    cfg,
                    layer_map,
                    rng=rng,
                    spalling_cfg=defect_cfg,
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
        item["instance_index"] = idx
        ready.append(item)
    return ready


def _apply_surface_group_subtractions(records, cfg, model_result):
    records = list(records or [])
    if not records:
        return {"groups": 0, "cutters": 0, "targets": 0}

    source_ids = _collect_object_ids(model_result)
    if not source_ids:
        return {"groups": 0, "cutters": 0, "targets": 0}

    target_surfaces = get_surfaces(
        object_ids=source_ids,
        layer_names=cfg.get("target_layers"),
        convert_polylines=False,
        explode_polysurfaces=False,
        keep_input=True,
    )
    by_layer_surfaces = {}
    for sid in target_surfaces:
        if not sid or not rs.IsObject(sid):
            continue
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

    by_layer_records = {}
    for record in records:
        if record.get("type") not in ("crack", "spalling", "exposed_rebar"):
            continue
        polygon = record.get("surface_cut_polygon") or []
        if len(polygon) < 4:
            continue
        layer_name = str(record.get("surface_layer") or "")
        by_layer_records.setdefault(layer_name, []).append(record)

    groups = 0
    cutters = 0
    targets = 0
    for layer_name, layer_records in by_layer_records.items():
        target_ids = [sid for sid in by_layer_surfaces.get(layer_name, []) if rs.IsObject(sid)]
        if not target_ids:
            continue

        cutter_ids = []
        for record in layer_records:
            polygon = record.get("surface_cut_polygon") or []
            curve_id = _add_polyline(polygon)
            if curve_id:
                cutter_ids.append(curve_id)
                if normal_extrude_distance > 0.0:
                    normal_vec = _unit(record.get("normal") or (0.0, 0.0, 1.0), fallback=(0.0, 0.0, 1.0))
                    offset_span = abs(_to_float(record.get("normal_offset"), 0.0))
                    extrude_distance = max(normal_extrude_distance, normal_extrude_distance + offset_span)
                    anchor = _vec3(polygon[0])
                    start_pt = _add(anchor, _scale(normal_vec, -extrude_distance))
                    end_pt = _add(anchor, _scale(normal_vec, extrude_distance))
                    extruded = rs.ExtrudeCurveStraight(curve_id, start_pt, end_pt)
                    if extruded and rs.IsObject(extruded):
                        cutter_ids.append(extruded)
        if not cutter_ids:
            continue

        try:
            split_ids = subtract_surface(cutter_ids, target_surfaces=target_ids, delete_inputs=True) or []
            _assign_layer(split_ids, layer_name)
            groups += 1
            cutters += len(cutter_ids)
            targets += len(target_ids)
        finally:
            _delete_objects(cutter_ids)

    return {"groups": groups, "cutters": cutters, "targets": targets}


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


def save_defect_records(path, payload):
    if not path:
        return None
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return abs_path


def load_defect_records(path):
    """Load saved defect placement records for rendering/camera seeding."""
    if not path:
        raise ValueError("A valid record path is required.")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise IOError("Defect record file not found: '{}'".format(abs_path))
    with open(abs_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data


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
            "record_output_path": None,
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
            "record_output_path": None,
            "summary": {"total": 0},
            "layer_map": layer_map,
        }

    seed = cfg.get("seed")
    if seed is not None:
        rng = random.Random(_to_int(seed, 0))
    else:
        rng = random.Random()

    candidates = _collect_reference_candidates(cfg, model_result=model_result)
    if not candidates:
        print("Defect pipeline: no valid placement candidates were found.")
        return {
            "enabled": True,
            "records": [],
            "camera_defects": [],
            "record_output_path": None,
            "summary": {"total": 0},
            "layer_map": layer_map,
        }

    records = []
    used_candidate_keys = set()
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
    output_path = save_defect_records(cfg.get("record_output_path"), payload)

    return {
        "enabled": True,
        "records": json_ready,
        "camera_defects": camera_defects,
        "record_output_path": output_path,
        "summary": summary,
        "layer_map": layer_map,
        "surface_subtraction": subtraction,
    }
