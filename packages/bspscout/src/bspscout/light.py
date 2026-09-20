"""Lightmap brightness, sampled off the LIGHTING lump - black corners and blown walls.

Insurgency ships HDR-only maps, and that has a trap in it: LIGHTING (lump 8) is
present but empty, LIGHTING_HDR (53) carries the luxels, and the face array
whose `lightofs` index those luxels is FACES_HDR (58), not FACES (7). The LDR
faces are all `lightofs 0`, so reading them against the HDR lump does not fail -
it returns luminances around 2^120 and looks like a units problem.

Caveat worth keeping in mind when reading the low tail: a face's lightmap is a
rectangular block, so luxels outside the face polygon are stored and are black.
That inflates the dark fraction on every map by roughly the same amount, which
is fine for comparing maps and wrong for "x% of this floor is unlit".
"""
from __future__ import annotations

import numpy as np

from .bsp import DT_FACE, Bsp
from .lumps import SURF_NODRAW, SURF_SKIP, SURF_SKY, SURF_SKY2D, SURF_TRIGGER
from .nav import MAX_SLOPE_NZ

_NOT_LIT = SURF_SKY | SURF_SKY2D | SURF_NODRAW | SURF_SKIP | SURF_TRIGGER
_LUMA = np.array([0.2126, 0.7152, 0.0722])


def lit_faces(bsp: Bsp):
    """(faces, luxels, hdr) - the face array that matches the lighting lump in use."""
    hdr = bsp.file.lumps[53].length > 0 and bsp.file.lumps[58].length > 0
    faces = bsp.file.array(58, DT_FACE) if hdr else bsp.faces
    raw = np.frombuffer(bsp.file.raw(53 if hdr else 8), dtype=np.uint8)
    return faces, raw, hdr


def floor_luxels(bsp: Bsp, min_nz: float = MAX_SLOPE_NZ):
    """Luminance of every style-0 luxel on standable-facing lit surfaces.

    Returns (luminance, area_weight): one weight per luxel, summing to the face
    area, so a big warehouse floor is not outvoted by a stack of small steps.
    """
    faces, raw, hdr = lit_faces(bsp)
    if not len(faces) or not len(raw):
        return np.zeros(0), np.zeros(0), 0, hdr
    pz = bsp.planes["normal"][faces["planenum"].astype(np.int64)][:, 2]
    nz = np.where(faces["side"].astype(bool), -pz, pz)
    ti = faces["texinfo"].astype(np.int64)
    flags = np.where(ti >= 0, bsp.texinfo["flags"][np.clip(ti, 0, None)], 0)
    sel = np.flatnonzero((nz >= min_nz) & (faces["lightofs"] >= 0)
                         & ((flags & _NOT_LIT) == 0) & (faces["area"] > 0))
    lum, wgt = [], []
    for fi in sel:
        w, h = faces["lm_size"][fi] + 1
        count = int(w) * int(h)
        off = int(faces["lightofs"][fi])
        block = raw[off:off + count * 4]
        if len(block) < count * 4:
            continue                      # truncated lump; skip rather than guess
        block = block.reshape(-1, 4)
        # ColorRGBExp32: three bytes of mantissa and a signed power of two.
        scale = np.exp2(block[:, 3].view(np.int8).astype(np.float64))
        lum.append(block[:, :3].astype(np.float64) @ _LUMA * scale)
        wgt.append(np.full(count, float(faces["area"][fi]) / count))
    if not lum:
        return np.zeros(0), np.zeros(0), 0, hdr
    return np.concatenate(lum), np.concatenate(wgt), len(sel), hdr


def wpercentile(values: np.ndarray, weights: np.ndarray, q) -> np.ndarray:
    """Weighted percentiles, at the midpoint of each weight step."""
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = (np.cumsum(w) - 0.5 * w) / w.sum()
    return np.interp(np.asarray(q, float) / 100.0, cum, v)


def stats(bsp: Bsp, dark: float = 1.0) -> dict:
    """Brightness distribution over standable-facing surfaces, area-weighted."""
    lum, wgt, nfaces, hdr = floor_luxels(bsp)
    if not len(lum):
        return {"light_hdr": hdr, "light_faces": 0, "light_p5": None, "light_p50": None,
                "light_p95": None, "light_dark_frac": None}
    p5, p50, p95 = wpercentile(lum, wgt, [5, 50, 95])
    return {
        "light_hdr": hdr,
        "light_faces": int(nfaces),
        "light_p5": float(p5),
        "light_p50": float(p50),
        "light_p95": float(p95),
        "light_dark_frac": float(wgt[lum < dark].sum() / wgt.sum()),
    }
