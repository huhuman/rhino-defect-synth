
"""Helpers for defect placement references and component-surface preparation."""

import json
import math
import os

import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
from utils_loc.defect_shapes import extract_point_sets


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (str, bytes, dict)):
        return [value]
    if hasattr(value, "X") and hasattr(value, "Y"):
        return [value]
    try:
        return list(value)
    except TypeError:
        pass
    return [value]


def _xyz(value):
    if value is None:
        return 0.0, 0.0, 0.0
    if hasattr(value, "X") and hasattr(value, "Y"):
        return float(value.X), float(value.Y), float(getattr(value, "Z", 0.0))
    if len(value) == 2:
        return float(value[0]), float(value[1]), 0.0
    return float(value[0]), float(value[1]), float(value[2])


def _distance(a, b):
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    dx = ax - bx
    dy = ay - by
    dz = az - bz
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _unit(v, fallback=(0.0, 0.0, 1.0)):
    vx, vy, vz = _xyz(v)
    norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if norm <= 1e-12:
        fx, fy, fz = _xyz(fallback)
        f_norm = math.sqrt(fx * fx + fy * fy + fz * fz)
        if f_norm <= 1e-12:
            return 0.0, 0.0, 1.0
        return fx / f_norm, fy / f_norm, fz / f_norm
    inv = 1.0 / norm
    return vx * inv, vy * inv, vz * inv


def _dedupe_ids(ids):
    ordered = []
    seen = set()
    for obj_id in ids:
        if not obj_id or obj_id in seen:
            continue
        seen.add(obj_id)
        ordered.append(obj_id)
    return ordered


def _is_valid_object_id(obj_id):
    if not obj_id:
        return False
    if str(obj_id) == "00000000-0000-0000-0000-000000000000":
        return False
    try:
        return bool(rs.IsObject(obj_id))
    except Exception:
        return False


def _normalize_layer_names(layer_names):
    if not layer_names:
        return None
    return {str(name) for name in _as_list(layer_names) if str(name).strip()}


def _object_in_layers(obj_id, layer_name_set):
    if layer_name_set is None:
        return True
    layer_name = rs.ObjectLayer(obj_id)
    return layer_name in layer_name_set


def _curve_to_surface_ids(curve_id):
    if not curve_id or not rs.IsCurve(curve_id) or not rs.IsCurveClosed(curve_id):
        return []

    surface_ids = rs.AddPlanarSrf(curve_id) or []
    if surface_ids:
        return list(surface_ids)

    pts = rs.CurvePoints(curve_id) or []
    vertices = []
    for pt in pts:
        p = _xyz(pt)
        if not vertices or _distance(vertices[-1], p) > 1e-6:
            vertices.append(p)
    if len(vertices) > 1 and _distance(vertices[0], vertices[-1]) <= 1e-6:
        vertices.pop()

    if len(vertices) == 4:
        srf_id = rs.AddSrfPt(vertices)
        if srf_id:
            return [srf_id]
    return []


def _surface_like_area(obj_id):
    if not obj_id or not rs.IsObject(obj_id):
        return 0.0

    try:
        area_data = rs.SurfaceArea(obj_id)
    except Exception:
        area_data = None

    if isinstance(area_data, (list, tuple)) and area_data:
        try:
            area = float(area_data[0])
        except (TypeError, ValueError):
            area = 0.0
        if area > 0.0:
            return area
    elif isinstance(area_data, (int, float)):
        area = float(area_data)
        if area > 0.0:
            return area

    bbox = rs.BoundingBox(obj_id)
    if not bbox:
        return 0.0

    xs = [pt.X for pt in bbox]
    ys = [pt.Y for pt in bbox]
    zs = [pt.Z for pt in bbox]
    lengths = sorted(
        [
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ],
        reverse=True,
    )
    if len(lengths) >= 2:
        return max(0.0, float(lengths[0]) * float(lengths[1]))
    if lengths:
        return max(0.0, float(lengths[0]))
    return 0.0


def _keep_largest_piece(piece_ids):
    valid_ids = [obj_id for obj_id in _dedupe_ids(_as_list(piece_ids)) if obj_id and rs.IsObject(obj_id)]
    if not valid_ids:
        return None

    best_id = valid_ids[0]
    best_area = _surface_like_area(best_id)
    for obj_id in valid_ids[1:]:
        area = _surface_like_area(obj_id)
        if area > best_area:
            best_id = obj_id
            best_area = area
    return best_id


def _brep_representative_point(brep):
    if brep is None:
        return None

    amp = None
    try:
        amp = Rhino.Geometry.AreaMassProperties.Compute(brep)
        if amp:
            return amp.Centroid
    except Exception:
        pass
    finally:
        if amp:
            dispose = getattr(amp, "Dispose", None)
            if dispose:
                dispose()

    try:
        bbox = brep.GetBoundingBox(True)
    except Exception:
        bbox = None
    if bbox and bbox.IsValid:
        return bbox.Center
    return None


def _surface_point_distance(obj_id, point):
    if not _is_valid_object_id(obj_id) or point is None or not rs.IsSurface(obj_id):
        return None
    try:
        uv = rs.SurfaceClosestPoint(obj_id, point)
    except Exception:
        uv = None
    if not uv:
        return None
    try:
        surface_point = rs.EvaluateSurface(obj_id, uv[0], uv[1])
    except Exception:
        surface_point = None
    if surface_point is None:
        return None
    return _distance(surface_point, point)


def _object_representative_point(obj_id):
    if not _is_valid_object_id(obj_id):
        return None

    bbox = rs.BoundingBox(obj_id)
    bbox_center = None
    if bbox:
        try:
            bbox_center = (
                sum(pt.X for pt in bbox) / float(len(bbox)),
                sum(pt.Y for pt in bbox) / float(len(bbox)),
                sum(pt.Z for pt in bbox) / float(len(bbox)),
            )
        except Exception:
            bbox_center = None

    if rs.IsSurface(obj_id):
        sample = bbox_center or _brep_representative_point(rs.coercebrep(obj_id))
        if sample is not None:
            try:
                uv = rs.SurfaceClosestPoint(obj_id, sample)
            except Exception:
                uv = None
            if uv:
                try:
                    point = rs.EvaluateSurface(obj_id, uv[0], uv[1])
                except Exception:
                    point = None
                if point is not None:
                    return point

    return _brep_representative_point(rs.coercebrep(obj_id))


def _point_inside_solid_brep(brep, point, tolerance):
    if brep is None or point is None or not getattr(brep, "IsSolid", False):
        return False
    try:
        return bool(brep.IsPointInside(point, float(tolerance), False))
    except Exception:
        return False


def _filter_split_breps_keep_outer(split_breps, cutter_breps, tolerance):
    split_breps = [brep for brep in _as_list(split_breps) if brep is not None]
    solid_cutters = [brep for brep in _as_list(cutter_breps) if brep is not None and getattr(brep, "IsSolid", False)]
    if not split_breps:
        return []
    if not solid_cutters:
        return None

    kept = []
    for brep in split_breps:
        point = _brep_representative_point(brep)
        if point is None:
            kept.append(brep)
            continue
        if any(_point_inside_solid_brep(cutter_brep, point, tolerance) for cutter_brep in solid_cutters):
            continue
        kept.append(brep)
    return kept


def _discard_piece_ids_from_points(piece_ids, discard_points, tolerance):
    valid_ids = [obj_id for obj_id in _dedupe_ids(_as_list(piece_ids)) if obj_id and rs.IsObject(obj_id)]
    points = [_xyz(point) for point in _as_list(discard_points) if point is not None]
    if len(valid_ids) <= 1 or not points:
        return []

    area_by_id = dict((obj_id, _surface_like_area(obj_id)) for obj_id in valid_ids)
    largest_area = max(area_by_id.values()) if area_by_id else 0.0
    tolerance_limit = max(float(tolerance) * 10.0, 1e-4)
    discard_ids = []
    for point in points:
        best_id = None
        best_score = None
        for obj_id in valid_ids:
            distance = _surface_point_distance(obj_id, point)
            if distance is None:
                continue
            try:
                on_surface = bool(rs.IsPointOnSurface(obj_id, point))
            except Exception:
                on_surface = False
            score = (
                1 if on_surface else 0,
                -float(distance),
                -_surface_like_area(obj_id),
            )
            if best_score is None or score > best_score:
                best_id = obj_id
                best_score = score
        if best_id is None:
            continue
        best_distance = -best_score[1]
        best_area = area_by_id.get(best_id, 0.0)
        if best_distance <= tolerance_limit and best_area < largest_area:
            discard_ids.append(best_id)
    return _dedupe_ids(discard_ids)


def _filter_split_piece_ids_keep_outer(piece_ids, cutter_ids, tolerance, discard_points=None):
    valid_ids = [obj_id for obj_id in _dedupe_ids(_as_list(piece_ids)) if obj_id and rs.IsObject(obj_id)]
    if not valid_ids:
        return []

    discard_ids = _discard_piece_ids_from_points(valid_ids, discard_points, tolerance)
    if discard_ids:
        return [obj_id for obj_id in valid_ids if obj_id not in set(discard_ids)]

    solid_cutters = []
    for cutter_id in _dedupe_ids(_as_list(cutter_ids)):
        cutter_brep = rs.coercebrep(cutter_id)
        if cutter_brep is not None and getattr(cutter_brep, "IsSolid", False):
            solid_cutters.append(cutter_brep)

    if solid_cutters:
        kept = []
        for obj_id in valid_ids:
            point = _object_representative_point(obj_id)
            if point is None or not any(_point_inside_solid_brep(cutter_brep, point, tolerance) for cutter_brep in solid_cutters):
                kept.append(obj_id)
        if len(kept) < len(valid_ids):
            return kept  # inside-cutter test discarded the crack/cavity piece

    # Thin-sliver fallback: a thin crack cutter is too narrow for the point/inside tests to
    # identify the sliver, so NOTHING gets discarded -> the surface is split into two pieces that
    # both stay -> no opening -> the groove is buried (blank). When discard_points were requested
    # (crack "strip" / spalling "cavity") and the split made exactly two pieces with one a clear
    # MINORITY, discard that small piece so the slit actually opens. The 0.35 cap means a near-
    # equal split (which would be a blown face) is left intact (B1 guard also catches that).
    if discard_points and len(valid_ids) == 2:
        ranked = sorted(valid_ids, key=_surface_like_area)
        small_a = _surface_like_area(ranked[0])
        total = small_a + _surface_like_area(ranked[1])
        if total > 0.0 and small_a <= 0.35 * total:
            return [ranked[1]]  # keep the large piece, drop the thin crack sliver -> open slit

    if not solid_cutters:
        return None
    return valid_ids


def _split_surface_keep_outer(target_id, cutter_ids, delete_input=False, discard_points=None):
    if not target_id or not rs.IsObject(target_id) or not rs.IsSurface(target_id):
        return None

    cutter_ids = [cid for cid in _dedupe_ids(_as_list(cutter_ids)) if cid and rs.IsObject(cid)]
    if not cutter_ids:
        return None

    target_brep = rs.coercebrep(target_id)
    if not target_brep:
        return None

    cutter_breps = []
    for cutter_id in cutter_ids:
        cutter_brep = rs.coercebrep(cutter_id)
        if cutter_brep:
            cutter_breps.append(cutter_brep)
    if not cutter_breps:
        return None

    tolerance = getattr(sc.doc, "ModelAbsoluteTolerance", None)
    if tolerance is None:
        tolerance = 0.01

    try:
        split_breps = target_brep.Split(cutter_breps, float(tolerance))
    except Exception:
        split_breps = None
    if not split_breps:
        return None
    split_breps = [brep for brep in _as_list(split_breps) if isinstance(brep, Rhino.Geometry.Brep)]
    if not split_breps:
        return None

    split_ids = []
    layer_name = rs.ObjectLayer(target_id)
    for brep in split_breps:
        new_id = sc.doc.Objects.AddBrep(brep)
        if not _is_valid_object_id(new_id):
            continue
        if layer_name and rs.IsLayer(layer_name):
            rs.ObjectLayer(new_id, layer_name)
        split_ids.append(new_id)

    if not split_ids:
        return None

    kept_ids = _filter_split_piece_ids_keep_outer(
        split_ids,
        cutter_ids,
        tolerance,
        discard_points=discard_points,
    )
    if kept_ids is None:
        keep_id = _keep_largest_piece(split_ids)
        kept_ids = [keep_id] if keep_id and rs.IsObject(keep_id) else []
    discard_ids = [sid for sid in split_ids if sid not in kept_ids and rs.IsObject(sid)]
    if discard_ids:
        rs.DeleteObjects(discard_ids)

    if not kept_ids:
        return None

    # Runaway-split guard: a defect cut should remove ~its own cut area. When the cut
    # polygon crosses the host face edge (e.g. a long crack on a small flange), Split divides the
    # face into large pieces and keep-outer/keep-largest then discards a big chunk -> a hole in
    # the surface ("blown" face). If the removed area is far larger than the cutter area,
    # revert to the uncut original (the defect's own groove/cavity geometry is separate, so the
    # defect still renders; we just don't punch a hole in the host).
    try:
        amp_o = Rhino.Geometry.AreaMassProperties.Compute(target_brep)
        orig_area = float(amp_o.Area) if amp_o else 0.0
        kept_area = 0.0
        for kid in kept_ids:
            kb = rs.coercebrep(kid)
            if kb:
                amp_k = Rhino.Geometry.AreaMassProperties.Compute(kb)
                if amp_k:
                    kept_area += abs(float(amp_k.Area))
        cutter_area = 0.0
        for cb in cutter_breps:
            amp_c = Rhino.Geometry.AreaMassProperties.Compute(cb)
            if amp_c:
                cutter_area += abs(float(amp_c.Area))
        removed = orig_area - kept_area
        if orig_area > 0.0 and removed > max(cutter_area * 5.0, 1.0):
            rs.DeleteObjects([kid for kid in kept_ids if rs.IsObject(kid)])
            print(
                "subtract_surface: reverted host cut (removed {:.0f} cm2 >> cutter {:.0f} cm2; "
                "cut polygon likely crosses the face edge) - host face kept intact.".format(
                    removed, cutter_area
                )
            )
            return [target_id]
    except Exception:
        pass

    if delete_input and rs.IsObject(target_id):
        rs.DeleteObject(target_id)

    return kept_ids


def _sample_domain(domain, sample_count, margin):
    start = float(domain[0])
    end = float(domain[1])
    if sample_count <= 1:
        return [(start + end) * 0.5]

    margin = max(0.0, min(0.45, float(margin)))
    lo = start + (end - start) * margin
    hi = end - (end - start) * margin
    if lo > hi:
        mid = (start + end) * 0.5
        return [mid] * sample_count

    step = (hi - lo) / float(sample_count - 1)
    return [lo + idx * step for idx in range(sample_count)]


def _surface_domain_midpoint(domain):
    return 0.5 * (float(domain[0]) + float(domain[1]))


def _domain_param_from_distance(domain, total_length, distance):
    start = float(domain[0])
    end = float(domain[1])
    if total_length <= 1e-9:
        return _surface_domain_midpoint(domain)
    ratio = max(0.0, min(1.0, float(distance) / float(total_length)))
    return start + (end - start) * ratio


def _resolve_axis_sampling(domain, length, edge_length, fallback_count, center_param, boundary_margin_ratio=0.0):
    count = max(1, int(fallback_count))
    edge = None if edge_length is None else float(edge_length)
    if edge is None or edge <= 0.0:
        values = _sample_domain(domain, count, 0.0)
        return {
            "values": values,
            "sample_count": len(values),
            "centered": False,
            "insufficient": False,
            "boundary_distance": 0.0,
            "sample_margin": 0.0,
        }

    boundary_distance = max(0.0, edge * max(0.0, float(boundary_margin_ratio or 0.0)))
    usable_length = float(length) - 2.0 * boundary_distance
    if usable_length + 1e-9 < edge:
        return {
            "values": [float(center_param)],
            "sample_count": 1,
            "centered": True,
            "insufficient": True,
            "boundary_distance": boundary_distance,
            "sample_margin": 0.0 if length <= 1e-9 else boundary_distance / float(length),
        }

    cell_count = max(1, int(math.floor((usable_length + 1e-9) / edge)))
    occupied_length = float(cell_count) * edge
    leftover = max(0.0, usable_length - occupied_length)
    first_center_distance = boundary_distance + 0.5 * leftover + 0.5 * edge
    values = [
        _domain_param_from_distance(domain, length, first_center_distance + edge * float(idx))
        for idx in range(cell_count)
    ]
    return {
        "values": values,
        "sample_count": len(values),
        "centered": False,
        "insufficient": False,
        "boundary_distance": boundary_distance,
        "sample_margin": 0.0 if length <= 1e-9 else boundary_distance / float(length),
    }


def _surface_curve_length(surface_id, direction, cross_param, sample_steps=24):
    sample_steps = max(4, int(sample_steps))
    domain = rs.SurfaceDomain(surface_id, 0 if int(direction) == 0 else 1)
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
            point = rs.EvaluateSurface(surface_id, param, cross_param)
        else:
            point = rs.EvaluateSurface(surface_id, cross_param, param)
        if not point:
            continue
        point = _xyz(point)
        if prev is not None:
            total += _distance(prev, point)
        prev = point
    return total if total > 1e-9 else None


def _surface_uv_lengths(surface_id, domain_u, domain_v):
    if not surface_id or not rs.IsObject(surface_id):
        return 0.0, 0.0

    u_cross_values = [
        float(domain_v[0]) + (float(domain_v[1]) - float(domain_v[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]
    v_cross_values = [
        float(domain_u[0]) + (float(domain_u[1]) - float(domain_u[0])) * ratio
        for ratio in (0.2, 0.5, 0.8)
    ]

    u_lengths = [
        length for length in (_surface_curve_length(surface_id, 0, value) for value in u_cross_values)
        if length is not None
    ]
    v_lengths = [
        length for length in (_surface_curve_length(surface_id, 1, value) for value in v_cross_values)
        if length is not None
    ]
    if u_lengths and v_lengths:
        return sum(u_lengths) / float(len(u_lengths)), sum(v_lengths) / float(len(v_lengths))

    bbox = rs.BoundingBox(surface_id)
    if not bbox:
        return 0.0, 0.0
    xs = [pt.X for pt in bbox]
    ys = [pt.Y for pt in bbox]
    zs = [pt.Z for pt in bbox]
    lengths = sorted(
        [
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ],
        reverse=True,
    )
    if len(lengths) >= 2:
        return float(lengths[0]), float(lengths[1])
    if lengths:
        return float(lengths[0]), float(lengths[0])
    return 0.0, 0.0


def _resolve_surface_sampling(
    surface_id,
    domain_u,
    domain_v,
    sample_count_u,
    sample_count_v,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    boundary_margin_ratio_u=0.0,
    boundary_margin_ratio_v=0.0,
):
    center_uv = (_surface_domain_midpoint(domain_u), _surface_domain_midpoint(domain_v))
    length_u, length_v = _surface_uv_lengths(surface_id, domain_u, domain_v)
    u_sampling = _resolve_axis_sampling(
        domain_u,
        length_u,
        sample_edge_length_u,
        sample_count_u,
        center_uv[0],
        boundary_margin_ratio=boundary_margin_ratio_u,
    )
    v_sampling = _resolve_axis_sampling(
        domain_v,
        length_v,
        sample_edge_length_v,
        sample_count_v,
        center_uv[1],
        boundary_margin_ratio=boundary_margin_ratio_v,
    )
    both_centered = bool(u_sampling["insufficient"] and v_sampling["insufficient"])
    one_centered = bool(u_sampling["centered"] or v_sampling["centered"])
    return {
        "sample_count_u": int(u_sampling["sample_count"]),
        "sample_count_v": int(v_sampling["sample_count"]),
        "u_values": list(u_sampling["values"]),
        "v_values": list(v_sampling["values"]),
        "center_uv": center_uv,
        "center_u": bool(u_sampling["centered"]),
        "center_v": bool(v_sampling["centered"]),
        "sample_margin_u": float(u_sampling["sample_margin"]),
        "sample_margin_v": float(v_sampling["sample_margin"]),
        "skip_checks": both_centered,
        "sampling_mode": (
            "center_fallback" if both_centered else
            "axis_centered_grid" if one_centered else
            "edge_grid"
        ),
    }


def _size_hint_for_surface(surface_id, sample_count):
    area_data = rs.SurfaceArea(surface_id)
    area = 0.0
    if isinstance(area_data, (list, tuple)) and area_data:
        area = float(area_data[0])
    elif isinstance(area_data, (int, float)):
        area = float(area_data)
    if area > 0:
        return max(1e-3, math.sqrt(area / max(1, sample_count)))

    bbox = rs.BoundingBox(surface_id)
    if not bbox:
        return 1.0
    xs = [pt.X for pt in bbox]
    ys = [pt.Y for pt in bbox]
    zs = [pt.Z for pt in bbox]
    lengths = sorted(
        [
            max(xs) - min(xs),
            max(ys) - min(ys),
            max(zs) - min(zs),
        ],
        reverse=True,
    )
    return max(1e-3, lengths[1] if len(lengths) > 1 else lengths[0])


def _reference_size_hint(
    surface_id,
    sample_count,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    sampling_mode=None,
):
    edge_lengths = []
    for value in (sample_edge_length_u, sample_edge_length_v):
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 1e-9:
            edge_lengths.append(numeric)

    if edge_lengths:
        return max(1e-3, min(edge_lengths))

    if sampling_mode == "center_fallback":
        domain_u = rs.SurfaceDomain(surface_id, 0)
        domain_v = rs.SurfaceDomain(surface_id, 1)
        if domain_u and domain_v:
            length_u, length_v = _surface_uv_lengths(surface_id, domain_u, domain_v)
            lengths = [length for length in (length_u, length_v) if length > 1e-9]
            if lengths:
                return max(1e-3, min(lengths))

    return _size_hint_for_surface(surface_id, sample_count)


def get_reference_points(
    surface,
    sample_count_u=3,
    sample_count_v=3,
    sample_edge_length_u=None,
    sample_edge_length_v=None,
    boundary_margin_ratio_u=0.0,
    boundary_margin_ratio_v=0.0,
    trim_margin=0.0,
    return_normals=False,
    return_metadata=False,
):
    """Sample candidate defect reference points on one surface.

    Args:
        surface: Rhino surface object id.
        sample_count_u: Number of samples along U domain.
        sample_count_v: Number of samples along V domain.
        sample_edge_length_u: Target surface sampling edge length along U in model units.
        sample_edge_length_v: Target surface sampling edge length along V in model units.
        boundary_margin_ratio_u: Inner sampling margin ratio relative to `sample_edge_length_u`.
        boundary_margin_ratio_v: Inner sampling margin ratio relative to `sample_edge_length_v`.
        trim_margin: Deprecated and ignored.
        return_normals: When True, also return sampled normals.
        return_metadata: When True, also return per-point metadata.

    Returns:
        Tuple of (points, sizes), (points, sizes, normals), or with metadata appended.
    """
    if not surface or not rs.IsObject(surface):
        if return_normals and return_metadata:
            return [], [], [], []
        if return_normals:
            return [], [], []
        if return_metadata:
            return [], [], []
        return [], []

    cleanup_ids = []
    if rs.IsPolysurface(surface):
        exploded = rs.ExplodePolysurfaces(surface, delete_input=False) or []
        if not exploded:
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []
        surface = exploded[0]
        cleanup_ids = exploded

    try:
        if not rs.IsSurface(surface):
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []

        domain_u = rs.SurfaceDomain(surface, 0)
        domain_v = rs.SurfaceDomain(surface, 1)
        if not domain_u or not domain_v:
            if return_normals and return_metadata:
                return [], [], [], []
            if return_normals:
                return [], [], []
            if return_metadata:
                return [], [], []
            return [], []

        sampling = _resolve_surface_sampling(
            surface,
            domain_u,
            domain_v,
            sample_count_u,
            sample_count_v,
            sample_edge_length_u=sample_edge_length_u,
            sample_edge_length_v=sample_edge_length_v,
            boundary_margin_ratio_u=boundary_margin_ratio_u,
            boundary_margin_ratio_v=boundary_margin_ratio_v,
        )
        u_values = list(sampling.get("u_values") or [sampling["center_uv"][0]])
        v_values = list(sampling.get("v_values") or [sampling["center_uv"][1]])

        points = []
        normals = []
        metadata = []
        size_hint = _reference_size_hint(
            surface,
            max(1, len(u_values) * len(v_values)),
            sample_edge_length_u=sample_edge_length_u,
            sample_edge_length_v=sample_edge_length_v,
            sampling_mode=sampling.get("sampling_mode"),
        )
        sizes = []

        for u in u_values:
            for v in v_values:
                point = rs.EvaluateSurface(surface, u, v)
                if not point:
                    continue
                point = _xyz(point)
                if not sampling["skip_checks"] and not rs.IsPointOnSurface(surface, point):
                    continue
                normal = rs.SurfaceNormal(surface, (u, v))
                normal = _unit(normal, fallback=(0.0, 0.0, 1.0))

                points.append(point)
                sizes.append(size_hint)
                normals.append(normal)
                metadata.append(
                    {
                        "uv": (float(u), float(v)),
                        "skip_checks": bool(sampling["skip_checks"]),
                        "sampling_mode": sampling["sampling_mode"],
                        "center_u": bool(sampling["center_u"]),
                        "center_v": bool(sampling["center_v"]),
                        "sample_margin_u": float(sampling.get("sample_margin_u", 0.0)),
                        "sample_margin_v": float(sampling.get("sample_margin_v", 0.0)),
                    }
                )
    finally:
        for obj_id in cleanup_ids:
            if obj_id and rs.IsObject(obj_id):
                rs.DeleteObject(obj_id)

    if return_normals and return_metadata:
        return points, sizes, normals, metadata
    if return_normals:
        return points, sizes, normals
    if return_metadata:
        return points, sizes, metadata
    return points, sizes


def get_surfaces(
    object_ids=None,
    layer_names=None,
    convert_polylines=True,
    explode_polysurfaces=False,
    keep_input=True,
):
    """Collect surface-like geometry suitable for defect placement.

    Args:
        object_ids: Optional object ids to inspect. Defaults to all doc objects.
        layer_names: Optional layer name or list of names for filtering.
        convert_polylines: Convert closed curves to surfaces when possible.
        explode_polysurfaces: Explode polysurfaces into individual surfaces.
        keep_input: Keep original objects when converting/exploding.

    Returns:
        List of Rhino surface object ids.
    """
    if object_ids is None:
        object_ids = rs.AllObjects(select=False, include_lights=False, include_grips=False) or []
    else:
        object_ids = _as_list(object_ids)

    layer_filter = _normalize_layer_names(layer_names)
    surfaces = []

    for obj_id in object_ids:
        if not obj_id or not rs.IsObject(obj_id):
            continue
        if not _object_in_layers(obj_id, layer_filter):
            continue

        if rs.IsSurface(obj_id):
            surfaces.append(obj_id)
            continue

        if rs.IsPolysurface(obj_id):
            if explode_polysurfaces:
                exploded = rs.ExplodePolysurfaces(obj_id, delete_input=not bool(keep_input)) or []
                for sid in exploded:
                    if sid and rs.IsSurface(sid):
                        surfaces.append(sid)
            else:
                surfaces.append(obj_id)
            continue

        if convert_polylines and rs.IsCurve(obj_id) and rs.IsCurveClosed(obj_id):
            created = _curve_to_surface_ids(obj_id)
            if created:
                layer_name = rs.ObjectLayer(obj_id)
                for sid in created:
                    if layer_name and rs.IsLayer(layer_name):
                        rs.ObjectLayer(sid, layer_name)
                    surfaces.append(sid)
                if not keep_input and rs.IsObject(obj_id):
                    rs.DeleteObject(obj_id)

    return _dedupe_ids(surfaces)


def create_curve_from_file(filename, close_curve=True, scale=1.0, z=0.0, layer_name=None):
    """Create one or more polylines from a JSON file.

    Supported JSON shapes:
      1) {"points": [[x, y], ...]}
      2) {"contours": [{"points": [[x, y], ...]}, ...]}
      3) [[x, y], ...]
      4) [[[x, y], ...], ...]
    """
    if not filename or not os.path.isfile(filename):
        raise IOError("File not found: {}".format(filename))

    with open(filename, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    curves = []
    for points in extract_point_sets(data):
        if not points:
            continue
        poly_pts = []
        for point in points:
            px, py, pz = _xyz(point)
            poly_pts.append((px * float(scale), py * float(scale), pz * float(scale) + float(z)))
        if close_curve and _distance(poly_pts[0], poly_pts[-1]) > 1e-6:
            poly_pts.append(poly_pts[0])

        curve_id = rs.AddPolyline(poly_pts)
        if not curve_id:
            continue
        if layer_name and rs.IsLayer(layer_name):
            rs.ObjectLayer(curve_id, layer_name)
        curves.append(curve_id)
    return curves


def subtract_surface(curves, target_surfaces=None, delete_inputs=False, discard_points=None):
    """Subtract/split target surfaces using closed defect curves.

    This helper tries boolean difference first; if that fails it falls back to
    brep split and returns produced pieces.
    """
    curve_ids = [cid for cid in _as_list(curves) if cid and rs.IsObject(cid)]
    if not curve_ids:
        return []

    if target_surfaces is None:
        target_surfaces = get_surfaces()
    target_surfaces = [sid for sid in _as_list(target_surfaces) if sid and rs.IsObject(sid)]
    if not target_surfaces:
        return []

    cutter_ids = []
    generated_cutters = []
    for cid in curve_ids:
        if rs.IsSurface(cid) or rs.IsPolysurface(cid):
            cutter_ids.append(cid)
            continue
        if rs.IsCurve(cid) and rs.IsCurveClosed(cid):
            surfaces = _curve_to_surface_ids(cid)
            if surfaces:
                cutter_ids.extend(surfaces)
                generated_cutters.extend(surfaces)
            else:
                # Keep non-planar closed curves as fallback cutters for SplitBrep.
                cutter_ids.append(cid)

    cutter_ids = _dedupe_ids(cutter_ids)
    if not cutter_ids:
        return target_surfaces

    tolerance = getattr(sc.doc, "ModelAbsoluteTolerance", None)
    if tolerance is None:
        tolerance = 0.01

    output_ids = []
    for target in target_surfaces:
        if not rs.IsObject(target):
            continue

        if rs.IsSurface(target):
            split_result = _split_surface_keep_outer(
                target,
                cutter_ids,
                delete_input=bool(delete_inputs),
                discard_points=discard_points,
            )
            if split_result:
                output_ids.extend(_as_list(split_result))
                continue

        try:
            diff = rs.BooleanDifference(target, cutter_ids, delete_input=False)
        except Exception:
            diff = None
        if diff:
            output_ids.extend(_as_list(diff))
            if delete_inputs and rs.IsObject(target):
                rs.DeleteObject(target)
            continue

        # RhinoScript SplitBrep expects a single cutter id (not a list), so
        # split sequentially with each cutter and keep the current piece set.
        pieces = [target]
        for cutter_id in cutter_ids:
            next_pieces = []
            for piece_id in pieces:
                if not piece_id or not rs.IsObject(piece_id):
                    continue
                try:
                    split = rs.SplitBrep(piece_id, cutter_id, delete_input=bool(delete_inputs))
                except Exception:
                    split = None
                if split:
                    split_ids = [sid for sid in _as_list(split) if sid and rs.IsObject(sid)]
                    if not split_ids:
                        if rs.IsObject(piece_id):
                            next_pieces.append(piece_id)
                        continue
                    if len(split_ids) <= 1:
                        next_pieces.extend(split_ids)
                        continue
                    next_pieces.extend(split_ids)
                else:
                    next_pieces.append(piece_id)
            pieces = _dedupe_ids(next_pieces)
            if not pieces:
                break

        if pieces:
            kept_ids = _filter_split_piece_ids_keep_outer(
                pieces,
                cutter_ids,
                tolerance,
                discard_points=discard_points,
            )
            if kept_ids is None:
                keep_id = _keep_largest_piece(pieces)
                kept_ids = [keep_id] if keep_id and rs.IsObject(keep_id) else []
            discard_ids = [sid for sid in pieces if sid not in kept_ids and rs.IsObject(sid)]
            if discard_ids:
                rs.DeleteObjects(discard_ids)
            output_ids.extend(kept_ids or [])
        else:
            output_ids.append(target)

    if generated_cutters and delete_inputs:
        for cutter in generated_cutters:
            if rs.IsObject(cutter):
                rs.DeleteObject(cutter)

    return _dedupe_ids(output_ids)


def modeling_spall(reference_points, reference_sizes):
    """Placeholder output structure for spall modeling workflow."""
    if len(reference_points) != len(reference_sizes):
        raise ValueError("reference_points and reference_sizes must have the same length.")
    return [
        {"point": _xyz(point), "size": float(size)}
        for point, size in zip(reference_points, reference_sizes)
    ]


def modeling_rebar(left, right, top, bottom):
    """Placeholder descriptor for rebar extent boundaries."""
    return {
        "left": float(left),
        "right": float(right),
        "top": float(top),
        "bottom": float(bottom),
    }


def modeling_efflore(reference_points, reference_sizes):
    """Placeholder output structure for efflorescence workflow."""
    if len(reference_points) != len(reference_sizes):
        raise ValueError("reference_points and reference_sizes must have the same length.")
    return [
        {"point": _xyz(point), "thickness": max(0.1, float(size) * 0.01)}
        for point, size in zip(reference_points, reference_sizes)
    ]


if __name__ == "__main__":
    pass
