"""Remix a shipped map: cut it, move the pieces, seal and rejoin.

Why this exists alongside the generator: the shipped maps are the only source
of Insurgency's *visuals* that does not need an artist. Their brushwork,
materials, props and prop placement come back out of a `.bsp` intact -
BSPSource returns ministry's 7,411 brushes as 7,411 brushes with their
materials - and a full official map decompiles, recompiles and lights in about
fourteen seconds. So layout can be rearranged on top of art that is already
finished, which is the one thing PLAN.md's box world cannot reach.

What is inherited, and what is not:

- **Inherited exactly.** Brush geometry, materials, displacement meshes, props,
  overlays, lights, soundscapes, the entity setup.
- **Inherited approximately.** Texture *alignment*: BSPSource recovers 28.5% of
  brushsides' original alignment exactly and reconstructs the rest. Moving a
  half keeps whatever it recovered - `blocks.translate` corrects each face's
  texture shift for the move - but it cannot improve on it.
- **Not inherited.** Baked lighting. Lightmaps are recompiled by vrad, per-prop
  vertex lighting comes back only from a full vrad pass, and cubemaps are baked
  by the game client, which is not part of this toolchain. `cubemaps` copies
  the shipped ones back in for the geometry that did not move.

The modules: `blocks` is geometry on parsed VMF blocks, `cut` is the splice,
`pick` chooses where to rejoin from bspscout's walkable graph, and `cubemaps`
carries the baked reflections across.
"""
from __future__ import annotations

from . import blocks, cubemaps, pick
from .cut import Plan, Report, splice

__all__ = ["Plan", "Report", "blocks", "cubemaps", "pick", "splice"]
