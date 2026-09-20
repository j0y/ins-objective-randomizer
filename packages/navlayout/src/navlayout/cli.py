"""`navlayout` — the command line over the surveyed nav graph.

    navlayout check ministry_coop        PLAN.md B1: the kill switch
    navlayout openness ministry_coop     PLAN.md B2: how open is this map
    navlayout corpus                     ...over every survey there is
    navlayout places ministry_coop       the mesh coarsened to rooms, and what that says
    navlayout places --all               ...over every survey, cpsetup or not
    navlayout reverse ministry_coop      play a linear map backwards, as a preset
    navlayout permute ministry_coop --all   ...and every other way round the ladder
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import TEAM_INSURGENT, TEAM_SECURITY, check, report
from .graph import NavGraph
from .honest import honest
# `playable` is imported under a longer name: `cmd_permute` has a local of
# that name for the layouts a map's gates passed, and two meanings of one
# word in one module is a trap for whoever calls the other one next.
from .objectives import capture_links, playable as playable_chain
from .survey import CpSetup, Survey, find_cpsetup, infer_stage_count

DEFAULT_SURVEYS = Path("surveys")
DEFAULT_GAME = Path("game")

# The three judgements a preset file makes about how a round plays, calibrated
# against the corpus in `docs/permute.md` §§ on the wrap, the detour and the
# walk-on. `layout()` leaves all three off - it answers facts about whether the
# engine will load a layout, and these are not that - but everything `permute`
# writes is a preset, so this is where they belong. They are defaults rather
# than something a caller has to remember, so that regenerating a preset file
# cannot quietly emit rounds the measurements already refused; `off` reports
# without refusing, which is what the ungated report was.
#
#   advance   the wrap, as a multiple of the map's own worst leg. Everything
#             that has been through the engine is <= 1.34x, the wrap-payers
#             start at 1.65x, and 1.5 is the gap between those two clusters.
#   detour    how far a stage sends the round off the straight line. The
#             measured walk gives 1.78-2.35x where the shipped chain of an
#             open map reaches 6.79x.
#   walk-on   the shortest stage, in units. 42 of the 43 surveyed shipped
#             chains stay above 1,258 u; below that an objective is captured
#             on arrival and the stage is no fight at all.
#   apart     the same floor, measured horizontally and only between rungs on
#             one level: two objectives can be 608 u apart across the ground
#             and still 5,591 u of walk from each other, and the player who has
#             to fight both is the one who is right about it.
#
# All four are read against the map's own stock round, and refuse a layout only
# where it is *worse* than that: the bar is the number here or the number the
# map already asks, whichever lets more through. `max-advance` was always a
# multiple of the stock worst; the other three became relative on 2026-09-19,
# when the 124-map server list showed 56 of the 115 measurable maps shipping a
# round one of them would refuse - de_aztec_coop detours 8.21x, pripyat 3.09x,
# uprising_open_coop asks a 389 u stage - so the corpus these were calibrated
# on is the shipped 13 and a workshop map need not resemble it. The one thing
# that does not relax is a detour of `inf`, two ends of a stage on one piece of
# ground: there is no multiple of an advance of nothing, and a map shipping
# that is not a licence to ship it again.
GATE_MAX_ADVANCE = 1.5
GATE_MAX_DETOUR = 2.5
GATE_MIN_ADVANCE = 1000.0
GATE_MIN_SEPARATION = 700.0

# Re-exported so the gates and the ranking read from one place.
from .reverse import KEEP_GOOD, KEEP_WITHIN  # noqa: E402


def _gate(text: str) -> float | None:
    """A refusal threshold, or None for `off` - report but do not refuse."""
    if text.strip().lower() in ("off", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number or 'off', not {text!r}")


def _survey_path(name: str, surveys: Path) -> Path:
    """Accept either a map name or a path to a survey file."""
    direct = Path(name)
    if direct.suffix == ".json" and direct.exists():
        return direct
    path = surveys / f"{name}.json"
    if not path.exists():
        raise SystemExit(
            f"no survey at {path} - run:  mise run survey -- {name}"
        )
    return path


def _linked(survey: Survey, game: Path, honest_mesh: bool = True) -> Survey:
    """Attach the map's own capture-volume pairing to a survey just loaded.

    Which volume captures which objective is a keyvalue in the entity lump, so
    the survey cannot carry it - the exporter writes what the engine's entity
    list gives it. It is read off the `.bsp` here, where the game tree is known,
    and every caller of `resolve` picks it up from the survey it was given. A
    map with no loose `.bsp` falls back to the geometry, which is what this
    always did. See `objectives.capture_links`.

    The same `.bsp` then answers a second question, because this is the one
    place every command loads a survey through: which of the mesh's connections
    the geometry denies. A `.nav` that outlived a wall keeps the connection, and
    every measurement downstream reads the map as better connected than it is -
    see `honest`. Dropping them here rather than per command is what keeps the
    classification and the walk costs talking about the same map.
    """
    bsp = Path(game) / "insurgency" / "maps" / f"{survey.map}.bsp"
    survey.capture_links = capture_links(bsp)
    if honest_mesh:
        survey, dropped, total = honest(survey, bsp)
        if dropped:
            print(f"[mesh] {survey.map}: dropped {dropped} of {total} nav "
                  f"connections the geometry denies ({100 * dropped / total:.1f}%)",
                  file=sys.stderr)
    return survey


def _setup_for(survey: Survey, game: Path) -> CpSetup | None:
    """The map's cpsetup, reconciled against the map the engine actually loaded.

    Reading the `.txt` and believing it is not the same thing. A chain name with
    no entity behind it is a stage the running map cannot play, and counting it
    is what made bombshelter's top rung reach for a spawn zone its BSP does not
    have and cobblestone look like eight objectives with seven zones. See
    `objectives.playable`.
    """
    setup = find_cpsetup(survey.map, game)
    if setup is None:
        return None
    setup, dropped = playable_chain(setup, survey)
    if dropped:
        print(f"{survey.map}: the cpsetup names {len(dropped)} objective"
              f"{'' if len(dropped) == 1 else 's'} the loaded map has no entity "
              f"for ({', '.join(dropped)}) - the chain it can play is "
              f"{len(setup.chain)} long", file=sys.stderr)
    return setup


def _load(name: str, args: argparse.Namespace) -> tuple[Survey, CpSetup]:
    survey = _linked(Survey.load(_survey_path(name, args.surveys)), args.game,
                     not args.keep_nav_lies)
    setup = _setup_for(survey, args.game)
    if setup is None:
        raise SystemExit(
            f"no cpsetup for {survey.map}: {args.game}/insurgency/maps/"
            f"{survey.map}.txt is missing. Workshop maps ship theirs beside the "
            f".bsp - run:  mise run workshop -- {survey.map}"
        )
    if args.objective:
        setup.chain = list(args.objective)
    if not setup.chain:
        raise SystemExit(f"{survey.map} has no objective chain in its cpsetup")
    return survey, setup


def _team(args: argparse.Namespace) -> int | None:
    """An explicit --attacker, or None to take the map's own AttackingTeam."""
    if args.attacker == "security":
        return TEAM_SECURITY
    if args.attacker == "insurgent":
        return TEAM_INSURGENT
    return None


def cmd_check(args: argparse.Namespace) -> int:
    survey, setup = _load(args.map, args)
    result = check(survey, setup, attacking_team=_team(args))
    print(report(result))
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "map": result.map,
                    "enumeration": result.enumeration,
                    "areas": result.area_count,
                    "edges": result.edge_count,
                    "components": result.components,
                    "reachable_from_spawn": result.reachable_from_spawn,
                    "passed": result.passed,
                    "objectives": [
                        {
                            "order": o.order,
                            "name": o.name,
                            "area": o.area,
                            "attacker_path": o.attacker_path,
                            "attacker_line": o.attacker_line,
                            "defender_path": o.defender_path,
                            "defender_line": o.defender_line,
                        }
                        for o in result.objectives
                    ],
                    "notes": result.notes,
                },
                indent=2,
            )
        )
    return 0 if result.passed else 1


def cmd_footprints(args: argparse.Namespace) -> int:
    """Hold the surveyed corner offsets against the shipped .nav's own."""
    from .survey import compare_to_nav

    survey = _linked(Survey.load(_survey_path(args.map, args.surveys)), args.game,
                     not args.keep_nav_lies)
    if not survey.corner_offsets:
        print("this survey has no footprints - the corner probe failed in game")
        return 1
    nav = args.nav or (args.game / "insurgency" / "maps" / f"{survey.map}.nav")
    if not Path(nav).exists():
        raise SystemExit(f"no .nav at {nav} - this map ships none, so there is nothing to check against")

    r = compare_to_nav(survey, nav)
    print(f"map            {survey.map}")
    print(f"survey areas   {r['survey_areas']}")
    print(f"nav areas      {r['nav_areas']}"
          + ("" if r["nav_areas"] == r["survey_areas"]
             else "   <- the file parser saw fewer; that is the truncation"))
    print(f"matched        {r['matched']} by centre position")
    print(f"worst corner   {r['worst']:.4f} u   (area {r['worst_at']})")
    ok = r["compared"] and r["worst"] < 1.0
    print("\n" + ("PASS  the probed corner offsets agree with the file"
                   if ok else "FAIL  the probed corner offsets do not agree with the file"))
    return 0 if ok else 1


def cmd_openness(args: argparse.Namespace) -> int:
    from .openness import measure, report as openness_report

    survey, setup = _load(args.map, args)
    m = measure(survey, setup, attacking_team=_team(args), routes=args.routes)
    print(openness_report(m))
    if args.json:
        Path(args.json).write_text(json.dumps(m.to_dict(), indent=2))
    return 0


def _corpus(args: argparse.Namespace):
    """Every survey that has a cpsetup, as (survey, setup) pairs."""
    out = []
    for path in sorted(args.surveys.glob("*.json")):
        try:
            survey = _linked(Survey.load(path), args.game, not args.keep_nav_lies)
        except ValueError as exc:
            print(f"skip {path.name}: {exc}", file=sys.stderr)
            continue
        setup = _setup_for(survey, args.game)
        if setup is None or not setup.chain:
            print(f"skip {survey.map}: no cpsetup objective chain", file=sys.stderr)
            continue
        out.append((survey, setup))
    if not out:
        raise SystemExit(f"no usable surveys in {args.surveys}")
    return out


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import calibrate, report as cal_report

    c = calibrate(_corpus(args))
    print(cal_report(c))
    if args.json:
        Path(args.json).write_text(json.dumps(c.to_dict(), indent=2))
    return 0


def cmd_corpus(args: argparse.Namespace) -> int:
    from .openness import corpus_table, measure

    surveys = sorted(args.surveys.glob("*.json"))
    if not surveys:
        raise SystemExit(f"no surveys in {args.surveys}")

    rows = []
    for path in surveys:
        try:
            survey = _linked(Survey.load(path), args.game, not args.keep_nav_lies)
        except ValueError as exc:
            print(f"skip {path.name}: {exc}", file=sys.stderr)
            continue
        setup = _setup_for(survey, args.game)
        if setup is None or not setup.chain:
            print(f"skip {survey.map}: no cpsetup objective chain", file=sys.stderr)
            continue
        rows.append(measure(survey, setup, routes=args.routes))

    table = corpus_table(rows)
    print(table)
    if args.md:
        Path(args.md).write_text(table + "\n")
    if args.json:
        Path(args.json).write_text(json.dumps([r.to_dict() for r in rows], indent=2))
    return 0


def _places_for(survey: Survey, args: argparse.Namespace):
    """Segment one survey, taking the chain length from wherever it can be had.

    The cpsetup if the map has one, the numbered spawn zones if it does not.
    Only the room test needs it, so a map with neither is still segmented and
    still classified on its connectivity - it just cannot be called small.
    """
    from .places import segment

    stages, inferred = None, False
    setup = _setup_for(survey, args.game)
    if setup is not None and setup.chain:
        stages = len(setup.chain)
    else:
        stages = infer_stage_count(survey)
        inferred = stages is not None
    return segment(survey, door=args.door, min_place=args.min_place,
                   hinge_share=args.hinge_share,
                   stages=stages, stages_inferred=inferred)


def cmd_places(args: argparse.Namespace) -> int:
    from .places import corpus_table, report as places_report

    if args.all:
        rows = []
        for path in sorted(args.surveys.glob("*.json")):
            try:
                survey = _linked(Survey.load(path), args.game, not args.keep_nav_lies)
            except ValueError as exc:
                print(f"skip {path.name}: {exc}", file=sys.stderr)
                continue
            try:
                rows.append(_places_for(survey, args))
            except Exception as exc:                      # noqa: BLE001
                print(f"skip {survey.map}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not rows:
            raise SystemExit(f"no usable surveys in {args.surveys}")
        table = corpus_table(rows)
        print(table)
        if args.md:
            Path(args.md).write_text(table + "\n")
        if args.json:
            Path(args.json).write_text(json.dumps([r.to_dict() for r in rows], indent=2))
        return 0

    if not args.map:
        raise SystemExit("navlayout places: give a map name, or --all")
    survey = _linked(Survey.load(_survey_path(args.map, args.surveys)), args.game,
                     not args.keep_nav_lies)
    p = _places_for(survey, args)
    print(places_report(p))
    if args.json:
        Path(args.json).write_text(json.dumps(p.to_dict(), indent=2))
    return 0


def cmd_reverse(args: argparse.Namespace) -> int:
    """Emit the reversed layout for one map, or say why it cannot be had.

    Gated on the map reading linear: the operation is "reverse the chain, and
    swap the spawns with it", and on an open map that is not the interesting
    thing to do with it - there the objectives can be re-sited freely. --force
    runs it anyway, because a reversal is well defined on any chain.
    """
    from .places import segment
    from .reverse import report as reverse_report
    from .reverse import reverse, to_cfg, to_dict

    survey = _linked(Survey.load(_survey_path(args.map, args.surveys)), args.game,
                     not args.keep_nav_lies)
    setup = _setup_for(survey, args.game)
    if setup is None or not setup.chain:
        raise SystemExit(
            f"navlayout reverse: no cpsetup for {survey.map} in {args.game}. "
            "The chain order lives in maps/<name>.txt and a reversal is a "
            "permutation of it, so there is nothing to reverse without one."
        )

    graph = NavGraph.build(survey)
    if not args.force:
        kind = segment(survey, graph=graph, stages=len(setup.chain)).kind
        if kind not in ("linear", "segmented"):
            raise SystemExit(
                f"navlayout reverse: {survey.map} reads {kind}, not linear. "
                "Reversal is the operation a corridor licenses; pass --force to "
                "run it anyway."
            )

    # The same calibrated bars `permute` uses. They cannot refuse this walk -
    # `layout` states them for the reversal rather than refusing it, since a
    # map with one walk has nothing to be sent back to - and that is why they
    # are passed at all: this command used to pass nothing, so a corridor's
    # reversal shipped without a word about how far it sends a stage off the
    # straight line, while the same walk on an open map was refused for it.
    # One walk, one set of numbers, said the same way in both places.
    rev = reverse(survey, setup, graph=graph, blockzones=args.blockzones,
                  rename=args.rename,
                  max_advance=GATE_MAX_ADVANCE, min_advance=GATE_MIN_ADVANCE,
                  min_separation=GATE_MIN_SEPARATION, max_detour=GATE_MAX_DETOUR)
    print(reverse_report(rev))

    if args.json:
        Path(args.json).write_text(json.dumps(to_dict(rev), indent=2))
    if not rev.ok:
        return 1
    if args.cfg:
        Path(args.cfg).write_text(to_cfg(rev, name=args.name))
        print(f"\nwrote {args.cfg}")
    return 0


def _load_for_layout(args: argparse.Namespace):
    """The survey, cpsetup and graph a layout needs, or the reason there is none."""
    survey = _linked(Survey.load(_survey_path(args.map, args.surveys)), args.game,
                     not args.keep_nav_lies)
    setup = _setup_for(survey, args.game)
    if setup is None or not setup.chain:
        raise SystemExit(
            f"navlayout: no cpsetup for {survey.map} in {args.game}. The chain "
            "order lives in maps/<name>.txt and every layout here is a "
            "permutation of it, so there is nothing to permute without one. "
            "Workshop maps ship theirs beside the .bsp."
        )
    # Every rung below the last needs the spawn zone that stood on it, and the
    # ladder indexes that list by rung. gioconda_eron_panj names eight
    # objectives and one zone, which used to come out of `dest_zone` as an
    # IndexError two calls deep and take the whole batch down with it.
    #
    # Asked of the objectives rather than of the chain, because `_setup_for` has
    # already dropped the names the loaded map has no entity for and a ladder is
    # built out of what is there. cobblestone's eight names and seven zones are
    # seven objectives and seven zones, which is a ladder; panj's eight
    # objectives and one zone are not, and it is the map that says so - sz_a is
    # the only `ins_spawnzone` in its BSP.
    if len(setup.zones) < len(setup.chain):
        raise SystemExit(
            f"navlayout: {survey.map} plays {len(setup.chain)} objectives but "
            f"its map authors only {len(setup.zones)} spawn zone"
            f"{'' if len(setup.zones) == 1 else 's'} for them. A layout re-sites "
            "each stage's spawns onto the zone another rung authored, so a "
            "ladder cannot be built without one zone per stage."
        )
    return survey, setup, NavGraph.build(survey)


def cmd_permute(args: argparse.Namespace) -> int:
    """Emit one layout of a map, or the whole family for the applier to rotate.

    The family is the point: `mapmaker_layout.sp` picks round-robin per map and
    persists the index, so a file of N layouts is N rounds that do not repeat.
    Refusals are per layout, so a map where only some of the family survives the
    offline gates still emits the ones that do.
    """
    from .reverse import (
        explicit_plan,
        geometry_ring,
        ladder,
        layout,
        report as layout_report,
        reverse_plan,
        arrival_clashes,
        volume_misfits,
        rank,
        ring_family,
        ring_plan,
        walk_family,
        stock_plan,
        to_cfg_all,
        to_dict,
    )

    survey, setup, graph = _load_for_layout(args)

    # The chain length a plan is built against has to be the number of
    # objectives that actually resolved, which is what layout() uses.
    from .objectives import capture_links, resolve
    n = len(resolve(survey, setup.chain))

    # The ring is the order the rungs are walked in, and every plan below that
    # is not stock or explicit is a walk round it. Measuring it costs one
    # ladder build; `--ring chain` skips that and is what the family did before.
    ring, rungs, D = None, None, None
    if args.ring != "chain":
        rungs, D = ladder(survey, setup, graph)
        ring = geometry_ring(rungs, D, mode=args.ring)
        if ring != list(range(len(rungs))):
            print(f"ring ({args.ring})     {' '.join(f'{r:>2}' for r in ring)}"
                  f"   - the chain numbers them "
                  f"{' '.join(f'{r:>2}' for r in range(len(rungs)))}")

    if args.order:
        try:
            rungs = [int(x) for x in args.order.replace(",", " ").split()]
        except ValueError:
            raise SystemExit(f"navlayout permute: --order wants integers, got {args.order!r}")
        plans = [explicit_plan(rungs, name=args.name or "custom")]
    elif args.all:
        # `walk` optimises each member from its own entry; the other modes
        # rotate one ring, which is what the family did before.
        clashes = arrival_clashes(survey, setup, rungs) if rungs else None
        if clashes:
            print(f"arrival clashes  {len(clashes)}: a step onto a rung whose "
                  f"own attacker volume the previous rung borrows - "
                  + ", ".join(f"{a}->{b}" for a, b in sorted(clashes)))
        # The other thing the engine refuses outright, asked of every
        # stage/rung pair up front so the optimiser can route round it rather
        # than lose the layout to it afterwards.
        misfits = None
        if rungs:
            from .objectives import Snapper
            misfits = volume_misfits(survey, setup, rungs, Snapper(survey), graph)
            if misfits:
                print(f"volume misfits   {len(misfits)}: a stage whose capture "
                      f"volume covers no floor on that rung - "
                      + ", ".join(f"{j}@{k}" for j, k in sorted(misfits)))
        plans = (walk_family(rungs, D, clashes, misfits)
                 if args.ring == "tour" and not args.rotate
                 else ring_family(n, ring=ring))
        if args.limit:
            # ring_family is ordered stock, reversed, then the interior starts,
            # which is also the order of decreasing confidence - so a prefix is
            # the right subset to take.
            plans = plans[:args.limit]
    elif args.start is not None:
        # --start names the rung the *first* objective is fought at, which is
        # the readable end of it: "start the round at objective 4".
        walk = ring or list(range(n + 1))
        at = walk.index(args.start % (n + 1))
        step = -1 if args.dir == "forward" else 1
        entry = walk[(at + step) % (n + 1)]
        plans = [ring_plan(n, entry, args.dir, ring=ring)]
    elif args.stock:
        plans = [stock_plan(n)]
    else:
        plans = [reverse_plan(n)]

    if args.seed is not None:
        import random
        random.Random(args.seed).shuffle(plans)
        plans = plans[:1]

    out, refused = [], []
    passed: list = []          # every layout the gates passed, before ranking
    for plan in plans:
        lay = layout(survey, setup, plan, graph=graph, blockzones=args.blockzones,
                     max_advance=args.max_advance, min_advance=args.min_advance,
                     min_separation=args.min_separation,
                     max_detour=args.max_detour, rename=args.rename)
        if len(plans) == 1:
            print(layout_report(lay))
        else:
            adv = "-" if not lay.advances else f"{lay.worst_advance:.0f}"
            import numpy as _np
            # "-" is "no triple to measure"; a round that returns to ground it
            # has already taken has an infinite detour, and saying "-" for it
            # was how the worst walk in the corpus read as the best one.
            det = ("-" if _np.isnan(lay.worst_detour)
                   else "returns" if not _np.isfinite(lay.worst_detour)
                   else f"{lay.worst_detour:.2f}x")
            gap = ("-" if not _np.isfinite(lay.min_advance)
                   else f"{lay.min_advance:.0f}")
            sep = ("-" if not _np.isfinite(lay.min_separation)
                   else f"{lay.min_separation:.0f}")
            print(f"{'emit  ' if lay.ok else 'REFUSE'} {plan.name:<14} "
                  f"rungs {' '.join(f'{r:>2}' for r in plan.order)}   "
                  f"worst advance {adv:>7}  detour {det:>6}  shortest {gap:>6}"
                  f"  apart {sep:>6}"
                  + ("" if lay.ok else "   " + next(
                      w for w in lay.warnings if w.startswith('REFUSED'))))
        (out if lay.ok else refused).append(lay)
        if lay.ok:
            passed.append(lay)

    # The stock walk is the control the applier writes for itself, not a layout,
    # so a family where nothing but stock passed has emitted nothing - and used
    # to say so by writing a preset file holding the control and no presets.
    # bridge_coop_2021 and dead_air both landed there on 2026-09-19.
    if not [lay for lay in out if lay.name != "stock"]:
        if args.json:
            Path(args.json).write_text(json.dumps(
                [to_dict(lay) for lay in out + refused], indent=2))
        print(f"\nnothing emitted: {len(refused)} of {len(plans)} layouts refused"
              + (" - only the stock walk passed, and that is the control, not a "
                 "layout" if out else ""))
        return 1

    # Which of the playable ones are worth rotating. A gate is a verdict about
    # one walk against the map that ships it; this is a comparison between the
    # walks that passed, and it is where "the better half of what this map can
    # do" is decided. Never empties the map: the best layout is within 1.00x of
    # itself. See `layout_score`.
    if len(out) > 1:
        from .reverse import layout_score
        chosen = rank(out, within=args.keep_within, good=args.keep_good,
                      keep=args.keep)
        held = {id(lay) for lay in chosen}
        dropped = sorted((lay for lay in out if id(lay) not in held), key=layout_score)
        if dropped:
            playable = [lay for lay in out if lay.name != "stock"]
            best = layout_score(min(playable, key=layout_score))
            bar = max(best * args.keep_within if args.keep_within is not None else 0.0,
                      args.keep_good if args.keep_good is not None else 0.0)
            limit = (f"scoring under {bar:.2f} - within "
                     f"{args.keep_within:.2f}x of this map's best ({best:.2f}) or "
                     f"under {args.keep_good:.2f} outright"
                     if args.keep_within is not None and args.keep_good is not None
                     else f"the best {args.keep}")
            kept = [lay for lay in chosen if lay.name != "stock"]
            print(f"\nkept {len(kept)} of {len(playable)} playable layouts: "
                  f"{limit} - dropped "
                  + ", ".join(f"{lay.name} {layout_score(lay):.2f}" for lay in dropped[:6])
                  + (" ..." if len(dropped) > 6 else ""))
        out = chosen

    # Written after the ranking, and carrying it: a reader of this file was
    # being told a layout emitted when the preset file had dropped it. `kept`
    # is what is in the .cfg, `ok` is still whether the gates passed it, and a
    # layout that passed and was ranked out keeps its score so the choice can be
    # argued with.
    if args.json:
        from .reverse import layout_score
        held = {id(lay) for lay in out}
        rows = []
        for lay in passed + refused:
            row = to_dict(lay)
            row["kept"] = id(lay) in held
            if lay.ok:
                row["score"] = round(layout_score(lay), 4)
            rows.append(row)
        Path(args.json).write_text(json.dumps(rows, indent=2))

    if args.cfg:
        text = to_cfg_all(out)
        Path(args.cfg).write_text(text)
        # to_cfg_all writes a bare "stock" control preset itself and skips the
        # computed stock layout, so the count is what is actually in the file.
        written = [lay.name for lay in out if lay.name != "stock"]
        print(f"\nwrote {args.cfg}: the stock control + {len(written)} layouts "
              f"({', '.join(written)})")
        kb = len(text.encode()) / 1024
        rules = sum(len(l.moves) for l in out if l.name != "stock")
        print(f"                 {kb:.0f} KiB, {rules} rules across those layouts")
        if kb > 1024:
            print("! the applier ImportFromFile()s the whole file in OnMapInit and "
                  "then reads only the preset it picked. Every layout here rewrites "
                  "every spawn point, so a full family is megabytes; --limit trims "
                  "the rotation if map load drags.")
    elif len(plans) > 1:
        print(f"\n{len(out)} of {len(plans)} layouts emit; pass --cfg to write them")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="navlayout", description=__doc__)
    ap.add_argument("--surveys", type=Path, default=DEFAULT_SURVEYS,
                    help="directory of survey JSON (default: surveys/)")
    ap.add_argument("--game", type=Path, default=DEFAULT_GAME,
                    help="game tree, for maps/<name>.txt objective chains")
    ap.add_argument("--keep-nav-lies", action="store_true",
                    help="measure the mesh as surveyed, including connections "
                         "the map's geometry denies. The default drops them; "
                         "this is how to see what a stale .nav was claiming")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("map", help="map name, or a path to a survey .json")
        p.add_argument("--attacker", choices=("security", "insurgent", "auto"),
                       default="auto",
                       help="attacking team; the default reads AttackingTeam out "
                            "of the map's own cpsetup")
        p.add_argument("--objective", action="append", default=[],
                       help="override the objective chain, once per control point, in order")
        p.add_argument("--json", help="also write the result to this path")

    c = sub.add_parser("check", help="PLAN.md B1: can the shipped spawn reach every shipped objective")
    common(c)
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("footprints",
                       help="check the runtime-probed corner offsets against the shipped .nav")
    f.add_argument("map")
    f.add_argument("--nav", help="path to the .nav (default: from the game tree)")
    f.set_defaults(func=cmd_footprints)

    o = sub.add_parser("openness", help="PLAN.md B2: route count, detour, chokepoints, breadth, layering")
    common(o)
    o.add_argument("--routes", type=int, default=4, help="how many disjoint routes to look for")
    o.set_defaults(func=cmd_openness)

    cal = sub.add_parser("calibrate",
                         help="measure the shipped layout conventions over the corpus")
    cal.add_argument("--json", help="write the bands to this path")
    cal.set_defaults(func=cmd_calibrate)

    k = sub.add_parser("corpus", help="openness over every survey, as a table")
    k.add_argument("--routes", type=int, default=4)
    k.add_argument("--md", help="write the table to this path")
    k.add_argument("--json", help="write the rows to this path")
    k.set_defaults(func=cmd_corpus)

    from .places import DOOR_CONDUCTANCE, HINGE_SHARE, MIN_PLACE
    places_defaults = (DOOR_CONDUCTANCE, MIN_PLACE, HINGE_SHARE)
    pl = sub.add_parser("places",
                        help="coarsen the mesh into the spaces a player sees, and "
                             "classify the map on the graph they form")
    pl.add_argument("map", nargs="?", help="map name, or a path to a survey .json")
    pl.add_argument("--all", action="store_true",
                    help="every survey, as a table; works without a cpsetup")
    pl.add_argument("--door", type=float, default=places_defaults[0],
                    help="how tight a boundary has to be to count as a door - the "
                         "fraction of the walk that crosses it (default: %(default)s)")
    pl.add_argument("--min-place", type=float, default=places_defaults[1],
                    help="u^2 of floor below which a piece is a corner, not a place")
    pl.add_argument("--hinge-share", type=float, default=places_defaults[2],
                    help="share of the map a place must strand to count as a hinge")
    pl.add_argument("--md", help="write the --all table to this path")
    pl.add_argument("--json", help="also write the result to this path")
    pl.set_defaults(func=cmd_places)

    rv = sub.add_parser("reverse",
                        help="play a linear map backwards by permuting the ground "
                             "under its chain, and emit the preset")
    rv.add_argument("map", help="map name, or a path to a survey .json")
    rv.add_argument("--cfg", help="write the plugin preset to this path")
    rv.add_argument("--json", help="write the rich result to this path")
    rv.add_argument("--name", default="reversed", help="preset name (default: %(default)s)")
    rv.add_argument("--no-rename", dest="rename", action="store_false",
                    help="move every zone's volume onto its rung instead of "
                         "renaming the volume already standing there")
    rv.add_argument("--blockzones", choices=("follow", "leave"), default="follow",
                    help="translate restricted areas with the defender zone they "
                         "sit on, or leave them stock (default: %(default)s)")
    rv.add_argument("--force", action="store_true",
                    help="reverse a map that does not read linear")
    rv.set_defaults(func=cmd_reverse)

    pm = sub.add_parser("permute",
                        help="emit one layout of a map, or every walk round its "
                             "ladder, by permuting the ground under the chain")
    pm.add_argument("map", help="map name, or a path to a survey .json")
    pm.add_argument("--all", action="store_true",
                    help="every ring walk - stock, reversed, and each starting "
                         "objective either way round - as one rotatable preset file")
    pm.add_argument("--start", type=int, metavar="RUNG",
                    help="the rung the first objective is fought at; with --dir "
                         "this is 'randomise the starting objective'")
    pm.add_argument("--dir", choices=("forward", "backward"), default="forward",
                    help="which way round the ladder to walk (default: %(default)s)")
    pm.add_argument("--order", metavar="R,R,...",
                    help="the rung for each stage, explicitly; the leftover rung "
                         "is where the attackers enter")
    pm.add_argument("--stock", action="store_true",
                    help="the shipped layout, as a preset - the control")
    pm.add_argument("--limit", type=int, metavar="N",
                    help="with --all, keep only the first N layouts - stock and "
                         "reversed come first, then the interior starts")
    pm.add_argument("--seed", type=int,
                    help="pick one layout at random from the selected set")
    pm.add_argument("--max-advance", type=_gate, metavar="X", dest="max_advance",
                    default=GATE_MAX_ADVANCE,
                    help="refuse a layout whose worst stage advance is more than "
                         "X times the stock worst; 'off' reports without "
                         "refusing (default: %(default)s)")
    pm.add_argument("--ring", choices=("tour", "axis", "circuit", "chain"),
                    default="tour",
                    help="the order the ladder is walked in: measured off the "
                         "mesh (tour), a push along the map's long axis, a loop "
                         "round it, or the order the chain numbers the rungs - "
                         "which is a progression only on a corridor "
                         "(default: %(default)s)")
    pm.add_argument("--rotate", action="store_true",
                    help="with --all, build the family by rotating one ring "
                         "instead of optimising each walk from its own entry")
    pm.add_argument("--min-advance", type=_gate, metavar="U", dest="min_advance",
                    default=GATE_MIN_ADVANCE,
                    help="refuse a layout with a stage that advances less than U "
                         "units - an objective captured on arrival - or less "
                         "than the stock round's own shortest stage, whichever "
                         "is smaller; 'off' reports without refusing "
                         "(default: %(default)s)")
    pm.add_argument("--min-separation", type=_gate, metavar="U",
                    dest="min_separation", default=GATE_MIN_SEPARATION,
                    help="refuse a layout with an objective standing less than U "
                         "units from the one before it, however far round the "
                         "walk between them goes - relaxed to the stock round's "
                         "own closest pair where that is nearer; 'off' reports "
                         "without refusing (default: %(default)s)")
    pm.add_argument("--max-detour", type=_gate, metavar="X", dest="max_detour",
                    default=GATE_MAX_DETOUR,
                    help="refuse a layout whose worst stage sends the round more "
                         "than X times further than going straight on would, or "
                         "more than the stock round does where that is worse; "
                         "'off' reports without refusing (default: %(default)s)")
    pm.add_argument("--keep-within", type=_gate, metavar="X", dest="keep_within",
                    default=KEEP_WITHIN,
                    help="of the layouts that pass, keep those scoring within X "
                         "times the best round the map has - the best one is "
                         "always kept, however it scores; 'off' keeps every "
                         "layout that passed (default: %(default)s)")
    pm.add_argument("--keep-good", type=_gate, metavar="S", dest="keep_good",
                    default=KEEP_GOOD,
                    help="...and keep any layout scoring under S outright, "
                         "however good the map's best is - the shipped maps' own "
                         "rounds score a median 1.98 (default: %(default)s)")
    pm.add_argument("--keep", type=int, metavar="N",
                    help="...and at most N of them, best first")
    pm.add_argument("--cfg", help="write the plugin preset to this path")
    pm.add_argument("--json", help="write the rich result to this path")
    pm.add_argument("--name", help="preset name, for --order")
    pm.add_argument("--blockzones", choices=("follow", "leave"), default="follow",
                    help="translate restricted areas with the defender zone they "
                         "sit on, or leave them stock (default: %(default)s)")
    pm.add_argument("--no-rename", dest="rename", action="store_false",
                    help="move every zone's volume onto its rung instead of "
                         "renaming the volume already standing there")
    pm.set_defaults(func=cmd_permute)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
