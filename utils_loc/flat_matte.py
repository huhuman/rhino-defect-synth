"""Flat matte material override for specific layers (render-only).

Some surfaces look wrong with the sampled textured PBR materials:
  - Bearings: glossy Rubber/plastic PBR shows a teal sheen at grazing angles (mid-gloss
    fresnel reflecting the bright skylight) + moire banding from the normal map box-mapped onto
    the small bearing boxes. A real elastomeric bearing is a dark near-matte block.
  - Cracks: the crack groove inherits a concrete material (same as the host surface),
    so the crack has almost no colour contrast and only reads via shadow. A real crack is a dark
    line. (The mask uses the flat LAYER colour, so the mask is unaffected by this override.)

Fix: build a flat (untextured) dark PBR material — dark base colour, no normal -> no banding,
high roughness -> no sky reflection — and assign it PER-OBJECT (material source = from object)
to the targeted layers' objects. Layers/masks are untouched, so defect labels are unchanged.
Mirrors the per-object assignment proven by spalling_host_color.
"""
import scriptcontext as sc
import rhinoscriptsyntax as rs

try:
    import Rhino
except Exception:  # pragma: no cover - only importable inside Rhino
    Rhino = None

_MAT_CACHE = {}  # name -> basic material index (per document/model)


def reset_material_cache():
    """Clear the per-document material-index cache. Call after a doc reset (materials are
    cleared there, so cached indices would be stale)."""
    _MAT_CACHE.clear()


def _index_valid(idx, name):
    try:
        if idx is None or idx < 0 or idx >= sc.doc.Materials.Count:
            return False
        mat = sc.doc.Materials[idx]
        return mat is not None and not getattr(mat, "IsDeleted", False) and mat.Name == name
    except Exception:
        return False


def get_or_create_flat_matte(rgb, roughness, name):
    """Build/find a flat untextured matte PBR material; return its basic-material index (-1 fail).
    rgb is 0-255; roughness 0..1. Shared per name per document."""
    if Rhino is None:
        return -1
    cached = _MAT_CACHE.get(name)
    if cached is not None and _index_valid(cached, name):
        return cached
    _MAT_CACHE.pop(name, None)
    idx = sc.doc.Materials.Find(name, True)
    if idx < 0:
        try:
            mat = Rhino.DocObjects.Material()
            mat.Name = name
            try:
                mat.ToPhysicallyBased()
            except Exception:
                pass
            pbr = getattr(mat, "PhysicallyBased", None)
            r, g, b = (max(0.0, min(1.0, float(c) / 255.0)) for c in rgb)
            if pbr is not None:
                try:
                    pbr.BaseColor = Rhino.Display.Color4f(r, g, b, 1.0)
                except Exception:
                    pass
                for attr, val in (("Roughness", float(roughness)), ("Metallic", 0.0),
                                  ("Reflectivity", 0.0), ("Specular", 0.0)):
                    try:
                        setattr(pbr, attr, val)
                    except Exception:
                        pass
            idx = sc.doc.Materials.Add(mat)
        except Exception as exc:  # noqa: BLE001
            print("flat matte: material build failed for {} ({})".format(name, exc))
            return -1
        if idx is None or idx < 0:
            return -1
    _MAT_CACHE[name] = idx
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


def apply_flat_matte_to_layers(layer_names, rgb, roughness, name, label="flat matte"):
    """Assign a flat matte material to every object on the given layers. Never raises.

    layer_names: iterable of full layer names (e.g. 'component::bearing', 'defect::crack::CS2').
    """
    if Rhino is None:
        return {"enabled": False}
    layer_names = [str(n).strip() for n in (layer_names or []) if str(n or "").strip()]
    if not layer_names:
        return {"enabled": False}
    mat_index = get_or_create_flat_matte(rgb, roughness, name)
    if mat_index < 0:
        print("{}: could not create material '{}'; skipping".format(label, name))
        return {"enabled": True, "assigned": 0}
    assigned = 0
    for layer in layer_names:
        try:
            if not rs.IsLayer(layer):
                continue
            obj_ids = rs.ObjectsByLayer(layer) or []
        except Exception:
            obj_ids = []
        for oid in obj_ids:
            if _assign_object_material(oid, mat_index):
                assigned += 1
    print("{}: {} objects on {} layer(s) set to flat matte (rgb={}, roughness={})".format(
        label, assigned, len(layer_names), tuple(rgb), roughness))
    return {"enabled": True, "assigned": assigned}
