"""navlayout — decide where a fight should happen on geometry that already exists.

Reads `surveys/<map>.json` (the engine's own nav graph, dumped by
`plugin/mapmaker_survey`), measures how open a map is, sites objectives and
spawns on the open ones, and emits the presets `plugin/mapmaker_layout` applies
at load. `PLAN.md` step B.

The split it lives inside: the engine owns the ground truth, offline owns the
thinking, and nothing offline is trusted until it has reproduced something the
engine already said.
"""

from .check import CheckResult, check, report
from .graph import NavGraph
from .places import Place, Places, segment
from .reverse import (
    Layout,
    Plan,
    Reversal,
    explicit_plan,
    layout,
    reverse,
    reverse_plan,
    ring_family,
    ring_plan,
    stock_plan,
)
from .survey import (
    Area,
    CpSetup,
    Entity,
    Survey,
    find_chain,
    find_cpsetup,
    infer_stage_count,
    read_chain,
    read_cpsetup,
)

__all__ = [
    "Area",
    "CheckResult",
    "CpSetup",
    "Entity",
    "Layout",
    "NavGraph",
    "Place",
    "Plan",
    "Places",
    "Reversal",
    "Survey",
    "check",
    "explicit_plan",
    "find_chain",
    "layout",
    "find_cpsetup",
    "infer_stage_count",
    "read_chain",
    "read_cpsetup",
    "report",
    "reverse",
    "reverse_plan",
    "ring_family",
    "ring_plan",
    "segment",
    "stock_plan",
]
