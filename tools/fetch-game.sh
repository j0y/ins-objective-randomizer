#!/usr/bin/env bash
# Insurgency Dedicated Server content -> game/.
#
# Anonymous: no Steam account, no password, nothing to type. ~9.4 GB on disk,
# 4.9 GB transferred. This is the asset kit and the calibration corpus - 49
# official .bsp maps and 31 content VPKs.
#
# What it is *not* is a compile toolchain. The only .exe in this depot is
# bin/crash_handler.exe. It does ship bin/vvis_dll.dll and bin/vrad_dll.dll,
# which hold the real vvis/vrad implementations, but not the launcher .exe
# stubs that load them, and nothing of vbsp at all. See tools/fetch-sdk.sh.
#
#   tools/fetch-game.sh [-validate]
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MM_UID="$(id -u)" MM_GID="$(id -g)"

mkdir -p "$root/game" "$root/cache/depotdl"
docker compose -f "$root/docker/compose.yml" run --rm depotdl \
  -app 237410 -depot 237411 -dir /work/game "$@"

n=$(find "$root/game/insurgency/maps" -maxdepth 1 -name '*.bsp' 2>/dev/null | wc -l)
echo
echo "$n maps in game/insurgency/maps"
echo "next:  tools/collect-maps.sh"
