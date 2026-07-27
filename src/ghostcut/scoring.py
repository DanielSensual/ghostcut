from __future__ import annotations

from collections import defaultdict

from .config import MAX_SEGMENT_DURATION, MIN_SEGMENT_DURATION
from .models import AudioAnalysis, CandidateSegment, MediaClip, ShotSpan, Transcript


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _chunk_span(start_s: float, end_s: float) -> list[tuple[float, float]]:
    if end_s - start_s < MIN_SEGMENT_DURATION:
        return []

    spans: list[tuple[float, float]] = []
    cursor = start_s
    while cursor < end_s:
        next_end = min(cursor + MAX_SEGMENT_DURATION, end_s)
        if next_end - cursor >= MIN_SEGMENT_DURATION:
            spans.append((cursor, next_end))
        cursor = next_end
    return spans


def _collect_overlapping_words(
    transcript: Transcript | None,
    start_s: float,
    end_s: float,
) -> tuple[str, float]:
    if not transcript:
        return "", 0.0

    words = [
        word
        for word in transcript.words
        if word.end_s > start_s and word.start_s < end_s and word.text.strip()
    ]
    if not words:
        return "", 0.0

    text = " ".join(word.text for word in words)
    confidence = sum(word.confidence for word in words) / len(words)

    duration = max(0.001, end_s - start_s)
    word_density = min(1.0, len(words) / (duration * 2.5))
    transcript_score = _clamp((confidence * 0.7) + (word_density * 0.3))
    return text, transcript_score


def _visual_score(shot_spans: list[ShotSpan], start_s: float, end_s: float) -> float:
    overlapping = [
        shot
        for shot in shot_spans
        if shot.end_s > start_s and shot.start_s < end_s
    ]
    if not overlapping:
        return 0.5

    total = 0.0
    for shot in overlapping:
        total += (shot.stability_score * 0.45) + (shot.blur_score * 0.3) + (shot.exposure_score * 0.25)
    return _clamp(total / len(overlapping))


def build_candidate_segments(
    clips: list[MediaClip],
    transcripts: list[Transcript],
    audio_analysis: list[AudioAnalysis],
    shot_spans: list[ShotSpan],
    vision_data: list | None = None,
) -> list[CandidateSegment]:
    transcript_by_clip = {item.clip_id: item for item in transcripts}
    audio_by_clip = {item.clip_id: item for item in audio_analysis}

    vision_by_clip: dict[str, object] = {}
    if vision_data:
        for v in vision_data:
            vision_by_clip[v.clip_id] = v

    shots_by_clip: dict[str, list[ShotSpan]] = defaultdict(list)
    for shot in shot_spans:
        shots_by_clip[shot.clip_id].append(shot)

    candidates: list[CandidateSegment] = []
    for clip in clips:
        clip_audio = audio_by_clip.get(clip.id)
        clip_transcript = transcript_by_clip.get(clip.id)
        clip_shots = shots_by_clip.get(clip.id, [])
        clip_vision = vision_by_clip.get(clip.id)

        base_spans: list[tuple[float, float]] = []
        if clip_audio and clip_audio.speech_segments:
            base_spans.extend(clip_audio.speech_segments)
        elif clip_shots:
            # Merge adjacent short shots into longer usable spans
            sorted_shots = sorted(clip_shots, key=lambda s: s.start_s)
            merged_start = sorted_shots[0].start_s
            merged_end = sorted_shots[0].end_s
            for shot in sorted_shots[1:]:
                if shot.start_s <= merged_end + 0.1:  # allow tiny gaps
                    merged_end = max(merged_end, shot.end_s)
                else:
                    base_spans.append((merged_start, merged_end))
                    merged_start = shot.start_s
                    merged_end = shot.end_s
            base_spans.append((merged_start, merged_end))
        else:
            base_spans.append((0.0, clip.duration_s))

        # Extract vision quality and scene label
        vision_quality = 0.5
        scene_label = ""
        is_dup = False
        if clip_vision:
            vision_quality = getattr(clip_vision, "quality_score", 0.5)
            scene_label = getattr(clip_vision, "scene_label", "")
            is_dup = bool(getattr(clip_vision, "is_duplicate_of", ""))

        for span_start, span_end in base_spans:
            for seg_start, seg_end in _chunk_span(span_start, span_end):
                transcript_text, transcript_score = _collect_overlapping_words(
                    clip_transcript, seg_start, seg_end
                )
                if clip_transcript and clip_transcript.words and transcript_score == 0.0:
                    transcript_score = 0.25
                if not clip_transcript or not clip_transcript.words:
                    transcript_score = 0.5

                # No-audio clips (visual-only B-roll) get neutral score, not 0.0
                if clip_audio and clip_audio.issues.missing_audio:
                    audio_score = 0.5
                elif clip_audio:
                    audio_score = clip_audio.clarity_score
                else:
                    audio_score = 0.5
                visual_score = _visual_score(clip_shots, seg_start, seg_end)

                # Weight scoring: when vision data is available, give it 20% weight
                if vision_by_clip:
                    total = (
                        (0.35 * transcript_score)
                        + (0.25 * audio_score)
                        + (0.20 * visual_score)
                        + (0.20 * vision_quality)
                    )
                else:
                    total = (0.45 * transcript_score) + (0.30 * audio_score) + (0.25 * visual_score)

                # Penalize duplicates heavily
                if is_dup:
                    total *= 0.3

                candidates.append(
                    CandidateSegment(
                        clip_id=clip.id,
                        start_s=round(seg_start, 3),
                        end_s=round(seg_end, 3),
                        transcript_score=round(transcript_score, 4),
                        audio_score=round(audio_score, 4),
                        visual_score=round(visual_score, 4),
                        total_score=round(total, 4),
                        transcript_text=transcript_text,
                        layer=clip.layer,
                        vision_score=round(vision_quality, 4),
                        scene_label=scene_label,
                    )
                )

    # Deterministic output ordering
    candidates.sort(key=lambda item: (-item.total_score, item.clip_id, item.start_s, item.end_s))
    return candidates

