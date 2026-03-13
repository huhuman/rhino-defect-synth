#! python3
"""Nested modeling/render driver for multi-variant dataset generation."""

import copy
import gc
import importlib
import os
import random
import sys
from datetime import datetime
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.config import load_config
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare

render = importlib.import_module("utils_loc.render")


class _TeeWriter:
    """Mirror writes to multiple stream targets."""

    def __init__(self, *targets):
        self._targets = [target for target in targets if target is not None]

    def write(self, data):
        for target in self._targets:
            target.write(data)
        return len(data)

    def flush(self):
        for target in self._targets:
            target.flush()

    def isatty(self):
        for target in self._targets:
            if hasattr(target, "isatty"):
                try:
                    return bool(target.isatty())
                except Exception:
                    continue
        return False


def _start_batch_logging(log_path):
    log_file = open(log_path, "w", encoding="utf-8")
    stdout_backup = sys.stdout
    stderr_backup = sys.stderr
    sys.stdout = _TeeWriter(stdout_backup, log_file)
    sys.stderr = _TeeWriter(stderr_backup, log_file)
    return log_file, stdout_backup, stderr_backup


def _stop_batch_logging(log_file, stdout_backup, stderr_backup):
    if stdout_backup is not None:
        sys.stdout = stdout_backup
    if stderr_backup is not None:
        sys.stderr = stderr_backup
    if log_file is not None:
        log_file.close()


def _create_timestamped_subdir(base_output_dir):
    if not str(base_output_dir or "").strip():
        raise ValueError("Config must include rendering.output_dir for batch runs.")

    base_dir = os.path.abspath(os.path.expanduser(str(base_output_dir)))
    os.makedirs(base_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = os.path.join(base_dir, stamp)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, f"{stamp}_{suffix:02d}")
        suffix += 1

    os.makedirs(candidate, exist_ok=False)
    return candidate


def _reset_scene_objects():
    """Delete all objects from the active document."""
    objs = rs.AllObjects(
        select=False,
        include_lights=True,
        include_grips=True,
    )
    if objs:
        rs.DeleteObjects(objs)


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
        tail = full_path.split("::")[-1]
        if tail in names:
            return True
    return False


def _setup_render_view(cfg=None):
    """Set active view mode and configure layer visibility."""
    render_view = sc.doc.Views.ActiveView
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    cfg = cfg or {}
    view_setup_cfg = cfg.get("view_setup") or {}
    only_set = _normalize_layer_name_set(view_setup_cfg.get("only_layers"))
    hide_set = _normalize_layer_name_set(view_setup_cfg.get("hide_layers"))

    if not only_set and not hide_set:
        # Backward-compatible default behavior for existing batch presets.
        for layer in sc.doc.Layers:
            if layer.Name:
                layer.IsVisible = "CS" not in layer.Name
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


def _list_json_files(folder_path):
    if not folder_path:
        raise ValueError("modeling.cube.cube_map_dir is required.")
    if not os.path.isdir(folder_path):
        raise ValueError(f"cube_map_dir does not exist: '{folder_path}'")
    return sorted(
        filename for filename in os.listdir(folder_path) if filename.lower().endswith(".json")
    )


def _compute_model_start_indices(total_files, start_face_index, faces_per_model):
    start = max(0, int(start_face_index))
    step = max(1, int(faces_per_model))
    if step != 6:
        print(f"Warning: expected faces_per_model=6 for cube workflow, got {step}.")

    if start >= total_files:
        return [], 0

    remaining = total_files - start
    model_count = remaining // step
    stop = start + model_count * step
    leftovers = remaining - model_count * step
    return list(range(start, stop, step)), leftovers


def _sample_value(spec, rng):
    """Sample one value from a sampler spec."""
    if isinstance(spec, (list, tuple)):
        if not spec:
            raise ValueError("Sampler list cannot be empty.")
        return copy.deepcopy(rng.choice(list(spec)))

    if isinstance(spec, dict):
        if "min" in spec and "max" in spec and len(spec) <= 3:
            min_value = float(spec["min"])
            max_value = float(spec["max"])
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            sampled = rng.uniform(min_value, max_value)
            if spec.get("type") == "int":
                return int(round(sampled))
            return sampled
        return {key: _sample_value(value, rng) for key, value in spec.items()}

    return copy.deepcopy(spec)


def _deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def _sample_rendering_params(base_rendering, rendering_sampler, rng):
    params = copy.deepcopy(base_rendering or {})
    if not rendering_sampler:
        return params

    sampled_overrides = _sample_value(rendering_sampler, rng)
    if not isinstance(sampled_overrides, dict):
        raise ValueError("nested_loop.rendering_sampler must resolve to a dict.")
    return _deep_update(params, sampled_overrides)


def _resolve_camera_arrangements(nested_cfg):
    raw = nested_cfg.get("camera_arrangements")
    if raw is None:
        return (None,)

    if isinstance(raw, (str, bytes)):
        candidates = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        raise ValueError(
            "nested_loop.camera_arrangements must be a string or a list of strings."
        )

    allowed = {"grid", "spherical"}
    resolved = []
    seen = set()
    for item in candidates:
        arrangement = str(item).strip().lower()
        if not arrangement:
            continue
        if arrangement not in allowed:
            raise ValueError(
                f"Unsupported nested_loop.camera_arrangements entry '{arrangement}'. "
                "Expected one of: grid, spherical."
            )
        if arrangement in seen:
            continue
        seen.add(arrangement)
        resolved.append(arrangement)

    if not resolved:
        return (None,)
    return tuple(resolved)


def _count_render_frames(poses, smooth_path, transition_frames):
    pose_count = len(poses or [])
    if pose_count <= 0:
        return 0
    if bool(smooth_path) and int(transition_frames) > 0:
        return pose_count + (pose_count - 1) * int(transition_frames)
    return pose_count


def _resolve_preparation_scope(nested_cfg):
    raw = str((nested_cfg or {}).get("preparation_scope", "arrangement")).strip().lower()
    allowed = {"arrangement", "render_iter", "model_iter"}
    if raw not in allowed:
        raise ValueError(
            "nested_loop.preparation_scope must be one of: arrangement, render_iter, model_iter."
        )
    return raw


def _to_non_negative_int(value, default=0):
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(0, parsed)


def _to_non_negative_float(value, default=0.0):
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(0.0, parsed)


def _resolve_stability_cfg(nested_cfg):
    raw = (nested_cfg or {}).get("stability")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("nested_loop.stability must be a dict when provided.")

    enabled = bool(raw.get("enabled", True))
    cfg = {
        "enabled": enabled,
        "wait_after_reset_ms": _to_non_negative_int(raw.get("wait_after_reset_ms", 20)),
        "wait_after_preparation_ms": _to_non_negative_int(
            raw.get("wait_after_preparation_ms", 40)
        ),
        "wait_before_render_ms": _to_non_negative_int(raw.get("wait_before_render_ms", 40)),
        "wait_after_render_ms": _to_non_negative_int(raw.get("wait_after_render_ms", 60)),
        "wait_on_retry_ms": _to_non_negative_int(raw.get("wait_on_retry_ms", 400)),
        "render_retry_count": _to_non_negative_int(raw.get("render_retry_count", 1)),
        "gc_every_render_passes": _to_non_negative_int(raw.get("gc_every_render_passes", 1)),
        "gc_every_model_iters": _to_non_negative_int(raw.get("gc_every_model_iters", 1)),
        "clear_undo_every_render_passes": _to_non_negative_int(
            raw.get("clear_undo_every_render_passes", 1)
        ),
        "clear_undo_every_model_iters": _to_non_negative_int(
            raw.get("clear_undo_every_model_iters", 1)
        ),
        "max_private_memory_mb": _to_non_negative_float(
            raw.get("max_private_memory_mb", 0.0)
        ),
        "max_render_passes_per_run": _to_non_negative_int(
            raw.get("max_render_passes_per_run", 0)
        ),
        "max_basic_materials": _to_non_negative_int(raw.get("max_basic_materials", 0)),
        "log_memory": bool(raw.get("log_memory", True)),
    }

    if not enabled:
        cfg.update(
            {
                "wait_after_reset_ms": 0,
                "wait_after_preparation_ms": 0,
                "wait_before_render_ms": 0,
                "wait_after_render_ms": 0,
                "wait_on_retry_ms": 0,
                "render_retry_count": 0,
                "gc_every_render_passes": 0,
                "gc_every_model_iters": 0,
                "clear_undo_every_render_passes": 0,
                "clear_undo_every_model_iters": 0,
                "max_private_memory_mb": 0.0,
                "max_render_passes_per_run": 0,
                "max_basic_materials": 0,
                "log_memory": False,
            }
        )

    return cfg


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


def _table_count(table):
    if table is None:
        return None
    try:
        return int(getattr(table, "Count"))
    except Exception:
        return None


def _private_memory_mb():
    process = None
    try:
        import System

        process = System.Diagnostics.Process.GetCurrentProcess()
        return float(process.PrivateMemorySize64) / (1024.0 * 1024.0)
    except Exception:
        return None
    finally:
        dispose = getattr(process, "Dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                pass


def _log_runtime_snapshot(label, enabled=False):
    if not enabled:
        return

    parts = []
    obj_count = _table_count(getattr(sc.doc, "Objects", None))
    if obj_count is not None:
        parts.append(f"objects={obj_count}")

    layer_count = _table_count(getattr(sc.doc, "Layers", None))
    if layer_count is not None:
        parts.append(f"layers={layer_count}")

    render_mat_count = _table_count(getattr(sc.doc, "RenderMaterials", None))
    if render_mat_count is not None:
        parts.append(f"render_materials={render_mat_count}")

    basic_mat_count = _table_count(getattr(sc.doc, "Materials", None))
    if basic_mat_count is not None:
        parts.append(f"basic_materials={basic_mat_count}")

    memory_mb = _private_memory_mb()
    if memory_mb is not None:
        parts.append(f"private_mem_mb={memory_mb:.1f}")

    if not parts:
        return
    print(f"[runtime] {label}: {', '.join(parts)}")


def _clear_undo_records():
    clear_fn = getattr(sc.doc, "ClearUndoRecords", None)
    if callable(clear_fn):
        try:
            clear_fn()
            return True
        except Exception:
            return False
    return False


def _run_gc():
    try:
        gc.collect()
    except Exception:
        pass
    try:
        import System

        System.GC.Collect()
        wait_fn = getattr(System.GC, "WaitForPendingFinalizers", None)
        if callable(wait_fn):
            wait_fn()
    except Exception:
        pass


def _run_render_with_retry(params, show_cameras=False, retry_count=0, wait_on_retry_ms=0):
    max_retries = max(0, int(retry_count))
    for attempt in range(max_retries + 1):
        try:
            return _run_render_with_frame_count(params=params, show_cameras=show_cameras)
        except Exception as exc:
            if attempt >= max_retries:
                raise
            print(
                f"Render pass failed ({attempt + 1}/{max_retries + 1}): {exc}. "
                f"Retrying after {int(wait_on_retry_ms)} ms..."
            )
            _stability_wait(wait_ms=wait_on_retry_ms, redraw=True)
            _run_gc()


def _memory_guard_triggered(stability_cfg, label=""):
    limit_mb = float(stability_cfg.get("max_private_memory_mb") or 0.0)
    if limit_mb <= 0.0:
        return False

    current_mb = _private_memory_mb()
    if current_mb is None:
        return False
    if current_mb < limit_mb:
        return False

    label = str(label or "runtime")
    print(
        f"[stability] memory_guard_triggered at {label}: "
        f"private_mem_mb={current_mb:.1f}, limit_mb={limit_mb:.1f}"
    )
    return True


def _basic_material_guard_triggered(stability_cfg, label=""):
    limit = int(stability_cfg.get("max_basic_materials") or 0)
    if limit <= 0:
        return False

    current = _table_count(getattr(sc.doc, "Materials", None))
    if current is None:
        return False
    if current <= limit:
        return False

    label = str(label or "runtime")
    print(
        f"[stability] basic_material_guard_triggered at {label}: "
        f"basic_materials={current}, limit={limit}"
    )
    return True


def _apply_batch_autosave_policy(preparation_params):
    prep_cfg = dict(preparation_params or {})
    autosave_cfg = dict(prep_cfg.get("autosave") or {})
    disable_during_batch = bool(autosave_cfg.get("disable_during_batch", True))
    if not disable_during_batch:
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
        print("[stability] autosave policy: disabled during batch.")
    else:
        print("[stability] autosave policy: no supported autosave property found.")
    return state


def _restore_batch_autosave_policy(state):
    state = dict(state or {})
    if not state:
        return
    if not bool(state.get("changed")):
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


def _run_render_with_frame_count(params, show_cameras=False):
    """Run render stage and return (captured_frame_count, preview_mode_used)."""
    render.setup_render_environment(params)
    context = render.build_render_context(params)
    if context is None:
        return 0, False

    render.redraw_views()
    poses = render.generate_render_poses(context)
    print(f"Generated {len(poses)} camera poses for rendering.")

    if show_cameras:
        print("show_cameras=True; drawing camera gizmos and exiting.")
        render.preview_camera_gizmos(poses, context["lengths"])
        return 0, True

    render.capture_pose_sequence(poses, context)
    frame_count = _count_render_frames(
        poses=poses,
        smooth_path=context.get("smooth_path", False),
        transition_frames=context.get("transition_frames", 0),
    )
    return frame_count, False


def run(
    config_name="cube_render.yaml",
    renders_per_model=None,
    max_iter=None,
    start_face_index=0,
    faces_per_model=6,
    seed=None,
    show_cameras=False,
    print_timings=True,
):
    """
    Run nested loops:
      model_iter over cube face-map chunks
      render_iter over random render variants per model
    """
    stage_times = []
    timer_start = perf_counter()
    log_file = None
    stdout_backup = None
    stderr_backup = None
    autosave_state = None

    cfg = load_config(config_name)
    nested_cfg = cfg.get("nested_loop", {})
    preparation_scope = _resolve_preparation_scope(nested_cfg)
    stability_cfg = _resolve_stability_cfg(nested_cfg)
    preparation_params = dict(cfg.get("preparation") or {})

    modeling_params = dict(cfg.get("modeling", {}))
    if not modeling_params:
        raise ValueError("Config must include a 'modeling' section.")
    if modeling_params.get("strategy") != "cube":
        raise ValueError("Nested runner currently supports only modeling.strategy='cube'.")
    cube_params = dict(modeling_params.get("cube") or {})
    if not cube_params:
        raise ValueError("Nested runner requires modeling.cube for cube strategy.")

    base_rendering = dict(cfg.get("rendering", {}))
    if not base_rendering:
        raise ValueError("Config must include a 'rendering' section.")

    batch_output_dir = _create_timestamped_subdir(base_rendering.get("output_dir"))
    base_rendering["output_dir"] = batch_output_dir
    batch_log_path = os.path.join(batch_output_dir, "batch_log.txt")

    try:
        log_file, stdout_backup, stderr_backup = _start_batch_logging(batch_log_path)
        print(f"Batch output directory: {batch_output_dir}")
        print(f"Batch log path: {batch_log_path}")

        if seed is None:
            seed = nested_cfg.get("seed")
        if seed is not None:
            rng = random.Random(int(seed))
            print(f"Random seed: {int(seed)}")
        else:
            rng = random.Random()

        if renders_per_model is None:
            renders_per_model = int(nested_cfg.get("renders_per_model", 1))
        renders_per_model = max(1, int(renders_per_model))

        if max_iter is None:
            max_iter = nested_cfg.get("max_iter")
        if max_iter is not None:
            max_iter = max(0, int(max_iter))

        rendering_sampler = nested_cfg.get("rendering_sampler", {})
        camera_arrangements = _resolve_camera_arrangements(nested_cfg)
        arrangement_passes = len(camera_arrangements)

        autosave_state = _apply_batch_autosave_policy(preparation_params)

        json_files = _list_json_files(cube_params.get("cube_map_dir"))
        model_starts, leftovers = _compute_model_start_indices(
            total_files=len(json_files),
            start_face_index=start_face_index,
            faces_per_model=faces_per_model,
        )
        total_model_iters = len(model_starts)
        if max_iter is not None:
            model_starts = model_starts[:max_iter]

        if leftovers > 0:
            print(
                f"Skipping trailing {leftovers} face maps because each model iteration needs "
                f"{int(faces_per_model)} maps."
            )

        print(
            f"Nested run: models={len(model_starts)}, renders_per_model={renders_per_model}, "
            f"arrangement_passes={arrangement_passes}, "
            f"total_render_passes={len(model_starts) * renders_per_model * arrangement_passes}"
        )
        print(f"Preparation scope: {preparation_scope}")
        print(
            "Stability controls: "
            f"enabled={stability_cfg['enabled']}, "
            f"render_retry_count={stability_cfg['render_retry_count']}, "
            f"gc_every_render_passes={stability_cfg['gc_every_render_passes']}, "
            f"gc_every_model_iters={stability_cfg['gc_every_model_iters']}, "
            f"clear_undo_every_render_passes={stability_cfg['clear_undo_every_render_passes']}, "
            f"clear_undo_every_model_iters={stability_cfg['clear_undo_every_model_iters']}, "
            f"max_private_memory_mb={stability_cfg['max_private_memory_mb']}, "
            f"max_render_passes_per_run={stability_cfg['max_render_passes_per_run']}, "
            f"max_basic_materials={stability_cfg['max_basic_materials']}"
        )
        if arrangement_passes > 1:
            print(f"Camera arrangements per render_iter: {list(camera_arrangements)}")
        if max_iter is not None:
            print(
                f"Iteration cap: max_iter={max_iter}, available_iters={total_model_iters}, "
                f"using_iters={len(model_starts)}"
            )

        next_output_index = int(nested_cfg.get("output_index_start", 0))
        stop_after_preview = False
        stop_after_guard = False
        render_pass_count = 0

        for model_iter, face_idx in enumerate(model_starts):
            print(f"=== model_iter={model_iter}, start_face_index={face_idx} ===")
            _log_runtime_snapshot(
                label=f"before_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if _memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break
            if _basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

            time_model_stage_start = perf_counter()
            _reset_scene_objects()
            _stability_wait(
                wait_ms=stability_cfg["wait_after_reset_ms"],
                redraw=True,
            )
            clear_imported_materials_from_doc()
            model_prepare_params = dict(preparation_params)
            if preparation_scope != "model_iter":
                model_prepare_params["_import_materials"] = False
            prepare(model_prepare_params)
            _stability_wait(
                wait_ms=stability_cfg["wait_after_preparation_ms"],
                redraw=True,
            )
            _setup_render_view(cfg=cfg)

            model_params = dict(modeling_params)
            model_params["start_face_index"] = int(face_idx)
            create_model(model_params)
            _stability_wait(
                wait_ms=stability_cfg["wait_before_render_ms"],
                redraw=True,
            )
            stage_times.append(
                (f"reset->modeling[{model_iter}]", perf_counter() - time_model_stage_start)
            )

            for render_iter in range(renders_per_model):
                prepared_for_render_iter = False
                for arrangement in camera_arrangements:
                    time_render_stage_start = perf_counter()
                    should_prepare = False
                    if preparation_scope == "arrangement":
                        should_prepare = True
                    elif preparation_scope == "render_iter":
                        should_prepare = not prepared_for_render_iter

                    if should_prepare:
                        clear_imported_materials_from_doc()
                        prepare(dict(preparation_params))
                        prepared_for_render_iter = True
                        _stability_wait(
                            wait_ms=stability_cfg["wait_after_preparation_ms"],
                            redraw=True,
                        )

                    _setup_render_view(cfg=cfg)
                    _stability_wait(
                        wait_ms=stability_cfg["wait_before_render_ms"],
                        redraw=True,
                    )

                    render_params = _sample_rendering_params(
                        base_rendering=base_rendering,
                        rendering_sampler=rendering_sampler,
                        rng=rng,
                    )
                    render_params["model_iter"] = model_iter
                    render_params["render_iter"] = render_iter
                    render_params["output_index_offset"] = next_output_index

                    arrangement_label = arrangement if arrangement is not None else "config"
                    camera_cfg = render_params.get("camera")
                    if arrangement is not None:
                        if not isinstance(camera_cfg, dict):
                            raise ValueError(
                                "rendering.camera must be a dict when nested_loop.camera_arrangements is used."
                            )
                        if str(camera_cfg.get("strategy", "")).lower() != "cube":
                            raise ValueError(
                                "nested_loop.camera_arrangements requires rendering.camera.strategy='cube'."
                            )
                        cube_cfg = dict(camera_cfg.get("cube") or {})
                        cube_cfg["arrangement"] = arrangement
                        camera_cfg = dict(camera_cfg)
                        camera_cfg["cube"] = cube_cfg
                        render_params["camera"] = camera_cfg

                    captured_count, preview_used = _run_render_with_retry(
                        params=render_params,
                        show_cameras=show_cameras,
                        retry_count=stability_cfg["render_retry_count"],
                        wait_on_retry_ms=stability_cfg["wait_on_retry_ms"],
                    )
                    next_output_index += int(captured_count)
                    if captured_count > 0:
                        print(
                            f"render_iter={render_iter}, arrangement={arrangement_label}: "
                            f"captured_views={captured_count}, next_view_id={next_output_index}"
                        )
                    stage_times.append(
                        (
                            f"preparation->rendering[{model_iter},{render_iter},{arrangement_label}]",
                            perf_counter() - time_render_stage_start,
                        )
                    )
                    _stability_wait(
                        wait_ms=stability_cfg["wait_after_render_ms"],
                        redraw=False,
                    )
                    render_pass_count += 1
                    if preparation_scope != "model_iter":
                        clear_imported_materials_from_doc()
                    if (
                        stability_cfg["gc_every_render_passes"] > 0
                        and render_pass_count % stability_cfg["gc_every_render_passes"] == 0
                    ):
                        _run_gc()
                    if (
                        stability_cfg["clear_undo_every_render_passes"] > 0
                        and render_pass_count % stability_cfg["clear_undo_every_render_passes"] == 0
                    ):
                        cleared = _clear_undo_records()
                        print(
                            f"[stability] clear_undo_after_render_pass={render_pass_count}: "
                            f"{'ok' if cleared else 'unsupported'}"
                        )
                    if (
                        stability_cfg["max_render_passes_per_run"] > 0
                        and render_pass_count >= stability_cfg["max_render_passes_per_run"]
                    ):
                        print(
                            "[stability] max_render_passes_per_run reached "
                            f"({render_pass_count}). Stopping run safely."
                        )
                        stop_after_guard = True
                        break
                    if _memory_guard_triggered(
                        stability_cfg=stability_cfg,
                        label=(
                            f"after_render_pass[{model_iter},{render_iter},{arrangement_label}]"
                        ),
                    ):
                        stop_after_guard = True
                        break
                    if _basic_material_guard_triggered(
                        stability_cfg=stability_cfg,
                        label=(
                            f"after_render_pass[{model_iter},{render_iter},{arrangement_label}]"
                        ),
                    ):
                        stop_after_guard = True
                        break

                    if preview_used:
                        # Camera preview mode intentionally exits after first render pass.
                        stop_after_preview = True
                        break
                if stop_after_preview or stop_after_guard:
                    break
            if stop_after_preview or stop_after_guard:
                break

            model_iter_1based = model_iter + 1
            if (
                stability_cfg["clear_undo_every_model_iters"] > 0
                and model_iter_1based % stability_cfg["clear_undo_every_model_iters"] == 0
            ):
                cleared = _clear_undo_records()
                print(
                    f"[stability] clear_undo_after_model_iter={model_iter_1based}: "
                    f"{'ok' if cleared else 'unsupported'}"
                )
            if (
                stability_cfg["gc_every_model_iters"] > 0
                and model_iter_1based % stability_cfg["gc_every_model_iters"] == 0
            ):
                _run_gc()
            _log_runtime_snapshot(
                label=f"after_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if _memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break
            if _basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

        if stop_after_guard:
            print(
                "[stability] run stopped early by guard. "
                f"resume_hint: start_face_index={face_idx}, output_index_start={next_output_index}"
            )

        if print_timings:
            total = perf_counter() - timer_start
            print("======== Nested Stage Timings ========")
            for name, duration in stage_times:
                print(f"{name}: {duration:.2f}s")
            print(f"total: {total:.2f}s")

        return stage_times
    finally:
        _restore_batch_autosave_policy(autosave_state)
        _stop_batch_logging(log_file, stdout_backup, stderr_backup)


if __name__ == "__main__":
    run(
        config_name="cube.local.yaml",
        renders_per_model=None,
        max_iter=None,
        start_face_index=24,
        faces_per_model=6,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
