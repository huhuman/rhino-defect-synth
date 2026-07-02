#! python3
"""Nested modeling/render driver for multi-variant dataset generation."""

import copy
import importlib
import json
import os
import random
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

from utils_loc.config import load_config
from utils_loc.layer_utils import normalize_layer_name_set, layer_matches
from utils_loc.logging_utils import install_timestamped_print
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare
from utils_loc.batch_utils import (
    start_batch_logging,
    stop_batch_logging,
    flush_batch_state,
    create_timestamped_subdir,
    sample_value,
    deep_update,
    sample_rendering_params,
    to_non_negative_int,
    to_non_negative_float,
    set_active_view_display_mode,
    set_batch_work_view,
    suspend_view_updates,
    stability_wait,
    run_gc,
    clear_undo_records,
    table_count,
    private_memory_mb,
    log_runtime_snapshot,
    memory_guard_triggered,
    basic_material_guard_triggered,
    apply_batch_autosave_policy,
    restore_batch_autosave_policy,
    apply_batch_undo_policy,
    restore_batch_undo_policy,
    resolve_stability_cfg,
    sample_mask_foreground,
)

install_timestamped_print()

render = importlib.import_module("utils_loc.render")


def _reset_scene_objects():
    """Delete all objects from the active document."""
    objs = rs.AllObjects(
        select=False,
        include_lights=True,
        include_grips=True,
    )
    if objs:
        rs.DeleteObjects(objs)


def _setup_render_view(cfg=None):
    """Set active view mode and configure layer visibility."""
    render_view = sc.doc.Views.ActiveView
    if render_view is None:
        raise RuntimeError("No active Rhino view available for batch rendering.")
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    cfg = cfg or {}
    view_setup_cfg = cfg.get("view_setup") or {}
    only_set = normalize_layer_name_set(view_setup_cfg.get("only_layers"))
    hide_set = normalize_layer_name_set(view_setup_cfg.get("hide_layers"))

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
            layer.IsVisible = layer_matches(layer, only_set)
        else:
            layer.IsVisible = True
        if hide_set and layer_matches(layer, hide_set):
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
            stability_wait(wait_ms=wait_on_retry_ms, redraw=True)
            run_gc()


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
    undo_state = None
    batch_state_path = None
    batch_state = None
    global_random_state = None

    cfg = load_config(config_name)
    nested_cfg = cfg.get("nested_loop", {})
    preparation_scope = _resolve_preparation_scope(nested_cfg)
    stability_cfg = resolve_stability_cfg(nested_cfg)
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

    batch_output_dir = create_timestamped_subdir(base_rendering.get("output_dir"))
    base_rendering["output_dir"] = batch_output_dir
    batch_log_path = os.path.join(batch_output_dir, "batch_log.txt")
    batch_state_path = os.path.join(batch_output_dir, "batch_state.json")

    try:
        log_file, stdout_backup, stderr_backup = start_batch_logging(batch_log_path)
        print(f"Batch output directory: {batch_output_dir}")
        print(f"Batch log path: {batch_log_path}")
        print(f"Batch state path: {batch_state_path}")

        global_random_state = random.getstate()
        if seed is None:
            seed = nested_cfg.get("seed")
        if seed is not None:
            rng = random.Random(int(seed))
            random.seed(int(seed))
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

        autosave_state = apply_batch_autosave_policy(preparation_params)
        undo_state = apply_batch_undo_policy(preparation_params)

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
            f"gc_every_capture_frames={stability_cfg['gc_every_capture_frames']}, "
            f"gc_every_render_passes={stability_cfg['gc_every_render_passes']}, "
            f"gc_every_model_iters={stability_cfg['gc_every_model_iters']}, "
            f"wait_after_capture_frame_ms={stability_cfg['wait_after_capture_frame_ms']}, "
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
        safe_resume_face_index = int(model_starts[0]) if model_starts else None
        safe_resume_output_index = int(next_output_index)
        stop_after_preview = False
        stop_after_guard = False
        mask_gate_failed = False
        render_pass_count = 0
        current_face_idx = None
        current_model_iter = None
        current_render_iter = None
        current_arrangement_label = None

        batch_state = {
            "status": "running",
            "config_name": config_name,
            "batch_output_dir": batch_output_dir,
            "batch_log_path": batch_log_path,
            "safe_resume": {
                "start_face_index": safe_resume_face_index,
                "output_index_start": safe_resume_output_index,
                "note": "Safe resume points are exact only at completed model boundaries.",
            },
            "progress": {
                "models_total": len(model_starts),
                "renders_per_model": renders_per_model,
                "arrangement_passes": arrangement_passes,
                "render_pass_count": render_pass_count,
                "next_output_index": next_output_index,
            },
            "current": {
                "model_iter": None,
                "start_face_index": None,
                "render_iter": None,
                "arrangement": None,
                "stage": "startup",
            },
        }
        flush_batch_state(batch_state_path, batch_state)

        for model_iter, face_idx in enumerate(model_starts):
            current_face_idx = int(face_idx)
            current_model_iter = int(model_iter)
            current_render_iter = None
            current_arrangement_label = None
            print(f"=== model_iter={model_iter}, start_face_index={face_idx} ===")
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "start_face_index": current_face_idx,
                "render_iter": None,
                "arrangement": None,
                "stage": "model_setup",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)
            log_runtime_snapshot(
                label=f"before_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break
            if basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

            time_model_stage_start = perf_counter()
            with suspend_view_updates():
                set_batch_work_view()
                _reset_scene_objects()
            stability_wait(
                wait_ms=stability_cfg["wait_after_reset_ms"],
                redraw=True,
            )
            with suspend_view_updates():
                set_batch_work_view()
                # Materials-half memory fix (mirrors main_component_batch): when material_reuse is on,
                # KEEP imported materials across models so textures decode once (the import path reuses
                # by name) instead of re-decoding every model — the native-texture-cache leak that
                # trips the memory guard. Off -> original per-model clear.
                if not bool(preparation_params.get("material_reuse", False)):
                    clear_imported_materials_from_doc()
                model_prepare_params = dict(preparation_params)
                if preparation_scope != "model_iter":
                    model_prepare_params["_import_materials"] = False
                prepare(model_prepare_params)
            stability_wait(
                wait_ms=stability_cfg["wait_after_preparation_ms"],
                redraw=True,
            )
            with suspend_view_updates():
                set_batch_work_view()
                model_params = dict(modeling_params)
                model_params["start_face_index"] = int(face_idx)
                create_model(model_params)
            stability_wait(
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
                        with suspend_view_updates():
                            set_batch_work_view()
                            if not bool(preparation_params.get("material_reuse", False)):
                                clear_imported_materials_from_doc()
                            prepare(dict(preparation_params))
                        prepared_for_render_iter = True
                        stability_wait(
                            wait_ms=stability_cfg["wait_after_preparation_ms"],
                            redraw=True,
                        )

                    _setup_render_view(cfg=cfg)
                    stability_wait(
                        wait_ms=stability_cfg["wait_before_render_ms"],
                        redraw=True,
                    )

                    render_params = sample_rendering_params(
                        base_rendering=base_rendering,
                        rendering_sampler=rendering_sampler,
                        rng=rng,
                    )
                    render_params["model_iter"] = model_iter
                    render_params["render_iter"] = render_iter
                    render_params["output_index_offset"] = next_output_index
                    render_params["capture_gc_every_frames"] = stability_cfg[
                        "gc_every_capture_frames"
                    ]
                    render_params["capture_wait_after_frame_ms"] = stability_cfg[
                        "wait_after_capture_frame_ms"
                    ]

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

                    mask_dir = os.path.join(batch_output_dir, "mask")
                    pre_mask_files = (
                        set(os.listdir(mask_dir)) if os.path.isdir(mask_dir) else set()
                    )

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

                    # Mask-coverage gate: stop safely if this pass's masks came back blank
                    # (see component batch + the 2026-06-20 DrawToBitmap-on-display-sleep RCA).
                    min_mask_fg = stability_cfg.get("min_mask_foreground_frac", 0.0)
                    if (
                        not show_cameras
                        and min_mask_fg > 0.0
                        and captured_count > 0
                        and os.path.isdir(mask_dir)
                    ):
                        new_masks = sorted(
                            os.path.join(mask_dir, f)
                            for f in (set(os.listdir(mask_dir)) - pre_mask_files)
                            if f.lower().endswith(".png")
                        )
                        mean_fg, n_sampled = sample_mask_foreground(new_masks)
                        if mean_fg is not None:
                            print(
                                f"[maskgate] model_iter={model_iter} render_iter={render_iter} "
                                f"arrangement={arrangement_label}: mean_mask_foreground="
                                f"{mean_fg * 100:.2f}% over {n_sampled} frames "
                                f"(threshold={min_mask_fg * 100:.2f}%)"
                            )
                            if mean_fg < min_mask_fg:
                                print(
                                    f"[maskgate] BLANK MASKS at model_iter={model_iter} "
                                    f"({mean_fg * 100:.2f}% < {min_mask_fg * 100:.2f}%). "
                                    "Stopping run safely. Likely the display slept/locked "
                                    "(DrawToBitmap GL-context loss) or the mask plugin drew "
                                    "nothing. Keep the screen awake and/or apply the "
                                    "ViewCapture plugin fix, then resume."
                                )
                                mask_gate_failed = True
                                stop_after_guard = True
                                break

                    stage_times.append(
                        (
                            f"preparation->rendering[{model_iter},{render_iter},{arrangement_label}]",
                            perf_counter() - time_render_stage_start,
                        )
                    )
                    stability_wait(
                        wait_ms=stability_cfg["wait_after_render_ms"],
                        redraw=False,
                    )
                    render_pass_count += 1
                    current_render_iter = int(render_iter)
                    current_arrangement_label = arrangement_label
                    batch_state["current"] = {
                        "model_iter": current_model_iter,
                        "start_face_index": current_face_idx,
                        "render_iter": current_render_iter,
                        "arrangement": current_arrangement_label,
                        "stage": "post_render_pass",
                    }
                    batch_state["progress"]["render_pass_count"] = render_pass_count
                    batch_state["progress"]["next_output_index"] = next_output_index
                    flush_batch_state(batch_state_path, batch_state)
                    if preparation_scope != "model_iter":
                        with suspend_view_updates():
                            set_batch_work_view()
                            if not bool(preparation_params.get("material_reuse", False)):
                                clear_imported_materials_from_doc()
                    if (
                        stability_cfg["gc_every_render_passes"] > 0
                        and render_pass_count % stability_cfg["gc_every_render_passes"] == 0
                    ):
                        run_gc()
                    if (
                        stability_cfg["clear_undo_every_render_passes"] > 0
                        and render_pass_count % stability_cfg["clear_undo_every_render_passes"] == 0
                    ):
                        cleared = clear_undo_records()
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
                    if memory_guard_triggered(
                        stability_cfg=stability_cfg,
                        label=(
                            f"after_render_pass[{model_iter},{render_iter},{arrangement_label}]"
                        ),
                    ):
                        stop_after_guard = True
                        break
                    if basic_material_guard_triggered(
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
                cleared = clear_undo_records()
                print(
                    f"[stability] clear_undo_after_model_iter={model_iter_1based}: "
                    f"{'ok' if cleared else 'unsupported'}"
                )
            if (
                stability_cfg["gc_every_model_iters"] > 0
                and model_iter_1based % stability_cfg["gc_every_model_iters"] == 0
            ):
                run_gc()
            next_model_idx = model_iter + 1
            safe_resume_face_index = (
                int(model_starts[next_model_idx]) if next_model_idx < len(model_starts) else None
            )
            safe_resume_output_index = int(next_output_index)
            batch_state["safe_resume"] = {
                "start_face_index": safe_resume_face_index,
                "output_index_start": safe_resume_output_index,
                "note": "Safe resume points are exact only at completed model boundaries.",
            }
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "start_face_index": current_face_idx,
                "render_iter": None,
                "arrangement": None,
                "stage": "completed_model",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)
            log_runtime_snapshot(
                label=f"after_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break
            if basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

        if stop_after_guard:
            batch_state["status"] = (
                "failed_mask_gate" if mask_gate_failed else "stopped_by_guard"
            )
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "start_face_index": current_face_idx,
                "render_iter": current_render_iter,
                "arrangement": current_arrangement_label,
                "stage": "mask_gate_stop" if mask_gate_failed else "guard_stop",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)
            print(
                "[stability] run stopped early by guard. "
                f"safe_resume: start_face_index={safe_resume_face_index}, "
                f"output_index_start={safe_resume_output_index}"
            )
            if current_face_idx != safe_resume_face_index:
                print(
                    "[stability] note: guard tripped mid-model. Resume from the safe model "
                    "boundary above to avoid partial-iteration duplication."
                )

        if stop_after_preview:
            batch_state["status"] = "preview_stopped"
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "start_face_index": current_face_idx,
                "render_iter": current_render_iter,
                "arrangement": current_arrangement_label,
                "stage": "preview_stop",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)

        if not stop_after_guard and not stop_after_preview:
            batch_state["status"] = "completed"
            batch_state["current"] = {
                "model_iter": None,
                "start_face_index": None,
                "render_iter": None,
                "arrangement": None,
                "stage": "completed",
            }
            batch_state["safe_resume"] = {
                "start_face_index": None,
                "output_index_start": next_output_index,
                "note": "Batch completed; no further resume point is required.",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)

        if print_timings:
            total = perf_counter() - timer_start
            print("======== Nested Stage Timings ========")
            for name, duration in stage_times:
                print(f"{name}: {duration:.2f}s")
            print(f"total: {total:.2f}s")

        return stage_times
    except Exception as exc:
        if batch_state is not None:
            batch_state["status"] = "failed"
            batch_state["error"] = f"{type(exc).__name__}: {exc}"
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "start_face_index": current_face_idx,
                "render_iter": current_render_iter,
                "arrangement": current_arrangement_label,
                "stage": "exception",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)
        raise
    finally:
        if global_random_state is not None:
            try:
                random.setstate(global_random_state)
            except Exception:
                pass
        restore_batch_undo_policy(undo_state)
        restore_batch_autosave_policy(autosave_state)
        stop_batch_logging(log_file, stdout_backup, stderr_backup)


if __name__ == "__main__":
    run(
        config_name="cube.local.yaml",
        renders_per_model=None,
        max_iter=None,
        start_face_index=0,
        faces_per_model=6,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
