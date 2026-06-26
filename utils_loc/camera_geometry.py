"""Pure-math camera view-direction sampling (NO Rhino import, so it's WSL-unit-testable).

Used by camera.generate_defect_camera_poses to view defects OBLIQUELY (off the surface
normal) instead of head-on, so spall cavities / crack grooves show their depth via parallax.
"""
import math
import random


def _normalize(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _angle_between(a, b):
    a = _normalize(a)
    b = _normalize(b)
    return math.acos(max(-1.0, min(1.0, _dot(a, b))))


def _orthonormal_basis(normal):
    n = _normalize(normal)
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.95 else (0.0, 1.0, 0.0)
    u = _normalize(_cross(ref, n))
    v = _normalize(_cross(n, u))
    return u, v


def _jitter(direction, max_deg, rng):
    if max_deg <= 0.0:
        return direction
    u, v = _orthonormal_basis(direction)
    phi = rng.uniform(0.0, 2.0 * math.pi)
    t = (u[0] * math.cos(phi) + v[0] * math.sin(phi),
         u[1] * math.cos(phi) + v[1] * math.sin(phi),
         u[2] * math.cos(phi) + v[2] * math.sin(phi))
    scale = math.tan(math.radians(rng.uniform(0.0, max_deg)))
    return _normalize((direction[0] + t[0] * scale,
                       direction[1] + t[1] * scale,
                       direction[2] + t[2] * scale))


def sample_view_direction(normal, oblique_range, head_on_fraction, jitter_deg, rng=random):
    """Unit view direction on the outward hemisphere of `normal`.

    With probability head_on_fraction (or if oblique_range is falsy): near head-on (jitter
    only). Otherwise oblique: tilt theta in oblique_range degrees off the normal, at a random
    azimuth around it. A small jitter_deg spread is applied on top either way.
    """
    n = _normalize(normal)
    if not oblique_range or rng.random() < float(head_on_fraction):
        d = n
    else:
        theta = math.radians(rng.uniform(float(oblique_range[0]), float(oblique_range[1])))
        phi = rng.uniform(0.0, 2.0 * math.pi)
        u, v = _orthonormal_basis(n)
        tangent = (u[0] * math.cos(phi) + v[0] * math.sin(phi),
                   u[1] * math.cos(phi) + v[1] * math.sin(phi),
                   u[2] * math.cos(phi) + v[2] * math.sin(phi))
        ct, st = math.cos(theta), math.sin(theta)
        d = (n[0] * ct + tangent[0] * st,
             n[1] * ct + tangent[1] * st,
             n[2] * ct + tangent[2] * st)
    d = _jitter(d, float(jitter_deg or 0.0), rng)
    if _dot(d, n) <= 0.0:  # numerical guard: keep it in front of the surface
        d = n
    return _normalize(d)
