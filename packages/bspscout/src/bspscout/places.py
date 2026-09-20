"""Give places names, using the static-prop model dictionary as the only in-file clue."""
from __future__ import annotations

import re

import numpy as np

# Pripyat asset packs name their models after the real buildings; that is the
# best available evidence for what a given structure is meant to be.
KEYWORDS: list[tuple[str, str]] = [
    (r"school", "School"),
    (r"kindergarden|kindergarten|detsad", "Kindergarten"),
    (r"hospital|medsan|mschs", "Hospital"),
    (r"library|biblio", "Library"),
    (r"univermag", "Univermag (department store)"),
    (r"\bkbo\b|oldkbo", "KBO (house of services)"),
    (r"berezka", "Berezka cafe"),
    (r"ovoshi", "Grocery"),
    (r"hostel|hotel|polissya|polesie", "Hotel"),
    (r"port_sign|\bport\b|prichal", "River port"),
    (r"lenin|prometey|statue|monument", "Monument / civic square"),
    (r"stadium|stadion|tribun", "Stadium"),
    (r"bassein|pool|lazurny", "Swimming pool"),
    (r"traintrack|_train|train_|vagon|elektrichka|railway|railroad", "Rail yard"),
    (r"avtobus|\bbus\b", "Bus depot"),
    (r"zapravka|benzo|fuel", "Fuel station"),
    (r"crane", "Construction crane"),
    (r"zavod|factory|reactor|industr|tsex", "Industrial building"),
    (r"garage|garazh", "Garages"),
    (r"cinema|kino", "Cinema"),
    (r"cafe|stolov|restoran", "Canteen / cafe"),
    (r"dvorec|palace|energetik|culture", "Palace of Culture"),
    (r"apartment|9floor|5floor|balkon|dom_|zhiloy|hrushch", "Apartment block"),
    (r"barrack|military|checkpoint|outpost", "Military post"),
]
# façade kits are reused all over the map, so they name an art style, not a place
_WEAK = re.compile(r"window|door|frame|zabor|fence|bush|tree|grass|debris|trash|"
                   r"rubble|pipe_|wire|lamp|light|curb|kerb|tile|detail|railing|"
                   r"stairs|roof|vent|antenna|balkon|plita|beton|facade|support")
# a sign or a one-off landmark is placed at the building it belongs to
_STRONG = re.compile(r"sign|statue|monument|lenin|prometey|vagon|elektrichka|"
                     r"_train|train_|avtobus|crane|gates")


def load_props(bsp):
    from .bsp import parse_static_props
    names, origin, ptype = parse_static_props(bsp)
    if len(origin) == 0:
        return [], np.zeros((0, 3)), np.zeros(0, np.int64)
    return names, origin, ptype


def label_area(names, origin, ptype, bbox, margin=256.0):
    """Best-guess human name for the area inside bbox, plus the evidence.

    Signs and one-off landmarks are trusted; façade kits only hint.
    """
    x0, y0, x1, y1 = bbox
    m = ((origin[:, 0] >= x0 - margin) & (origin[:, 0] <= x1 + margin)
         & (origin[:, 1] >= y0 - margin) & (origin[:, 1] <= y1 + margin))
    if not m.any():
        return None, []
    inside = ptype[m]
    counts: dict[str, int] = {}
    for t, n in zip(*np.unique(inside, return_counts=True)):
        counts[names[int(t)]] = int(n)
    strong: dict[str, int] = {}
    weak: dict[str, int] = {}
    ev_s: dict[str, str] = {}
    ev_w: dict[str, str] = {}
    for model, n in counts.items():
        base = model.rsplit("/", 1)[-1]
        for rx, label in KEYWORDS:
            if not re.search(rx, base, re.I):
                continue
            if _STRONG.search(base) and not _WEAK.search(base):
                strong[label] = strong.get(label, 0) + n
                ev_s.setdefault(label, base)
            else:
                weak[label] = weak.get(label, 0) + n
                ev_w.setdefault(label, base)
            break
    if strong:
        best = max(strong.items(), key=lambda kv: kv[1])
        return best[0], [(ev_s[best[0]], best[1], "sign")]
    if weak:
        best = max(weak.items(), key=lambda kv: kv[1])
        return f"{best[0]}?", [(ev_w[best[0]], best[1], "facade kit")]
    return None, [(k.rsplit("/", 1)[-1], v, "prop") for k, v in
                  sorted(counts.items(), key=lambda kv: -kv[1])[:2]]


def landmark_clusters(names, origin, ptype, radius=768.0, min_props=3):
    """Open-air landmarks (rail yard, monument, cranes) worth an objective."""
    keep_rx = re.compile(r"train|vagon|elektrichka|crane|statue|monument|lenin|"
                         r"prometey|avtobus|zapravka|stadium", re.I)
    sel = np.array([bool(keep_rx.search(names[int(t)].rsplit("/", 1)[-1])) for t in ptype])
    if not sel.any():
        return []
    pts = origin[sel]
    types = ptype[sel]
    used = np.zeros(len(pts), bool)
    out = []
    order = np.argsort(-pts[:, 2])
    for i in order:
        if used[i]:
            continue
        d = np.hypot(pts[:, 0] - pts[i, 0], pts[:, 1] - pts[i, 1])
        grp = (d < radius) & ~used
        if grp.sum() < min_props:
            continue
        used |= grp
        bb = [pts[grp, 0].min(), pts[grp, 1].min(), pts[grp, 0].max(), pts[grp, 1].max()]
        label, ev = label_area(names, origin, ptype, bb)
        out.append({"centroid": [float(pts[grp, 0].mean()), float(pts[grp, 1].mean())],
                    "bbox": [float(v) for v in bb], "n_props": int(grp.sum()),
                    "label": label or "landmark cluster", "evidence": ev})
    return out
