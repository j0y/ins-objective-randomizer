"""Compare two .vmf files as geometry, not as text.

The check this exists for: the writer reproduces maps/build/test_room.vmf, the
hand-made map that is known to compile. Diffing the text cannot say that - a
side names any three points on its plane and any of its vertices may come
first, so two identical brushes are written many different ways. Reducing each
solid to its set of oriented planes removes that freedom: two brushes are the
same brush exactly when their plane sets match.

Not a VMF parser. It reads the plane and axis lines and ignores everything
else, which is all a geometric comparison needs.
"""
from __future__ import annotations

import math
import re

PLANE = re.compile(r'"plane" "\(([^)]*)\) \(([^)]*)\) \(([^)]*)\)"')
AXIS = re.compile(r'"([uv]axis)" "\[([^\]]*)\] ([^"]*)"')
QUANT = 4  # unit-scale rounding, so a plane is not "different" by 1e-12


def plane(p1, p2, p3) -> tuple:
    """The oriented plane `n . x = d` vbsp reads off three side points."""
    u = tuple(p1[i] - p2[i] for i in range(3))
    v = tuple(p3[i] - p2[i] for i in range(3))
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = math.sqrt(sum(c * c for c in n))
    if length < 1e-9:
        raise ValueError(f"degenerate side: {p1} {p2} {p3}")
    n = tuple(c / length for c in n)
    d = sum(n[i] * p1[i] for i in range(3))
    return tuple(round(c, QUANT) for c in (*n, d))


def solids(text: str) -> list[frozenset]:
    """Every solid in the file as a frozen set of its planes."""
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "solid":
            cur = []
        elif line == "}" and cur is not None:
            if len(cur) >= 4:
                out.append(frozenset(cur))
            cur = None
        elif cur is not None:
            m = PLANE.search(line)
            if m:
                cur.append(plane(*[tuple(float(c) for c in g.split()) for g in m.groups()]))
    return out


def axes(text: str) -> list:
    return sorted(set(AXIS.findall(text)))


def differences(a: str, b: str) -> list[str]:
    """What differs between two VMFs geometrically. Empty means the same map."""
    sa, sb = solids(a), solids(b)
    out = []
    if len(sa) != len(sb):
        out.append(f"{len(sa)} solids vs {len(sb)}")
    for extra, side in ((set(sa) - set(sb), "first"), (set(sb) - set(sa), "second")):
        for brush in extra:
            out.append(f"brush only in the {side} file: {sorted(brush)}")
    if axes(a) != axes(b):
        out.append(f"texture axes differ: {axes(a)} vs {axes(b)}")
    return out
