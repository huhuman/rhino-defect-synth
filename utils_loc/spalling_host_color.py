"""Assign host-derived pure-colour materials to spall cavities (render-only).

Spall cavities keep their `defect::spalling` / `defect::exposed_rebar::*::spalling` layer
(the mask plugin paints the flat LAYER colour, so labels are unchanged) but get a per-OBJECT
material whose colour is derived from the host surface's selected material — so a spall reads
as the same concrete broken open instead of a clashing random gravel texture.

Materials are BASIC materials (DiffuseColor + PBR base colour, matte), shared by colour and
assigned via object material source/index. Only a handful (one per distinct host colour) are
created per model; they are cleared at reset like other materials (call reset_material_cache
after each reset). Pure-colour route — no texture files.

See docs/superpowers/specs/2026-06-25-spalling-host-color-design.md
"""
import json
import os

import System
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

from utils_loc.materials import _enable_physically_based_material

_COLOR_TABLE = None
_COLOR_TABLE_PATH = None
_MATERIAL_CACHE = {}   # (r,g,b,roughness) -> basic material index (per document/model)

_SPALL_RECORD_TYPES = ("spalling", "exposed_rebar")


def _resolve_table_path(path):
    """Rhino's CWD is not the repo root, so a relative color_table_path fails. Resolve it
    relative to the repo root (this module is <repo>/utils_loc/spalling_host_color.py)."""
    if os.path.isfile(path):
        return path
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(repo_root, path)
    if os.path.isfile(candidate):
        return candidate
    return path  # fall through so open() raises a clear message with the original path


def load_color_table(path):
    """Load + cache the {material_name: [[r,g,b],...]} table. Returns {} on failure (but
    does NOT cache the failure, so a later good path/file still loads)."""
    global _COLOR_TABLE, _COLOR_TABLE_PATH
    if _COLOR_TABLE and _COLOR_TABLE_PATH == path:
        return _COLOR_TABLE
    try:
        with open(_resolve_table_path(path)) as fh:
            _COLOR_TABLE = json.load(fh)
        _COLOR_TABLE_PATH = path
    except Exception as exc:  # noqa: BLE001
        print("spalling host-color: could not load table {} ({})".format(path, exc))
        return {}
    return _COLOR_TABLE


def reset_material_cache():
    """Clear the per-document colour-material index cache. Call after a doc reset
    (materials are cleared there, so cached indices would be stale)."""
    _MATERIAL_CACHE.clear()


def _color_material_name(rgb, roughness):
    return "spall_host_{:02x}{:02x}{:02x}_r{:02d}".format(
        int(rgb[0]), int(rgb[1]), int(rgb[2]), int(round(roughness * 10)))


def get_or_create_color_material(rgb, roughness):
    """Return a basic-material INDEX (doc.Materials) for (rgb, roughness), created once
    per colour per document. DiffuseColor + matte + PBR base colour so it renders in
    'Rendered'/ViewCapture. Returns -1 on failure."""
    key = (int(rgb[0]), int(rgb[1]), int(rgb[2]), round(float(roughness), 2))
    if key in _MATERIAL_CACHE:
        return _MATERIAL_CACHE[key]
    name = _color_material_name(rgb, roughness)
    idx = sc.doc.Materials.Find(name, True)
    if idx < 0:
        try:
            col = System.Drawing.Color.FromArgb(255, int(rgb[0]), int(rgb[1]), int(rgb[2]))
            mat = Rhino.DocObjects.Material()
            mat.Name = name
            mat.DiffuseColor = col          # legacy display path
            mat.SpecularColor = System.Drawing.Color.FromArgb(255, 255, 255, 255)
            mat.Shine = 0.0                 # matte concrete
            mat.Reflectivity = 0.0
            _enable_physically_based_material(mat)
            try:
                pbr = getattr(mat, "PhysicallyBased", None)
                if pbr is not None:
                    pbr.BaseColor = Rhino.Display.Color4f(
                        rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)
                    pbr.Roughness = float(roughness)
                    pbr.Metallic = 0.0
            except Exception:               # PBR optional; DiffuseColor still applies
                pass
            idx = sc.doc.Materials.Add(mat)
        except Exception as exc:  # noqa: BLE001
            print("spalling host-color: material create failed for {} ({})".format(name, exc))
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


def apply_spalling_host_color(selected_materials, cfg, rng):
    """Colour each spall cavity from its host surface's selected material. Render-only;
    layers/masks untouched. Never raises."""
    if not cfg or not cfg.get("enabled"):
        return {"enabled": False}

    table = load_color_table(cfg.get("color_table_path", "configs/spalling_host_colors.json"))
    roughness = float(cfg.get("roughness", 0.9))
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
        variants = table.get(host_mat) if host_mat else None
        if not variants:
            fell_back += 1
            continue
        rgb = variants[rng.randint(0, len(variants) - 1)]
        mat_index = get_or_create_color_material(rgb, roughness)
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
