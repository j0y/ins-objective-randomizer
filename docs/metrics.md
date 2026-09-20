# Metrics

Every number here is computed from a *compiled* `.bsp`, so it can be measured on
the official maps and on any other map with the same code. That symmetry is the
whole point: the official maps supply the target distributions, which turns "is
this plausible" into distribution matching rather than invented thresholds.

Measured values for the official corpus are in [corpus.md](corpus.md), rebuilt by
`mise run metrics`. Everything below is implemented.

**Scope note.** These metrics were written to hold a *generated* map against the
corpus. Since the pivot (PLAN.md), their job is to calibrate **objective and
spawn placement** on maps that already exist — the same distributions, asked a
narrower question. Two consequences:

- The 13 `_coop` maps are the right scope, and `SCOPE=coop` is the default.
  Objective siting and spawn geography are what a game mode changes.
- The topology metrics the layout work actually runs on — route count, detour
  ratio, chokepoint min-cut, path distance — are **not here**. They are computed
  on the engine's nav graph from a survey, not on a `.bsp`, because the offline
  `.nav` connection graph cannot be trusted. See `docs/layout-variants.md` §3
  and PLAN.md step B2. What this file measures is the geometric context those
  sit in.

## Needs no rays

| metric | source | reads as |
|---|---|---|
| walkable area, storey mix | bspscout nav graph | scale, verticality |
| sealed regions | flood fill vs standable surface | missing doors, blockout gaps |
| objective count and spacing | `point_controlpoint` clustered per place, graph distance | pacing |
| capture zone size | `trigger_capture_zone` brush model bounds | how big a point is to hold |
| entrances per interior | graph crossings from outdoor to indoor | defensibility |
| ways into an objective | walkable groups on a ring at the zone boundary, with how open that ring is | approach structure |
| PVS cost | `VISIBILITY` lump: mean visible clusters per cluster (`bspscout/vis.py`) | render cost proxy |
| lightmap brightness | `LIGHTING_HDR` sampled on standable-facing faces (`bspscout/light.py`) | black corners, blown-out walls |

Two traps in that last pair. Clusters are not leaves - solid and detail leaves
carry cluster -1 and are not in the PVS at all. And Insurgency ships HDR-only
maps, where `LIGHTING` is present but empty, the luxels are in `LIGHTING_HDR`,
and the face array whose `lightofs` index them is `FACES_HDR`, not `FACES`;
using `FACES` does not fail, it returns luminances around 2^120.

There is no `ins_objective` in any shipped map, which is what `bspscout
objectives` still emits as its entity stub. A capture point is a
`point_controlpoint` plus a `trigger_capture_zone` brush naming it, one pair per
game mode, so a place with four modes on it is four control points at the same
spot - hence the clustering.

## Needs a segment trace - `bspscout/trace.py`, `bspscout/sight.py`

| metric | how | reads as |
|---|---|---|
| sightline length | 16 horizontal rays at eye height per sampled position, first hit | the accidental cross-map snipe lane |
| longest lane per position | the max of that fan | where the long shots are, rather than how many |
| exposure | sampled positions within 2048 u with a mutual sightline, as a fraction of those in range | risk structure; open plazas vs pockets |
| crouch cover | of the sightlines open standing, how many drop to crouch height breaks | whether there is anything to fight from |
| approach under watch | fraction of the ring just outside a capture zone seen from inside it | can you get to the point at all |
| defending spots watching an approach | median share of inside positions with a shot at a given way in | is the point a grinder or a walk-in |
| spawn safety | line of sight from each `ins_spawnzone` to each capture point | being shot before you have moved |

Sampling is a seeded uniform subsample of the reachable walk graph, so the
statistics are area-weighted and a re-measurement reproduces. Ray pairs are
pre-filtered through the PVS, though less usefully than expected: at 2048 u
engagement range, points are usually close enough that their clusters do see
each other, and the filter only removes about a quarter of the pairs on
ministry. It would earn much more on map-wide pairs.

### Three things the trace has to get right

**Detail brushes are not in the tree.** vbsp filters `func_detail` into the
leaves it touches and records it in LEAFBRUSHES, leaving the leaf itself
CONTENTS_EMPTY. Detail is 58-70% of brushes on the maps measured, and a
tree-contents test finds solid at 0-1% of their centres, so a trace that reads
leaf contents passes through most of the map's walls. The trace clips against
leaf brushes instead, which is what the engine does.

This applies to walkability too, and it did: `clearance_filter` asked
`Bsp.is_solid`, so a quarter of ministry's supposedly reachable nodes sat inside
a solid detail brush and another fifth inside a clip brush. Fixing it cut the
map's reachable area from 103M to 50M u². Every number in corpus.md moved.

**Some brushes carry CONTENTS_SOLID and are not solid.** `func_dustmotes` (on
all 49 maps), `ins_spawnzone`, `ins_blockzone`, `trigger_*` and
`func_detail_blocker` are volumes the entity turns non-solid on spawn. Their
faces are tool-textured, which is how `Bsp.triangles` filtered them without
anyone noticing; a brush trace never sees a texture flag. A spawn zone spans a
whole spawn area, so leaving these in walls off sight across the middle of the
map.

**Displacements are not brush volumes.** The displaced mesh can sit a long way
off the original brush plane - p95 vertex offset is 70 u on a median map and
1544 u on sinjar - so clipping terrain at its plane is not an approximation, it
is a different map. Displacement brushes are dropped from the brush set and the
mesh is traced as triangles over a uniform xy grid. `brushsides["dispinfo"]`
cannot identify them (vbsp writes it zero everywhere); the test is geometric.

Still missing: static props do not collide, because they are not in the BSP and
bspscout does not read `.mdl` yet (PLAN.md step 3). Prop fences, containers and
doors are transparent to every ray here, so exposure is overstated wherever a
map fights with props rather than brushes.

## Reading the results

Report as distributions, not thresholds. "Official maps: median sightline
1400u, p95 2900u; this map p95 4600u" is actionable. A hand-picked ceiling is
not, and optimising against one produces a warren of closets — the metrics are
proxies, and they Goodhart if pushed.
