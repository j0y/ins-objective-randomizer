#!/usr/bin/env bash
# BSPSource -> vendor/bspsrc/.
#
# ata4's decompiler: a compiled .bsp back to an editable .vmf. Two things it is
# needed for here - reading the shipped maps as reference, and remixing them,
# for which see packages/mapmaker/src/mapmaker/splice/.
#
# It is fast enough not to be part of the loop's cost: 0.9 s for ministry, a
# 57 MB official map. The `-linux` release carries its own JRE, so no system
# Java is required; the jar-only release needs one.
#
# Free download, no account.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/vendor/bspsrc"
version="${BSPSRC_VERSION:-v1.4.8}"
url="https://github.com/ata4/bspsrc/releases/download/${version}/bspsrc-linux.zip"

mkdir -p "$dest"
curl -sSL -o "$dest/bspsrc.zip" "$url"
unzip -o -q "$dest/bspsrc.zip" -d "$dest"
chmod +x "$dest/bspsrc.sh" "$dest/bin/java"

"$dest/bspsrc.sh" --version || true
echo
echo "next:  tools/decompile-map.sh --splice-ready ministry"
