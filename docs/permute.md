# Permuting the ground under a chain

`docs/reverse.md` plays a linear map backwards. This is the operation it turned
out to be a case of: the chain still reads 1..N, and **which rung each slot is
fought at is a free choice**. A reversal is one of those choices. Randomising
the starting objective — what `places.py` prescribes for a map that reads
`small` — is a family of them.

    navlayout permute ministry_coop --start 4          one layout
    navlayout permute ministry_coop --all --cfg <path> the family, rotatable
    navlayout permute ministry_coop --order 5,4,3,2,1,0

Status: **2026-09-17, cs_officeb3_coop_v1_5: a player found two objectives
155 u apart, and the cost function was the reason.** §8 is that fault, the term
that fixes it, and the twelve maps it exposed as never having had the measured
walk at all. The map now emits 4 layouts where it emitted 9, each of them the
best walk available from its entry — fewer layouts being better than bad ones.

Before it: **2026-09-03, tell_open_coop: a 31-layout family, every one of them
through the engine, no rule unmatched and no objective lost — and tell_coop's
11 layouts each ask the gamemode for less than the shipped map does.** That is the
largest family emitted and the first on a map of this size; §6b is the table.
Ahead of it, **both small families rotated through the engine, 2026-09-02. All
14 of inferno_new_v1's layouts are clean.** officeb3 keeps every objective on all 12
but still loses the one or two clusters per layout that have to move, for a
reason particular to that map. §6 is what the engine said.
`inferno_new_v1` emits all 14 of its layouts and `cs_officeb3_coop_v1_5` 13 of
14 — the refusal is stage 1's defenders having 4 usable coordinates on rung 0
for 31 points. Both files are written by `tools/make-presets.sh`, which picks
the operation from what the map is: a corridor gets its reversal, a small map
gets the family, and a map whose chain closes gets the family whatever else it
is — §3c is that measurement. The same applier reads all of them.

---

## 1. A layout is an injection from stages into rungs

The ladder is `docs/reverse.md` §2 unchanged: rung 0 is the attacker entry,
rung *k* is objective *k* with the defender zone that guards it, so a map with
N objectives has N+1 rungs and the shipped convention puts a fight and two
teams' worth of validated ground on every one of them.

A layout assigns each of the N stages one rung. That leaves exactly one rung
unclaimed, and **that rung is where the attackers arrive** — stage *j*'s
attackers spawn at stage *j−1*'s rung, because in stock play the attackers
respawn on the objective they have just taken, and stage 1's spawn on the
leftover.

That is the whole abstraction. `Plan` is the injection:

| plan | rungs, entry first | is |
|---|---|---|
| `stock_plan(n)` | `0 1 2 … N` | the map as it shipped |
| `reverse_plan(n)` | `N N−1 … 0` | `docs/reverse.md` |
| `ring_plan(n, e, d)` | walk the ladder as a ring from `e` | every starting objective, both directions |
| `explicit_plan([…])` | given rung by rung | anything |

`ring_plan(n, 0, "forward")` **is** `stock_plan(n)` and
`ring_plan(n, n, "backward")` **is** `reverse_plan(n)`, asserted rather than
asserted-about: those two are not special cases in the code, they are two of the
2(N+1) members, and they are the two that begin at an end of the corridor.

## 2. The cost of not starting at an end, measured

The rungs are a path. A walk that starts in the middle has to double back for
the rungs it went past, so **exactly one stage transition spans the corridor**.
Stock and the reversal are the only two walks that avoid it.

That is not a guess. Every layout now carries its per-stage **advance** — the
path distance from the rung a stage's attackers spawn on to the rung its
objective sits at, over the engine's own graph — beside the stock advances the
map was authored around. On ministry_coop the whole family reads:

| layout | rungs | worst advance |
|---|---|--:|
| `stock` | 0 1 2 3 4 5 6 | 13,324 u |
| `reversed` | 6 5 4 3 2 1 0 | 13,324 u |
| all twelve others | … | **24,349 u** |

13,324 u is the stock map's own longest leg (rung 4→5, `cachepoint_e` to
`cp6`). 24,349 u is the rung 6→0 step, and it is identical across all twelve
interior starts because it is always the same wrap. So on a corridor the family
is two good layouts and twelve that ask one stage to cross the map — which is
exactly why `places.py` licenses reversal for `linear` and randomised starts for
`small`, where that wrap is short because the whole map is.

**`layout()` warns, it does not refuse.** An advance is a judgement about how a
round plays; the refusals in that operation are otherwise all facts about
whether the engine will load the map — a capture volume covering no walkable
floor, a rung with nowhere to stand. `--max-advance X` turns it into a refusal
at X times the stock worst. The library leaves it off and `navlayout permute`
defaults it on, at the value calibrated below: everything the command writes is
a preset, and a preset is exactly the judgement the library declines to make.
`--max-advance off` is the ungated number again.

### All five corridors, and the exception that would earn a family

2026-09-02, every reversed map's whole family measured against its own stock:

| map | family | stock worst | reversed | best rotation | best rot / stock |
|---|---|--:|--:|--:|--:|
| ministry_coop | 14 | 13,324 | 13,324 | 24,349 | 1.8x |
| gizab_aof | 18 | 5,101 | 5,216 | 12,228 | 2.4x |
| market_coop | 18 | 6,659 | 6,303 | 24,117 | 3.6x |
| congress_coop | 20 | 8,351 | 8,351 | 30,150 | 3.6x |
| dead_air | 26 | 6,654 | 6,877 | 28,284 | 4.3x |

**The reversal is free on all five** - 0.95x to 1.03x, and exactly 1.00x where
the longest leg is interior, because reversing walks the same ladder the other
way and the longest advance belongs to the ladder rather than to the direction.
**Every rotation pays 1.8x at the very best**, and that is the cheapest one of
each family, not the worst; market_coop's and dead_air's rotations sit within
100 u of each other, so there is no lucky interior start hiding in them. A
corridor therefore needs exactly one layout, and it is the reversal.

The exception is a corridor whose ends actually meet. A rotation adds exactly
one edge - rung N back to rung 0 - so **`best rotation / stock` is a measurement
of whether the two ends connect**, and it is already computed. A ring would
score about 1.0x and would license its whole family; nothing in the corpus
scores under 1.8x yet. Two things that follow, neither done:

- a threshold in `tools/make-presets.sh` - a `linear` map inside ~1.25x is a
  ring and gets `permute --all`, otherwise reversal only, which is today's
  behaviour written down rather than assumed;
- a sweep of the ~40 surveyed maps that have a cpsetup, which is where a ring
  would turn up, rather than among the five already reversed.

## 3. The identity layout, and the bug it found

`stock_plan` moves nothing: every stage stays on its own rung. So the layout it
produces should rewrite every spawn point to the coordinate it already has, and
that is a self-test the operation had never been given. It failed it — **431 of
ministry_coop's 442 points landed somewhere other than where they started.**

Two causes, both now fixed, and both were making every other layout worse too:

- **`_assign` ranked authored coordinates and hull-fills together.** `_usable`
  returns the coordinates the engine already accepted first and the ones its
  hull probe validated afterwards, and re-sorting the whole pool by distance
  from its centre threw that order away — a point could be handed an invented
  coordinate while its own authored one went unused. The pool is now spent in
  tiers, precedent first.
- **A rung's authored pool was every point inside its zone volume.** Zones
  overlap, so a contested point was offered as a *destination* to two stages
  while belonging to one as a source. The incoming cluster and the destination
  pool were then non-corresponding sets with different centroids, and
  `place_zone` slid the zone by that difference even for a stage that was not
  moving. `dest_points` now narrows the pool to the stage that claimed those
  points, falling back to the whole pool where the narrowing empties it.

After both, seven of the thirteen shipped maps round-tripped exactly and the
rest came within a handful of points. The residual was real and was the
contested split: `place_zone` still searched for a delta, and where a zone's
ownership is genuinely ambiguous the search could prefer a placement a couple of
units off the identity.

**A third bug, found by the same self-test on a different map.** 2026-09-03,
`tell_open_coop`: the identity layout was *refused*, for `cp_p`'s capture volume
covering no walkable area on rung 16 — its own rung, where the map itself put
it. `cz_p` is a 144x240 u box over ten nav areas a player hull does not fit in,
so the gate was reading a fact about the shipped map as damage done by the
layout. The marker half of the same check had drawn that distinction since
`docs/reverse.md` §6 — it says "as it is on the stock map, so it is the map's
own and not this layout's" — and the volume half had not. It now measures the
box at its shipped origin too: **empty here and empty there is a warning, empty
here and covered there is still a refusal.** `cp_e` on rung 0 stays refused on
the same map, which is the discrimination working — that volume covers six
areas where it shipped.

**Handing the zone over instead of moving it removed the residual entirely.**
Since `docs/reverse.md` §3, a stage stands in the zone already on its rung, so
under `stock_plan` every stage is already in place, every cluster is a no-op,
and the identity layout emits **no spawn edit at all** — on all thirteen maps.
market_coop's 38 stray points, tell_coop's 28, verticality_coop's 21 and
revolt_coop's 20 are gone with the search that produced them, and revolt_coop is
no longer a special case here: there is nothing left for its ninth zone to
confuse.

## 3b. A small map is where the family is worth having

`places.py` calls a map `small` when its coarsened mesh has few places and no
hinge worth the name — 9 places on `inferno_new_v1`, 8 on
`cs_officeb3_coop_v1_5`, both around 6 Mu² of walkable floor against
ministry_coop's 31. §2's objection to an interior start is the wrap, and the
wrap is the length of the corridor: on a map this size there is barely one, so
`inferno_new_v1`'s whole family sits between 2,321 and 3,523 u of worst
advance against a stock worst of 2,627. That is the difference between the two
prescriptions, in one number.

Both maps are CS ports, which is also why they are the ones to try it on: the
objectives are rooms in a building rather than stops along a road, and the
chain the author picked is one of several the geometry would carry.

Where the family costs something, it says so per layout rather than in the
aggregate — `cs_officeb3_coop_v1_5`'s one refusal is a rung that cannot hold a
31-point defender cluster, and the other 13 layouts are emitted anyway.

## 3c. What the family costs is the chain's closing distance

§2 measured the wrap on one corridor. It generalises for a structural reason: a
ring walk traverses **the same ladder legs stock does, plus one** — the wrap,
from the last rung back to the first. So a walk's worst advance is
`max(the map's own legs, the wrap)`, every walk that starts in the middle pays
that one edge, and stock and the reversal are the only two that do not.

The wrap is therefore **how far a map's last objective is from its first**, and
that is a fact about the chain, not about the map. Measured 2026-09-03:

| map | wrap, rung N→0 | its own worst leg | ratio | emits | within 1.05x |
|---|--:|--:|--:|--:|--:|
| drycanal_open_coop | 5,417 | 8,985 | 0.60 | 24 | 24 |
| buhriz_coop | 5,309 | 8,092 | 0.66 | 14 | 14 |
| tell_coop | 4,504 | 6,597 | 0.68 | 28 | 28 |
| tell_open_coop | 5,880 | 7,675 | 0.77 | 32 | 32 |
| sinjar_coop | 8,202 | 6,140 | 1.34 | 16 | 2 |
| market_open_coop | 9,933 | 6,032 | 1.65 | 26 | 2 |
| contact_coop | 10,878 | 6,364 | 1.71 | 16 | 2 |
| ministry_coop | 24,349 | 13,324 | 1.83 | 14 | 2 |
| market_coop | 24,218 | 6,659 | 3.64 | — | — |
| **drycanal_coop** | **27,622** | 5,762 | **4.79** | 22 | 2 |

**Two shipped maps that both play as corridors sit at opposite ends of it.**
`drycanal_coop` is out and back — walk the length of the map and walk it again,
27,622 u of wrap, 4.79x its own worst leg, the sharpest measured. `tell_coop`
walks the whole map too, and its chain **closes**: the last objective is 4,504 u
from the first, *less than one ordinary leg of it*, so entering that loop at any
objective costs nothing at all. Both are corridors to play. One can afford the
whole family and the other cannot, and no property of the map says which — the
shape of the chain laid on it does.

So this measurement is not an openness test, and `places.py`'s label is not a
wrap test. They disagree in both directions, which is the argument for keeping
them apart: `drycanal_coop` is labelled `open` (its hinge fraction lands exactly
on the 0.10 boundary, with 6 hinges and 45% severance reported beside it) and
pays 4.79x; `tell_coop` is labelled `open`, plays as a corridor, and pays 0.68.

`tools/make-presets.sh` therefore offers the family to every map, and `permute`
gates it **per layout on the wrap**: `--max-advance` defaults to 1.5
(`GATE_MAX_ADVANCE` in `cli.py`), landed in the gap in the distribution above — everything that has ever been through the engine is at or
below 1.34, and the wrap-payers start at 1.65. A corridor whose chain does not
close comes out of that gate with the two walks a corridor has, stock and
reversed, which is the `linear` prescription arrived at by measuring instead of
by classifying. `market_open_coop`, `contact_coop`, `district_coop`,
`heights_coop` and `verticality_coop` each keep exactly those two;
`siege_coop` keeps none, for the reasons in §5 that predate this.

**What the family is not, on a closed chain.** Every ring walk uses the same
ladder legs, so what varies is *where the round enters the loop*, not which
ground it uses: 31 layouts on `tell_open_coop` traverse the map's objectives in
31 orders and add no route the stock chain did not have. That is a real variant
for a player and it is not new ground. New ground is re-siting, which is
`docs/authoring.md`.

## 4. Emitting a family

`--all` writes one preset file holding the whole family, which is what
`mapmaker_layout.sp` wants: it picks round-robin per map and persists the index,
so a file of N layouts is N rounds that do not repeat. The bare `stock` control
comes first, as before, and the computed stock layout is skipped rather than
written — an identity layout that rewrites 442 origins to themselves is a worse
control than one that rewrites nothing.

**The file used to get large.** When every layout rewrote every spawn point
individually, ministry_coop's family of thirteen was 1.2 MiB and 6,084 rules.
Handing zones over rather than moving them cut it to **228 KiB and 1,116 rules**
for the same thirteen: a layout is now a dozen zone rules plus the two clusters
that really move. The applier `ImportFromFile()`s the whole file in `OnMapInit`
and then reads only the preset it picked, so the cost is parse time at map load.
`--limit N` trims the rotation; `ring_family` is ordered stock, reversed, then
the interior starts, so a prefix is the right subset to take.

`MM_MAX_PRESETS` is 32 and the largest family here is tell_coop's 26 walks plus
the control, so the applier's limit is not the binding one. `MM_MAX_EDITS` is
1024 against market_coop's 761 rules per layout, and it is per *preset*, not per
file.

## 5. What is unchanged, and what still refuses

The mechanism underneath is `docs/reverse.md` §3–§6 exactly: a stage handed the
zone already standing on its rung, points re-sited onto authored twins only
where there is no zone to hand over, the move searched rather than computed,
contested points going to the zone they sit deepest inside, capture volumes
translating without resizing, block zones staying with the ground they guard.

The corpus verdict moved with it: of the 13 shipped `_coop` maps the reversal
now emits on **12** and is refused only on `siege_coop`. `embassy_coop` and
`revolt_coop` were refused for too few usable coordinates on the entry rung, and
the cluster that needed them no longer has to find them — it stands in the zone
that was already there. `siege_coop` still refuses on both of its reasons: the
one cluster that genuinely has to move finds 1 usable coordinate on rung 9 for
18 points, and `cp_9`'s volume covers no walkable floor where it lands.

`revolt_coop` is still worth a line for its shape. It has **nine** numbered
spawn zones and nine control points against a chain of eight: `spawnzone9`
carries 10 defender points where every in-chain zone carries 23–50. The ninth is
a stage that was cut with its entities left behind. Under the hand-over that
costs nothing — the ninth zone simply belongs to no rung any stage claims, and
is renamed out of the cpsetup's vocabulary with the other unclaimed volumes.

## 6. What the engine said

2026-09-02, `tools/check-layout.sh --rounds 14` and `--rounds 13`: each family
walked end to end in one session, every layout against the map's own stock load.
Both maps applied every layout and neither lost a route. Both also failed, for
two unrelated reasons, and neither reason is the permutation.

**inferno_new_v1 — 6 of 14 layouts clean; the other 8 lose one or two objectives
to the gamemode, and `marker_gap` does not predict which.** No rule failed on
any layout (`bad` 0 throughout) and the hull count stayed within a few points of
stock's 277/299. What varies is `Failed finding CP area for N` — 0 on stock, 1
or 2 on eight of the thirteen non-stock layouts:

| clean | refuses an objective |
|---|---|
| `stock`, `enter1_fwd`, `enter2_rev`, `enter3_rev`, `enter4_rev`, `enter5_rev` | `reversed`, `enter0_rev`, `enter1_rev`, `enter2_fwd`, `enter3_fwd`, `enter4_fwd`, `enter5_fwd`, `enter6_fwd` |

**Every refusal was a marker the generator translated. Every marker it re-sited
was accepted.** That was the whole of it. Over the first run's 14 layouts there
were 70 marker placements:

| | accepted | refused |
|---|---|---|
| marker re-sited, 72 u | 28 | **0** |
| marker translated | 32 | **10** |

The re-siting `docs/reverse.md` §6 introduced was never the problem. The test
deciding when to use it was, and no threshold on the quantity it tests can be
made to work, because `marker_gap` is a distance to the nearest walkable area's
**centre** — it adds a horizontal error to a vertical one, and the engine reads
those as nothing like the same thing. Measured against the survey's footprints:

- every marker the engine **accepted** is within 11 u horizontally of a walkable
  footprint, median 0 — standing over one;
- it accepts markers as much as **232 u above** the floor they stand over, and
  the shipped map ships two markers with *no footprint under them at all*,
  10.0 and 11.2 u past the nearest edge, which it also accepts;
- of the ten it **refused**, six had no footprint under them, 7.5 to 32.5 u out
  past the edge, and the other four were over one but 109–117 u up.

Accepted runs to 11.2 u off the mesh and refused starts at 7.5. The two
distributions overlap on every column, solidity included — four accepted
placements are inside solid geometry and two refused ones are not — so there is
no line to draw. Trying to draw one was the mistake.

**So the gate is gone: a marker that moves is now stood on floor the objective
covers, always.** There was never anything to gain by testing first — re-siting
was 28 for 28, and it cannot be worse than leaving the marker wherever the
translation dropped it. `MARKER_GAP` survives only as the number the warnings
quote. Re-run against that change, the whole family comes back clean:

    load  preset       edits  rules  bad  marker      hull  CP area
    1     stock            -      -    0       0   277/299        0
    2     stock            0      0    0       0   277/299        0
    3     reversed        77     76    0       0   278/299        0
    ...   (ten more)                                             0
    15    enter6_fwd     109    107    0       1   286/299        0

Ten refusals to none, `bad` 0 throughout, and the hull count within a few points
of stock's 277/299 on every layout. All five corridor maps were re-run against
the same change and are unchanged.

The one thing worth keeping from the search is why ministry's calibration
looked sound for so long: the single marker it was fitted against was out
horizontally *and* vertically at once, so one scalar over the two could still
rank it. A reversal re-sites or leaves markers alone; only a permutation
translates enough of them to expose it.

**cs_officeb3_coop_v1_5 — every layout loses the two clusters that have to
move, because this map's spawn zones cannot be moved at all.** The first run
was much worse than that: *every* zone rule on all 13 layouts read back
`unmatched`, 10–12 per layout, 0 ok. The cause was in the applier and is now
fixed (`plugin/README.md`) — this is the one map of the seven whose
`ins_spawnzone` entries carry **no `origin` key** (0 of 27, where inferno has
36 of 36 and ministry 22 of 22), and `ApplyEdits` dropped any lump entry
without one before it looked at a single rule. Since §3 made all but two
clusters per layout a *rename*, which needs no origin, that discarded almost
the whole layout. With the guard moved to the rules that actually need a
coordinate:

    ins_spawnzone: 12 rules over 27 entities - unmatched=2 ... ok=10
    (was)          12 rules over 27 entities - unmatched=12 ... ok=0

and `bad` per layout falls from 10–12 to **1–2**. Every objective survives, and
better than survives: officeb3's `CP area` is 2 on stock and 0 on ten of the
twelve layouts, 1 on the other two — the permuted chain asks the gamemode for
*less* than the shipped map does — and the hull count is within a couple of
points on half the family, though `enter1_rev`, `enter3_rev` and `enter2_rev`
lose 10–18 points that stock accepted.

What is left is real and is not a bug: the one or two clusters per layout that
genuinely have to *move* cannot. A brush entity with no origin key carries its
position in its model, and writing an absolute origin onto it translates it by
the whole coordinate instead of to it — so the applier refuses the move and
leaves the rule loudly unmatched rather than quietly doing only the renaming
half. **A map whose spawn zones are pure brush entities can be handed its zones
but cannot have them moved**, which makes officeb3 a map for the layouts that
only need hand-over. Which of the 13 those are is not yet asked offline; the
emitter knows which clusters it marks `moved`, so it is a gate it could apply.

The oracle for all of the above is the map's own stock load, which the applier
now verifies as well: on every map in this run the second stock load reproduced
the boot load's hull count and CP-area count exactly.


## 6b. What the engine said about an open map

2026-09-03, `tools/check-layout.sh --rounds 8` then `--rounds 24` over
`tell_open_coop`: **all 31 layouts, and nothing to report.**

| | stock | the 31 layouts |
|---|--:|---|
| rules that did not describe the map | 0 | **0 on every layout** |
| `Failed finding CP area` | 0 | **0 on every layout** |
| spawn points that fit the player hull | 448/483 | 442–457 |
| edits per layout | — | 65–145 (46–125 rules) |

This is the map the openness argument was aimed at, and it is the largest thing
the applier has been asked to do: 16 objectives, 8 of them caches, 52 spawn
zones, 483 spawn points, 2,709 rules across the family. Three things are worth
saying about the columns.

**Nothing was lost.** `bad` is 0 everywhere — every rule in every layout matched
the entity its lump entry became — and `CP area` is 0 everywhere including
stock, so this map asks the gamemode for nothing it refuses, in any order. That
is a better result than either small map got (§6), and the difference is that
tell_open_coop's markers are all re-sited rather than translated.

**The hull count moves both ways, which is what measuring the ground looks
like.** Fourteen layouts stand a few points on geometry no player fits — 8 at
worst, on `enter5_fwd` — and five stand *more* than stock does, up to 457
against stock's 448. A layout that only ever lost points would be a layout doing
damage; one that gains and loses a handful is one being measured.

**tell_coop, the vanilla map, the same day: 11 layouts, and they ask the
gamemode for less than the shipped map does.** `bad` 0 and `adrift` 0 on every
layout, hull 637–647 against stock's 642/669 — and `Failed finding CP area`
**3 on stock, 0 on all eleven**. The shipped map leaves three markers where the
mapper put them and the gamemode cannot find an area for them; a layout that
moves one stands it on floor the objective itself covers, so the permutation
repairs what it touches. `cs_officeb3_coop_v1_5` did the same thing in §6, on a
map of a completely different shape, which makes it the second sighting of the
same effect rather than a curiosity of one map.

**The offline gate was pessimistic in the right direction.** `permute` warned
that three of the shipped markers — `cp_h`, `cp_k`, `cp_l` — sit 193–201 u from
the nearest walkable centre *on the stock map*, which is over `MARKER_GAP`, and
predicted the gamemode would refuse them either way. It refused none of them.
The warning is still worth having, because it costs nothing and the failure it
guards is invisible until someone plays the round, but on this map it named
three objectives the engine was happy with.

## 7. Playing it, and the two things a player found in ten minutes

2026-09-03, `tell_open_coop` on a live server, `enter1_rev` off the 31-layout
family §6b passed. Both faults below were invisible to every offline check in
this document and to the in-engine harness, because both are about *how a round
plays* and everything before this measured whether the engine would load it.

**"I spawned between enemies with an objective right in front of me."** The
ladder is built on the shipped convention that attacker zone k+1 stands on rung
k, which §1 states and which both corridor maps confirmed. It is a convention,
not a guarantee. tell_open_coop has **nine distinct attacker volumes for
sixteen stages** — `sz_a`=`sz_b`, `sz_g`=`sz_h`=`sz_m`=`sz_o`,
`sz_i`=`sz_l`=`sz_n`, `sz_j`=`sz_k` — so "the ground stage 2's attackers stood
on" is not rung 1, it is rung 0, the same insertion point stage 1 uses. A walk
that then fights a stage on rung 0 puts the objective and its defenders in that
same box: on `enter1_rev`, **23 of the 38 stage-1 defender points landed inside
the stage-1 attacker spawn volume**, and `cache_a` 427 u from its centre.

Seven such steps exist on this map — `1->0`, `3->16`, `5->3`, `8->7`, `10->9`,
`11->7`, `13->7` — and nine of the 31 layouts took one. `arrival_clashes`
enumerates them off the survey, `_path_cost` charges for them so the optimiser
routes around them, and `layout` refuses a plan that takes one anyway. The
layout the player was standing in is now refused by name, naming `sz_b`.

*(§11 keeps the charge and drops the refusal: the clash is a fact about the
borrow, not about the walk, and a volume moved onto the previous rung answers
it. `layout` still refuses where the move cannot clear the objective.)*

**"The order doesn't make sense. Objective D is far away, but C and E are next
to each other."** Correct, and measured: D is 4,331 u from C and 3,336 u from E
while C and E are 1,235 u apart. The detour ratio — `(there + back) / straight`
— is 6.21x at that stage, and the family's gate never looked at it. It gates on
*advance*, which is each stage against the last, and says nothing about whether
the sequence goes anywhere.

The cause is one line of §1's design: `ring_family` walks the ring `0,1,..,N`,
the order the **chain** numbers the rungs. On a corridor that is also the order
they stand in on the ground. On an open map it is not, and tell_open_coop's
shipped chain crosses itself five times — so all 31 members inherited it, every
one of them between 6.47x and 6.79x, with **the shipped chain itself the worst
of the lot at 6.79x**. No rotation of a ring can be better than the ring.

So the ring is measured instead. `geometry_ring` seeds from the chain order,
the principal axis and the polar angle, and 2-opts against the mesh distance
matrix the advances already use; `walk_family` then optimises each member from
its own entry rather than rotating one ring, because the edge a walk drops is
the ring's longest and it should fall at the end of the round, not the middle.
Ranking is `(clashes, walk-ons, detour, longest march, length)` — §8 added the
second term — with detours under `DETOUR_FLOOR` treated as equal so the
optimiser does not buy 0.02x of straightness with a 4,000 u march.

| tell_open_coop | detour | worst advance | layouts |
|---|--:|--:|--:|
| the shipped chain | 6.79x | 7,675 | — |
| family on the chain ring (§6b) | 6.47–6.79x | 7,675 | 31 |
| family on the measured walk | **1.78–2.35x** | 5,377–9,373 | 27 |

`--max-detour` (2.5, `GATE_MAX_DETOUR` in `cli.py`) is the gate, and it refuses
the shipped chain — which is the right verdict on a round that doubles back five
times. Corridors are unaffected, as they should be: contact_coop 1.84x stock
against 1.80–1.92x for its family, congress_coop 1.86x against 1.80–2.03x,
drycanal_coop 1.84x against 1.55–1.95x. Where it bites is the maps that were
already wandering — cs_officeb3 7.00x stock, best member now 1.32x; badlands_b1
3.76x, now 1.70x; district_coop 2.81x, now 1.71x. Swept over all 45 surveyed
maps with a cpsetup, three cannot meet it at all — `gioconda_eron_agroprom`
(best 3.43x), `gioconda_eron_garbage` (2.75x) and `gioconda_eron_darkscape`
(2.90x) — and a fourth, `gioconda_eron_kordon`, keeps 4 of 25. Those four are
one author's set of large scattered-objective maps whose sites no order walks
coherently, and they get a thin family or none rather than a bad one. Everywhere
else the gate costs at most one member: 25 of 25 on dead_air, 30 of 30 on
gioconda_eron_mountains, 32 of 34 on tell_open_coop.

**What is still open.** The chain's *length* is the limit §8 of
`docs/layout-variants.md` names, and it is what this cannot fix: tell_open_coop
ships `cp_h` and `cp_k` 123 u apart, so some stage is always captured on
arrival — 442 u is the shortest advance in every member of the new family, and
it is that pair. Fewer, better-separated objectives is the right answer and it
needs a way to change the chain's length server-side. `min_advance` in the
preset's metrics block is the count of what that would buy.

*(§8 corrects the "always" in that paragraph: the 442 u was the optimiser's
choice, not the map's. It is 1,284 u now.)*

## 8. A step can be too short, and the cost function was paying for it

2026-09-17, a player on `cs_officeb3_coop_v1_5` `enter1_fwd`: **"objectives D
and E were too close together."** They were 155 u apart — stage 5's objective
sat 155 u from the ground stage 4 had just taken, so E was captured on arrival
at D. Like both faults in §7 this was invisible to every offline check and to
the in-engine harness, which had passed the layout with `bad` 0 and *better*
numbers than stock: hull 204/214 against 186, `Failed finding CP area` 0
against 2.

**The map offers the step; the cost function went looking for it.** officeb3
has three of its seven rungs in one cluster — 0–3 155 u, 0–5 820 u, 3–5 929 u —
and `_path_cost` ranked on `(clashes, detour, longest march, length)`, whose
last two terms are the longest edge and the total. The shortest edge in the
matrix is therefore free money, so 2-opt seeks it out and pairs those rungs in
every member it can. §7 built the measured walk to stop the round doubling
back, and in doing so gave it a reason to prefer a step that is no round at
all.

So `SHORT_ADVANCE` is a term in the cost now, counted with the arrival clashes
and ranked above everything about distance — both are "this stage is not a
fight", and no amount of straightness buys either back. **1,000 u is the
shipped corpus's own answer.** Over the 43 surveyed maps with a cpsetup, the
shortest step a shipped chain asks of anyone:

| | shortest stock step |
|---|--:|
| gioconda_eron_mountains | 0 u — two objectives in the same place |
| — the gap — | |
| siege_coop | 1,258 |
| tell_open_coop | 1,295 |
| market_open_coop | 1,609 |
| median of all 43 | 3,007 |
| p90 | 4,598 |

42 of 43 mappers stay above 1,258 u, the one exception is a map broken in this
exact way, and 1,000 is the round number in the gap.

**It costs two layouts across the corpus.** 393 emitted before, 391 after, and
35 of the 42 maps identical — the term only bites where rungs sit on each
other. Four maps lose the fault outright:

| map | family's shortest step | layouts |
|---|--:|--:|
| cs_officeb3_coop_v1_5 | 155 u → **1,702** | 9 → 4 |
| tell_open_coop | 442 → **1,284** | 29 → 26 |
| drycanal_open_coop | 950 → **3,891** | 23 → 26 |
| embassy_coop | 520 → **2,275** | 6 → 8 |

Two of them gain layouts, which is the term breaking ties the old cost broke
badly. officeb3 pays, 9 down to 4, and **brute force over all 5,040 of its
walks says that is the map**: entries 1, 2, 4 and 6 have no walk at all that is
clean and inside the detour gate, and the three it does emit (plus one
reversal) are each the global optimum from their entry. Fewer layouts is the
right trade — the nine included the one the player was standing in.

`--min-advance` is the refusal for what the ranking cannot avoid, 1,000 u
(`GATE_MIN_ADVANCE` in `cli.py`).

All three gates are `permute`'s defaults rather than flags a caller has to
remember, so that a preset file regenerated by any route cannot quietly carry
walks these measurements already refused. `tools/make-presets.sh` names no
values of its own; `MM_MAX_ADVANCE`, `MM_MAX_DETOUR` and `MM_MIN_ADVANCE`
override one for an experiment, and `off` turns one off.

### What the refusal exposed: twelve maps never got the measured walk

Turning it on collapses `inferno_new_v1` from 14 layouts to 1 and `shellshock`
from 4 to none, and the reason is not those maps' geometry. `walk_family`
gives up and calls `ring_family` — the rotated chain ring §7 replaced — when
**any** pair of rungs is unreachable, because an infinite edge makes every tour
through it infinite and the seeds cannot be ranked. inferno's rung 5 is
unreachable from all six others, so every layout on that map has always been a
rotation of the chain ring, §7's improvement included, and its 517 u step
between rungs 0 and 6 is one no walk on that map has ever tried to avoid.

Measured across the corpus, **12 of 42 maps have at least one rung the rest of
the ladder cannot walk to** and so fall back: `inferno_new_v1`, `gizab_aof`,
`shellshock`, `verticality_coop`, `warehouse_coopv2u1`, `panama_canal_b2`,
`oilfield_pve` and five of the gioconda set. Whether those rungs are genuinely
disconnected ground or a snap that failed is not yet asked, and it decides the
fix: a distance that is *unknown* is not a distance that is *impossible*.

## 9. A cache is stood on its rung, not translated onto it

2026-09-18, `tell_coop` on a live server: *"current preset also puts stockpile
objectives in strange locations."* It did, and the fault was the one §6 had
already fixed for markers, left unfixed for caches.

A cache moved by `delta = dest.floor - src_floor`, which is the difference
between two **area centres**. That is the right delta for the rung, and the
wrong one for the entity: what it emits is `dest.floor + (cache - src_floor)`,
so the cache keeps its offset from its own rung's floor point and both ends of
that offset carry the error between an area's centre and the spot in the room
the mapper actually used. The two add, and a cache lands where neither end of
the map ever put one.

Measured over tell_coop's 26 layouts, 156 cache placements:

| | before | after |
|---|---|---|
| 157–289 u above the floor | 18 | 0 |
| worst lift above the snapped area | 289 u | 18 u |

18 u is `cache_c`'s own shipped rounding, so the residue is the map's, not the
layout's. The shipped caches sit within 18 u of their area centre on this map;
the emitted ones now do too. Over five maps — tell_coop, tell_open_coop,
de_vertigo_coop, ministry_coop, inferno_new_v1, 106 layouts and 520 placements —
nothing is off the mesh and nothing floats beyond the lift its own map gave it
(tell_open_coop ships two caches at +36 and +41 u, and those are kept).

`_cache_stand` is the placement: the rung's own floor when the player hull fits
there, else the nearest area within `CACHE_STAND` = 384 u on the same storey
that it does fit on. The reach is needed — 7 of tell_coop's 14 rungs and 6 of
ministry_coop's 7 resolve onto an area the hull is refused on — and 384 is above
the furthest any rung in four maps has to go (258 u) while staying inside the
room, so it cannot put a cache through a wall. A cache is a point entity with no
volume whose shape translation preserves, which is exactly why there is nothing
to trade away here; the capture volumes still translate, because for them there
is.

**It only fires on a cache that moves.** `norm(delta) > 1.0`, the same guard the
marker uses, so the identity layout stays the identity: stock's caches come out
0.00 u from where the map shipped them on all three maps checked.

## 10. Nine maps that "had no map data", and what they actually had

2026-09-19, over the 125-map survey corpus: 18 maps emitted nothing, and half
of them were written off as having no objective chain to permute — *no cpsetup,
or a malformed one, so there is nothing to work with*. That was a statement
about this reader rather than about the maps. Eight of the nine have everything
a ladder needs.

**Six were turned away at the door by the root block's name.** A cpsetup is
KeyValues, and KeyValues takes a root block's name from the file, not from the
loader that opens it: `"map.txt" { "checkpoint" { ... } }` reaches the gamemode
exactly as `"cpsetup.txt" { ... }` does. `read_cpsetup` was asking for the
shipped spelling and falling back to the file's *top level*, where there are no
`controlpoint` keys, so it returned an empty chain and every caller said "no
cpsetup". The six:

| map | calls its root block | chain |
|---|---|---|
| haditha_coop | `"haditha_coop.txt"` | 6 |
| iron_express | `"iron_express.txt"` | 11 |
| karkand_coop_p1_redux_v1_7 | `"map.txt"` | 12 |
| karkand_redux_p1_v1_2 | `"map.txt"` | 12 |
| karkand_redux_p2 | `"map.txt"` | 7 |
| szepezd_redux_coop | `"controlpointsetup.txt"` | 10 |

All six carry a full `checkpoint` block, all six play in game, and between them
they were holding **45 layouts**. `_root_block` now takes the file's outer
block whatever it is called, preferring one that holds a `checkpoint` so a file
that opens with something else cannot shadow it. The other 119 maps name it
`"cpsetup.txt"` and read exactly as before.

**Two named objectives the map does not have.** cobblestone's chain runs `cp1`
to `cp8` and its BSP has `cp1, cp2, cp3, cp5, cp8` with `cp4` and `cp6` created
by the `.txt`'s own entities block — `cp7` exists nowhere, so the map plays
seven objectives against its seven spawn zones and the guard was reading it as
eight against seven. ins_kuwaiti_oilfields names three cache points whose
`obj_weapon_cache` blocks have an empty `origin` and no `point_controlpoint` to
go with them; the three that are real are `cp1`, `cp2`, `cp3`, and the map has
three spawn zones.

`resolve` had always skipped a chain name with no entity behind it — this is
the same rule, applied one step earlier so the *counts* follow it too.
`objectives.playable` drops those names before anything is built, which also
closes a gap between `build_rungs`, which sizes the ladder from the objectives,
and `dest_zone`, which sized it from the chain: bombshelter's top rung was
asking for `spawnzone_7`, a name its `.txt` lists and its BSP does not have.
Four maps are affected and the emitted counts of the two that already worked do
not move (bombshelter 14, launch_control_coop_ws 20); what changes there is
that a zone belonging to no stage is no longer claimed by one.

**Which zone a surviving stage inherits** is the only judgement in it, and the
shipped convention decides it — defender zone on its own objective, attacker
zone on the one before. Measured both ways: the survivors taking the zones **in
order** covers all seven of cobblestone's stages at 910 u / 823 u where keeping
each survivor's own index covers six and five at 899 / 1,065; on kuwaiti it is
three stages at 2,103 / 283 against two at 4,849 / 4,431; on
launch_control_coop_ws the defender half is identical and the attacker half is
969 against 1,129, because `cp_k` keeps `spawnzone_j`, whose seven attacker
points stand 413 u from `cp_i`, rather than `spawnzone_k`'s at 2,427 u. On
bombshelter the two rules agree, because the name it drops is the last one.

**The ninth is honestly refused, and it is the map that says so.**
gioconda_eron_panj names eight objectives and its BSP contains exactly two
`ins_spawnzone` volumes, both called `sz_a` — one per team, holding all 110 of
its spawn points. There is no ladder of authored zones to permute, because the
map authored one place to spawn and leaves the rest to the engine's nav
spawning. The refusal now says that in those terms rather than blaming the
`.txt`.

Over the corpus, `permute --all` on all 125 surveys: **986 layouts before, 1,033
after; 20 maps emitting nothing before, 13 after.** No map's count falls.

### Could the objectives be found with no cpsetup at all?

The set can be. A `point_controlpoint` is the marker, a `trigger_capture_zone`
names the marker it captures in its own `controlpoint` key, and an
`obj_weapon_cache` names its control point too — all three are in the entity
lump, so *which* objectives a map has is readable from the `.bsp` alone and
`objectives.resolve` already reads it that way.

What is not in the map is the **order**, and the chain order is what a stock
walk is measured against. It can be inferred, because the shipped convention
binds each numbered spawn zone to an objective: pair the two as an assignment
problem over the defender zones' own spawn points and order the objectives by
their zone's number. Measured against the 90 surveyed maps that have both a
chain and numbered zones, that reproduces the shipped chain **exactly on 62**,
with a median per-slot agreement of 1.00 — and gets badlands_b1 and
point_blank_fix wholly wrong, because their largest numbered zone family
belongs to another game mode.

So it is a fallback and not an authority, and at present it has nothing to fall
back for: all 125 surveyed maps now have a `.txt` in the game tree, and after
§10 all 125 of them parse.

## 11. The arrival clash was a fact about the borrow, not about the walk

2026-09-19, off the back of the twelve maps in the 125-map corpus that still
emitted no preset. §7 made "the attackers arrive inside the objective" a
refusal, on the grounds that no judgement is involved in it. That much is still
true. What was wrong is what the refusal was aimed at.

Measured over the 1,902 layouts the corpus builds, the clash took **53 of
them — every single one a reversed walk**, never a forward one, and it lands
nowhere in particular in the round: 4 at stage 1, 42 at an interior stage, 15 at
the last. Sixteen layouts across fourteen maps died of nothing else, which cost
almaden_coop, dead_air and liberty_mall their only layout and cost crash_course,
fairgrounds, caves_coop, dust_new, dog_red, sinjar_coop, revolt_coop,
district_night_coop, dunkirk_pve and tell_open_coop a member of their family.

Look at what the check actually asks and the aim is off by one step. Stage j's
attackers are meant to stand on the objective they have **just taken**, which is
rung `plan.at(j-1)`, and the ladder gets them there by naming the zone authored
one rung along. The clash is that the named volume happens to stand over the
rung stage j is now *fought* at. That is a defect in the borrow, not in the
walk — and `layout` already has the other half of the answer, because the ends
of the ladder have no zone to borrow either and **move** a volume onto the rung
instead.

So the detection stays where it was and its verdict changes: it records the
stage, forces `move`, and anchors the mover on the previous rung's own floor.
The refusal moves to after the placement, where it can ask whether the move
worked — a map with no free volume, or none that clears the objective, is
refused in the words §7 used.

**Two things had to be ranked differently for the move to go anywhere.**
`place_zone` ranks placements by how many coordinates a team can stand on, and
for a re-site every term below that votes to stay: the `authored` pool *is* the
cluster standing on the objective, so the stock position keeps all of its
precedent while the new rung has to fill from area centres. almaden_coop stage 5
is the case — twelve authored coordinates where it is, nine on the rung it
should be on — and with the rung ranked below the count the zone came back
exactly where it started, to be refused for the clash the move was supposed to
fix. Under a `forbid` the rung outranks the count, with a `viable` floor under
it so the trade cannot buy a box nothing fits in.

And **`forbid` is the objective's capture volume, not a point on its rung.** The
rung floor is what detects the clash and it is too small to place against:
clearing one coordinate leaves the near side of the objective as well as the
far, and the near side is where the coordinate count wants to go. With the point,
4 of 48 re-sites emitted between 135 and 195 u of the objective they had been
moved off — dust_new `enter3_rev` at 135 u is a player spawning against the
capture volume's wall. With the volume, that same placement goes to 1,578 u or
is refused.

| over the 125-map corpus | before | after |
|---|--:|--:|
| layouts passing | 1,075 | 1,066 |
| gained | — | 12 over 10 maps |
| lost | — | **0** |
| maps emitting a preset | 113 | 116 |
| clashes re-sited | — | 43 |
| clashes still refused | 53 | 18 |
| median distance off the objective | — | 2,036 u |

The three new preset files are almaden_coop, dead_air and liberty_mall, each
gaining exactly its reversal; everything else on those maps is refused by
`--max-advance` and still is. crash_course, dead_air and fairgrounds also stop
falling through `make-presets.sh`'s corridor fallback into the family, because
the reversal their shape licenses now places — which is why the layouts the
corpus *builds* drops from 1,902 to 1,836 while the layouts it passes barely
moves.

de_aztec_coop is the map that shows the volume test earning its place. Against
the rung point its reversal emitted, with the cluster 181 u off the objective.
Against the volume the only placement that clears the objective seats **1 of its
13 points**, so it is refused — and 1 of 13 is the honest answer for that map.

`_path_cost` still charges for a clash and the optimiser still routes around
one, which is right: a re-sited arrival gives up its authored ground and fills
from area centres, and that is a cost even when it works. It is no longer a
reason to throw the walk away.

The identity self-test is untouched: over the 106 maps whose stock walk both
runs measure, every one comes back with the same points moved, the same verdict
and the same warnings. No stock layout has ever had a clash — all 53 were
reversals — so nothing in this path runs for it.

**All three went through the engine, 2026-09-19**, `tools/check-layout.sh
--rounds 2`, each against its own stock load as the control:

| map | preset | edits | bad rules | hull, stock → preset | CP area |
|---|---|--:|--:|---|--:|
| dead_air | `reversed` | 90 | 0 | 633/634 → 631/634 | 0 |
| almaden_coop | `reversed` | 55 | 0 | 302/308 → 302/308 | **2 → 0** |
| liberty_mall | `reversed` | 89 | 0 | 347/348 → 347/348 | 0 |

No rule failed to describe its map, no objective was lost, and the re-sited
arrival cost dead_air two spawn points of 634 and the other two none. almaden_coop
is the one worth noticing: the stock map logs `Failed finding CP area` twice and
the reversal logs it **not at all**, because standing an objective on a rung
puts its marker on floor that the shipped map never had under it.
