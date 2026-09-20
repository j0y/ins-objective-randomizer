#!/usr/bin/env bash
# The Insurgency dedicated server.
#
# srcds_run would normally set LD_LIBRARY_PATH and then exec srcds_linux; it
# wants a Steam runtime that is not there, so this sets the path and execs
# directly. Everything after the options below is passed through to the engine.
#
# srcds_linux and every _srv.so beside it are 32-bit ELF, so the only real
# question is where the i386 libc comes from. Two answers:
#
#   docker   the default, and what docker/compose.yml is for. Any image with
#            32-bit libc/libstdc++ will do - MM_SRCDS_IMAGE names it, and
#            mapmaker/srcds (docker/srcds.Dockerfile) is one that carries
#            nothing else. The container's own HOME and /dev/shm are the part
#            that matters; see the Steam note below.
#   native   the host already has those libraries. Nothing to build and
#            nothing to pull, but it needs the Steam workaround.
#
# MM_SRCDS=docker|native forces one; unset picks docker when an image is
# there and falls back to native when the host can run the binary itself.
#
#   tools/srcds.sh --map ministry_coop -- +sv_lan 1
#   MM_SRCDS_IMAGE=someproject/insurgency tools/srcds.sh --map ministry_coop
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MM_UID="$(id -u)" MM_GID="$(id -g)"

service=srcds
map=ministry_coop
players=8
port=27015
cfg=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) service="$2"; shift 2 ;;
    --map)     map="$2"; shift 2 ;;
    --players) players="$2"; shift 2 ;;
    --port)    port="$2"; shift 2 ;;
    --cfg)     cfg="$2"; shift 2 ;;
    --)        shift; break ;;
    *)         break ;;
  esac
done

[[ -x "$root/game/srcds_linux" ]] || {
  echo "no srcds_linux at $root/game - run tools/fetch-game.sh" >&2; exit 1; }

image="${MM_SRCDS_IMAGE:-mapmaker/srcds}"
have_image() { docker image inspect "$image" >/dev/null 2>&1; }

# Can the host load a 32-bit ELF? ldd answers without running anything; the
# two _srv.so it cannot find come from LD_LIBRARY_PATH and are not the test.
host_can_run() {
  local out
  out="$(ldd "$root/game/srcds_linux" 2>/dev/null)" || return 1
  grep -q 'libc\.so\.6 => /' <<<"$out" && grep -q 'libstdc++\.so\.6 => /' <<<"$out"
}

mode="${MM_SRCDS:-}"
if [[ -z "$mode" ]]; then
  if have_image;     then mode=docker
  elif host_can_run; then mode=native
  else                    mode=docker   # let compose build, and say why
  fi
fi

if [[ "$mode" == native ]]; then
  host_can_run || {
    echo "MM_SRCDS=native but the host cannot resolve 32-bit libc/libstdc++" >&2
    echo "install libc6:i386 libstdc++6:i386, or use MM_SRCDS=docker" >&2
    exit 1; }
  cd "$root/game"
  export LD_LIBRARY_PATH="$root/game/bin:$root/game/insurgency/bin:${LD_LIBRARY_PATH:-}"
  # steam_appid.txt beside srcds_linux makes SteamAPI look for a *running Steam
  # client*, and if the host has one the server blocks in select() inside Steam
  # IPC and never reaches Host_NewGame. A container never hits this - it has
  # its own HOME and its own /dev/shm. Natively we can only move HOME, to the
  # same place docker/compose.yml gives it.
  export HOME="$root/cache/srcds"
  mkdir -p "$HOME"
  exec ./srcds_linux \
    -game insurgency -console -nomaster -insecure -norestart \
    -port "$port" -tickrate 64 \
    +maxplayers "$players" \
    ${cfg:+ +servercfgfile "$cfg"} \
    +map "$map" "$@"
fi

have_image || {
  echo "no image '$image' - compose will try to build it, which needs network." >&2
  echo "To avoid that: MM_SRCDS_IMAGE=<an image with 32-bit libc/libstdc++>," >&2
  echo "or MM_SRCDS=native to run on the host." >&2; }

export MM_PORT="$port" MM_SRCDS_IMAGE="$image"
exec docker compose -f "$root/docker/compose.yml" run --rm --service-ports "$service" -c "
  cd /work/game
  export LD_LIBRARY_PATH=/work/game/bin:/work/game/insurgency/bin:\$LD_LIBRARY_PATH
  exec ./srcds_linux \
    -game insurgency -console -nomaster -insecure -norestart \
    -port $port -tickrate 64 \
    +maxplayers $players \
    ${cfg:+ +servercfgfile $cfg} \
    +map $map $*
"
