# Depth Realism (oblique camera + host-tinted gravel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make spall/crack depth legible by viewing defects obliquely (not head-on) and rendering spall cavities with a host-tinted gravel material that keeps real surface relief (normal map).

**Architecture:** Part A puts the pure-math view-direction sampler in a Rhino-free module (`camera_geometry.py`) so it's unit-testable in WSL, and wires it into `camera.generate_defect_camera_poses` + `render.py` + config. Part B adds an offline numpy/PIL generator that tints a gravel PBR set's albedo toward each host colour (keeping its normal/roughness maps), then swaps the spall runtime material from flat colour to that tinted-gravel set.

**Tech Stack:** Python (numpy, Pillow) for offline + pure math; RhinoCommon for the runtime material. Tests are plain-assert runners (`python3 <test>.py`) — repo has no pytest.

**Testing note:** Tasks A1, B1, B2 run fully in WSL. Tasks A2/A3 (camera wiring) and B3/B4 (runtime/config) touch Rhino-imported modules or live config — static-check (`py_compile`) here, verify in Rhino per Task C.

---

## File Structure

- Create `utils_loc/camera_geometry.py` — pure-math `sample_view_direction` + tiny vector helpers (no Rhino import).
- Create `utils_loc/test_camera_geometry.py` — plain-assert tests.
- Modify `utils_loc/camera.py` — import + use `sample_view_direction` in `generate_defect_camera_poses`; new params.
- Modify `utils_loc/render.py:533` — pass `oblique_angle_range` + `head_on_fraction` from `component_cfg`.
- Modify `configs/component.local.yaml` — camera params + `spalling_host_color` proc dir.
- Create `tools/spalling/tinted_gravel.py` — offline tint + generator CLI.
- Create `tools/spalling/test_tinted_gravel.py` — plain-assert tests.
- Modify `utils_loc/spalling_host_color.py` — host-tinted-gravel material instead of flat colour.

---

## Part A — Oblique camera

### Task A1: pure-math view-direction sampler

**Files:**
- Create: `utils_loc/camera_geometry.py`
- Test: `utils_loc/test_camera_geometry.py`

- [ ] **Step 1: Write the failing tests**

```python
# utils_loc/test_camera_geometry.py
import math, os, sys, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_geometry import sample_view_direction, _angle_between

NORMAL = (0.0, 0.0, 1.0)

def test_oblique_angle_within_range():
    random.seed(1)
    for _ in range(200):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.0, jitter_deg=0.0)
        ang = math.degrees(_angle_between(d, NORMAL))
        assert 20.0 - 1e-6 <= ang <= 50.0 + 1e-6, ang

def test_always_outward():
    random.seed(2)
    for _ in range(200):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.25, jitter_deg=5.0)
        assert d[0]*NORMAL[0] + d[1]*NORMAL[1] + d[2]*NORMAL[2] > 0.0

def test_head_on_fraction_roughly_holds():
    random.seed(3)
    near = 0
    n = 1000
    for _ in range(n):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.3, jitter_deg=0.0)
        if math.degrees(_angle_between(d, NORMAL)) < 10.0:
            near += 1
    assert 0.2 < near / n < 0.4, near / n

def test_no_range_is_head_on():
    random.seed(4)
    d = sample_view_direction(NORMAL, oblique_range=None,
                              head_on_fraction=0.0, jitter_deg=0.0)
    assert math.degrees(_angle_between(d, NORMAL)) < 1e-6

def _run():
    ts = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for t in ts:
        try:
            t(); print("  PASS", t.__name__)
        except Exception as e:  # noqa: BLE001
            bad += 1; print("  FAIL", t.__name__, "->", repr(e))
    print("{}/{} passed".format(len(ts) - bad, len(ts)))
    return bad

if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/rhino_modeling/rhino-defect-synth && python3 utils_loc/test_camera_geometry.py`
Expected: FAIL (ModuleNotFoundError / no `sample_view_direction`).

- [ ] **Step 3: Implement**

```python
# utils_loc/camera_geometry.py
"""Pure-math camera view-direction sampling (NO Rhino import, so it's WSL-unit-testable).
Used by camera.generate_defect_camera_poses to view defects obliquely for depth, not head-on."""
import math
import random


def _normalize(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _angle_between(a, b):
    a = _normalize(a); b = _normalize(b)
    return math.acos(max(-1.0, min(1.0, _dot(a, b))))


def _orthonormal_basis(normal):
    n = _normalize(normal)
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
    u = _normalize(_cross(ref, n))
    v = _normalize(_cross(n, u))
    return u, v


def _jitter(direction, max_deg, rng):
    if max_deg <= 0.0:
        return direction
    u, v = _orthonormal_basis(direction)
    phi = rng.uniform(0.0, 2.0 * math.pi)
    t = (u[0] * math.cos(phi) + v[0] * math.sin(phi),
         u[1] * math.cos(phi) + v[1] * math.sin(phi),
         u[2] * math.cos(phi) + v[2] * math.sin(phi))
    scale = math.tan(math.radians(rng.uniform(0.0, max_deg)))
    return _normalize((direction[0] + t[0] * scale,
                       direction[1] + t[1] * scale,
                       direction[2] + t[2] * scale))


def sample_view_direction(normal, oblique_range, head_on_fraction, jitter_deg, rng=random):
    """Unit view direction on the outward hemisphere of `normal`.
    With prob head_on_fraction (or if oblique_range is falsy): near head-on (just jitter).
    Else oblique: tilt theta in oblique_range degrees off the normal at a random azimuth."""
    n = _normalize(normal)
    if not oblique_range or rng.random() < float(head_on_fraction):
        d = n
    else:
        theta = math.radians(rng.uniform(float(oblique_range[0]), float(oblique_range[1])))
        phi = rng.uniform(0.0, 2.0 * math.pi)
        u, v = _orthonormal_basis(n)
        tangent = (u[0] * math.cos(phi) + v[0] * math.sin(phi),
                   u[1] * math.cos(phi) + v[1] * math.sin(phi),
                   u[2] * math.cos(phi) + v[2] * math.sin(phi))
        ct, st = math.cos(theta), math.sin(theta)
        d = (n[0] * ct + tangent[0] * st, n[1] * ct + tangent[1] * st, n[2] * ct + tangent[2] * st)
    d = _jitter(d, float(jitter_deg or 0.0), rng)
    if _dot(d, n) <= 0.0:   # numerical guard: keep it in front of the surface
        d = n
    return _normalize(d)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 utils_loc/test_camera_geometry.py`
Expected: `4/4 passed`

- [ ] **Step 5: Commit**

```bash
git add utils_loc/camera_geometry.py utils_loc/test_camera_geometry.py
git commit -m "feat(camera): pure-math oblique view-direction sampler (WSL-testable)"
```

### Task A2: use the sampler in generate_defect_camera_poses

**Files:**
- Modify: `utils_loc/camera.py` (imports; `generate_defect_camera_poses` signature + the candidate-direction line ~392)

- [ ] **Step 1: Add the import**

At the top of `utils_loc/camera.py`, after the existing imports, add:

```python
from utils_loc.camera_geometry import sample_view_direction
```

- [ ] **Step 2: Add params to the signature**

Change `generate_defect_camera_poses(...)` to add two keyword params (default off = current behaviour):

```python
    min_visible_size_ratio: float = 0.0,
    framing_factor_by_type: Mapping[str, float] = None,
    oblique_angle_range: Sequence[float] = None,
    head_on_fraction: float = 0.0,
) -> List[Mapping[str, Vec3]]:
```

- [ ] **Step 3: Normalize the new params (near `jitter_deg = ...`, ~line 348)**

```python
    jitter_deg = max(0.0, float(direction_jitter_degrees))
    oblique = None
    if oblique_angle_range and len(list(oblique_angle_range)) == 2:
        lo, hi = float(oblique_angle_range[0]), float(oblique_angle_range[1])
        if hi > 0.0:
            oblique = (max(0.0, min(lo, hi)), max(lo, hi))
    head_on_frac = min(1.0, max(0.0, float(head_on_fraction or 0.0)))
```

- [ ] **Step 4: Replace the candidate-direction call (~line 392)**

```python
                cand_dir = sample_view_direction(
                    normal, oblique, head_on_frac, jitter_deg
                )
```

(Replaces `cand_dir = _direction_on_normal_hemisphere(normal, jitter_deg)`. The old helper
may stay unused or be removed; leave it to avoid churn.)

- [ ] **Step 5: Static-check**

Run: `python3 -m py_compile utils_loc/camera.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add utils_loc/camera.py
git commit -m "feat(camera): oblique view sampling in generate_defect_camera_poses"
```

### Task A3: wire render.py + config

**Files:**
- Modify: `utils_loc/render.py:533` (the `generate_defect_camera_poses(...)` call)
- Modify: `configs/component.local.yaml` (under `rendering.camera.component`)

- [ ] **Step 1: Pass the new args (in the call at render.py ~line 542, before the closing `)`)**

```python
        framing_factor_by_type=component_cfg.get("framing_factor_by_type"),
        oblique_angle_range=component_cfg.get("oblique_angle_range"),
        head_on_fraction=float(component_cfg.get("head_on_fraction", 0.0) or 0.0),
    )
```

- [ ] **Step 2: Static-check**

Run: `python3 -m py_compile utils_loc/render.py`
Expected: no output.

- [ ] **Step 3: Add config (under `rendering.camera.component`)**

```yaml
      oblique_angle_range: [20.0, 50.0]
      head_on_fraction: 0.25
```

- [ ] **Step 4: Validate YAML**

Run: `python3 -c "import yaml; c=yaml.safe_load(open('configs/component.local.yaml'))['rendering']['camera']['component']; print(c['oblique_angle_range'], c['head_on_fraction'])"`
Expected: `[20.0, 50.0] 0.25`

- [ ] **Step 5: Commit**

```bash
git add utils_loc/render.py
git commit -m "feat(camera): wire oblique-angle config into component camera poses"
```

---

## Part B — Host-tinted gravel spall material

### Task B1: ratio-scale tint

**Files:**
- Create: `tools/spalling/tinted_gravel.py`
- Test: `tools/spalling/test_tinted_gravel.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/spalling/test_tinted_gravel.py
import os, sys, tempfile
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tinted_gravel import ratio_tint

def test_ratio_tint_shifts_mean_keeps_variance():
    rng = np.random.default_rng(0)
    base = (rng.normal(120, 25, (64, 64, 3)).clip(0, 255)).astype(np.uint8)
    target = (60, 90, 140)
    out = ratio_tint(base, target)
    m = out.reshape(-1, 3).mean(axis=0)
    assert all(abs(m[i] - target[i]) <= 6 for i in range(3)), m
    # variance preserved (aggregate detail not flattened): per-channel std stays > half base std
    bstd = base.reshape(-1, 3).std(axis=0)
    ostd = out.reshape(-1, 3).std(axis=0)
    assert all(ostd[i] > 0.4 * bstd[i] for i in range(3)), (bstd, ostd)

def _run():
    ts = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for t in ts:
        try:
            t(); print("  PASS", t.__name__)
        except Exception as e:  # noqa: BLE001
            bad += 1; print("  FAIL", t.__name__, "->", repr(e))
    print("{}/{} passed".format(len(ts) - bad, len(ts)))
    return bad

if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/rhino_modeling/rhino-defect-synth && python3 tools/spalling/test_tinted_gravel.py`
Expected: FAIL (no `ratio_tint`).

- [ ] **Step 3: Implement**

```python
# tools/spalling/tinted_gravel.py
"""Offline: tint a gravel PBR albedo toward each host colour (keeping its normal/roughness
relief) so spall cavities read as host-toned aggregate, not a flat patch."""
import os
import shutil

import numpy as np
from PIL import Image


def ratio_tint(base_rgb, target_rgb):
    """Recolour an HxWx3 uint8 albedo so its MEAN becomes target_rgb while preserving the
    relative per-pixel variation (aggregate detail): out = base * (target / base_mean)."""
    arr = np.asarray(base_rgb, dtype=np.float64)
    mean = arr.reshape(-1, 3).mean(axis=0)
    mean = np.where(mean < 1.0, 1.0, mean)
    scale = np.asarray(target_rgb, dtype=np.float64) / mean
    out = np.clip(arr * scale, 0.0, 255.0)
    return out.astype(np.uint8)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 tools/spalling/test_tinted_gravel.py`
Expected: `1/1 passed`

- [ ] **Step 5: Commit**

```bash
git add tools/spalling/tinted_gravel.py tools/spalling/test_tinted_gravel.py
git commit -m "feat(spalling): ratio-scale albedo tint (mean->host, keep aggregate variance)"
```

### Task B2: generator CLI + generate real sets

**Files:**
- Modify: `tools/spalling/tinted_gravel.py` (add generator + CLI)
- Create (generated): `Textures/spall_host_proc/spallhost_<host>_*` sets

- [ ] **Step 1: Add the generator (reuses host colours + the gravel base PBR companions)**

```python
_PBR_SUFFIXES = ("_BaseColor", "_Color", "_Normal", "_Roughness", "_AO", "_Height", "_Metallic")

def _find_companions(gravel_base, texture_root):
    """Return {suffix: path} for the gravel base's PBR maps found under texture_root."""
    found = {}
    for root, _dirs, files in os.walk(texture_root):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            for suf in _PBR_SUFFIXES:
                if stem == gravel_base + suf:
                    found.setdefault(suf, os.path.join(root, f))
    return found

def generate_tinted_sets(host_colors, gravel_base, texture_root, out_dir):
    """For each host -> first colour variant, write a tinted-gravel PBR set
    spallhost_<host>_<suffix>. Albedo is ratio-tinted; all other maps are copied verbatim."""
    comp = _find_companions(gravel_base, texture_root)
    albedo_suf = "_BaseColor" if "_BaseColor" in comp else ("_Color" if "_Color" in comp else None)
    if not albedo_suf:
        raise SystemExit("gravel base {} has no albedo map under {}".format(gravel_base, texture_root))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    base_albedo = np.asarray(Image.open(comp[albedo_suf]).convert("RGB"), dtype=np.uint8)
    written = 0
    for host, variants in host_colors.items():
        target = tuple(int(c) for c in variants[0])
        tinted = ratio_tint(base_albedo, target)
        stem = "spallhost_{}".format(host)
        Image.fromarray(tinted, "RGB").save(os.path.join(out_dir, stem + "_BaseColor.png"))
        for suf, path in comp.items():
            if suf in ("_BaseColor", "_Color"):
                continue
            shutil.copyfile(path, os.path.join(out_dir, stem + suf + os.path.splitext(path)[1]))
        written += 1
    return written

def main(argv=None):
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--color-table", default="configs/spalling_host_colors.json")
    ap.add_argument("--gravel-base", default="Gravel02_2K")
    ap.add_argument("--texture-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    host_colors = json.load(open(a.color_table))
    n = generate_tinted_sets(host_colors, a.gravel_base, a.texture_root, a.out_dir)
    print("wrote {} tinted-gravel sets to {}".format(n, a.out_dir))

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add a file-output test**

```python
# append to tools/spalling/test_tinted_gravel.py (before _run)
from tinted_gravel import generate_tinted_sets

def test_generate_sets_writes_albedo_and_copies_normal():
    with tempfile.TemporaryDirectory() as tex, tempfile.TemporaryDirectory() as out:
        Image.fromarray(np.full((8, 8, 3), [130, 120, 110], np.uint8), "RGB").save(
            os.path.join(tex, "Gravel02_2K_BaseColor.png"))
        Image.fromarray(np.full((8, 8, 3), [128, 128, 255], np.uint8), "RGB").save(
            os.path.join(tex, "Gravel02_2K_Normal.png"))
        n = generate_tinted_sets({"HostA": [[60, 90, 140]]}, "Gravel02_2K", tex, out)
        assert n == 1
        assert os.path.isfile(os.path.join(out, "spallhost_HostA_BaseColor.png"))
        nrm = np.asarray(Image.open(os.path.join(out, "spallhost_HostA_Normal.png")).convert("RGB"))
        assert (nrm == [128, 128, 255]).all()   # normal copied verbatim (relief preserved)
```

- [ ] **Step 3: Run tests**

Run: `python3 tools/spalling/test_tinted_gravel.py`
Expected: `2/2 passed`

- [ ] **Step 4: Generate the real sets (WSL)**

Run: `python3 tools/spalling/tinted_gravel.py --texture-root "/mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/" --out-dir "/mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/spall_host_proc" --gravel-base Gravel02_2K`
Expected: `wrote 48 tinted-gravel sets to .../spall_host_proc`

- [ ] **Step 5: Spot-check one set's tone**

Run: `python3 -c "import numpy as np; from PIL import Image; import glob,os; d='/mnt/c/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/spall_host_proc'; f=sorted(glob.glob(d+'/*_BaseColor.png'))[0]; a=np.asarray(Image.open(f).convert('RGB')).reshape(-1,3); print(os.path.basename(f),'mean',a.mean(0).round(),'std',a.std(0).round())"`
Expected: mean = a concrete-ish tone, std > 0 (texture variance retained).

- [ ] **Step 6: Commit (tool only; generated textures live outside the repo tree under Textures/)**

```bash
git add tools/spalling/tinted_gravel.py tools/spalling/test_tinted_gravel.py
git commit -m "feat(spalling): tinted-gravel generator CLI + generated sets"
```

### Task B3: runtime — assign tinted-gravel material per host

**Files:**
- Modify: `utils_loc/spalling_host_color.py`

RhinoCommon-dependent; static-check here, verify in Task C.

- [ ] **Step 1: Replace the colour-material factory with a tinted-gravel material factory**

Replace `get_or_create_color_material(rgb, roughness)` with:

```python
import os
from utils_loc.materials import build_material_from_texture_bitmaps, find_texture_bitmaps, add_material_to_render_table

def get_or_create_host_material(host_name, proc_dir):
    """Build/import the host's tinted-gravel material (textured: albedo+normal+roughness) and
    return its basic-material index. Shared per host per document; -1 on failure/missing."""
    key = ("host", str(host_name))
    if key in _MATERIAL_CACHE:
        return _MATERIAL_CACHE[key]
    base_color = os.path.join(proc_dir, "spallhost_{}_BaseColor.png".format(host_name))
    if not os.path.isfile(base_color):
        return -1
    name = "spall_host_{}".format(host_name)
    idx = sc.doc.Materials.Find(name, True)
    if idx < 0:
        try:
            bitmaps = find_texture_bitmaps(base_color)
            mat, _status = build_material_from_texture_bitmaps(bitmaps, name)
            add_material_to_render_table(mat, material_name=name, make_unique=True,
                                         texture_bitmaps=bitmaps, channel_status=_status)
            idx = sc.doc.Materials.Find(name, True)
        except Exception as exc:  # noqa: BLE001
            print("spalling host-color: tinted-gravel material failed for {} ({})".format(name, exc))
            return -1
    _MATERIAL_CACHE[key] = idx
    return idx
```

(Delete `get_or_create_color_material` and `_color_material_name`; keep `_MATERIAL_CACHE`,
`reset_material_cache`, `_assign_object_material`, `_load_defect_records`.)

- [ ] **Step 2: Update `apply_spalling_host_color` to use host material + proc_dir**

Replace the colour-lookup/creation block inside the record loop:

```python
    proc_dir = cfg.get("proc_texture_dir", "")
    ...
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
```

(Drop `table = load_color_table(...)`, `roughness`, `rng` colour pick — no longer used; the
`rng` param stays in the signature for compatibility but is unused.)

- [ ] **Step 3: Static-check**

Run: `python3 -m py_compile utils_loc/spalling_host_color.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add utils_loc/spalling_host_color.py
git commit -m "feat(spalling): assign host-tinted gravel material to spall cavities"
```

### Task B4: config

**Files:**
- Modify: `configs/component.local.yaml` (`preparation.spalling_host_color`)

- [ ] **Step 1: Add proc dir + gravel base**

```yaml
  spalling_host_color:
    enabled: true
    proc_texture_dir: "C:/Users/shh/Documents/ShunHsiangHsu/DefectSynthetic/Textures/spall_host_proc"
    gravel_base: "Gravel02_2K"
    color_table_path: "configs/spalling_host_colors.json"
```

- [ ] **Step 2: Validate YAML**

Run: `python3 -c "import yaml; print(yaml.safe_load(open('configs/component.local.yaml'))['preparation']['spalling_host_color'])"`
Expected: dict with `enabled: True` and `proc_texture_dir`.

- [ ] **Step 3: Commit** (config is gitignored; on-disk only — no commit needed, just confirm it loads)

---

## Task C: Rhino verification (user, fresh session)

After a FRESH Rhino restart, short component run (max_iter ~5):

- [ ] Camera: defect close-ups are visibly oblique (cavity walls / crack groove sides show);
      per-model viewpoints differ; a minority are near head-on; no blank/occluded junk frames
      (occlusion cull + size-filter still working).
- [ ] Material: `spalling host-color: N recoloured ...` with N>0; spall cavities show aggregate
      texture + relief tinted to the host tone (not flat, not clashing); masks still
      Gold/DarkOrange; `basic_materials` bounded.
- [ ] Revert: remove `oblique_angle_range` → head-on; `spalling_host_color.enabled: false` →
      old look.

---

## Self-Review notes

- **Spec coverage:** Part A oblique sampler (A1) + wiring/config (A2,A3) = spec Part A. Part B
  tint (B1) + generator (B2) + runtime tinted-gravel material (B3) + config (B4) = spec Part B.
  Verification (C) = spec testing section. All covered.
- **Type consistency:** `sample_view_direction(normal, oblique_range, head_on_fraction, jitter_deg, rng)`
  used identically in A1/A2; `get_or_create_host_material(host_name, proc_dir)` defined in B3 and
  called in B3 Step 2; `_MATERIAL_CACHE`/`reset_material_cache`/`_assign_object_material`/
  `_load_defect_records` retained from the existing module.
- **Risk:** B3 (textured material per-object via basic index) is the part most likely to need a
  RhinoCommon tweak on first run (flat-colour worked; textures add the importer path) — Task C
  covers it. `add_material_to_render_table` is reused so it respects `material_reuse` naming.
