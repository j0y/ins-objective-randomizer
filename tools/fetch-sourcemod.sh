#!/usr/bin/env bash
# Metamod:Source + SourceMod -> game/insurgency/addons/.
#
# The layout route needs a modded server; that is the trade docs/layout-variants.md
# §2 makes in exchange for never patching a .bsp. Nothing here is baked into an
# image: both live in the bind-mounted game tree, so a fetch is a fetch and the
# srcds container stays a plain 32-bit runtime.
#
# Versions are pinned to the pairing the upstream smartbots server runs. Bump
# them together, and rebuild the spcomp image if SM moves - a .smx from a newer
# compiler will not load on an older server.
#
#   tools/fetch-sourcemod.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

MM_VERSION="${MM_VERSION:-1.11.0-git1148}"
SM_VERSION="${SM_VERSION:-1.11.0-git6968}"

game="$root/game/insurgency"
[[ -f "$game/gameinfo.txt" ]] || {
  echo "no game content at $game - run tools/fetch-game.sh first" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== Metamod:Source $MM_VERSION"
curl -sqL "https://mms.alliedmods.net/mmsdrop/1.11/mmsource-${MM_VERSION}-linux.tar.gz" \
  | tar xz -C "$tmp"

echo "== SourceMod $SM_VERSION"
curl -sqL "https://sm.alliedmods.net/smdrop/1.11/sourcemod-${SM_VERSION}-linux.tar.gz" \
  | tar xz -C "$tmp"

# Neither archive should clobber a plugin or a config that is already there.
cp -rn "$tmp/addons" "$game/" 2>/dev/null || true
cp -r  "$tmp/addons/metamod/bin" "$game/addons/metamod/" 2>/dev/null || true
cp -r  "$tmp/addons/sourcemod/bin" "$game/addons/sourcemod/" 2>/dev/null || true
cp -r  "$tmp/addons/sourcemod/extensions" "$game/addons/sourcemod/" 2>/dev/null || true

# Metamod needs a loader in addons/ for the engine to find it at all.
cat > "$game/addons/metamod.vdf" <<'VDF'
"Plugin"
{
	"file"	"addons/metamod/bin/server"
}
VDF

# Nothing in this project wants the stock SourceMod admin/fun plugins running
# during a survey; they add chat noise and map-vote hooks to a headless run.
disabled="$game/addons/sourcemod/plugins/disabled"
mkdir -p "$disabled"
for p in funcommands funvotes basefuncommands basevotes nextmap mapchooser \
         rockthevote randomcycle basecommands; do
  [[ -f "$game/addons/sourcemod/plugins/$p.smx" ]] \
    && mv "$game/addons/sourcemod/plugins/$p.smx" "$disabled/" || true
done

echo
echo "metamod:  $(ls "$game/addons/metamod/bin/" 2>/dev/null | tr '\n' ' ')"
echo "sourcemod: $(ls "$game/addons/sourcemod/bin/" 2>/dev/null | tr '\n' ' ')"
echo "next:  tools/build-plugins.sh"
