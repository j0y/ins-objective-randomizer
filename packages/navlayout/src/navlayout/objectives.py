"""What "objective *i* is here" actually means on the nav graph.

A checkpoint objective is three entities that are easy to confuse:

* `point_controlpoint` — the **marker**. It is what `maps/<name>.txt` names in
  the chain, and it is what the HUD points at. It is not where the fight is: the
  shipped convention puts it **+72 above** the cache, and the capture markers
  float higher still.
* `obj_weapon_cache` — the thing you blow up. Sits on the floor, and its nav
  area's centre z *is* the floor (cache A is at z=32; its area's centre is
  32.03).
* `trigger_capture_zone` — a brush **volume** you stand in. An objective that is
  a room is not a point, and pretending it is one throws away the part that
  decides whether two routes into it are different routes.

Snapping the marker is what goes wrong if you skip this. `GetNearestNavArea` is
called with `anyZ = true`, which is the right question for "what is under this
spawn point" and the wrong one for a marker hanging in a stairwell: on
ministry_coop it binds `cp3` to an area **135 u above** the objective, and the
defender path to it comes back as 5,660 u for a spawn 148 u away. Resolve the
marker to the thing it marks first, then snap that.

Nothing here overrides the engine. The survey's `area` is `GetNearestNavArea`'s
answer to its own question and is kept; this asks a different question of the
same exported data, and says so when the two disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .survey import CpSetup, Entity, Survey

# A control point sits +72 above its cache by the shipped convention, so a cache
# within this of the marker is that marker's cache. Generous enough to survive a
# map that rounded it differently, tight enough that two objectives in one room
# cannot swap.
CACHE_RADIUS = 160.0

# How far outside its capture volume a marker may sit and still belong to it.
# ministry_coop's cp4 is 31 u outside cap4 in y; cp3 and cp6 are inside theirs.
CAPTURE_SLACK = 256.0

# A player stands *on* a floor, so the area under a thing is at or a little below
# it. This much above still counts as the same storey - a cache on a crate, a
# marker rounded up - and much more than this is the floor above.
STOREY = 96.0


@dataclass
class Objective:
    """One objective in the chain, resolved onto the graph."""

    order: int
    name: str                      # the control point's targetname
    kind: str                      # "cache" | "capture" | "marker"
    marker: Entity
    anchor: Entity                 # the cache, the capture volume, or the marker
    area: int | None               # the single best area, z-aware
    areas: list[int] = field(default_factory=list)  # every area the objective covers
    engine_area: int | None = None  # what GetNearestNavArea said about the marker
    note: str = ""

    @property
    def origin(self) -> np.ndarray:
        return self.anchor.origin

    @property
    def resnapped(self) -> bool:
        """Whether asking the z-aware question moved the objective."""
        return self.area is not None and self.area != self.engine_area


class Snapper:
    """Z-aware area lookup over a survey's footprints.

    Built once per survey: the footprint arrays are what make this a couple of
    vector operations per query rather than a scan over 3,128 dataclasses.
    """

    def __init__(self, survey: Survey):
        self.survey = survey
        self.have_footprints = survey.corner_offsets
        self.center = survey.centers()
        if self.have_footprints:
            self.nw = np.array([a.nw for a in survey.areas])
            self.se = np.array([a.se for a in survey.areas])
        else:
            self.nw = self.se = None

    def covering(self, point: np.ndarray) -> np.ndarray:
        """Indices of areas whose footprint contains `point` in xy."""
        if not self.have_footprints:
            return np.zeros(0, dtype=int)
        m = (
            (self.nw[:, 0] <= point[0]) & (point[0] <= self.se[:, 0])
            & (self.nw[:, 1] <= point[1]) & (point[1] <= self.se[:, 1])
        )
        return np.flatnonzero(m)

    def overlapping(self, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
        """Indices of areas whose footprint overlaps a box in xy and sits in its z span.

        The z span is widened downward by one storey: an area whose centre is a
        little below the volume is the floor of that volume.
        """
        if not self.have_footprints:
            return np.zeros(0, dtype=int)
        m = (
            (self.nw[:, 0] <= hi[0]) & (lo[0] <= self.se[:, 0])
            & (self.nw[:, 1] <= hi[1]) & (lo[1] <= self.se[:, 1])
            & (self.center[:, 2] >= lo[2] - STOREY)
            & (self.center[:, 2] <= hi[2])
        )
        return np.flatnonzero(m)

    def snap(self, point: np.ndarray, fallback: int | None = None) -> int | None:
        """The area a thing at `point` is standing on.

        Prefers an area whose footprint contains the point and whose centre is
        on the same storey, nearest below. Falls back to the nearest area centre
        in 3D, and finally to whatever the engine said.
        """
        if not self.have_footprints:
            return fallback

        cand = self.covering(point)
        if cand.size:
            dz = self.center[cand, 2] - point[2]
            same_storey = cand[(dz <= STOREY) & (dz >= -4 * STOREY)]
            pool = same_storey if same_storey.size else cand
            return int(pool[np.abs(self.center[pool, 2] - point[2]).argmin()])

        # No footprint covers it - a marker over a stairwell, or off the mesh.
        d = np.linalg.norm(self.center - point, axis=1)
        nearest = int(d.argmin())
        return nearest if d[nearest] <= 512.0 else fallback


# A chain name does not have to identify one entity. `maps/<name>.txt` can
# create its own `point_controlpoint` beside the one the mapper baked into the
# BSP, and congress_coop does exactly that for all three of its caches - so the
# running map carries two entities called `cachepoint_g`, and the ranking below
# is which of them the chain means. Cache first because a cache is an anchor
# with a floor under it; a marker that pairs with nothing is the last resort.
KIND_RANK = {"cache": 0, "capture": 1, "marker": 2}


def capture_links(bsp: str | Path) -> dict[str, str]:
    """`trigger_capture_zone` targetname -> the control point it captures.

    **This is the engine's own pairing and geometry is only a guess at it.** A
    capture volume carries a `controlpoint` key naming its marker - every
    volume on every map in this corpus does - and that key, not proximity, is
    what the gamemode captures an objective through.

    Where the two disagree the layout breaks in a way nothing offline sees.
    gioconda_eron_mountains stacks cp9 and cp10 at one xy, 448 u apart in z,
    and cz10's volume is 529 u tall: it swallows cp9's marker, so "the volume
    this marker is inside" answered cz10 for both. `enter3_fwd` then moved
    cp9's marker to rung 1 and moved **cz10** with it, leaving cz9 - the volume
    the gamemode captures cp9 in - at the far end of the map. Reported off the
    server 2026-09-18: objective N could not be captured by standing on it, and
    the applier's read-back had already said as much, reporting cz10's rule as
    shadowed.

    Returns an empty mapping when the `.bsp` is not there to read, which is the
    case the geometry below still has to cover.
    """
    from bspscout.lumps import BspFile

    path = Path(bsp)
    if not path.exists():
        return {}
    try:
        text = BspFile(path).raw(0).decode("latin-1")
    except (OSError, ValueError, NotImplementedError):
        return {}
    out: dict[str, str] = {}
    for block in re.finditer(r"\{(.*?)\n\}", text, re.S):
        kv = dict(re.findall(r'"([^"]*)"\s+"([^"]*)"', block.group(1)))
        if kv.get("classname") == "trigger_capture_zone":
            name, cp = kv.get("targetname", ""), kv.get("controlpoint", "")
            if name and cp:
                out[name] = cp
    return out


def _marked(marker: Entity, caches: list[Entity], zones: list[Entity],
            snapper: Snapper,
            links: dict[str, str] | None = None) -> tuple[Entity, str, str, list[int], float]:
    """What one marker entity marks: its cache, its capture volume, or itself.

    Returns the anchor, its kind, a note, the areas a capture volume covers,
    and how far the marker sits from what it found - which is what tells two
    entities of the same name apart.
    """
    # A cache first: it is a point on the floor, and the +72 convention makes
    # the pairing unambiguous.
    near = [
        (float(np.linalg.norm(c.origin - marker.origin)), c) for c in caches
    ]
    near = [(d, c) for d, c in near if d <= CACHE_RADIUS]
    if near:
        d, cache = min(near, key=lambda t: t[0])
        return cache, "cache", "", [], d

    # Otherwise the capture volume the marker belongs to. The map says which
    # one outright where the `.bsp` could be read; see `capture_links`.
    named = [z for z in zones if links and links.get(z.target) == marker.target]
    if len(named) == 1:
        best = named[0]
        box = Survey.world_box(best)
        lo, hi = box if box is not None else (marker.origin, marker.origin)
        d = float(np.linalg.norm(np.maximum(np.maximum(lo - marker.origin,
                                                       marker.origin - hi), 0.0)))
        areas = [int(i) for i in snapper.overlapping(lo, hi)] if box is not None else []
        note = f"marker sits {d:.0f} u outside {best.target}" if d > 0 else ""
        return best, "capture", note, areas, d

    # Failing that, the one whose box it is inside, or nearest to if it sits
    # just outside - and the *smallest* of those where several contain it, since
    # a volume that holds two markers is claiming one of them by being big
    # rather than by being its. Only the tie-break is new; a marker inside one
    # volume resolves exactly as it did.
    best, best_d, best_vol = None, np.inf, np.inf
    for z in zones:
        box = Survey.world_box(z)
        if box is None:
            continue
        lo, hi = box
        d = float(np.linalg.norm(np.maximum(np.maximum(lo - marker.origin,
                                                       marker.origin - hi), 0.0)))
        vol = float(np.prod(np.maximum(hi - lo, 1.0)))
        if (d, vol) < (best_d, best_vol):
            best, best_d, best_vol = z, d, vol
    if best is not None and best_d <= CAPTURE_SLACK:
        note = f"marker sits {best_d:.0f} u outside {best.target}" if best_d > 0 else ""
        box = Survey.world_box(best)
        areas = [int(i) for i in snapper.overlapping(*box)] if box is not None else []
        return best, "capture", note, areas, best_d

    return (marker, "marker",
            "no cache or capture volume found - using the marker itself", [], np.inf)


def resolve(survey: Survey, chain: list[str],
            links: dict[str, str] | None = None) -> list[Objective]:
    """Resolve an objective chain onto the graph, marker by marker.

    `links` is `capture_links` of the map's own `.bsp` - the pairing the engine
    uses. A caller that has not read it can leave it out and the survey's own,
    if one was attached at load, is used instead.
    """
    links = links if links is not None else getattr(survey, "capture_links", None)
    snapper = Snapper(survey)
    caches = survey.by_class("obj_weapon_cache")
    zones = survey.by_class("trigger_capture_zone")

    named: dict[str, list[Entity]] = {}
    for e in survey.by_class("point_controlpoint"):
        named.setdefault(e.target, []).append(e)

    out: list[Objective] = []
    for order, name in enumerate(chain, start=1):
        markers = named.get(name, [])
        if not markers:
            continue

        # One name, possibly several entities: take the one that marks
        # something, nearest to what it marks. Keeping the last one the
        # enumeration happened to hand over is what put congress_coop's
        # cachepoint_g 1,696 u from cache_g - its cpsetup has the sign of the y
        # wrong - and an objective anchored on that decoy is a reversal
        # refused for a fault the map has and the layout does not.
        marked = [(_marked(m, caches, zones, snapper, links), m) for m in markers]
        (anchor, kind, note, areas, dist), marker = min(
            marked, key=lambda t: (KIND_RANK[t[0][1]], t[0][4]))

        if len(markers) > 1:
            others = [d for (_, _, _, _, d), m in marked if m is not marker]
            note = "; ".join(filter(None, [
                note,
                f"{len(markers)} entities are called {name}; took the one "
                f"{dist:.0f} u from {anchor.target}"
                + (f", against {min(others):.0f} u for the next" if others
                   and np.isfinite(min(others)) else " - the rest mark nothing"),
            ]))

        if areas:
            # The objective is a volume, and its representative area has to come
            # from inside it. Snapping the volume's *origin* independently does
            # not: a brush centre is a point in mid-air, and on cap3 the only
            # footprint covering it is the floor 400 u above.
            centers = snapper.center[areas]
            area = int(areas[int(np.linalg.norm(centers - anchor.origin, axis=1).argmin())])
        else:
            area = snapper.snap(anchor.origin, fallback=anchor.area)
            areas = [area] if area is not None else []

        out.append(
            Objective(
                order=order,
                name=name,
                kind=kind,
                marker=marker,
                anchor=anchor,
                area=area,
                areas=sorted(areas),
                engine_area=marker.area,
                note=note,
            )
        )
    return out


# ── the chain the running map can actually play ──────────────────────

def playable(setup: CpSetup, survey: Survey) -> tuple[CpSetup, list[str]]:
    """`setup` with the stages the loaded map has no entity for removed.

    **A cpsetup is what the mapper asked for; the survey is what the engine
    loaded.** Where the two disagree the engine wins, and four of the 125
    surveyed maps disagree: cobblestone names `cp7`, bombshelter `cp_g`,
    launch_control_coop_ws `cp_j`, and ins_kuwaiti_oilfields three cache points
    whose `obj_weapon_cache` blocks carry an empty `origin` and no marker to go
    with them. No `point_controlpoint` of those names exists in the running map,
    so there is no objective for the chain slot, and `resolve` has always
    skipped it.

    What had not followed the skip was the *count*. `build_rungs` builds a
    ladder of `len(objs) + 1` rungs while `dest_zone` reads `len(setup.chain)`
    for the same number, so on bombshelter the top rung asked for
    `spawnzone_7` - a name its `.txt` lists and its BSP does not have - and the
    guard in `cli._load_for_layout` counted cobblestone as eight objectives
    against seven zones and refused it outright, when what the map has is seven
    of each. Reconciling here makes the two counts one count.

    **Which zone a surviving stage inherits is the one real choice**, and the
    shipped convention answers it: a stage's defender zone stands on its own
    objective and its attacker zone on the one before (docs/reverse.md, and the
    ladder in `reverse`). Measured both ways over the four maps, the surviving
    stages taking the zones **in order** beats keeping each survivor's own index
    and letting the dropped stage take its zone with it:

        cobblestone    in order covers all 7 stages, 910 u defender / 823 u
                       attacker; by index covers 6 and 5 at 899 / 1,065
        kuwaiti        3 stages at 2,103 / 283; by index 2 stages at 4,849 /
                       4,431
        launch_control defender identical at 418; attacker 969 against 1,129 -
                       `cp_k` keeps `spawnzone_j`, whose seven attacker points
                       sit 413 u from `cp_i`, rather than `spawnzone_k`'s at
                       2,427 u
        bombshelter    identical - the name it drops is the last one

    which is what a `.txt` written from the top down would do anyway: the zone
    list is read by stage number, and dropping a stage renumbers the ones after
    it.

    Returns the reconciled setup and the names that were dropped, so a caller
    can say so rather than quietly playing a shorter map.
    """
    have = {e.target for e in survey.by_class("point_controlpoint") if e.target}
    keep = [name for name in setup.chain if name in have]
    if len(keep) == len(setup.chain):
        return setup, []
    dropped = [name for name in setup.chain if name not in have]
    return replace(setup, chain=keep, zones=setup.zones[:len(keep)]), dropped
