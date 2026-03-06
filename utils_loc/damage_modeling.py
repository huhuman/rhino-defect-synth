"""Unified defect placement interfaces for crack/efflore/exposed-rebar."""

import copy
import json
import math
import os
import random

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc.damage_shapes import load_shape_templates
from utils_loc.defect_modeling import get_reference_points, get_surfaces


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


def _ensure_closed(points):
    pts = [tuple(point) for point in points or []]
    if len(pts) < 3:
        return pts
    if _distance(pts[0], pts[-1]) > 1e-6:
        pts.append(pts[0])
    return pts


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


def _project_points_to_surface(points_2d, candidate, scale, angle_deg, normal_offset=0.0):
    normal = (candidate or {}).get("normal") or (0.0, 0.0, 1.0)
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
    closed = _ensure_closed(points)
    if len(closed) < 4:
        return None
    obj_id = rs.AddPolyline(closed)
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

    candidates = []
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
                normal_3d = _unit(normal, fallback=(0.0, 0.0, 1.0))
                u_axis, v_axis, n_axis = _surface_axes(surface_id, point_3d, normal_3d)
                boundary_dist = _distance_to_boundary(point, border_curves)
                if boundary_dist < min_boundary_distance:
                    continue
                candidates.append(
                    {
                        "surface_id": surface_id,
                        "surface_layer": surface_layer,
                        "point": point_3d,
                        "normal": normal_3d,
                        "u_axis": u_axis,
                        "v_axis": v_axis,
                        "n_axis": n_axis,
                        "reference_size": float(size),
                        "boundary_dist": float(boundary_dist),
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


def _resolve_random_value(damage_cfg, random_cfg, key, default):
    value = damage_cfg.get(key)
    if value is None:
        value = random_cfg.get(key, default)
    return value


def _sample_transform(damage_cfg, random_cfg, candidate, shape, rng):
    scale_min = _to_float(_resolve_random_value(damage_cfg, random_cfg, "scale_min", 0.6), 0.6)
    scale_max = _to_float(_resolve_random_value(damage_cfg, random_cfg, "scale_max", 1.4), 1.4)
    if scale_min > scale_max:
        scale_min, scale_max = scale_max, scale_min

    angle_min = _to_float(_resolve_random_value(damage_cfg, random_cfg, "orientation_min_deg", 0.0), 0.0)
    angle_max = _to_float(_resolve_random_value(damage_cfg, random_cfg, "orientation_max_deg", 360.0), 360.0)
    if angle_min > angle_max:
        angle_min, angle_max = angle_max, angle_min

    boundary_margin = _to_float(_resolve_random_value(damage_cfg, random_cfg, "boundary_margin", 0.9), 0.9)
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
    normal_offset = _to_float(_resolve_random_value(damage_cfg, random_cfg, "normal_offset", 0.0), 0.0)

    return {
        "scale": scale,
        "angle_deg": angle_deg,
        "normal_offset": normal_offset,
    }


def _resolve_shape_library(global_cfg, damage_cfg):
    shape_cfg = _deep_merge(global_cfg.get("shape_library") or {}, damage_cfg.get("shape_library") or {})
    return load_shape_templates(
        paths=shape_cfg.get("paths") or [],
        shape_dir=shape_cfg.get("shape_dir"),
        recursive=bool(shape_cfg.get("recursive", True)),
        pattern=shape_cfg.get("pattern", "*.json"),
        file_format=shape_cfg.get("file_format", "auto"),
    )


def _pick_shape_points(shape):
    offset_poly = shape.get("offset_poly") or shape.get("base_poly") or []
    base_poly = shape.get("base_poly") or offset_poly
    crack_polys = shape.get("crack_polys") or ([base_poly] if base_poly else [])
    inside_polys = shape.get("inside_polys") or []
    diff_polys = shape.get("diff_polys") or []
    return offset_poly, base_poly, crack_polys, inside_polys, diff_polys


def _create_seed_marker(candidate, layer_name):
    ensure_layer(layer_name)
    if not rs.IsLayer(layer_name):
        return None
    marker = rs.AddPoint(candidate["point"])
    if marker:
        rs.ObjectLayer(marker, layer_name)
    return marker


def _record_common(damage_type, candidate, transform, shape):
    return {
        "type": damage_type,
        "point": list(candidate["point"]),
        "normal": list(candidate["normal"]),
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
    }


def _model_crack_instance(candidate, shape, transform, cfg, layer_map, rng):
    offset_2d, base_2d, crack_2d, inside_2d, diff_2d = _pick_shape_points(shape)
    offset_3d = _project_points_to_surface(offset_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    base_3d = _project_points_to_surface(base_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])

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

    mask_ids = _add_mask_from_polygon(offset_3d, layer_map["mask"]["crack"], as_surface=True)

    crack_created = create_crack(
        crack_polys,
        inside_polys,
        base_curve,
        offset_curve,
        diff_polys,
        inward_dir=_scale(candidate["normal"], -1.0),
        d1_range=tuple(cfg["crack"].get("d1_range", (0.5, 2.5))),
        delta_depth_range=tuple(cfg["crack"].get("delta_depth_range", (10.0, 30.0))),
        layer_crack_extrusion=layer_map["geometry"]["crack"],
        layer_parent_surface=candidate.get("surface_layer"),
        cleanup_inputs=True,
        rng=rng,
    ) or {}
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

    record = _record_common("crack", candidate, transform, shape)
    record["geometry_ids"] = _as_strings(crack_geometry)
    record["mask_ids"] = _as_strings(mask_ids)
    record["crack_metrics"] = {
        "d1": crack_created.get("d1"),
        "d2": crack_created.get("d2"),
    }
    return record


def _model_efflore_instance(candidate, shape, transform, cfg, layer_map, rng):
    offset_2d, _, _, _, _ = _pick_shape_points(shape)
    polygon = _project_points_to_surface(offset_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    curve_id = _add_polyline(polygon)
    if not curve_id:
        return None

    mask_ids = _add_mask_from_polygon(polygon, layer_map["mask"]["efflore"], as_surface=True)
    _assign_layer([curve_id], layer_map["geometry"]["efflore"])

    thickness_min, thickness_max = cfg["efflore"].get("thickness_range", [0.2, 1.5])
    thickness_min = _to_float(thickness_min, 0.2)
    thickness_max = _to_float(thickness_max, 1.5)
    if thickness_min > thickness_max:
        thickness_min, thickness_max = thickness_max, thickness_min
    thickness = rng.uniform(thickness_min, thickness_max)

    start = candidate["point"]
    end = _add(start, _scale(candidate["normal"], thickness))
    extrusion = rs.ExtrudeCurveStraight(curve_id, start, end)
    geometry_ids = _coerce_ids([extrusion])
    if not geometry_ids:
        geometry_ids.extend(_coerce_ids(rs.AddPlanarSrf(curve_id) or []))
    _assign_layer(geometry_ids, layer_map["geometry"]["efflore"])

    if rs.IsObject(curve_id):
        rs.DeleteObject(curve_id)
    if not geometry_ids:
        _delete_objects(mask_ids)
        return None

    record = _record_common("efflore", candidate, transform, shape)
    record["geometry_ids"] = _as_strings(geometry_ids)
    record["mask_ids"] = _as_strings(mask_ids)
    record["efflore_metrics"] = {"thickness": thickness}
    return record


def _model_spall_from_polygon(polygon, candidate, depth, layer_name):
    top_curve = _add_polyline(polygon, layer_name=layer_name)
    if not top_curve:
        return []
    bottom_curve = rs.CopyObject(top_curve, _scale(candidate["normal"], -depth))
    ids = [top_curve]
    if bottom_curve:
        ids.append(bottom_curve)
        loft = rs.AddLoftSrf([top_curve, bottom_curve]) or []
        bottom_cap = rs.AddPlanarSrf(bottom_curve) or []
        top_cap = rs.AddPlanarSrf(top_curve) or []
        ids.extend(_coerce_ids(loft))
        ids.extend(_coerce_ids(bottom_cap))
        ids.extend(_coerce_ids(top_cap))
    _assign_layer(ids, layer_name)
    return _coerce_ids(ids)


def _model_rebar_bars(candidate, transform, shape_radius, cfg, layer_name, rng):
    u_axis, v_axis, n_axis = _candidate_axes(candidate)
    rebar_cfg = cfg["exposed_rebar"]

    bar_count_min, bar_count_max = rebar_cfg.get("rebar_count_range", [1, 3])
    bar_count_min = max(1, _to_int(bar_count_min, 1))
    bar_count_max = max(1, _to_int(bar_count_max, 3))
    if bar_count_min > bar_count_max:
        bar_count_min, bar_count_max = bar_count_max, bar_count_min
    bar_count = rng.randint(bar_count_min, bar_count_max)

    radius_min, radius_max = rebar_cfg.get("rebar_radius_range", [0.8, 2.5])
    radius_min = max(0.05, _to_float(radius_min, 0.8))
    radius_max = max(0.05, _to_float(radius_max, 2.5))
    if radius_min > radius_max:
        radius_min, radius_max = radius_max, radius_min

    length_scale = max(0.2, _to_float(rebar_cfg.get("rebar_length_scale"), 1.3))
    cover_depth = max(0.0, _to_float(rebar_cfg.get("rebar_cover_depth"), 2.0))
    span = max(5.0, shape_radius * transform["scale"] * 2.0)
    bar_length = span * length_scale
    spacing = span / float(max(1, bar_count))

    center_base = _add(candidate["point"], _scale(n_axis, -cover_depth))
    created = []
    for idx in range(bar_count):
        side_offset = (idx - (bar_count - 1) * 0.5) * spacing
        center = _add(center_base, _scale(v_axis, side_offset))
        start = _add(center, _scale(u_axis, -0.5 * bar_length))
        end = _add(center, _scale(u_axis, 0.5 * bar_length))
        line_id = rs.AddLine(start, end)
        if not line_id:
            continue
        radius = rng.uniform(radius_min, radius_max)
        pipes = _coerce_ids(rs.AddPipe(line_id, 0.0, radius, cap=2) or [])
        if pipes:
            created.extend(pipes)
        if rs.IsObject(line_id):
            rs.DeleteObject(line_id)
    _assign_layer(created, layer_name)
    return created, {"bar_count": bar_count, "bar_length": bar_length}


def _model_exposed_rebar_instance(candidate, shape, transform, cfg, layer_map, rng):
    offset_2d, _, _, _, _ = _pick_shape_points(shape)
    polygon = _project_points_to_surface(offset_2d, candidate, transform["scale"], transform["angle_deg"], transform["normal_offset"])
    if len(polygon) < 4:
        return None

    spall_depth_min, spall_depth_max = cfg["exposed_rebar"].get("spall_depth_range", [5.0, 20.0])
    spall_depth_min = max(0.1, _to_float(spall_depth_min, 5.0))
    spall_depth_max = max(0.1, _to_float(spall_depth_max, 20.0))
    if spall_depth_min > spall_depth_max:
        spall_depth_min, spall_depth_max = spall_depth_max, spall_depth_min
    spall_depth = rng.uniform(spall_depth_min, spall_depth_max)

    mask_ids = []
    mask_ids.extend(_add_mask_from_polygon(polygon, layer_map["mask"]["exposed_rebar"], as_surface=True))
    mask_ids.extend(_add_mask_from_polygon(polygon, layer_map["mask"]["spall"], as_surface=False))

    spall_ids = _model_spall_from_polygon(polygon, candidate, spall_depth, layer_map["geometry"]["spall"])
    rebar_ids, rebar_metrics = _model_rebar_bars(
        candidate,
        transform,
        shape_radius=max(1.0, _to_float(shape.get("shape_radius"), 1.0)),
        cfg=cfg,
        layer_name=layer_map["geometry"]["rebar"],
        rng=rng,
    )
    geometry_ids = _coerce_ids(spall_ids + rebar_ids)
    if not geometry_ids:
        _delete_objects(mask_ids + spall_ids + rebar_ids)
        return None

    record = _record_common("exposed_rebar", candidate, transform, shape)
    record["spall_geometry_ids"] = _as_strings(spall_ids)
    record["rebar_geometry_ids"] = _as_strings(rebar_ids)
    record["geometry_ids"] = _as_strings(geometry_ids)
    record["mask_ids"] = _as_strings(mask_ids)
    record["spall_metrics"] = {"depth": spall_depth}
    record["rebar_metrics"] = rebar_metrics
    return record


def _resolve_layer_map(cfg):
    layers_cfg = cfg.get("layers") or {}
    layer_map = {
        "seeds": layers_cfg.get("seeds", "defects::seeds"),
        "geometry": dict((layers_cfg.get("geometry") or {})),
        "mask": dict((layers_cfg.get("mask") or {})),
    }
    for key, value in {
        "crack": "defects::geometry::crack",
        "efflore": "defects::geometry::efflore",
        "spall": "defects::geometry::spall",
        "rebar": "defects::geometry::rebar",
    }.items():
        layer_map["geometry"].setdefault(key, value)
    for key, value in {
        "crack": "defects::mask::crack",
        "efflore": "defects::mask::efflore",
        "spall": "defects::mask::spall",
        "rebar": "defects::mask::rebar",
        "exposed_rebar": "defects::mask::exposed_rebar",
    }.items():
        layer_map["mask"].setdefault(key, value)

    ensure_layer(layer_map["seeds"])
    for layer_name in layer_map["geometry"].values():
        ensure_layer(layer_name)
    for layer_name in layer_map["mask"].values():
        ensure_layer(layer_name)
    return layer_map


def _build_instance_records_for_type(damage_type, cfg, candidates, shapes, layer_map, rng):
    if damage_type not in ("crack", "efflore", "exposed_rebar"):
        return []

    damage_cfg = cfg.get(damage_type) or {}
    if not bool(damage_cfg.get("enabled", True)):
        return []
    count = max(0, _to_int(damage_cfg.get("count"), 0))
    if count <= 0:
        return []

    random_cfg = cfg.get("random") or {}
    max_attempts = max(1, _to_int(cfg.get("max_attempts_per_instance"), 40))
    records = []

    for _ in range(count):
        placed = None
        for _attempt in range(max_attempts):
            candidate = rng.choice(candidates)
            shape = rng.choice(shapes)
            transform = _sample_transform(damage_cfg, random_cfg, candidate, shape, rng=rng)
            if transform is None:
                continue

            if damage_type == "crack":
                placed = _model_crack_instance(candidate, shape, transform, cfg, layer_map, rng=rng)
            elif damage_type == "efflore":
                placed = _model_efflore_instance(candidate, shape, transform, cfg, layer_map, rng=rng)
            else:
                placed = _model_exposed_rebar_instance(candidate, shape, transform, cfg, layer_map, rng=rng)

            if placed is None:
                continue

            geometry_ids = _coerce_ids(placed.get("geometry_ids") or [])
            if not geometry_ids:
                _delete_objects(
                    (placed.get("geometry_ids") or [])
                    + (placed.get("spall_geometry_ids") or [])
                    + (placed.get("rebar_geometry_ids") or [])
                    + (placed.get("mask_ids") or [])
                )
                placed = None
                continue

            seed_id = _create_seed_marker(candidate, layer_map["seeds"])
            if seed_id:
                placed["seed_id"] = str(seed_id)
            records.append(placed)
            break
        if placed is None:
            print("Damage {}: failed to place one instance within attempt budget.".format(damage_type))
    return records


def _json_ready_records(records):
    ready = []
    for idx, record in enumerate(records):
        item = dict(record)
        item["instance_index"] = idx
        ready.append(item)
    return ready


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
                "damage_type": record.get("type"),
                "instance_index": record.get("instance_index"),
            }
        )
    return defects


def save_damage_records(path, payload):
    if not path:
        return None
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return abs_path


def load_damage_records(path):
    """Load saved damage placement records for rendering/camera seeding."""
    if not path:
        raise ValueError("A valid record path is required.")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise IOError("Damage record file not found: '{}'".format(abs_path))
    with open(abs_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data


def defects_from_record_payload(payload, include_damage_types=None):
    """Convert damage records into camera defect seeds."""
    if not payload:
        return []
    if not isinstance(payload, dict):
        raise ValueError("Damage record payload must be a mapping/dict.")

    defects = payload.get("camera_defects")
    if defects is None:
        records = payload.get("records") or []
        if not isinstance(records, (list, tuple)):
            raise ValueError("Damage record payload field 'records' must be a list.")
        defects = _extract_camera_defects(records)
    elif not isinstance(defects, (list, tuple)):
        raise ValueError("Damage record payload field 'camera_defects' must be a list.")

    if not include_damage_types:
        return list(defects)
    if isinstance(include_damage_types, str):
        include_damage_types = [include_damage_types]
    allowed = {str(name) for name in include_damage_types}
    return [item for item in defects if isinstance(item, dict) and item.get("damage_type") in allowed]


def apply_damage_pipeline(params=None, model_result=None):
    """Place crack/efflore/exposed-rebar defects on model surfaces."""
    cfg = copy.deepcopy(params or {})
    if not bool(cfg.get("enabled", True)):
        return {
            "enabled": False,
            "records": [],
            "camera_defects": [],
            "record_output_path": None,
            "summary": {"total": 0},
            "layer_map": _resolve_layer_map(cfg),
        }

    seed = cfg.get("seed")
    if seed is not None:
        rng = random.Random(_to_int(seed, 0))
    else:
        rng = random.Random()

    layer_map = _resolve_layer_map(cfg)
    candidates = _collect_reference_candidates(cfg, model_result=model_result)
    if not candidates:
        print("Damage pipeline: no valid placement candidates were found.")
        return {
            "enabled": True,
            "records": [],
            "camera_defects": [],
            "record_output_path": None,
            "summary": {"total": 0},
            "layer_map": layer_map,
        }

    shape_cache = {}
    records = []
    for damage_type in ("crack", "efflore", "exposed_rebar"):
        damage_cfg = cfg.get(damage_type) or {}
        if not bool(damage_cfg.get("enabled", True)) or max(0, _to_int(damage_cfg.get("count"), 0)) == 0:
            continue

        if damage_type not in shape_cache:
            shape_cache[damage_type] = _resolve_shape_library(cfg, damage_cfg)
        shapes = shape_cache[damage_type]
        records.extend(
            _build_instance_records_for_type(
                damage_type,
                cfg,
                candidates,
                shapes,
                layer_map,
                rng=rng,
            )
        )

    json_ready = _json_ready_records(records)
    camera_defects = _extract_camera_defects(json_ready)
    summary = {
        "total": len(json_ready),
        "crack": sum(1 for item in json_ready if item.get("type") == "crack"),
        "efflore": sum(1 for item in json_ready if item.get("type") == "efflore"),
        "exposed_rebar": sum(1 for item in json_ready if item.get("type") == "exposed_rebar"),
    }

    payload = {
        "records": json_ready,
        "camera_defects": camera_defects,
        "summary": summary,
        "layer_map": layer_map,
    }
    output_path = save_damage_records(cfg.get("record_output_path"), payload)

    return {
        "enabled": True,
        "records": json_ready,
        "camera_defects": camera_defects,
        "record_output_path": output_path,
        "summary": summary,
        "layer_map": layer_map,
    }
