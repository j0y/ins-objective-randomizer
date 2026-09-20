"""What the rays are for: sightlines, exposure, cover, and objective asymmetry.

The measures PLAN.md step 1 listed and could not produce, because every one of
them is a question about line of sight rather than about geometry: how far can
you see, how many places can see you, does crouching break the shot, and is the
fight at a capture point tilted toward whoever is holding it.

Sample points are a uniform random subsample of the reachable walk graph, which
makes every statistic here area-weighted - a big warehouse floor contributes in
proportion to its size, and a stairwell does not outvote it. The subsample is
seeded, so re-measuring a map twice gives the same number.

Ray pairs are pre-filtered through the PVS before they are traced. vvis already
computed which clusters can see which, so a pair whose clusters are mutually
invisible cannot possibly have a sightline and never reaches the tracer. On the
shipped maps an average cluster sees about 12% of the map, so this is where most
of the work goes away.
"""
from __future__ import annotations

import numpy as np

from .trace import Tracer
from .vis import Pvs

# Source's view offsets: standing eye at +64, crouched at +32 above the feet.
EYE_STAND = 64.0
EYE_CROUCH = 32.0

# A sightline this long is a long shot rather than a room: 1024 u is 64 m.
LONG_SIGHT = 1024.0
# Beyond this, two positions are not in a fight with each other, so the pair is
# not worth a ray. Engagement ranges in Insurgency sit well inside it.
ENGAGE_RANGE = 2048.0
RAYS_PER_POINT = 16


def sample_eyes(cache, reach: np.ndarray, n: int, seed: int = 0):
    """(node ids, stand-eye points, crouch-eye points) for a spread of positions.

    Crouch eye is clamped to the stance the node actually allows: a node that
    only fits a crouching player has no standing eye, so both heights use the
    crouch offset there rather than inventing a viewpoint inside the ceiling.
    """
    ok = np.flatnonzero(reach)
    if not len(ok):
        z = np.zeros((0, 3))
        return np.zeros(0, np.int64), z, z
    rng = np.random.default_rng(seed)
    sel = ok if len(ok) <= n else rng.choice(ok, n, replace=False)
    sel = np.sort(sel)
    xy = cache.xy(sel)
    z = cache.surf["z"][sel].astype(np.float64)
    stand = np.where(cache.surf["stance"][sel] == 2, EYE_STAND, EYE_CROUCH)
    hi = np.stack([xy[:, 0], xy[:, 1], z + stand], axis=1)
    lo = np.stack([xy[:, 0], xy[:, 1], z + EYE_CROUCH], axis=1)
    return sel, hi, lo


# ---------------------------------------------------------------- sightlines
def sightlines(tracer: Tracer, pts: np.ndarray, maxdist: float,
               rays: int = RAYS_PER_POINT, seed: int = 0) -> np.ndarray:
    """How far you can see from each point, in `rays` horizontal directions.

    The fan is rotated by a random offset per point. A fixed fan aligned with the
    world axes over-samples axis-aligned streets and corridors, which is most of
    them in a city map, and reports a longer map than there is.
    """
    if not len(pts):
        return np.zeros(0)
    rng = np.random.default_rng(seed)
    base = np.arange(rays) * (2 * np.pi / rays)
    ang = base[None, :] + rng.uniform(0, 2 * np.pi, size=(len(pts), 1))
    d = np.stack([np.cos(ang), np.sin(ang), np.zeros_like(ang)], axis=2).reshape(-1, 3)
    o = np.repeat(pts, rays, axis=0)
    return tracer.sight_range(o, d, maxdist).reshape(len(pts), rays)


# ---------------------------------------------------------------- pair sets
def _pvs_pairs(tracer: Tracer, pvs: Pvs, pts: np.ndarray, rng_u: float):
    """Unordered point pairs within range whose PVS clusters can see each other."""
    n = len(pts)
    if n < 2:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    i, j = np.triu_indices(n, k=1)
    within = ((pts[i, 0] - pts[j, 0]) ** 2 + (pts[i, 1] - pts[j, 1]) ** 2
              + (pts[i, 2] - pts[j, 2]) ** 2) <= rng_u ** 2
    i, j = i[within], j[within]
    if not len(i) or not pvs.clusters:
        return i, j
    cl = pvs.leaf_cluster[tracer.point_leaf(pts)]
    ci, cj = cl[i], cl[j]
    # A point in a solid or detail leaf has cluster -1 and no PVS row; keep those
    # pairs rather than silently deciding they cannot see anything.
    unknown = (ci < 0) | (cj < 0)
    vis = np.zeros(len(i), bool)
    k = np.flatnonzero(~unknown)
    if len(k):
        vis[k] = (pvs.rows[ci[k], cj[k] >> 3] & (1 << (cj[k] & 7))) != 0
    keep = unknown | vis
    return i[keep], j[keep]


def exposure(tracer: Tracer, pvs: Pvs, stand: np.ndarray, crouch: np.ndarray,
             engage: float = ENGAGE_RANGE):
    """Per-point visibility degree, and what crouching does to it.

    Returns (seen, candidates, cover) where `seen[i]` counts the sampled
    positions with a sightline to i, `candidates[i]` counts those close enough to
    have had one, and `cover` is the pair-level summary of the crouch test.
    """
    n = len(stand)
    seen = np.zeros(n, np.int64)
    cand = np.zeros(n, np.int64)
    i, j = _pvs_pairs(tracer, pvs, stand, engage)
    if not len(i):
        return seen, cand, {"pairs": 0, "sightlines": 0, "crouch_breaks": None}
    np.add.at(cand, i, 1)
    np.add.at(cand, j, 1)
    ss = tracer.occluded(stand[i], stand[j])
    open_ = ~ss
    np.add.at(seen, i[open_], 1)
    np.add.at(seen, j[open_], 1)
    # Crouch test, both ends: with a sightline open standing, does dropping to
    # crouch at one end break it? That is what a windowsill or a low wall does.
    lit = np.flatnonzero(open_)
    breaks = None
    if len(lit):
        a, b = i[lit], j[lit]
        hidden_b = tracer.occluded(stand[a], crouch[b])
        hidden_a = tracer.occluded(crouch[a], stand[b])
        breaks = float((hidden_a | hidden_b).mean())
    return seen, cand, {"pairs": int(len(i)), "sightlines": int(open_.sum()),
                        "crouch_breaks": breaks}


# ---------------------------------------------------------------- objectives
def objective_sight(tracer: Tracer, pvs: Pvs, cache, reach: np.ndarray,
                    centre: np.ndarray, ring: float, approach: float = 768.0,
                    cap: int = 160, seed: int = 0) -> dict | None:
    """The fight at one capture point: who sees whom coming in.

    `inner` is the ground inside the capture ring - where a defender stands.
    `outer` is the annulus just outside it - the last stretch an attacker has to
    cross. Every inner/outer pair is traced, which is the asymmetry the mode
    turns on: a point whose approach is seen from twenty places at once is a
    grinder, and one whose defenders cannot see out is a walk-in.
    """
    xy = cache.xy()
    d = np.hypot(xy[:, 0] - centre[0], xy[:, 1] - centre[1])
    near_z = np.abs(cache.surf["z"] - centre[2]) <= 320.0
    inner = np.flatnonzero(reach & near_z & (d <= ring))
    outer = np.flatnonzero(reach & near_z & (d > ring) & (d <= ring + approach))
    if len(inner) < 4 or len(outer) < 4:
        return None
    rng = np.random.default_rng(seed)
    if len(inner) > cap:
        inner = np.sort(rng.choice(inner, cap, replace=False))
    if len(outer) > cap:
        outer = np.sort(rng.choice(outer, cap, replace=False))

    def eyes(nodes, height):
        p = cache.xy(nodes)
        return np.stack([p[:, 0], p[:, 1], cache.surf["z"][nodes] + height], axis=1)

    di = eyes(inner, EYE_STAND)
    dc = eyes(inner, EYE_CROUCH)
    ao = eyes(outer, EYE_STAND)
    ii = np.repeat(np.arange(len(inner)), len(outer))
    jj = np.tile(np.arange(len(outer)), len(inner))
    open_ = ~tracer.occluded(di[ii], ao[jj])
    m = open_.reshape(len(inner), len(outer))
    per_approach = m.sum(0)                       # defenders seeing each way in
    per_defender = m.sum(1)
    # Crouched at the same spot, is the shot still there? Line of sight between
    # two points is symmetric, so this is not an advantage to either side - it is
    # how much of the fight a waist-high edge takes away. Claiming more than
    # that would need a body volume rather than an eye point.
    hidden = tracer.occluded(dc[ii], ao[jj]).reshape(len(inner), len(outer))
    return {
        "inner_positions": int(len(inner)),
        "approach_positions": int(len(outer)),
        "approach_seen_frac": float((per_approach > 0).mean()),
        "approach_watchers_frac": float(np.median(per_approach) / len(inner)),
        "approach_watchers_p90_frac": float(np.percentile(per_approach, 90) / len(inner)),
        # Median over defending spots of how much of the approach can shoot back.
        # "Hidden from every one of 160 approach positions" is almost never true,
        # so the fraction-of-watchers is the number that separates maps.
        "defender_seen_frac": float(np.median(per_defender) / len(outer)),
        "defender_crouch_breaks": float((m & hidden).sum() / max(int(m.sum()), 1)),
    }


def spawn_exposure(tracer: Tracer, cache, reach: np.ndarray, spawn_nodes: np.ndarray,
                   sites: np.ndarray) -> dict:
    """Can a capture point be seen from the spawn that attacks it?

    A sightline from a spawn onto an objective (or back) means the fight starts
    before anyone has moved, which in checkpoint means being shot while still
    loading in.
    """
    if not len(spawn_nodes) or not len(sites):
        return {"spawn_sees_objective_frac": None, "spawn_objective_pairs": 0}
    p = cache.xy(spawn_nodes)
    a = np.stack([p[:, 0], p[:, 1], cache.surf["z"][spawn_nodes] + EYE_STAND], axis=1)
    b = sites + np.array([0.0, 0.0, EYE_STAND])
    ii = np.repeat(np.arange(len(a)), len(b))
    jj = np.tile(np.arange(len(b)), len(a))
    open_ = ~tracer.occluded(a[ii], b[jj])
    return {"spawn_sees_objective_frac": float(open_.mean()),
            "spawn_objective_pairs": int(len(ii))}


# ---------------------------------------------------------------- summary
def _pct(v, q):
    return float(np.percentile(v, q)) if len(v) else None


def measure(bsp, cache, reach: np.ndarray, sites: np.ndarray | None = None,
            rings: np.ndarray | None = None, spawn_nodes: np.ndarray | None = None,
            n_sight: int = 2000, n_expose: int = 1200, maxdist: float | None = None,
            seed: int = 0, progress=print) -> tuple[dict, dict]:
    """Every ray-derived number for one map, plus the per-point arrays to draw.

    Returns (flat scalars, arrays). The scalars are what the corpus aggregates;
    the arrays are the exposure heatmap.
    """
    tracer = Tracer(bsp)
    pvs = Pvs(bsp)
    if maxdist is None:
        # The full map diagonal, not a round number. An 8192 u cap censored 24%
        # of sinjar's rays and put its p99 sightline exactly on the cap, which
        # reads as a measurement and is not one. Removing it costs ~25% more
        # time and drops the censored share to 0.05%.
        mn, mx = bsp.world_bounds
        maxdist = float(np.hypot(*(mx[:2] - mn[:2])))

    progress(f"  sightlines: {n_sight} points x {RAYS_PER_POINT} rays, cap {maxdist:.0f} u")
    s_nodes, s_hi, _ = sample_eyes(cache, reach, n_sight, seed)
    r = sightlines(tracer, s_hi, maxdist, seed=seed)
    flat = r.ravel()
    out = {
        "sight_points": int(len(s_nodes)),
        "sight_cap_u": maxdist,
        "sight_p50_u": _pct(flat, 50), "sight_p90_u": _pct(flat, 90),
        "sight_p99_u": _pct(flat, 99),
        "sight_long_frac": float((flat >= LONG_SIGHT).mean()) if len(flat) else None,
        # What share of rays reached the cap without hitting anything. Anything
        # much above zero means the percentiles above are censored.
        "sight_at_cap_frac": (float((flat >= maxdist - 1.0).mean())
                              if len(flat) else None),
        # The longest shot each position offers, then the spread of that: a map
        # where every point has one long lane reads differently from one where
        # the long lanes are concentrated.
        "sight_best_p50_u": _pct(r.max(1), 50) if len(r) else None,
        "sight_best_p90_u": _pct(r.max(1), 90) if len(r) else None,
    }

    progress(f"  exposure: {n_expose} points, pairs within {ENGAGE_RANGE:.0f} u")
    e_nodes, e_hi, e_lo = sample_eyes(cache, reach, n_expose, seed + 1)
    seen, cand, cover = exposure(tracer, pvs, e_hi, e_lo)
    frac = np.divide(seen, cand, out=np.zeros(len(seen)), where=cand > 0)
    live = cand > 0
    out.update({
        "exposure_points": int(len(e_nodes)),
        "exposure_pairs": cover["pairs"],
        "exposure_p50": _pct(frac[live], 50), "exposure_p90": _pct(frac[live], 90),
        "exposure_mean": float(frac[live].mean()) if live.any() else None,
        "exposure_hidden_frac": float((seen[live] == 0).mean()) if live.any() else None,
        "cover_crouch_breaks": cover["crouch_breaks"],
    })

    if sites is not None and len(sites):
        progress(f"  objective rings: {len(sites)} sites")
        rows = []
        for k, c in enumerate(sites):
            ring = float(rings[k]) if rings is not None else 512.0
            o = objective_sight(tracer, pvs, cache, reach, c, ring, seed=seed + 2 + k)
            if o:
                rows.append(o)
        if rows:
            for key in ("approach_seen_frac", "approach_watchers_frac",
                        "approach_watchers_p90_frac", "defender_seen_frac",
                        "defender_crouch_breaks"):
                out[f"obj_{key}"] = float(np.median([x[key] for x in rows]))
            out["obj_sight_sites"] = len(rows)
        if spawn_nodes is not None:
            out.update(spawn_exposure(tracer, cache, reach, spawn_nodes, sites))
    return out, {"nodes": e_nodes, "exposure": frac, "candidates": cand,
                 "sight_nodes": s_nodes, "sight_best": r.max(1) if len(r) else r}


# ---------------------------------------------------------------- command
def _control_points(ents, radius=512.0, dz=192.0):
    """Distinct capture locations from the shipped entities, one per place.

    A place carries one point_controlpoint per game mode, so counting entities
    counts modes. Merged by proximity, the same way `mapmaker.metrics` does it.
    """
    pts = []
    for e in ents:
        if e.get("classname") != "point_controlpoint":
            continue
        if "extract" in e.get("targetname", "").lower():
            continue
        try:
            pts.append([float(v) for v in e["origin"].split()[:3]])
        except (KeyError, ValueError, IndexError):
            continue
    groups: list[list[int]] = []
    pts = np.asarray(pts, float).reshape(-1, 3)
    for i, q in enumerate(pts):
        for g in groups:
            c = pts[g[0]]
            if np.hypot(q[0] - c[0], q[1] - c[1]) <= radius and abs(q[2] - c[2]) <= dz:
                g.append(i)
                break
        else:
            groups.append([i])
    return np.asarray([pts[g].mean(0) for g in groups], float).reshape(-1, 3)


def cmd_sight(args):
    import json
    import time
    from pathlib import Path

    from . import render
    from .bsp import Bsp
    from .cli import Cache

    t0 = time.time()
    def p(msg=""):
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    c = Cache(args.cache)
    b = Bsp(args.bsp)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    sites = _control_points(c.ents)
    spawn = _spawn_nodes(c, c.reach)
    p(f"{int(c.reach.sum()):,} reachable nodes, {len(sites)} capture sites, "
      f"{len(spawn)} spawn zones")
    out, arr = measure(b, c, c.reach, sites=sites if len(sites) else None,
                       spawn_nodes=spawn, n_sight=args.points,
                       n_expose=args.expose_points, seed=args.seed, progress=p)
    (outdir / "sight.json").write_text(json.dumps(out, indent=2))

    live = arr["candidates"] > 0
    xy = c.xy(arr["nodes"][live])
    markers = [{"xy": (float(s[0]), float(s[1])), "color": "#ffffff", "marker": "o",
                "ms": 6, "label": chr(65 + i), "fs": 9} for i, s in enumerate(sites)]
    render.render_scatter(
        c.grid, c.top_z, c.ground, c.crop(), xy, arr["exposure"][live] * 100.0,
        outdir / "exposure.png",
        "Exposure - how much of the map can see each position",
        f"{int(live.sum()):,} sampled standing positions; pairs within "
        f"{ENGAGE_RANGE:.0f} u, PVS-filtered",
        cmap="inferno", vmax=None, label="% of positions in range with a sightline",
        markers=markers)
    sxy = c.xy(arr["sight_nodes"])
    render.render_scatter(
        c.grid, c.top_z, c.ground, c.crop(), sxy, arr["sight_best"],
        outdir / "sightlines.png",
        "Sightlines - longest clear shot from each position",
        f"{len(sxy):,} sampled positions x {RAYS_PER_POINT} horizontal rays at eye height",
        cmap="viridis", label="longest clear line of sight (units)", markers=markers)

    width = max(len(k) for k in out)
    for k, v in out.items():
        print(f"  {k:<{width}}  {v if not isinstance(v, float) else round(v, 4)}")
    print(f"\nwrote {outdir}/sight.json, exposure.png, sightlines.png")


def _spawn_nodes(cache, reach, radius=512.0):
    """Nearest reachable node to each ins_spawnzone origin."""
    pts = []
    for e in cache.ents:
        if e.get("classname") != "ins_spawnzone":
            continue
        try:
            pts.append([float(v) for v in e["origin"].split()[:3]])
        except (KeyError, ValueError, IndexError):
            continue
    if not pts:
        return np.zeros(0, np.int64)
    ok = np.flatnonzero(reach)
    xy = cache.xy(ok)
    z = cache.surf["z"][ok]
    out = []
    for q in np.asarray(pts, float):
        d = (xy[:, 0] - q[0]) ** 2 + (xy[:, 1] - q[1]) ** 2 + (z - q[2]) ** 2
        k = int(np.argmin(d))
        if d[k] <= radius ** 2:
            out.append(int(ok[k]))
    return np.unique(np.asarray(out, np.int64))


def add_parser(sub):
    a = sub.add_parser("sight", help="sightlines, exposure, cover and objective asymmetry")
    a.add_argument("cache")
    a.add_argument("--bsp", required=True, help="the .bsp the cache was built from")
    a.add_argument("--outdir", default="out")
    a.add_argument("--points", type=int, default=2000,
                   help="positions sampled for sightline fans")
    a.add_argument("--expose-points", type=int, default=1200,
                   help="positions sampled for the all-pairs exposure test")
    a.add_argument("--seed", type=int, default=0)
    a.set_defaults(fn=cmd_sight)
