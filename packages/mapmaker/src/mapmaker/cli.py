"""mapmaker command line.

Three maps to exercise the VMF writer end to end, all meant to be compiled:
`room` is the code-written twin of the hand-made maps/build/test_room.vmf,
`duplex` is two rooms joined by a doorway cut through a shared wall - the case
where a partition off by a unit becomes a leak - and `pillar` puts one brush at
an angle. `check` compares two VMFs as geometry, which is how `room` is held to
the hand-made map.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import vmf as V

BUILD = Path("maps/build")


def build_room(size=(512, 512, 256), thickness=32, material=V.DEV) -> V.Vmf:
    """One sealed room, a light and a spawn: the smallest map that compiles."""
    w, l, h = (float(c) for c in size)
    lo = (-w / 2, -l / 2, 0.0)
    hi = (w / 2, l / 2, h)
    m = V.Vmf()
    m.add(V.room(lo, hi, thickness, material))
    m.light((0, 0, h * 0.75))
    m.player_start((0, 0, 16))
    return m


def build_duplex(size=(512, 512, 256), thickness=32, door=(64, 128), material=V.DEV) -> V.Vmf:
    """Two rooms of `size` either side of a shared wall, with a doorway through it.

    The shared wall is one slab spanning the interior exactly, tiled around the
    opening by `slab_with_opening`. Both rooms are inside a single shell, so the
    seal is the shell's; what this map tests is that the tiling leaves a hole a
    player fits through and no hole anywhere else.
    """
    w, l, h = (float(c) for c in size)
    t = float(thickness)
    dw, dh = (float(c) for c in door)
    x = w + t / 2  # interior spans -x .. x, with the wall t thick across the middle
    lo = (-x, -l / 2, 0.0)
    hi = (x, l / 2, h)
    m = V.Vmf()
    m.add(V.room(lo, hi, t, material))
    m.add(V.doorway((-t / 2, -l / 2, 0), (t / 2, l / 2, h), along=0, width=dw, height=dh, material=material))
    for cx in (-(w + t) / 2, (w + t) / 2):
        m.light((cx, 0, h * 0.75))
    m.player_start((-(w + t) / 2, 0, 16))
    return m


def build_pillar(size=(512, 512, 256), thickness=32, material=V.DEV) -> V.Vmf:
    """A room with an angled column in it: the same shell, plus one `prism`.

    Nothing here needs a prism - a box column would do. The point is that the
    street grid in PLAN.md step 6 puts brushes at angles, and this is the map
    that says vbsp accepts the plane windings `prism` writes before anything
    depends on it.
    """
    w, l, h = (float(c) for c in size)
    m = build_room(size, thickness, material)
    # Snapped to 16 units: an irregular column is fine, an off-grid one is not,
    # and Vmf.problems() will refuse the map rather than let it drift.
    r = 16 * max(1, round(min(w, l) / 96))
    o = r + 16  # off-centre: a column over the spawn is an entity in solid, so a leak
    poly = [(-r, -r), (r, -r - 32), (r + 16, r), (-r + 48, r + 16)]
    m.add(V.prism([(x + o, y + o) for x, y in poly], 0, h, material))
    return m


MAPS = {"room": build_room, "duplex": build_duplex, "pillar": build_pillar}


def write(m: V.Vmf, path: Path) -> int:
    problems = m.problems()
    for p in problems:
        print(f"  ! {p}", file=sys.stderr)
    if problems:
        print("refusing to write; fix the geometry first", file=sys.stderr)
        return 1
    m.write(path)
    lo, hi = m.bounds()
    n = len(list(m.all_solids()))
    print(f"wrote {path}: {n} solids, {len(m.entities)} entities, bounds {lo} .. {hi}")
    print(f"compile: tools/compile-map.sh {path}")
    print(f"verify:  BSP={path.with_suffix('.bsp')} mise run scout")
    return 0


def splice_map(args) -> int:
    """Cut a decompiled map in two, move one half, and seal the halves back together."""
    from . import splice as S

    doc = V.read.load(args.vmf)
    plan = S.Plan(
        axis=args.axis, at=args.at, gap=args.gap, slide=args.slide,
        thickness=args.thickness,
    )
    if args.door:
        plan.doors = [tuple(d) for d in args.door]
    else:
        cache = args.cache or Path("cache") / f"{args.vmf.stem.removesuffix('_np')}.npz"
        if not Path(cache).exists():
            print(f"no nav cache at {cache}; pass --door A0 B0 A1 B1 or scout the map first",
                  file=sys.stderr)
            return 1
        xyz, res = S.pick.load(cache)
        cands = S.pick.candidates(xyz, res, plan.axis, plan.at)
        if not cands:
            print(f"nothing walkable on both sides of {plan.axis}={plan.at:g} in {cache}",
                  file=sys.stderr)
            return 1
        print(f"joins available at {plan.axis}={plan.at:g}, widest first:")
        for n, c in enumerate(cands):
            mark = "  <- door" if n < args.joins else ""
            print(f"  centre {c.centre:8.0f}  span {c.span:6.0f} u  floor {c.floor:8.0f}  "
                  f"{c.cells} cells{mark}")
        plan.doors = [c.door(args.door_width, args.door_height) for c in cands[:args.joins]]
        if len(cands) > args.joins:
            print(f"  ({len(cands) - args.joins} narrower crossings left sealed - each one "
                  "sealed is a route the moved half loses)")

    out, report = S.splice(doc, plan)
    for line in report.lines():
        print(line)
    path = args.out or BUILD / f"{args.vmf.stem.removesuffix('_np')}_splice.vmf"
    V.read.save(out, path)

    # A sidecar, so the steps after this one - cubemaps, the fidelity check -
    # do not have to re-derive the offset the splice used.
    plan_path = path.with_suffix(".plan.json")
    plan_path.write_text(json.dumps({
        "source": str(args.vmf), "axis": plan.axis, "at": plan.at, "gap": plan.gap,
        "slide": plan.slide, "delta": list(plan.delta), "doors": [list(d) for d in plan.doors],
        "seal_solids": report.seal_solids,
    }, indent=2) + "\n")
    print(f"wrote {path} and {plan_path}")
    print(f"compile: tools/compile-map.sh {path}")
    print(f"then:    mapmaker cubemaps <shipped.bsp> {path.with_suffix('.bsp')} --plan {plan_path}")
    print(f"check:   tools/check-splice.py {args.vmf} {path} --plan {plan_path}")
    return 1 if report.buried else 0


def transplant_cubemaps(args) -> int:
    """Copy a shipped map's baked cubemaps into a spliced one, following the move."""
    from .splice import cubemaps

    delta = args.delta
    if args.plan:
        delta = json.loads(Path(args.plan).read_text())["delta"]
    result = cubemaps.transplant(args.shipped, args.spliced, delta, args.out)
    for line in result.lines():
        print(line)
    print(f"wrote {result.out}")
    return 0


def check(a: Path, b: Path) -> int:
    """Are two VMFs the same geometry? The writer's regression test."""
    diffs = V.compare.differences(a.read_text(), b.read_text())
    for d in diffs:
        print(f"  ! {d}")
    print(f"{a} and {b} are {'not ' if diffs else ''}the same geometry")
    return 1 if diffs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mapmaker", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help=check.__doc__.splitlines()[0])
    p.add_argument("vmf", type=Path, nargs=2)

    p = sub.add_parser("splice", help=splice_map.__doc__.splitlines()[0])
    p.add_argument("vmf", type=Path, help="a decompiled map, e.g. maps/ref/ministry_np.vmf")
    p.add_argument("-o", "--out", type=Path, help=f"default {BUILD}/<name>_splice.vmf")
    p.add_argument("--axis", choices=("x", "y", "z"), default="x", help="cut plane normal")
    p.add_argument("--at", type=float, required=True, help="where the cut plane sits")
    p.add_argument("--gap", type=float, default=1024, help="how far the far half moves, default 1024")
    p.add_argument("--slide", type=float, default=0, help="how far it also slides along the cut")
    p.add_argument("--thickness", type=float, default=64, help="seal wall thickness, default 64")
    p.add_argument("--door", type=float, nargs=4, action="append",
                   metavar=("A0", "B0", "A1", "B1"),
                   help="an opening, in the two in-plane axes; repeatable. "
                        "Default is picked from the nav cache")
    p.add_argument("--joins", type=int, default=4,
                   help="how many of the walkable crossings to keep open, widest first")
    p.add_argument("--door-width", type=float, default=256)
    p.add_argument("--door-height", type=float, default=160)
    p.add_argument("--cache", type=Path, help="nav cache to pick the join from")

    p = sub.add_parser("cubemaps", help=transplant_cubemaps.__doc__.splitlines()[0])
    p.add_argument("shipped", type=Path, help="the map the geometry came from")
    p.add_argument("spliced", type=Path, help="the compiled splice, edited in place")
    p.add_argument("-o", "--out", type=Path, help="write here instead of in place")
    p.add_argument("--delta", type=float, nargs=3, default=(0, 0, 0),
                   metavar=("DX", "DY", "DZ"), help="how far the moved half moved")
    p.add_argument("--plan", type=Path, help="read the offset from a splice's .plan.json instead")
    for name in MAPS:
        p = sub.add_parser(name, help=MAPS[name].__doc__.splitlines()[0])
        p.add_argument("-o", "--out", type=Path, help=f"default {BUILD}/{name}.vmf")
        p.add_argument("--size", type=float, nargs=3, metavar=("W", "L", "H"), default=(512, 512, 256))
        p.add_argument("--thickness", type=float, default=32, help="wall thickness, default 32")
        p.add_argument("--material", default=V.DEV)
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return check(*args.vmf)
    if args.cmd == "splice":
        return splice_map(args)
    if args.cmd == "cubemaps":
        return transplant_cubemaps(args)

    kw = {"size": args.size, "thickness": args.thickness, "material": args.material}
    m = MAPS[args.cmd](**kw)
    return write(m, args.out or BUILD / f"{args.cmd}.vmf")


if __name__ == "__main__":
    sys.exit(main())
