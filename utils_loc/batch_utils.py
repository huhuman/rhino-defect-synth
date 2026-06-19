"""Shared utilities for batch dataset-generation scripts."""

import copy
import gc
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class TeeWriter:
    """Mirror writes to multiple stream targets."""

    def __init__(self, *targets):
        self._targets = [t for t in targets if t is not None]

    def write(self, data):
        for t in self._targets:
            t.write(data)
        return len(data)

    def flush(self):
        for t in self._targets:
            t.flush()

    def isatty(self):
        for t in self._targets:
            if hasattr(t, "isatty"):
                try:
                    return bool(t.isatty())
                except Exception:
                    continue
        return False


def start_batch_logging(log_path):
    log_file = open(log_path, "w", encoding="utf-8")
    stdout_backup = sys.stdout
    stderr_backup = sys.stderr
    sys.stdout = TeeWriter(stdout_backup, log_file)
    sys.stderr = TeeWriter(stderr_backup, log_file)
    return log_file, stdout_backup, stderr_backup


def stop_batch_logging(log_file, stdout_backup, stderr_backup):
    if stdout_backup is not None:
        sys.stdout = stdout_backup
    if stderr_backup is not None:
        sys.stderr = stderr_backup
    if log_file is not None:
        log_file.close()


# ---------------------------------------------------------------------------
# JSON / batch state
# ---------------------------------------------------------------------------

def write_json_atomic(path, payload):
    if not path:
        return
    target_path = os.path.abspath(str(path))
    tmp_path = f"{target_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except Exception:
            pass
    os.replace(tmp_path, target_path)


def flush_batch_state(path, state):
    if not path or not isinstance(state, dict):
        return
    payload = dict(state)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(path, payload)


def create_timestamped_subdir(base_output_dir, modeling_strategy="cube"):
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


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_value(spec, rng):
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
        return {key: sample_value(value, rng) for key, value in spec.items()}

    return copy.deepcopy(spec)


def deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value
    return target


def sample_rendering_params(base_rendering, rendering_sampler, rng):
    params = copy.deepcopy(base_rendering or {})
    if not rendering_sampler:
        return params
    sampled_overrides = sample_value(rendering_sampler, rng)
    if not isinstance(sampled_overrides, dict):
        raise ValueError("nested_loop.rendering_sampler must resolve to a dict.")
    return deep_update(params, sampled_overrides)


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------

def to_non_negative_int(value, default=0):
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(0, parsed)


def to_non_negative_float(value, default=0.0):
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(0.0, parsed)


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------

def set_active_view_display_mode(mode_name):
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


def set_batch_work_view():
    for mode_name in ("Wireframe", "Shaded"):
        if set_active_view_display_mode(mode_name):
            return mode_name
    return None


@contextmanager
def suspend_view_updates():
    redraw_previous = None
    active_view = None
    drawing_previous = None

    try:
        redraw_previous = rs.EnableRedraw(False)
    except Exception:
        redraw_previous = None

    try:
        active_view = getattr(sc.doc.Views, "ActiveView", None)
        if active_view is not None and hasattr(active_view, "EnableDrawing"):
            drawing_previous = bool(active_view.EnableDrawing)
            if drawing_previous:
                active_view.EnableDrawing = False
    except Exception:
        drawing_previous = None

    try:
        yield
    finally:
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


# ---------------------------------------------------------------------------
# Stability / GC / Undo
# ---------------------------------------------------------------------------

def stability_wait(wait_ms=0, redraw=False):
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


def run_gc():
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


def clear_undo_records():
    clear_fn = getattr(sc.doc, "ClearUndoRecords", None)
    if callable(clear_fn):
        for args in ((True,), tuple()):
            try:
                clear_fn(*args)
                return True
            except Exception:
                continue
        return False
    return False


# ---------------------------------------------------------------------------
# Runtime diagnostics
# ---------------------------------------------------------------------------

def table_count(table):
    if table is None:
        return None
    try:
        return int(getattr(table, "Count"))
    except Exception:
        return None


def private_memory_mb():
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


def working_set_mb():
    """Total OS-resident memory (includes native/unmanaged allocations)."""
    process = None
    try:
        import System

        process = System.Diagnostics.Process.GetCurrentProcess()
        return float(process.WorkingSet64) / (1024.0 * 1024.0)
    except Exception:
        return None
    finally:
        dispose = getattr(process, "Dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                pass


def managed_memory_mb():
    """Managed .NET heap only. If this stays flat while private/working-set climb,
    the leak is native (bitmaps, GDI handles, render cache), not managed objects."""
    try:
        import System

        return float(System.GC.GetTotalMemory(False)) / (1024.0 * 1024.0)
    except Exception:
        return None


def log_runtime_snapshot(label, enabled=False):
    if not enabled:
        return
    parts = []
    obj_count = table_count(getattr(sc.doc, "Objects", None))
    if obj_count is not None:
        parts.append(f"objects={obj_count}")
    layer_count = table_count(getattr(sc.doc, "Layers", None))
    if layer_count is not None:
        parts.append(f"layers={layer_count}")
    render_mat_count = table_count(getattr(sc.doc, "RenderMaterials", None))
    if render_mat_count is not None:
        parts.append(f"render_materials={render_mat_count}")
    basic_mat_count = table_count(getattr(sc.doc, "Materials", None))
    if basic_mat_count is not None:
        parts.append(f"basic_materials={basic_mat_count}")
    # Tables the snapshot was previously blind to. Embedded bitmaps are the prime
    # suspect for texture-driven growth that material-table cleanup does not touch.
    bitmap_count = table_count(getattr(sc.doc, "Bitmaps", None))
    if bitmap_count is not None:
        parts.append(f"bitmaps={bitmap_count}")
    idef_count = table_count(getattr(sc.doc, "InstanceDefinitions", None))
    if idef_count is not None:
        parts.append(f"instance_defs={idef_count}")
    group_count = table_count(getattr(sc.doc, "Groups", None))
    if group_count is not None:
        parts.append(f"groups={group_count}")
    named_view_count = table_count(getattr(sc.doc, "NamedViews", None))
    if named_view_count is not None:
        parts.append(f"named_views={named_view_count}")
    # Memory split: managed flat + native climbing => native leak (bitmaps / GDI /
    # render cache). Managed climbing => .NET/Python object leak.
    managed_mb = managed_memory_mb()
    if managed_mb is not None:
        parts.append(f"managed_mem_mb={managed_mb:.1f}")
    memory_mb = private_memory_mb()
    if memory_mb is not None:
        parts.append(f"private_mem_mb={memory_mb:.1f}")
    working_mb = working_set_mb()
    if working_mb is not None:
        parts.append(f"working_set_mb={working_mb:.1f}")
    if parts:
        print(f"[runtime] {label}: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def memory_guard_triggered(stability_cfg, label=""):
    limit_mb = float(stability_cfg.get("max_private_memory_mb") or 0.0)
    if limit_mb <= 0.0:
        return False
    current_mb = private_memory_mb()
    if current_mb is None or current_mb < limit_mb:
        return False
    label = str(label or "runtime")
    print(
        f"[stability] memory_guard_triggered at {label}: "
        f"private_mem_mb={current_mb:.1f}, limit_mb={limit_mb:.1f}"
    )
    return True


def basic_material_guard_triggered(stability_cfg, label=""):
    limit = int(stability_cfg.get("max_basic_materials") or 0)
    if limit <= 0:
        return False
    current = table_count(getattr(sc.doc, "Materials", None))
    if current is None or current <= limit:
        return False
    label = str(label or "runtime")
    print(
        f"[stability] basic_material_guard_triggered at {label}: "
        f"basic_materials={current}, limit={limit}"
    )
    return True


# ---------------------------------------------------------------------------
# Autosave / undo policies
# ---------------------------------------------------------------------------

def apply_batch_autosave_policy(preparation_params):
    prep_cfg = dict(preparation_params or {})
    autosave_cfg = dict(prep_cfg.get("autosave") or {})
    disable_during_batch = bool(autosave_cfg.get("disable_during_batch", True))
    if not disable_during_batch:
        print("[stability] autosave policy: keep enabled (disable_during_batch=false).")
        return None

    app_settings = getattr(Rhino, "ApplicationSettings", None)
    settings_obj = (
        getattr(app_settings, "FileSettings", None) if app_settings is not None else None
    )
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


def restore_batch_autosave_policy(state):
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


def apply_batch_undo_policy(preparation_params):
    prep_cfg = dict(preparation_params or {})
    undo_cfg = dict(prep_cfg.get("undo") or {})
    disable_during_batch = bool(undo_cfg.get("disable_during_batch", True))
    if not disable_during_batch:
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
            print("[stability] undo policy: disabled undo recording during batch.")
        except Exception:
            print("[stability] undo policy: failed to disable undo recording.")
    else:
        print("[stability] undo policy: already disabled before batch.")

    return {
        "doc": doc,
        "enabled_value": enabled_value,
        "changed": bool(changed),
    }


def restore_batch_undo_policy(state):
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


# ---------------------------------------------------------------------------
# Stability config resolution
# ---------------------------------------------------------------------------

def resolve_stability_cfg(nested_cfg):
    raw = (nested_cfg or {}).get("stability")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("nested_loop.stability must be a dict when provided.")

    enabled = bool(raw.get("enabled", True))
    cfg = {
        "enabled": enabled,
        "wait_after_reset_ms": to_non_negative_int(raw.get("wait_after_reset_ms", 20)),
        "wait_after_preparation_ms": to_non_negative_int(
            raw.get("wait_after_preparation_ms", 40)
        ),
        "wait_before_render_ms": to_non_negative_int(raw.get("wait_before_render_ms", 40)),
        "wait_after_render_ms": to_non_negative_int(raw.get("wait_after_render_ms", 60)),
        "wait_after_capture_frame_ms": to_non_negative_int(
            raw.get("wait_after_capture_frame_ms", 0)
        ),
        "wait_on_retry_ms": to_non_negative_int(raw.get("wait_on_retry_ms", 400)),
        "gc_every_capture_frames": to_non_negative_int(
            raw.get("gc_every_capture_frames", 8)
        ),
        "render_retry_count": to_non_negative_int(raw.get("render_retry_count", 1)),
        "gc_every_render_passes": to_non_negative_int(raw.get("gc_every_render_passes", 1)),
        "gc_every_model_iters": to_non_negative_int(raw.get("gc_every_model_iters", 1)),
        "clear_undo_every_render_passes": to_non_negative_int(
            raw.get("clear_undo_every_render_passes", 1)
        ),
        "clear_undo_every_model_iters": to_non_negative_int(
            raw.get("clear_undo_every_model_iters", 1)
        ),
        "max_private_memory_mb": to_non_negative_float(
            raw.get("max_private_memory_mb", 0.0)
        ),
        "max_render_passes_per_run": to_non_negative_int(
            raw.get("max_render_passes_per_run", 0)
        ),
        "max_basic_materials": to_non_negative_int(raw.get("max_basic_materials", 0)),
        "log_memory": bool(raw.get("log_memory", True)),
    }

    if not enabled:
        cfg.update(
            {
                "wait_after_reset_ms": 0,
                "wait_after_preparation_ms": 0,
                "wait_before_render_ms": 0,
                "wait_after_render_ms": 0,
                "wait_after_capture_frame_ms": 0,
                "wait_on_retry_ms": 0,
                "gc_every_capture_frames": 0,
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
