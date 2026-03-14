"""Simple entry point orchestrating material, modeling, and rendering steps."""

import json
import os
from datetime import datetime

import rhinoscriptsyntax as rs

from utils_loc.crack_modeling import create_crack
from utils_loc.materials import choose_and_import_layer_materials_with_metadata
from utils_loc.layers import create_layers
from utils_loc.cube_modeling import create_cube
from utils_loc.component_modeling import create_bridge_component
from utils_loc.defect_placement import apply_defect_pipeline, get_active_defect_requests
from utils_loc.plugin_autoload import ensure_plugin_commands
from utils_loc.texture_mapping import apply_component_texture_mapping

import importlib
render = importlib.import_module("utils_loc.render")
render_demo = importlib.import_module("utils_loc.render_demo")

_LAST_MODEL_RESULT = None
_LAST_PREPARATION_LAYER_METADATA = {}


def _normalize_optional_path(path):
    text = str(path or "").strip()
    if not text or text.lower() in ("none", "null"):
        return None
    return os.path.abspath(os.path.expanduser(text))


def _resolve_record_output_path(path):
    target_path = _normalize_optional_path(path)
    if not target_path:
        return None

    if os.path.isdir(target_path):
        output_dir = target_path
        prefix = "defect_records"
    else:
        root, ext = os.path.splitext(target_path)
        if ext.lower() == ".json":
            output_dir = os.path.dirname(target_path) or os.getcwd()
            stem = os.path.basename(root).strip()
            prefix = stem or "defect_records"
        else:
            output_dir = target_path
            prefix = "defect_records"

    os.makedirs(output_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = os.path.join(output_dir, "{}_{}.json".format(prefix, stamp))
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, "{}_{}_{:02d}.json".format(prefix, stamp, suffix))
        suffix += 1
    return candidate


def _write_json_atomic(path, payload):
    if not path:
        return

    target_path = os.path.abspath(os.path.expanduser(str(path)))
    if not target_path:
        return

    parent_dir = os.path.dirname(target_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    tmp_path = target_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except Exception:
            pass
    os.replace(tmp_path, target_path)


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
            record_text = json.dumps(record, sort_keys=True)
        except Exception:
            record_text = str(record)
        print("Defect record[{}]: {}".format(idx, record_text))


def _save_defect_records_if_requested(defect_result, debug_cfg=None):
    debug_cfg = debug_cfg if isinstance(debug_cfg, dict) else {}
    save_path = _resolve_record_output_path(debug_cfg.get("save_record_path"))
    if not save_path:
        return None

    payload = defect_result if isinstance(defect_result, dict) else {}
    _write_json_atomic(save_path, payload)
    print("Defect records saved to '{}'".format(save_path))
    return save_path


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


def create_model(params):
    """Create the model based on the provided parameters.
    Args:
        params (dict): Dictionary containing modeling parameters.
    """
    strategy = params["strategy"]

    global _LAST_MODEL_RESULT

    if strategy == "cube":
        cube_cfg = dict(params.get("cube") or {})
        if not cube_cfg:
            raise ValueError("modeling.cube is required when modeling.strategy='cube'.")
        print ("-------- Start Cube Modeling -------")
        crack_faces = create_cube(
            cube_map_dir=cube_cfg["cube_map_dir"],
            start_face_index=params.get("start_face_index", cube_cfg.get("start_face_index", 0)),
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
        defect_cfg = params.get("defect") or {}
        if get_active_defect_requests(defect_cfg):
            print("-------- Start Defect Placement -------")
            defect_result = apply_defect_pipeline(defect_cfg, model_result=result, debug_cfg=debug_cfg)
            result["defect"] = defect_result
            _log_defect_records(defect_result)
            _save_defect_records_if_requested(defect_result, debug_cfg=debug_cfg)
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
    camera_cfg = dict(params.get("camera") or {})
    if camera_cfg.get("strategy") == "component":
        component_cfg = dict(camera_cfg.get("component") or {})
        has_defects = bool(component_cfg.get("defects"))
        has_record = bool(component_cfg.get("defect_record_path") or params.get("defect_record_path"))
        if not has_defects and not has_record:
            last_defect = (_LAST_MODEL_RESULT or {}).get("defect") or {}
            if last_defect.get("camera_defects"):
                component_cfg["defects"] = last_defect["camera_defects"]
                camera_cfg["component"] = component_cfg
                params["camera"] = camera_cfg

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
