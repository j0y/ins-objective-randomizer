#!/usr/bin/env bash
# Where Steam put things, asked of Steam rather than hardcoded.
#
# Sourced, not run: `. tools/steam-paths.sh`.
#
# Steam records every library root in steamapps/libraryfolders.vdf, and a
# machine usually has more than one - this one has the default under
# ~/.local/share/Steam and a second on another disk, with the Insurgency
# content in the second. So neither an absolute path nor a path relative to
# this repo can name it: the repo happening to sit inside a library is an
# accident of where it was cloned, and only one library can ever be `..`.

# Every Steam library root on this machine, one per line, existing ones only.
steam_libraries() {
  local vdf
  for vdf in "${STEAM_ROOT:-$HOME/.steam/steam}" "$HOME/.local/share/Steam" \
             "$HOME/.steam/root" "$HOME/Steam"; do
    [[ -f "$vdf/steamapps/libraryfolders.vdf" ]] || continue
    # "path"  "/media/Data/SteamLibrary" -> the second quoted field. The three
    # usual locations are symlinks to one file, so the first hit is enough.
    sed -nE 's/^[[:space:]]*"path"[[:space:]]+"(.*)"[[:space:]]*$/\1/p' \
      "$vdf/steamapps/libraryfolders.vdf"
    return 0
  done
  return 0
}

# Colon-separated workshop content roots for an app id, existing ones only.
steam_workshop_roots() {
  local app="$1" lib out=""
  while IFS= read -r lib; do
    [[ -d "$lib/steamapps/workshop/content/$app" ]] \
      && out="${out:+$out:}$lib/steamapps/workshop/content/$app"
  done < <(steam_libraries)
  printf '%s' "$out"
}

# The host's steamcmd, or empty. Not built or pulled: this is the one Steam
# download in the project that is not a depot.
#
# A manual steamcmd is unpacked wherever its owner felt like, so the search
# ends with each library root's own parent - a second-disk library tends to
# have it as a sibling, which is where this machine's lives.
find_steamcmd() {
  local c lib cands=("${MM_STEAMCMD:-}" "$(command -v steamcmd || true)"
                     "$HOME/steamcmd/steamcmd.sh" "$HOME/.steam/steamcmd/steamcmd.sh"
                     "/usr/games/steamcmd")
  while IFS= read -r lib; do
    cands+=("$(dirname "$lib")/steamcmd/steamcmd.sh" "$lib/steamcmd/steamcmd.sh")
  done < <(steam_libraries)
  for c in "${cands[@]}"; do
    [[ -n "$c" && -x "$c" ]] && { printf '%s' "$c"; return 0; }
  done
  return 0
}
