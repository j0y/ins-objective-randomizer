"""PLAN.md step B2 — openness: which maps are worth re-routing at all.

The shipped campaign maps are corridors. They play forward and they play
backward, and that is most of the variation they have. Moving an objective on a
corridor produces a different corridor. Moving one on a map that is genuinely
open — a compound, a street that connects more than two ways — produces a
different map. This module is how that difference is measured rather than
asserted.

Given an attacker spawn `A` and a site `O`, on the surveyed graph:

| metric            | definition                                          | reads as |
|-------------------|-----------------------------------------------------|----------|
| route count       | area-disjoint routes within a length budget          | how many ways in there actually are |
| detour ratio      | length of route 2 / route 1                          | whether the alternative is viable; ~1.0 is genuinely parallel, 2.0 means nobody takes it |
| chokepoints       | minimum **vertex** cut between A and O               | 1 is a corridor, >=3 is a compound |
| corridor breadth  | narrow dimension of the areas along each route       | flanking room, or a hallway |
| layering          | z-spread and indoor fraction of the reachable set    | rooftops and interiors, or one ground plane |

Two decisions worth stating, because both could reasonably have gone the other
way:

**The cut is over vertices, not edges.** A chokepoint is a *place* — a doorway,
a stairwell — and the nav mesh represents a place as an area. An edge cut counts
the connections between two areas, which on a wide-open plaza is large for
reasons that have nothing to do with whether the plaza is a chokepoint.

**Routes are area-disjoint, not edge-disjoint.** Two paths that squeeze through
the same doorway and diverge afterwards are one route, and only a vertex-disjoint
constraint says so.

Calibration is the point, not the formula: the 13 shipped `_coop` maps are 13
labelled examples of a corridor, so if they do not come out low and the open
reworkings high, the metric is wrong rather than the maps.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra, maximum_flow
from scipy.spatial import cKDTree

from .check import TEAM_INSURGENT, TEAM_SECURITY, _zone_areas
from .graph import NavGraph
from .objectives import Objective, resolve
from .survey import CpSetup, Survey

# A second route is only a route if people would take it. Beyond this multiple
# of the shortest, an "alternative" is a scenic tour that nobody walks, and
# counting it as a way in overstates how open a map is.
ROUTE_BUDGET = 1.6

# How far apart two routes have to be before they are two routes.
#
# Area-disjointness alone is not enough and the first run of this metric said so:
# every objective on ministry_coop came back with the maximum route count,
# because a nav mesh subdivides a hallway into several areas across its width and
# two paths running side by side down the same hallway are area-disjoint. They
# are not two ways in. Separating routes by a distance instead - about a room's
# width - asks the question that was meant: does this map offer a different way
# through the building, or the same way with more lanes.
ROUTE_SEPARATION = 256.0

# Capacity for something a cut is not allowed to sever: the spawn itself, the
# objective, and the direction-preserving half of every split node.
UNCUTTABLE = 1 << 20

# How many candidate sites the variant-capacity sample looks at. Every one costs
# a Dijkstra, so this is the knob between a stable number and a fast one.
CANDIDATE_SAMPLE = 24


@dataclass
class SiteMetrics:
    """Openness of one site, seen from one attacker spawn."""

    order: int
    name: str
    kind: str
    reachable: bool
    length: float                  # shortest approach, engine units
    routes: int                    # area-disjoint routes within the budget
    detour: float                  # route 2 / route 1, inf when there is no route 2
    chokepoints: int               # minimum vertex cut
    breadth: float                 # median narrow dimension along the routes
    route_areas: list[int] = field(default_factory=list)


@dataclass
class MapOpenness:
    """Openness of a whole map: the sites it ships, plus what it is made of."""

    map: str
    enumeration: str
    whole_mesh: bool
    areas: int
    reachable: int
    sites: list[SiteMetrics]
    z_spread: float                # p95 - p05 of reachable area centre z
    indoor_fraction: float
    hull_fraction: float
    median_breadth: float
    variant_capacity: float        # mean pairwise route dissimilarity over sampled sites
    navfile: str | None = None     # the mesh this map runs on, if not its own
    note: str = ""

    @property
    def borrowed_nav(self) -> bool:
        """Whether this map plays on another map's nav mesh.

        Five subscribed maps ship no `.nav` and name one in their cpsetup
        instead - `"navfile" "tell"`. The engine loads that file, so the graph
        surveyed here is the *base* map's connectivity, and every number in this
        row describes the base map rather than the reworking.

        Measured, not assumed: `tell_open_coop`'s surveyed mesh matches
        `tell.nav` on 4,391 of 4,391 areas to within 0.01 u, and it scores 0.211
        against `tell_coop`'s 0.209. That is not two similar maps, it is one
        mesh read twice.
        """
        return bool(self.navfile) and self.navfile != self.map

    # ── the aggregate the corpus is ranked on ────────────────────────

    @property
    def median_routes(self) -> float:
        vals = [s.routes for s in self.sites if s.reachable]
        return statistics.median(vals) if vals else 0.0

    @property
    def median_chokepoints(self) -> float:
        vals = [s.chokepoints for s in self.sites if s.reachable]
        return statistics.median(vals) if vals else 0.0

    @property
    def median_detour(self) -> float:
        vals = [s.detour for s in self.sites if s.reachable and np.isfinite(s.detour)]
        return statistics.median(vals) if vals else float("inf")

    @property
    def corridor_only(self) -> bool:
        """Every shipped site has exactly one way in.

        `PLAN.md` B2 asks for this to be *reported* rather than papered over: a
        map like this admits micro-variants only, and saying so is a result.
        """
        return bool(self.sites) and all(
            s.chokepoints <= 1 for s in self.sites if s.reachable
        )

    @property
    def score(self) -> float:
        """A single 0-1 number, for ranking a corpus.

        Four normalised terms, equally weighted except that the cut counts
        double. That is not a tuned model and does not pretend to be one - it is
        a summary of numbers that are each meaningful on their own, and the
        columns are printed beside it so a surprising rank can be read rather
        than trusted. The cut is doubled because it is the one term that
        separates a compound from a corridor on its own.
        """
        cut = _norm(self.median_chokepoints, 1.0, 4.0)
        routes = _norm(self.median_routes, 1.0, 3.0)
        detour = 0.0 if not np.isfinite(self.median_detour) else _norm(
            2.0 - min(self.median_detour, 2.0), 0.4, 1.0
        )
        space = 0.5 * _norm(self.median_breadth, 48.0, 256.0) + 0.5 * _norm(
            self.variant_capacity, 0.3, 0.85
        )
        return (2.0 * cut + routes + detour + space) / 5.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["median_routes"] = self.median_routes
        d["median_chokepoints"] = self.median_chokepoints
        d["median_detour"] = self.median_detour
        d["corridor_only"] = self.corridor_only
        d["borrowed_nav"] = self.borrowed_nav
        d["score"] = self.score
        for s in d["sites"]:
            s.pop("route_areas", None)
        return d


def _norm(v: float, lo: float, hi: float) -> float:
    if not np.isfinite(v) or hi <= lo:
        return 0.0
    return float(min(max((v - lo) / (hi - lo), 0.0), 1.0))


# ── routes ───────────────────────────────────────────────────────────

def disjoint_routes(
    graph: NavGraph,
    sources: list[int],
    targets: list[int],
    *,
    k: int = 4,
    budget: float = ROUTE_BUDGET,
    separation: float = ROUTE_SEPARATION,
) -> list[list[int]]:
    """Up to `k` genuinely different routes from any source to any target.

    Greedy: take the shortest, forbid everything within `separation` of it, take
    the shortest again. Greedy routing is not optimal - it can block a pair of
    routes that a joint solution would have found - but it is the *honest*
    direction to be wrong in here, because it under-counts rather than over-
    counts how open a map is, and this metric exists to decide whether a map is
    worth the trouble.

    Endpoints are exempt: every route has to start at a spawn and end at the
    objective, and those are the same places for all of them.
    """
    # Every route starts at the same spawn and ends at the same objective, so
    # near both ends they all run together. Blocking that convergence zone does
    # not separate routes, it deletes them: with a plain radius block, route 2
    # could not get out of the spawn or in to the objective and the first run of
    # this metric reported exactly one route on every map in the corpus.
    tree = cKDTree(graph.centers) if separation > 0 else None
    exempt = set(sources) | set(targets)
    if tree is not None:
        ends = graph.centers[list(exempt)]
        for group in tree.query_ball_point(ends, r=separation):
            exempt.update(group)
    blocked: set[int] = set()
    routes: list[list[int]] = []
    first_len: float | None = None

    # A super-source keeps this one Dijkstra rather than one per spawn area.
    n = graph.n
    for _ in range(k):
        mask = np.ones(n, dtype=bool)
        if blocked:
            mask[list(blocked)] = False
        sub = graph.without(blocked) if blocked else graph.adj

        live_sources = [s for s in sources if mask[s]]
        live_targets = [t for t in targets if mask[t]]
        if not live_sources or not live_targets:
            break

        dist, pred = dijkstra(
            sub, directed=True, indices=live_sources, return_predecessors=True
        )
        dist = np.atleast_2d(dist)
        pred = np.atleast_2d(pred)

        best = None
        for row in range(dist.shape[0]):
            for t in live_targets:
                if np.isfinite(dist[row, t]) and (best is None or dist[row, t] < best[0]):
                    best = (float(dist[row, t]), row, t)
        if best is None:
            break

        length, row, target = best
        if first_len is None:
            first_len = length
        elif length > first_len * budget:
            break

        path = [target]
        while path[-1] != live_sources[row]:
            nxt = int(pred[row, path[-1]])
            if nxt < 0:
                path = []
                break
            path.append(nxt)
        if not path:
            break
        path.reverse()

        routes.append(path)
        if tree is None:
            blocked.update(a for a in path if a not in exempt)
        else:
            near = tree.query_ball_point(graph.centers[path], r=separation)
            blocked.update(a for group in near for a in group if a not in exempt)

    return routes


# ── the cut ──────────────────────────────────────────────────────────

def min_vertex_cut(graph: NavGraph, sources: list[int], targets: list[int]) -> int:
    """How many areas have to be removed to keep the attacker off the objective.

    Menger's theorem in the form that matters here: the minimum vertex cut
    equals the maximum number of internally vertex-disjoint paths. So this is
    also the *true* route count, where `disjoint_routes` is a greedy lower bound
    that additionally insists a route be short enough to be worth walking.

    Built by node splitting - every area `v` becomes `v_in -> v_out` with
    capacity 1 - so a unit of flow is an area rather than a connection. Sources,
    targets and the super-terminals are uncuttable.
    """
    if not sources or not targets:
        return 0
    src = set(sources)
    dst = set(targets)
    if src & dst:
        return 0                        # the spawn is on the objective

    n = graph.n
    # v_in = v, v_out = v + n, then two super-terminals.
    S, T = 2 * n, 2 * n + 1
    size = 2 * n + 2

    rows: list[int] = []
    cols: list[int] = []
    caps: list[int] = []

    node_cap = np.ones(n, dtype=np.int64)
    for v in src | dst:
        node_cap[v] = UNCUTTABLE
    rows.extend(range(n))
    cols.extend(range(n, 2 * n))
    caps.extend(node_cap.tolist())

    adj = graph.adj.tocoo()
    rows.extend((adj.row + n).tolist())
    cols.extend(adj.col.tolist())
    caps.extend([UNCUTTABLE] * adj.nnz)

    for v in src:
        rows.append(S); cols.append(v); caps.append(UNCUTTABLE)
    for v in dst:
        rows.append(v + n); cols.append(T); caps.append(UNCUTTABLE)

    flow = maximum_flow(
        csr_matrix((np.asarray(caps, dtype=np.int32), (rows, cols)), shape=(size, size)),
        S,
        T,
    )
    # A cut at or above UNCUTTABLE means the only way to separate them is to
    # remove a spawn or the objective itself, which is not a chokepoint count.
    value = int(flow.flow_value)
    return value if value < UNCUTTABLE else 0


# ── measurement ──────────────────────────────────────────────────────

def measure(
    survey: Survey,
    setup: CpSetup,
    *,
    attacking_team: int | None = None,
    routes: int = 4,
    graph: NavGraph | None = None,
    sample: int = CANDIDATE_SAMPLE,
    seed: int = 0,
) -> MapOpenness:
    """Openness of one map, over the sites it actually ships."""
    graph = graph or NavGraph.build(survey)
    attacking_team = setup.attacking_team if attacking_team is None else attacking_team
    objs = resolve(survey, setup.chain)

    zone1 = setup.zone_for(1)
    stage1 = _zone_areas(survey, zone1, attacking_team) if zone1 else []
    reach = graph.reachable(stage1) if stage1 else np.zeros(graph.n, dtype=bool)

    sites: list[SiteMetrics] = []
    for obj in objs:
        # A stage's own spawn if the map has one, else stage 1: the openness of
        # a site is a property of the approach to it, and every approach starts
        # somewhere the attacker actually is.
        zone = setup.zone_for(obj.order)
        spawn = (_zone_areas(survey, zone, attacking_team) if zone else []) or stage1
        sites.append(_measure_site(graph, survey, obj, spawn, k=routes))

    # Layering and breadth are properties of the map, so they are measured over
    # what the attacker can actually get to rather than over the whole mesh: an
    # unreachable rooftop is not a storey anybody plays on.
    zs = graph.centers[reach, 2] if reach.any() else graph.centers[:, 2]
    z_spread = float(np.percentile(zs, 95) - np.percentile(zs, 5)) if zs.size else 0.0
    indoor = float(graph.indoor[reach].mean()) if reach.any() else 0.0
    hull = float(graph.hull_ok[reach].mean()) if reach.any() else 0.0
    # "Width distribution of areas along each route", not over the whole map:
    # a mesh subdivides finely against walls, so the map-wide median is a
    # measure of how detailed the mesh is rather than of how wide the map is.
    site_breadths = [s.breadth for s in sites if s.reachable and s.breadth > 0]
    median_breadth = float(statistics.median(site_breadths)) if site_breadths else 0.0

    capacity, note = _variant_capacity(
        graph, survey, stage1, reach, sample=sample, seed=seed, k=routes
    )

    return MapOpenness(
        map=survey.map,
        enumeration=survey.enumeration,
        whole_mesh=survey.whole_mesh,
        areas=graph.n,
        reachable=int(reach.sum()),
        sites=sites,
        z_spread=z_spread,
        indoor_fraction=indoor,
        hull_fraction=hull,
        median_breadth=median_breadth,
        variant_capacity=capacity,
        navfile=setup.navfile,
        note=note,
    )


def _measure_site(
    graph: NavGraph,
    survey: Survey,
    obj: Objective,
    spawn: list[int],
    *,
    k: int,
) -> SiteMetrics:
    targets = obj.areas or ([obj.area] if obj.area is not None else [])
    if not spawn or not targets:
        return SiteMetrics(obj.order, obj.name, obj.kind, False,
                           float("inf"), 0, float("inf"), 0, 0.0)

    paths = disjoint_routes(graph, spawn, targets, k=k)
    if not paths:
        return SiteMetrics(obj.order, obj.name, obj.kind, False,
                           float("inf"), 0, float("inf"), 0, 0.0)

    lengths = [_path_length(graph, p) for p in paths]
    detour = lengths[1] / lengths[0] if len(lengths) > 1 and lengths[0] > 0 else float("inf")

    walked = sorted({a for p in paths for a in p})
    b = graph.breadth[walked]
    b = b[b > 0]

    return SiteMetrics(
        order=obj.order,
        name=obj.name,
        kind=obj.kind,
        reachable=True,
        length=lengths[0],
        routes=len(paths),
        detour=detour,
        chokepoints=min_vertex_cut(graph, spawn, targets),
        breadth=float(np.median(b)) if b.size else 0.0,
        route_areas=walked,
    )


def _path_length(graph: NavGraph, path: list[int]) -> float:
    return float(sum(graph.adj[path[i], path[i + 1]] for i in range(len(path) - 1)))


def _variant_capacity(
    graph: NavGraph,
    survey: Survey,
    spawn: list[int],
    reach: np.ndarray,
    *,
    sample: int,
    seed: int,
    k: int,
) -> tuple[float, str]:
    """How *different* are the layouts this map admits, not how good any one is.

    Sample plausible sites, take the areas each one's routes traverse, and
    measure mean pairwise Jaccard **dissimilarity**. Two layouts pushing players
    through the same 400 areas are one layout with the furniture moved, and that
    is what a low number here means.

    Sites are sampled far from the spawn and from each other rather than
    uniformly: a candidate 200 u from the spawn is not a layout, and twenty
    candidates in one room measure one placement twenty times.
    """
    if not spawn or not reach.any():
        return 0.0, "no attacker spawn - variant capacity unmeasured"

    dist = graph.distances(spawn)
    ok = reach & graph.hull_ok & ~graph.blocked & np.isfinite(dist)
    # An objective wants somewhere to stand, and a real approach to it.
    ok &= graph.breadth >= 64.0
    ok &= dist >= max(600.0, float(np.nanpercentile(dist[np.isfinite(dist)], 25)))
    pool = np.flatnonzero(ok)
    if pool.size < 2:
        return 0.0, "too few plausible candidate sites to measure variant capacity"

    rng = np.random.default_rng(seed)
    # Farthest-point sampling in world space, so the sample spans the map
    # instead of clustering wherever areas happen to be dense.
    chosen = [int(rng.choice(pool))]
    centers = graph.centers[pool]
    d = np.linalg.norm(centers - graph.centers[chosen[0]], axis=1)
    while len(chosen) < min(sample, pool.size):
        nxt = int(pool[int(d.argmax())])
        chosen.append(nxt)
        d = np.minimum(d, np.linalg.norm(centers - graph.centers[nxt], axis=1))

    sets: list[set[int]] = []
    for site in chosen:
        paths = disjoint_routes(graph, spawn, [site], k=k)
        if paths:
            sets.append({a for p in paths for a in p})
    if len(sets) < 2:
        return 0.0, "sampled sites produced no comparable routes"

    scores = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if not union:
                continue
            scores.append(1.0 - len(sets[i] & sets[j]) / len(union))
    return (float(statistics.fmean(scores)) if scores else 0.0), ""


# ── reporting ────────────────────────────────────────────────────────

def report(m: MapOpenness) -> str:
    out = [
        f"map              {m.map}",
        f"enumeration      {m.enumeration}"
        + ("" if m.whole_mesh else "   <- reachable component only"),
        f"areas            {m.areas}   reachable from spawn {m.reachable}",
        f"z spread         {m.z_spread:.0f} u   indoor {m.indoor_fraction:.0%}"
        f"   hull-valid {m.hull_fraction:.0%}",
        f"median breadth   {m.median_breadth:.0f} u",
        f"variant capacity {m.variant_capacity:.2f}   (0 = every layout is the same layout)",
    ]
    if m.borrowed_nav:
        out.append(
            f"BORROWED NAV     this map ships none and runs on {m.navfile}.nav, so every\n"
            f"                 number below is the *base* map's connectivity, not this "
            f"map's"
        )
    out += [
        "",
        f"  {'#':>2}  {'name':<14} {'kind':<8} {'approach':>9} {'routes':>7}"
        f" {'detour':>7} {'chokes':>7} {'breadth':>8}",
    ]
    for s in m.sites:
        if not s.reachable:
            out.append(f"  {s.order:>2}  {s.name:<14} {s.kind:<8}   unreachable")
            continue
        detour = "  -  " if not np.isfinite(s.detour) else f"{s.detour:7.2f}"
        out.append(
            f"  {s.order:>2}  {s.name:<14} {s.kind:<8} {s.length:9.0f} {s.routes:7d}"
            f" {detour:>7} {s.chokepoints:7d} {s.breadth:8.0f}"
        )
    out += [
        "",
        f"openness score   {m.score:.3f}"
        f"   (cut {m.median_chokepoints:.0f}, routes {m.median_routes:.0f})",
    ]
    if m.corridor_only:
        out.append(
            "CORRIDOR         every shipped site has one way in: this map admits "
            "micro-variants only"
        )
    if m.note:
        out.append(f"note             {m.note}")
    return "\n".join(out)


def corpus_table(rows: list[MapOpenness]) -> str:
    """The corpus as a markdown table, most open first."""
    rows = sorted(rows, key=lambda r: r.score, reverse=True)
    out = [
        "| map | nav | score | cut | routes | detour | breadth | variant | z spread | indoor | areas |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        detour = "-" if not np.isfinite(r.median_detour) else f"{r.median_detour:.2f}"
        nav = f"**{r.navfile}**" if r.borrowed_nav else "own"
        out.append(
            f"| {r.map} | {nav} | {r.score:.3f} | {r.median_chokepoints:.0f} | "
            f"{r.median_routes:.0f} | {detour} | {r.median_breadth:.0f} | "
            f"{r.variant_capacity:.2f} | {r.z_spread:.0f} | "
            f"{r.indoor_fraction:.0%} | {r.areas} |"
        )
    if any(r.borrowed_nav for r in rows):
        out += [
            "",
            "A **bold** nav column is a map that ships no `.nav` and names another "
            "map's in its cpsetup. The engine loads that file, so the row measures "
            "the base map's connectivity and not this map's - see "
            "`MapOpenness.borrowed_nav`.",
        ]
    return "\n".join(out)
