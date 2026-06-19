# Public datasets as spalling/efflorescence shape candidates (fact-checked 2026-06-19)

Goal: datasets with reusable per-instance segmentation masks / polygons for **spalling** and
**efflorescence** to drive 3D modeling. Polygons/masks >> bbox >> classification.

## Verification of the two you named
- **"DCAL10kv3" is not a real name** — almost certainly **`dacl10k`** (dacl-challenge, Univ.
  Bundeswehr München; WACV2024 workshop). The "10k" matches (~9,920 imgs); letters are a
  transposition of "dacl". Distractor: a separate smaller **`dacl1k`** real-world test set exists
  — don't confuse them.
- **S2DS = Structural Defects Dataset** (Benz & Rodehorst, Bauhaus-Univ. Weimar, GCPR 2022). Real.

## Comparison

| Dataset | Year | Classes | Annotation | #imgs | Spall? | Efflore? | License | URL |
|---|---|---|---|---|---|---|---|---|
| **dacl10k** | 2023–24 | 13 damage + 6 component (crack, spalling, efflorescence, rust, cavity, exposed rebar, …) | **Polygon** (labelme JSON → multilabel masks) | 9,920 | ✅ | ✅ | CC BY-NC 4.0 | dacl.ai/workshop.html · github.com/phiyodr/dacl10k-toolkit · arxiv.org/abs/2309.00460 |
| **S2DS** | 2022 | bg, crack, spalling, corrosion, efflorescence, vegetation, control point | **Semantic seg** (pixel masks) | 743 (1024²) | ✅ | ✅ | GPL-3.0, academic-only | github.com/ben-z-original/s2ds |
| CODEBRIM | 2019 | crack, spall, exposed bars, efflorescence, corrosion | **bbox + classification only** | 1,590 | ✅(bbox) | ✅(bbox) | non-commercial | zenodo.org/record/2620293 |
| MCDS | 2019 | cracks, efflorescence, scaling, spalling, exposed reinf, rust | **classification only** | 3,607 | ✅(label) | ✅(label) | non-commercial | — |
| GYU-DET | 2025 | cracks, spalling, seepage, honeycomb, exposed rebar, holes | **bbox (YOLO)** | 11,123 | ✅(bbox) | ❌ | CC BY-NC-ND 4.0 | nature.com/articles/s41597-025-05395-w |
| SDNET2018 | 2018 | cracked/non | classification | 56k+ | ❌ | ❌ | open | doi.org/10.15142/T3TD19 |
| CrackForest | 2016 | crack | pixel masks | 118 | ❌ | ❌ | open | — |

## Ranking as SHAPE sources
**Spalling:** 1) dacl10k (polygons, thousands of instances) — STRONG. 2) S2DS (clean pixel masks,
fewer instances) — STRONG. 3) GYU-DET (bbox only) — weak. CODEBRIM/MCDS — unusable for shape.

**Efflorescence (rare!):** 1) dacl10k — dedicated efflorescence polygon class, ~2.6% pixel
coverage; **the single best public source of efflorescence shapes.** 2) S2DS — efflorescence
semantic class, small dataset. 3) CODEBRIM — bbox only (833 instances), weak. **Nothing else has
efflorescence masks.**

## Licensing (the key constraint)
All viable mask sources are **non-commercial / academic-only**:
- **dacl10k → CC BY-NC 4.0** — research/non-commercial reuse with attribution OK; commercial
  prohibited.
- **S2DS → GPL-3.0, academic-only** — copyleft; treat as academic-only.
- GYU-DET → CC BY-NC-**ND** (NoDerivatives) — avoid for a derivative shape pipeline.

**Bottom line:** a spalling/efflorescence shape library from **dacl10k (primary) + S2DS
(secondary/validation)** is fine for non-commercial research with attribution. Commercial use
needs separate licensing or your own annotations.

## Confidence
High: class lists, annotation types, counts, licenses (each from ≥2 sources incl. official repos).
Inference (high conf): "DCAL10kv3" → dacl10k mapping — confirm you didn't mean `dacl1k`.
Unverified: "TokaikuCrack" (not found; crack-only regardless).
