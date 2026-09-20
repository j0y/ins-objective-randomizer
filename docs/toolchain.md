# Toolchain

How to get the game content, the compile tools and the decompiler onto this
machine, and what each is for.

**Still current:** the game content fetch (`mise run game`), which supplies the
asset kit, the 49 official maps that are the calibration corpus, and
`srcds_linux` itself. Everything the project does now depends on it.

**Parked with the geometry tracks:** the Wine compile tools and BSPSource. They
work and they are documented here; nothing on the current path calls them,
because the current path never recompiles a map. See PLAN.md.

For the server side — Metamod:Source, SourceMod and the two plugin containers —
see `plugin/README.md`.

## What lives where

| thing | ships with | runs on Linux? |
|---|---|---|
| `.bsp` maps, models, materials (VPKs) | client and dedicated server | yes — bspscout reads them directly |
| `vbsp`, `vvis`, `vrad` | client + SDK, in `insurgency/bin/` | Wine |
| Hammer (`hammer.exe`) | client + SDK | Wine, but not needed if VMFs are generated |
| BSPSource (decompiler) | third party, Java | yes, natively |

**Verified 2026-08-27.** App IDs confirmed against `app_info_print`: **237410**
is "Insurgency Dedicated Server" (`freetodownload 1`, so anonymous), **222880**
is "Insurgency".

The dedicated server does **not** ship the compile tools. The only `.exe` in
depot 237411 is `bin/crash_handler.exe`. It does ship `bin/vvis_dll.dll` and
`bin/vrad_dll.dll` - which in Source 2013 hold the actual vvis/vrad
implementations, the `.exe`s being thin launchers - but not those launchers,
and nothing of vbsp at all (vbsp is monolithic, there is no `vbsp_dll`).

Valve's tools therefore need a Steam login, and there is no way around that:
the anonymous account holds dedicated-server licenses only and is refused by
app 243750 (Source SDK Base 2013 Multiplayer) and app 222890 (Insurgency SDK)
alike. Had we gone that route it would have been depot 222882 or 222890 of app
222880, ~170 MB together against ~9 GB for the whole client.

## The tools actually used: VBSP++ / VVIS++ / VRAD++

None of that turned out to be necessary. ficool2's rebuilt compile tools are a
free download with no account, and they sidestep every problem above:

* **monolithic** - no `vvis_dll`/`vrad_dll`, which are precisely the pieces the
  dedicated server does not ship;
* they carry **their own 64-bit compatibility DLLs** (`tier0`, `vstdlib`,
  `filesystem_stdio`, `materialsystem`, `shaderapiempty`, `studiorender`,
  `vphysics`), so no game binaries are needed either;
* vbsp++ **knows Insurgency specifically**: `-insurgency` selects the
  Insurgency BSP format and `-staticpropformat 10` is documented in its own
  help as the Insurgency static prop version. Insurgency is not on the
  project's advertised game list, but the tool plainly targets it.

`tools/fetch-tools.sh` fetches them; `tools/compile-map.sh` drives them.

Two quirks worth knowing:

* They resolve their compatibility DLLs from the **working directory**, not
  from the directory the `.exe` lives in. Hence the flat `vendor/plusplus/bin`
  and the `--workdir` in `compile-map.sh`.
* vbsp++ prints "Auto-detected that this game requires -csgo BSP format" even
  when `-insurgency` is passed. The message appears to precede flag handling.
  On a map with no static props the two produce an identical lump table, and
  the compile is not byte-deterministic anyway, so this is unresolved but
  currently harmless. It should be revisited once props are placed.

## Compile order

`vbsp` (geometry, entities, leak check) → `vvis` (visibility) → `vrad`
(lighting). Each takes `-game <path-to-insurgency>` and the `.vmf` path.
`-fast` on vvis/vrad for iteration; final passes are much slower.

## Leaks

The failure mode that will dominate early. A leak is any gap between the sealed
world and the void — including a one-unit misalignment between two brushes,
which is easy to generate accidentally.

- `vbsp` reports it and writes a `.lin` pointfile next to the `.vmf`.
- The pointfile is a plain list of coordinates tracing from the leaked entity
  out to the void. It is machine-readable, so leak diagnosis can be automated:
  parse the `.lin`, find where it crosses the intended shell, report the brush.
- `vvis` output after a leak is meaningless. Do not measure a leaked compile.

Entities like `light` and `info_player_start` must be inside the sealed volume;
a leak is always reported relative to one of them.

## Containers

Everything except the Python packages runs in a container - see
`docker/compose.yml`. The repo is bind-mounted at `/work`, so container paths
mirror repo paths, and each service runs as the invoking user so downloads and
compiled maps are not owned by root.

| service | image | for |
|---|---|---|
| `depotdl` | `docker/depotdl.Dockerfile` | Steam depot fetch (`tools/fetch-*.sh`) |
| `wine` | `docker/wine.Dockerfile` | vbsp/vvis/vrad (`tools/compile-map.sh`) |
| `bspsrc` | `eclipse-temurin:21-jre` | BSPSource (`tools/decompile-map.sh`) |

The Python side stays on the host on purpose: mise already owns that venv.

## Wine notes

Wine 8.0 from Debian bookworm, and the image installs i386 multiarch - the
compile tools are 32-bit, which is the usual reason a Source toolchain fails to
start on a 64-bit host. `WINEDEBUG=-all` keeps fixme spam out of vbsp's output.
The prefix lives in `cache/wine/prefix` on the host, so it survives a rebuild
and can be deleted to start clean.

Path translation: Wine maps the container root at `Z:`, and the repo is at
`/work`, so a repo-relative path `maps/build/x.vmf` becomes
`Z:\work\maps\build\x.vmf`. `tools/compile-map.sh` does the conversion.

**Verified:** `maps/build/test_room.vmf` compiles clean through all three
stages - sealed, no `.lin`, `VBSP v21` (the same version ministry reports), and
bspscout reads the result. A trivial map compiles in ~0 seconds; real timings
go here once there is a real map.

**Still unverified:** the compiled `.bsp` has never been loaded by the actual
game. That needs a running Insurgency, which is also what step 8 needs for
`nav_generate`.
