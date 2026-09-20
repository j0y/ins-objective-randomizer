"""Find defensible, connected places and lay them out as a capture-point chain."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from . import places

ROOF_CLEAR = 96.0      # a floor with drawn geometry this far above it is "under cover"
MIN_ROOM_CELLS = 120   # ignore closets, ledges and bits of pipe
COVER_FRAC = 0.6       # neighbourhood cover needed, so wires/pipes do not read as a roof
COVER_WIN = 7
STOREY = 128.0


def _csr(n, src, dst, wgt):
    return coo_matrix((wgt, (src, dst)), shape=(n, n)).tocsr()


# ---------------------------------------------------------------- cover
def floor_image(cache) -> np.ndarray:
    """Lowest standable z in each grid cell - the floor a roof has to be above."""
    z = np.full(cache.grid.w * cache.grid.h, np.nan, np.float32)
    order = np.argsort(-cache.surf["z"])          # lowest written last, so it wins
    z[cache.surf["cell"][order]] = cache.surf["z"][order]
    return z.reshape(cache.grid.h, cache.grid.w)


def cover_image(cache) -> np.ndarray:
    """Cells with drawn geometry far enough above their floor to be a roof.

    Measured against the floor in that cell, not against the global ground
    datum: on a map whose datum sits above most of its drawn geometry - station
    (datum z=876), verticality (1084), embassy - a datum test finds no roof
    anywhere and the map reads as entirely open air, interiors and all.
    """
    top = cache.top_z
    floor = floor_image(cache)
    ref = np.where(np.isfinite(floor), floor, cache.ground)
    cov = np.isfinite(top) & ((top - ref) > ROOF_CLEAR)
    frac = ndimage.uniform_filter(cov.astype(np.float32), size=COVER_WIN)
    return frac > COVER_FRAC


def indoor_mask(cache) -> np.ndarray:
    top = cache.top_z.ravel()[cache.surf["cell"]]
    under_roof = cover_image(cache).ravel()[cache.surf["cell"]]
    return under_roof & np.isfinite(top) & ((top - cache.surf["z"]) > ROOF_CLEAR)


def buildings(cache, indoor, reach):
    """Label buildings by footprint, so a street always separates two of them."""
    sel = indoor & reach
    idx = np.flatnonzero(sel)
    shape = (cache.grid.h, cache.grid.w)
    img = np.zeros(shape[0] * shape[1], bool)
    img[cache.surf["cell"][idx]] = True
    lbl, n = ndimage.label(img.reshape(shape), structure=np.ones((3, 3)))
    return idx, lbl.ravel()[cache.surf["cell"][idx]] - 1, n, lbl.reshape(shape)


# ---------------------------------------------------------------- placement
def place_in(cache, cells_2d_mask, bbox):
    """Deepest interior point of a footprint: furthest from any exterior wall."""
    res = cache.grid.res
    d = ndimage.distance_transform_edt(cells_2d_mask)
    iy, ix = np.unravel_index(int(np.argmax(d)), d.shape)
    depth_u = float(d[iy, ix] * res)
    return ix, iy, depth_u


def describe(cache, idx, lab, nlab, lblimg, indoor, reach, props):
    names, porg, ptype = props
    xy = cache.xy(idx)
    agl = cache.agl[idx]
    res = cache.grid.res
    shape = (cache.grid.h, cache.grid.w)
    cell_area = res * res

    inside = np.full(len(cache.agl), -1, np.int64)
    inside[idx] = lab
    open_reach = reach & ~indoor
    cross = (inside[cache.src] >= 0) & open_reach[cache.dst]
    cross_lab = inside[cache.src[cross]]
    cross_cell = cache.surf["cell"][cache.src[cross]]

    objs = np.arange(1, nlab + 1)
    slices = ndimage.find_objects(lblimg)
    out = []
    for b in range(nlab):
        m = lab == b
        cnt = int(m.sum())
        if cnt < MIN_ROOM_CELLS:
            continue
        sl = slices[b]
        if sl is None:
            continue
        sub = lblimg[sl] == (b + 1)
        ncells = int(sub.sum())
        ix, iy, depth = place_in(cache, sub, None)
        gx = sl[1].start + ix
        gy = sl[0].start + iy
        cx = cache.grid.origin[0] + (gx + 0.5) * res
        cy = cache.grid.origin[1] + (gy + 0.5) * res

        ent_cells = np.unique(cross_cell[cross_lab == b])
        ent_img = np.zeros(shape[0] * shape[1], bool)
        ent_img[ent_cells] = True
        _, n_ent = ndimage.label(ent_img.reshape(shape), structure=np.ones((3, 3)))

        st = np.unique(np.floor((agl[m] + STOREY / 2) / STOREY).astype(int))
        bbox = [float(xy[m, 0].min()), float(xy[m, 1].min()),
                float(xy[m, 0].max()), float(xy[m, 1].max())]
        label, evidence = (places.label_area(names, porg, ptype, bbox)
                           if len(porg) else (None, []))
        out.append({
            "id": b, "nodes": cnt,
            "footprint_u2": ncells * cell_area,
            "size_u": [round(bbox[2] - bbox[0]), round(bbox[3] - bbox[1])],
            "centroid": [float(cx), float(cy)],
            "depth_u": depth,
            "bbox": bbox,
            "agl_min": float(agl[m].min()), "agl_max": float(agl[m].max()),
            "storeys": [int(s) for s in st], "n_storeys": len(st),
            "entrances": int(n_ent), "entrance_cells": int(len(ent_cells)),
            "label": label, "evidence": evidence,
            "node_ids": idx[m],
        })
    return out


# ---------------------------------------------------------------- axis
def _nearest_node(cache, ok, pt):
    xy = cache.xy()
    d = (xy[:, 0] - pt[0]) ** 2 + (xy[:, 1] - pt[1]) ** 2
    return int(np.argmin(np.where(ok, d, np.inf)))


def axis(cache, reach, outdoor, from_pt=None, to_pt=None):
    """The map's geographic long axis: where an approach and an exfil would sit.

    A graph double-sweep finds the longest *walk*, which on a big map is some
    dead-end interior; the principal axis of the walkable footprint is what
    reads as "one end of the map to the other".
    """
    n = len(cache.agl)
    g = _csr(n, cache.src, cache.dst, cache.wgt)
    ok = reach & outdoor
    if ok.sum() < 2:
        # An enclosed map - a test room, an all-interior blockout - has no
        # open-air cells for the axis to run through, or a stray one or two at a
        # corner the cover filter missed, which is worse: an axis through them
        # is noise. The walkable area itself is then the only thing there is to
        # take a long axis of.
        ok = reach
    sel = np.flatnonzero(ok)
    if len(sel) < 2:
        raise SystemExit("not enough reachable surface to place objectives on")

    # Endpoints have to come from the largest *strongly* connected component.
    # `reach` is a weak component - it is a flood fill over a directed graph, so
    # it includes ledges you can drop onto and not climb out of. Taking the
    # geometric extreme without this lands the insertion point on one of them on
    # 4 of the 13 checkpoint maps (contact_coop, siege_coop, drycanal_coop,
    # district_coop), and the whole map is then unroutable from the start: a
    # 103 u "end-to-end walk" across contact.
    _n, strong = connected_components(g, directed=True, connection="strong")
    counts = np.bincount(strong[sel], minlength=strong.max() + 1)
    main = int(np.argmax(counts))
    both_ways = ok & (strong == main)
    if both_ways.sum() >= 2:
        ok_end = both_ways
    else:
        ok_end = ok          # nothing mutually routable; the map has bigger problems
    end_sel = np.flatnonzero(ok_end)

    xy = cache.xy(sel)
    c = xy.mean(0)
    _u, _s, vt = np.linalg.svd(xy - c, full_matrices=False)
    t_end = (cache.xy(end_sel) - c) @ vt[0]
    a = (end_sel[int(np.argmin(t_end))] if from_pt is None
         else _nearest_node(cache, ok_end, from_pt))
    b = (end_sel[int(np.argmax(t_end))] if to_pt is None
         else _nearest_node(cache, ok_end, to_pt))
    da = dijkstra(g, directed=True, indices=a)

    # The extremes of the principal axis are geometric, and the graph is
    # directed - one-way drops mean the far end need not be walkable *to*.
    # Ministry is the case in point: its far extreme is only reachable by
    # dropping in. Fall back to the furthest-along point that is actually
    # routable, so insertion -> extraction stays a walk someone could make.
    # An explicitly requested endpoint is left alone; an unreachable one is
    # the caller's business and shows up as a null span.
    if to_pt is None and not np.isfinite(da[b]):
        cand = np.flatnonzero(ok_end & np.isfinite(da))
        if len(cand):
            b = int(cand[int(np.argmax((cache.xy(cand) - c) @ vt[0]))])

    db = dijkstra(g.T.tocsr(), directed=True, indices=b)
    return a, b, da, db


# ---------------------------------------------------------------- scoring
def score(bld, da, db):
    nodes = bld["node_ids"]
    fa, fb = da[nodes], db[nodes]
    bld["dist_a"] = float(fa[np.isfinite(fa)].min()) if np.isfinite(fa).any() else np.inf
    bld["dist_b"] = float(fb[np.isfinite(fb)].min()) if np.isfinite(fb).any() else np.inf
    tot = bld["dist_a"] + bld["dist_b"]
    bld["progress"] = float(bld["dist_a"] / tot) if np.isfinite(tot) and tot > 0 else np.nan

    area = bld["footprint_u2"]
    s_area = float(np.clip(np.log10(max(area, 1.0) / 30000.0) + 1.0, 0.0, 2.0))
    s_ent = float(np.clip((bld["entrances"] - 1) / 3.0, 0.0, 2.0))
    s_vert = float(np.clip((bld["n_storeys"] - 1) * 0.6, 0.0, 1.8))
    s_depth = float(np.clip(bld["depth_u"] / 320.0, 0.0, 1.2))
    named = 0.8 if bld.get("label") else 0.0
    bld["score"] = s_area * 1.3 + s_ent + s_vert + s_depth + named
    bld["score_parts"] = {"size": round(s_area * 1.3, 2), "entrances": round(s_ent, 2),
                          "verticality": round(s_vert, 2), "depth": round(s_depth, 2),
                          "named": named}
    return bld


def pick_chain(cache, cands, start_node, count, min_gap, max_leg,
               min_score=4.5, min_entrances=2, progress=print):
    """Walk the map: from the insertion point, repeatedly take the best place
    that is a sensible patrol leg further on. Objectives you cannot actually
    walk between in a reasonable time do not make a playable push.
    """
    g = _csr(len(cache.agl), cache.src, cache.dst, cache.wgt)
    pool = [x for x in cands if np.isfinite(x["dist_a"]) and x["score"] >= min_score
            and x["entrances"] >= min_entrances]
    chosen: list[dict] = []
    cur = start_node
    leg_cap = max_leg
    while len(chosen) < count and pool:
        d = dijkstra(g, directed=True, indices=[cur])[0]
        best, best_key = None, None
        for cand in pool:
            nodes = cand["node_ids"]
            dv = d[nodes]
            if not np.isfinite(dv).any():
                continue
            leg = float(dv[np.isfinite(dv)].min())
            if leg < min_gap or leg > leg_cap:
                continue
            if chosen and cand["dist_a"] <= chosen[-1]["dist_a"]:
                continue          # keep pushing away from the insertion point
            key = (cand["score"], -leg)
            if best_key is None or key > best_key:
                best, best_key, best_leg = cand, key, leg
        if best is None:
            if leg_cap >= max_leg * 4:
                break
            leg_cap *= 1.6        # nothing in range: allow a longer march
            continue
        best["leg_u"] = None if not chosen else best_leg
        chosen.append(best)
        pool = [x for x in pool if x is not best and
                np.hypot(x["centroid"][0] - best["centroid"][0],
                         x["centroid"][1] - best["centroid"][1]) > min_gap]
        cur = int(best["node_ids"][np.argmin(d[best["node_ids"]])])
        leg_cap = max_leg
    return chosen


def objective_z(cache, bld):
    nodes = bld["node_ids"]
    lowest = nodes[np.argsort(cache.agl[nodes])[:max(1, len(nodes) // 4)]]
    return float(np.median(cache.surf["z"][lowest]))


def radius_for(bld):
    return int(np.clip(bld["depth_u"] * 1.15, 224, 640))


def sealed_regions(cache, indoor, min_cells=400, top=12, sky_agl=1500.0):
    """Standable ground that no route reaches - fences, blockout, missing doors."""
    lab = cache.labels
    unreach = ~cache.reach
    out = []
    cnt = np.bincount(lab[unreach], minlength=lab.max() + 1)
    res = cache.grid.res
    for L in np.argsort(-cnt)[:64]:
        if cnt[L] < min_cells:
            break
        m = (lab == L) & unreach
        agl = cache.agl[m]
        if agl.min() > sky_agl:
            continue
        xy = cache.xy(np.flatnonzero(m))
        ncell = len(np.unique(cache.surf["cell"][m]))
        out.append({
            "cells": int(cnt[L]), "area_u2": int(ncell * res * res),
            "centre": [float(np.median(xy[:, 0])), float(np.median(xy[:, 1]))],
            "size_u": [round(float(np.ptp(xy[:, 0]))), round(float(np.ptp(xy[:, 1])))],
            "agl": [float(agl.min()), float(agl.max())],
        })
        if len(out) >= top:
            break
    return out


# ---------------------------------------------------------------- command
def cmd_objectives(args):
    from .cli import Cache
    from . import render
    from .bsp import Bsp
    c = Cache(args.cache)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    reach = c.reach

    props = ([], np.zeros((0, 3)), np.zeros(0, np.int64))
    if args.bsp:
        props = places.load_props(Bsp(args.bsp))
        print(f"static props: {len(props[1]):,}")

    print("classifying covered floor ...")
    ind = indoor_mask(c)
    print(f"  {int((ind & reach).sum()):,} covered / {int((~ind & reach).sum()):,} open-air cells")
    idx, lab, nlab, lblimg = buildings(c, ind, reach)
    blds = describe(c, idx, lab, nlab, lblimg, ind, reach, props)
    print(f"  {len(blds)} interior spaces >= {MIN_ROOM_CELLS} cells")

    print("routing ...")
    fp = [float(v) for v in args.start.split(",")] if args.start else None
    tp = [float(v) for v in args.end.split(",")] if args.end else None
    a, b, da, db = axis(c, reach, ~ind, fp, tp)
    xa, xb = c.xy([a])[0], c.xy([b])[0]
    span = float(da[b])
    routable = np.isfinite(da) & reach
    print(f"  {int(routable.sum()):,}/{int(reach.sum()):,} reachable cells routable from insertion")
    print(f"  insertion {xa.round(0).tolist()} -> extraction {xb.round(0).tolist()}, "
          f"walk {span:,.0f} u (~{span/16:.0f} m)")

    sealed = sealed_regions(c, ind)
    if sealed:
        print(f"  {len(sealed)} sealed regions, largest "
              f"{sealed[0]['area_u2']:,} u2 at {sealed[0]['centre']}")
    blds = [score(x, da, db) for x in blds]
    chain = pick_chain(c, blds, a, args.count, args.min_gap, args.max_leg,
                       args.min_score, args.min_entrances)
    lm = places.landmark_clusters(*props) if len(props[1]) else []

    letters = "ABCDEFGH"
    rows = []
    for i, o in enumerate(chain):
        z = objective_z(c, o)
        rows.append({
            "name": letters[i], "label": o["label"] or "unnamed structure",
            "x": round(o["centroid"][0]), "y": round(o["centroid"][1]), "z": round(z),
            "agl": round(z - c.ground), "radius": radius_for(o),
            "footprint_u2": int(o["footprint_u2"]), "size_u": o["size_u"],
            "storeys": o["n_storeys"], "entrances": o["entrances"],
            "score": round(o["score"], 2), "parts": o["score_parts"],
            "dist_from_start_u": int(o["dist_a"]) if np.isfinite(o["dist_a"]) else None,
            "evidence": ", ".join(f"{e[0]} x{e[1]} ({e[2]})" for e in o["evidence"][:2]) or "-",
        })
    for r, o in zip(rows, chain):
        leg = o.get("leg_u")
        r["leg_from_prev_u"] = int(leg) if leg is not None and np.isfinite(leg) else None

    (outdir / "objectives.json").write_text(json.dumps({
        "ground_z": c.ground,
        "insertion": [round(float(xa[0])), round(float(xa[1]))],
        "extraction": [round(float(xb[0])), round(float(xb[1]))],
        "span_u": round(span) if np.isfinite(span) else None,
        "objectives": rows, "sealed_regions": sealed,
        "landmarks": [{k: v for k, v in l.items() if k != "evidence"} for l in lm],
    }, indent=2))

    top = sorted(blds, key=lambda x: -x["score"])[:args.list]
    L = ["# Capture-point siting", "",
         f"Ground datum: world z = {c.ground:.0f}; AGL values are relative to it.",
         f"Insertion `{xa[0]:.0f} {xa[1]:.0f}` -> extraction `{xb[0]:.0f} {xb[1]:.0f}`, "
         f"walk {span:,.0f} u (~{span/16:.0f} m).", "",
         "## Proposed sequential chain", "",
         "| # | place | evidence | world x y z | AGL | size | storeys | ways in | radius | walk from prev |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        gap = f"{r['leg_from_prev_u']:,} u" if r.get("leg_from_prev_u") else "-"
        L.append(f"| {r['name']} | {r['label']} | {r['evidence']} | `{r['x']} {r['y']} {r['z']}` | "
                 f"{r['agl']:+d} | {r['size_u'][0]}x{r['size_u'][1]} u | {r['storeys']} | "
                 f"{r['entrances']} | {r['radius']} | {gap} |")
    L += ["", "## All candidate interiors, best first", "",
          "| rank | place | centre | size | storeys | ways in | score |",
          "|---|---|---|---|---|---|---|"]
    for i, t in enumerate(top, 1):
        L.append(f"| {i} | {t['label'] or '-'} | `{t['centroid'][0]:.0f} {t['centroid'][1]:.0f}` | "
                 f"{t['size_u'][0]}x{t['size_u'][1]} u | {t['n_storeys']} | {t['entrances']} | "
                 f"{t['score']:.2f} |")
    if lm:
        L += ["", "## Open-air landmarks (props, not enclosed)", "",
              "| place | centre | props |", "|---|---|---|"]
        for l in lm:
            L.append(f"| {l['label']} | `{l['centroid'][0]:.0f} {l['centroid'][1]:.0f}` | "
                     f"{l['n_props']} |")
    if sealed:
        L += ["", "## Sealed-off regions (walkable floor with no route in)", "",
              "These are standable surfaces that cannot be reached from the main play "
              "area - fenced compounds, unfinished blockout, or missing doorways.", "",
              "| cells | area | centre | extent | AGL |", "|---|---|---|---|---|"]
        for r in sealed:
            L.append(f"| {r['cells']:,} | {r['area_u2']:,} u\u00b2 | "
                     f"`{r['centre'][0]:.0f} {r['centre'][1]:.0f}` | "
                     f"{r['size_u'][0]}x{r['size_u'][1]} u | {r['agl'][0]:+.0f}..{r['agl'][1]:+.0f} |")
    L += ["", "## Entity stubs", "", "```"]
    for r in rows:
        L += [f'// {r["name"]} - {r["label"]}', "{", '  "classname" "ins_objective"',
              f'  "objectivename" "{r["name"]}"',
              f'  "origin" "{r["x"]} {r["y"]} {r["z"]}"',
              f'  "radius" "{r["radius"]}"', "}"]
    L.append("```")
    (outdir / "objectives.md").write_text("\n".join(L))

    markers = [{"xy": (float(xa[0]), float(xa[1])), "color": "#7CFC00", "marker": "P",
                "ms": 11, "label": "INSERT", "fs": 9},
               {"xy": (float(xb[0]), float(xb[1])), "color": "#FF6EC7", "marker": "X",
                "ms": 11, "label": "EXTRACT", "fs": 9}]
    for r in rows:
        markers.append({"xy": (r["x"], r["y"]), "color": "#ffffff", "marker": "o", "ms": 7,
                        "label": f"{r['name']}  {r['label']}", "fs": 10, "radius": r["radius"]})
    for l in lm:
        markers.append({"xy": tuple(l["centroid"]), "color": "#ffcc55", "marker": "v",
                        "ms": 5, "label": l["label"], "fs": 6})
    render.render_overview(c.grid, c.top_z, c.surf, c.agl, reach, c.ground, c.crop(),
                           outdir / "objectives.png", markers=markers,
                           title="Proposed capture-point chain")
    print()
    print("\n".join(L[6:9 + len(rows)]))
    print(f"\nwrote {outdir}/objectives.md, objectives.json, objectives.png")


def add_parser(sub):
    a = sub.add_parser("objectives", help="suggest capture-point locations")
    a.add_argument("cache")
    a.add_argument("--bsp", default=None, help="BSP to read static props from (for names)")
    a.add_argument("--outdir", default="out")
    a.add_argument("--count", type=int, default=6, help="objectives in the chain")
    a.add_argument("--min-gap", type=float, default=2200.0,
                   help="minimum spacing between objectives, world units")
    a.add_argument("--list", type=int, default=25, help="how many candidates to table")
    a.add_argument("--max-leg", type=float, default=6000.0,
                   help="longest acceptable walk between consecutive objectives")
    a.add_argument("--min-entrances", type=int, default=2,
                   help="reject places with fewer separate ways in")
    a.add_argument("--min-score", type=float, default=4.5,
                   help="skip a band rather than site a weak objective")
    a.add_argument("--start", default=None, help="x,y of the insertion point")
    a.add_argument("--end", default=None, help="x,y of the extraction point")
    a.set_defaults(fn=cmd_objectives)
