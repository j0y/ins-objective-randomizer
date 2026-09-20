"""Raw VBSP lump access."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LUMP_NAMES = {
    0: "ENTITIES", 1: "PLANES", 2: "TEXDATA", 3: "VERTEXES", 4: "VISIBILITY",
    5: "NODES", 6: "TEXINFO", 7: "FACES", 8: "LIGHTING", 9: "OCCLUSION",
    10: "LEAFS", 11: "FACEIDS", 12: "EDGES", 13: "SURFEDGES", 14: "MODELS",
    15: "WORLDLIGHTS", 16: "LEAFFACES", 17: "LEAFBRUSHES", 18: "BRUSHES",
    19: "BRUSHSIDES", 20: "AREAS", 21: "AREAPORTALS", 22: "PROPCOLLISION",
    23: "PROPHULLS", 24: "PROPHULLVERTS", 25: "PROPTRIS", 26: "DISPINFO",
    27: "ORIGINALFACES", 28: "PHYSDISP", 29: "PHYSCOLLIDE", 30: "VERTNORMALS",
    31: "VERTNORMALINDICES", 32: "DISP_LIGHTMAP_ALPHAS", 33: "DISP_VERTS",
    34: "DISP_LIGHTMAP_SAMPLE_POSITIONS", 35: "GAME_LUMP", 36: "LEAFWATERDATA",
    37: "PRIMITIVES", 38: "PRIMVERTS", 39: "PRIMINDICES", 40: "PAKFILE",
    41: "CLIPPORTALVERTS", 42: "CUBEMAPS", 43: "TEXDATA_STRING_DATA",
    44: "TEXDATA_STRING_TABLE", 45: "OVERLAYS", 46: "LEAFMINDISTTOWATER",
    47: "FACE_MACRO_TEXTURE_INFO", 48: "DISP_TRIS", 49: "PHYSCOLLIDESURFACE",
    50: "WATEROVERLAYS", 51: "LEAF_AMBIENT_INDEX_HDR", 52: "LEAF_AMBIENT_INDEX",
    53: "LIGHTING_HDR", 54: "WORLDLIGHTS_HDR", 55: "LEAF_AMBIENT_LIGHTING_HDR",
    56: "LEAF_AMBIENT_LIGHTING", 57: "XZIPPAKFILE", 58: "FACES_HDR",
    59: "MAP_FLAGS", 60: "OVERLAY_FADES", 61: "OVERLAY_SYSTEM_LEVELS",
    62: "PHYSLEVEL", 63: "DISP_MULTIBLEND",
}

# contents flags (bspflags.h)
CONTENTS_EMPTY = 0
CONTENTS_SOLID = 0x1
CONTENTS_WINDOW = 0x2
CONTENTS_GRATE = 0x8
CONTENTS_SLIME = 0x10
CONTENTS_WATER = 0x20
CONTENTS_MOVEABLE = 0x4000
CONTENTS_PLAYERCLIP = 0x10000
CONTENTS_MONSTERCLIP = 0x20000
CONTENTS_LADDER = 0x20000000
CONTENTS_DETAIL = 0x8000000
CONTENTS_TRANSLUCENT = 0x10000000
CONTENTS_AREAPORTAL = 0x8000

# texinfo surface flags
SURF_SKY2D = 0x2
SURF_SKY = 0x4
SURF_NODRAW = 0x80
SURF_HINT = 0x100
SURF_SKIP = 0x200
SURF_TRIGGER = 0x40


@dataclass(frozen=True)
class LumpEntry:
    index: int
    offset: int
    length: int
    version: int
    fourcc: int

    @property
    def name(self) -> str:
        return LUMP_NAMES.get(self.index, f"LUMP_{self.index}")


class BspFile:
    """Lazy reader over a VBSP file. Only touches the lumps you ask for."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open("rb") as fh:
            head = fh.read(8 + 64 * 16 + 4)
        ident, self.version = struct.unpack_from("<4si", head, 0)
        if ident != b"VBSP":
            raise ValueError(f"not a VBSP file: {ident!r}")
        self.lumps: list[LumpEntry] = []
        for i in range(64):
            ofs, ln, ver, fc = struct.unpack_from("<iii i", head, 8 + i * 16)
            self.lumps.append(LumpEntry(i, ofs, ln, ver, fc))
        self.revision = struct.unpack_from("<i", head, 8 + 64 * 16)[0]

    def raw(self, index: int) -> bytes:
        e = self.lumps[index]
        if e.length == 0:
            return b""
        with self.path.open("rb") as fh:
            fh.seek(e.offset)
            data = fh.read(e.length)
        if data[:4] == b"LZMA":
            raise NotImplementedError(f"lump {e.name} is LZMA-compressed (console BSP)")
        return data

    def array(self, index: int, dtype: np.dtype) -> np.ndarray:
        data = self.raw(index)
        if not data:
            return np.zeros(0, dtype=dtype)
        n = len(data) // dtype.itemsize
        return np.frombuffer(data[: n * dtype.itemsize], dtype=dtype).copy()
