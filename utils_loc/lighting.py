"""Lighting helpers for Rhino renders.

Currently supports driving the built-in Sun and adding per-face helper lights.
"""

import random
import colorsys
from datetime import datetime
from pathlib import Path

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
from System import DateTime, DateTimeKind


def _split_time(time_of_day):
    """Split float hours to (hour, minute)."""
    t = max(0.0, min(24.0, float(time_of_day)))
    hour = int(t)
    minute = int(round((t - hour) * 60))
    return hour, minute


def _get_doc_sun():
    """Return the document Sun object from the light table."""
    sun = getattr(sc.doc.Lights, "Sun", None)
    if sun is None:
        raise RuntimeError("Document Sun is unavailable in this Rhino version.")
    return sun


def setup_sun(time_of_day=12.0, date=None, latitude=None, longitude=None, timezone=None, intensity=1.0, north=0.0):
    """
    Drive Rhino's built-in Sun by time-of-day and optional site info.

    Args:
        time_of_day (float): Hour in [0, 24). 6–9 morning, 12 noon, 17–19 sunset.
        date (datetime/date): Optional calendar date; defaults to today.
        latitude (float): Optional site latitude.
        longitude (float): Optional site longitude.
        timezone (float): Optional timezone offset from UTC (hours).
        intensity (float): Scalar multiplier for sun brightness.
        north (float): Degrees to rotate north (model orientation correction).
    """
    sun = _get_doc_sun()
    sun.Enabled = True
    sun.ManualControl = False

    if time_of_day is None:
        time_of_day = random.uniform(5.0, 19.0)

    if latitude is not None:
        sun.Latitude = latitude
    if longitude is not None:
        sun.Longitude = longitude
    if timezone is not None:
        sun.TimeZone = timezone
    if north:
        sun.North = north

    use_date = date or datetime.now().date()
    if isinstance(use_date, datetime):
        use_date = use_date.date()
    hour, minute = _split_time(time_of_day)
    dt = DateTime(use_date.year, use_date.month, use_date.day, hour, minute, 0)
    # Second arg is a DateTimeKind enum; use Unspecified to keep explicit time.
    sun.SetDateTime(dt, DateTimeKind.Unspecified)
    sun.Intensity = intensity
    return sun


def set_skylight(intensity=0.25, enabled=True):
    sc.doc.RenderSettings.Skylight.Enabled = enabled
    sky_env = sc.doc.CurrentEnvironment.ForLighting
    if sky_env is not None:
        tex = sky_env.FindChild("texture")
        if tex is not None:
            tex.SetParameter("multiplier", intensity)


def setup_face_lights(
    bbox_pts=None,
    faces=None,
    distance_factor=0.35,
    intensities=None,
    light_type="directional",
    replace_existing=True,
    spot_hotspot=0.6,
    spot_falloff=55.0,
):
    """
    Add one light per cube face pointing toward the model center.

    Args:
        bbox_pts (list[Point3d or tuple]): Bounding-box corners of the geometry
            to light. If None, AllObjects() is used (excluding lights/grips).
        faces (iterable[str]): Any subset of ["+x", "-x", "+y", "-y", "+z", "-z"].
            Defaults to all six.
        distance_factor (float): Multiple of half-extent to push lights away
            from each face (0.35 works for soft fill, larger for rim).
        intensity (float): Value passed to rs.LightIntensity for each light.
        light_type (str): "directional" (default), "spot", or "point".
        replace_existing (bool): If True, deletes lights named face_light_* first.
        spot_hotspot (float): Hotspot factor for spot lights (0–1 range).
        spot_falloff (float): Full cone angle in degrees for spot lights.

    Returns:
        dict: {face: light_guid}
    """
    if faces is None:
        raise ValueError("Faces must be specified for setup_face_lights.")
    
    if intensities is None:
        intensities = [0.5 for _ in faces]

    if bbox_pts is None:
        bbox_pts = rs.BoundingBox(
            rs.AllObjects(select=False, include_lights=False, include_grips=False)
        )
    if not bbox_pts:
        raise ValueError("Cannot place face lights without geometry (empty bounding box).")

    xs = [pt.X if hasattr(pt, "X") else pt[0] for pt in bbox_pts]
    ys = [pt.Y if hasattr(pt, "Y") else pt[1] for pt in bbox_pts]
    zs = [pt.Z if hasattr(pt, "Z") else pt[2] for pt in bbox_pts]

    def _safe_len(vals):
        return max(max(vals) - min(vals), 1e-3)

    x_len, y_len, z_len = _safe_len(xs), _safe_len(ys), _safe_len(zs)
    center = (
        (max(xs) + min(xs)) * 0.5,
        (max(ys) + min(ys)) * 0.5,
        (max(zs) + min(zs)) * 0.5,
    )

    normals = {
        "+x": (1, 0, 0),
        "-x": (-1, 0, 0),
        "+y": (0, 1, 0),
        "-y": (0, -1, 0),
        "+z": (0, 0, 1),
        "-z": (0, 0, -1),
    }

    axis_lengths = {
        "x": x_len,
        "y": y_len,
        "z": z_len,
    }

    def _clear_existing_face_lights():
        # Remove any prior face-light objects so names remain unique per render.
        # Use both rs.ObjectsByName and a direct scan of the light table to be robust.
        existing_named = rs.ObjectsByName("face_light") or []
        for existing in existing_named:
            rs.DeleteObject(existing)
        for key in normals:
            for obj in rs.ObjectsByName(f"face_light_{key}") or []:
                rs.DeleteObject(obj)

        # Scan all lights in the document and delete those whose names start with the prefix.
        prefix = "face_light"
        light_indices = []
        for i in range(sc.doc.Lights.Count):
            lo = sc.doc.Lights[i]
            if lo and lo.Name and lo.Name.lower().startswith(prefix):
                light_indices.append(i)
        # Delete by index from the end to keep indices valid.
        for idx in reversed(light_indices):
            sc.doc.Lights.Delete(idx, True)

    if replace_existing:
        _clear_existing_face_lights()

    def _set_light_intensity(light_id, value):
        lo = sc.doc.Lights.FindId(light_id)
        if not lo:
            print("Light not found")
        else:
            lg = lo.DuplicateLightGeometry()
            lg.Intensity = value
            sc.doc.Lights.Modify(lo.Id, lg)

    # def _random_natural_light_color():
    #     # Warm/neutral whites: hue 20–70° (no blue/purple), low saturation.
    #     h = random.uniform(20.0 / 360.0, 70.0 / 360.0)
    #     s = random.uniform(0.02, 0.12)
    #     v = random.uniform(0.92, 1.0)
    #     r, g, b = colorsys.hsv_to_rgb(h, s, v)
    #     return int(r * 255), int(g * 255), int(b * 255)

    def _random_natural_light_color():
        # Sunset-biased warm lights: red/orange/amber, occasionally yellow.
        r = random.random()
        if r < 0.35:
            # Deep red / red-orange
            h_deg = random.uniform(2.0, 15.0)
        elif r < 0.80:
            # Orange / amber (most common)
            h_deg = random.uniform(15.0, 38.0)
        else:
            # Yellow / golden highlights
            h_deg = random.uniform(38.0, 58.0)

        h = h_deg / 360.0
        s = random.uniform(0.18, 0.65)   # more color than neutral white
        v = random.uniform(0.45, 0.90)   # darker overall than current 0.92–1.0

        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return int(r * 255), int(g * 255), int(b * 255)

    created = {}
    for i, face in enumerate(faces):
        n = normals.get(face.lower())
        if not n:
            continue

        axis = "x" if n[0] else "y" if n[1] else "z"
        half_extent = axis_lengths[axis] * 0.5
        offset = half_extent * (1.0 + distance_factor)
        pos = (
            center[0] + n[0] * offset,
            center[1] + n[1] * offset,
            center[2] + n[2] * offset,
        )

        if light_type == "point":
            lid = rs.AddPointLight(pos)
        elif light_type == "spot":
            # Use explicit direction to avoid signature ambiguity across Rhino versions.
            direction = (
                center[0] - pos[0],
                center[1] - pos[1],
                center[2] - pos[2],
            )
            lid = None
            for args in (
                (pos, direction, spot_falloff, spot_hotspot),  # angle, hotspot
                (pos, direction, spot_hotspot, spot_falloff),  # hotspot, angle (if reversed)
            ):
                try:
                    lid = rs.AddSpotLight(*args)
                    if lid:
                        break
                except Exception:
                    lid = None
        else:
            # Directional light points from pos toward center.
            lid = rs.AddDirectionalLight(pos, center)

        if not lid:
            # Fallback to directional if spot/point creation failed.
            lid = rs.AddDirectionalLight(pos, center)
        if not lid:
            continue

        _set_light_intensity(lid, intensities[i])
        rs.LightColor(lid, _random_natural_light_color())
        rs.ObjectName(lid, f"face_light_{face.lower()}")
        created[face.lower()] = lid

    return created


def set_random_wallpaper(folder_path, view=None):
    """
    Pick a random image from a folder and set it as the viewport wallpaper.

    Args:
        folder_path (str or Path): Directory containing images.
        view (str): Optional view name; defaults to current view.

    Returns:
        str: Full path to the chosen image.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError(f"Folder does not exist: {folder}")

    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
    candidates = [p for p in folder.iterdir() if p.suffix.lower() in exts and p.is_file()]
    if not candidates:
        raise ValueError(f"No image files found in {folder}")

    choice = random.choice(candidates)
    rhino_view = sc.doc.Views.Find(view, False) if view else sc.doc.Views.ActiveView
    if rhino_view is None:
        raise ValueError("No active view to set wallpaper.")

    vp = rhino_view.ActiveViewport
    try:
        vp.ClearWallpaper()
    except Exception:
        pass
    vp.SetWallpaper(str(choice), False)
    rhino_view.Redraw()
    return str(choice)
