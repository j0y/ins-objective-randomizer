# plugin

The in-game half of `docs/layout-variants.md`: a survey exporter and a layout
applier, both SourceMod plugins, plus the gamedata that resolves the nav-mesh
symbols they need.

| | |
|---|---|
| `scripting/mapmaker_survey.sp` | **stage A.** Walks the engine's nav mesh, traces the player hull, inventories the objective entities, writes `surveys/<map>.json`, changelevels. Run once per map version. |
| `scripting/mapmaker_layout.sp` | **stage C.** Reads a generated preset, rewrites entity origins at map init, rotates presets round-robin, verifies and announces. |
| `scripting/include/mm_nav.inc` | raw `CNavArea` access — centres, corners, connections with the engine's own edge lengths, `m_insFlags` |
| `gamedata/mapmaker.txt` | `@`-symbol lookups for `TheNavMesh`, `GetNearestNavArea`, `IsPotentiallyVisible`, `IsBlocked`, and the `CNavArea` field offsets |
| `presets/example.cfg` | the preset format, commented |

## The layout plugin's commands

| | who | does |
|---|---|---|
| `sm_preset` (`!preset` in chat) | anyone | which layout this map loaded — its name, which of N in the file, edits made, and whether any rule matched nothing. Says *why* when the map is stock, which is four different answers: the toggle is off, stock is pinned, the map has no preset file, or the preset it picked asked for nothing |
| `sm_layout` | admin | the same line, plus the pin |
| `sm_layout list` | admin | the presets in this map's file, marking the active one and the control |
| `sm_layout <name>` \| `stock` | admin | pin one for the **next** load |
| `sm_layout off` \| `on` | admin | set `mm_layout_enabled`; takes effect on the next load |
| `mm_layout_objdump [x y z [r]]` | server console | every objective entity and where it stands now, or every entity near a coordinate |

`mm_layout_enabled` `mm_layout_announce` `mm_layout_dir` `mm_layout_blockzones`
`mm_layout_max_failures` `mm_layout_debug` `mm_layout_hull_min` `mm_layout_hull_max`
are the cvars; `tools/layout-server.sh` writes the first four and `--stock`
boots with the applier off.

**Checkpoint only, and it checks.** A preset is a permutation of a checkpoint
objective chain, and a `_coop` map is not only ever played as checkpoint — the
same .bsp appears in a rotation under conquer, and hunt, survival and outpost
run on these maps too. Nothing in the map name says which game is starting, so
the applier reads `mp_gamemode` at map init and leaves anything that is not
checkpoint exactly as it ships: no lump edit, no objective moved, no restricted
area touched, and no rotation slot spent. `sm_preset` on such a map answers
"stock — this map is loading as 'conquer', and a preset is a permutation of a
checkpoint objective chain". Verified over three consecutive loads: the same
map applied its preset as checkpoint and was declined as conquer.

**The rotation skips the control.** Every generated preset file opens with a
`stock` entry, and it used to be rotated to like any other member — one load in
N of every map spent on the unmodified map, which on a two-preset map is every
other load. There are three cheaper ways to the same place: the boot load of a
session is always stock, `sm_layout off` / `--stock` turn the applier off, and
`sm_layout stock` pins it for one map. So the applier skips it when it picks,
*unless it is all the file has* — a map the generator emitted no layout for then
loads stock and `sm_preset` says "its preset file holds only the stock control"
rather than leaving it to look like a failure. The entry itself stays: it
carries the stock round's metrics, which every gate in the generator measures a
layout against.

**Nothing is said on load.** `mm_layout_announce` is `0`: the applier writes its
line to the log and waits to be asked. A permuted map is the same map — same
geometry, same name, same objectives in the same HUD order — and a player told
at round start that this is walk 3 of 16 has been handed both facts the
permutation exists to withhold, that the map is not the one it looks like and
that there are fifteen others. The line is still one command away: `sm_preset`
answers anyone who types it, so a player with a bad round to report can name it
after the fact, which is the only time they need it. `mm_layout_announce 1` puts
it back in chat once per map and once to each player who joins, for a test that
wants the layout named without anyone having to ask.

**The toggle only ever means "from the next load".** The lump is rewritten in
`OnMapInit`, before a single entity exists, so a map that loaded with a layout
has no un-layouted state to go back to. The half that *is* live — the burst that
re-teleports caches every round — would if it stopped leave the caches back on
their `.txt` coordinates while every spawn zone stayed permuted, which is worse
than either end. The running map therefore keeps what it loaded with, and the
cvar's change hook says so in the log and in chat rather than leaving someone to
flip it and watch nothing happen.

**And the console outranks the config, which it had to be made to do.** A server
config is executed on *every* map load, so the `mm_layout_enabled 0` that
`--stock` writes is put back after each one. Measured 2026-09-19 on a
`--stock ministry_coop` server: `sm_layout on` was read correctly by the next
`OnMapInit` — which runs before the config — the map loaded a preset, and the
config exec immediately behind it set the cvar to 0 again, so the load after
that was stock. One load of reprieve, from a switch that said nothing about
being temporary. `OnConfigsExecuted` now re-asserts whatever the console last
asked for and logs that it did; it is the same precedence `sm_layout <name>` has
always had over a preset file, applied to *whether* rather than to *which*, and
there is still one switch — the override re-asserts the cvar rather than
shadowing it. `sm_layout off` on a server whose config says `1` hands it back.

`mm_nav.inc` and the nav half of `gamedata/mapmaker.txt` were taken from the
sibling **insurgencySamrtBots** project
(`insurgency-server/scripting/smartbots/navmesh.inc` and
`insurgency-server/gamedata/smartbots_navspawn.txt`). That project is upstream
for the signatures: when a game update moves one, fix it there and copy the
line back. The offsets marked VERIFIED are the ones its navspawn plugin runs
against this build today.

## Why any of this is in the game rather than offline

Three things the map files cannot give:

1. **The nav connection graph.** The offline `.nav` parser truncates — on
   ministry_coop it stops at area 3102 of 3128 and leaves the graph in
   components of 2379/533/178, with no path from the security spawn to
   objectives 2–6. Every metric the layout generator runs on is an edge
   computation, so that is the whole basis of the pivot. Here the graph is the
   engine's own, with the engine's own edge lengths.
2. **Hull validity.** `CINSRules::IsSpawnPointValid` is a player-hull fit
   against the real collision world, `func_detail` and static props included.
   Three offline predictors were tried and all three failed. `TR_TraceHull` is
   that same test, in-process.
3. **A mesh at all.** Five of the subscribed workshop maps carry no `.nav` of
   their own. Four of them borrow one: a cpsetup's `navfile` key names the mesh
   to load, and `tell_open_coop`'s says `tell`, `market_open_coop`'s says
   `market` — shipped meshes, on disk, under a name no lookup for `<map>.nav`
   will find. `drycanal_open_coop` names a file that does not exist and the
   server generates that one. So this reason is weaker than it was written
   (corrected 2026-09-03); the first two stand unchanged, and they are the ones
   the pivot rests on.

## Build and run

```bash
mise run sourcemod    # Metamod:Source + SourceMod into game/insurgency/addons
mise run plugins      # compile to plugin/build/, install into the game tree
mise run survey -- --coop           # stage A over the 13 checkpoint maps
mise run survey -- --workshop       # ...or every subscribed workshop map
MAP=ministry_coop mise run play     # stage C, a playable server
MM_BLOCKZONES=keep MAP=ministry_coop mise run play   # ...with the map's own restricted areas
MM_LAYOUT_ENABLED=0 MAP=ministry_coop mise run play  # ...stock, as the control
```

A layout takes effect on the *next* load, because SourceMod runs the server
config after the map is already up: the first map of a fresh server comes up
stock and one `changelevel` gets the layout. `tools/layout-server.sh` now issues
that changelevel itself — it waits for the applier's post-load line in
SourceMod's log and feeds one `changelevel` down the server's stdin, so a
booted server arrives at a layout without anyone typing at the console. It also
sends `sm_layout <name>` there when `--preset` was given, which is the one
place a pin does stick. `--no-boot-reload` turns it off, and
`tools/check-layout.sh` passes it: that first stock load is its control.
`mm_layout_objdump` logs every
objective entity and where it is standing, or, given `<x> <y> <z> [radius]`,
every entity near a coordinate — which is how you find what a moved objective
left behind.

Both servers are compose services over the same bind-mounted game tree
(`docker/compose.yml`): `survey` runs headless and unattended, `layout`
publishes a port. Metamod and SourceMod live in `game/insurgency/addons`, not
in an image, so neither is baked into a build.

## What the first live run cost

Four faults, none of which any offline check could have reached. They are worth
listing because each one is a place where the running game disagrees with what
the file says.

**A `within` box lost its second half.** The preset writes a box as
`"minx miny minz  maxx maxy maxz"`, padded, and `ExplodeString` returns an empty
token for the second space — so the box parsed as min `(4144 -2036 25)`, max
`(0 4148 -2032)`, which contains nothing. All 74 spawn-point rules and both
blockzone rules matched nothing, silently, and the apply summary called them
"matched nothing" rather than "misparsed". `ParseFloats` now skips empties.

**A cache is respawned by every round.** `obj_weapon_cache` entities are new
entities each round, standing on the `.txt` coordinate, so the one teleport five
seconds after map start held for exactly one round. Objectives are now
re-applied in a burst over the first six seconds of every round; it is
idempotent, and a pass with nothing to do says nothing.

**The map screen does not read the marker.** The control point master copies
every point's origin into `CINSObjectiveResource::m_vCPPositions` during setup,
once, and the client's HUD, compass and minimap draw from that array. A
lump-rewritten marker is therefore already right — the lump is rewritten before
any entity exists — but a cache marker is teleported after setup, so the world
and the map screen disagreed. The array is now written directly, keyed by the
coordinate it still holds, since this build's `point_controlpoint` carries no
readable point index.

**A restricted area is put back by the stage that names it.**
`CINSSpawnZone::ToggleBlockzone` re-enables the volumes its own stage names
whenever that stage goes live, so `StartDisabled` at load survives until the
round advances. `mm_layout_blockzones 0` now also drops the `blockzone` key from
every `ins_spawnzone` — the same edit `tools/blockzones.py --disable` makes in
the file — and fires `Disable` at runtime as well. This matters for a
permutation specifically: a restricted area holds the attackers behind the
objective, and moving the ground under the chain moves which side that is — so
`mm_layout_blockzones` defaults to `0` and `MM_BLOCKZONES=keep` is what asks
for the map's own walls back. A stock load is untouched either way: it edits no
lump entry at all.

## What the next five maps cost

2026-09-02, the same server driven by `tools/check-layout.sh` over
market_coop, congress_coop, dead_air, gizab_aof and ministry_coop, then both
`small` families. Five more things the file could not have said:

**A targetname does not identify an entity.** `maps/<map>.txt` creates a
`point_controlpoint` for each cache beside whatever the mapper baked into the
BSP, so an objective name answers twice on nine of the 46 surveyed maps — and on
congress_coop the two `cachepoint_g` entities sit 1,696 u apart, because the
shipped cpsetup has the sign of the y wrong. The applier took the first match.
It now takes the one nearest to `from` / `cp_from` — the stock coordinate the
preset was measured at — or to the coordinate this preset moves it to, since a
later pass in the same round finds the real one already moved and the decoy
still where it always was. A name that answers more than once is logged.

**The gamemode looks an objective's nav area up from the marker.** `Failed
finding CP area for N!` is not about the capture volume: ministry_coop's `cp3`
covers 53 walkable areas and is refused anyway, because its marker hangs in a
stairwell. The generator now stands a moved marker on floor the objective
covers instead of translating it — `docs/reverse.md` §6 has the measurement
that fixed the threshold.

**The first load of a session is always stock.** The server config is what
loads this plugin, and the engine executes it *after* the first map's
`OnMapInit` — so nothing is applied to the map the server boots on, whatever
the rotation says. That is a free control rather than a bug: `check-layout.sh`
reads every engine count against it, and `layout-server.sh` spends one
changelevel on it so a played server does not.

**A stock load has to report too, or the harness waits for a line that is
never coming.** `OnMapStart` used to return early on a stock preset — nothing
was moved, so there was nothing to verify — and `check-layout.sh` counts
finished loads by the post-load pass's own `verify: map-wide` line. Every
rotation that came back around to `stock` therefore hung the run for the full
load timeout, three retries deep, on maps whose layouts had already applied
cleanly one load earlier. The pass now runs on every load and only the *moving*
half is gated, which also makes the stock load print the map-wide hull count
that the rest of the table is read against: on all four maps it comes back
identical to the boot load's, which is the plainest available statement that a
stock preset is a no-op.

**A brush entity need not carry an origin key, and a rename does not need one.**
`ApplyEdits` dropped any lump entry with no `origin` before it considered a
single rule. On six of the seven maps run so far that is free — every
`ins_spawnzone` carries one. `cs_officeb3_coop_v1_5` is the seventh, and not
one of its 27 does: it is a CS port whose zones are pure brush entities, which
keep their position in their model. Since `docs/reverse.md` §3 made all but two
clusters per layout a *rename*, and a rename needs no coordinate, the guard was
throwing away almost every layout on that map — all 13 read back with every
zone rule `unmatched`. The origin key is now required only of the rules that
need one: a `within` box cannot match an entry with no coordinate to test, and
a rule that moves is refused outright rather than silently doing only its
renaming half, because an absolute origin written onto a brush that never had
one translates it by the whole coordinate instead of to it. `bad` on that map
went from 10-12 per layout to 1-2, and ministry_coop re-run against the change
is unchanged at 101 edits and 0 bad.

## Two things to know before trusting output

**The corner offsets are probed, not known.** `CNavArea::m_center` is verified
against this build; `m_nwCorner` / `m_seCorner` are not. `MM_NavProbeCorners`
checks the identity `center.xy == midpoint(nw.xy, se.xy)` over a sample of real
areas and, if the hinted offsets fail it, scans for a pair that holds. The
survey logs what it found and records `"corner_offsets": true|false` in the
JSON. Put the discovered values back into `gamedata/mapmaker.txt`. Without
them the survey still exports centres, connections and hull validity — only
footprints are lost.

**`TheNavAreas` is unconfirmed.** If the symbol does not resolve, the survey
floods from the map's spawn and objective entities instead. That covers the
reachable component, which is what the metrics score anyway; the JSON records
which method produced it in `"enumeration"`.

## Not yet done

- The survey plugin has still never been run against a server. The layout
  plugin has: 2026-09-01, ministry_coop, preset `reversed`, played through, and
  2026-09-02 over seven maps unattended (`tools/check-layout.sh`) - five
  corridor reversals and both `small` families, 40 map loads in all.
- **`sm_layout <name>` in the server config pins one load too late.** Traced
  2026-09-03: the engine executes the config after the map's `OnMapInit`, the
  same reason the first load of a session is stock — so the pin is set, logs
  `pinned '<name>' - takes effect on the next load`, and the load it was meant
  for has already chosen by rotation. (It also runs twice per load, because the
  engine execs the config twice.) `tools/layout-server.sh` therefore sends
  `sm_layout` down stdin ahead of its boot changelevel, where it lands before
  the `OnMapInit` that reads it; the config line still serves the
  `--no-boot-reload` path from its second load on.
- `survey_hash` is written but not enforced. The live check on a stale preset
  is the post-load read-back: every rule is matched to the entity its lump
  entry became — by lump ordinal, since a permutation leaves an entity on every
  target coordinate whether or not anything moved — and reported as one of
  `unmatched`, `shadowed`, `missing`, `stuck`, `nudged`, `adrift`, `misnamed`,
  `hull` or `ok`. `mm_layout_debug 1` lists every rule rather than the first
  twenty failures.
- Visibility export (`mm_survey_vis 1`) is off by default. It is O(n²)
  SDKCalls — ~9.6M on a 3,100-area map — and its cost is unmeasured.
