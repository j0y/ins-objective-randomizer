"""The .vmf document: worldspawn, its solids, and the entities.

Ids are handed out at write time by one counter walking the tree, which is what
Hammer does and what keeps a generated file diffable against a hand-made one:
world is 1, then each solid takes an id before its own sides, then the entities.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

from .brush import Solid
from .kv import EDITOR_ENTITY, Block, editor

# Source clamps the world to +/-16384 on each axis; vbsp refuses beyond it.
MAP_LIMIT = 16384

WORLD_DEFAULTS = {
    "skyname": "sky_insurgency04",
    "maxpropscreenwidth": -1,
    "detailvbsp": "detail.vbsp",
    "detailmaterial": "detail/detailsprites",
}


@dataclass
class Entity:
    classname: str
    keys: dict = field(default_factory=dict)
    solids: list[Solid] = field(default_factory=list)

    def block(self, ids) -> Block:
        b = Block("entity").set("id", next(ids))
        b.set("classname", self.classname)
        b.update(self.keys)
        for s in self.solids:
            b.add(s.block(ids))
        b.add(editor(EDITOR_ENTITY))
        return b


class Vmf:
    """Accumulate world brushes and entities, then render the whole file."""

    def __init__(self, grid: int = 64, **world_keys):
        self.world = dict(WORLD_DEFAULTS) | world_keys
        self.grid = grid
        self.solids: list[Solid] = []
        self.entities: list[Entity] = []

    # ------------------------------------------------------------ building
    def add(self, solids) -> Vmf:
        """Add one solid or any iterable of them to the world."""
        self.solids.extend([solids] if isinstance(solids, Solid) else list(solids))
        return self

    def entity(self, classname: str, solids=None, **keys) -> Entity:
        e = Entity(classname, keys, [] if solids is None else list(solids))
        self.entities.append(e)
        return e

    def light(self, origin, brightness=400, colour=(255, 255, 255)) -> Entity:
        return self.entity(
            "light",
            _light=(*colour, brightness),
            _fifty_percent_distance=0,
            _zero_percent_distance=0,
            origin=origin,
        )

    def player_start(self, origin, yaw: float = 0.0) -> Entity:
        return self.entity("info_player_start", angles=(0, yaw, 0), origin=origin)

    # ------------------------------------------------------------ checking
    def bounds(self):
        pts = [p for s in self.all_solids() for p in s.points()]
        if not pts:
            return None
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi

    def all_solids(self):
        yield from self.solids
        for e in self.entities:
            yield from e.solids

    def problems(self) -> list[str]:
        """Cheap checks worth running before a compile that costs minutes.

        Deliberately not a leak test - only vbsp can tell you that. These are
        the mistakes that produce a *confusing* leak: a brush off the grid by a
        fraction, or geometry outside the world vbsp will build.
        """
        out = []
        for i, s in enumerate(self.all_solids()):
            out += [f"solid {i}: {p}" for p in s.problems()]
            for p in s.points():
                if max(abs(c) for c in p) > MAP_LIMIT:
                    out.append(f"solid {i}: point {p} is outside the +/-{MAP_LIMIT} world")
                    break
            for p in s.points():
                if any(abs(c - round(c)) > 1e-6 for c in p):
                    out.append(f"solid {i}: point {p} is not on an integer coordinate")
                    break
        # An entity inside world geometry leaks the map as surely as a gap
        # does, and vbsp's report ("Entity ... outside the world") points at
        # the symptom rather than the buried entity.
        for e in self.entities:
            origin = e.keys.get("origin")
            if origin is None:
                continue
            for i, s in enumerate(self.solids):
                if s.contains(origin):
                    out.append(f"{e.classname} at {tuple(origin)} is inside solid {i} - vbsp calls that a leak")
                    break
        if not any(e.classname == "info_player_start" for e in self.entities):
            out.append("no info_player_start - vbsp warns and the map cannot be entered")
        return out

    # ------------------------------------------------------------ output
    def blocks(self) -> list[Block]:
        ids = itertools.count(1)
        head = [
            Block("versioninfo").update(
                {
                    "editorversion": 400,
                    "editorbuild": 8456,
                    "mapversion": 1,
                    "formatversion": 100,
                    "prefab": 0,
                }
            ),
            Block("visgroups"),
            Block("viewsettings").update(
                {
                    "bSnapToGrid": 1,
                    "bShowGrid": 1,
                    "nGridSpacing": self.grid,
                    "bShow3DGrid": 0,
                }
            ),
        ]
        world = Block("world").set("id", next(ids))
        world.set("mapversion", 1).set("classname", "worldspawn").update(self.world)
        for s in self.solids:
            world.add(s.block(ids))
        return head + [world] + [e.block(ids) for e in self.entities]

    def dumps(self) -> str:
        return "".join(b.render() for b in self.blocks())

    def write(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dumps())
        return path
