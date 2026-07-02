"""Procedural efflorescence (白華 / lime bloom) material + label generator.

Efflorescence is physically a thin, near-white, water-soluble-salt deposit (mostly
CaCO3) that migrates to a porous concrete surface and crystallises as water
evaporates. Visually it is *the base concrete with a thin whitish coating on top*:
the substrate texture still reads through a fresh powdery bloom, while a hardened
lime crust is more opaque and can streak ("lime run") down from cracks/joints.

This module synthesises that appearance procedurally instead of pasting cropped
photos, which is the key advantage for synthetic training data:

    The procedural COVERAGE MASK that places the deposit IS the ground-truth
    segmentation label -- exact by construction, no annotation/photo-edge noise.

Outputs per sample (all the same HxW):
    albedo    RGB uint8   base concrete blended toward white where deposit covers
    alpha     gray  uint8 continuous 0..255 deposit coverage (soft label / opacity)
    label     gray  uint8 binary 0/255 (alpha >= 0.5) -- the hard segmentation label
    roughness gray  uint8 base roughness raised toward chalky where deposit covers

Designed to run OFFLINE in CPython (numpy + scipy + Pillow). The generated
albedo + alpha PNGs are then consumed inside Rhino as a Blend-material amount, a
PBR Opacity channel, or a Decal (see the integration design doc). The alpha PNG is
reused directly as the mask label, guaranteeing render/label alignment.

Refs: see docs/superpowers/specs/2026-06-19-efflorescence-material-design.md
"""

from __future__ import annotations

import json
import os

import numpy as np
from scipy.ndimage import gaussian_filter, zoom
from scipy.spatial import cKDTree

# Off-white deposit defaults. Efflorescence is white -> off-white -> light gray;
# vanadium/manganese variants tint green/brown but white is by far most common.
DEPOSIT_WHITE = (236, 234, 228)
BASE_CONCRETE_GRAY = (176, 173, 168)


# ---------------------------------------------------------------------------
# Noise primitives (pure numpy/scipy; robust for any output shape)
# ---------------------------------------------------------------------------

def _upsample(low, shape):
    """Smoothly upsample a small grid to exactly `shape` (bicubic)."""
    zy = shape[0] / float(low.shape[0])
    zx = shape[1] / float(low.shape[1])
    hi = zoom(low, (zy, zx), order=3, mode="reflect")
    hi = hi[: shape[0], : shape[1]]
    if hi.shape != tuple(shape):  # pad if zoom under-shot by a pixel
        pad = ((0, max(0, shape[0] - hi.shape[0])), (0, max(0, shape[1] - hi.shape[1])))
        hi = np.pad(hi, pad, mode="edge")[: shape[0], : shape[1]]
    return hi.astype(np.float32)


def fbm(shape, rng, base_cells=4, octaves=5, lacunarity=2.0, gain=0.5):
    """Fractal Brownian motion: sum of smooth value-noise octaves.

    Gives the cloudy, multi-scale spread of moisture permeating a wall. Returns a
    field normalised to ~[0, 1].
    """
    field = np.zeros(shape, np.float32)
    amp, cells, total = 1.0, int(base_cells), 0.0
    for _ in range(int(octaves)):
        low = rng.random((cells + 1, cells + 1)).astype(np.float32)
        field += amp * _upsample(low, shape)
        total += amp
        amp *= gain
        cells = max(1, int(round(cells * lacunarity)))
    field /= max(total, 1e-6)
    return _normalize(field)


def worley(shape, rng, n_points=64, kind="f2-f1"):
    """Cellular (Worley) noise -> crystalline/granular salt-grain structure.

    kind="f2-f1" gives bright cell edges (veined crystalline look);
    kind="f1" gives blobby grains (use 1-f1 for deposits sitting in cell centres).
    """
    n_points = max(4, int(n_points))
    pts = rng.random((n_points, 2)) * np.array([shape[0], shape[1]], np.float32)
    tree = cKDTree(pts)
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    coords = np.column_stack([ys.ravel(), xs.ravel()])
    dist, _ = tree.query(coords, k=2, workers=-1)
    f1 = dist[:, 0].reshape(shape)
    f2 = dist[:, 1].reshape(shape)
    w = f1 if kind == "f1" else (f2 - f1)
    return _normalize(w.astype(np.float32))


def _normalize(a):
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-9:
        return np.zeros_like(a, np.float32)
    return ((a - lo) / (hi - lo)).astype(np.float32)


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _drip_field(shape, rng, strength=0.4):
    """Vertically streaked field for 'lime run' drips from accumulation points."""
    # Few horizontal cells, many vertical cells -> tall narrow vertical streaks.
    low = rng.random((max(8, shape[0] // 12), max(3, shape[1] // 48))).astype(np.float32)
    streaks = _normalize(_upsample(low, shape))
    streaks = _normalize(gaussian_filter(streaks, sigma=(0.5, 2.0)))
    # Bias streaks to emanate downward (stronger lower on the surface).
    grad = np.linspace(0.2, 1.0, shape[0], dtype=np.float32)[:, None]
    return _normalize(streaks * grad) * float(strength)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_efflorescence(
    size=(512, 512),
    seed=0,
    base_albedo=None,
    coverage=0.45,
    feather=0.10,
    grain_strength=0.45,
    worley_points=80,
    worley_kind="f2-f1",
    drips=True,
    drip_strength=0.45,
    bias_map=None,
    deposit_color=DEPOSIT_WHITE,
    base_color=BASE_CONCRETE_GRAY,
    hardness=0.5,
    octaves=5,
):
    """Generate one efflorescence sample.

    Args:
        size: (H, W).
        seed: RNG seed (vary per sample for diversity).
        base_albedo: optional HxWx3 uint8 base concrete; flat noisy gray if None.
        coverage: target fraction of pixels covered by deposit (sets the threshold).
        feather: smoothstep half-width for the soft (anti-aliased) deposit edge.
        grain_strength: 0..1 amount of crystalline Worley grain mixed into the fBm.
        drips: add downward lime-run streaks.
        drip_strength: 0..1 strength of the drip contribution.
        bias_map: optional HxW in [0,1] to bias placement (joints/cracks/low areas);
                  multiply higher where deposit should accumulate.
        hardness: 0=fresh powder (substrate reads through, very matte),
                  1=hard lime crust (more opaque, slightly less rough).

    Returns dict with float `coverage` map plus uint8 albedo/alpha/label/roughness.
    """
    h, w = int(size[0]), int(size[1])
    shape = (h, w)
    rng = np.random.default_rng(int(seed))

    # --- coverage field: fBm cloud modulated by crystalline grain ----------
    cloud = fbm(shape, rng, base_cells=4, octaves=octaves)
    grain = worley(shape, rng, n_points=worley_points, kind=worley_kind)
    field = cloud * ((1.0 - grain_strength) + grain_strength * grain)

    if bias_map is not None:
        b = np.asarray(bias_map, np.float32)
        if b.shape != shape:
            b = _upsample(b, shape)
        field = field * (0.4 + 0.6 * _normalize(b))

    field = _normalize(field)

    if drips:
        drip = _drip_field(shape, rng, strength=drip_strength)
        # Drips only originate where there is already some deposit cloud.
        field = _normalize(np.maximum(field, drip * (field > np.quantile(field, 0.5))))

    # --- threshold by target coverage, feathered edge ----------------------
    coverage = float(np.clip(coverage, 0.02, 0.98))
    thr = float(np.quantile(field, 1.0 - coverage))
    alpha = _smoothstep(thr - feather, thr + feather, field)

    # Deposit reads as a near-opaque white coating (was 0.55-0.95, which left it gray/
    # translucent over the concrete in renders). Keep a little softness so it isn't flat
    # paint, but high enough that the white actually reads (issue 2026-06-22).
    max_opacity = 0.85 + 0.15 * float(np.clip(hardness, 0.0, 1.0))
    alpha = alpha * max_opacity

    label = (alpha >= 0.5 * max_opacity).astype(np.uint8) * 255

    # --- base concrete -----------------------------------------------------
    if base_albedo is None:
        base = _synth_base(shape, rng, base_color)
    else:
        base = np.asarray(base_albedo, np.float32)
        if base.shape[:2] != shape:
            base = np.stack([_upsample(base[..., c], shape) for c in range(3)], axis=-1)

    # --- deposit colour: near-white with subtle per-pixel variation --------
    deposit = np.array(deposit_color, np.float32)[None, None, :]
    variation = (0.90 + 0.10 * fbm(shape, rng, base_cells=12, octaves=3))[..., None]
    deposit = np.clip(deposit * variation, 0, 255)

    a3 = alpha[..., None]
    albedo = np.clip(base * (1.0 - a3) + deposit * a3, 0, 255).astype(np.uint8)

    # --- roughness: chalky where covered -----------------------------------
    base_rough = 0.62
    deposit_rough = 0.95 - 0.25 * float(np.clip(hardness, 0.0, 1.0))  # crust glossier
    roughness = (base_rough * (1.0 - alpha) + deposit_rough * alpha)
    roughness_u8 = np.clip(roughness * 255.0, 0, 255).astype(np.uint8)

    return {
        "coverage": alpha.astype(np.float32),
        "albedo": albedo,
        "alpha": np.clip(alpha / max(max_opacity, 1e-6) * 255.0, 0, 255).astype(np.uint8),
        "label": label,
        "roughness": roughness_u8,
        "meta": {
            "seed": int(seed),
            "size": [h, w],
            "coverage_target": coverage,
            "coverage_actual": float((label > 0).mean()),
            "feather": feather,
            "grain_strength": grain_strength,
            "drip_strength": drip_strength if drips else 0.0,
            "hardness": hardness,
            "max_opacity": max_opacity,
        },
    }


def _synth_base(shape, rng, base_color):
    """Cheap synthetic concrete base when no real albedo is provided."""
    base = np.array(base_color, np.float32)[None, None, :]
    mottle = (0.85 + 0.15 * fbm(shape, rng, base_cells=8, octaves=4))[..., None]
    speck = (0.96 + 0.08 * worley(shape, rng, n_points=200, kind="f1"))[..., None]
    return np.clip(base * mottle * speck, 0, 255)


# ---------------------------------------------------------------------------
# IO helpers + CLI
# ---------------------------------------------------------------------------

def save_sample(sample, out_dir, stem):
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    Image.fromarray(sample["albedo"], "RGB").save(os.path.join(out_dir, stem + "_albedo.png"))
    Image.fromarray(sample["alpha"], "L").save(os.path.join(out_dir, stem + "_alpha.png"))
    Image.fromarray(sample["label"], "L").save(os.path.join(out_dir, stem + "_label.png"))
    Image.fromarray(sample["roughness"], "L").save(os.path.join(out_dir, stem + "_roughness.png"))
    with open(os.path.join(out_dir, stem + "_meta.json"), "w") as fh:
        json.dump(sample["meta"], fh, indent=2)


def contact_sheet(samples, cols=3, pad=6):
    """Build a preview grid: each cell shows albedo with its label outline below."""
    from PIL import Image

    h, w = samples[0]["albedo"].shape[:2]
    rows = (len(samples) + cols - 1) // cols
    sheet = np.full((rows * (h + pad) + pad, cols * (w + pad) + pad, 3), 30, np.uint8)
    for i, s in enumerate(samples):
        r, c = divmod(i, cols)
        y, x = pad + r * (h + pad), pad + c * (w + pad)
        sheet[y : y + h, x : x + w] = s["albedo"]
    return Image.fromarray(sheet, "RGB")


def build_pipeline_library(out_dir, count=24, size=(1024, 1024), seed=0, prefix="efflore"):
    """Emit texture sets named for the pipeline's suffix convention (texture_naming.MAP_SUFFIXES)
    so the EXISTING texture-seed importer auto-wires albedo + opacity + roughness with no code change.

    Per sample writes: <prefix>_NNN_color.png (albedo), _opacity.png (deposit coverage -> opacity;
    transparent where bare concrete), _roughness.png, and _label.png (exact GT, for the Option-B
    alpha-accurate mask). The config material option to list for an efflore layer is the stem
    '<prefix>_NNN'. Run this ON the machine that has the Rhino texture_root, writing into it.
    """
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    names = []
    for i in range(count):
        s = generate_efflorescence(
            size=size,
            seed=seed + i,
            coverage=0.55 + 0.35 * ((i * 3 % 5) / 4.0),  # raised floor so deposit reads as white
            grain_strength=0.30 + 0.30 * ((i * 7 % 4) / 3.0),
            hardness=0.5 + 0.5 * ((i % 3) / 2.0),         # more opaque (chalky crust) on average
            drips=True,                                    # flow/drip morphology on every sample
        )
        stem = "%s_%03d" % (prefix, i)
        Image.fromarray(s["albedo"], "RGB").save(os.path.join(out_dir, stem + "_color.png"))
        Image.fromarray(s["alpha"], "L").save(os.path.join(out_dir, stem + "_opacity.png"))
        Image.fromarray(s["roughness"], "L").save(os.path.join(out_dir, stem + "_roughness.png"))
        Image.fromarray(s["label"], "L").save(os.path.join(out_dir, stem + "_label.png"))
        names.append(stem)
    print("library: wrote %d efflore texture sets to %s" % (count, out_dir))
    print("config material options (efflore layer lists) -> use these stems:")
    print(names)
    return names


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Generate procedural efflorescence samples.")
    ap.add_argument("--mode", choices=["samples", "library"], default="samples",
                    help="samples=preview contact sheet; library=pipeline-named texture sets")
    ap.add_argument("--out", default="tools/efflorescence/samples")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prefix", default="efflore")
    args = ap.parse_args()

    if args.mode == "library":
        build_pipeline_library(args.out, args.count, (args.size, args.size), args.seed, args.prefix)
        return

    samples = []
    for i in range(args.count):
        # Sweep parameters so the contact sheet shows the achievable range.
        s = generate_efflorescence(
            size=(args.size, args.size),
            seed=args.seed + i,
            coverage=0.25 + 0.45 * (i / max(args.count - 1, 1)),
            grain_strength=0.3 + 0.4 * ((i * 7 % 5) / 4.0),
            hardness=(i % 3) / 2.0,
            drips=(i % 2 == 0),
        )
        save_sample(s, args.out, "efflore_%03d" % i)
        samples.append(s)
        print(
            "efflore_%03d: coverage target=%.2f actual=%.2f hardness=%.1f"
            % (i, s["meta"]["coverage_target"], s["meta"]["coverage_actual"], s["meta"]["hardness"])
        )

    sheet = contact_sheet(samples)
    sheet_path = os.path.join(args.out, "_contact_sheet.png")
    sheet.save(sheet_path)
    print("wrote %d samples + contact sheet to %s" % (len(samples), args.out))


if __name__ == "__main__":
    main()
