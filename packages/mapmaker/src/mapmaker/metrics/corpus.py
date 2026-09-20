"""Per-map records -> target distributions, and one map read against them.

The unit of calibration is a distribution, not a threshold. "Official maps sit
at 1.9k u median objective spacing, p5-p95 1.2k-3.4k" is something a generator
can be steered by and a person can argue with; "spacing must exceed 1500" is a
number someone made up, and optimising against it produces a warren of closets.

Which maps count: the _night variants are the same geometry relit and the _coop
variants the same geometry with different objectives, so including all 49 files
would triple-count 19 layouts and make the spread look tighter than it is. Base
maps only, unless asked otherwise.

`scope="coop"` is the exception that matters. Objective siting, spawn geography
and the fight at a capture point are mode-specific - a _coop map is the same
geometry with a *different chain of objectives*, which is precisely what those
fields measure. For anything downstream of the checkpoint mode, the 13 _coop
maps are the corpus and the base maps are the wrong baseline.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

VARIANT_SUFFIXES = ("_coop", "_night")

# (field, label, format) in print order. Formats: i integer, u units, k thousands
# of square units, % fraction, x bare ratio, f float.
SECTIONS: dict[str, list[tuple[str, str, str]]] = {
    "Scale": [
        ("footprint_u2", "footprint", "k"),
        ("walkable_area_u2", "walkable area", "k"),
        ("extent_x_u", "extent x", "u"),
        ("extent_y_u", "extent y", "u"),
        ("span_u", "end-to-end walk", "u"),
    ],
    "Verticality": [
        ("stacking", "walkable / footprint", "x"),
        ("storeys_occupied", "storey bands in use", "i"),
        ("storey_frac_ground", "area at ground", "%"),
        ("storey_frac_l1", "area on L1", "%"),
        ("storey_frac_l2", "area on L2", "%"),
        ("storey_frac_l3plus", "area on L3+", "%"),
    ],
    "Enclosure": [
        ("indoor_frac", "area under cover", "%"),
        ("n_interiors", "interior spaces", "i"),
        ("interiors_per_100k_u2", "interiors per 100k u2", "f"),
        ("interior_footprint_u2", "interior footprint", "k"),
        ("interior_entrances", "ways into an interior", "f"),
        ("interior_storeys", "storeys per interior", "f"),
        ("interior_depth_u", "interior depth", "u"),
    ],
    "Connectivity": [
        ("reachable_frac", "standable area reachable", "%"),
        ("routable_frac", "reachable area routable", "%"),
        ("n_sealed_regions", "sealed regions", "i"),
        ("sealed_frac", "sealed area", "%"),
        ("crouch_frac", "crouch-only area", "%"),
    ],
    "Objectives": [
        ("n_objective_sites", "capture points", "i"),
        ("n_extraction_sites", "extraction points", "i"),
        ("objective_sites_per_100k_u2", "capture points per 100k u2", "f"),
        ("capture_zone_u2", "capture zone footprint", "k"),
        ("capture_zone_extent_u", "capture zone extent", "u"),
        ("objective_spacing_u", "nearest-neighbour walk", "u"),
        ("objective_spacing_straight_u", "nearest-neighbour straight", "u"),
        ("objective_detour", "walk / straight", "x"),
        ("objective_approaches", "ways into an objective", "f"),
        ("objective_ring_open_frac", "objective ring open", "%"),
        ("objective_indoor_frac", "objectives under cover", "%"),
        ("n_spawn_zones", "spawn zones", "i"),
        ("spawn_to_objective_u", "spawn to nearest objective", "u"),
    ],
    "Sightlines": [
        ("sight_p50_u", "sightline p50", "u"),
        ("sight_p90_u", "sightline p90", "u"),
        ("sight_p99_u", "sightline p99", "u"),
        ("sight_long_frac", "rays clear past 1024 u", "%"),
        ("sight_at_cap_frac", "rays that never hit anything", "%"),
        ("sight_best_p50_u", "longest lane per position, p50", "u"),
        ("sight_best_p90_u", "longest lane per position, p90", "u"),
    ],
    "Exposure and cover": [
        ("exposure_p50", "positions in range that see you, p50", "%"),
        ("exposure_p90", "positions in range that see you, p90", "%"),
        ("exposure_hidden_frac", "positions nothing can see", "%"),
        ("cover_crouch_breaks", "sightlines crouching breaks", "%"),
    ],
    "The fight at an objective": [
        ("obj_approach_seen_frac", "approach under watch", "%"),
        ("obj_approach_watchers_frac", "defending spots watching an approach, p50", "%"),
        ("obj_approach_watchers_p90_frac", "...at the worst approach", "%"),
        ("obj_defender_seen_frac", "approach that can shoot a defender", "%"),
        ("obj_defender_crouch_breaks", "objective sightlines crouching breaks", "%"),
        ("spawn_sees_objective_frac", "spawn/objective pairs with line of sight", "%"),
    ],
    "Render cost": [
        ("pvs_clusters", "PVS clusters", "i"),
        ("pvs_mean_visible", "clusters visible per cluster", "f"),
        ("pvs_visible_frac", "map visible per cluster", "%"),
        ("n_static_props", "static props", "i"),
        ("props_per_100k_u2", "props per 100k u2", "f"),
    ],
    "Light": [
        ("light_p50", "floor luminance p50", "f"),
        ("light_p95", "floor luminance p95", "f"),
        ("light_dark_frac", "floor area unlit", "%"),
    ],
}

FIELDS = [f for fields in SECTIONS.values() for f, _, _ in fields]
LABEL = {f: (label, fmt) for fields in SECTIONS.values() for f, label, fmt in fields}


def is_variant(name: str) -> bool:
    return name.endswith(VARIANT_SUFFIXES)


def _fmt(value, kind: str) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "-"
    if kind == "i":
        return f"{value:,.0f}"
    if kind == "u":
        return f"{value:,.0f} u"
    if kind == "k":
        return f"{value / 1000:,.0f}k u²"
    if kind == "%":
        return f"{100 * value:.1f}%"
    if kind == "x":
        return f"{value:.2f}x"
    return f"{value:,.2f}" if abs(value) < 1000 else f"{value:,.0f}"


class Corpus:
    """A set of per-map records, and the distributions over them."""

    def __init__(self, records: list[dict], scope: str = "base"):
        self.records = records
        self.scope = scope

    @classmethod
    def load(cls, dirpath, include_variants: bool = False,
             source: str | None = "official", scope: str = "base") -> "Corpus":
        """scope: "base" (default), "coop" (the checkpoint maps), or "all"."""
        recs = []
        for p in sorted(Path(dirpath).glob("*.json")):
            if p.name == "summary.json":
                continue
            r = json.loads(p.read_text())
            name = r.get("map", p.stem)
            if source is not None and r.get("source") != source:
                continue          # a generated map is not part of its own baseline
            if scope == "coop":
                if not name.endswith("_coop"):
                    continue
            elif not (include_variants or scope == "all") and is_variant(name):
                continue
            recs.append(r)
        return cls(cls._sorted(recs), scope=scope)

    @staticmethod
    def _sorted(recs):
        return sorted(recs, key=lambda r: r.get("map", ""))

    @property
    def maps(self) -> list[str]:
        return [r.get("map", "?") for r in self.records]

    def values(self, field: str) -> np.ndarray:
        v = [r.get(field) for r in self.records]
        return np.asarray([x for x in v if isinstance(x, (int, float))
                           and not isinstance(x, bool) and np.isfinite(x)], float)

    def stats(self, field: str) -> dict | None:
        v = self.values(field)
        if not len(v):
            return None
        return {"n": int(len(v)), "median": float(np.median(v)), "mean": float(v.mean()),
                "sd": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                "p5": float(np.percentile(v, 5)), "p95": float(np.percentile(v, 95)),
                "min": float(v.min()), "max": float(v.max())}

    def summary(self) -> dict[str, dict]:
        return {f: s for f in FIELDS if (s := self.stats(f)) is not None}

    def percentile_of(self, field: str, value) -> float | None:
        v = self.values(field)
        if not len(v) or value is None or not np.isfinite(value):
            return None
        return float(100.0 * (v < value).mean())

    # ---------------------------------------------------------------- output
    def table(self) -> str:
        """"Official maps sit at X +/- Y" for every field, as markdown."""
        hollow = [r.get("map", "?") for r in self.records if not r.get("n_interiors")]
        title = {"coop": "Checkpoint calibration corpus",
                 "all": "Calibration corpus, every shipped file"}.get(
                     self.scope, "Calibration corpus")
        out = [f"# {title} ({len(self.records)} maps)", "",
               "Generated by `mise run metrics`. Ranges are the p5-p95 band across the "
               "corpus, which is the width a generated map has to land inside to be "
               "unremarkable. `sd` is there for fields where the spread is worth reading "
               "as a spread, and `n` is how many maps the field could be measured on.", "",
               f"Maps: {', '.join(self.maps)}", "",
               "## Reading this", "",
               ("- **The `_coop` maps only.** These are the checkpoint layouts: the same "
                "geometry as their base maps, with a different chain of objectives, "
                "different spawn zones and different block zones. Objective siting, "
                "spawn geography and the fight at a capture point are mode-specific, so "
                "for anything aimed at checkpoint the base maps are the wrong baseline."
                if self.scope == "coop" else
                "- **Base maps only.** The `_night` variants are the same geometry relit "
                "and the `_coop` variants the same geometry with different objectives, so "
                "counting all 49 files would triple-count 19 layouts and make every band "
                "look tighter than it is. `--all` includes them."),
               "- **Walkable area is brush-accurate as of PLAN.md step 2.** It was not "
               "before: the solid test descended the BSP tree, which cannot see "
               "`func_detail`, so a quarter of ministry's reachable nodes sat inside "
               "solid geometry. Every number here moved when that was fixed, most on "
               "detail-heavy interiors and least on open terrain.",
               "- **Sight measures are sampled, not exhaustive.** A seeded uniform "
               "subsample of the walk graph, so they are area-weighted and reproducible; "
               "exposure pairs are capped at 2048 u apart, which is the range past which "
               "two positions are not in a fight with each other.",
               "- **Interiors are brush interiors.** Static props are not in the BSP tree "
               "and bspscout does not read `.mdl` yet (PLAN.md step 3), so a building "
               "whose floors are prop models has no walkable interior here."
               + (f" That is why {', '.join(hollow)} reports no interior spaces at all."
                  if hollow else ""),
               "- **The luminance low tail is pessimistic.** A face's lightmap is a "
               "rectangular block, so luxels outside the face polygon are stored, are "
               "black, and count. The bias is roughly equal across maps, which is fine "
               "for comparing them and wrong as an absolute \"unlit floor\" figure.", ""]
        for section, fields in SECTIONS.items():
            rows = [(f, label, fmt, self.stats(f)) for f, label, fmt in fields]
            rows = [row for row in rows if row[3]]
            if not rows:
                continue
            out += [f"## {section}", "",
                    "| metric | median | mean ± sd | p5 – p95 | min – max | n |",
                    "|---|---|---|---|---|---|"]
            for f, label, fmt, s in rows:
                out.append(
                    f"| {label} | {_fmt(s['median'], fmt)} | "
                    f"{_fmt(s['mean'], fmt)} ± {_fmt(s['sd'], fmt)} | "
                    f"{_fmt(s['p5'], fmt)} – {_fmt(s['p95'], fmt)} | "
                    f"{_fmt(s['min'], fmt)} – {_fmt(s['max'], fmt)} | {s['n']} |")
            out.append("")
        return "\n".join(out)

    def compare(self, record: dict) -> list[dict]:
        """Where one map sits in each distribution."""
        rows = []
        for f in FIELDS:
            s = self.stats(f)
            if s is None:
                continue
            v = record.get(f)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v is None:
                rows.append({"field": f, "value": None, "stats": s, "verdict": "-",
                             "pct": None})
                continue
            pct = self.percentile_of(f, v)
            verdict = "in band"
            if v < s["p5"]:
                verdict = "below p5"
            elif v > s["p95"]:
                verdict = "above p95"
            rows.append({"field": f, "value": float(v), "stats": s, "verdict": verdict,
                         "pct": pct})
        return rows

    def compare_table(self, record: dict, only_outliers: bool = False) -> str:
        rows = self.compare(record)
        name = record.get("map", "?")
        out = [f"# {name} vs {len(self.records)} official maps", ""]
        outside = [r for r in rows if r["verdict"] not in ("in band", "-")]
        out += [f"{len(outside)} of {len(rows)} metrics fall outside the corpus "
                f"p5-p95 band.", ""]
        for section, fields in SECTIONS.items():
            keys = {f for f, _, _ in fields}
            sec = [r for r in rows if r["field"] in keys
                   and (not only_outliers or r["verdict"] not in ("in band", "-"))]
            if not sec:
                continue
            out += [f"## {section}", "",
                    "| metric | this map | corpus median | corpus p5 – p95 | percentile | |",
                    "|---|---|---|---|---|---|"]
            for r in sec:
                label, fmt = LABEL[r["field"]]
                s = r["stats"]
                mark = {"in band": "ok", "below p5": "**low**",
                        "above p95": "**high**", "-": "-"}[r["verdict"]]
                pct = f"p{r['pct']:.0f}" if r["pct"] is not None else "-"
                out.append(f"| {label} | {_fmt(r['value'], fmt)} | {_fmt(s['median'], fmt)} | "
                           f"{_fmt(s['p5'], fmt)} – {_fmt(s['p95'], fmt)} | {pct} | {mark} |")
            out.append("")
        return "\n".join(out)
