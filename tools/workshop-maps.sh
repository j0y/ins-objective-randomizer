#!/usr/bin/env bash
# Put subscribed workshop maps where the dedicated server can load them.
#
# The workshop tree lives outside the game tree, and srcds only looks in
# insurgency/maps. So the map has to be brought in - and it is more than the
# .bsp: a checkpoint map's objective chain, its stage->spawnzone mapping and its
# cache entities live in maps/<name>.txt, and navlayout reads that same file for
# the chain. Five of the subscribed maps ship no .nav of their own and name a
# borrowed one instead ("navfile" "tell"), which is why the base map has to
# already be installed - it is, they all borrow from shipped maps.
#
# Copies rather than symlinks: the game tree is a copy too, so it stays one
# self-contained thing that can be moved or thrown away without dangling.
#
# **Copying the file in is half of it: a client has to be told.** A workshop map
# is not announced by its presence in insurgency/maps - the server publishes the
# `WorkshopContent` string table, the client's CClientUGCManager syncs it and
# mounts its own subscribed copy of each file ID in it, and a loose .bsp says
# nothing. A subscriber joining a server that serves the map as a loose file is
# told "missing map" while the file sits in their own workshop tree. So every
# map installed here also has its file ID written to
# insurgency/subscribed_file_ids.txt, which is what `sv_workshop_enabled 1`
# reads (tools/layout-server.sh sets both). The ID is the directory the map was
# found in, so this is the only place that knows it.
#
# The list is what the server installs at boot, one item at a time, so it is
# worth keeping to the maps actually being served: name them and only those IDs
# are added. Installing is quick - seconds each, and it wrote nothing to the
# game tree here - but a bare run announces all 34 subscribed items. Items can
# carry children, and the child is announced by the parent rather than by this
# script: cs_officeb3_coop_v1_5 pulls in 2159576320, the CS:GO soundscapes its
# sounds come from, which is a second reason a loose .bsp is not enough.
#
# **There is more than one workshop root.** The Steam client owns one library
# and tools/fetch-workshop.sh downloads into another under cache/, because only
# one program can maintain a tree's appworkshop acf. WORKSHOP is therefore a
# colon-separated list, searched in order, and a map name found in two roots is
# taken from the first and named as a duplicate.
#
# **An ID list is a subscription list off a server**, and naming one does two
# things at once: only those items are installed, and *every* ID in it is
# announced - including the asset-only children (a soundscape pack, a `docks
# content`) that carry no .bsp and so would never be found by a search for
# maps. That is what the client needs to mount them.
#
# **A loose .bsp is the map and nothing else.** An item is a game directory -
# maps/, materials/, models/, sound/ - and copying the .bsp out of it leaves the
# rest behind, so the server has the geometry and none of the custom content the
# map names. gameinfo.txt mounts `insurgency/custom/*` at boot, each subfolder
# as a search path, which is exactly what a client does with a subscribed item.
# `--mount` puts each item there under its own file ID, hardlinked - the trees
# are on one filesystem, so 13 GB of content costs nothing twice and the game
# tree still holds real files rather than links out of it.
#
#   tools/workshop-maps.sh                     # every map in every root
#   tools/workshop-maps.sh tell_open_coop market_open_coop
#   tools/workshop-maps.sh --ids serverconfig/subscribed_file_ids.txt
#   tools/workshop-maps.sh --mount --ids serverconfig/subscribed_file_ids.txt
#   tools/workshop-maps.sh --list              # what is there, and what it needs
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
. "$root/tools/steam-paths.sh"
app=222880
steam_ws="${STEAM_WORKSHOP:-$(steam_workshop_roots "$app")}"
ws="${WORKSHOP:-$steam_ws:$root/cache/workshop/steamapps/workshop/content/$app}"
dest="$root/game/insurgency/maps"

roots=()
IFS=: read -r -a roots <<< "$ws"
present=()
for r in "${roots[@]}"; do [[ -d "$r" ]] && present+=("$r"); done
[[ ${#present[@]} -gt 0 ]] || { echo "no workshop content in any of: $ws" >&2; exit 1; }
roots=("${present[@]}")

[[ -d "$dest" ]] || { echo "no game tree at $dest - run tools/fetch-game.sh" >&2; exit 1; }

list=0
mount=0
ids_file=""
wanted=()
ids=()
subs="$root/game/insurgency/subscribed_file_ids.txt"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)  list=1; shift ;;
    --mount) mount=1; shift ;;
    --ids)   ids_file="$2"; shift 2 ;;
    *)      wanted+=("$1"); shift ;;
  esac
done

# The IDs a list asks for, if one was named. A `//` line is an item the server
# owner turned off and is not installed or announced.
declare -A want_id=()
listed_ids=()
if [[ -n "$ids_file" ]]; then
  [[ -f "$ids_file" ]] || { echo "no ID list at $ids_file" >&2; exit 1; }
  while IFS= read -r id; do want_id["$id"]=1; listed_ids+=("$id"); done < <(
    awk '{sub(/\r$/, "")} !/^[[:space:]]*\/\//{print $1}' "$ids_file" \
      | grep -E '^[0-9]+$' | sort -u)
  echo "${#listed_ids[@]} file ID(s) named by $ids_file"
fi

# The nav a map will actually use: its own file, or the one its .txt borrows.
navfile_of() {
  sed -n 's/.*"navfile"[[:space:]]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1
}

# ── mounting the content ────────────────────────────────────────────
# **One search path, not one per item.** gameinfo.txt's first entry is
# `GameBin |gameinfo_path|addons/metamod/bin`, and the engine resolves it
# against every game search path it has - so 144 items mounted as 144
# `custom/<id>` folders made 144 bogus dlopens of Metamod's server_srv.so and a
# boot that never finished: ministry_coop, which surveys in seconds, had not
# reached `OnConfigsExecuted` after 300 s (measured 2026-09-18, 496 dlopen
# failures against 64 on a bare tree). Merged into one folder it is one extra
# path, and the engine searches it the way it searches any other.
#
# A VPK is the exception and has to stay at the top: `custom/*` mounts a VPK
# *entry*, and one nested inside custom/workshop/ is not mounted at all. Around
# 15 of these items ship their assets that way, and a multi-part set keeps its
# own names - a `_dir.vpk` looks for `<base>_000.vpk` beside it.
if [[ $mount -eq 1 ]]; then
  custom="$root/game/insurgency/custom"
  merged="$custom/workshop"
  mkdir -p "$merged"
  # Hardlinked: the workshop roots and the game tree are one filesystem, so
  # 27 GB of content costs directory entries and nothing else. The fallback is
  # a real copy, for the day a root is mounted from somewhere else.
  link() {  # link <src> <destdir>
    cp -alu "$1" "$2" 2>/dev/null || cp -au "$1" "$2"
  }
  mounted=0 vpks=0
  for r in "${roots[@]}"; do
    for d in "$r"/*/; do
      id="$(basename "$d")"
      [[ "$id" =~ ^[0-9]+$ ]] || continue
      if [[ ${#want_id[@]} -gt 0 && -z "${want_id[$id]:-}" ]]; then continue; fi
      # An item holding nothing but a stray sound.cache is an unsubscribed
      # leftover rather than content.
      find "$d" -type f \( -name '*.bsp' -o -name '*.vmt' -o -name '*.vtf' \
           -o -name '*.mdl' -o -name '*.wav' -o -name '*.mp3' -o -name '*.pcf' \
           -o -name '*.vpk' \) -print -quit | grep -q . || continue
      for sub in "$d"*/; do
        [[ -d "$sub" ]] && link "$sub" "$merged/"
      done
      for v in "$d"*.vpk; do
        [[ -f "$v" ]] || continue
        link "$v" "$custom/"
        vpks=$((vpks + 1))
      done
      mounted=$((mounted + 1))
    done
  done
  echo "$mounted item(s) merged into $merged, $vpks VPK(s) beside it"
fi

n=0
declare -A seen=()
while IFS= read -r bsp; do
  name="$(basename "$bsp" .bsp)"
  src="$(dirname "$bsp")"
  # .../content/222880/<file id>/maps/<name>.bsp - the ID is the grandparent.
  id="$(basename "$(dirname "$src")")"

  # An ID list installs exactly what it names.
  if [[ ${#want_id[@]} -gt 0 && -z "${want_id[$id]:-}" ]]; then
    continue
  fi

  # Two items can ship a map of the same name - a subscription list can carry
  # an original and a fixed fork of it - and one maps/ can hold one of them. First root, first item wins,
  # and both are still announced, so a client mounts whichever its own workshop
  # gives priority.
  if [[ -n "${seen[$name]:-}" ]]; then
    echo "note: $name is in item $id too - keeping the copy from ${seen[$name]}" >&2
    continue
  fi
  seen["$name"]="$id"

  if [[ ${#wanted[@]} -gt 0 ]]; then
    keep=0
    for w in "${wanted[@]}"; do [[ "$w" == "$name" ]] && keep=1; done
    [[ $keep -eq 1 ]] || continue
  fi

  nav="$name"
  [[ -f "$src/$name.txt" ]] && borrowed="$(navfile_of "$src/$name.txt")" || borrowed=""
  [[ -n "$borrowed" ]] && nav="$borrowed"

  have_nav="missing"
  [[ -f "$src/$nav.nav" || -f "$dest/$nav.nav" ]] && have_nav="$nav.nav"

  if [[ $list -eq 1 ]]; then
    printf '%-32s nav %s\n' "$name" "$have_nav"
    continue
  fi

  for ext in bsp txt nav; do
    [[ -f "$src/$name.$ext" ]] && cp -u "$src/$name.$ext" "$dest/"
  done
  if [[ "$have_nav" == "missing" ]]; then
    echo "warning: $name wants $nav.nav and neither tree has it" >&2
  fi
  ids+=("$id")
  n=$((n + 1))
done < <(find "${roots[@]}" -name '*.bsp' | sort)

[[ $list -eq 1 ]] && exit 0

# The announcement. Merged with what is already there rather than rewritten, so
# installing one map does not un-announce the rest, and kept a plain list of
# IDs: the engine parses it token by token and says "Invalid file id" for
# anything else, comments included.
# An asset-only item carries no .bsp and so is not in `ids` - it has to come
# from the list, or a client mounts the map and none of its sounds.
[[ ${#listed_ids[@]} -gt 0 ]] && ids+=("${listed_ids[@]}")

if [[ ${#ids[@]} -gt 0 ]]; then
  { [[ -f "$subs" ]] && cat "$subs"; printf '%s\n' "${ids[@]}"; } \
    | grep -E '^[0-9]+$' | sort -u > "$subs.new"
  mv "$subs.new" "$subs"
  echo "$(wc -l < "$subs") file ID(s) announced in $subs"
fi

echo "$n workshop map(s) installed into $dest"
