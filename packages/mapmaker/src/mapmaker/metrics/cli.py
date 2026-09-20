"""python -m mapmaker.metrics - build the corpus distributions and read a map against them.

    extract            measure every official map that has a bspscout cache
    summary            print "official maps sit at X +/- Y" for each metric
    compare <map>      one map against the corpus, flagging what sits outside p5-p95
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import corpus as C
from .record import measure, write

_pkg = Path(__file__).resolve()
# .../packages/mapmaker/src/mapmaker/metrics/cli.py -> repo root, unless the
# package is installed somewhere else entirely, in which case cwd is the repo.
ROOT = _pkg.parents[5] if (_pkg.parents[5] / "maps").is_dir() else Path.cwd()
CACHE = Path(os.environ.get("CACHE", ROOT / "cache"))
OUT = Path(os.environ.get("OUT", ROOT / "out"))
RECORDS = CACHE / "metrics"
MAP_DIRS = [ROOT / "maps" / "official", ROOT / "maps" / "build"]


def _resolve(name: str) -> tuple[Path, Path] | None:
    """(bsp, cache) for a map name or .bsp path, if both exist."""
    p = Path(name)
    if p.suffix == ".bsp" and p.exists():
        bsp = p
    else:
        stem = p.stem or name
        bsp = next((d / f"{stem}.bsp" for d in MAP_DIRS if (d / f"{stem}.bsp").exists()), None)
        if bsp is None:
            return None
    cache = CACHE / f"{bsp.stem}.npz"
    return (bsp, cache) if cache.exists() else None


def cmd_extract(args) -> int:
    names = args.map or sorted(p.name for d in MAP_DIRS[:1] for p in d.glob("*.bsp"))
    RECORDS.mkdir(parents=True, exist_ok=True)
    done = failed = skipped = 0
    for name in names:
        pair = _resolve(name)
        if pair is None:
            print(f"  --    {name}: no .bsp and .npz pair - run 'mise run corpus' first")
            failed += 1
            continue
        bsp, cache = pair
        rec = RECORDS / f"{bsp.stem}.json"
        if not args.force and rec.exists() and rec.stat().st_mtime > max(
                bsp.stat().st_mtime, cache.stat().st_mtime):
            skipped += 1
            continue
        t0 = time.time()
        try:
            r = measure(bsp, cache, progress=lambda *a: None)
        except Exception as exc:                      # one bad map is not the sweep
            print(f"  FAIL  {bsp.stem}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        write(r, rec)
        done += 1
        print(f"  ok    {bsp.stem}  ({time.time() - t0:.1f}s)")
    print(f"{done} measured, {skipped} already current, {failed} failed -> {RECORDS}")
    return 1 if failed else 0


def cmd_summary(args) -> int:
    corp = C.Corpus.load(RECORDS, include_variants=args.all, scope=args.scope)
    if not corp.records:
        print(f"no records in {RECORDS} - run 'extract' first", file=sys.stderr)
        return 1
    text = corp.table()
    print(text)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(text + "\n")
        print(f"\nwrote {args.md}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(corp.summary(), indent=2))
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


def cmd_compare(args) -> int:
    corp = C.Corpus.load(RECORDS, include_variants=args.all, scope=args.scope)
    if not corp.records:
        print(f"no records in {RECORDS} - run 'extract' first", file=sys.stderr)
        return 1
    pair = _resolve(args.map)
    if pair is None:
        print(f"{args.map}: need both a .bsp and its cache/<map>.npz", file=sys.stderr)
        return 1
    bsp, cache = pair
    rec = RECORDS / f"{bsp.stem}.json"
    if rec.exists() and not args.force:
        record = json.loads(rec.read_text())
    else:
        record = measure(bsp, cache, progress=lambda *a: None)
        write(record, rec)
    text = corp.compare_table(record, only_outliers=args.outliers)
    print(text)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(text + "\n")
        print(f"\nwrote {args.md}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mapmaker.metrics", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("extract", help="measure maps into cache/metrics/*.json")
    a.add_argument("map", nargs="*", help="map names or .bsp paths; default all official")
    a.add_argument("--force", action="store_true", help="re-measure current records")
    a.set_defaults(fn=cmd_extract)

    a = sub.add_parser("summary", help="print the corpus distributions")
    a.add_argument("--scope", choices=("base", "coop", "all"), default="coop",
                   help="the 13 _coop checkpoint maps (default), base maps, or every file")
    a.add_argument("--all", action="store_true",
                   help="include _coop/_night variants (same geometry, counted again)")
    a.add_argument("--md", help="also write the table here")
    a.add_argument("--json", help="also write the raw distributions here")
    a.set_defaults(fn=cmd_summary)

    a = sub.add_parser("compare", help="one map against the corpus")
    a.add_argument("map")
    a.add_argument("--scope", choices=("base", "coop", "all"), default="coop",
                   help="which corpus to read against (default: the checkpoint maps)")
    a.add_argument("--all", action="store_true", help="include _coop/_night variants")
    a.add_argument("--outliers", action="store_true", help="only rows outside p5-p95")
    a.add_argument("--force", action="store_true", help="re-measure rather than reuse")
    a.add_argument("--md", help="also write the table here")
    a.set_defaults(fn=cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
