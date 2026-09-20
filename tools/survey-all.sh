#!/usr/bin/env bash
# Survey a whole map list, unattended, and survive the maps that kill the
# server.
#
# tools/survey.sh boots one server and walks a list, which is the right shape
# for a handful of maps and the wrong one for 140: the batch is one process, so
# a map that crashes srcds or hangs the load ends every map after it, and
# nothing says which one did it. This drives that script instead of replacing
# it - it computes what is still missing, launches a batch for exactly those,
# watches for progress, and starts again after a death - so the unit of failure
# is one map rather than the run.
#
# **A survey can also come back empty.** The dump fires a fixed delay after the
# map starts, and the nav mesh is decorated some way into the load: a big map
# read 0 areas at 12 s (tools/survey.sh's own note about toujane_b2). That
# writes a file, so "the file exists" is not the test - `area_count`, the
# entity inventory and the `end` marker are. An empty one is deleted and
# retried with the delay doubled, and a map that comes back empty twice is
# named at the end rather than silently carried into the preset run, where it
# would look like a map with no objectives.
#
#   tools/survey-all.sh                       # every installed map with no survey
#   tools/survey-all.sh --cycle serverconfig/mapcycle_checkpoint.txt
#   tools/survey-all.sh --force ministry_coop
#   MM_SURVEY_DELAY=20 tools/survey-all.sh    # a slower floor for big maps
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
game="$root/game/insurgency"
out="$game/surveys"

# A batch that stops producing surveys is either loading a very big map or
# wedged, and they look the same from here. The watchdog gives up on silence
# rather than on a clock, so a slow map only costs its own load.
stall="${MM_SURVEY_STALL:-420}"
# How many launches one map may cost before it is left out of the run.
tries="${MM_SURVEY_TRIES:-2}"
delay0="${MM_SURVEY_DELAY:-14.0}"

force=0
cycle=""
maps=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=1; shift ;;
    --cycle) cycle="$2"; shift 2 ;;
    -*)      echo "unknown option $1" >&2; exit 2 ;;
    *)       maps+=("$1"); shift ;;
  esac
done

# A mapcycle is "<map> <mode>" per line, and only the checkpoint lines have an
# objective chain for a preset to permute. The file is a server's own, so it
# arrives with CRLF and the \r rides on the mode token.
if [[ -n "$cycle" ]]; then
  [[ -f "$cycle" ]] || { echo "no mapcycle at $cycle" >&2; exit 1; }
  while IFS= read -r m; do maps+=("$m"); done < <(
    awk '{sub(/\r$/, "")}
         !/^[[:space:]]*\/\//{ if (tolower($2) == "checkpoint") print $1 }' "$cycle")
fi

if [[ ${#maps[@]} -eq 0 ]]; then
  while IFS= read -r b; do maps+=("$(basename "$b" .bsp)"); done \
    < <(find "$game/maps" -maxdepth 1 -name '*.bsp' | sort)
fi

# A mapcycle names maps by whatever the server that wrote it had installed, and
# some entries are capitalised where the .bsp on disk is not. The engine takes
# either; the file test here has to be told.
resolve() {  # resolve <name> -> the .bsp basename as it is on disk, or nothing
  local m="$1"
  [[ -f "$game/maps/$m.bsp" ]] && { printf '%s\n' "$m"; return 0; }
  local hit
  hit="$(find "$game/maps" -maxdepth 1 -iname "$m.bsp" -print -quit 2>/dev/null)"
  [[ -n "$hit" ]] && { basename "$hit" .bsp; return 0; }
  return 1
}

# Is this survey usable? See the header: the file existing is not the test.
# Read off the two ends of the file rather than parsed - `area_count` is in the
# first line or two and the `end` marker is the last, and a survey is megabytes
# of nav areas in between. This is asked once per map per round and the
# watchdog polls while a round runs, so the cost of asking matters.
good() {  # good <map>
  local f="$out/$1.json" n
  [[ -s "$f" ]] || return 1
  tail -c 64 "$f" | grep -q '"end": true' || return 1
  n="$(head -c 400 "$f" | sed -n 's/.*"area_count": *\([0-9][0-9]*\).*/\1/p')"
  [[ -n "$n" && "$n" -gt 0 ]]
}

mkdir -p "$out"
declare -A attempt=()
declare -A missing_on_disk=()
declare -a wanted=()
seen=""
for m in "${maps[@]}"; do
  # A mapcycle repeats nothing, but a list built from one plus the maps dir can.
  [[ ":$seen:" == *":$m:"* ]] && continue
  seen="$seen:$m"
  if name="$(resolve "$m")"; then
    wanted+=("$name")
  else
    missing_on_disk["$m"]=1
  fi
done

echo "${#wanted[@]} map(s) asked for, ${#missing_on_disk[@]} not installed here"
if [[ ${#missing_on_disk[@]} -gt 0 ]]; then
  echo "not installed: ${!missing_on_disk[*]}"
  echo "(tools/workshop-maps.sh installs what the workshop roots have)"
fi

todo() {  # the maps still wanting a survey, in order, minus the ones given up on
  local m
  for m in "${wanted[@]}"; do
    [[ "${attempt[$m]:-0}" -ge "$tries" ]] && continue
    if [[ $force -eq 0 ]] && good "$m"; then continue; fi
    printf '%s\n' "$m"
  done
}

count_good() { local n=0 m; for m in "${wanted[@]}"; do good "$m" && n=$((n+1)); done; echo "$n"; }

# What the watchdog watches. A survey appearing at all is progress, even if it
# turns out to be an empty one - the round is working its way down the list
# either way, and `good` is what decides at the end of it.
count_files() { find "$out" -maxdepth 1 -name '*.json' | wc -l; }

have0="$(count_good)"
echo "$have0 of ${#wanted[@]} already surveyed"
echo

round=0
while :; do
  mapfile -t batch < <(todo)
  [[ ${#batch[@]} -gt 0 ]] || break
  round=$((round + 1))
  head="${batch[0]}"
  attempt["$head"]=$(( ${attempt[$head]:-0} + 1 ))
  # A map that came back empty gets a longer look at its own nav mesh the
  # second time round, since that is what "empty" usually means.
  delay="$delay0"
  [[ "${attempt[$head]}" -gt 1 ]] && delay="$(awk -v d="$delay0" 'BEGIN{print d*2}')"
  # An empty file from a previous try would count as done to survey.sh's own
  # bookkeeping; it does not to ours, but it would be left behind on a map we
  # give up on, looking like a survey.
  [[ -f "$out/$head.json" ]] && ! good "$head" && rm -f "$out/$head.json"

  echo "── round $round: ${#batch[@]} map(s) left, from $head (delay ${delay}s," \
       "try ${attempt[$head]}/$tries)"

  log="$root/cache/survey-round-$round.log"
  MM_SURVEY_DELAY="$delay" "$root/tools/survey.sh" "${batch[@]}" >"$log" 2>&1 &
  pid=$!

  # Watch for progress rather than for a clock: any new usable survey resets
  # the timer, and silence for $stall means the server is wedged or gone.
  last="$(count_files)"
  quiet=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    now="$(count_files)"
    if [[ "$now" -gt "$last" ]]; then
      printf '   %d surveyed\n' "$now"
      last="$now"; quiet=0
    else
      quiet=$((quiet + 10))
      if [[ "$quiet" -ge "$stall" ]]; then
        echo "   ! no survey in ${stall}s - killing the batch at $head"
        kill "$pid" 2>/dev/null || true
        sleep 2
        kill -9 "$pid" 2>/dev/null || true
        break
      fi
    fi
  done
  wait "$pid" 2>/dev/null || true
  # srcds under `docker compose run` does not always die with the wrapper.
  # Matched on the compose project's own container names rather than on the
  # image: MM_SRCDS_IMAGE can point at an image another project built and
  # happens to be running, and this must not reach into that.
  docker ps -q --filter "name=^mapmaker[-_]" 2>/dev/null \
    | xargs -r docker kill >/dev/null 2>&1 || true

  if [[ "$(count_good)" -le "$have0" && "$round" -gt 1 ]]; then
    echo "   (no progress this round - see $log)"
  fi
  have0="$(count_good)"
done

# The analysis reads surveys/ at the repo root; the plugin writes into the game
# tree. One is a copy of the other and this is where the copy happens.
mkdir -p "$root/surveys"
cp -u "$out"/*.json "$root/surveys/" 2>/dev/null || true

echo
ok=0; bad=()
for m in "${wanted[@]}"; do
  if good "$m"; then ok=$((ok + 1)); else bad+=("$m"); fi
done
echo "$ok of ${#wanted[@]} map(s) surveyed -> $root/surveys/"
if [[ ${#bad[@]} -gt 0 ]]; then
  echo "${#bad[@]} map(s) gave nothing usable in $tries tries:"
  printf '   %s\n' "${bad[@]}"
  echo "cache/survey-round-*.log has what the server said about each"
fi
