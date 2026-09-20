#!/usr/bin/env python3
"""A first draft of `authored/<map>.kv`: candidate sites, and the clusters at them.

    tools/author-sites.py buhriz_coop [how many sites]

Nothing here scores a placement against another - `docs/authoring.md` §1 is why
there is nothing to calibrate a scorer against. It gates on the envelope the
map's own shipped objectives sit in, spreads the survivors across the mesh by
farthest-point sampling on *path* distance, sizes every cluster off that map's
own shipped clusters, and prints what it measured so the choice stays a human
one. The output is a draft to read against the plan and edit, not an answer.

What it does decide, and how:

* a site must have as much floor around it as the thinnest shipped objective on
  the same map, and sit at least one corpus p5 leg (2,177 u) from every other;
* a `defend` cluster goes in the band that map's own defenders occupy, in the
  smallest box that fills to its smallest shipped cluster;
* a `stage` cluster goes *behind* the site relative to where the chain is going
  next, which is what the shipped maps do - buhriz's attackers respawn 148-179
  deg off the advance axis, so they cross the objective they just took;
* a `stage` sector is derived rather than placeholdered: it is the arc of onward
  path bearings - arrival tangents, not chords - that the cluster was placed for.

`defend` sectors are left "0 360". Those need the per-door arcs, and there is
nothing to derive them from until `navlayout plan` renders the storeys.
"""
import pathlib
import sys
import numpy as np
from navlayout.survey import Survey, find_cpsetup
from navlayout.graph import NavGraph
from navlayout.objectives import resolve, Snapper
from navlayout.places import segment

MAP = sys.argv[1] if len(sys.argv) > 1 else 'buhriz_coop'
WANT = int(sys.argv[2]) if len(sys.argv) > 2 else 14

# The corpus band for one leg of a chain: p5 2,177 u of path (docs/authoring.md).
# Two sites closer than that cannot be consecutive, so a library keeps them apart.
SPACING = 2177.0
# `_usable`'s fill spacing, so a capacity here is a capacity there.
MIN_SEPARATION = 96.0

sv = Survey.load(f'surveys/{MAP}.json')
g = NavGraph.build(sv)
snap = Snapper(sv)
setup = find_cpsetup(MAP, 'game')
pl = segment(sv, graph=g, stages=len(setup.chain) if setup else None)
objs = resolve(sv, setup.chain) if setup else []

C = g.centers
foot = np.array([a.area for a in sv.areas], dtype=float)
live = g.hull_ok & ~g.blocked
main = np.zeros(g.n, bool)
main[g.largest_component()] = True
live &= main

def floor_within(i, r, dz=128.0):
    d = np.linalg.norm(C[:, :2] - C[i, :2], axis=1)
    m = live & (d <= r) & (np.abs(C[:, 2] - C[i, 2]) <= dz)
    return float(foot[m].sum()), int(m.sum())

def places_within(i, r=512.0, dz=192.0):
    d = np.linalg.norm(C[:, :2] - C[i, :2], axis=1)
    m = (d <= r) & (np.abs(C[:, 2] - C[i, 2]) <= dz) & (pl.label >= 0)
    return sorted(set(pl.label[m].tolist()))

# ── the envelope the shipped objectives live in ──────────────────────
ship = [snap.snap(o.origin) for o in objs]
f384 = np.array([floor_within(i, 384)[0] for i in ship])
f768 = np.array([floor_within(i, 768)[0] for i in ship])
GATE384, GATE768 = f384.min(), f768.min()

pool = []
for i in np.flatnonzero(live):
    a, n = floor_within(i, 384)
    if a < GATE384 or n < 4:
        continue
    b, _ = floor_within(i, 768)
    if b < GATE768:
        continue
    pool.append(i)
pool = np.array(pool)

# ── spread them: farthest-point sampling on path distance ────────────
# Seeded at the map's own entry, so the first sites are the ones a round would
# reach late, and every later pick is the pool area furthest on foot from
# everything already chosen.
entry_pts = sv.points_in_zone(setup.zones[0], setup.attacking_team) if setup else []
entry = [p.area if p.area is not None else snap.snap(p.origin) for p in entry_pts]
entry = [int(e) for e in entry if e is not None]
seed = entry or [int(pool[0])]

# Sharpen first, then sample. Each pool area stands for the best spot within
# 384 u of it - the one with the most floor around it - so the sampler picks
# between spots rather than between neighbourhoods, and cannot pick a
# neighbourhood whose spot it has already taken.
rep = {}
for i in pool:
    near = pool[np.linalg.norm(C[pool][:, :2] - C[i, :2], axis=1) <= 384]
    rep[int(i)] = int(max(near, key=lambda j: floor_within(j, 384)[0]))
spots = np.array(sorted(set(rep.values())))

chosen: list[int] = []
mind = g.distances(seed)[spots]
# A spot the seed cannot walk to is `inf`, and `inf` wins `argmax` - so one
# one-way pocket ends the sampling on its first pass and writes an empty
# library. Take the unreachable out of contention rather than stopping at them;
# `verticality_coop` has stage pairs at infinite path distance, so this is a
# shape the corpus has, not a hypothetical.
unreachable = ~np.isfinite(mind)
mind[unreachable] = -1.0
while len(chosen) < WANT:
    k = int(np.argmax(mind))
    if mind[k] < SPACING:
        break
    i = int(spots[k])
    chosen.append(i)
    mind = np.minimum(mind, g.distances(i)[spots])
    mind[k] = -1.0

# ── one defender spawn area per site ─────────────────────────────────
# Sized and sited off this map's own defender clusters, measured per stage.
# Half-extents to try, smallest first: a box is as big as it has to be to hold
# a shipped-size cluster and no bigger. buhriz's own defender zones run from
# 2,874 x 4,914 u down to 2,928 x 2,724 u, so nothing here is out of scale.
HALVES = [np.array([320.0, 320.0, 192.0]),
          np.array([480.0, 480.0, 192.0]),
          np.array([640.0, 640.0, 256.0]),
          np.array([896.0, 896.0, 320.0])]
def capacity(box_lo, box_hi, sep=MIN_SEPARATION):
    m = live & np.all(C > box_lo, axis=1) & np.all(C < box_hi, axis=1)
    pts, kept = C[m], []
    order = np.argsort(-foot[m])
    for p in pts[order]:
        if all(np.linalg.norm(p - q) >= sep for q in kept):
            kept.append(p)
    return len(kept), int(m.sum())

def spawn_area(obj_i, others, taken=(), lo=900.0, hi=1600.0, target=31):
    """The smallest box in the band that holds a shipped-size cluster.

    The band is this map's own: its defender points sit a straight 983-1,570 u
    from the objective they hold, median over the seven stages. `target` is its
    smallest shipped cluster, 31 points.

    `taken` are the boxes already placed, and a candidate may not intersect one:
    binding is containment, so two overlapping clusters hand each stage the
    other's spawn points - the leak `reverse.py` counts and warns about.
    """
    d = np.linalg.norm(C[:, :2] - C[obj_i, :2], axis=1)
    band = np.flatnonzero(live & (d >= lo) & (d <= hi))
    fallback = None
    for half in HALVES:
        best = None
        for j in band:
            blo, bhi = C[j] - half, C[j] + half
            if np.all(C[obj_i] > blo) and np.all(C[obj_i] < bhi):
                continue
            if any(np.all(C[o] > blo) and np.all(C[o] < bhi) for o in others):
                continue
            if any(np.all(blo < t[1]) and np.all(t[0] < bhi) for t in taken):
                continue
            cap, held = capacity(blo, bhi)
            key = (cap, -abs(d[j] - 1200.0))
            if best is None or key > best[0]:
                best = (key, int(j), cap, held, float(d[j]), half)
        if best is None:
            continue
        fallback = best if fallback is None or best[2] > fallback[2] else fallback
        if best[2] >= target:
            return best
    return fallback

print(f"map {MAP}: {len(sv.areas)} areas, {len(pl)} places, "
      f"{int(live.sum())} live hull-valid areas")
print(f"gates from the shipped {len(ship)}: floor384 >= {GATE384:,.0f} u^2, "
      f"floor768 >= {GATE768:,.0f} u^2  ->  {len(pool)} areas pass, "
      f"{len(spots)} spots ({int(unreachable.sum())} unreachable from the entry, "
      f"dropped), {len(chosen)} sites at >= {SPACING:,.0f} u of path apart\n")

hdr = (f"{'#':>3} {'area':>6} {'x y z':>22} {'place':>6}{'exits':>6}{'aprch':>6}"
       f"{'floor384':>10}{'in':>4}  | {'spawn':>6}{'cap':>5}{'held':>5}{'dist':>6}"
       f"{'bear':>8}{'box':>6}  {'nearest shipped obj':>22}")
print(hdr)
rows, placed = [], []
for n, i in enumerate(chosen, 1):
    f, cnt = floor_within(i, 384)
    p = int(pl.label[i])
    sa = spawn_area(i, [c for c in chosen if c != i])
    if sa:
        placed.append((n, C[sa[1]] - sa[5], C[sa[1]] + sa[5]))
    near = min(((np.linalg.norm(C[i] - o.origin), o.name) for o in objs),
               default=(float('nan'), '-'))
    xyz = f"{C[i][0]:.0f} {C[i][1]:.0f} {C[i][2]:.0f}"
    if sa:
        _k, j, cap, held, dist, half = sa
        v = C[j] - C[i]
        bearing = (np.degrees(np.arctan2(v[1], v[0])) + 360) % 360
        srow = (f"{j:>6}{cap:>5}{held:>5}{dist:>6.0f}{bearing:>8.0f}"
                f"{int(half[0]) * 2:>6}")
    else:
        j = cap = held = dist = bearing = None
        half = None
        srow = f"{'-':>6}{'-':>5}{'-':>5}{'-':>6}{'-':>8}{'-':>6}"
    print(f"{n:>3} {i:>6} {xyz:>22} {p:>6}{pl.places[p].degree:>6}"
          f"{len(places_within(i)):>6}{f:>10.0f}{'y' if g.indoor[i] else 'n':>4}  | {srow}"
          f"  {near[1]:>14} {near[0]:>7.0f}")
    rows.append(dict(n=n, area=i, xyz=C[i], place=p, floor=f, indoor=bool(g.indoor[i]),
                     spawn=j, cap=cap, held=held, dist=dist, half=half,
                     bearing=None if j is None else float(bearing),
                     near=near))

# ── which spawn areas cannot share a chain ───────────────────────────
# Binding is containment, so two overlapping clusters hand each stage the
# other's spawn points. It only bites when both sites are in one chain, so it
# is reported rather than designed around - the alternative starves the sites
# picked last.
import itertools
clash = [(a, b) for (a, alo, ahi), (b, blo, bhi) in itertools.combinations(placed, 2)
         if np.all(alo < bhi) and np.all(blo < ahi)]
print("\nspawn areas that overlap - never both in one chain: "
      + (", ".join(f"{a}+{b}" for a, b in clash) if clash else "none"))

# ── the attacker stage arrangements ──────────────────────────────────
# A `stage` cluster is where the attackers who have just taken this site
# respawn, and its sector is the way they are going next - so a site needs one
# per direction the chain can leave it in, not one per site.
#
# The shipped relation, measured on this map's own seven stages: the attacker
# cluster for stage k sits a median 1,143-1,620 u from objective k-1 (p10 470,
# p90 3,024; stage 3's 4,520 is the one outlier) and at an azimuth of 148-179
# deg off the advance axis. That is not a detail - it means the attackers
# respawn *behind* the objective they just took, facing it, and have to cross
# it to reach the next one. Clusters hold 20-22 points.
BEHIND = 180.0          # where the box goes, relative to where they are going
# Tried in order, and the first rung that fills a shipped-size cluster wins:
# hold the shipped geometry where the ground allows it, give on distance before
# giving on direction. The shipped clusters sit 148-179 deg off the advance -
# never more than 32 deg from directly behind - at 470-4,637 u.
RUNGS = [(35.0, 900.0, 1800.0),
         (60.0, 900.0, 1800.0),
         (60.0, 600.0, 2600.0),
         (90.0, 600.0, 3200.0)]
STAGE_TARGET = 20       # this map's own attacker clusters hold 20-22
BIN_GAP = 45.0          # bearings further apart than this are different doors
SECTOR_PAD = 15.0       # an arc is padded past the bearings it was cut for
TANGENT = 384.0         # how far along the path the departure bearing is read

from scipy.sparse.csgraph import dijkstra

def bearing_of(v):
    return float((np.degrees(np.arctan2(v[1], v[0])) + 360) % 360)

def departures(i, targets):
    """Where a chain leaving this site actually goes, per successor.

    The *arrival tangent*, not the chord: `path/straight` runs to 25.3 across
    the corpus, so a chord can point at a wall. Read the direction of the
    shortest path once it is TANGENT u clear of the site.
    """
    d, pred = dijkstra(g.adj, directed=True, indices=i, return_predecessors=True)
    out = {}
    for t in targets:
        if t == i or not np.isfinite(d[t]):
            continue
        walk, node = [t], t
        while node != i:
            node = int(pred[node])
            walk.append(node)
        v = C[t] - C[i]
        for a in reversed(walk):
            if np.linalg.norm(C[a][:2] - C[i][:2]) >= TANGENT:
                v = C[a] - C[i]
                break
        out[t] = (bearing_of(v), float(d[t]))
    return out

def sectors(dep, cap=4):
    """Group the onward bearings into arcs, one per direction that exists."""
    items = sorted(dep.items(), key=lambda kv: kv[1][0])
    if not items:
        return []
    angs = [kv[1][0] for kv in items]
    gaps = [(angs[(k + 1) % len(angs)] - angs[k]) % 360 for k in range(len(angs))]
    start = (int(np.argmax(gaps)) + 1) % len(items)
    order = [items[(start + k) % len(items)] for k in range(len(items))]
    groups = [[order[0]]]
    for prev, cur in zip(order, order[1:]):
        if (cur[1][0] - prev[1][0]) % 360 > BIN_GAP:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    # Too many arcs is too many clusters to author; merge across the tightest
    # remaining gap until there are `cap` of them.
    while len(groups) > cap:
        gap = [(groups[k + 1][0][1][0] - groups[k][-1][1][0]) % 360
               for k in range(len(groups) - 1)]
        k = int(np.argmin(gap))
        groups[k] = groups[k] + groups[k + 1]
        del groups[k + 1]
    return groups

def stage_area(site_i, going, others, target=STAGE_TARGET):
    """A box behind the site, relative to the way the attackers are leaving."""
    want = (going + BEHIND) % 360
    d = np.linalg.norm(C[:, :2] - C[site_i, :2], axis=1)
    ang = np.degrees(np.arctan2(C[:, 1] - C[site_i, 1], C[:, 0] - C[site_i, 0]))
    off = np.abs((ang - want + 180) % 360 - 180)
    fallback = None
    for rung, (cone, lo, hi) in enumerate(RUNGS):
        band = np.flatnonzero(live & (d >= lo) & (d <= hi) & (off <= cone))
        for half in HALVES:
            best = None
            for j in band:
                blo, bhi = C[j] - half, C[j] + half
                if any(np.all(C[o] > blo) and np.all(C[o] < bhi) for o in others):
                    continue
                cap, held = capacity(blo, bhi)
                key = (cap, -off[j])
                if best is None or key > best[0]:
                    best = (key, int(j), cap, held, float(d[j]), half,
                            float(off[j]), rung)
            if best is None:
                continue
            if fallback is None or best[2] > fallback[2]:
                fallback = best
            if best[2] >= target:
                return best
    return fallback

print(f"\nattacker stage arrangements - a cluster per direction the chain can "
      f"leave a site in, placed\nbehind it as this map's own attackers stand. "
      f"rung 0 is {RUNGS[0][0]:.0f} deg off directly behind at "
      f"{RUNGS[0][1]:.0f}-{RUNGS[0][2]:.0f} u; each rung after it gives ground:")
print(f"{'site':>5}{'sector':>15}  {'serves':>26}{'leg med':>9}  | "
      f"{'anchor':>7}{'cap':>5}{'held':>5}{'dist':>6}{'bear':>6}{'off':>5}{'box':>6}{'rung':>5}")
stage_rows = {}
for r in rows:
    i = r['area']
    dep = departures(i, [c for c in chosen if c != i])
    dep = {t: v for t, v in dep.items() if v[1] >= SPACING}
    arrs = []
    for grp in sectors(dep):
        angs = [v[0] for _t, v in grp]
        legs = [v[1] for _t, v in grp]
        lo_a, hi_a = angs[0] - SECTOR_PAD, angs[-1] + SECTOR_PAD
        if (hi_a - lo_a) % 360 < 1e-6 and len(angs) > 1:
            lo_a, hi_a = 0.0, 360.0
        mid = lo_a + ((hi_a - lo_a) % 360) / 2 if hi_a != lo_a else angs[0]
        sa = stage_area(i, mid % 360, [c for c in chosen if c != i])
        serves = [f"s{next(x['n'] for x in rows if x['area'] == t):02d}"
                  for t, _v in grp]
        if sa:
            _k, j, cap, held, dist, half, off, rung = sa
            bear = bearing_of(C[j] - C[i])
            row = (f"{j:>7}{cap:>5}{held:>5}{dist:>6.0f}{bear:>6.0f}"
                   f"{off:>5.0f}{int(half[0])*2:>6}{rung:>5}")
        else:
            j = cap = held = dist = half = bear = off = rung = None
            row = (f"{'-':>7}{'-':>5}{'-':>5}{'-':>6}{'-':>6}{'-':>5}{'-':>6}"
                   f"{'-':>5}")
        served = " ".join(serves)
        served = served if len(served) <= 24 else served[:21] + f"..+{len(serves)}"
        print(f"s{r['n']:02d}  {f'{lo_a:.0f} {hi_a:.0f}':>13}  {served:>26}"
              f"{np.median(legs):>9.0f}  | {row}")
        arrs.append(dict(sector=(lo_a, hi_a), serves=serves, legs=legs,
                         anchor=j, cap=cap, held=held, dist=dist, half=half,
                         bearing=bear, off=off, rung=rung))
    stage_rows[r['n']] = arrs
    r['stage'] = arrs

# A stage cluster landing in the defend cluster of the site it is heading for
# puts the two teams on one piece of ground. Different teams, so no binding
# leak - `points_in_zone` is per team - but it is a round that starts in
# contact, and it is worth knowing before a chain uses that leg.
warn = []
for r in rows:
    for a in r['stage']:
        if a['anchor'] is None:
            continue
        alo, ahi = C[a['anchor']] - a['half'], C[a['anchor']] + a['half']
        for t in a['serves']:
            o = next(x for x in rows if f"s{x['n']:02d}" == t)
            if o['spawn'] is None:
                continue
            blo, bhi = C[o['spawn']] - o['half'], C[o['spawn']] + o['half']
            if np.all(alo < bhi) and np.all(blo < ahi):
                warn.append(f"s{r['n']:02d}->{t}")
rungs = [a['rung'] for r in rows for a in r['stage'] if a['anchor'] is not None]
print(f"\n{len(rungs)} stage arrangements: "
      + ", ".join(f"{rungs.count(k)} at rung {k}" for k in sorted(set(rungs)))
      + " - a rung above 0 is ground the shipped cone does not have here")
print("stage cluster overlapping the defenders it attacks: "
      + (", ".join(warn) if warn else "none"))

# pairwise path distances, so a chain can be picked off this table
print("\npath distance between sites (u, '.' = under the 2,177 u band):")
D = {i: g.distances(i) for i in chosen}
print("     " + "".join(f"{n:>7}" for n in range(1, len(chosen) + 1)))
for a, i in enumerate(chosen, 1):
    cells = []
    for b, j in enumerate(chosen, 1):
        if a == b:
            cells.append(f"{'-':>7}")
        else:
            d = D[i][j]
            cells.append(f"{d:>7.0f}" if np.isfinite(d) and d >= SPACING
                         else (f"{'.':>7}" if np.isfinite(d) else f"{'inf':>7}"))
    print(f"{a:>4} " + "".join(cells))

# ── the authored file, as a draft ────────────────────────────────────
# docs/authoring.md §3. Positions are area indices, never typed coordinates:
# an index resolves to that area's own centre, which the survey carries with
# the engine's hull verdict already attached. The resolved coordinate is
# written beside it as a comment so the file stays readable against a re-survey.
out = [f'// draft - tools/author-sites.py {MAP}, from surveys/{MAP}.json.',
       '// Read it against the plan and edit it by hand; nothing here is a decision',
       '// the map was asked about.',
       f'// {len(chosen)} candidate sites. Each carries one defend arrangement,',
       '// whose sector is still unauthored - "0 360" is a placeholder for a real',
       '// per-door arc - and one stage arrangement per direction the chain can',
       '// leave the site in, whose sector *is* derived: it is the arc of onward',
       '// path bearings that arrangement was placed for.',
       '//',
       '// Spawn areas that overlap, and so must never appear in one chain: '
       + (", ".join(f"s{a:02d}+s{b:02d}" for a, b in clash) if clash else "none"),
       '"authored"', '{', f'\t"map"     "{MAP}"', f'\t"survey"  "surveys/{MAP}.json"', '']
for r in rows:
    c = r['xyz']
    out += ['\t"site"', '\t{',
            f'\t\t"name"    "s{r["n"]:02d}-place{r["place"]}"',
            '\t\t"slot"    "any"',
            f'\t\t"at"      "area {r["area"]}"        '
            f'// {c[0]:.0f} {c[1]:.0f} {c[2]:.0f}',
            f'\t\t"note"    "place {r["place"]}, {pl.places[r["place"]].degree} exits, '
            f'{r["floor"]:,.0f} u^2 of hull-valid floor within 384 u, '
            f'{"indoor" if r["indoor"] else "outdoor"}; '
            f'nearest shipped objective {r["near"][1]} at {r["near"][0]:.0f} u"']
    if r['spawn'] is not None:
        j, half = r['spawn'], r['half']
        lo, hi = C[j] - half, C[j] + half
        out += ['', '\t\t"arrangement"', '\t\t{',
                '\t\t\t"role"    "defend"',
                '\t\t\t"sector"  "0 360"        // unauthored: any bearing',
                f'\t\t\t"anchor"  "area {j} 0"',
                f'\t\t\t"box"     "{lo[0]:.0f} {lo[1]:.0f} {lo[2]:.0f}  '
                f'{hi[0]:.0f} {hi[1]:.0f} {hi[2]:.0f}"',
                f'\t\t\t"note"    "{r["cap"]} hull-valid coordinates at 96 u '
                f'separation ({r["held"]} areas in the box), '
                f'{r["dist"]:.0f} u from the site at bearing {r["bearing"]:.0f} deg"',
                '\t\t}']
    for a in r.get('stage', []):
        if a['anchor'] is None:
            continue
        lo, hi = C[a['anchor']] - a['half'], C[a['anchor']] + a['half']
        out += ['', '\t\t"arrangement"', '\t\t{',
                '\t\t\t"role"    "stage"',
                f'\t\t\t"sector"  "{a["sector"][0]:.0f} {a["sector"][1]:.0f}"'
                f'        // onward to {" ".join(a["serves"])}',
                f'\t\t\t"anchor"  "area {a["anchor"]} 0"',
                f'\t\t\t"box"     "{lo[0]:.0f} {lo[1]:.0f} {lo[2]:.0f}  '
                f'{hi[0]:.0f} {hi[1]:.0f} {hi[2]:.0f}"',
                f'\t\t\t"note"    "{a["cap"]} hull-valid coordinates at 96 u '
                f'separation ({a["held"]} areas in the box), '
                f'{a["dist"]:.0f} u back at bearing {a["bearing"]:.0f} deg, '
                f'{a["off"]:.0f} deg off directly behind; '
                f'legs served {min(a["legs"]):.0f}-{max(a["legs"]):.0f} u"',
                '\t\t}']
    out += ['\t}', '']
out += ['}', '']
path = pathlib.Path('authored') / f'{MAP}.kv'
path.parent.mkdir(exist_ok=True)
path.write_text("\n".join(out))
print(f"\nwrote {path}")
