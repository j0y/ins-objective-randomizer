"""Pick where to join two halves, from bspscout's walkable graph.

A door in the seal wall is only a door if there is floor on both sides of it.
That is exactly what the nav cache already knows: `mise run scout` leaves a
grid of standable cells per map in `cache/<name>.npz`, with the component each
cell belongs to. So instead of guessing a corridor from a render, look for a
band of cells hugging the cut plane that is reachable on both sides at the same
height, and put the door in the widest one.

The cache is keyed to the *uncut* map, which is the right question to ask: the
door has to land where the map was walkable before it was cut in half.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cut import IN_PLANE
from .blocks import AXIS_INDEX

STAND = 2  # nav.py's stance codes: 1 crouch, 2 stand


@dataclass
class Candidate:
    centre: float          # along the first in-plane axis
    span: float            # how wide the matched run is
    floor: float           # world z (or the second in-plane axis) of the floor
    cells: int

    def door(self, width: float, height: float, drop: float = 8.0):
        """A door rectangle `(a0, b0, a1, b1)` centred on the run."""
        w = min(width, self.span) / 2.0
        return (self.centre - w, self.floor - drop, self.centre + w, self.floor + height)


def load(path):
    z = np.load(path, allow_pickle=True)
    res, origin, w = float(z["res"]), z["origin"], int(z["w"])
    cell = z["cell"]
    ix, iy = cell % w, cell // w
    xyz = np.stack([
        origin[0] + (ix + 0.5) * res,
        origin[1] + (iy + 0.5) * res,
        z["z"].astype(np.float64),
    ], axis=1)
    keep = z["reach"] & (z["stance"] == STAND)
    return xyz[keep], res


def candidates(xyz, res: float, axis: str, at: float, band: float = 96.0,
               z_tol: float = 24.0, min_span: float = 96.0) -> list[Candidate]:
    """Runs along the cut plane where both halves are standable at one height.

    Matching is per grid column: a column counts when there is a cell within
    `band` on each side of the plane and their floors agree to `z_tol`. Runs of
    adjacent matching columns are the places a door can go, longest first.
    """
    i = AXIS_INDEX[axis]
    a, b = IN_PLANE[axis]
    across = xyz[:, i]
    low = xyz[(across > at - band) & (across < at)]
    high = xyz[(across > at) & (across < at + band)]
    if not len(low) or not len(high):
        return []

    def by_column(pts):
        # Floor, not round: cell centres sit half a cell off the grid, so
        # rounding maps two neighbouring cells onto the same even column and
        # every run comes out one cell long.
        col = np.floor(pts[:, a] / res).astype(np.int64)
        out: dict[int, list[float]] = {}
        for c, v in zip(col, pts[:, b]):
            out.setdefault(int(c), []).append(float(v))
        return out

    lo_col, hi_col = by_column(low), by_column(high)
    matched: dict[int, tuple[float, int]] = {}
    for c, lvals in lo_col.items():
        hvals = hi_col.get(c)
        if not hvals:
            continue
        best = None
        for lv in lvals:
            for hv in hvals:
                if abs(lv - hv) <= z_tol:
                    pair = (lv + hv) / 2.0
                    if best is None or pair < best:
                        best = pair
        if best is not None:
            matched[c] = (best, len(lvals) + len(hvals))

    out: list[Candidate] = []
    for run in _runs(sorted(matched)):
        floors = [matched[c][0] for c in run]
        span = (run[-1] - run[0] + 1) * res
        if span < min_span:
            continue
        out.append(Candidate(
            centre=((run[0] + run[-1]) / 2.0 + 0.5) * res,
            span=span,
            floor=float(np.median(floors)),
            cells=sum(matched[c][1] for c in run),
        ))
    out.sort(key=lambda c: (-c.span, -c.cells))
    return out


def _runs(cols: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for c in cols:
        if out and c == out[-1][-1] + 1:
            out[-1].append(c)
        else:
            out.append([c])
    return out
