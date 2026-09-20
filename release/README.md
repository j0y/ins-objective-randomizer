# release/ — the copy-to-a-server payload

Everything here but this file is generated. `tools/build-plugins.sh` writes `addons/` and
`BUILD.txt`; `tools/make-presets.sh` writes `presets/`. Edit the sources, not
these files.

This directory mirrors an `insurgency/` directory, so installing is a copy:

```bash
cp -r release/addons release/presets  /path/to/insurgency/
```

It needs **Metamod:Source and SourceMod already installed** on the server; it
ships neither.

| | |
|---|---|
| `addons/sourcemod/plugins/mapmaker_layout.smx` | auto-loads. Rewrites objective and spawn placement at map init, rotating a map's presets. |
| `addons/sourcemod/plugins/disabled/mapmaker_survey.smx` | **not** loaded. Only wanted for a survey run — it changelevels the server when it is done. `sm plugins load disabled/mapmaker_survey` to use it. |
| `addons/sourcemod/gamedata/mapmaker.txt` | the nav-mesh symbol lookups both plugins need. |
| `presets/<map>.cfg` | one file per map. The layout plugin reads `presets/<GetCurrentMap()>.cfg`; a map with no file here loads stock. |
| `BUILD.txt` | which sources the committed `.smx` files were built from. |

## Checking it works

Nothing is announced on load, by design. Ask:

```
sm_preset          # in chat as !preset - which layout this map loaded, or why it is stock
sm_layout list     # admin - the presets in this map's file, active one marked
```

A layout takes effect on the **next** load: SourceMod runs the server config
after the first map is already up, so the map a server boots on is always
stock. One `changelevel` gets you a layout.

## Defaults worth knowing

`mm_layout_enabled 1` is the only cvar you need. The rest default sensibly;
`plugin/README.md` in the repo documents all of them. Two that surprise people:

- **`mm_layout_announce 0`** — the applier logs its line and waits to be asked.
  A player told at round start that this is walk 3 of 16 has been handed both
  facts the permutation exists to withhold.
- **`mm_layout_blockzones 0`** — the map's own restricted areas are dropped.
  A restricted area holds attackers behind the objective, and moving the ground
  under the chain moves which side that is. Set `1` to keep them.

**Checkpoint only, and it checks.** A preset is a permutation of a checkpoint
objective chain. The applier reads `mp_gamemode` at map init and leaves hunt,
survival, outpost and conquer exactly as they ship — no lump edit, no rotation
slot spent.
