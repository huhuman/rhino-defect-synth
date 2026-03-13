#! python3
from contextlib import contextmanager
from time import perf_counter

import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

from utils_loc.config import load_config
from utils_loc.logging_utils import install_timestamped_print
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare, run_render

install_timestamped_print()

STAGE_ORDER = (
    "reset",
    "load_config",
    "preparation",
    "view_setup",
    "modeling",
    "rendering",
)
STAGE_DEPENDENCIES = {
    "preparation": ("load_config",),
    "view_setup": ("load_config",),
    "modeling": ("load_config",),
    "rendering": ("load_config",),
}


def reset():
    """Delete all objects, layers, and imported materials from the active document."""
    with _suspend_view_updates(use_work_view=True):
        objs = rs.AllObjects(
            select=False,
            include_lights=True,
            include_grips=True,
        )
        if objs:
            rs.DeleteObjects(objs)
        _clear_all_layers(base_layer_name="__reset__")
        clear_imported_materials_from_doc()
    _stability_wait(redraw=True)


def _clear_all_layers(base_layer_name="__reset__"):
    """Delete all layers except a single base layer."""
    if not rs.IsLayer(base_layer_name):
        created = rs.AddLayer(base_layer_name)
        if created:
            base_layer_name = created

    if rs.IsLayer(base_layer_name):
        try:
            rs.CurrentLayer(base_layer_name)
        except Exception:
            pass

    layer_names = rs.LayerNames(sort=True) or []
    delete_candidates = [
        name for name in layer_names if name and name != base_layer_name
    ]
    delete_candidates.sort(
        key=lambda name: (name.count("::"), len(name)),
        reverse=True,
    )

    for layer_name in delete_candidates:
        if not rs.IsLayer(layer_name):
            continue

        deleted = False
        for delete_fn in (rs.DeleteLayer, rs.PurgeLayer):
            try:
                deleted = bool(delete_fn(layer_name))
            except Exception:
                deleted = False
            if deleted:
                break

    if base_layer_name != "Default" and rs.IsLayer("Default"):
        for delete_fn in (rs.DeleteLayer, rs.PurgeLayer):
            try:
                if bool(delete_fn("Default")):
                    break
            except Exception:
                continue


def _normalize_layer_name_set(layer_names):
    if layer_names is None:
        return None
    if isinstance(layer_names, (str, bytes)):
        return {str(layer_names)}
    return {str(name) for name in layer_names if str(name).strip()}


def _layer_matches(layer, names):
    if not names:
        return False
    layer_name = getattr(layer, "Name", None)
    full_path = getattr(layer, "FullPath", None)
    if layer_name in names or full_path in names:
        return True
    if full_path:
        for name in names:
            if full_path.startswith(f"{name}::"):
                return True
    if full_path:
        tail = full_path.split("::")[-1]
        if tail in names:
            return True
    return False


def setup_render_view(cfg=None):
    """Set active view mode and configure layer visibility from config."""
    render_view = sc.doc.Views.ActiveView
    if render_view is None:
        raise RuntimeError("No active Rhino view available.")
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    cfg = cfg or {}
    view_setup_cfg = cfg.get("view_setup") or {}
    only_set = _normalize_layer_name_set(view_setup_cfg.get("only_layers"))
    hide_set = _normalize_layer_name_set(view_setup_cfg.get("hide_layers"))
    if not only_set and not hide_set:
        return

    for layer in sc.doc.Layers:
        if not layer.Name:
            continue
        if only_set:
            layer.IsVisible = _layer_matches(layer, only_set)
        else:
            layer.IsVisible = True
        if hide_set and _layer_matches(layer, hide_set):
            layer.IsVisible = False


def _set_active_view_display_mode(mode_name):
    if not str(mode_name or "").strip():
        return False

    render_view = getattr(sc.doc.Views, "ActiveView", None)
    if render_view is None:
        return False

    mode = Rhino.Display.DisplayModeDescription.FindByName(str(mode_name))
    if mode is None:
        return False

    try:
        render_view.ActiveViewport.DisplayMode = mode
        return True
    except Exception:
        return False


def _stability_wait(wait_ms=0, redraw=False):
    wait_ms = max(0, int(wait_ms))
    if redraw:
        try:
            sc.doc.Views.Redraw()
        except Exception:
            pass
    try:
        Rhino.RhinoApp.Wait()
    except Exception:
        pass
    if wait_ms > 0:
        rs.Sleep(wait_ms)


@contextmanager
def _suspend_view_updates(use_work_view=False):
    redraw_previous = None
    active_view = None
    drawing_previous = None
    display_mode_previous = None

    try:
        redraw_previous = rs.EnableRedraw(False)
    except Exception:
        redraw_previous = None

    try:
        active_view = getattr(sc.doc.Views, "ActiveView", None)
        if active_view is not None:
            if hasattr(active_view, "EnableDrawing"):
                drawing_previous = bool(active_view.EnableDrawing)
                if drawing_previous:
                    active_view.EnableDrawing = False
            if use_work_view:
                display_mode_previous = active_view.ActiveViewport.DisplayMode
                if not _set_active_view_display_mode("Wireframe"):
                    _set_active_view_display_mode("Shaded")
    except Exception:
        drawing_previous = None
        display_mode_previous = None

    try:
        yield
    finally:
        if active_view is not None and display_mode_previous is not None:
            try:
                active_view.ActiveViewport.DisplayMode = display_mode_previous
            except Exception:
                pass
        if active_view is not None and drawing_previous is not None:
            try:
                active_view.EnableDrawing = bool(drawing_previous)
            except Exception:
                pass
        try:
            if redraw_previous is None:
                rs.EnableRedraw(True)
            else:
                rs.EnableRedraw(bool(redraw_previous))
        except Exception:
            pass


def _run_with_suspended_view_updates(func, *args, **kwargs):
    with _suspend_view_updates(use_work_view=True):
        result = func(*args, **kwargs)
    _stability_wait(redraw=True)
    return result


def _apply_run_autosave_policy(preparation_params):
    prep_cfg = dict(preparation_params or {})
    autosave_cfg = dict(prep_cfg.get("autosave") or {})
    disable_during_run = bool(autosave_cfg.get("disable_during_batch", True))
    if not disable_during_run:
        print("[stability] autosave policy: keep enabled (disable_during_batch=false).")
        return None

    app_settings = getattr(Rhino, "ApplicationSettings", None)
    settings_obj = getattr(app_settings, "FileSettings", None) if app_settings is not None else None
    if settings_obj is None:
        print("[stability] autosave policy: FileSettings API unavailable; skipping.")
        return None

    enabled_value = None
    changed = False
    if hasattr(settings_obj, "AutoSaveEnabled"):
        try:
            enabled_value = bool(settings_obj.AutoSaveEnabled)
            settings_obj.AutoSaveEnabled = False
            changed = True
        except Exception:
            changed = False

    state = {
        "settings_obj": settings_obj,
        "enabled_value": enabled_value,
        "changed": bool(changed),
    }
    if changed:
        print("[stability] autosave policy: disabled during run.")
    else:
        print("[stability] autosave policy: no supported autosave property found.")
    return state


def _restore_run_autosave_policy(state):
    state = dict(state or {})
    if not state or not bool(state.get("changed")):
        return

    settings_obj = state.get("settings_obj")
    if settings_obj is None:
        return

    restored_enabled = False
    if state.get("enabled_value") is not None and hasattr(settings_obj, "AutoSaveEnabled"):
        try:
            settings_obj.AutoSaveEnabled = bool(state.get("enabled_value"))
            restored_enabled = True
        except Exception:
            restored_enabled = False

    if restored_enabled:
        print("[stability] autosave policy: restored previous settings.")
    else:
        print("[stability] autosave policy: restore failed (properties unavailable).")


def _apply_run_undo_policy(preparation_params):
    prep_cfg = dict(preparation_params or {})
    undo_cfg = dict(prep_cfg.get("undo") or {})
    disable_during_run = bool(undo_cfg.get("disable_during_batch", True))
    if not disable_during_run:
        print("[stability] undo policy: keep recording enabled (disable_during_batch=false).")
        return None

    doc = getattr(sc, "doc", None)
    if doc is None or not hasattr(doc, "UndoRecordingEnabled"):
        print("[stability] undo policy: UndoRecordingEnabled API unavailable; skipping.")
        return None

    try:
        enabled_value = bool(doc.UndoRecordingEnabled)
    except Exception:
        print("[stability] undo policy: unable to read current undo state; skipping.")
        return None

    changed = False
    if enabled_value:
        try:
            doc.UndoRecordingEnabled = False
            changed = True
            print("[stability] undo policy: disabled undo recording during run.")
        except Exception:
            print("[stability] undo policy: failed to disable undo recording.")
    else:
        print("[stability] undo policy: already disabled before run.")

    return {
        "doc": doc,
        "enabled_value": enabled_value,
        "changed": bool(changed),
    }


def _restore_run_undo_policy(state):
    state = dict(state or {})
    if not state or not bool(state.get("changed")):
        return

    doc = state.get("doc")
    if doc is None or not hasattr(doc, "UndoRecordingEnabled"):
        return

    restored = False
    try:
        doc.UndoRecordingEnabled = bool(state.get("enabled_value"))
        restored = True
    except Exception:
        restored = False

    if restored:
        print("[stability] undo policy: restored previous undo-recording setting.")
    else:
        print("[stability] undo policy: failed to restore previous undo-recording setting.")


def time_stage(name, func, stage_times, *args, **kwargs):
    """Run a stage function and record elapsed seconds."""
    start = perf_counter()
    result = func(*args, **kwargs)
    duration = perf_counter() - start
    stage_times.append((name, duration))
    return result


def _normalize_stage_name(name):
    stage_name = name.strip().lower()
    if stage_name not in STAGE_ORDER:
        valid = ", ".join(STAGE_ORDER)
        raise ValueError(f"Unknown stage '{name}'. Valid stages: {valid}")
    return stage_name


def _resolve_stage_list(stages=None, skip=None):
    if stages is None:
        selected = []
    else:
        requested = set(_normalize_stage_name(stage) for stage in stages)
        selected = [stage for stage in STAGE_ORDER if stage in requested]

    skip = set(skip or [])
    for stage in selected:
        for dep in STAGE_DEPENDENCIES.get(stage, ()):
            if dep in skip:
                raise ValueError(f"Cannot skip '{dep}' while running '{stage}'.")

    selected = [stage for stage in selected if stage not in skip]

    required = set(selected)
    for stage in selected:
        for dep in STAGE_DEPENDENCIES.get(stage, ()):
            required.add(dep)
    selected = [stage for stage in STAGE_ORDER if stage in required and stage not in skip]

    return selected


def run(
    config_name="cube_render.yaml",
    stages=None,
    skip=None,
    start_face_index=0,
    show_cameras=False,
    print_timings=True,
):
    """Run selected pipeline stages."""
    selected_stages = _resolve_stage_list(
        stages=stages,
        skip=skip,
    )
    if selected_stages:
        print("Selected stages:", ", ".join(selected_stages))
    else:
        print("Selected stages: (none)")

    stage_times = []
    context = {"cfg": None}
    autosave_state = None
    undo_state = None

    try:
        if "reset" in selected_stages:
            time_stage("reset", reset, stage_times)

        if "load_config" in selected_stages:
            context["cfg"] = time_stage("load_config", load_config, stage_times, config_name)

        cfg = context["cfg"] or {}
        preparation_cfg = dict(cfg.get("preparation") or {})
        if any(stage in selected_stages for stage in ("preparation", "modeling", "rendering")):
            autosave_state = _apply_run_autosave_policy(preparation_cfg)
            undo_state = _apply_run_undo_policy(preparation_cfg)

        if "preparation" in selected_stages:
            preparation_params = dict(preparation_cfg)
            exclude_layer_prefixes = list(preparation_params.get("exclude_layer_prefixes") or [])

            modeling_cfg = dict(cfg.get("modeling") or {})
            if str(modeling_cfg.get("strategy") or "").lower() == "component":
                debug_cfg = dict(modeling_cfg.get("debug") or {})
                surface_normals_cfg = dict(debug_cfg.get("surface_normals") or {})
                defect_normals_cfg = dict(debug_cfg.get("defect_normals") or {})
                reference_points_cfg = debug_cfg.get("reference_points")
                defect_seeds_cfg = dict(debug_cfg.get("defect_seeds") or {})

                if isinstance(reference_points_cfg, dict):
                    reference_points_enabled = bool(reference_points_cfg.get("enabled", False))
                    reference_points_layer = str(reference_points_cfg.get("layer") or "debug::reference_points")
                else:
                    reference_points_enabled = bool(reference_points_cfg)
                    reference_points_layer = "debug::reference_points"

                if not (
                    bool(surface_normals_cfg.get("enabled", False))
                    or bool(defect_normals_cfg.get("enabled", False))
                ):
                    exclude_layer_prefixes.append("debug::normal")

                if not reference_points_enabled:
                    exclude_layer_prefixes.append(reference_points_layer)

                if not bool(defect_seeds_cfg.get("enabled", True)):
                    exclude_layer_prefixes.append(str(defect_seeds_cfg.get("layer") or "debug::seed"))

            if exclude_layer_prefixes:
                preparation_params["exclude_layer_prefixes"] = exclude_layer_prefixes
            time_stage(
                "preparation",
                _run_with_suspended_view_updates,
                stage_times,
                prepare,
                params=preparation_params,
            )

        if "view_setup" in selected_stages:
            time_stage("view_setup", setup_render_view, stage_times, cfg=cfg)

        if "modeling" in selected_stages:
            modeling_params = dict(cfg.get("modeling", {}))
            if not modeling_params:
                raise ValueError("Selected 'modeling' stage but config has no 'modeling' section.")
            if modeling_params["strategy"] == "cube":
                modeling_params["start_face_index"] = start_face_index
            time_stage(
                "modeling",
                _run_with_suspended_view_updates,
                stage_times,
                create_model,
                params=modeling_params,
            )

        if "rendering" in selected_stages:
            rendering_params = cfg.get("rendering", {})
            if not rendering_params:
                raise ValueError("Selected 'rendering' stage but config has no 'rendering' section.")
            time_stage(
                "rendering",
                run_render,
                stage_times,
                params=rendering_params,
                show_cameras=show_cameras,
            )
            _stability_wait(redraw=False)
    finally:
        _restore_run_undo_policy(undo_state)
        _restore_run_autosave_policy(autosave_state)

    if print_timings:
        total = 0.0
        print("======== Stage Timings ========")
        for name, duration in stage_times:
            total += duration
            print(f"{name}: {duration:.2f}s")
        print(f"total: {total:.2f}s")

    return stage_times


if __name__ == "__main__":
    # Rhino Python entrypoint.
    # Edit `run(...)` below for debug presets.
    #
    # Parameter guide:
    # - config_name: YAML filename under `configs/`.
    # - stages: explicit stage list in execution order subset.
    #   Valid: reset, load_config, preparation, view_setup, modeling, rendering
    #   Use None or [] to run no stages.
    # - skip: remove stages from the selected set (dependencies are validated).
    # - start_face_index: forwarded to modeling stage (cube face debug).
    # - show_cameras: for rendering stage, draw camera gizmos and exit.
    # - print_timings: print per-stage timing summary.
    #
    # Common presets:
    # 1) Modeling only from face 3:
    #    run(stages=["modeling"], start_face_index=3)
    # 2) Full run with cleanup + rendering:
    #    run(stages=["reset", "load_config", "preparation", "view_setup", "modeling", "rendering"])
    # 3) Preparation only:
    #    run(stages=["preparation"])

    # run(
    #     config_name="cube.local.yaml",
    #     stages=["preparation", "rendering"],
    #     skip=[],
    #     start_face_index=0,
    #     show_cameras=False,
    #     print_timings=True,
    # )

    run(
        config_name="component.local.yaml",
        stages=["reset", "load_config", "preparation", "view_setup", "modeling"],
        skip=[],
        show_cameras=False,
        print_timings=True,
    )
