#!/usr/bin/env python3
"""Self-contained tests for tinted_gravel (no pytest). Run: python3 tools/spalling/test_tinted_gravel.py"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tinted_gravel import ratio_tint, generate_tinted_sets  # noqa: E402


def test_ratio_tint_shifts_mean_keeps_variance():
    rng = np.random.default_rng(0)
    base = (rng.normal(120, 25, (64, 64, 3)).clip(0, 255)).astype(np.uint8)
    target = (60, 90, 140)
    out = ratio_tint(base, target)
    m = out.reshape(-1, 3).mean(axis=0)
    assert all(abs(m[i] - target[i]) <= 6 for i in range(3)), m
    bstd = base.reshape(-1, 3).std(axis=0)
    ostd = out.reshape(-1, 3).std(axis=0)
    assert all(ostd[i] > 0.4 * bstd[i] for i in range(3)), (bstd, ostd)


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
        assert (nrm == [128, 128, 255]).all()  # normal copied verbatim (relief preserved)


def _run():
    ts = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for t in ts:
        try:
            t()
            print("  PASS", t.__name__)
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("  FAIL", t.__name__, "->", repr(e))
    print("{}/{} passed".format(len(ts) - bad, len(ts)))
    return bad


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
