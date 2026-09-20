"""Turn BSP geometry into a walkable-surface graph ("where can a bot stand and go")."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bsp import Bsp

# Source player metrics (units). Insurgency uses stock HL2-ish hull sizes.
STAND_HEIGHT = 72.0
CROUCH_HEIGHT = 36.0
HULL_HALFWIDTH = 16.0
STEP_HEIGHT = 18.0
JUMP_HEIGHT = 56.0        # crouch-jump reach
MAX_DROP = 250.0          # survivable-ish drop
MAX_SLOPE_NZ = 0.7        # cos(~45.5 deg): steeper than this is not standable


@dataclass
class Grid:
    res: float
    origin: np.ndarray      # world (x, y) of cell (0, 0) corner
    w: int
    h: int

    def cell_of(self, xy: np.ndarray) -> np.ndarray:
        return np.floor((xy - self.origin) / self.res).astype(np.int64)

    def world_of(self, ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
        return np.stack([
            self.origin[0] + (ix + 0.5) * self.res,
            self.origin[1] + (iy + 0.5) * self.res,
        ], axis=-1)

    @property
    def extent(self):
        return (self.origin[0], self.origin[0] + self.w * self.res,
                self.origin[1], self.origin[1] + self.h * self.res)


def _bary_pattern(level: int) -> np.ndarray:
    """Regular barycentric sample pattern inside the unit triangle."""
    i, j = np.meshgrid(np.arange(level + 1), np.arange(level + 1), indexing="ij")
    keep = (i + j) <= level
    a = i[keep] / level
    b = j[keep] / level
    return np.stack([1.0 - a - b, a, b], axis=1)


def _tri_normals(tris):
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1)
    good = ln > 1e-6
    return tris[good], nrm[good, 2] / ln[good]


def rasterize(bsp: Bsp, res: float, zres: float = 8.0, progress=print):
    """Top-down rasterisation.

    Standable surfaces come from *collision* geometry (brush planes +
    displacements) so nodraw greybox and clip brushes are included; the visual
    roof heightmap comes from the drawn faces.

    Returns (grid, top_z, surf).
    """
    vis_tris, _tags = bsp.triangles()
    col_tris = bsp.collision_triangles()
    tris = np.concatenate([vis_tris, col_tris])
    is_col = np.zeros(len(tris), bool)
    is_col[len(vis_tris):] = True
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1)
    good = ln > 1e-6
    tris = tris[good]
    nz = nrm[good, 2] / ln[good]
    is_col = is_col[good]
    progress(f"  {int((~is_col).sum()):,} drawn tris, {int(is_col.sum()):,} collision tris")

    pts_all = tris.reshape(-1, 3)
    mn = pts_all.min(0)
    mx = pts_all.max(0)
    origin = np.floor(mn[:2] / res) * res
    w = int(np.ceil((mx[0] - origin[0]) / res)) + 1
    h = int(np.ceil((mx[1] - origin[1]) / res)) + 1
    grid = Grid(res, origin, w, h)
    zmin = float(np.floor(mn[2] / zres) * zres)
    nzbins = int(np.ceil((mx[2] - zmin) / zres)) + 2
    progress(f"  grid {w}x{h} cells @ {res}u, z {zmin:.0f}..{mx[2]:.0f} ({nzbins} bins @ {zres}u)")

    top_z = np.full(w * h, -np.inf, np.float32)
    key_chunks: list[np.ndarray] = []
    z_chunks: list[np.ndarray] = []
    nz_chunks: list[np.ndarray] = []

    edges = np.linalg.norm(tris - tris[:, [1, 2, 0]], axis=2).max(1)
    level = np.clip(np.ceil(edges / (res * 0.5)).astype(np.int64), 1, 96)

    for lv in np.unique(level):
        sel = np.flatnonzero(level == lv)
        bary = _bary_pattern(int(lv))
        npts = len(bary)
        chunk = max(1, int(3_000_000 // npts))
        for s in range(0, len(sel), chunk):
            idx = sel[s:s + chunk]
            t = tris[idx]                                   # n,3,3
            p = np.einsum("kb,nbc->nkc", bary, t)           # n,npts,3
            fz = np.repeat(nz[idx], npts)
            fc = np.repeat(is_col[idx], npts)
            px = p[..., 0].ravel(); py = p[..., 1].ravel(); pz = p[..., 2].ravel()
            ix = np.clip(((px - origin[0]) / res).astype(np.int64), 0, w - 1)
            iy = np.clip(((py - origin[1]) / res).astype(np.int64), 0, h - 1)
            cell = iy * w + ix
            vis = ~fc
            if vis.any():
                np.maximum.at(top_z, cell[vis], pz[vis].astype(np.float32))
            up = (fz >= MAX_SLOPE_NZ) & fc
            if up.any():
                c = cell[up]; z = pz[up]
                zb = ((z - zmin) / zres).astype(np.int64)
                key_chunks.append(c * nzbins + zb)
                z_chunks.append(z.astype(np.float32))
                nz_chunks.append(fz[up].astype(np.float32))

    keys = np.concatenate(key_chunks)
    zs = np.concatenate(z_chunks)
    nzs = np.concatenate(nz_chunks)
    progress(f"  {len(keys):,} surface samples -> deduping")
    uk, inv = np.unique(keys, return_inverse=True)
    su_z = np.zeros(len(uk), np.float32)
    np.maximum.at(su_z, inv, zs)
    su_nz = np.zeros(len(uk), np.float32)
    np.maximum.at(su_nz, inv, nzs)
    surf = {
        "cell": uk // nzbins,
        "z": su_z,
        "nz": su_nz,
    }
    top_z = top_z.reshape(h, w)
    top_z[~np.isfinite(top_z)] = np.nan
    progress(f"  {len(uk):,} candidate standing surfaces")
    return grid, top_z, surf


def merge_layers(grid: Grid, surf: dict, merge_dz: float = 24.0):
    """Collapse near-identical z samples in the same cell into distinct floors."""
    order = np.lexsort((surf["z"], surf["cell"]))
    cell = surf["cell"][order]
    z = surf["z"][order]
    nz = surf["nz"][order]
    new_cell = np.empty(len(cell), bool)
    new_cell[0] = True
    new_cell[1:] = cell[1:] != cell[:-1]
    gap = np.empty(len(cell), bool)
    gap[0] = True
    gap[1:] = (z[1:] - z[:-1]) > merge_dz
    start = new_cell | gap
    gid = np.cumsum(start) - 1
    out_cell = cell[start]
    out_z = np.zeros(gid[-1] + 1, np.float32)
    np.maximum.at(out_z, gid, z)
    out_nz = np.zeros(gid[-1] + 1, np.float32)
    np.maximum.at(out_nz, gid, nz)
    return {"cell": out_cell, "z": out_z, "nz": out_nz}


def clearance_filter(bsp: Bsp, grid: Grid, surf: dict, progress=print,
                     stand=STAND_HEIGHT, chunk=2_000_000, solid=None):
    """Keep only surfaces with room for a player, and record stance (stand/crouch).

    `solid` is the point-in-solid test. It must be brush-aware: `Bsp.is_solid`
    descends the tree only, which cannot see `func_detail`, and passing it here
    admits a quarter of ministry's nodes from inside solid geometry. See
    `trace.Tracer.point_solid`.
    """
    if solid is None:
        solid = bsp.is_solid
    cell = surf["cell"]
    ix = (cell % grid.w).astype(np.float64)
    iy = (cell // grid.w).astype(np.float64)
    xy = grid.world_of(ix, iy)
    z = surf["z"].astype(np.float64)
    n = len(z)
    stance = np.zeros(n, np.int8)     # 0 blocked, 1 crouch, 2 stand

    def solid_at(dz, dx=0.0, dy=0.0):
        out = np.empty(n, bool)
        for s in range(0, n, chunk):
            e = min(n, s + chunk)
            p = np.empty((e - s, 3))
            p[:, 0] = xy[s:e, 0] + dx
            p[:, 1] = xy[s:e, 1] + dy
            p[:, 2] = z[s:e] + dz
            out[s:e] = solid(p)
        return out

    progress("  clearance: centre column")
    blocked_low = solid_at(4.0) | solid_at(CROUCH_HEIGHT - 4.0)
    stance[~blocked_low] = 1
    hi = ~blocked_low & ~solid_at(stand - 4.0)
    stance[hi] = 2

    progress("  clearance: hull width")
    live = stance > 0
    off = HULL_HALFWIDTH - 2.0
    for dx, dy in ((off, 0), (-off, 0), (0, off), (0, -off)):
        b = solid_at(CROUCH_HEIGHT * 0.5, dx, dy)
        live &= ~b
    stance[~live] = 0
    surf = {k: v[stance > 0] for k, v in surf.items()}
    surf["stance"] = stance[stance > 0]
    progress(f"  {len(surf['cell']):,} standable surfaces "
             f"({int((surf['stance']==2).sum()):,} full height)")
    return surf


def global_ground(surf: dict, zres: float = 8.0) -> float:
    """One ground datum for the whole map.

    City maps sit on a flat datum; using it as the origin makes every height
    read as "storeys above street" instead of an arbitrary world Z.
    """
    z = surf["z"]
    lo, hi = np.percentile(z, [0.5, 99.5])
    # A map flat to within one bin - a test room, a single-storey blockout -
    # gives np.arange a single edge, and an empty histogram has no argmax.
    # There is nothing to choose between in that case, so take the median.
    if hi - lo < zres:
        return float(np.median(z))
    bins = np.arange(np.floor(lo / zres) * zres, hi + zres, zres)
    hist, edges = np.histogram(z, bins=bins)
    k = int(np.argmax(hist))
    return float((edges[k] + edges[k + 1]) * 0.5)
