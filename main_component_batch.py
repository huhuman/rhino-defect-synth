#! python3
"""Batch driver for component-model dataset generation."""

import copy
import json
import os
import random
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc

import main as main_entry

from utils_loc.config import load_config
from utils_loc.logging_utils import install_timestamped_print
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare, run_render_demo
from utils_loc.texture_mapping import (
    apply_component_texture_mapping,
    apply_efflore_texture_mapping,
)
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
)

install_timestamped_print()

_DEFAULT_COMPONENT_CENTERLINE_SPANS_CM = [1828.8, 2133.6, 2438.4]
_DEFAULT_COMPONENT_NUM_BASE_PTS = [3, 4, 5]
_DEFAULT_COMPONENT_SLAB_WIDTHS_CM = [1097.28, 1219.2, 1341.12]
_DEFAULT_COMPONENT_SLAB_THICKNESSES_CM = [22.86, 25.4, 27.94]
_DEFAULT_COMPONENT_BEAM_SECTION_KEYS = [
    "36 I-beam",
    "42 I-beam",
    "48 I-beam",
    "54 I-beam",
    "63 bulb_t-beam",
    "72 bulb_t-beam",
    "36A IL-beam",
    "45A IL-beam",
    "54A IL-beam",
    "36B IL-beam",
    "45B IL-beam",
]
_DEFAULT_COMPONENT_PIER_PRESETS = [
    {"type": "hammerhead", "H": 1097.28, "V": 457.2, "W": 137.16},
    {"type": "hammerhead", "H": 1417.32, "V": 609.6, "W": 152.4},
    {"type": "m_column", "H": 1219.2, "V": 609.6, "W": 152.4},
    {"type": "m_column", "H": 1341.12, "V": 762.0, "W": 182.88},
]


def _enabled_channel_names(channel_cfg):
    all_names = ("color", "depth", "normal", "depth_buffer", "normal_buffer", "mask", "camera")
    if not isinstance(channel_cfg, dict):
        return list(all_names)
    return [name for name in all_names if bool(channel_cfg.get(name, True))]



def _default_component_sampler(component_cfg):
    component_cfg = dict(component_cfg or {})
    sampler = {}

    if component_cfg.get("centerline") is not None:
        sampler["centerline"] = {
            "span": list(_DEFAULT_COMPONENT_CENTERLINE_SPANS_CM),
            "num_base_pts": list(_DEFAULT_COMPONENT_NUM_BASE_PTS),
        }

    if component_cfg.get("slab") is not None:
        sampler["slab"] = {
            "width": list(_DEFAULT_COMPONENT_SLAB_WIDTHS_CM),
            "thickness": list(_DEFAULT_COMPONENT_SLAB_THICKNESSES_CM),
        }

    if component_cfg.get("beam") is not None:
        sampler["beam"] = {
            "section_key": list(_DEFAULT_COMPONENT_BEAM_SECTION_KEYS),
        }

    if component_cfg.get("pier") is not None:
        sampler["pier"] = [dict(item) for item in _DEFAULT_COMPONENT_PIER_PRESETS]

    return sampler


def _resolve_component_sampler(nested_cfg, base_component_cfg):
    raw = nested_cfg.get("component_sampler")

    if raw is None:
        modeling_sampler = nested_cfg.get("modeling_sampler")
        if isinstance(modeling_sampler, dict):
            component_sampler = modeling_sampler.get("component")
            if isinstance(component_sampler, dict):
                raw = component_sampler
            elif component_sampler is False:
                raw = False

    if raw is False:
        return {}

    if raw is None:
        return _default_component_sampler(base_component_cfg)

    if not isinstance(raw, dict):
        raise ValueError(
            "nested_loop.component_sampler must be a dict, false, or omitted."
        )
    return dict(raw)


def _sample_component_params(base_component_cfg, component_sampler, rng):
    params = copy.deepcopy(base_component_cfg or {})
    if not component_sampler:
        return params

    sampled_overrides = sample_value(component_sampler, rng)
    if not isinstance(sampled_overrides, dict):
        raise ValueError("nested_loop.component_sampler must resolve to a dict.")
    return deep_update(params, sampled_overrides)


def _assign_missing_seed(cfg, rng):
    if not isinstance(cfg, dict):
        return None
    if cfg.get("seed") is not None:
        return cfg.get("seed")
    sampled_seed = int(rng.randint(0, 2**31 - 1))
    cfg["seed"] = sampled_seed
    return sampled_seed


def _prepare_params_for_batch(preparation_params, rng):
    params = copy.deepcopy(preparation_params or {})
    _assign_missing_seed(params, rng)
    return params


def _component_dimension_snapshot(component_cfg):
    component_cfg = dict(component_cfg or {})
    centerline_cfg = dict(component_cfg.get("centerline") or {})
    slab_cfg = dict(component_cfg.get("slab") or {})
    pier_cfg = dict(component_cfg.get("pier") or {})
    beam_cfg = dict(component_cfg.get("beam") or {})
    return {
        "centerline_span": centerline_cfg.get("span"),
        "centerline_num_base_pts": centerline_cfg.get("num_base_pts"),
        "slab_width": slab_cfg.get("width"),
        "slab_thickness": slab_cfg.get("thickness"),
        "beam_num_lines": beam_cfg.get("num_lines"),
        "beam_section_key": beam_cfg.get("section_key"),
        "pier_H": pier_cfg.get("H"),
        "pier_V": pier_cfg.get("V"),
        "pier_W": pier_cfg.get("W"),
        "pier_type": pier_cfg.get("type"),
        "pier_count": pier_cfg.get("count"),
    }


def _format_dimension_snapshot(snapshot):
    ordered_keys = (
        "centerline_span",
        "centerline_num_base_pts",
        "slab_width",
        "slab_thickness",
        "beam_num_lines",
        "beam_section_key",
        "pier_H",
        "pier_V",
        "pier_W",
        "pier_type",
        "pier_count",
    )
    parts = []
    for key in ordered_keys:
        value = snapshot.get(key)
        if value is None:
            continue
        if isinstance(value, float):
            parts.append(f"{key}={value:.3f}")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def _run_render_demo_with_retry(base_out_dir, params, retry_count=0, wait_on_retry_ms=0):
    max_retries = max(0, int(retry_count))
    for attempt in range(max_retries + 1):
        try:
            return run_render_demo(base_out_dir=base_out_dir, params=params)
        except Exception as exc:
            if attempt >= max_retries:
                raise
            print(
                f"Render pass failed ({attempt + 1}/{max_retries + 1}): {exc}. "
                f"Retrying after {int(wait_on_retry_ms)} ms..."
            )
            stability_wait(wait_ms=wait_on_retry_ms, redraw=True)
            run_gc()


def _reapply_texture_mapping(modeling_params, material_metadata):
    material_metadata = dict(material_metadata or {})
    component_cfg = dict(modeling_params.get("component") or {})
    defect_cfg = dict(modeling_params.get("defect") or {})

    efflore_result = apply_efflore_texture_mapping(
        defect_cfg=defect_cfg,
        layer_material_metadata=material_metadata,
    )
    if efflore_result.get("enabled"):
        print(
            "-------- Efflore Texture Mapping Refresh ------- "
            "(applied: {}, surfaces: {}, skipped: {})".format(
                efflore_result.get("applied", 0),
                efflore_result.get("surface_objects", 0),
                efflore_result.get("skipped", 0),
            )
        )

    component_result = apply_component_texture_mapping(
        component_cfg=component_cfg,
        layer_material_metadata=material_metadata,
    )
    if component_result.get("enabled"):
        print(
            "-------- Component Texture Mapping Refresh ------- "
            "(applied: {}, surfaces: {}, solids: {}, skipped: {})".format(
                component_result.get("applied", 0),
                component_result.get("surface_objects", 0),
                component_result.get("solid_objects", 0),
                component_result.get("skipped", 0),
            )
        )

    return {
        "efflore": efflore_result,
        "component": component_result,
    }


def run(
    config_name="component.local.yaml",
    renders_per_model=None,
    max_iter=None,
    seed=None,
    show_cameras=False,
    print_timings=True,
):
    """Run component modeling in batch and capture randomized render passes per model."""
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
    current_model_iter = None
    current_render_iter = None

    cfg = load_config(config_name)
    nested_cfg = dict(cfg.get("nested_loop") or {})
    stability_cfg = resolve_stability_cfg(nested_cfg)
    preparation_params = dict(cfg.get("preparation") or {})

    modeling_params_base = dict(cfg.get("modeling") or {})
    if not modeling_params_base:
        raise ValueError("Config must include a 'modeling' section.")
    if str(modeling_params_base.get("strategy") or "").lower() != "component":
        raise ValueError("Component batch requires modeling.strategy='component'.")

    base_component_cfg = dict(modeling_params_base.get("component") or {})
    if not base_component_cfg:
        raise ValueError(
            "Component batch requires modeling.component when strategy='component'."
        )

    base_rendering = dict(cfg.get("rendering") or {})
    if not base_rendering:
        raise ValueError("Config must include a 'rendering' section.")

    camera_cfg = dict(base_rendering.get("camera") or {})
    if str(camera_cfg.get("strategy") or "").lower() != "component":
        raise ValueError("Component batch requires rendering.camera.strategy='component'.")

    component_sampler = _resolve_component_sampler(nested_cfg, base_component_cfg)
    rendering_sampler = nested_cfg.get("rendering_sampler") or {}
    reapply_mapping_per_render = bool(
        nested_cfg.get("reapply_texture_mapping_per_render", True)
    )

    batch_output_dir = create_timestamped_subdir(
        base_rendering.get("output_dir"),
        modeling_strategy="component",
    )
    batch_log_path = os.path.join(batch_output_dir, "batch_log.txt")
    batch_state_path = os.path.join(batch_output_dir, "batch_state.json")

    try:
        log_file, stdout_backup, stderr_backup = start_batch_logging(batch_log_path)
        print(f"Loaded config: {config_name}")
        print(f"Batch output directory: {batch_output_dir}")
        print(f"Batch log path: {batch_log_path}")
        print(f"Batch state path: {batch_state_path}")

        global_random_state = random.getstate()
        if seed is None:
            seed = nested_cfg.get("seed")
        if seed is not None:
            seed = int(seed)
            random.seed(seed)
            rng = random.Random(seed)
            print(f"Random seed: {seed}")
        else:
            rng = random.Random()

        autosave_state = apply_batch_autosave_policy(preparation_params)
        undo_state = apply_batch_undo_policy(preparation_params)

        if renders_per_model is None:
            renders_per_model = nested_cfg.get("renders_per_model", 2)
        renders_per_model = max(1, int(renders_per_model))

        if max_iter is None:
            max_iter = nested_cfg.get("max_iter", 1)
        max_iter = max(0, int(max_iter))

        next_output_index = int(nested_cfg.get("output_index_start", 0))
        stop_after_preview = False
        stop_after_guard = False
        render_pass_count = 0

        batch_state = {
            "status": "running",
            "config_name": config_name,
            "batch_output_dir": batch_output_dir,
            "batch_log_path": batch_log_path,
            "progress": {
                "models_total": max_iter,
                "renders_per_model": renders_per_model,
                "render_pass_count": render_pass_count,
                "next_output_index": next_output_index,
            },
            "current": {
                "model_iter": None,
                "render_iter": None,
                "stage": "startup",
            },
            "history": [],
        }
        flush_batch_state(batch_state_path, batch_state)

        print(
            f"Component batch: models={max_iter}, renders_per_model={renders_per_model}, "
            f"reapply_texture_mapping_per_render={reapply_mapping_per_render}"
        )
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
        print(
            "Render capture logging: "
            f"log_output_timings={bool(base_rendering.get('log_output_timings', False))}, "
            f"enabled_channels={','.join(_enabled_channel_names(base_rendering.get('channel')))}"
        )
        if component_sampler:
            print(
                "Component sampler enabled: "
                + json.dumps(component_sampler, sort_keys=True)
            )
        else:
            print("Component sampler disabled.")

        for model_iter in range(max_iter):
            current_model_iter = int(model_iter)
            current_render_iter = None
            model_stage_start = perf_counter()
            print(f"=== model_iter={model_iter} ===")
            log_runtime_snapshot(
                label=f"before_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ) or basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"before_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

            sampled_component_cfg = _sample_component_params(
                base_component_cfg,
                component_sampler=component_sampler,
                rng=rng,
            )
            sampled_component_seed = _assign_missing_seed(sampled_component_cfg, rng)

            sampled_modeling_params = copy.deepcopy(modeling_params_base)
            sampled_modeling_params["component"] = sampled_component_cfg
            if isinstance(sampled_modeling_params.get("defect"), dict):
                sampled_defect_cfg = dict(sampled_modeling_params.get("defect") or {})
                sampled_defect_seed = _assign_missing_seed(sampled_defect_cfg, rng)
                sampled_modeling_params["defect"] = sampled_defect_cfg
            else:
                sampled_defect_seed = None

            dimension_snapshot = _component_dimension_snapshot(sampled_component_cfg)
            print("Sampled component dimensions:", _format_dimension_snapshot(dimension_snapshot))
            print(
                f"Sampled seeds: component={sampled_component_seed}, "
                f"defect={sampled_defect_seed}"
            )

            batch_state["current"] = {
                "model_iter": model_iter,
                "render_iter": None,
                "stage": "modeling",
            }
            batch_state["progress"]["render_pass_count"] = render_pass_count
            batch_state["progress"]["next_output_index"] = next_output_index
            flush_batch_state(batch_state_path, batch_state)

            with suspend_view_updates():
                set_batch_work_view()
                main_entry.reset()
            stability_wait(
                wait_ms=stability_cfg["wait_after_reset_ms"],
                redraw=True,
            )

            model_prepare_params = _prepare_params_for_batch(preparation_params, rng)
            print(f"Model preparation seed: {model_prepare_params.get('seed')}")
            with suspend_view_updates():
                set_batch_work_view()
                prepare(model_prepare_params)
            stability_wait(
                wait_ms=stability_cfg["wait_after_preparation_ms"],
                redraw=True,
            )
            with suspend_view_updates():
                set_batch_work_view()
                create_model(sampled_modeling_params)
            stability_wait(
                wait_ms=stability_cfg["wait_before_render_ms"],
                redraw=True,
            )
            main_entry.setup_render_view(cfg=cfg)

            stage_times.append(
                (f"reset->modeling[{model_iter}]", perf_counter() - model_stage_start)
            )

            model_history = {
                "model_iter": model_iter,
                "component_seed": sampled_component_seed,
                "defect_seed": sampled_defect_seed,
                "dimensions": dimension_snapshot,
                "renders": [],
            }

            for render_iter in range(renders_per_model):
                current_render_iter = int(render_iter)
                render_stage_start = perf_counter()
                print(f"-- render_iter={render_iter} --")

                with suspend_view_updates():
                    set_batch_work_view()
                    clear_imported_materials_from_doc()
                render_prepare_params = _prepare_params_for_batch(preparation_params, rng)
                print(f"Render preparation seed: {render_prepare_params.get('seed')}")
                with suspend_view_updates():
                    set_batch_work_view()
                    prepare_result = prepare(render_prepare_params) or {}
                stability_wait(
                    wait_ms=stability_cfg["wait_after_preparation_ms"],
                    redraw=True,
                )
                main_entry.setup_render_view(cfg=cfg)
                stability_wait(
                    wait_ms=stability_cfg["wait_before_render_ms"],
                    redraw=True,
                )

                if reapply_mapping_per_render:
                    _reapply_texture_mapping(
                        sampled_modeling_params,
                        material_metadata=prepare_result.get(
                            "selected_material_metadata"
                        ),
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
                render_params.setdefault(
                    "output_basename_pattern",
                    "m{model_iter:04d}_r{render_iter:02d}_view_{output_idx:06d}",
                )

                demo_params = dict(render_params)
                demo_params["demo_type"] = "camera"
                demo_params["camera_demo_mode"] = "show" if show_cameras else "capture"

                batch_state["current"] = {
                    "model_iter": model_iter,
                    "render_iter": render_iter,
                    "stage": "rendering",
                }
                batch_state["progress"]["render_pass_count"] = render_pass_count
                batch_state["progress"]["next_output_index"] = next_output_index
                flush_batch_state(batch_state_path, batch_state)

                captured_paths = _run_render_demo_with_retry(
                    base_out_dir=batch_output_dir,
                    params=demo_params,
                    retry_count=stability_cfg["render_retry_count"],
                    wait_on_retry_ms=stability_cfg["wait_on_retry_ms"],
                )
                captured_count = 0 if show_cameras else len(captured_paths)
                next_output_index += captured_count
                render_pass_count += 1

                print(
                    f"render_iter={render_iter}: captured_views={captured_count}, "
                    f"next_view_id={next_output_index}"
                )

                model_history["renders"].append(
                    {
                        "render_iter": render_iter,
                        "preparation_seed": render_prepare_params.get("seed"),
                        "selected_materials": dict(
                            prepare_result.get("selected_materials") or {}
                        ),
                        "captured_views": captured_count,
                    }
                )

                stage_times.append(
                    (
                        f"rendering[{model_iter},{render_iter}]",
                        perf_counter() - render_stage_start,
                    )
                )
                stability_wait(
                    wait_ms=stability_cfg["wait_after_render_ms"],
                    redraw=False,
                )

                with suspend_view_updates():
                    set_batch_work_view()
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
                batch_state["progress"]["next_output_index"] = next_output_index
                batch_state["progress"]["render_pass_count"] = render_pass_count
                flush_batch_state(batch_state_path, batch_state)

                if show_cameras:
                    stop_after_preview = True
                    break

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
                    label=f"after_render_pass[{model_iter},{render_iter}]",
                ) or basic_material_guard_triggered(
                    stability_cfg=stability_cfg,
                    label=f"after_render_pass[{model_iter},{render_iter}]",
                ):
                    stop_after_guard = True
                    break

            if stop_after_preview or stop_after_guard:
                break

            batch_state["history"].append(model_history)
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
            flush_batch_state(batch_state_path, batch_state)
            log_runtime_snapshot(
                label=f"after_model_iter[{model_iter}]",
                enabled=stability_cfg["log_memory"],
            )
            if memory_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ) or basic_material_guard_triggered(
                stability_cfg=stability_cfg,
                label=f"after_model_iter[{model_iter}]",
            ):
                stop_after_guard = True
                break

            if stop_after_preview:
                break

        if stop_after_guard:
            batch_state["status"] = "stopped_by_guard"
            batch_state["current"] = {
                "model_iter": current_model_iter,
                "render_iter": current_render_iter,
                "stage": "guard_stop",
            }
        elif stop_after_preview:
            batch_state["status"] = "preview_stopped"
            batch_state["current"] = {
                "model_iter": batch_state["current"].get("model_iter"),
                "render_iter": batch_state["current"].get("render_iter"),
                "stage": "preview_stop",
            }
        else:
            batch_state["status"] = "completed"
            batch_state["current"] = {
                "model_iter": None,
                "render_iter": None,
                "stage": "completed",
            }
        batch_state["progress"]["next_output_index"] = next_output_index
        batch_state["progress"]["render_pass_count"] = render_pass_count
        flush_batch_state(batch_state_path, batch_state)

        if print_timings:
            total = perf_counter() - timer_start
            print("======== Component Batch Timings ========")
            for name, duration in stage_times:
                print(f"{name}: {duration:.2f}s")
            print(f"total: {total:.2f}s")

        return stage_times
    except Exception as exc:
        if batch_state is not None:
            batch_state["status"] = "failed"
            batch_state["error"] = f"{type(exc).__name__}: {exc}"
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
        config_name="component.local.yaml",
        renders_per_model=None,
        max_iter=None,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
