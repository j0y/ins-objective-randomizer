# bspscout

> Part of the `mapmaker` workspace. Run it via the tasks in the repo root
> (`mise run scout`, `BSP=... mise run info`) — the per-package `mise.toml` was
> dropped in favour of the workspace one. Direct `python -m bspscout ...` still
> works as documented below.

Reads a Source-engine `.bsp` and answers two questions about a map you cannot
open in Hammer right now:

* **What does it look like?** — top-down renders with a world-coordinate grid,
  a height map relative to a single ground datum, and one floor plan per storey.
* **Where can a bot actually go?** — a walkable-surface graph built from real
  collision geometry, flood-filled from a seed, with sealed-off areas called out.

On top of that it proposes a **sequential capture-point chain** for Insurgency
(`ins_objective`), sited on enclosed, multi-entrance interiors and spaced by
real walking distance.

## Usage

```bash
mise run setup                     # venv + deps
mise run info                      # lumps, entities, bounds
mise run all                       # build cache -> render -> objectives
```

Or directly:

```bash
python -m bspscout build map.bsp --res 16 --out out/nav.npz
python -m bspscout render out/nav.npz --outdir out
python -m bspscout slice out/nav.npz --agl 40      # torso-level cut
python -m bspscout objectives out/nav.npz --bsp map.bsp --count 6
```

Useful flags: `--ground Z` to force the ground datum, `--seed x,y,z` to
flood-fill from a specific spot, `--start/--end x,y` to set the insertion and
extraction ends, `--max-leg` to cap the walk between objectives.

## How it works

1. **Parse** the lump directory: planes, nodes, leaves, faces, brushes,
   brushsides, displacements, entities, and the `sprp` static-prop game lump.
2. **Collision geometry**, not drawn faces: every brush side is clipped against
   its own brush to recover the real polygon, so nodraw greybox and clip
   brushes count. Displacements are tessellated from `DISP_VERTS`.
3. **Rasterise** upward-facing surfaces into a grid, keeping every floor layer
   stacked in a cell, then test player clearance with point traces through the
   BSP tree (crouch, stand, and hull width).
4. **Graph**: 8-connected, `STEP_HEIGHT` walk edges both ways, one-way drops and
   jump-ups, plus ladders, teleports and static-prop staircases.
5. **Analyse**: flood fill, cover detection, footprint labelling, and a greedy
   route that walks the map picking the best defensible place one leg at a time.

## Known limits

* **Static props do not collide.** They are not in the BSP tree and this tool
  does not load `.mdl` files. Prop fences, containers and rubble will not block
  a bot here even though they do in game. Prop staircases and ladders are the
  exception - those are linked explicitly by model name.
* Brush entities that move (doors, elevators) are treated as open.
* `func_areaportal`, occluders and triggers are ignored for movement.
* Naming comes from static-prop model names. A name ending in `?` was guessed
  from a reused façade kit, not from a sign, and should be treated as a hint.
