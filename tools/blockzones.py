#!/usr/bin/env python3
"""Turn a map's restricted areas off by editing the BSP entity lump.

An `ins_blockzone` is the restricted-area volume: `CINSBlockZoneBase::StartTouch`
calls `CINSPlayer::SetRestricted(true)` on any enemy-team player who enters an
active one, which plays the HQ warning and disables their weapon after
`mp_restricted_area_wpn_time`. Checkpoint inherits `GetRestrictedAreaSetup() == 1`
from `CINSRules`, so it fires unconditionally. See docs/spawns-and-objectives.md §5.

Two edits make them inert, and both are needed:

  - `StartDisabled` -> 1 on every volume, because some ship enabled
    (ministry_coop's `bz_ins_5` model *27 does) and are live from map load.
  - drop the `blockzone` key from every `ins_spawnzone`, because
    `CINSSpawnZone::ToggleBlockzone` re-enables the named volumes whenever its
    stage goes live. With the key gone the name is empty and it returns early.

    tools/blockzones.py MAP.bsp --list
    tools/blockzones.py MAP.bsp --disable --out NEW.bsp
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from movespawns import KV, parse_blocks, write_lump, zone_bounds  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/bspscout/src"))
from bspscout.bsp import Bsp  # noqa: E402

START_DISABLED = re.compile(r'("StartDisabled"\s+")([^"]*)(")')
BLOCKZONE_KV = re.compile(r'^[^\S\n]*"blockzone"\s+"[^"]*"\n', re.M)


def cmd_list(bsp: Bsp, blocks):
    zones = [d for _, _, d in blocks if d.get("classname") == "ins_blockzone"]
    print(f"{'blockzone':<14}{'team':>5}{'model':>7}  {'centre':<26}{'size':<20}{'at load':>9}")
    for d in zones:
        lo, hi = zone_bounds(bsp, d)
        c, s = (lo + hi) / 2, hi - lo
        live = "DISABLED" if d.get("StartDisabled") == "1" else "ENABLED"
        print(f"{d.get('targetname',''):<14}{d.get('TeamNum','-'):>5}{d['model']:>7}  "
              f"[{c[0]:7.0f} {c[1]:7.0f} {c[2]:7.0f}]  {s[0]:5.0f}x{s[1]:.0f}x{s[2]:.0f}"
              f"{'':<4}{live:>9}")
    binds = [(d.get("targetname"), int(d.get("TeamNum", 0)), d["blockzone"])
             for _, _, d in blocks
             if d.get("classname") == "ins_spawnzone" and d.get("blockzone")]
    print(f"\n{len(zones)} ins_blockzone, {len(binds)} ins_spawnzone naming one:")
    for name, team, bz in binds:
        print(f"    {name} team {team} -> {bz}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bsp", type=Path)
    ap.add_argument("--list", action="store_true", help="show the block zones and exit")
    ap.add_argument("--disable", action="store_true",
                    help="make every block zone inert (no restricted areas)")
    ap.add_argument("--out", type=Path, help="output .bsp")
    a = ap.parse_args()

    bsp = Bsp(a.bsp)
    text = bsp.file.raw(0).decode("latin-1")
    blocks = parse_blocks(text)

    if a.list or not a.disable:
        cmd_list(bsp, blocks)
        return 0
    if not a.out:
        ap.error("--disable needs --out")

    edits = []       # (start, end, new block text)
    off, unbound = 0, 0
    for s, e, d in blocks:
        block = text[s:e]
        if d.get("classname") == "ins_blockzone":
            if START_DISABLED.search(block):
                block = START_DISABLED.sub(lambda m: m.group(1) + "1" + m.group(3), block, 1)
            else:
                block = block[:-1] + '"StartDisabled" "1"\n' + block[-1]
            if d.get("StartDisabled") != "1":
                off += 1
        elif d.get("classname") == "ins_spawnzone" and d.get("blockzone"):
            block = BLOCKZONE_KV.sub("", block, count=1)
            unbound += 1
        else:
            continue
        edits.append((s, e, block))

    if not edits:
        print("no ins_blockzone and no spawn zone naming one; nothing to do")
        return 1

    # back to front, so the spans of earlier blocks stay valid
    out = text
    for s, e, block in reversed(edits):
        out = out[:s] + block + out[e:]

    n_bz = sum(1 for _, _, d in blocks if d.get("classname") == "ins_blockzone")
    print(f"{n_bz} block zones forced to StartDisabled 1 ({off} were live at map load)")
    print(f"{unbound} spawn zones no longer name one, so no stage can re-enable them")
    write_lump(bsp, a.bsp, a.out, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
