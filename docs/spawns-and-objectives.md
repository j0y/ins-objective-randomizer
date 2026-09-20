# Editing spawns and objectives without recompiling

Changing where players, bots and objectives are on a **shipped** Insurgency map,
with no vbsp/vvis/vrad, no Hammer, no BSPSource — and no server plugins, so it
works on a **vanilla** server.

**This is one of two routes to the same edit**, and the one that is proven.
It patches the file, so it needs no plugin and runs on a vanilla server; the
cost is that clients must download the patched map and a patched workshop map
is a fork you maintain against its author. `docs/layout-variants.md` is the
other route — the same edits made in the server process at load, so clients
join stock — and it also carries the analysis that decides *where* to move
things. Read this one first: everything it establishes about how the edit works
is what the other one automates.

Status: **confirmed in game.** `ministry_coop_v2` stage 1 has the cache moved,
the insurgent spawn moved onto it, and restricted areas removed; it has been played
and the defenders show up where they should. Capture-point objectives are still
untried — only cache objectives have been moved so far.

The lesson that cost the most: nothing offline predicts whether the engine will
accept a spawn point. Run the server and read its verdicts (§4).

---

## 1. The two places a checkpoint map's behaviour lives

| lives in | holds | edit cost |
|---|---|---|
| `maps/<name>.txt` (cpsetup) | objective **chain order**, which control points, stage→spawnzone mapping, `AttackingTeam`, cache objectives, arbitrary spawned entities, per-map cvars | plain text, no tooling |
| the BSP **entity lump** | `ins_spawnzone`, `ins_blockzone`, `ins_spawnpoint`, `trigger_capture_zone`, `point_controlpoint` | patch the lump, no recompile |

The `.txt` ships inside `insurgency_misc_dir.vpk` (1034 files, one `.txt` per map,
49 maps). A loose copy in `insurgency/maps/` overrides it — the server already has
all 49 extracted there, and they are byte-identical to the VPK versions.

**Caches are free.** Three of ministry_coop's six objectives are `obj_weapon_cache`
+ `point_controlpoint` pairs defined entirely in the `.txt`, at arbitrary
coordinates (the shipped file uses fractional ones). Move them with a text editor.
You can add and delete them too. Capture points are *not* free — they need the BSP.

---

## 2. The mechanism: brush entities translate by their `origin` key

The entity lump is **plain ASCII** inside the BSP (575,962 bytes / 1,896 entities on
ministry_coop) and ends with `}\n\0`.

vbsp stores a brush entity's geometry **relative to its `origin` key** and the engine
re-applies it at load — the same reason `func_door` can move. Proof: spawn zone
model `*31` has `origin "3997 -2166 114.98"` and its model `mins` is
`[-368, -248, -112]`, i.e. a box centred on nothing, waiting for the origin.

**So changing the `origin` string moves the volume.** Confirmed in game for
`ins_spawnzone`. All three of these classes carry the same relative-geometry origin
key, so all three translate the same way:

- `ins_spawnzone`
- `ins_blockzone`
- `trigger_capture_zone`

You can **translate** volumes. You cannot **resize** them — that is brush geometry,
which means a recompile. This limitation bites (see §6).

### Spawn points bind to zones by containment, not by name

All **442/442** `ins_spawnpoint` entities on ministry_coop fall inside a same-team
`ins_spawnzone` volume. There is no name reference. So moving a zone means moving
the points inside it, or the zone arrives empty.

Note several zone entities can share one `targetname` (ministry has four
`spawnzone_2` volumes for team 2).

---

## 3. The tool

`tools/movespawns.py`

```bash
# see what a map has
tools/movespawns.py MAP.bsp --list

# move a spawn zone and the spawn points inside it
tools/movespawns.py MAP.bsp --zone spawnzone_1 --team 2 --to "10 -4036 83" --out NEW.bsp

# same for other volume classes (these own no spawn points, so just the volume moves)
tools/movespawns.py MAP.bsp --class ins_blockzone --zone bz_ins_1 --to "x y z" --out NEW.bsp
tools/movespawns.py MAP.bsp --class trigger_capture_zone --zone cap3 --to "x y z" --out NEW.bsp
```

`--by "dx dy dz"` translates instead of targeting a centre. `--nav PATH` if the
`.nav` is not beside the input `.bsp` (needed when chaining edits through `/tmp`).

The "no usable spawn point" refusal only applies to `ins_spawnzone`; the
volume-only classes own no points and write without `--force`.

`--inset` (default 24) is how far inside a nav area a snapped point must sit; half a
player hull is 16. It is necessary but nowhere near sufficient — see §4.

`tools/placespawns.py` closes the loop against the server:

```bash
docker logs <server> 2>&1 | grep -oP 'Spawnpoint @ \(\K[^)]+' | sort -u > rejects.txt
tools/placespawns.py MAP.bsp --repair rejects.txt \
    --avoid 96 --near-valid 256 --toward "<objective xyz>" --out NEW.bsp
```

`--avoid` is the one that matters: without it a rejected point is usually its own
nearest candidate and nothing moves. `--near-valid` restricts candidates to the
neighbourhood of a point the engine accepted in the same run, which is the only
evidence available that an area is actually open.

Watch the byte budget: a longer coordinate string can push the lump past its
original slot, and it is then relocated to the end of the file (`201 8 46` ->
`-175 -3300 40` grew ministry_coop by 576 KB). That is safe, but the client copy
must be the same file.

**How it writes.** The patched lump is padded with NULs back to its original byte
length so no other lump offset moves and the file stays byte-identical in size. If
the new text is longer, the lump is relocated to the end of the file and only lump
0's offset/length change — deliberately *not* a full repack, because `GAME_LUMP`
stores absolute file offsets internally and would break if it moved. Integer-rounded
coordinates normally keep it in the original slot.

---

## 4. The validation trap — read this before trusting any placement

**`bspscout`'s `is_solid` cannot see `func_detail`.** It is a pure BSP-tree descent,
and vbsp does not split the tree with `func_detail`, which is most of a map's
interior walls (the project README documents the same trap for the walkable graph).

This cost us a full round trip: geometric probing walked straight through a
building, reported a floor 17 units *below* the real one, and reported clear
sightlines from all 33 nearby spawn points to a position that was out of bounds.
Every check passed. The placement was inside a building.

**Validate against the shipped `.nav` instead.** It defines where a player can stand.
The parser is vendored:

```
packages/bspscout/src/bspscout/navmesh.py
    parse_nav(path) -> NavMesh{ areas: {id: NavArea{nw, se, connections, ...}} }
```

ministry_coop.nav: v16.3, analyzed, 3,102 areas (it warns `EOF at area 3102/3128` —
harmless for footprints, fatal for connections, see below).

`movespawns.py` now refuses to run without a `.nav` and snaps every moved point onto
the nearest walkable area. Useful properties of the mesh:

- A nav area's footprint **is** where a player can stand, and its centre z **is** the
  floor height. The shipped cache A sits at z=32 and its nav area's centre z is
  32.03 — that is the placement convention.
- The control point marker goes **+72** above the cache (shipped convention).

### The `connections` graph is NOT trustworthy — do not path over it

An earlier version of this document claimed BFS over `NavArea.connections` gives
guaranteed reachability. **It does not, with this parser.** Run against *stock*
ministry_coop, a Dijkstra from the shipped security spawn reaches 533 of 3,102
areas and cannot reach **any** of objectives 2–6 — impossible on a shipped map that
people play. The graph splits into components of 2,379 / 533 / 178 / …, so the
parser is losing the connections that bridge them (it also warns `EOF at area
3102/3128` and leaves 42 edges pointing at ids it never parsed).

Area footprints survive that truncation and are fine — they are what `snap_to_nav`
uses, and those placements were confirmed in game. Only the edges are bad.

So **score placements by straight-line distance plus footprint size**, and calibrate
the distance against the shipped map rather than inventing a number.

### The engine's spawn-point test, and why nothing offline predicts it

`CINSRules::ValidateSpawnpoints` runs at map load and calls
`CINSRules::IsSpawnPointValid`, which is:

```
IsSpawnPointValid(point, player):
    if !this->vt[0x128](point, player)              return false
    pos = UTIL_SpawnPositionOffset(point)           // origin + a static offset
    v   = INSRules->GetViewVectors()
    return UTIL_CanEntityFit(player, pos, v->m_vHullMin, v->m_vHullMax)
```

A **player-hull fit against the real collision world** — world brushes, `func_detail`
and static props alike, with trace mask `0x0200400b`. A point that fails is logged
and dropped:

```
Spawnpoint @ (x, y, z) for team N was determined to be an invalid spawnpoint
```

Three offline predictors were tried against it and **all three failed**:

| predictor | why it does not work |
|---|---|
| nav area footprint | areas run right up to the wall they end at; clipping into one puts the point in the wall |
| `bspscout.is_solid` hull probe | cannot see `func_detail` or props — it passed 21/21 points the engine had just rejected |
| `data/*_clearance.npz` rays | horizontal only; rejected points had up to 448 u of measured clearance |

So **the server is the oracle.** Start it, collect the rejects, feed them to
`tools/placespawns.py --repair`, restart, repeat. Stock ministry_coop logs exactly
**1** rejection, so anything above that is yours.

**Sanity check any placement method** by pointing it at a shipped object first. If
it does not reproduce where NWI put cache A, it is wrong. Stock ministry_coop stage 1:

| shipped stage 1 | distance to cache A |
|---|---|
| security `spawnzone_1` `3997 -2166 115` | **1900 u** |
| insurgent `spawnzone_1` `2252 -1024 88` | **215 u** |

That is the convention worth copying: the attacker gets a ~1900 u approach, and the
defenders spawn effectively *on* the objective.

---

## 5. What `ins_blockzone` actually is

Short answer: **it is the restricted-area volume** — the thing that stops an
attacking player walking past the current objective — *and* a bot-AI exclusion
region. It is bound to a spawn zone and toggled with it.

### Player-facing effect (confirmed — this is the "you can't advance" zone)

From `server_srv.so`, `CINSBlockZoneBase::StartTouch` / `EndTouch`:

```
StartTouch(pOther):
    CBaseTrigger::StartTouch(pOther)
    if !pOther->IsPlayer()                       return
    if pOther->GetTeamNumber() not in {2,3}      return
    if !INSRules                                 return
    if pOther->GetTeamNumber() == this->GetTeamNumber()  return   // own team is free
    setup = INSRules->GetRestrictedAreaSetup()   // vtable +0x3fc
    if setup == 0                                return   // mode has no restricted areas
    if setup == 2:                                        // Push-style, needs waves
        if !INSRules->TeamHasReinforcementWaves(otherTeam) return
        if INSRules->vt[0x438]()                 return
    if pOther->InRestrictedArea()                return   // already flagged
    pOther->SetRestricted(true)

EndTouch(pOther):  same guards, then pOther->SetRestricted(false)
```

`CINSRules::GetRestrictedAreaSetup()` returns **1** in the base class and
checkpoint does not override it (only Push=2, Hunt, Ambush, Outpost, Survival,
Vendetta, Invasion and Elimination override), so on a coop map every enemy-team
player touching an active block zone gets restricted, unconditionally.

`SetRestricted(true)` sets player flag `0x200`, stamps `m_flRestrictedZoneTime`,
and broadcasts `Player.Security_RestrictedArea` / `Player.Insurgent_RestrictedArea`
(the HQ voice line). `CINSPlayer::PlayerWeaponPostThink` then compares
`curtime - m_flRestrictedZoneTime` against `mp_restricted_area_wpn_time` and calls
`SetWeaponRestricted(true)` — **weapons stop working**. No kill was found in that
path; it is a weapon lockout plus a warning, cleared on `EndTouch`.

So a block zone *is* the out-of-bounds boundary, and where it sits is entirely up
to the map: nothing else in the engine stops an attacker running ahead.

### Bot-facing effect

Also confirmed, from the decompiled server binary:

- Each insurgent `ins_spawnzone` names one via a `blockzone` key
  (`spawnzone_1` team 3 → `bz_ins_1`). Enabling the spawn zone enables the block
  zone — the server logs `[ins_spawnzone] '<name>' enabled for team N. bBlockZones = true`.
- The three *AI* places the game reads it are all scoring:
  - `CINSNavArea::GetSpawnScore` — an area whose associated spawn zone has an active
    block zone scores **−1.0** as a spawn position for the *other* team.
  - `CINSNavArea::ScoreHidingSpot` — same check changes hiding-spot scoring.
  - `CINSBotGuardCP::GetRandomHidingSpotForPoint` — iterates `ins_spawnzone` and
    checks `CINSBlockZoneBase::IsActive` when choosing where a bot guarding a
    control point takes cover.

So on the bot side: *while this stage is live, don't spawn the other team in this
region and don't have defending bots hide there.*

### Turning restricted areas off

`tools/blockzones.py MAP.bsp --disable --out NEW.bsp`. Two edits are needed and it
does both: `StartDisabled` -> 1 on every volume (some ship enabled, see below), and
the `blockzone` key stripped from every `ins_spawnzone` so no stage can re-enable
them. `--list` shows the volumes and which spawn zone names each.

Note this also gives up the AI scoring above — bots may then spawn and hide in
regions the map intended to keep them out of.

### Binding, and the trap in it

A spawn zone names its block zone with the `blockzone` key.
`CINSSpawnZone::ToggleBlockzone` resolves it with `FindEntityByName` **in a loop**,
so *every* volume sharing that targetname toggles together (`bz_ins_5` has three,
`bz_ins_3` has two). Enabling the spawn zone enables them all.

**Watch out:** `bz_ins_5` model `*27` (centre `-300 -2603 -200`, 2600x534x208) ships
with `StartDisabled 0` — it is live from map load, before any stage enables it. Any
security route through that box weapon-locks the player.

On ministry_coop there are 8 volumes across 5 names, all `TeamNum 3`,
`spawnflags 2`, mostly `StartDisabled 1`. Sizes vary wildly: `bz_ins_1` is a
3022×496×188 slab, `bz_ins_4` is 6664×2544×784.

---

## 6. Current state of `ministry_coop_v2`

Built from stock `ministry_coop`. Same file size (58,089,908 bytes), entity lump
patched in place both times.

| what | stock | v2 | check |
|---|---|---|---|
| security `spawnzone_1` (`*31`) + 33 points | `3997 -2166 115` | `10 -4036 83` | 33/33 on nav |
| insurgent `spawnzone_1` (`*36`) + 56 points | `2252 -1024 88` | `-1875 -3550 -28` | 56 usable after repair |
| `cache_a` (in the `.txt`) | `2457 -1057 32` | `-1875 -3825 -28` | nav area 146, floor z −27.97 |
| `cachepoint_a` (in the `.txt`) | `2457 -1057 104` | `-1875 -3825 44` | +72 convention |
| all 8 `ins_blockzone` | 1 live at load, 9 spawn zones bind them | inert | 0 live, 0 bound |

Stage 1 matches the shipped proportions: **1900 u** security spawn → cache A
(exactly the stock figure) and **275 u** insurgent spawn → cache A (stock is 215 u).
Because spawn points bind by containment, the moved volume also encloses points that
used to serve other stages, so the stage-1 pool is larger than the 56 that moved.

The defence, measured against stock stage 1 and verified on a running server:

| | stock | v2 |
|---|---|---|
| usable insurgent points | 45 | **56** |
| within 400 u of the cache | 10 | **10** |
| within 600 u | 22 | **20** |
| within 900 u | 36 | **39** |
| median distance to the cache | 603 u | 741 u |
| still rejected by the engine | 0 | 16 |

Entity counts are identical to stock (1,896 entities, 22 spawn zones, 8 block zones,
442 spawn points).

### Getting there took four server round trips

The first build looked fine offline and played wrong: the engine threw out 27 of the
stage-1 insurgent points, so the bots that did spawn were the far ones and nothing
defended the cache. `snap_to_nav` was clipping points onto the *edge* of a nav area,
which is a wall. Rejections per build, against a stock baseline of 1:

| build | change | rejections |
|---|---|---|
| 1 | `snap_to_nav` inset 8 | 40 |
| 2 | inset 24, area must have interior | 34 |
| 3 | `placespawns.py --repair` onto clearance samples | 23 |
| 4 | `--avoid 96 --near-valid 256` | **17** (16 insurgent, 0 security) |

The 16 that remain are a stubborn cluster north-west of the cache; they are surplus,
since the usable pool already exceeds stock. Another `--repair` round would chip at
them.

Objective chain order is unchanged.

**Restricted areas are off** — `tools/blockzones.py --disable`, because the target
server does not use them. That means nothing holds an attacker behind the current
objective, which is the intended behaviour here; it also removes the `bz_ins_1`
sizing problem that used to block this stage (a 3022×496 slab could not cover the
approach without swallowing the cache, and volumes can only be translated).

A few insurgent spawn points would not snap and stayed put. They are not orphans —
they fall inside `spawnzone_2` / `spawnzone_5` and serve those stages instead.

### Files — server and client must match

An edited BSP no longer matches the stock one, so both ends need the same file.

```
$SERVER/insurgency/maps/   ministry_coop_v2.{bsp,nav,txt}
$CLIENT/insurgency/maps/   ministry_coop_v2.{bsp,nav,txt}
```

`$SERVER` is the dedicated server's install root, `$CLIENT` the game's.

The client needs the `.nav` too, not just the `.bsp` and `.txt`. Testing solo runs a
listen server, so the client *is* the server: without the mesh there is nothing for
the bots to path on. It is only the dedicated-server case that can skip it.

A renamed map needs its `.nav` and `.txt` copied to the new name too — the cpsetup
lookup is by map name.

---

## 7. Running a vanilla test server

The image bakes maps in, so bind-mount the maps dir over them. The image's
`entrypoint-vanilla.sh` may also be stale, so mount the current one too.

```bash
SERVER=...        # the dedicated server install
docker run -d --name insurgency-vanilla-v2 \
  -p 27025:27025/udp -p 27025:27025/tcp \
  -e START_MAP=ministry_coop_v2 -e MAX_PLAYERS=32 -e TICKRATE=64 \
  -v "$SERVER/insurgency/maps":/home/steam/insurgency-server/insurgency/maps \
  -v "$SERVER/../scripts/entrypoint-vanilla.sh":/home/steam/entrypoint-vanilla.sh:ro \
  --entrypoint /home/steam/entrypoint-vanilla.sh \
  insurgency-server-insurgency:latest
docker logs -f insurgency-vanilla-v2
```

This is the loop that matters: it is the only thing that can tell you whether a spawn
point is real (§4).

Log lines that matter:

- `[ins_spawnzone] '<name>' enabled for team N` — the stage's zones went live.
- `Spawnpoint @ (x,y,z) for team N was determined to be an invalid spawnpoint` —
  a point the engine rejected. This is the symptom of the §4 trap.
- `Failed finding CP area for N!` — an objective with no nav area under it.
  **Pre-existing, not ours**: a stock `ministry_coop` server start prints
  `Failed finding CP area for 2!` too. Ignore it.
- Restricted areas are off in v2, so `bBlockZones = true` in the spawn-zone log line
  no longer means anything is live; `tools/blockzones.py --list` is the check.
- `The Navigation Mesh was built using a different version of this map.` — expected
  and harmless; the mesh still loads.

---

## 8. Next steps

1. **Stages 2-6 keep stock geometry and stock spawns.** Only stage 1 is re-sited,
   so the chain currently runs from a deliberate stage 1 into five shipped ones.
2. **Capture objectives.** `trigger_capture_zone` (the volume) plus its
   `point_controlpoint` (the marker, a point entity — just edit `origin`). On
   ministry_coop: `cap3`/`cp3`, `cap4`/`cp4`, `cap6`/`cp6`. `--class
   trigger_capture_zone` is wired up and now writes without `--force`, but **has
   never been run**. Validate by checking the moved volume contains nav areas, or
   the game prints `Failed finding CP area`.
3. **Re-chain the objective order** in the `.txt` — pure text, not tried yet. The
   stock chain wanders (south → far west → centre → back south → far west); with
   cache A moved west it is worth revisiting.
4. **Chip at the last 16 rejections** with another `placespawns.py --repair` round,
   if a bigger stage-1 defence is wanted. They are surplus today.

## 9. Unverified / open

- Whether a `.txt`-only change works server-side without clients downloading
  anything (would make cpsetup edits far cheaper than BSP edits).
- `movespawns.py` leaves un-snappable points at their original coordinates, which
  orphans them unless another same-team zone happens to contain them. It warns when
  that happens; so far they have always landed in another stage's zone.
- Why the last 16 rejections resist repair. They sit at the nav floor with hundreds
  of units of horizontal clearance, so it is neither the wall nor the ceiling —
  static props are the likely culprit, and nothing offline currently sees them.
