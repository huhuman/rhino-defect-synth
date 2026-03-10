"""Shared texture naming rules for material map discovery."""

from __future__ import annotations

import re


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".tga", ".exr")
MAP_SUFFIXES = {
    "albedo": ("basecolor", "base_color", "albedo", "diffuse", "color", "col"),
    "normal": ("normal", "norm", "nrm", "nor", "normal_opengl", "normalgl"),
    "occlusion": ("ao", "oc", "ambientocclusion", "ambient_occlusion", "occlusion", "occ"),
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


def _token_to_tail_pattern(token: str) -> str | None:
    token = (token or "").strip().lower()
    if not token:
        return None
    parts = [part for part in re.split(r"[_\-\s]+", token) if part]
    if not parts:
        return None
    body = r"[_\-\s]*".join(re.escape(part) for part in parts)
    return r"(?:^|[_\-\s]){}$".format(body)


def stem_ends_with_suffix_token(stem: str, suffix: str) -> tuple[bool, int]:
    """Return (matched, start_idx) when stem ends with suffix token (case-insensitive)."""
    stem_lc = (stem or "").lower()
    if not stem_lc:
        return False, -1

    candidates = [suffix] + list(expand_token_variants(suffix))
    seen = set()
    for token in candidates:
        token_lc = (token or "").lower()
        if token_lc in seen:
            continue
        seen.add(token_lc)
        pattern = _token_to_tail_pattern(token_lc)
        if not pattern:
            continue
        match = re.search(pattern, stem_lc)
        if match:
            return True, match.start()
    return False, -1


def guess_map_kind_from_stem(stem: str) -> str | None:
    for kind, suffixes in MAP_SUFFIXES.items():
        for suffix in suffixes:
            matched, _ = stem_ends_with_suffix_token(stem, suffix)
            if matched:
                return kind
    return None


def strip_known_suffix(stem: str) -> str:
    for suffix in sorted(ALL_SUFFIXES, key=len, reverse=True):
        matched, start_idx = stem_ends_with_suffix_token(stem, suffix)
        if matched:
            return stem[:start_idx].rstrip("_- ")
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
