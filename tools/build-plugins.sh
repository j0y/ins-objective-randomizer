#!/usr/bin/env bash
# plugin/scripting/*.sp -> plugin/build/*.smx, then install into the game tree.
#
# Compiling and installing are one step on purpose: a .smx in plugin/build that
# is not the one the server loaded is the kind of thing that costs an hour.
#
#   tools/build-plugins.sh              # build both, install into game/
#   tools/build-plugins.sh --no-install # build only
#
# It also refreshes release/, the committed copy-to-a-server payload. The .smx
# is committed, so it can go stale against the .sp that produced it silently -
# release/BUILD.txt records the source hash and compiler of the binary sitting
# beside it, which is what makes a mismatch show up in a diff.
#
# Compiling in the container is what keeps that binary publishable: spcomp
# embeds its source path, and the bind mount makes that /work/plugin/scripting
# on every machine. A host spcomp would bake the builder's own directory into
# a file installed on other people's servers.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MM_UID="$(id -u)" MM_GID="$(id -g)"

install=1
[[ "${1:-}" == "--no-install" ]] && install=0

mkdir -p "$root/plugin/build"

for sp in "$root"/plugin/scripting/*.sp; do
  name="$(basename "$sp" .sp)"
  echo "== $name"
  docker compose -f "$root/docker/compose.yml" run --rm spcomp -c \
    "\$SM_SCRIPTING/spcomp \
       -i\$SM_SCRIPTING/include \
       -i/work/plugin/scripting \
       -o/work/plugin/build/$name.smx \
       /work/plugin/scripting/$name.sp"
done

if [[ $install -eq 1 ]]; then
  game="$root/game/insurgency"
  [[ -d "$game/addons/sourcemod" ]] || {
    echo "SourceMod is not installed - run tools/fetch-sourcemod.sh" >&2; exit 1; }

  # Plugins go into disabled/ and are loaded explicitly by the server cfg, so
  # the survey server never accidentally runs the layout plugin or vice versa.
  mkdir -p "$game/addons/sourcemod/plugins/disabled"
  cp "$root"/plugin/build/*.smx "$game/addons/sourcemod/plugins/disabled/"
  cp "$root"/plugin/gamedata/*.txt "$game/addons/sourcemod/gamedata/"
  mkdir -p "$game/surveys" "$game/presets"
  echo
  echo "installed into $game/addons/sourcemod/plugins/disabled/"
fi

# release/ mirrors an insurgency/ directory, so publishing is a copy. The
# layout plugin auto-loads from plugins/; the survey plugin sits in disabled/
# because it changelevels the server and is only wanted for a survey run.
rel="$root/release"
mkdir -p "$rel/addons/sourcemod/plugins/disabled" \
         "$rel/addons/sourcemod/gamedata" "$rel/presets"

# spcomp does not build the same bytes twice. Recompiling mapmaker_survey.sp
# without touching it rewrote 12,514 of its 18,747 bytes, because the .smx is
# compressed and whatever it stamps in cascades through the whole stream. So a
# committed binary cannot be diffed to see whether anything changed, and copying
# it on every build would push ~59 KB of noise into the history each time for
# nothing. The inputs hash below is the test instead: the payload is rewritten
# when a source changed and left alone when none did.
inputs=("$root"/plugin/scripting/*.sp "$root"/plugin/scripting/include/*.inc
        "$root"/plugin/gamedata/*.txt)
stamp="$(sha256sum "${inputs[@]}" | sha256sum | cut -c1-16)"

if [[ -f "$rel/BUILD.txt" ]] && grep -q "^inputs  *$stamp\$" "$rel/BUILD.txt" \
   && [[ -f "$rel/addons/sourcemod/plugins/mapmaker_layout.smx" ]]; then
  echo "release/ payload unchanged - no source differs from what built it"
else
  cp "$root/plugin/build/mapmaker_layout.smx" "$rel/addons/sourcemod/plugins/"
  cp "$root/plugin/build/mapmaker_survey.smx" "$rel/addons/sourcemod/plugins/disabled/"
  cp "$root"/plugin/gamedata/*.txt "$rel/addons/sourcemod/gamedata/"

  {
    echo "# What is in release/addons, and what built it."
    echo "# Regenerate with tools/build-plugins.sh; commit the result."
    echo "#"
    echo "# The .smx files cannot be diffed - spcomp does not produce the same"
    echo "# bytes twice. These hashes are what says whether they are current."
    echo
    echo "inputs  $stamp"
    echo "spcomp  $(docker compose -f "$root/docker/compose.yml" run --rm spcomp \
                      -c '$SM_SCRIPTING/spcomp 2>&1 | grep -m1 Compiler' | tr -d '\r')"
    echo
    for f in "${inputs[@]}"; do
      printf '%-46s %s\n' "${f#$root/}" "$(sha256sum "$f" | cut -c1-16)"
    done
  } > "$rel/BUILD.txt"
  echo "release/ payload refreshed"
fi

echo "release/presets: $(ls "$rel/presets" | wc -l) file(s)"
