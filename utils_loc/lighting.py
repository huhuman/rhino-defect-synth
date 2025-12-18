"""Sun-only lighting helpers and simple wallpaper utility."""

import random
from datetime import datetime
from pathlib import Path

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

    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}
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
