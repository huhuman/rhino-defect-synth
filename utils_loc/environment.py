"""Document-level metadata and environment setup."""

import scriptcontext as sc

try:
    import Rhino
except Exception:  # Rhino is only importable inside Rhino; guard for tooling/lint.
    Rhino = None


_UNIT_SYSTEM_NAMES = {
    "mm": "Millimeters",
    "millimeter": "Millimeters",
    "millimeters": "Millimeters",
    "cm": "Centimeters",
    "centimeter": "Centimeters",
    "centimeters": "Centimeters",
    "m": "Meters",
    "meter": "Meters",
    "meters": "Meters",
}


def apply_document_metadata(doc, metadata):
    """
    Apply metadata (author, project info, custom keys) to the Rhino document.

    Args:
        doc: Rhino document reference.
        metadata: mapping of keys → values to attach.
    """
    raise NotImplementedError("Wire Rhino document metadata as needed.")


def _coerce_positive_float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0.0 else None


def ensure_document_environment(units=None, tolerances=None, named_views=None):
    """Set document-wide units / tolerances from config.

    Only values that are explicitly provided are changed; anything left as None keeps
    the document's current setting, so this never disturbs an existing run unless asked.
    Units are switched WITHOUT rescaling existing geometry. Returns a dict of the values
    actually applied (for logging). ``named_views`` is accepted but not yet handled.
    """
    doc = sc.doc
    applied = {}

    if units and Rhino is not None:
        name = _UNIT_SYSTEM_NAMES.get(str(units).strip().lower())
        unit_enum = getattr(Rhino.UnitSystem, name, None) if name else None
        if unit_enum is not None and doc.ModelUnitSystem != unit_enum:
            # scale=False keeps geometry coordinates as-is, only relabels the unit.
            doc.AdjustModelUnitSystem(unit_enum, False)
            applied["units"] = name

    tolerances = tolerances or {}

    abs_tol = _coerce_positive_float(tolerances.get("absolute"))
    if abs_tol is not None:
        doc.ModelAbsoluteTolerance = abs_tol
        applied["absolute_tolerance"] = abs_tol

    angle_tol = _coerce_positive_float(tolerances.get("angle_degrees"))
    if angle_tol is not None:
        doc.ModelAngleToleranceDegrees = angle_tol
        applied["angle_tolerance_degrees"] = angle_tol

    return applied
