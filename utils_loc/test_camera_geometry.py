import math
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_geometry import sample_view_direction, _angle_between  # noqa: E402

NORMAL = (0.0, 0.0, 1.0)


def test_oblique_angle_within_range():
    random.seed(1)
    for _ in range(200):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.0, jitter_deg=0.0)
        ang = math.degrees(_angle_between(d, NORMAL))
        assert 20.0 - 1e-6 <= ang <= 50.0 + 1e-6, ang


def test_always_outward():
    random.seed(2)
    for _ in range(200):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.25, jitter_deg=5.0)
        assert d[0] * NORMAL[0] + d[1] * NORMAL[1] + d[2] * NORMAL[2] > 0.0


def test_head_on_fraction_roughly_holds():
    random.seed(3)
    near = 0
    n = 1000
    for _ in range(n):
        d = sample_view_direction(NORMAL, oblique_range=(20.0, 50.0),
                                  head_on_fraction=0.3, jitter_deg=0.0)
        if math.degrees(_angle_between(d, NORMAL)) < 10.0:
            near += 1
    assert 0.2 < near / n < 0.4, near / n


def test_no_range_is_head_on():
    random.seed(4)
    d = sample_view_direction(NORMAL, oblique_range=None,
                              head_on_fraction=0.0, jitter_deg=0.0)
    assert math.degrees(_angle_between(d, NORMAL)) < 1e-6


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
