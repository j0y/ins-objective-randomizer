# mapmaker

Move where an Insurgency (Source 2013) checkpoint map's **objectives, spawns and
restricted areas** are — and analyse maps well enough to decide where they
should go.

The shipped campaign maps are corridors: they play forward, they play backward,
and that is most of the variation they have. Workshop maps are not all like
that. A good many are open — courtyards, compounds, buildings with real
interiors, streets that connect more than two ways — and the shipped layout
picks one route through that space and plays the map as if the other routes were
not there. Re-siting an objective on a map like that is not a nudge. It is a
different map, built out of geometry someone already finished.

## Install it on a server

`release/` is the built payload, committed: the compiled plugins, the gamedata,
and one preset per map, laid out as an `insurgency/` directory.

```bash
cp -r release/addons release/presets /path/to/insurgency/
```

It needs Metamod:Source and **SourceMod 1.11.0.6919 or newer** — the build that
backported the `EntityLump` API the applier rewrites the lump through — and
nothing else: no Python, no Docker, no survey run. Clients join stock; the
`.bsp` on disk is never touched, so a workshop map stays the subscriber's map
and there is nothing to download. `release/README.md` is the operator's
documentation: the cvars, the `sm_preset` command, why a layout takes effect on
the *next* map load, and what an older SourceMod looks like when it fails.

117 maps have presets, over the 146 checkpoint maps of the server list this was
built against. Of the rest, 7 are refused by the generator's gates and 22 have
never been surveyed.

## Two ways to move an objective

Both are built and both are confirmed in game. They trade the same thing in
opposite directions.

**Rewrite it at load** — `plugin/`, `docs/layout-variants.md`. The edits are
made in the server process against SourceMod's `EntityLump` API during
`OnMapInit`. One map can carry any number of layouts, rotated between loads. The
cost is a modded server.

```bash
MAP=ministry_coop mise run play      # rotates presets from presets/<map>.cfg
```

**Patch the file** — `tools/`, `docs/spawns-and-objectives.md`. The BSP entity
lump is plain ASCII, and vbsp stores a brush entity's geometry relative to its
`origin` key, so changing that key translates the volume. No recompile, no
Hammer, no plugin, and the result runs on a **vanilla** server. The cost is that
clients must download the patched map and match it byte for byte, and a patched
*workshop* map is a fork you now maintain against its author's updates.

```bash
tools/movespawns.py  MAP.bsp --zone spawnzone_1 --team 3 --to "x y z" --out NEW.bsp
tools/blockzones.py  MAP.bsp --disable --out NEW.bsp        # no restricted areas
tools/placespawns.py MAP.bsp --repair rejects.txt --out NEW.bsp
```

Caches are cheaper than either: three of ministry_coop's six objectives are an
`obj_weapon_cache` plus a `point_controlpoint` defined entirely in
`maps/<name>.txt`, at arbitrary coordinates. Those move with a text editor.

## The analysis, which is the actual problem

Moving an objective is easy. Knowing where to move it is not, and one trap is
sharp enough to belong on the front page:

**Nothing offline predicts whether the engine will accept a spawn point.**
`CINSRules::IsSpawnPointValid` is a player-hull fit against the real collision
world, `func_detail` and static props included. Three offline predictors were
tried against it and all three failed; one passed 21 of 21 points the engine had
just rejected. `docs/spawns-and-objectives.md` §4 is the whole story, and the
most expensive thing this project has learned.

So ground truth comes from the engine, and the work splits three ways:

```
survey    in game, once per map    nav areas, connections with the engine's own
          plugin/mapmaker_survey     edge lengths, hull validity per area,
                                     objective entity inventory
                                     -> surveys/<map>.json

analyse   offline, Python          how open is this map, how many genuinely
          packages/navlayout         different routes does a site admit, where
                                     do defenders belong -> presets/<map>.cfg

apply     in game, deliberately    rewrite origins at load, rotate presets,
          plugin/mapmaker_layout     verify with a hull trace
```

The middle step is calibrated rather than invented. The 13 shipped `_coop` maps
are 13 labelled examples of placement that works, measured in the same units, so
"is this layout plausible" is distribution matching against them.
`docs/corpus.md` is that table and `docs/metrics.md` defines what is in it.

**Why the survey has to run in game**, when most maps ship a `.nav`: the offline
`.nav` parser truncates its connection graph — on ministry_coop it stops at area
3102 of 3128 and leaves components of 2379/533/178, with no path from the
shipped security spawn to objectives 2–6. Every metric the generator runs on is
an edge computation, so that is the whole basis of it. Hull validity is the trap
above. Both are the engine's own answers here, with the engine's own edge
lengths.

## Running the pipeline yourself

No Steam account is needed — the asset kit comes from the dedicated server,
which is an anonymous download. Docker and mise are the only host requirements.

```bash
mise run setup                       # venv + packages
mise run game                        # asset kit + shipped maps (9.4 GB, anonymous)
mise run sourcemod                   # Metamod:Source + SourceMod into game/
mise run plugins                     # compile plugin/scripting, refresh release/

mise run survey -- --coop            # stage A: the 13 checkpoint maps
mise run presets                     # stage B: a reversal or a family, per map
MAP=ministry_coop mise run play      # stage C: a playable server
MAP=ministry_coop mise run checklayout   # ...or drive it and read the verdict
```

To analyse a map without any of the server half — `bspscout` needs only a
`.bsp`, and will tell you what the layout looks like, where a player can walk,
what sees what, and where the plausible objective sites are:

```bash
mise run maps                                        # shipped .bsp -> maps/official/
BSP=maps/official/ministry_coop.bsp mise run scout    # one map -> out/<name>/
mise run corpus && mise run metrics                   # ...all of them -> docs/corpus.md
```

### A whole server's map list

A running server's config is two text files — the workshop IDs it subscribes to
and the mapcycle it plays — and that is enough to reproduce its map list here
and hand every map a layout. Drop the pair in `serverconfig/`, which is
gitignored: someone else's server, someone else's map list.

```bash
mise run subscribe                   # download every ID in the list, anonymously
mise run workshop -- --mount --ids serverconfig/subscribed_file_ids.txt
mise run surveyall -- --cycle serverconfig/mapcycle_checkpoint.txt
mise run presets
CYCLE=serverconfig/mapcycle_checkpoint.txt mise run serve
```

**A run serves one gamemode.** The engine initialises its gamemode settings at
boot and never again, so a rotation that changes mode mid-flight loses its spawn
points and takes the server down with it. `MM_GAMEMODE=conquer` serves the other
half as its own run — and gets no layout either way, because a preset is a
permutation of a *checkpoint* objective chain and the applier declines anything
else at map init.

`docs/layout-variants.md` has the rest of what a hundred-odd workshop maps cost
to serve: why the workshop needs no Steam account, why `sv_playlist custom` is
what lets a rotation of workshop maps run at all, and why one map has to be the
unit of failure rather than the whole run.

## Layout

```
packages/bspscout/     parse + analyse compiled maps — the analysis engine
packages/navlayout/    survey JSON -> presets: segmentation, permutation, gates
packages/mapmaker/     the asset kit and .vmf writer. Parked, except metrics/
plugin/                SourceMod: survey exporter + layout applier, gamedata
release/               the committed copy-to-a-server payload
tools/                 entity-lump editing, the servers, the generators, fetchers
docs/                  spawns-and-objectives  (the file route)
                       layout-variants        (the plugin route, and serving a list)
                       places, reverse, permute   (segmentation; the generated half)
                       metrics, corpus        (what is measured, and on what)
                       authoring, toolchain, plan-v1
authored/              hand-authored sites and spawn clusters, one .kv per map
data/                  measured map data the tools read (player-hull clearance)
cache/  out/  vendor/  game/  surveys/  maps/  serverconfig/   all gitignored
```

## State

**Both routes are confirmed in game.** `ministry_coop_v2` stage 1 has its cache
moved, the insurgent spawn moved onto it and restricted areas removed, and it
has been played with defenders showing up where they should. The plugin has been
run far more widely — 2026-09-01 ministry_coop `reversed` played through, then
seven maps unattended, then whole-rotation runs. Capture-point objectives are
wired up but only cache objectives have actually been moved.

**Everything expensive was learned from a server, not a file.** A `within` box
lost its second half to padding, so no spawn point ever moved. Caches are
respawned every round and had to be re-teleported rather than placed once. The
minimap draws from `CINSObjectiveResource::m_vCPPositions`, not from the marker
entity, so a moved marker was invisible to the map screen.
`CINSSpawnZone::ToggleBlockzone` puts a restricted area back whenever its stage
goes live. A targetname does not identify an entity — `maps/<map>.txt` creates a
second `point_controlpoint` beside whatever the mapper baked in, 1,696 u away on
congress_coop. `plugin/README.md` lists all of them and what each one cost.

**bspscout is solid, and was wrong for a long time.** Writing the ray tracer
turned up that the walkable graph had been wrong the whole time: vbsp does not
split the BSP tree with `func_detail`, so the tree-descent solid test could not
see 58–70% of a map's brushes. A quarter of ministry's supposedly reachable
nodes were inside solid geometry; its walkable area was 103M u² and is 50M.
Every distribution in `docs/corpus.md` moved and the corpus was re-scouted.
Worked examples are in `out/ministry/` and `out/gioconda_eron_pripyat/`.

**The gap is the 22 unsurveyed maps** in the rotation, which need a server run
each, and the openness metrics that would decide which maps are worth re-routing
at all rather than merely reversing.

## Parked: generating and remixing geometry

This began as a map generator, and the generator still works — it is simply not
what the project is aimed at. Making *new* geometry was never the binding
constraint; deciding where a fight should happen on geometry that already exists
is.

A hand-written `.vmf` compiles clean through vbsp/vvis/vrad and comes back as a
`.bsp` bspscout measures (`MAP=duplex mise run loop`). A full official map
decompiles, recompiles and lights in about fourteen seconds with its geometry
intact, and `mise run remix` cuts a map at a plane, moves everything past it,
reseals both cross-sections and opens a door per walkable crossing — 94% of
ministry's reachable area kept, both halves in one component.

What was never established for either: **nothing has loaded on a server, and
nobody has looked at a seam in the engine.** Every fidelity claim is a
measurement of a file rather than of a picture. That is a large part of why the
project turned toward maps whose art is already finished and already shipped.
