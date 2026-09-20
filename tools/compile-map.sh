#!/usr/bin/env bash
# vbsp -> vvis -> vrad a .vmf, under Wine in a container. Nothing Wine-related
# and no Steam login is involved; see tools/fetch-tools.sh for where the tools
# come from and docs/toolchain.md for why they are the ++ ones.
#
# On a leak vbsp still writes a .bsp, but everything vvis and vrad then produce
# is meaningless, so this stops. Read the .lin pointfile vbsp drops next to the
# .vmf: a plain coordinate list from the leaked entity out to the void, which
# means leak diagnosis can be automated rather than eyeballed.
#
#   tools/compile-map.sh maps/build/test_room.vmf          # -fast, for iteration
#   tools/compile-map.sh maps/build/test_room.vmf -final
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MM_UID="$(id -u)" MM_GID="$(id -g)"

vmf="${1:?usage: compile-map.sh <path-to-vmf> [-fast|-final]}"
mode="${2:--fast}"
[[ "$mode" == "-final" ]] && mode=""

bindir="$root/vendor/plusplus/bin"
for t in vbspplusplus vvisplusplus vradplusplus; do
  [[ -f "$bindir/$t.exe" ]] || { echo "missing $bindir/$t.exe - run tools/fetch-tools.sh" >&2; exit 1; }
done
[[ -f "$root/game/insurgency/gameinfo.txt" ]] || {
  echo "no game content at $root/game - run tools/fetch-game.sh" >&2; exit 1; }

# The repo is bind-mounted at /work and Wine maps the container root at Z:,
# so only a repo-relative path has to be threaded through.
vmf_abs="$(cd "$(dirname "$vmf")" && pwd)/$(basename "$vmf")"
case "$vmf_abs" in
  "$root"/*) rel="${vmf_abs#"$root"/}" ;;
  *) echo "the vmf must live inside the repo so the container can see it: $root" >&2; exit 1 ;;
esac
[[ -f "$vmf_abs" ]] || { echo "no such vmf: $vmf_abs" >&2; exit 1; }

towin() { printf 'Z:\\work\\%s' "${1//\//\\}"; }
game_win="$(towin game/insurgency)"
vmf_win="$(towin "$rel")"
bsp="${vmf_abs%.vmf}.bsp"
lin="${vmf_abs%.vmf}.lin"
rm -f "$lin"

# --workdir matters: the tools resolve their compatibility DLLs from the
# working directory, not from the directory the .exe lives in.
wine_run() {
  local exe="$1"; shift
  docker compose -f "$root/docker/compose.yml" run --rm \
    --workdir /work/vendor/plusplus/bin wine "$(towin "vendor/plusplus/bin/$exe")" "$@"
}

# -insurgency selects the Insurgency BSP format. Without it vbsp++
# auto-detects and picks CS:GO, which is not what this game reads.
echo "== vbsp++"
wine_run vbspplusplus.exe -game "$game_win" -insurgency "$vmf_win"

if [[ -f "$lin" ]]; then
  echo >&2
  echo "LEAK: vbsp wrote $lin - the world is not sealed." >&2
  echo "vvis and vrad are skipped; their output would be meaningless." >&2
  exit 2
fi

echo "== vvis++ ${mode:--full}"
wine_run vvisplusplus.exe -game "$game_win" $mode "$vmf_win"
echo "== vrad++ ${mode:--full}"
wine_run vradplusplus.exe -game "$game_win" $mode "$vmf_win"

echo
echo "compiled $bsp"
echo "verify:  BSP=$bsp mise run scout"
