# Overnight findings & plan — 2026-06-19

Branch: `overnight/leak-fix-and-realism`. Nothing merged to `main`; all changes are
reviewable. This addresses the four problems raised: (1) batch memory leak,
(2) realism of cube/component, (3) exposed-rebar labeling + efflorescence material,
(4) public datasets for spalling/efflorescence shapes.

Read this first, then the two research reports under `docs/research/`.

---

## 1. Memory leak — ROOT CAUSE CONFIRMED (from your own logs)

Evidence: `rendered/runs/component/20260323_020006/batch_log.txt`.

| snapshot | objects | layers | basic_materials | private_mem | wall-clock |
|---|---|---|---|---|---|
| before iter 0 | 0 | 6 | 0 | 0.97 GB | — |
| after iter 0 | 3,635 | 47 | 34 | **13.9 GB** | ~51 min |
| after iter 1 | 4,354 | 87 | 68 | **19.8 GB** | ~101 min |
| after iter 2 | 4,601 | 127 | 102 | **24.2 GB** | ~151 min |
| after iter 3 | 4,495 | 167 | 136 | **30.2 GB** | ~208 min |

Findings:
- **Geometry reset works** — `objects` stays bounded. Not the leak.
- **`basic_materials` leaks +34/iter, never cleaned.** `Material cleanup` shows
  `remaining render` always 0 (render materials *are* removed) but `remaining basic`
  climbs 17→34→51→68→…→153. Cleanup removes RenderMaterials but **not their backing
  basic `Materials`**. Root: `_remove_table_item_at` (utils_loc/materials.py:1330)
  accepts a bare `True` as success even when the table count does not drop.
- **`layers` leaks +40/iter** (dynamic debug/component sublayers, component_modeling.py:141,
  defect_placement.py:846). Real but tiny (KB).
- **THE KILLER: native memory +~5 GB/model-iter, never released.** `prepare()` re-imports
  ~17 fresh 2K–4K PBR materials per pass and `clear_imported_materials_from_doc()` only
  deletes *table entries* — it never releases the decoded bitmaps in Rhino's render/texture
  cache. The pool is 167 unique materials; this grows until OOM. Wall-clock grows in lockstep.

**Key point:** the table leaks (layers, basic materials) are kilobytes — they cannot explain
+5 GB/iter. Fixing only the cleanup will NOT save the overnight run. The native texture memory
is the one to kill.

### Applied tonight (safe only)
- **Instrumentation** (utils_loc/batch_utils.py): `log_runtime_snapshot` now also logs
  `bitmaps=`, `instance_defs=`, `groups=`, `named_views=`, and a memory split
  `managed_mem_mb` / `private_mem_mb` / `working_set_mb`. Managed-flat + native-climbing ⇒
  native leak (expected). This is additive, no behavior change.
- **Graceful-stop guard** (configs/{component,cube}.local.yaml): `max_private_memory_mb: 24000`.
  The batch now self-stops with a clean resume point before OOM instead of crashing/thrashing.
  **Tune to ~80–85% of your physical RAM.**

### NOT done tonight (needs your Rhino to verify — too risky to ship blind)
I deliberately did **not** refactor material handling or add a process-restart wrapper
unattended: a subtle bug there silently ruins an overnight batch, and I cannot run Rhino on
WSL to verify. The fix plan, in priority order, to do together:

1. **Confirm import-vs-render split** with the fast probe (a loop of `prepare()`+clear with no
   modeling/rendering, logging the new fields). Minutes, not hours. Decides 2 vs 3 below.
2. **Material cache + never-clear** (likely the real fix): import each unique material ONCE,
   reuse by stable name across iterations (no `make_unique`, no per-iter clear). Native memory
   then plateaus at the union actually used instead of leaking every iter.
3. **Texture resolution cap** on import (4K→1K/2K). 4K decoded ≈ 16× the memory of 1K;
   training renders are 1920×1080 so 4K source is overkill. This multiplies #2's headroom.
4. **Process-restart wrapper** as the bulletproof overnight net: run the guard so the batch
   self-stops, then an outer script relaunches Rhino from the `batch_state.json` resume point
   (frees ALL native memory). Needs a deterministic per-iter seed scheme for the component
   sampler so resume doesn't re-randomize — a small design item.
5. Fix `_remove_table_item_at` to only count success when the table count drops (kills the
   +34/iter basic-material leak), and purge dynamic layers at reset (kills +40/iter).

Recommended order: **1 → 2 (+3) → verify → 4 as the net.** #2+#3 likely make overnight runs
viable on their own; #4 guarantees it.

---

## 2. Realism

### 2a. Cube cracks (2D→3D)
Cracks are extruded **10–30 mm deep** (component_defect_defaults.yaml: `delta_depth_range:
[1.0,3.0]` cm; cube similar). Real cracks are hairline (sub-mm). The deep cut renders as a dark
gouge that doesn't read as a crack, so the geometry adds little over the RGB mask — matching
your observation. Recommendation:
- Stop deep-extruding cracks. Render them as a **shallow surface feature**: a thin V-groove
  (~0.5–2 mm) or, better, a **bump/normal + slight displacement + albedo-darkening along the
  crack polyline** on an otherwise intact face. This gives realistic crack shading and correct
  depth/normal GT without the dark-hole artifact. The mask stays exact (rasterised polyline).
- Re-frame the cube pipeline's value as what 3D uniquely provides: lit/shaded multi-view
  renders, scale/perspective diversity, and accurate depth/normal GT — with cracks as surface
  shading, not deep geometry. (Proper spec to follow once you pick this up.)

### 2b. Component not training
Labeling was already fixed (see §3), so that wasn't the cause of the Mar-23 run. Likely real
causes, in order of suspicion:
1. **Defect scale-in-frame.** `camera.component.distance_ranges` renders efflore/spalling at
   **150–500 cm** and `defect_types: ["__none__"]` avoids close-ups → defects may be too small
   in 1920×1080 frames to learn. **Check rendered crops; tighten distances or add defect-focused
   views.** Cheap, high-impact.
2. **Efflorescence material/label mismatch** — fixed by the new generator (§3).
3. Procedural spalling/rebar realism (secondary).
4. Domain gap — add a small REAL validation set (dacl10k/S2DS, §4) to measure sim-to-real.

---

## 3. Exposed-rebar labeling + efflorescence material

### Rebar labeling — ALREADY FIXED (commit c405566, Mar 15)
Spalling-of-rebar now lands on `defect::spalling::CSN` (plain spalling class); only the rebar
metal goes to `defect::exposed_rebar::CSN::rebar`. So spalling looks/labels like spalling, and
exposed_rebar = metal only — exactly what you asked for. The Mar-23 failing run already had
this fix, so labeling was not its cause.
- **Cleanup TODO (minor):** the `defect::exposed_rebar::CS2/3::spalling` entries in
  configs/component_layer_color.json and component_defect_defaults.yaml are now **dead/vestigial**.
  Remove them so the training class taxonomy isn't ambiguous. Confirm your training class-map
  consumes the current 6-ish classes (crack CS1/2/3, spalling CS2/3, efflore CS2/CS3 in+out,
  exposed_rebar rebar) and does not still list the spalling-of-rebar classes.

### Efflorescence material — NEW procedural generator (built + tested tonight)
Your read is right: efflorescence is "base material + a thin white coating," and pasting cropped
photos gives incomplete labels. New tool: **`tools/efflorescence/efflore_synth.py`** (pure
numpy/scipy/Pillow, runs offline). It synthesises the deposit as fBm cloud × Worley crystalline
grain, biased/feathered, blended over a concrete base — and **the coverage mask IS the label**
(exact by construction; verified: target coverage 0.25/0.52/0.70 → actual 0.25/0.52/0.70).
Outputs albedo + alpha + label + roughness PNGs. See `tools/efflorescence/samples/_contact_sheet.png`.

Rhino integration (design — see efflorescence research report): feed the generated albedo+alpha
either as (a) a **Blend material** amount (concrete ↔ white-deposit PBR), (b) a white PBR with the
alpha in its **Opacity** channel layered over concrete, or (c) a **Decal** with the alpha as
transparency. Reuse the **same alpha PNG as the mask label** so render and label always align —
replacing the current photo-paste + `mask_ids=[]` path (defect_placement_efflore.py:226).
Tuning notes: keep `grain_strength` moderate (pure F2-F1 veining looks Voronoi-artificial at high
strength); composite over real CC0 concrete base albedo (ambientCG/Poly Haven) for realism.

---

## 4. Public datasets for spalling/efflorescence shapes
Full report: `docs/research/2026-06-19-defect-shape-datasets.md`. Headlines (verified):
- **"DCAL10kv3" → `dacl10k`** (Univ. Bundeswehr München). Polygon annotations, ~9.9k images,
  13 damage classes incl. **spalling AND efflorescence**. CC BY-NC 4.0. **Best shape source for
  both classes.** (Don't confuse with the smaller `dacl1k` test set.)
- **S2DS** confirmed: pixel-wise masks, 743 imgs, has spalling + efflorescence, GPL-3.0
  academic-only. Clean secondary/validation source.
- CODEBRIM/MCDS = bbox/classification only (not shape sources). Crack-only sets (SDNET2018,
  CrackForest) irrelevant for spalling/efflore.
- **Licensing:** both viable sources are non-commercial/academic only. Fine for research with
  attribution; not for a commercial product without separate licensing.
- Next step (design): a small ingestion script to convert dacl10k/S2DS polygons/masks into the
  pipeline's shape-candidate JSON format (the spalling/efflore `overview_csv` + polygon JSONs).

---

## Decisions I need from you (morning)
1. Memory: proceed with fix plan #1→#2(+#3) then #4 net? Any RAM ceiling to set the guard to?
2. Cube cracks: switch to shallow surface-feature rendering (drop deep extrusion)?
3. Efflorescence: adopt the procedural generator and wire it into the component pipeline?
4. Datasets: want the dacl10k/S2DS → shape-candidate ingestion script? (non-commercial use ok?)
5. Component cameras: investigate defect scale-in-frame (distances 150–500 cm)?
