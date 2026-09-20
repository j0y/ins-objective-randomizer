"""One map, measured: the per-map record the corpus distributions are built from.

Everything here comes from a compiled .bsp and its bspscout cache, so the same
call measures an official map and a generated one - which is the only reason the
comparison means anything. Fields are flat scalars on purpose: a distribution is
"this number across 49 maps", and anything shaped more interestingly than a
number does not aggregate.

The ray-derived fields - sightlines, exposure, cover, and the fight at each
capture point - come from `bspscout.sight`, which needs the segment tracer. They
are the slowest part of a measurement by a wide margin; everything else here is
a lump read or a graph walk.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from bspscout import light, places, sight, vis
from bspscout.bsp import Bsp
from bspscout.cli import Cache
from bspscout.objectives import (axis, buildings, describe, indoor_mask,
                                 sealed_regions)

# Storey bands in AGL, matching the per-level plans `bspscout render` draws.
BANDS = [("below", -2000.0, -64.0), ("ground", -64.0, 96.0), ("l1", 96.0, 224.0),
         ("l2", 224.0, 352.0), ("l3plus", 352.0, 640.0)]

# A control point marks a place, and one place carries a point per game mode -
# ministry's cap4 and its elimination twin are the same room. Merge them, or
# "objectives per map" measures how many modes shipped rather than the layout.
SITE_RADIUS = 512.0
SITE_DZ = 192.0
# How far a control point may sit from walkable floor before we call it unplaced
# rather than silently attaching it to the nearest thing that happens to exist.
MATCH_RADIUS = 512.0
# The ring an objective's approaches are counted on: its own capture zone where
# there is one, since that is the boundary players actually contest.
DEFAULT_RING = 512.0
MIN_RING = 256.0
MAX_RING = 1024.0

CONTROL_POINT = "point_controlpoint"
CAPTURE_ZONE = "trigger_capture_zone"
SPAWN_ZONE = "ins_spawnzone"
SPAWN_POINT = "ins_spawnpoint"


def _graph(cache):
    n = len(cache.surf["cell"])
    return coo_matrix((cache.wgt, (cache.src, cache.dst)), shape=(n, n)).tocsr()


def _origins(ents, classname) -> np.ndarray:
    out = []
    for e in ents:
        if e.get("classname") != classname:
            continue
        try:
            out.append([float(v) for v in e["origin"].split()[:3]])
        except (KeyError, ValueError, IndexError):
            continue
    return np.asarray(out, float).reshape(-1, 3)


def objective_sites(ents, radius=SITE_RADIUS, dz=SITE_DZ):
    """Distinct capture locations, one per place rather than one per game mode.

    Returns (centres Nx3, names per centre). The names are the control points'
    targetnames, which is how a capture zone brush finds its way back to the
    place it belongs to.
    """
    pts, names = [], []
    for e in ents:
        if e.get("classname") != CONTROL_POINT:
            continue
        try:
            pts.append([float(v) for v in e["origin"].split()[:3]])
        except (KeyError, ValueError, IndexError):
            continue
        names.append(e.get("targetname", ""))
    groups: list[list[int]] = []
    pts = np.asarray(pts, float).reshape(-1, 3)
    for i, p in enumerate(pts):
        for g in groups:
            c = pts[g[0]]
            if np.hypot(p[0] - c[0], p[1] - c[1]) <= radius and abs(p[2] - c[2]) <= dz:
                g.append(i)
                break
        else:
            groups.append([i])
    centres = np.asarray([pts[g].mean(0) for g in groups], float).reshape(-1, 3)
    return centres, [[names[i] for i in g] for g in groups]


def capture_zones(bsp: Bsp, ents) -> dict[str, np.ndarray]:
    """{control point name: brush size} for each trigger_capture_zone.

    The zone is the volume a player stands in to capture, so its extent is what
    a generated objective radius has to match. Extraction zones are a different
    animal - a doorway-sized box you leave the map through, ~300 u against a cap
    zone's ~2000 - so they are named here and separated by the caller.
    """
    out: dict[str, np.ndarray] = {}
    for e in ents:
        if e.get("classname") != CAPTURE_ZONE:
            continue
        model = e.get("model", "")
        if not model.startswith("*"):
            continue
        try:
            mi = int(model[1:])
        except ValueError:
            continue
        if mi <= 0 or mi >= len(bsp.models):
            continue
        m = bsp.models[mi]
        name = e.get("controlpoint") or e.get("targetname", "")
        out[name] = (m["maxs"].astype(float) - m["mins"].astype(float))
    return out


def is_extraction(names) -> bool:
    return any("extract" in n.lower() for n in names)


def approaches(cache, site_xy, radius, reach) -> tuple[int, float]:
    """(ways in, how open the ring is) for a disc around an objective.

    Two numbers because one is not enough. Counting the connected groups of
    walkable cells on the ring says "one way in" for a shed with a single door
    and also for a capture point in the middle of a field, where the ring is one
    unbroken circle of open ground. The open fraction separates them: a shed
    reads (1, 0.05), a field reads (1, 0.95).

    Deliberately not the containing building's door count - an objective inside
    one big connected interior would inherit every doorway in it, 64 of them on
    ministry, which says nothing about the fight at the capture point.
    """
    res = cache.grid.res
    xy = cache.xy()
    d = np.hypot(xy[:, 0] - site_xy[0], xy[:, 1] - site_xy[1])
    band = (d >= radius) & (d < radius + 2 * res)
    if not band.any():
        return 0, 0.0
    img = np.zeros(cache.grid.w * cache.grid.h, bool)
    img[cache.surf["cell"][band & reach]] = True
    ring = np.zeros_like(img)
    ring[cache.surf["cell"][band]] = True
    _lbl, n = ndimage.label(img.reshape(cache.grid.h, cache.grid.w), structure=np.ones((3, 3)))
    # The ring's own cells are the denominator, so a capture point at the map
    # edge is not called enclosed just because a quarter of its ring is off-map.
    circumference = max(int(np.ceil(2 * np.pi * (radius + res) / res)), 1)
    return int(n), float(min(img.sum() / max(ring.sum(), circumference), 1.0))


def _nearest_nodes(cache, pts, reach):
    """Nearest reachable node to each point, and how far away it is."""
    if not len(pts):
        return np.zeros(0, int), np.zeros(0)
    ok = np.flatnonzero(reach)
    xy = cache.xy(ok)
    z = cache.surf["z"][ok]
    nodes, dists = [], []
    for p in pts:
        d = (xy[:, 0] - p[0]) ** 2 + (xy[:, 1] - p[1]) ** 2 + (z - p[2]) ** 2
        k = int(np.argmin(d))
        nodes.append(int(ok[k]))
        dists.append(float(np.sqrt(d[k])))
    return np.asarray(nodes, int), np.asarray(dists, float)


def _median(v):
    v = np.asarray([x for x in np.asarray(v, float).ravel() if np.isfinite(x)])
    return float(np.median(v)) if len(v) else None


def measure(bsp_path, cache_path, progress=print, n_sight=2000, n_expose=1200) -> dict:
    """Every calibration number for one map."""
    t0 = time.time()
    bsp_path, cache_path = Path(bsp_path), Path(cache_path)
    c = Cache(cache_path)
    b = Bsp(bsp_path)
    reach = c.reach
    res = c.grid.res
    cell_area = res * res
    # Which set this map belongs to: the corpus is the official maps, and a
    # generated map measured for comparison must not join the thing it is
    # measured against.
    r = {"map": bsp_path.stem, "source": bsp_path.parent.name, "res": res,
         "standable_cells": int(len(reach)), "reachable_cells": int(reach.sum())}

    # ---- scale
    walk_area = float(reach.sum()) * cell_area
    footprint = float(len(np.unique(c.surf["cell"][reach]))) * cell_area
    xy = c.xy(np.flatnonzero(reach))
    r.update({
        "walkable_area_u2": walk_area,
        "footprint_u2": footprint,
        "stacking": walk_area / footprint if footprint else None,
        "extent_x_u": float(np.ptp(xy[:, 0])), "extent_y_u": float(np.ptp(xy[:, 1])),
        "reachable_frac": float(reach.sum() / max(len(reach), 1)),
        "crouch_frac": float((c.surf["stance"][reach] == 1).mean()),
    })

    # ---- storey mix
    agl = c.agl[reach]
    for name, lo, hi in BANDS:
        r[f"storey_frac_{name}"] = float(((agl >= lo) & (agl < hi)).mean())
    r["storeys_occupied"] = int(sum(r[f"storey_frac_{n}"] >= 0.02 for n, _, _ in BANDS))

    # ---- interiors: cover, ways in, how much of the map is inside
    progress("  interiors")
    ind = indoor_mask(c)
    r["indoor_frac"] = float((ind & reach).sum() / max(reach.sum(), 1))
    idx, lab, nlab, lblimg = buildings(c, ind, reach)
    empty_props = ([], np.zeros((0, 3)), np.zeros(0, np.int64))
    blds = describe(c, idx, lab, nlab, lblimg, ind, reach, empty_props)
    interior_of = np.full(len(reach), -1, np.int64)
    for i, bld in enumerate(blds):
        interior_of[bld["node_ids"]] = i
    r.update({
        "n_interiors": len(blds),
        "interiors_per_100k_u2": len(blds) / (footprint / 1e5) if footprint else None,
        "interior_footprint_u2": _median([x["footprint_u2"] for x in blds]),
        "interior_entrances": _median([x["entrances"] for x in blds]),
        "interior_storeys": _median([x["n_storeys"] for x in blds]),
        "interior_depth_u": _median([x["depth_u"] for x in blds]),
    })

    # ---- blockout quality
    sealed = sealed_regions(c, ind)
    r["n_sealed_regions"] = len(sealed)
    r["sealed_area_u2"] = float(sum(s["area_u2"] for s in sealed))
    r["sealed_frac"] = r["sealed_area_u2"] / footprint if footprint else None

    # ---- the long axis: end-to-end walk
    progress("  span")
    g = _graph(c)
    try:
        a, bnode, da, _db = axis(c, reach, ~ind)
        span = float(da[bnode])
        r["span_u"] = span if np.isfinite(span) else None
        r["routable_frac"] = float((np.isfinite(da) & reach).sum() / max(reach.sum(), 1))
    except SystemExit:
        # axis() bails on a map with too little walkable surface to span.
        r["span_u"] = None
        r["routable_frac"] = None

    # ---- objectives, as the map actually ships them
    progress("  objectives")
    sites, site_names = objective_sites(c.ents)
    zones = capture_zones(b, c.ents)
    nodes, dists = _nearest_nodes(c, sites, reach)
    placed = dists <= MATCH_RADIUS
    extraction = np.asarray([is_extraction(n) for n in site_names], bool)
    cap = placed & ~extraction          # capture points; extraction is its own thing
    r.update({
        "n_control_points": int(len(_origins(c.ents, CONTROL_POINT))),
        "n_objective_sites": int((~extraction).sum()),
        "n_extraction_sites": int(extraction.sum()),
        "modes_per_site": _median([len(n) for n in site_names]) if site_names else None,
        "objective_sites_unplaced": int((~placed).sum()),
        "objective_sites_per_100k_u2": (int((~extraction).sum()) / (footprint / 1e5)
                                        if footprint else None),
    })

    # Zone extents, per site, capture points only.
    site_extent = []
    for i, names in enumerate(site_names):
        sizes = [zones[n] for n in names if n in zones]
        site_extent.append(float(max(max(s[0], s[1]) for s in sizes)) if sizes else np.nan)
    site_extent = np.asarray(site_extent, float)
    zone_area = [float(zones[n][0] * zones[n][1]) for i, names in enumerate(site_names)
                 if not extraction[i] for n in names if n in zones]
    r["capture_zone_u2"] = _median(zone_area) if zone_area else None
    r["capture_zone_extent_u"] = _median(site_extent[~extraction])

    if cap.sum() >= 2:
        D = dijkstra(g, directed=True, indices=nodes[cap])
        walk = D[:, nodes[cap]]
        np.fill_diagonal(walk, np.inf)
        nn = np.nanmin(np.where(np.isfinite(walk), walk, np.nan), axis=1)
        r["objective_spacing_u"] = _median(nn)
        r["objective_spacing_min_u"] = float(np.nanmin(nn)) if np.isfinite(nn).any() else None
        pair = walk[np.isfinite(walk)]
        r["objective_pair_walk_u"] = _median(pair) if len(pair) else None
        straight = np.hypot(sites[cap][:, None, 0] - sites[cap][None, :, 0],
                            sites[cap][:, None, 1] - sites[cap][None, :, 1])
        np.fill_diagonal(straight, np.inf)
        r["objective_spacing_straight_u"] = _median(straight.min(1))
        r["objective_detour"] = (r["objective_spacing_u"] / r["objective_spacing_straight_u"]
                                if r["objective_spacing_straight_u"] else None)
    else:
        for k in ("objective_spacing_u", "objective_spacing_min_u", "objective_pair_walk_u",
                  "objective_spacing_straight_u", "objective_detour"):
            r[k] = None

    # Ways in, measured at the capture zone's own boundary.
    rings, counts, openness, indoor_hits = [], [], [], 0
    for i in np.flatnonzero(cap):
        extent = site_extent[i]
        ring = float(np.clip((extent / 2 if np.isfinite(extent) else DEFAULT_RING),
                             MIN_RING, MAX_RING))
        rings.append(ring)
        n_ways, open_frac = approaches(c, sites[i], ring, reach)
        counts.append(n_ways)
        openness.append(open_frac)
        if interior_of[nodes[i]] >= 0:
            indoor_hits += 1
    r["objective_ring_u"] = _median(rings) if rings else None
    r["objective_approaches"] = _median(counts) if counts else None
    r["objective_ring_open_frac"] = _median(openness) if openness else None
    r["objective_indoor_frac"] = (indoor_hits / int(cap.sum())) if cap.sum() else None

    # ---- spawns
    progress("  spawns")
    zone_pts = _origins(c.ents, SPAWN_ZONE)
    r["n_spawn_zones"] = int(len(zone_pts))
    r["n_spawn_points"] = int(len(_origins(c.ents, SPAWN_POINT)))
    spawn_nodes, spawn_d = _nearest_nodes(c, zone_pts, reach)
    spawn_nodes = spawn_nodes[spawn_d <= MATCH_RADIUS]
    if len(spawn_nodes) and placed.sum():
        # One multi-source pass: distance from the nearest spawn to everywhere.
        d = dijkstra(g, directed=True, indices=spawn_nodes, min_only=True)
        r["spawn_to_objective_u"] = _median(d[nodes[placed]])
    else:
        r["spawn_to_objective_u"] = None

    # ---- what needs rays: sightlines, exposure, cover, objective asymmetry
    progress("  sight")
    try:
        ray, _arrays = sight.measure(
            b, c, reach,
            sites=sites[cap] if cap.any() else None,
            rings=np.asarray(rings, float) if rings else None,
            spawn_nodes=spawn_nodes if len(spawn_nodes) else None,
            n_sight=n_sight, n_expose=n_expose, progress=progress)
        r.update(ray)
    except Exception as exc:
        # A map with almost no walkable surface has nothing to trace between.
        # Record the failure rather than losing the whole measurement.
        progress(f"  sight failed: {type(exc).__name__}: {exc}")
        r["sight_error"] = f"{type(exc).__name__}: {exc}"

    # ---- render cost and light, straight off the lumps
    progress("  pvs + lightmap")
    r.update(vis.cost(b))
    r.update(light.stats(b))
    names, porg, _ptype = places.load_props(b)
    r["n_static_props"] = int(len(porg))
    r["props_per_100k_u2"] = len(porg) / (footprint / 1e5) if footprint else None
    r["prop_models"] = len(names)

    r["measured_s"] = round(time.time() - t0, 1)
    return r


def write(record: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=False))
    return path
