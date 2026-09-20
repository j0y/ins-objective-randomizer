"""Segment tracing against brush volumes: what can see what.

The obvious implementation - descend the BSP tree and read the leaf's
`contents` - is wrong here, and quietly so. VBSP does not split the tree with
`func_detail`: detail brushes are filtered into the leaves they touch and
recorded in LEAFBRUSHES, while the leaf itself stays CONTENTS_EMPTY. Measured on
the shipped maps, detail is 70% of ministry_coop's brushes and 58% of
buhriz_coop's, and a tree-contents test finds solid at 1% and 0% of their detail
brush centres respectively. A trace built that way passes through most of the
map's walls.

So this walks the tree only to find candidate leaves, then clips the segment
against each leaf brush's half-spaces - `CM_ClipBoxToBrush` for a ray. Interior
is `dot(p, n) <= d` on every side, the same convention `Bsp.brush_polygons`
clips with.

Vectorised the same way `point_contents` is: one array of (ray, node, t0, t1)
work items advanced in lockstep, splitting where a segment straddles a plane, so
the cost is per-batch numpy rather than per-ray Python. Leaves are drained and
their brushes clipped every iteration, which is what makes the early-out work -
in occlusion mode a ray that has hit anything is dropped from the frontier.

Brush entities (`func_brush`, doors, breakables) are separate submodels with
their own headnode, so they are traced separately, gated by a slab test against
each model's bounding box. Glass and grates do not block sight: the default mask
is CONTENTS_SOLID alone, so CONTENTS_WINDOW and CONTENTS_GRATE are see-through
as they are in game.

The same correction applies to walkability, not just sight. `clearance_filter`
used to ask `Bsp.is_solid`, so it could not see detail brushes either: measured
on ministry, 25% of the nodes it called reachable have their ankle point inside a
solid detail brush (median 68 u below that brush's top) and another 19% sit
inside a clip brush. `point_solid` is what those tests use now.

Known approximation: a brush side replaced by a displacement is clipped as the
original brush plane, so terrain blocks sight at the plane rather than at the
displaced surface.
"""
from __future__ import annotations

import re

import numpy as np

from .bsp import Bsp
from .lumps import (CONTENTS_MONSTERCLIP, CONTENTS_PLAYERCLIP, CONTENTS_SOLID,
                    CONTENTS_WINDOW)

SIGHT_MASK = CONTENTS_SOLID
# What stops a body rather than a photon: glass and the clip brushes count.
MOVE_MASK = CONTENTS_SOLID | CONTENTS_WINDOW | CONTENTS_MONSTERCLIP | CONTENTS_PLAYERCLIP

# Brush entities whose brushes carry CONTENTS_SOLID but are not solid in game.
# The brush was drawn as a solid brush, so vbsp writes SOLID; the entity turns
# itself non-solid on spawn. Their faces are tool-textured, which is why
# `Bsp.triangles` filters them on SURF_TRIGGER and never noticed - a brush trace
# reads BRUSHES and never sees a texture flag.
#
# Swept over all 49 shipped maps, these are the classnames that matter and the
# SOLID brushes each would contribute: func_dustmotes 2758 (all 49 maps),
# ins_blockzone 891, trigger_capture_zone 753, ins_spawnzone 711,
# func_detail_blocker 226 (a vvis hint volume), plus the trigger_* family.
# A spawn zone spans a whole spawn area, so leaving these in walls off sight
# across the middle of the map.
_PHANTOM_SOLID = re.compile(
    r"^(trigger_"
    r"|ins_(spawnzone|blockzone)"
    r"|func_(dustmotes|detail_blocker|weapon_lower)"
    r"|game_zone_player)", re.I)
# func_brush's "Never Solid" setting. Not used by any shipped map, but a
# generated one has no excuse for getting it wrong.
_NEVER_SOLID = "1"
# Segments are pulled in from both ends before tracing. Sample points sit on
# floors and against walls, so a ray that starts exactly on a surface would
# otherwise report a hit at t=0 for every direction.
NUDGE = 2.0
_PARALLEL = 1e-9
_ON_PLANE = 1e-4
_MAX_DEPTH = 256


class Tracer:
    """Segment-vs-brush queries against one compiled map."""

    def __init__(self, bsp: Bsp, mask: int = SIGHT_MASK, include_entities: bool = True,
                 include_displacements: bool = True):
        self.bsp = bsp
        self.mask = int(mask)
        self.pn = bsp.planes["normal"].astype(np.float64)
        self.pd = bsp.planes["dist"].astype(np.float64)
        self.nplane = bsp.nodes["planenum"].astype(np.int64)
        self.nchild = bsp.nodes["children"].astype(np.int64)
        self.lfirst = bsp.leafs["firstleafbrush"].astype(np.int64)
        self.lnum = bsp.leafs["numleafbrushes"].astype(np.int64)
        self.leafbrushes = bsp.leafbrushes
        self.nbrush = len(bsp.brushes)

        cont = bsp.brushes["contents"].astype(np.int64)
        self.brush_ok = (cont & self.mask) != 0
        # A displacement brush's volume is not what it collides as; the mesh is.
        self.brush_ok &= ~bsp.disp_brush
        self.disp = _TriSoup(bsp.displacement_triangles() if include_displacements
                             else np.zeros((0, 3, 3)))

        # Per-brush side list with bevel planes dropped: they are tangent
        # padding for box sweeps and redundant for a ray.
        bfirst = bsp.brushes["firstside"].astype(np.int64)
        bnum = bsp.brushes["numsides"].astype(np.int64)
        keep_side = bsp.brushsides["bevel"] == 0
        planes, starts, counts = [], np.zeros(self.nbrush, np.int64), np.zeros(self.nbrush, np.int64)
        cursor = 0
        bs_plane = bsp.brushsides["planenum"].astype(np.int64)
        for bi in range(self.nbrush):
            if not self.brush_ok[bi]:
                continue
            sl = slice(int(bfirst[bi]), int(bfirst[bi] + bnum[bi]))
            pl = bs_plane[sl][keep_side[sl]]
            if len(pl) < 4:
                self.brush_ok[bi] = False       # degenerate; cannot bound a volume
                continue
            planes.append(pl)
            starts[bi] = cursor
            counts[bi] = len(pl)
            cursor += len(pl)
        self.side_plane = np.concatenate(planes) if planes else np.zeros(0, np.int64)
        self.bstart, self.bcount = starts, counts

        # Brush entities: (headnode, mins, maxs, origin) for the slab prefilter.
        self.ent_models: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]] = []
        if include_entities:
            solidity = {}
            for e in bsp.entities:
                mi = e.model_index
                if mi is not None:
                    solidity[mi] = (e.props.get("Solidity")
                                    or e.props.get("solidity") or "")
            for mi, org, cls in bsp.solid_entity_models():
                if _PHANTOM_SOLID.match(cls) or solidity.get(mi) == _NEVER_SOLID:
                    continue
                m = bsp.models[mi]
                org = np.asarray(org, float)
                self.ent_models.append((int(m["headnode"]),
                                        m["mins"].astype(float) + org,
                                        m["maxs"].astype(float) + org, org))

    # ---------------------------------------------------------------- points
    def point_leaf(self, pts: np.ndarray) -> np.ndarray:
        """Leaf index containing each point (world tree)."""
        pts = np.ascontiguousarray(pts, dtype=np.float64)
        node = np.zeros(len(pts), np.int64)
        active = np.arange(len(pts))
        out = np.zeros(len(pts), np.int64)
        for _ in range(_MAX_DEPTH):
            if not active.size:
                break
            cur = node[active]
            leaf = cur < 0
            if leaf.any():
                out[active[leaf]] = -1 - cur[leaf]
                active = active[~leaf]
                if not active.size:
                    break
                cur = node[active]
            pl = self.nplane[cur]
            d = (pts[active] * self.pn[pl]).sum(1) - self.pd[pl]
            node[active] = np.where(d >= 0, self.nchild[cur, 0], self.nchild[cur, 1])
        return out

    def point_solid(self, pts: np.ndarray, chunk: int = 200_000) -> np.ndarray:
        """Is each point inside a masked brush? Detail brushes included.

        `Bsp.point_contents` cannot answer this - see the module docstring - so
        this is the honest version of `Bsp.is_solid` for anything that has to
        agree with what the player collides with.

        Chunked because the intermediate is (points x leaf brushes x sides): a
        single 2M-point call is several GB of temporaries, which is enough to
        put the corpus sweep over its per-job memory ceiling.
        """
        pts = np.ascontiguousarray(pts, dtype=np.float64)
        out = np.zeros(len(pts), bool)
        for s in range(0, len(pts), chunk):
            e = min(len(pts), s + chunk)
            out[s:e] = self._point_solid_chunk(pts[s:e])
        return out

    def _point_solid_chunk(self, pts):
        leaf = self.point_leaf(pts)
        out = np.zeros(len(pts), bool)
        pt_i, brush = self._leaf_brushes(np.arange(len(pts)), leaf)
        if not len(brush):
            return out
        sides, group, nb = self._brush_sides(brush)
        if not nb:
            return out
        d = (pts[pt_i[group]] * self.pn[sides]).sum(1) - self.pd[sides]
        starts = self._group_starts(brush)
        inside = np.maximum.reduceat(d, starts) <= _ON_PLANE
        out[pt_i[inside]] = True
        return out

    # ---------------------------------------------------------------- rays
    def occluded(self, a: np.ndarray, b: np.ndarray, nudge: float = NUDGE,
                 chunk: int = 65536) -> np.ndarray:
        """Is the segment a->b blocked? Early-outs on the first hit per ray."""
        return self._trace(a, b, nudge, chunk, nearest=False) < np.inf

    def first_hit(self, a: np.ndarray, b: np.ndarray, nudge: float = NUDGE,
                  chunk: int = 65536) -> np.ndarray:
        """Fraction along a->b of the first hit, or inf if the segment is clear."""
        return self._trace(a, b, nudge, chunk, nearest=True)

    def sight_range(self, origin: np.ndarray, direction: np.ndarray, maxdist: float,
                    **kw) -> np.ndarray:
        """How far you can see from each origin along each direction."""
        d = np.asarray(direction, float)
        d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
        t = self.first_hit(origin, origin + d * maxdist, **kw)
        return np.where(np.isfinite(t), t * maxdist, maxdist)

    # ---------------------------------------------------------------- kernel
    def _trace(self, a, b, nudge, chunk, nearest):
        a = np.ascontiguousarray(a, dtype=np.float64).reshape(-1, 3)
        b = np.ascontiguousarray(b, dtype=np.float64).reshape(-1, 3)
        if len(a) != len(b):
            raise ValueError(f"{len(a)} origins vs {len(b)} endpoints")
        if nudge:
            v = b - a
            ln = np.linalg.norm(v, axis=1, keepdims=True)
            step = v / np.maximum(ln, 1e-9) * np.minimum(ln * 0.25, nudge)
            a = a + step
            b = b - step
        best = np.full(len(a), np.inf)
        for s in range(0, len(a), chunk):
            e = min(len(a), s + chunk)
            self._trace_chunk(a[s:e], b[s:e], best[s:e], nearest)
        return best

    def _trace_chunk(self, a, b, best, nearest):
        self._descend(a, b, best, nearest, headnode=0)
        self.disp.hit(a, b, best, nearest)
        for headnode, mn, mx, org in self.ent_models:
            live = self._slab(a, b, mn, mx, best, nearest)
            if not live.any():
                continue
            idx = np.flatnonzero(live)
            sub = best[idx]
            self._descend(a[idx] - org, b[idx] - org, sub, nearest, headnode)
            best[idx] = np.minimum(best[idx], sub)

    @staticmethod
    def _slab(a, b, mn, mx, best, nearest):
        """Rays whose segment overlaps an AABB, ignoring already-settled rays."""
        live = np.isinf(best) if not nearest else np.ones(len(a), bool)
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        for ax in range(3):
            live &= (hi[:, ax] >= mn[ax]) & (lo[:, ax] <= mx[ax])
        return live

    def _descend(self, a, b, best, nearest, headnode):
        n = len(a)
        if not n:
            return
        it_ray = np.arange(n, dtype=np.int64)
        it_node = np.full(n, headnode, np.int64)
        it_t0 = np.zeros(n)
        it_t1 = np.ones(n)
        for _ in range(_MAX_DEPTH):
            if not len(it_ray):
                return
            isleaf = it_node < 0
            if isleaf.any():
                self._hit_leaves(a, b, it_ray[isleaf], -1 - it_node[isleaf], best, nearest)
                keep = ~isleaf
                it_ray, it_node = it_ray[keep], it_node[keep]
                it_t0, it_t1 = it_t0[keep], it_t1[keep]
                if not len(it_ray):
                    return
            # Anything that can no longer improve the answer is dead weight.
            alive = it_t0 < best[it_ray] if nearest else np.isinf(best[it_ray])
            if not alive.all():
                it_ray, it_node = it_ray[alive], it_node[alive]
                it_t0, it_t1 = it_t0[alive], it_t1[alive]
                if not len(it_ray):
                    return
            pl = self.nplane[it_node]
            N = self.pn[pl]
            D = self.pd[pl]
            da = (a[it_ray] * N).sum(1) - D
            db = (b[it_ray] * N).sum(1) - D
            slope = db - da
            d0 = da + slope * it_t0
            d1 = da + slope * it_t1
            f0 = d0 >= 0
            f1 = d1 >= 0
            near = self.nchild[it_node, np.where(f0, 0, 1)]
            same = f0 == f1
            if same.all():
                it_node = near
                continue
            split = ~same
            tmid = it_t0[split] + (it_t1[split] - it_t0[split]) * \
                (d0[split] / (d0[split] - d1[split]))
            # A straddling item becomes the near half [t0, tmid]; its far half
            # [tmid, t1] is appended. Order does not matter - the reduction is a
            # min over hits, so every leaf the segment crosses gets enumerated
            # regardless of which side is visited first.
            near_t1 = it_t1.copy()
            near_t1[split] = tmid
            it_ray = np.concatenate([it_ray, it_ray[split]])
            it_node = np.concatenate([near, self.nchild[it_node[split],
                                                        np.where(f1[split], 0, 1)]])
            it_t0 = np.concatenate([it_t0, tmid])
            it_t1 = np.concatenate([near_t1, it_t1[split]])
        raise RuntimeError("BSP descent exceeded depth limit")

    def _hit_leaves(self, a, b, ray, leaf, best, nearest):
        ray_i, brush = self._leaf_brushes(ray, leaf)
        if not len(brush):
            return
        sides, group, nb = self._brush_sides(brush)
        if not nb:
            return
        N = self.pn[sides]
        D = self.pd[sides]
        r = ray_i[group]
        ds = (a[r] * N).sum(1) - D
        de = (b[r] * N).sum(1) - D
        den = de - ds
        par = np.abs(den) < _PARALLEL
        t = np.where(par, 0.0, -ds / np.where(par, 1.0, den))
        starts = self._group_starts(brush)
        outside = np.maximum.reduceat((par & (ds > _ON_PLANE)).astype(np.int8), starts)
        enter = np.maximum(0.0, np.maximum.reduceat(
            np.where(~par & (den < 0), t, -np.inf), starts))
        exit_ = np.minimum(1.0, np.minimum.reduceat(
            np.where(~par & (den > 0), t, np.inf), starts))
        hit = (outside == 0) & (enter <= exit_)
        if not hit.any():
            return
        hray = ray_i[hit]
        if not nearest:
            best[hray] = 0.0
            return
        ht = enter[hit]
        order = np.argsort(hray, kind="stable")
        hray, ht = hray[order], ht[order]
        edge = np.flatnonzero(np.r_[True, hray[1:] != hray[:-1]])
        best[hray[edge]] = np.minimum(best[hray[edge]], np.minimum.reduceat(ht, edge))

    # ---------------------------------------------------------------- ragged
    def _leaf_brushes(self, ray, leaf):
        """(ray index, brush index) for every masked brush in each leaf, deduped."""
        cnt = self.lnum[leaf]
        total = int(cnt.sum())
        if not total:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        idx = np.repeat(np.arange(len(cnt)), cnt)
        off = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        brush = self.leafbrushes[self.lfirst[leaf][idx] + off]
        ok = self.brush_ok[brush]
        brush, ray_i = brush[ok], ray[idx[ok]]
        if not len(brush):
            return ray_i, brush
        # One brush spans many leaves, so the same (ray, brush) pair recurs.
        key = ray_i * self.nbrush + brush
        _, first = np.unique(key, return_index=True)
        return ray_i[first], brush[first]

    def _brush_sides(self, brush):
        cnt = self.bcount[brush]
        total = int(cnt.sum())
        if not total:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), 0
        group = np.repeat(np.arange(len(cnt)), cnt)
        off = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        return self.side_plane[self.bstart[brush][group] + off], group, len(brush)

    def _group_starts(self, brush):
        cnt = self.bcount[brush]
        return np.cumsum(cnt) - cnt

class _TriSoup:
    """Segment-vs-triangle tracing over a uniform xy grid.

    Only displacements need this. They are a mesh rather than a brush volume, so
    they cannot go through the brush clip, and outdoors they are what most sight
    rays actually stop against - approximating them at the original brush plane
    is wrong by up to 1544 units (sinjar). Every shipped map has displacements.

    Grid traversal is Amanatides-Woo in xy, all rays advanced one cell per
    iteration in lockstep. Each cell carries the z range of its triangles, which
    rejects most visits before a triangle is touched: a ray crossing a valley at
    eye height passes over hundreds of terrain cells and can only hit the few
    whose z range it actually enters.
    """

    def __init__(self, tris: np.ndarray, target_per_cell: int = 6, max_axis: int = 512):
        self.n = len(tris)
        if not self.n:
            return
        self.v0 = tris[:, 0]
        self.e1 = tris[:, 1] - tris[:, 0]
        self.e2 = tris[:, 2] - tris[:, 0]
        lo = tris.min(1)
        hi = tris.max(1)
        self.gmin = lo.min(0)
        self.gmax = hi.max(0)
        span = np.maximum(self.gmax[:2] - self.gmin[:2], 1.0)
        naxis = int(np.clip(np.sqrt(self.n / target_per_cell), 1, max_axis))
        self.nx = self.ny = naxis
        self.cw = span / np.array([self.nx, self.ny], float)
        ix0, iy0 = self._cell(lo[:, 0], lo[:, 1])
        ix1, iy1 = self._cell(hi[:, 0], hi[:, 1])
        cnt = (ix1 - ix0 + 1) * (iy1 - iy0 + 1)
        total = int(cnt.sum())
        tri = np.repeat(np.arange(self.n), cnt)
        off = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        w = (ix1 - ix0 + 1)[tri]
        cx = ix0[tri] + off % w
        cy = iy0[tri] + off // w
        cell = cy * self.nx + cx
        order = np.argsort(cell, kind="stable")
        self.cell_tri = tri[order].astype(np.int64)
        counts = np.bincount(cell[order], minlength=self.nx * self.ny)
        self.cell_start = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
        self.cell_count = counts.astype(np.int64)
        zlo = np.full(self.nx * self.ny, np.inf)
        zhi = np.full(self.nx * self.ny, -np.inf)
        np.minimum.at(zlo, cell, lo[tri, 2])
        np.maximum.at(zhi, cell, hi[tri, 2])
        self.cell_zlo, self.cell_zhi = zlo, zhi
        self.max_steps = 2 * (self.nx + self.ny) + 4

    def _cell(self, x, y):
        ix = np.clip(((x - self.gmin[0]) / self.cw[0]).astype(np.int64), 0, self.nx - 1)
        iy = np.clip(((y - self.gmin[1]) / self.cw[1]).astype(np.int64), 0, self.ny - 1)
        return ix, iy

    def hit(self, a, b, best, nearest):
        if not self.n:
            return
        d = b - a
        t_lo = np.zeros(len(a))
        t_hi = np.ones(len(a))
        with np.errstate(divide="ignore", invalid="ignore"):
            for ax in range(3):
                inv = 1.0 / d[:, ax]
                p = (self.gmin[ax] - a[:, ax]) * inv
                q = (self.gmax[ax] - a[:, ax]) * inv
                flat = d[:, ax] == 0
                near = np.where(flat, -np.inf, np.minimum(p, q))
                far = np.where(flat, np.inf, np.maximum(p, q))
                outside = flat & ((a[:, ax] < self.gmin[ax]) | (a[:, ax] > self.gmax[ax]))
                t_lo = np.maximum(t_lo, near)
                t_hi = np.minimum(t_hi, np.where(outside, -np.inf, far))
        alive = t_lo < t_hi
        if nearest:
            alive &= t_lo < best
        else:
            alive &= np.isinf(best)
        if not alive.any():
            return
        r = np.flatnonzero(alive)
        ar, dr = a[r], d[r]
        tlo, thi = t_lo[r], t_hi[r]
        entry = ar + dr * tlo[:, None]
        ix, iy = self._cell(entry[:, 0], entry[:, 1])
        stepx = np.sign(dr[:, 0]).astype(np.int64)
        stepy = np.sign(dr[:, 1]).astype(np.int64)
        with np.errstate(divide="ignore", invalid="ignore"):
            bx = self.gmin[0] + (ix + (stepx > 0)) * self.cw[0]
            by = self.gmin[1] + (iy + (stepy > 0)) * self.cw[1]
            tnx = np.where(stepx == 0, np.inf, (bx - ar[:, 0]) / dr[:, 0])
            tny = np.where(stepy == 0, np.inf, (by - ar[:, 1]) / dr[:, 1])
            tdx = np.where(stepx == 0, np.inf, self.cw[0] / np.abs(dr[:, 0]))
            tdy = np.where(stepy == 0, np.inf, self.cw[1] / np.abs(dr[:, 1]))
        t_in = tlo
        live = np.ones(len(r), bool)
        for _ in range(self.max_steps):
            if not live.any():
                return
            t_out = np.minimum(np.minimum(tnx, tny), thi)
            self._cells(ar, dr, r, ix, iy, t_in, t_out, live, best, nearest)
            advx = tnx < tny
            t_in = np.where(advx, tnx, tny)
            ix = np.where(advx, ix + stepx, ix)
            iy = np.where(advx, iy, iy + stepy)
            tnx = np.where(advx, tnx + tdx, tnx)
            tny = np.where(advx, tny, tny + tdy)
            live &= (t_in < thi) & (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
            if nearest:
                live &= t_in < best[r]
            else:
                live &= np.isinf(best[r])

    def _cells(self, ar, dr, r, ix, iy, t_in, t_out, live, best, nearest):
        sel = np.flatnonzero(live)
        if not len(sel):
            return
        cell = iy[sel] * self.nx + ix[sel]
        cnt = self.cell_count[cell]
        # The z range the ray occupies while inside this cell, against the z
        # range of the cell's triangles.
        z0 = ar[sel, 2] + dr[sel, 2] * t_in[sel]
        z1 = ar[sel, 2] + dr[sel, 2] * t_out[sel]
        keep = (cnt > 0) & (np.minimum(z0, z1) <= self.cell_zhi[cell]) \
            & (np.maximum(z0, z1) >= self.cell_zlo[cell])
        sel, cell, cnt = sel[keep], cell[keep], cnt[keep]
        if not len(sel):
            return
        total = int(cnt.sum())
        g = np.repeat(np.arange(len(sel)), cnt)
        off = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        tri = self.cell_tri[self.cell_start[cell][g] + off]
        ray = sel[g]
        t = self._moller(ar[ray], dr[ray], tri)
        ok = np.isfinite(t)
        if not ok.any():
            return
        gray = r[ray[ok]]
        gt = t[ok]
        if not nearest:
            best[gray] = 0.0
            return
        order = np.argsort(gray, kind="stable")
        gray, gt = gray[order], gt[order]
        edge = np.flatnonzero(np.r_[True, gray[1:] != gray[:-1]])
        best[gray[edge]] = np.minimum(best[gray[edge]], np.minimum.reduceat(gt, edge))

    def _moller(self, orig, dvec, tri):
        e1 = self.e1[tri]
        e2 = self.e2[tri]
        pv = np.cross(dvec, e2)
        det = (e1 * pv).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = orig - self.v0[tri]
        u = (tv * pv).sum(1) * inv
        qv = np.cross(tv, e1)
        v = (dvec * qv).sum(1) * inv
        t = (e2 * qv).sum(1) * inv
        good = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
        return np.where(good, t, np.inf)
