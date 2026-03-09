"""Shared texture naming rules for material map discovery."""

from __future__ import annotations


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".tga", ".exr")
MAP_SUFFIXES = {
    "albedo": ("basecolor", "base_color", "albedo", "diffuse", "color", "col"),
    "normal": ("normal", "norm", "nrm", "nor", "normal_opengl", "normal_directx"),
    "occlusion": ("ao", "oc", "ambientocclusion", "ambient_occlusion", "occlusion"),
    "roughness": ("roughness", "rough", "rgh"),
    "metallic": ("metallic", "metalness", "metal"),
    "height": ("height", "displacement", "disp", "bump"),
    "opacity": ("opacity", "alpha", "mask", "transparency"),
    "specular": ("specular", "spec", "glossiness", "gloss"),
}
ALL_SUFFIXES = tuple(
    suffix for suffixes in MAP_SUFFIXES.values() for suffix in suffixes
)


def expand_token_variants(token: str) -> tuple[str, ...]:
    token = (token or "").lower()
    variants = {token}
    if "_" in token:
        variants.add(token.replace("_", "-"))
    if "-" in token:
        variants.add(token.replace("-", "_"))
    return tuple(sorted((v for v in variants if v), key=len, reverse=True))


def guess_map_kind_from_stem(stem: str) -> str | None:
    stem_lc = stem.lower()
    for kind, suffixes in MAP_SUFFIXES.items():
        for suffix in suffixes:
            if stem_lc.endswith("_" + suffix):
                return kind
    return None


def strip_known_suffix(stem: str) -> str:
    stem_lc = stem.lower()
    for suffix in sorted(ALL_SUFFIXES, key=len, reverse=True):
        token = "_" + suffix
        if stem_lc.endswith(token):
            return stem[: -len(token)]
    return stem


def stem_contains_color_token(stem: str) -> bool:
    if not stem:
        return False
    stem_lc = stem.lower()
    for color_token in MAP_SUFFIXES["albedo"]:
        for token_variant in expand_token_variants(color_token):
            if token_variant in stem_lc:
                return True
    return False
