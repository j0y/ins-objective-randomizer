# Hand-authored layouts

The pivot: **stop scoring placements and start reading the map.** The survey, the
graph, the places, the gates, the emitter and the applier all stay. What goes is
the scoring model that was going to choose between candidate sites, and the sim
that was going to calibrate it.

The decision is made from an **eagle-eye view of the nav mesh** — a rendered
top-down plan, one image per storey, with the engine's own hull verdict marked on
every area — and written into a per-map file by hand. Composition is then a
lookup, not a search.

`docs/layout-variants.md` is the spine. `docs/reverse.md` and `docs/permute.md`
are the generated half, and they still stand: permuting ground the map already
authored needs no judgement, which is why it was taken first. This is the half
that needs judgement, and the argument is that judgement is cheaper to *look at*
than to model.

---

## 1. Why the generator stops here

Three measurements, in the order they arrived, each narrowing what a scorer
could honestly claim.

**The corpus gives a band, not a placement.** Across 106 stages of the 13
shipped `_coop` maps, path distance from a stage's attacker spawn to its
objective runs p5 2,177 u, median 4,061 u, p95 7,087 u (straight-line median
2,836 u). The band is real and worth gating on. It is also satisfied by hundreds
of sites on an open map, and it ranks none of them against each other.

**The one tactical rule worth adding has no precedent to match.** Measuring the
defender spawn's azimuth off the attacker's advance axis, the shipped maps come
back at median 8°, p75 15°, p95 32°, max 59°, with 1 stage of 106 past 45°. So a
90°-wide "not behind them, but able to flank" cone *contains the entire corpus*
and selects nothing within it, and the interesting version — deliberately 20-45°
off axis, which no shipped stage does — has nothing to be calibrated against.
Distribution matching, the strongest idea in this project, is silent here
precisely because the question is about departing from the distribution.

**The sim that would have supplied the missing judgement cannot be trusted to.**
It was designed once and parked (`PLAN.md` step 6), on a box world nobody is
building now. Four of its five outputs are static and already written or
arithmetic: the min-cut between consecutive objectives is
`openness.min_vertex_cut`, whether an approach is decoration is
`disjoint_routes` plus the detour ratio, time to first contact is path distance
÷ 150 u/s, and simultaneous sight count needs the visibility matrix
`mapmaker_survey.sp` already knows how to export. The fifth — the death heatmap,
the only output that ranks one layout above another — rests entirely on a combat
model deliberately described as crude, with no oracle to calibrate it against:
**nothing this project has produced has ever loaded on a server.** A generator
calibrated against a sim calibrated against nothing is two unverified models
agreeing with each other.

What replaces it is not a better model. It is looking at the plan of the map and
deciding, the way the shipped maps were made.

---

## 2. Sites, arrangements, sectors

Two nouns, and the combination rule is one sentence.

A **site** is a point on the floor where a fight can happen: a position, which
chain slot it can stand for, and a cache offset if it is a cache. A map's **site
library** is its shipped objectives — already validated by a decade of play —
plus however many new ones are authored.

An **arrangement** is a cluster of spawn positions at a site, tagged with the
direction it serves:

- `defend` — the cluster that holds this site against an attack arriving from a
  given direction.
- `stage` — the cluster the attackers who just took this site respawn in, when
  the next objective lies in a given direction.

A **sector** is a world-bearing arc, in degrees in the xy plane, of the ray
leaving the arrangement's own site. One definition for both roles, and it is what
makes an arrangement reusable across chains: at the site, a `defend` sector faces
the door they will come through and a `stage` sector faces the way they are
going.

**The combination rule.** For a chain `S1..SN` entered at `E`:

    stage k is fought at S_k
    attackers spawn at S_(k-1)   (at E when k = 1)
      using S_(k-1)'s `stage` arrangement whose sector contains bearing(S_(k-1) -> S_k)
    defenders hold S_k
      using S_k's `defend` arrangement whose sector contains bearing(S_k -> S_(k-1))

That is the whole generator: a lookup. The tactical decision was made while
looking at the map; composition only asks which decision applies to this leg.

Three details that are not free:

**The bearing is the arrival tangent, not the chord.** `path/straight` across the
corpus is median 1.34, p90 1.90 and **max 25.3** — on ministry two sites 175 u
apart are 4,432 u apart on foot. A chord can point at a wall, and an arrangement
picked by chord bearing can face away from the door the attackers actually use.
Take the direction of the shortest path's last leg as it enters the site's place,
and of its first leg as it leaves. `NavGraph.path` already returns it.

**Overlapping sectors are allowed; gaps are refused.** A sector is an arc
someone wrote, not a bin the tool chose. Where two arcs contain a bearing the
tighter one wins, deterministically. Where none does, that pairing of sites is
**refused** rather than served by the nearest arrangement — an approach nobody
authored for is an approach nobody looked at.

**A cluster is authored as an extent, not as 40 points.** A shipped attacker zone
holds 16-18 spawn points and a defender zone 40-57. An arrangement records an
anchor and a box, and `_usable` fills the rest from hull-validated area centres
inside it, spending authored coordinates first and separating fills by
`MIN_SEPARATION` (96 u). That function already exists and already does exactly
this. Authoring supplies the judgement; the fill supplies the count.

---

## 3. The authoring file

One KeyValues file per map, `authored/<map>.kv`. KeyValues because
`survey.parse_kv` already reads this dialect and every other hand-edited file in
this project — the presets, the cpsetups — is in it.

```
"authored"
{
    "map"     "buhriz_coop"
    "survey"  "surveys/buhriz_coop.json"

    "site"
    {
        "name"       "market-north"
        "slot"       "any"           // or a cp/cache name it must stand for
        "at"         "area 1423"     // an area index, or "x y z"
        "cp_offset"  "72"            // marker above a cache; shipped convention
        "note"       "loading bay, place 12, one door east and a ramp north"

        "arrangement"
        {
            "role"    "defend"
            "sector"  "-45 45"       // attackers arrive from the +x side
            "anchor"  "area 1455 90" // area index plus yaw
            "box"     "980 -560 88  1220 -300 200"
        }

        "arrangement"
        {
            "role"    "stage"
            "sector"  "135 225"      // and leave toward -x, for the next site
            "anchor"  "area 1390 270"
            "box"     "760 -600 88  980 -410 200"
        }
    }

    "site" { ... }
}
```

**Positions are written as area indices wherever possible, not as typed
coordinates.** An area index resolves to that area's own centre, which the survey
already carries with the engine's hull verdict attached, so a position cannot be
a transcription error and cannot land in a wall. Raw `x y z` stays legal for the
cases that want an offset from an area centre. `navlayout validate` writes the
resolved coordinate back into the file as a comment, so the file stays readable
against a re-survey — area indices are enumeration order and a map update
renumbers them, which is the one thing this notation is worse at than
coordinates.

Everything else about a preset — `survey_hash`, the edit rules, the cache
teleports, the block-zone list — is derived, and `reverse.py` already emits it.

---

## 4. The eagle-eye view

This is the piece that has to be built, and its quality decides whether the
pivot works. `bspscout/render.py` already has the parts that make a top-down
plan readable — `_grid_overlay` (1024 u major, 256 u minor, ticks labelled in
world coordinates), `_scalebar`, `_north`, `storey_of` — and they take an axis
and an extent, so they are reusable as they are. What is new is drawing the
**survey** rather than bspscout's voxel grid.

`navlayout plan <map>` writes one PNG per storey plus a companion table:

- **Area footprints** as rectangles. Nav areas are axis-aligned and the survey
  carries `nw`/`se`, so this is exact geometry, not a raster.
- **The hull verdict on every area.** `hull_ok` is `TR_TraceHull` with the player
  hull, run in game, which is the same test `CINSRules::IsSpawnPointValid`
  makes — the one offline predictor of spawn validity that did not have to be
  thrown out. Drawn as fill vs. hatch, it means *the picture already shows where
  a spawn point can legally go*. This is what makes authoring possible without
  standing in the map.
- **Place segmentation** as fill colour, with the place index printed at its
  centroid, hinges outlined, and the cut edges between places drawn as the doors
  they are. `places.py` produces all of it.
- **Everything the map already authored**: objectives and their capture volumes,
  spawn zone volumes per team, and every `ins_spawnpoint`. Precedent is worth
  more than anything derived, and it is also how the shipped conventions become
  visible — a defender cluster sitting 831 u off its objective looks like
  something once it is drawn.
- **`indoor` per area**, so street and interior are distinguishable.
- **One storey per image.** Overlapping floors hide each other, and
  `verticality_coop` is unreadable any other way.

**Coordinates come from the table, never from the picture.** The image decides
*which* place, *which* side, and *which* door; the numbers come from a companion
listing — place index → centroid, area count, hull-valid floor, degree,
neighbours, and the hull-valid area indices inside it with their centres. So an
arrangement is authored by naming areas from the table that the picture says are
the right ones. Nothing is measured off pixels.

`navlayout plan` needs no BSP. The substrate is the nav mesh, so it works on all
46 surveys, including the 33 workshop maps whose BSP is not on this machine —
which is a wider reach than the generator ever had.

---

## 5. What the existing tools do now

Nothing is thrown away; the pieces change job, not shape.

| piece | its job now |
|---|---|
| `mapmaker_survey.sp`, `survey.py` | unchanged — the nav graph, the hull verdict, the entity inventory that everything is drawn from and checked against |
| `bspscout/render.py` | its grid, scale bar, north arrow and storey banding, reused by `navlayout plan` |
| `places.py` | the segmentation the plan is coloured by, and the count of a room's real approaches — so arrangements are authored per door that exists, not per compass point |
| `objectives.Snapper` | resolve an authored position onto its area; refuse a site off the mesh |
| `graph.NavGraph` | path distance and reachability between sites, and the arrival tangent that picks the arrangement |
| `openness.disjoint_routes` | whether a flank is a flank: a route from the `stage` cluster to the corridor that is area-disjoint from it. An angle is not a flank; a second route is |
| `reverse.py` — `Move`, `ObjectiveMove`, `_usable`, `coverage`, `to_cfg_all` | the emitter, unchanged. Authored positions *replace* `place_zone`'s search outright: there is nothing to search for once the box is chosen |
| `mapmaker_layout.sp` | unchanged — absolute origins per entity, cache teleports, round-robin rotation, persisted index |
| `check.py` | still the kill switch, per authored chain: the entry must reach every site |

Three new commands:

- **`navlayout plan <map>`** — the storey plans and the companion table. §4.
- **`navlayout validate <map>`** — the authored file against its survey: every
  position on the mesh and hull-valid, every cluster fillable to a shipped-size
  count, every sector arc covering a door the place graph says exists, every
  site reachable from every other.
- **`navlayout compose <map>`** — enumerate chains over the site library, gate
  them, emit the family `.cfg` and the rich JSON.

`compose` gates, all of which exist as code or as a measured number:

1. every site resolves onto the mesh and all sites are mutually reachable —
   `verticality_coop` has 3 of 8 stage pairs at *infinite* path distance, so this
   is not a formality
2. consecutive advance inside the corpus band, 2,177-7,087 u of path; outside it
   warns, and past a multiple of the map's own stock worst it refuses, which is
   the `--max-advance` policy `permute` already has
3. an arrangement exists at both ends of every leg for that leg's bearing
4. every cluster fills to a shipped-size count — `MIN_POINTS` is 6, shipped
   clusters are 16-18 attackers and 40-57 defenders
5. the capture volume covers walkable floor where it lands (`coverage`, written)
6. block zones named for disabling — they guard a side, and re-siting moves which
   side that is
7. diversity: pairwise Jaccard overlap of traversed areas, keep the most
   different K. Two chains through the same 400 areas are one chain

---

## 6. The workflow, per map

Entirely offline until the play test.

1. **Survey** — done for 46 maps.
2. **`navlayout plan <map>`** — read the storey plans and the place table.
   Seconds to render.
3. **Author `authored/<map>.kv`** — sites first, then per site one arrangement
   per door the place graph says exists. The shipped objectives go in as sites
   for free; new ones are the point of the exercise. `tools/author-sites.py
   <map>` writes a first draft to edit against the plan: it gates candidate
   sites on the envelope that map's own objectives sit in, spreads them by path
   distance, and fills every cluster to that map's own shipped size. It decides
   nothing the plan should decide — its `defend` sectors come out `"0 360"`, and
   its `stage` sectors are the only ones it can honestly derive, being the arc of
   onward path bearings each cluster was placed for.
4. **`navlayout validate`**, fix what it refuses, **`navlayout compose`** —
   seconds.
5. **Load it.** Read the spawn-rejection count against that map's stock
   baseline — stock ministry_coop logs exactly 1 — then play a round.

Only step 5 needs a server, and it is the same step the generated half has been
waiting on since `reverse` was written.

**Order.** Every map with a survey can be planned and authored. Emitting a preset
that *applies* also needs the stage↔zone mapping, which is read from the cpsetup
on the 13 maps that have one locally and otherwise has to be inferred from the
zone numbering — `infer_stage_count` already does that, exactly on 10 of the 11
shipped maps that number their zones, and any preset built on an inference should
say so and stay unloaded until the map is mounted.

| map | places | obj | headroom | why |
|---|--:|--:|--:|---|
| buhriz_coop | 41 | 7 | 5.9 | locally complete, most places, no pinch, 72 Mu² of floor — start here |
| sinjar_coop | 32 | 7 | 4.6 | locally complete, open, 66 Mu² |
| contact_coop | 30 | 7 | 4.3 | locally complete, open |
| embassy_coop | 36 | 7 | 5.1 | locally complete, and `reverse` refuses it — see below |
| drycanal_coop | 62 | 10 | 6.2 | most places of the 13, but 6 hinges and 45% severance |
| district_coop | 35 | 7 | 5.0 | one place holds 44% behind it |
| verticality_coop | 21 | 8 | 2.6 | last of the 13: three stage pairs are one-way |
| tell_open_coop, market_open_coop, drycanal_open_coop | 35, 30, 41 | 16, 13, 11 | 2.2, 2.3, 3.7 | mounted after all — their cpsetups ship beside the BSP, so the objective counts above are read rather than inferred. The reworkings the openness argument was aimed at, and `docs/permute.md` §3c is what the family costs on each |
| karkand_redux_p1_v1_2 | 119 | 12* | 9.9 | survey-only, and by far the most material of anything surveyed |

And a result worth stating on its own: **authoring dissolves the refusals that
block three of the thirteen.** `embassy_coop`, `revolt_coop` and `siege_coop` are
all refused by `reverse` for the same reason — too few usable coordinates on the
entry rung — which is a fact about ground the stock map only ever used as an
objective, never as a spawn. Choosing a box in that room from the plan removes
the reason. The generated and authored halves fail on different maps, which is
the argument for keeping both.

---

## 7. What the nav mesh cannot show

The honest limit of authoring from a plan, and it is a real one: **a nav mesh is
a tiling of the floor.** It has no walls, no props, no windows, no cover and no
sightlines. From the eagle-eye view the topology is exact — which rooms exist,
which doors connect them, how far apart they are on foot, what is behind what,
where a hull fits — and *what can see what is invisible*.

So arrangements authored from the plan alone get the topology right and the
sightlines unexamined. Two sources close that, both already built:

- **`bspscout`'s ray tracer** — 90-430k rays/s, agreeing with brute force to
  0.000000 u on five maps, with `exposure`, `objective_sight` and
  `spawn_exposure` written. Offline, needs the BSP, so the 13 shipped maps only.
- **The plugin's visibility export** — `mapmaker_survey.sp` already implements
  the area-pair matrix, off by default because it is O(n²) SDKCalls and stalls
  the server (~9.8M pairs on ministry's 3,128 areas). Engine ground truth, one
  pass per map, any map that can be mounted.

Recommended order: author topology from the plan first, because that is what
composition gates on, and run one of the two before deciding *where in a room* a
defender cluster sits. Without either, an arrangement is a judgement about doors
and distances, which is better than a scorer's guess and is not the same as
knowing what overlooks the street.

## 8. What this gives up

- **Any claim of optimality**, or of matching the shipped distribution. The
  corpus numbers become advice while authoring and a band to gate on, not a
  selector.
- **Variety is bounded by effort**, not by map capacity: a map yields the chains
  its authored sites admit, not the 2(N+1) ring walks `permute` gets for free.
- **A map update invalidates authored positions** exactly as it invalidates a
  survey, and area indices are renumbered by re-enumeration even when the
  geometry is unchanged. `survey_hash` is written into presets and still not
  enforced; authoring makes that worth fixing.
- **Nothing here has run in game either.** The authored half inherits the
  generated half's one honest weakness: the oracle is a server start and its
  spawn-rejection count, and it has not been asked yet.
