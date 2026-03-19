"""Helpers for assigning Rhino texture mapping to generated component objects."""

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc


_TEXTURE_MAPPING_CLS = getattr(Rhino.Render, "TextureMapping", None)
if _TEXTURE_MAPPING_CLS is None:
    _TEXTURE_MAPPING_CLS = getattr(Rhino.DocObjects, "TextureMapping", None)


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


def _interval(size):
    half = 0.5 * max(1e-6, float(size))
    return Rhino.Geometry.Interval(-half, half)


def _vec3(values):
    return float(values[0]), float(values[1]), float(values[2])


def _dot(a, b):
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a, b):
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return (
        ay * bz - az * by,
        az * bx - ax * bz,
        ax * by - ay * bx,
    )


def _normalize(vec):
    x, y, z = _vec3(vec)
    length = (x * x + y * y + z * z) ** 0.5
    if length <= 1e-12:
        return None
    inv = 1.0 / length
    return x * inv, y * inv, z * inv


def _coerce_rhino_object(obj_id):
    try:
        return rs.coercerhinoobject(obj_id, True, True)
    except Exception:
        return None


def _object_geometry(rhino_object):
    if rhino_object is None:
        return None
    try:
        return rhino_object.Geometry
    except Exception:
        return None


def _surface_frame(obj_id):
    rhino_object = _coerce_rhino_object(obj_id)
    geometry = _object_geometry(rhino_object)
    if geometry is None:
        return None

    plane = None
    try_get_plane = getattr(geometry, "TryGetPlane", None)
    if try_get_plane is not None:
        try:
            ok, plane = try_get_plane()
            if ok and plane is not None:
                return plane
        except Exception:
            plane = None

    surface_like = geometry
    if hasattr(geometry, "Faces") and getattr(geometry.Faces, "Count", 0) > 0:
        surface_like = geometry.Faces[0]
    elif hasattr(geometry, "ToBrep"):
        try:
            brep = geometry.ToBrep()
        except Exception:
            brep = None
        if brep is not None and getattr(brep.Faces, "Count", 0) > 0:
            surface_like = brep.Faces[0]

    try_get_plane = getattr(surface_like, "TryGetPlane", None)
    if try_get_plane is not None:
        try:
            ok, plane = try_get_plane()
            if ok and plane is not None:
                return plane
        except Exception:
            plane = None

    frame_at = getattr(surface_like, "FrameAt", None)
    domain_fn = getattr(surface_like, "Domain", None)
    if frame_at is None or domain_fn is None:
        return None

    try:
        domain_u = domain_fn(0)
        domain_v = domain_fn(1)
        u = 0.5 * (float(domain_u.T0) + float(domain_u.T1))
        v = 0.5 * (float(domain_v.T0) + float(domain_v.T1))
        ok, plane = frame_at(u, v)
        if ok and plane is not None:
            return plane
    except Exception:
        return None
    return None


def _world_orthogonal_plane_from_surface(obj_id):
    plane = _surface_frame(obj_id)
    if plane is None:
        return None

    try:
        origin = plane.Origin
        normal = _normalize((plane.Normal.X, plane.Normal.Y, plane.Normal.Z))
    except Exception:
        return None
    if normal is None:
        return None

    ref_axes = (
        (0.0, 0.0, 1.0),
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
    )
    up_axis = None
    for ref in ref_axes:
        projected = tuple(ref[idx] - normal[idx] * _dot(ref, normal) for idx in range(3))
        up_axis = _normalize(projected)
        if up_axis is not None:
            break
    if up_axis is None:
        return None

    x_axis = _normalize(_cross(up_axis, normal))
    if x_axis is None:
        return None
    y_axis = _normalize(_cross(normal, x_axis))
    if y_axis is None:
        return None

    try:
        return Rhino.Geometry.Plane(
            origin,
            Rhino.Geometry.Vector3d(x_axis[0], x_axis[1], x_axis[2]),
            Rhino.Geometry.Vector3d(y_axis[0], y_axis[1], y_axis[2]),
        )
    except Exception:
        return None


def _resolve_layer_metadata(layer_name, layer_material_metadata):
    if not layer_name:
        return {}
    metadata = dict(layer_material_metadata or {})
    if layer_name in metadata:
        return metadata[layer_name] or {}
    tail = layer_name.split("::")[-1]
    for key, value in metadata.items():
        if str(key or "").split("::")[-1] == tail:
            return value or {}
    return {}


def _resolve_mapping_sizes(mapping_cfg, layer_name, layer_material_metadata):
    metadata = _resolve_layer_metadata(layer_name, layer_material_metadata)
    preserve_aspect_ratio = bool(mapping_cfg.get("preserve_aspect_ratio", True))
    aspect_ratio = _to_float(mapping_cfg.get("aspect_ratio_fallback"), 1.0)
    if preserve_aspect_ratio:
        material_aspect_ratio = _to_float(metadata.get("texture_aspect_ratio"), 0.0)
        if material_aspect_ratio > 1e-6:
            aspect_ratio = material_aspect_ratio

    explicit_u = mapping_cfg.get("world_size_u")
    explicit_v = mapping_cfg.get("world_size_v")

    if explicit_u is not None and explicit_v is not None:
        size_u = max(1e-6, _to_float(explicit_u, 100.0))
        size_v = max(1e-6, _to_float(explicit_v, 100.0))
    elif explicit_u is not None:
        size_u = max(1e-6, _to_float(explicit_u, 100.0))
        size_v = max(1e-6, size_u / max(aspect_ratio, 1e-6))
    else:
        size_v = max(1e-6, _to_float(explicit_v, 100.0))
        size_u = max(1e-6, size_v * max(aspect_ratio, 1e-6))

    size_z = max(1e-6, _to_float(mapping_cfg.get("world_size_z"), size_v))
    return {
        "size_u": size_u,
        "size_v": size_v,
        "size_z": size_z,
        "aspect_ratio": aspect_ratio,
        "material_name": metadata.get("resolved_material_name") or metadata.get("material_name"),
        "texture_seed_path": metadata.get("path"),
    }


def _set_texture_mapping(obj_id, mapping, channel):
    if mapping is None:
        return False

    rhino_object = _coerce_rhino_object(obj_id)
    if rhino_object is None:
        return False

    setter = getattr(rhino_object, "SetTextureMapping", None)
    if setter is not None:
        try:
            ok = bool(setter(int(channel), mapping))
            if ok:
                commit = getattr(rhino_object, "CommitChanges", None)
                if commit is not None:
                    try:
                        commit()
                    except Exception:
                        pass
                return True
        except Exception:
            pass

    attrs = getattr(rhino_object, "Attributes", None)
    if attrs is None:
        return False

    duplicate = getattr(attrs, "Duplicate", None)
    if duplicate is None:
        return False

    try:
        attrs_copy = duplicate()
    except Exception:
        return False

    attr_setter = getattr(attrs_copy, "SetTextureMapping", None)
    if attr_setter is None:
        return False

    try:
        ok = bool(attr_setter(int(channel), mapping))
    except Exception:
        ok = False
    if not ok:
        return False

    try:
        return bool(sc.doc.Objects.ModifyAttributes(rhino_object, attrs_copy, True))
    except Exception:
        return False


def _planar_mapping_for_surface(obj_id, sizes):
    if _TEXTURE_MAPPING_CLS is None:
        return None
    plane = _surface_frame(obj_id)
    if plane is None:
        return None

    create = getattr(_TEXTURE_MAPPING_CLS, "CreatePlaneMapping", None)
    if create is None:
        return None

    try:
        return create(
            plane,
            _interval(sizes["size_u"]),
            _interval(sizes["size_v"]),
            _interval(sizes["size_z"]),
        )
    except Exception:
        return None


def _world_orthogonal_mapping_for_surface(obj_id, sizes):
    if _TEXTURE_MAPPING_CLS is None:
        return None
    plane = _world_orthogonal_plane_from_surface(obj_id)
    if plane is None:
        return None

    create = getattr(_TEXTURE_MAPPING_CLS, "CreatePlaneMapping", None)
    if create is None:
        return None

    try:
        return create(
            plane,
            _interval(sizes["size_u"]),
            _interval(sizes["size_v"]),
            _interval(sizes["size_z"]),
        )
    except Exception:
        return None


def _box_mapping_for_object(sizes):
    if _TEXTURE_MAPPING_CLS is None:
        return None

    create = getattr(_TEXTURE_MAPPING_CLS, "CreateBoxMapping", None)
    if create is None:
        return None

    try:
        return create(
            Rhino.Geometry.Plane.WorldXY,
            _interval(sizes["size_u"]),
            _interval(sizes["size_u"]),
            _interval(sizes["size_z"]),
            False,
        )
    except Exception:
        return None


def _is_surface_like(obj_id):
    try:
        return bool(rs.IsSurface(obj_id))
    except Exception:
        return False


def _is_solid_like(obj_id):
    try:
        return bool(rs.IsPolysurface(obj_id) or rs.IsSolid(obj_id) or rs.IsPolysurfaceClosed(obj_id))
    except Exception:
        return False


def _apply_texture_mapping_to_layers(
    layer_names,
    mapping_cfg,
    layer_material_metadata=None,
    surface_mapping_mode="surface_frame",
):
    mapping_cfg = dict(mapping_cfg or {})
    if not bool(mapping_cfg.get("enabled", False)):
        return {
            "enabled": False,
            "applied": 0,
            "surface_objects": 0,
            "solid_objects": 0,
            "skipped": 0,
            "layers": {},
        }

    channel = max(1, _to_int(mapping_cfg.get("texture_channel"), 1))
    layer_names = [str(name) for name in (layer_names or []) if str(name or "").strip()]
    if surface_mapping_mode == "world_orthogonal":
        surface_mapping_fn = _world_orthogonal_mapping_for_surface
    else:
        surface_mapping_fn = _planar_mapping_for_surface

    summary = {
        "enabled": True,
        "texture_channel": channel,
        "surface_mapping_mode": str(surface_mapping_mode),
        "applied": 0,
        "surface_objects": 0,
        "solid_objects": 0,
        "skipped": 0,
        "layers": {},
    }

    for layer_name in layer_names:
        object_ids = rs.ObjectsByLayer(layer_name, select=False) or []
        if not object_ids:
            continue

        layer_stats = summary["layers"].setdefault(
            layer_name,
            {
                "objects": 0,
                "applied": 0,
                "skipped": 0,
                "material_name": None,
                "aspect_ratio": None,
                "size_u": None,
                "size_v": None,
            },
        )

        sizes = _resolve_mapping_sizes(mapping_cfg, layer_name, layer_material_metadata)
        layer_stats["material_name"] = sizes.get("material_name")
        layer_stats["aspect_ratio"] = sizes.get("aspect_ratio")
        layer_stats["size_u"] = sizes.get("size_u")
        layer_stats["size_v"] = sizes.get("size_v")

        for obj_id in object_ids:
            layer_stats["objects"] += 1

            if _is_surface_like(obj_id):
                mapping = surface_mapping_fn(obj_id, sizes)
                if _set_texture_mapping(obj_id, mapping, channel):
                    summary["applied"] += 1
                    summary["surface_objects"] += 1
                    layer_stats["applied"] += 1
                else:
                    summary["skipped"] += 1
                    layer_stats["skipped"] += 1
                continue

            if _is_solid_like(obj_id):
                mapping = _box_mapping_for_object(sizes)
                if _set_texture_mapping(obj_id, mapping, channel):
                    summary["applied"] += 1
                    summary["solid_objects"] += 1
                    layer_stats["applied"] += 1
                else:
                    summary["skipped"] += 1
                    layer_stats["skipped"] += 1
                continue

            summary["skipped"] += 1
            layer_stats["skipped"] += 1

    if summary["applied"] > 0:
        try:
            sc.doc.Views.Redraw()
        except Exception:
            pass

    return summary


def apply_component_texture_mapping(component_cfg=None, layer_material_metadata=None):
    component_cfg = dict(component_cfg or {})
    layer_names = [
        str(name)
        for name in (component_cfg.get("layers") or {}).values()
        if str(name or "").strip()
    ]
    return _apply_texture_mapping_to_layers(
        layer_names=layer_names,
        mapping_cfg=component_cfg.get("texture_mapping") or {},
        layer_material_metadata=layer_material_metadata,
        surface_mapping_mode="surface_frame",
    )


def apply_efflore_texture_mapping(defect_cfg=None, layer_material_metadata=None):
    defect_cfg = dict(defect_cfg or {})
    eff_cfg = dict(defect_cfg.get("efflore") or {})
    geometry_layers = dict((defect_cfg.get("layers") or {}).get("geometry") or {})
    layer_names = [
        geometry_layers.get("efflore_cs2"),
        geometry_layers.get("efflore_cs3_inner"),
        geometry_layers.get("efflore_cs3_outer"),
    ]
    return _apply_texture_mapping_to_layers(
        layer_names=layer_names,
        mapping_cfg=eff_cfg.get("texture_mapping") or {},
        layer_material_metadata=layer_material_metadata,
        surface_mapping_mode="world_orthogonal",
    )
