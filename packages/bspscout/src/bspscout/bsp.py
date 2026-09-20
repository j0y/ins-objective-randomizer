"""Parsed VBSP geometry: planes, tree, faces, displacements, entities."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from .lumps import (
    CONTENTS_LADDER,
    CONTENTS_MONSTERCLIP,
    CONTENTS_PLAYERCLIP,
    CONTENTS_SOLID,
    CONTENTS_WINDOW,
    SURF_NODRAW,
    SURF_SKIP,
    SURF_SKY,
    SURF_SKY2D,
    SURF_TRIGGER,
    BspFile,
)

DT_PLANE = np.dtype([("normal", "<f4", 3), ("dist", "<f4"), ("type", "<i4")])
DT_VERTEX = np.dtype([("p", "<f4", 3)])
DT_NODE = np.dtype([
    ("planenum", "<i4"), ("children", "<i4", 2), ("mins", "<i2", 3),
    ("maxs", "<i2", 3), ("firstface", "<u2"), ("numfaces", "<u2"),
    ("area", "<i2"), ("pad", "<i2"),
])
DT_LEAF = np.dtype([
    ("contents", "<i4"), ("cluster", "<i2"), ("area_flags", "<u2"),
    ("mins", "<i2", 3), ("maxs", "<i2", 3), ("firstleafface", "<u2"),
    ("numleaffaces", "<u2"), ("firstleafbrush", "<u2"), ("numleafbrushes", "<u2"),
    ("leafwater", "<i2"), ("pad", "<i2"),
])
DT_FACE = np.dtype([
    ("planenum", "<u2"), ("side", "u1"), ("onnode", "u1"), ("firstedge", "<i4"),
    ("numedges", "<i2"), ("texinfo", "<i2"), ("dispinfo", "<i2"),
    ("fogvolume", "<i2"), ("styles", "u1", 4), ("lightofs", "<i4"),
    ("area", "<f4"), ("lm_mins", "<i4", 2), ("lm_size", "<i4", 2),
    ("origface", "<i4"), ("numprims", "<u2"), ("firstprim", "<u2"),
    ("smoothing", "<u4"),
])
DT_EDGE = np.dtype([("v", "<u2", 2)])
DT_MODEL = np.dtype([
    ("mins", "<f4", 3), ("maxs", "<f4", 3), ("origin", "<f4", 3),
    ("headnode", "<i4"), ("firstface", "<i4"), ("numfaces", "<i4"),
])
DT_TEXINFO = np.dtype([
    ("texvecs", "<f4", (2, 4)), ("lmvecs", "<f4", (2, 4)),
    ("flags", "<i4"), ("texdata", "<i4"),
])
DT_TEXDATA = np.dtype([
    ("reflectivity", "<f4", 3), ("namestring", "<i4"), ("width", "<i4"),
    ("height", "<i4"), ("view_width", "<i4"), ("view_height", "<i4"),
])
DT_BRUSH = np.dtype([("firstside", "<i4"), ("numsides", "<i4"), ("contents", "<i4")])
DT_BRUSHSIDE = np.dtype([
    ("planenum", "<u2"), ("texinfo", "<i2"), ("dispinfo", "<i2"),
    ("bevel", "u1"), ("thin", "u1"),
])
DT_DISPVERT = np.dtype([("vec", "<f4", 3), ("dist", "<f4"), ("alpha", "<f4")])

_NONSOLID_SURF = SURF_SKY | SURF_SKY2D | SURF_NODRAW | SURF_SKIP | SURF_TRIGGER
_TRIGGER_CLASSES = re.compile(r"^(trigger_|func_(areaportal|occluder|instance|smokevolume|dustcloud|precipitation|clip))")


@dataclass
class Entity:
    props: dict[str, str]

    @property
    def classname(self) -> str:
        return self.props.get("classname", "")

    @property
    def origin(self) -> np.ndarray | None:
        raw = self.props.get("origin")
        if not raw:
            return None
        try:
            return np.array([float(v) for v in raw.split()[:3]], dtype=np.float64)
        except ValueError:
            return None

    @property
    def model_index(self) -> int | None:
        m = self.props.get("model", "")
        return int(m[1:]) if m.startswith("*") else None


class Bsp:
    def __init__(self, path: str | Path):
        self.file = BspFile(path)
        f = self.file
        self.planes = f.array(1, DT_PLANE)
        self.vertexes = f.array(3, DT_VERTEX)["p"].astype(np.float64)
        self.nodes = f.array(5, DT_NODE)
        self.leafs = f.array(10, DT_LEAF)
        self.faces = f.array(7, DT_FACE)
        self.edges = f.array(12, DT_EDGE)["v"].astype(np.int64)
        self.surfedges = f.array(13, np.dtype("<i4")).astype(np.int64)
        self.models = f.array(14, DT_MODEL)
        self.texinfo = f.array(6, DT_TEXINFO)
        self.texdata = f.array(2, DT_TEXDATA)
        self.brushes = f.array(18, DT_BRUSH)
        self.brushsides = f.array(19, DT_BRUSHSIDE)
        self.leafbrushes = f.array(17, np.dtype("<u2")).astype(np.int64)
        self.dispverts = f.array(33, DT_DISPVERT)
        self._dispinfo_raw = f.raw(26)

    # ---------- entities ----------
    @cached_property
    def entities(self) -> list[Entity]:
        text = self.file.raw(0).decode("latin-1")
        out: list[Entity] = []
        for block in re.finditer(r"\{(.*?)\n\}", text, re.S):
            props: dict[str, str] = {}
            for k, v in re.findall(r'"([^"]*)"\s+"([^"]*)"', block.group(1)):
                if k not in props:
                    props[k] = v
            if props:
                out.append(Entity(props))
        return out

    def by_class(self, pattern: str) -> list[Entity]:
        rx = re.compile(pattern)
        return [e for e in self.entities if rx.match(e.classname)]

    # ---------- textures ----------
    @cached_property
    def texnames(self) -> list[str]:
        table = self.file.array(44, np.dtype("<i4"))
        data = self.file.raw(43)
        names = []
        for off in table:
            end = data.index(b"\0", off)
            names.append(data[off:end].decode("latin-1"))
        return names

    @cached_property
    def face_texname(self) -> np.ndarray:
        ti = self.faces["texinfo"].astype(np.int64)
        td = np.where(ti >= 0, self.texinfo["texdata"][np.clip(ti, 0, None)], -1)
        nid = np.where(td >= 0, self.texdata["namestring"][np.clip(td, 0, None)], -1)
        names = np.array(self.texnames + ["<none>"], dtype=object)
        return names[np.where(nid >= 0, nid, len(self.texnames))]

    @cached_property
    def face_flags(self) -> np.ndarray:
        ti = self.faces["texinfo"].astype(np.int64)
        return np.where(ti >= 0, self.texinfo["flags"][np.clip(ti, 0, None)], 0)

    # ---------- displacements ----------
    @cached_property
    def dispinfo(self) -> dict[str, np.ndarray]:
        raw = self._dispinfo_raw
        n = len(raw) // 176
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(n, 176) if n else np.zeros((0, 176), np.uint8)
        def field(off, dt, count=1):
            sub = np.ascontiguousarray(buf[:, off:off + np.dtype(dt).itemsize * count])
            arr = sub.view(dt)
            return arr.reshape(n, count) if count > 1 else arr.reshape(n)
        return {
            "start_pos": field(0, "<f4", 3).astype(np.float64),
            "vert_start": field(12, "<i4"),
            "power": field(20, "<i4"),
            "contents": field(32, "<i4"),
            "map_face": field(36, "<u2"),
        }

    # ---------- face polygons ----------
    def face_vertices(self, fi: int) -> np.ndarray:
        fe = int(self.faces["firstedge"][fi])
        ne = int(self.faces["numedges"][fi])
        se = self.surfedges[fe:fe + ne]
        idx = np.where(se >= 0, self.edges[np.abs(se), 0], self.edges[np.abs(se), 1])
        return self.vertexes[idx]

    def face_normal(self, fi: int) -> np.ndarray:
        pl = self.planes[int(self.faces["planenum"][fi])]
        n = pl["normal"].astype(np.float64)
        return -n if self.faces["side"][fi] else n

    def model_face_range(self, mi: int) -> range:
        m = self.models[mi]
        return range(int(m["firstface"]), int(m["firstface"]) + int(m["numfaces"]))

    def solid_entity_models(self) -> list[tuple[int, np.ndarray, str]]:
        """(model_index, origin_offset, classname) for brush entities worth colliding with."""
        out = []
        for e in self.entities:
            mi = e.model_index
            if mi is None or mi == 0 or mi >= len(self.models):
                continue
            if _TRIGGER_CLASSES.match(e.classname):
                continue
            org = e.origin
            out.append((mi, org if org is not None else np.zeros(3), e.classname))
        return out

    # ---------- triangles ----------
    def triangles(self, model_indices=(0,), include_entities=True):
        """Yield (verts Nx3x3 float64, source-tag) triangle soup of drawable world surfaces.

        Displacement faces are tessellated; ordinary faces are fanned.
        """
        tris: list[np.ndarray] = []
        tags: list[np.ndarray] = []
        disp = self.dispinfo
        face_to_disp = {}
        for di in range(len(disp["power"])):
            face_to_disp[int(disp["map_face"][di])] = di

        jobs: list[tuple[range, np.ndarray, int]] = []
        for mi in model_indices:
            jobs.append((self.model_face_range(mi), np.zeros(3), 0))
        if include_entities:
            for mi, org, _cls in self.solid_entity_models():
                jobs.append((self.model_face_range(mi), np.asarray(org, float), 1))

        for frange, offset, tag in jobs:
            for fi in frange:
                flags = int(self.face_flags[fi])
                if flags & _NONSOLID_SURF:
                    continue
                di = int(self.faces["dispinfo"][fi])
                if di >= 0:
                    t = self._disp_triangles(fi, di)
                    if t is None:
                        continue
                else:
                    v = self.face_vertices(fi)
                    if len(v) < 3:
                        continue
                    t = np.stack([
                        np.repeat(v[:1], len(v) - 2, axis=0),
                        v[1:-1],
                        v[2:],
                    ], axis=1)
                if offset.any():
                    t = t + offset
                tris.append(t)
                tags.append(np.full(len(t), tag, np.int8))
        if not tris:
            return np.zeros((0, 3, 3)), np.zeros(0, np.int8)
        return np.concatenate(tris), np.concatenate(tags)

    def _disp_triangles(self, fi: int, di: int) -> np.ndarray | None:
        corners = self.face_vertices(fi)
        if len(corners) != 4:
            return None
        d = self.dispinfo
        power = int(d["power"][di])
        size = (1 << power) + 1
        start = d["start_pos"][di]
        # rotate so corner nearest start_pos is index 0
        k = int(np.argmin(((corners - start) ** 2).sum(1)))
        c = np.roll(corners, -k, axis=0)
        tv = np.linspace(0.0, 1.0, size)[:, None]
        left = c[0] + (c[1] - c[0]) * tv           # size x 3
        right = c[3] + (c[2] - c[3]) * tv
        tu = np.linspace(0.0, 1.0, size)[None, :, None]
        grid = left[:, None, :] + (right - left)[:, None, :] * tu   # size x size x 3
        vs = int(d["vert_start"][di])
        dv = self.dispverts[vs:vs + size * size]
        if len(dv) < size * size:
            return None
        grid = grid + (dv["vec"] * dv["dist"][:, None]).reshape(size, size, 3)
        a = grid[:-1, :-1]; b = grid[:-1, 1:]; cc = grid[1:, 1:]; dd = grid[1:, :-1]
        t1 = np.stack([a, b, cc], axis=2).reshape(-1, 3, 3)
        t2 = np.stack([a, cc, dd], axis=2).reshape(-1, 3, 3)
        return np.concatenate([t1, t2])

    # ---------- point contents (BSP tree walk, vectorised) ----------
    def point_contents(self, pts: np.ndarray, headnode: int = 0) -> np.ndarray:
        """Contents flags at each point, via iterative BSP descent over all points."""
        pts = np.ascontiguousarray(pts, dtype=np.float64)
        n = len(pts)
        node = np.full(n, headnode, np.int64)
        out = np.zeros(n, np.int32)
        pn = self.planes["normal"].astype(np.float64)
        pd = self.planes["dist"].astype(np.float64)
        nplane = self.nodes["planenum"].astype(np.int64)
        nchild = self.nodes["children"].astype(np.int64)
        lcont = self.leafs["contents"].astype(np.int32)
        active = np.arange(n)
        while active.size:
            cur = node[active]
            leaf = cur < 0
            if leaf.any():
                li = -1 - cur[leaf]
                out[active[leaf]] = lcont[li]
                active = active[~leaf]
                if not active.size:
                    break
                cur = node[active]
            pl = nplane[cur]
            d = (pts[active] * pn[pl]).sum(1) - pd[pl]
            node[active] = np.where(d >= 0, nchild[cur, 0], nchild[cur, 1])
        return out

    def is_solid(self, pts: np.ndarray, mask: int = CONTENTS_SOLID | CONTENTS_WINDOW | CONTENTS_MONSTERCLIP | CONTENTS_PLAYERCLIP) -> np.ndarray:
        return (self.point_contents(pts) & mask) != 0

    def is_ladder_content(self, pts: np.ndarray) -> np.ndarray:
        return (self.point_contents(pts) & CONTENTS_LADDER) != 0

    @property
    def world_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        m = self.models[0]
        return m["mins"].astype(np.float64), m["maxs"].astype(np.float64)

    # ---------- collision geometry (brush planes, not drawn faces) ----------
    @cached_property
    def _disp_plane_boxes(self):
        """(planenum -> list of xy bboxes) of faces replaced by displacements."""
        out: dict[int, list[np.ndarray]] = {}
        for di in range(len(self.dispinfo["power"])):
            fi = int(self.dispinfo["map_face"][di])
            v = self.face_vertices(fi)
            if len(v) < 3:
                continue
            pn = int(self.faces["planenum"][fi])
            box = np.array([v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max()])
            out.setdefault(pn, []).append(box)
        return out

    def brush_polygons(self, want_contents=CONTENTS_SOLID | CONTENTS_WINDOW | CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP,
                       min_nz: float = 0.0):
        """Clip every brush side against its brush to recover real collision polygons.

        Unlike the FACES lump this includes nodraw / tools-textured surfaces, so
        greybox and clip-brush floors show up.
        """
        pn = self.planes["normal"].astype(np.float64)
        pd = self.planes["dist"].astype(np.float64)
        bs_plane = self.brushsides["planenum"].astype(np.int64)
        bs_disp = self.brushsides["dispinfo"].astype(np.int64)
        bs_bevel = self.brushsides["bevel"].astype(np.int64)
        tris: list[np.ndarray] = []
        for bi in range(len(self.brushes)):
            cont = int(self.brushes["contents"][bi])
            if not (cont & want_contents):
                continue
            fs = int(self.brushes["firstside"][bi])
            ns = int(self.brushes["numsides"][bi])
            sides = np.arange(fs, fs + ns)
            sides = sides[bs_bevel[sides] == 0]
            if len(sides) < 4:
                continue
            pl = bs_plane[sides]
            N = pn[pl]
            D = pd[pl]
            for si in range(len(sides)):
                if N[si, 2] < min_nz:
                    continue
                poly = _plane_quad(N[si], D[si])
                for sj in range(len(sides)):
                    if sj == si:
                        continue
                    poly = _clip_poly(poly, N[sj], D[sj])
                    if len(poly) < 3:
                        break
                if len(poly) < 3:
                    continue
                p = np.asarray(poly)
                if self._disp_covered(int(pl[si]), p):
                    continue          # replaced by the displacement surface
                tris.append(np.stack([
                    np.repeat(p[:1], len(p) - 2, axis=0), p[1:-1], p[2:],
                ], axis=1))
        return np.concatenate(tris) if tris else np.zeros((0, 3, 3))

    def _disp_covered(self, planenum: int, poly: np.ndarray) -> bool:
        boxes = self._disp_plane_boxes.get(planenum)
        if not boxes:
            return False
        x0, y0 = poly[:, 0].min(), poly[:, 1].min()
        x1, y1 = poly[:, 0].max(), poly[:, 1].max()
        for b in boxes:
            if not (x1 < b[0] or x0 > b[2] or y1 < b[1] or y0 > b[3]):
                return True
        return False

    @cached_property
    def disp_brush(self) -> np.ndarray:
        """Brushes whose face was replaced by a displacement surface.

        Such a brush's volume is not its collision volume - the displaced mesh
        is, and the mesh can sit a long way off the original plane: measured
        over the shipped maps the p95 vertex offset is 70 u on a median map and
        1544 u on sinjar. Anything tracing brushes has to drop these and trace
        `displacement_triangles` instead, or terrain blocks in the wrong place.

        `brushsides["dispinfo"]` cannot answer this - vbsp writes it as zero
        everywhere - so the test is geometric, the same one `brush_polygons`
        uses to skip drawing these faces. Only brushes with a side on a plane
        some displacement sits on are candidates, which is a handful per map.
        """
        out = np.zeros(len(self.brushes), bool)
        boxes = self._disp_plane_boxes
        if not boxes:
            return out
        pn = self.planes["normal"].astype(np.float64)
        pd = self.planes["dist"].astype(np.float64)
        bs_plane = self.brushsides["planenum"].astype(np.int64)
        bs_bevel = self.brushsides["bevel"].astype(np.int64)
        for bi in range(len(self.brushes)):
            fs = int(self.brushes["firstside"][bi])
            ns = int(self.brushes["numsides"][bi])
            sides = np.arange(fs, fs + ns)
            sides = sides[bs_bevel[sides] == 0]
            pl = bs_plane[sides]
            cand = [i for i, x in enumerate(pl) if int(x) in boxes]
            if not cand:
                continue
            N, D = pn[pl], pd[pl]
            for si in cand:
                poly = _plane_quad(N[si], D[si])
                for sj in range(len(sides)):
                    if sj == si:
                        continue
                    poly = _clip_poly(poly, N[sj], D[sj])
                    if len(poly) < 3:
                        break
                if len(poly) >= 3 and self._disp_covered(int(pl[si]), np.asarray(poly)):
                    out[bi] = True
                    break
        return out

    def displacement_triangles(self) -> np.ndarray:
        out = []
        d = self.dispinfo
        for di in range(len(d["power"])):
            t = self._disp_triangles(int(d["map_face"][di]), di)
            if t is not None:
                out.append(t)
        return np.concatenate(out) if out else np.zeros((0, 3, 3))

    def collision_triangles(self, min_nz: float = 0.0) -> np.ndarray:
        """Everything a player can stand on: brush sides + displacement surfaces."""
        parts = [self.brush_polygons(min_nz=min_nz), self.displacement_triangles()]
        parts = [p for p in parts if len(p)]
        return np.concatenate(parts) if parts else np.zeros((0, 3, 3))


_BIG = 1 << 16


def _plane_quad(n: np.ndarray, d: float) -> list[np.ndarray]:
    """A huge quad lying on the plane n.p = d."""
    up = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(up, n); u /= np.linalg.norm(u)
    v = np.cross(n, u)
    o = n * d
    return [o - u * _BIG - v * _BIG, o + u * _BIG - v * _BIG,
            o + u * _BIG + v * _BIG, o - u * _BIG + v * _BIG]


def _clip_poly(poly, n, d, eps=1e-5):
    """Keep the half-space n.p <= d (brush interior side)."""
    if not poly:
        return poly
    out = []
    m = len(poly)
    dist = [float(np.dot(p, n) - d) for p in poly]
    for i in range(m):
        j = (i + 1) % m
        di, dj = dist[i], dist[j]
        if di <= eps:
            out.append(poly[i])
        if (di > eps) != (dj > eps):
            t = di / (di - dj)
            out.append(poly[i] + (poly[j] - poly[i]) * t)
    return out


def parse_static_props(bsp: "Bsp"):
    """GAME_LUMP 'sprp': model dictionary + prop placements.

    Static props are not in the BSP tree, so they never block the walk graph -
    but their model names are the only in-file clue to what a place *is*.
    """
    raw = bsp.file.raw(35)
    if not raw:
        return [], np.zeros((0, 3)), np.zeros(0, np.int64)
    count = struct.unpack_from("<i", raw, 0)[0]
    off = 4
    entry = None
    for _ in range(count):
        gid, flags, ver, fofs, flen = struct.unpack_from("<ihhii", raw, off)
        off += 16
        if gid == 0x73707270:      # 'sprp'
            entry = (ver, fofs, flen)
    if entry is None:
        return [], np.zeros((0, 3)), np.zeros(0, np.int64)
    ver, fofs, flen = entry
    base = bsp.file.lumps[35].offset
    p = fofs - base
    ndict = struct.unpack_from("<i", raw, p)[0]
    p += 4
    names = [raw[p + i * 128: p + i * 128 + 128].split(b"\0")[0].decode("latin-1")
             for i in range(ndict)]
    p += ndict * 128
    nleaf = struct.unpack_from("<i", raw, p)[0]
    p += 4 + nleaf * 2
    nprop = struct.unpack_from("<i", raw, p)[0]
    p += 4
    remain = (fofs - base + flen) - p
    if nprop <= 0 or remain <= 0:
        return names, np.zeros((0, 3)), np.zeros(0, np.int64)
    size = remain // nprop
    buf = np.frombuffer(raw[p: p + nprop * size], dtype=np.uint8).reshape(nprop, size)
    origin = np.ascontiguousarray(buf[:, 0:12]).view("<f4").reshape(nprop, 3).astype(np.float64)
    ptype = np.ascontiguousarray(buf[:, 24:26]).view("<u2").reshape(nprop).astype(np.int64)
    return names, origin, np.clip(ptype, 0, max(len(names) - 1, 0))
