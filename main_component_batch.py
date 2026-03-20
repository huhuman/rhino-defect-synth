#! python3
"""Batch driver for component-model dataset generation."""

import copy
import gc
import json
import os
import random
import sys
from datetime import datetime
from time import perf_counter

import main as main_entry

from utils_loc.config import load_config
from utils_loc.logging_utils import install_timestamped_print
from utils_loc.materials import clear_imported_materials_from_doc
from utils_loc.pipeline import create_model, prepare, run_render_demo
from utils_loc.texture_mapping import (
    apply_component_texture_mapping,
    apply_efflore_texture_mapping,
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


def _write_json_atomic(path, payload):
    target_path = os.path.abspath(str(path))
    tmp_path = f"{target_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, target_path)


def _flush_batch_state(path, state):
    if not path or not isinstance(state, dict):
        return
    payload = dict(state)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_atomic(path, payload)


def _create_timestamped_subdir(base_output_dir, modeling_strategy="component"):
    if not str(base_output_dir or "").strip():
        raise ValueError("Config must include rendering.output_dir for batch runs.")

    root_dir = os.path.abspath(os.path.expanduser(str(base_output_dir)))
    base_dir = os.path.join(root_dir, "runs", modeling_strategy)
    os.makedirs(base_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = os.path.join(base_dir, stamp)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, f"{stamp}_{suffix:02d}")
        suffix += 1

    os.makedirs(candidate, exist_ok=False)
    return candidate


def _sample_value(spec, rng):
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

    sampled_overrides = _sample_value(component_sampler, rng)
    if not isinstance(sampled_overrides, dict):
        raise ValueError("nested_loop.component_sampler must resolve to a dict.")
    return _deep_update(params, sampled_overrides)


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
    batch_state_path = None
    batch_state = None
    global_random_state = None

    cfg = load_config(config_name)
    nested_cfg = dict(cfg.get("nested_loop") or {})
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

    batch_output_dir = _create_timestamped_subdir(
        base_rendering.get("output_dir"),
        modeling_strategy="component",
    )
    batch_log_path = os.path.join(batch_output_dir, "batch_log.txt")
    batch_state_path = os.path.join(batch_output_dir, "batch_state.json")

    try:
        log_file, stdout_backup, stderr_backup = _start_batch_logging(batch_log_path)
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

        if renders_per_model is None:
            renders_per_model = nested_cfg.get("renders_per_model", 2)
        renders_per_model = max(1, int(renders_per_model))

        if max_iter is None:
            max_iter = nested_cfg.get("max_iter", 1)
        max_iter = max(0, int(max_iter))

        next_output_index = int(nested_cfg.get("output_index_start", 0))
        stop_after_preview = False

        batch_state = {
            "status": "running",
            "config_name": config_name,
            "batch_output_dir": batch_output_dir,
            "batch_log_path": batch_log_path,
            "progress": {
                "models_total": max_iter,
                "renders_per_model": renders_per_model,
                "next_output_index": next_output_index,
            },
            "current": {
                "model_iter": None,
                "render_iter": None,
                "stage": "startup",
            },
            "history": [],
        }
        _flush_batch_state(batch_state_path, batch_state)

        print(
            f"Component batch: models={max_iter}, renders_per_model={renders_per_model}, "
            f"reapply_texture_mapping_per_render={reapply_mapping_per_render}"
        )
        if component_sampler:
            print(
                "Component sampler enabled: "
                + json.dumps(component_sampler, sort_keys=True)
            )
        else:
            print("Component sampler disabled.")

        for model_iter in range(max_iter):
            model_stage_start = perf_counter()
            print(f"=== model_iter={model_iter} ===")

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
            batch_state["progress"]["next_output_index"] = next_output_index
            _flush_batch_state(batch_state_path, batch_state)

            main_entry.reset()

            model_prepare_params = _prepare_params_for_batch(preparation_params, rng)
            print(f"Model preparation seed: {model_prepare_params.get('seed')}")
            prepare(model_prepare_params)
            main_entry.setup_render_view(cfg=cfg)
            create_model(sampled_modeling_params)

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
                render_stage_start = perf_counter()
                print(f"-- render_iter={render_iter} --")

                clear_imported_materials_from_doc()
                render_prepare_params = _prepare_params_for_batch(preparation_params, rng)
                print(f"Render preparation seed: {render_prepare_params.get('seed')}")
                prepare_result = prepare(render_prepare_params) or {}
                main_entry.setup_render_view(cfg=cfg)

                if reapply_mapping_per_render:
                    _reapply_texture_mapping(
                        sampled_modeling_params,
                        material_metadata=prepare_result.get(
                            "selected_material_metadata"
                        ),
                    )

                render_params = _sample_rendering_params(
                    base_rendering=base_rendering,
                    rendering_sampler=rendering_sampler,
                    rng=rng,
                )
                render_params["model_iter"] = model_iter
                render_params["render_iter"] = render_iter
                render_params["output_index_offset"] = next_output_index
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
                batch_state["progress"]["next_output_index"] = next_output_index
                _flush_batch_state(batch_state_path, batch_state)

                captured_paths = run_render_demo(
                    base_out_dir=batch_output_dir,
                    params=demo_params,
                )
                captured_count = 0 if show_cameras else len(captured_paths)
                next_output_index += captured_count

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

                batch_state["progress"]["next_output_index"] = next_output_index
                _flush_batch_state(batch_state_path, batch_state)

                if show_cameras:
                    stop_after_preview = True
                    break

            batch_state["history"].append(model_history)
            _flush_batch_state(batch_state_path, batch_state)
            _run_gc()

            if stop_after_preview:
                break

        if stop_after_preview:
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
        _flush_batch_state(batch_state_path, batch_state)

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
            _flush_batch_state(batch_state_path, batch_state)
        raise
    finally:
        if global_random_state is not None:
            try:
                random.setstate(global_random_state)
            except Exception:
                pass
        _stop_batch_logging(log_file, stdout_backup, stderr_backup)


if __name__ == "__main__":
    run(
        config_name="component.local.yaml",
        renders_per_model=None,
        max_iter=1,
        seed=None,
        show_cameras=False,
        print_timings=True,
    )
