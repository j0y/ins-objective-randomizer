"""Cut a map in two along a plane, move one half, and rejoin them.

The test this exists for: splice an official map with *itself*. Both halves are
known-good Insurgency geometry, so anything that breaks - a leak, a texture
that slid, a displacement that lost its face - is the splice's fault and not
the map's. Once that round trip holds, splicing halves from two different maps
is the same operation with a different second argument.

How the seal works, and why it cannot leak by construction:

    half A (unmoved)          junction            half B (moved by gap)
    ...........####|                        |####...........
    ...........####| <- wall A     wall B -> |####...........
    ...........####|====== hollow box =======|####...........
                   ^ x = at        x = at + gap ^

Every world solid that crosses the plane is *cut* rather than assigned, so each
half keeps the part of the map's sealing shell that was on its side. That
leaves both halves open across the whole cross-section, which is what the two
walls cover - each spans its half's full extent in the other two axes, with one
opening. The junction is a hollow box between them, missing exactly the two
faces the walls provide.

So the only new leak risk is the junction, which is six axis-aligned brushes
from `shell.hollow_box`, and openings tiled by `shell.slab_with_opening` - both
of which already prove their own tiling.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..vmf import Solid, hollow_box, slab_with_opening
from ..vmf.kv import Block
from ..vmf.read import children, entities, get, max_id, world
from . import blocks as B

# In-plane axes for each cut axis, in the order a door rectangle names them.
IN_PLANE = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}

WALL_MATERIAL = "maps/ministry/ministry_bare_wall_02"
FLOOR_MATERIAL = "maps/ramadi/concretefloor_01"

# vbsp refuses geometry beyond this, and a seal wall spans a whole half.
WORLD_LIMIT = 16380.0
# A seal wall is thousands of units across. At the writer's default of 16 that
# is a million luxels on one face; the wall is a backdrop, so light it coarsely.
SEAL_LIGHTMAP = 128


@dataclass
class Plan:
    axis: str = "x"
    at: float = 0.0
    gap: float = 1024.0
    slide: float = 0.0
    doors: list[tuple[float, float, float, float]] = field(
        default_factory=lambda: [(-256.0, 0.0, 256.0, 128.0)])
    thickness: float = 64.0
    material: str = WALL_MATERIAL
    floor_material: str = FLOOR_MATERIAL

    @property
    def delta(self) -> tuple[float, float, float]:
        """Where half B goes: `gap` across the cut, `slide` along it."""
        d = [0.0, 0.0, 0.0]
        d[B.AXIS_INDEX[self.axis]] = float(self.gap)
        d[IN_PLANE[self.axis][0]] = float(self.slide)
        return tuple(d)


@dataclass
class Report:
    kept: int = 0
    moved: int = 0
    cut: int = 0
    uncut_displacements: int = 0
    entities_kept: int = 0
    entities_moved: int = 0
    entities_split: int = 0
    pivots_fixed: int = 0
    seal_solids: int = 0
    buried: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"world solids: {self.kept} stayed, {self.moved} moved, {self.cut} cut in two",
            f"  {self.uncut_displacements} displacement solids moved whole (a dispinfo cannot be cut)",
            f"entities: {self.entities_kept} stayed, {self.entities_moved} moved, "
            f"{self.entities_split} split across both halves",
            f"  {self.pivots_fixed} brush-entity pivots pulled back onto their own brushes",
            f"seal: {self.seal_solids} new solids (two walls and the junction)",
        ] + [f"  ! {b}" for b in self.buried]


def _split_solids(solid_blocks, plan: Plan, ids, report: Report):
    """Sort solids into the two halves, cutting the ones that cross the plane.

    Returns `(low, high, moved_ids)`. Solids that stay whole keep their ids so
    that an `info_overlay` naming their sides still finds them; the high copy of
    a cut solid cannot, so it is renumbered and the mapping reported.
    """
    i = B.AXIS_INDEX[plan.axis]
    low, high, moved_ids = [], [], {}
    for solid in solid_blocks:
        pts = B.solid_points(solid)
        if not pts:
            low.append(solid)
            continue
        if B.straddles(solid, plan.axis, plan.at):
            if B.has_displacement(solid):
                report.uncut_displacements += 1
            else:
                low.append(B.cut_solid(solid, plan.axis, plan.at, True, ids, plan.material))
                hi = B.cut_solid(solid, plan.axis, plan.at, False, ids, plan.material)
                moved_ids.update(B.renumber(hi, ids))
                high.append(hi)
                report.cut += 1
                continue
        (low if B.centroid(pts)[i] < plan.at else high).append(solid)
    return low, high, moved_ids


def _split_entity(entity: Block, plan: Plan, ids, report: Report):
    """One entity in, one or two out - `(low, high, moved_ids)`, either may be None.

    A brush entity whose solids fall either side of the plane becomes two
    entities, each with its own volume. That is more faithful than sending the
    whole thing one way: a `func_detail` wall panel spanning the cut would
    otherwise teleport out of the facade it belongs to.
    """
    solids = children(entity, "solid")
    if not solids:
        origin = get(entity, "origin")
        pt = None
        if origin:
            try:
                pt = [float(c) for c in origin.split()[:3]]
            except ValueError:
                pt = None
        i = B.AXIS_INDEX[plan.axis]
        if pt is not None and pt[i] >= plan.at:
            return None, entity, {}
        return entity, None, {}

    low, high, moved_ids = _split_solids(solids, plan, ids, report)
    others = [c for c in entity.children if c.name != "solid"]

    def with_solids(sol):
        out = copy.deepcopy(entity)
        out.children = [copy.deepcopy(c) for c in sol] + [copy.deepcopy(c) for c in others]
        return out

    if low and high:
        a, b = with_solids(low), with_solids(high)
        moved_ids.update(B.renumber(b, ids))
        return a, b, moved_ids
    if high:
        return None, with_solids(high), moved_ids
    return with_solids(low), None, moved_ids


def _seal(plan: Plan, low_bounds, high_bounds, ids) -> list[Solid]:
    """The two walls, one opening per join, and a junction behind each.

    Each wall spans its own half's extent in the two in-plane axes, expanded by
    the wall thickness, so it covers the whole open cross-section however ragged
    the cut left it.

    Several openings in one wall stay a partition rather than a subtraction by
    splitting the wall into strips - one per door, divided at the midpoints
    between them - and giving each strip to `slab_with_opening`, which proves
    its own tiling. One door per strip, so no strip needs two holes.

    Why more than one join: ministry's cut at x=512 is crossed by the walkable
    graph in four separate places. Sealing it and opening a single door left
    only 19,285 of the moved half's 153,974 standable cells reachable - the
    other four fifths of the map was still there, still lit, and unreachable.
    A join per crossing is what keeps a spliced map playable.
    """
    i = B.AXIS_INDEX[plan.axis]
    a, b = IN_PLANE[plan.axis]
    t = float(plan.thickness)
    slide = float(plan.slide)
    doors = sorted((tuple(float(c) for c in d) for d in plan.doors), key=lambda d: d[0])

    def wall(at_lo, at_hi, half_bounds, wall_doors):
        lo, hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        lo[i], hi[i] = at_lo, at_hi
        for j in (a, b):
            lo[j] = max(half_bounds[0][j] - t, -WORLD_LIMIT)
            hi[j] = min(half_bounds[1][j] + t, WORLD_LIMIT)
        # Strip boundaries: the wall's own edges, and the midpoint between each
        # neighbouring pair of doors.
        edges = [lo[a]]
        for first, second in zip(wall_doors, wall_doors[1:]):
            edges.append((first[2] + second[0]) / 2.0)
        edges.append(hi[a])
        out = []
        for door, (s_lo, s_hi) in zip(wall_doors, zip(edges, edges[1:])):
            strip_lo, strip_hi = list(lo), list(hi)
            strip_lo[a], strip_hi[a] = s_lo, s_hi
            if strip_hi[a] - strip_lo[a] < 1.0:
                continue
            h_lo, h_hi = list(strip_lo), list(strip_hi)
            h_lo[a], h_hi[a] = door[0], door[2]
            h_lo[b], h_hi[b] = door[1], door[3]
            out += slab_with_opening(strip_lo, strip_hi, h_lo, h_hi, plan.material)
        return out

    doors_a = doors
    doors_b = [(d[0] + slide, d[1], d[2] + slide, d[3]) for d in doors]
    out = wall(plan.at, plan.at + t, low_bounds, doors_a)
    out += wall(plan.at + plan.gap - t, plan.at + plan.gap, high_bounds, doors_b)

    # A junction per join, each big enough in-plane to hold both of its doors.
    omit = {0: ("-x", "+x"), 1: ("-y", "+y"), 2: ("floor", "ceiling")}[i]
    for door_a, door_b in zip(doors_a, doors_b):
        j_lo, j_hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        j_lo[i], j_hi[i] = plan.at, plan.at + plan.gap
        j_lo[a], j_hi[a] = min(door_a[0], door_b[0]) - t, max(door_a[2], door_b[2]) + t
        j_lo[b], j_hi[b] = min(door_a[1], door_b[1]) - t, max(door_a[3], door_b[3]) + t
        out += hollow_box(
            j_lo, j_hi, t, plan.material,
            materials={"floor": plan.floor_material}, omit=omit,
        )
    for solid in out:
        for side in solid.sides:
            side.lightmapscale = SEAL_LIGHTMAP
    return out


def splice(doc: list[Block], plan: Plan) -> tuple[list[Block], Report]:
    """Halve `doc` at the plane, move one half, seal both, join them.

    `doc` is the block tree from `vmf.read`; the result is a new tree ready to
    write. The input is not modified.
    """
    doc = copy.deepcopy(doc)
    ids = B.ids_from(max_id(doc))
    report = Report()
    w = world(doc)

    low, high, moved_ids = _split_solids(children(w, "solid"), plan, ids, report)
    report.kept, report.moved = len(low), len(high)

    low_ents, high_ents = [], []
    for e in entities(doc):
        a, b, moved = _split_entity(e, plan, ids, report)
        moved_ids.update(moved)
        if a is not None:
            low_ents.append(a)
        if b is not None:
            high_ents.append(b)
        if a is not None and b is not None:
            report.entities_split += 1
        elif b is not None:
            report.entities_moved += 1
        else:
            report.entities_kept += 1

    for solid in high:
        B.translate(solid, plan.delta)
    for e in high_ents:
        B.remap_sides(e, moved_ids)
        B.translate(e, plan.delta)
    # After the move, not before: a pivot is only stranded once its brushes
    # have gone somewhere it did not.
    report.pivots_fixed = sum(B.fix_pivot(e) for e in low_ents + high_ents)

    # Exact corners, not plane points: a cut solid's planes still describe the
    # brush before the cut, so the seal would be sized to the uncut map.
    lb = _extent(low)
    hb = _extent(high)
    seal = _seal(plan, lb, hb, ids)
    report.seal_solids = len(seal)
    report.buried = _buried(seal, low_ents + high_ents)

    keep = [c for c in w.children if c.name != "solid"]
    w.children = low + high + [s.block(ids) for s in seal] + keep
    rest = [b for b in doc if b.name not in ("world", "entity")]
    return [w] + low_ents + high_ents + rest, report


def _extent(solids: list[Block]):
    boxes = [b for b in (B.solid_extent(s) for s in solids) if b]
    if not boxes:
        raise ValueError("a half came out empty - is the cut plane outside the map?")
    return (tuple(min(x[0][i] for x in boxes) for i in range(3)),
            tuple(max(x[1][i] for x in boxes) for i in range(3)))


def _buried(seal: list[Solid], ents: list[Block]) -> list[str]:
    """Entities that ended up inside the new geometry - vbsp calls that a leak.

    The same check `Vmf.problems()` runs, aimed at the only brushes here that
    were not in the shipped map: an entity that happens to sit where a seal
    wall now is would otherwise come back as a leak pointing at the wall.
    """
    out = []
    for e in ents:
        origin = get(e, "origin")
        if not origin or children(e, "solid"):
            continue  # a brush entity's origin is a pivot, not a position in space
        try:
            pt = [float(c) for c in origin.split()[:3]]
        except ValueError:
            continue
        for s in seal:
            if s.contains(pt):
                out.append(f"{get(e, 'classname', '?')} at {origin} is inside a seal brush")
                break
    return out
