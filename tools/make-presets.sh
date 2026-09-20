#!/usr/bin/env bash
# Write the layout presets the server reads, one file per map, choosing the
# operation from what the map is.
#
# `docs/places.md` classifies a map off its own coarsened mesh, and that
# classification is a prescription: a corridor licenses **reversal** and
# nothing more interesting, because every other walk round its ladder asks one
# stage to cross the whole map (`docs/permute.md` §2). A small map licenses the
# **whole family** - randomising the starting objective is cheap there exactly
# because the map is small, so the wrap costs little. This puts that in one
# place rather than in a habit.
#
# An **open** map is asked the question rather than told the answer. Re-siting
# is the operation its shape licenses and that is PLAN.md step B, but the
# family is available to it too and costs nothing to try: `--max-advance`
# refuses, per layout, the walks that pay for the wrap. Measured 2026-09-03 as
# the ratio of each walk's worst stage advance to the map's own worst leg, the
# corpus is in two clusters and the boundary is the gap between them:
#
#   tell_coop 28/28, tell_open_coop 32/32, drycanal_open_coop 24/24,
#   buhriz_coop 14/14 all at <= 1.01x - the wrap is not a corridor traverse
#   inferno_new_v1 (small) tops out at 1.34x, and all 14 rotated through the
#   engine cleanly
#   ---- 1.5 ----
#   sinjar_coop and market_open_coop 1.67x, contact_coop 1.74x,
#   ministry_coop 1.83x, and drycanal_coop **4.79x** on 20 of its 22 walks
#
# The last one is the point: `places.py` calls drycanal_coop open on a hinge
# fraction that lands exactly on its 0.10 boundary, and by advance it is the
# sharpest corridor in the corpus. The label mispredicts in both directions -
# market_open_coop and sinjar_coop are open by label and pay the wrap - so the
# gate is the measurement, not the label, and a corridor that is called open
# comes out of it with the two walks a corridor has: stock and reversed.
#
# **The second gate: does the round progress?** Advance measures each stage
# against the last. It does not measure whether the sequence goes anywhere, and
# on an open map that is the thing that goes wrong. tell_open_coop, played
# 2026-09-03: objective D is 4,331 u from C and 3,336 u from E while C and E are
# 1,235 u apart, so the round crosses the town and comes straight back. Every
# member of its first family did it, because they were all rotations of one
# ring, and that ring was the order the *chain* numbers the rungs - which on an
# open map is not the order they stand in on the ground. The shipped chain is
# the worst of the lot: 6.79x, against 6.47-6.79x for all 31 layouts built on
# it.
#
# `--max-detour` refuses that, and `--ring tour` (the default) stops producing
# it: the walk each layout takes is now measured off the mesh rather than
# assumed from the chain, and optimised per entry rather than rotated. Same map,
# after: 27 layouts at 1.78-2.35x. 2.5 is above the whole of that band and well
# below anything the old ring produced, and it refuses the shipped chain, which
# is the correct verdict on a round that doubles back five times.
#
# **The third gate: is each stage a fight?** A step can also be too *short*. A
# player on cs_officeb3_coop_v1_5 `enter1_fwd`, 2026-09-17: "objectives D and E
# were too close together" - 155 u apart, so E was captured on arrival at D. The
# walk cost was the cause rather than the map: its last two terms are the
# longest edge and the total, so the shortest edge in the matrix was free money
# and 2-opt sought it out. officeb3 has three rungs in one cluster (0-3 155 u,
# 0-5 820 u, 3-5 929 u) and every emitted layout paired two of them.
#
# **The fourth gate: is the next objective anywhere else?** The third one
# measures the walk, and a walk can be long while the ground is not. A player on
# gioconda_eron_mountains `enter3_fwd`, 2026-09-18: objective I stood 608 u
# across the floor from objective H, 135 u below it, while the mesh path between
# them is 5,591 u because the only way down is round the building.
# `--min-advance` read 5,591 u and passed it.
#
# So the walk cost and `--min-separation` ask the same question of the ground:
# the horizontal distance between two rung floors, and only where they are
# within two storeys of each other in z - a floor above is a different place,
# which is why ministry_coop's stacked pair (38 u apart across the ground, 496 u
# down) is not one of these and has never been complained about. Over the 45
# shipped chains the tightest same-level step is 1,018 u, so the corpus clears
# this as cleanly as it clears the walk; the gate sits at 700 rather than 1,000
# because 1,000 costs cs_officeb3_coop_v1_5 and siege_coop their whole families
# for pairs nobody has complained about. `reverse.SHORT_SEPARATION` is the
# measurement.
#
# Same map, after: its `enter3_fwd` puts no two objectives closer than 2,228 u,
# its detour improves from 1.85x to 1.77x, and 28 of its 34 layouts emit where
# 26 did.
#
# `SHORT_ADVANCE` is now a term in that cost, ranked with the arrival clashes
# above distance, so the optimiser stops choosing it; `--min-advance` refuses
# what is left. 1,000 u is the shipped corpus's own answer - over the 43
# surveyed maps with a cpsetup, the shortest step a shipped chain asks for is
# 1,258 u (siege_coop), then 1,295 (tell_open_coop), median 3,007 - with one
# exception at 0 u, gioconda_eron_mountains, which ships two objectives in the
# same place. So 42 of 43 mappers stay above 1,258 and the gap below them is
# wide.
#
# Measured over the corpus, the cost of the term is two layouts: 393 emitted
# before, 391 after, 35 of 42 maps identical. Four maps lose the fault
# entirely - officeb3's family shortest step 155 u -> 1,702, tell_open_coop
# 442 -> 1,284, drycanal_open_coop 950 -> 3,891, embassy_coop 520 -> 2,275 -
# and officeb3 pays for it, 9 layouts down to 4, because brute force over all
# 5,040 of its walks says entries 1, 2, 4 and 6 have **no** walk that is clean
# and inside the detour gate. Fewer layouts is the right trade: the four it
# emits are each the global optimum from their entry.
#
# **The second gate could not see the ground either, and that is the same
# blindness.** `--max-detour` is a ratio - what the player walks over how far
# the round advances - and both halves were read off the mesh. A player on
# gioconda_eron_mountains `enter3_fwd`, 2026-09-18: the round walked 8,041 u out
# to one objective and 8,041 u straight back to the spot it had just captured,
# because the map ships cp9 and cp10 on one nav area at 4312 -4488 16. The mesh
# distance between the two ends of that stage is 0, the code divided by it under
# an `if straight > 1.0` and so skipped the stage entirely, and the walk reported
# **1.77x - its best number**. The same blindness passed the pair the fourth gate
# was written for: rungs 10 and 11 stand 608 u apart across one floor and 5,591 u
# apart on the mesh, so a walk that goes 10, somewhere, 11 spends 8,498 u to move
# the round 608 u and reads as 1.52x.
#
# So `reverse._detour` asks `rung_gaps` where the two *ends* of a stage stand,
# the way `--min-separation` already asks it of the two ends of a step, and two
# ends that are one place make the detour infinite: there is no multiple of an
# advance of nothing. `--max-detour` refuses it like any other round that
# doubles back, and the term ranks it, so the optimiser stops proposing it.
#
# **Refusing two stages on one piece of ground outright was the other candidate,
# and the corpus says it cannot be afforded.** Eight of the 47 surveyed maps have
# two rungs within `SHORT_SEPARATION` on one level, and on six of them a walk
# cannot avoid using both, because one entry can excuse only one rung of a
# cluster: gioconda_eron_mountains has a clique of three (14-15 at 0 u, 13-14 and
# 13-15 at 253 u) and de_vertigo_coop one of four, so both would emit *nothing*,
# and cs_officeb3_coop_v1_5 - rungs 0, 3 and 5, 155-573 u apart - would lose its
# whole family. What is unique to the map that was complained about is the
# *triple*: out and straight back with one stage in between. Every other map's
# tightest return already has two or more stages inside it, so scoring the triple
# is the measurement that costs the corpus nothing and the errand everything.
#
# **And the optimiser could not see which rungs an objective cannot stand on.**
# A capture volume translates but does not resize, so whether it still covers
# floor depends only on the objective and the rung - the server's "Failed
# finding CP area for N!", which `layout` has always refused *after* choosing a
# walk. `reverse.volume_misfits` asks it of every stage/rung pair up front and
# hands the answer to the optimiser, the way `arrival_clashes` already is: it
# agrees with `layout` exactly (over the whole corpus it forbids no assignment
# an emitted layout was using), and a walk that routes round it keeps the
# layout. Eighteen of the 48 surveyed maps have at least one -
# gioconda_eron_mountains' rung 3 is ground three of its objectives cannot stand
# on, baghdad_remastered's rung 7 is ground for five - and without this the
# shape correction above would have cost that map ten layouts to a constraint it
# could not see.
#
# Measured over the corpus, the two together are **+19 layouts: 302 emitted
# before, 321 after, 34 of 44 maps identical.** Taken one at a time, because
# they pull in opposite directions:
#
#   the detour   302 -> 293, three maps move. gioconda_eron_mountains 28 -> 18
#                and cs_officeb3_coop_v1_5 4 -> 3 (its two entry-0 walks now
#                optimise to the same round), de_vertigo_coop 11 -> 13
#   the misfits  293 -> 321. gioconda_eron_mountains back to 30, above the 28 it
#                had; baghdad_remastered 8 -> 14, market_open_coop and
#                market_night_coop 18 -> 23, embassy_coop 7 -> 10, buhriz_coop
#                3 -> 4; against tell_open_coop 22 -> 19 and siege_coop 9 -> 8,
#                which is the honest price - a walk sent round a rung it cannot
#                use takes a longer leg, and `--max-advance` refuses four of
#                them at 1.54-1.99x
#
# Same map, after: no walk gioconda_eron_mountains emits shuttles out to one
# objective and back, and 30 of its 34 emit where 28 did. It still *returns* to
# that corner - it must, because three of its rungs are one place and the third
# gate forbids taking two of them in a row - but the tightest return left has
# two stages inside it rather than none.
#
# **Every gate above is a judgement about the map as much as the layout, and
# four of them were holding a layout to a standard the map itself does not
# meet.** Measured over the 124-map server list, 2026-09-19: 56 of the 115 maps
# with a stock measurement ship a round one of these gates would refuse.
# de_aztec_coop's shipped chain detours 8.21x against a 2.50x bar, pripyat's
# 3.09x, uprising_open_coop asks a 389 u stage against a 1,000 u floor. Nine of
# those maps emitted nothing at all, so the generator was withholding rounds
# that improve on the one people play today.
#
# So a gate now refuses a walk for being worse than the map, never for being as
# bad as the map already is: the bar is the calibrated number or the stock
# round's own, whichever lets more through. `--max-advance` always worked this
# way - it is a multiple of the stock worst - and the other three now do.
# 796 layouts before, 987 after, 97 maps emitting before and 109 after, and
# nothing that emitted before stops: relaxing a bar cannot refuse what it was
# passing.
#
# The one thing that does not relax is a detour of `inf`, both ends of a stage
# on one piece of ground. There is no multiple of an advance of nothing, and a
# map that ships one is not thereby licensed to ship another.
#
# **The spawn-point floor was the same mistake, one level down.** A stage that
# is fought on a rung borrows the volume the map authored there, by name, and
# moves nothing: the points inside it are the map's own. `MIN_POINTS = 6` was
# being asked of that inherited count, so it measured the map. The shipped maps
# carry 16-17 points in an attacker zone and the server list does not:
# tell_night_coop ships sz_f with 2, sz_h with 3, sz_i with 2, and sz_e and sz_g
# with none at all, where tell_coop - the same geometry in daylight - carries a
# tidy 8 in each. That one refusal was taking 27 of tell_night_coop's 29
# layouts, 21 of 21 from drycanal_coop_old and 11 of 15 from sinjar_night_coop.
# It is now said and not refused; the floor still governs the coordinates a
# *move* invents, which are this code's to answer for.
#
# **And what passes is not the same question as what is worth playing.** A gate
# asks whether a round is playable against the map that ships it, which on a map
# with a bad stock round admits some poor company: after the change above, the
# worst layout emitted on uprising_open_coop detours 5.66x and on
# launch_control_coop_ws 9.31x, because that is exactly what those maps ship.
# `layout_score` ranks the ones that passed - detour first, then a stage longer
# than the map's own worst, then the two floors, then backtracks and thin zones.
# Two lines then decide what is kept, because they answer different halves of
# the question. `--keep-within` is 1.25x the best round *this* map has, and
# drops a round that is poor for this map when there is a better one to rotate
# to. `--keep-good` is 2.20 outright, and keeps a round that is fine by the
# standard of the maps people already play: score the shipped checkpoint maps'
# own rounds the same way and the median is 1.98, with peak_coop at 1.40,
# tell_coop 1.83, revolt_coop 2.08 and sinjar_coop 2.19 under the line against
# heights_coop 2.70 and buhriz_coop 3.25 over it.
#
# The band alone punishes a map for being good at its best - tell_coop's best
# scores 1.41, so the band cut it to 6 layouts of 25 although its worst was an
# ordinary round. Over the 92 emitting maps the band alone keeps 722 of 911 and
# the two together keep 803: 81 more rounds at the same p90 (2.18) and the same
# worst (3.17). `--keep N` is a cap as well, and none of them can empty a map -
# the best layout is within 1.00x of itself. launch_control_coop_ws keeps a
# round that detours 8.24x because that is the best that map can be played, and
# 1.07x better than the one it ships.
#
#   tools/make-presets.sh                 # every survey that has a cpsetup
#   tools/make-presets.sh ministry_coop inferno_new_v1
#   tools/make-presets.sh --all ministry_coop     # the family, on a corridor
#
# Presets land in game/insurgency/presets/<map>.cfg, which is where the applier
# looks them up, and the rich JSON beside them in out/layouts/<map>.json.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

py="$root/.venv/bin/python"
[[ -x "$py" ]] || { echo "no venv - run:  mise run setup" >&2; exit 1; }

force_all=0
# The four gates the header calibrates are `navlayout permute`'s own defaults
# (GATE_MAX_ADVANCE, GATE_MAX_DETOUR, GATE_MIN_ADVANCE, GATE_MIN_SEPARATION in
# `cli.py`), so this
# script no longer names their values: a number written in both places is one
# that can disagree with itself, and it is the generator, not this wrapper,
# that has to refuse a bad walk however it is called. The MM_* variables are
# still here to override one for an experiment, and `off` turns a gate off.
gates=()
[[ -n "${MM_MAX_ADVANCE:-}" ]] && gates+=(--max-advance "$MM_MAX_ADVANCE")
[[ -n "${MM_MAX_DETOUR:-}"  ]] && gates+=(--max-detour  "$MM_MAX_DETOUR")
[[ -n "${MM_MIN_ADVANCE:-}" ]] && gates+=(--min-advance "$MM_MIN_ADVANCE")
[[ -n "${MM_MIN_SEPARATION:-}" ]] && gates+=(--min-separation "$MM_MIN_SEPARATION")
maps=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) force_all=1; shift ;;
    *)     maps+=("$1"); shift ;;
  esac
done

if [[ ${#maps[@]} -eq 0 ]]; then
  while IFS= read -r s; do maps+=("$(basename "$s" .json)"); done \
    < <(find "$root/surveys" -maxdepth 1 -name '*.json' | sort)
fi

presets="$root/game/insurgency/presets"
layouts="$root/out/layouts"
mkdir -p "$presets" "$layouts"

# One map, and never the batch. `navlayout` exits non-zero for two different
# reasons and both used to be invisible: a map whose whole family is refused by
# the gates says so and exits 1, and a map the generator cannot read at all
# raises. Neither is a reason to stop regenerating the other forty, so each is
# counted and named at the end instead - and since `main()` now returns its
# code to the shell, `set -e` would otherwise stop at the first of them.
run_map() {  # run_map <what> <cmd...>
  local what="$1"; shift
  # `$?` inside an `if` is the `if`'s own status, not the command's, so the
  # status is taken in a `||` list - which is also what keeps `set -e` from
  # ending the batch on the first map that refuses everything.
  local rc=0
  "$@" || rc=$?
  [[ $rc -eq 0 ]] \
    || echo "   ! $map: $what exited $rc - no preset written, see above"
  return "$rc"
}

made=0 skipped=0 failed=0
declare -a failures=()
for map in "${maps[@]}"; do
  # No cpsetup means no objective chain, and every layout here is a
  # permutation of one. Ask cheaply, before doing any work.
  if [[ ! -f "$root/game/insurgency/maps/$map.txt" ]]; then
    skipped=$((skipped + 1))
    continue
  fi

  kind=""
  if [[ $force_all -eq 0 ]]; then
    tmp="$(mktemp)"
    if "$py" -m navlayout places "$map" --json "$tmp" >/dev/null 2>&1; then
      kind="$("$py" -c "import json,sys; print(json.load(open(sys.argv[1]))['kind'])" "$tmp")"
    fi
    rm -f "$tmp"
  fi

  cfg="$presets/$map.cfg"
  json="$layouts/$map.json"
  case "$kind" in
    small)
      echo "== $map: small - the whole family, rotated"
      run_map permute "$py" -m navlayout permute "$map" --all "${gates[@]}" \
        --cfg "$cfg" --json "$json" | tail -3 \
        || { failed=$((failed + 1)); failures+=("$map"); continue; }
      ;;
    linear|segmented)
      # A corridor licenses the reversal and the reversal is one walk, so a
      # corridor whose reversal is refused used to get nothing at all - and the
      # refusal is usually about *that* walk rather than about the map. Four of
      # the server list's corridors were in that position on 2026-09-19:
      # caves_coop, crash_course, dead_air and fairgrounds, each refused because
      # the reversal fights its last stage on the entry rung while the zone the
      # map authored one rung along stands over that same ground - the round
      # would start with the attackers inside the objective. It is a real defect
      # on crash_course (12 of 12 spawn points inside the capture volume) and
      # dead_air (14 of 14), so the refusal is right and the map still wants a
      # layout.
      #
      # So the family is the fallback, not the prescription: asked for only when
      # the reversal is refused, ranked by `layout_score`, and `--max-advance` is
      # still what refuses a walk that asks one stage to cross the corridor
      # (docs/permute.md S2). fairgrounds 0 -> 16 layouts, caves_coop 0 -> 3,
      # crash_course 0 -> 2; dead_air stays at nothing, which is the honest
      # answer for a map whose every walk arrives inside its own objective.
      echo "== $map: $kind - reversed"
      if run_map reverse "$py" -m navlayout reverse "$map" --cfg "$cfg" --json "$json" \
           | tail -2; then :; else
        echo "   $map: the reversal is refused - asking the family instead"
        run_map permute "$py" -m navlayout permute "$map" --all "${gates[@]}" \
          --cfg "$cfg" --json "$json" | tail -3 \
          || { failed=$((failed + 1)); failures+=("$map"); continue; }
      fi
      ;;
    "")
      if [[ $force_all -eq 1 ]]; then
        echo "== $map: the whole family, asked for"
        run_map permute "$py" -m navlayout permute "$map" --all "${gates[@]}" \
          --cfg "$cfg" --json "$json" | tail -3 \
        || { failed=$((failed + 1)); failures+=("$map"); continue; }
      else
        skipped=$((skipped + 1)); continue
      fi
      ;;
    open)
      # Re-siting is what an open map's shape licenses and PLAN.md step B is
      # where that lives. The family is what it can have today, and the gate
      # above is what keeps a mislabelled corridor from shipping 20 walks that
      # cross the map. See the header.
      echo "== $map: open - the whole family, minus any walk that pays for the wrap"
      run_map permute "$py" -m navlayout permute "$map" --all "${gates[@]}" \
        --cfg "$cfg" --json "$json" | tail -3 \
        || { failed=$((failed + 1)); failures+=("$map"); continue; }
      ;;
    *)
      skipped=$((skipped + 1)); continue
      ;;
  esac
  made=$((made + 1))
done

echo
echo "$made preset file(s) in $presets, $skipped map(s) skipped, $failed emitted nothing"
if [[ $failed -gt 0 ]]; then
  echo "no preset written for: ${failures[*]}"
  echo "each of those keeps whatever file it already had, which may predate this run"
fi

# Which is worth knowing before a release, because release/presets is a mirror
# and a refused map's old file rides along in it. Three were found doing that on
# 2026-09-20 - caves_coop, docks_day and station_aof still held presets from the
# day before, written before the mesh gate began denying the nav connections the
# geometry refuses, and refused outright when re-run. They were deleted by hand
# and are regenerable. Nothing here deletes them for you: a generator that
# removes its own outputs on a failure is one transient error away from throwing
# away work, and the place to want an empty directory is before a full run, not
# after each map.
#
# release/presets is the committed copy a server gets. Mirrored rather than
# written twice, so the generator keeps one output directory and the payload
# cannot hold a preset the game tree does not.
rsync -a --delete "$presets/" "$root/release/presets/" 2>/dev/null \
  || { mkdir -p "$root/release/presets"; cp -f "$presets"/*.cfg "$root/release/presets/"; }
echo "release/presets: $(ls "$root/release/presets" | wc -l) file(s)"
