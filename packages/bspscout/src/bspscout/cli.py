"""bspscout command line."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from .bsp import Bsp
from . import graph, nav, render
from .trace import MOVE_MASK, Tracer


def _log(t0):
    def p(msg=""):
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)
    return p


# ---------------------------------------------------------------- info
def cmd_info(args):
    b = Bsp(args.bsp)
    print(f"{b.file.path.name}: VBSP v{b.file.version} rev {b.file.revision} "
          f"({b.file.path.stat().st_size / 1e6:.0f} MB)")
    print("\nlumps:")
    for e in b.file.lumps:
        if e.length:
            print(f"  {e.index:2d} {e.name:32s} {e.length:12,d} bytes  v{e.version}")
    mn, mx = b.world_bounds
    print(f"\nworld bounds: {mn} .. {mx}")
    print(f"size: {mx - mn} units")
    from collections import Counter
    c = Counter(e.classname for e in b.entities)
    print(f"\n{len(b.entities)} entities:")
    for k, v in c.most_common():
        print(f"  {v:5d} {k}")
    # The classnames the shipped maps actually use. `ins_objective` is not one
    # of them: a capture point is a point_controlpoint plus a
    # trigger_capture_zone brush that names it, one pair per game mode.
    print("\ngameplay entities present:")
    for k in ("point_controlpoint", "trigger_capture_zone", "ins_spawnzone",
              "ins_spawnpoint", "ins_blockzone", "ins_viewpoint", "func_precipitation"):
        print(f"  {k}: {c.get(k, 0)}")


# ---------------------------------------------------------------- build
def cmd_build(args):
    t0 = time.time()
    p = _log(t0)
    p(f"reading {args.bsp}")
    b = Bsp(args.bsp)
    p("rasterising surfaces")
    grid, top_z, surf = nav.rasterize(b, args.res, progress=p)
    surf = nav.merge_layers(grid, surf)
    p("player clearance")
    # Brush-aware, because the tree alone cannot see func_detail: see
    # trace.py's docstring for what that cost the walkable set.
    solid = Tracer(b, mask=MOVE_MASK, include_entities=True).point_solid
    surf = nav.clearance_filter(b, grid, surf, progress=p, solid=solid)
    ground = args.ground if args.ground is not None else nav.global_ground(surf)
    p(f"global ground plane: world z = {ground:.1f}")
    sky_cells = np.isfinite(top_z) & ((top_z - ground) > args.sky_agl)
    top_z[sky_cells] = np.nan
    p(f"  masked {int(sky_cells.sum()):,} roof cells above +{args.sky_agl:.0f} as 3D skybox")

    p("building movement graph")
    slot_id, slot_z = graph.build_slots(grid, surf)
    src, dst, wgt = graph.build_edges(b, grid, surf, slot_id, slot_z, progress=p,
                                      solid=solid)
    ls, ld, lw = graph.link_volumes(b, grid, surf, slot_id, slot_z, progress=p)
    ps, pd_, pw = graph.link_prop_stairs(b, grid, surf, progress=p)
    src = np.concatenate([src, ls, ps]); dst = np.concatenate([dst, ld, pd_])
    wgt = np.concatenate([wgt, lw, pw])
    p(f"  {len(src):,} directed edges")

    agl = surf["z"] - ground
    p("connected components")
    g, ncomp, labels, _ = graph.analyse(len(surf["cell"]), src, dst, wgt, np.zeros(0, int))
    counts = np.bincount(labels)

    # the 3D skybox is a scale model floating far above the map: drop it
    comp_min_agl = np.full(len(counts), np.inf)
    np.minimum.at(comp_min_agl, labels, agl)
    sky = comp_min_agl > args.sky_agl
    p(f"  {ncomp:,} components, dropping {int(sky.sum())} as 3D-skybox "
      f"({int(counts[sky].sum()):,} nodes above +{args.sky_agl:.0f})")
    counts_ok = counts.copy(); counts_ok[sky] = 0
    main = int(np.argmax(counts_ok))

    if args.seed:
        sx, sy, sz = [float(v) for v in args.seed.split(",")]
        seeds = graph._nodes_in_box(grid, surf, [sx - 64, sy - 64, sz - 96],
                                    [sx + 64, sy + 64, sz + 96])
        p(f"  seeding from {args.seed}: {len(seeds)} nodes")
    else:
        # Where the players spawn, not the biggest slab of floor. The two agree
        # on every shipped map - the largest component holds 94-100% of placed
        # spawns on all 13 checkpoint maps - and disagree completely on a sealed
        # test box, where the largest standable surface is the outside of the
        # roof and the room itself is the smaller component.
        spawns = graph.spawn_nodes(b, grid, surf)
        spawns = spawns[~sky[labels[spawns]]] if len(spawns) else spawns
        if len(spawns):
            pick = int(np.bincount(labels[spawns]).argmax())
            seeds = spawns[labels[spawns] == pick]
            p(f"  seeding from {len(seeds)} spawn nodes in component {pick} "
              f"({counts[pick]:,} nodes, {100*counts[pick]/len(labels):.1f}% of "
              f"standable area){'' if pick == main else '  [not the largest]'}")
        else:
            seeds = np.flatnonzero(labels == main)
            p(f"  no spawn entity lands on walkable floor - seeding from largest "
              f"component ({counts[main]:,} nodes, "
              f"{100*counts[main]/len(labels):.1f}% of standable area)")
    _, _, _, dist = graph.analyse(len(surf["cell"]), src, dst, wgt,
                                  seeds if len(seeds) < 4096 else seeds[::max(1, len(seeds)//4096)])
    reach = labels == labels[seeds[0]] if len(seeds) else np.zeros(len(labels), bool)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out,
             res=grid.res, origin=grid.origin, w=grid.w, h=grid.h,
             ground=ground, top_z=top_z,
             cell=surf["cell"], z=surf["z"], nz=surf["nz"], stance=surf["stance"],
             labels=labels, dist=dist, reach=reach, main=main,
             src=src.astype(np.int32), dst=dst.astype(np.int32), wgt=wgt.astype(np.float32),
             ents=np.array(json.dumps([e.props for e in b.entities]), dtype=object))
    p(f"wrote {out} ({out.stat().st_size/1e6:.0f} MB)")
    p(f"reachable: {int(reach.sum()):,} / {len(reach):,} standable cells")


# ---------------------------------------------------------------- load
class Cache:
    def __init__(self, path):
        d = np.load(path, allow_pickle=True)
        self.grid = nav.Grid(float(d["res"]), d["origin"], int(d["w"]), int(d["h"]))
        self.ground = float(d["ground"])
        self.top_z = d["top_z"]
        self.surf = {k: d[k] for k in ("cell", "z", "nz", "stance")}
        self.labels = d["labels"]; self.dist = d["dist"]; self.reach = d["reach"]
        self.src, self.dst, self.wgt = d["src"], d["dst"], d["wgt"]
        self.ents = json.loads(str(d["ents"]))
        self.agl = self.surf["z"] - self.ground

    def crop(self, pad=512.0):
        c = self.surf["cell"][self.reach]
        w = self.grid.w
        x = self.grid.origin[0] + (c % w + 0.5) * self.grid.res
        y = self.grid.origin[1] + (c // w + 0.5) * self.grid.res
        return (x.min() - pad, x.max() + pad, y.min() - pad, y.max() + pad)

    def xy(self, idx=None):
        c = self.surf["cell"] if idx is None else self.surf["cell"][idx]
        w = self.grid.w
        return np.stack([self.grid.origin[0] + (c % w + 0.5) * self.grid.res,
                         self.grid.origin[1] + (c // w + 0.5) * self.grid.res], axis=-1)


def _ent_markers(cache):
    out = []
    style = {
        "info_player_start": ("#ff4d4d", "*", 9),
        "func_simpleladder": ("#66d9ff", "^", 4),
        "trigger_teleport": ("#ff9cf0", "D", 5),
        "prop_door_rotating": ("#ffd166", "s", 2.5),
    }
    for e in cache.ents:
        cls = e.get("classname", "")
        if cls not in style or "origin" not in e:
            continue
        col, mk, ms = style[cls]
        try:
            o = [float(v) for v in e["origin"].split()[:3]]
        except ValueError:
            continue
        out.append({"xy": (o[0], o[1]), "color": col, "marker": mk, "ms": ms})
    return out


# ---------------------------------------------------------------- render
def cmd_render(args):
    c = Cache(args.cache)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    crop = c.crop()
    mk = _ent_markers(c) if args.entities else None
    print("overview ...")
    render.render_overview(c.grid, c.top_z, c.surf, c.agl, c.reach, c.ground, crop,
                           outdir / "overview.png", markers=mk,
                           title=f"{Path(args.cache).stem}: bot-walkable space")
    print("height ...")
    render.render_height(c.grid, c.surf, c.agl, c.reach, crop,
                         outdir / "height_agl.png", c.ground)
    for lo, hi, name in [(-2000, -64, "below"), (-64, 96, "L0_ground"),
                         (96, 224, "L1"), (224, 352, "L2"), (352, 640, "L3+")]:
        sel = c.reach & (c.agl >= lo) & (c.agl < hi)
        if sel.sum() < 200:
            continue
        print(f"level {name} ({int(sel.sum()):,} cells) ...")
        render.render_slice(c.grid, c.top_z, c.surf, c.agl, c.reach, crop,
                            outdir / f"level_{name}.png", (lo, hi), c.ground, markers=mk)
    print(f"wrote images to {outdir}")


def cmd_slice(args):
    c = Cache(args.cache)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    lo = args.agl - args.thickness / 2
    hi = args.agl + args.thickness / 2
    render.render_slice(c.grid, c.top_z, c.surf, c.agl, c.reach, c.crop(),
                        outdir / f"slice_agl{int(args.agl):+d}.png", (lo, hi), c.ground)
    print(f"wrote {outdir}/slice_agl{int(args.agl):+d}.png")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bspscout", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("info", help="dump lumps/entities/bounds")
    a.add_argument("bsp"); a.set_defaults(fn=cmd_info)

    a = sub.add_parser("build", help="parse BSP into a walkability cache")
    a.add_argument("bsp")
    a.add_argument("--res", type=float, default=16.0, help="grid resolution in units")
    a.add_argument("--ground", type=float, default=None,
                   help="override the global ground plane (world z)")
    a.add_argument("--sky-agl", type=float, default=1500.0,
                   help="components entirely above this AGL are treated as 3D skybox")
    a.add_argument("--seed", default=None, help="x,y,z to flood-fill from")
    a.add_argument("--out", default="out/nav.npz")
    a.set_defaults(fn=cmd_build)

    a = sub.add_parser("render", help="draw overview / height / per-storey plans")
    a.add_argument("cache"); a.add_argument("--outdir", default="out")
    a.add_argument("--entities", action="store_true", default=True)
    a.set_defaults(fn=cmd_render)

    a = sub.add_parser("slice", help="floor plan at a given height above ground")
    a.add_argument("cache"); a.add_argument("--outdir", default="out")
    a.add_argument("--agl", type=float, default=40.0)
    a.add_argument("--thickness", type=float, default=160.0)
    a.set_defaults(fn=cmd_slice)

    from .objectives import add_parser as obj_parser
    obj_parser(sub)

    from .sight import add_parser as sight_parser
    sight_parser(sub)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
