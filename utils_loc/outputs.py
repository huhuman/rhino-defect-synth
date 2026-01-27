"""Rendering outputs for color, depth, normal, and masks."""

import os
import struct

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System.Drawing as Drawing
import System.Drawing.Imaging as Imaging
from System import Array, Single
from Rhino.Render import ComponentOrders


def _ensure_out_dir(path):
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)


def _capture_bitmap(rhino_view, width=None, height=None, transparent=False):
    """Capture the given view to a bitmap with optional size override."""
    size = rhino_view.ActiveViewport.Size
    capture = Rhino.Display.ViewCapture()
    capture.Width = int(width) if width else size.Width
    capture.Height = int(height) if height else size.Height
    capture.ScaleScreenItems = False
    capture.DrawAxes = False
    capture.DrawGrid = False
    capture.DrawGridAxes = False
    capture.TransparentBackground = bool(transparent)

    # Ensure the viewport has drawn the latest state before capture.
    rhino_view.Redraw()
    rs.Sleep(50)

    # CaptureToBitmap expects a RhinoView instance.
    bitmap = capture.CaptureToBitmap(rhino_view)
    if bitmap is None:
        raise RuntimeError("View capture failed to produce a bitmap.")
    return bitmap


def _save_bitmap(bitmap, out_path):
    """Save a bitmap to disk as PNG."""
    if not out_path:
        raise ValueError("An output path is required to save a capture.")
    _ensure_out_dir(out_path)
    bitmap.Save(out_path, Imaging.ImageFormat.Png)
    return out_path


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


def _capture_render_channels(rhino_view, need_depth=True, need_normal=True):
    """
    Use RenderPipeline -> RenderWindow channels to pull linear depth and normal buffers.
    Returns dict label -> (width, height, channel_count, bytes) or {} on failure.
    """
    try:
        pipeline_to_window = getattr(Rhino.Render.RenderPipeline, "RenderToWindow", None)
        if pipeline_to_window is None:
            return {}

        rw = pipeline_to_window(sc.doc, rhino_view)
        if rw is None:
            return {}

        # Wait for the render to finish if the API exposes a waiter.
        for waiter_name in ("WaitForRender", "WaitForFinished", "Wait"):
            waiter = getattr(rw, waiter_name, None)
            if callable(waiter):
                try:
                    waiter()
                except Exception:
                    pass
                break

        size = rw.Size() if hasattr(rw, "Size") else rhino_view.ActiveViewport.Size
        rect = Drawing.Rectangle(0, 0, size.Width, size.Height)

        # Resolve enum values defensively; names vary slightly across Rhino versions.
        std = Rhino.Render.RenderWindow.StandardChannels
        distance_enum = None
        normal_enum = None
        for name in ("DistanceFromCamera", "ZDepth"):
            distance_enum = getattr(std, name, distance_enum)
        for name in ("NormalTowardsCamera", "NormalXYZ", "Normals"):
            normal_enum = getattr(std, name, normal_enum)

        results = {}
        if need_depth and distance_enum is not None:
            ch = rw.OpenChannel(distance_enum) if hasattr(rw, "OpenChannel") else None
            if ch:
                arr = Array.CreateInstance(Single, size.Width * size.Height)
                ch.GetValues(rect, size.Width, ComponentOrders.Irrelevant, arr)
                results["depth"] = (size.Width, size.Height, 1, arr)
                ch.Dispose()

        if need_normal and normal_enum is not None:
            ch = rw.OpenChannel(normal_enum) if hasattr(rw, "OpenChannel") else None
            if ch:
                arr = Array.CreateInstance(Single, size.Width * size.Height * 3)
                ch.GetValues(rect, size.Width * 3, ComponentOrders.XYZ, arr)
                results["normal"] = (size.Width, size.Height, 3, arr)
                ch.Dispose()

        try:
            rw.Dispose()
        except Exception:
            pass

        return results
    except Exception:
        return {}


def render_image(rhino_view, out_path=None, preset=None, width=None, height=None):
    """
    Render the active/named view to an image file.

    Args:
        rhino_view: Rhino view object.
        out_path: filepath for the color render.
        preset: render preset name (display mode name).
        width: optional override width.
        height: optional override height.
    """
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        if preset:
            mode = Rhino.Display.DisplayModeDescription.FindByName(preset)
            if mode:
                rhino_view.ActiveViewport.DisplayMode = mode
        bitmap = _capture_bitmap(rhino_view, width=width, height=height)
        return _save_bitmap(bitmap, out_path)
    finally:
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_depth(rhino_view, out_path=None, width=None, height=None):
    """Render a depth pass for the view using Rhino's ZBuffer preview."""
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        rs.Command("-ShowZBuffer _Enter", echo=False)
        bitmap = _capture_bitmap(rhino_view, width=width, height=height)
        return _save_bitmap(bitmap, out_path)
    finally:
        # Toggle back to the previous display and restore the original mode.
        rs.Command("-ShowZBuffer _Enter", echo=False)
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_normal(rhino_view, out_path=None, width=None, height=None):
    """Render a normal pass for the view using the test normal map preview."""
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    prev_mode = rhino_view.ActiveViewport.DisplayMode

    try:
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        bitmap = _capture_bitmap(rhino_view, width=width, height=height)
        return _save_bitmap(bitmap, out_path)
    finally:
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        rhino_view.ActiveViewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_mask(rhino_view, out_path=None, width=None, height=None):
    """
    Render an object mask pass using Rhino's built-in "Flat Shade" display mode.
    """
    rs.CurrentView(rhino_view.ActiveViewport.Name)
    viewport = rhino_view.ActiveViewport
    prev_mode = viewport.DisplayMode
    prev_grid = getattr(viewport, "ConstructionGridVisible", None)
    prev_cplane = getattr(viewport, "ConstructionPlaneVisible", None)

    if prev_grid is not None:
        viewport.ConstructionGridVisible = False
    if prev_cplane is not None:
        viewport.ConstructionPlaneVisible = False
    try:
        flat_mode = (
            Rhino.Display.DisplayModeDescription.FindByName("Flat Shade")
            or Rhino.Display.DisplayModeDescription.FindByName("Base Color")
        )
        if flat_mode:
            viewport.DisplayMode = flat_mode
        rhino_view.Redraw()
        bitmap = _capture_bitmap(rhino_view, width=width, height=height, transparent=False)
        return _save_bitmap(bitmap, out_path)
    finally:
        viewport.DisplayMode = prev_mode
        rhino_view.Redraw()


def render_all_outputs(view=None, out_dir=None, basename="frame", width=None, height=None):
    """
    Convenience helper to render color, depth, normal, and mask in one call.
    """
    if not out_dir:
        raise ValueError("out_dir is required to save renders.")

    outputs = {
        "color": os.path.abspath(os.path.join(out_dir, f"color/{basename}.png")),
        "depth": os.path.abspath(os.path.join(out_dir, f"depth/{basename}.png")),
        "normal": os.path.abspath(os.path.join(out_dir, f"normal/{basename}.png")),
        "depth_linear": os.path.abspath(os.path.join(out_dir, f"depth/{basename}.pfm")),
        "normal_linear": os.path.abspath(os.path.join(out_dir, f"normal/{basename}.pfm")),
        "mask": os.path.abspath(os.path.join(out_dir, f"mask/{basename}.png")),
    }
    for path in outputs.values():
        _ensure_out_dir(path)

    render_view = sc.doc.Views.ActiveView if view is None else sc.doc.Views.Find(view, False)
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    render_view.ActiveViewport.DisplayMode = mode
    for layer in sc.doc.Layers:
        if layer.Name:
            if "CS" in layer.Name:
                layer.IsVisible = False
            else:
                layer.IsVisible = True

    prev_wallpaper_file = render_view.ActiveViewport.WallpaperFilename
    render_view.Redraw()

    render_image(rhino_view=render_view, out_path=outputs["color"], width=width, height=height)
    render_depth(rhino_view=render_view, out_path=outputs["depth"], width=width, height=height)

    render_view.ActiveViewport.SetWallpaper("", False)

    render_normal(rhino_view=render_view, out_path=outputs["normal"], width=width, height=height)

    mode = (
        Rhino.Display.DisplayModeDescription.FindByName("Flat Shade")
        or Rhino.Display.DisplayModeDescription.FindByName("Base Color")
    )
    if mode:
        render_view.ActiveViewport.DisplayMode = mode
    for layer in sc.doc.Layers:
        if layer.Name == "crack_extrusion":
            layer.IsVisible = False
        else:
            layer.IsVisible = True
    render_view.Redraw()

    render_mask(rhino_view=render_view, out_path=outputs["mask"], width=width, height=height)

    # Attempt to pull linear depth/normal from the render pipeline; fall back silently if unavailable.
    channel_data = _capture_render_channels(render_view, need_depth=True, need_normal=True)
    if "depth" in channel_data:
        w, h, c, buf = channel_data["depth"]
        _write_pfm(outputs["depth_linear"], w, h, c, buf)
    if "normal" in channel_data:
        w, h, c, buf = channel_data["normal"]
        _write_pfm(outputs["normal_linear"], w, h, c, buf)

    render_view.ActiveViewport.SetWallpaper(prev_wallpaper_file, False)

    return outputs
