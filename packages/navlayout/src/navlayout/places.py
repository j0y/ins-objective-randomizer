"""PLAN.md step B2 — the mesh, coarsened to the spaces a player actually sees.

A nav mesh is not a description of space. It is a tiling of the floor, cut for
the pathfinder's convenience: a wide street is several areas abreast, a
courtyard is dozens, and a doorway is one. Every metric that counts something -
routes, chokepoints, ways in - has therefore been answering a question about the
tiling rather than about the map, and the symptoms are all in `openness.py`:
`disjoint_routes` needed a 256 u separation radius invented for it because two
lanes of one street came back as two routes, and the same granularity read
buhriz as having 22 parallel ways in when it was asked as a flow problem.

Diffusion is the operator that does not care how the floor was cut. A random
walk released in a courtyard mixes across it in a few steps and leaks out
through a doorway slowly, so the slow modes of the walk *are* the rooms - which
is spectral clustering on the normalised Laplacian, and costs one `eigsh` per
map. What comes back is a graph of ten to thirty **places**, and on that graph
the questions are one-liners over a structure small enough to look at:

| question                          | on the places graph            |
|-----------------------------------|--------------------------------|
| is this map a corridor            | are most places cut vertices   |
| is there room to move an objective| are there more places than objectives |
| does it offer a choice of ways    | how many places have >2 exits  |

**Conductance is the check on the whole idea, not decoration.** A place that
keeps 95% of the walk inside it is a room. One the walk leaves half the time is
a patch of open ground a split ran through, and says the partition is too fine
for the map rather than that the map has that many rooms.

**A door cuts the segment; open ground does not.** The first version asked for
places of a target *area* and got wings of a building rather than rooms, which
is the same granularity error one level up: a size is not what makes a space a
space. What makes one is that you have to go through something to leave it. So a
place is split at its tightest boundary - order its areas by the Fiedler vector,
sweep every prefix cut, take the one the walk crosses least - and the recursion
stops when the best cut available is still wide open. `DOOR_CONDUCTANCE` is the
one knob and it is a door, not a size: below it there is a way through worth
calling a way through, above it the place is one room and stays whole.

The spectrum was tried as the scale first and gives nothing usable: on
ministry_coop the first forty eigenvalues all sit within 1e-3 of zero and the
largest gaps land at k=31, 21, 9 in that order, which is not a map speaking.

**A hinge has to separate two halves, not lop off a dead end.** At door
granularity a map is full of rooms with one way in, and the room next door is an
articulation point in the graph sense while gating nothing anybody walks
through. Counting those put drycanal_coop at 18% hinges beside ministry_coop's
44%, which is not what either map plays like. A hinge is therefore a place whose
removal strands at least `HINGE_SHARE` of the walkable floor, and on that
definition drycanal falls to 10% and its `_open_` reworking to 0%.

`severance` is reported beside the count because the two say different things and
a map can have one of each. drycanal_coop has few hinges and 76% of its places
branching - it reads open - and it still has a single place holding 45% of the
map behind it. That pinch is real, it is the upper-left district, and it is the
thing to know before re-siting an objective there.

**What the coarsening does and does not add, measured over the 46 surveys.**
Both axes now carry information the mesh could not give directly, which was not
true of the first version. Place count correlates 0.81 with walkable area - big
maps do have more rooms - but the room density varies nine-fold across the
corpus, 0.16 to 1.53 places per Mu², so the partition is reading structure and
not dividing by a constant. Headroom correlates 0.64 with area per objective,
down from the 0.999 the fixed-area version scored, which is the difference
between a measurement and a restatement. The hinge fraction correlates -0.29
with log area, so calling a map a chain is not a roundabout way of calling it
small.

**Most maps come out open, and that is the finding rather than a slack
threshold.** 39 of 46 are open, four are chains and two are small. What
separates the open ones from each other is no longer the hinge count - it is
`severance`, and seven of the open maps carry a place with a quarter or more of
the floor behind it. The three-way taxonomy is really a two-way one plus a
per-map list of pinches to respect.

**A disconnected piece is already two places** and is split before any cut is
looked for, so the partition never returns a place that is two rooms at opposite
ends of the map. The old k-means pass needed a repair step for exactly this and
produced a split cluster about a fifth of the time; the recursion gets it for
free by checking components at every level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.sparse import coo_matrix, csr_matrix, diags
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import eigsh

from .graph import NavGraph
from .survey import Survey

# What counts as a door. A boundary the random walk crosses less often than this
# is something you go *through*; anything looser is open ground and does not
# divide a space. Landed by sweeping: at 0.02 ministry_coop comes back as 34
# places whose cuts sit on its doorways, at 0.05 it is 57 and rooms start
# splitting down the middle, and above about 0.10 the recursion is stopped by
# MIN_PLACE rather than by the door test, which means the rule has stopped doing
# the work.
DOOR_CONDUCTANCE = 0.02

# A place smaller than this is not a space, it is a corner. Also the floor that
# terminates the recursion when a map has no doors left to cut.
MIN_PLACE = 0.25e6

# How much of the map a place has to strand before it is a hinge rather than the
# only way into somebody's back room.
HINGE_SHARE = 0.10

# A place the walk leaves this often per step is not a room. Reported rather
# than enforced: it is the signal that DOOR_CONDUCTANCE is too loose for the map.
ROOM_CONDUCTANCE = 0.15

# Where the taxonomy's boundaries sit, on the places graph. Both are landed in
# gaps in the measured distribution over all 46 surveys, and both are quoted
# here so a later run that moves them is visibly a change of answer.
#
# **Hinge fraction** sorts as .35 .33 .30 .26 | .19 .12 | .10 .06 .05 .05 .04 and
# down. The upper gap puts the linear boundary at 0.25 and it selects dead_air,
# congress_coop, market_coop and ministry_coop; of the 13 shipped `_coop` maps
# that is market and ministry, which are also the two `openness.score` puts last
# by its own independent route, at 0.197 and 0.095. The lower gap puts the open
# boundary at 0.10.
#
# **Headroom** used to need no tuning - when a place was a wing of a building,
# a map with fewer places than objectives plainly had nowhere new to put one and
# 1.0 was the boundary by meaning. Cutting at doors ended that: every map in the
# corpus now has more rooms than objectives, and the smallest still comes last
# rather than below one. So this is a calibrated boundary like the others, and
# it lands in the widest gap in the corpus: inferno_new at 1.29 and cs_officeb3
# at 1.33, then nothing until iron_express at 2.00. It reads as "fewer than
# about two rooms per objective, so any two layouts must reuse the same rooms".
SMALL_HEADROOM = 1.60
LINEAR_HINGE_FRACTION = 0.25
OPEN_HINGE_FRACTION = 0.10

# **A map with no hinges is not thereby open.** The hinge fraction counts cut
# vertices, which is a measurement of how *tree-like* the places graph is, and a
# graph can be free of them and still be a corridor: a ring has no cut vertex
# anywhere and plays exactly as linearly as a path, because at every point there
# are two ways on and both of them are the same round. So the hinge test cannot
# distinguish a map you can go round from a map you can go *through*, and it
# calls both open.
#
# The loop density is the term that can. It is the cyclomatic number of the
# places graph - `edges - places + components`, the count of independent cycles -
# divided by the number of places, so it says how many ways round there are per
# room rather than whether there is one. A path scores 0, a single ring 1/k, and
# a web scores near 1.
#
# Landed in the widest gap in the corpus over the region that matters: sorted
# over all 125 surveys the density runs ... 0.386 0.394 0.406 | 0.444 0.465
# 0.469 ..., and 0.44 sits in that 0.038-wide gap. It also sits above the whole
# `linear` population bar two - dedust1p2_aof 0.06, nova_prospect 0.07, dead_air
# and ministry_coop 0.26, crash_course 0.29, congress_coop 0.30, bunker_busting
# 0.39 - with market_coop at 0.47 and hard_rain at 0.49 already linear on their
# hinges, so the two tests agree on every map either of them calls a corridor.
#
# It moves six maps out of `open`, all of them few-hinged and loop-poor at once:
# ministry_coop_old_fixbysakey 0.30, launch_control_coop_ws 0.31, iron_express
# 0.36, docks_day 0.39, siege_coop 0.40, warehouse_coopv2u1 0.41.
OPEN_LOOP_DENSITY = 0.44

@dataclass

@dataclass
class Place:
    """One space, and how tightly it holds together."""

    index: int
    areas: list[int]
    walkable: float                # u^2 of floor
    center: np.ndarray             # centroid of its areas, weighted by footprint
    conductance: float             # fraction of the walk that leaves per step
    degree: int = 0                # how many other places it touches
    hinge: bool = False            # removing it strands a real share of the map
    severs: float = 0.0            # the share it strands, 0 if it strands nothing

    @property
    def is_room(self) -> bool:
        return self.conductance <= ROOM_CONDUCTANCE


@dataclass
class Places:
    """A map as a graph of places, and what that graph says about it."""

    map: str
    places: list[Place]
    edges: list[tuple[int, int]]
    label: np.ndarray              # area index -> place index, -1 off the mesh
    walkable: float
    door: float = DOOR_CONDUCTANCE
    stages: int | None = None      # objectives in the chain, or None if unknown
    stages_inferred: bool = False

    def __len__(self) -> int:
        return len(self.places)

    @property
    def hinge_places(self) -> int:
        return sum(1 for p in self.places if p.hinge)

    @property
    def hinge_fraction(self) -> float:
        return self.hinge_places / len(self.places) if self.places else 0.0

    @property
    def severance(self) -> float:
        """The largest share of the map any one place holds behind it.

        A different question from the hinge count and worth both: a map can have
        few hinges and still have one pinch that halves it. drycanal_coop is
        that map - 10% hinges, 76% of its places branching, and a single place
        with 45% of the floor behind it.
        """
        return max((p.severs for p in self.places), default=0.0)

    @property
    def loops(self) -> int:
        """Independent cycles in the places graph - `edges - places + components`.

        How many ways *round* the map has, as opposed to how many ways through.
        A tree scores 0 however many rooms it has; every loop past that is one
        more circuit a player can take that is not a retracing of the last.
        """
        k = len(self.places)
        if not k:
            return 0
        seen = list(range(k))

        def find(i: int) -> int:
            while seen[i] != i:
                seen[i] = seen[seen[i]]
                i = seen[i]
            return i

        comps = k
        for a, b in self.edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                seen[ra] = rb
                comps -= 1
        return len(self.edges) - k + comps

    @property
    def loop_density(self) -> float:
        """Loops per place. 0 is a tree, 1/k a single ring, near 1 a web."""
        return self.loops / len(self.places) if self.places else 0.0

    @property
    def branching(self) -> int:
        return sum(1 for p in self.places if p.degree > 2)

    @property
    def branch_fraction(self) -> float:
        return self.branching / len(self.places) if self.places else 0.0

    @property
    def median_conductance(self) -> float:
        vals = [p.conductance for p in self.places if np.isfinite(p.conductance)]
        return float(np.median(vals)) if vals else float("nan")

    @property
    def rooms(self) -> int:
        return sum(1 for p in self.places if p.is_room)

    @property
    def headroom(self) -> float | None:
        """Places per objective - how much room a re-siting pass has to work in."""
        if not self.stages:
            return None
        return len(self.places) / self.stages

    @property
    def kind(self) -> str:
        """Which of the layout operations this map's shape actually licenses.

        Ordered so the binding constraint wins. Having nowhere to put an
        objective is binding whatever the connectivity looks like, so the room
        test comes first; being a chain of hinges is binding next.

        `severance` deliberately does **not** enter this. It is reported beside
        the label instead, because a single pinch does not make a map a chain -
        it makes one place on an otherwise open map worth knowing about, and
        folding it in here would relabel drycanal_coop and district_coop on the
        strength of one room each.

        **Open is the one label that takes two measurements.** Having no hinges
        says only that nothing strands when a place is taken away, and a ring
        satisfies that while playing as a corridor - so a map has to be
        loop-rich as well as hinge-free to be re-sited freely. A map that is
        hinge-free and loop-poor is walked round rather than crossed, which is
        a corridor by another route, and it gets the corridor's operation. See
        `OPEN_LOOP_DENSITY`.
        """
        h = self.headroom
        if h is not None and h < SMALL_HEADROOM:
            return "small"
        if self.hinge_fraction >= LINEAR_HINGE_FRACTION:
            return "linear"
        if self.hinge_fraction <= OPEN_HINGE_FRACTION:
            return "open" if self.loop_density >= OPEN_LOOP_DENSITY else "linear"
        return "segmented"

    @property
    def operation(self) -> str:
        """What may be done to the chain, in one phrase."""
        return {
            "small": "reorder the chain and randomise the starting objective",
            "linear": "reverse the chain, and swap the spawns with it",
            "segmented": "shuffle within each run of places between hinges, reverse across them",
            "open": "re-site objectives and spawns freely",
        }[self.kind]

    def hinges(self) -> list[int]:
        """The hinge places, worst first."""
        return [p.index for p in sorted(self.places, key=lambda q: -q.severs) if p.hinge]

    def place_of(self, area: int) -> int:
        return int(self.label[area]) if 0 <= area < self.label.size else -1

    def to_dict(self) -> dict:
        return {
            "map": self.map,
            "places": len(self.places),
            "walkable": self.walkable,
            "door": self.door,
            "stages": self.stages,
            "stages_inferred": self.stages_inferred,
            "headroom": self.headroom,
            "hinge_places": self.hinge_places,
            "hinge_fraction": self.hinge_fraction,
            "loops": self.loops,
            "loop_density": self.loop_density,
            "severance": self.severance,
            "branching": self.branching,
            "branch_fraction": self.branch_fraction,
            "median_conductance": self.median_conductance,
            "rooms": self.rooms,
            "kind": self.kind,
            "operation": self.operation,
            "edges": [list(e) for e in self.edges],
            "detail": [
                {
                    "index": p.index,
                    "areas": len(p.areas),
                    "walkable": p.walkable,
                    "center": [round(float(c), 1) for c in p.center],
                    "conductance": p.conductance,
                    "degree": p.degree,
                    "hinge": p.hinge,
                    "severs": p.severs,
                }
                for p in self.places
            ],
        }


# ── the mesh a place is cut from ─────────────────────────────────────

def live_mask(graph: NavGraph) -> np.ndarray:
    """The part of the mesh worth partitioning.

    The largest connected component, minus areas the engine marked blocked. Not
    filtered by `hull_ok`: that is whether a *spawn* fits there, and a corridor
    a player walks down but cannot spawn in is still part of the space.
    """
    _, comp = connected_components(graph.undirected, directed=False)
    if comp.size == 0:
        return np.zeros(graph.n, dtype=bool)
    big = np.bincount(comp).argmax()
    return (comp == big) & ~graph.blocked


def _conductance_matrix(graph: NavGraph, live: np.ndarray) -> tuple[csr_matrix, np.ndarray]:
    """Symmetric conductance over the live areas: 1 / the engine's edge length.

    Nothing is done about how wide a join is, and that is deliberate. The mesh
    already encodes width by subdividing - a street four areas across is four
    parallel conductors and a doorway is one - so the width term is there
    whether or not it is written down, and writing it down again would count it
    twice.
    """
    u = graph.undirected.tocoo()
    m = (u.data > 0) & live[u.row] & live[u.col]
    idx = np.flatnonzero(live)
    pos = np.full(graph.n, -1, dtype=np.int64)
    pos[idx] = np.arange(idx.size)
    A = coo_matrix(
        (1.0 / u.data[m], (pos[u.row[m]], pos[u.col[m]])), shape=(idx.size, idx.size)
    ).tocsr()
    return A.maximum(A.T), idx


# ── cutting at the doors ─────────────────────────────────────────────

def tightest_cut(A: csr_matrix, foot: np.ndarray, min_place: float):
    """The tightest boundary through one place: `(conductance, mask)` or `None`.

    The standard Cheeger sweep. The Fiedler vector of the normalised Laplacian
    orders the areas along the place's slowest direction of mixing, every prefix
    of that order is a candidate boundary, and the one the walk crosses least is
    the door if there is one. Divided by the *smaller* side's volume, so a cut
    that shaves off a corner is not flattered by the big side's bulk.

    Returns `None` when the place is too small to divide or no candidate leaves
    `min_place` of floor on both sides.
    """
    n = A.shape[0]
    if n < 4:
        return None
    d = np.asarray(A.sum(1)).ravel()
    d[d <= 0] = 1e-12
    Dm = diags(1.0 / np.sqrt(d))
    L = (diags(np.ones(n)) - Dm @ A @ Dm).tocsc()
    try:
        # A fixed starting vector, because ARPACK's default is a random one and
        # the same map segmented twice then came back with the same verdict but
        # a different partition. A run has to be reproducible before its numbers
        # are worth writing into a preset.
        _vals, vec = eigsh(L, k=2, sigma=-1e-6, which="LM", v0=np.ones(n))
    except Exception:                                    # noqa: BLE001
        return None                                      # ARPACK gave up: leave it whole
    f = vec[:, 1] / np.sqrt(d)
    order = np.argsort(f)

    vol = d.sum()
    C = A.tocoo()
    rank = np.empty(n, dtype=np.int64)
    rank[order] = np.arange(n)
    # An edge is cut from the moment the sweep passes its first endpoint until
    # it passes its second, so one cumulative sum gives every prefix's cut
    # weight at once.
    delta = np.zeros(n + 1)
    np.add.at(delta, np.minimum(rank[C.row], rank[C.col]), C.data)
    np.add.at(delta, np.maximum(rank[C.row], rank[C.col]), -C.data)
    cut = np.cumsum(delta)[:n]
    volc = np.cumsum(d[order])
    areac = np.cumsum(foot[order])
    total = foot.sum()

    small = np.minimum(volc, vol - volc)
    ok = ((areac >= min_place) & (total - areac >= min_place)
          & (small > 0) & (volc < vol))
    ok[-1] = False
    if not ok.any():
        return None
    cond = np.full(n, np.inf)
    cond[ok] = cut[ok] / small[ok]
    i = int(np.argmin(cond))
    mask = np.zeros(n, dtype=bool)
    mask[order[:i + 1]] = True
    return float(cond[i]), mask


def partition(A: csr_matrix, foot: np.ndarray, door: float, min_place: float,
              max_places: int = 400) -> tuple[np.ndarray, int]:
    """Split at doors until there are none left, breadth-first over the queue."""
    queue: list[np.ndarray] = [np.arange(A.shape[0])]
    done: list[np.ndarray] = []
    while queue and len(done) + len(queue) < max_places:
        member = queue.pop()
        sub = A[member][:, member]
        ncomp, part = connected_components(sub, directed=False)
        if ncomp > 1:                      # already more than one place
            queue.extend(member[part == j] for j in range(ncomp))
            continue
        found = tightest_cut(sub, foot[member], min_place)
        if found is None or found[0] > door:
            done.append(member)
            continue
        _cond, mask = found
        queue.append(member[mask])
        queue.append(member[~mask])
    done.extend(queue)

    labels = np.zeros(A.shape[0], dtype=np.int64)
    for i, member in enumerate(done):
        labels[member] = i
    return labels, len(done)


def _hinges(k: int, edges: list[tuple[int, int]], weight: np.ndarray,
            share: float) -> tuple[np.ndarray, np.ndarray]:
    """Which places strand a real share of the map, and how much each strands.

    Brute force - remove one, look at what falls off - because k is tens and a
    correct loop is worth more here than Hopcroft-Tarjan. The share is measured
    in walkable floor rather than in places, so a hinge in front of one large
    district counts and a hinge in front of three closets does not.
    """
    hinge = np.zeros(k, dtype=bool)
    severs = np.zeros(k, dtype=float)
    if k <= 2 or not edges:
        return hinge, severs
    rows = [a for a, b in edges] + [b for a, b in edges]
    cols = [b for a, b in edges] + [a for a, b in edges]
    base = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(k, k)).tocsr()
    total = weight.sum()
    if total <= 0:
        return hinge, severs
    for v in range(k):
        keep = np.ones(k, dtype=bool)
        keep[v] = False
        ncomp, comp = connected_components(base[keep][:, keep], directed=False)
        if ncomp <= 1:
            continue
        w = weight[keep]
        sizes = np.sort([w[comp == c].sum() for c in range(ncomp)])
        stranded = float(sizes[-2] / total)     # everything but the main body
        severs[v] = stranded
        hinge[v] = stranded >= share
    return hinge, severs


def segment(
    survey: Survey,
    *,
    graph: NavGraph | None = None,
    door: float = DOOR_CONDUCTANCE,
    min_place: float = MIN_PLACE,
    hinge_share: float = HINGE_SHARE,
    stages: int | None = None,
    stages_inferred: bool = False,
) -> Places:
    """Coarsen a surveyed mesh into places, and read the graph they form."""
    graph = graph or NavGraph.build(survey)
    live = live_mask(graph)
    foot_all = np.array([a.area for a in survey.areas], dtype=float)
    walkable = float(foot_all[live].sum())

    A, idx = _conductance_matrix(graph, live)
    foot = foot_all[idx]
    lab, k = partition(A, foot, door, min_place)

    d = np.asarray(A.sum(1)).ravel()
    C = A.tocoo()
    crossing = lab[C.row] != lab[C.col]
    edges = sorted({
        (int(min(lab[r], lab[c])), int(max(lab[r], lab[c])))
        for r, c in zip(C.row[crossing], C.col[crossing])
    })
    degree = np.zeros(k, dtype=int)
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1

    weight = np.array([foot[lab == i].sum() for i in range(k)])
    hinge, severs = _hinges(k, edges, weight, hinge_share)

    places: list[Place] = []
    for i in range(k):
        member = np.flatnonzero(lab == i)
        vol = float(d[member].sum())
        leaving = float(C.data[crossing & (lab[C.row] == i)].sum())
        w = foot[member]
        center = (graph.centers[idx[member]] * w[:, None]).sum(0) / max(w.sum(), 1e-9)
        places.append(
            Place(
                index=i,
                areas=[int(a) for a in idx[member]],
                walkable=float(w.sum()),
                center=center,
                conductance=leaving / vol if vol > 0 else float("nan"),
                degree=int(degree[i]),
                hinge=bool(hinge[i]),
                severs=float(severs[i]),
            )
        )

    label = np.full(graph.n, -1, dtype=np.int64)
    label[idx] = lab
    return Places(
        map=survey.map,
        places=places,
        edges=edges,
        label=label,
        walkable=walkable,
        door=door,
        stages=stages,
        stages_inferred=stages_inferred,
    )


# ── reporting ────────────────────────────────────────────────────────

def report(p: Places) -> str:
    lines = [
        f"map              {p.map}",
        f"walkable         {p.walkable / 1e6:.1f} Mu^2",
        f"places           {len(p)}  (cut at doors tighter than {p.door})",
        f"conductance      median {p.median_conductance:.3f}, "
        f"{p.rooms}/{len(p)} hold the walk in",
    ]
    if p.stages:
        how = " (inferred from spawn zones)" if p.stages_inferred else ""
        lines.append(f"objectives       {p.stages}{how}   headroom {p.headroom:.2f} places each")
    else:
        lines.append("objectives       unknown - no cpsetup and no numbered spawn zones")
    lines += [
        f"hinges           {p.hinge_places}/{len(p)} places strand "
        f"{HINGE_SHARE:.0%}+ of the map ({p.hinge_fraction:.0%})",
        f"severance        worst single place holds {p.severance:.0%} of the floor behind it",
        f"loops            {p.loops} ways round for {len(p)} places "
        f"({p.loop_density:.2f} each, open needs {OPEN_LOOP_DENSITY:.2f})",
        f"branching        {p.branching}/{len(p)} places have more than two exits",
        "",
        f"kind             {p.kind.upper()}",
        f"                 {p.operation}",
    ]
    if p.kind == "linear" and p.hinge_fraction < LINEAR_HINGE_FRACTION:
        lines.append(
            f"                 nothing strands here - {p.hinge_places} hinge"
            f"{'' if p.hinge_places == 1 else 's'} - but there are only "
            f"{p.loops} ways round {len(p)} places, so it is walked round "
            f"rather than crossed"
        )
    if p.kind == "open" and p.severance >= 0.25:
        lines.append(
            f"                 but one place still holds {p.severance:.0%} behind it - "
            f"see the hinge list below before siting anything past it"
        )
    lines += [
        "",
        f"   {'#':>3} {'areas':>6} {'Mu^2':>7} {'cond':>6} {'exits':>6} {'severs':>7}  centre",
    ]
    for q in sorted(p.places, key=lambda q: (-q.severs, -q.walkable)):
        lines.append(
            f"   {q.index:3} {len(q.areas):6} {q.walkable / 1e6:7.2f} "
            f"{q.conductance:6.3f} {q.degree:6} "
            f"{(f'{q.severs:.0%}' if q.severs else ''):>7}  "
            f"{q.center[0]:.0f} {q.center[1]:.0f} {q.center[2]:.0f}"
        )
    return "\n".join(lines)


def corpus_table(rows: list[Places]) -> str:
    """The corpus as a markdown table, most open first."""
    order = {"open": 0, "segmented": 1, "linear": 2, "small": 3}
    rows = sorted(rows, key=lambda r: (order.get(r.kind, 9), r.hinge_fraction, -len(r)))
    out = [
        "| map | kind | places | obj | headroom | hinges | loops | severance | branching | walkable |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        obj = "?" if not r.stages else (f"{r.stages}*" if r.stages_inferred else str(r.stages))
        head = "-" if r.headroom is None else f"{r.headroom:.1f}"
        sev = f"**{r.severance:.0%}**" if r.severance >= 0.25 else f"{r.severance:.0%}"
        loops = (f"**{r.loop_density:.2f}**" if r.loop_density < OPEN_LOOP_DENSITY
                 else f"{r.loop_density:.2f}")
        out.append(
            f"| {r.map} | {r.kind} | {len(r)} | {obj} | {head} | "
            f"{r.hinge_places} ({r.hinge_fraction:.0%}) | {loops} | {sev} | "
            f"{r.branch_fraction:.0%} | {r.walkable / 1e6:.0f} Mu² |"
        )
    out += [
        "",
        "`obj` with a `*` is inferred from the numbered spawn zones rather than read "
        "from a cpsetup - exact on 10 of the 11 shipped maps that number their zones, "
        "and one too many on revolt_coop.",
        "",
        "`hinges` counts places that strand %.0f%% or more of the walkable floor. "
        "`severance` is the largest share any single place holds behind it, and is "
        "**bold** past 25%%: those maps are classified on their hinge count like "
        "everything else, but they carry one pinch that a re-siting pass has to "
        "respect." % (HINGE_SHARE * 100),
        "",
        "`loops` is the places graph's independent cycles per place - ways *round* "
        "the map rather than ways through. It is **bold** below %.2f, the "
        "boundary a map has to clear to be called open on top of having no "
        "hinges: the hinge count measures cut vertices, and a ring has none "
        "while playing as a corridor, so hinge-free and loop-poor at once is "
        "read as linear." % OPEN_LOOP_DENSITY,
        "",
        "**`kind` does not predict whether a map can afford the layout family.** "
        "That is a separate measurement - the worst stage advance of each walk "
        "round the ladder against the map's own worst leg - and it disagrees with "
        "this column in both directions: drycanal_coop is `open` here and pays "
        "4.79x on 20 of its 22 walks, the sharpest corridor measured, while "
        "tell_coop is `open` here and pays nothing. `docs/permute.md` §3c is the "
        "table, and `tools/make-presets.sh` gates on the measurement rather than "
        "on this label.",
    ]
    return "\n".join(out)
