"""Watertight assemblies: partitions of a volume into brushes that meet exactly.

The rule this module exists to enforce: never subtract, only partition. Source
has no CSG at compile time - a "hole" in a wall is several brushes tiling the
wall around it, and if the tiling is off by one unit vvis stops with a leak. So
each helper here works out the cut planes, and the brushes it returns share
those planes by construction rather than by arithmetic that happened to agree.

`slab_with_opening` checks itself: axis-aligned box volumes are exact in
floating point at map scale, so parts + hole == slab is a real proof that the
tiling has no gap and no overlap, not a smoke test.
"""
from __future__ import annotations

from .brush import DEV, Solid, box

# The parts of a hollow box, and what a caller may omit.
PARTS = ("floor", "ceiling", "-x", "+x", "-y", "+y")


def hollow_box(mins, maxs, thickness: float, material: str = DEV, materials=None, omit=()) -> list[Solid]:
    """Six brushes tiling the shell of a box, hollow inside. Hammer's Hollow tool.

    `mins`/`maxs` are the *outer* bounds, so the interior is inset by
    `thickness` on all six faces. Floor and ceiling take the full footprint,
    the x walls span the full y, and the y walls are trimmed to what is left -
    a partition, so no two brushes overlap and nothing is left uncovered.

    Omitting a part leaves a hole that something else must fill: the whole
    point of this module is that the caller knows it is doing that.
    """
    bad = set(omit) - set(PARTS)
    if bad:
        raise ValueError(f"unknown parts {sorted(bad)}; expected from {PARTS}")
    x0, y0, z0 = (float(c) for c in mins)
    x1, y1, z1 = (float(c) for c in maxs)
    t = float(thickness)
    if min(x1 - x0, y1 - y0, z1 - z0) <= 2 * t:
        raise ValueError(f"{thickness}-unit walls leave no interior in {mins} .. {maxs}")
    parts = {
        "floor": ((x0, y0, z0), (x1, y1, z0 + t)),
        "ceiling": ((x0, y0, z1 - t), (x1, y1, z1)),
        "-x": ((x0, y0, z0 + t), (x0 + t, y1, z1 - t)),
        "+x": ((x1 - t, y0, z0 + t), (x1, y1, z1 - t)),
        "-y": ((x0 + t, y0, z0 + t), (x1 - t, y0 + t, z1 - t)),
        "+y": ((x0 + t, y1 - t, z0 + t), (x1 - t, y1, z1 - t)),
    }
    materials = materials or {}
    return [
        box(lo, hi, materials.get(name, material))
        for name, (lo, hi) in parts.items()
        if name not in omit
    ]


def room(mins, maxs, thickness: float, material: str = DEV, materials=None, omit=()) -> list[Solid]:
    """A sealed room given its *interior* bounds - walls grow outwards from them.

    The interior is what a map spec talks about: floor at z=mins[2], headroom to
    maxs[2]. `hollow_box` is the same thing measured from the outside.
    """
    t = float(thickness)
    lo = tuple(float(c) - t for c in mins)
    hi = tuple(float(c) + t for c in maxs)
    return hollow_box(lo, hi, t, material, materials, omit)


def slab_with_opening(mins, maxs, hole_mins, hole_maxs, material: str = DEV) -> list[Solid]:
    """Tile a wall slab with brushes, leaving a rectangular opening.

    The slab's thinnest axis is its thickness; the opening is given in world
    coordinates and clamped to the slab, so a doorway can be specified as a
    box that pokes through the wall without arithmetic at the call site. The
    cut is: full-width band below the opening, full-width band above it, then
    the band beside it split left and right.
    """
    lo = [float(c) for c in mins]
    hi = [float(c) for c in maxs]
    if any(hi[i] <= lo[i] for i in range(3)):
        raise ValueError(f"slab has no volume: {mins} .. {maxs}")
    thin = min(range(3), key=lambda i: hi[i] - lo[i])
    a, b = (i for i in range(3) if i != thin)

    h_lo = [max(lo[i], float(hole_mins[i])) for i in range(3)]
    h_hi = [min(hi[i], float(hole_maxs[i])) for i in range(3)]
    if any(h_hi[i] <= h_lo[i] for i in (a, b)):
        return [box(lo, hi, material)]  # the opening misses the slab entirely

    parts = []

    def piece(alo, ahi, blo, bhi):
        if ahi - alo < 1e-6 or bhi - blo < 1e-6:
            return
        p_lo, p_hi = list(lo), list(hi)
        p_lo[a], p_hi[a] = alo, ahi
        p_lo[b], p_hi[b] = blo, bhi
        parts.append(box(p_lo, p_hi, material))

    piece(lo[a], hi[a], lo[b], h_lo[b])          # below
    piece(lo[a], hi[a], h_hi[b], hi[b])          # above
    piece(lo[a], h_lo[a], h_lo[b], h_hi[b])      # beside, one way
    piece(h_hi[a], hi[a], h_lo[b], h_hi[b])      # beside, the other

    slab_v = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
    hole_v = (hi[thin] - lo[thin]) * (h_hi[a] - h_lo[a]) * (h_hi[b] - h_lo[b])
    made = 0.0
    for s in parts:
        plo, phi = s.bounds()
        made += (phi[0] - plo[0]) * (phi[1] - plo[1]) * (phi[2] - plo[2])
    if abs(made + hole_v - slab_v) > 1e-3:
        raise AssertionError(
            f"opening tiling is not exact: {made} + {hole_v} != {slab_v} "
            f"for slab {mins}..{maxs} hole {hole_mins}..{hole_maxs}"
        )
    return parts


def doorway(mins, maxs, along: float, width: float, height: float, material: str = DEV) -> list[Solid]:
    """A wall slab with a door-sized opening centred at `along` on its long axis."""
    lo = [float(c) for c in mins]
    hi = [float(c) for c in maxs]
    thin = min(range(3), key=lambda i: hi[i] - lo[i])
    long_axis = max((i for i in range(3) if i != thin and i != 2), key=lambda i: hi[i] - lo[i])
    h_lo, h_hi = list(lo), list(hi)
    h_lo[long_axis], h_hi[long_axis] = along - width / 2, along + width / 2
    h_lo[2], h_hi[2] = lo[2], lo[2] + height
    return slab_with_opening(lo, hi, h_lo, h_hi, material)
