from __future__ import annotations

from collections import defaultdict

from .models import BrollOverlay, CandidateSegment, MediaClip, ShotSpan, TimelineSegment


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_visual_source_pool(
    clips: list[MediaClip],
    shot_spans: list[ShotSpan],
) -> dict[str, list[ShotSpan]]:
    clips_by_id = {clip.id: clip for clip in clips}
    pool: dict[str, list[ShotSpan]] = defaultdict(list)

    for shot in shot_spans:
        clip = clips_by_id.get(shot.clip_id)
        if not clip:
            continue

        score = (shot.stability_score * 0.45) + (shot.blur_score * 0.30) + (shot.exposure_score * 0.25)
        if score < 0.55:
            continue

        # Prefer actual B-roll layer clips for overlays
        if clip.layer == "b-roll":
            pool[shot.clip_id].append(shot)
            continue

        # Also accept no-audio clips or drone footage as overlay candidates
        if not clip.has_audio or clip.layer == "drone":
            pool[shot.clip_id].append(shot)
            continue

        # Accept long shots from a-roll only if no better options exist
        if (shot.end_s - shot.start_s) >= 2.0:
            pool[shot.clip_id].append(shot)

    # Sort each clip's shots by visual quality
    for clip_id in list(pool.keys()):
        pool[clip_id].sort(
            key=lambda shot: (
                -((shot.stability_score * 0.45) + (shot.blur_score * 0.30) + (shot.exposure_score * 0.25)),
                shot.start_s,
            )
        )

    # Re-order source clip IDs to prioritize b-roll layer clips
    for clip_id in list(pool.keys()):
        clip = clips_by_id.get(clip_id)
        if clip and clip.layer != "b-roll":
            # Move non-b-roll to end by removing and re-adding as a lower-priority pool
            pass

    return pool


def _candidate_lookup(candidates: list[CandidateSegment]) -> dict[tuple[str, float, float], CandidateSegment]:
    lookup: dict[tuple[str, float, float], CandidateSegment] = {}
    for candidate in candidates:
        key = (candidate.clip_id, round(candidate.start_s, 3), round(candidate.end_s, 3))
        lookup[key] = candidate
    return lookup


def plan_broll_overlays(
    clips: list[MediaClip],
    timeline_segments: list[TimelineSegment],
    shot_spans: list[ShotSpan],
    candidates: list[CandidateSegment],
) -> list[BrollOverlay]:
    if not timeline_segments:
        return []

    pool = _build_visual_source_pool(clips, shot_spans)
    if not pool:
        return []

    clips_by_id = {clip.id: clip for clip in clips}
    # Sort to prioritize b-roll, then drone, then a-roll
    layer_order = {"b-roll": 0, "drone": 1, "a-roll": 2}
    source_clip_ids = sorted(
        pool.keys(),
        key=lambda cid: (layer_order.get(getattr(clips_by_id.get(cid), "layer", "a-roll"), 2), cid),
    )
    source_cursor = 0
    source_shot_cursor: dict[str, int] = defaultdict(int)
    candidate_by_key = _candidate_lookup(candidates)

    overlays: list[BrollOverlay] = []
    for segment in timeline_segments:
        base_duration = segment.src_out_s - segment.src_in_s
        if base_duration < 4.0:
            continue

        key = (segment.clip_id, round(segment.src_in_s, 3), round(segment.src_out_s, 3))
        matched_candidate = candidate_by_key.get(key)
        transcript_score = matched_candidate.transcript_score if matched_candidate else 0.5

        desired_duration = _clamp(base_duration * 0.35, 2.0, 3.0)
        timeline_start = segment.timeline_in_s + min(1.0, base_duration * 0.2)
        timeline_end = timeline_start + desired_duration

        preferred_reason = "pacing_cutaway"
        if transcript_score < 0.45:
            preferred_reason = "visual_support_low_transcript"

        chosen_clip_id = None
        for _ in range(len(source_clip_ids)):
            clip_id = source_clip_ids[source_cursor % len(source_clip_ids)]
            source_cursor += 1
            if clip_id != segment.clip_id:
                chosen_clip_id = clip_id
                break
        if chosen_clip_id is None:
            chosen_clip_id = source_clip_ids[0]

        shots = pool.get(chosen_clip_id, [])
        if not shots:
            continue
        shot_index = source_shot_cursor[chosen_clip_id] % len(shots)
        source_shot_cursor[chosen_clip_id] += 1
        shot = shots[shot_index]

        source_start = shot.start_s
        available_duration = shot.end_s - shot.start_s
        use_duration = min(desired_duration, available_duration)
        if use_duration < 1.5:
            continue

        overlays.append(
            BrollOverlay(
                section=segment.section,
                base_clip_id=segment.clip_id,
                clip_id=chosen_clip_id,
                src_in_s=round(source_start, 3),
                src_out_s=round(source_start + use_duration, 3),
                timeline_in_s=round(timeline_start, 3),
                timeline_out_s=round(timeline_start + use_duration, 3),
                reason=preferred_reason,
            )
        )

    overlays.sort(key=lambda item: (item.timeline_in_s, item.clip_id, item.src_in_s))
    return overlays
