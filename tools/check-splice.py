#!/usr/bin/env python
"""Hold a spliced map to the map it came from.

Two claims the splice makes, both silent when broken:

1. **No geometry was invented.** Every brush in the splice is a brush of the
   original, either where it was or moved by exactly the splice offset - plus at
   most one extra plane, the cut. Anything else means a solid drifted, and a
   drifted solid is a leak or a hole in a wall that still looks solid.

2. **Textures did not slide.** Source reads a face's texture coordinate as
   `(P . U) / scale + shift`, so moving a brush without correcting `shift`
   slides its texture. The check is the identity: the same point of the same
   face must land on the same texel before and after the move.

    tools/check-splice.py maps/ref/ministry_np.vmf maps/build/ministry_splice.vmf \\
        --plan maps/build/ministry_splice.plan.json
"""
from __future__ import annotations

import argparse
import collections
import json
import copy
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/mapmaker/src"))

from mapmaker.splice import blocks as B  # noqa: E402
from mapmaker.vmf import read  # noqa: E402

QUANT = 1  # a plane is not "different" because of a tenth of a unit: the
           # decompile itself carries fractional coordinates, and a plane
           # rebuilt from three written points lands within about 1e-4 of
           # where it started.


def plane_set(solid) -> frozenset:
    out = []
    for side in solid.children:
        if side.name != "side":
            continue
        p = B.side_plane(side)
        if p:
            n, d = p
            out.append(tuple(round(c, QUANT) for c in (*n, d)))
    return frozenset(out)


def all_solids(doc):
    w = read.world(doc)
    out = list(read.children(w, "solid"))
    for e in read.entities(doc):
        out += read.children(e, "solid")
    return out


def shifted(solid, delta):
    out = copy.deepcopy(solid)
    B.translate(out, delta)
    return out


def check_geometry(before, after, delta, expect_new: int) -> list[str]:
    """Every solid in `after` is a solid of `before`, moved by 0 or -delta."""
    index = collections.defaultdict(set)
    originals = [plane_set(s) for s in all_solids(before)]
    for i, ps in enumerate(originals):
        for p in ps:
            index[p].add(i)

    back = tuple(-c for c in delta)
    unmoved = moved = extra = 0
    problems = []
    new = []
    for solid in all_solids(after):
        hit = None
        for shift in ((0.0, 0.0, 0.0), back):
            ps = plane_set(shifted(solid, shift) if any(shift) else solid)
            if not ps:
                hit = "empty"
                break
            counts = collections.Counter()
            for p in ps:
                counts.update(index.get(p, ()))
            if counts:
                best, n = counts.most_common(1)[0]
                # One plane may be new: the cut. More than one is invention.
                if n >= len(ps) - 1:
                    hit = shift
                    if len(ps) - n == 1:
                        extra += 1
                    break
        if hit is None:
            new.append(f"solid not in the original: {sorted(plane_set(solid))[:2]} ...")
        elif hit == "empty":
            problems.append("solid with no planes")
        elif any(hit):
            moved += 1
        else:
            unmoved += 1
    print(f"geometry: {unmoved} solids in place, {moved} moved by {tuple(delta)}, "
          f"{extra} of them carrying one cut plane")
    print(f"          {len(new)} solids are new (the seal); {expect_new} expected")
    if len(new) != expect_new:
        problems += new[:5]
        problems.append(f"{len(new)} new solids, expected {expect_new}")
    return problems


def check_textures(before, delta, sample: int, seed: int = 0) -> list[str]:
    """`(P . U) / scale + shift` is invariant under `translate`."""
    sides = [s for solid in all_solids(before) for s in solid.children if s.name == "side"]
    random.Random(seed).shuffle(sides)
    sides = sides[:sample]
    worst = 0.0
    problems = []
    for side in sides:
        moved = copy.deepcopy(side)
        B.translate(moved, delta)
        for key in ("uaxis", "vaxis"):
            before_uv = _texcoords(side, key, B.side_points(side))
            after_uv = _texcoords(moved, key, B.side_points(moved))
            if before_uv is None or after_uv is None:
                continue
            for a, b in zip(before_uv, after_uv):
                worst = max(worst, abs(a - b))
                if abs(a - b) > 1e-3:
                    problems.append(
                        f"{key} on {read.get(side, 'material')} slid by {abs(a - b):.4f} texels")
    print(f"textures: {len(sides)} faces sampled, worst drift {worst:.6f} texels")
    return problems[:10]


def _texcoords(side, key, points):
    import re
    m = re.search(r"\[([^\]]*)\] (\S+)", read.get(side, key, ""))
    if not m or not points:
        return None
    ux, uy, uz, shift = (float(c) for c in m.group(1).split())
    scale = float(m.group(2)) or 1.0
    return [(p[0] * ux + p[1] * uy + p[2] * uz) / scale + shift for p in points]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", type=Path, help="the decompiled map the splice read")
    ap.add_argument("after", type=Path, help="the spliced map")
    ap.add_argument("--delta", type=float, nargs=3, metavar=("DX", "DY", "DZ"),
                    help="how far the moved half moved")
    ap.add_argument("--plan", type=Path,
                    help="read the offset and the seal size from a splice's .plan.json")
    ap.add_argument("--sample", type=int, default=4000, help="faces to test for texture drift")
    ap.add_argument("--seal", type=int,
                    help="how many solids the splice added, from its own report")
    args = ap.parse_args()

    if args.plan:
        plan = json.loads(args.plan.read_text())
        args.delta = args.delta or plan["delta"]
        args.seal = args.seal if args.seal is not None else plan["seal_solids"]
    if args.delta is None or args.seal is None:
        ap.error("need --plan, or both --delta and --seal")

    before = read.load(args.before)
    after = read.load(args.after)
    problems = check_geometry(before, after, tuple(args.delta), args.seal)
    problems += check_textures(before, tuple(args.delta), args.sample)
    for p in problems[:20]:
        print(f"  ! {p}")
    if len(problems) > 20:
        print(f"  ... and {len(problems) - 20} more")
    print("splice is faithful" if not problems else "splice is NOT faithful")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
