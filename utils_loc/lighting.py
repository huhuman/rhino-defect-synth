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


def delete_named_lights(prefixes):
    """Delete Rhino lights whose names match any provided prefix."""
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    normalized = [
        str(prefix).strip().lower()
        for prefix in (prefixes or [])
        if str(prefix).strip()
    ]
    if not normalized:
        return 0

    deleted = 0
    for idx in range(sc.doc.Lights.Count - 1, -1, -1):
        light_obj = sc.doc.Lights[idx]
        name = str(getattr(light_obj, "Name", "") or "").strip().lower()
        if not name:
            continue
        if any(name == prefix or name.startswith(prefix) for prefix in normalized):
            sc.doc.Lights.Delete(idx, True)
            deleted += 1
    return deleted


def set_light_intensity(light_id, value):
    """Set Rhino light intensity for an existing light object."""
    light_obj = sc.doc.Lights.FindId(light_id)
    if not light_obj:
        return False

    geometry = light_obj.DuplicateLightGeometry()
    geometry.Intensity = float(value)
    sc.doc.Lights.Modify(light_obj.Id, geometry)
    return True


def random_natural_light_color():
    """Sample the same warm sunset-biased helper-light colors used by cube lights."""
    bucket = random.random()
    if bucket < 0.35:
        hue_deg = random.uniform(2.0, 15.0)
    elif bucket < 0.80:
        hue_deg = random.uniform(15.0, 38.0)
    else:
        hue_deg = random.uniform(38.0, 58.0)

    hue = hue_deg / 360.0
    saturation = random.uniform(0.18, 0.65)
    value = random.uniform(0.45, 0.90)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return int(red * 255), int(green * 255), int(blue * 255)


def create_targeted_light(
    position,
    target=None,
    light_type="directional",
    intensity=0.5,
    color=None,
    name=None,
    spot_hotspot=0.6,
    spot_falloff=55.0,
):
    """Create a Rhino light at a position, optionally aimed at a target."""
    pos = tuple(float(v) for v in position)
    tgt = tuple(float(v) for v in (target or (0.0, 0.0, 0.0)))
    light_kind = str(light_type or "directional").strip().lower()

    if light_kind == "point":
        light_id = rs.AddPointLight(pos)
    elif light_kind == "spot":
        direction = tuple(tgt[i] - pos[i] for i in range(3))
        light_id = None
        for args in (
            (pos, direction, spot_falloff, spot_hotspot),
            (pos, direction, spot_hotspot, spot_falloff),
        ):
            try:
                light_id = rs.AddSpotLight(*args)
                if light_id:
                    break
            except Exception:
                light_id = None
    else:
        light_id = rs.AddDirectionalLight(pos, tgt)

    if not light_id and light_kind != "directional":
        light_id = rs.AddDirectionalLight(pos, tgt)
    if not light_id:
        return None

    set_light_intensity(light_id, intensity)
    rs.LightColor(light_id, color or random_natural_light_color())
    if name:
        rs.ObjectName(light_id, str(name))
    return light_id


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

    if replace_existing:
        delete_named_lights("face_light")

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

        lid = create_targeted_light(
            position=pos,
            target=center,
            light_type=light_type,
            intensity=intensities[i],
            color=random_natural_light_color(),
            name=f"face_light_{face.lower()}",
            spot_hotspot=spot_hotspot,
            spot_falloff=spot_falloff,
        )
        if not lid:
            continue

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
