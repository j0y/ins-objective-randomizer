#!/usr/bin/env python3
"""Move Insurgency spawn zones and their spawn points by editing the BSP entity lump.

No recompile. The entity lump is plain ASCII; it is patched in place and padded with
NULs back to its original byte length, so every other lump offset in the file is
untouched and the map is otherwise bit-identical.

An ins_spawnzone is a brush entity whose geometry vbsp stored relative to its "origin"
key, so translating the key moves the volume. Spawn points are bound to a zone by
containment, not by name, so the points inside a moved zone must move with it.

    tools/movespawns.py MAP.bsp --list
    tools/movespawns.py MAP.bsp --zone spawnzone_1 --team 2 --to "10 -4036 83" --out NEW.bsp
    tools/movespawns.py MAP.bsp --zone spawnzone_1 --team 2 --by "0 0 512"   --out NEW.bsp
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/bspscout/src"))
from bspscout.bsp import Bsp  # noqa: E402
from bspscout.navmesh import parse_nav  # noqa: E402

BLOCK = re.compile(r"\{[^{}]*\}")
KV = re.compile(r'"([^"]*)"\s+"([^"]*)"')
ORIGIN_KV = re.compile(r'("origin"\s+")([^"]*)(")')


def parse_blocks(text: str):
    """(start, end, dict) for every entity block, in file order."""
    out = []
    for m in BLOCK.finditer(text):
        out.append((m.start(), m.end(), dict(KV.findall(m.group(0)))))
    return out


def vec(s: str) -> np.ndarray:
    return np.array([float(x) for x in s.split()], dtype=np.float64)


def fmt(v: np.ndarray) -> str:
    """Shortest round-trippable form, so the patched lump stays within its budget."""
    return " ".join(f"{x:g}" for x in v)


def zone_bounds(bsp: Bsp, ent: dict):
    m = bsp.models[int(ent["model"][1:])]
    org = vec(ent.get("origin", "0 0 0"))
    return m["mins"] + org, m["maxs"] + org


def spawnpoints(blocks):
    idx = [i for i, (_, _, d) in enumerate(blocks) if d.get("classname") == "ins_spawnpoint"]
    xyz = np.array([vec(blocks[i][2]["origin"]) for i in idx]) if idx else np.zeros((0, 3))
    team = np.array([int(blocks[i][2].get("TeamNum", 0)) for i in idx], dtype=int)
    return idx, xyz, team


def contained(xyz, team, lo, hi, want_team, pad=8.0):
    return ((xyz >= lo - pad) & (xyz <= hi + pad)).all(1) & (team == want_team)


def validate(bsp: Bsp, pts: np.ndarray):
    """A spawn point needs clear space at knee and head height, and ground under it."""
    if len(pts) == 0:
        return np.zeros(0, bool)
    clear = ~bsp.is_solid(pts + [0, 0, 8]) & ~bsp.is_solid(pts + [0, 0, 64])
    ground = np.zeros(len(pts), bool)
    for dz in range(-4, -100, -8):
        ground |= bsp.is_solid(pts + [0, 0, dz])
    return clear & ground


def load_nav(path: Path):
    """Walkable footprints from the shipped .nav. This is the authority on where a
    player can stand: the BSP tree test cannot see func_detail, which is most of a
    map's interior walls, so geometric probing walks straight through them."""
    mesh = parse_nav(str(path))
    lo, hi, z = [], [], []
    for a in mesh.areas.values():
        lo.append([min(a.nw.x, a.se.x), min(a.nw.y, a.se.y)])
        hi.append([max(a.nw.x, a.se.x), max(a.nw.y, a.se.y)])
        z.append((a.nw.z + a.se.z) / 2)
    return np.array(lo), np.array(hi), np.array(z)


def snap_to_nav(pts: np.ndarray, nav, max_dist: float = 256.0, inset: float = 24.0):
    """Pull each point onto the nearest walkable area, keeping its position within
    that area so a zone's spawn points stay spread out instead of piling up.

    `inset` holds the point off the area's edge. A nav area runs right up to the wall
    it ends at, so a point on its boundary has nowhere to put the 32x32 player hull:
    the engine drops it at map load with `Spawnpoint @ (...) was determined to be an
    invalid spawnpoint`. Half a hull is 16 u, so 24 leaves a little slack. Areas with
    no interior left at this inset are not candidates at all — better a point the tool
    reports as unplaced than one the engine silently throws away."""
    LO, HI, Z = nav
    room = ((HI - LO) >= 2 * inset).all(1)
    LO, HI, Z = LO[room], HI[room], Z[room]
    out = pts.copy()
    ok = np.zeros(len(pts), bool)
    for k, p in enumerate(pts):
        cl = np.clip(p[:2], LO + inset, HI - inset)
        d = np.hypot(*(cl - p[:2]).T) + 0.25 * np.abs(Z - p[2])
        j = int(np.argmin(d))
        if d[j] <= max_dist:
            out[k] = [cl[j, 0], cl[j, 1], Z[j]]
            ok[k] = True
    return out, ok


def snap(bsp: Bsp, pts: np.ndarray, max_dz: float = 384.0, step: float = 8.0):
    """Drop each point onto the nearest floor. Destinations rarely share the source's
    floor height, so a pure translation lands points in the air or inside geometry."""
    if len(pts) == 0:
        return pts, np.zeros(0, bool), np.zeros(0)
    offsets = sorted(np.arange(-max_dz, max_dz + step, step), key=abs)
    out = pts.copy()
    ok = np.zeros(len(pts), bool)
    used = np.zeros(len(pts))
    for dz in offsets:
        todo = ~ok
        if not todo.any():
            break
        cand = pts[todo] + [0, 0, dz]
        good = validate(bsp, cand)
        idx = np.nonzero(todo)[0][good]
        out[idx] = cand[good]
        used[idx] = dz
        ok[idx] = True
    return out, ok, used


def write_lump(bsp: Bsp, src: Path, dst: Path, text: str):
    """Write `text` back as lump 0 of `src`, leaving every other lump where it is."""
    raw = bsp.file.raw(0)
    data = text.encode("latin-1")
    blob = bytearray(src.read_bytes())
    off = bsp.file.lumps[0].offset

    if len(data) <= len(raw):
        # fits its original slot: pad with NULs so no other lump offset moves
        pad = len(raw) - len(data)
        blob[off:off + len(raw)] = data + b"\x00" * pad
        how = f"patched in place, {pad} bytes padding"
    else:
        # relocate the entity lump to the end. Every other lump keeps its offset, which
        # matters because GAME_LUMP stores absolute file offsets internally.
        while len(blob) % 4:
            blob += b"\x00"
        new_off = len(blob)
        blob += data
        struct.pack_into("<ii", blob, 8, new_off, len(data))
        how = f"relocated to end (+{len(data) - len(raw)} bytes, old slot abandoned)"

    dst.write_bytes(blob)
    print(f"\nwrote {dst}  ({len(blob):,} bytes, entity lump {how})")


def cmd_list(bsp: Bsp, blocks):
    _, xyz, team = spawnpoints(blocks)
    print(f"{'zone':<16}{'team':>5}{'model':>7}  {'centre':<26}{'size':<22}{'points':>7}")
    for _, _, d in blocks:
        if d.get("classname") != "ins_spawnzone":
            continue
        t = int(d.get("TeamNum", 0))
        lo, hi = zone_bounds(bsp, d)
        n = contained(xyz, team, lo, hi, t).sum()
        c, s = (lo + hi) / 2, hi - lo
        side = {2: "security", 3: "insurgent"}.get(t, "?")
        print(f"{d.get('targetname',''):<16}{side:>10}{d['model']:>7}  "
              f"[{c[0]:7.0f} {c[1]:7.0f} {c[2]:7.0f}]  {s[0]:5.0f}x{s[1]:.0f}x{s[2]:.0f}"
              f"{'':<6}{n:>5}")
    blockzones = [d for _, _, d in blocks if d.get("classname") == "ins_blockzone"]
    print(f"\n{len(blockzones)} ins_blockzone, {len(xyz)} ins_spawnpoint "
          f"({int((team==2).sum())} security / {int((team==3).sum())} insurgent)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bsp", type=Path)
    ap.add_argument("--list", action="store_true", help="show spawn zones and exit")
    ap.add_argument("--zone", help="targetname of the brush volume to move")
    ap.add_argument("--class", dest="cls", default="ins_spawnzone",
                    choices=("ins_spawnzone", "ins_blockzone", "trigger_capture_zone"),
                    help="which brush entity class --zone names (default ins_spawnzone). "
                         "All three store geometry relative to an origin key, so all three "
                         "translate the same way; only ins_spawnzone owns spawn points.")
    ap.add_argument("--team", type=int, choices=(2, 3), help="2 security, 3 insurgent")
    ap.add_argument("--to", help='new zone centre, "x y z"')
    ap.add_argument("--by", help='translate by "dx dy dz"')
    ap.add_argument("--out", type=Path, help="output .bsp")
    ap.add_argument("--force", action="store_true", help="write even if points fail validation")
    ap.add_argument("--no-snap", action="store_true",
                    help="translate verbatim instead of snapping onto walkable ground")
    ap.add_argument("--nav", type=Path,
                    help="the map's .nav (default: alongside the input .bsp)")
    ap.add_argument("--inset", type=float, default=24.0,
                    help="how far inside a nav area a snapped point must sit (default 24; "
                         "half a player hull is 16)")
    a = ap.parse_args()

    bsp = Bsp(a.bsp)
    navpath = a.nav or a.bsp.with_suffix(".nav")
    nav = None
    if not a.no_snap:
        if not navpath.exists():
            print(f"no nav mesh at {navpath}; pass --nav. Geometric probing alone cannot "
                  "tell inside-a-wall from open space on this engine.", file=sys.stderr)
            return 4
        nav = load_nav(navpath)
        print(f"nav mesh: {len(nav[2])} walkable areas from {navpath.name}")
    raw = bsp.file.raw(0)
    text = raw.decode("latin-1")
    blocks = parse_blocks(text)

    if a.list or not a.zone:
        cmd_list(bsp, blocks)
        return 0
    if a.cls != "ins_spawnzone":
        a.no_snap = True          # nothing to snap: these volumes own no spawn points
    if not (a.to or a.by) or not a.out:
        ap.error("--zone needs --to or --by, and --out")

    sp_idx, sp_xyz, sp_team = spawnpoints(blocks)

    targets = [i for i, (_, _, d) in enumerate(blocks)
               if d.get("classname") == a.cls
               and d.get("targetname") == a.zone
               and (a.team is None or int(d.get("TeamNum", 0)) == a.team)]
    if not targets:
        print(f"no {a.cls} named {a.zone!r}"
              + (f" on team {a.team}" if a.team else ""), file=sys.stderr)
        return 1

    lo, hi = zone_bounds(bsp, blocks[targets[0]][2])
    delta = vec(a.by) if a.by else vec(a.to) - (lo + hi) / 2

    edits = {}   # block index -> new origin
    moved_pts = []
    for zi in targets:
        d = blocks[zi][2]
        t = int(d.get("TeamNum", 0))
        zlo, zhi = zone_bounds(bsp, d)
        edits[zi] = vec(d.get("origin", "0 0 0")) + delta
        inside = (np.nonzero(contained(sp_xyz, sp_team, zlo, zhi, t))[0]
                  if a.cls == "ins_spawnzone" else np.zeros(0, int))
        pts = sp_xyz[inside] + delta
        if a.no_snap:
            ok, used = validate(bsp, pts), np.zeros(len(pts))
        else:
            pts, ok = snap_to_nav(pts, nav, inset=a.inset)
            used = np.zeros(len(pts))
        pts = np.round(pts)
        for k, good, q in zip(inside, ok, pts):
            if good:
                edits[sp_idx[k]] = q
                moved_pts.append(q)
        orphans = sp_xyz[inside[~ok]]
        print(f"{d.get('targetname')} team {t} model {d['model']}: zone + {len(inside)} spawn points")
        print(f"   {int(ok.sum())}/{len(inside)} placed on walkable nav areas"
              + (f", {len(orphans)} left behind" if len(orphans) else ""))
        for o in orphans:
            for _, _, e2 in blocks:
                if (e2.get("classname") == "ins_spawnzone" and int(e2.get("TeamNum", 0)) == t
                        and e2 is not d):
                    l2, h2 = zone_bounds(bsp, e2)
                    if ((o >= l2) & (o <= h2)).all():
                        print(f"   WARNING left-behind point {fmt(o)} sits in "
                              f"{e2.get('targetname')} and will spawn there")
                        break

    moved = np.array(moved_pts) if moved_pts else np.zeros((0, 3))
    print(f"\ndelta {fmt(delta)}   ->  new centre {fmt((lo + hi) / 2 + delta)}")
    if a.cls == "ins_spawnzone":
        print(f"placed {len(moved)} spawn points in open space with ground under them")
    if a.cls == "ins_spawnzone" and len(moved) == 0 and not a.force:
        print("\nrefusing to write: no moved spawn point is usable (--force to override)",
              file=sys.stderr)
        return 2

    # patch origins, back to front so earlier spans stay valid
    out = text
    for bi in sorted(edits, reverse=True):
        s, e, _ = blocks[bi]
        block = out[s:e]
        new_origin = fmt(edits[bi])
        if ORIGIN_KV.search(block):
            block = ORIGIN_KV.sub(lambda m: m.group(1) + new_origin + m.group(3), block, count=1)
        else:
            block = block[:-1] + f'"origin" "{new_origin}"\n' + block[-1]
        out = out[:s] + block + out[e:]

    write_lump(bsp, a.bsp, a.out, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
