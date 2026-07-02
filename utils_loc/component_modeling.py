"""Configurable bridge component modeling utilities.

This module refactors the procedural bridge script (tmp.py) into a parameterized
pipeline that can be called from utils_loc.pipeline::create_model.
"""

import math
import random

import rhinoscriptsyntax as rs

INCH_TO_UNIT = 2.54
FOOT_TO_UNIT = 30.48

DEFAULT_BEAM_SECTION_LIBRARY_INCH = {
    "63 bulb_t-beam": [42, 2, 6, 26, 3.5, 2, 2, 45, 4.5, 6],
    "72 bulb_t-beam": [42, 2, 6, 26, 3.5, 2, 2, 54, 4.5, 6],
    "36 I-beam": [12, 6, 18, 4, 3, 17, 6, 6],
    "42 I-beam": [16, 6, 22, 4, 2.5, 21.5, 8, 6],
    "48 I-beam": [18, 7.5, 22, 4, 2 + 5 / 8, 27 + 1 / 8, 7.25, 7],
    "54 I-beam": [20, 6, 22, 6, 3, 30.75, 7.25, 7],
    "36A IL-beam": [24, 7, 38, 6 + 1 / 16, 6 + 7 / 16, 1.5, 15, 7],
    "45A IL-beam": [24, 7, 38, 6 + 1 / 16, 6 + 7 / 16, 10.5, 15, 7],
    "54A IL-beam": [24, 7, 38, 6 + 1 / 16, 6 + 7 / 16, 19.5, 15, 7],
    "36B IL-beam": [38, 7, 38, 5, 7.5, 1.5, 15, 7],
    "45B IL-beam": [38, 7, 38, 5, 7.5, 10.5, 15, 7],
}

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


def _xyz(value):
    if value is None:
        return 0.0, 0.0, 0.0
    if hasattr(value, "X") and hasattr(value, "Y"):
        return float(value.X), float(value.Y), float(getattr(value, "Z", 0.0))
    if len(value) == 2:
        return float(value[0]), float(value[1]), 0.0
    return float(value[0]), float(value[1]), float(value[2])


def _add(a, b):
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    return ax + bx, ay + by, az + bz


def _sub(a, b):
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    return ax - bx, ay - by, az - bz


def _scale(v, scalar):
    vx, vy, vz = _xyz(v)
    s = float(scalar)
    return vx * s, vy * s, vz * s


def _cross(a, b):
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    return ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx


def _unit(v, fallback=(0.0, 0.0, 1.0)):
    vx, vy, vz = _xyz(v)
    length = math.sqrt(vx * vx + vy * vy + vz * vz)
    if length <= 1e-12:
        fx, fy, fz = _xyz(fallback)
        fallback_len = math.sqrt(fx * fx + fy * fy + fz * fz)
        if fallback_len <= 1e-12:
            return 0.0, 0.0, 1.0
        return fx / fallback_len, fy / fallback_len, fz / fallback_len
    inv = 1.0 / length
    return vx * inv, vy * inv, vz * inv


def _distance(a, b):
    dx, dy, dz = _sub(a, b)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _close_points(points):
    if not points:
        return []
    closed = [_xyz(pt) for pt in points]
    if _distance(closed[0], closed[-1]) > 1e-6:
        closed.append(closed[0])
    return closed


def _unique_vertices(points):
    if not points:
        return []
    verts = []
    for pt in points:
        p = _xyz(pt)
        if not verts or _distance(verts[-1], p) > 1e-6:
            verts.append(p)
    if len(verts) > 1 and _distance(verts[0], verts[-1]) <= 1e-6:
        verts.pop()
    return verts


def _layer_name(cfg, logical_name):
    return (cfg.get("layers") or {}).get(logical_name, logical_name)


def _layer_path_parts(layer_name):
    return [part.strip() for part in str(layer_name).split("::") if part.strip()]


def _ensure_layer(layer_name):
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


def _assign_layer(obj_id, layer_name):
    if obj_id and layer_name:
        _ensure_layer(layer_name)
    if obj_id and layer_name and rs.IsLayer(layer_name):
        rs.ObjectLayer(obj_id, layer_name)


def _append_component_object(result, component, obj_id):
    if not obj_id:
        return
    by_component = result["objects_by_component"]
    if component not in by_component:
        by_component[component] = []
    by_component[component].append(obj_id)


def _add_polygon(result, component, layer_name, points, cfg):
    closed = _close_points(points)
    if len(closed) < 4:
        return []

    keep_curve = bool(cfg.get("keep_polygon_curves", False))
    convert = bool(cfg.get("convert_polygons_to_surfaces", True))
    vertices = _unique_vertices(closed)

    # Fast path for the common quad case: avoid polyline creation/deletion churn.
    if convert and len(vertices) == 4 and not keep_curve:
        srf_id = rs.AddSrfPt(vertices)
        if srf_id:
            _assign_layer(srf_id, layer_name)
            result["surfaces"].append(srf_id)
            _append_component_object(result, component, srf_id)
            return [srf_id]

    curve_id = rs.AddPolyline(closed)
    if not curve_id:
        return []
    _assign_layer(curve_id, layer_name)

    created_ids = []

    if convert:
        surface_ids = rs.AddPlanarSrf(curve_id) or []
        if not surface_ids:
            if len(vertices) == 4:
                srf_id = rs.AddSrfPt(vertices)
                if srf_id:
                    surface_ids = [srf_id]
        for srf_id in surface_ids:
            _assign_layer(srf_id, layer_name)
            result["surfaces"].append(srf_id)
            _append_component_object(result, component, srf_id)
            created_ids.append(srf_id)
        if created_ids and not keep_curve and rs.IsObject(curve_id):
            rs.DeleteObject(curve_id)

    if keep_curve or not created_ids:
        result["polylines"].append(curve_id)
        _append_component_object(result, component, curve_id)
        created_ids.append(curve_id)

    return created_ids


def _box_corners(center_pt, width_u, width_v, height_w, u_dir, v_dir, w_dir):
    center = _xyz(center_pt)
    u = _unit(u_dir, fallback=(1.0, 0.0, 0.0))
    v = _unit(v_dir, fallback=(0.0, 1.0, 0.0))
    w = _unit(w_dir, fallback=(0.0, 0.0, -1.0))
    hu = 0.5 * float(width_u)
    hv = 0.5 * float(width_v)
    hw = float(height_w)

    corners = []
    corners.append(_add(_add(center, _scale(u, hu)), _scale(v, hv)))
    corners.append(_add(_add(center, _scale(u, hu)), _scale(v, -hv)))
    corners.append(_add(_add(center, _scale(u, -hu)), _scale(v, -hv)))
    corners.append(_add(_add(center, _scale(u, -hu)), _scale(v, hv)))
    for idx in range(4):
        corners.append(_add(corners[idx], _scale(w, hw)))
    return corners


def _add_box(result, component, layer_name, corners):
    box_id = rs.AddBox(corners)
    if not box_id:
        return None
    _assign_layer(box_id, layer_name)
    result["solids"].append(box_id)
    _append_component_object(result, component, box_id)
    return box_id


def _build_centerline(centerline_cfg, rng=None):
    rng = random if rng is None else rng
    span = max(1e-6, _to_float(centerline_cfg.get("span"), 70 * FOOT_TO_UNIT))
    num_base_pts = max(2, _to_int(centerline_cfg.get("num_base_pts"), 3))
    start_pt = _xyz(centerline_cfg.get("start_point", [0.0, 0.0, 0.0]))
    total_length = float(num_base_pts - 1) * span
    end_pt = (start_pt[0], start_pt[1] + total_length, start_pt[2])

    use_curve = bool(centerline_cfg.get("use_curve", True))
    theta_deg = centerline_cfg.get("theta_deg")
    if theta_deg is None:
        t_min = _to_float(centerline_cfg.get("theta_deg_min"), 75.0)
        t_max = _to_float(centerline_cfg.get("theta_deg_max"), 105.0)
        if t_min > t_max:
            t_min, t_max = t_max, t_min
        theta_deg = rng.uniform(t_min, t_max)
    theta_rad = math.radians(float(theta_deg))

    if use_curve:
        half_length = 0.5 * total_length
        mid_pt = (
            start_pt[0] + half_length * math.cos(theta_rad),
            start_pt[1] + half_length * math.sin(theta_rad),
            start_pt[2],
        )
        curve_id = rs.AddCurve([start_pt, mid_pt, end_pt], 2)
    else:
        curve_id = rs.AddLine(start_pt, end_pt)

    if not curve_id:
        raise RuntimeError("Failed to create centerline curve.")

    base_pts = rs.DivideCurveLength(curve_id, span) or []
    if len(base_pts) < 2:
        base_pts = rs.DivideCurve(curve_id, num_base_pts - 1) or []
    base_pts = [_xyz(pt) for pt in base_pts]
    if len(base_pts) < 2:
        base_pts = [start_pt, end_pt]

    road_dirs = []
    norm_dirs = []
    for base_pt in base_pts:
        param = rs.CurveClosestPoint(curve_id, base_pt)
        tangent = _xyz(rs.CurveTangent(curve_id, param))
        road_dir = _unit((tangent[0], tangent[1], 0.0), fallback=(0.0, 1.0, 0.0))
        norm_dir = _unit((road_dir[1], -road_dir[0], 0.0), fallback=(1.0, 0.0, 0.0))
        road_dirs.append(road_dir)
        norm_dirs.append(norm_dir)

    return curve_id, base_pts, road_dirs, norm_dirs, float(theta_deg)


def _build_slab_and_parapet(result, base_pts, norm_dirs, cfg):
    slab_cfg = cfg.get("slab") or {}
    parapet_cfg = cfg.get("parapet") or {}
    slab_layer = _layer_name(cfg, "slab")
    parapet_layer = _layer_name(cfg, "parapet")
    parapet_enabled = bool(parapet_cfg.get("enabled", True))

    width = max(1e-6, _to_float(slab_cfg.get("width"), 1200.0))
    thickness = max(1e-6, _to_float(slab_cfg.get("thickness"), 25.0))
    cross_slope = _to_float(slab_cfg.get("cross_slope_ratio"), 1.5 / 100.0)

    h1 = _to_float(parapet_cfg.get("h1"), 4 * INCH_TO_UNIT)
    h2 = _to_float(parapet_cfg.get("h2"), 9 * INCH_TO_UNIT)
    h3 = _to_float(parapet_cfg.get("h3"), 37.5 * INCH_TO_UNIT)
    h4 = _to_float(parapet_cfg.get("h4"), 39 * INCH_TO_UNIT)
    w1 = _to_float(parapet_cfg.get("w1"), 30 * INCH_TO_UNIT)
    w2 = _to_float(parapet_cfg.get("w2"), 9.5 * INCH_TO_UNIT)
    w3 = _to_float(parapet_cfg.get("w3"), 7.5 * INCH_TO_UNIT)

    deck_bottom_pts = [_add(base_pt, (0.0, 0.0, -thickness)) for base_pt in base_pts]

    section_points = []
    rail_points = []
    half_width = 0.5 * width
    delta_height = cross_slope * half_width
    z_up = (0.0, 0.0, 1.0)

    for base_pt, norm_dir in zip(base_pts, norm_dirs):
        station_sections = []
        station_rails = []
        for dist_dir in (1.0, -1.0):
            dist = half_width * dist_dir
            top_edge = _add(base_pt, _scale(norm_dir, dist))
            top_edge = (top_edge[0], top_edge[1], top_edge[2] - delta_height)

            bottom_edge = _add(base_pt, _scale(norm_dir, dist))
            bottom_edge = (bottom_edge[0], bottom_edge[1], bottom_edge[2] - thickness)

            center_bottom = (base_pt[0], base_pt[1], base_pt[2] - thickness)
            section = [base_pt, top_edge, bottom_edge, center_bottom]
            station_sections.append(section)

            if not parapet_enabled:
                continue

            rail = [top_edge]
            pt = _add(rail[0], _scale(z_up, h4 - h3 - h2 - h1))
            rail.append(pt)
            pt = _add(_add(pt, _scale(norm_dir, w1 * dist_dir)), _scale(z_up, h1))
            rail.append(pt)
            pt = _add(pt, _scale(z_up, h2))
            rail.append(pt)
            pt = _add(_add(pt, _scale(norm_dir, -(w2 + w3) * dist_dir)), _scale(z_up, -(h4 - h3)))
            rail.append(pt)
            pt = _add(_add(pt, _scale(norm_dir, w3 * dist_dir)), _scale(z_up, h4))
            rail.append(pt)
            pt = _add(pt, _scale(norm_dir, w2 * dist_dir))
            rail.append(pt)
            pt = _add(pt, _scale(z_up, -h3))
            rail.append(pt)
            station_rails.append(rail)

        section_points.append(station_sections)
        rail_points.append(station_rails)

    cap_indices = [0]
    if len(section_points) > 1:
        cap_indices.append(len(section_points) - 1)
    for station_idx in cap_indices:
        for i, section in enumerate(section_points[station_idx]):
            if i == 1:
                section = section[::-1]
            if station_idx == 0:
                section = section[::-1]
            _add_polygon(result, "slab", slab_layer, section, cfg)
        if parapet_enabled:
            for i, rail in enumerate(rail_points[station_idx]):
                if (station_idx == 0 and i == 0) or (station_idx != 0 and i == 1):
                    upper = rail[:5]
                    lower = (rail[5:] + rail[4:6])[::-1]
                else:
                    upper = rail[:5][::-1]
                    lower = rail[5:] + rail[4:6]
                _add_polygon(result, "parapet", parapet_layer, upper, cfg)
                _add_polygon(result, "parapet", parapet_layer, lower, cfg)

    for sid in range(1, len(section_points)):
        prev_sections = section_points[sid - 1]
        cur_sections = section_points[sid]
        for i, (prev, cur) in enumerate(zip(prev_sections, cur_sections)):
            for edge_idx in range(len(cur) - 1):
                quad = [prev[edge_idx], prev[edge_idx + 1], cur[edge_idx + 1], cur[edge_idx]]
                if i == 1:
                    quad = quad[::-1]
                _add_polygon(result, "slab", slab_layer, quad, cfg)

        if parapet_enabled:
            prev_rails = rail_points[sid - 1]
            cur_rails = rail_points[sid]
            for i, (prev, cur) in enumerate(zip(prev_rails, cur_rails)):
                # left/right
                reversed_edge_indices = (4, 5, 6) if i == 0 else (0, 1, 2)
                for edge_idx in range(len(cur) - 1):
                    if edge_idx == 3: # skip the inner face 
                        continue
                    quad = [cur[edge_idx], cur[edge_idx + 1], prev[edge_idx + 1], prev[edge_idx]]
                    if edge_idx in reversed_edge_indices:
                        quad = quad[::-1]
                    _add_polygon(result, "parapet", parapet_layer, quad, cfg)
                quad = [cur[0], cur[4], prev[4], prev[0]]
                if i == 0 :
                    quad = quad[::-1]
                _add_polygon(result, "parapet", parapet_layer, quad, cfg)

    return {
        "width": width,
        "thickness": thickness,
        "deck_bottom_points": deck_bottom_pts,
    }


def _resolve_beam_section(beam_cfg):
    section_library = dict(DEFAULT_BEAM_SECTION_LIBRARY_INCH)
    section_library.update(beam_cfg.get("section_library_inch") or {})
    section_key = str(beam_cfg.get("section_key", "36 I-beam"))

    if section_key not in section_library:
        keys = sorted(section_library.keys())
        raise ValueError(
            "Unknown beam.section_key '{}' (available examples: {}).".format(
                section_key,
                ", ".join(keys[:6]),
            )
        )

    props_cm = [float(value) * INCH_TO_UNIT for value in section_library[section_key]]
    beam_type = section_key.split(" ")[-1].lower()
    if beam_type not in ("bulb_t-beam", "i-beam", "il-beam"):
        raise ValueError("Unsupported beam section type '{}'.".format(beam_type))

    if beam_type == "bulb_t-beam":
        top_width = props_cm[0]
        beam_height = sum(props_cm[4:])
    else:
        top_width = props_cm[0]
        beam_height = sum(props_cm[3:])

    return {
        "section_key": section_key,
        "beam_type": beam_type,
        "props_cm": props_cm,
        "top_width": top_width,
        "beam_height": beam_height,
    }


def _beam_half_profile(base_pt, norm_dir, beam_type, props_cm, direction):
    d = float(direction)
    if beam_type == "bulb_t-beam":
        w1, w2, w3, w4, h1, h2, h3, h4, h5, h6 = props_cm
        return [
            _add(base_pt, _scale(norm_dir, -d * w1 / 2.0)),
            _add(_add(base_pt, _scale(norm_dir, -d * w1 / 2.0)), (0.0, 0.0, -h1)),
            _add(_add(base_pt, _scale(norm_dir, -d * (w2 + w3 / 2.0))), (0.0, 0.0, -(h1 + h2))),
            _add(_add(base_pt, _scale(norm_dir, -d * (w3 / 2.0))), (0.0, 0.0, -(h1 + h2 + h3))),
            _add(_add(base_pt, _scale(norm_dir, -d * (w3 / 2.0))), (0.0, 0.0, -(h1 + h2 + h3 + h4))),
            _add(_add(base_pt, _scale(norm_dir, -d * (w4 / 2.0))), (0.0, 0.0, -(h1 + h2 + h3 + h4 + h5))),
            _add(_add(base_pt, _scale(norm_dir, -d * (w4 / 2.0))), (0.0, 0.0, -(h1 + h2 + h3 + h4 + h5 + h6))),
        ]

    if beam_type in ("i-beam", "il-beam"):
        w1, w2, w3, h1, h2, h3, h4, h5 = props_cm
        return [
            _add(base_pt, _scale(norm_dir, -d * w1 / 2.0)),
            _add(_add(base_pt, _scale(norm_dir, -d * w1 / 2.0)), (0.0, 0.0, -h1)),
            _add(_add(base_pt, _scale(norm_dir, -d * w2 / 2.0)), (0.0, 0.0, -(h1 + h2))),
            _add(_add(base_pt, _scale(norm_dir, -d * w2 / 2.0)), (0.0, 0.0, -(h1 + h2 + h3))),
            _add(_add(base_pt, _scale(norm_dir, -d * w3 / 2.0)), (0.0, 0.0, -(h1 + h2 + h3 + h4))),
            _add(_add(base_pt, _scale(norm_dir, -d * w3 / 2.0)), (0.0, 0.0, -(h1 + h2 + h3 + h4 + h5))),
        ]

    return []


def _build_beams(result, deck_bottom_pts, norm_dirs, slab_width, cfg):
    beam_cfg = cfg.get("beam") or {}
    beam_layer = _layer_name(cfg, "beam")
    enabled = bool(beam_cfg.get("enabled", True))

    if not enabled:
        return {
            "enabled": False,
            "num_lines": 0,
            "valid_width": slab_width,
            "line_spacing": slab_width,
            "beam_height": 0.0,
            "bottom_points": list(deck_bottom_pts),
            "line_bottom_points": [],
        }

    section = _resolve_beam_section(beam_cfg)
    beam_type = section["beam_type"]
    props_cm = section["props_cm"]
    top_width = section["top_width"]
    beam_height = section["beam_height"]

    num_lines = max(1, _to_int(beam_cfg.get("num_lines"), 7))
    valid_width = max(0.0, float(slab_width) - float(top_width))
    spacing = valid_width / float(max(1, num_lines - 1))

    station_profiles = []
    line_bottom_points = []
    if num_lines == 1:
        line_offsets = [0.0]
    else:
        line_offsets = [valid_width * 0.5 - line_idx * spacing for line_idx in range(num_lines)]
    for deck_bottom, norm_dir in zip(deck_bottom_pts, norm_dirs):
        station = []
        station_line_bottom = []
        for offset in line_offsets:
            center = _add(deck_bottom, _scale(norm_dir, offset))
            station_line_bottom.append(_add(center, (0.0, 0.0, -beam_height)))

            right_half = _beam_half_profile(center, norm_dir, beam_type, props_cm, direction=1)
            left_half = list(reversed(_beam_half_profile(center, norm_dir, beam_type, props_cm, direction=-1)))
            profile = _close_points(right_half + left_half)
            station.append(profile)

        station_profiles.append(station)
        line_bottom_points.append(station_line_bottom)

    for station_idx in range(1, len(station_profiles)):
        prev_station = station_profiles[station_idx - 1]
        cur_station = station_profiles[station_idx]
        for prev_profile, cur_profile in zip(prev_station, cur_station):
            edge_count = min(len(prev_profile), len(cur_profile)) - 1
            for edge_idx in range(max(0, edge_count)):
                quad = [
                    cur_profile[edge_idx],
                    cur_profile[edge_idx + 1],
                    prev_profile[edge_idx + 1],
                    prev_profile[edge_idx],
                ]
                _add_polygon(result, "beam", beam_layer, quad, cfg)

    cap_indices = [0]
    if len(station_profiles) > 1:
        cap_indices.append(len(station_profiles) - 1)
    for station_idx in cap_indices:
        for profile in station_profiles[station_idx]:
            _add_polygon(result, "beam", beam_layer, profile, cfg)

    return {
        "enabled": True,
        "num_lines": num_lines,
        "valid_width": valid_width,
        "line_spacing": spacing,
        "beam_height": beam_height,
        "bottom_points": [_add(pt, (0.0, 0.0, -beam_height)) for pt in deck_bottom_pts],
        "line_bottom_points": line_bottom_points,
    }


def _build_bearings(result, beam_meta, road_dirs, norm_dirs, cfg):
    bearing_cfg = cfg.get("bearing") or {}
    pier_cfg = cfg.get("pier") or {}
    bearing_layer = _layer_name(cfg, "bearing")
    enabled = bool(bearing_cfg.get("enabled", True))

    beam_bottom_pts = beam_meta.get("bottom_points") or []
    if not enabled or not beam_bottom_pts:
        return {"foundation_points": list(beam_bottom_pts), "total_height": 0.0}

    base_norm_width = max(1e-6, _to_float(bearing_cfg.get("base_norm_width"), 120.0))
    thickness = max(1e-6, _to_float(bearing_cfg.get("thickness"), 5.0))
    road_width_scale = _to_float(bearing_cfg.get("road_width_scale"), 0.8)
    mid_width_scale = _to_float(bearing_cfg.get("mid_width_scale"), 0.8)
    mid_thickness_scale = _to_float(bearing_cfg.get("mid_thickness_scale"), 1.5)

    pier_width = _to_float(pier_cfg.get("W"), 100.0)
    road_width = max(1e-6, abs(pier_width * road_width_scale))
    mid_road_width = road_width * mid_width_scale
    mid_norm_width = base_norm_width * mid_width_scale

    down = (0.0, 0.0, -1.0)
    line_bottom_points = beam_meta.get("line_bottom_points") or []
    for station_idx, line_points in enumerate(line_bottom_points):
        road_dir = road_dirs[station_idx]
        norm_dir = norm_dirs[station_idx]
        for top_center in line_points:
            corners = _box_corners(
                top_center,
                base_norm_width,
                road_width,
                thickness,
                norm_dir,
                road_dir,
                down,
            )
            _add_box(result, "bearing", bearing_layer, corners)

            top_center = _add(top_center, (0.0, 0.0, -thickness))
            corners = _box_corners(
                top_center,
                mid_norm_width,
                mid_road_width,
                thickness * mid_thickness_scale,
                norm_dir,
                road_dir,
                down,
            )
            _add_box(result, "bearing", bearing_layer, corners)

            top_center = _add(top_center, (0.0, 0.0, -thickness * mid_thickness_scale))
            corners = _box_corners(
                top_center,
                base_norm_width,
                road_width,
                thickness,
                norm_dir,
                road_dir,
                down,
            )
            _add_box(result, "bearing", bearing_layer, corners)

    total_height = thickness * 2.0 + thickness * mid_thickness_scale
    return {
        "foundation_points": [_add(pt, (0.0, 0.0, -total_height)) for pt in beam_bottom_pts],
        "total_height": total_height,
    }


def _resolve_pier_anchor_indices(pier_cfg, n_stations):
    if n_stations <= 0:
        return []

    explicit = pier_cfg.get("anchor_indices")
    if explicit is not None:
        explicit_indices = explicit
        if not isinstance(explicit_indices, (list, tuple, set)):
            explicit_indices = [explicit_indices]
        ordered = []
        for idx in explicit_indices:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if i < 0:
                i += n_stations
            if 0 <= i < n_stations and i not in ordered:
                ordered.append(i)
        if ordered:
            return ordered

    count = max(1, _to_int(pier_cfg.get("count"), 1))
    candidates = list(range(n_stations))
    if bool(pier_cfg.get("use_internal_stations_only", True)) and n_stations > 2:
        candidates = list(range(1, n_stations - 1))
    if not candidates:
        candidates = list(range(n_stations))

    if count >= len(candidates):
        return candidates
    if count == 1:
        return [candidates[len(candidates) // 2]]

    step = float(len(candidates) - 1) / float(count - 1)
    picks = []
    for i in range(count):
        picked = candidates[int(round(i * step))]
        if picked not in picks:
            picks.append(picked)
    return picks


def _hammerhead_section(anchor_pt, road_dir, norm_dir, offset_w, H, V, hcfg):
    slope = _to_float(hcfg.get("slope_ratio"), 3.0 / (2.0 * 12.0))
    head_height_ratio = _to_float(hcfg.get("head_height_ratio"), 0.15)
    head_cap_height = _to_float(hcfg.get("head_cap_section_height"), 80.0)

    column_height = V - head_height_ratio * H
    section_base = _add(
        anchor_pt,
        _add(
            _scale(road_dir, offset_w),
            _add(_scale(norm_dir, slope * column_height + 0.2 * H), (0.0, 0.0, -V)),
        ),
    )
    pts = [section_base]
    pts.append(_add(section_base, _add(_scale(norm_dir, -slope * column_height), (0.0, 0.0, column_height))))
    pts.append(
        _add(
            section_base,
            _add(_scale(norm_dir, -(slope * column_height - 0.3 * H)), (0.0, 0.0, V - head_cap_height)),
        )
    )
    pts.append(_add(section_base, _add(_scale(norm_dir, -(slope * column_height - 0.3 * H)), (0.0, 0.0, V))))
    pts.append(_add(section_base, _add(_scale(norm_dir, -(slope * column_height + 0.7 * H)), (0.0, 0.0, V))))
    pts.append(
        _add(
            section_base,
            _add(_scale(norm_dir, -(slope * column_height + 0.7 * H)), (0.0, 0.0, V - head_cap_height)),
        )
    )
    pts.append(_add(section_base, _add(_scale(norm_dir, -(slope * column_height + 0.4 * H)), (0.0, 0.0, column_height))))
    pts.append(_add(section_base, _scale(norm_dir, -(2.0 * slope * column_height + 0.4 * H))))
    pts.append(section_base)
    return pts


def _mcol_cap_section(anchor_pt, road_dir, norm_dir, offset_w, H, cap_height, edge_offset):
    section_base = _add(anchor_pt, _scale(road_dir, offset_w))
    pts = []
    pts.append(_add(section_base, _scale(norm_dir, -0.5 * H)))
    pts.append(_add(section_base, _add(_scale(norm_dir, -0.5 * H), (0.0, 0.0, -(cap_height - FOOT_TO_UNIT)))))
    pts.append(_add(section_base, _add(_scale(norm_dir, -(0.5 * H - edge_offset)), (0.0, 0.0, -cap_height))))
    pts.append(_add(section_base, _add(_scale(norm_dir, 0.5 * H - edge_offset), (0.0, 0.0, -cap_height))))
    pts.append(_add(section_base, _add(_scale(norm_dir, 0.5 * H), (0.0, 0.0, -(cap_height - FOOT_TO_UNIT)))))
    pts.append(_add(section_base, _scale(norm_dir, 0.5 * H)))
    pts.append(_add(section_base, _scale(norm_dir, -0.5 * H)))
    return pts


def _mcol_column_sections(anchor_pt, road_dir, norm_dir, offset_w, H, V, cap_height, bottom_height, mcfg):
    slope = _to_float(mcfg.get("slope_ratio"), 1.0 / 12.0)
    col_min_width = _to_float(mcfg.get("column_min_width"), 3.0 * FOOT_TO_UNIT)
    edge_offset = _to_float(mcfg.get("edge_offset"), 7.0 * FOOT_TO_UNIT)

    section_base = _add(_add(anchor_pt, _scale(road_dir, offset_w)), (0.0, 0.0, -cap_height))
    col_H = H - 2.0 * edge_offset
    col_height = V - cap_height - bottom_height
    bottom_min_width = max(col_min_width, 0.1 * H)
    col_slope_width = slope * col_height
    base_widths = [0.5 * col_H, 0.05 * H, -0.5 * col_H + 2.0 * col_slope_width + bottom_min_width]

    sections = []
    for is_slope, base_width in zip((1.0, 0.0, 1.0), base_widths):
        sub = []
        sub.append(_add(section_base, _scale(norm_dir, base_width)))
        sub.append(_add(section_base, _scale(norm_dir, base_width - 2.0 * is_slope * col_slope_width - bottom_min_width)))
        sub.append(
            _add(
                section_base,
                _add(_scale(norm_dir, base_width - is_slope * col_slope_width - bottom_min_width), (0.0, 0.0, -col_height)),
            )
        )
        sub.append(
            _add(
                section_base,
                _add(_scale(norm_dir, base_width - is_slope * col_slope_width), (0.0, 0.0, -col_height)),
            )
        )
        sub.append(_add(section_base, _scale(norm_dir, base_width)))
        sections.append(sub)
    return sections


def _mcol_bottom_section(anchor_pt, road_dir, norm_dir, offset_w, H, V, bottom_height, edge_offset):
    section_base = _add(_add(anchor_pt, _scale(road_dir, offset_w)), (0.0, 0.0, -(V - bottom_height)))
    half = 0.5 * H - edge_offset
    pts = []
    pts.append(_add(section_base, _scale(norm_dir, -half)))
    pts.append(_add(section_base, _scale(norm_dir, half)))
    pts.append(_add(section_base, _add(_scale(norm_dir, half), (0.0, 0.0, -bottom_height))))
    pts.append(_add(section_base, _add(_scale(norm_dir, -half), (0.0, 0.0, -bottom_height))))
    pts.append(_add(section_base, _scale(norm_dir, -half)))
    return pts


def _build_single_hammerhead_pier(result, cfg, anchor_pt, road_dir, norm_dir):
    pier_layer = _layer_name(cfg, "pier")
    pier_cfg = cfg.get("pier") or {}
    hcfg = pier_cfg.get("hammerhead") or {}
    H = _to_float(pier_cfg.get("H"), 1200.0)
    V = _to_float(pier_cfg.get("V"), 500.0)
    W = _to_float(pier_cfg.get("W"), 100.0)

    section1 = _hammerhead_section(anchor_pt, road_dir, norm_dir, -W / 2.0, H, V, hcfg)
    section2 = _hammerhead_section(anchor_pt, road_dir, norm_dir, W / 2.0, H, V, hcfg)
    # Winding fix: the previous point order produced surfaces whose underlying normal (what defect
    # placement reads via rs.SurfaceNormal) pointed INWARD for every hammerhead face (confirmed 10/10
    # per pier by the normal-audit parity test) — defects grew into the pier / the camera rendered the
    # interior. Reversing each face's point order negates its planar normal, so all faces now point
    # OUTWARD. (rs.FlipSurface only toggles brep-face OrientationIsReversed, which rs.SurfaceNormal
    # ignores, so it could NOT fix this — the winding must be correct at construction.)
    _add_polygon(result, "pier", pier_layer, section1, cfg)
    _add_polygon(result, "pier", pier_layer, list(reversed(section2)), cfg)

    edge_count = min(len(section1), len(section2)) - 1
    for edge_idx in range(max(0, edge_count)):
        quad = [section2[edge_idx], section2[edge_idx + 1], section1[edge_idx + 1], section1[edge_idx]]
        _add_polygon(result, "pier", pier_layer, quad, cfg)


def _build_single_mcolumn_pier(result, cfg, anchor_pt, road_dir, norm_dir):
    pier_layer = _layer_name(cfg, "pier")
    pier_cfg = cfg.get("pier") or {}
    mcfg = pier_cfg.get("m_column") or {}
    H = _to_float(pier_cfg.get("H"), 1200.0)
    V = _to_float(pier_cfg.get("V"), 500.0)
    W = _to_float(pier_cfg.get("W"), 100.0)
    cap_height = _to_float(mcfg.get("cap_height"), 3.25 * FOOT_TO_UNIT)
    bottom_height = _to_float(mcfg.get("bottom_height"), 5.0 * FOOT_TO_UNIT)
    edge_offset = _to_float(mcfg.get("edge_offset"), 7.0 * FOOT_TO_UNIT)

    section1 = _mcol_cap_section(anchor_pt, road_dir, norm_dir, -W / 2.0, H, cap_height, edge_offset)
    section2 = _mcol_cap_section(anchor_pt, road_dir, norm_dir, W / 2.0, H, cap_height, edge_offset)
    _add_polygon(result, "pier", pier_layer, section1, cfg)
    _add_polygon(result, "pier", pier_layer, list(reversed(section2)), cfg)

    edge_count = min(len(section1), len(section2)) - 1
    for edge_idx in range(max(0, edge_count)):
        quad = [section1[edge_idx], section2[edge_idx], section2[edge_idx + 1], section1[edge_idx + 1]]
        _add_polygon(result, "pier", pier_layer, quad, cfg)

    cols1 = _mcol_column_sections(anchor_pt, road_dir, norm_dir, -W / 2.0, H, V, cap_height, bottom_height, mcfg)
    cols2 = _mcol_column_sections(anchor_pt, road_dir, norm_dir, W / 2.0, H, V, cap_height, bottom_height, mcfg)
    for col in cols1:
        _add_polygon(result, "pier", pier_layer, col, cfg)
    for col in cols2:
        _add_polygon(result, "pier", pier_layer, list(reversed(col)), cfg)

    for left_col, right_col in zip(cols1, cols2):
        for edge_idx in range(1, len(left_col) - 1):
            if edge_idx == 2:
                continue
            quad = [left_col[edge_idx], right_col[edge_idx], right_col[edge_idx + 1], left_col[edge_idx + 1]]
            _add_polygon(result, "pier", pier_layer, quad, cfg)

    bottom1 = _mcol_bottom_section(anchor_pt, road_dir, norm_dir, -W / 2.0, H, V, bottom_height, edge_offset)
    bottom2 = _mcol_bottom_section(anchor_pt, road_dir, norm_dir, W / 2.0, H, V, bottom_height, edge_offset)
    _add_polygon(result, "pier", pier_layer, list(reversed(bottom1)), cfg)
    _add_polygon(result, "pier", pier_layer, bottom2, cfg)

    edge_count = min(len(bottom1), len(bottom2)) - 1
    for edge_idx in range(max(0, edge_count)):
        quad = [bottom1[edge_idx], bottom1[edge_idx + 1], bottom2[edge_idx + 1], bottom2[edge_idx]]
        _add_polygon(result, "pier", pier_layer, quad, cfg)


def _build_piers(result, anchor_pts, road_dirs, norm_dirs, cfg):
    pier_cfg = cfg.get("pier") or {}
    if not bool(pier_cfg.get("enabled", True)):
        return []

    anchor_indices = _resolve_pier_anchor_indices(pier_cfg, len(anchor_pts))
    pier_type = str(pier_cfg.get("type", "hammerhead")).strip().lower()
    used_indices = []
    for idx in anchor_indices:
        if idx < 0 or idx >= len(anchor_pts):
            continue
        anchor_pt = anchor_pts[idx]
        road_dir = road_dirs[idx]
        norm_dir = norm_dirs[idx]
        if pier_type == "hammerhead":
            _build_single_hammerhead_pier(result, cfg, anchor_pt, road_dir, norm_dir)
        elif pier_type == "m_column":
            _build_single_mcolumn_pier(result, cfg, anchor_pt, road_dir, norm_dir)
        else:
            raise ValueError("Unsupported pier.type '{}'.".format(pier_type))
        used_indices.append(idx)
    return used_indices


def _surface_sample_point_and_uv(surface_id):
    point = None
    centroid = rs.SurfaceAreaCentroid(surface_id)
    if centroid:
        centroid_pt = centroid[0] if isinstance(centroid, (list, tuple)) else centroid
        try:
            point = _xyz(centroid_pt)
        except (TypeError, ValueError, IndexError):
            point = None

    if point is None or not rs.IsPointOnSurface(surface_id, point):
        domain_u = rs.SurfaceDomain(surface_id, 0)
        domain_v = rs.SurfaceDomain(surface_id, 1)
        if not domain_u or not domain_v:
            return None, None
        u = 0.5 * (float(domain_u[0]) + float(domain_u[1]))
        v = 0.5 * (float(domain_v[0]) + float(domain_v[1]))
        eval_pt = rs.EvaluateSurface(surface_id, u, v)
        if not eval_pt:
            return None, None
        point = _xyz(eval_pt)

    uv = rs.SurfaceClosestPoint(surface_id, point)
    if uv is None:
        return None, None
    return point, uv


def _surface_frame_axes(surface_id, uv, normal):
    frame = None
    try:
        frame = rs.SurfaceFrame(surface_id, uv)
    except Exception:
        frame = None

    if frame and hasattr(frame, "XAxis") and hasattr(frame, "YAxis"):
        x_axis = _unit(frame.XAxis, fallback=(1.0, 0.0, 0.0))
        y_axis = _unit(frame.YAxis, fallback=(0.0, 1.0, 0.0))
        return x_axis, y_axis

    ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.95 else (0.0, 1.0, 0.0)
    x_axis = _unit(_cross(ref, normal), fallback=(1.0, 0.0, 0.0))
    y_axis = _unit(_cross(normal, x_axis), fallback=(0.0, 1.0, 0.0))
    return x_axis, y_axis


def _add_surface_normal_arrows(result, debug_cfg):
    normal_cfg = (debug_cfg or {}).get("surface_normals") or {}
    if not bool(normal_cfg.get("enabled", False)):
        return

    layer_name = str(normal_cfg.get("layer") or "debug::normal::component")
    by_component = bool(normal_cfg.get("by_component", True))
    unknown_component = str(normal_cfg.get("unknown_component") or "unknown")
    length = max(1e-3, _to_float(normal_cfg.get("length"), 80.0))
    head_length_ratio = max(1e-3, _to_float(normal_cfg.get("head_length_ratio"), 0.25))
    head_width_ratio = max(1e-3, _to_float(normal_cfg.get("head_width_ratio"), 0.35))
    max_surfaces = max(0, _to_int(normal_cfg.get("max_surfaces"), 0))

    surfaces = [sid for sid in (result.get("surfaces") or []) if sid and rs.IsSurface(sid)]
    if max_surfaces > 0:
        surfaces = surfaces[:max_surfaces]

    component_lookup = {}
    if by_component:
        by_component_ids = result.get("objects_by_component") or {}
        surface_keys = {str(sid) for sid in surfaces}
        for component_name, ids in by_component_ids.items():
            if component_name.startswith("normal_arrows"):
                continue
            for obj_id in ids or []:
                key = str(obj_id)
                if key in surface_keys and key not in component_lookup:
                    component_lookup[key] = str(component_name)

    created = []
    for surface_id in surfaces:
        component_name = component_lookup.get(str(surface_id))
        component_key = component_name or unknown_component
        arrow_layer = layer_name
        if by_component:
            arrow_layer = "{}::{}".format(layer_name, component_key)

        point, uv = _surface_sample_point_and_uv(surface_id)
        if point is None or uv is None:
            continue

        normal = _unit(rs.SurfaceNormal(surface_id, uv), fallback=(0.0, 0.0, 1.0))
        x_axis, y_axis = _surface_frame_axes(surface_id, uv, normal)
        head_len = min(length * head_length_ratio, length * 0.9)
        head_width = max(1e-3, head_len * head_width_ratio)

        shaft_end = _add(point, _scale(normal, length - head_len))
        tip = _add(point, _scale(normal, length))
        arrow_ids = []

        shaft_id = rs.AddLine(point, shaft_end)
        if shaft_id:
            arrow_ids.append(shaft_id)

        for axis in (x_axis, _scale(x_axis, -1.0), y_axis, _scale(y_axis, -1.0)):
            wing_end = _add(shaft_end, _scale(axis, head_width))
            wing_id = rs.AddLine(tip, wing_end)
            if wing_id:
                arrow_ids.append(wing_id)

        for obj_id in arrow_ids:
            _assign_layer(obj_id, arrow_layer)
            _append_component_object(result, "normal_arrows::{}".format(component_key), obj_id)
        created.extend(arrow_ids)
        result["normal_arrows_by_component"].setdefault(component_key, []).extend(arrow_ids)

    result["normal_arrows"].extend(created)


def create_bridge_component(params=None, debug_cfg=None):
    """Create bridge components using a configurable version of tmp.py logic.

    Args:
        params (dict): Optional component-modeling parameters.

    Returns:
        dict: Created component geometry ids and metadata.
    """
    cfg = params or {}
    debug_cfg = debug_cfg or {}
    seed = cfg.get("seed")
    rng = random.Random() if seed is None else random.Random(_to_int(seed, 0))

    result = {
        "config": cfg,
        "centerline_id": None,
        "centerline_theta_deg": None,
        "base_points": [],
        "road_dirs": [],
        "norm_dirs": [],
        "objects_by_component": {},
        "surfaces": [],
        "polylines": [],
        "solids": [],
        "normal_arrows": [],
        "normal_arrows_by_component": {},
        "pier_anchor_indices": [],
    }

    centerline_id, base_pts, road_dirs, norm_dirs, theta_deg = _build_centerline(
        cfg.get("centerline") or {},
        rng=rng,
    )
    result["centerline_id"] = centerline_id
    result["centerline_theta_deg"] = theta_deg
    result["base_points"] = base_pts
    result["road_dirs"] = road_dirs
    result["norm_dirs"] = norm_dirs

    slab_meta = _build_slab_and_parapet(result, base_pts, norm_dirs, cfg)
    beam_meta = _build_beams(
        result,
        slab_meta["deck_bottom_points"],
        norm_dirs,
        slab_meta["width"],
        cfg,
    )
    bearing_meta = _build_bearings(result, beam_meta, road_dirs, norm_dirs, cfg)
    pier_anchors = _build_piers(
        result,
        bearing_meta["foundation_points"] or beam_meta.get("bottom_points") or slab_meta["deck_bottom_points"],
        road_dirs,
        norm_dirs,
        cfg,
    )
    result["pier_anchor_indices"] = pier_anchors

    if bool(cfg.get("delete_centerline_curve", True)) and centerline_id and rs.IsObject(centerline_id):
        rs.DeleteObject(centerline_id)
        result["centerline_id"] = None

    _add_surface_normal_arrows(result, debug_cfg)
    return result
