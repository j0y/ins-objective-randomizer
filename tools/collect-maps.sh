#!/usr/bin/env bash
# Copy the shipped .bsp files out of the Steam install into maps/official/.
# Those are the calibration corpus: bspscout reads compiled maps directly, so
# the official maps give empirical target distributions with no decompile step.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
ins="${INS:-$root/game}"

if [[ ! -d "$ins" ]]; then
  echo "no Steam install at $ins" >&2
  echo "symlink it:  ln -s ~/.steam/steam/steamapps/common/insurgency2 $root/game" >&2
  exit 1
fi

src="$ins/insurgency/maps"
if [[ ! -d "$src" ]]; then
  echo "no maps dir at $src - is this an Insurgency install?" >&2
  exit 1
fi

mkdir -p "$root/maps/official"
n=0
for f in "$src"/*.bsp; do
  [[ -e "$f" ]] || continue
  cp -n "$f" "$root/maps/official/"
  n=$((n + 1))
done
echo "$n maps in $root/maps/official"
