#!/usr/bin/env python
"""Verify bspscout's segment tracer against brute force. `mise run check`.

The tracer is an acceleration structure, and the failure mode of an
acceleration structure is being subtly wrong rather than slow - a trace that
misses one wall in fifty produces exposure numbers that look plausible and are
not. So each of its three paths is checked against an implementation with no
structure at all: clip every brush, or test every triangle.

The comparison has to be scoped to the same geometry the path under test covers,
or it fails for the wrong reason. Brute-forcing every brush in the lump
over-reports against a world-tree trace, because brush entities are separate
submodels whose brushes no world leaf references - 25% of buhriz_coop's solid
brushes are in one.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from bspscout.bsp import Bsp
from bspscout.trace import Tracer, _TriSoup

ROOT = Path(__file__).resolve().parents[1]
MAPS = [("maps/build/test_room.bsp", 2000), ("maps/official/ministry_coop.bsp", 300),
        ("maps/official/buhriz_coop.bsp", 300), ("maps/official/sinjar.bsp", 300),
        ("maps/official/panj.bsp", 300)]


def world_leaf_brushes(tr: Tracer) -> np.ndarray:
    """The masked brushes reachable from headnode 0 - what a world trace can find."""
    leaves, stack = [], [0]
    while stack:
        n = stack.pop()
        if n < 0:
            leaves.append(-1 - n)
        else:
            stack.extend(int(c) for c in tr.nchild[n])
    seen: set[int] = set()
    for L in set(leaves):
        f, c = int(tr.lfirst[L]), int(tr.lnum[L])
        seen.update(int(x) for x in tr.leafbrushes[f:f + c])
    return np.array(sorted(b for b in seen if tr.brush_ok[b]), dtype=np.int64)


def brute_brushes(tr: Tracer, a, b, which) -> np.ndarray:
    """First-hit t per ray, clipping the segment against each brush in turn."""
    out = np.full(len(a), np.inf)
    for bi in which:
        pl = tr.side_plane[tr.bstart[bi]:tr.bstart[bi] + tr.bcount[bi]]
        N, D = tr.pn[pl], tr.pd[pl]
        ds, de = a @ N.T - D, b @ N.T - D
        den = de - ds
        par = np.abs(den) < 1e-9
        t = np.where(par, 0.0, -ds / np.where(par, 1.0, den))
        bad = (par & (ds > 1e-4)).any(1)
        enter = np.maximum(0.0, np.where(~par & (den < 0), t, -np.inf).max(1))
        exit_ = np.minimum(1.0, np.where(~par & (den > 0), t, np.inf).min(1))
        hit = ~bad & (enter <= exit_)
        out[hit] = np.minimum(out[hit], enter[hit])
    return out


def brute_tris(soup: _TriSoup, a, b, block=400) -> np.ndarray:
    """First-hit t per ray over every triangle, Moller-Trumbore, no grid."""
    out = np.full(len(a), np.inf)
    d = b - a
    for s in range(0, soup.n, block):
        tri = np.arange(s, min(soup.n, s + block))
        e1, e2, v0 = soup.e1[tri], soup.e2[tri], soup.v0[tri]
        pv = np.cross(d[:, None, :], e2[None])
        det = (e1[None] * pv).sum(2)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = a[:, None, :] - v0[None]
        u = (tv * pv).sum(2) * inv
        qv = np.cross(tv, e1[None])
        v = (d[:, None, :] * qv).sum(2) * inv
        t = (e2[None] * qv).sum(2) * inv
        good = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
        out = np.minimum(out, np.where(good, t, np.inf).min(1))
    return out


def compare(name, ref, got, span, extra=""):
    agree = np.isfinite(ref) == np.isfinite(got)
    both = np.isfinite(ref) & np.isfinite(got)
    err = np.abs(ref[both] - got[both]) * span[both]
    worst = float(err.max()) if len(err) else 0.0
    ok = bool(agree.all()) and worst < 1e-3
    print(f"   {'ok  ' if ok else 'FAIL'} {name:22s} blocked {int(np.isfinite(ref).sum()):5d} "
          f"ref / {int(np.isfinite(got).sum()):5d} traced, agree "
          f"{int(agree.sum())}/{len(ref)}, max error {worst:.6f} u{extra}")
    if not ok:
        i = np.flatnonzero(~agree)[:3]
        for k in i:
            print(f"        ray {k}: ref {ref[k]} traced {got[k]}")
    return ok


def run(path: Path, nrays: int, seed: int = 0) -> bool:
    b = Bsp(path)
    mn, mx = b.world_bounds
    rng = np.random.default_rng(seed)
    a = rng.uniform(mn, mx, size=(nrays, 3))
    e = rng.uniform(mn, mx, size=(nrays, 3))
    span = np.linalg.norm(e - a, axis=1)
    print(f"{path.name}  {nrays} rays through the world bounds")
    ok = True

    # 1. world brushes, with the other two paths switched off
    tw = Tracer(b, include_entities=False, include_displacements=False)
    which = world_leaf_brushes(tw)
    t0 = time.time(); ref = brute_brushes(tw, a, e, which); tb = time.time() - t0
    t0 = time.time(); got = tw.first_hit(a, e, nudge=0.0); tt = time.time() - t0
    ok &= compare(f"world ({len(which)} brushes)", ref, got, span,
                  f"  [brute {tb:.2f}s vs {tt:.3f}s, {tb / max(tt, 1e-9):.0f}x]")

    # 2. the displacement mesh on its own
    soup = _TriSoup(b.displacement_triangles())
    if soup.n:
        t0 = time.time(); ref = brute_tris(soup, a, e); tb = time.time() - t0
        got = np.full(nrays, np.inf)
        t0 = time.time(); soup.hit(a, e, got, nearest=True); tt = time.time() - t0
        ok &= compare(f"disp ({soup.n:,} tris)", ref, got, span,
                      f"  [brute {tb:.2f}s vs {tt:.3f}s, {tb / max(tt, 1e-9):.0f}x]")

    # 3. occluded() must agree with first_hit() on the full tracer, and adding a
    #    geometry path can only ever block more rays, never fewer.
    full = Tracer(b)
    hit = full.first_hit(a, e, nudge=0.0)
    occ = full.occluded(a, e, nudge=0.0)
    same = int((occ == np.isfinite(hit)).sum())
    world_only = np.isfinite(tw.first_hit(a, e, nudge=0.0))
    monotone = not bool((world_only & ~np.isfinite(hit)).any())
    good = same == nrays and monotone
    ok &= good
    print(f"   {'ok  ' if good else 'FAIL'} {'full tracer':22s} occluded == first_hit "
          f"{same}/{nrays}, extra paths never unblock: {monotone}")
    return ok


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    todo = [(Path(m), n) for m, n in MAPS] if not args else [(Path(x), 300) for x in args]
    ok = True
    ran = 0
    for path, n in todo:
        p = path if path.is_absolute() else ROOT / path
        if not p.exists():
            print(f"{path.name}: not built, skipped")
            continue
        ok &= run(p, n)
        ran += 1
    if not ran:
        print("no maps to check - run 'mise run maps' first")
        return 1
    print("\nall paths exact" if ok else "\nFAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
