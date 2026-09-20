"""Calibration: what do maps that play well actually measure like?

bspscout produces the raw numbers (sightlines, exposure, cover, lightmap
brightness, PVS cost). This turns them into target distributions by running
over the shipped maps, so generation matches an empirical corpus instead of
thresholds someone invented.
"""

from .corpus import Corpus, SECTIONS          # noqa: E402
from .record import measure                   # noqa: E402

__all__ = ["Corpus", "SECTIONS", "measure"]
