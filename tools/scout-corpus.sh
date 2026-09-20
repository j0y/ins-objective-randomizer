#!/usr/bin/env bash
# Run bspscout over every official map - PLAN.md step 1, the calibration corpus.
#
# Doing this the obvious way is what kills the machine. One map peaks at
# ~1.9 GB resident, and there are 24 cores inviting a
# `for m in *.bsp; do ... & done`: 49 of those is ~70 GB on a 60 GB box, so the
# OOM killer picks a victim and the desktop goes with it. (Worse in the attempt
# that produced cache/corpus-logs/buhriz.log, where BSP was still pinned in
# mise's [env] and all 49 jobs analysed ministry.)
#
# So: one process per map, job count sized from *free* RAM rather than core
# count, a hard memory ceiling per job with swap denied - a pathological map
# dies alone instead of dragging the machine into swap - and one log per map so
# a failure is readable afterwards. Sequential is only ~10 min for all 49.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
maps="${MAPS:-$root/maps/official}"
cache="${CACHE:-$root/cache}"
out="${OUT:-$root/out}"
logs="$cache/corpus-logs"
py="$root/.venv/bin/python"
[[ -x "$py" ]] || py=python

res=16
mem_mb=6144          # per-job ceiling: 3x the measured peak, still 10 jobs to a 60 GB box
jobs=""
force=0
base_only=0
dry=0

usage() {
  cat >&2 <<USAGE
usage: $(basename "$0") [-j N] [--base] [--force] [--res U] [--mem MB] [-n] [map ...]

  -j N      concurrent maps (default: from free RAM, capped at 6)
  --base    skip the _coop / _night variants (19 maps instead of 49)
  --force   re-scout maps that already have a cache and an objectives report
  --res U   grid resolution in units (default $res)
  --mem MB  per-job memory ceiling (default $mem_mb)
  -n        print the plan and exit
  map ...   map names or .bsp paths; default every map in $maps
USAGE
  exit 2
}

# ---- one map, in its own process. Re-entrant call from the pool below.
if [[ "${1:-}" == "__one" ]]; then
  bsp="$2"; res="$3"; cache="$4"; out="$5"; py="$6"
  name="$(basename "$bsp" .bsp)"
  "$py" -m bspscout build "$bsp" --res "$res" --out "$cache/$name.npz"
  "$py" -m bspscout render "$cache/$name.npz" --outdir "$out/$name"
  "$py" -m bspscout objectives "$cache/$name.npz" --bsp "$bsp" --outdir "$out/$name"
  "$py" -m bspscout sight "$cache/$name.npz" --bsp "$bsp" --outdir "$out/$name"
  exit
fi

targets=()
while (($#)); do
  case "$1" in
    -j) jobs="$2"; shift 2 ;;
    -j*) jobs="${1#-j}"; shift ;;
    --base) base_only=1; shift ;;
    --force) force=1; shift ;;
    --res) res="$2"; shift 2 ;;
    --mem) mem_mb="$2"; shift 2 ;;
    -n|--dry-run) dry=1; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option $1" >&2; usage ;;
    *) targets+=("$1"); shift ;;
  esac
done

if ((${#targets[@]} == 0)); then
  shopt -s nullglob
  targets=("$maps"/*.bsp)
  shopt -u nullglob
fi
((${#targets[@]})) || { echo "no maps found in $maps - run 'mise run maps' first" >&2; exit 1; }

# Resolve names to paths, drop variants if asked, drop what is already done.
todo=()
skipped=0
for t in "${targets[@]}"; do
  bsp="$t"
  [[ -e "$bsp" ]] || bsp="$maps/${t%.bsp}.bsp"
  if [[ ! -e "$bsp" ]]; then
    echo "no such map: $t" >&2
    exit 1
  fi
  name="$(basename "$bsp" .bsp)"
  if ((base_only)) && [[ "$name" == *_coop || "$name" == *_night ]]; then
    continue
  fi
  if ((!force)) && [[ -f "$cache/$name.npz" && "$cache/$name.npz" -nt "$bsp" \
        && -f "$out/$name/objectives.md" && -f "$out/$name/sight.json" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  todo+=("$bsp")
done

# Free RAM, not core count, is the binding constraint. "available" already
# accounts for reclaimable page cache, which is most of what reading 2.6 GB of
# .bsp leaves behind.
avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [[ -z "$jobs" ]]; then
  jobs=$(( (avail_mb - 4096) / 2600 ))       # 1.9 GB measured peak + slack, 4 GB for the desktop
  ((jobs > 6)) && jobs=6                     # past this it is disk and cache, not CPU
  ((jobs < 1)) && jobs=1
fi
nproc_n=$(nproc)
((jobs > nproc_n)) && jobs=$nproc_n

limit=()
if command -v systemd-run >/dev/null 2>&1 && [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
  # A cgroup ceiling with swap denied: a map that blows up is killed inside its
  # own scope. Swap is the part that actually freezes a desktop, so refuse it.
  limit=(systemd-run --user --scope --quiet --collect
         -p "MemoryMax=${mem_mb}M" -p MemorySwapMax=0 --)
fi

echo "corpus: ${#todo[@]} to scout, $skipped already done, -j $jobs"
echo "        ${avail_mb} MB available, ${mem_mb} MB ceiling per job$([[ ${#limit[@]} -eq 0 ]] && echo ' (unenforced: no systemd-run)')"
echo "        logs in $logs"
if ((dry)); then
  for bsp in "${todo[@]}"; do echo "  $(basename "$bsp" .bsp)"; done
  exit 0
fi
((${#todo[@]})) || exit 0

mkdir -p "$logs" "$cache" "$out"
status="$(mktemp)"
trap 'rm -f "$status"' EXIT

start() {
  local bsp="$1" name
  name="$(basename "$bsp" .bsp)"
  (
    t0=$SECONDS
    # One BLAS thread per job. Default-threaded numpy would put 24 threads in
    # each of N processes and spend the run in the scheduler.
    export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
           NUMEXPR_NUM_THREADS=1
    if "${limit[@]}" "$0" __one "$bsp" "$res" "$cache" "$out" "$py" \
         >"$logs/$name.log" 2>&1; then
      printf '%s\tok\t%ds\n' "$name" "$((SECONDS - t0))" >>"$status"
      echo "  ok    $name  ($((SECONDS - t0))s)"
    else
      rc=$?
      printf '%s\tFAIL(%d)\t%ds\n' "$name" "$rc" "$((SECONDS - t0))" >>"$status"
      echo "  FAIL  $name  (rc=$rc, $((SECONDS - t0))s)  see $logs/$name.log"
    fi
  ) &
}

for bsp in "${todo[@]}"; do
  while (( $(jobs -rp | wc -l) >= jobs )); do wait -n; done
  start "$bsp"
done
wait

ok=$(grep -c $'\tok\t' "$status" || true)
bad=$(grep -c $'\tFAIL' "$status" || true)
echo "done: $ok ok, $bad failed"
if ((bad)); then
  grep $'\tFAIL' "$status" | sed 's/^/  /' >&2
  exit 1
fi
