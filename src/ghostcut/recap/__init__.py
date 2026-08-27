"""Recap mode: beat-synced music recap reels from event footage.

Pipeline: song beat analysis -> motion-peak candidate discovery ->
face-aware selection (frontality scoring, person exclusion, whip-pan
rejection) -> per-segment vid.stab stabilization -> beat-locked assembly
with crossfades.

Requires the [recap] extra: pip install -e ".[recap]"
"""

from .beat import BeatGrid, analyze_song
from .casting import CastMember, discover_cast
from .motion import find_motion_peaks, pan_metrics
from .select import (
    RecapCandidate,
    augment_with_cast,
    build_candidates,
    choose_picks,
    slot_pattern,
)
from .render import assemble_reel

__all__ = [
    "BeatGrid",
    "analyze_song",
    "CastMember",
    "discover_cast",
    "find_motion_peaks",
    "pan_metrics",
    "RecapCandidate",
    "augment_with_cast",
    "build_candidates",
    "choose_picks",
    "slot_pattern",
    "assemble_reel",
]
