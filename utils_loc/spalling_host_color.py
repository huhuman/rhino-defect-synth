"""Assign host-tinted gravel materials to spall cavities (render-only).

Spall cavities keep their `defect::spalling` / `defect::exposed_rebar::*::spalling` layer (the
mask plugin paints the flat LAYER colour, so labels are unchanged) but get a per-OBJECT
material: a gravel PBR texture (its normal/roughness relief preserved) whose albedo is tinted
toward the host surface's selected material — so a spall reads as the same concrete broken
open, with aggregate texture + depth, instead of a clashing random gravel OR a flat colour patch.

The tinted-gravel texture sets are generated offline by `tools/spalling/tinted_gravel.py` into
`proc_texture_dir` as `spallhost_<hostName>_BaseColor/_Normal/_Roughness…`. Only a handful of
materials (one per distinct host) are imported per model; they are cleared at reset like other
materials (call reset_material_cache after each reset).

See docs/superpowers/specs/2026-06-26-depth-realism-oblique-camera-tinted-gravel-design.md
"""
import os

import scriptcontext as sc
import rhinoscriptsyntax as rs

from utils_loc.materials import (
    build_material_from_texture_bitmaps,
    find_texture_bitmaps,
)

_MATERIAL_CACHE = {}   # ("host", host_name) -> basic material index (per document/model)
_DIAG_LOGGED = False

_SPALL_RECORD_TYPES = ("spalling", "exposed_rebar")


def reset_material_cache():
    """Clear the per-document material-index cache. Call after a doc reset (materials are
    cleared there, so cached indices would be stale)."""
    global _DIAG_LOGGED
    _MATERIAL_CACHE.clear()
    _DIAG_LOGGED = False


def _basic_index_valid(idx, name):
    """A cached basic-material index goes stale if the Materials table was cleared/reindexed
    since it was cached (e.g. a per-render clear_imported_materials_from_doc when
    material_reuse is off). Confirm the slot still holds our named material before reusing it."""
    try:
        if idx is None or idx < 0 or idx >= sc.doc.Materials.Count:
            return False
        mat = sc.doc.Materials[idx]
        return mat is not None and not getattr(mat, "IsDeleted", False) and mat.Name == name
    except Exception:
        return False


def get_or_create_host_material(host_name, proc_dir):
    """Build/import the host's tinted-gravel material (textured: albedo + normal + roughness)
    and return its basic-material index. Shared per host per document; -1 on failure/missing."""
    global _DIAG_LOGGED
    name = "spall_host_{}".format(host_name)
    key = ("host", str(host_name))
    cached = _MATERIAL_CACHE.get(key)
    if cached is not None and _basic_index_valid(cached, name):
        return cached
    _MATERIAL_CACHE.pop(key, None)  # stale (table cleared/reindexed) -> rebuild below
    base_color = os.path.join(proc_dir or "", "spallhost_{}_BaseColor.png".format(host_name))
    if not proc_dir or not os.path.isfile(base_color):
        if not _DIAG_LOGGED:
            _DIAG_LOGGED = True
            try:
                listing = os.listdir(proc_dir)[:3] if proc_dir and os.path.isdir(proc_dir) else "<no dir>"
            except Exception as exc:  # noqa: BLE001
                listing = "<listdir err: {}>".format(exc)
            print("spalling host-color DIAG: host={!r} proc_dir={!r} isdir={} base={!r} "
                  "isfile={} sample={}".format(
                      host_name, proc_dir, os.path.isdir(proc_dir or ""),
                      base_color, os.path.isfile(base_color), listing))
        return -1
    idx = sc.doc.Materials.Find(name, True)
    if idx < 0:
        try:
            bitmaps = find_texture_bitmaps(base_color)
            mat, _status = build_material_from_texture_bitmaps(bitmaps, name)
            mat.Name = name
            # Add as a BASIC material directly (like the proven flat-colour path) so we get a
            # real doc.Materials index to assign per-object — add_material_to_render_table made
            # a RenderMaterial whose backing basic wasn't findable by name (silent -1 -> fallback).
            idx = sc.doc.Materials.Add(mat)
        except Exception as exc:  # noqa: BLE001
            print("spalling host-color: tinted-gravel material failed for {} ({})".format(name, exc))
            return -1
        if idx is None or idx < 0:
            if not _DIAG_LOGGED:
                _DIAG_LOGGED = True
                print("spalling host-color DIAG: Materials.Add returned {!r} for {}".format(idx, name))
            return -1
    _MATERIAL_CACHE[key] = idx
    return idx


def _assign_object_material(obj_id, mat_index):
    """Set object's material to the given basic-material index (source = from object)."""
    try:
        if not rs.IsObject(obj_id):
            return False
        rs.ObjectMaterialSource(obj_id, 1)      # 1 = material from object
        rs.ObjectMaterialIndex(obj_id, mat_index)
        return True
    except Exception:
        return False


def _load_defect_records():
    """Raw placement records WITH geometry_ids — needed to find the actual spall objects.
    The returned/cached defect payload runs through _json_ready_records, which STRIPS every
    *_geometry_ids key, so we must read defect_placement's raw in-session stash instead."""
    try:
        from utils_loc import defect_placement
        recs = defect_placement.get_last_placed_records()
        if recs:
            return list(recs)
    except Exception:
        pass
    return []


def apply_spalling_host_color(selected_materials, cfg, rng=None):
    """Give each spall cavity its host's tinted-gravel material. Render-only; layers/masks
    untouched. Never raises. (rng kept for signature compatibility; unused.)"""
    if not cfg or not cfg.get("enabled"):
        return {"enabled": False}

    proc_dir = cfg.get("proc_texture_dir", "")
    selected_materials = dict(selected_materials or {})

    records = _load_defect_records()
    recoloured = 0
    fell_back = 0
    spall_seen = 0
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") not in _SPALL_RECORD_TYPES:
            continue
        spall_seen += 1
        spall_ids = rec.get("spall_geometry_ids") or rec.get("geometry_ids") or []
        if not spall_ids:
            continue
        host_mat = selected_materials.get(rec.get("surface_layer"))
        mat_index = get_or_create_host_material(host_mat, proc_dir) if host_mat else -1
        if mat_index < 0:
            fell_back += 1
            continue
        for oid in spall_ids:
            if _assign_object_material(oid, mat_index):
                recoloured += 1
    print("spalling host-color: {} cavities recoloured, {} fell back "
          "({} spall recs / {} total)".format(
              recoloured, fell_back, spall_seen, len(records)))
    return {"enabled": True, "recoloured": recoloured, "fell_back": fell_back,
            "spall_records": spall_seen, "total_records": len(records)}
