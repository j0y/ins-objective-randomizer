#!/usr/bin/env bash
# Stage C of docs/layout-variants.md: the playable server.
#
# Loads game/insurgency/presets/<map>.cfg, rewrites the entity lump at
# OnLevelInit, and rotates presets round-robin between loads. Clients join
# stock - the .bsp on disk is untouched, so a workshop map stays the
# subscriber's map and there is nothing to download.
#
#   tools/layout-server.sh ministry_coop
#   tools/layout-server.sh --preset west-compound ministry_coop
#   tools/layout-server.sh --port 27016 --players 8 ministry_coop
#   tools/layout-server.sh --no-boot-reload ministry_coop
#   tools/layout-server.sh --stock ministry_coop
#
# **`--stock` is the control.** It boots the same server with
# `mm_layout_enabled 0`, so every map loads as it ships and the applier still
# says so - `sm_preset` in the console or `!preset` in chat answers
# "stock - layouts are off" rather than leaving someone to wonder whether the
# preset failed. `sm_layout on` at the console turns them back on from the next
# load without restarting anything.
#
# **The map a server boots on is stock, so this reloads it once.** The engine
# executes the server config *after* the first map's OnMapInit, and the config
# is what loads the plugin - so nothing is applied to that first map whatever
# the rotation says, and getting a layout used to mean typing `changelevel` at
# the console. This waits for the plugin's own post-load line and then feeds
# one changelevel down stdin, which is where the engine reads console commands
# from. `--no-boot-reload` is for tools/check-layout.sh, whose stock control
# *is* that first load.
#
# **A client joining needs a workshop map announced, not just present**: see
# the sv_workshop_enabled block below. Run tools/workshop-maps.sh first and the
# announcement is already written.
#
# **One map, or a rotation.** Named alone, a map is pinned and the rotation is
# between its own presets, which is what a checked layout wants. `--cycle`
# takes a server's mapcycle instead - serverconfig/mapcycle_checkpoint.txt is one,
# straight off a server that plays these maps - and serves every checkpoint
# entry of it that is installed here, with that map's own presets applied at
# each load. The applier looks a preset up by the name of the map it is on, so
# a rotation needs nothing else; a map with no preset file loads stock and says
# so.
#
#   tools/layout-server.sh --cycle serverconfig/mapcycle_checkpoint.txt
#   MM_GAMEMODE=conquer tools/layout-server.sh --cycle serverconfig/mapcycle_checkpoint.txt
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

map=""
preset=""
cycle=""
# The playlist is a map whitelist as well as a ruleset, and the shipped ones
# name the 13 official checkpoint maps and nothing else: an entry outside it is
# dropped with "Ignoring entry ... not in playlist allowed_maps whitelist" and
# a rotation of workshop maps comes to nothing. `custom` is the playlist Valve
# ships for this - "for server owners who wish to have stats recorded but not
# use an existing playlist", with an empty allowed_maps.
playlist="${MM_PLAYLIST:-custom}"
# **One mode per run, because the engine will not change gamemode after boot.**
# A server's mapcycle is not one mode - the pair this was built against is 146
# checkpoint entries and 39 conquer ones over the same maps - and serving both
# in one rotation looks free, since the applier reads mp_gamemode at map init
# and declines a conquer load rather than permuting a ladder that mode never
# climbs. It is not free. The engine initialises its gamemode settings once, at
# startup; setting mp_gamemode afterwards changes what the cycle and the plugin
# report and nothing else. Measured on buhriz, 2026-09-19:
#
#   fresh boot, conquer, 24 slots            gamemode settings load
#   fresh boot, conquer, 8 slots             gamemode settings load
#   boot checkpoint, then mp_gamemode
#     conquer + changelevel buhriz           FAIL
#
# The failure is "Game mode settings failed to load" and "InitSpawnPoints: no
# gamemode data", after which team 2 collects 0 spawn points on every attempt
# and the bots' investigation state takes the server down with SIGSEGV once a
# round tries to run - which is how a full-mapcycle run died at its first
# conquer entry. So the cycle is filtered to one mode and the rotation stays
# inside it. The conquer half is a run of its own: MM_GAMEMODE=conquer.
mode="${MM_GAMEMODE:-checkpoint}"
# 0 is what a single pinned map wants: the round decides when it is over. A
# rotation wants a ceiling, or a map nobody finishes is the last one served.
timelimit="${MM_TIMELIMIT:-0}"
players=8
port="${MM_PORT:-27015}"
# Restricted areas hold the attackers behind the objective, and permuting the
# ground under the chain moves which side that is - a reversed layout can be
# walled off its own route by a volume no preset touched. So they come off by
# default, and MM_BLOCKZONES=keep (or --blockzones keep) puts them back for a
# layout that wants the map's own walls. keep|off.
blockzones="${MM_BLOCKZONES:-off}"
# Whether the applier applies anything at all. A layout server with the layouts
# off is the control every judgement about a layout is made against - the same
# maps, the same rotation, the same plugin reporting itself, and nothing moved -
# and it is one flag rather than a second server config. It also turns the boot
# reload off below, because the reload exists to get off a stock map and this is
# asking for one.
enabled="${MM_LAYOUT_ENABLED:-1}"
# Reload the boot map once, unprompted, so the server does not sit on a stock
# map waiting to be told. 0 leaves the first load alone.
reload="${MM_BOOT_RELOAD:-1}"
# How long that first load may take before the reload is given up on. A cold
# load of a big workshop map is tens of seconds; toujane_b2 is a 311 MB BSP.
boot_timeout="${MM_BOOT_TIMEOUT:-240}"
# See the generated config below for what this is for and why it is not a
# secret.
rcon_pass="${MM_RCON_PASS:-mapmaker}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)     preset="$2"; shift 2 ;;
    --cycle)      cycle="$2"; shift 2 ;;
    --playlist)   playlist="$2"; shift 2 ;;
    --timelimit)  timelimit="$2"; shift 2 ;;
    --players)    players="$2"; shift 2 ;;
    --port)       port="$2"; shift 2 ;;
    --blockzones) blockzones="$2"; shift 2 ;;
    --no-boot-reload) reload=0; shift ;;
    --stock)      enabled=0; shift ;;
    --layouts)    enabled=1; shift ;;
    *)            map="$1"; shift ;;
  esac
done

game="$root/game/insurgency"

# ── the rotation ────────────────────────────────────────────────────
# A mapcycle is "<map> <mode>" per line, CRLF because it comes off a server
# that runs on Windows. Only the entries in this server's mode are taken:
# a preset is a permutation of an objective chain and conquer has none, and a
# mode this server is not running would be asked for by name. An entry whose
# .bsp is not installed here is dropped rather than left in, because the engine
# does not skip a missing map gracefully - it fails the load and stops.
cyclefile="mapcycle_mapmaker.txt"
if [[ -n "$cycle" ]]; then
  [[ -f "$cycle" ]] || { echo "no mapcycle at $cycle" >&2; exit 1; }
  kept=(); dropped=()
  while IFS= read -r name; do
    # A mapcycle carries whatever case the server that wrote it had, and some
    # entries are capitalised where the .bsp is not. The engine takes either;
    # the file test here has to be told.
    hit="$(find "$game/maps" -maxdepth 1 -iname "$name.bsp" -print -quit 2>/dev/null || true)"
    if [[ -n "$hit" ]]; then kept+=("$(basename "$hit" .bsp)"); else dropped+=("$name"); fi
  done < <(awk '{sub(/\r$/, "")}
                !/^[[:space:]]*\/\//{ if (tolower($2) == "'"$mode"'") print $1 }' "$cycle")

  [[ ${#kept[@]} -gt 0 ]] || { echo "no $mode entry in $cycle is installed here" >&2; exit 1; }
  { echo "// Generated by tools/layout-server.sh from $cycle - edits are overwritten."
    printf '%s '"$mode"'\n' "${kept[@]}"; } > "$game/$cyclefile"
  echo "${#kept[@]} $mode map(s) in $game/$cyclefile, ${#dropped[@]} not installed here"
  [[ ${#dropped[@]} -gt 0 ]] && echo "   not installed: ${dropped[*]}"
  # The boot map is the head of the rotation unless one was named.
  [[ -n "$map" ]] || map="${kept[0]}"
fi

[[ -n "$map" ]] || { echo "usage: layout-server.sh [--preset N] [--cycle FILE] [--port N] [--blockzones keep|off] [--no-boot-reload] <map>" >&2; exit 1; }

[[ -f "$game/addons/sourcemod/plugins/disabled/mapmaker_layout.smx" ]] || {
  echo "layout plugin is not built - run tools/build-plugins.sh" >&2; exit 1; }

mkdir -p "$game/presets" "$game/cfg"

if [[ ! -f "$game/presets/$map.cfg" ]]; then
  echo "warning: no presets/$map.cfg - the map will load stock" >&2
fi

cat > "$game/cfg/mapmaker_layout.cfg" <<CFG
// Generated by tools/layout-server.sh - edits here are overwritten.
hostname "mapmaker layout"
sv_lan 1
sv_pure 0
mp_timelimit $timelimit

// The playlist doubles as the map whitelist; see the top of this script.
sv_playlist "$playlist"
mp_gamemode "$mode"

// The console is the only way to drive this server, and a detached container
// has none - so rcon is how a changelevel, a pin or a status is asked for.
// sv_lan 1 and -insecure, on a LAN: the password is a formality the protocol
// requires, and MM_RCON_PASS changes it.
rcon_password "$rcon_pass"

// **A workshop map has to be announced, not just present.** The server
// publishes the WorkshopContent string table, the client mounts its own
// subscribed copy of each file ID in it, and insurgency/maps/<name>.bsp says
// nothing - so a subscriber joining a server that serves it as a loose file is
// told "missing map" while the file sits in their own workshop tree. The IDs
// come from tools/workshop-maps.sh, which knows them because they are the
// directories it copied the maps out of - or, when it was given a server's
// subscription list, from the list, which is the only place an asset-only item
// with no .bsp of its own is named.
sv_workshop_enabled $([[ -s "$game/subscribed_file_ids.txt" ]] && echo 1 || echo 0)
sv_workshop_mapcycle_start 0
$(if [[ -n "$cycle" ]]; then
cat <<ROT
// The rotation. sv_workshop_mapcycle_start stays off: this cycle is a file
// that names installed maps, not the workshop list the engine would build for
// itself from subscribed_file_ids.txt.
mapcyclefile "$cyclefile"
ROT
fi)

// The post-load hull verdict is a timer, and an empty Source server hibernates
// rather than running timers. Reading the verdict before anyone joins needs
// this off; see tools/survey.sh.
sv_hibernate_when_empty 0

sm plugins load disabled/mapmaker_layout

mm_layout_enabled $enabled
mm_layout_dir "presets"
mm_layout_max_failures 3
mm_layout_debug 0
mm_layout_blockzones $([[ "$blockzones" == off ]] && echo 0 || echo 1)
// Which layout a round was, in chat, once per map and once to each player who
// joins. Off, because a player told on load which of N walks this is knows
// before the round starts both that the map is permuted and how many
// permutations there are. \`sm_preset\` still answers anyone who asks, and the
// log has it either way. MM_LAYOUT_ANNOUNCE=1 for a test that wants it said.
mm_layout_announce ${MM_LAYOUT_ANNOUNCE:-0}
CFG

# A pin names which layout; with the layouts off there is no which. Said
# rather than silently dropped, because the two flags together are a mistake
# worth hearing about before the server comes up.
if [[ "$enabled" == 0 ]]; then
  if [[ -n "$preset" ]]; then
    echo "[layout-server] --stock overrides --preset $preset: nothing is pinned" >&2
    preset=""
  fi
  # The boot reload exists to get off the stock map the server comes up on.
  # That is what was asked for.
  reload=0
fi

if [[ -n "$preset" ]]; then
  echo "sm_layout \"$preset\"" >> "$game/cfg/mapmaker_layout.cfg"
fi

echo "layout server on port $port, map $map${preset:+, preset $preset}${blockzones:+, restricted areas $blockzones}$([[ "$enabled" == 0 ]] && echo ", layouts OFF - every map loads stock")"

if [[ "$reload" == 0 ]]; then
  exec "$root/tools/srcds.sh" --service layout --map "$map" \
    --players "$players" --port "$port" --cfg mapmaker_layout.cfg
fi

# ── the boot reload ─────────────────────────────────────────────────
# The signal that the first load is over is the applier's own post-load pass,
# which runs on every load - the stock one included - a few seconds after the
# map is up. It is read out of SourceMod's log rather than off the console so
# that the console stays exactly what it was: the server's, on the terminal.
# Counted across every L<date>.log, so a server booted just before midnight
# still sees its own line. (Another layout server running out of the same game
# tree writes here too. There is one game tree and one port; that is a note
# rather than a race.)
logdir="$root/game/insurgency/addons/sourcemod/logs"
marker="[layout] verify: map-wide"
count_loads() { cat "$logdir"/L*.log 2>/dev/null | grep -c -F "$marker" || true; }
before="$(count_loads)"

tmp="$(mktemp -d)"
fifo="$tmp/stdin"
mkfifo "$fifo"
# Held open read-write: a fifo whose only writer has exited reads as EOF, and
# srcds takes EOF on stdin as a reason to shut down.
exec 9<>"$fifo"

srv=""
cleanup() {
  if [[ -n "$srv" ]] && kill -0 "$srv" 2>/dev/null; then
    echo "quit" >&9 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$srv" 2>/dev/null || break; sleep 1; done
    kill -9 "$srv" 2>/dev/null || true
  fi
  exec 9>&- || true
  rm -rf "$tmp"
}
trap cleanup EXIT

# Under a pty, for check-layout.sh's reason: srcds writes its console with
# ordinary stdio, and a console going into a pipe is a 4 KB block buffer. It
# is also what lets the container keep its tty while stdin comes from a fifo.
script -qfe -c "MM_PORT=$port '$root/tools/srcds.sh' --service layout \
  --map '$map' --players '$players' --port '$port' --cfg mapmaker_layout.cfg" \
  /dev/null <"$fifo" &
srv=$!

(
  waited=0
  while [[ "$waited" -lt "$boot_timeout" ]]; do
    kill -0 "$srv" 2>/dev/null || exit 0
    if [[ "$(count_loads)" -gt "$before" ]]; then
      # A preset named on the command line is pinned here rather than from the
      # config: the config's own sm_layout runs before the plugin has a map to
      # apply it to, and the rotation picks anyway (plugin/README.md).
      [[ -n "$preset" ]] && echo "sm_layout \"$preset\"" >&9
      echo "changelevel $map" >&9
      echo "[layout-server] boot load is stock - reloading $map for the layout" >&2
      exit 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "[layout-server] no post-load line in ${boot_timeout}s - the map is still" \
       "stock; 'changelevel $map' at the console applies a layout" >&2
) &

# The operator's own console input, forwarded into the same pipe. This is the
# foreground of the script, so ^C reaches the server the way it always did.
cat >&9 || true
exec 9>&-
rc=0
wait "$srv" || rc=$?
srv=""
exit "$rc"
