# Playing a linear map backwards

The cheapest real variant a corridor map admits, and the first layout operation
built: run the objective chain from the far end. It needs no new placement
anywhere — every coordinate it emits is one the shipped map already used —
and it runs entirely through the layout plugin, so clients join stock.

    navlayout reverse ministry_coop --cfg game/insurgency/presets/ministry_coop.cfg

Status: **all five corridor maps emit, and all five have been through the engine.**
2026-09-01, ministry_coop, a live server: 101 lump edits, every rule matched,
and the corridor walked from the far end. What that cost is in
`plugin/README.md` — four faults no offline check could have found.

2026-09-02 the other four maps that read `linear` or `segmented` —
market_coop, congress_coop, dead_air, gizab_aof — emit as well, and
`tools/check-layout.sh` was written to put a layout in front of the engine
without a person at the console: it loads the map stock, changelevels it once
per preset, and reads the applier's verdict and the gamemode's own complaints
back off the console. Its first answer is §6, and it is the reason
`marker_gap` exists — the gamemode looks an objective's nav area up from the
**marker**, not from the capture volume, and a translated marker loses it.
All five have now been through it, each against its own stock load:

| map | edits | rules | bad | adrift | marker | hull, stock -> reversed | CP area |
|---|---|---|---|---|---|---|---|
| ministry_coop | 101 | 92 | 0 | 1 | 3 | 441/442 -> 440/442 | 1 -> 1 |
| market_coop | 119 | 114 | 0 | 1 | 3 | 619/636 -> 626/636 | 0 -> 0 |
| congress_coop | 57 | 57 | 0 | 0 | 3 | 287/294 -> 288/294 | 0 -> 0 |
| dead_air | 94 | 90 | 0 | 0 | 5 | 633/634 -> 632/634 | 0 -> 0 |
| gizab_aof | 96 | 94 | 0 | 1 | 1 | 647/652 -> 645/652 | 0 -> 0 |

**No rule failed to describe its map, and no map lost an objective.** The one
CP-area complaint is ministry's, the one the stock map already makes; the other
four maps make none reversed because they make none stock. The hull count moves
by a point or two either way and once upward — market_coop's reversal stands
seven points that stock left in geometry no player fits — which is the shape of
a number that is measuring the ground rather than the edit. That is the
prediction `MARKER_GAP = 150` makes, and it was calibrated on ministry alone;
four maps it had never seen kept it.

`marker` above is pessimistic by construction and always will be (§6, last
paragraph).

Since 2026-08-31 a stage is handed the zone already standing on its rung rather
than having one moved onto it (§3), so all but two clusters per layout are a
rename and nothing else.

> This turned out to be one case of a general operation: which rung each slot is
> fought at is a free choice, and a reversal is one choice of it.
> `docs/permute.md` is that generalisation, `navlayout permute` runs it, and
> everything below is unchanged underneath it.

---

## 1. The chain cannot be reordered, so the ground is permuted instead

Objective order lives in `maps/<name>.txt` and the applier cannot rewrite it —
§8 of `docs/layout-variants.md` is the limit, and it survived the move from
patching files to rewriting the entity lump at load.

So a reversal does not reorder anything. **The chain still reads 1..N and the
ground under it is permuted:** slot 1 is fought where slot N was, slot 2 where
slot N-1 was. The round plays the corridor from the far end and the `.txt` is
untouched, which also means one `.txt` still serves every preset — and, since
the permutation is free, every *other* permutation of it as well
(`docs/permute.md`).

## 2. The ladder

Measured on both linear maps that have a cpsetup on this machine, the shipped
convention is exact:

> **defender zone k sits on cp_k; attacker zone k sits on cp_(k-1)**

so the attackers respawn on the objective they just took. ministry_coop's median
path distance from a spawn point to its nearest objective — defender zones at
869, 1388, 1240, 1435, –, 1373 u; attacker zones landing on cp1, cp1, cp2, cp3,
cp4, – — and market_coop repeats it across eight stages.

That gives a ladder of **rungs**, each one a place the map authored as somewhere
a fight happens and two teams stand:

| rung | is |
|---|---|
| 0 | the attacker entry — attacker zone 1 |
| k | objective k, with the defender zone that guards it |

Stock, stage *j* is fought at rung *j* with attackers at rung *j−1*. Reversed,
stage *j* is fought at rung *N−j* with attackers at rung *N−j+1* — the same
relation, walked the other way:

| new stage | objective at | defenders | attackers |
|---|---|---|---|
| 1 | rung N−1 | rung N−1 | rung N |
| 2 | rung N−2 | rung N−2 | rung N−1 |
| … | … | … | … |
| N | rung 0 | rung 0 | rung 1 |

**Both ends close.** The attackers enter at rung N, which is the last
objective's ground — that objective becomes a spawn rather than a target. The
final stage's objective lands on rung 0, the ground the attackers used to spawn
on. Nothing has to be invented at either end, which is what makes this operation
cheap: there is no candidate siting pass and no offline placement to be wrong
about.

## 3. The zone on the rung is handed over, not moved

A rung already carries the zone the map authored for it. The cheapest correct
thing a permutation can do is give the stage fought there **that zone**, and the
cpsetup makes it a one-word edit: a stage is bound to a spawn zone by
*targetname*, the applier rewrites the entity lump before any entity exists, so
renaming the volume standing on rung 5 to `spawnzone_1` hands stage 1 the ground
— and the 68 spawn points already inside it, at the coordinates the map shipped.

Nothing moves. No volume slides, no point is re-sited, and there is no fill,
no separation rule and no assignment to be wrong about. On ministry_coop the
reversal went from 468 lump rewrites to 92, and from 442 spawn points moved to
74.

**Both ends of the ladder are the exception**, and they are the only one: rung 0
carries no defender zone and rung N no attacker zone, because in stock play
nobody ever defends the entry or attacks from past the last objective. Those two
stages fall back to moving a volume onto their rung, which is what §4 is about.
The reversal on ministry_coop is therefore ten renames and two moves.

**A rename cycle is safe in one pass.** Stage 1 takes `spawnzone_5`'s name while
stage 5 takes `spawnzone_1`'s, so the edits form a permutation. The applier
visits each lump entry once and stops at its first matching rule, so an entry it
has already renamed A → B is never re-read by the rule for B, and the cycle
resolves without an intermediate name.

**A volume nobody stands in is renamed out of the way.** Exactly one rung goes
unclaimed — the one the attackers enter on — and the stock zone sitting there
would otherwise still answer to a name the cpsetup lists, and be enabled
alongside the zone that replaced it. It is renamed `<name>_unused`, which the
cpsetup never mentions, and is thereby inert. On the reversal the unclaimed rung
is the last objective's, whose zone the moving stage takes anyway; on an interior
start it is a real orphan, and `enter2_fwd` on ministry parks `spawnzone_2` for
both teams.

Two consequences worth stating plainly. A stage inherits the *point count* of
the ground it is given rather than its own — ministry stage 1's defenders go
from 45 points to rung 5's 68 — which is what that room supports. And a
restricted area guarding that ground stays where it is, still guarding it, which
is more nearly right than following a stage around the map: only a zone that
really slides now takes its block zone with it.

## 4. Where there is no zone to hand over, points are re-sited onto authored twins

A cluster of spawn points was authored to fit the room it is in, so sliding
stage 1's seventeen attacker points onto rung 5 puts most of them in walls.
That is the four-server-restart lesson in §6 of
`docs/spawns-and-objectives.md`, and here it is avoidable: the destination rung
already holds points the engine validated, so an incoming point takes one of
**those** coordinates.

The mover does not have to use its own volume, and must not use a borrowed one:
two rules on one entity would be one edit lost in silence. It takes its own
where that is still free — which is the reversal's case at both ends — and
otherwise the unclaimed one, renamed on the way so the cpsetup still finds a
zone under the name it lists.

They are matched role to role, not name to name, and that distinction is the
whole correctness of it. Zones share a targetname across teams — `spawnzone_5`
is a team-2 volume at rung 4 *and* a team-3 volume at rung 5 — so asking for
"the points in spawnzone_5" fetches two rungs at once and scatters the incoming
cluster down the corridor. Defenders on rung k inherit from the defender zone of
stage k; attackers on rung k from the attacker zone of stage k+1, because stock
stage k+1 is where the attackers respawn on objective k.

Within a cluster, both ends are ranked by distance from their own centre and
zipped, so a point that was deep in a room stays deep in one, and the same
survey always emits the same preset.

**Where the destination held a smaller cluster than the one arriving** — the
last stage's 57-point defence moves onto a 17-point attacker entry — the rest
are filled from nav area centres inside the moved volume that pass the survey's
hull probe. That probe is `TR_TraceHull` with the player hull, run in game: the
same test as `CINSRules::IsSpawnPointValid`, and the one offline predictor §4 of
the other document did not have to throw out. A filled coordinate is not a
guess; it is just not a precedent.

Fills are spaced 96 u apart, and that spacing **yields rather than starves**.
Holding the line when the pool has run short does not spread the spawns out, it
stacks them: the assignment cycles a short list and two points land on the same
coordinate, a separation of zero. So a second pass runs at 64 u and a third at
48 u, still inside what the shipped maps authored — their own nearest neighbour
within a zone is 40 u at its closest and 58 u at p5. Before that, ministry_coop's
last attacker cluster took 11 coordinates for 16 points and put 5 pairs on top of
each other.

### Where a zone lands is searched, not computed

Spawn points bind to a zone by **containment and nothing else**, so a volume
that misses its own points arrives empty and those points belong to no stage.
Two things went wrong here before it worked, and both are measured:

- **Frames.** A brush's `origin` is its own centre, which is not where the
  points inside it average out. Translating the box by a delta taken between
  brush centres and then filling it from coordinates taken between point centres
  lands the box off its own contents: ministry_coop stage 6 bound 0 of 16.
- **Several volumes, far apart.** `spawnzone_6` team 2 is two boxes with the
  cluster centre in the gap between them, so aligning centres straddles the
  destination and both boxes arrive empty.

So the move is searched: put each volume on each authored coordinate in turn,
alongside the centre-to-centre move, and keep the placement giving **the most
places to stand nearest to where the destination's own cluster was**.
Maximising *authored* coverage instead picks a far lobe over a near one whenever
the far one holds one more coordinate, and that put ministry_coop stage 1's
attackers 3,996 u from the objective they enter on against a shipped ~1,900 u
approach. Counting hull-validated fills alongside authored coordinates makes the
near placement win.

Every emitted point is then checked back against the moved volume, and a stage
whose points fall outside it is refused rather than shipped.

## 5. Spawn zones overlap, so a point can be claimed twice

A point inside two stages' zones serves both in stock play — the engine
re-evaluates containment every time a zone is enabled. §6 of
`docs/spawns-and-objectives.md` hit this from the file side: *"the moved volume
also encloses points that used to serve other stages, so the stage-1 pool is
larger than the 56 that moved."*

A reversal cannot leave the ambiguity in place, because it sends the two stages
to different rungs. A contested point goes to the stage whose volume it sits
**deepest** inside — a point 400 u into one zone and 8 u into another is that
first zone's point. Measured: ministry_coop 2 of 442, market_coop 126 of 636.

Two points can also share an origin, and a rule identifies one by the origin it
has *before* the edit, so coincident points are one rule and one destination
whatever is done about it. They are collapsed deliberately and counted rather
than emitting a second rule that can never match; they were coincident on the
stock map too.

**Overlap has a side, and permuting the ground flips it.** A zone holding
another stage's points is not by itself wrong: at stage j the attackers hold
every rung the plan has already sent them through, so a defender who spawns
behind that front appears in ground the round has cleared, and an attacker who
spawns ahead of it appears past the objective — while sharing the other way is
what the shipped maps do on purpose. Stock, an overlap between consecutive zones
puts a defender one rung *ahead*, which is harmless. Reversed, the same physical
overlap is one rung *behind*, and the players come out where the round has
already been.

So the count is reported per stage and totalled, against the shipped map's own
contested count. Nothing new is created by a rename — ministry_coop's 2 are the
2 the shipped map already had — but a map whose zones overlap heavily inherits
the re-signed version of that: `tell_coop` shares 238 of its 661 points between
zones as shipped, and 171 of them come back on the wrong side of a reversed
front. That is a fact about the map, and it is reported rather than papered over.

Where a zone does slide, its placement is re-searched once against everything
else's final position, and kept only where the count actually drops. A zone that
is not moving is never re-placed: the sharing around it is the shipped map's own.

## 6. What is measured and what is only reported

**Capture volumes translate but do not resize.** Reversal pairs slot 1 with slot
N, and on both maps that pairs a 124×77×34 cache with a capture volume up to
1154×1026×450, so the volume clips into whatever is in the room it arrives in.
The one thing that can be checked offline is whether it still covers walkable
floor — an empty one is `Failed finding CP area for N!` in the server log — and
`coverage` is that count per objective. The tightest on the two targets is
ministry's `cp6 -> cachepoint_a` at 13 walkable areas. `siege_coop` fails it
outright and is refused.

That check reads rung 0's position, and until the zone centroid was taken from
the volumes rather than the `origin` key it was reading the world origin on the
three maps that leave the key unset — so it accused `embassy_coop`'s `cp7` and
cleared `siege_coop`'s `cp_9`, and both were verdicts about coordinate `0 0 0`.
Corrected, the accusation moves to `siege_coop`. §7 lists both maps' surviving
reasons, and neither map's verdict was ever in doubt: each also fails on usable
coordinates.

**A marker is placed, not translated — because it is what the gamemode looks
the objective up from.** `coverage` above is a fact about the capture volume,
and it is not the whole question. ministry_coop's `cp3` covers 53 walkable areas
on the stock map and the server still logs `Failed finding CP area for 2!` for
it, because its marker hangs in a stairwell 169 u from the nearest walkable
centre. The volume is where the fight is; the **marker** is where the gamemode
asks the nav mesh what area this objective is in, and a marker floats — 137 u
above the floor for ministry's `cp4`, 433 u for market's `cp_f`. Translate that
float to the other end of the corridor and it is over a roof, a stairwell or
nothing.

Four of the five maps translated a marker past that line before this was found,
and the engine is what said so: `ministry_coop` reversed logged `Failed finding
CP area` for one objective more than stock did, and the offline
`marker_gap` — the distance from the moved marker to the nearest walkable area
centre — ranked all six of ministry's objectives exactly as the server log did:

| | accepted | refused |
|---|---|---|
| stock | `cp4` 138 u, `cp6` 135 u | `cp3` 169 u |
| reversed, translated marker | `cp6` 118 u | `cp3` 169 u, `cp4` 269 u |

So the line is somewhere in (138, 169], `MARKER_GAP` is 150, and a marker whose
translation would cross it is **not translated**: it is stood on the walkable
area nearest its landing point *inside the objective's own volume*, 72 u up —
the map's own convention for a cache marker. Every moved marker on all five maps
is then 63–119 u from the mesh, and a marker that stays on its own rung is left
alone: ministry's `cp3` is the stock map's own fault and reversal does not
inherit the blame for it.

**`MARKER_GAP` is no longer a gate, and rotating inferno_new_v1's family is
why.** That put 70 markers in front of the engine at once: every one the
generator *re-sited* was accepted, 28 for 28, and all 10 refusals were markers
it had *translated* because the gap came in under 150. No threshold on that
quantity can separate them, because the gap is a distance to the nearest area
**centre** and so adds a horizontal error to a vertical one — the engine accepts
a marker 232 u above its floor, and inferno ships two markers with no walkable
footprint under them at all, which it also accepts, while refusing one 32 u out
past an edge. Accepted runs to 11.2 u off the mesh and refused starts at 7.5;
the distributions overlap on every quantity measured, solidity included.

So the test is gone and the answer is unconditional: **a marker that moves is
stood on floor the objective covers.** Re-siting can never be worse than leaving
it where the translation dropped it, and there was nothing to gain by asking
first. What remains of `MARKER_GAP` is reporting — the number the warnings
quote, and the flag on a marker a layout leaves standing where the stock map put
it, which is the map's own problem and not the layout's. inferno's family went
from 10 refusals to none; all five maps below re-run unchanged.
`docs/permute.md` §6 has the measurements.

**Restricted areas end up guarding the wrong side.** An `ins_blockzone` holds
the attackers behind the current objective, and behind is now in front. A block
zone now stays where the map put it, which is right when the zone it guards also
stayed: the stage fought on that ground is the one standing in that volume. Only
a zone that really slides takes its block zone along, paired by the defender
cluster it physically sits on. Either way a reversed layout wants them off:
`tools/blockzones.py --disable`, or a server that does not use them.

**What the server is still the only witness to.** The offline gate says the
points are bound, the coordinates are ones the engine accepted, the volumes
cover floor and the markers are on it. `tools/check-layout.sh` now asks the
engine the rest — it loads the map once stock, changelevels it once per preset,
and reads back the applier's per-rule verdict, the map-wide hull count and the
gamemode's own `Failed finding CP area` lines, each against that first stock
load rather than against zero. What it cannot reach is the round: a cache
marker takes its move from the round-start burst and `mp_restartgame` on an
empty checkpoint server segfaults it, so a player is still what says the markers
followed their caches.

## 7. The corpus

`navlayout reverse` gates on the map reading `linear` — the operation a corridor
licenses, per `docs/places.md` — and `--force` runs it anywhere. Of the 13
shipped `_coop` maps, all of which have a cpsetup, 12 emit and 1 is refused:

| map | verdict |
|---|---|
| **market_coop**, **ministry_coop** | emit — the two `linear` maps, and the two `openness.score` puts last |
| buhriz, contact, district, drycanal, embassy, heights, revolt, sinjar, tell, verticality | emit under `--force` |
| siege_coop | refused: 1 usable coordinate on rung 9 for 18 points, and `cp_9`'s volume covers no walkable area on rung 0 |

**Handing over the zone rather than moving it is what changed that.** All three
earlier refusals were the same fact about the same ground — the rung a reversal
asks a full attacker cluster to enter on is one the stock map only ever used as
an objective, and it holds too little standable floor to fill a spawn zone with.
Eleven of the twelve clusters no longer ask: they stand in the zone that was
already there. `embassy_coop` and `revolt_coop` emit for that reason.
`siege_coop` still refuses, because the cluster that has to move is the one with
nowhere to go, and because `cp_9`'s volume covers no walkable floor on rung 0 —
a second, independent reason.

**And the identity layout is now exact on all 13.** `stock_plan` asks every
stage to stand where it already stands, so under the hand-over every cluster is
"in place" and the whole layout emits *no spawn edits at all*. Before, seven of
the thirteen round-tripped exactly and six carried a residual — market_coop 38
points, tell_coop 28, verticality_coop 21, revolt_coop 20 — all of it the
placement search re-deriving a position the map already had. There is nothing
left to re-derive.

**The three workshop maps that read linear or segmented all reverse.** A
workshop map ships its cpsetup beside the `.bsp`, and `tools/workshop-maps.sh`
copies both into the game tree, so the chain a reversal permutes is there to
read:

| map | reads | verdict |
|---|---|---|
| congress_coop | linear, 9 objectives | emit — after the marker fix below |
| dead_air | linear, 12 objectives | emit |
| gizab_aof | segmented, 8 objectives | emit, with 311 spawn points offered by a zone on the wrong side of their own front, against 226 the shipped map already offers |

congress_coop needed one thing that was not about layouts at all: its cpsetup
creates a `point_controlpoint` for each cache beside the one the mapper baked
into the BSP, so the running map holds **two entities called `cachepoint_g`** —
and the shipped `.txt` has the sign of the y wrong on one of them, leaving the
decoy 1,696 u from the cache it claims to mark. Resolving the chain to whichever
entity the enumeration reached last anchored objective 7 on that decoy, put its
volume on ground with no floor under it, and refused the reversal for a fault
the map has and the layout does not. A chain name is now resolved to the entity
that actually marks something — a cache first, then a capture volume, nearest
first — and the applier is told the coordinate to expect (§8). Nine of the 46
surveyed maps carry a duplicate objective name; `cs_officeb3_coop_v1_5` is
another, and there the two sit on the same spot.

## 8. What the applier needed

Four changes in `plugin/scripting/mapmaker_layout.sp`:

- **A rule can rename an entity.** `"rename" "spawnzone_1"` updates the
  `targetname` — appending the key where a nameless entity somehow lacks it —
  and a rule may carry a rename, a move, or both: a mover that borrowed another
  zone's volume does both in one rule. A rename-only rule has no geometry, which
  is why the loader no longer drops a rule with neither `origin` nor `offset`.
  Correctness rests on the walk's existing shape: one edit per entry, first
  matching rule wins, so a permutation of names resolves in a single pass.

- **A cache's control point is named in the preset,** not derived. The applier
  looked it up as `<name>_cp`; ministry_coop's `cache_a` is marked by
  `cachepoint_a`, and the guess silently left every marker behind its cache.
- **...and a name is not enough to identify it.** `FindEntityByTargetname`
  returned the first entity of that name, and on nine of the 46 surveyed maps
  an objective name answers twice. The preset now carries `from` and `cp_from`
  — where the cache and its marker stand on the stock map — and the applier
  takes the candidate nearest to *either* that coordinate or the one this
  preset moves it to, because a later pass over the same round finds the real
  one already moved and the decoy still standing where it always was. A name
  that answers more than once is logged whichever way it resolves.
- **One pass over the entity lump, not one per rule.** A reversal moves every
  spawn point individually, which is 442 rules on ministry_coop and 633 on
  market_coop; a pass each meant ~1.4M `EntityLumpEntry` handles in `OnMapInit`
  to make 633 edits. The rules are now read into memory first and every entry
  tested against all of them in one walk.

Because a spawn-point rule carries an absolute `origin` rather than an `offset`,
it is idempotent: two rules matching one entry cannot compound the way two
offsets would. The emitted presets were checked for that anyway — 0 ambiguous
matches on both maps, and no entity is now double-ruled on any of the 14 layouts
ministry_coop admits, which is the check that caught a mover trying to drag a
volume a borrower was already standing in.
