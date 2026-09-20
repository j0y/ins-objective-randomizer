#!/usr/bin/env bash
# VBSP++ / VVIS++ / VRAD++ -> vendor/plusplus/bin/.
#
# ficool2's rebuilt Source compile tools, and the reason this project needs no
# Steam login at all. They matter here for three reasons:
#
#   * they are monolithic - no vvis_dll/vrad_dll needed, unlike Valve's, whose
#     .exe stubs are exactly the files the dedicated server does not ship;
#   * they carry their own 64-bit compatibility DLLs (tier0, vstdlib,
#     filesystem_stdio, materialsystem, shaderapiempty, studiorender,
#     vphysics), so no game binaries are required either;
#   * vbsp++ knows Insurgency specifically - `-staticpropformat 10` is
#     documented in its own help as the Insurgency static prop version.
#
# Free download, no account. Everything runs under Wine in a container.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
url="https://github.com/ficool2/misc_tools/releases/download/v1/tools_plusplus.zip"
dest="$root/vendor/plusplus"

mkdir -p "$dest/bin"
docker run --rm -u "$(id -u):$(id -g)" -v "$dest:/dl" -w /dl \
  --entrypoint bash mapmaker/depotdl -c "
    set -e
    wget -q -O tools_plusplus.zip '$url'
    unzip -o -q tools_plusplus.zip -d extracted
  "

# The tools resolve their DLLs from the working directory, so exes and
# compatibility DLLs have to sit in one flat directory together.
cp "$dest"/extracted/tools_plusplus/tools/*.exe "$dest/bin/"
cp "$dest"/extracted/tools_plusplus/tools/*.fgd "$dest/bin/" 2>/dev/null || true
cp "$dest"/extracted/tools_plusplus/compatibility/*.dll "$dest/bin/"

echo
ls -1 "$dest/bin"/*.exe | while read -r f; do echo "  $(basename "$f")"; done
echo
echo "next:  tools/compile-map.sh maps/build/test_room.vmf"
