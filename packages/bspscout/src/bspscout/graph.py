"""Connect standable surfaces into a movement graph and flood-fill from spawns."""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .bsp import Bsp
from .nav import JUMP_HEIGHT, MAX_DROP, STEP_HEIGHT, Grid

MAXK = 8   # max floors stacked in one cell


def build_slots(grid: Grid, surf: dict):
    """Dense [cell, k] lookup tables: node id and z of each floor layer in a cell."""
    cell = surf["cell"]
    ncell = grid.w * grid.h
    counts = np.bincount(cell, minlength=ncell)
    order = np.lexsort((surf["z"], cell))
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    k = np.arange(len(cell)) - starts[cell[order]]
    keep = k < MAXK
    slot_id = np.full((ncell, MAXK), -1, np.int32)
    slot_z = np.full((ncell, MAXK), np.nan, np.float32)
    slot_id[cell[order][keep], k[keep]] = order[keep].astype(np.int32)
    slot_z[cell[order][keep], k[keep]] = surf["z"][order][keep]
    return slot_id, slot_z


def _dir_pairs(grid: Grid, slot_id, slot_z, dx, dy):
    """All (node_a, node_b, dz) pairs between cell c and c+(dx,dy)."""
    w, h = grid.w, grid.h
    ax0, ax1 = max(0, -dx), w - max(0, dx)
    ay0, ay1 = max(0, -dy), h - max(0, dy)
    ys = np.arange(ay0, ay1)
    xs = np.arange(ax0, ax1)
    base = (ys[:, None] * w + xs[None, :]).ravel()
    other = base + dy * w + dx
    out_a, out_b, out_dz = [], [], []
    for ka in range(MAXK):
        za = slot_z[base, ka]
        va = np.isfinite(za)
        if not va.any():
            break
        for kb in range(MAXK):
            zb = slot_z[other, kb]
            m = va & np.isfinite(zb)
            if not m.any():
                continue
            dz = zb[m] - za[m]
            keep = np.abs(dz) <= MAX_DROP
            if not keep.any():
                continue
            idx = np.flatnonzero(m)[keep]
            out_a.append(slot_id[base[idx], ka])
            out_b.append(slot_id[other[idx], kb])
            out_dz.append(dz[keep])
    if not out_a:
        return (np.zeros(0, np.int32),) * 2 + (np.zeros(0, np.float32),)
    return np.concatenate(out_a), np.concatenate(out_b), np.concatenate(out_dz)


def _vertical_clear(bsp: Bsp, grid: Grid, surf: dict, a, b, dz, solid=None):
    """Reject jump/drop links whose vertical span passes through solid (e.g. a ceiling)."""
    solid = bsp.is_solid if solid is None else solid
    lo = np.where(dz >= 0, a, b)
    hi = np.where(dz >= 0, b, a)
    zlo = surf["z"][lo].astype(np.float64)
    zhi = surf["z"][hi].astype(np.float64)
    cl = surf["cell"][lo]
    xy = grid.world_of((cl % grid.w).astype(np.float64), (cl // grid.w).astype(np.float64))
    ok = np.ones(len(a), bool)
    for t in (0.35, 0.65, 0.95):
        z = zlo + (zhi - zlo + 40.0) * t
        p = np.stack([xy[:, 0], xy[:, 1], z], axis=1)
        ok &= ~solid(p)
    return ok


def build_edges(bsp: Bsp, grid: Grid, surf: dict, slot_id, slot_z,
                allow_jump=True, allow_drop=True, progress=print, solid=None):
    src, dst, wgt = [], [], []
    res = grid.res
    dirs = [(1, 0, res), (0, 1, res), (1, 1, res * 1.4142), (1, -1, res * 1.4142)]
    for dx, dy, cost in dirs:
        a, b, dz = _dir_pairs(grid, slot_id, slot_z, dx, dy)
        if not len(a):
            continue
        adz = np.abs(dz)
        walk = adz <= STEP_HEIGHT
        if dx and dy:      # diagonals: only where both orthogonal cells are also floor
            cl = surf["cell"][a]
            ok1 = np.isfinite(slot_z[cl + dx, 0]) | np.isfinite(slot_z[cl + dx, 1])
            ok2 = np.isfinite(slot_z[cl + dy * grid.w, 0]) | np.isfinite(slot_z[cl + dy * grid.w, 1])
            walk &= ok1 & ok2
        src.append(a[walk]); dst.append(b[walk]); wgt.append(np.full(walk.sum(), cost, np.float32))
        src.append(b[walk]); dst.append(a[walk]); wgt.append(np.full(walk.sum(), cost, np.float32))
        # every height-separated pair gets BOTH directions considered: you can
        # always fall down a ledge, but only climb one you can jump.
        special = ~walk & (np.abs(dz) <= MAX_DROP)
        if dx and dy:
            special &= False
        if special.any():
            sa, sb, sdz = a[special], b[special], dz[special]
            clear = _vertical_clear(bsp, grid, surf, sa, sb, sdz, solid)
            sa, sb, sdz = sa[clear], sb[clear], sdz[clear]
            lo = np.where(sdz >= 0, sa, sb)      # lower node
            hi = np.where(sdz >= 0, sb, sa)      # higher node
            rise = np.abs(sdz)
            if allow_drop:
                fall = rise <= MAX_DROP
                src.append(hi[fall]); dst.append(lo[fall])
                wgt.append(np.full(int(fall.sum()), cost * 1.5, np.float32))
            if allow_jump:
                climb = rise <= JUMP_HEIGHT
                src.append(lo[climb]); dst.append(hi[climb])
                wgt.append(np.full(int(climb.sum()), cost * 3.0, np.float32))
        progress(f"  dir({dx},{dy}): {int(walk.sum()):,} walk, "
                 f"{int((~walk & (np.abs(dz) <= MAX_DROP)).sum()):,} jump/drop pairs")
    return np.concatenate(src), np.concatenate(dst), np.concatenate(wgt)


def link_volumes(bsp: Bsp, grid: Grid, surf: dict, slot_id, slot_z, progress=print):
    """Ladders and teleports: extra edges the geometry alone cannot express."""
    src, dst, wgt = [], [], []
    nodes_in = lambda mins, maxs: _nodes_in_box(grid, surf, mins, maxs)

    nlad = 0
    for e in bsp.entities:
        if e.classname not in ("func_simpleladder", "func_useableladder", "func_ladder"):
            continue
        mi = e.model_index
        if mi is None:
            continue
        m = bsp.models[mi]
        org = e.origin if e.origin is not None else np.zeros(3)
        mins = m["mins"].astype(float) + org - np.array([24.0, 24.0, 0.0])
        maxs = m["maxs"].astype(float) + org + np.array([24.0, 24.0, 8.0])
        nodes = nodes_in(mins, maxs)
        if len(nodes) < 2:
            continue
        z = surf["z"][nodes]
        bot = nodes[z <= z.min() + 32]
        top = nodes[z >= z.max() - 32]
        for a in bot:
            for b in top:
                src += [a, b]; dst += [b, a]
                wgt += [float(z.max() - z.min()) * 1.5] * 2
        nlad += 1

    dests = {e.props.get("targetname", ""): e for e in bsp.entities
             if e.classname == "info_teleport_destination"}
    ntp = 0
    for e in bsp.entities:
        if e.classname != "trigger_teleport":
            continue
        tgt = dests.get(e.props.get("target", ""))
        mi = e.model_index
        if tgt is None or mi is None or tgt.origin is None:
            continue
        m = bsp.models[mi]
        org = e.origin if e.origin is not None else np.zeros(3)
        a_nodes = nodes_in(m["mins"].astype(float) + org, m["maxs"].astype(float) + org)
        b_nodes = nodes_in(tgt.origin - np.array([64.0, 64.0, 16.0]),
                           tgt.origin + np.array([64.0, 64.0, 96.0]))
        if not len(a_nodes) or not len(b_nodes):
            continue
        for a in a_nodes[:200]:
            src.append(a); dst.append(b_nodes[0]); wgt.append(1.0)
        ntp += 1
    progress(f"  linked {nlad} ladders, {ntp} teleports ({len(src)} extra edges)")
    if not src:
        return (np.zeros(0, np.int32),) * 2 + (np.zeros(0, np.float32),)
    return (np.asarray(src, np.int32), np.asarray(dst, np.int32), np.asarray(wgt, np.float32))


def _nodes_in_box(grid: Grid, surf: dict, mins, maxs) -> np.ndarray:
    cx = surf["cell"] % grid.w
    cy = surf["cell"] // grid.w
    x = grid.origin[0] + (cx + 0.5) * grid.res
    y = grid.origin[1] + (cy + 0.5) * grid.res
    z = surf["z"]
    m = ((x >= mins[0]) & (x <= maxs[0]) & (y >= mins[1]) & (y <= maxs[1])
         & (z >= mins[2] - 8) & (z <= maxs[2] + 8))
    return np.flatnonzero(m)


SPAWN_CLASSES = ("ins_spawnpoint", "info_player_start", "ins_spawnzone")


def spawn_nodes(bsp: Bsp, grid: Grid, surf: dict, classes=SPAWN_CLASSES,
                cap: int = 96) -> np.ndarray:
    """Nodes under the map's spawn entities.

    Capped, and the cap is not laziness: a shipped map carries 400-670
    `ins_spawnpoint`, each costing a full pass over every standable node to
    place, and the only thing the result is used for is identifying which
    component the play space is. Ninety-six of them settle that.
    """
    ents = [e for e in bsp.entities if e.classname in classes and e.origin is not None]
    if len(ents) > cap:
        ents = ents[:: max(1, len(ents) // cap)][:cap]
    out = []
    for e in ents:
        o = e.origin
        n = _nodes_in_box(grid, surf, o - np.array([64.0, 64.0, 80.0]),
                          o + np.array([64.0, 64.0, 32.0]))
        if len(n):
            d = np.abs(surf["z"][n] - o[2])
            out.append(int(n[np.argmin(d)]))
    return np.unique(np.asarray(out, np.int64))


def analyse(nnodes, src, dst, wgt, seeds):
    g = coo_matrix((wgt, (src, dst)), shape=(nnodes, nnodes)).tocsr()
    ncomp, labels = connected_components(g, directed=True, connection="weak")
    dist = dijkstra(g, directed=True, indices=seeds, min_only=True) if len(seeds) else \
        np.full(nnodes, np.inf)
    return g, ncomp, labels, dist


def link_prop_stairs(bsp: Bsp, grid: Grid, surf: dict, radius=176.0, progress=print):
    """Static props are not in the BSP tree, so prop stairs and ladders leave no
    trace in the collision hull. Link floors vertically wherever one stands."""
    import re

    from .places import load_props
    names, org, ptype = load_props(bsp)
    if len(org) == 0:
        return (np.zeros(0, np.int32),) * 2 + (np.zeros(0, np.float32),)
    rx = re.compile(r"stair|ladder|lestn|steps", re.I)
    want = np.array([bool(rx.search(names[int(t)])) for t in ptype])
    pts = org[want]
    if not len(pts):
        return (np.zeros(0, np.int32),) * 2 + (np.zeros(0, np.float32),)

    cx = (surf["cell"] % grid.w).astype(np.float64)
    cy = (surf["cell"] // grid.w).astype(np.float64)
    nx = grid.origin[0] + (cx + 0.5) * grid.res
    ny = grid.origin[1] + (cy + 0.5) * grid.res
    nz = surf["z"]
    order = np.lexsort((nx, ny))
    src, dst, wgt = [], [], []
    for p in pts:
        m = (np.abs(nx - p[0]) < radius) & (np.abs(ny - p[1]) < radius) \
            & (nz > p[2] - 128) & (nz < p[2] + 512)
        n = np.flatnonzero(m)
        if len(n) < 2:
            continue
        z = nz[n]
        o = np.argsort(z)
        n, z = n[o], z[o]
        brk = np.flatnonzero(np.diff(z) > 40.0)
        groups = np.split(n, brk + 1)
        for g0, g1 in zip(groups[:-1], groups[1:]):
            a = g0[len(g0) // 2]; b = g1[len(g1) // 2]
            cost = float(abs(nz[b] - nz[a])) * 1.4 + grid.res
            src += [int(a), int(b)]; dst += [int(b), int(a)]; wgt += [cost, cost]
    progress(f"  linked {len(pts)} stair/ladder props ({len(src)} edges)")
    if not src:
        return (np.zeros(0, np.int32),) * 2 + (np.zeros(0, np.float32),)
    return (np.asarray(src, np.int32), np.asarray(dst, np.int32),
            np.asarray(wgt, np.float32))
