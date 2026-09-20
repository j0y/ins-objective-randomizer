#!/usr/bin/env bash
# Decompile a shipped map to an editable .vmf with BSPSource (Java, no Wine).
#
# Two uses now. Reference: it shows how the official maps use the kit - room
# proportions, prop conventions, entity setup, func_detail choices. And reuse:
# the output recompiles, so a shipped map's geometry can be cut up and
# rearranged. See packages/mapmaker/src/mapmaker/splice/.
#
# `--splice-ready` drops the two things that stop a decompile recompiling:
#
#   areaportals   vbsp fails with "areaportal brush doesn't touch two areas"
#                 and writes a pointfile. 55 of them in ministry; they are a
#                 rendering optimisation, so dropping them costs nothing but
#                 some framerate.
#   visclusters   func_viscluster is deprecated and vvis++ says so.
#
#   tools/decompile-map.sh ministry
#   tools/decompile-map.sh --splice-ready ministry     # -> maps/ref/ministry_np.vmf
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

flags=()
suffix=""
if [[ "${1:-}" == "--splice-ready" ]]; then
  flags=(--no_areaportals --no_visclusters)
  suffix="_np"
  shift
fi

arg="${1:?usage: decompile-map.sh [--splice-ready] <map-name|path-to-bsp>}"
bsp="$arg"
[[ -f "$bsp" ]] || bsp="$root/maps/official/${arg%.bsp}.bsp"
[[ -f "$bsp" ]] || { echo "no such bsp: $arg" >&2; exit 1; }

# The linux release ships its own JRE and a launcher; the jar-only release does
# not. Take whichever is unpacked in vendor/bspsrc.
launcher="$root/vendor/bspsrc/bspsrc.sh"
jar="$root/vendor/bspsrc/bspsrc.jar"
mkdir -p "$root/maps/ref"
out="$root/maps/ref/$(basename "${bsp%.bsp}")${suffix}.vmf"

if [[ -x "$launcher" ]]; then
  "$launcher" "${flags[@]}" -o "$out" "$bsp"
elif [[ -f "$jar" ]]; then
  java -jar "$jar" "${flags[@]}" -o "$out" "$bsp"
else
  echo "BSPSource not found in $root/vendor/bspsrc" >&2
  echo "get bspsrc-linux.zip from https://github.com/ata4/bspsrc/releases and unzip it there" >&2
  exit 1
fi
echo "wrote $out"
