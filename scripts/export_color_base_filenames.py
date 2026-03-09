#!/usr/bin/env python3
"""Export color-related base filenames from texture directories.

Rules are aligned with `utils_loc/materials.py`:
1) Prefer files whose stem contains any "albedo" token.
2) If none found in a directory, fall back to .jpg/.jpeg files.
3) Skip explicit non-albedo map types (normal/roughness/etc).
4) Deduplicate by stripped root stem (case-insensitive) per directory.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils_loc.texture_naming import (  # noqa: E402
    IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS,
    guess_map_kind_from_stem as _guess_map_kind_from_stem,
    stem_contains_color_token as _stem_contains_color_token,
    strip_known_suffix as _strip_known_suffix,
)


def _collect_from_single_dir(texture_dir: Path) -> list[str]:
    all_files = sorted(os.listdir(str(texture_dir)))
    color_candidates: list[str] = []

    for filename in all_files:
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        if _stem_contains_color_token(stem):
            color_candidates.append(filename)

    candidate_files = color_candidates
    if not candidate_files:
        candidate_files = [
            name
            for name in all_files
            if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg")
        ]

    results: list[str] = []
    processed_roots: set[str] = set()
    for filename in candidate_files:
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        kind = _guess_map_kind_from_stem(stem)
        if kind and kind != "albedo":
            continue

        root_stem = _strip_known_suffix(stem)
        dedup_key = root_stem.lower()
        if dedup_key in processed_roots:
            continue
        processed_roots.add(dedup_key)
        results.append(root_stem)

    return results


def _collect(texture_dir: Path, recursive: bool) -> list[str]:
    if not recursive:
        return _collect_from_single_dir(texture_dir)

    all_results: list[str] = []
    for root, _, files in os.walk(str(texture_dir)):
        if not any(os.path.splitext(name)[1].lower() in _IMAGE_EXTENSIONS for name in files):
            continue
        all_results.extend(_collect_from_single_dir(Path(root)))
    return all_results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export color-related base filenames using materials.py matching rules."
    )
    parser.add_argument("--texture-dir", required=True, help="Texture root directory to scan.")
    parser.add_argument("--output", required=True, help="Output .txt path.")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan subdirectories (same behavior as create_materials_from_texture_dir recursive mode).",
    )
    parser.add_argument(
        "--dedup-global",
        action="store_true",
        help="Deduplicate output across all scanned directories (case-insensitive).",
    )
    args = parser.parse_args()

    texture_dir = Path(args.texture_dir).expanduser().resolve()
    if not texture_dir.is_dir():
        raise SystemExit("Texture directory does not exist: {}".format(texture_dir))

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    names = _collect(texture_dir, recursive=args.recursive)
    if args.dedup_global:
        seen: set[str] = set()
        deduped: list[str] = []
        for name in names:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        names = deduped

    with output_path.open("w", encoding="utf-8", newline="\n") as fp:
        for name in names:
            fp.write(name + "\n")

    print("Scanned: {}".format(texture_dir))
    print("Recursive: {}".format(bool(args.recursive)))
    print("Exported {} base filename(s) to: {}".format(len(names), output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
