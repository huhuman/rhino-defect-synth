"""Shared polygon-shape parsing utilities for defect placement/modeling."""

import glob
import json
import os


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_point2d(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    raise ValueError("Each point must be a 2-item [x, y] sequence.")


def _normalize_points(points):
    normalized = []
    for point in points or []:
        normalized.append(_as_point2d(point))
    return normalized


def _normalize_contour_item(item):
    return {
        "parent": int(item.get("parent", -1)),
        "points": _normalize_points(item.get("points") or []),
    }


def _center_points(points, width_mm, height_mm):
    cx = float(width_mm) * 0.5
    cy = float(height_mm) * 0.5
    return [(float(x) - cx, float(y) - cy) for x, y in points]


def _shape_radius(shape):
    candidates = []
    for key in ("offset_poly", "base_poly"):
        candidates.extend(shape.get(key) or [])
    if not candidates:
        for points in shape.get("crack_polys") or []:
            candidates.extend(points)
    if not candidates:
        return 0.0
    return max((x * x + y * y) ** 0.5 for x, y in candidates)


def read_cube_contour_json(filepath):
    """Read cube-style contour JSON and convert pixels into millimeter units."""
    if not os.path.isfile(filepath):
        raise IOError("File not found: {}".format(filepath))

    with open(filepath, "r") as handle:
        data = json.load(handle)

    if "pixel_size_cm" not in data:
        raise KeyError('JSON must contain "pixel_size_cm".')

    pixel_size_mm = _as_float(data["pixel_size_cm"]) * 10.0
    width_px = _as_float(data.get("width_px"), 0.0)
    height_px = _as_float(data.get("height_px"), 0.0)
    if width_px <= 0 and height_px <= 0:
        raise KeyError('JSON must contain valid "width_px" or "height_px".')
    if width_px <= 0:
        width_px = height_px
    if height_px <= 0:
        height_px = width_px

    width_mm = width_px * pixel_size_mm
    height_mm = height_px * pixel_size_mm

    def to_mm_points(points):
        return [(float(x) * pixel_size_mm, float(y) * pixel_size_mm) for x, y in points]

    raw_contours = data.get("contours")
    if raw_contours is None:
        raise KeyError('JSON must contain "contours".')

    contours = []
    for contour_group in raw_contours:
        converted_group = []
        for item in contour_group:
            parsed = _normalize_contour_item(item)
            parsed["points"] = to_mm_points(parsed["points"])
            converted_group.append(parsed)
        contours.append(converted_group)

    expanded = []
    for item in data.get("expanded_contours") or []:
        parsed = _normalize_contour_item(item)
        parsed["points"] = to_mm_points(parsed["points"])
        expanded.append(parsed)

    base = []
    for item in data.get("base_contours") or []:
        parsed = _normalize_contour_item(item)
        parsed["points"] = to_mm_points(parsed["points"])
        base.append(parsed)

    diffs = []
    for diff_group in data.get("difference_contours") or []:
        converted_group = []
        for item in diff_group:
            parsed = _normalize_contour_item(item)
            parsed["points"] = to_mm_points(parsed["points"])
            converted_group.append(parsed)
        diffs.append(converted_group)

    severities = list(data.get("severities") or [])

    return {
        "contours": contours,
        "expanded_contours": expanded,
        "base_contours": base,
        "difference_contours": diffs,
        "severities": severities,
        "pixel_size_mm": pixel_size_mm,
        "width_mm": width_mm,
        "height_mm": height_mm,
    }


def iter_cube_shape_templates(filepath):
    """Flatten one cube contour JSON file into per-defect shape templates."""
    parsed = read_cube_contour_json(filepath)
    contours = parsed["contours"]
    expanded = parsed["expanded_contours"]
    base = parsed["base_contours"]
    diffs = parsed["difference_contours"]
    severities = parsed["severities"]
    width_mm = parsed["width_mm"]
    height_mm = parsed["height_mm"]

    n_items = min(len(contours), len(expanded), len(base), len(diffs), len(severities))
    templates = []
    for idx in range(n_items):
        contour_group = contours[idx]
        crack_polys = []
        inside_polys = []
        for contour in contour_group:
            centered = _center_points(contour["points"], width_mm, height_mm)
            if contour.get("parent", -1) == -1:
                crack_polys.append(centered)
            else:
                inside_polys.append(centered)

        template = {
            "source_file": os.path.abspath(filepath),
            "source_index": idx,
            "severity": severities[idx],
            "offset_poly": _center_points(expanded[idx]["points"], width_mm, height_mm),
            "base_poly": _center_points(base[idx]["points"], width_mm, height_mm),
            "crack_polys": crack_polys,
            "inside_polys": inside_polys,
            "diff_polys": [
                _center_points(item["points"], width_mm, height_mm)
                for item in diffs[idx]
            ],
            "width_mm": width_mm,
            "height_mm": height_mm,
        }
        template["shape_radius"] = _shape_radius(template)
        templates.append(template)
    return templates


def _simple_shape_template(data, source_file=None, source_index=0):
    if isinstance(data, dict):
        offset_poly = _normalize_points(data.get("offset_poly") or data.get("points") or [])
        base_poly = _normalize_points(data.get("base_poly") or offset_poly)
        crack_polys_raw = data.get("crack_polys")
        if crack_polys_raw:
            crack_polys = [_normalize_points(points) for points in crack_polys_raw]
        else:
            crack_polys = [base_poly] if base_poly else []
        inside_polys = [_normalize_points(points) for points in data.get("inside_polys") or []]
        diff_polys = [_normalize_points(points) for points in data.get("diff_polys") or []]
        severity = data.get("severity")
    else:
        offset_poly = _normalize_points(data)
        base_poly = list(offset_poly)
        crack_polys = [list(base_poly)] if base_poly else []
        inside_polys = []
        diff_polys = []
        severity = None

    template = {
        "source_file": os.path.abspath(source_file) if source_file else None,
        "source_index": int(source_index),
        "severity": severity,
        "offset_poly": offset_poly,
        "base_poly": base_poly,
        "crack_polys": crack_polys,
        "inside_polys": inside_polys,
        "diff_polys": diff_polys,
        "width_mm": None,
        "height_mm": None,
    }
    template["shape_radius"] = _shape_radius(template)
    return template


def iter_simple_shape_templates(filepath):
    """Read simple polygon JSON and emit templates with unified keys."""
    if not os.path.isfile(filepath):
        raise IOError("File not found: {}".format(filepath))
    with open(filepath, "r") as handle:
        data = json.load(handle)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        return [
            _simple_shape_template(item, source_file=filepath, source_index=idx)
            for idx, item in enumerate(data)
        ]

    if isinstance(data, dict) and isinstance(data.get("templates"), list):
        return [
            _simple_shape_template(item, source_file=filepath, source_index=idx)
            for idx, item in enumerate(data["templates"])
        ]

    return [_simple_shape_template(data, source_file=filepath, source_index=0)]


def _glob_json_files(shape_dir, recursive=True, pattern="*.json"):
    base_dir = os.path.abspath(shape_dir)
    if not os.path.isdir(base_dir):
        raise IOError("shape_dir does not exist: '{}'".format(base_dir))
    search_pattern = os.path.join(base_dir, "**", pattern) if recursive else os.path.join(base_dir, pattern)
    files = glob.glob(search_pattern, recursive=bool(recursive))
    files = [path for path in files if path.lower().endswith(".json")]
    return sorted(set(os.path.abspath(path) for path in files))


def load_shape_templates(paths=None, shape_dir=None, recursive=True, pattern="*.json", file_format="auto"):
    """Load polygon templates in a format shared by all defect builders."""
    filepaths = []
    for path in paths or []:
        if not path:
            continue
        filepaths.append(os.path.abspath(path))
    if shape_dir:
        filepaths.extend(_glob_json_files(shape_dir, recursive=recursive, pattern=pattern))
    filepaths = sorted(set(filepaths))

    templates = []
    for filepath in filepaths:
        if not os.path.isfile(filepath):
            continue

        selected_format = str(file_format or "auto").strip().lower()
        if selected_format not in ("auto", "cube", "simple"):
            raise ValueError("Unsupported shape file_format '{}'.".format(file_format))

        if selected_format in ("auto", "cube"):
            try:
                cube_items = iter_cube_shape_templates(filepath)
            except Exception:
                cube_items = None
            if cube_items:
                templates.extend(cube_items)
                continue
            if selected_format == "cube":
                raise ValueError("Failed to parse '{}' as cube contour JSON.".format(filepath))

        templates.extend(iter_simple_shape_templates(filepath))

    if not templates:
        raise ValueError("No shape templates were loaded from the provided paths.")
    return templates
