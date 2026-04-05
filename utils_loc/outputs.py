"""Rendering outputs for color, depth, normal, and masks."""
import os
import struct
from time import perf_counter

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System.Drawing as Drawing
import System.Drawing.Imaging as Imaging
import System.IO as IO

from utils_loc.layer_utils import normalize_layer_name_set, layer_matches

_TIMING_LOG_STATUS_EMITTED = False


def _dispose_if_possible(obj):
    if obj is None:
        return
    dispose = getattr(obj, "Dispose", None)
    if callable(dispose):
        try:
            dispose()
        except Exception:
            pass


def _ensure_out_dir(path):
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)


def _rhino_idle_wait(wait_ms=0):
    try:
        Rhino.RhinoApp.Wait()
    except Exception:
        pass
    if int(wait_ms) > 0:
        rs.Sleep(int(wait_ms))


def _wait_for_file_ready(path, timeout_ms=2000, poll_ms=50):
    target = os.path.abspath(str(path))
    deadline = perf_counter() + max(0, int(timeout_ms)) / 1000.0
    while perf_counter() <= deadline:
        if os.path.isfile(target):
            return True
        _rhino_idle_wait(wait_ms=poll_ms)
    return os.path.isfile(target)


def _validate_dimension(value, name):
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _resolve_capture_size(rhino_view, width=None, height=None, max_length=None):
    """
    Resolve capture size.
    If max_length is provided (without explicit width/height), preserve viewport aspect ratio
    and set the longest side to max_length.
    """
    size = rhino_view.ActiveViewport.Size
    default_width = max(1, int(size.Width))
    default_height = max(1, int(size.Height))

    if max_length is not None and width is None and height is None:
        target = _validate_dimension(max_length, "max_length")
        if default_width >= default_height:
            out_width = target
            out_height = max(1, int(round(target * float(default_height) / float(default_width))))
        else:
            out_height = target
            out_width = max(1, int(round(target * float(default_width) / float(default_height))))
        return out_width, out_height

    out_width = _validate_dimension(width, "width") if width is not None else default_width
    out_height = _validate_dimension(height, "height") if height is not None else default_height
    return out_width, out_height


def _match_capture_aspect_to_viewport(
    capture_width,
    capture_height,
    view_width,
    view_height,
    preserve_axis="width",
):
    """Adjust capture size to match viewport aspect ratio."""
    view_ratio = float(view_width) / float(max(1, view_height))
    out_ratio = float(capture_width) / float(max(1, capture_height))
    if abs(view_ratio - out_ratio) <= 1e-6:
        return int(capture_width), int(capture_height), False

    if preserve_axis == "height":
        adjusted_w = max(1, int(round(float(capture_height) * view_ratio)))
        return adjusted_w, int(capture_height), True

    adjusted_h = max(1, int(round(float(capture_width) / view_ratio)))
    return int(capture_width), adjusted_h, True


def _capture_bitmap(rhino_view, width=None, height=None, max_length=None, transparent=False):
    """Capture the given view to a bitmap with optional size override."""
    resolved_width, resolved_height = _resolve_capture_size(
        rhino_view=rhino_view,
        width=width,
        height=height,
        max_length=max_length,
    )
    capture = None
    try:
        capture = Rhino.Display.ViewCapture()
        capture.Width = resolved_width
        capture.Height = resolved_height
        capture.ScaleScreenItems = False
        capture.DrawAxes = False
        capture.DrawGrid = False
        capture.DrawGridAxes = False
        capture.TransparentBackground = bool(transparent)

        # Ensure the viewport has drawn the latest state before capture.
        rhino_view.Redraw()
        _rhino_idle_wait(wait_ms=5)

        # CaptureToBitmap expects a RhinoView instance.
        bitmap = capture.CaptureToBitmap(rhino_view)
        if bitmap is None:
            raise RuntimeError("View capture failed to produce a bitmap.")
        return bitmap
    finally:
        _dispose_if_possible(capture)


def _try_set_attr(target, name, value):
    """Best-effort setter for RhinoCommon attributes across Rhino versions."""
    if not hasattr(target, name):
        return
    try:
        setattr(target, name, value)
    except Exception:
        pass


def _save_bitmap(bitmap, out_path):
    """Save a bitmap to disk as PNG."""
    if not out_path:
        raise ValueError("An output path is required to save a capture.")
    _ensure_out_dir(out_path)
    try:
        bitmap.Save(out_path, Imaging.ImageFormat.Png)
        return out_path
    finally:
        dispose = getattr(bitmap, "Dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                pass


def _write_pfm(out_path, width, height, channel_count, data):
    """
    Minimal PFM (Portable Float Map) writer for float32 channel data.
    Stores values in little-endian, row order from top to bottom.
    """
    _ensure_out_dir(out_path)
    scale = -1.0  # negative for little-endian
    header = f"PF\n{width} {height}\n{scale}\n" if channel_count == 3 else f"Pf\n{width} {height}\n{scale}\n"
    with open(out_path, "wb") as f:
        f.write(header.encode("ascii"))
        packed = struct.pack(f"<{len(data)}f", *list(data))
        f.write(packed)
    return out_path


def _capture_render_channels_to_files(rhino_view, depth_path, normal_path, width=None, height=None, renderer_id=None):
    """
    Use the CaptureRenderChannels Rhino command (from the C# plugin) to write PFM files.
    """
    if not depth_path or not normal_path:
        raise ValueError("depth_path and normal_path are required.")
    view_name = rhino_view.ActiveViewport.Name
    width_value = int(width) if width else 0
    height_value = int(height) if height else 0

    # Force the same active view used by color capture and let the command
    # consume active view with Enter to avoid accidental lookup mismatch.
    rs.CurrentView(view_name)
    rhino_view.Redraw()
    _rhino_idle_wait(wait_ms=5)

    cmd_parts = [
        "-CaptureRenderChannels",
        f'"{depth_path}"',
        f'"{normal_path}"',
        "_Enter",
        str(width_value),
        str(height_value),
    ]
    if renderer_id:
        cmd_parts.append(f'"{renderer_id}"')
    else:
        cmd_parts.append("_Enter")

    for output_path in (depth_path, normal_path):
        try:
            if os.path.isfile(output_path):
                os.remove(output_path)
        except Exception:
            pass

    ok = rs.Command(" ".join(cmd_parts), echo=False)
    if not ok:
        raise RuntimeError("CaptureRenderChannels command failed. Is the plugin loaded?")
    _rhino_idle_wait(wait_ms=5)
    if not _wait_for_file_ready(depth_path, timeout_ms=3000, poll_ms=50):
        raise RuntimeError(
            f"CaptureRenderChannels completed but depth output was not found: {depth_path}"
        )
    if not _wait_for_file_ready(normal_path, timeout_ms=3000, poll_ms=50):
        raise RuntimeError(
            f"CaptureRenderChannels completed but normal output was not found: {normal_path}"
        )


def _capture_selected_render_channels(
    rhino_view,
    depth_path=None,
    normal_path=None,
    width=None,
    height=None,
    renderer_id=None,
):
    """Capture buffer channels while allowing either depth or normal buffer to be disabled."""
    want_depth = bool(depth_path)
    want_normal = bool(normal_path)
    if not want_depth and not want_normal:
        return

    temp_paths = []
    capture_depth_path = depth_path
    capture_normal_path = normal_path

    if not want_depth:
        capture_depth_path = os.path.abspath(
            os.path.join(IO.Path.GetTempPath(), f"rhino_depth_{IO.Path.GetRandomFileName()}.pfm")
        )
        temp_paths.append(capture_depth_path)
    if not want_normal:
        capture_normal_path = os.path.abspath(
            os.path.join(IO.Path.GetTempPath(), f"rhino_normal_{IO.Path.GetRandomFileName()}.pfm")
        )
        temp_paths.append(capture_normal_path)

    try:
        _capture_render_channels_to_files(
            rhino_view,
            depth_path=capture_depth_path,
            normal_path=capture_normal_path,
            width=width,
            height=height,
            renderer_id=renderer_id,
        )
    finally:
        for temp_path in temp_paths:
            try:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass


def _capture_mask_basecolor_to_file(rhino_view, mask_path, width=None, height=None):
    """
    Use the CaptureBaseColorMask Rhino command (from the C# plugin) to write a crisp PNG mask.
    """
    if not mask_path:
        raise ValueError("mask_path is required.")
    view_name = rhino_view.ActiveViewport.Name
    width_value = int(width) if width else 0
    height_value = int(height) if height else 0

    rs.CurrentView(view_name)
    rhino_view.Redraw()
    _rhino_idle_wait(wait_ms=5)

    cmd_parts = [
        "-CaptureBaseColorMask",
        f'"{mask_path}"',
        "_Enter",
        str(width_value),
        str(height_value),
    ]
    try:
        if os.path.isfile(mask_path):
            os.remove(mask_path)
    except Exception:
        pass

    ok = rs.Command(" ".join(cmd_parts), echo=False)
    if not ok:
        raise RuntimeError("CaptureBaseColorMask command failed. Is the plugin loaded?")
    _rhino_idle_wait(wait_ms=5)
    if not _wait_for_file_ready(mask_path, timeout_ms=3000, poll_ms=50):
        raise RuntimeError(f"CaptureBaseColorMask completed but output was not found: {mask_path}")
    return mask_path


def render_image(rhino_view, out_path=None, preset=None, width=None, height=None, max_length=None):
    """
    Render the active/named view to an image file.

    Args:
        rhino_view: Rhino view object.
        out_path: filepath for the color render.
        preset: render preset name (display mode name).
        width: optional override width.
        height: optional override height.
        max_length: optional longest-side resolution that preserves aspect ratio.
    """
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        if preset:
            mode = Rhino.Display.DisplayModeDescription.FindByName(preset)
            if mode:
                rhino_view.ActiveViewport.DisplayMode = mode
        bitmap = _capture_bitmap(rhino_view, width=width, height=height, max_length=max_length)
        return _save_bitmap(bitmap, out_path)
    finally:
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_depth(rhino_view, out_path=None, width=None, height=None, max_length=None):
    """Render a depth pass for the view using Rhino's ZBuffer preview."""
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        rs.Command("-ShowZBuffer _Enter", echo=False)
        bitmap = _capture_bitmap(rhino_view, width=width, height=height, max_length=max_length)
        return _save_bitmap(bitmap, out_path)
    finally:
        # Toggle back to the previous display and restore the original mode.
        rs.Command("-ShowZBuffer _Enter", echo=False)
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_normal(rhino_view, out_path=None, width=None, height=None, max_length=None):
    """Render a normal pass for the view using the test normal map preview."""
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        bitmap = _capture_bitmap(rhino_view, width=width, height=height, max_length=max_length)
        return _save_bitmap(bitmap, out_path)
    finally:
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_mask(rhino_view, out_path=None, width=None, height=None, max_length=None):
    """
    Render an object mask pass using the RhinoChannels plugin command.
    """
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    viewport = rhino_view.ActiveViewport
    prev_mode = viewport.DisplayMode
    prev_grid = getattr(viewport, "ConstructionGridVisible", None)
    prev_cplane = getattr(viewport, "ConstructionPlaneVisible", None)
    prev_caxes = getattr(viewport, "ConstructionAxesVisible", None)
    prev_waxes = getattr(viewport, "WorldAxesVisible", None)
    prev_wallpaper_file = getattr(viewport, "WallpaperFilename", "")

    if prev_grid is not None:
        viewport.ConstructionGridVisible = False
    if prev_cplane is not None:
        viewport.ConstructionPlaneVisible = False
    if prev_caxes is not None:
        viewport.ConstructionAxesVisible = False
    if prev_waxes is not None:
        viewport.WorldAxesVisible = False
    try:
        viewport.SetWallpaper("", False)
    except Exception:
        pass
    capture_width, capture_height = _resolve_capture_size(
        rhino_view=rhino_view,
        width=width,
        height=height,
        max_length=max_length,
    )
    try:
        try:
            return _capture_mask_basecolor_to_file(
                rhino_view,
                mask_path=out_path,
                width=capture_width,
                height=capture_height,
            )
        except Exception as exc:
            msg = f"CaptureBaseColorMask failed for '{out_path}': {exc}"
            print(f"ERROR: {msg}")
            raise RuntimeError(msg) from exc
    finally:
        if prev_grid is not None:
            try:
                viewport.ConstructionGridVisible = prev_grid
            except Exception:
                pass
        if prev_cplane is not None:
            try:
                viewport.ConstructionPlaneVisible = prev_cplane
            except Exception:
                pass
        if prev_caxes is not None:
            try:
                viewport.ConstructionAxesVisible = prev_caxes
            except Exception:
                pass
        if prev_waxes is not None:
            try:
                viewport.WorldAxesVisible = prev_waxes
            except Exception:
                pass
        try:
            viewport.SetWallpaper(prev_wallpaper_file, False)
        except Exception:
            pass
        viewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def _apply_scene_layer_visibility(scene_only_layers=None, scene_hide_layers=None):
    only_set = normalize_layer_name_set(scene_only_layers)
    hide_set = normalize_layer_name_set(scene_hide_layers)
    if not only_set and not hide_set:
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


def _apply_mask_layer_visibility(mask_only_layers=None, mask_hide_layers=None):
    only_set = normalize_layer_name_set(mask_only_layers)
    hide_set = normalize_layer_name_set(mask_hide_layers)
    if not only_set and not hide_set:
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


def _snapshot_layer_visibility():
    snapshot = []
    for layer in sc.doc.Layers:
        layer_id = getattr(layer, "Id", None)
        if layer_id is None:
            continue
        snapshot.append((layer_id, bool(getattr(layer, "IsVisible", True))))
    return snapshot


def _restore_layer_visibility(snapshot):
    for layer_id, is_visible in snapshot or []:
        try:
            layer = sc.doc.Layers.FindId(layer_id)
        except Exception:
            layer = None
        if layer is None:
            continue
        try:
            layer.IsVisible = bool(is_visible)
        except Exception:
            continue


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _normalize_channel_flags(channels):
    defaults = {
        "color": True,
        "depth": True,
        "normal": True,
        "depth_buffer": True,
        "normal_buffer": True,
        "mask": True,
    }
    if not isinstance(channels, dict):
        return defaults
    for key in list(defaults.keys()):
        if key in channels:
            defaults[key] = _coerce_bool(channels.get(key))
    return defaults


def render_all_outputs(
    view=None,
    out_dir=None,
    basename="frame",
    width=None,
    height=None,
    max_length=None,
    match_viewport_aspect=True,
    scene_only_layers=None,
    scene_hide_layers=None,
    mask_only_layers=None,
    mask_hide_layers=None,
    channels=None,
    log_timings=False,
):
    """
    Convenience helper to render color, depth, normal, and mask in one call.
    """
    if not out_dir:
        raise ValueError("out_dir is required to save renders.")

    all_outputs = {
        "color": os.path.abspath(os.path.join(out_dir, f"color/{basename}.png")),
        "depth": os.path.abspath(os.path.join(out_dir, f"depth/{basename}.png")),
        "normal": os.path.abspath(os.path.join(out_dir, f"normal/{basename}.png")),
        "depth_buffer": os.path.abspath(os.path.join(out_dir, f"depth_buffer/{basename}.pfm")),
        "normal_buffer": os.path.abspath(os.path.join(out_dir, f"normal_buffer/{basename}.pfm")),
        "mask": os.path.abspath(os.path.join(out_dir, f"mask/{basename}.png")),
    }
    channel_flags = _normalize_channel_flags(channels)
    outputs = {
        name: path for name, path in all_outputs.items() if channel_flags.get(name, True)
    }
    for path in outputs.values():
        _ensure_out_dir(path)

    render_view = sc.doc.Views.ActiveView if view is None else sc.doc.Views.Find(view, False)
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    render_view.ActiveViewport.DisplayMode = mode

    prev_wallpaper_file = render_view.ActiveViewport.WallpaperFilename
    layer_visibility_snapshot = _snapshot_layer_visibility()
    render_view.Redraw()
    log_timings = bool(log_timings)
    total_start = perf_counter()
    step_timings = {}
    global _TIMING_LOG_STATUS_EMITTED
    if not _TIMING_LOG_STATUS_EMITTED:
        enabled_channels = [
            name for name, enabled in channel_flags.items() if bool(enabled)
        ]
        print(
            "[timing] channel-wise capture logging "
            f"{'enabled' if log_timings else 'disabled'}; "
            f"channels={','.join(enabled_channels)}"
        )
        _TIMING_LOG_STATUS_EMITTED = True

    def _record_timing(name, start_time):
        if log_timings:
            step_timings[name] = perf_counter() - start_time

    try:
        capture_width, capture_height = _resolve_capture_size(
            rhino_view=render_view,
            width=width,
            height=height,
            max_length=max_length,
        )
        view_size = render_view.ActiveViewport.Size
        view_ratio = float(view_size.Width) / float(max(1, view_size.Height))
        out_ratio = float(capture_width) / float(max(1, capture_height))
        if abs(view_ratio - out_ratio) > 1e-6:
            if bool(match_viewport_aspect):
                preserve_axis = "width" if width is not None else "height"
                adjusted_w, adjusted_h, changed = _match_capture_aspect_to_viewport(
                    capture_width=capture_width,
                    capture_height=capture_height,
                    view_width=view_size.Width,
                    view_height=view_size.Height,
                    preserve_axis=preserve_axis,
                )
                if changed:
                    print(
                        "Info: auto-adjusted output size to match viewport aspect "
                        f"(view={view_size.Width}x{view_size.Height}, requested={capture_width}x{capture_height}, "
                        f"adjusted={adjusted_w}x{adjusted_h})."
                    )
                    capture_width, capture_height = adjusted_w, adjusted_h
            else:
                print(
                    "Warning: viewport/output aspect mismatch may cause buffer FOV mismatch "
                    f"(view={view_size.Width}x{view_size.Height}, out={capture_width}x{capture_height})."
                )

        if any(channel_flags[name] for name in ("color", "depth", "normal", "depth_buffer", "normal_buffer")):
            step_start = perf_counter()
            _apply_scene_layer_visibility(
                scene_only_layers=scene_only_layers,
                scene_hide_layers=scene_hide_layers,
            )
            render_view.Redraw()
            _record_timing("scene_visibility", step_start)

        if channel_flags["color"]:
            step_start = perf_counter()
            render_image(
                rhino_view=render_view,
                out_path=outputs["color"],
                width=capture_width,
                height=capture_height,
            )
            _record_timing("color", step_start)

        if channel_flags["depth_buffer"] or channel_flags["normal_buffer"]:
            step_start = perf_counter()
            _capture_selected_render_channels(
                render_view,
                depth_path=outputs.get("depth_buffer"),
                normal_path=outputs.get("normal_buffer"),
                width=capture_width,
                height=capture_height,
            )
            _record_timing("buffer_channels", step_start)

        if channel_flags["depth"]:
            step_start = perf_counter()
            render_depth(
                rhino_view=render_view,
                out_path=outputs["depth"],
                width=capture_width,
                height=capture_height,
            )
            _record_timing("depth", step_start)

        if channel_flags["normal"]:
            step_start = perf_counter()
            render_view.ActiveViewport.SetWallpaper("", False)
            render_normal(
                rhino_view=render_view,
                out_path=outputs["normal"],
                width=capture_width,
                height=capture_height,
            )
            _record_timing("normal", step_start)

        if channel_flags["mask"]:
            step_start = perf_counter()
            _restore_layer_visibility(layer_visibility_snapshot)
            _apply_mask_layer_visibility(
                mask_only_layers=mask_only_layers,
                mask_hide_layers=mask_hide_layers,
            )
            render_view.Redraw()
            _record_timing("mask_visibility", step_start)
            step_start = perf_counter()
            render_mask(
                rhino_view=render_view,
                out_path=outputs["mask"],
                width=capture_width,
                height=capture_height,
            )
            _record_timing("mask", step_start)

        if log_timings:
            timing_parts = [f"{name}={duration:.2f}s" for name, duration in step_timings.items()]
            total_duration = perf_counter() - total_start
            print(
                f"[timing] frame={basename} total={total_duration:.2f}s "
                + " ".join(timing_parts)
            )

        return outputs
    finally:
        _restore_layer_visibility(layer_visibility_snapshot)
        try:
            render_view.ActiveViewport.SetWallpaper(prev_wallpaper_file, False)
        except Exception:
            pass
        try:
            render_view.Redraw()
        except Exception:
            pass
