"""Solids: convex polyhedra written as a set of sides, each side a three-point plane.

vbsp derives a side's normal as `(p1 - p2) x (p3 - p2)` and requires it to point
*out* of the solid, which means the three points are wound clockwise as seen
from outside the brush. Every constructor in here owes that guarantee to its
caller; get the winding backwards and vbsp either rejects the brush or, worse,
inverts it and the map leaks through a wall that looks solid in the editor.

Texture axes follow Hammer's world alignment: u and v are picked from the
dominant axis of the face normal. Wrong axes are only cosmetic - a face is
still sealed - so this stays simple until something needs otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .kv import EDITOR_SOLID, Block, editor, num

Vec = tuple[float, float, float]

DEV = "DEV/DEV_MEASUREGENERIC01B"
NODRAW = "TOOLS/TOOLSNODRAW"
SKYBOX = "TOOLS/TOOLSSKYBOX"

LIGHTMAP_SCALE = 16
TEX_SCALE = 0.25

# The six faces of a box, in the order Hammer writes them.
FACES = ("top", "bottom", "+x", "-x", "+y", "-y")


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def normal(p1: Vec, p2: Vec, p3: Vec) -> Vec:
    """The outward normal vbsp will read off these three points, unnormalised."""
    return cross(sub(p1, p2), sub(p3, p2))


def axes_for(n: Vec) -> tuple[Vec, Vec]:
    """Hammer's world-aligned u/v axes for a face with normal `n`."""
    ax = max(range(3), key=lambda i: abs(n[i]))
    if ax == 2:
        return (1, 0, 0), (0, -1, 0)
    if ax == 0:
        return (0, 1, 0), (0, 0, -1)
    return (1, 0, 0), (0, 0, -1)


@dataclass
class Side:
    points: tuple[Vec, Vec, Vec]
    material: str = DEV
    uaxis: Vec | None = None
    vaxis: Vec | None = None
    ushift: float = 0.0
    vshift: float = 0.0
    scale: float = TEX_SCALE
    rotation: float = 0.0
    lightmapscale: int = LIGHTMAP_SCALE
    smoothing_groups: int = 0

    def __post_init__(self):
        if self.uaxis is None or self.vaxis is None:
            u, v = axes_for(self.normal())
            self.uaxis = self.uaxis or u
            self.vaxis = self.vaxis or v

    def normal(self) -> Vec:
        return normal(*self.points)

    def plane(self) -> tuple[Vec, float]:
        """The side as a unit outward normal and offset: `n . x = d`."""
        n = self.normal()
        length = sum(c * c for c in n) ** 0.5
        n = tuple(c / length for c in n)
        return n, sum(n[i] * self.points[0][i] for i in range(3))

    def block(self, ids) -> Block:
        b = Block("side").set("id", next(ids))
        pts = " ".join("(" + " ".join(num(c) for c in p) + ")" for p in self.points)
        b.set("plane", pts)
        b.set("material", self.material)
        b.set("uaxis", _axis(self.uaxis, self.ushift, self.scale))
        b.set("vaxis", _axis(self.vaxis, self.vshift, self.scale))
        b.set("rotation", self.rotation)
        b.set("lightmapscale", self.lightmapscale)
        b.set("smoothing_groups", self.smoothing_groups)
        return b


def _axis(a: Vec, shift: float, scale: float) -> str:
    return "[" + " ".join(num(c) for c in a) + f" {num(shift)}] {num(scale)}"


@dataclass
class Solid:
    sides: list[Side] = field(default_factory=list)
    colour: dict = field(default_factory=lambda: dict(EDITOR_SOLID))

    def block(self, ids) -> Block:
        b = Block("solid").set("id", next(ids))
        for s in self.sides:
            b.add(s.block(ids))
        b.add(editor(self.colour))
        return b

    def points(self):
        for s in self.sides:
            yield from s.points

    def bounds(self) -> tuple[Vec, Vec]:
        pts = list(self.points())
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi

    def contains(self, point, tol: float = 1e-6) -> bool:
        """Is a point strictly inside this brush?

        A solid is the intersection of its sides' half-spaces, so a point is
        inside exactly when it sits behind every outward plane. This is how an
        entity buried in world geometry gets caught before vbsp calls it a leak.
        """
        for side in self.sides:
            n, d = side.plane()
            if sum(n[i] * float(point[i]) for i in range(3)) - d >= -tol:
                return False
        return True

    def problems(self) -> list[str]:
        out = []
        if len(self.sides) < 4:
            out.append(f"solid with {len(self.sides)} sides - a closed brush needs at least 4")
        for i, s in enumerate(self.sides):
            n = s.normal()
            if max(abs(c) for c in n) < 1e-6:
                out.append(f"side {i} has three collinear points, so no plane")
        return out


# ---------------------------------------------------------------- constructors
def from_faces(faces, material: str = DEV, materials=None, **side_kw) -> Solid:
    """A solid from face polygons, each wound clockwise as seen from outside.

    `materials` maps a face's index or name (when `faces` is a dict) to a
    material, so a floor can carry a different texture from the walls it is
    built out of.
    """
    named = list(faces.items()) if isinstance(faces, dict) else list(enumerate(faces))
    materials = materials or {}
    sides = []
    for key, poly in named:
        poly = [tuple(float(c) for c in p) for p in poly]
        if len(poly) < 3:
            raise ValueError(f"face {key!r} has {len(poly)} points")
        sides.append(Side(_plane_points(poly, key), materials.get(key, material), **side_kw))
    return Solid(sides)


def _plane_points(poly, key) -> tuple[Vec, Vec, Vec]:
    """Three consecutive, non-collinear vertices - the plane, winding preserved."""
    p1 = poly[0]
    for i in range(1, len(poly) - 1):
        p2, p3 = poly[i], poly[i + 1]
        if max(abs(c) for c in normal(p1, p2, p3)) > 1e-6:
            return (p1, p2, p3)
    raise ValueError(f"face {key!r} is degenerate - all its points are collinear")


def box(mins, maxs, material: str = DEV, materials=None, **side_kw) -> Solid:
    """An axis-aligned box. `materials` keys are the names in FACES."""
    x0, y0, z0 = (float(c) for c in mins)
    x1, y1, z1 = (float(c) for c in maxs)
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise ValueError(f"box has no volume: {mins} .. {maxs}")
    faces = {
        "top": [(x0, y0, z1), (x0, y1, z1), (x1, y1, z1), (x1, y0, z1)],
        "bottom": [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],
        "+x": [(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)],
        "-x": [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)],
        "+y": [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
        "-y": [(x0, y0, z0), (x0, y0, z1), (x1, y0, z1), (x1, y0, z0)],
    }
    return from_faces(faces, material, materials, **side_kw)


def prism(poly, z0: float, z1: float, material: str = DEV, materials=None, **side_kw) -> Solid:
    """Extrude a convex polygon, wound counter-clockwise in xy, between two z.

    This is the constructor for anything the street grid puts at an angle. The
    polygon must be convex: vbsp's brushes are intersections of half-spaces and
    a concave outline silently comes out as its own convex hull.
    """
    poly = [(float(p[0]), float(p[1])) for p in poly]
    if len(poly) < 3:
        raise ValueError(f"prism needs 3 or more points, got {len(poly)}")
    if z1 <= z0:
        raise ValueError(f"prism has no height: {z0} .. {z1}")
    if _area(poly) <= 0:
        raise ValueError("prism polygon is clockwise or degenerate in xy; wind it counter-clockwise")
    faces = {
        "top": [(x, y, z1) for x, y in reversed(poly)],
        "bottom": [(x, y, z0) for x, y in poly],
    }
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        faces[f"side{i}"] = [(*a, z0), (*a, z1), (*b, z1), (*b, z0)]
    return from_faces(faces, material, materials, **side_kw)


def _area(poly) -> float:
    """Twice the signed area; positive when the polygon is counter-clockwise."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
