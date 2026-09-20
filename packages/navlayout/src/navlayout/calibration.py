"""What the shipped maps actually do, measured rather than assumed.

`docs/spawns-and-objectives.md` §4 pinned two numbers off ministry_coop stage 1
— a ~1900 u attacker approach and defenders at ~215 u, effectively on the
objective — and warned that any placement method should be pointed at a shipped
object before it is trusted. This is that warning turned into a distribution:
every stage of every surveyed map with its own nav mesh, measured in **path**
distance over the engine's graph, which is the unit the generator selects on.

Two exclusions, both of which change the answer:

* **Borrowed nav meshes.** A map that ships no `.nav` and names another map's in
  its cpsetup is playing on the base map's connectivity, so its stage distances
  are measured against a graph that is not its geometry.
* **Unreachable stages.** A stage whose objective the attacker cannot path to
  contributes no approach length, and averaging `inf` into a convention is how a
  calibration quietly becomes a fiction.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

import numpy as np

from .check import TEAM_INSURGENT, TEAM_SECURITY, _zone_areas
from .graph import NavGraph
from .objectives import resolve
from .survey import CpSetup, Survey


@dataclass
class Band:
    """A calibrated range: what the shipped maps do, and how widely they vary."""

    n: int
    median: float
    p25: float
    p75: float
    p05: float
    p95: float

    @classmethod
    def of(cls, values: list[float]) -> "Band":
        vals = [v for v in values if np.isfinite(v)]
        if not vals:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0)
        a = np.asarray(vals, dtype=np.float64)
        return cls(
            n=len(vals),
            median=float(np.median(a)),
            p25=float(np.percentile(a, 25)),
            p75=float(np.percentile(a, 75)),
            p05=float(np.percentile(a, 5)),
            p95=float(np.percentile(a, 95)),
        )

    def contains(self, v: float, *, wide: bool = False) -> bool:
        lo, hi = (self.p05, self.p95) if wide else (self.p25, self.p75)
        return bool(np.isfinite(v) and lo <= v <= hi)

    def __str__(self) -> str:
        return (f"n={self.n:4d}  p05 {self.p05:7.0f}  p25 {self.p25:7.0f}  "
                f"median {self.median:7.0f}  p75 {self.p75:7.0f}  p95 {self.p95:7.0f}")


@dataclass
class Conventions:
    """The shipped layout conventions, as bands over path distance."""

    attacker_approach: Band       # attacker stage spawn -> that stage's objective
    defender_distance: Band       # defender stage spawn -> that stage's objective
    objective_spacing: Band       # objective i -> objective i+1
    maps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "maps": self.maps,
            "attacker_approach": asdict(self.attacker_approach),
            "defender_distance": asdict(self.defender_distance),
            "objective_spacing": asdict(self.objective_spacing),
        }


def measure_map(
    survey: Survey, setup: CpSetup, *, graph: NavGraph | None = None
) -> tuple[list[float], list[float], list[float]]:
    """One map's (approaches, defender distances, spacings), in path units."""
    graph = graph or NavGraph.build(survey)
    atk_team = setup.attacking_team
    def_team = TEAM_INSURGENT if atk_team == TEAM_SECURITY else TEAM_SECURITY
    objs = resolve(survey, setup.chain)

    approaches: list[float] = []
    defences: list[float] = []
    spacing: list[float] = []

    for obj in objs:
        zone = setup.zone_for(obj.order)
        if not zone or not obj.areas:
            continue
        atk = _zone_areas(survey, zone, atk_team)
        dfn = _zone_areas(survey, zone, def_team)
        if atk:
            d = graph.distances(atk)
            v = min(d[a] for a in obj.areas)
            if np.isfinite(v):
                approaches.append(float(v))
        if dfn:
            d = graph.distances(dfn)
            v = min(d[a] for a in obj.areas)
            if np.isfinite(v):
                defences.append(float(v))

    for a, b in zip(objs, objs[1:]):
        if not a.areas or not b.areas:
            continue
        d = graph.distances(a.areas)
        v = min(d[i] for i in b.areas)
        if np.isfinite(v):
            spacing.append(float(v))

    return approaches, defences, spacing


def calibrate(pairs: list[tuple[Survey, CpSetup]]) -> Conventions:
    """Fold a corpus into the bands the generator selects against."""
    approaches: list[float] = []
    defences: list[float] = []
    spacing: list[float] = []
    used: list[str] = []

    for survey, setup in pairs:
        if setup.navfile and setup.navfile != survey.map:
            continue                      # borrowed mesh: not this map's geometry
        a, d, s = measure_map(survey, setup)
        if not a:
            continue
        approaches += a
        defences += d
        spacing += s
        used.append(survey.map)

    return Conventions(
        attacker_approach=Band.of(approaches),
        defender_distance=Band.of(defences),
        objective_spacing=Band.of(spacing),
        maps=sorted(used),
    )


def report(c: Conventions) -> str:
    return "\n".join([
        f"calibrated over {len(c.maps)} maps with their own nav mesh",
        f"  {', '.join(c.maps)}",
        "",
        "path distance over the engine's graph, engine units:",
        f"  attacker approach   {c.attacker_approach}",
        f"  defender distance   {c.defender_distance}",
        f"  objective spacing   {c.objective_spacing}",
    ])
