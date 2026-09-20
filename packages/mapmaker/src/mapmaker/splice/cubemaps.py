"""Carry the shipped baked cubemaps across into a spliced map.

Cubemaps are the one part of a map's lighting the compile tools cannot make.
`buildcubemaps` runs in the *game client*, which is not in this toolchain, so a
recompiled map gets vbsp's placeholders instead: every shiny surface - glass,
tile, metal, water - reflects a flat grey nothing. On ministry that is 113
reflections lost.

They are recoverable because of how they are named. vbsp keys each one to the
position of its `env_cubemap`:

    materials/maps/ministry/c-1040_-120_250.vtf
    materials/maps/ministry/c-1040_-120_250.hdr.vtf

So for any cubemap whose position a splice did not change, the shipped file is
still the correct file, and for one that moved by a known offset the shipped
file from the old position is the right content under a new name. Both are just
zip entries in the BSP's pakfile lump.

What this does not fix: a reflection is only right if what it reflects has not
moved. Across a splice boundary a transplanted cubemap is an approximation -
the same approximation the shipped map makes for anything that changed after it
was last baked.
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HEADER = 8 + 64 * 16 + 4
PAKFILE = 40
CUBEMAP = re.compile(r"^materials/maps/(?P<map>[^/]+)/c(?P<x>-?\d+)_(?P<y>-?\d+)_(?P<z>-?\d+)(?P<hdr>\.hdr)?\.vtf$",
                     re.IGNORECASE)


@dataclass
class Result:
    transplanted: int = 0
    unmoved: int = 0
    moved: int = 0
    from_other_variant: int = 0
    missing: list[str] = field(default_factory=list)
    out: Path | None = None

    def lines(self) -> list[str]:
        out = [f"cubemaps: {self.transplanted} transplanted "
               f"({self.unmoved} in place, {self.moved} from the moved half)"]
        if self.from_other_variant:
            out.append(f"  {self.from_other_variant} filled from the other exposure variant - "
                       "the shipped maps pack HDR cubemaps only, and a recompile asks for both")
        if self.missing:
            out.append(f"  {len(self.missing)} had no shipped original: {', '.join(self.missing[:6])}"
                       + (" ..." if len(self.missing) > 6 else ""))
        return out


# --------------------------------------------------------------- pakfile access
def read_pakfile(path) -> bytes:
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(HEADER)
        ident = struct.unpack_from("<4s", head, 0)[0]
        if ident != b"VBSP":
            raise ValueError(f"not a VBSP file: {path}")
        offset, length = struct.unpack_from("<ii", head, 8 + PAKFILE * 16)[:2]
        fh.seek(offset)
        return fh.read(length)


def write_pakfile(path, data: bytes, out_path=None) -> Path:
    """Rewrite the pakfile lump, appending it and repointing the header.

    A lump may live anywhere in the file, so growing one costs nothing but the
    dead bytes where it used to be - which is what bspzip does too. Every other
    lump keeps its offset, so nothing else can be disturbed by this.
    """
    path = Path(path)
    out_path = Path(out_path) if out_path else path
    raw = bytearray(path.read_bytes())
    offset = len(raw)
    if offset % 4:
        raw += b"\0" * (4 - offset % 4)
        offset = len(raw)
    raw += data
    struct.pack_into("<ii", raw, 8 + PAKFILE * 16, offset, len(data))
    out_path.write_bytes(raw)
    return out_path


# ------------------------------------------------------------------ transplant
def _index(pak: bytes) -> dict[tuple[int, int, int, str], bytes]:
    out = {}
    with zipfile.ZipFile(io.BytesIO(pak)) as z:
        for info in z.infolist():
            m = CUBEMAP.match(info.filename.replace("\\", "/"))
            if m:
                key = (int(m["x"]), int(m["y"]), int(m["z"]), (m["hdr"] or "").lower())
                out[key] = z.read(info)
    return out


def transplant(shipped, spliced, delta=(0.0, 0.0, 0.0), out_path=None) -> Result:
    """Copy the shipped cubemaps into a spliced map, following the splice.

    For each cubemap in the new map, the shipped original is either at the same
    position (the half that did not move) or `delta` back along the splice (the
    half that did). Both are tried, nearer first, because a position sitting on
    the seam can plausibly be either.
    """
    dx, dy, dz = (int(round(float(c))) for c in delta)
    shipped_index = _index(read_pakfile(shipped))
    pak = read_pakfile(spliced)
    result = Result()

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(pak)) as src, \
         zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            m = CUBEMAP.match(info.filename.replace("\\", "/"))
            if m:
                x, y, z_ = int(m["x"]), int(m["y"]), int(m["z"])
                hdr = (m["hdr"] or "").lower()
                other = "" if hdr else ".hdr"
                # Position first, exposure variant second: a shipped map packs
                # only the variant it was built for, and the right reflection in
                # the wrong exposure beats vbsp's grey placeholder.
                keys = ((x, y, z_, hdr), (x - dx, y - dy, z_ - dz, hdr),
                        (x, y, z_, other), (x - dx, y - dy, z_ - dz, other))
                for n, key in enumerate(keys):
                    if key in shipped_index:
                        data = shipped_index[key]
                        result.transplanted += 1
                        if key[:3] == (x, y, z_):
                            result.unmoved += 1
                        else:
                            result.moved += 1
                        if n >= 2:
                            result.from_other_variant += 1
                        break
                else:
                    result.missing.append(f"c{x}_{y}_{z_}{hdr}")
            dst.writestr(info, data)

    result.out = write_pakfile(spliced, buf.getvalue(), out_path)
    return result
