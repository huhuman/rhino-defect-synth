"""Efflore-specific geometry modeling helpers for defect placement."""


def _resolve_efflore_condition_state(has_expand_polygon):
    return "CS3" if bool(has_expand_polygon) else "CS2"


def _explode_to_surface_faces(runtime, object_ids):
    faces = []
    for obj_id in runtime._coerce_ids(object_ids):
        if not obj_id or not runtime.rs.IsObject(obj_id):
            continue
        if runtime.rs.IsSurface(obj_id):
            faces.append(obj_id)
            continue
        exploded = []
        try:
            exploded = runtime._coerce_ids(runtime.rs.ExplodePolysurfaces(obj_id, delete_input=True) or [])
        except Exception:
            exploded = []
        if exploded:
            faces.extend([sid for sid in exploded if sid and runtime.rs.IsObject(sid)])
            continue
        if runtime.rs.IsObject(obj_id):
            faces.append(obj_id)
    return runtime._coerce_ids(faces)


def _split_surfaces_by_normal(runtime, surface_ids, normal, min_parallel_dot=0.93):
    target = runtime._unit(normal, fallback=(0.0, 0.0, 1.0))
    cap_candidates = []
    side_ids = []

    for surface_id in runtime._coerce_ids(surface_ids):
        if not surface_id or not runtime.rs.IsObject(surface_id):
            continue
        if not runtime.rs.IsSurface(surface_id):
            side_ids.append(surface_id)
            continue
        sample_point = runtime._surface_sample_point(surface_id)
        if sample_point is None:
            side_ids.append(surface_id)
            continue
        face_normal = runtime._surface_normal_at_point(surface_id, sample_point, fallback=target)
        dot_value = runtime._dot(runtime._unit(face_normal, fallback=target), target)
        if abs(dot_value) >= float(min_parallel_dot):
            cap_candidates.append((surface_id, runtime._dot(runtime._vec3(sample_point), target)))
        else:
            side_ids.append(surface_id)

    top_ids = []
    bottom_ids = []
    if cap_candidates:
        cap_candidates.sort(key=lambda item: item[1], reverse=True)
        top_ids.append(cap_candidates[0][0])
        bottom_ids.extend([item[0] for item in cap_candidates[1:]])

    all_ids = runtime._coerce_ids(top_ids + side_ids + bottom_ids)
    return {
        "all": all_ids,
        "top": runtime._coerce_ids(top_ids),
        "side": runtime._coerce_ids(side_ids),
        "bottom": runtime._coerce_ids(bottom_ids),
    }


def model_efflore_instance(runtime, candidate, shape, transform, cfg, layer_map, rng, defect_cfg=None, debug_cfg=None):
    eff_cfg = defect_cfg if isinstance(defect_cfg, dict) else (cfg.get("efflore") or {})
    outward_normal = runtime._candidate_outward_normal(candidate)

    inner_2d = shape.get("efflore_inner_poly") or shape.get("offset_poly") or shape.get("base_poly") or []
    outer_2d = shape.get("efflore_outer_poly")
    if not inner_2d:
        return None

    def _offset_polygon_along_normal(polygon_points, distance):
        offset = float(distance)
        return [runtime._add(runtime._vec3(pt), runtime._scale(outward_normal, offset)) for pt in (polygon_points or [])]

    def _create_extrusion_volume(polygon_points, thickness):
        curve_id = runtime._add_polyline(polygon_points)
        if not curve_id:
            return []
        try:
            depth = max(1e-4, float(thickness))
            start = runtime.rs.CurveStartPoint(curve_id)
            if not start:
                return []
            start_pt = runtime._vec3(start)
            end_pt = runtime._add(start_pt, runtime._scale(outward_normal, -3.0 * depth))
            extrusion = runtime.rs.ExtrudeCurveStraight(curve_id, start_pt, end_pt)
            extrusion_ids = runtime._coerce_ids([extrusion])
            if not extrusion_ids:
                return []

            capped = False
            for obj_id in extrusion_ids:
                try:
                    capped = bool(runtime.rs.CapPlanarHoles(obj_id)) or capped
                except Exception:
                    continue

            geometry_ids = list(extrusion_ids)
            if not capped:
                top_cap_ids = runtime._coerce_ids(runtime.rs.AddPlanarSrf(curve_id) or [])
                geometry_ids.extend(top_cap_ids)

                end_curve_id = runtime.rs.CopyObject(curve_id, runtime._sub(end_pt, start_pt))
                try:
                    if end_curve_id and runtime.rs.IsObject(end_curve_id):
                        bottom_cap_ids = runtime._coerce_ids(runtime.rs.AddPlanarSrf(end_curve_id) or [])
                        geometry_ids.extend(bottom_cap_ids)
                finally:
                    if end_curve_id and runtime.rs.IsObject(end_curve_id):
                        runtime.rs.DeleteObject(end_curve_id)

                try:
                    joined_ids = runtime._coerce_ids(runtime.rs.JoinSurfaces(geometry_ids, delete_input=True) or [])
                except Exception:
                    joined_ids = []
                if joined_ids:
                    geometry_ids = joined_ids

            return runtime._coerce_ids(geometry_ids)
        finally:
            if runtime.rs.IsObject(curve_id):
                runtime.rs.DeleteObject(curve_id)

    def _split_extrusion_volume(volume_ids):
        face_ids = _explode_to_surface_faces(runtime, volume_ids)
        return _split_surfaces_by_normal(runtime, face_ids, outward_normal)

    def _subtract_extrusion_volume(target_ids, cutter_ids):
        target_ids = runtime._coerce_ids(target_ids)
        cutter_ids = runtime._coerce_ids(cutter_ids)
        if not target_ids:
            return [], False
        if not cutter_ids:
            return target_ids, False

        try:
            diff_ids = runtime._coerce_ids(runtime.rs.BooleanDifference(target_ids, cutter_ids, delete_input=False) or [])
        except Exception:
            diff_ids = []
        if diff_ids:
            runtime._delete_objects(target_ids + cutter_ids)
            return diff_ids, True

        runtime._delete_objects(cutter_ids)
        return target_ids, False

    inner_polygon = runtime._project_points_to_surface(
        inner_2d,
        candidate,
        transform["angle_deg"],
        transform["normal_offset"],
    )
    if len(inner_polygon) < 4:
        return None

    outer_polygon = []
    has_outer = bool(outer_2d and len(outer_2d) >= 3)
    if has_outer:
        outer_polygon = runtime._project_points_to_surface(
            outer_2d,
            candidate,
            transform["angle_deg"],
            transform["normal_offset"],
        )
        has_outer = len(outer_polygon) >= 4

    thickness = max(1e-4, runtime._to_float(eff_cfg.get("fixed_thickness"), 0.1))
    sampled_cs = str(shape.get("condition_state") or "").strip().upper()
    if sampled_cs not in ("CS2", "CS3"):
        sampled_cs = _resolve_efflore_condition_state(has_outer)
    if sampled_cs == "CS2":
        has_outer = False
    elif sampled_cs == "CS3" and not has_outer:
        sampled_cs = "CS2"
    uses_expand_polygon = bool(has_outer)
    cs_level = sampled_cs

    inner_lifted_polygon = _offset_polygon_along_normal(inner_polygon, thickness)
    inner_split = _split_extrusion_volume(_create_extrusion_volume(inner_lifted_polygon, thickness))
    inner_geometry = runtime._coerce_ids(inner_split.get("all") or [])
    inner_top_geometry = runtime._coerce_ids(inner_split.get("top") or [])
    inner_side_geometry = runtime._coerce_ids((inner_split.get("side") or []) + (inner_split.get("bottom") or []))
    runtime._orient_surfaces_to_normal(inner_geometry, outward_normal)

    inner_layer = runtime._geometry_layer_for_condition(layer_map, "efflore", cs_level, part="inner")
    runtime._assign_layer(inner_geometry, inner_layer)

    outer_geometry = []
    outer_top_geometry = []
    outer_side_geometry = []
    outer_cut_applied = False
    if has_outer:
        outer_lifted_polygon = _offset_polygon_along_normal(outer_polygon, thickness)
        outer_volume = _create_extrusion_volume(outer_lifted_polygon, thickness)
        outer_cutter = _create_extrusion_volume(inner_lifted_polygon, thickness)
        outer_ring_volume, outer_cut_applied = _subtract_extrusion_volume(outer_volume, outer_cutter)
        outer_split = _split_extrusion_volume(outer_ring_volume)
        outer_geometry = runtime._coerce_ids(outer_split.get("all") or [])
        outer_top_geometry = runtime._coerce_ids(outer_split.get("top") or [])
        outer_side_geometry = runtime._coerce_ids((outer_split.get("side") or []) + (outer_split.get("bottom") or []))
        runtime._orient_surfaces_to_normal(outer_geometry, outward_normal)
        outer_layer = runtime._geometry_layer_for_condition(layer_map, "efflore", cs_level, part="outer")
        runtime._assign_layer(outer_geometry, outer_layer)

    geometry_ids = runtime._coerce_ids(inner_geometry + outer_geometry)
    if not geometry_ids:
        runtime._delete_objects(inner_geometry + outer_geometry)
        return None

    record = runtime._record_common("efflore", candidate, transform, shape)
    record["condition_state"] = cs_level
    record["geometry_ids"] = runtime._as_strings(geometry_ids)
    record["efflore_inner_geometry_ids"] = runtime._as_strings(inner_geometry)
    record["efflore_outer_geometry_ids"] = runtime._as_strings(outer_geometry)
    record["efflore_top_geometry_ids"] = runtime._as_strings(inner_top_geometry + outer_top_geometry)
    record["efflore_side_geometry_ids"] = runtime._as_strings(inner_side_geometry + outer_side_geometry)
    record["efflore_inner_top_geometry_ids"] = runtime._as_strings(inner_top_geometry)
    record["efflore_inner_side_geometry_ids"] = runtime._as_strings(inner_side_geometry)
    record["efflore_outer_top_geometry_ids"] = runtime._as_strings(outer_top_geometry)
    record["efflore_outer_side_geometry_ids"] = runtime._as_strings(outer_side_geometry)
    record["mask_ids"] = []
    record["efflore_metrics"] = {
        "thickness": float(thickness),
        "outer_thickness": float(thickness) if has_outer else 0.0,
        "lift_offset": float(thickness),
        "extrude_depth": float(3.0 * thickness),
        "has_outer_layer": bool(outer_geometry),
        "top_face_count": int(len(inner_top_geometry) + len(outer_top_geometry)),
        "side_face_count": int(len(inner_side_geometry) + len(outer_side_geometry)),
        "mask_uses_outer": False,
        "uses_expand_polygon": bool(uses_expand_polygon),
        "outer_cut_inner": bool(outer_cut_applied),
    }
    record["efflore_outer_source_file"] = shape.get("efflore_outer_source_file")
    runtime._attach_normal_debug(record, "efflore", candidate, debug_cfg)
    return record
