"""PLAN.md step B1 — the kill switch.

One question, asked before any siting code exists, because a wrong answer makes
everything downstream worthless:

    **Dijkstra from ministry_coop's shipped security spawn must reach all six
    shipped objectives.**

Over the parsed `.nav` it reaches 533 of 3,102 areas and cannot reach objectives
2-6 (`docs/spawns-and-objectives.md` §4). That is not a subtle bias, it is a map
people have played for a decade coming back as unplayable, and it is what the
whole survey-in-game pivot is for. If the surveyed graph does the same thing,
the survey is wrong too and nothing built on it means anything.

The second half is calibration rather than pass/fail: the shipped distances are
the convention any generated layout is copying, so they are measured here in the
same units the generator will emit.

    | shipped stage 1        | to cache A |
    |------------------------|------------|
    | security spawnzone_1   | 1900 u     |
    | insurgent spawnzone_1  |  215 u     |

Those two figures in `docs/spawns-and-objectives.md` §4 are straight-line
distances between origins. Path distance over the nav graph is the thing the
generator actually has to select on, and it is necessarily longer, so both are
reported side by side: the straight line reproduces the documented number and
proves the entities were identified correctly, and the path distance is what
`presets/<map>.cfg` will be scored against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .graph import NavGraph
from .objectives import Objective, resolve
from .survey import CpSetup, Entity, Survey


@dataclass
class ObjectiveReach:
    """One objective, as seen from one team's stage spawn."""

    order: int                  # 1-based position in the chain
    name: str                   # control-point targetname
    kind: str                   # cache | capture | marker
    area: int | None            # representative area, z-aware
    areas: list[int]            # every area the objective covers
    engine_area: int | None     # what GetNearestNavArea said about the marker
    attacker_zone: str
    attacker_areas: list[int]
    attacker_path: float        # nav-graph distance, inf if unreachable
    attacker_line: float        # straight line between origins
    defender_zone: str
    defender_areas: list[int]
    defender_path: float
    defender_line: float

    @property
    def reached(self) -> bool:
        return np.isfinite(self.attacker_path)


@dataclass
class CheckResult:
    map: str
    enumeration: str
    whole_mesh: bool
    area_count: int
    edge_count: int
    corner_offsets: bool
    components: int
    component_sizes: list[int]
    attacking_team: int
    stage1_zone: str
    reachable_from_spawn: int
    objectives: list[ObjectiveReach] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def all_reached(self) -> bool:
        return bool(self.objectives) and all(o.reached for o in self.objectives)

    @property
    def passed(self) -> bool:
        return self.all_reached


# Team numbers as the entity lump stores them.
TEAM_SECURITY = 2
TEAM_INSURGENT = 3


def _zone_areas(survey: Survey, name: str, team: int) -> list[int]:
    """Every area a stage's spawn actually puts a player in.

    The `ins_spawnpoint` entities inside the zone volume, not the volume's own
    origin. Insurgent `spawnzone_1` on ministry_coop is a 1882x2050 slab and its
    origin is the centre of that slab, which can be inside a wall and is in any
    case not where anybody spawns; the 56 points inside it are.

    Several volumes can share one targetname - ministry has four `spawnzone_2`
    for team 2 - and the game picks among them, so this is multi-source over all
    of them. Falls back to the volume origins when a zone owns no points, which
    is what the volume-only classes look like.
    """
    points = survey.points_in_zone(name, team)
    areas = {p.area for p in points if p.area is not None}
    if areas:
        return sorted(areas)
    return [z.area for z in survey.spawn_zone(name, team) if z.area is not None]


def _zone_origins(survey: Survey, name: str, team: int) -> list[np.ndarray]:
    """The positions the straight-line figures are measured from.

    Deliberately the *volume* origins, not the spawn points: that is what
    `docs/spawns-and-objectives.md` §4 measured to get 1900 u and 215 u, and the
    point of reporting it is to reproduce a number that is already written down.
    """
    return [z.origin for z in survey.spawn_zone(name, team)]


def _nearest(graph: NavGraph, sources: list[int], targets: list[int]) -> float:
    """Shortest path from any source area to any target area."""
    if not sources or not targets:
        return float("inf")
    d = graph.distances(sources)
    return float(min(d[t] for t in targets))


def _line(a: list[np.ndarray], b: np.ndarray) -> float:
    """Shortest straight-line distance from any of `a` to `b`."""
    if not a:
        return float("inf")
    return float(min(np.linalg.norm(np.asarray(o) - b) for o in a))


def check(
    survey: Survey,
    setup: CpSetup,
    *,
    attacking_team: int | None = None,
    graph: NavGraph | None = None,
) -> CheckResult:
    """Run the step B1 check over one survey.

    `setup` is the map's own `maps/<name>.txt`: the objective chain, and the
    spawn zone each stage uses for both teams. That pairing is what makes the
    shipped distances a convention rather than a coincidence, and it has to be
    read rather than guessed - only some maps call their zones `spawnzone_N`.
    """
    graph = graph or NavGraph.build(survey)
    chain = setup.chain
    attacking_team = setup.attacking_team if attacking_team is None else attacking_team
    defending_team = TEAM_INSURGENT if attacking_team == TEAM_SECURITY else TEAM_SECURITY

    n_comp, labels = graph.components()
    sizes = sorted(np.bincount(labels).tolist(), reverse=True) if graph.n else []

    resolved = resolve(survey, chain)
    notes: list[str] = []

    for obj in resolved:
        if obj.note:
            notes.append(f"objective {obj.order} '{obj.name}': {obj.note}")
        if obj.resnapped:
            eng = obj.engine_area
            eng_z = survey.areas[eng].center[2] if eng is not None else float("nan")
            our_z = survey.areas[obj.area].center[2]
            notes.append(
                f"objective {obj.order} '{obj.name}' re-snapped {eng} (z={eng_z:.0f}) "
                f"-> {obj.area} (z={our_z:.0f}): the marker is not the objective, "
                f"the {obj.kind} is"
            )

    # Stage 1 is what "reachable from the spawn" is measured from: it is the
    # only spawn the attacker actually starts the map in.
    zone1 = setup.zone_for(1)
    stage1 = _zone_areas(survey, zone1, attacking_team) if zone1 else []
    if not stage1:
        notes.append(
            f"no attacker spawn zone for stage 1 ({zone1!r}) - reachability unmeasured"
        )
    reachable = int(graph.reachable(stage1).sum()) if stage1 else 0

    named = {name for name in chain}
    for name in named - set(survey.control_points()):
        notes.append(f"'{name}' is in the chain but not on the map")

    objectives: list[ObjectiveReach] = []
    for obj in resolved:
        order = obj.order
        zone = setup.zone_for(order)
        if zone is None:
            notes.append(f"objective {order}: the cpsetup lists no spawn zone for this stage")
            zone = ""
        atk_areas = _zone_areas(survey, zone, attacking_team) if zone else []
        def_areas = _zone_areas(survey, zone, defending_team) if zone else []
        if zone and not atk_areas:
            notes.append(f"objective {order}: no attacker {zone}")
        if zone and not def_areas:
            notes.append(f"objective {order}: no defender {zone}")

        # An objective that is a *volume* is reached when you reach any part of
        # it. Measuring to one representative area would overstate the approach
        # by however far it happens to be across the room.
        atk_path = _nearest(graph, atk_areas, obj.areas)
        def_path = _nearest(graph, def_areas, obj.areas)
        if not obj.areas:
            notes.append(f"objective {order} '{obj.name}' snapped to no nav area")

        objectives.append(
            ObjectiveReach(
                order=order,
                name=obj.name,
                kind=obj.kind,
                area=obj.area,
                areas=obj.areas,
                engine_area=obj.engine_area,
                attacker_zone=zone,
                attacker_areas=atk_areas,
                attacker_path=atk_path,
                attacker_line=_line(_zone_origins(survey, zone, attacking_team), obj.marker.origin),
                defender_zone=zone,
                defender_areas=def_areas,
                defender_path=def_path,
                defender_line=_line(_zone_origins(survey, zone, defending_team), obj.marker.origin),
            )
        )

    if not survey.whole_mesh:
        notes.append(
            f"enumeration was '{survey.enumeration}', not TheNavAreas: "
            "counts are of the reachable component, not of the map"
        )
    if not survey.corner_offsets:
        notes.append("corner offsets were not found: footprints are missing")

    return CheckResult(
        map=survey.map,
        enumeration=survey.enumeration,
        whole_mesh=survey.whole_mesh,
        area_count=len(survey),
        edge_count=survey.edge_count,
        corner_offsets=survey.corner_offsets,
        components=int(n_comp),
        component_sizes=sizes[:6],
        attacking_team=attacking_team,
        stage1_zone=zone1 or "",
        reachable_from_spawn=reachable,
        objectives=objectives,
        notes=notes,
    )


def report(result: CheckResult) -> str:
    """The check as text. Written to be read, not parsed."""
    n = result.area_count
    pct = 100.0 * result.reachable_from_spawn / n if n else 0.0
    out = [
        f"map            {result.map}",
        f"enumeration    {result.enumeration}"
        + ("" if result.whole_mesh else "   <- not the whole mesh"),
        f"areas          {n}",
        f"edges          {result.edge_count}",
        f"footprints     {'yes' if result.corner_offsets else 'NO - corner probe failed'}",
        f"components     {result.components}  sizes {result.component_sizes}",
        f"reachable      {result.reachable_from_spawn} / {n} areas from "
        f"{result.stage1_zone or '(no stage-1 zone)'}  ({pct:.1f}%)",
        "",
        "objective chain, attacker path / straight line, defender path / straight line:",
        "",
        f"  {'#':>2}  {'name':<14} {'kind':<8} {'areas':>5}  {'atk path':>9} {'atk line':>9}"
        f"  {'def path':>9} {'def line':>9}",
    ]

    def fmt(v: float) -> str:
        return "unreachable" if not np.isfinite(v) else f"{v:9.0f}"

    for o in result.objectives:
        out.append(
            f"  {o.order:>2}  {o.name:<14} {o.kind:<8} {len(o.areas):>5}  "
            f"{fmt(o.attacker_path)} {o.attacker_line:9.0f}  "
            f"{fmt(o.defender_path)} {o.defender_line:9.0f}"
        )

    out.append("")
    if result.all_reached:
        out.append(f"PASS  all {len(result.objectives)} objectives are reachable from the shipped spawn")
    else:
        missed = [o.order for o in result.objectives if not o.reached]
        out.append(
            f"FAIL  objectives {missed} are unreachable from the shipped spawn. "
            "The survey is wrong; nothing downstream of it means anything."
        )

    for note in result.notes:
        out.append(f"note  {note}")
    return "\n".join(out)
