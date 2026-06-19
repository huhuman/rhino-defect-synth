# Data-gen speed plan (audit 2026-06-19)

From a 71-agent parallel audit (render/capture/materials/modeling/stability/throughput), each finding
adversarially verified against the code, plus a direct frame-redundancy measurement.

## Where the time goes (measured, run 20260619_172416, iter 0)
- Total 472 s. **`rendering[0,0]` = 413 s (≈87%)**; reset→modeling = 58 s.
- 49 frames/model × **6 channels** (color, depth PNG, normal PNG, depth_buffer PFM, normal_buffer PFM,
  mask) ≈ **8.4 s/frame**. So throughput ≈ frames/model × per-frame channel cost. Modeling is minor.

## Key measurement — frames are NOT redundant
Adjacent color-frame mean|Δ| (0–255) across the 49 frames: min 3.9, **median 12.0**, max 19.0;
**0/48 pairs below Δ<3**, 1/48 below 5. → these are genuinely distinct viewpoints, not video tweens.
**Cutting `transition_frames`/`sample_count` = real loss of viewpoint diversity**, which is the
opposite of the stated goal (max diverse angles/distances, as much data as possible). So the audit's
nominal "biggest single lever" (fewer frames) is **rejected for this project.**

## Priority for THIS goal (max diverse data, don't cut frames)

### 1. Multi-instance parallelism — the main lever (keeps all data)
Rhino Python is single-threaded, but run N Rhino processes over **disjoint model ranges** writing to
the same output dir with offset `output_index_start`. ~1.5–1.8× on one workstation (GPU/disk/display
pipeline saturate near N=2), **near-linear across separate machines** (render farm).
- Needs code: `main_component_batch.py:736-744` hardcodes config/max_iter/seed and has no partition.
  Add argv/env for (model_index_start, model_count, seed, output_index_start) so each instance does a
  disjoint slice. Verify Rhino concurrent-license.
- Effort: medium. Risk: low (pure orchestration). Biggest images/hour multiplier without touching data.

### 2. Channel pruning — decide with the training owner (data decision)
6 channels per frame. Per-frame cost roughly halves if the geometry channels are dropped.
- If training needs only **RGB + mask** → set `depth/normal/depth_buffer/normal_buffer: false`
  (configs/component.local.yaml:111-115) → ~2× per frame.
- If dense geometry IS needed → the **PFM buffers are the authoritative 32-bit source; the 8-bit PNG
  `depth`/`normal` are redundant lossy previews** → drop only those (`depth:false, normal:false`,
  keep PFM) → ~10–20% per frame, zero information loss.
- Config-only but drops artifacts → confirm the downstream loader first.

### 3. Material import-once / reuse (speed + memory)
Component re-imports ~17 materials EVERY render_iter (clear + re-`FromMaterial`); import each unique
material once and re-point `layer.RenderMaterial` instead. Modest per-render save now (downsampling
already cut the decode cost) and it also bounds the residual native leak. Effort: medium; needs Rhino test.

### 4. Trim safe stability sleeps (free, tiny)
`wait_after_render_ms 60→0` (post-capture, redraw=False — safest) and `wait_after_reset_ms 20→0`.
**Leave the redraw=True pre-capture waits and ALL GC knobs alone** (guard partial-redraw/capture
corruption and bound the residual native leak). ~80 ms/model, <1%. Hygiene only.
Also `clear_undo_every_*: 1→0` (no-ops — undo is globally disabled) and `gc_every_capture_frames 8→16`.

### 5. Mask plugin micro-opt (code, small)
`CaptureBaseColorMaskCommand.cs` duplicates render meshes + writes per-vertex false colors EVERY mask
frame. Use a single per-object color override instead of per-vertex writes, or cache the false-color
meshes across frames of one render pass (static geometry, camera-only motion; invalidate on
prepare()/create_model()). Mask is 1 of 6 channels and DrawToBitmap+PNG dominate it → realistic
~3–10% of the mask step (low single-digit % overall). C# recompile + in-Rhino test.

## Honest cumulative estimate (preserving frame diversity)
- Config-only keeping all frames: drop redundant PNG depth/normal previews + trim safe sleeps →
  ~1.1–1.3×. If training is RGB+mask-only (drop all 4 geometry channels) → ~2× (a data decision).
- + 2-instance parallelism → ×1.5–1.8 on one box; ×(machines) across a farm.
- **Realistic on one workstation, full data quality: ~2–3×** (channels-permitting + 2 instances).
  The big multipliers only come from accepting fewer frames or fewer channels — flagged, your call.

## Decisions needed
1. Which channels does training actually consume? (RGB+mask only → big win; else keep PFM, drop PNG previews.)
2. Parallelism: how many Rhino instances/machines available (concurrent license, GPUs)? → I implement the
   model-range partition + output-index offset.
