#!/usr/bin/env python3
"""Repair spawn points the engine rejected, using measured player-hull clearance.

The engine's own test is `CINSRules::IsSpawnPointValid`, which ends in
`UTIL_CanEntityFit(NULL, UTIL_SpawnPositionOffset(point), mins, maxs)` — a player
hull fit against the real collision world. Neither the nav mesh nor a BSP-tree
descent reproduces it: nav areas run right up to the walls they end at, and the BSP
tree cannot see `func_detail`. A rejected point is logged at map load as

    Spawnpoint @ (x, y, z) for team N was determined to be an invalid spawnpoint

so the server is the oracle. Feed that list back in here:

    docker logs <server> 2>&1 | grep -oP 'Spawnpoint @ \\(\\K[^)]+' | sort -u > rejects.txt
    tools/placespawns.py MAP.bsp --repair rejects.txt --out NEW.bsp

Each rejected point moves to the nearest position that *is* known to fit, taken from
the clearance samples in `data/<map>_clearance.npz` (54,644 positions for
ministry_coop, each with 3x72 rays traced against the real collision world).
Clearance alone does not predict the engine's verdict, so this narrows the search
rather than deciding it — see `--avoid` and `--near-valid`. Candidates must keep
the point inside the same spawn zones it already belongs to — binding is by
containment, so a point that leaves its zone leaves its stage — and must not land on
top of another spawn point of the same team.

`--toward "x y z"` biases the choice toward a position, for pulling defenders in
around their objective rather than leaving them at the edge of a large zone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from movespawns import (ORIGIN_KV, fmt, parse_blocks, spawnpoints,  # noqa: E402
                        vec, write_lump, zone_bounds)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/bspscout/src"))
from bspscout.bsp import Bsp  # noqa: E402

CLEARANCE = Path(__file__).resolve().parent.parent / "data"


def load_clearance(path: Path):
    """Sample positions and the worst clearance at each, over every ray and height."""
    z = np.load(path)
    return (z["sample_positions"].astype(np.float64),
            z["clearance"].astype(np.float32).min(axis=(1, 2)))


def zones_holding(bsp: Bsp, blocks, p: np.ndarray, team: int, pad: float = 8.0):
    """Every same-team spawn zone whose volume contains p — the point's stages."""
    out = []
    for _, _, d in blocks:
        if d.get("classname") != "ins_spawnzone" or int(d.get("TeamNum", 0)) != team:
            continue
        lo, hi = zone_bounds(bsp, d)
        if ((p >= lo - pad) & (p <= hi + pad)).all():
            out.append((lo, hi))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bsp", type=Path)
    ap.add_argument("--repair", type=Path, required=True,
                    help="file of rejected 'x, y, z' lines from the server log")
    ap.add_argument("--clearance", type=Path,
                    help="<map>_clearance.npz (default: data/<map>_clearance.npz)")
    ap.add_argument("--min-clearance", type=float, default=40.0,
                    help="required clear radius in every direction (default 40; half a "
                         "player hull is 16, and shipped points sit at a median of 84)")
    ap.add_argument("--spacing", type=float, default=64.0,
                    help="keep this far from other same-team spawn points (default 64)")
    ap.add_argument("--toward", help='bias the choice toward "x y z"')
    ap.add_argument("--max-move", type=float, default=768.0,
                    help="give up on a point rather than move it this far (default 768)")
    ap.add_argument("--avoid", type=float, default=0.0,
                    help="exclude candidates within this far of ANY position in the repair "
                         "list. Without it a rejected point is usually its own nearest "
                         "candidate and nothing moves. 96 is a reasonable first try.")
    ap.add_argument("--near-valid", type=float, default=0.0,
                    help="require a candidate to sit within this far of a spawn point the "
                         "engine accepted in the same run. Clearance rays miss static props "
                         "and func_detail, so they do not predict UTIL_CanEntityFit on their "
                         "own; an accepted point is proof that its neighbourhood is open. "
                         "Try 192 after a first pass still leaves rejects.")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    npz = a.clearance or CLEARANCE / f"{a.bsp.stem.replace('_v2','')}_clearance.npz"
    if not npz.exists():
        print(f"no clearance data at {npz}; pass --clearance", file=sys.stderr)
        return 4
    P, C = load_clearance(npz)
    print(f"{len(P)} clearance samples from {npz.name}, "
          f"{(C >= a.min_clearance).sum()} at >= {a.min_clearance:g}u")

    bsp = Bsp(a.bsp)
    text = bsp.file.raw(0).decode("latin-1")
    blocks = parse_blocks(text)
    sp_idx, sp_xyz, sp_team = spawnpoints(blocks)

    want = [np.array([float(x) for x in l.replace(",", " ").split()])
            for l in a.repair.read_text().split("\n") if l.strip()]
    toward = vec(a.toward) if a.toward else None

    ok = C >= a.min_clearance
    if a.avoid > 0:
        W = np.array(want)
        keep = np.ones(len(P), bool)
        for i in range(0, len(P), 4096):
            keep[i:i + 4096] = (np.linalg.norm(P[i:i + 4096, None, :] - W[None, :, :],
                                               axis=2).min(1) >= a.avoid)
        ok &= keep
        print(f"{ok.sum()} samples clear enough and >= {a.avoid:g}u from every rejected spot")
    if a.near_valid > 0:
        rejected = np.zeros(len(sp_xyz), bool)
        for p in want:
            d = np.linalg.norm(sp_xyz - p, axis=1)
            if d.min() <= 2.0:
                rejected[int(np.argmin(d))] = True
        good = sp_xyz[~rejected]
        near = np.full(len(P), np.inf)
        for i in range(0, len(P), 4096):        # chunked: 54k x 400 would be large
            near[i:i + 4096] = np.linalg.norm(
                P[i:i + 4096, None, :] - good[None, :, :], axis=2).min(1)
        ok &= near <= a.near_valid
        print(f"{len(good)} spawn points the engine accepted; "
              f"{ok.sum()} samples within {a.near_valid:g}u of one and clear enough")
    edits, moved, failed = {}, [], []
    for p in want:
        d = np.linalg.norm(sp_xyz - p, axis=1)
        k = int(np.argmin(d))
        if d[k] > 2.0:
            print(f"  no spawn point at {fmt(p)} (nearest is {d[k]:.0f}u away) — skipped")
            continue
        team = int(sp_team[k])
        boxes = zones_holding(bsp, blocks, sp_xyz[k], team)
        cand = ok.copy()
        if boxes:                       # stay in the stages this point already serves
            keep = np.zeros(len(P), bool)
            for lo, hi in boxes:
                keep |= ((P >= lo) & (P <= hi)).all(1)
            cand &= keep
        # never land on another same-team point (the one being moved excepted)
        others = sp_xyz[(sp_team == team)]
        others = others[np.linalg.norm(others - sp_xyz[k], axis=1) > 1e-6]
        if len(others):
            near = np.linalg.norm(P[:, None, :] - others[None, :, :], axis=2).min(1)
            cand &= near >= a.spacing
        if not cand.any():
            failed.append((p, "no candidate position"))
            continue
        cost = np.linalg.norm(P - sp_xyz[k], axis=1)
        if toward is not None:
            cost = cost + 0.5 * np.linalg.norm(P - toward, axis=1)
        cost = np.where(cand, cost, np.inf)
        j = int(np.argmin(cost))
        dist = float(np.linalg.norm(P[j] - sp_xyz[k]))
        if dist > a.max_move:
            failed.append((p, f"nearest fit is {dist:.0f}u away"))
            continue
        q = np.round(P[j])
        edits[sp_idx[k]] = q
        sp_xyz[k] = q                   # so later points space off the new position
        moved.append((p, q, dist, float(C[j])))

    for p, q, dist, c in moved:
        print(f"  team point {fmt(p):<28} -> {fmt(q):<24} {dist:5.0f}u, clearance {c:.0f}u")
    print(f"\nrepaired {len(moved)}/{len(want)} rejected spawn points")
    for p, why in failed:
        print(f"  LEFT AS IS {fmt(p)}: {why}")
    if not edits:
        print("nothing to write", file=sys.stderr)
        return 2

    out = text
    for bi in sorted(edits, reverse=True):
        s, e, _ = blocks[bi]
        block = out[s:e]
        new = fmt(edits[bi])
        block = ORIGIN_KV.sub(lambda m: m.group(1) + new + m.group(3), block, count=1)
        out = out[:s] + block + out[e:]
    write_lump(bsp, a.bsp, a.out, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
