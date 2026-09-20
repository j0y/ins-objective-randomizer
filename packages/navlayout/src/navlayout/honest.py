"""Drop the nav connections the map's own geometry denies.

**A `.nav` outlives the geometry it was built for.** A mapper who adds a wall to
a finished map and does not rebuild the mesh leaves the connection behind, and
the engine keeps it: `CNavArea` stores its neighbours as indices, not as a
question asked of the world. Nothing in the survey can see this, because the
survey is the engine's own graph and the engine believes it too.

Everything this package measures is then wrong in one direction. A connection
through a wall is a shortcut no player can take, so the mesh reads as more
connected than the map is: more loops, fewer hinges, a lower `severance` - and
`places` calls the map **open** and licenses the whole family of layouts, on a
map that plays as a corridor. The walk costs are wrong the same way, because a
route that crosses the wall is shorter than the one the player has to walk, so
`--max-detour` and `--min-advance` are measuring a map nobody is on.

Reported off the server 2026-09-20, `market_coop_old_fixbysakey` `enter0_fwd`:
"a bunch of doors are locked, so player has to run the long way round". The map
is a workshop fix of `market_coop` that added walls and shipped the old mesh.
It reads open on a hinge fraction of 0.045 with 14 loops; 3.5% of its
connections cross solid brushwork, and with those dropped it is **segmented**,
hinge fraction 0.208, 6 loops - which is a corridor, and licenses the reversal
and nothing more.

**The test is a segment trace, centre to centre, at knee height.** `MOVE_MASK`
is what a player collides with, entity brushes included, which is the point: an
added wall is usually a `func_brush` or a brush entity rather than world
geometry. Knee height rather than the floor because a kerb, a step or a ramp lip
sits between two area centres and is walked over, not into.

**It is a screening test and it is calibrated as one.** Centre to centre is
cruder than the ground truth - a pillar standing between two area centres reads
blocked although a player walks round it inside the same pair of areas - so the
question is never "is this connection blocked" on its own but "does this map
carry more of them than a map nobody complains about". Measured 2026-09-20 over
the surveyed corpus:

    ministry_coop               0.3%
    contact_coop                0.3%
    district_coop               0.7%
    buhriz_coop                 1.0%
    market_coop                 1.7%     the original of the geometry below
    market_coop_old_fixbysakey  3.5%     the fix that added the walls

The shipped maps sit under 2% and that is the false-positive floor this test
has; a map that doubles it is carrying walls its mesh does not know about. The
filter runs on every map because a false positive is cheap - one connection of
several thousand, on a mesh whose remaining routes still reach the same ground -
and a false negative is the bug this module exists for.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .survey import Survey

# Knee height. A player steps over what is below this, so a step between two
# area centres is not a wall; a wall is.
KNEE = 24.0

# **Some meshes do not stand on the geometry they are shipped with, and on those
# this test measures nothing.** The probe is the cheapest question that finds
# them: trace straight up from a little above each area centre, and count the
# areas with no standing room. A mesh that sits on its map answers almost never;
# a mesh that does not answers most of the time, because the centre is flush
# against a surface and *every* ray from it starts in contact.
#
# Measured 2026-09-20 over all 125 surveyed maps with a `.bsp` here: median
# 0.6%, p90 4.4%, and the bar sits in the gap the corpus leaves rather than at
# a round number that happens to work.
#
#   ... karkand_redux_p2 5.6%, karkand_coop_p1_redux_v1_7 6.6%,
#   desert_hell_coop_remix 7.1%, karkand_redux_p1_v1_2 7.6%   - 118 maps
#   ---- 10% ----
#   hard_rain 11.8%, dog_red 15.4%, ins_saint_lo_v1 18.8%,
#   cs_downtown_coop 38.6%, fairgrounds 37.5%, dead_air 40.6%,
#   cs_officeb3_coop_v1_5 59.1%                              - 7 maps
#
# On those seven the connection test reports 12-53% of the mesh blocked, which
# is not a fact about those maps, and taking it at face value moved dead_air
# from linear to small and fairgrounds from segmented to small. Above this bar
# the filter declines to run and the mesh is used as surveyed: a map whose
# geometry cannot be asked is one this test has no opinion about, and saying so
# is better than a confident wrong answer.
TRUST_PROBE = 0.10

# How far above the centre the probe starts and ends. Above a step, below the
# height of anything a player walks under.
PROBE_FROM, PROBE_TO = 8.0, 40.0


def _crossing(a, b, direction: int) -> np.ndarray | None:
    """The middle of the boundary two areas share, or None if they share none.

    A player crossing between two areas goes over the edge between them, not
    over the line joining their centres, and on an L-shaped pair or either side
    of a pillar those are different places. Tracing centre to centre asks
    whether the *centres* see each other, which is a harder question than the
    one that matters and fails on geometry nobody is blocked by: before this,
    cs_officeb3_coop_v1_5 read 52.6% blocked and dead_air 40.5%, against 0.3%
    for ministry_coop, which is not a fact about those maps.

    `CNavArea` keeps its neighbours per side, north being -y and east +x, and
    the survey carries the two corners, so the shared span is an interval
    intersection on the other axis.
    """
    if a.nw is None or a.se is None or b.nw is None or b.se is None:
        return None
    if direction in (0, 2):                      # north / south: a y edge
        y = a.nw[1] if direction == 0 else a.se[1]
        lo = max(min(a.nw[0], a.se[0]), min(b.nw[0], b.se[0]))
        hi = min(max(a.nw[0], a.se[0]), max(b.nw[0], b.se[0]))
        if hi < lo:
            return None
        return np.array([(lo + hi) / 2.0, y, (a.center[2] + b.center[2]) / 2.0])
    x = a.se[0] if direction == 1 else a.nw[0]   # east / west: an x edge
    lo = max(min(a.nw[1], a.se[1]), min(b.nw[1], b.se[1]))
    hi = min(max(a.nw[1], a.se[1]), max(b.nw[1], b.se[1]))
    if hi < lo:
        return None
    return np.array([x, (lo + hi) / 2.0, (a.center[2] + b.center[2]) / 2.0])


def blocked_edges(survey: Survey, bsp: str | Path,
                  knee: float = KNEE) -> np.ndarray:
    """Per directed connection, does solid geometry stand in the way?

    Two traces, centre to the shared boundary and boundary to centre, so the
    question asked is the one a player asks. Flat, in the order `survey.areas`
    yields them, so the caller can map an entry back to the `(area, slot)` it
    came from. All false when the `.bsp` is not there to read - the mesh is then
    the only account of the map there is.
    """
    from bspscout.bsp import Bsp
    from bspscout.trace import MOVE_MASK, Tracer

    order = [(k, n, c[0], c[1]) for k, a in enumerate(survey.areas)
             for n, c in enumerate(a.conn)]
    if not order:
        return np.zeros(0, dtype=bool)

    path = Path(bsp)
    if not path.exists():
        return np.zeros(len(order), dtype=bool)

    n_areas = len(survey.areas)
    centers = survey.centers()
    lift = np.array([0.0, 0.0, float(knee)])

    a_pt = np.empty((len(order), 3))
    mid_pt = np.empty((len(order), 3))
    b_pt = np.empty((len(order), 3))
    real = np.zeros(len(order), dtype=bool)
    for row, (k, _slot, direction, target) in enumerate(order):
        if not (0 <= target < n_areas) or target == k:
            continue
        cross = _crossing(survey.areas[k], survey.areas[target], int(direction))
        if cross is None:
            continue
        real[row] = True
        a_pt[row] = centers[k] + lift
        mid_pt[row] = cross + lift
        b_pt[row] = centers[target] + lift

    out = np.zeros(len(order), dtype=bool)
    if not real.any():
        return out
    try:
        tracer = Tracer(Bsp(str(path)), mask=MOVE_MASK, include_entities=True)
        first = tracer.occluded(a_pt[real], mid_pt[real])
        second = tracer.occluded(mid_pt[real], b_pt[real])
    except (OSError, ValueError, NotImplementedError):
        return np.zeros(len(order), dtype=bool)
    out[real] = first | second
    return out


def trustworthy(survey: Survey, bsp: str | Path) -> tuple[bool, float]:
    """Does this mesh stand on this geometry? See `TRUST_PROBE`."""
    from bspscout.bsp import Bsp
    from bspscout.trace import MOVE_MASK, Tracer

    path = Path(bsp)
    if not path.exists() or not survey.areas:
        return False, float("nan")
    centers = survey.centers()
    try:
        tracer = Tracer(Bsp(str(path)), mask=MOVE_MASK, include_entities=True)
        rate = float(tracer.occluded(centers + [0, 0, PROBE_FROM],
                                     centers + [0, 0, PROBE_TO]).mean())
    except (OSError, ValueError, NotImplementedError):
        return False, float("nan")
    return rate <= TRUST_PROBE, rate


def honest(survey: Survey, bsp: str | Path,
           knee: float = KNEE) -> tuple[Survey, int, int]:
    """The survey with the denied connections removed, and the count either way.

    Mutates and returns the survey it was given: it is loaded per command and
    every caller wants the same answer, so there is nothing to be gained by
    carrying two meshes around and something to lose if they diverge. Drops
    nothing, and reports a total of 0, when the mesh and the geometry do not
    belong to each other - `trustworthy` is that question.
    """
    ok, _rate = trustworthy(survey, bsp)
    if not ok:
        return survey, 0, 0

    mask = blocked_edges(survey, bsp, knee)
    total = int(mask.size)
    if not mask.any():
        return survey, 0, total

    row = 0
    dropped = 0
    for area in survey.areas:
        keep = []
        for conn in area.conn:
            if mask[row]:
                dropped += 1
            else:
                keep.append(conn)
            row += 1
        area.conn = keep
    return survey, dropped, total
