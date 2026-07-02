"""Per-host darkened crack material (render-only).

A crack should read as a thin DARK line of the SAME concrete as the host surface — not a flat
black/grey patch and not the host concrete at full brightness (which has no contrast). For each
crack we rebuild the host surface's material from its texture set and DARKEN the PBR base colour
(a multiplier on the albedo, so the concrete texture/tone is preserved) → the crack looks like a
shadowed/stained recess of the host concrete; the shallow groove adds real depth shadow on top.

Assigned PER-OBJECT to the crack groove geometry (record['geometry_ids']); layers/masks are
untouched (the mask paints the flat CS layer colour, so labels are unchanged). Mirrors
spalling_host_color: host material resolved from selected_material_metadata[surface_layer]['path']
(the chosen texture seed), built with build_material_from_texture_bitmaps, then darkened.

NOTE: darkening relies on the PBR base-colour acting as a multiplier on the albedo texture. Verify
in a render; tune `modeling.defect.crack.dark_factor` (0..1, lower = darker). If a textured base
colour doesn't darken in your Rhino build, fall back to an offline darkened-texture set.
"""
import scriptcontext as sc
import rhinoscriptsyntax as rs

try:
    import Rhino
except Exception:  # pragma: no cover - only inside Rhino
    Rhino = None

from utils_loc.materials import build_material_from_texture_bitmaps, find_texture_bitmaps

_CACHE = {}  # (host_layer, factor) -> basic material index
_CRACK_TYPES = ("crack",)


def reset_material_cache():
    """Clear the per-document cache (materials are cleared on reset, so indices go stale)."""
    _CACHE.clear()


def _index_valid(idx, name):
    try:
        if idx is None or idx < 0 or idx >= sc.doc.Materials.Count:
            return False
        mat = sc.doc.Materials[idx]
        return mat is not None and not getattr(mat, "IsDeleted", False) and mat.Name == name
    except Exception:
        return False


def _darken_base_color(mat, factor):
    f = max(0.0, min(1.0, float(factor)))
    pbr = getattr(mat, "PhysicallyBased", None)
    if pbr is not None:
        try:
            bc = pbr.BaseColor
            pbr.BaseColor = Rhino.Display.Color4f(bc.R * f, bc.G * f, bc.B * f, 1.0)
        except Exception:
            try:
                pbr.BaseColor = Rhino.Display.Color4f(f, f, f, 1.0)
            except Exception:
                pass
        for attr in ("Metallic", "Reflectivity", "Specular"):
            try:
                setattr(pbr, attr, 0.0)
            except Exception:
                pass
        # A real crack interior is a dark, NON-reflective gap. The host material's NORMAL/bump map
        # made the recessed groove WALLS catch specular highlights (they "shine" when light reaches
        # in), and any roughness texture re-introduced gloss. Clear bump/normal + roughness textures
        # and force fully matte so the walls stay dark even when directly lit.
        for tname in ("Bump", "PBR_Bump", "PBR_Normal", "PBR_Roughness"):
            try:
                ttype = getattr(Rhino.DocObjects.TextureType, tname, None)
                if ttype is not None and mat.GetTexture(ttype) is not None:
                    mat.SetTexture(None, ttype)
            except Exception:
                pass
        try:
            pbr.Roughness = 1.0
        except Exception:
            pass


def get_or_create_dark_host_material(host_layer, host_texture_path, factor):
    """Build/find the host's darkened material; return its basic-material index (-1 on fail)."""
    if Rhino is None or not host_layer:
        return -1
    name = "crack_dark_{}".format(str(host_layer).replace("::", "_"))
    key = (str(host_layer), round(float(factor), 3))
    cached = _CACHE.get(key)
    if cached is not None and _index_valid(cached, name):
        return cached
    _CACHE.pop(key, None)
    idx = sc.doc.Materials.Find(name, True)
    if idx < 0:
        if not host_texture_path:
            return -1
        try:
            bitmaps = find_texture_bitmaps(host_texture_path)
            mat, _status = build_material_from_texture_bitmaps(bitmaps, name)
            mat.Name = name
            _darken_base_color(mat, factor)
            idx = sc.doc.Materials.Add(mat)
        except Exception as exc:  # noqa: BLE001
            print("crack dark: material build failed for {} ({})".format(name, exc))
            return -1
        if idx is None or idx < 0:
            return -1
    _CACHE[key] = idx
    return idx


def _assign_object_material(obj_id, mat_index):
    try:
        if not rs.IsObject(obj_id):
            return False
        rs.ObjectMaterialSource(obj_id, 1)   # 1 = material from object
        rs.ObjectMaterialIndex(obj_id, mat_index)
        return True
    except Exception:
        return False


def _load_crack_records():
    """Raw placement records WITH geometry_ids (the cached payload is geometry-stripped)."""
    try:
        from utils_loc import defect_placement
        return list(defect_placement.get_last_placed_records() or [])
    except Exception:
        return []


def _flat_dark_material_index(factor):
    """Flat dark-grey matte material (no texture) for cracks whose host has no texture path."""
    try:
        from utils_loc.flat_matte import get_or_create_flat_matte
        v = int(max(8, min(80, round(150.0 * float(factor)))))  # ~concrete tone * dark_factor
        return get_or_create_flat_matte((v, v, max(8, v - 3)), 0.9, "crack_dark_flat")
    except Exception:
        return -1


def apply_crack_host_dark(selected_material_metadata, cfg=None, dark_factor=0.35):
    """Give every crack groove a single flat near-black MATTE material (render-only; never raises).

    A real crack interior is a dark, non-reflective gap. The earlier per-host approach copied the
    host material WITH its albedo+normal textures: the albedo texture overrode the darkened
    BaseColor (so the groove walls rendered at FULL host brightness — brighter than the surface,
    confirmed: crack-mask colour lum 113 vs 100 around it) and the normal map made the walls catch
    specular highlights ("shiny"). A flat untextured near-black matte has no albedo to override and
    no normal to shine -> the walls stay dark from any angle. `dark_factor` sets the value
    (0..1 -> 0..255); lower = darker. (selected_material_metadata kept for signature compatibility.)
    """
    if Rhino is None:
        return {"enabled": False}
    cfg = dict(cfg or {})
    if cfg.get("enabled") is False:
        return {"enabled": False}
    factor = max(0.0, min(1.0, float(cfg.get("dark_factor", dark_factor))))
    v = int(max(3, min(70, round(255.0 * factor))))
    try:
        from utils_loc.flat_matte import get_or_create_flat_matte
        mat_index = get_or_create_flat_matte((v, v, v), 1.0, "crack_dark")
    except Exception as exc:  # noqa: BLE001
        print("crack dark: material build failed ({})".format(exc))
        return {"enabled": True, "recoloured": 0}
    if mat_index < 0:
        print("crack dark: could not create flat dark material; skipping")
        return {"enabled": True, "recoloured": 0}
    records = _load_crack_records()
    recoloured = 0
    seen = 0
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") not in _CRACK_TYPES:
            continue
        seen += 1
        for oid in rec.get("geometry_ids") or []:
            if _assign_object_material(oid, mat_index):
                recoloured += 1
    print("crack dark: {} crack objects -> flat near-black matte rgb=({},{},{}) ({} crack recs)".format(
        recoloured, v, v, v, seen))
    return {"enabled": True, "recoloured": recoloured, "crack_records": seen}
