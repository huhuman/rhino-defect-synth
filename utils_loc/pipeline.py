"""Simple entry point orchestrating material, modeling, and rendering steps."""

import json
import os

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import (
    choose_and_import_layer_materials_with_metadata,
    set_texture_downsampling,
    set_material_reuse_enabled,
)
from utils_loc.layers import create_layers
from utils_loc.environment import ensure_document_environment
from utils_loc.cube_modeling import create_cube
from utils_loc.component_modeling import create_bridge_component
from utils_loc.defect_placement import apply_defect_pipeline, get_active_defect_requests
from utils_loc.defect_record_store import store_defect_record_payload
from utils_loc.plugin_autoload import ensure_plugin_commands
from utils_loc.texture_mapping import (
    apply_component_texture_mapping,
    apply_efflore_texture_mapping,
    apply_spalling_texture_mapping,
)

import importlib
render = importlib.import_module("utils_loc.render")
render_demo = importlib.import_module("utils_loc.render_demo")

_LAST_MODEL_RESULT = None
_LAST_PREPARATION_LAYER_METADATA = {}


def _round_if_number(value, digits=3):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, digits)
    return value


def _round_vec3(value, digits=3):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return value
    rounded = []
    for item in value:
        try:
            rounded.append(round(float(item), digits))
        except Exception:
            return value
    return rounded


def _summarize_metric_block(value, digits=3):
    if not isinstance(value, dict):
        return value
    return {
        str(key): _round_if_number(metric, digits=digits)
        for key, metric in sorted(value.items())
    }


def _summarize_defect_record_for_log(record):
    record = dict(record or {})
    source_file = record.get("shape_source_file")
    if source_file:
        source_file = os.path.basename(str(source_file))

    summary = {
        "type": record.get("type"),
        "condition_state": record.get("condition_state"),
        "severity": record.get("severity"),
        "surface_layer": record.get("surface_layer"),
        "point": _round_vec3(record.get("point")),
        "normal": _round_vec3(record.get("normal")),
        "reference_size": _round_if_number(record.get("reference_size")),
        "boundary_dist": _round_if_number(record.get("boundary_dist")),
        "angle_deg": _round_if_number(record.get("angle_deg")),
        "normal_offset": _round_if_number(record.get("normal_offset")),
        "source_file": source_file,
        "source_index": record.get("shape_source_index"),
        "instance_id": record.get("instance_id"),
        "instance_index": record.get("instance_index"),
        "target_metric_cm": _round_if_number(record.get("target_metric_cm")),
        "metric_scale": _round_if_number(record.get("metric_scale")),
        "has_exposed_rebar": bool(record.get("has_exposed_rebar", False)),
        # crack_tangent presence proves the azimuth-aware placement code is loaded (debug aid).
        "crack_tangent": _round_vec3(record.get("crack_tangent")) if record.get("crack_tangent") else None,
    }

    for metric_key in ("crack_metrics", "efflore_metrics", "spall_metrics", "rebar_metrics"):
        if metric_key in record:
            summary[metric_key] = _summarize_metric_block(record.get(metric_key))

    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _log_defect_records(defect_result):
    payload = defect_result if isinstance(defect_result, dict) else {}
    records = payload.get("records") or []
    summary = payload.get("summary") or {}
    print(
        "Defect records: total={} crack={} efflore={} spalling={} exposed_rebar={}".format(
            summary.get("total", len(records)),
            summary.get("crack", 0),
            summary.get("efflore", 0),
            summary.get("spalling", 0),
            summary.get("exposed_rebar", 0),
        )
    )
    if not records:
        print("Defect records: no placed records to log.")
        return

    for idx, record in enumerate(records):
        try:
            record_text = json.dumps(_summarize_defect_record_for_log(record), sort_keys=True)
        except Exception:
            record_text = str(record)
        print("Defect record[{}]: {}".format(idx, record_text))


def _cache_defect_records_in_document(defect_result):
    payload = defect_result if isinstance(defect_result, dict) else {}
    if not payload:
        return False
    cached = bool(store_defect_record_payload(payload))
    if cached:
        print("Defect records cached in Rhino document metadata.")
    return cached


def _filter_layer_map_by_prefix(layer_map, prefixes):
    layer_map = dict(layer_map or {})
    normalized = [str(prefix).strip() for prefix in (prefixes or []) if str(prefix).strip()]
    if not normalized:
        return layer_map

    def _is_excluded(layer_name):
        name = str(layer_name or "")
        for prefix in normalized:
            if name == prefix or name.startswith(prefix + "::"):
                return True
        return False

    return {
        layer_name: value
        for layer_name, value in layer_map.items()
        if not _is_excluded(layer_name)
    }


def prepare(params=None):
    """Prepare the environment by importing materials and creating layers.
    Args:
        params (dict): Dictionary containing preparation parameters.
    """
    params = dict(params or {})
    import_materials = bool(params.pop("_import_materials", True))
    global _LAST_PREPARATION_LAYER_METADATA
    _LAST_PREPARATION_LAYER_METADATA = {}
    plugin_autoload_cfg = params.get("plugin_autoload")
    print("Preparation plugin autoload: checking configuration...")
    ensure_plugin_commands(plugin_autoload_cfg)

    exclude_layer_prefixes = params.get("exclude_layer_prefixes") or []
    colors = _filter_layer_map_by_prefix(params.get("colors", {}), exclude_layer_prefixes)
    material_choices = _filter_layer_map_by_prefix(params.get("materials", {}), exclude_layer_prefixes)
    texture_materials = params.get("texture_materials", {})
    builtin_cfg = params.get("builtin_material_library", {}) or {}

    # Optional on-import texture downsampling (memory-leak mitigation). Off unless
    # preparation.texture_materials.max_resolution is set > 0.
    try:
        _ds_max_res = int((texture_materials or {}).get("max_resolution", 0) or 0)
    except Exception:
        _ds_max_res = 0
    set_texture_downsampling(_ds_max_res, (texture_materials or {}).get("downsample_cache_dir"))
    set_material_reuse_enabled(bool(params.get("material_reuse", False)))

    selected_materials = {}
    selected_material_metadata = {}
    if import_materials:
        # Materials: pick one available option per layer, then import only selected ones.
        selected_materials, selected_material_metadata = choose_and_import_layer_materials_with_metadata(
            layer_material_choices=material_choices,
            rng_seed=params.get("seed"),
            texture_root_dir=texture_materials.get("texture_root_dir"),
            texture_recursive=texture_materials.get("recursive", True),
            builtin_category=builtin_cfg.get("category", "Architectural"),
            builtin_subcategory1=builtin_cfg.get("subcategory1", "Wall"),
            builtin_subcategory2=builtin_cfg.get("subcategory2", "Concrete"),
            material_search_paths=params.get("material_search_paths"),
        )
        if selected_materials:
            print(
                "Preparation layer materials: "
                + ", ".join(
                    "{}={}".format(layer_name, material_name)
                    for layer_name, material_name in sorted(selected_materials.items())
                )
            )
    else:
        print("Preparation layer materials: skipped import for modeling-only pass.")

    _LAST_PREPARATION_LAYER_METADATA = dict(selected_material_metadata or {})

    # Layers
    create_layers(
        layer_material_dict=selected_materials,
        layer_color_dict=colors,
    )

    return {
        "selected_materials": dict(selected_materials or {}),
        "selected_material_metadata": dict(selected_material_metadata or {}),
    }


def _apply_document_environment(params, strategy):
    """Set document tolerance/units from config before building geometry.

    Reads ``tolerance_mm`` / ``units`` from the active strategy's config block, falling
    back to the modeling-level keys. Absent keys leave the document untouched, so this is
    a no-op (and thus safe for existing runs) unless the config opts in. Per-strategy so
    a large component model and a small cube model can each pick their own tolerance.
    """
    strat_cfg = params.get(strategy)
    strat_cfg = strat_cfg if isinstance(strat_cfg, dict) else {}
    tolerance_mm = strat_cfg.get("tolerance_mm", params.get("tolerance_mm"))
    units = strat_cfg.get("units", params.get("units"))
    if tolerance_mm is None and units is None:
        return
    try:
        applied = ensure_document_environment(
            units=units,
            tolerances={"absolute": tolerance_mm} if tolerance_mm is not None else None,
        )
    except Exception as exc:
        print("Document environment setup skipped ({}): {}".format(strategy, exc))
        return
    if applied:
        print("Document environment applied for {}: {}".format(strategy, applied))


def _audit_component_surface_normals():
    """DEBUG (modeling-only): verify component surface normals point OUTWARD — independent of defects.

    Component faces are built as LOOSE planar surfaces (not joined solids), but each component's
    surfaces still enclose a closed shell. We build ONE combined mesh from every component:: surface,
    then for each surface evaluate the RAW normal (the SAME one defect placement consumes via
    rs.SurfaceNormal — no orientation correction) at mid-uv, offset a test point a hair along it, and
    parity-count ray intersections with the combined shell. ODD => the test point is inside the
    structure => the surface normal points INWARD (a winding bug — a defect here grows into the
    structure / camera renders the interior). Per-layer inward counts localise the mis-oriented part.
    (Caveat: faces flush against a NEIGHBOUR component can read as inward — but free, visible faces,
    e.g. piers, are unaffected, so a high pier count is a real orientation bug.)"""
    try:
        import Rhino
        import scriptcontext as sc
        from collections import defaultdict

        layers = sc.doc.Layers
        struct = Rhino.Geometry.Mesh()
        comp_objs = []

        def _mesh_geo(geo):
            meshes = []
            try:
                brep = None
                if isinstance(geo, Rhino.Geometry.Brep):
                    brep = geo
                elif isinstance(geo, Rhino.Geometry.Extrusion):
                    brep = geo.ToBrep()
                elif isinstance(geo, Rhino.Geometry.Surface):
                    brep = geo.ToBrep()
                if brep is not None:
                    meshes = list(Rhino.Geometry.Mesh.CreateFromBrep(brep, Rhino.Geometry.MeshingParameters.Default) or [])
            except Exception:
                meshes = []
            return [m for m in meshes if m]

        def _faces(geo):
            try:
                if isinstance(geo, Rhino.Geometry.Brep):
                    return list(geo.Faces)
                if isinstance(geo, Rhino.Geometry.Extrusion):
                    b = geo.ToBrep()
                    return list(b.Faces) if b else []
                if isinstance(geo, Rhino.Geometry.Surface):
                    return [geo]
            except Exception:
                pass
            return []

        for obj in sc.doc.Objects:
            try:
                li = obj.Attributes.LayerIndex
                if li < 0 or li >= layers.Count:
                    continue
                lp = str(layers[li].FullPath or "")
                if not lp.startswith("component::"):
                    continue
                added = False
                for m in (obj.GetMeshes(Rhino.Geometry.MeshType.Render) or []):
                    if m:
                        struct.Append(m)
                        added = True
                if not added:
                    for m in _mesh_geo(obj.Geometry):
                        struct.Append(m)
                comp_objs.append((obj, lp))
            except Exception:
                continue
        if struct.Faces.Count == 0 or not comp_objs:
            print("COMPONENT NORMAL AUDIT: no component meshes found; skipping")
            return

        mesh_line = Rhino.Geometry.Intersect.Intersection.MeshLine
        by_layer = defaultdict(lambda: [0, 0])  # layer -> [inward_faces, total_faces]
        samples = []
        FAR = 1.0e5

        for obj, lp in comp_objs:
            lay = lp.split("::")[-1]
            for f in _faces(obj.Geometry):
                try:
                    u = f.Domain(0).Mid
                    v = f.Domain(1).Mid
                    pt = f.PointAt(u, v)
                    nrm = f.NormalAt(u, v)  # RAW surface normal (matches rs.SurfaceNormal / defect path)
                    if not nrm.Unitize():
                        continue
                    by_layer[lay][1] += 1
                    test = Rhino.Geometry.Point3d(pt.X + nrm.X * 0.02, pt.Y + nrm.Y * 0.02, pt.Z + nrm.Z * 0.02)
                    end = Rhino.Geometry.Point3d(test.X + nrm.X * FAR, test.Y + nrm.Y * FAR, test.Z + nrm.Z * FAR)
                    hits = mesh_line(struct, Rhino.Geometry.Line(test, end))
                    npts = 0
                    if hits is not None:
                        try:
                            npts = len(hits)
                        except Exception:
                            try:
                                npts = len(hits[0])
                            except Exception:
                                npts = 0
                    if npts % 2 == 1:  # test point inside the structure => raw normal points inward
                        by_layer[lay][0] += 1
                        if len(samples) < 4:
                            samples.append("{}[{:.2f},{:.2f},{:.2f}]@({:.0f},{:.0f},{:.0f})".format(
                                lay, nrm.X, nrm.Y, nrm.Z, pt.X, pt.Y, pt.Z))
                except Exception:
                    continue

        summary = ", ".join("{}={}/{}".format(k, v[0], v[1]) for k, v in sorted(by_layer.items()))
        print("COMPONENT NORMAL AUDIT (odd parity = INWARD-facing surface normal): {}{}".format(
            summary, (" | inward=" + ", ".join(samples)) if samples else " | all outward"))
    except Exception as exc:  # noqa: BLE001
        print("COMPONENT NORMAL AUDIT failed: {}".format(exc))


def create_model(params):
    """Create the model based on the provided parameters.
    Args:
        params (dict): Dictionary containing modeling parameters.
    """
    strategy = params["strategy"]
    _apply_document_environment(params, strategy)

    global _LAST_MODEL_RESULT

    if strategy == "cube":
        cube_cfg = dict(params.get("cube") or {})
        if not cube_cfg:
            raise ValueError("modeling.cube is required when modeling.strategy='cube'.")
        print ("-------- Start Cube Modeling -------")
        crack_faces = create_cube(
            cube_map_dir=cube_cfg["cube_map_dir"],
            start_face_index=params.get("start_face_index", cube_cfg.get("start_face_index", 0)),
            build_offset_poly=bool(cube_cfg.get("build_offset_poly", True)),
        )

        inward_dirs = {
            "+x": (-1, 0, 0),
            "-x": (1, 0, 0),
            "+y": (0, -1, 0),
            "-y": (0, 1, 0),
            "+z": (0, 0, -1),
            "-z": (0, 0, 1),
        }
        redraw_was_enabled = bool(rs.EnableRedraw(False))
        try:
            for face, crack_items in crack_faces.items():
                print(f"-------- Modeling cracks on face {face} -------")
                inward = inward_dirs.get(face)
                for item in crack_items:
                    create_crack(
                        item.get("crack_polys"),
                        item.get("inside_polys"),
                        item.get("base_poly"),
                        item.get("offset_poly"),
                        item.get("diff_polys"),
                        inward_dir=inward,
                        wall_slope_deg=cube_cfg.get("wall_slope_deg"),
                        layer_crack_extrusion=item.get("crack_layer") or "crack::CS1",
                        layer_erosion=item.get("crack_layer") or "crack::CS1",
                        layer_parent_surface="cube::face",
                        disable_redraw=False,
                    )
        finally:
            if redraw_was_enabled:
                rs.EnableRedraw(True)

        # Cube workflow uses face crack maps directly; no secondary defect placement stage.
        model_result = {"strategy": "cube", "crack_faces": crack_faces}
        _LAST_MODEL_RESULT = model_result
        return model_result

    elif strategy == "component":
        print("-------- Start Component Modeling -------")
        component_cfg = dict(params.get("component", {}))
        debug_cfg = dict(params.get("debug") or {})
        if not component_cfg:
            raise ValueError("modeling.component is required when modeling.strategy='component'.")

        result = create_bridge_component(component_cfg, debug_cfg=debug_cfg)
        if dict(debug_cfg or {}).get("audit_surface_normals", True):
            _audit_component_surface_normals()
        defect_cfg = params.get("defect") or {}
        if get_active_defect_requests(defect_cfg):
            print("-------- Start Defect Placement -------")
            defect_result = apply_defect_pipeline(defect_cfg, model_result=result, debug_cfg=debug_cfg)
            result["defect"] = defect_result
            _log_defect_records(defect_result)
            _cache_defect_records_in_document(defect_result)
            summary = defect_result.get("summary", {})
            print(
                "-------- Defect Placement Complete ------- "
                "(total: {}, crack: {}, efflore: {}, spalling: {}, exposed_rebar: {})".format(
                    summary.get("total", 0),
                    summary.get("crack", 0),
                    summary.get("efflore", 0),
                    summary.get("spalling", 0),
                    summary.get("exposed_rebar", 0),
                )
            )
        efflore_texture_mapping_result = apply_efflore_texture_mapping(
            defect_cfg=defect_cfg,
            layer_material_metadata=_LAST_PREPARATION_LAYER_METADATA,
        )
        result["efflore_texture_mapping"] = efflore_texture_mapping_result
        if efflore_texture_mapping_result.get("enabled"):
            print(
                "-------- Efflore Texture Mapping ------- "
                "(applied: {}, surfaces: {}, skipped: {})".format(
                    efflore_texture_mapping_result.get("applied", 0),
                    efflore_texture_mapping_result.get("surface_objects", 0),
                    efflore_texture_mapping_result.get("skipped", 0),
                )
            )
        spalling_texture_mapping_result = apply_spalling_texture_mapping(
            defect_cfg=defect_cfg,
            layer_material_metadata=_LAST_PREPARATION_LAYER_METADATA,
        )
        result["spalling_texture_mapping"] = spalling_texture_mapping_result
        if spalling_texture_mapping_result.get("enabled"):
            print(
                "-------- Spalling/Rebar Texture Mapping ------- "
                "(applied: {}, surfaces: {}, solids: {}, skipped: {})".format(
                    spalling_texture_mapping_result.get("applied", 0),
                    spalling_texture_mapping_result.get("surface_objects", 0),
                    spalling_texture_mapping_result.get("solid_objects", 0),
                    spalling_texture_mapping_result.get("skipped", 0),
                )
            )
        texture_mapping_result = apply_component_texture_mapping(
            component_cfg=component_cfg,
            layer_material_metadata=_LAST_PREPARATION_LAYER_METADATA,
        )
        result["texture_mapping"] = texture_mapping_result
        if texture_mapping_result.get("enabled"):
            print(
                "-------- Component Texture Mapping ------- "
                "(applied: {}, surfaces: {}, solids: {}, skipped: {})".format(
                    texture_mapping_result.get("applied", 0),
                    texture_mapping_result.get("surface_objects", 0),
                    texture_mapping_result.get("solid_objects", 0),
                    texture_mapping_result.get("skipped", 0),
                )
            )
        print(
            "-------- Component Modeling Complete ------- "
            "(surfaces: {}, polylines: {}, solids: {})".format(
                len(result["surfaces"]),
                len(result["polylines"]),
                len(result["solids"]),
            )
        )
        _LAST_MODEL_RESULT = result
        return result
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def run_render(params, show_cameras=False):
    """Pipeline render stage."""
    params = dict(params or {})

    try:
        rs.UnselectAllObjects()
    except Exception:
        pass

    render.setup_render_environment(params)
    context = render.build_render_context(params)
    if context is None:
        return

    render.redraw_views()
    poses = render.generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        render.preview_camera_gizmos(poses, context["lengths"])
        return

    return render.capture_pose_sequence(poses, context)


def run_render_demo(base_out_dir, params=None):
    """Pipeline demo stage for camera/material/lighting visualization."""
    captured_paths = render_demo.render_demo(base_out_dir=base_out_dir, params=params)
    print(f"run_render_demo: captured {len(captured_paths)} images to '{base_out_dir}'.")
    return captured_paths
