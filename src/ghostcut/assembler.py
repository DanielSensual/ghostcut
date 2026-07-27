from __future__ import annotations

from collections import defaultdict

from .config import MIN_SEGMENT_DURATION
from .models import CandidateSegment, TimelineSegment
from .story_template import StorySection

# Layer‑preference boost applied when a candidate matches the section's preferred layer.
_LAYER_BOOST = 0.20


def _clip_overlap(
    candidate: CandidateSegment,
    taken: dict[str, list[tuple[float, float]]],
) -> bool:
    spans = taken.get(candidate.clip_id, [])
    for start_s, end_s in spans:
        if candidate.end_s > start_s and candidate.start_s < end_s:
            return True
    return False


def _keyword_bonus(text: str, keywords: tuple[str, ...]) -> float:
    if not text.strip() or not keywords:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lowered)
    if hits == 0:
        return 0.0
    return min(1.0, hits / max(1.0, len(keywords) / 2.0))


def _layer_bonus(candidate: CandidateSegment, preferred_layer: str) -> float:
    """Return a scoring boost when the candidate's layer matches the section preference."""
    if candidate.layer == preferred_layer:
        return _LAYER_BOOST
    return 0.0


# Map section keywords to vision scene labels for content-aware matching
_SCENE_LABEL_MAP: dict[str, set[str]] = {
    "kitchen": {"kitchen", "dining_room"},
    "island": {"kitchen"},
    "countertops": {"kitchen"},
    "appliances": {"kitchen"},
    "dining": {"kitchen", "dining_room"},
    "living": {"living_room"},
    "family": {"living_room"},
    "great room": {"living_room"},
    "bedroom": {"bedroom"},
    "master": {"bedroom"},
    "bathroom": {"bathroom"},
    "closet": {"bedroom"},
    "suite": {"bedroom"},
    "exterior": {"exterior"},
    "front": {"exterior"},
    "curb": {"exterior"},
    "driveway": {"exterior"},
    "backyard": {"exterior", "pool", "patio"},
    "patio": {"patio", "exterior"},
    "pool": {"pool"},
    "outdoor": {"exterior", "pool", "patio"},
    "lanai": {"patio"},
    "aerial": {"aerial"},
    "neighborhood": {"aerial"},
    "street": {"aerial", "exterior"},
}

_SCENE_BONUS = 0.15


def _scene_label_bonus(candidate: CandidateSegment, section_keywords: tuple[str, ...]) -> float:
    """Boost candidates whose vision scene_label matches the section's expected content."""
    if not candidate.scene_label or candidate.scene_label == "other":
        return 0.0
    expected_labels: set[str] = set()
    for kw in section_keywords:
        expected_labels.update(_SCENE_LABEL_MAP.get(kw.lower(), set()))
    if candidate.scene_label in expected_labels:
        return _SCENE_BONUS
    return 0.0


def assemble_timeline(
    candidates: list[CandidateSegment],
    sections: list[StorySection],
    *,
    max_clip_uses: int = 3,
) -> tuple[list[TimelineSegment], list[dict]]:
    timeline: list[TimelineSegment] = []
    section_summary: list[dict] = []
    taken_ranges: dict[str, list[tuple[float, float]]] = defaultdict(list)
    clip_use_count: dict[str, int] = defaultdict(int)
    cursor = 0.0

    for section in sections:
        remaining = section.target_duration
        selected_count = 0
        preferred = getattr(section, "preferred_layer", "a-roll")

        # Sort candidates: prefer matching layer + scene label, then score + keyword bonus
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -(
                    candidate.total_score
                    + (0.2 * _keyword_bonus(candidate.transcript_text, section.keywords))
                    + _layer_bonus(candidate, preferred)
                    + _scene_label_bonus(candidate, section.keywords)
                ),
                candidate.layer != preferred,  # False (match) sorts before True
                candidate.clip_id,
                candidate.start_s,
            ),
        )

        for candidate in ranked:
            if remaining < MIN_SEGMENT_DURATION:
                break
            if _clip_overlap(candidate, taken_ranges):
                continue
            # Enforce clip variety — don't reuse the same source too many times
            if clip_use_count[candidate.clip_id] >= max_clip_uses:
                continue

            candidate_duration = candidate.end_s - candidate.start_s
            usable = min(candidate_duration, remaining)
            if usable < MIN_SEGMENT_DURATION:
                continue

            src_in_s = candidate.start_s
            src_out_s = candidate.start_s + usable
            taken_ranges[candidate.clip_id].append((src_in_s, src_out_s))
            clip_use_count[candidate.clip_id] += 1
            timeline.append(
                TimelineSegment(
                    section=section.name,
                    clip_id=candidate.clip_id,
                    src_in_s=round(src_in_s, 3),
                    src_out_s=round(src_out_s, 3),
                    timeline_in_s=round(cursor, 3),
                )
            )

            selected_count += 1
            cursor += usable
            remaining -= usable

        section_summary.append(
            {
                "section": section.name,
                "preferred_layer": preferred,
                "target_duration_s": round(section.target_duration, 3),
                "filled_duration_s": round(section.target_duration - max(0.0, remaining), 3),
                "selected_segments": selected_count,
            }
        )

    timeline.sort(key=lambda item: item.timeline_in_s)
    return timeline, section_summary
