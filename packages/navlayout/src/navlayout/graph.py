"""The surveyed nav graph, and the path questions asked of it.

Every metric in this package is an edge computation, which is the whole reason
the graph comes from the engine rather than from the shipped `.nav`. Edge
lengths are `NavConnect::length` as the engine stores it, so a path distance
here is the distance the game itself would quote.

Connections are **directed**. Nav meshes are full of one-way edges - a drop you
can fall down but not climb - and treating them as symmetric would quietly
invent routes. Where a symmetric view is the right one (undirected components,
or a cut that should not care which way a player is travelling) it is asked for
by name.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .survey import Survey

INF = np.inf


@dataclass
class NavGraph:
    """A survey's areas as a sparse weighted digraph."""

    n: int
    adj: csr_matrix              # directed, weight = engine edge length
    undirected: csr_matrix       # symmetrised, for components and cuts
    # The same edges as flat arrays. Route finding removes areas over and over,
    # and rebuilding from these is both faster and safer than masking the csr:
    # multiplying a sparse matrix by a 0/1 mask leaves *explicit zeros*, and
    # scipy's dijkstra reads a stored zero as an edge of length zero rather than
    # as no edge. That silently let every route after the first walk through the
    # areas it had just been forbidden, for free.
    edge_row: np.ndarray
    edge_col: np.ndarray
    edge_len: np.ndarray
    centers: np.ndarray          # (n, 3)
    hull_ok: np.ndarray          # (n,) bool
    blocked: np.ndarray          # (n,) bool
    indoor: np.ndarray           # (n,) bool
    breadth: np.ndarray          # (n,) narrow dimension of each area

    @classmethod
    def build(cls, survey: Survey) -> "NavGraph":
        n = len(survey.areas)
        rows: list[int] = []
        cols: list[int] = []
        data: list[float] = []

        for a in survey.areas:
            for _dir, target, length in a.conn:
                if not (0 <= target < n):
                    continue
                rows.append(a.i)
                cols.append(target)
                # A zero-length edge would make dijkstra treat the pair as one
                # node and is not a distance anyone means; the engine emits a
                # few. Floor them rather than drop them - the connection is
                # real, only its length is degenerate.
                data.append(max(float(length), 1e-3))

        edge_row = np.asarray(rows, dtype=np.int32)
        edge_col = np.asarray(cols, dtype=np.int32)
        edge_len = np.asarray(data, dtype=np.float64)
        adj = csr_matrix((edge_len, (edge_row, edge_col)), shape=(n, n))

        # The symmetric view exists for questions about *structure* - which
        # areas hang together, where the graph pinches - so an edge present in
        # either direction is an edge. Where both directions exist the engine
        # gives near-identical lengths, so elementwise max is not a choice that
        # costs anything; where only one does, max is that one length.
        undirected = adj.maximum(adj.T).tocsr()

        return cls(
            n=n,
            adj=adj,
            undirected=undirected,
            edge_row=edge_row,
            edge_col=edge_col,
            edge_len=edge_len,
            centers=survey.centers(),
            hull_ok=np.array([a.hull_ok for a in survey.areas]),
            blocked=np.array([a.blocked for a in survey.areas]),
            indoor=np.array([a.indoor for a in survey.areas]),
            breadth=np.array([a.breadth for a in survey.areas]),
        )

    # ── distance ─────────────────────────────────────────────────────

    def distances(self, sources: int | list[int]) -> np.ndarray:
        """Shortest path length from `sources` to every area, `inf` if none.

        Multi-source when given a list, which is what a spawn *zone* is: several
        volumes can share one targetname, and a player leaves from whichever of
        them the game picked.
        """
        src = [sources] if isinstance(sources, int) else list(sources)
        if not src:
            return np.full(self.n, INF)
        d = dijkstra(self.adj, directed=True, indices=src)
        return np.atleast_2d(d).min(axis=0)

    def distances_to(self, targets: int | list[int]) -> np.ndarray:
        """Shortest path length from every area *to* `targets`, `inf` if none.

        `distances` run over the reversed edges. The two differ wherever a route
        has a one-way edge in it: a drop is walkable towards the bottom and not
        back, so measuring outward from an objective calls ground unreachable
        that bots walk to it from every round.
        """
        dst = [targets] if isinstance(targets, int) else list(targets)
        if not dst:
            return np.full(self.n, INF)
        d = dijkstra(self.adj.T.tocsr(), directed=True, indices=dst)
        return np.atleast_2d(d).min(axis=0)

    def path(self, source: int, target: int) -> list[int]:
        """One shortest path, as area indices. Empty when unreachable."""
        d, pred = dijkstra(
            self.adj, directed=True, indices=source, return_predecessors=True
        )
        if not np.isfinite(d[target]):
            return []
        out = [target]
        while out[-1] != source:
            out.append(int(pred[out[-1]]))
        return out[::-1]

    def without(self, removed: np.ndarray | set[int]) -> csr_matrix:
        """The graph with some areas deleted - edges dropped, not zeroed."""
        keep = np.ones(self.n, dtype=bool)
        idx = np.fromiter(removed, dtype=np.int64) if isinstance(removed, set) else removed
        if len(idx):
            keep[idx] = False
        m = keep[self.edge_row] & keep[self.edge_col]
        return csr_matrix(
            (self.edge_len[m], (self.edge_row[m], self.edge_col[m])),
            shape=(self.n, self.n),
        )

    def reachable(self, sources: int | list[int]) -> np.ndarray:
        """Boolean mask of what `sources` can walk to."""
        return np.isfinite(self.distances(sources))

    # ── shape of the graph itself ────────────────────────────────────

    def components(self, *, directed: bool = False) -> tuple[int, np.ndarray]:
        """Connected components. Undirected by default.

        The check this exists for is blunt: a shipped map that people play is
        one component. Several means the graph is wrong, and on the parsed
        `.nav` that is exactly what happened - 2379 / 533 / 178.
        """
        if directed:
            return connected_components(self.adj, directed=True, connection="strong")
        return connected_components(self.undirected, directed=False)

    def largest_component(self) -> np.ndarray:
        _n, labels = self.components()
        if labels.size == 0:
            return np.zeros(0, dtype=bool)
        biggest = np.bincount(labels).argmax()
        return labels == biggest
