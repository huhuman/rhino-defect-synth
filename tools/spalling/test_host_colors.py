#!/usr/bin/env python3
"""Self-contained tests for host_colors (no pytest dependency).

Run: python3 tools/spalling/test_host_colors.py
"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from host_colors import (  # noqa: E402
    representative_color,
    make_variants,
    find_base_texture,
    build_color_table,
    host_material_names_from_config,
)


def test_representative_color_is_trimmed_mean():
    with tempfile.TemporaryDirectory() as d:
        arr = np.full((10, 10, 3), [128, 120, 110], dtype=np.uint8)
        arr[0, 0] = [255, 255, 255]
        arr[9, 9] = [0, 0, 0]
        p = os.path.join(d, "t.png")
        Image.fromarray(arr, "RGB").save(p)
        r, g, b = representative_color(p, trim_pct=10.0)
        assert abs(r - 128) <= 6 and abs(g - 120) <= 6 and abs(b - 110) <= 6, (r, g, b)


def test_make_variants_exact_darker_desaturated():
    base = (120, 110, 100)
    vs = make_variants(base, count=3, darker_factor=0.85, desaturate=0.15)
    assert len(vs) == 3, vs
    assert vs[0] == base, vs
    assert sum(vs[1]) < sum(base), vs
    g = round(sum(base) / 3)
    assert abs(vs[2][0] - g) < abs(base[0] - g) + 1, vs
    for v in vs:
        assert all(0 <= c <= 255 for c in v), v


def test_make_variants_clamps_and_pads():
    vs = make_variants((10, 10, 10), count=5, darker_factor=0.5, desaturate=0.2)
    assert len(vs) == 5
    for v in vs:
        assert all(0 <= c <= 255 for c in v), v


def test_find_base_texture_prefers_color_suffix():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Concrete001_2K-PNG_Color.png"), "wb").close()
        open(os.path.join(d, "Concrete001_2K-PNG_Normal.png"), "wb").close()
        hit = find_base_texture("Concrete001_2K-PNG", d)
        assert hit and hit.endswith("Concrete001_2K-PNG_Color.png"), hit


def test_find_base_texture_falls_back_to_bare_stem():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "GreyRock01_2K.png"), "wb").close()
        hit = find_base_texture("GreyRock01_2K", d)
        assert hit and hit.endswith("GreyRock01_2K.png"), hit


def test_find_base_texture_missing_returns_none():
    with tempfile.TemporaryDirectory() as d:
        assert find_base_texture("Nope", d) is None


def test_build_color_table_maps_name_to_variants():
    with tempfile.TemporaryDirectory() as d:
        Image.fromarray(np.full((8, 8, 3), [130, 120, 110], np.uint8), "RGB").save(
            os.path.join(d, "Concrete001_2K-PNG_Color.png"))
        table = build_color_table(["Concrete001_2K-PNG", "Missing_Mat"], d,
                                  variants=3, darker_factor=0.85, desaturate=0.15,
                                  trim_pct=10.0)
        assert "Concrete001_2K-PNG" in table, table
        assert len(table["Concrete001_2K-PNG"]) == 3, table
        assert "Missing_Mat" not in table, table


def test_host_material_names_reads_preparation_materials():
    cfg = (
        "preparation:\n"
        "  materials:\n"
        "    \"component::slab\": [\"Concrete001_2K-PNG\", \"Asphalt01_2K\"]\n"
        "    \"component::pier\": [\"Concrete001_2K-PNG\", \"concrete_0006_color_2k\"]\n"
        "    \"defect::spalling::CS2\": [\"Gravel01_2K\"]\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "component_base.yaml")
        open(p, "w").write(cfg)
        names = host_material_names_from_config(p)
        # only component:: materials, de-duplicated, sorted
        assert names == ["Asphalt01_2K", "Concrete001_2K-PNG", "concrete_0006_color_2k"], names


def test_host_material_names_excludes_layers():
    cfg = (
        "preparation:\n"
        "  materials:\n"
        "    \"component::slab\": [\"Concrete001_2K-PNG\"]\n"
        "    \"component::bearing\": [\"Rubber001_2K-PNG\", \"plastic_0001_color_2k\"]\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "component_base.yaml")
        open(p, "w").write(cfg)
        names = host_material_names_from_config(p, exclude_layers=["component::bearing"])
        assert names == ["Concrete001_2K-PNG"], names


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("  PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ERROR", t.__name__, "->", repr(e))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
