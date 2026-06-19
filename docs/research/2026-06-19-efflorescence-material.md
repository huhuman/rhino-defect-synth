# Rendering realistic efflorescence procedurally (fact-checked 2026-06-19)

Implemented by `tools/efflorescence/efflore_synth.py`.

## TL;DR
Stop pasting cropped photos. Model efflorescence as what it is: **a thin, high-roughness,
near-white salt deposit layered over the base concrete, modulated by a procedural coverage mask
that is thickest at moisture-exit points and feathers at the edges.** Drive coverage with one
grayscale mask (fBm × granular/cellular noise × bias toward joints/cracks/low points). **That same
mask, thresholded, IS your segmentation label** — exact by construction. Rhino can do a usable
version natively (Blend + Noise/fBm/Marble); for crystalline grain and pixel-exact labels,
generate albedo+alpha maps offline (numpy/PIL) and feed them as bitmaps.

## (a) Physical & visual
- **What:** water-soluble salts migrate to a porous surface and crystallise as water evaporates;
  on Portland cement mostly **CaCO3** (from Ca(OH)2 + atmospheric CO2). [Concrete Society; Wikipedia]
- **Color:** white → off-white → light gray (most common). Vanadium=greenish, manganese=brown.
  [Construction Specifier; Sherwin-Williams]
- **Texture/opacity:** fresh bloom = thin, powdery, substrate reads through; hardened lime crust =
  crystalline, opaque, can streak ("lime run") down from cracks/joints. [Construction Specifier;
  The Concrete Society]
- **Distribution:** tracks moisture/evaporation paths — bands along joints, concentration at
  cracks/penetrations, drips down faces, patches at wall bases. [Sherwin-Williams; Concrete Society]
- Caveat: "feathering at edges" is a sound inference from the evaporation-front mechanism, not a
  quoted construction term.

## (b) Procedural recipe (one mask → render + label)
Layer a "deposit" material over the base via a single grayscale mask (0=bare, 1=full); let the
mask drive albedo, roughness, and bump together.
```
fbm        = fractal Brownian motion (~5 octaves, lacunarity 2, gain 0.5)   # cloudy patches
granular   = Worley/cellular F2-F1                                          # crystalline grain
field      = normalize(fbm) * lerp(1, granular, grain_strength)
bias       = AO/pointiness * joint_proximity * (1 - height)                 # accumulate in joints/low
streak     = vertically-stretched noise gated downward from cracks          # lime-run drips
field      = field * bias + streak
mask       = smoothstep(thr - w, thr + w, field)                            # feathered 0..1 coverage
label      = mask >= 0.5                                                     # exact hard label
albedo     = lerp(concrete, deposit_white, mask); roughness = lerp(rough_c, 0.9, mask)
```
**Why mask = label:** the deposit is placed by a known procedural mask, so thresholding it yields a
pixel-perfect label — no detector, no human annotation, no photo-edge noise. (The synthetic-data
advantage over copy-paste; arXiv 2510.09110.)

## (c) Rhino-native vs offline
- **Native (Rhino 8):** PBR channels Base/Roughness/Metallic/**Opacity**/Bump/Displacement/etc.;
  procedurals Noise/Turbulence/**fBm**/Gradient/Marble/**Blend**/**Mask**/Add/Mul. Layer via
  **Blend material** (amount driven by a texture's luminance) or a semi-transparent PBR with alpha
  in **Opacity** over concrete, or a **Decal** with PNG alpha. Scriptable via
  `Rhino.Render.RenderMaterial.SetChild/SetParameter` (ref: github.com/mcneel/RhinoPbrMaterial).
- **Limitations:** no native Voronoi/cellular noise; native procedurals are low-level; no
  render-time alpha-holdout (use Object-ID passes). → **Generate albedo+alpha offline** (numpy/PIL),
  feed as bitmaps; the alpha PNG doubles as the label. Use `vnoise`/`opensimplex` (avoid the
  unbuildable `noise` pkg) or, as here, scipy (`zoom` value-noise fBm + `cKDTree` Worley).

## (d) Free CC0 base assets (no attribution, commercial OK)
- **Concrete (base):** ambientCG Concrete034/048/036; Poly Haven concrete_floor_02,
  concrete_wall_008, rough_concrete.
- **White deposit / lime-wash albedo:** ambientCG Plaster001, PaintedPlaster017/016; Poly Haven
  concrete_wall_001, concrete_wall_003 (yellow-white leaching), worn_cracked_plaster.
- No site has a literal "efflorescence" set → synthesise by overlaying whitish plaster albedo onto
  clean concrete via the procedural mask (stays CC0 + label-aligned).
- **Avoid textures.com** (not CC0; redistribution prohibited).
- Bulk APIs: ambientCG `api/v2/full_json?type=Material&category=Concrete`; Poly Haven
  `api.polyhaven.com/assets?t=textures&c=concrete`.

Licenses: ambientCG & Poly Haven = **CC0 1.0** (verified on their license pages).
