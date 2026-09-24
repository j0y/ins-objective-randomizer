"""Play a linear map backwards, using only positions the map already authored.

A checkpoint map's chain order lives in `maps/<name>.txt`, and the applier
cannot rewrite it - that is the one limit in §8 of `docs/layout-variants.md`
that survived the move to runtime. So a reversal cannot be done by reordering
the chain. It is done by **permuting the geometry underneath a chain that still
reads 1..N**: slot 1 gets slot N's ground, slot 2 gets slot N-1's, and the
round plays the corridor from the far end without the `.txt` being touched.

**The ladder, and why nothing has to be invented.** Measured on both linear
maps with a cpsetup on this machine, the shipped convention is exact and it is
what makes the permutation close:

    defender zone k sits *on* cp_k;  attacker zone k sits on cp_(k-1)

so the attackers respawn on the objective they just took. ministry_coop's
median path from a spawn point to its nearest objective: defender zones 869,
1388, 1240, 1435, -, 1373 u; attacker zones land on cp1, cp1, cp2, cp3, cp4, -.
market_coop repeats it across eight stages.

That gives a ladder of **rungs**, each a place the map authored as somewhere a
fight happens and two teams stand:

    rung 0 = the attacker entry (attacker zone 1)
    rung k = objective k, with the defender zone that guards it

Stock, stage j is fought at rung j with attackers at rung j-1. Reversed, stage
j is fought at rung N-j with attackers at rung N-j+1 - the same relation,
walked the other way. The attackers enter at rung N, which is the *last*
objective's ground, and the final stage's objective lands on rung 0, the
ground the attackers used to spawn on. Both ends are authored, so a reversal
needs no derived placement at all: every coordinate it emits is one the engine
already accepted on the stock map.

**Spawn points are re-sited onto their authored twins, not translated.** A
cluster of points was authored to fit the room it is in, so sliding stage 1's
seventeen attacker points onto rung 5 puts most of them in walls - which is the
four-server-restart lesson in §6 of `docs/spawns-and-objectives.md`, and it is
avoidable here. The destination rung already holds points the engine validated,
so an incoming point takes one of *those* coordinates. Rank by distance from
the cluster centre so an interior point stays interior. Nothing offline has to
predict the engine's hull test, because nothing offline chooses a new position.

**What still cannot be checked offline, and is therefore reported.** A capture
objective is a brush volume, and volumes translate but do not resize. Reversal
pairs slot 1 with slot N, which on both maps brings a capture volume up to
1154x1026x450 into a room that held a 124x77x34 cache, so it clips into
whatever is there. The one thing that can be measured is whether it still
covers walkable floor - an empty one is the `Failed finding CP area for N!` in
the server log - and `coverage` is that count per objective. Both maps come
through it: the tightest is ministry's cp6 -> cachepoint_a at 13 walkable
areas.

**Restricted areas end up guarding the wrong side.** An `ins_blockzone` holds
the attackers behind the current objective, and behind is now in front. They
are translated along with the defender zone they sit on so they at least follow
the fight, but a reversed layout wants them off - `tools/blockzones.py
--disable`, or the server they were built for, which does not use them.

**A reversal is one layout out of a family, and the rest cost nothing extra.**
Everything above is the case `stage j is fought at rung N-j`. Nothing in the
machinery needs that particular map: a layout is exactly an **injection from
the N stages into the N+1 rungs**, with the one rung no stage claims being the
ground the attackers arrive on. `Plan` is that injection, `stock_plan` and
`reverse_plan` are two of them, and `ring_plan` walks the ladder as a ring -
enter at any rung, go either way - which is `places.py`'s "randomise the
starting objective" for a map whose shape licenses it.

**What a randomised start costs, and why it is reported rather than refused.**
The rungs are a path, so a walk that starts in the middle of it has to come
back for the rungs it skipped: exactly one stage transition is then the length
of the whole corridor. Stock and the reversal are the only two ring walks that
avoid it, because they are the two that start at an end. So every layout
carries its per-stage **advance** - the path distance a stage's attackers cover
from their spawn rung to their objective - beside the stock advances the map was
authored around, and the wrap shows up as one large number rather than as a
silently bad round. That is a judgement about how a map plays, not a fact about
whether the engine will load it, so it warns; `max_advance` is there to turn it
into a refusal for a caller that wants one. The offline *refusals* stay what
they were - a capture volume covering no floor, a rung with nowhere to stand -
because those are the engine's answers, not opinions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .graph import NavGraph
from .objectives import STOREY, Objective, Snapper, resolve
from .survey import CpSetup, Entity, Survey

# How far apart two *filled-in* spawn points must sit. It governs only the
# coordinates the fill invents: an authored one is kept whatever its neighbour
# does, because the shipped maps put two of their own points as close as 40 u
# and precedent outranks a rule invented here. `tools/placespawns.py` needed
# `--avoid 96` before a repair round moved the rejection count at all: without
# it a rejected point is usually its own nearest candidate.
MIN_SEPARATION = 96.0

# ...and what it relaxes to when holding that line would leave a destination
# short of coordinates. A starved pool does not produce spawns that are spread
# out, it produces coincident ones: `_assign` cycles a short list and two points
# land on the same coordinate, a separation of zero. Measured on ministry_coop
# stage 6 - six fills rejected at 96 u, 11 coordinates for 16 points, 5 pairs
# on top of each other. The floor is the shipped maps' own spacing: nearest
# neighbour within a zone is 40 u at its closest and 58 u at p5.
FILL_SEPARATIONS = (MIN_SEPARATION, 64.0, 48.0)

# Below this many usable destination coordinates a stage has no defence worth
# the name, and the reversal is refused rather than emitted half-built. Stock
# attacker zones carry 16-17 points, defender zones 45-72.
#
# It is a floor on the coordinates a *move* invents, and only that. A stage
# that borrows the volume standing on its rung inherits the map's own points
# and moves none of them, so counting them there measures the map rather than
# the layout - and a workshop map is free to ship a zone with two points in it
# and play fine. See the borrow branch below.
MIN_POINTS = 6

# How far a control point's marker may sit from the nearest walkable area
# centre before the gamemode stops finding an area for it - `Failed finding CP
# area for N!` in the server log, and the objective then has no nav area to its
# name. Measured rather than chosen, on the two answers ministry_coop gives:
#
#   stock     cp3 169 u -> refused      cp4 138 u, cp6 135 u -> accepted
#   reversed  cp3 169 u, cp4 269 u -> refused      cp6 118 u -> accepted
#
# so the line is somewhere in (138, 169] and 150 is the round number inside it.
# This is a *marker* test and it applies to a capture objective only: a cache's
# marker is teleported after the gamemode has already set its control points
# up, so the coordinate that decides this for a cache is the stock one, which a
# layout cannot change.
#
# **No longer a gate.** A marker that moves is now re-sited whether or not it
# would have passed this, because inferno_new_v1's family showed the threshold
# does not separate what the engine accepts from what it refuses (docs/permute.md
# §6). What is left of it is reporting: it is still the number the warnings
# below quote, and it still names the markers a layout leaves standing where the
# stock map put them, which are the map's own problem rather than the layout's.
MARKER_GAP = 150.0

# Where a re-sited marker is put above the floor it was given. The shipped
# convention for a cache marker, and well inside MARKER_GAP.
MARKER_LIFT = 72.0

# How far from a rung's own floor a cache may be stood to find ground the player
# hull fits on. Measured over the rungs of four maps - every one of them has a
# usable floor within one storey, and the furthest any rung has to reach is
# 258 u (tell_coop; ministry_coop 177, de_vertigo_coop 150, inferno_new_v1 50).
# 384 is above all of them and still inside the room the rung is in, so it
# cannot quietly move a cache to the far side of a wall.
CACHE_STAND = 384.0

# Below this, a detour is a road bending rather than a round doubling back, and
# walks are ranked on their longest march instead. See `_path_cost`.
DETOUR_FLOOR = 2.0

# How close to a bar counts as being on it rather than over it.
#
# Every gate here relaxes to the stock walk's own number, so the common case of
# a bar being missed is a walk tied with the map it came from - and a tie is
# where floating point stops being an implementation detail. `rung_distances`
# runs a separate Dijkstra from each rung and sums the same edge weights in a
# different order, and addition is not associative, so the mesh's own symmetry
# comes back one to four ULPs apart. The reversal of a symmetric corridor is
# the mirror of the stock walk triple for triple and measures the identical
# detour; over the corpus 2026-09-19, eight maps had theirs refused for being
# worse than itself by 4.4e-16 - uprising_night_coop 5.662457337883959 against
# 5.662457337883958, launch_control_coop_ws 9.312212463404435 against
# ...434. 1e-9 is far under any difference a player could be told about and far
# over the noise: the nearest real margin in the corpus is
# gioconda_eron_darkscape's reversal at 3.1e-3.
GATE_TIE = 1e-9

# Under this, a step is not an advance: the stage is captured on arrival and the
# map has spent an objective without giving a fight for it. A player found this
# on cs_officeb3_coop_v1_5 `enter1_fwd` - objectives D and E were 155 u apart -
# and the cause is that `_path_cost` used to *reward* the step. Its last two
# terms are the longest edge and the total, so the shortest edge in the matrix
# is free money, and 2-opt seeks it: that map has three rungs in one cluster
# (0-3 155 u, 0-5 820 u, 3-5 929 u) and the optimiser pairs them up every time.
#
# The number is the shipped corpus's own answer. Measured over the 43 surveyed
# maps with a cpsetup, the shortest step a shipped chain asks of anyone:
#
#   gioconda_eron_mountains 0 u - two objectives in the same place
#   ---- 1,000 ----
#   siege_coop 1,258   tell_open_coop 1,295   market_open_coop 1,609 ...
#   median 3,007, p90 4,598
#
# So 42 of 43 mappers stay above 1,258 u and the one exception is a map that is
# broken in this exact way. 1,000 is the round number in the gap, and it is a
# *ranking* term rather than a refusal: on a map whose rungs genuinely sit on
# each other every walk pays it, the term is then constant, and the family is
# still the best walks available. `layout(min_advance=...)` is the refusal for a
# caller that wants one.
SHORT_ADVANCE = 1000.0

# The same question, asked of the ground instead of the walk.
#
# A step can be a long march on the mesh and still be no advance at all. On
# gioconda_eron_mountains `enter3_fwd`, played 2026-09-18, cachepoint_c stands
# 608 u across the floor from cp6 - 135 u below it - while the mesh path between
# them is 5,591 u, because the only way down is round the building. The walk
# gate read 5,591 u and passed it; the player read the next objective off the one
# they were standing on, and said so.
#
# **A floor above is a different place; the far side of a wall is not.** Measured
# straight, the tightest pair in the whole shipped corpus is ministry_coop's
# 497 u - and 496 u of that is vertical: its two objectives are stacked, 38 u
# apart across the ground, and nobody has ever complained about them. So the
# measure is the *horizontal* distance between two rung floors, and only where
# they are within `LEVEL` of each other in z; a step that changes level is two
# places whatever the plan view says.
#
# Under that rule the corpus is as clean as it is for the walk. Over the 45
# surveyed shipped chains, the tightest same-level consecutive pair:
#
#   gioconda_eron_mountains 0 u - the map that ships two objectives in one place
#   ---- 1,000 ----
#   gioconda_eron_agroprom 1,018   de_vertigo_coop 1,038   inferno_new_v1 1,062
#   district_coop 1,248 ... median 1,867
#
# So 44 of 45 mappers stay above 1,018 u and the one exception is the map this
# was reported on. `SHORT_ADVANCE` is the threshold for both measures: the two
# corpus floors - 1,258 u of walk, 1,018 u of floor - land on the same side of
# it.

# Two storeys. Within this much of each other in z, two objectives are on the
# same level and the ground between them is what separates them; beyond it one
# is above the other and the stairs are the distance. Ministry's stacked pair is
# 496 u apart in z, warehouse_coopv2u1's 280 and siege_coop's 199 - all of them
# shipped, none of them complained about - and the pair that was complained
# about is 135. See `SHORT_ADVANCE`.
LEVEL = 2 * STOREY

# How close two objectives may stand on one level before the second is not a
# place of its own. The corpus says a mapper never goes under 1,018 u; this
# corpus's *layouts* say 1,000 is more than can be asked for, and the gap
# between the two is where the number lands.
#
#   608 u  gioconda_eron_mountains `enter3_fwd` H -> I, reported by a player
#   ---- 700 ----
#   799 u  cs_officeb3_coop_v1_5 rungs 1-6, in all four of its layouts
#   941 u  siege_coop rungs 0-1 - its own entry and its own first objective
#   1,018 u  the tightest same-level step any of the 45 shipped chains takes
#
# Refusing everything under 1,000 costs cs_officeb3_coop_v1_5 all four of its
# layouts and siege_coop eight of its nine, for pairs no one has complained
# about; 700 keeps every one of those and still refuses the pair that was
# complained about. Below it the term is counted with the walk-ons, above it
# nothing is said.
SHORT_SEPARATION = 700.0

# How far a counter-attack may have to walk. **The zone a stage's defenders
# spawn in is also the one the counter-attack on the objective before it comes
# out of**: `CINSRules_Checkpoint::CounterWaveStarted(i)` calls
# `AdvanceSpawns(i, defenders)`, which enables cpsetup key i+1 - the next
# stage's zone - and that zone then stays live for the whole of the next stage.
# So on a map whose legs are long, a counter-attack from "the next objective"
# is bots who spend the counter-attack timer walking and never arrive.
#
# gioconda_eron_kordon is the map that shows the author knew it: every one of
# its eleven counter-attacks starts 2,418-4,088 u by path from the objective
# just taken, on legs of up to 12,016 u, because its zones are authored around
# the *previous* objective rather than their own.
#
# Measured on the ground a counter-attack actually comes out of - every point
# of the zone, not its middle - and in time, because the timer is the budget: a
# player runs at 170 u/s (`speed_run` in the playerclass script; `speed_sprint`
# 288 is stamina-limited), and on a one-minute counter-attack the slowest tenth
# of the wave, over the 759 stock stages of the maps above, needs:
#
#   p25 22 s   median 29 s   p75 35 s   p90 43 s
#
# while the gioconda maps ship stages at 40 s (kordon), 59 s (mountains), 74 s
# (agroprom_bef) and 103 s (garbage). **30 s is the budget** - what a typical
# shipped stage asks of its slowest bots, and the answer the server's operator
# gave for how long a player may be left waiting in a one-minute timer - so a
# stage whose slowest tenth would take longer is given ground that is nearer,
# and a small map never reaches it, which is why nothing here changes one.
#
# Where no authored ground qualifies a volume is moved onto the path forward
# from the objective just taken, centred half the budget out, and only
# coordinates inside the budget are handed to its points.
RUN_SPEED = 170.0
COUNTER_SECONDS = 30.0
COUNTER_REACH = COUNTER_SECONDS * RUN_SPEED
COUNTER_SLOWEST = 90
COUNTER_TARGET = COUNTER_REACH / 2

# How close to a rung's own floor counts as standing on it. Where the ladder
# runs out - rung 0 has no defender zone, rung N no attacker one - the only
# pool `dest_points` can offer is the *other* team's, and that team's zone can
# be enormous: inferno_new_v1's stage-6 defender volume spans 2,264 x 3,458 u,
# so its centroid sits 1,046 u off the rung it is supposed to stand for. Inside
# one bucket a placement is on the rung and the authored count decides; outside
# it, being on the rung wins. See `place_zone`.
ANCHOR_BUCKET = 128.0

# A `within` box is how the applier singles out one nameless entity: spawn
# points carry no targetname, so they are matched on the origin they have
# *before* the edit. Tight, because the point next to it must not match too.
WITHIN_SLOP = 2.0


@dataclass
class Plan:
    """Which rung each stage is fought at, and where the attackers enter.

    A layout is an injection from the N stages into the N+1 rungs. `rungs[j]` is
    where stage j is fought, 1-based - index 0 is unused so the list reads by
    stage number - and `entry` is the one rung no stage claims, which is the
    ground the attackers arrive on. Stage j's attackers spawn at the rung of
    stage j-1, and stage 1's at `entry`, because in stock play the attackers
    respawn on the objective they have just taken.
    """

    name: str
    rungs: list[int]           # rungs[j] for j in 1..N; rungs[0] is a placeholder
    entry: int
    note: str = ""

    def __len__(self) -> int:
        return len(self.rungs) - 1

    def at(self, stage: int) -> int:
        """The rung stage `stage` is fought at. Stage 0 is the attacker entry."""
        return self.entry if stage == 0 else self.rungs[stage]

    @property
    def order(self) -> list[int]:
        """The rungs in the order the round visits them, entry first."""
        return [self.entry] + self.rungs[1:]

    def valid(self, n: int) -> str:
        """Empty if this is an injection into 0..n, else what is wrong with it.

        A plan covering n stages always names n+1 rungs counting the entry, so
        distinctness and range are the whole of it - there is no way to leave a
        rung unaccounted for without failing one of the other two.
        """
        if len(self) != n:
            return f"plan covers {len(self)} stages, the chain has {n}"
        seen = self.order
        if len(set(seen)) != len(seen):
            return f"two stages share a rung: {seen}"
        if any(not 0 <= r <= n for r in seen):
            return f"a rung is outside 0..{n}: {seen}"
        return ""


def stock_plan(n: int) -> Plan:
    """The map as it shipped: stage j at rung j, attackers entering at rung 0."""
    return Plan("stock", [-1] + list(range(1, n + 1)), 0,
                "the map as it shipped - the control")


def reverse_plan(n: int) -> Plan:
    """Slot 1 fought where slot N was. `ring_plan(n, n, "backward")`."""
    return Plan("reversed", [-1] + [n - j for j in range(1, n + 1)], n,
                "the corridor from the far end: slot 1 is fought where slot N was, "
                "the attackers enter on the last objective's ground, and the old "
                "attacker spawn becomes the last objective")


def ring_plan(n: int, entry: int, direction: str = "forward",
              ring: list[int] | None = None) -> Plan:
    """Walk the ladder as a ring: enter at `entry`, then go one way round.

    Stock is `ring_plan(n, 0, "forward")` and the reversal is
    `ring_plan(n, n, "backward")`, which is the whole point of writing it this
    way - those two are not special cases of the code, they are two of the
    2(N+1) members, and they are the two that start at an end of the corridor.
    Any other entry has to double back for the rungs it walked past, so exactly
    one stage transition spans the corridor. That cost is measured, not assumed:
    see `advances`.

    `ring` is the order the rungs stand in *on the ground*, and it defaults to
    the order the chain numbers them. The two are the same thing only on a
    corridor. On an open map the chain wanders - tell_open_coop's shipped chain
    doubles back five times - and walking `0,1,..,N` inherits every one of those
    doublings in all 2(N+1) members. `geometry_ring` measures the order instead;
    see its docstring for what that buys.
    """
    if direction not in ("forward", "backward"):
        raise ValueError(f"direction is forward or backward, not {direction!r}")
    ring = list(range(n + 1)) if ring is None else list(ring)
    if sorted(ring) != list(range(n + 1)):
        raise ValueError(f"a ring over {n + 1} rungs must name each once: {ring}")
    step = 1 if direction == "forward" else -1
    at = ring.index(entry)
    rungs = [ring[(at + step * j) % (n + 1)] for j in range(1, n + 1)]
    tag = "fwd" if direction == "forward" else "rev"
    return Plan(f"enter{entry}_{tag}", [-1] + rungs, entry,
                f"attackers enter at rung {entry} and work {direction} round the "
                f"ladder: stage 1 is fought at rung {rungs[0]}")


def explicit_plan(rungs: list[int], name: str = "custom") -> Plan:
    """A layout given rung by rung. The leftover rung is the attacker entry."""
    n = len(rungs)
    spare = [r for r in range(n + 1) if r not in rungs]
    if len(spare) != 1:
        raise ValueError(
            f"{n} stages over rungs 0..{n} must leave exactly one rung for the "
            f"attackers to enter on; this leaves {spare}"
        )
    return Plan(name, [-1] + list(rungs), spare[0],
                "given rung by rung: " + " ".join(map(str, rungs)))


def ring_family(n: int, ring: list[int] | None = None) -> list[Plan]:
    """Every ring walk, stock first and the reversal second.

    2(N+1) walks, less the two that are already in the list: `ring_plan(n, 0,
    "forward")` is stock and `ring_plan(n, n, "backward")` is the reversal. They
    are dropped on their rung order rather than their name, so the identity is
    checked rather than assumed - and on a measured `ring` some other walk may
    *be* stock, in which case it is dropped the same way.
    """
    out: list[Plan] = [stock_plan(n), reverse_plan(n)]
    seen = {tuple(pl.order) for pl in out}
    for entry in range(n + 1):
        for direction in ("forward", "backward"):
            pl = ring_plan(n, entry, direction, ring=ring)
            key = tuple(pl.order)
            if key not in seen:
                seen.add(key)
                out.append(pl)
    return out


# ── the ring the ground forms, rather than the one the chain implies ──
#
# A ring walk is only a progression if consecutive rungs on the ring are
# neighbours on the ground. The chain gives that for free on a corridor, which
# is the shape the first two maps had. An open map is where it stops being
# free: tell_open_coop's sixteen objectives are scattered over a town, its
# shipped chain crosses itself, and a family built on `0,1,..,N` reproduces
# that crossing in every member - measured, before this: worst detour 7.3x on
# stock and 6.2x on the best of the 31.
#
# So measure the ring. The rungs are points on a mesh with a distance matrix
# already built for the advances; the order to walk them in is the shortest
# closed tour over that matrix, which is the formal statement of "go round once
# and do not come back for anything". Seventeen rungs is small enough to solve
# properly - two geometric seeds and 2-opt to a local optimum, which on this
# size is the optimum often enough not to matter.


def _plan_view(rungs: list["Rung"]) -> np.ndarray:
    """Rung positions in plan. Height is storeys, not progression."""
    return np.array([r.floor[:2] for r in rungs], dtype=float)


def _axis_order(P: np.ndarray) -> list[int]:
    """Rungs sorted along their own principal axis: a push in one direction."""
    c = P.mean(axis=0)
    d = P - c
    # The principal axis of a 2xN scatter, without pulling in a solver for it.
    sxx, syy = float((d[:, 0] ** 2).sum()), float((d[:, 1] ** 2).sum())
    sxy = float((d[:, 0] * d[:, 1]).sum())
    th = 0.5 * np.arctan2(2 * sxy, sxx - syy)
    proj = d[:, 0] * np.cos(th) + d[:, 1] * np.sin(th)
    return sorted(range(len(P)), key=lambda i: float(proj[i]))


def _circuit_order(P: np.ndarray) -> list[int]:
    """Rungs sorted by angle about their centroid: a loop round the map."""
    c = P.mean(axis=0)
    d = P - c
    ang = np.arctan2(d[:, 1], d[:, 0])
    return sorted(range(len(P)), key=lambda i: float(ang[i]))


def _tour_length(order: list[int], S: np.ndarray) -> float:
    """Closed length of a walk. `inf` if any leg is unreachable."""
    return float(sum(S[order[i], order[(i + 1) % len(order)]]
                     for i in range(len(order))))


def _detour(there: float, back: float, straight: float,
            apart: float = float("inf")) -> float:
    """How far a stage sends the round off the straight line, as a multiple.

    `there + back` is what the player walks; `straight` is how far the round
    got for it, from where the stage started to where the next one ends. 1.0 is
    an objective standing on the way to the next, 6.0 is being sent across the
    map and brought back to where you were.

    **The two ends can be one place, and that is the worst case rather than no
    case.** Written with only the mesh - `if straight > 1.0` - this used to
    *skip* such a triple, so the one walk that is entirely a detour scored as
    having none: gioconda_eron_mountains ships cp9 and cp10 on one nav area at
    4312 -4488 16, and `enter3_fwd` put cp1 between them. 8,041 u out and
    8,041 u back to the ground the round had just taken, reported as 1.77x -
    its *best* number. A round that ends where it began has advanced nothing
    and there is no multiple of nothing, so the detour is infinite and
    `max_detour` refuses it like any other round that doubles back.

    **`apart` is the same question asked of the ground.** It is `rung_gaps`'
    answer for the two ends, and it is here for the reason `SHORT_SEPARATION`
    exists at all: the mesh can be long where the ground is not. The same map's
    rungs 10 and 11 stand 608 u apart across one floor and 5,591 u apart on the
    mesh, because the only way down is round the building - so a walk that goes
    10, somewhere, 11 walks 8,498 u to move the round 608 u, and by the mesh
    that is 1.52x and passes. Two ends that are one place are one place however
    far the mesh has to go between them, and `SHORT_SEPARATION` is already the
    corpus's answer for how close that is.

    Three rungs in one place is a different complaint - no stage moves at all -
    and `SHORT_ADVANCE` and `SHORT_SEPARATION` are the terms that make it, so
    it is not counted twice here.
    """
    if apart < SHORT_SEPARATION or straight <= 1.0:
        return float("inf") if there + back > 1.0 else 0.0
    return (there + back) / straight


def _ring_detour(order: list[int], S: np.ndarray,
                 E: np.ndarray | None = None) -> float:
    """The worst `_detour` anywhere on a closed ring.

    Length alone does not say what a round *feels* like: a tour can be short
    and still hang one rung off a spur, which is the stage that gets walked to
    and walked back from. This is the number that catches that, and the walks
    the family emits are this ring less one edge, so minimising it here is
    minimising it for all 2(N+1) of them at once - including the spur whose two
    ends are one piece of ground, which is why `E` is threaded this far down.
    """
    n = len(order)
    worst = 0.0
    for i in range(n):
        a, b, c = order[i - 1], order[i], order[(i + 1) % n]
        worst = max(worst, _detour(
            float(S[a, b]), float(S[b, c]), float(S[a, c]),
            float("inf") if E is None else float(E[a, c])))
    return worst


def _ring_cost(order: list[int], S: np.ndarray,
               E: np.ndarray | None = None) -> tuple[float, float]:
    """Rank rings by the worst detour first and total length second.

    In that order deliberately. A ring 5% longer that never doubles back is the
    better round, and the whole complaint this replaces was about doubling back.
    """
    return (_ring_detour(order, S, E), _tour_length(order, S))


def _two_opt(order: list[int], S: np.ndarray, rounds: int = 64,
             E: np.ndarray | None = None) -> list[int]:
    """2-opt a closed ring: reverse the segment that crosses, until none does."""
    best = list(order)
    n = len(best)
    if n < 4:
        return best
    cost = _ring_cost(best, S, E)
    if not np.isfinite(cost[1]):
        return best
    for _ in range(rounds):
        improved = False
        for i in range(n - 1):
            for k in range(i + 2, n):
                if i == 0 and k == n - 1:
                    continue
                cand = best[:i + 1] + best[i + 1:k + 1][::-1] + best[k + 1:]
                ccost = _ring_cost(cand, S, E)
                if ccost < cost:
                    best, cost, improved = cand, ccost, True
        if not improved:
            break
    return best


def _or_opt(order: list[int], S: np.ndarray, rounds: int = 32,
            E: np.ndarray | None = None) -> list[int]:
    """Lift one rung out of the ring and re-insert it where it costs least.

    2-opt cannot fix a spur - reversing a segment never moves a single rung to
    the other side of the map - and a spur is exactly the shape that makes a
    round double back. This is the move that can.
    """
    best = list(order)
    n = len(best)
    if n < 4:
        return best
    cost = _ring_cost(best, S, E)
    for _ in range(rounds):
        improved = False
        for i in range(n):
            rest = best[:i] + best[i + 1:]
            node = best[i]
            for j in range(len(rest) + 1):
                if j == i:
                    continue
                cand = rest[:j] + [node] + rest[j:]
                ccost = _ring_cost(cand, S, E)
                if ccost < cost:
                    best, cost, improved = cand, ccost, True
        if not improved:
            break
    return best


def geometry_ring(rungs: list["Rung"], D: np.ndarray,
                  mode: str = "tour") -> list[int]:
    """The order to walk the rungs in, measured rather than assumed.

    `chain` is what the family did before: the order the cpsetup numbers them,
    which is a progression only if the shipped chain is a corridor. `axis` and
    `circuit` are the two shapes a round can have and be worth playing - a push
    one way, or a loop - taken straight off the rung positions. `tour` is both
    of those plus the chain, 2-opted against the mesh distances, best kept: it
    is the one to use, because which of the two shapes a given map wants is
    exactly the thing that should be measured rather than chosen.

    The mesh distance is directed and a tour is not, so the ordering is done on
    the symmetrised matrix and every *number* reported afterwards comes back
    from the directed one.
    """
    if mode == "chain":
        return list(range(len(rungs)))
    P = _plan_view(rungs)
    if mode == "axis":
        return _axis_order(P)
    if mode == "circuit":
        return _circuit_order(P)
    if mode != "tour":
        raise ValueError(f"ring mode is chain, axis, circuit or tour, not {mode!r}")

    S = (D + D.T) / 2.0
    if not np.isfinite(S).all():
        # An unreachable pair makes every tour through it infinite, so the
        # seeds cannot be ranked. The geometry still can be.
        return _axis_order(P)
    # The ring is ranked on its worst detour, and a detour whose two ends are
    # one piece of ground is the worst there is - so the seed the whole family
    # is optimised from has to be able to see the ground, not only the mesh.
    E = rung_gaps(rungs)
    seeds = [list(range(len(rungs))), _axis_order(P), _circuit_order(P)]
    best, bcost = None, (float("inf"), float("inf"))
    for seed in seeds:
        cand = _or_opt(_two_opt(seed, S, E=E), S, E=E)
        cand = _two_opt(cand, S, E=E)     # the re-insertion can open a crossing
        ccost = _ring_cost(cand, S, E)
        if ccost < bcost:
            best, bcost = cand, ccost
    return best if best is not None else seeds[0]


def arrival_clashes(survey: Survey, setup: CpSetup,
                    rungs: list["Rung"]) -> set[tuple[int, int]]:
    """Steps that would drop a stage's attackers onto its own objective.

    `(a, b)` is in the set when the attacker zone standing on rung `a` is in
    fact the volume standing on rung `b` - so a walk that goes `a` then `b`
    fights stage b's objective with its attackers arriving inside it. It is a
    property of the map, not of any layout: tell_open_coop has six of them,
    because four of its sixteen stages share an attacker volume with another.

    A walk that never takes one of these steps cannot produce the defect, which
    is why this is handed to the optimiser rather than only checked after.
    """
    atk = setup.attacking_team
    out: set[tuple[int, int]] = set()
    for a in range(len(rungs)):
        zname, zteam, _ = dest_zone(survey, setup, a, "attacker")
        if zteam != atk:
            continue
        vols = survey.spawn_zone(zname, zteam)
        if not vols:
            continue
        boxes = _boxes(vols, np.zeros(3))
        for b in range(len(rungs)):
            if b != a and _inside(rungs[b].floor, boxes):
                out.add((a, b))
    return out


def volume_misfits(survey: Survey, setup: CpSetup, rungs: list["Rung"],
                   snap: Snapper, graph: NavGraph) -> set[tuple[int, int]]:
    """Stages that cannot be fought at a rung, because the volume loses its floor.

    `(j, k)` is in the set when objective j's capture volume, translated to rung
    k, covers no area the player hull fits on - which is the server's "Failed
    finding CP area for N!" - while it covers floor where the map itself put it,
    so the layout is what lost it. `layout` refuses exactly this, and the box
    does not resize, so the answer depends only on the objective and the rung:
    it can be asked of every pair once and handed to the optimiser.

    Which is the point of computing it here. It was only ever checked *after* a
    walk was chosen, so the optimiser would happily route a stage onto ground
    its volume cannot stand on and lose the whole layout for it -
    gioconda_eron_mountains' rung 3 is such ground for three of its objectives,
    and a shape correction that moved more stages onto it cost the map ten
    layouts before this existed. A walk that never takes one of these steps
    cannot produce the defect, which is the same argument `arrival_clashes`
    makes.
    """
    out: set[tuple[int, int]] = set()
    for j, o in enumerate(resolve(survey, setup.chain), start=1):
        if o.kind == "cache" or o.anchor.mins is None:
            # A cache is a point entity with nothing to cover floor with, and
            # `layout` stands it on its rung rather than refusing it.
            continue

        def covers(at: np.ndarray) -> int:
            return sum(1 for i in snap.overlapping(at + o.anchor.mins,
                                                   at + o.anchor.maxs)
                       if graph.hull_ok[i] and not graph.blocked[i])

        if covers(o.anchor.origin) == 0:
            # No floor where the map itself put it: whatever the gamemode says
            # about this objective it says stock, and no rung is to blame.
            continue
        src_floor = _objective_floor(o, snap, graph)
        for k, dest in enumerate(rungs):
            if covers(o.anchor.origin + (dest.floor - src_floor)) == 0:
                out.add((j, k))
    return out


def rung_gaps(rungs: list["Rung"]) -> np.ndarray:
    """How far apart two rungs are *on the same level*, or `inf` if they are not.

    `rung_distances` is the walk; this is the ground. It needs neither the mesh
    nor a snap, and it is deliberately not the straight line: a rung a storey
    above another is a different place however close it is in plan, so the
    distance is horizontal and only between rungs within `LEVEL` in z. See
    `SHORT_ADVANCE` for the corpus that sets both.
    """
    P = np.array([r.floor for r in rungs], dtype=float)
    flat = np.linalg.norm(P[:, None, :2] - P[None, :, :2], axis=-1)
    climb = np.abs(P[:, None, 2] - P[None, :, 2])
    return np.where(climb <= LEVEL, flat, np.inf)


def _path_cost(order: list[int], S: np.ndarray,
               clashes: set[tuple[int, int]] | None = None,
               E: np.ndarray | None = None,
               misfits: set[tuple[int, int]] | None = None) -> tuple[float, ...]:
    """Rank open walks: never arrive in a fight, never capture on arrival, never
    double back, then no huge step, then keep it short.

    The first two terms are both "this stage is not a fight" - one where the
    attackers spawn inside the defence, the other where the objective is close
    enough to the last one to be taken on the way past - and they come before
    every term about distance because no amount of straightness buys either
    back. `SHORT_ADVANCE` and `SHORT_SEPARATION` have the maps that taught
    this: the second of them is the same closeness measured across the floor
    rather than along the mesh, and is counted with the first.

    The third term is `_detour`, and the case it exists for is the one a ratio
    cannot express: a step whose two ends are the same ground scores `inf`
    here, so the optimiser will pay any march to stop shuttling out to one
    objective and back to the place it just took.

    An open walk is what a round actually is - the ring less one edge - and the
    edge it drops matters. Ranking closed rings and then rotating them makes
    every walk carry the ring's longest edge somewhere in the middle;
    tell_open_coop's is 18,795 u, which is two and a half times the longest step
    the shipped map asks of anyone. Optimising the walk itself puts that edge at
    the end, where it is the map's outlying objective being the last one, which
    is what an outlier should be.
    """
    n = len(order)
    edges = [float(S[order[i], order[i + 1]]) for i in range(n - 1)]
    # Steps that are not fights, counted before anything about length is
    # weighed: a walk that captures an objective on arrival has spent it, and
    # no amount of straightness buys that back. See `SHORT_ADVANCE`.
    walkons = sum(1 for e in edges if e < SHORT_ADVANCE)
    # The same steps measured across the ground rather than along the mesh, and
    # `inf` where the step changes level: a stage that marches a long way round
    # and arrives next door to where it started.
    #
    # **The arrival is not one of these, and it is the whole first step.** The
    # attackers spawn on the entry rung and walk to the first objective; that
    # they can see it from where they spawn is how a round starts, not a stage
    # spent. siege_coop has exactly one pair of rungs within 1,000 u of each
    # other on one level and it is that one - its entry, 941 u from its own
    # first objective, as the map ships it - so counting it refused the shipped
    # chain and cost the map all nine of its layouts.
    #
    # **What it costs is why the threshold is not the walk's.** This term is
    # ranked with the walk-ons, over everything about distance, so where it can
    # be reduced the optimiser will pay a longer march and a wider detour to
    # reduce it - and on a small map it can always be reduced. At 1,000 u
    # cs_officeb3_coop_v1_5, whose rungs 0, 3 and 5 stand 155-573 u apart on one
    # level, buys them apart with rounds that double back 6.59x and loses all
    # four of its layouts; siege_coop loses eight of nine the same way. At
    # `SHORT_SEPARATION` both keep every layout they had. See there.
    gaps = ([float(E[order[i], order[i + 1]]) for i in range(1, n - 1)]
            if E is not None else [])
    near = sum(1 for g in gaps if g < SHORT_SEPARATION)
    # Counted with the walk-ons rather than after them: both are "this stage is
    # not a fight", and a walk that has the choice should take neither.
    worst = 0.0
    for i in range(1, n - 1):
        a, b, c = order[i - 1], order[i], order[i + 1]
        worst = max(worst, _detour(
            float(S[a, b]), float(S[b, c]), float(S[a, c]),
            float("inf") if E is None else float(E[a, c])))
    # Detours below the floor are all the same walk as far as a player is
    # concerned - a stage that is 1.3x rather than 1.5x off the straight line is
    # not a round doubling back, it is a road bending. Flattening them is what
    # lets the second term do its job: with a strict ordering the optimiser will
    # buy a 0.02x detour with a 4,000 u march, which is the wrong trade.
    # Both of these are the engine refusing the layout rather than a judgement
    # about how it plays, so they rank above everything: a step whose attackers
    # arrive inside their own objective, and a stage whose capture volume finds
    # no floor on the rung the walk sends it to. See `volume_misfits`.
    bad = 0 if not clashes else sum(
        1 for i in range(n - 1) if (order[i], order[i + 1]) in clashes)
    if misfits:
        bad += sum(1 for j in range(1, n) if (j, order[j]) in misfits)
    return (bad, walkons + near, max(worst, DETOUR_FLOOR),
            max(edges) if edges else 0.0, sum(edges))


def _optimise_walk(order: list[int], S: np.ndarray, rounds: int = 32,
                   clashes: set[tuple[int, int]] | None = None,
                   E: np.ndarray | None = None,
                   misfits: set[tuple[int, int]] | None = None) -> list[int]:
    """2-opt and or-opt an open walk, holding its first rung where it is.

    The first rung is the ground the attackers arrive on, and the point of a
    family is one walk per possible arrival, so it is the one thing not free to
    move.
    """
    best = list(order)
    n = len(best)
    cost = _path_cost(best, S, clashes, E, misfits)
    if not np.isfinite(cost[-1]):
        return best
    for _ in range(rounds):
        improved = False
        for i in range(1, n - 1):
            for k in range(i + 1, n):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                ccost = _path_cost(cand, S, clashes, E, misfits)
                if ccost < cost:
                    best, cost, improved = cand, ccost, True
        for i in range(1, n):
            rest = best[:i] + best[i + 1:]
            node = best[i]
            for j in range(1, len(rest) + 1):
                if j == i:
                    continue
                cand = rest[:j] + [node] + rest[j:]
                ccost = _path_cost(cand, S, clashes, E, misfits)
                if ccost < cost:
                    best, cost, improved = cand, ccost, True
        if not improved:
            break
    return best


def walk_family(rungs: list["Rung"], D: np.ndarray,
                clashes: set[tuple[int, int]] | None = None,
                misfits: set[tuple[int, int]] | None = None) -> list[Plan]:
    """One layout per arrival rung: the best progression that starts there.

    `ring_family` rotates a single ring, so all 2(N+1) of its members share one
    shape - and on an open map they share its faults too, which is why every
    member of tell_open_coop's first family had the same 6.5x detour. Here each
    walk is optimised on its own, from its own entry, against the mesh. The
    family is then N+1 walks that are each the best round available from where
    they start, rather than N+1 rotations of one round.

    Stock and the reversal are prepended and kept out of the dedup, as they are
    for `ring_family`: they are the controls, not proposals.
    """
    n = len(rungs) - 1
    S = (D + D.T) / 2.0
    if not np.isfinite(S).all():
        return ring_family(n)
    E = rung_gaps(rungs)
    seed = geometry_ring(rungs, D, mode="tour")
    out: list[Plan] = [stock_plan(n), reverse_plan(n)]
    seen = {tuple(pl.order) for pl in out}
    for entry in range(n + 1):
        at = seed.index(entry)
        for direction in ("forward", "backward"):
            step = 1 if direction == "forward" else -1
            start = [seed[(at + step * j) % (n + 1)] for j in range(n + 1)]
            walk = _optimise_walk(start, S, clashes=clashes, E=E,
                                  misfits=misfits)
            key = tuple(walk)
            if key in seen:
                continue
            seen.add(key)
            tag = "fwd" if direction == "forward" else "rev"
            out.append(Plan(
                f"enter{entry}_{tag}", [-1] + walk[1:], walk[0],
                f"attackers enter at rung {entry}; the walk from there is the one "
                f"the mesh makes shortest without doubling back, "
                f"stage 1 at rung {walk[1]}"))
    return out


def ladder(survey: Survey, setup: CpSetup,
           graph: NavGraph | None = None) -> tuple[list["Rung"], np.ndarray]:
    """The rungs and the distances between them, for a caller that has to
    choose a walk before it can ask for one. `layout` rebuilds both; they cost
    a snap per rung and one Dijkstra per rung, and doing it twice is cheaper
    than threading them through every call that does not need them."""
    graph = graph or NavGraph.build(survey)
    snap = Snapper(survey)
    rungs = build_rungs(survey, setup, resolve(survey, setup.chain), snap, graph)
    return rungs, rung_distances(rungs, snap, graph)


def _returns(plan: Plan, D: np.ndarray,
             E: np.ndarray | None = None) -> tuple[str, int, int, int, float, float]:
    """The first step of a walk that lands back on ground the round has left.

    Which is what an infinite `_detour` is, and a refusal has to be able to
    name it: what held the ground, the stage that returns to it, the two rungs
    that are one place, how far apart they stand, and how far the round walks
    out in between.
    """
    order = plan.order
    for i in range(1, len(order) - 1):
        a, b, c = order[i - 1], order[i], order[i + 1]
        there, back, straight = float(D[a, b]), float(D[b, c]), float(D[a, c])
        if not (np.isfinite(there) and np.isfinite(back) and np.isfinite(straight)):
            continue
        apart = float("inf") if E is None else float(E[a, c])
        if not np.isfinite(_detour(there, back, straight, apart)):
            held = "the entry" if i == 1 else f"stage {i - 1}"
            return held, i + 1, a, c, min(apart, straight), there + back
    return "?", 0, -1, -1, float("nan"), float("nan")


# How far off the best layout on a map another one may be and still be worth
# rotating to. A gate answers "is this round playable"; this answers "is it one
# of the better rounds this map can give", which is a different question and the
# one a preset file of 30 layouts actually raises. Measured over the 109 maps
# that emit, 2026-09-19: the band keeps 752 of 987 layouts and takes the p90
# layout from 2.66 to 2.22 on the score below, where a cap at 8 per map keeps
# 588 and only reaches 2.51 - so the band is what drops *bad* rounds and a cap
# mostly drops variety. Both are available; the band is the default.
#
# It can never empty a map: the best layout a map has is within 1.0x of itself,
# and that is the one the whole exercise is for. launch_control_coop_ws' best
# round detours 8.24x, which is a bad round by any absolute standard and still
# the best that map can be played - and 1.07x better than the round it ships.
KEEP_WITHIN = 1.25

# ...and the line under which a layout is good enough to rotate whatever else
# the map can do. The band alone is relative to the best round *this* map has,
# which punishes a map for being good at its best: tell_coop's best scores 1.41,
# so the band cut it to 6 layouts of 25 although its worst was a perfectly
# ordinary round. The floor here is the shipped corpus's own answer - score the
# 13 checkpoint maps' own rounds the same way and the median is 1.98, the p75
# 2.32, and 7 of the 10 measurable ones are at or under 2.20: peak_coop 1.40,
# embassy_coop 1.74, tell_coop 1.83, contact_coop 1.85, verticality_coop 1.88,
# revolt_coop 2.08, sinjar_coop 2.19, against heights_coop 2.70 and buhriz_coop
# 3.25. A round no worse than a median shipped one is not one to throw away.
#
# Measured over the 92 emitting maps: the band alone keeps 722 of 911, and the
# band or this floor keeps 803 - 81 more rounds at the *same* p90 (2.18) and the
# same worst (3.17), because everything between the two lines sits on a map
# whose best is very good rather than on a map with a bad round.
KEEP_GOOD = 2.2


def layout_score(lay: "Layout") -> float:
    """How good a round this layout asks for - lower is better, and it is a rank
    rather than a verdict.

    The gates in `layout` decide whether a walk is playable at all, each against
    the map's own stock round. This orders the ones that are, so a preset file
    can hold the better half of what a map can do rather than everything that
    was not refused. The terms are the same measurements the gates use, which is
    deliberate - a layout that only just cleared a gate should rank last, not
    look equal to one that cleared it by a mile:

    * `worst_detour` carries the score, because doubling back is what a player
      notices first and what `geometry_ring` was written to fix. An infinite
      detour cannot be ranked and is scored as 6.0, worse than anything the
      corpus emits, though `max_detour` refuses it before this is asked.
    * a worst stage *longer* than the map's own worst, as the excess ratio.
    * a stage too short to be a fight, and the same measured across the ground
      - both as how far under the calibrated floor they fall, so a walk that
      clears them costs nothing here.
    * backtracks and thin borrowed zones, per stage, as tie-breakers.
    """
    from .cli import GATE_MIN_ADVANCE, GATE_MIN_SEPARATION

    det = lay.worst_detour
    det = 6.0 if (np.isnan(det) or not np.isfinite(det)) else det
    stock = lay.stock_max
    ratio = (lay.worst_advance / stock
             if np.isfinite(lay.worst_advance) and np.isfinite(stock) and stock > 0
             else 1.0)
    gap = lay.min_advance if np.isfinite(lay.min_advance) else GATE_MIN_ADVANCE
    sep = lay.min_separation if np.isfinite(lay.min_separation) else GATE_MIN_SEPARATION
    n = max(len(lay.stages), 1)
    thin = sum(1 for w in lay.warnings if "spawn points, under the" in w)
    return (det
            + 0.5 * max(0.0, ratio - 1.0)
            + 0.5 * max(0.0, GATE_MIN_ADVANCE / max(gap, 1.0) - 1.0)
            + 0.3 * max(0.0, GATE_MIN_SEPARATION / max(sep, 1.0) - 1.0)
            + 0.2 * (lay.metrics.get("backtracks", 0) / n)
            + 0.1 * (thin / n))


def rank(layouts: list["Layout"], within: float | None = KEEP_WITHIN,
         good: float | None = KEEP_GOOD, keep: int | None = None) -> list["Layout"]:
    """The layouts worth rotating, best first: within `within` times the best
    score or under `good` outright, at most `keep` of them, never fewer than one.

    The two lines answer different halves of the same question. The band drops a
    round that is poor *for this map* - there is a better one to rotate to
    instead. The floor keeps a round that is fine by the standard of the maps
    people already play, because a map with an excellent best round should not
    thereby ship fewer of its good ones.

    The stock walk rides along unranked. It is the control the applier writes
    anyway, it is not a permutation of anything, and a map whose stock round
    scores well is not thereby a map with a layout.
    """
    stock = [l for l in layouts if l.name == "stock"]
    ranked = sorted((l for l in layouts if l.name != "stock"), key=layout_score)
    if not ranked:
        return stock
    if within is not None or good is not None:
        best = layout_score(ranked[0])
        bar = max(best * within if within is not None else 0.0,
                  good if good is not None else 0.0)
        ranked = [l for l in ranked if layout_score(l) <= bar] or ranked[:1]
    if keep is not None:
        ranked = ranked[:keep]
    return stock + ranked


def _no_worse_floor(gate: float, stock: float) -> float:
    """A lower-bound gate, relaxed to whatever the map itself already asks.

    `min_advance` and `min_separation` are floors calibrated on the 45 shipped
    chains, and a workshop map is under no obligation to clear them: the point
    of the gate is that a *layout* must not make a round worse than the one the
    map ships, not that every map ships a good round. So the bar is the gate or
    the stock walk's own number, whichever is lower, and a map whose stock round
    is already under the gate can have a layout that is no worse than it.
    """
    return gate if np.isnan(stock) else min(gate, stock)


def _than_stock(bar: float, gate: float) -> str:
    """Say so when a bar is the map's own number rather than the calibrated one."""
    return "" if bar == gate else " - which is what the map's own round asks"


def plan_metrics(plan: Plan, D: np.ndarray,
                 E: np.ndarray | None = None) -> dict:
    """What a walk asks of the player, beyond how far each stage advances.

    * `min_advance` - the shortest step. A step of nothing is a stage captured
      on arrival, which is the map spending an objective and giving no fight
      for it.
    * `worst_detour` - the largest `_detour`. 1.0 is a stage standing on the
      way to the next one; 6.0 is being sent across the map and returned to
      where you were; `inf` is being returned to the very ground you were on,
      which a map that ships two objectives in one place can ask for. This is
      the number the shipped chain is bad at and the one `geometry_ring` exists
      to fix.
    * `backtracks` - stages that finish *nearer* their own start than the
      objective they were sent to was.
    * `min_separation` - the shortest step measured across the ground rather
      than along the mesh, when the rung positions are given, and `inf` where
      every step changes level. A stage can march a long way round and arrive
      next door to where it started; this is the measure that says so. See
      `SHORT_ADVANCE`.
    """
    order = plan.order
    hops = [float(D[order[i], order[i + 1]]) for i in range(len(order) - 1)]
    finite = [h for h in hops if np.isfinite(h)]
    detours, backtracks = [], 0
    for i in range(1, len(order) - 1):
        a, b, c = order[i - 1], order[i], order[i + 1]
        there, back, straight = float(D[a, b]), float(D[b, c]), float(D[a, c])
        if not (np.isfinite(there) and np.isfinite(back) and np.isfinite(straight)):
            continue
        detours.append(_detour(
            there, back, straight,
            float("inf") if E is None else float(E[a, c])))
        if straight < there:
            backtracks += 1
    # From the first objective on: the step from the entry rung is the round
    # starting, not a stage captured on arrival. See `_path_cost`.
    gaps = ([float(E[order[i], order[i + 1]]) for i in range(1, len(order) - 1)]
            if E is not None else [])
    out = {
        "min_advance": min(finite) if finite else float("nan"),
        "worst_detour": max(detours) if detours else float("nan"),
        "backtracks": backtracks,
        "hops": hops,
    }
    if gaps:
        out["min_separation"] = min(gaps)
        out["gaps"] = gaps
    return out


def walk_ons(layout_metrics: dict, under: float) -> int:
    """Stages whose advance is under `under` - objectives that are not fights.

    The count matters more than the shortest one: a map with two sites 123 u
    apart cannot have both of them be a fight, and the question a preset file
    has to answer is how many of its N slots are real.
    """
    return sum(1 for h in layout_metrics.get("hops", []) if h < under)


@dataclass
class Move:
    """One entity edit, in the form the applier's `edit` block takes."""

    cls: str
    target: str = ""
    team: int | None = None
    within: tuple[np.ndarray, np.ndarray] | None = None
    origin: np.ndarray | None = None
    offset: np.ndarray | None = None
    rename: str = ""           # a new targetname, and no geometry change at all
    note: str = ""


@dataclass
class ObjectiveMove:
    """A cache and its marker, moved after they spawn.

    Caches are defined in `maps/<name>.txt` rather than in the entity lump, so
    the applier teleports them instead of rewriting them - `CObjWeaponCache::
    Teleport` is overridden in the server binary and fixes up the cache's own
    trigger.

    A targetname does not have to be unique, and on this corpus it often is
    not: nine of the 46 surveyed maps carry two entities under one objective
    name, because `maps/<name>.txt` creates its own marker beside the one the
    mapper baked into the BSP. A name alone would leave the applier taking
    whichever the enumeration reached first, so it is given the coordinate to
    expect as well - `frm` and `cp_from` are where the two stand before this
    move, and they are what makes the pick the entity it was measured from.
    """

    name: str                  # the cache's targetname
    cp: str                    # its control point's targetname
    origin: np.ndarray
    cp_offset: float
    frm: np.ndarray | None = None      # where the cache stands on the stock map
    cp_from: np.ndarray | None = None  # ...and where its marker does
    note: str = ""


@dataclass
class Rung:
    """One position on the corridor - somewhere a stage is fought.

    Rung 0 is the attacker entry and has no objective; rung k is objective k
    together with the defender zone that guards it.
    """

    index: int
    floor: np.ndarray                  # the walkable point that stands for it
    objective: Objective | None = None
    def_zone: str = ""                 # defender zone targetname sitting here
    def_at: np.ndarray | None = None   # centroid of that zone's volumes
    atk_zone: str = ""                 # attacker zone whose stage is index+1
    atk_at: np.ndarray | None = None


@dataclass
class Layout:
    """One layout of a map, and what could be checked about it offline."""

    map: str
    chain: list[str]
    zones: list[str]
    attacking_team: int
    rungs: list[Rung]
    plan: Plan | None = None
    moves: list[Move] = field(default_factory=list)
    objectives: list[ObjectiveMove] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    advances: list[dict] = field(default_factory=list)
    stock_advances: list[float] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)   # plan_metrics of this walk
    stock_metrics: dict = field(default_factory=dict)
    contested: int = 0                 # points two stages' zones both hold
    unclaimed: int = 0                 # points inside no stage zone at all
    coincident: int = 0                # points sharing an origin with another
    leaks: int = 0                     # points a moved zone offers on the wrong
                                       # side of its own front
    pads: int = 0                      # zone volumes holding no spawn points,
                                       # left where the map put them
    warnings: list[str] = field(default_factory=list)

    @property
    def defending_team(self) -> int:
        return 5 - self.attacking_team

    @property
    def points_moved(self) -> int:
        return sum(1 for m in self.moves if m.cls == "ins_spawnpoint")

    @property
    def ok(self) -> bool:
        return not any(w.startswith("REFUSED") for w in self.warnings)

    @property
    def name(self) -> str:
        return self.plan.name if self.plan else "reversed"

    @property
    def stock_max(self) -> float:
        """The longest advance the map itself asks of a stage."""
        finite = [d for d in self.stock_advances if np.isfinite(d)]
        return max(finite) if finite else float("nan")

    @property
    def worst_advance(self) -> float:
        finite = [a["advance"] for a in self.advances if np.isfinite(a["advance"])]
        return max(finite) if finite else float("nan")

    @property
    def worst_detour(self) -> float:
        """How far out of the way the worst stage sends the round."""
        return float(self.metrics.get("worst_detour", float("nan")))

    @property
    def min_advance(self) -> float:
        """The shortest step the walk asks for - a free stage if it is small."""
        return float(self.metrics.get("min_advance", float("nan")))

    @property
    def min_separation(self) -> float:
        """The closest two consecutive objectives stand, across the ground.

        A stage can be a long march on the mesh and still end up next door to
        the one before it, which is the same free stage by another route.
        """
        return float(self.metrics.get("min_separation", float("nan")))

    # The same three, for the walk the map itself ships. They are what the gates
    # relax to: a layout is refused for being worse than the map, not for being
    # as bad as the map already is. `nan` is "the stock walk could not be
    # measured either", which relaxes nothing.
    @property
    def stock_min_advance(self) -> float:
        return float(self.stock_metrics.get("min_advance", float("nan")))

    @property
    def stock_min_separation(self) -> float:
        return float(self.stock_metrics.get("min_separation", float("nan")))

    @property
    def stock_worst_detour(self) -> float:
        return float(self.stock_metrics.get("worst_detour", float("nan")))


# The old name. A reversal is one Layout out of the family, and nothing that
# imported this before the family existed needs to know that.
Reversal = Layout


# ── the ladder ───────────────────────────────────────────────────────

def _centroid(ents: list[Entity]) -> np.ndarray | None:
    """Where a set of brush volumes sits, from their boxes rather than their keys.

    A brush entity's `origin` key is not required to be anywhere near its
    geometry, and on three of the thirteen shipped maps it is not set at all:
    every `ins_spawnzone` on embassy_coop, revolt_coop and siege_coop carries
    `origin [0 0 0]` with world-absolute model bounds, so averaging origins put
    all 22 of each map's zones - and with them rung 0's floor - at the world
    origin. `Survey.world_box` is right either way, because absolute bounds plus
    a zero origin is still absolute, so the box centre is what to average.

    On the ten maps whose origins are set the two agree exactly, and on
    district_coop and tell_coop the box centre moves 605 u and 354 u off the
    key, which is asymmetric bounds and the box is the honest answer there too.
    No map's verdict turns on it: the refusals come out of `_usable`, which
    already worked from `world_box`. What it fixes is everything that reads a
    zone's *position* - the placement search's tie-break, rung 0's coverage
    check, and any later pass that measures an angle or a distance from a zone.
    """
    if not ents:
        return None
    pts = [
        e.origin if (box := Survey.world_box(e)) is None else (box[0] + box[1]) / 2.0
        for e in ents
    ]
    return np.mean(pts, axis=0)


def _floor_of(point: np.ndarray, snap: Snapper, graph: NavGraph) -> np.ndarray:
    """The walkable point under something, or the thing itself off the mesh.

    A brush centre is a point in mid-air and a control point marker floats +72
    above its cache, so neither is where a player stands. The area under it is.
    """
    a = snap.snap(point)
    return graph.centers[a] if a is not None else point


def _cache_stand(floor: np.ndarray, snap: Snapper, graph: NavGraph) -> np.ndarray | None:
    """Ground a player fits on, at a rung, to stand a cache on.

    The rung's own floor first - it is an area centre already - and the nearest
    usable one within `CACHE_STAND` when that area is one the player hull does
    not fit in, which is common enough to matter: 7 of tell_coop's 14 rungs and
    6 of ministry_coop's 7 resolve onto an area the hull is refused on.

    Same storey only. An area a little below the floor is that floor rounded;
    one above it is the next storey up, and a cache put there is a cache in the
    ceiling.
    """
    usable = np.flatnonzero(graph.hull_ok & ~graph.blocked)
    if usable.size == 0:
        return None
    c = graph.centers[usable]
    dxy = np.linalg.norm(c[:, :2] - floor[:2], axis=1)
    near = (np.abs(c[:, 2] - floor[2]) <= STOREY) & (dxy <= CACHE_STAND)
    if not near.any():
        return None
    return graph.centers[usable[int(np.where(near, dxy, np.inf).argmin())]]


def _objective_floor(o: Objective, snap: Snapper, graph: NavGraph) -> np.ndarray:
    """Where a player stands to fight objective `o`.

    Its representative area if it has one - `resolve` already picked that from
    *inside* a capture volume rather than by snapping the volume's centre, which
    on cap3 finds the floor 400 u above.
    """
    if o.area is not None:
        return graph.centers[o.area]
    return _floor_of(o.origin, snap, graph)


def build_rungs(survey: Survey, setup: CpSetup, objs: list[Objective],
                snap: Snapper, graph: NavGraph) -> list[Rung]:
    """The stock corridor, as the positions a fight and its two spawns occupy."""
    atk, dfn = setup.attacking_team, 5 - setup.attacking_team
    n = len(objs)

    rungs: list[Rung] = []
    for k in range(n + 1):
        obj = objs[k - 1] if k >= 1 else None
        def_zone = setup.zone_for(k) or "" if k >= 1 else ""
        atk_zone = setup.zone_for(k + 1) or "" if k + 1 <= n else ""
        def_at = _centroid(survey.spawn_zone(def_zone, dfn)) if def_zone else None
        atk_at = _centroid(survey.spawn_zone(atk_zone, atk)) if atk_zone else None

        if obj is not None:
            floor = _objective_floor(obj, snap, graph)
        elif atk_at is not None:
            # Rung 0 has no objective: the attacker entry itself is the position.
            floor = _floor_of(atk_at, snap, graph)
        else:
            floor = np.zeros(3)

        rungs.append(Rung(index=k, floor=floor, objective=obj, def_zone=def_zone,
                          def_at=def_at, atk_zone=atk_zone, atk_at=atk_at))
    return rungs


# ── re-siting one cluster of spawn points onto another rung ──────────

def dest_zone(survey: Survey, setup: CpSetup, rung: int, role: str) -> tuple[str, int, int]:
    """Which spawn zone stood on this rung in this role, and for which stage.

    * defenders on rung k stood in the defender zone of stage k;
    * attackers on rung k stood in the attacker zone of stage k+1, because
      stock stage k+1 is where the attackers respawn on objective k;
    * rung 0 has no defenders and rung N has no stage N+1, and each falls back
      to the one team that did stand there - which is the *other* team, and the
      caller has to notice: a zone can only be borrowed by the team it belongs
      to, so those two ends of the ladder are exactly where a volume has to be
      moved instead.
    """
    atk, dfn = setup.attacking_team, 5 - setup.attacking_team
    n = len(setup.chain)
    if role == "defender":
        # Rung 0 is the attacker entry: the only team that ever stood there is
        # the attackers, and the last objective of a reversal lands on it.
        return ((setup.zones[rung - 1], dfn, rung) if rung >= 1
                else (setup.zones[0], atk, 1))
    # Past the last objective there is no stage N+1, so the ground the
    # attackers enter on is the ground its stock defenders held.
    return ((setup.zones[rung], atk, rung + 1) if rung < n
            else (setup.zones[n - 1], dfn, n))


def dest_points(survey: Survey, setup: CpSetup, rung: int, role: str,
                owner: dict[int, int] | None = None) -> list[Entity]:
    """The authored spawn points of whoever stood on this rung in this role.

    Role-matched rather than name-matched, and that distinction is the whole
    correctness of it. Several zones share a targetname across teams -
    `spawnzone_5` is a team-2 volume at rung 4 *and* a team-3 volume at rung 5 -
    so asking for "the points in spawnzone_5" fetches two different rungs at
    once and the incoming cluster gets scattered down the corridor.

    * defenders on rung k stood in the defender zone of stage k;
    * attackers on rung k stood in the attacker zone of stage k+1, because
      stock stage k+1 is where the attackers respawn on objective k;
    * rung 0 has no defenders and rung N has no stage N+1, and each falls back
      to the one team that did stand there.

    The counts come out matched, which is the point: ministry's attacker zones
    hold 16-17 points and a reversal hands an attacker cluster to another
    attacker cluster's ground.

    **Given `owner`, the pool is narrowed to the stage that actually claimed
    those points.** Zones overlap, so a contested point sits in two of them, and
    without this it is offered as a *destination* to two different stages while
    belonging to only one as a source. That makes the incoming cluster and the
    destination pool non-corresponding sets, their centroids differ, and
    `place_zone` slides the zone by that difference even when the stage is not
    moving at all - which is why the identity layout came back with 431 of
    ministry_coop's 442 points shifted off themselves. Narrowed, seven of the
    thirteen shipped maps round-trip exactly, and no map's verdict changes.

    It falls back to the whole pool where the narrowing empties it, so a rung
    whose ownership cannot be read is no worse off than before.
    """
    name, team, stage = dest_zone(survey, setup, rung, role)
    pts = survey.points_in_zone(name, team)
    if owner is None:
        return pts
    return [q for q in pts if owner.get(id(q)) == stage] or pts


def _boxes(vols: list[Entity], delta: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Where a zone's volumes end up once translated."""
    out = []
    for v in vols:
        box = Survey.world_box(v)
        if box is not None:
            out.append((box[0] + delta, box[1] + delta))
    return out


def _inside(p: np.ndarray, boxes: list[tuple[np.ndarray, np.ndarray]]) -> bool:
    return any(bool(np.all(p >= lo) and np.all(p <= hi)) for lo, hi in boxes)


def _inside_mask(pts: np.ndarray, boxes: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """`_inside` for an (n, 3) array of points at once."""
    m = np.zeros(len(pts), dtype=bool)
    for lo, hi in boxes:
        m |= np.all((pts >= lo) & (pts <= hi), axis=1)
    return m


def _overlaps(a: list[tuple[np.ndarray, np.ndarray]],
              b: list[tuple[np.ndarray, np.ndarray]]) -> bool:
    """Do any two of these boxes share space?

    A point is a box with no size, so this answers `_inside` as well, which is
    what lets a forbidden *place* be written either way round: the objective's
    capture volume where it has one, and the bare floor of its rung where it is
    a cache and has none.
    """
    return any(bool(np.all(alo <= bhi) and np.all(ahi >= blo))
               for alo, ahi in a for blo, bhi in b)


def _zone_split(survey: Survey, name: str, team: int) -> tuple[list[Entity], list[Entity]]:
    """A zone's volumes, split into the ones players spawn in and the rest.

    **A volume holding no spawn points is not a spawn zone.** It is where the
    map keeps something else that has to be inside a zone to work, and on this
    corpus that something is resupply: gioconda_eron_mountains gives security
    six `spawnzone1` volumes, one of them the insertion point with all 32 of the
    team's points, and the other five small pads spread along the route, each
    wrapped round three `models/generic/ammocrate3.mdl` props and holding no
    spawn point at all. Those five are the map's resupply areas.

    A rule matched on class + targetname + team takes all six, so permuting the
    ladder renamed the resupply pads onto whichever stage borrowed the zone -
    stage 14 under `enter3_fwd`, played 2026-09-18 - and the crates stood in
    ground that no longer resupplied anyone for the first thirteen stages. The
    props themselves are `prop_dynamic` and no rule touches them, which is why
    the report was "the crates are there and do nothing".

    So the pads are left where the map put them, and only the volumes that hold
    points are moved or renamed. Where a name has no pads - which is every
    shipped map and most of the workshop ones - this returns everything in the
    first list and nothing changes.
    """
    vols = survey.spawn_zone(name, team)
    pts = [q.origin for q in survey.spawn_points(team)]
    held, pads = [], []
    for v in vols:
        box = Survey.world_box(v)
        if box is None:
            held.append(v)
            continue
        lo, hi = box
        if any(bool(np.all(q >= lo) and np.all(q <= hi)) for q in pts):
            held.append(v)
        else:
            pads.append(v)
    return held, pads


def _zone_moves(survey: Survey, name: str, team: int, **kw) -> tuple[list[Move], list[Entity]]:
    """The edits that carry one zone rule, and the pads they leave behind.

    A `within` box is the only thing that tells two volumes of one name apart,
    and it is asked of the entity's **origin key** - so this can only single a
    volume out where the origins differ. Three shipped maps keep every zone
    origin at (0 0 0) with world-absolute bounds (see `_centroid`), and there
    the question cannot be asked at all: those fall back to the single rule
    they had before, pads and all, and the caller says so.
    """
    held, pads = _zone_split(survey, name, team)
    if not pads or not held:
        return [Move(cls="ins_spawnzone", target=name, team=team, **kw)], []
    keys = [tuple(np.round(v.origin, 2)) for v in held]
    pad_keys = {tuple(np.round(v.origin, 2)) for v in pads}
    if len(set(keys)) != len(keys) or pad_keys & set(keys):
        return [Move(cls="ins_spawnzone", target=name, team=team, **kw)], []
    return [Move(cls="ins_spawnzone", target=name, team=team,
                 within=(v.origin - WITHIN_SLOP, v.origin + WITHIN_SLOP), **kw)
            for v in held], pads


def _allowed_mask(points: list[np.ndarray], snap: Snapper,
                  allowed: np.ndarray | None) -> np.ndarray:
    """Which of `points` stand on an area `allowed` admits; all of them with no mask."""
    if allowed is None:
        return np.ones(len(points), dtype=bool)
    return np.array([(a := snap.snap(p)) is not None and bool(allowed[a])
                     for p in points], dtype=bool)


def _usable(vols: list[Entity], delta: np.ndarray, authored: list[np.ndarray],
            snap: Snapper, graph: NavGraph,
            need: int | None = None,
            allowed: np.ndarray | None = None,
            authored_ok: np.ndarray | None = None) -> tuple[list[np.ndarray], int, float]:
    """Where a team could stand if its zone were moved by `delta`.

    Authored coordinates first - one the engine accepted on the stock map is
    worth strictly more than anything derived - then area centres that pass the
    survey's hull probe. That probe is `TR_TraceHull` with the player hull run
    in game, which is the same test as `CINSRules::IsSpawnPointValid`: the one
    offline predictor §4 of `docs/spawns-and-objectives.md` did not have to
    throw out. So a filled coordinate is not a guess, it is just not a
    precedent.

    Strict containment throughout, because containment *is* the binding: an area
    whose footprint clips the box but whose centre is outside it would be a
    point belonging to no spawn zone.

    `need` is how many points are coming, and given it the fill spacing yields
    rather than starves: one pass at `MIN_SEPARATION`, and only if the pool is
    still short does it go round again at the relaxed distances, which are still
    inside what the shipped maps authored. Nothing already accepted is dropped
    by a later pass, so the first coordinates are still the well-spread ones.
    Returns the spacing it settled at alongside the coordinates.

    `allowed`, where given, is a mask over areas: a coordinate standing on any
    other area is not offered, authored or not. It is how a counter-attack is
    kept inside its budget when its volume is bigger than the budget is.
    `authored_ok` is that mask already asked of `authored`, which does not
    depend on `delta` - so a caller trying many moves need only snap them once.
    """
    boxes = _boxes(vols, delta)
    if authored_ok is None:
        authored_ok = _allowed_mask(authored, snap, allowed)

    # Everything below is asked of whole arrays at once. `place_zone` calls this
    # once per candidate move, dozens of times a stage, and asked point by point
    # it was nine-tenths of what `permute --all` spent.
    inside_auth = (_inside_mask(np.asarray(authored), boxes) if len(authored)
                   else np.zeros(0, dtype=bool))
    keep = [a for a, m, ok in zip(authored, inside_auth, authored_ok) if m and ok]
    n_authored = len(keep)

    pool: set[int] = set()
    for lo, hi in boxes:
        pool.update(int(i) for i in snap.overlapping(lo, hi))
    # Nearest the precedent first, so a pool that runs short is spent beside the
    # authored cluster rather than in the far corner of the volume. With no
    # authored coordinate inside the box at all there is no cluster to sit
    # beside, and the volume's own middle is the only thing left to measure from.
    anchor = (np.mean(keep, axis=0) if keep
              else np.mean([(lo + hi) / 2 for lo, hi in boxes], axis=0))
    # The set's own iteration order, so the stable sort breaks ties as the
    # `sorted()` this replaced did.
    idx = np.fromiter(pool, dtype=np.intp, count=len(pool))
    m = graph.hull_ok[idx] & ~graph.blocked[idx]
    if allowed is not None:
        m &= allowed[idx]
    idx = idx[m]
    idx = idx[_inside_mask(graph.centers[idx], boxes)]
    order = np.argsort(np.linalg.norm(graph.centers[idx] - anchor, axis=1),
                       kind="stable")
    fill = idx[order]
    F = graph.centers[fill]

    # Greedy spacing, in `fill` order: a centre is taken when it is at least
    # `sep` from everything kept so far. `near` is each candidate's distance to
    # the nearest kept point, updated once per point taken rather than measured
    # afresh against all of `keep` for every candidate. A taken centre is 0 from
    # itself, so a later, looser pass cannot take it twice.
    near = np.full(len(fill), np.inf)
    for k in keep:
        near = np.minimum(near, np.linalg.norm(F - k, axis=1))

    sep = FILL_SEPARATIONS[0]
    for sep in FILL_SEPARATIONS:
        t = 0
        while t < len(fill):
            hits = np.flatnonzero(near[t:] >= sep)
            if not hits.size:
                break
            t += int(hits[0])
            c = graph.centers[fill[t]]
            keep.append(c)
            near = np.minimum(near, np.linalg.norm(F - c, axis=1))
            t += 1
        if need is None or len(keep) >= need:
            break
    return keep, n_authored, sep


def place_zone(vols: list[Entity], src_at: np.ndarray, authored: list[np.ndarray],
               need: int, snap: Snapper, graph: NavGraph, *,
               avoid: list[np.ndarray] = (),
               avoid_boxes: list[tuple[np.ndarray, np.ndarray]] = (),
               anchor: np.ndarray | None = None,
               forbid: list[tuple[np.ndarray, np.ndarray]] = (),
               allowed: np.ndarray | None = None):
    """Where to slide a zone, and what its team can stand on once it is there.

    Lining the incoming cluster's centre up with the destination cluster's is
    the obvious move and it is wrong when a zone is several volumes far apart:
    ministry's `spawnzone_6` team 2 is two boxes with the cluster centre in the
    gap between them, so that delta straddles the destination and both boxes
    arrive empty - 0 of 16 points bound. So the move is searched rather than
    computed: put each volume on each authored coordinate in turn, alongside the
    centre-to-centre move.

    What the search maximises is **enough places to stand, nearest to where the
    destination's own cluster was**. Maximising *authored* coverage instead
    picks a far lobe over a near one whenever the far one holds one more
    coordinate, and on ministry_coop that put stage 1's attackers 3,996 u from
    the objective they enter on - a small attacker box cannot cover much of a
    57-point defender cluster wherever it sits, so coverage is the wrong thing
    to be trading position away for. Counting hull-validated fills alongside
    authored ones makes the near placement win, which is the answer the shipped
    ~1,900 u approach says it should be.

    `avoid` and `avoid_boxes` are the leakage pass, empty on the first placement
    and filled on the second: points belonging to another stage that this volume
    must not swallow, and other stages' volumes this zone's own coordinates must
    not sit inside. Binding is containment and nothing else, so either overlap
    hands one stage the other's ground. It is ranked below the coordinate count
    and above precedent: a fill still passes the engine's own hull test, while a
    defender spawning in ground the attackers cleared two stages ago is a round
    that plays wrong.

    `forbid` is space the moved volume must not end up sharing, and unlike
    everything else here it ranks **above** the coordinate count. It has to: an
    arrival re-sited off its own objective is placed against `authored`
    coordinates that all stand on that objective, so every term below this one
    votes to stay - the stock position keeps all twelve precedents and the new
    rung has to fill from area centres. almaden_coop stage 5 is the case, and
    with this ranked any lower the zone came back exactly where it started and
    the layout was refused for a clash the move was supposed to have fixed.

    **It is the objective's own capture volume, not a point on its rung.** The
    rung floor is what detects the clash and it is too small to place against:
    clearing one coordinate leaves the near side of the objective as well as the
    far, and the near side is where the coordinate count wants to go. Measured
    over the corpus with the point, 4 of 48 re-sites emitted between 135 and
    195 u of the objective they were moved off - dust_new `enter3_rev` at 135 u
    is a player spawning against the capture volume's wall - while against the
    volume the same placements have somewhere else to be or are refused.

    It is a preference and not a filter, so a zone with nowhere else to go still
    places; `layout` asks afterwards whether it actually cleared the objective,
    and refuses there.

    Returns `(delta, coordinates, how many were authored, fill spacing, leaks)`.
    """
    dest_at = np.mean(authored, axis=0)
    home = (anchor if anchor is not None else dest_at) - src_at
    cands = [home] + [dest_at - v.origin for v in vols]
    cands += [a - v.origin for v in vols for a in authored]
    if anchor is not None:
        cands += [anchor - v.origin for v in vols]
    avoid_boxes = list(avoid_boxes)
    avoid_pts = np.asarray(avoid, dtype=float).reshape(-1, 3)
    authored_ok = _allowed_mask(authored, snap, allowed)

    best = (home, [], 0, FILL_SEPARATIONS[0], 0)
    best_key = None
    for d in cands:
        coords, n_auth, sep = _usable(vols, d, authored, snap, graph, need, allowed,
                                      authored_ok)
        leaks = 0
        if len(avoid) or avoid_boxes:
            leaks = int(_inside_mask(avoid_pts, _boxes(vols, d)).sum())
            if coords[:need]:
                leaks += int(_inside_mask(np.asarray(coords[:need]), avoid_boxes).sum())
        # Enough is enough: past the incoming point count another coordinate
        # buys nothing, so it stops competing with position.
        enough = min(len(coords), need)
        far = -float(np.linalg.norm(d - home))
        # Given a rung to stand on, standing on it outranks covering more of
        # the pool. The pool here is the other team's whole zone, so the
        # coverage term is precisely what drags the cluster off the rung - it is
        # kept, one bucket down, to choose between placements that are equally
        # on it.
        on_rung = None
        if anchor is not None:
            at = (np.mean(coords[:need], axis=0) if coords
                  else np.mean([v.origin + d for v in vols], axis=0))
            on_rung = -int(float(np.linalg.norm(at - anchor)) // ANCHOR_BUCKET)

        if not forbid:
            key = ((enough, -leaks, on_rung, n_auth, far) if on_rung is not None
                   else (enough, -leaks, n_auth, far))
        else:
            # A placement that keeps the clash is not a better placement, it is
            # the thing being fixed, so `clear` outranks everything.
            #
            # And with a rung to go to, that outranks the coordinate count too -
            # which it does nowhere else here. The two rungs in a clash are
            # neighbours by construction, since the volume on one covers the
            # other, so "clear of the objective" leaves the near side of it as
            # well as the far, and the count picks the near side every time:
            # almaden_coop's stage 5 kept all twelve precedents 249 u off the
            # objective it is attacking, over the placement that stands on rung
            # 4 with nine. Nine is above `MIN_POINTS`, `_assign` cycles the other
            # three onto coordinates already taken, and standing on the right
            # rung is the whole of what the re-site is for.
            #
            # `viable` is the floor under that trade. Promoting the rung over
            # the count promotes it over an empty box too, and a bucket is
            # 128 u, so a placement with nowhere to stand could win by standing
            # 128 u nearer the rung - a layout refused for no coordinates in
            # place of one refused for the clash, which is no progress. A
            # placement that cannot seat `MIN_POINTS` is not a placement, and
            # the count goes back on top for choosing among the rest.
            clear = 0 if _overlaps(_boxes(vols, d), forbid) else 1
            viable = 1 if enough >= min(MIN_POINTS, need) else 0
            key = ((clear, viable, on_rung, enough, -leaks, n_auth, far)
                   if on_rung is not None
                   else (clear, enough, -leaks, n_auth, far))
        if best_key is None or key > best_key:
            best, best_key = (d, coords, n_auth, sep, leaks), key
    return best


def claim_points(survey: Survey, setup: CpSetup) -> tuple[dict[int, int], int, int]:
    """Which stage owns each spawn point, where two stages' zones overlap.

    Spawn zones overlap, and a point inside two of them serves both stages -
    the engine re-evaluates containment every time a zone is enabled, so in
    stock play the point simply belongs to whichever stage is live. Measured on
    the two linear maps with a cpsetup: ministry_coop has 2 such points of 442,
    market_coop 128 of 633. §6 of `docs/spawns-and-objectives.md` hit the same
    thing from the file side - "the moved volume also encloses points that used
    to serve other stages, so the stage-1 pool is larger than the 56 that
    moved".

    A reversal cannot leave that ambiguity in place, because it sends the two
    stages to different rungs and a point can only be at one of them. So a
    contested point goes to the stage whose volume it sits **deepest** inside:
    a point 400 u into one zone and 8 u into another is that first zone's
    point, and the rule is geometric rather than an order of iteration.

    Keyed by object identity: a spawn point carries no targetname and two of
    them can share an origin, so the entity itself is the only handle there is.

    Returns `(id(point) -> stage, contested, unclaimed)`.
    """
    owner: dict[int, int] = {}
    depth: dict[int, float] = {}
    contested = 0
    teams = sorted({p.team for p in survey.spawn_points()})
    for j, name in enumerate(setup.zones, start=1):
        for team in teams:
            boxes = [b for z in survey.spawn_zone(name, team)
                     if (b := Survey.world_box(z)) is not None]
            for pt in survey.points_in_zone(name, team):
                key = id(pt)
                # How far inside the zone it sits - the distance to the nearest
                # face of the box that holds it, over every volume of the name.
                inside = max(
                    (float(np.min(np.minimum(pt.origin - lo, hi - pt.origin)))
                     for lo, hi in boxes), default=-np.inf)
                if key in owner:
                    contested += 1
                    if inside <= depth[key]:
                        continue
                owner[key] = j
                depth[key] = inside
    unclaimed = sum(1 for pt in survey.spawn_points() if id(pt) not in owner)
    return owner, contested, unclaimed


def _assign(src: list[Entity], dest: list[np.ndarray],
            n_authored: int | None = None) -> list[np.ndarray]:
    """Match an incoming cluster onto destination coordinates, inner to inner.

    Rank both by distance from their own centre and zip. It keeps the shape of
    the cluster - a point that was deep in the room stays deep in it - and it is
    stable, so the same survey emits the same preset. Surplus points cycle back
    through the list rather than being dropped: a duplicate coordinate costs a
    spawn choice, a dropped point costs a player a spawn.

    **Authored coordinates are spent before any fill.** `_usable` returns them
    first and hull-validated fills after, and ranking the whole pool by distance
    from its centre threw that order away: a point could be handed a coordinate
    the survey's hull probe invented while its own authored one went unused. The
    identity layout is the case where that is visible, because there the
    destination pool *is* the source cluster - and it came back with 431 of
    ministry_coop's 442 points moved off themselves. Tiering it fixes that case
    exactly and uses more precedent in every other one.
    """
    if not dest:
        return []
    if n_authored is None:
        n_authored = len(dest)
    n_authored = max(0, min(n_authored, len(dest)))

    sc = np.mean([p.origin for p in src], axis=0)
    # Both tiers are ranked against the authored centre, so a fill's rank is
    # comparable to an authored coordinate's rather than measured from a
    # different middle.
    dc = np.mean(dest[:n_authored] if n_authored else dest, axis=0)
    authored = sorted(range(n_authored),
                      key=lambda i: float(np.linalg.norm(dest[i] - dc)))
    fills = sorted(range(n_authored, len(dest)),
                   key=lambda i: float(np.linalg.norm(dest[i] - dc)))
    d_order = authored + fills

    s_order = np.argsort([float(np.linalg.norm(p.origin - sc)) for p in src])
    out: list[np.ndarray] = [np.zeros(3)] * len(src)
    for rank, si in enumerate(s_order):
        out[si] = dest[d_order[rank % len(d_order)]]
    return out


# ── how far a stage asks its attackers to walk ───────────────────────

def rung_areas(rungs: list[Rung], snap: Snapper,
               graph: NavGraph | None = None) -> list[int]:
    """The nav area standing for each rung, or -1 where nothing does.

    An objective already resolved one from *inside* its capture volume, which is
    a better answer than snapping the rung's floor a second time - **as long as
    a walk can get to it.**

    It could not, on de_aztec_coop. `cp_b` covers 22 areas, 21 of them ordinary
    floor with 2-11 connections each, and the one it resolved is area 619: zero
    incoming edges and 110 u above the other 21, a ledge you can drop off and
    cannot climb. Measured from there the objective is infinitely far from
    everything, one infinite pair makes every tour through it infinite, and
    `walk_family` gives the whole map up and rotates the shipped chain instead -
    so all 16 of aztec's walks carried its 7.56x-21.68x detour and every one was
    refused. The ground was never the problem: all eight rungs snap into the
    same 1,234-area component.

    An island counts as unreachable for the same reason, and it is the other
    half of this: inferno_new_v1's `cp5` resolved to area 441, which has two
    connections and is in a **13-area component** of its own, 5 u above the
    room's floor and joined to none of the 1,568 areas around it. So a rung is
    measured from an area with a way in *and* in the map's main body, and cp5
    covers ten such areas at z=160 under the ones it was using at z=165.

    So a rung whose area nothing can walk into takes another of the areas its
    own objective covers - the nearest one with a way in, preferring ground a
    player hull fits on. Only that case changes; where the resolved area is
    reachable, which is most of the corpus, this returns exactly what it used
    to. Rung 0 has no objective and so no second candidate: it is snapped as
    before, and a map whose *entry* is exit-only still falls back.
    """
    into = main = None
    if graph is not None:
        into = np.zeros(graph.n, dtype=bool)
        into[graph.edge_col] = True
        main = graph.largest_component()

    def reachable(a: int) -> bool:
        if into is None or not (0 <= a < len(into)):
            return into is None
        return bool(into[a]) and bool(main[a])

    out = []
    for r in rungs:
        obj = r.objective
        if obj is not None and obj.area is not None:
            a = int(obj.area)
            if not reachable(a):
                here = graph.centers[a] if graph is not None else r.floor
                cand = [c for c in obj.areas if c != a and reachable(int(c))]
                if cand:
                    cand.sort(key=lambda c: (not bool(graph.hull_ok[c]),
                                             float(np.linalg.norm(graph.centers[c] - here))))
                    a = int(cand[0])
            out.append(a)
        else:
            s = snap.snap(r.floor)
            out.append(int(s) if s is not None else -1)
    return out


def rung_distances(rungs: list[Rung], snap: Snapper, graph: NavGraph) -> np.ndarray:
    """Path distance between every pair of rungs, `inf` where there is no route.

    Directed, because the mesh is: a drop you can fall down but not climb is a
    real edge one way only, and a stage's attackers walk one particular way.
    Rows and columns for a rung with no area under it are `inf` throughout, which
    is how `verticality_coop`'s cp3 shows up - its *stock* stages measure
    infinite too, so it is the survey's problem and not the layout's.
    """
    areas = rung_areas(rungs, snap, graph)
    n = len(areas)
    D = np.full((n, n), np.inf)
    live = [i for i, a in enumerate(areas) if a >= 0]
    for i in live:
        d = graph.distances(areas[i])
        for j in live:
            D[i, j] = d[areas[j]]
    np.fill_diagonal(D, 0.0)
    return D


def _reach(pts: list[np.ndarray], dist: np.ndarray, snap: Snapper,
           q: float = COUNTER_SLOWEST) -> float:
    """How far a cluster's counter-attack walks: the path distance from its
    points to the rung `dist` was measured from, at the `q`th percentile - the
    slowest of the wave, not its middle, since a zone is a volume and a big one
    spawns bots in its far corner too. `inf` for a cluster with no point on the
    mesh, which no rule can accept - and `inf` where the percentile falls among
    points with no path at all, which interpolating between two `inf`s would
    otherwise make NaN: a NaN compares false against every budget, so the
    stages furthest out of reach were the ones that passed."""
    areas = [snap.snap(p) for p in pts]
    d = [float(dist[a]) for a in areas if a is not None]
    if not d:
        return float("inf")
    r = float(np.percentile(d, q))
    return float("inf") if np.isnan(r) else r


def _along(graph: NavGraph, src: int, dst: int, at: float) -> np.ndarray | None:
    """The floor `at` units down the mesh path from area `src` towards `dst` -
    or the far end, where the path is shorter than that."""
    path = graph.path(src, dst) if src >= 0 and dst >= 0 else []
    if not path:
        return None
    walked = 0.0
    for a, b in zip(path, path[1:]):
        walked += float(np.linalg.norm(graph.centers[b] - graph.centers[a]))
        if walked >= at:
            return graph.centers[b].copy()
    return graph.centers[path[-1]].copy()


def measure_advances(plan: Plan, D: np.ndarray) -> tuple[list[dict], list[float]]:
    """Each stage's advance under `plan`, and the stock advances to read it against.

    A stage's advance is the distance from the rung its attackers spawn on to the
    rung its objective sits at - which stock authored as one step along the
    ladder, and which any walk starting mid-corridor has to make one long step
    of somewhere.

    `D` is expected to be `mesh_or_ground`'s answer rather than `rung_distances`'
    own, for the reason written there: `worst_advance` takes the max over the
    *finite* advances, so a stage the mesh has no route for does not make a walk
    look bad here, it makes it disappear from the measurement.
    """
    n = len(plan)
    advances = []
    for j in range(1, n + 1):
        frm, to = plan.at(j - 1), plan.at(j)
        advances.append({"stage": j, "from_rung": frm, "to_rung": to,
                         "advance": float(D[frm, to])})
    stock = [float(D[k - 1, k]) for k in range(1, n + 1)]
    return advances, stock


def mesh_or_ground(rungs: list["Rung"], D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`D`, with every unreachable pair replaced by the straight line between them.

    **An advance the mesh cannot walk is not a free advance, and that is how it
    was being read.** `worst_advance` and `stock_max` both take the max over the
    finite advances, `_detour` skips a triple with a non-finite leg, and
    `min_advance` takes the min of the finite hops - so a stage with no route
    between its ends was dropped out of all four judgements rather than failing
    any of them. A walk that asks for one therefore measured *better* than the
    walks that stayed on the mesh.

    **"Unreachable" here is a fact about the surveyed graph, not about the map.**
    A player walks these pairs; the mesh simply has no link drawn between them,
    and a bot would be as stuck as this matrix is. So the pair cannot be thrown
    away as impossible, and it cannot be taken as free either - it has to be
    measured with whatever the survey does know, which is where the two rungs
    stand.

    l4d2_bridge is the map that showed it. Nothing in its mesh reaches rung 6,
    and nothing leaves rung 0, so of the 16 walks the family builds, all nine
    that passed carried an unreachable stage - and seven carried `0->7`, which
    is one end of the bridge to the other. Measured on the mesh: dropped.
    Measured on the ground: 20,098 u against a stock worst of 5,245, which
    `max_advance` refuses at 3.83x. The one that survives is the reversal, whose
    single unlinked leg is `7->6` at 2,614 u - two rungs standing beside each
    other. Which is the whole distinction: the same missing link is nothing on
    one walk and the length of the bridge on another, and only the ground can
    tell them apart.

    The straight line is the right stand-in because it is a *lower bound* on any
    walk between two points: whatever route a player finds, they cannot beat it.
    So this can only ever under-state a stage, never invent one - and it says so
    per stage, since a pair the survey cannot walk is worth knowing about
    whether or not it refuses anything.

    Returns the patched matrix and a boolean mask of what was patched.
    """
    P = np.array([r.floor for r in rungs], dtype=float)
    ground = np.linalg.norm(P[:, None, :] - P[None, :, :], axis=-1)
    missing = ~np.isfinite(D)
    return np.where(missing, ground, D), missing


# ── the layout ───────────────────────────────────────────────────────

def layout(
    survey: Survey,
    setup: CpSetup,
    plan: Plan | None = None,
    **kw,
) -> Layout:
    """Build one layout - see `_layout` - re-siting far counter-attacks where
    that costs nothing.

    **The counter-attack budget is a target, and a target cannot refuse a
    layout.** Re-siting a far counter-attack borrows or moves a defender zone,
    and that volume can cover the ground another stage's defenders were going to
    stand on. On district_coop_old_fixbysakey it cost the reversal: stage 4's
    counter-attack was 32 s against the 30 s budget, the zone borrowed for it
    covered rung 4, and stage 2's 88 defenders were left 0 coordinates - on a
    map whose own stock walk counter-attacks from 46 s out. glycencity,
    karkand_redux_p2 and oilfield_pve lost every layout they had the same way.

    So a layout the re-site refused is built again with every counter-attack
    left where the permutation put it, and that one is kept if it passes - with
    the far stages warned about, as any stage the re-site could not help is.
    """
    lay = _layout(survey, setup, plan, **kw)
    if lay.ok or not any("counter_from" in st for st in lay.stages):
        return lay
    plain = _layout(survey, setup, plan, resite_counters=False, **kw)
    if not plain.ok:
        return lay
    why = next(w for w in lay.warnings if w.startswith("REFUSED"))
    plain.warnings.append(
        f"counter-attacks left unmoved: re-siting them refused this layout "
        f"({why.removeprefix('REFUSED: ')})")
    return plain


def _layout(
    survey: Survey,
    setup: CpSetup,
    plan: Plan | None = None,
    *,
    graph: NavGraph | None = None,
    blockzones: str = "follow",
    max_advance: float | None = None,
    min_advance: float | None = None,
    min_separation: float | None = None,
    max_detour: float | None = None,
    rename: bool = True,
    resite_counters: bool = True,
) -> Layout:
    """Build one layout of a map: which rung each stage is fought at.

    `plan` defaults to the reversal, which is what this module was written for.
    `blockzones` is `follow` (translate each with the defender zone it sits on)
    or `leave` (emit no edit for them). Neither is right, because a restricted
    area guards a side and every layout here swaps which side that is; `follow`
    at least keeps them near the fight.

    `max_advance` turns the advance report into a refusal, as a multiple of the
    longest advance the stock map itself asks of a stage. Off by default: an
    advance is a judgement about how a round plays, and the refusals in here are
    otherwise all facts about whether the engine will load it.

    All four of those judgements are *stated* rather than enforced when the plan
    is the reversal, because refusing the one walk a chain defines leaves a map
    with nothing rather than with something better. See the gate block below.

    `min_advance` and `max_detour` are the other two halves of that judgement,
    and they are the ones that decide whether a round reads as a progression.
    A stage whose advance is under `min_advance` is captured on arrival - the
    map spends an objective and gets no fight for it - and a stage over
    `max_detour` sends the round away and brings it straight back. Both are off
    by default for the same reason `max_advance` is; `navlayout permute` sets
    all three from its own calibrated defaults, because a preset file is a
    judgement about how rounds play and this module is not.

    `rename` is how a stage gets its ground, and it is on by default. A rung
    already carries the zone the map authored for it, so a stage fought there
    can be given *that* zone - by name, since the cpsetup binds a stage to a
    targetname and the applier writes the lump before the entities exist. Then
    nothing moves: no volume slides, no spawn point is re-sited, and every
    coordinate in the round is one the shipped map placed there itself. Only the
    two ends of the ladder have no zone of the right team to borrow, and only
    they fall back to moving a volume. `rename=False` moves every zone, which is
    what this module did before, and is kept because it is the path that still
    works when a rung has no zone at all.
    """
    graph = graph or NavGraph.build(survey)
    snap = Snapper(survey)
    objs = resolve(survey, setup.chain)
    n = len(objs)
    atk, dfn = setup.attacking_team, 5 - setup.attacking_team

    if plan is None:
        plan = reverse_plan(n)

    rev = Layout(map=survey.map, chain=[o.name for o in objs], zones=list(setup.zones),
                 attacking_team=atk, rungs=[], plan=plan)

    if n < 3:
        rev.warnings.append(f"REFUSED: a chain of {n} is not a corridor to permute")
        return rev
    if len(setup.zones) < n:
        rev.warnings.append(
            f"REFUSED: cpsetup maps {len(setup.zones)} spawn zones to {n} objectives, "
            "so a stage's spawns cannot be identified"
        )
        return rev
    if bad := plan.valid(n):
        rev.warnings.append(f"REFUSED: {bad}")
        return rev

    rev.rungs = build_rungs(survey, setup, objs, snap, graph)
    rungs = rev.rungs

    # ── what the layout asks of each stage, before anything is emitted ──
    #
    # **These four gates say what they found; on the reversal they do not
    # refuse.** A gate about how a round plays is an instruction to pick a
    # different walk, and on a family there is one to pick: `rank` drops the
    # poor member and rotates the rest. The reversal is not a member of
    # anything - it is the one walk the chain itself defines, and refusing it
    # leaves the map with no layout at all rather than with a better one. That
    # was already the policy for a corridor, where `navlayout reverse` passed
    # no gates and every refusal in here was a fact about whether the engine
    # would load the map; it was not the policy for the same walk on an open
    # map, which reached `layout` through `permute` and its calibrated
    # defaults. Measured over the 125-map corpus 2026-09-19: 29 maps have a
    # reversal these four refuse, and on 25 of them `layout_score` ranks it out
    # anyway - launch_control_coop_ws detours 9.31x where its best walk scores
    # 1.53. The four it changes are cobblestone, district_coop,
    # embassy_coop_141103 and panama_canal_b2, which have nothing else and so
    # ship nothing. So the ranking decides between walks and the gate decides
    # whether a walk can be played at all, which is what each is for.
    #
    # It is still said, in the same words: a reversal that sends a stage 3.64x
    # off the straight line is a thing a playtest should be told before it is
    # blamed on the layout.
    def _judgement(msg: str) -> None:
        rev.warnings.append(
            msg + " - said rather than refused, because the reversal is the one "
            "walk this chain defines and there is no other to pick instead"
            if plan.name == "reversed" else "REFUSED: " + msg)

    # Every measurement below reads `D`, the mesh patched with the ground where
    # the mesh has no route: an advance the survey cannot walk is a stage this
    # layout asks for like any other, and dropping it out of the maxima was how
    # l4d2_bridge came to ship seven walks that cross the whole bridge in one
    # step. `mesh` keeps the unpatched answer for the report. See
    # `mesh_or_ground`.
    mesh = rung_distances(rungs, snap, graph)
    D, unlinked = mesh_or_ground(rungs, mesh)
    rev.advances, rev.stock_advances = measure_advances(plan, D)
    for a in rev.advances:
        if unlinked[a["from_rung"], a["to_rung"]]:
            a["on_ground"] = True
    if (crossed := [a for a in rev.advances if a.get("on_ground")]):
        rev.warnings.append(
            f"{len(crossed)} stage(s) cross ground the surveyed mesh draws no "
            "link over, so their advance is the straight line between the two "
            "rungs - the least the walk can be, since a player who has a route "
            "cannot beat it: "
            + ", ".join(f"stage {a['stage']} rung {a['from_rung']}->"
                        f"{a['to_rung']} {a['advance']:.0f} u" for a in crossed[:4])
            + (" ..." if len(crossed) > 4 else "")
        )
    worst, stock_worst = rev.worst_advance, rev.stock_max
    if np.isfinite(worst) and np.isfinite(stock_worst) and stock_worst > 0:
        ratio = worst / stock_worst
        if max_advance is not None and ratio > max_advance * (1 + GATE_TIE):
            _judgement(
                f"worst stage advance {worst:.0f} u is {ratio:.2f}x the "
                f"stock worst of {stock_worst:.0f} u, over the {max_advance:.2f}x limit"
            )
        elif ratio > 1.0 + GATE_TIE:
            rev.warnings.append(
                f"worst stage advance {worst:.0f} u is {ratio:.2f}x the stock worst "
                f"of {stock_worst:.0f} u - a walk that does not start at an end of "
                f"the corridor doubles back for the rungs it passed"
            )
    elif not np.isfinite(worst):
        # `mesh_or_ground` leaves nothing infinite that has two rung positions
        # to measure between, so reaching here means a rung has no position at
        # all - the survey found no floor for it, and there is no ground to
        # fall back to any more than there was a mesh.
        rev.warnings.append(
            f"{sum(1 for a in rev.advances if not np.isfinite(a['advance']))} "
            "stage advance(s) cannot be measured at all: neither the mesh nor "
            "the ground has a position for one end, which is the survey to look "
            "at rather than this walk"
        )

    # Shape: is this walk a progression, and does every stage earn its place?
    # Read against the stock walk, which is the only baseline the map itself
    # authored - and which an open map can be worse at than its own family.
    E = rung_gaps(rungs)
    rev.metrics = plan_metrics(plan, D, E)
    rev.stock_metrics = plan_metrics(stock_plan(n), D, E)
    gap, detour = rev.min_advance, rev.worst_detour
    # ...and read against it in the literal sense: a gate refuses a walk for
    # being worse than the map, never for being as bad as the map already is.
    # The bar is the calibrated limit or the stock walk's own number, whichever
    # lets more through - which is what `max_advance` has always done, as a
    # multiple of the stock worst, and what the other three did not. Measured
    # over the 124-map server list 2026-09-19: 56 of the 115 maps with a stock
    # measurement ship a round one of these three would refuse - de_aztec_coop
    # detours 8.21x, bridge_coop_2021 3.60x, pripyat 3.09x against a 2.50x bar -
    # so the gates were withholding layouts that improve on what the map plays
    # today. 796 layouts before, 939 after, and no layout that emitted before
    # stops: relaxing a bar cannot refuse anything it was passing.
    floor_advance = (None if min_advance is None
                     else _no_worse_floor(min_advance, rev.stock_min_advance))
    if floor_advance is not None and np.isfinite(gap) \
            and gap < floor_advance * (1 - GATE_TIE):
        near = [a["stage"] for a in rev.advances
                if np.isfinite(a["advance"]) and a["advance"] < floor_advance]
        _judgement(
            f"stage {near[0]} advances {gap:.0f} u, under the "
            f"{floor_advance:.0f} u a stage needs to be a fight rather than a "
            f"walk-on{_than_stock(floor_advance, min_advance)}; {len(near)} of {n} are"
        )
    # The same refusal read off the ground rather than the mesh. A stage whose
    # objective stands next to the one before it is captured on arrival however
    # far round the walk went to reach it - and the walk is all `min_advance`
    # sees. See `SHORT_ADVANCE`.
    sep = rev.min_separation
    floor_sep = (None if min_separation is None
                 else _no_worse_floor(min_separation, rev.stock_min_separation))
    if floor_sep is not None and np.isfinite(sep) and sep < floor_sep * (1 - GATE_TIE):
        gaps = rev.metrics.get("gaps", [])
        near = [i + 2 for i, g in enumerate(gaps) if g < floor_sep]
        stage = near[0] if near else 1
        walked = rev.metrics.get("hops", [float("nan")])[stage - 1]
        _judgement(
            f"stage {stage}'s objective stands {sep:.0f} u across the floor "
            f"from the one before it, on the same level, under the "
            f"{floor_sep:.0f} u two objectives need to be two places"
            f"{_than_stock(floor_sep, min_separation)}; the walk "
            f"between them is {walked:.0f} u, which is the mesh going round what a "
            f"player can see across. {len(near)} of {n} are"
        )
    # `np.isnan` rather than `np.isfinite`: NaN is "there was no triple to
    # measure", which is not a refusal, but `inf` is the worst detour there is
    # and used to walk straight through this test. See `_detour`.
    # The ceiling relaxes to the stock walk the same way the two floors do, with
    # one exception at each end. A *finite* detour is measured against a stock
    # round that may itself be infinite, and anything finite is better than
    # being returned to the ground you were on, so that relaxes to no ceiling at
    # all. An *infinite* one never relaxes: an advance of nothing has no
    # multiple, the two ends of the stage are one place, and a map shipping that
    # is not a licence to ship it again. See `_detour`.
    ceil_detour = (None if max_detour is None
                   else max(max_detour, rev.stock_worst_detour)
                   if not np.isnan(rev.stock_worst_detour) else max_detour)
    if ceil_detour is not None and not np.isnan(detour) \
            and (not np.isfinite(detour) or detour > ceil_detour * (1 + GATE_TIE)):
        if np.isfinite(detour):
            _judgement(
                f"a stage sends the round {detour:.2f}x further than going "
                f"straight on would, over the {ceil_detour:.2f}x limit"
                f"{_than_stock(ceil_detour, max_detour)} - the round "
                f"doubles back rather than progressing"
            )
        else:
            # `rev.warnings` rather than `_judgement`: this one does not soften
            # for the reversal either, for the reason it does not relax to the
            # stock walk. A round returned to the ground it has just taken has
            # advanced nothing, and there is no multiple of nothing to weigh
            # against anything else - so it is not a walk that ranks poorly, it
            # is a walk with a stage missing from it.
            held, stage, ra, rc, apart, walked = _returns(plan, D, E)
            rev.warnings.append(
                f"REFUSED: stage {stage} is fought on the ground {held} has "
                f"just taken - rungs {ra} and {rc} stand {apart:.0f} u apart on "
                f"one level - so the round walks {walked:.0f} u out to the one "
                f"stage between them and back to where it started. An advance "
                f"of nothing has no multiple, and this is the worst detour "
                f"there is"
            )

    # ── does a stage's arrival land on its own objective? ────────────
    #
    # The ladder says attacker zone k+1 stands on rung k, and on a corridor it
    # does. It is a convention, not a guarantee, and tell_open_coop breaks it:
    # sz_a and sz_b share one team-2 volume, so "the ground stage 2's attackers
    # stood on" is rung 0 rather than rung 1. Permuting under that assumption
    # put stage 1's objective and its 38 defenders on rung 0 while stage 1's
    # attackers arrived in the same box - 23 of those defender points inside
    # it, and the cache 427 u from its centre. Which is a round that starts
    # with the player spawning among the enemy, looking at the objective.
    #
    # So it is checked rather than assumed, against where the volumes actually
    # are. **It used to be a refusal outright, and that was one answer too
    # blunt.** Measured over the 1,887 layouts in the corpus it took 53 of them,
    # every one a reversed walk, and 16 died of nothing else - which cost
    # almaden_coop, dead_air, de_aztec_coop and liberty_mall their only layout
    # and four other maps a member of their family.
    #
    # What the clash actually says is narrower than "this walk is bad": the
    # *borrow* is bad. Stage j's attackers are meant to stand on the objective
    # they have just taken, and the ladder names the zone authored one rung
    # along to put them there; the defect is only that the named volume happens
    # to sit over the rung stage j is now fought at. Moving a volume onto the
    # previous rung's own floor keeps the intent and drops the clash, and the
    # machinery for it is already here - it is what the ends of the ladder do,
    # where `dest_zone` answers with the other team's zone and there is nothing
    # to borrow.
    #
    # So the clash is recorded here and forces `move` below rather than
    # refusing, and the refusal moves to after the placement, where it can ask
    # whether the move actually got the cluster off the objective. A map with no
    # floor to move to is refused exactly as before.
    resite: dict[int, tuple[int, int, str]] = {}
    for j in range(1, n + 1):
        here, from_rung = plan.at(j), plan.at(j - 1)
        zname, zteam, _ = dest_zone(survey, setup, from_rung, "attacker")
        vols = survey.spawn_zone(zname, zteam)
        if not vols or zteam != atk:
            continue                      # no authored ground; a volume moves
        boxes = _boxes(vols, np.zeros(3))
        if _inside(rungs[here].floor, boxes):
            resite[j] = (here, from_rung, zname)

    owner, contested, unclaimed = claim_points(survey, setup)
    # The move each stage's defender zone ends up making, so a restricted area
    # bound to it can follow the same one rather than a re-derivation of it.
    def_delta: dict[int, np.ndarray] = {}
    def_at: dict[int, np.ndarray] = {}
    rev.contested = contested
    rev.unclaimed = unclaimed

    # ── objectives: slot j takes the rung the plan gives it ──────────
    # Where each objective ends up, as a box, for the re-sited arrivals below:
    # "not on the objective" is a question about the capture volume, and this is
    # the loop that knows where the capture volume goes. A cache has no volume,
    # so its rung's floor stands in for one - a box with no size, which
    # `_overlaps` answers the same way.
    obj_box: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
    for j in range(1, n + 1):
        o = objs[j - 1]
        dest = rungs[plan.at(j)]
        src_floor = _objective_floor(o, snap, graph)
        delta = dest.floor - src_floor
        obj_box[j] = ([(o.anchor.origin + delta + o.anchor.mins,
                        o.anchor.origin + delta + o.anchor.maxs)]
                      if o.kind != "cache" and o.anchor.mins is not None
                      else [(dest.floor, dest.floor)])

        entry = {"order": j, "name": o.name, "kind": o.kind,
                 "to_rung": dest.index,
                 "to": dest.objective.name if dest.objective else "attacker entry"}

        if o.kind == "cache":
            # A cache stands on the floor and its marker floats above it by the
            # map's own offset, not by an assumed 72.
            #
            # **Stood on its rung's floor, not translated onto it** - the same
            # correction the marker below gets, for the same reason. Translating
            # carries the cache's offset from its *own* rung's floor point over
            # to the destination, and both ends of that are an area centre, so
            # the two errors add: measured over tell_coop's family, 18 of 156
            # cache placements came out 157-289 u above the floor - a cache
            # hanging in the air over the room it belongs in - while the shipped
            # caches sit within 18 u of their own area centre. A cache is a
            # point entity with no volume to preserve the shape of, so there is
            # nothing translation buys back: stand it on ground the hull fits on
            # and the round can reach it.
            at = o.anchor.origin + delta
            moved = float(np.linalg.norm(delta)) > 1.0
            if moved:
                stand = _cache_stand(dest.floor, snap, graph)
                if stand is not None:
                    # The map's own lift for *this* cache, which is a rounding
                    # rather than a design - the six on tell_coop are -18 to +9.
                    lift = float(o.anchor.origin[2] - src_floor[2])
                    at = stand + np.array(
                        [0.0, 0.0, lift if abs(lift) <= STOREY else 0.0])
                    entry["cache_resited"] = True
                else:
                    rev.warnings.append(
                        f"{o.name} is a cache moving to rung {dest.index} and "
                        f"there is no floor the player hull fits on within "
                        f"{CACHE_STAND:.0f} u of it, so it is left where the "
                        "translation dropped it - which may be off the mesh"
                    )
            rev.objectives.append(ObjectiveMove(
                name=o.anchor.target,
                cp=o.marker.target,
                origin=at,
                cp_offset=float(o.marker.origin[2] - o.anchor.origin[2]),
                frm=o.anchor.origin.copy(),
                cp_from=o.marker.origin.copy(),
                note=f"{o.name} -> rung {dest.index}",
            ))
            entry["coverage"] = 1 if snap.snap(at) is not None else 0
        else:
            new_origin = o.anchor.origin + delta
            # A capture volume need not carry a targetname - all six of
            # congress_coop's are nameless - and a rule with only a class
            # matches every entity of that class. The applier gives each entry
            # to its first matching rule, so one nameless rule claims every
            # volume on the map and the rest read as shadowed, which the
            # read-back reports without counting as a failure. Single the
            # entity out by the origin it has *before* the edit, the way a
            # spawn point is.
            rev.moves.append(Move(cls=o.anchor.cls, target=o.anchor.target,
                                  within=None if o.anchor.target else
                                  (o.anchor.origin - WITHIN_SLOP,
                                   o.anchor.origin + WITHIN_SLOP),
                                  origin=new_origin, note=entry["to"]))
            # Volumes translate but do not resize, so the only offline question
            # is whether the box still covers floor. Empty is the server's
            # "Failed finding CP area for N!".
            def _covers(at: np.ndarray) -> list[int]:
                if o.anchor.mins is None:
                    return []
                return [int(i) for i in snap.overlapping(at + o.anchor.mins,
                                                         at + o.anchor.maxs)
                        if graph.hull_ok[i] and not graph.blocked[i]]

            walkable = _covers(new_origin)
            cover = len(walkable)
            entry["coverage"] = cover
            # The same box where the map itself put it. A volume that covers no
            # hull-valid floor at its own shipped origin is a fact about the
            # map, not about this layout - tell_open_coop's cz_p is 144x240 u
            # over ten areas a player does not fit in, and refusing that
            # refuses the identity layout, which is the one thing this
            # operation is checked against. The marker test below already
            # draws this distinction; this one did not.
            entry["stock_coverage"] = len(_covers(o.anchor.origin))

            # The marker is a separate entity and it is what the gamemode looks
            # the objective's nav area up from, so it gets placed rather than
            # translated. A marker floats above its volume - ministry_coop's cp4
            # by 137 u, market_coop's cp_f by 433 - and a float that was over a
            # floor at one end of the corridor is over a stairwell, a roof or
            # nothing at the other. Translating it is what loses an objective
            # its area; standing it on floor the objective itself covers cannot.
            marker_at = o.marker.origin + delta
            moved = float(np.linalg.norm(delta)) > 1.0
            # Unconditionally, for a marker that moves at all. This used to run
            # only when the translated marker came in over MARKER_GAP, and
            # rotating inferno_new_v1's family is what killed that: of 70 marker
            # placements the engine refused 10, every one of them a marker
            # translated because its gap passed the threshold, and it accepted
            # all 28 that had been re-sited. The threshold cannot separate them
            # - it is a distance to the nearest area *centre*, so it adds a
            # horizontal error to a vertical one, while the engine accepts a
            # marker 232 u above its floor and refuses one 32 u out past the
            # edge of it. There is nothing to gain by testing first: standing
            # the marker on floor the objective itself covers is never worse
            # than leaving it wherever the translation dropped it. See
            # docs/permute.md §6.
            if moved and walkable:
                centers = snap.center[walkable]
                nearest = walkable[int(np.linalg.norm(
                    centers[:, :2] - marker_at[:2], axis=1).argmin())]
                marker_at = snap.center[nearest] + np.array([0.0, 0.0, MARKER_LIFT])
                entry["marker_resited"] = True
            rev.moves.append(Move(cls="point_controlpoint", target=o.marker.target,
                                  origin=marker_at, note=entry["to"]))
            if cover == 0 and entry["stock_coverage"] == 0:
                rev.warnings.append(
                    f"{o.name}'s volume covers no walkable area on rung "
                    f"{dest.index}, and covers none where the map itself put it "
                    "either - the objective is as well off here as it shipped, "
                    "and whatever the gamemode says about it, it says stock"
                )
            elif cover == 0:
                rev.warnings.append(
                    f"REFUSED: {o.name}'s volume covers no walkable area on rung "
                    f"{dest.index} - it covers {entry['stock_coverage']} where the "
                    "map put it, so this layout is what lost it, and the server "
                    "would log 'Failed finding CP area'"
                )

            # ...and the other half of the same question, which the volume
            # cannot answer: the gamemode looks the area up from the *marker*,
            # not from the box. ministry_coop's cp3 covers 53 areas and is
            # still refused, because its marker hangs in a stairwell.
            gap = _marker_gap(marker_at, snap)
            stock_gap = _marker_gap(o.marker.origin, snap)
            entry["marker_gap"] = round(gap, 1)
            if gap > MARKER_GAP:
                if not moved:
                    rev.warnings.append(
                        f"{o.name} stays on its own rung and its marker is "
                        f"{gap:.0f} u from the nearest walkable centre there, as it "
                        "is on the stock map - the gamemode logs 'Failed finding CP "
                        "area' for this objective either way, so it is the map's "
                        "own and not this layout's"
                    )
                else:
                    rev.warnings.append(
                        f"{o.name}'s marker is {gap:.0f} u from the nearest walkable "
                        f"centre on rung {dest.index} and there was no floor under "
                        "the objective to stand it on - the gamemode logs 'Failed "
                        "finding CP area' and the objective has no nav area"
                    )

        rev.coverage.append(entry)

    # ── spawns: stage j's defenders at its rung, attackers at the previous ──
    # Stage j's attackers stand on the objective they have just taken, which is
    # stage j-1's rung; stage 1's stand on the rung no stage claims.
    # Gathered first and placed afterwards, because what one stage can have
    # depends on what the others took - a volume borrowed by one stage is not
    # available for another to move, and where one zone lands changes what the
    # next one should avoid.
    clusters: list[dict] = []
    for j in range(1, n + 1):
        name = setup.zones[j - 1]
        for role, team, rung in (("defender", dfn, plan.at(j)),
                                 ("attacker", atk, plan.at(j - 1))):
            authored = [q.origin for q in
                        dest_points(survey, setup, rung, role, owner)]

            # The zone the map itself put on this rung, if it belongs to this
            # team. Borrowing it is a rename and nothing else; where the ladder
            # runs out - rung 0 has no defender zone, rung N no attacker one -
            # `dest_zone` answers with the other team's, which cannot be
            # borrowed, and a volume has to be moved onto the rung instead.
            from_name, from_team, _ = dest_zone(survey, setup, rung, role)
            borrowed = (survey.spawn_zone(from_name, team)
                        if rename and from_team == team else [])
            standing = (survey.points_in_zone(from_name, team) if borrowed else [])

            # **The arrival clash, refused above and re-sited here.** The volume
            # this stage's attackers would borrow stands over the rung they are
            # attacking, so borrowing it in place is the one thing that cannot
            # be done with it. Refusing the borrow is enough: `mode` falls to
            # `move` on the next line and the mover picks a free volume, exactly
            # as it does at the ends of the ladder.
            clash = role == "attacker" and j in resite
            if clash:
                borrowed, standing = [], []

            stage = {"stage": j, "zone": name, "team": team, "role": role,
                     "to_rung": rung, "points": 0}
            mode = ("in place" if from_name == name else "borrow") \
                if borrowed and standing else "move"
            stage["source"] = from_name if mode == "borrow" else mode
            if clash:
                stage["resited_off"] = resite[j][0]
            # Where the ladder runs out `dest_zone` answered with the other
            # team's zone, so `authored` is that team's spread and its centroid
            # is not this rung. The rung's own floor is, and it is the only
            # thing here that knows where this cluster belongs.
            #
            # A re-sited arrival needs it for the same reason and a sharper one:
            # `authored` is the spread of the very points that stand on the
            # objective, so placing against it would put the cluster back where
            # the clash was. The rung's floor is the only honest destination.
            anchor = (rungs[rung].floor if from_team != team or clash else None)
            clusters.append({"j": j, "role": role, "team": team, "rung": rung,
                             "name": name, "authored": authored, "stage": stage,
                             "mode": mode, "from": from_name, "live": mode != "move",
                             "borrowed": borrowed, "standing": standing,
                             "anchor": anchor, "vols": [], "src": [],
                             # The objective this stage is fought at, and the
                             # floor of its rung: the one place its own attackers
                             # must not arrive in.
                             "forbid": (obj_box[j] + [(rungs[resite[j][0]].floor,
                                                       rungs[resite[j][0]].floor)])
                                       if clash else ()})

    # ── counter-attacks: can the next stage's defenders reach the ground? ──
    #
    # Stage j's defender zone is what the counter-attack on stage j-1's
    # objective spawns in (see `COUNTER_REACH`), so the rung it is handed is
    # measured against the rung just taken as well as the one it defends. Where
    # it is out of reach the stage is given authored defender ground that is in
    # reach - nearest its own objective, and not on top of the objective just
    # taken - which on gioconda_eron_kordon is the zone its author put around
    # that objective for exactly this. Where no authored ground qualifies a
    # free volume is moved onto the path forward from the rung just taken.
    rareas = rung_areas(rungs, snap, graph)
    _from: dict[int, np.ndarray] = {}

    # Path length from every area *to* rung r - the way a counter-attack walks.
    # Measured outward instead, every one-way drop on the route read as a wall.
    #
    # Where the mesh has no path at all it is the straight line to the rung's
    # floor, for the reason `mesh_or_ground` gives for advances: the pair is
    # walked in game and the survey draws no link, and the straight line is the
    # least any walk can be. Left `inf`, it was worse than a bad measurement:
    # ps7's honest mesh strands five of its eleven rungs on islands, every
    # counter-attack onto them read as out of reach from everywhere, and the
    # fallback's `allowed` mask - ground inside the budget - left a rung 1-4
    # coordinates to put 32 defenders on. Before that `_reach` interpolated
    # between two `inf`s and got NaN, which passed every check it was put to.
    def _dist_from(r: int) -> np.ndarray | None:
        if rareas[r] < 0:
            return None
        if r not in _from:
            d = graph.distances_to(rareas[r])
            ground = np.linalg.norm(graph.centers - rungs[r].floor, axis=1)
            _from[r] = np.where(np.isfinite(d), d, ground)
        return _from[r]

    defenders = {c["j"]: c for c in clusters if c["role"] == "defender"}
    attackers = {c["j"]: c for c in clusters if c["role"] == "attacker"}
    # The stock walk is left as the map ships it, far counter-attacks and all:
    # it is the control every layout is read against, and the identity it has to
    # be is what checks this whole operation (§3 of docs/permute.md).
    far: list[dict] = []
    counters = resite_counters and plan.order != stock_plan(n).order
    for j in range(2, n + 1) if counters else ():
        c, dist = defenders[j], _dist_from(plan.at(j - 1))
        if dist is None:
            continue
        pts = ([q.origin for q in c["standing"]] if c["mode"] != "move"
               else c["authored"])
        if _reach(pts, dist, snap) > COUNTER_REACH:
            far.append(c)

    claimed = {c["from"] for c in defenders.values()
               if c["mode"] != "move" and not any(c is f for f in far)}
    ground = {zn: (survey.spawn_zone(zn, dfn), survey.points_in_zone(zn, dfn))
              for zn in dict.fromkeys(setup.zones)}
    everyone = [q.origin for _, pts in ground.values() for q in pts]
    for c in far:
        j = c["j"]
        prev, here = plan.at(j - 1), plan.at(j)
        dp, dh = _dist_from(prev), _dist_from(here)
        arrival = attackers[j]
        arrive_boxes = (_boxes(arrival["borrowed"], np.zeros(3))
                        if arrival["mode"] != "move" else [])
        was = _reach([q.origin for q in c["standing"]] if c["mode"] != "move"
                     else c["authored"], dp, snap)
        best = None
        for zn, (vols, pts) in ground.items():
            if zn in claimed or not vols or len(pts) < MIN_POINTS:
                continue
            at = [q.origin for q in pts]
            reach = _reach(at, dp, snap)
            if reach > COUNTER_REACH or _reach(at, dp, snap, 50) < SHORT_ADVANCE:
                continue
            if arrive_boxes and _overlaps(_boxes(vols, np.zeros(3)), arrive_boxes):
                continue
            key = (_reach(at, dh, snap) if dh is not None else 0.0, reach)
            if best is None or key < best[0]:
                best = (key, zn, vols, pts, reach)
        if best is not None:
            _key, zn, vols, pts, reach = best
            mode = "in place" if zn == c["name"] else "borrow"
            c.update(mode=mode, **{"from": zn}, borrowed=vols, standing=pts,
                     authored=[q.origin for q in pts], live=True, anchor=None)
            c["stage"]["source"] = zn if mode == "borrow" else mode
            c["stage"]["counter_from"] = was
            claimed.add(zn)
            rev.warnings.append(
                f"stage {j}'s defenders would counter-attack rung {prev} from "
                f"{was:.0f} u away ({was / RUN_SPEED:.0f} s for the slowest "
                f"tenth); they stand where {zn} stood instead, {reach:.0f} u "
                f"({reach / RUN_SPEED:.0f} s)")
            continue

        at = _along(graph, rareas[prev], rareas[here],
                    min(COUNTER_TARGET, float(D[prev, here]) / 2))
        if at is None:
            rev.warnings.append(
                f"stage {j}'s defenders counter-attack rung {prev} from "
                f"{was:.0f} u away and there is no path to put them nearer")
            continue
        # Somewhere a bot can stand, inside the budget and off the objective
        # just taken - which is where the attackers now respawn.
        allowed = (dp <= COUNTER_REACH) & (dp >= SHORT_ADVANCE)
        near = [p for p in everyone
                if float(np.linalg.norm(p - at)) <= COUNTER_TARGET
                and (a := snap.snap(p)) is not None and allowed[a]]
        c.update(mode="move", borrowed=[], standing=[], live=False, anchor=at,
                 authored=near or [at], allowed=allowed,
                 forbid=list(obj_box[j - 1]) + arrive_boxes)
        c["stage"]["source"] = "move"
        c["stage"]["counter_from"] = was

    def _distinct(pts: list[Entity]) -> list[Entity]:
        """Two spawn points can share an origin, and a rule identifies one by the
        origin it has *before* the edit - so coincident points are one rule and
        one destination whatever is done about them. Collapsed here rather than
        emitting a second rule that can never match; they were coincident on the
        stock map too, so nothing is lost."""
        out, seen = [], set()
        for q in pts:
            key = tuple(np.round(q.origin, 2))
            if key not in seen:
                seen.add(key)
                out.append(q)
        return out

    def _put(c: dict, avoid=(), avoid_boxes=()) -> dict:
        """Slide one cluster's zone, and hand its points the coordinates there.

        The move is measured cluster centre to cluster centre, and it has to be:
        a brush's origin is its own centre, which is not where the points inside
        it average out. Translating the box by a delta taken between brush
        centres and then filling it with coordinates taken between point centres
        lands the box off its own contents, and since binding is containment and
        nothing else, the zone then arrives empty. Measured on ministry_coop
        stage 6: 0 of 16.
        """
        src_at = np.mean([q.origin for q in c["src"]], axis=0)
        delta, cand, n_auth, sep, _leaked = place_zone(
            c["vols"], src_at, c["authored"], len(c["src"]), snap, graph,
            avoid=avoid, avoid_boxes=avoid_boxes, anchor=c.get("anchor"),
            forbid=c.get("forbid", ()), allowed=c.get("allowed"))
        return {"src_at": src_at, "delta": delta, "cand": cand, "n_auth": n_auth,
                "sep": sep, "boxes": _boxes(c["vols"], delta),
                "points": _assign(c["src"], cand, n_auth)}

    # ── borrowers first: they claim the volume they stand in ─────────
    for c in clusters:
        if c["mode"] == "move":
            continue
        c["vols"], c["src"] = c["borrowed"], c["standing"]
        c["stage"]["points"] = len(c["standing"])
        c["stage"]["volumes"] = len(c["borrowed"])
        # Nothing to search for: the volume is where the map left it and the
        # points inside it are the ones the map put there.
        c["put"] = {"src_at": None, "delta": np.zeros(3), "cand": c["standing"],
                    "n_auth": len(c["standing"]), "sep": FILL_SEPARATIONS[0],
                    "boxes": _boxes(c["borrowed"], np.zeros(3)),
                    "points": [q.origin for q in c["standing"]]}

    # ── then the movers, out of what is left ─────────────────────────
    # A mover cannot take a volume a borrower is standing in: two rules on one
    # entity, and the applier makes one edit per entity, so the second would be
    # dropped in silence. Its own zone is the first choice - that is the stock
    # behaviour and needs no rename - and where a borrower has already taken it,
    # the mover takes an unclaimed one instead. The pick is the free cluster
    # most like the one that stood on the destination: nearest in point count,
    # then whichever places with the most precedent under it.
    spoken_for = {(c["team"], c["from"]) for c in clusters if c["mode"] != "move"}
    for c in clusters:
        if c["mode"] != "move":
            continue
        team, want = c["team"], len(c["authored"])
        options = [zn for zn in dict.fromkeys(setup.zones[:n])
                   if (team, zn) not in spoken_for and survey.spawn_zone(zn, team)]
        if c["name"] in options:
            options = [c["name"]]
        best = None
        for zn in options:
            vols = survey.spawn_zone(zn, team)
            src = _distinct([q for q in survey.points_in_zone(zn, team)
                             if zn != c["name"] or owner.get(id(q)) == c["j"]])
            if not vols or not src or not c["authored"]:
                continue
            trial = dict(c, vols=vols, src=src)
            put = _put(trial)
            key = (-abs(len(src) - want), put["n_auth"], len(put["cand"]))
            if best is None or key > best[0]:
                best = (key, zn, vols, src, put)
        if best is None:
            c["stage"]["note"] = ("no free volume, no points in it or no authored "
                                  "destination - left stock")
            continue
        _key, zn, vols, src, put = best
        raw = [q for q in survey.points_in_zone(zn, team)
               if zn != c["name"] or owner.get(id(q)) == c["j"]]
        rev.coincident += len(raw) - len(src)
        c.update(vols=vols, src=src, put=put, live=True, took=zn)
        c["stage"].update(points=len(src), volumes=len(vols),
                          source=f"moved{'' if zn == c['name'] else ' ' + zn}")
        spoken_for.add((team, zn))

    live = [c for c in clusters if c["live"]]
    # ── the leakage pass ─────────────────────────────────────────────
    # A spawn point binds to every zone whose volume contains it, so a zone that
    # lands on another stage's cluster hands that stage's ground to this one.
    # Stock authors its way around that - ministry_coop has 2 such points of 442
    # - but permuting slides big volumes onto other people's ground: its
    # `spawnzone_5` team 3 is a 5074 x 1986 u slab and moves 3,365 u.
    #
    # What makes a shared point wrong is which side of the front it is on. At
    # stage j the attackers hold every rung the plan has already sent them
    # through and are pushing at `plan.at(j)`, so a defender who spawns behind
    # that front appears in ground the round has already cleared, and an
    # attacker who spawns ahead of it appears past the objective. Sharing in the
    # other direction - a defender deeper in defender ground - is what the
    # shipped maps do on purpose, and is left alone.
    def _wrong_side(host: dict, guest: dict) -> bool:
        if host["team"] != guest["team"]:
            return False        # a zone never offers the other team's points
        taken = {plan.at(k) for k in range(host["j"])}
        return (guest["rung"] in taken if host["role"] == "defender"
                else guest["rung"] not in taken)

    def _swallowed(host: dict) -> int:
        """Foreign points this zone would offer on the wrong side of its front."""
        return sum(1 for g in live if g is not host and _wrong_side(host, g)
                   for p in g["put"]["points"] if _inside(p, host["put"]["boxes"]))

    def _leaks(c: dict) -> int:
        """Both directions of it: what this zone swallows, and where its own
        points end up. Either overlap is the same misplaced player, and a
        re-placement that fixes one by causing the other is no fix."""
        return _swallowed(c) + sum(
            1 for g in live if g is not c and _wrong_side(g, c)
            for p in c["put"]["points"] if _inside(p, g["put"]["boxes"]))

    # One sequential pass, each zone re-placed against where everything else
    # currently is and kept only where it measurably improves: the measurement
    # moves as the zones do, so a simultaneous re-place could make it worse.
    #
    # A zone that is not moving is never re-placed. Where a stage keeps its own
    # ground the sharing around it is the shipped map's own, authored on purpose
    # and none of this pass's business - and sliding it off to "fix" that breaks
    # the one self-test the operation has: under `stock_plan` every point must be
    # rewritten to the coordinate it already has. It moved 54 of drycanal_coop's
    # and 45 more of market_coop's before this line existed.
    for c in live:
        if float(np.linalg.norm(c["put"]["delta"])) < 1.0:
            continue
        before = _leaks(c)
        if not before:
            continue
        avoid = [p for g in live if g is not c and _wrong_side(c, g)
                 for p in g["put"]["points"]]
        avoid_boxes = [b for g in live if g is not c and _wrong_side(g, c)
                       for b in g["put"]["boxes"]]
        kept, c["put"] = c["put"], _put(c, avoid, avoid_boxes)
        # Coordinates first: a zone short of places to stand is a worse failure
        # than a shared one, and it is the failure that refuses the layout.
        if _leaks(c) >= before or len(c["put"]["cand"]) < len(kept["cand"]):
            c["put"] = kept

    # ── did the re-sited arrivals actually get off the objective? ────
    #
    # The refusal the detection pass used to make, asked of the placement
    # instead of the ladder. Forcing `move` is a licence to try, not a promise:
    # the mover can find no free volume and leave the zone stock, or find one
    # and have nowhere to put it but back over the objective. Either way the
    # round still starts with the attackers inside the thing they are attacking,
    # and that is refused exactly as it was before.
    #
    # Where it worked it is still said, because a stage whose attackers stand
    # somewhere the map never authored is a thing a playtest should be told
    # about before it is blamed on the layout.
    for c in clusters:
        if c["role"] != "attacker" or c["j"] not in resite:
            continue
        j = c["j"]
        here, from_rung, zname = resite[j]
        on_objective = rungs[here].floor
        if not c["live"]:
            rev.warnings.append(
                f"REFUSED: stage {j} is fought on rung {here}, and its attackers "
                f"arrive in {zname} - which stands on that same rung, not on "
                f"rung {from_rung}. No free volume could be moved onto rung "
                f"{from_rung} instead, so the stage would start with the "
                f"attackers inside the objective and its defenders"
            )
        elif _overlaps(c["put"]["boxes"], c["forbid"]):
            rev.warnings.append(
                f"REFUSED: stage {j} is fought on rung {here}, and its attackers "
                f"arrive in {zname} - which stands on that same rung, not on "
                f"rung {from_rung}. Re-siting the zone onto rung {from_rung} "
                f"left it over the objective anyway, so the stage would still "
                f"start with the attackers inside it and its defenders"
            )
        else:
            took = c.get("took", c["name"])
            away = float(np.linalg.norm(
                np.mean(c["put"]["points"], axis=0) - on_objective)) \
                if c["put"]["points"] else 0.0
            c["stage"]["resited"] = True
            rev.warnings.append(
                f"stage {j}'s attackers would have arrived in {zname}, which "
                f"stands on rung {here} where the stage is fought, so the zone "
                f"was not borrowed: {took} is moved onto rung {from_rung} "
                f"instead and the cluster lands {away:.0f} u off the objective"
            )

    # ── how far each counter-attack walks, as placed ─────────────────
    for j in range(2, n + 1):
        c, dist = defenders[j], _dist_from(plan.at(j - 1))
        if dist is None or not c["live"] or not c["put"]["points"]:
            continue
        reach = _reach(c["put"]["points"], dist, snap)
        c["stage"]["counter_reach"] = reach
        if reach > COUNTER_REACH:
            rev.warnings.append(
                f"stage {j}'s defenders counter-attack rung {plan.at(j - 1)} "
                f"from {reach:.0f} u away - {reach / RUN_SPEED:.0f} s for the "
                f"slowest tenth, over the {COUNTER_SECONDS:.0f} s budget")

    # ── and what all that comes to, one cluster at a time ────────────
    for c in clusters:
        j, role, team, rung = c["j"], c["role"], c["team"], c["rung"]
        name, src_pts, stage = c["name"], c["src"], c["stage"]
        if not c["live"]:
            rev.stages.append(stage)
            continue

        put = c["put"]
        delta, cand, n_auth, boxes = put["delta"], put["cand"], put["n_auth"], put["boxes"]
        stage["authored"] = n_auth
        stage["candidates"] = len(cand)
        if put["sep"] < MIN_SEPARATION:
            stage["fill_separation"] = put["sep"]

        if c["mode"] != "move":
            # The zone the map put on this rung, handed to the stage that is
            # fought there. One rule, no geometry: the cpsetup binds a stage to
            # a targetname, and the lump is rewritten before the entities exist,
            # so the volume arrives already belonging to this stage. A rename
            # cycle applies in one pass because the walk visits each entry once
            # and stops at its first matching rule - an entity renamed A -> B is
            # never re-read by the B -> C rule.
            if role == "defender":
                own = [q.origin for q in survey.points_in_zone(name, team)]
                def_delta[j] = np.zeros(3)
                def_at[j] = (np.mean(own, axis=0) if own else rungs[rung].floor)
            if c["mode"] == "borrow":
                moves, pads = _zone_moves(
                    survey, c["from"], team, rename=name,
                    note=f"stage {j} {role}s stand where {c['from']} stood, on rung {rung}",
                )
                rev.moves.extend(moves)
                rev.pads += len(pads)
            pts = put["points"]
            stage["bound"] = len(pts)
            stage["distinct"] = len({tuple(np.round(q, 2)) for q in pts})
            stage["leaks"] = _swallowed(c)
            stage["median_to_rung"] = float(np.median(
                [float(np.linalg.norm(q - rungs[rung].floor)) for q in pts])) if pts else 0.0
            # Not refused for being few: these are the map's own points, in the
            # map's own volume, and nothing here moved them. Whatever number is
            # in there is the number the map spawns that team on at that rung
            # every round it plays, so the count measures the map and not the
            # layout - and MIN_POINTS is calibrated on the shipped maps, which
            # carry 16-17. The server list has maps that carry 2:
            # tell_night_coop ships sz_f with 2, sz_h with 3, sz_i with 2 and
            # sz_e and sz_g with none at all, where tell_coop - the same
            # geometry, daylight - carries a tidy 8 in each. That refusal alone
            # was taking 27 of its 29 layouts, 21 of 21 from drycanal_coop_old
            # and 11 of 15 from sinjar_night_coop. It is still worth saying,
            # because a thin zone is a thin fight; it is not grounds to refuse.
            if len(pts) < MIN_POINTS:
                rev.warnings.append(
                    f"stage {j} {role}s borrow {c['from']} on rung {rung} and it "
                    f"holds {len(pts)} spawn points, under the {MIN_POINTS} a "
                    f"stage wants - which is what the map spawns there itself"
                )
            rev.stages.append(stage)
            continue

        if len(cand) < min(MIN_POINTS, len(src_pts)):
            rev.warnings.append(
                f"REFUSED: stage {j} {role}s have {len(cand)} usable coordinates "
                f"on rung {rung} for {len(src_pts)} points"
            )
            rev.stages.append(stage)
            continue

        # One rule moves every volume sharing the name, which is what the
        # engine needs: ministry has four spawnzone_2 volumes for team 2 and
        # they are one region. Where the volume borrowed for the move is not
        # this stage's own, the same rule renames it, so the cpsetup still finds
        # a zone under the name it lists.
        took = c.get("took", name)
        moves, pads = _zone_moves(
            survey, took, team, offset=delta, rename="" if took == name else name,
            note=f"stage {j} {role}s -> rung {rung}"
                 + ("" if took == name else f", in {took}'s volume"))
        rev.moves.extend(moves)
        rev.pads += len(pads)
        if role == "defender":
            # Its own ground, and what its own volume did - a stage that borrowed
            # somebody else's left its own volume where it was.
            def_delta[j] = delta if took == name else np.zeros(3)
            def_at[j] = put["src_at"]

        placed = put["points"]
        bound, dists = 0, []
        for src_pt, dst in zip(src_pts, placed, strict=True):
            rev.moves.append(Move(
                cls="ins_spawnpoint", team=team,
                within=(src_pt.origin - WITHIN_SLOP, src_pt.origin + WITHIN_SLOP),
                origin=dst,
                note=f"stage {j} {role} -> rung {rung}",
            ))
            bound += _inside(dst, boxes)
            dists.append(float(np.linalg.norm(dst - rungs[rung].floor)))
        stage["bound"] = bound
        stage["distinct"] = len({tuple(np.round(d, 2)) for d in placed})
        stage["leaks"] = _swallowed(c)
        # Distance to the rung, not to the objective under attack: a stage's
        # attackers stand on the objective they just took, which is the
        # shipped relation and the one worth reading against ministry's
        # 1900 u approach.
        stage["median_to_rung"] = float(np.median(dists)) if dists else 0.0

        # The binding is the whole mechanism. A point outside every moved
        # volume belongs to no stage and the engine will never pick it.
        if bound < min(MIN_POINTS, len(src_pts)):
            rev.warnings.append(
                f"REFUSED: stage {j} {role}s - only {bound} of {len(src_pts)} "
                f"moved points fall inside the moved zone volume"
            )
        elif bound < len(src_pts):
            rev.warnings.append(
                f"stage {j} {role}s: {len(src_pts) - bound} of {len(src_pts)} "
                f"moved points fall outside the moved volume and are orphaned"
            )
        rev.stages.append(stage)

    # A stock volume whose name the cpsetup still lists, and which no stage is
    # now standing in, would be enabled alongside the one that replaced it -
    # binding is containment, so its points would serve a stage fought two rungs
    # away. Renaming it out of the cpsetup's vocabulary makes it inert. It
    # happens whenever the rung a zone sits on is the one no stage claims.
    for team, role in ((dfn, "defender"), (atk, "attacker")):
        held = {(c["from"] if c["mode"] != "move" else c.get("took", c["name"]))
                for c in clusters if c["role"] == role and c["live"]}
        for zn in dict.fromkeys(setup.zones[:n]):
            if zn in held or not survey.spawn_zone(zn, team):
                continue
            # A pad holds no points, so there is nothing in it to serve the
            # wrong stage and nothing to make inert - and renaming it out of
            # the cpsetup's vocabulary is what takes a resupply area off the
            # map. Where every volume under the name is one, no rule is written
            # at all. See `_zone_split`.
            standing, _ = _zone_split(survey, zn, team)
            if not standing:
                rev.pads += len(survey.spawn_zone(zn, team))
                continue
            moves, pads = _zone_moves(
                survey, zn, team, rename=f"{zn}_unused",
                note=f"no stage stands in {zn} for team {team} under this layout")
            rev.moves.extend(moves)
            rev.pads += len(pads)

    if rev.pads:
        rev.warnings.append(
            f"{rev.pads} zone volume(s) hold no spawn points and are left where the "
            f"map put them - on this corpus that is where a map keeps resupply, and "
            f"a rule matched on the name alone would take it with the zone"
        )

    rev.leaks = sum(st.get("leaks", 0) for st in rev.stages)
    if rev.leaks > rev.contested:
        rev.warnings.append(
            f"{rev.leaks} spawn points sit inside another stage's moved zone on the "
            f"wrong side of its front, and the engine binds a point to every zone "
            f"whose volume holds it - so those stages can put a player in ground the "
            f"round has already passed. The shipped map shares {rev.contested}."
        )

    # ── restricted areas ─────────────────────────────────────────────
    bz = survey.by_class("ins_blockzone")
    if bz:
        if blockzones == "follow" and def_delta:
            for b in bz:
                # Nothing in the survey binds a block zone to its spawn zone -
                # the `blockzone` key is on the zone and is not exported - so it
                # is paired with the defender cluster it physically sits on, and
                # inherits the move that cluster actually made.
                #
                # Usually that move is now none: a borrowed zone stays where the
                # map put it, and so does the restricted area guarding it - which
                # is the right answer, because the stage fought on that ground is
                # the one standing in that volume. Only a zone that really slid
                # takes its block zone along.
                stage = min(def_at, key=lambda k: float(np.linalg.norm(b.origin - def_at[k])))
                away = float(np.linalg.norm(b.origin - def_at[stage]))
                if float(np.linalg.norm(def_delta[stage])) < 1.0:
                    continue
                rev.moves.append(Move(
                    cls="ins_blockzone", target=b.target,
                    within=(b.origin - WITHIN_SLOP, b.origin + WITHIN_SLOP),
                    origin=b.origin + def_delta[stage],
                    note=f"follows stage {stage}'s defenders ({away:.0f} u away)",
                ))
        rev.warnings.append(
            f"{len(bz)} restricted-area volumes: a block zone holds the attackers "
            f"behind the objective, and permuting the ground under the chain moves "
            f"which side that is. Disable them (tools/blockzones.py --disable) or "
            f"run a server that does not use them."
        )

    return rev


def _marker_gap(point: np.ndarray, snap: Snapper) -> float:
    """How far a control point marker sits from the nearest walkable centre.

    The gamemode's own lookup is in the binary and this is not it; it is the
    one measurement that ranks ministry_coop's six objectives the way the
    server log does. See `MARKER_GAP`.
    """
    return float(np.linalg.norm(snap.center - point, axis=1).min())


def reverse(
    survey: Survey,
    setup: CpSetup,
    *,
    graph: NavGraph | None = None,
    blockzones: str = "follow",
    max_advance: float | None = None,
    min_advance: float | None = None,
    min_separation: float | None = None,
    max_detour: float | None = None,
    rename: bool = True,
) -> Layout:
    """Play the map backwards. The layout this module was written for."""
    return layout(survey, setup, None, graph=graph, blockzones=blockzones,
                  max_advance=max_advance, min_advance=min_advance,
                  min_separation=min_separation,
                  max_detour=max_detour, rename=rename)


# ── emitting ─────────────────────────────────────────────────────────

def _v(a: np.ndarray) -> str:
    return " ".join(f"{x:.2f}" for x in a)


def _wrap(text: str, indent: str, width: int = 74) -> list[str]:
    """A comment paragraph, so a plan's own words can go in the preset."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(f"{indent}// {cur}")
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(f"{indent}// {cur}")
    return lines


def _preset_block(rev: Layout, name: str | None = None) -> list[str]:
    """One `preset` block. The applier reads whichever of these it picks."""
    name = name or rev.name
    out = ['\t"preset"', "\t{", f'\t\t"name"  "{name}"', ""]
    out += _wrap(
        "The chain still reads 1..N; the ground under it is permuted. "
        + ((rev.plan.note[0].upper() + rev.plan.note[1:] + ". ")
           if rev.plan and rev.plan.note else "")
        + "Every coordinate below is one the stock map used.", "\t\t")
    if rev.plan:
        out.append(f'\t\t// rungs, entry first: {" ".join(map(str, rev.plan.order))}')
    out += ['\t\t"metrics"', "\t\t{",
            f'\t\t\t"objectives"    "{len(rev.coverage)}"',
            f'\t\t\t"points_moved"  "{rev.points_moved}"',
            f'\t\t\t"min_coverage"  "{min((c["coverage"] for c in rev.coverage), default=0)}"']
    if np.isfinite(rev.worst_advance):
        out.append(f'\t\t\t"worst_advance" "{rev.worst_advance:.0f}"')
    if np.isfinite(rev.stock_max):
        out.append(f'\t\t\t"stock_advance" "{rev.stock_max:.0f}"')
    if np.isfinite(rev.min_advance):
        out.append(f'\t\t\t"min_advance"   "{rev.min_advance:.0f}"')
    if np.isfinite(rev.min_separation):
        out.append(f'\t\t\t"min_separate"  "{rev.min_separation:.0f}"')
    if np.isfinite(rev.worst_detour):
        out.append(f'\t\t\t"worst_detour"  "{rev.worst_detour:.2f}"')
        stock_detour = rev.stock_metrics.get("worst_detour")
        if stock_detour is not None and np.isfinite(stock_detour):
            out.append(f'\t\t\t"stock_detour"  "{stock_detour:.2f}"')
    counter = [st["counter_reach"] for st in rev.stages
               if np.isfinite(st.get("counter_reach", float("nan")))]
    if counter:
        out.append(f'\t\t\t"worst_counter" "{max(counter):.0f}"')
    out += ["\t\t}", ""]

    out += ['\t\t"edit"', "\t\t{"]
    for i, m in enumerate(rev.moves, start=1):
        out.append(f'\t\t\t"{i}"')
        out.append("\t\t\t{")
        out.append(f'\t\t\t\t"class"   "{m.cls}"')
        if m.target:
            out.append(f'\t\t\t\t"target"  "{m.target}"')
        if m.team is not None:
            out.append(f'\t\t\t\t"team"    "{m.team}"')
        if m.within is not None:
            lo, hi = m.within
            out.append(f'\t\t\t\t"within"  "{_v(lo)}  {_v(hi)}"')
        if m.origin is not None:
            out.append(f'\t\t\t\t"origin"  "{_v(m.origin)}"')
        if m.offset is not None:
            out.append(f'\t\t\t\t"offset"  "{_v(m.offset)}"')
        if m.rename:
            out.append(f'\t\t\t\t"rename"  "{m.rename}"')
        if m.note:
            out.append(f"\t\t\t\t// {m.note}")
        out.append("\t\t\t}")
    out.append("\t\t}")

    if rev.objectives:
        out += ["", "\t\t// Caches come from maps/<name>.txt, not the lump, so they are",
                "\t\t// teleported after they spawn.", '\t\t"objective"', "\t\t{"]
        for i, o in enumerate(rev.objectives, start=1):
            out += [f'\t\t\t"{i}"', "\t\t\t{",
                    f'\t\t\t\t"name"       "{o.name}"',
                    f'\t\t\t\t"cp"         "{o.cp}"',
                    f'\t\t\t\t"origin"     "{_v(o.origin)}"',
                    f'\t\t\t\t"cp_offset"  "{o.cp_offset:.2f}"']
            # Where the two entities stand before the move. The applier matches
            # on the name first and on these second, so a map with two entities
            # under one objective name moves the one this layout measured.
            if o.frm is not None:
                out.append(f'\t\t\t\t"from"       "{_v(o.frm)}"')
            if o.cp_from is not None:
                out.append(f'\t\t\t\t"cp_from"    "{_v(o.cp_from)}"')
            if o.note:
                out.append(f"\t\t\t\t// {o.note}")
            out.append("\t\t\t}")
        out.append("\t\t}")

    out.append("\t}")
    return out


def _stock_block() -> list[str]:
    """The control, written first and **not rotated to**.

    The applier skips a preset called `stock` when it picks one, because a
    rotation slot is an expensive way to reach the unmodified map and there are
    three cheaper ones: the boot load of every session is stock, `sm_layout
    off` turns the applier off, and `sm_layout stock` pins it for one map. It
    stays in the file because it carries the stock round's own metrics - which
    is what every gate here measures a layout against - and because a file that
    holds nothing else is a map with no layout, where the applier falls back to
    it.
    """
    return ["\t// The control. Not rotated to - the applier skips it, because the",
            "\t// boot load, `sm_layout off` and `sm_layout stock` all reach the",
            "\t// map as it shipped without spending a load on it. Kept for its",
            "\t// metrics below, which are what a layout is measured against.",
            '\t"preset"', "\t{",
            '\t\t"name"  "stock"', "\t}"]


def to_cfg(rev: Layout, name: str | None = None,
           survey_hash: str = "0000000000000000",
           include_stock: bool = True) -> str:
    """The preset file `mapmaker_layout.sp` reads, holding one layout.

    KeyValues rather than the rich JSON because SourceMod parses KeyValues
    natively, and hand-rolling a JSON reader in SourcePawn to save one export
    step would be a bad trade.
    """
    return to_cfg_all([rev], names=[name] if name else None,
                      survey_hash=survey_hash, include_stock=include_stock)


def to_cfg_all(layouts: list[Layout], names: list[str] | None = None,
               survey_hash: str = "0000000000000000",
               include_stock: bool = True) -> str:
    """One preset file holding several layouts, for the applier to rotate over.

    `mapmaker_layout.sp` picks round-robin per map and persists the index, so a
    file of N layouts is N rounds that do not repeat - which is the whole reason
    to emit a family rather than one at a time. It reads only the preset it
    picks, so the file being large costs nothing at load.
    """
    if not layouts:
        raise ValueError("no layouts to write")
    out = ['"layout"', "{", f'\t"map"          "{layouts[0].map}"',
           f'\t"survey_hash"  "{survey_hash}"', ""]
    if include_stock:
        out += _stock_block() + [""]
    for i, lay in enumerate(layouts):
        nm = names[i] if names and i < len(names) else None
        if (nm or lay.name) == "stock" and include_stock:
            continue
        out += _preset_block(lay, nm) + [""]
    out += ["}", ""]
    return "\n".join(out)


def to_dict(rev: Layout) -> dict:
    """The rich half: why each move was made, so a bad round is traceable."""
    return {
        "map": rev.map,
        "chain": rev.chain,
        "zones": rev.zones,
        "attacking_team": rev.attacking_team,
        "ok": rev.ok,
        "plan": None if rev.plan is None else {
            "name": rev.plan.name,
            "entry": rev.plan.entry,
            "rungs": rev.plan.rungs[1:],
            "order": rev.plan.order,
            "note": rev.plan.note,
        },
        "advances": rev.advances,
        "stock_advances": rev.stock_advances,
        "worst_advance": None if not np.isfinite(rev.worst_advance) else rev.worst_advance,
        "stock_worst_advance": None if not np.isfinite(rev.stock_max) else rev.stock_max,
        "shape": rev.metrics,
        "stock_shape": rev.stock_metrics,
        "rungs": [
            {"index": r.index,
             "objective": r.objective.name if r.objective else None,
             "floor": [round(float(x), 1) for x in r.floor],
             "defender_zone": r.def_zone or None,
             "attacker_zone": r.atk_zone or None}
            for r in rev.rungs
        ],
        "objectives": rev.coverage,
        "stages": rev.stages,
        "warnings": rev.warnings,
        "edits": len(rev.moves),
        "points_moved": rev.points_moved,
        "contested_points": rev.contested,
        "unclaimed_points": rev.unclaimed,
        "coincident_points": rev.coincident,
        "leaked_points": rev.leaks,
        "pads_left": rev.pads,
    }


def report(rev: Layout) -> str:
    lines = [
        f"map              {rev.map}",
        f"chain            {len(rev.chain)} objectives, attacking team {rev.attacking_team}",
        f"                 {' -> '.join(rev.chain)}",
    ]
    if rev.plan is not None:
        lines += [
            "",
            f"layout           {rev.plan.name}",
            f"                 {rev.plan.note}",
            f"                 attackers enter at rung {rev.plan.entry}; "
            f"stage -> rung  " + "  ".join(
                f"{j}->{rev.plan.rungs[j]}" for j in range(1, len(rev.plan) + 1)),
        ]
    lines += [
        "",
        "the ladder       stock rung -> what stands there",
    ]
    for r in rev.rungs:
        what = r.objective.name if r.objective else "attacker entry"
        lines.append(
            f"   rung {r.index:<2}       {what:<16} "
            f"def {r.def_zone or '-':<14} atk {r.atk_zone or '-':<14} "
            f"floor {r.floor[0]:.0f} {r.floor[1]:.0f} {r.floor[2]:.0f}"
        )

    if rev.coverage:
        lines += ["", f"objectives       slot -> rung   {'kind':<9}"
                      f"{'areas covered':>14}{'marker gap':>12}"]
        for c in rev.coverage:
            gap = c.get("marker_gap")
            lines.append(
                f"   {c['name']:<16} {c['order']} -> {c['to_rung']:<4} "
                f"{c['kind']:<9}{c['coverage']:>14}"
                + (f"{gap:>12.0f}" if gap is not None else f"{'-':>12}")
                + ("   EMPTY" if c["coverage"] == 0 else
                   "   NO AREA" if gap is not None and gap > MARKER_GAP else "")
            )

    if rev.stages:
        head = (f"spawns           {'stage':>5} {'zone':<14}{'role':<9}{'->rung':>7}"
                f"  {'stands in':<16}{'points':>7}{'authored':>9}{'filled':>7}"
                f"{'distinct':>9}{'bound':>7}{'to rung':>8}{'leaks':>7}")
        lines += ["", head]
        for st in rev.stages:
            lines.append(
                f"                 {st['stage']:>5} {st['zone']:<14}{st.get('role', ''):<9}"
                f"{st['to_rung']:>7}  {st.get('source', ''):<16}"
                f"{st.get('points', 0):>7}{st.get('authored', 0):>9}"
                f"{max(0, st.get('candidates', 0) - st.get('authored', 0)):>7}"
                f"{st.get('distinct', 0):>9}{st.get('bound', 0):>7}"
                f"{st.get('median_to_rung', 0.0):>8.0f}{st.get('leaks', 0):>7}"
            )

    if rev.advances:
        lines += ["", f"advance          {'stage':>5} {'rung':>10}  {'this layout':>11}"
                      f"{'stock':>9}"]
        for a, st in zip(rev.advances, rev.stock_advances, strict=False):
            mine = "unreachable" if not np.isfinite(a["advance"]) else f"{a['advance']:.0f}"
            theirs = "unreachable" if not np.isfinite(st) else f"{st:.0f}"
            flag = ""
            if np.isfinite(a["advance"]) and np.isfinite(rev.stock_max) and rev.stock_max > 0:
                if a["advance"] > rev.stock_max:
                    flag = f"   <-- {a['advance'] / rev.stock_max:.2f}x the stock worst"
            lines.append(
                f"                 {a['stage']:>5} {a['from_rung']:>4} ->{a['to_rung']:>3}"
                f"  {mine:>11}{theirs:>9}{flag}"
            )

    if rev.metrics:
        st = rev.stock_metrics or {}
        def _n(v: float | None, fmt: str) -> str:
            if v is None or np.isnan(v):
                return "-"
            # An infinite detour is a measurement, not a missing one: the round
            # comes back to ground it has already taken. See `_detour`.
            return "returns" if not np.isfinite(v) else format(v, fmt)
        lines += ["", f"shape            {'':>16}{'this layout':>12}{'stock':>9}",
                  f"                 {'shortest advance':<16}"
                  f"{_n(rev.min_advance, '11.0f'):>12}{_n(st.get('min_advance'), '8.0f'):>9}",
                  f"                 {'worst detour':<16}"
                  f"{_n(rev.worst_detour, '11.2f'):>12}{_n(st.get('worst_detour'), '8.2f'):>9}",
                  f"                 {'stages doubling back':<16}"
                  f"{rev.metrics.get('backtracks', 0):>12}{st.get('backtracks', 0):>9}"]

    tail = (f"edits            {len(rev.moves)} lump rewrites "
            f"({rev.points_moved} spawn points), "
            f"{len(rev.objectives)} objectives teleported")
    lines += ["", tail]
    if rev.contested or rev.unclaimed or rev.coincident:
        lines.append(
            f"                 {rev.contested} points sat in two stages' zones and "
            f"went to the deeper one, {rev.coincident} shared an origin with "
            f"another and share its destination, {rev.unclaimed} sat in no stage "
            f"zone and were left stock"
        )
    if rev.warnings:
        lines.append("")
        for w in rev.warnings:
            lines.append(f"! {w}")
    lines.append("")
    lines.append("verdict          " + ("emit" if rev.ok else "REFUSED"))
    return "\n".join(lines)
