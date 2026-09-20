#!/usr/bin/env bash
# Download workshop items by file ID - anonymously, no Steam account.
#
# The maps this project serves come from a real server's subscription list
# (serverconfig/subscribed_file_ids.txt is one such file, straight off the server
# that plays them), which is 144 file IDs where a Steam *subscription* is a
# click each. `steamcmd +login anonymous +workshop_download_item 222880 <id>`
# needs no account for Insurgency's workshop - confirmed 2026-09-18 - so the
# list is the only input needed.
#
# **It downloads beside the Steam client's tree, not into it.** steamcmd keeps
# its own appworkshop_222880.acf next to whatever it downloads, and only one
# program can maintain that file; pointing it at the library the Steam client
# owns would leave the client's manifest describing items it did not fetch. So
# this gets its own root under cache/ and the tools read both - see WORKSHOP in
# tools/workshop-maps.sh.
#
# An item already installed in *any* workshop root is skipped, which is what
# makes this re-runnable and what keeps the 71 items the Steam client already
# has from being fetched twice.
#
#   tools/fetch-workshop.sh                       # every ID in serverconfig/
#   tools/fetch-workshop.sh --list                # what is missing, and nothing else
#   tools/fetch-workshop.sh --ids other-list.txt
#   tools/fetch-workshop.sh 2959755350 2888146733
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
. "$root/tools/steam-paths.sh"
app=222880

# Where a downloaded item ends up: steamcmd's +force_install_dir gives it
# <dir>/steamapps/workshop/content/<app>/<id> and keeps its state there, so the
# layout is steamcmd's rather than this script's - moving the item out from
# under it would make every later run re-download.
dest="${MM_WORKSHOP_DEST:-$root/cache/workshop}"
mine="$dest/steamapps/workshop/content/$app"

# Every root an item might already be in. The Steam client's library first,
# since a subscription list is mostly items already subscribed to.
steam_ws="${STEAM_WORKSHOP:-$(steam_workshop_roots "$app")}"
roots="${MM_WORKSHOP_ROOTS:-$steam_ws:$mine}"

ids_file="$root/serverconfig/subscribed_file_ids.txt"
list=0
force=0
want=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ids)   ids_file="$2"; shift 2 ;;
    --dest)  dest="$2"; mine="$dest/steamapps/workshop/content/$app"; shift 2 ;;
    --list)  list=1; shift ;;
    --force) force=1; shift ;;
    -*)      echo "unknown option $1" >&2; exit 2 ;;
    *)       want+=("$1"); shift ;;
  esac
done

# steamcmd: the host's if it has one. Nothing is built or pulled - this is the
# one Steam download in the project that is not a depot, and DepotDownloader
# (docker/depotdl.Dockerfile) cannot fetch UGC anonymously.
steamcmd="$(find_steamcmd)"

# An ID is "installed" if some root has a directory for it with real content in
# it. Not just a directory: an item the Steam client has unsubscribed from
# leaves one behind holding a stray sound/sound.cache, and a good part of a list
# look installed by that test and are not.
installed_in() {  # installed_in <id> -> prints the root that has it
  local id="$1" r
  local IFS=:
  for r in $roots; do
    [[ -d "$r/$id" ]] || continue
    # A map item is a .bsp; a content item is materials/models/sound/particles.
    if find "$r/$id" -type f \
         \( -name '*.bsp' -o -name '*.vpk' -o -name '*.vmt' -o -name '*.vtf' \
            -o -name '*.mdl' -o -name '*.wav' -o -name '*.mp3' -o -name '*.pcf' \
            -o -name '*.nav' -o -name '*.txt' \) -print -quit | grep -q .; then
      printf '%s\n' "$r"; return 0
    fi
  done
  return 1
}

if [[ ${#want[@]} -eq 0 ]]; then
  [[ -f "$ids_file" ]] || { echo "no ID list at $ids_file" >&2; exit 1; }
  # A commented-out line is a map the server owner turned off, and the trailing
  # `//name` is the only place an ID's name is written down. The file comes off
  # a Windows box - CRLF - and the \r rides on the last field of every line.
  while IFS= read -r id; do want+=("$id"); done < <(
    awk '{sub(/\r$/, "")} !/^[[:space:]]*\/\//{print $1}' "$ids_file" \
      | grep -E '^[0-9]+$' | sort -u)
fi

missing=()
for id in "${want[@]}"; do
  if [[ $force -eq 0 ]] && have="$(installed_in "$id")"; then
    [[ $list -eq 1 ]] && printf '%-12s have   %s\n' "$id" "$have"
    continue
  fi
  [[ $list -eq 1 ]] && printf '%-12s FETCH\n' "$id"
  missing+=("$id")
done

echo "${#want[@]} ID(s) asked for, ${#missing[@]} to fetch into $mine"
[[ $list -eq 1 ]] && exit 0
[[ ${#missing[@]} -gt 0 ]] || exit 0

[[ -n "$steamcmd" && -x "$steamcmd" ]] || {
  echo "no steamcmd - set MM_STEAMCMD=/path/to/steamcmd.sh" >&2; exit 1; }

mkdir -p "$dest"
ok=0; failed=()
i=0
for id in "${missing[@]}"; do
  i=$((i + 1))
  printf '[%d/%d] %s ... ' "$i" "${#missing[@]}" "$id"
  # One item per invocation. A batch of `+workshop_download_item` lines is one
  # startup instead of 73, but an item that hangs or 403s takes the rest of the
  # batch with it and says so about the wrong ID.
  log="$dest/fetch-$id.log"
  if "$steamcmd" +force_install_dir "$dest" +login anonymous \
       +workshop_download_item "$app" "$id" +quit >"$log" 2>&1 \
     && grep -q 'Success\. Downloaded item' "$log"; then
    sz="$(du -sh "$mine/$id" 2>/dev/null | cut -f1)"
    echo "ok ${sz:-?}"
    rm -f "$log"
    ok=$((ok + 1))
  else
    echo "FAILED (see $log)"
    failed+=("$id")
  fi
done

echo
echo "$ok item(s) downloaded into $mine"
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "${#failed[@]} failed: ${failed[*]}"
  echo "a failed ID is usually one whose author took it down - the server that"
  echo "published the list may still have its own copy"
  exit 1
fi
