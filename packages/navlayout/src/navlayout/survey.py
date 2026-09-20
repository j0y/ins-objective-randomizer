"""Read `surveys/<map>.json` — the engine's own nav graph, as `plugin/mapmaker_survey` dumped it.

This is the substrate the whole analysis stands on, and it exists because the
shipped `.nav` cannot be read well enough offline. `docs/spawns-and-objectives.md`
§4 is the long version; the short version is that the offline parser truncates
the connection graph on ministry_coop and leaves no path from the security spawn
to objectives 2-6, which is exactly the part every openness metric is an
computation over.

Nothing here scores anything. It loads the JSON, snaps the entity table onto the
graph, and answers the two questions everything else asks first: which area is
this thing in, and what does the shipped map say the objective chain is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# The direction a connection was stored under, N/E/S/W, as CNavArea keeps it.
DIRECTIONS = ("north", "east", "south", "west")

# CINSNavArea::m_insFlags, bit 0x80. The survey exports the whole word as
# "flags" as well, so a bit that turns out to matter later needs no re-survey.
INS_FLAG_INDOOR = 0x80


@dataclass(slots=True)
class Area:
    """One `CNavArea`, with the engine's own numbers rather than the file's."""

    i: int
    center: np.ndarray          # (3,)
    nw: np.ndarray | None       # (3,) — None when the corner probe failed
    se: np.ndarray | None
    flags: int
    indoor: bool
    blocked: bool
    hull_ok: bool               # player hull fits at the centre
    hull_probes: int            # of centre + 4 interior offsets, how many fit
    conn: list[tuple[int, int, float]]   # (direction, target index, edge length)

    @property
    def width(self) -> float:
        return 0.0 if self.nw is None else float(self.se[0] - self.nw[0])

    @property
    def depth(self) -> float:
        return 0.0 if self.nw is None else float(self.se[1] - self.nw[1])

    @property
    def area(self) -> float:
        return self.width * self.depth

    @property
    def breadth(self) -> float:
        """The narrow dimension. A corridor is narrow whichever way it runs."""
        return 0.0 if self.nw is None else min(self.width, self.depth)


@dataclass(slots=True)
class Entity:
    """One objective, spawn zone, spawn point or restricted-area volume."""

    cls: str
    target: str
    team: int                   # 2 = security, 3 = insurgent, 0 = neither
    origin: np.ndarray          # (3,)
    area: int | None            # index into Survey.areas, already snapped in game
    model: str = ""
    mins: np.ndarray | None = None
    maxs: np.ndarray | None = None


@dataclass
class Survey:
    """One `surveys/<map>.json`."""

    map: str
    schema: int
    enumeration: str            # "TheNavAreas" or "flood from N seeds"
    corner_offsets: bool
    hull_mins: np.ndarray
    hull_maxs: np.ndarray
    areas: list[Area]
    entities: list[Entity]
    visibility: list[list[int]] | None = None
    path: Path | None = None
    # `trigger_capture_zone` -> the control point it captures, read off the
    # map's own `.bsp` by `objectives.capture_links`. The survey cannot carry
    # it: the exporter writes what the engine's entity list gives it, and this
    # is a keyvalue in the lump. Empty where the .bsp was not there to read.
    capture_links: dict[str, str] | None = None

    # ── loading ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "Survey":
        path = Path(path)
        with path.open() as fh:
            raw = json.load(fh)

        # The plugin writes "end": true last. A truncated file - the server was
        # killed mid-dump - otherwise parses as a perfectly good short map.
        if not raw.get("end"):
            raise ValueError(
                f"{path} has no end marker: the survey did not finish writing"
            )

        areas = [
            Area(
                i=a["i"],
                center=np.asarray(a["center"], dtype=np.float64),
                nw=np.asarray(a["nw"], dtype=np.float64) if "nw" in a else None,
                se=np.asarray(a["se"], dtype=np.float64) if "se" in a else None,
                flags=a["flags"],
                indoor=a["indoor"],
                blocked=a["blocked"],
                hull_ok=a["hull_ok"],
                hull_probes=a["hull_probes"],
                conn=[(c[0], c[1], c[2]) for c in a["conn"]],
            )
            for a in raw["areas"]
        ]

        entities = [
            Entity(
                cls=e["class"],
                target=e.get("target", ""),
                team=e.get("team", 0),
                origin=np.asarray(e["origin"], dtype=np.float64),
                area=e.get("area"),
                model=e.get("model", ""),
                mins=np.asarray(e["mins"], dtype=np.float64) if "mins" in e else None,
                maxs=np.asarray(e["maxs"], dtype=np.float64) if "maxs" in e else None,
            )
            for e in raw["entities"]
        ]

        hull = raw["hull"]
        return cls(
            map=raw["map"],
            schema=raw["schema"],
            enumeration=raw["enumeration"],
            corner_offsets=raw["corner_offsets"],
            hull_mins=np.asarray(hull["mins"], dtype=np.float64),
            hull_maxs=np.asarray(hull["maxs"], dtype=np.float64),
            areas=areas,
            entities=entities,
            visibility=raw.get("visibility"),
            path=path,
        )

    # ── shape ────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.areas)

    @property
    def whole_mesh(self) -> bool:
        """Whether the survey saw every area or only what it could reach.

        A flood is not worthless - the reachable component is what the metrics
        score - but it cannot report what it never visited, so anything phrased
        as a fraction *of the map* has to say which it is measuring.
        """
        return self.enumeration == "TheNavAreas"

    @property
    def edge_count(self) -> int:
        return sum(len(a.conn) for a in self.areas)

    def centers(self) -> np.ndarray:
        return np.array([a.center for a in self.areas], dtype=np.float64)

    # ── the entity table ─────────────────────────────────────────────

    def by_class(self, *classes: str) -> list[Entity]:
        want = set(classes)
        return [e for e in self.entities if e.cls in want]

    def spawn_zones(self, team: int | None = None) -> list[Entity]:
        """`ins_spawnzone` volumes. Several can share one targetname."""
        zones = self.by_class("ins_spawnzone")
        return zones if team is None else [z for z in zones if z.team == team]

    def spawn_zone(self, name: str, team: int) -> list[Entity]:
        return [z for z in self.spawn_zones(team) if z.target == name]

    def control_points(self) -> dict[str, Entity]:
        """`point_controlpoint` by targetname - the objective markers."""
        return {e.target: e for e in self.by_class("point_controlpoint")}

    def caches(self) -> dict[str, Entity]:
        return {e.target: e for e in self.by_class("obj_weapon_cache")}

    def spawn_points(self, team: int | None = None) -> list[Entity]:
        pts = self.by_class("ins_spawnpoint")
        return pts if team is None else [p for p in pts if p.team == team]

    # ── zones and the points inside them ─────────────────────────────

    @staticmethod
    def world_box(ent: Entity) -> tuple[np.ndarray, np.ndarray] | None:
        """A brush entity's volume in world space.

        vbsp stores a brush entity's geometry *relative to its origin key* and
        the engine re-applies it at load - which is the whole mechanism behind
        moving one at all (`docs/spawns-and-objectives.md` §2). So the exported
        model bounds are relative too, and world space is origin + bounds.
        """
        if ent.mins is None or ent.maxs is None:
            return None
        return ent.origin + ent.mins, ent.origin + ent.maxs

    def points_in_zone(self, name: str, team: int) -> list[Entity]:
        """The `ins_spawnpoint` entities a spawn zone actually owns.

        There is no name reference between a point and its zone: binding is by
        **containment**, and all 442 of ministry_coop's points fall inside a
        same-team zone volume. That is why moving a zone means moving the points
        inside it, and why a spawn zone's *origin* - the centre of a brush that
        may be a 1882x2050 slab - is a poor stand-in for where players appear.
        """
        boxes = [
            box
            for z in self.spawn_zone(name, team)
            if (box := self.world_box(z)) is not None
        ]
        if not boxes:
            return []
        out = []
        for p in self.spawn_points(team):
            for lo, hi in boxes:
                if np.all(p.origin >= lo) and np.all(p.origin <= hi):
                    out.append(p)
                    break
        return out

    def area_of(self, ent: Entity) -> int | None:
        """The area an entity stands in.

        Already snapped in game by `CNavMesh::GetNearestNavArea`, which is the
        engine's own answer and better than anything re-derived from centres
        offline. `None` means the engine found nothing within 512 u, which for
        an objective is itself a finding.
        """
        return ent.area


# ── the shipped cpsetup ──────────────────────────────────────────────
#
# A checkpoint map's behaviour is split in two, and this is the half that never
# reaches the entity lump: the objective **chain order**, which spawn zone each
# stage uses, which team attacks, and any borrowed nav file. See
# `docs/spawns-and-objectives.md` §1.

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(\S+)')


class KVNode:
    """One KeyValues block, keeping duplicate keys and their order.

    A cpsetup file has seven `controlpoint` keys in one block and the order is
    the objective chain, so a dict would destroy the only thing being read.
    """

    __slots__ = ("pairs",)

    def __init__(self) -> None:
        self.pairs: list[tuple[str, "str | KVNode"]] = []

    def all(self, key: str) -> list["str | KVNode"]:
        low = key.lower()
        return [v for k, v in self.pairs if k.lower() == low]

    def get(self, key: str, default=None):
        vals = self.all(key)
        return vals[0] if vals else default

    def block(self, key: str) -> "KVNode | None":
        for v in self.all(key):
            if isinstance(v, KVNode):
                return v
        return None


def parse_kv(text: str) -> KVNode:
    """A minimal KeyValues reader: quoted tokens, nested blocks, // comments.

    Written rather than regexed because the shipped files contain
    `//"controlpoint" "cp9"` - a commented-out objective - and a regex over
    quoted pairs reads it as a real one, silently adding a ninth stage to
    revolt_coop that the game does not play.
    """
    # Strip line comments outside strings.
    lines = []
    for raw in text.splitlines():
        out, in_str, i = [], False, 0
        while i < len(raw):
            c = raw[i]
            if c == '"':
                in_str = not in_str
            elif not in_str and c == "/" and raw[i + 1 : i + 2] == "/":
                break
            out.append(c)
            i += 1
        lines.append("".join(out))

    tokens: list[str] = []
    for m in _TOKEN.finditer("\n".join(lines)):
        tokens.append(m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3)))

    root = KVNode()
    stack = [root]
    pending: str | None = None
    for tok in tokens:
        if tok == "{":
            node = KVNode()
            stack[-1].pairs.append((pending if pending is not None else "", node))
            stack.append(node)
            pending = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            pending = None
        elif pending is None:
            pending = tok
        else:
            stack[-1].pairs.append((pending, tok))
            pending = None
    return root


@dataclass
class CpSetup:
    """`maps/<name>.txt` — what the map says about its own checkpoint layout."""

    chain: list[str]                 # objective order, as control-point names
    zones: list[str]                 # stage index -> ins_spawnzone targetname
    attacking_team: int              # 2 security, 3 insurgent
    navfile: str | None = None       # a borrowed .nav, for maps that ship none

    def zone_for(self, order: int) -> str | None:
        """The spawn zone stage `order` (1-based) uses, either team.

        Not `f"spawnzone_{order}"`: only some maps name them that way. district
        uses `sz_1`, tell uses `sz_a`, revolt uses `spawnzone1` with no
        underscore, and guessing gets every distance on those maps wrong by
        silently finding no spawn at all.
        """
        i = order - 1
        return self.zones[i] if 0 <= i < len(self.zones) else None


def _root_block(root: KVNode) -> KVNode:
    """The file's outer block, whatever the mapper called it.

    KeyValues takes a root block's name from the file, not from the loader, so
    `"map.txt" { "checkpoint" {...} }` reaches the gamemode exactly as
    `"cpsetup.txt" { ... }` does: the name is a comment. Insisting on the
    shipped spelling read **six of the 125 surveyed maps as having no objective
    chain at all** - haditha_coop and iron_express name the block after the map,
    the three karkand forks call it `"map.txt"`, szepezd_redux_coop
    `"controlpointsetup.txt"` - and all six carry a full checkpoint block and
    all six play in game. They were being turned away at the door with "no
    cpsetup", which is a statement about this reader and not about the map.

    A block holding a `checkpoint` is preferred over merely the first one, so a
    file that opens with something else cannot shadow the one being looked for.
    """
    blocks = [v for _k, v in root.pairs if isinstance(v, KVNode)]
    for b in blocks:
        if b.block("checkpoint") is not None:
            return b
    return blocks[0] if blocks else root


def read_cpsetup(txt: str | Path) -> CpSetup:
    root = parse_kv(Path(txt).read_text(errors="replace"))
    top = _root_block(root)
    mode = top.block("checkpoint") or top

    chain = [v for v in mode.all("controlpoint") if isinstance(v, str)]

    zones: list[str] = []
    zb = mode.block("spawnzones")
    if zb is not None:
        numbered = [
            (int(k), v) for k, v in zb.pairs
            if isinstance(v, str) and k.lstrip("-").isdigit()
        ]
        zones = [v for _i, v in sorted(numbered)]

    team = str(mode.get("AttackingTeam", "security")).strip().lower()
    navfile = top.get("navfile")
    return CpSetup(
        chain=chain,
        zones=zones,
        attacking_team=3 if team.startswith("ins") else 2,
        navfile=navfile if isinstance(navfile, str) else None,
    )


def find_cpsetup(map_name: str, game_dir: str | Path) -> CpSetup | None:
    """`read_cpsetup` for a map in a game tree, or `None` if it has no loose copy.

    The `.txt` normally lives inside `insurgency_misc_dir.vpk`; a loose copy in
    `insurgency/maps/` overrides it, and the dedicated server already has all 49
    extracted there. Workshop maps ship theirs beside the `.bsp`.
    """
    txt = Path(game_dir) / "insurgency" / "maps" / f"{map_name}.txt"
    return read_cpsetup(txt) if txt.exists() else None


def read_chain(txt: str | Path) -> list[str]:
    """Just the objective order."""
    return read_cpsetup(txt).chain


def find_chain(map_name: str, game_dir: str | Path) -> list[str]:
    setup = find_cpsetup(map_name, game_dir)
    return setup.chain if setup else []


# ── cross-checking the footprints ────────────────────────────────────

def compare_to_nav(survey: "Survey", nav_path: str | Path) -> dict:
    """Hold the surveyed footprints against the shipped `.nav`'s own.

    `CNavArea::m_nwCorner` / `m_seCorner` are found at runtime by probing the
    identity `center.xy == midpoint(nw.xy, se.xy)` over sampled areas, and that
    identity is satisfiable by more than one pair of offsets in principle. This
    is the independent check: the `.nav` file stores corners directly, and its
    *footprints* are trustworthy even though its connection graph is not
    (`docs/spawns-and-objectives.md` §4 - the truncation loses edges, not areas).

    Areas are matched by centre position rather than by index, because nothing
    says `TheNavAreas` is in file order.
    """
    from bspscout.navmesh import parse_nav

    mesh = parse_nav(nav_path)
    file_areas = list(mesh.areas.values())
    if not file_areas:
        return {"matched": 0, "compared": 0, "worst": None}

    file_centers = np.array([
        [(a.nw.x + a.se.x) / 2, (a.nw.y + a.se.y) / 2, (a.nw.z + a.se.z) / 2]
        for a in file_areas
    ])
    file_nw = np.array([[a.nw.x, a.nw.y] for a in file_areas])
    file_se = np.array([[a.se.x, a.se.y] for a in file_areas])

    matched = compared = 0
    worst = 0.0
    worst_at = None
    for a in survey.areas:
        if a.nw is None:
            continue
        d = np.linalg.norm(file_centers - a.center, axis=1)
        j = int(d.argmin())
        if d[j] > 1.0:
            continue                       # no counterpart in the file
        matched += 1
        err = float(max(
            np.abs(file_nw[j] - a.nw[:2]).max(),
            np.abs(file_se[j] - a.se[:2]).max(),
        ))
        compared += 1
        if err > worst:
            worst, worst_at = err, a.i

    return {
        "nav_areas": len(file_areas),
        "survey_areas": len(survey.areas),
        "matched": matched,
        "compared": compared,
        "worst": worst,
        "worst_at": worst_at,
    }


# ── the chain length, when there is no cpsetup to read it from ───────

# A stage's spawn zone is numbered, and a checkpoint map ships one per
# objective. `spawnzone_1`, `sz_1`, `spawnzone1`, `coop_sz_00` - the prefix
# varies per map and the separator with it, so the number is taken as whatever
# trails the name.
_NUMBERED_ZONE = re.compile(r"^(?P<stem>.*?)[ _-]?(?P<n>\d+)$")


def infer_stage_count(survey: "Survey") -> int | None:
    """How many objectives the chain has, read off the numbered spawn zones.

    A fallback for a survey whose `maps/<name>.txt` is not on this machine - it
    ships beside the `.bsp` in the workshop tree, and the survey does not carry
    it. Everything in `places.py` except the room test works without it, so this
    exists to make the room test available rather than to be believed on its
    own.

    **It has nothing to fall back for at the moment.** It was written for 31
    workshop maps with no loose `.txt`; `tools/fetch-workshop.sh` has since
    brought them all in, and as of 2026-09-19 all 125 surveyed maps have one and
    all 125 parse (`docs/permute.md` §10 - six of them only after the reader
    stopped insisting on the root block's name).

    **Measured against the shipped corpus**: of the 11 `_coop` maps that number
    their zones it is exact on 10, and one too many on revolt_coop, which ships
    a ninth zone its eight-objective chain never uses. market_coop and tell_coop
    name theirs by letter (`sz_a`) and get `None` rather than a guess.

    A checkpoint stage owns a zone for **both** teams - the attackers move up
    into it and the defenders fall back into it - and that is what separates the
    chain from the other game modes sharing the map. badlands_b1 carries sixteen
    `op_ins_*` for outpost, insurgents only, beside the nine `spawn*` that exist
    for both teams; taking the largest family says 16 and taking the largest
    two-team family says 9, which is the chain.
    """
    families: dict[int, dict[str, set[int]]] = {2: {}, 3: {}}
    for e in survey.by_class("ins_spawnzone"):
        if e.team not in families or not e.target:
            continue
        m = _NUMBERED_ZONE.match(e.target)
        if m:
            families[e.team].setdefault(m.group("stem"), set()).add(int(m.group("n")))

    shared = set(families[2]) & set(families[3])
    if shared:
        return max(len(families[2][s] | families[3][s]) for s in shared)
    loose = [len(v) for fam in families.values() for v in fam.values()]
    return max(loose) if loose else None
