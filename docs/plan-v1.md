# Plan (v1 — superseded twice, kept for the record)

**Historical.** This is the first plan: a street-grid generator calibrated
against all 49 shipped maps, with a headless sim last. It was superseded on
2026-08-27 by a box-world ordering, and that in turn by the objective-relocation
spine on 2026-08-30. `PLAN.md` is the live one; both later revisions are
described in its header.

Kept because the reasoning is still legible and several of its unknowns were
resolved by later steps rather than abandoned — most importantly that the
checkpoint objective chain lives in `maps/<name>.txt` and is authored data
rather than geometry, which is the finding the whole current spine rests on.

---

Ordered so that each step is verifiable before the next depends on it. The
recurring failure mode in this kind of project is building a generator before
the toolchain is proven, then debugging both at once.

## 0. Prove the toolchain before writing a generator

Insurgency client + SDK on real disk (not the ramdisk — the install is well
over the 8 GB there; `~/Work` has 1.3 TB free). The **dedicated server package
ships maps and assets but likely not `vbsp`/`vvis`/`vrad`** — those come with
the client SDK. Verify which before committing to a download.

Then: hand-write the most trivial legal `.vmf` possible — one sealed room, one
light, one `info_player_start` — and get it through `tools/compile-map.sh` to a
`.bsp` that loads. Until that works nothing downstream can be debugged.

**Done when** a hand-made VMF compiles and `mise run scout` measures the result.

**DONE 2026-08-27**, and more cheaply than this step assumed. The compile tools
are ficool2's VBSP++/VVIS++/VRAD++ - free, no Steam account, monolithic, and
carrying their own compatibility DLLs, with an explicit `-insurgency` BSP
format flag. The game content came from the dedicated server anonymously. So
no Steam login is needed anywhere in this project. `maps/build/test_room.vmf`
is the hand-made map; `tools/compile-map.sh` is one command end to end.

## 1. Calibration corpus

`mise run maps`, then run bspscout over every official map. This needs no Wine
and no generator, and it is the highest-value early step: it produces the target
distributions everything later is measured against.

Collect per map: sightline length percentiles, exposure (visibility degree)
distribution, cover fraction at stand vs crouch height, objective count and
spacing, entrances per objective, storey mix, walkable area, lightmap brightness
histogram, PVS cost (mean visible leaves per leaf).

**Done when** `metrics/` can print "official maps sit at X ± Y" for each.

**DONE 2026-08-27.** `mise run corpus` scouts all 49 shipped maps, `mise run
metrics` measures them, and `docs/corpus.md` is the table. **Numbers below are
the step-2 revision** - the walkable area they rest on was wrong until the solid
test became brush-aware, and every one of them moved.

`docs/corpus.md` is now scoped to the **13 `_coop` maps**, because those are the
checkpoint layouts: the same geometry as their base maps with a different chain
of objectives, different spawn zones, different block zones. Objective siting and
spawn geography are exactly what a mode changes, so for anything aimed at
checkpoint the base maps are the wrong baseline. `SCOPE=base mise run metrics`
still builds the other table.

Numbers worth carrying in your head, across the 13: 6 capture points per map
(p5-p95 4-8), 3.0k u nearest-neighbour walk between them (1.7k-5.0k), 19% of
walkable area under cover, 1.67x walkable area per unit of footprint, and an
average PVS cluster seeing 20% of the map.

Four things this step turned up:

- Both passes are bounded, deliberately. `tools/scout-corpus.sh` sizes its job
  count from free RAM and puts each map in its own cgroup with swap denied.
  Launching all 49 at once asks for ~70 GB on a 60 GB box, and the OOM killer
  takes the desktop with it - which is how this step failed the first time.
- **No shipped map contains an `ins_objective`.** A capture point is a
  `point_controlpoint` plus a `trigger_capture_zone` brush naming it, one pair
  per game mode, so a place four modes use is four control points in one spot -
  counting entities counts modes, not places. They are clustered by proximity
  before anything is measured. `bspscout objectives` still emits `ins_objective`
  stubs, which step 6 will have to replace with the real pair.
- `cover_image` tested each cell's roof against the *global* ground datum, so a
  map whose datum sits above most of its drawn geometry - station at z=876,
  verticality at 1084 - read as entirely open air, interiors and all. It tests
  against the floor in that cell now, which recovered 4 maps.
- Sightlines, exposure and cover were not here: they needed step 2's rays, and
  are in now. The rest of the list above was in from the start, plus
  capture-zone size and objective ring openness.

## 2. Rays in bspscout

Segment-vs-BSP-tree trace, alongside the existing `point_contents` descent
(`bsp.py:279`) which already does the vectorised walk. Pre-filter ray pairs
through the `VISIBILITY` lump PVS — 2.95 MB / ~13.4k leaves on the Pripyat map,
so leaf-pair rejection is cheap and kills most of the work.

Then the derived measures: sightlines, exposure heatmap, cover rings,
spawn-to-objective visibility, attacker/defender asymmetry per objective.

Also worth having, and needing no rays at all:
- **Lightmap sampling.** `LIGHTING` is 27 MB on the Pripyat map. Sample it on
  walkable surfaces to find black corners and blown-out walls.
- **PVS cost.** Mean visible leaves per leaf as a render-cost proxy, so
  optimisation becomes a measured number rather than a judgment call.

**DONE 2026-08-27.** The last two were already in from step 1 (`light.py`,
`vis.py`). `trace.py` is the tracer and `sight.py` the measures; `mise run sight`
draws them for one map, `mise run check` verifies the tracer, and the new fields
are in `docs/corpus.md`. Throughput is 90-430k rays/s, so a map's full set of
sight measures costs about 15 seconds.

The heading of this step is wrong, and that is the whole story of it. A
segment-vs-BSP-*tree* trace does not work:

- **vbsp does not split the tree with `func_detail`.** Detail brushes are filed
  into the leaves they touch and listed in LEAFBRUSHES, while the leaf itself
  stays CONTENTS_EMPTY. Detail is 58-70% of brushes on the maps measured, and a
  tree-contents test finds solid at 0-1% of their centres. So the trace descends
  the tree only to find candidate leaves and then clips against each leaf's
  brushes, which is what the engine does.
- **Some brushes carry CONTENTS_SOLID and are not solid.** `func_dustmotes` (all
  49 maps), `ins_spawnzone`, `ins_blockzone`, `trigger_*`, `func_detail_blocker`
  - volumes the entity turns non-solid on spawn. Their faces are tool-textured,
  which is how `Bsp.triangles` filtered them without anyone noticing; a brush
  trace never sees a texture flag. A spawn zone spans a whole spawn area, so
  leaving these in walls off sight across the middle of the map.
- **Displacements are not brush volumes.** The mesh sits up to 1544 u off the
  original plane (sinjar; 70 u on a median map), so clipping terrain at its
  plane is a different map, not an approximation. They are traced as triangles
  over a uniform xy grid, and `brushsides["dispinfo"]` cannot identify which
  brushes to drop - vbsp writes it zero everywhere - so the test is geometric.

Each of those three paths is checked against brute force in `tools/check-trace.py`
and agrees exactly, to 0.000000 u, on five maps. That mattered more than the
speed: an acceleration structure that misses one wall in fifty produces exposure
numbers that look entirely plausible.

Four things this step changed outside its own scope:

- **The walkable graph was wrong, and had been since step 1.** `clearance_filter`
  asked `Bsp.is_solid`, so it could not see detail brushes either: a quarter of
  ministry's reachable nodes sat inside solid detail geometry, a median 68 u
  below the containing brush's top, and another fifth inside clip brushes. Its
  walkable area was 103M u² and is 50M; stacking was 1.63x and is 1.41x. The
  corpus has been re-scouted and every distribution in `docs/corpus.md` moved.
  Outdoor maps moved least (sinjar 898k → 739k nodes), detail-heavy interiors
  most - which is the shape you would expect if this is the real cause.
- **The PVS pre-filter is worth much less than this step assumed.** At 2048 u
  engagement range two positions are usually close enough that their clusters do
  see each other, and the filter removes only about a quarter of the pairs on
  ministry. It would earn its keep on map-wide pairs; it does not here, and the
  brush clip is fast enough that it did not need to.
- **`axis()` picked insertion points you cannot walk out of.** `reach` is a flood
  fill over a *directed* graph, so it is a weak component and includes ledges you
  can drop onto and not climb back off. Taking the geometric extreme of that set
  put the insertion point on one of them on 4 of the 13 checkpoint maps, and the
  reported end-to-end walk was 103 u on contact_coop with 0% of the map routable
  from the start. Endpoints now come from the largest *strongly* connected
  component: contact_coop reads 11,675 u and 74%, and the maps that were already
  healthy do not move at all.
- **The walk graph was flood-filled from the largest slab of floor, not from the
  spawns.** `cmd_build` never called `graph.spawn_nodes`, and printed "no usable
  spawn entities" without having looked for any. On a sealed test box the largest
  standable surface is the *outside of the roof*, so `test_room` was measured
  entirely on its own roof - every sightline ran to the cap because there is
  nothing up there to hit. It seeds from spawn entities now. The shipped maps are
  unaffected and that was checked rather than assumed: the largest component
  holds 94-100% of placed spawns on all 13 checkpoint maps, and ministry_coop
  rebuilds to a bit-identical `reach`. Only `Cache.dist` changed, which nothing
  reads.

One measure was scoped down rather than delivered as written. "Attacker/defender
asymmetry" cannot come from ray tests between eye points, because line of sight
between two points is symmetric - there is no asymmetry in it to find. What is
measurable is how much of the approach is under watch, from how many defending
positions at once, and how much of that a crouch takes away; a real asymmetry
needs a body volume rather than an eye point, and would be honest work for step
7 rather than a metric here.

## 3. Asset catalogue

VPK reader, `.mdl` header parse for bounding box and `$staticprop`, `.phy` for
collision hulls. Index by name so a generator can request a kind of building
and get models that exist.

This also fixes bspscout's worst limitation — static props do not collide there,
because they are not in the BSP tree and it does not read `.mdl`. With hulls
available, prop fences and containers block sight and movement as they do in
game.

## 4. Reference reading

Decompile two or three official maps with BSPSource (Java, already installed,
no Wine). Not to reuse — to learn the kit's conventions: room proportions, prop
density, `func_detail` choices, how spawn zones and objectives are laid out.

## 5. VMF writer

Solids and sides with three-point planes, entities, then displacements. Keep
watertightness a property of the writer, not something checked afterwards: a
one-unit gap between brushes is a leak and `vvis` will refuse.

**Done when** a VMF produced by code compiles as cleanly as the hand-made one.

**DONE 2026-08-27** for brushes and entities. Displacements are not written -
see step 6's note on brush terrain first. `packages/mapmaker/src/mapmaker/vmf/`
splits as `kv` (the text), `brush` (convex solids, outward plane windings),
`shell` (watertight assemblies), `doc` (the file), `compare` (geometry diff).

`mapmaker room` is the code-written twin of the hand-made `test_room.vmf`: same
brushes, same texture axes, and a `.bsp` matching it to within the length of the
map name. Compared with `mapmaker check`, which reduces each solid to its set of
oriented planes - text diffing cannot do this job, because a side may name any
three points on its plane and any vertex first. `duplex` and `pillar` cover what
step 6 needs next: a doorway tiled through a wall, and a brush at an angle.
`MAP=duplex mise run loop` writes, compiles and measures in one command.

Two things worth carrying forward:

- Watertightness is partitioning, never subtraction. `slab_with_opening` proves
  its own tiling: axis-aligned box volumes are exact at map scale, so
  `parts + hole == slab` rules out both a gap and an overlap.
- The leak that actually happened was not a gap. It was a light inside the
  pillar - vbsp reports that as a leak, and its message points at the geometry
  rather than the buried entity. `Vmf.problems()` now tests every entity origin
  against each brush's half-spaces and refuses to write the file. It also
  refuses off-grid coordinates, which caught an off-grid pillar polygon before
  the compiler saw it.

## 6. Generator

Street grid → block subdivision → buildings from a room-and-corridor spec →
door and window cuts → props → gameplay entities. Then close the loop against
step 1's distributions.

## 7. Headless bot simulation

Numbered here, but buildable as soon as 2 and 3 are in: it needs rays and prop
hulls, not a generator. The point is to answer "is this map any fun" before a
human spends a session finding out, and coop checkpoint is the mode to answer it
for - one linear chain of objectives, AI on one side, so the whole game state is
a stage index.

Most of the input is already on disk. Two things this step would otherwise have
to build ship with the game.

**The nav meshes ship.** 30 `.nav` files in `game/insurgency/maps/`, 8 of the 10
coop variants among them (`contact_coop` and `verticality_coop` are the gaps).
They parse: v16, `analyzed=1`, 3128 areas on ministry_coop, 10501 on
buhriz_coop. `analyzed` is the valuable part - such a mesh carries hiding spots
flagged ideal-cover vs exposed, encounter spots along each area transition, and
approach data. That is Source's own precomputed answer to "where does a bot take
cover" and "where do you get shot on the way in", for every shipped coop map,
free.

Trust them with a whitelist. The BSP size in the header mismatches on nearly
every one, but usually by 1-3%, which is a lighting recompile and not a layout
change - the game ships them like that and loads them anyway. `drycanal_coop`
claims an 18.8 MB BSP against the shipped 68.9 MB, and `market` and
`revolt_coop` are off by ~10%. Those three are from another map version;
distrust them.

**The checkpoint state machine is in the entity lump.** Per shipped coop map:

- `point_controlpoint` named `cp1`..`cp6` - the objective order is in the
  targetname. Plus `cachepoint_b` on some maps, a destroy objective rather than
  a capture.
- `trigger_capture_zone` names its point (`cz3` -> `controlpoint: cp3`) and
  carries the capture volume as a brush model.
- `ins_spawnzone`, 19-22 per map, `TeamNum` 2 or 3, `targetname: spawnzone_5`,
  `StartDisabled: 1` - per-stage spawn geography for both teams, as brush
  volumes.
- `ins_blockzone`, `TeamNum 3`, `bz1`..`bz6` - the volumes that pen the
  defenders to their stage.
- 442 `ins_spawnpoint` on ministry_coop, each with team and facing, associated
  to a zone spatially: point-in-brush-model, and `Bsp.models` has all 287.

So stage *i* determines which zones open, where defenders may not go, and which
volume the attackers have to hold. Nothing here needs the game running.

### The sim

Not a reimplementation of Source's AI. An agent sim on the nav graph, run Monte
Carlo, order of 200 rounds per objective:

- 10 Hz tick. Agents are points on nav areas, moving at about 150 u/s.
- Attackers path toward the live capture zone, preferring cover-flagged nodes and
  avoiding nodes they have already been shot from.
- Defenders spawn from the stage's zones onto hiding spots with sight of the
  objective's approaches, confined by the blockzone. On capture, the next wave
  counterattacks toward the nearest attacker.
- Engagements resolve from a visibility test plus a hit probability in range and
  exposure time. Deliberately crude.

The crude combat model is the whole reason this works without a server. The sim
is not asked to predict difficulty in absolute terms. It is asked to rank a
generated map against the eight shipped coop maps put through the same sim -
distribution matching again, exactly as in step 1.

What comes out, in rough order of how much it tells you:

- **Death heatmap per objective.** One bright blob in a corridor is a grinder.
- **Route usage.** If one approach carries 90% of the traffic, the other two are
  decoration.
- **Time to first contact** from each spawn, per stage. A five-second walk and a
  two-minute empty one are both bugs.
- **Simultaneous sight count** - how many defenders see one attacker at once.
- **Min-cut** between consecutive objectives. A one-cell cut is a grenade tunnel.

**Done when** the eight shipped coop maps give an envelope for each of those, and
a generated map can be placed inside or outside it.

Two things to be honest about up front:

- Before step 3's prop hulls this overstates exposure badly, because every prop
  fence and shipping container reads as open air. Buildable before step 3, not
  trustworthy before it.
- The corpus gives "shipped", not "good". Landing inside the envelope means a map
  is *normal for Insurgency coop*, and some shipped coop layouts are disliked.
  Sharpening past that needs per-objective labels of one's own, which means
  playing them. The sim's job is the obvious failures, not the last 20%.

The `.nav` reader earns its place for a second reason: it is an independent check
on `nav.py`'s 16-unit grid graph. Where bspscout's walkable set disagrees with
Valve's mesh on a shipped map, bspscout is wrong.

## 8. Nav mesh for generated maps

Shipped maps already have one - see step 7. A *generated* map does not, and
`.nav` cannot honestly be written from outside: it comes from the running game
(`nav_generate`), so this needs an actual install driving it, plus hand-fixing of
problem areas. Bots in the real game do nothing useful without it.

Worth establishing early how stale a mesh may be before the game rejects it. The
shipped meshes disagree with their own BSPs by 1-3% and load regardless, which
suggests a nav generated for one compile survives the next - and if it does not,
every recompile in step 6's loop invalidates its nav.

## 9. Software renderer

Flat-shaded perspective views from sampled standpoints at eye height. `.vtf`
decode (DXT1/5) if textures turn out to matter for judging a space.

---

## Known unknowns

- ~~Whether the dedicated server package includes the compile tools.~~
  **Resolved: it does not.** Only `bin/crash_handler.exe`. It does carry
  `vvis_dll.dll` and `vrad_dll.dll`, but not the launcher `.exe`s and nothing
  of vbsp. See `docs/toolchain.md`.
- ~~Exact Steam app IDs.~~ **Resolved:** 237410 dedicated server (anonymous),
  222880 client; the compile tools are in its Windows depot **222882**, 61 MB.
- Whether Insurgency's Hammer branch has VMF quirks vs stock Source 2013.
  Partly answered: vbsp++ carries `-insurgency` and `-staticpropformat 10`, so
  the differences are known to *someone*. A trivial VMF compiles fine.
- Whether a `.bsp` built this way actually loads in Insurgency. Untested - it
  needs a running game, same as step 8.
- How badly displacement authoring hurts. Terrain may be better done as brush
  geometry initially.
- Whether a headless sim ranks maps the way real play does. Step 7 rests on it
  and nothing on disk can answer it: it needs sessions on maps the sim has
  already scored, including one it scores badly.
- Where Insurgency's `.nav` diverges from stock Source. The header reads v16
  sub-version 3 or 4, and sub-version is where a mod puts its own fields, so the
  area records past the extent may not be stock layout. Step 7 parses more of
  this file than the header, so this gets answered early or it bites.

## What this approach does not reach

Calibration converts a lot of apparent taste into measurement, but not all of
it. Art direction, mood, and whether a space feels like an abandoned Pripyat
library rather than grey boxes with library props in it — no metric covers
those. Nor does matching every measured statistic guarantee the result is any
good: the official maps are a sample of good maps, not a definition of one, and
the metrics only span what someone thought to measure. Expect to look at the
renders and say what is wrong.
