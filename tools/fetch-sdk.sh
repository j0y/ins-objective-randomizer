#!/usr/bin/env bash
# vbsp / vvis / vrad -> game/bin/.
#
# Two candidate depots of app 222880 (Insurgency), both Windows, ~170 MB
# together against ~9 GB for the whole client:
#
#   222882  the client Windows binary depot   166 MB /  61 MB transferred
#   222890  the "Insurgency SDK" DLC          109 MB /  17 MB transferred
#
# Which of the two actually carries vbsp/vvis/vrad has not been established, so
# this pulls both and then checks. DepotDownloader will pull a Windows depot
# onto Linux, so no Windows machine and no Steam client are involved.
#
# A login is unavoidable here. Verified 2026-08-27: the anonymous account holds
# dedicated-server licenses only, and is refused by 222890, by 243750 (Source
# SDK Base 2013 Multiplayer) and by the client depot alike. The account needs
# to own Insurgency; the SDK DLC is free with it.
#
# -qr prints a QR code to scan with the Steam mobile app, so no password is
# typed at this terminal. Pass -remember-password to skip the login next time.
#
#   tools/fetch-sdk.sh <steam-username>
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MM_UID="$(id -u)" MM_GID="$(id -g)"

user="${1:?usage: fetch-sdk.sh <steam-username> [extra DepotDownloader args]}"
shift

mkdir -p "$root/game" "$root/cache/depotdl"
# -remember-password on the first depot so the second does not re-prompt.
for depot in 222882 222890; do
  echo "== depot $depot"
  docker compose -f "$root/docker/compose.yml" run --rm depotdl \
    -app 222880 -depot "$depot" -os windows \
    -username "$user" -qr -remember-password -dir /work/game "$@" \
    || echo "   depot $depot failed - continuing; the check below is what counts" >&2
done

echo
missing=0
for t in vbsp vvis vrad; do
  if [[ -f "$root/game/bin/$t.exe" ]]; then
    echo "  ok      game/bin/$t.exe"
  else
    echo "  MISSING game/bin/$t.exe"; missing=1
  fi
done
[[ $missing -eq 0 ]] || { echo "the depot layout is not what was assumed - look in game/bin/" >&2; exit 1; }
echo
echo "next:  tools/compile-map.sh maps/build/<something>.vmf"
