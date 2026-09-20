#!/usr/bin/env bash
# Load a map on the layout server, rotate its presets through it, and report
# what the engine said about each one.
#
# `navlayout` refuses a layout on the two things it can answer offline - a
# capture volume covering no floor, a rung with nowhere to stand. Everything
# else is the engine's to say, and up to now saying it meant sitting at a
# console watching one preset. This drives that: it boots the layout server,
# changelevels the map once per round so the applier picks the next preset,
# starts a round so the cache pass runs, and reads back the applier's own
# verdict beside the two lines only the game prints.
#
#   tools/check-layout.sh ministry_coop                # stock, then one preset
#   tools/check-layout.sh --rounds 13 inferno_new_v1   # a whole family
#   tools/check-layout.sh --preset reversed market_coop
#
# **The first load is always stock, and that is the control.** The plugin is
# loaded by the server config, which the engine executes *after* the first
# map's OnMapInit - so load 1 carries no preset whatever the rotation says.
# Every later load is a changelevel, by which time the plugin is there. That
# is also why this passes --no-boot-reload: layout-server.sh reloads the boot
# map by itself now, and here that first load is the measurement. Both
# numbers that matter are read against that load rather than against zero:
# ministry_coop prints one `Failed finding CP area` unmodified and rejects one
# spawn point unmodified, and a count without its control reads as damage this
# project did.
#
# What is a failure here, and what is only worth seeing:
#
#   fail      a rule that did not describe the map - unmatched, stuck, missing
#             or misnamed; an objective the map lost that stock kept; a spawn
#             point rejected that stock accepted.
#   report    adrift volumes (ministry_coop's cap6 reads adrift on the stock
#             map too - the origin brush is 1,185 u from the volume it names),
#             shadowed and nudged rules, and a cache marker that has not landed
#             by the post-load pass.
#
# **The marker column is pessimistic here and always will be.** A cache marker
# takes its move from the round-start burst, not from the post-load pass, and
# this harness never starts a round: `mp_restartgame` on an empty checkpoint
# server segfaults it. What the post-load pass does settle is the cache itself,
# every lump rule, and both engine lines - so a run says whether the layout
# describes the map, and a player is still what says whether the markers
# followed their caches.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

map=""
preset=""
rounds=1
port="${MM_PORT:-27016}"
# Off by default, as on the played server: a permuted chain can be walled off
# its own route by a volume no preset touched.
blockzones="${MM_BLOCKZONES:-off}"
# How long one map load may take before the run is called stuck. A cold load
# of a big workshop map is tens of seconds; toujane_b2 is a 311 MB BSP.
timeout="${MM_LOAD_TIMEOUT:-240}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds)     rounds="$2"; shift 2 ;;
    --preset)     preset="$2"; shift 2 ;;
    --port)       port="$2"; shift 2 ;;
    --blockzones) blockzones="$2"; shift 2 ;;
    *)            map="$1"; shift ;;
  esac
done

[[ -n "$map" ]] || {
  echo "usage: check-layout.sh [--rounds N] [--preset NAME] [--port P] <map>" >&2
  exit 1; }

game="$root/game/insurgency"
presets="$game/presets/$map.cfg"
[[ -f "$presets" ]] || {
  echo "no presets/$map.cfg - run:  navlayout permute $map --all --cfg $presets" >&2
  exit 1; }

out="${MM_OUT:-$root/out/layouts}"
mkdir -p "$out"
console="$out/$map.console.log"

# Everything is read back off this server's own console rather than out of
# SourceMod's log directory: LogError goes to errors_<date>.log and LogMessage
# to L<date>.log, both shared with any other server running out of this game
# tree, and the console carries both in one stream in load order.
tmp="$(mktemp -d)"
fifo="$tmp/console"
mkfifo "$fifo"
# The engine reads console commands from stdin, which is how a changelevel is
# asked for without an rcon password or a terminal. The extra fd keeps the
# pipe open: a fifo whose only writer has exited reads as EOF, and srcds takes
# EOF on stdin as a reason to shut down.
exec 9<>"$fifo"

cleanup() {
  # srcds ignores SIGTERM, and a server left running holds the port and goes on
  # writing to the log directory the next run reads.
  if [[ -n "${srv:-}" ]] && kill -0 "$srv" 2>/dev/null; then
    echo "quit" >&9 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$srv" 2>/dev/null || break; sleep 1; done
    kill -9 "$srv" 2>/dev/null || true
  fi
  exec 9>&-
  rm -rf "$tmp"
}
trap cleanup EXIT

# Under a pty, because srcds writes its console with ordinary stdio: into a
# pipe that is a 4 KB block buffer, and a run that watches the log for a line
# the server printed two minutes ago looks exactly like a server that hung.
echo "== $map: the stock control, then $rounds preset load(s), port $port"
script -qfe -c "MM_PORT=$port '$root/tools/layout-server.sh' --no-boot-reload \
  --port $port --blockzones $blockzones ${preset:+--preset $preset} $map" \
  /dev/null <"$fifo" >"$console" 2>&1 &
srv=$!

# The applier's post-load pass runs on a timer a few seconds after the map is
# up and runs on every load, the stock one included - so counting those lines
# counts loads that have finished, which is the thing to wait for.
wait_for_loads() {
  local want="$1" waited=0 have
  while :; do
    have=$(grep -c "\[layout\] verify: map-wide" "$console" 2>/dev/null || true)
    [[ "$have" -ge "$want" ]] && return 0
    kill -0 "$srv" 2>/dev/null || { echo "server exited early - see $console" >&2; return 1; }
    sleep 2
    waited=$((waited + 2))
    [[ "$waited" -ge "$timeout" ]] && {
      echo "load $want did not finish in ${timeout}s - see $console" >&2; return 1; }
  done
}

wait_for_loads 1 || exit 1
echo "   load 1: stock - the plugin is not loaded until the server config runs"

for ((i = 1; i <= rounds; i++)); do
  echo "changelevel $map" >&9
  wait_for_loads $((i + 1)) || exit 1
  echo "   load $((i + 1)): $(grep "\[layout\] $map: preset" "$console" | tail -1 \
    | sed "s/.*preset //; s/ - \([0-9]*\) edits.*/  (\1 edits)/")"
done

echo "quit" >&9
wait "$srv" 2>/dev/null || true

# ── what the engine said, per load ──────────────────────────────────
awk -v map="$map" '
  function row(l) {
    printf "%-5d %-16s %6s %7s %6d %6d %6d %6d %11s %8d\n",
      l, (preset[l] == "" ? "stock" : preset[l]),
      (edits[l] == "" ? "-" : edits[l]), (rules[l] == "" ? "-" : rules[l]),
      bad[l] + 0, adrift[l] + 0, marker[l] + 0, shadow[l] + 0,
      (hull[l] == "" ? "-" : hull[l]), cparea[l] + 0
  }
  # The console comes off a pty, so every line ends in CR as well.
  { sub(/\r$/, "") }
  # The first load says NewGame and every changelevel says Changelevel.
  /---- Host_(NewGame|Changelevel) ----/       { load++ }
  $0 ~ ("\\[layout\\] " map ": preset")        {
      # [layout] <map>: preset "X" - N edits applied, ..., M of R rules ...
      p = $0; sub(/.*preset /, "", p); q = p; sub(/ -.*/, "", q); gsub(/'"'"'/, "", q)
      preset[load] = q
      if (match(p, /- [0-9]+ edits/))  { e = substr(p, RSTART + 2); sub(/ .*/, "", e); edits[load] = e }
      if (match(p, /of [0-9]+ rules/)) { r = substr(p, RSTART + 3); sub(/ .*/, "", r); rules[load] = r }
  }
  # verify <class>: N rules over M entities - unmatched=.. stuck=.. ...
  /\[layout\] verify [a-z_]+:.*unmatched=/ {
      for (i = 1; i <= NF; i++) {
        split($i, kv, "=")
        if (kv[2] == "") continue
        if (kv[1] == "unmatched" || kv[1] == "missing" || kv[1] == "stuck" || kv[1] == "misnamed")
          bad[load] += kv[2]
        else if (kv[1] == "adrift")   adrift[load] += kv[2]
        else if (kv[1] == "shadowed") shadow[load] += kv[2]
      }
  }
  /\[layout\] verify: map-wide/ {
      if (match($0, /map-wide [0-9]+ of [0-9]+/)) {
        split(substr($0, RSTART, RLENGTH), w, " ")
        hull[load] = w[2] "/" w[4]
      }
  }
  # A marker that has not landed. Counted per load, and the round-start burst
  # is why the last one of these is the one that counts.
  /control point .*asked for/   { marker[load]++ }
  /Failed finding CP area/      { cparea[load]++ }
  END {
    printf "%-5s %-16s %6s %7s %6s %6s %6s %6s %11s %8s\n",
      "load", "preset", "edits", "rules", "bad", "adrift", "marker", "shadow",
      "hull", "CP area"
    for (l = 1; l <= load; l++) row(l)
  }' "$console" > "$tmp/table"

echo
cat "$tmp/table"
cat <<'KEY'

bad     rules that did not describe the map - unmatched, stuck, missing, misnamed
adrift  a brush volume that is not around its own origin  (see the header)
marker  cache markers not yet on their cache when the pass read them back
hull    spawn points that fit the player hull, map-wide
KEY

# Load 1 is the map's own answer to the last two columns, and everything else
# is read against it.
read -r stock_hull stock_cp <<<"$(awk 'NR == 2 { print $9, $10 }' "$tmp/table")"
stock_fit=${stock_hull%%/*}

fail=0
while read -r load preset _ _ bad _ _ _ hull cparea; do
  [[ "$load" == "load" || "$load" == 1 ]] && continue
  [[ "$bad" -gt 0 ]] && { echo "FAIL load $load ($preset): $bad rule(s) did not describe the map"; fail=1; }
  [[ "$cparea" -gt "$stock_cp" ]] && { echo "FAIL load $load ($preset): $((cparea - stock_cp)) objective(s) with no nav area that stock kept"; fail=1; }
  fit=${hull%%/*}
  [[ "$fit" -lt "$stock_fit" ]] && echo "note load $load ($preset): $((stock_fit - fit)) spawn point(s) that stock accepted no longer fit the hull"
done < "$tmp/table"

echo
echo "console -> $console"
if [[ "$fail" -ne 0 ]]; then
  echo "FAIL  the engine did not accept every layout above"
  exit 1
fi
echo "PASS  every preset applied, and kept every objective and route the stock load had"
