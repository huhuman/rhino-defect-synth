"""Rendering outputs for color, depth, normal, and masks."""
import os
import struct

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System.Drawing as Drawing
import System.Drawing.Imaging as Imaging


def _ensure_out_dir(path):
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)


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


def _capture_bitmap(rhino_view, width=None, height=None, max_length=None, transparent=False):
    """Capture the given view to a bitmap with optional size override."""
    resolved_width, resolved_height = _resolve_capture_size(
        rhino_view=rhino_view,
        width=width,
        height=height,
        max_length=max_length,
    )
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
    rs.Sleep(50)

    # CaptureToBitmap expects a RhinoView instance.
    bitmap = capture.CaptureToBitmap(rhino_view)
    if bitmap is None:
        raise RuntimeError("View capture failed to produce a bitmap.")
    return bitmap


def _try_set_attr(target, name, value):
    """Best-effort setter for RhinoCommon attributes across Rhino versions."""
    if not hasattr(target, name):
        return
    try:
        setattr(target, name, value)
    except Exception:
        pass


def _clone_display_attributes(attrs):
    """Create a mutable copy of display attributes with compatibility fallbacks."""
    if attrs is None:
        return Rhino.Display.DisplayPipelineAttributes()

    try:
        return Rhino.Display.DisplayPipelineAttributes(attrs)
    except Exception:
        pass

    if hasattr(attrs, "Duplicate"):
        try:
            return attrs.Duplicate()
        except Exception:
            pass

    cloned = Rhino.Display.DisplayPipelineAttributes()
    if hasattr(cloned, "CopyContentsFrom"):
        try:
            cloned.CopyContentsFrom(attrs)
            return cloned
        except Exception:
            pass
    return cloned


def _mask_display_mode():
    return (
        Rhino.Display.DisplayModeDescription.FindByName("Flat Shade")
        or Rhino.Display.DisplayModeDescription.FindByName("Base Color")
    )


def _build_mask_display_attributes(viewport):
    """
    Build display attributes for segmentation-like masks:
    no shadows/edges/transparency/post effects; preserve object/layer display colors.
    """
    mode = _mask_display_mode()
    base_mode = mode or viewport.DisplayMode
    attrs = _clone_display_attributes(getattr(base_mode, "DisplayAttributes", None))

    bool_overrides = {
        "CastShadows": False,
        "DisableTransparency": True,
        "FlatShade": True,
        "IgnoreHighlights": True,
        "PostProcessFrameBuffer": False,
        "ShadowsOn": False,
        "ShowAnnotations": False,
        "ShowClippingPlanes": False,
        "ShowConduits": False,
        "ShowCurves": False,
        "ShowIsocurves": False,
        "ShowLights": False,
        "ShowMeshEdges": False,
        "ShowMeshNakedEdges": False,
        "ShowMeshWires": False,
        "ShowPoints": False,
        "ShowSurfaceEdges": False,
        "ShowText": False,
        "UseAssignedObjectMaterial": False,
        "UseCustomObjectColor": False,
        "UseCustomObjectColorBackfaces": False,
        "UseCustomObjectMaterial": False,
        "UseCustomObjectMaterialBackfaces": False,
        "UseObjectMaterial": False,
        "UseObjectMaterialBackfaces": False,
        "UseSingleObjectColor": False,
    }
    for name, value in bool_overrides.items():
        _try_set_attr(attrs, name, value)

    _try_set_attr(attrs, "ShadowEdgeBlur", 0)

    if hasattr(attrs, "SetFill"):
        try:
            attrs.SetFill(Drawing.Color.Black)
        except Exception:
            pass

    return attrs


def _capture_mask_bitmap(rhino_view, width=None, height=None, max_length=None):
    """Capture mask bitmap with dedicated display attributes and AA disabled."""
    resolved_width, resolved_height = _resolve_capture_size(
        rhino_view=rhino_view,
        width=width,
        height=height,
        max_length=max_length,
    )
    out_size = Drawing.Size(resolved_width, resolved_height)
    viewport = rhino_view.ActiveViewport
    attrs = _build_mask_display_attributes(viewport)

    prev_aa = None
    aa_changed = False
    try:
        ogl = Rhino.ApplicationSettings.OpenGLSettings
        prev_aa = ogl.AntialiasLevel
        if prev_aa != 0:
            ogl.AntialiasLevel = 0
            aa_changed = True
    except Exception:
        prev_aa = None

    try:
        rhino_view.Redraw()
        rs.Sleep(50)
        bitmap = None
        try:
            bitmap = rhino_view.CaptureToBitmap(out_size, attrs)
        except Exception:
            try:
                bitmap = rhino_view.CaptureToBitmap(attrs)
            except Exception:
                bitmap = None

        if bitmap is None:
            raise RuntimeError("RhinoView.CaptureToBitmap returned no bitmap for mask output.")
        return bitmap
    finally:
        if aa_changed and prev_aa is not None:
            try:
                Rhino.ApplicationSettings.OpenGLSettings.AntialiasLevel = prev_aa
                rhino_view.Redraw()
            except Exception:
                pass


def _force_visible_objects_color_by_layer():
    """
    Temporarily force visible objects to use layer color source.
    Returns list of (obj_id, previous_source) for restoration.
    """
    changed = []
    for obj_id in rs.VisibleObjects() or []:
        try:
            prev_source = rs.ObjectColorSource(obj_id)
            if prev_source is None or prev_source == 0:
                continue
            if rs.ObjectColorSource(obj_id, 0) is not None:
                changed.append((obj_id, prev_source))
        except Exception:
            continue
    return changed


def _restore_object_color_sources(changed_items):
    for obj_id, source in changed_items:
        try:
            if rs.IsObject(obj_id):
                rs.ObjectColorSource(obj_id, source)
        except Exception:
            continue


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
    rs.Sleep(50)

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

    ok = rs.Command(" ".join(cmd_parts), echo=False)
    if not ok:
        raise RuntimeError("CaptureRenderChannels command failed. Is the plugin loaded?")


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
    Render an object mask pass using explicit display attributes for crisp layer colors.
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
    changed_sources = _force_visible_objects_color_by_layer()
    try:
        try:
            bitmap = _capture_mask_bitmap(rhino_view, width=width, height=height, max_length=max_length)
        except Exception:
            # Compatibility fallback to previous behavior if custom capture fails.
            flat_mode = _mask_display_mode()
            if flat_mode:
                viewport.DisplayMode = flat_mode
            rhino_view.Redraw()
            bitmap = _capture_bitmap(
                rhino_view,
                width=width,
                height=height,
                max_length=max_length,
                transparent=False,
            )
        return _save_bitmap(bitmap, out_path)
    finally:
        _restore_object_color_sources(changed_sources)
        viewport.DisplayMode = prev_mode
        rhino_view.Redraw()


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
        tail = full_path.split("::")[-1]
        if tail in names:
            return True
    return False


def _apply_mask_layer_visibility(mask_only_layers=None, mask_hide_layers=None):
    only_set = _normalize_layer_name_set(mask_only_layers)
    hide_set = _normalize_layer_name_set(mask_hide_layers)

    if only_set:
        for layer in sc.doc.Layers:
            if layer.Name:
                layer.IsVisible = _layer_matches(layer, only_set)
        return

    for layer in sc.doc.Layers:
        if not layer.Name:
            continue
        if "CS" in layer.Name:
            layer.IsVisible = False
        else:
            layer.IsVisible = True
        if hide_set and _layer_matches(layer, hide_set):
            layer.IsVisible = False


def render_all_outputs(
    view=None,
    out_dir=None,
    basename="frame",
    width=None,
    height=None,
    max_length=None,
    mask_only_layers=None,
    mask_hide_layers=None,
):
    """
    Convenience helper to render color, depth, normal, and mask in one call.
    """
    if not out_dir:
        raise ValueError("out_dir is required to save renders.")

    outputs = {
        "color": os.path.abspath(os.path.join(out_dir, f"color/{basename}.png")),
        "depth": os.path.abspath(os.path.join(out_dir, f"depth/{basename}.png")),
        "normal": os.path.abspath(os.path.join(out_dir, f"normal/{basename}.png")),
        "depth_linear": os.path.abspath(os.path.join(out_dir, f"depth_buffer/{basename}.pfm")),
        "normal_linear": os.path.abspath(os.path.join(out_dir, f"normal_buffer/{basename}.pfm")),
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
        print(
            "Warning: viewport/output aspect mismatch may cause buffer FOV mismatch "
            f"(view={view_size.Width}x{view_size.Height}, out={capture_width}x{capture_height})."
        )

    render_image(rhino_view=render_view, out_path=outputs["color"], width=capture_width, height=capture_height)
    # Capture linear channels in the same camera/display/layer state as color.
    _capture_render_channels_to_files(
        render_view,
        depth_path=outputs["depth_linear"],
        normal_path=outputs["normal_linear"],
        width=capture_width,
        height=capture_height,
    )

    render_depth(rhino_view=render_view, out_path=outputs["depth"], width=capture_width, height=capture_height)

    render_view.ActiveViewport.SetWallpaper("", False)

    render_normal(rhino_view=render_view, out_path=outputs["normal"], width=capture_width, height=capture_height)

    _apply_mask_layer_visibility(
        mask_only_layers=mask_only_layers,
        mask_hide_layers=mask_hide_layers if mask_hide_layers is not None else ["crack_extrusion"],
    )
    render_view.Redraw()

    render_mask(rhino_view=render_view, out_path=outputs["mask"], width=capture_width, height=capture_height)

    render_view.ActiveViewport.SetWallpaper(prev_wallpaper_file, False)

    return outputs 
