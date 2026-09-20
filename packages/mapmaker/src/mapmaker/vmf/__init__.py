"""Read and write .vmf: solids, sides, displacements, entities.

VMF is nested key-value text; a brush is six or more `side` blocks each
carrying a three-point plane. The hard parts are keeping solids watertight
(a one-unit gap is a leak and vvis refuses) and getting texture axes right.

The split: `kv` renders the text, `brush` makes convex solids with outward
plane windings, `shell` assembles them into partitions that are watertight by
construction, and `doc` is the file. `read` goes the other way, parsing a file
back into `kv`'s own block tree - which is what lets `mapmaker.splice` rearrange
a decompiled official map with the same code that writes a new one.
Displacements are not *written* yet - see PLAN.md step 5; brush terrain comes
first. A decompile's displacements are read, moved and written back out
unchanged, which is a different thing.
"""
from __future__ import annotations

from . import compare, read
from .brush import DEV, NODRAW, SKYBOX, Side, Solid, box, from_faces, prism
from .doc import Entity, Vmf
from .kv import Block
from .shell import doorway, hollow_box, room, slab_with_opening

__all__ = [
    "Block",
    "compare",
    "read",
    "DEV",
    "Entity",
    "NODRAW",
    "SKYBOX",
    "Side",
    "Solid",
    "Vmf",
    "box",
    "doorway",
    "from_faces",
    "hollow_box",
    "prism",
    "room",
    "slab_with_opening",
]
