# Layout variants at runtime

Generating *several* playable layouts for one map — different objective sites,
different spawns, and above all **different routes** — and rotating them at map
load from a server plugin, with no patched `.bsp` and nothing for clients to
download.

Status: **stages A and C are written; nothing has been run.** `plugin/` holds
the survey exporter and the layout applier, both compiling clean under
SourceMod 1.11, and `tools/survey.sh` / `tools/layout-server.sh` boot them in
containers. Stage B — the offline generator — is not started. No plugin here
has executed against a server yet.

The companion document, `docs/spawns-and-objectives.md`, describes the
file-patching route that *is* built and confirmed in game; this one moves the
same edits into the server process and puts a real analysis in front of them.

---

## 1. What this is for

The shipped campaign maps are corridors. They play forward and they play
backward, and that is most of the variation they have — the geometry only ever
offered one way through, so a layout edit can move the fight along the corridor
but cannot change its shape.

Workshop maps are not all like that. A good many are **open**: courtyards,
compounds, multiple buildings with real interiors, streets that connect more
than two ways. The shipped layout picks one route through that space and the map
is then played as if the other routes were not there. Those maps are the target,
because on them a layout edit is not a nudge — it is a different map.

That sets a payoff ladder, and it is worth being honest about where the value is:

| variant class | what actually changes | who notices | payoff |
|---|---|---|---|
| micro — spawns moved a few hundred units | pressure timing, first-contact point | veterans only | low |
| re-sited stage — one objective + its spawns moved | where a stage's fight happens | most players | medium |
| **re-routed layout** — objective sited so a *different approach* is the live one | which parts of the map are in play at all | everyone | **high, and only possible on open maps** |

Micro variants are still worth generating: they are free once the machinery
exists, and they keep a rotation from feeling like two maps. But the reason to
build any of this is the third row.

So the first job of the analysis is not placement. It is **finding which maps
are open enough to be worth re-routing**, and how much.

A local corpus already exists to work against: `steamapps/workshop/content/222880`
holds **34** loose `.bsp` files, **29** of them with a shipped `.nav`. The five
without are `tell_open_coop`, `market_open_coop`, `market_night_coop`,
`tell_night_coop` and `sinjar_night_coop` — and the first two being *open*
reworkings of stock maps is exactly the wrong luck, since those are the obvious
first targets. It is also the sharpest argument for surveying in game: for
those five there is no mesh on disk to parse at any quality, and the server
generates one at load.

---

## 2. Why runtime rather than a patched BSP

`docs/spawns-and-objectives.md` patches the entity lump in the file. Everything
it does is available in-process instead:

| | patched `.bsp` | plugin |
|---|---|---|
| client must download | **yes** — the whole map, and it must match byte for byte | no, clients join stock |
| server | vanilla | needs Metamod:Source + SourceMod |
| variants per map | one file per variant | any number, chosen at load |
| placement validation | restart the server and read its verdicts | `TR_TraceHull` in-process, at map start |
| workshop maps | a patched copy diverges from the workshop original | original is untouched |

The last two rows are what make the whole idea practical. A workshop map you
patch is a fork you now maintain against its author's updates; a workshop map
you rewrite at load is still the subscriber's map. And the four-restart
placement loop documented in §6 of the other document exists only because
nothing offline could predict the engine's hull test — in-process, that test is
just a function call.

The trade is real and worth stating plainly: this route requires a modded
server, which the other document deliberately avoided.

---

## 3. Openness — the measurement that decides which maps are worth it

Everything runs on the nav-area graph, treated as the map's real topology.
Given an attacker spawn `A` and a candidate objective site `O`:

| metric | definition | what it tells you |
|---|---|---|
| route count | k-shortest paths under an area-disjointness constraint | how many ways in there actually are |
| detour ratio | length of route 2 ÷ length of route 1 | how *viable* the alternative is; near 1.0 means genuinely parallel, 2.0 means nobody will take it |
| chokepoint count | min-cut between `A` and `O` on the area graph | 1 means a corridor; ≥3 means a compound |
| corridor breadth | width distribution of areas along each route | flanking room vs. a hallway |
| layering | z-spread and indoor fraction of the reachable set | rooftops and interiors, or one ground plane |
| detour-to-straight ratio | path distance ÷ straight-line distance | how much the map bends; a proxy for "maze" vs "field" |

A map's **variant capacity** is then not a property of one placement but of the
*set* of placements it admits: generate candidate layouts and measure how
different they are from each other — Jaccard overlap of the area sets their
routes traverse. Two layouts that push players through the same 400 areas are
one layout with the furniture moved.

That gives the survey a job it can do across a whole workshop collection: rank
subscriptions by how many distinct high-quality layouts they support, and spend
effort on the ones at the top. It also gives an honest floor — a map whose every
candidate objective has route count 1 gets micro-variants and nothing more, and
that should be reported, not papered over.

**Calibration comes from the corpus.** The 49 shipped maps are 49 labeled
positive examples: NWI's own objective and spawn placements, measured in these
exact units. "Is this layout plausible" becomes distribution matching against
them rather than invented thresholds — the same argument the project README
makes for geometry, applied to topology.

---

## 4. Three stages

The split follows one rule: **the engine owns the ground truth, offline owns the
thinking, the plugin owns nothing it can get wrong.**

### A. Survey — in game, once per map

A plugin that dumps engine state to JSON and advances the map. Not gameplay
code; a data exporter. Per map it writes:

- **Nav areas** walked from `TheNavMesh`: id, corners, centre z, `m_insFlags`,
  and `m_connect[4]` with connection lengths. This is the engine's own graph, so
  the parser truncation documented in §4 of the other document
  (`EOF at area 3102/3128`, a graph that splits into 2379/533/178 components and
  cannot reach five of six shipped objectives) simply does not arise. The walk
  already exists in `smartbots_navspawn.sp`.
- **Hull validity**, sampled: `TR_TraceHull` with the player hull at each area
  centre and a few interior offsets. This is the same test as
  `CINSRules::IsSpawnPointValid`, so it sees `func_detail` and static props —
  the three offline predictors that all failed in §4 are replaced by the oracle
  itself, as a column in a table.
- **Visibility**: `CNavArea::IsPotentiallyVisible` over area pairs, packed as a
  bitmap. ~3,100 areas is ~9.6M pairs, ~1.2 MB. Already wired in the
  `smartbots_navspawn` gamedata.
- **Entity inventory**: every `ins_spawnzone`, `ins_blockzone`,
  `trigger_capture_zone`, `ins_spawnpoint`, cache and control point, each with
  its `model` key **and that model's mins/maxs**. The bounds table is what makes
  the model-swap trick (§8) a deliberate choice rather than a guess.

Run it over a whole collection with a `changelevel` loop and a survey cvar. One
batch per map version, then the analysis never needs a server again.

### B. Generate — offline, Python, in `mapmaker`

With exact areas, exact connections, exact validity and exact visibility, the
interesting work is finally unobstructed:

1. **Score the map** for openness (§3). Decide what class of variant it can support.
2. **Site objectives.** Candidate areas filtered by hull validity and footprint,
   scored by route count and detour ratio from each plausible attacker spawn,
   and by how much of the map each choice brings into play.
3. **Site spawns.** Defenders within a path-distance band of the objective —
   *path* distance, not straight-line, which is the number the shipped
   convention was always about; then filtered for hull validity, scored for
   angular coverage around the objective, indoor fraction, and visibility to the
   approach corridor rather than to the map at large. Attackers at a
   corpus-calibrated approach length.
4. **Select for diversity, not just quality.** Emit K presets chosen to maximise
   pairwise route dissimilarity subject to each one clearing the plausibility
   bar. A rotation of five near-identical good layouts is worth less than three
   different ones.
5. **Emit presets** with their metrics attached, so a layout that plays badly can
   be traced back to the numbers that produced it.

### C. Apply — plugin, deliberately dumb

A plugin can rewrite the entity lump before any entity is created.

An earlier version of this section named the wrong mechanism. It cited

```sourcepawn
// insurgency/addons/sourcemod/scripting/include/sdkhooks.inc:376
forward Action OnLevelInit(const char[] mapName, char mapEntities[2097152]);
```

which on **SourceMod 1.11 is deprecated and its buffer is documented "Unused,
always empty"** — a plugin written against it compiles, runs, and silently
changes nothing. The signature survives in older SM trees, which is where that
citation came from.

The current API is better than the one it replaced: the **EntityLump natives**,
writable during `OnMapInit`, hand over the lump already parsed into key/value
entries.

```sourcepawn
// entitylump.inc — write operations are only allowed during OnMapInit
EntityLumpEntry entry = EntityLump.Get(i);
int idx = entry.FindKey("origin");
entry.Update(idx, NULL_STRING, "2252 -1024 88");
```

No 2 MB string surgery, no re-finding block boundaries after an edit changes a
value's length. The effect is the one this section always claimed: the map
loads as though it had shipped that way — nothing downstream can observe the
difference, so no ordering hazards. Objectives that live in `maps/<name>.txt` (caches and their
control points) are moved after they spawn instead;
`CObjWeaponCache::Teleport` is overridden in the server binary and fixes up the
cache's own trigger, so the game already supports moving them. One `.txt` then
serves every variant.

Two things the plugin must do beyond applying the file:

- **Gate.** Trace-hull every point as it is written. Drop failures; if a preset
  loses more than a handful, fall back to stock and log loudly. Presets go stale
  when a workshop author updates a map — the gate is what makes stale mean
  "reverted", never "broken".
- **Announce.** Log the preset name at load, so a demo, a complaint or a
  playtest note is traceable to a layout.

---

## 5. Preset format

```jsonc
{
  "map": "some_workshop_map",
  "survey_hash": "…",              // map + nav checksum; refuse to apply on mismatch
  "presets": [
    {
      "name": "west-compound-3-approach",
      "metrics": {                  // why the generator chose this
        "attacker_path": 1880,      // path distance, not straight line
        "defender_median": 420,
        "route_count": 3,
        "detour_ratio": 1.18,
        "route_overlap_with": { "north-street-flank": 0.21 }
      },
      "entities": [
        { "class": "ins_spawnzone",  "target": "spawnzone_1", "team": 3, "origin": [x, y, z] },
        { "class": "ins_spawnpoint", "team": 3, "origins": [[x, y, z], [x, y, z]] },
        { "class": "ins_blockzone",  "target": "bz_ins_1", "start_disabled": 1 }
      ],
      "objectives": [
        { "name": "cache_a", "origin": [x, y, z], "cp_offset": 72 }
      ]
    }
  ]
}
```

`cp_offset` is the shipped +72 convention for the control point marker above a
cache.

---

## 6. Rotation policy

- **Round-robin, not random.** Persist the last index per map in a data file.
  Players notice repeats far more than they notice a rotation.
- **The control is not one of the members.** Every generated file opens with a
  `stock` preset and it used to come round like any other, so one load in N of
  every map was spent on the map as it ships — on a two-preset map like
  bombshelter, every other load. That was right when the rotation was the only
  way to reach the control and it is not now: the boot load of every session is
  stock, `sm_layout off` and `--stock` turn the applier off, and `sm_layout
  stock` pins it for one map. The entry stays in the file, because it carries
  the stock round's own metrics — what every gate measures a layout against —
  and because a file holding nothing else is a map with no layout, where the
  applier falls back to it and says so.
- `sm_layout <name>` to pin one, for testing and for a server that wants a fixed
  set.
- Advance the index only on a completed round, so a map that is restarted or
  voted off does not burn a variant.
- **Be able to say which one it is, without saying it unasked.** A permuted map
  is the same map — same geometry, same name on the scoreboard, same objectives
  in the same HUD order — so a player who has just had a bad round has nothing
  to put in a report unless they can ask. `sm_preset` — `!preset` in chat,
  ungated, since it reports and changes nothing — answers: the preset's name,
  which of N in the file it is, how many edits it made, and whether any of its
  rules matched nothing, which is what "stale preset" looks like from inside a
  round. It is not announced on load, because a player told at round start that
  this is walk 3 of 16 has been handed the two facts the permutation exists to
  withhold — that the map is not the one it looks like, and how many others
  there are — before playing a second of it. `mm_layout_announce 1` pushes the
  line to chat once per map and once to each player who joins, for a test that
  wants it named without anyone asking.
- **Apply in checkpoint and nowhere else.** A preset permutes an objective
  chain, and the mode is not a property of the .bsp: the same `_coop` map is in
  a rotation under conquer as well, and hunt, survival and outpost run on these
  maps too. The map name cannot tell them apart, so the applier reads
  `mp_gamemode` at map init — the only moment the lump is still writable, and
  late enough that the cvar already holds the incoming map's mode — and a map
  loading as anything but checkpoint is left as it ships, down to the
  restricted areas and the cache teleports. A server can therefore rotate
  through both without the plugin being a reason to split it in two.
- **And be able to turn it off.** `mm_layout_enabled 0`, or `sm_layout off`,
  which sets the same cvar. It can only ever mean *from the next load*: the
  lump is rewritten before any entity exists, so a map that loaded with a
  layout has no un-layouted state to return to, and the live half — the burst
  that re-teleports caches each round — would if it stopped leave the caches on
  their `.txt` coordinates with every spawn zone still permuted, which is worse
  than either end. So the running map keeps what it loaded with, and the change
  hook says so instead of leaving someone to flip the switch and watch nothing
  happen. `tools/layout-server.sh --stock` boots that way, which is the control
  a judgement about a layout is made against.
- **A server config is executed on every map load, so the console has to
  outrank it.** Measured 2026-09-19: on a `--stock` server, `sm_layout on` was
  read correctly by the next `OnMapInit` — which runs before the config — and
  the config exec behind it set the cvar back to 0, so exactly one load got a
  layout. `OnConfigsExecuted` now re-asserts whatever the console last asked
  for. It is the same precedence a pin has always had over the preset file,
  applied to *whether* instead of to *which*.
- Rotate at map load only. Moving objectives mid-round is possible but is a
  different feature with its own failure modes; leave it out of the first build.

---

## 7. What the engine gives us

Established by reading `server_srv.so` (32-bit, **unstripped** — so Linux
gamedata is `@`-symbol lookups, as in the existing `smartbots_navspawn.txt`):

| symbol / fact | what it proves |
|---|---|
| the `EntityLump` natives in `entitylump.inc`, writable during `OnMapInit` | the entity lump can be rewritten in memory at load — the whole of §2 of the other document, without touching a file. **Not** `OnLevelInit`: see §4C |
| `CINSRules::ValidateSpawnpoints()`, called from `LevelInitPostEntity` | it iterates teams 2–3, calls `IsSpawnPointValid`, `Msg`s each failure and `Warning`s at the end — and **removes nothing**. It is a diagnostic pass; rejection bites at selection time |
| `CINSSpawnZone::PointInSpawnZone(Vector, int)` | spawn point → zone binding is evaluated live, not cached at load, so a moved zone re-binds |
| `CINSNavArea::AssociateWithSpawnZone`, called from `CINSSpawnZone::Enable(bool)` | nav ↔ spawn-zone association is rebuilt when a stage enables its zone |
| `CINSNavMesh::DecorateMesh()` ← `RecomputeInternalData()` ← `OnServerActivate` / `OnRoundRestart` / `Update` | objective and control-point decoration of the mesh re-runs; runtime moves are not stranded against a load-time snapshot |
| `CObjWeaponCache::Teleport` (overridden; also `CreateTrigger` / `RemoveTrigger`) | caches are designed to move, trigger included |
| `CNavArea::IsPotentiallyVisible`, `CNavMesh::GetNearestNavArea` | already resolved in `smartbots_navspawn` gamedata — the survey's visibility and snapping come for free |

---

## 8. Limits that survive the move to runtime

- **Brush volumes translate; they do not resize.** Same as the file route. The
  one escape is swapping an entity's `model` key to borrow another brush's
  dimensions — which is why the survey exports model bounds. This is what the
  `bz_ins_1` sizing problem in §6 of the other document needed.
- **World geometry is untouched.** No new doors, no new holes. Re-routing means
  using routes that exist, not making them.
- **Objective chain order** still lives in `maps/<name>.txt`. Objectives can be
  *moved* by the plugin; re-ordering the chain is a text edit, and is per-map,
  not per-preset, unless a way to override the chain server-side is found.
  **This is less binding than it looks.** A reversal does not need the chain
  reordered: leave it reading 1..N and permute the ground under it, so slot 1 is
  fought where slot N was. `docs/reverse.md` is that operation, and it is the
  first layout variant built. Any permutation of the chain is reachable the same
  way; what stays out of reach is changing the chain's *length*.
- **Workshop churn.** Presets are keyed to a map + nav hash and refuse to apply
  on mismatch. An updated map means a re-survey.

---

## 9. Open questions

- Do clients need `maps/<name>.txt`, or is the cpsetup purely server-side? Open
  in the other document too; it decides whether objective edits are ever
  client-visible.
- Whether a shipped `.nav` needs anything done to it at all. Some workshop
  items carry none of their own — but a cpsetup's `navfile` key names the mesh
  to load, and `tell_open_coop` plays on `tell.nav` while `market_open_coop`
  plays on `market.nav`, both shipped. `drycanal_open_coop` names a file that
  does not exist, so that mesh is generated server-side, and what the server
  does in that case is still worth confirming: it is also the answer for any map
  whose mesh turns out to be thin.
- Cost of the visibility export on large maps, and whether area-pair granularity
  is enough or route-corridor sampling is needed. `mm_survey_vis` is off by
  default until someone times it.
- Whether `@TheNavAreas` resolves against this binary. If it does the survey
  enumerates the whole mesh; if not it floods from spawn entities and sees only
  the reachable component. The survey records which in `"enumeration"`.
- Where `CNavArea::m_nwCorner` / `m_seCorner` actually live. `m_center` is
  verified at +44; the corners are probed at runtime against the identity
  `center.xy == midpoint(nw.xy, se.xy)` and the discovered offsets logged.
  Until they are pinned, footprint-derived metrics — corridor breadth above all
  — have nothing to run on.
- How many distinct presets a map needs before rotation stops feeling repetitive
  — likely fewer than it seems, if diversity is selected for rather than counted.
- Whether the openness metrics separate the corpus the way they should: the 49
  shipped maps should score *low*, and a known-open workshop map high. If they
  do not, the metric is wrong, not the maps. This is the same sanity check §4 of
  the other document insists on — point the method at something whose answer is
  already known before trusting it on something new.
