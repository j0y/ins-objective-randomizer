"""The PVS out of the VISIBILITY lump: how much of the map each cluster can see.

No rays here - vvis already did that work at compile time and stored the answer,
so "how expensive is this map to render" is a lump read rather than a
measurement. Mean visible clusters per cluster is the render-cost proxy PLAN.md
step 1 asks for; step 2 wants the same bitset to reject ray pairs before
tracing them, which is why this returns the rows and not just the counts.

Clusters, not leaves: leaves outside the PVS (solid, and detail leaves) carry
cluster -1 and are not part of the visibility problem at all.
"""
from __future__ import annotations

import numpy as np

from .bsp import Bsp

_POPCOUNT = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.int64)


class Pvs:
    """Decoded cluster-to-cluster visibility.

    `rows[c]` is a packed bitset of the clusters visible from cluster c, so
    `visible(a, b)` is one bit test and a whole-map count is a popcount.
    """

    def __init__(self, bsp: Bsp):
        raw = bsp.file.raw(4)
        self.clusters = int(np.frombuffer(raw[:4], "<i4")[0]) if len(raw) >= 4 else 0
        self.stride = (self.clusters + 7) // 8
        self.rows = _decode(raw, self.clusters, self.stride) if self.clusters else \
            np.zeros((0, 0), np.uint8)
        # A leaf's cluster is how a world position enters the PVS.
        self.leaf_cluster = bsp.leafs["cluster"].astype(np.int64)

    def visible(self, a: int, b: int) -> bool:
        if not self.clusters or a < 0 or b < 0:
            return True          # no PVS to reject with: assume the worst
        return bool(self.rows[a, b >> 3] & (1 << (b & 7)))

    def counts(self) -> np.ndarray:
        """Clusters visible from each cluster."""
        if not self.clusters:
            return np.zeros(0, np.int64)
        counts = _POPCOUNT[self.rows].sum(1)
        # The last byte of a row carries padding bits beyond `clusters`; vvis
        # writes them zero, but a truncated lump would not, so mask rather than
        # trust it.
        spare = self.stride * 8 - self.clusters
        if spare:
            mask = np.uint8((1 << (8 - spare)) - 1)
            counts -= _POPCOUNT[self.rows[:, -1]] - _POPCOUNT[self.rows[:, -1] & mask]
        return counts


def _decode(raw: bytes, clusters: int, stride: int) -> np.ndarray:
    """Undo vvis's run-length encoding: a zero byte means "N zero bytes follow"."""
    offs = np.frombuffer(raw[4:4 + 8 * clusters], "<i4").reshape(clusters, 2)
    data = np.frombuffer(raw, dtype=np.uint8)
    out = np.zeros((clusters, stride), np.uint8)
    n = len(data)
    for c in range(clusters):
        p = int(offs[c, 0])          # [c, 1] is the PAS, the audible set
        if p < 0:
            continue
        col = 0
        while col < stride and p < n:
            v = data[p]; p += 1
            if v:
                out[c, col] = v
                col += 1
            elif p < n:
                col += int(data[p]); p += 1
            else:
                break
    return out


def cost(bsp: Bsp) -> dict:
    """Render-cost proxy: how much of the map an average viewpoint can see."""
    pvs = Pvs(bsp)
    n = pvs.clusters
    if not n:
        return {"pvs_clusters": 0, "pvs_mean_visible": None, "pvs_visible_frac": None,
                "pvs_p95_visible": None}
    c = pvs.counts()
    return {
        "pvs_clusters": n,
        "pvs_mean_visible": float(c.mean()),
        "pvs_visible_frac": float(c.mean() / n),
        "pvs_p95_visible": float(np.percentile(c, 95)),
    }
