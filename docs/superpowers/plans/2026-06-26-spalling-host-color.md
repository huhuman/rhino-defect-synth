# Spalling host-colour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render spall cavities in a pure colour derived from their host surface's selected material (concrete roughness, no texture), so spalls read as the same concrete broken open instead of clashing random gravel — without touching masks/labels.

**Architecture:** An offline tool computes a `{host_material_name: [colour_variants]}` table from the component-surface textures (runs in WSL). At runtime, a post-placement step reads the cached defect records, maps each spall's host layer → host material → a colour variant, and assigns a shared per-colour PBR material to the cavity objects. Spalls stay on their `defect::spalling` layer so the mask (flat layer colour) is unchanged.

**Tech Stack:** Python (numpy, Pillow, PyYAML) for the offline tool; RhinoCommon (`Rhino.DocObjects.Material` PBR) for the runtime material; existing `defect_record_store` + `materials` helpers.

**Testing note:** Tasks 1–4 (offline) are TDD'd with pytest in WSL. Tasks 5–8 (runtime) touch RhinoCommon and CANNOT run in WSL — they are static-checked (`py_compile`) here and verified by the user in a fresh Rhino session per Task 9.

---

## File Structure

- Create `tools/spalling/host_colors.py` — offline colour-table builder (CLI + pure functions).
- Create `tools/spalling/test_host_colors.py` — pytest for the pure functions.
- Create `configs/spalling_host_colors.json` — generated table (committed artifact).
- Create `utils_loc/spalling_host_color.py` — runtime: load table, get-or-create colour material, assign to spall cavities.
- Modify `main_component_batch.py` — `_reapply_texture_mapping` gains `selected_materials`; call site passes it; invoke the new assignment.
- Modify `configs/component.local.yaml` — add `preparation.spalling_host_color` block.
- Reference only: `utils_loc/defect_record_store.py` (`load_defect_record_payload_from_document`), `utils_loc/materials.py` (`_enable_physically_based_material`, `add_material_to_render_table`).

---

## Task 1: Offline — representative colour

**Files:**
- Create: `tools/spalling/host_colors.py`
- Test: `tools/spalling/test_host_colors.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/spalling/test_host_colors.py
import numpy as np
from PIL import Image
from tools.spalling.host_colors import representative_color

def _img(tmp_path, pixels):
    p = tmp_path / "t.png"
    Image.fromarray(np.array(pixels, dtype=np.uint8), "RGB").save(p)
    return str(p)

def test_representative_color_is_trimmed_mean(tmp_path):
    # 100 mid-grey pixels + a few pure-white/black outliers; trimmed mean ~ grey
    px = [[[128,120,110]]*10]*10
    arr = np.array(px, dtype=np.uint8)
    arr[0,0] = [255,255,255]; arr[9,9] = [0,0,0]
    p = tmp_path / "t.png"; Image.fromarray(arr, "RGB").save(p)
    r,g,b = representative_color(str(p), trim_pct=10.0)
    assert abs(r-128) <= 6 and abs(g-120) <= 6 and abs(b-110) <= 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/rhino_modeling/rhino-defect-synth && python3 -m pytest tools/spalling/test_host_colors.py::test_representative_color_is_trimmed_mean -v`
Expected: FAIL (ModuleNotFoundError / no `representative_color`).

- [ ] **Step 3: Write minimal implementation**

```python
# tools/spalling/host_colors.py
import numpy as np
from PIL import Image

def representative_color(image_path, trim_pct=10.0):
    """Trimmed-mean RGB of an image: drop the top/bottom `trim_pct` luminance
    percentiles (kills highlights/shadows), average the rest. Returns (r,g,b) ints."""
    img = Image.open(image_path).convert("RGB")
    arr = np.asarray(img, dtype=np.float64).reshape(-1, 3)
    lum = arr @ np.array([0.299, 0.587, 0.114])
    lo, hi = np.percentile(lum, [trim_pct, 100.0 - trim_pct])
    keep = arr[(lum >= lo) & (lum <= hi)]
    if keep.size == 0:
        keep = arr
    mean = keep.mean(axis=0)
    return tuple(int(round(c)) for c in mean)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tools/spalling/test_host_colors.py::test_representative_color_is_trimmed_mean -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/spalling/host_colors.py tools/spalling/test_host_colors.py
git commit -m "feat(spalling): offline representative-colour extraction"
```

---

## Task 2: Offline — colour variants ("混色列表")

**Files:**
- Modify: `tools/spalling/host_colors.py`
- Test: `tools/spalling/test_host_colors.py`

- [ ] **Step 1: Write the failing test**

```python
from tools.spalling.host_colors import make_variants

def test_make_variants_exact_darker_desaturated():
    base = (120, 110, 100)
    vs = make_variants(base, count=3, darker_factor=0.85, desaturate=0.15)
    assert len(vs) == 3
    assert vs[0] == base                       # first variant = exact
    assert sum(vs[1]) < sum(base)              # second = darker
    # third = desaturated: closer to its own grey than the base is
    g = round(sum(base)/3)
    assert abs(vs[2][0]-g) < abs(base[0]-g) + 1
    for v in vs:                               # all in range
        assert all(0 <= c <= 255 for c in v)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tools/spalling/test_host_colors.py::test_make_variants_exact_darker_desaturated -v`
Expected: FAIL (no `make_variants`).

- [ ] **Step 3: Write minimal implementation**

```python
def make_variants(base, count=3, darker_factor=0.85, desaturate=0.15):
    """Return `count` colour variants of base (r,g,b):
    [exact, darker, desaturated, ...]. Broken interior reads darker/dustier."""
    r, g, b = base
    out = [(int(r), int(g), int(b))]
    if count >= 2:
        out.append(tuple(int(round(c * darker_factor)) for c in base))
    if count >= 3:
        grey = (r + g + b) / 3.0
        out.append(tuple(int(round(c + (grey - c) * desaturate)) for c in base))
    while len(out) < count:                    # pad with mild extra darkening
        f = darker_factor ** (len(out))
        out.append(tuple(max(0, min(255, int(round(c * f)))) for c in base))
    return [tuple(max(0, min(255, c)) for c in v) for v in out[:count]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tools/spalling/test_host_colors.py::test_make_variants_exact_darker_desaturated -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/spalling/host_colors.py tools/spalling/test_host_colors.py
git commit -m "feat(spalling): per-host colour variants"
```

---

## Task 3: Offline — host-name → texture-file resolution + table builder

**Files:**
- Modify: `tools/spalling/host_colors.py`
- Test: `tools/spalling/test_host_colors.py`

Material names (e.g. `Concrete001_2K-PNG`) are texture stems; the base-colour file is
`<stem>` plus a colour suffix or the bare stem. Resolve by globbing the texture root.

- [ ] **Step 1: Write the failing test**

```python
from tools.spalling.host_colors import find_base_texture, build_color_table

def test_find_base_texture_prefers_color_suffix(tmp_path):
    (tmp_path / "Concrete001_2K-PNG_Color.png").write_bytes(b"")  # exists
    (tmp_path / "Concrete001_2K-PNG_Normal.png").write_bytes(b"")
    hit = find_base_texture("Concrete001_2K-PNG", str(tmp_path))
    assert hit.endswith("Concrete001_2K-PNG_Color.png")

def test_build_color_table_maps_name_to_variants(tmp_path):
    import numpy as np
    from PIL import Image
    Image.fromarray(np.full((8,8,3), [130,120,110], np.uint8), "RGB").save(
        tmp_path / "Concrete001_2K-PNG_Color.png")
    table = build_color_table(["Concrete001_2K-PNG", "Missing_Mat"],
                              str(tmp_path), variants=3,
                              darker_factor=0.85, desaturate=0.15, trim_pct=10.0)
    assert "Concrete001_2K-PNG" in table and len(table["Concrete001_2K-PNG"]) == 3
    assert "Missing_Mat" not in table        # unresolved host is skipped (runtime falls back)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tools/spalling/test_host_colors.py -k "find_base_texture or build_color_table" -v`
Expected: FAIL (no `find_base_texture` / `build_color_table`).

- [ ] **Step 3: Write minimal implementation**

```python
import os, glob

_COLOR_SUFFIXES = ("_Color", "_color", "_COL", "_albedo", "_Albedo", "_diffuse", "")
_IMG_EXTS = (".png", ".jpg", ".jpeg")

def find_base_texture(material_name, texture_root):
    """Find the base-colour texture file for a material stem under texture_root
    (recursive). Prefer a *_Color-style companion; fall back to the bare stem."""
    candidates = []
    for root, _dirs, files in os.walk(texture_root):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in _IMG_EXTS:
                continue
            for suf in _COLOR_SUFFIXES:
                if stem == material_name + suf:
                    candidates.append((_COLOR_SUFFIXES.index(suf), os.path.join(root, f)))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])      # lowest suffix index = most preferred
    return candidates[0][1]

def build_color_table(material_names, texture_root, variants=3,
                      darker_factor=0.85, desaturate=0.15, trim_pct=10.0):
    table = {}
    for name in material_names:
        path = find_base_texture(name, texture_root)
        if not path:
            continue
        base = representative_color(path, trim_pct=trim_pct)
        table[name] = make_variants(base, count=variants,
                                    darker_factor=darker_factor, desaturate=desaturate)
    return table
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tools/spalling/test_host_colors.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tools/spalling/host_colors.py tools/spalling/test_host_colors.py
git commit -m "feat(spalling): host-name texture resolution + colour-table builder"
```

---

## Task 4: Offline — CLI + generate the real table

**Files:**
- Modify: `tools/spalling/host_colors.py` (add `main()`/argparse + host-name extraction from config)
- Create (generated): `configs/spalling_host_colors.json`

- [ ] **Step 1: Add CLI that reads host names from component config + writes JSON**

```python
def host_material_names_from_config(component_base_path):
    """Collect every material name listed under component::* in the materials map."""
    import yaml
    cfg = yaml.safe_load(open(component_base_path))
    mats = (cfg.get("materials") or {})
    names = set()
    for layer, lst in mats.items():
        if str(layer).startswith("component::") and isinstance(lst, list):
            names.update(str(n) for n in lst)
    return sorted(names)

def main(argv=None):
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--component-config", default="configs/component_base.yaml")
    ap.add_argument("--texture-root", required=True)
    ap.add_argument("--out", default="configs/spalling_host_colors.json")
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--darker-factor", type=float, default=0.85)
    ap.add_argument("--desaturate", type=float, default=0.15)
    ap.add_argument("--trim-pct", type=float, default=10.0)
    a = ap.parse_args(argv)
    names = host_material_names_from_config(a.component_config)
    table = build_color_table(names, a.texture_root, variants=a.variants,
                              darker_factor=a.darker_factor, desaturate=a.desaturate,
                              trim_pct=a.trim_pct)
    with open(a.out, "w") as fh:
        json.dump(table, fh, indent=2, sort_keys=True)
    print("wrote {} ({} hosts resolved of {} listed)".format(a.out, len(table), len(names)))

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the tool against the real textures (WSL)**

Run: `python3 tools/spalling/host_colors.py --texture-root "/mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/"`
Expected: `wrote configs/spalling_host_colors.json (N hosts resolved of M listed)`, N > 0.

- [ ] **Step 3: Sanity-check the colours**

Run: `python3 -c "import json; t=json.load(open('configs/spalling_host_colors.json')); import statistics as s; vals=[v[0] for v in t.values()]; print('hosts:',len(t)); print('sample:',list(t.items())[:3]); print('all mid-tone:', all(20<sum(c)/3<235 for c in vals))"`
Expected: hosts > 0; `all mid-tone: True` (no pure black/white); sample colours look concrete-ish.

- [ ] **Step 4: Commit the tool + generated table**

```bash
git add tools/spalling/host_colors.py configs/spalling_host_colors.json
git commit -m "feat(spalling): host-colour CLI + generated colour table"
```

---

## Task 5: Runtime — load table + get-or-create colour material

**Files:**
- Create: `utils_loc/spalling_host_color.py`

RhinoCommon-dependent; verify in Rhino (Task 9). Static-check only here.

- [ ] **Step 1: Write the module (loader + material factory)**

```python
# utils_loc/spalling_host_color.py
"""Assign host-derived pure-colour materials to spall cavities (render-only;
masks stay layer-based). See docs/superpowers/specs/2026-06-25-spalling-host-color-design.md."""
import json
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

from utils_loc.materials import _enable_physically_based_material, add_material_to_render_table

_COLOR_TABLE = None
_COLOR_TABLE_PATH = None
_MATERIAL_CACHE = {}   # (r,g,b,roughness) -> render material name, per document session

def load_color_table(path):
    global _COLOR_TABLE, _COLOR_TABLE_PATH
    if _COLOR_TABLE is not None and _COLOR_TABLE_PATH == path:
        return _COLOR_TABLE
    try:
        with open(path) as fh:
            _COLOR_TABLE = json.load(fh)
    except Exception as exc:
        print("spalling host-color: could not load table {} ({})".format(path, exc))
        _COLOR_TABLE = {}
    _COLOR_TABLE_PATH = path
    return _COLOR_TABLE

def _color_material_name(rgb, roughness):
    return "spall_host_{:02x}{:02x}{:02x}_r{:02d}".format(
        rgb[0], rgb[1], rgb[2], int(round(roughness * 10)))

def get_or_create_color_material(rgb, roughness):
    """Return the DisplayName of a flat-colour PBR material for (rgb, roughness),
    creating + importing it once per document. Returns None on failure."""
    key = (int(rgb[0]), int(rgb[1]), int(rgb[2]), round(roughness, 2))
    name = _color_material_name(rgb, roughness)
    if key in _MATERIAL_CACHE:
        return _MATERIAL_CACHE[key]
    try:
        mat = Rhino.DocObjects.Material()
        mat.Name = name
        _enable_physically_based_material(mat)
        pbr = mat.PhysicallyBased
        pbr.BaseColor = Rhino.Display.Color4f(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)
        pbr.Roughness = float(roughness)
        pbr.Metallic = 0.0
        render_material, _info = add_material_to_render_table(mat, material_name=name, make_unique=True)
        final = render_material.Name if render_material is not None else name
    except Exception as exc:
        print("spalling host-color: material create failed for {} ({})".format(name, exc))
        return None
    _MATERIAL_CACHE[key] = final
    return final

def reset_material_cache():
    """Call when the document is reset (materials cleared) so names are re-created."""
    _MATERIAL_CACHE.clear()
```

- [ ] **Step 2: Static-check**

Run: `python3 -m py_compile utils_loc/spalling_host_color.py`
Expected: no output (compiles). (Imports fail only at Rhino runtime, not at compile.)

- [ ] **Step 3: Commit**

```bash
git add utils_loc/spalling_host_color.py
git commit -m "feat(spalling): runtime colour-table loader + PBR colour material factory"
```

---

## Task 6: Runtime — assign colours to spall cavities

**Files:**
- Modify: `utils_loc/spalling_host_color.py`

- [ ] **Step 1: Add the assignment function**

```python
from utils_loc.defect_record_store import load_defect_record_payload_from_document

_SPALL_RECORD_TYPES = ("spalling", "exposed_rebar")

def apply_spalling_host_color(selected_materials, cfg, rng):
    """For each spalling/exposed_rebar record, colour its cavity objects from the host
    surface's selected material. Returns a summary dict. Never raises."""
    if not cfg or not cfg.get("enabled"):
        return {"enabled": False}
    table = load_color_table(cfg.get("color_table_path", "configs/spalling_host_colors.json"))
    roughness = float(cfg.get("roughness", 0.9))
    selected_materials = dict(selected_materials or {})

    payload = load_defect_record_payload_from_document() or {}
    records = payload.get("records") or []
    recoloured = 0
    fell_back = 0
    for rec in records:
        if rec.get("type") not in _SPALL_RECORD_TYPES:
            continue
        spall_ids = rec.get("spall_geometry_ids") or rec.get("geometry_ids") or []
        if not spall_ids:
            continue
        host_layer = rec.get("surface_layer")
        host_mat = selected_materials.get(host_layer)
        variants = table.get(host_mat) if host_mat else None
        if not variants:
            fell_back += 1
            continue
        rgb = variants[rng.randint(0, len(variants) - 1)]
        mat_name = get_or_create_color_material(rgb, roughness)
        if not mat_name:
            fell_back += 1
            continue
        for oid in spall_ids:
            try:
                if rs.IsObject(oid):
                    rs.ObjectMaterialSource(oid, 1)   # 1 = material from object
                    idx = sc.doc.Materials.Find(mat_name, True)
                    if idx >= 0:
                        rs.ObjectMaterialIndex(oid, idx)
                    recoloured += 1
            except Exception:
                continue
    print("spalling host-color: {} cavities recoloured, {} fell back".format(recoloured, fell_back))
    return {"enabled": True, "recoloured": recoloured, "fell_back": fell_back}
```

- [ ] **Step 2: Static-check**

Run: `python3 -m py_compile utils_loc/spalling_host_color.py`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add utils_loc/spalling_host_color.py
git commit -m "feat(spalling): assign host colour to spall cavity objects"
```

---

## Task 7: Wire into the batch + plumb selected_materials

**Files:**
- Modify: `main_component_batch.py` (`_reapply_texture_mapping` signature + body; the call site ~line 552; add `rng`/config access)

- [ ] **Step 1: Extend `_reapply_texture_mapping` to take and use selected_materials**

In `_reapply_texture_mapping(modeling_params, material_metadata)` change the signature to
`_reapply_texture_mapping(modeling_params, material_metadata, selected_materials=None, host_color_cfg=None, rng=None)`
and append, after the spalling/component mapping blocks:

```python
    if host_color_cfg and host_color_cfg.get("enabled") and rng is not None:
        from utils_loc.spalling_host_color import apply_spalling_host_color
        apply_spalling_host_color(selected_materials or {}, host_color_cfg, rng)
```

- [ ] **Step 2: Pass the new args at the call site (~line 552)**

```python
                if reapply_mapping_per_render:
                    _reapply_texture_mapping(
                        sampled_modeling_params,
                        material_metadata=prepare_result.get("selected_material_metadata"),
                        selected_materials=prepare_result.get("selected_materials"),
                        host_color_cfg=preparation_params.get("spalling_host_color"),
                        rng=rng,
                    )
```

- [ ] **Step 3: Reset the colour-material cache each model (after reset)**

After `main_entry.reset()` (~line 486) add:

```python
            try:
                from utils_loc.spalling_host_color import reset_material_cache
                reset_material_cache()
            except Exception:
                pass
```

- [ ] **Step 4: Static-check**

Run: `python3 -m py_compile main_component_batch.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add main_component_batch.py
git commit -m "feat(spalling): wire host-colour assignment into batch post-mapping"
```

---

## Task 8: Config block

**Files:**
- Modify: `configs/component.local.yaml` (under `preparation:`)

- [ ] **Step 1: Add the config**

```yaml
  spalling_host_color:
    enabled: true
    color_table_path: "configs/spalling_host_colors.json"
    roughness: 0.9
    # the following are recorded for reproducibility of the offline table:
    variants: 3
    darker_factor: 0.85
    desaturate: 0.15
```

- [ ] **Step 2: Validate YAML**

Run: `python3 -c "import yaml; print(yaml.safe_load(open('configs/component.local.yaml'))['preparation']['spalling_host_color'])"`
Expected: prints the dict with `enabled: True`.

- [ ] **Step 3: Commit**

```bash
git add configs/component.local.yaml
git commit -m "feat(spalling): enable host-colour in component.local config"
```

---

## Task 9: Rhino verification (user, fresh session)

Not automatable in WSL. After a FRESH Rhino restart (clears the Python module cache):

- [ ] Run a short component batch (max_iter ~5).
- [ ] Log shows `spalling host-color: N cavities recoloured, M fell back` with N > 0.
- [ ] In the colour renders, spall cavities take a tone matching their host component; two
      spalls on differently-textured components in the same model differ in colour.
- [ ] Masks still show spalling layer colours (Gold/DarkOrange/Goldenrod/Chocolate) — labels unchanged.
- [ ] `basic_materials=` growth is bounded (only a handful of `spall_host_*` materials per model).
- [ ] Set `spalling_host_color.enabled: false` → old gravel look returns (revert path works).

---

## Self-Review notes

- **Spec coverage:** offline table (Tasks 1–4), runtime loader+factory (5), assignment incl. exposed_rebar + fallback (6), hook+plumb+cache-reset (7), config+revert flag (8), verification incl. mask-unchanged + material-bound (9). All spec sections covered.
- **Type consistency:** `representative_color`/`make_variants`/`find_base_texture`/`build_color_table` (offline) and `load_color_table`/`get_or_create_color_material`/`reset_material_cache`/`apply_spalling_host_color` (runtime) names are used consistently across tasks.
- **Risk / verify-in-Rhino:** the PBR API (`Material.PhysicallyBased.BaseColor/Roughness/Metallic`) and `rs.ObjectMaterialIndex`/`Materials.Find` paths are the parts most likely to need adjustment on first Rhino run; Task 9 covers them. `add_material_to_render_table` is reused as-is so colour materials respect `material_reuse` naming.
