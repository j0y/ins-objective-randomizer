"""Geometry on parsed VMF blocks: measure, translate, cut.

The operations a splice needs are all cheap in the half-space representation a
VMF already uses. A solid *is* the intersection of its sides' outward planes,
so cutting one with a plane is adding a side - not clipping polygons - and the
result is convex and closed by construction. vbsp computes the windings.

Two things this module is careful about, because both are silent when wrong:

- **Texture shift under translation.** Source reads a face's texture coordinate
  as `(P . U) / scale + shift`, so moving a brush without adjusting `shift`
  slides the texture across it. Every wall in a translated half would be
  visibly misaligned against its neighbours, which is exactly the fidelity a
  splice is supposed to inherit for free.
- **Displacements cannot be cut.** A `dispinfo` block's rows are indexed
  against the face it sits on; cut that face and the mesh no longer maps to it.
  Solids carrying one are moved whole or not at all.
"""
from __future__ import annotations

import copy
import itertools
import re

from ..vmf import NODRAW, Side, Solid, box
from ..vmf.brush import axes_for
from ..vmf.kv import Block
from ..vmf.read import get, walk

PLANE = re.compile(r"\(([^)]*)\) \(([^)]*)\) \(([^)]*)\)")
AXIS = re.compile(r"\[([^\]]*)\] (\S+)")

# World-space points, one vector per key. `BasisOrigin` anchors an info_overlay;
# the corners are how func_breakable_surf records its pane.
POINT_KEYS = ("origin", "BasisOrigin", "upperleft", "upperright", "lowerleft", "lowerright")

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def coord(v) -> str:
    """Format a moved coordinate without losing it.

    `kv.num` writes non-integers with `%g` - six significant digits - which is
    right for a writer whose coordinates are whole numbers on a grid. A shipped
    map is not that: 14.7% of ministry's plane coordinates are fractional and
    they run to five digits before the point, so `%g` rounds 12345.6789 to
    12345.7 and moves the plane by 0.08 units. Small, and exactly the kind of
    drift that turns a watertight wall into a hairline gap.
    """
    f = float(v)
    r = round(f)
    if abs(f - r) < 1e-6:
        return str(int(r))
    return f"{f:.6f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ measuring
def side_points(side: Block) -> list[tuple[float, float, float]]:
    m = PLANE.search(get(side, "plane", ""))
    if not m:
        return []
    return [tuple(float(c) for c in g.split()) for g in m.groups()]


def solid_points(solid: Block) -> list[tuple[float, float, float]]:
    """Every plane-defining point in the solid.

    Not the solid's vertices - three points naming each plane. For deciding
    which side of a cut a brush is on, and for bounding a seal wall, the
    difference does not matter: BSPSource writes actual face corners, so these
    points span the brush.
    """
    return [p for s in solid.children if s.name == "side" for p in side_points(s)]


def bounds(points):
    if not points:
        return None
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    return lo, hi


def centroid(points):
    n = len(points)
    return tuple(sum(p[i] for p in points) / n for i in range(3)) if n else None


def side_plane(side: Block):
    """A side as `(unit outward normal, offset)`, or None if it names no plane."""
    pts = side_points(side)
    if len(pts) != 3:
        return None
    p1, p2, p3 = pts
    u = [p1[i] - p2[i] for i in range(3)]
    v = [p3[i] - p2[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    ln = sum(c * c for c in n) ** 0.5
    if ln < 1e-9:
        return None
    n = [c / ln for c in n]
    return n, sum(n[i] * p1[i] for i in range(3))


def solid_vertices(solid: Block, eps: float = 0.01):
    """The solid's actual corners, from the intersection of its half-spaces.

    Needed because a *cut* solid's plane points lie about its extent: cutting
    adds a side and leaves every other side's three points where they were, so
    the points still describe the brush before the cut. The brush itself is
    correct - it is the intersection - but anything that measures it from those
    points reads the old shape, which is how a pivot that is outside a brush
    passes an inside-the-bounds test and leaks the map.

    Corners come from every triple of planes that meets in a point, kept when
    the point satisfies all the other half-spaces.
    """
    planes = [p for s in solid.children if s.name == "side" for p in (side_plane(s),) if p]
    n = len(planes)
    if n < 4:
        return []
    out = []
    for i in range(n - 2):
        (a, da) = planes[i]
        for j in range(i + 1, n - 1):
            (b, db) = planes[j]
            for k in range(j + 1, n):
                (c, dc) = planes[k]
                det = (a[0] * (b[1] * c[2] - b[2] * c[1])
                       - a[1] * (b[0] * c[2] - b[2] * c[0])
                       + a[2] * (b[0] * c[1] - b[1] * c[0]))
                if abs(det) < 1e-6:
                    continue
                p = (
                    (da * (b[1] * c[2] - b[2] * c[1]) - a[1] * (db * c[2] - dc * b[2])
                     + a[2] * (db * c[1] - dc * b[1])) / det,
                    (-da * (b[0] * c[2] - b[2] * c[0]) + a[0] * (db * c[2] - dc * b[2])
                     - a[2] * (db * c[0] - dc * b[0])) / det,
                    (da * (b[0] * c[1] - b[1] * c[0]) - a[0] * (db * c[1] - dc * b[1])
                     + a[1] * (db * c[0] - dc * b[0])) / det,
                )
                if all(sum(pl[0][m] * p[m] for m in range(3)) <= pl[1] + eps for pl in planes):
                    out.append(p)
    return out


def solid_extent(solid: Block):
    """Bounds from the solid's corners, falling back to its plane points."""
    v = solid_vertices(solid)
    return bounds(v) if v else bounds(solid_points(solid))


def has_displacement(solid: Block) -> bool:
    return any(node.name == "dispinfo" for node in walk(solid))


def straddles(solid: Block, axis: str, at: float, margin: float = 1.0) -> bool:
    """Does the solid cross the plane by more than a rounding error either side?"""
    b = bounds(solid_points(solid))
    if b is None:
        return False
    i = AXIS_INDEX[axis]
    return b[0][i] < at - margin and b[1][i] > at + margin


# ----------------------------------------------------------------- translating
def translate(block: Block, delta) -> None:
    """Move a block and everything under it by `delta`, in place.

    Handles the four places a VMF hides world coordinates: side planes, texture
    shifts, displacement start positions, and the point-valued entity keys.
    """
    dx, dy, dz = (float(c) for c in delta)
    if (dx, dy, dz) == (0.0, 0.0, 0.0):
        return
    for node in walk(block):
        pairs = node.pairs
        for i, (k, v) in enumerate(pairs):
            if k == "plane" and node.name == "side":
                pairs[i] = (k, _shift_plane(v, dx, dy, dz))
            elif k in ("uaxis", "vaxis") and node.name == "side":
                pairs[i] = (k, _shift_axis(v, dx, dy, dz))
            elif k == "startposition":
                pairs[i] = (k, _shift_bracketed(v, dx, dy, dz))
            elif k in POINT_KEYS:
                pairs[i] = (k, _shift_vector(v, dx, dy, dz))


def _shift_plane(value: str, dx: float, dy: float, dz: float) -> str:
    m = PLANE.search(value)
    if not m:
        return value
    out = []
    for g in m.groups():
        x, y, z = (float(c) for c in g.split())
        out.append("(" + " ".join(coord(c) for c in (x + dx, y + dy, z + dz)) + ")")
    return " ".join(out)


def _shift_axis(value: str, dx: float, dy: float, dz: float) -> str:
    """Keep the texture where it was on the brush: shift -= (delta . U) / scale."""
    m = AXIS.search(value)
    if not m:
        return value
    parts = [float(c) for c in m.group(1).split()]
    if len(parts) != 4:
        return value
    ux, uy, uz, shift = parts
    scale = float(m.group(2))
    if scale:
        shift -= (dx * ux + dy * uy + dz * uz) / scale
    return "[" + " ".join(coord(c) for c in (ux, uy, uz, shift)) + f"] {m.group(2)}"


def _shift_vector(value: str, dx: float, dy: float, dz: float) -> str:
    parts = value.split()
    if len(parts) != 3:
        return value
    try:
        x, y, z = (float(c) for c in parts)
    except ValueError:
        return value
    return " ".join(coord(c) for c in (x + dx, y + dy, z + dz))


def _shift_bracketed(value: str, dx: float, dy: float, dz: float) -> str:
    v = value.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return value
    return "[" + _shift_vector(v[1:-1], dx, dy, dz) + "]"


# --------------------------------------------------------------------- cutting
def cut_plane_points(axis: str, at: float, lo, hi, keep_low: bool):
    """Three points naming the cut plane, wound as `box` winds that face.

    Taken from an actual `box` rather than written out here: the winding rule
    (clockwise seen from outside, so the normal points out of the brush) is the
    one thing about a side that vbsp will not forgive, and `brush.box` is the
    constructor that is already known to get it right.
    """
    i = AXIS_INDEX[axis]
    lo = list(float(c) for c in lo)
    hi = list(float(c) for c in hi)
    for j in range(3):  # a degenerate span names no plane, so keep every axis fat
        if hi[j] - lo[j] < 1.0:
            lo[j], hi[j] = lo[j] - 8.0, hi[j] + 8.0
    if keep_low:
        hi[i] = at  # the kept part is below the plane, so the cut is its high face
        face = ("+x", "+y", "top")[i]
    else:
        lo[i] = at
        face = ("-x", "-y", "bottom")[i]
    faces = dict(zip(("top", "bottom", "+x", "-x", "+y", "-y"), box(lo, hi).sides))
    return faces[face].points


def cut_side(axis: str, at: float, lo, hi, keep_low: bool, material: str = NODRAW) -> Side:
    pts = cut_plane_points(axis, at, lo, hi, keep_low)
    u, v = axes_for(Solid([Side(pts)]).sides[0].normal())
    return Side(pts, material, uaxis=u, vaxis=v)


def cut_solid(solid: Block, axis: str, at: float, keep_low: bool, ids, material: str = NODRAW) -> Block:
    """A copy of the solid with one more side: the cut plane.

    The half-space representation is why this is three lines rather than a
    polygon clipper. Every other side, its texture and its alignment are
    untouched, so the kept part looks exactly like the part of the wall it was.
    """
    out = copy.deepcopy(solid)
    lo, hi = bounds(solid_points(solid))
    out.children.append(cut_side(axis, at, lo, hi, keep_low, material).block(ids))
    # `editor` conventionally comes last in a solid; keep it there.
    for i, child in enumerate(out.children):
        if child.name == "editor":
            out.children.append(out.children.pop(i))
            break
    return out


# ------------------------------------------------------------------ renumbering
def fix_pivot(entity: Block) -> bool:
    """Pull a brush entity's `origin` back onto its own brushes, if it has left them.

    A brush entity's origin is a pivot, not a position - Hammer writes it
    wherever the entity was rotated about, often nowhere near the volume. vbsp
    floods the leak test from it anyway, so an entity whose brushes went one way
    across a splice and whose pivot stayed the other way lands in the void and
    reports as a leak: `Entity func_brush (1028 -6114 136) leaked!`, pointing at
    a func_brush that is in fact sealed.

    Only moved when the pivot is outside the entity's own bounds, so a hinge
    that means something keeps meaning it.
    """
    solids = [c for c in entity.children if c.name == "solid"]
    if not solids:
        return False
    origin = None
    for k, v in entity.pairs:
        if k == "origin":
            try:
                origin = [float(c) for c in v.split()[:3]]
            except ValueError:
                return False
            break
    if origin is None:
        return False
    boxes = [solid_extent(s) for s in solids]
    boxes = [b for b in boxes if b]
    if not boxes:
        return False
    b = (tuple(min(x[0][i] for x in boxes) for i in range(3)),
         tuple(max(x[1][i] for x in boxes) for i in range(3)))
    if all(b[0][i] - 1.0 <= origin[i] <= b[1][i] + 1.0 for i in range(3)):
        return False
    c = tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))
    entity.pairs[:] = [
        (k, " ".join(coord(v) for v in c) if k == "origin" else v) for k, v in entity.pairs
    ]
    return True


def renumber(block: Block, ids) -> dict[str, str]:
    """Give every id under `block` a fresh value; report what moved where.

    A cut solid becomes two solids, and both cannot keep the original ids. The
    mapping is what lets an `info_overlay` that names a side by id follow the
    copy that ended up in its half.
    """
    moved: dict[str, str] = {}
    for node in walk(block):
        for i, (k, v) in enumerate(node.pairs):
            if k == "id":
                new = str(next(ids))
                moved[v] = new
                node.pairs[i] = (k, new)
    return moved


def remap_sides(block: Block, moved: dict[str, str]) -> None:
    """Rewrite `sides` id lists (info_overlay, func_areaportal) through a remap."""
    for node in walk(block):
        for i, (k, v) in enumerate(node.pairs):
            if k == "sides":
                node.pairs[i] = (k, " ".join(moved.get(s, s) for s in v.split()))


def ids_from(start: int):
    return itertools.count(start + 1)


def solid_block(solid: Solid, ids) -> Block:
    return solid.block(ids)
