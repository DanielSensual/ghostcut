from __future__ import annotations

from .models import AudioAnalysis, BrollOverlay, CandidateSegment, MediaClip, TimelineSegment, Transcript


def _candidate_lookup(candidates: list[CandidateSegment]) -> dict[tuple[str, float, float], CandidateSegment]:
    return {
        (candidate.clip_id, round(candidate.start_s, 3), round(candidate.end_s, 3)): candidate
        for candidate in candidates
    }


def _transcript_lookup(transcripts: list[Transcript]) -> dict[str, Transcript]:
    return {transcript.clip_id: transcript for transcript in transcripts}


def _audio_lookup(audio_analysis: list[AudioAnalysis]) -> dict[str, AudioAnalysis]:
    return {audio.clip_id: audio for audio in audio_analysis}


def build_review_queue(
    clips: list[MediaClip],
    timeline_segments: list[TimelineSegment],
    candidates: list[CandidateSegment],
    transcripts: list[Transcript],
    audio_analysis: list[AudioAnalysis],
    broll_overlays: list[BrollOverlay],
) -> dict:
    candidate_by_key = _candidate_lookup(candidates)
    transcript_by_clip = _transcript_lookup(transcripts)
    audio_by_clip = _audio_lookup(audio_analysis)
    clip_by_id = {clip.id: clip for clip in clips}

    findings: list[dict] = []
    for segment in timeline_segments:
        key = (segment.clip_id, round(segment.src_in_s, 3), round(segment.src_out_s, 3))
        candidate = candidate_by_key.get(key)
        clip = clip_by_id.get(segment.clip_id)
        transcript = transcript_by_clip.get(segment.clip_id)
        audio = audio_by_clip.get(segment.clip_id)

        if candidate and candidate.total_score < 0.55:
            findings.append(
                {
                    "priority": "high",
                    "issue": "low_segment_confidence",
                    "section": segment.section,
                    "clip_id": segment.clip_id,
                    "timeline_in_s": segment.timeline_in_s,
                    "details": {
                        "total_score": candidate.total_score,
                        "transcript_score": candidate.transcript_score,
                        "audio_score": candidate.audio_score,
                        "visual_score": candidate.visual_score,
                    },
                    "recommended_action": "Replace with alternate candidate or trim segment.",
                }
            )

        if transcript and transcript.average_confidence < 0.55 and clip and clip.has_audio:
            findings.append(
                {
                    "priority": "medium",
                    "issue": "low_transcript_confidence",
                    "section": segment.section,
                    "clip_id": segment.clip_id,
                    "timeline_in_s": segment.timeline_in_s,
                    "details": {
                        "average_confidence": round(transcript.average_confidence, 4),
                    },
                    "recommended_action": "Manual transcript verification or recut around clearer speech.",
                }
            )

        if audio and (audio.issues.clipping_risk or audio.issues.low_rms or audio.issues.long_silence):
            findings.append(
                {
                    "priority": "medium",
                    "issue": "audio_quality_flag",
                    "section": segment.section,
                    "clip_id": segment.clip_id,
                    "timeline_in_s": segment.timeline_in_s,
                    "details": {
                        "clipping_risk": audio.issues.clipping_risk,
                        "low_rms": audio.issues.low_rms,
                        "long_silence": audio.issues.long_silence,
                    },
                    "recommended_action": "Manual audio cleanup or replace with alternate take.",
                }
            )

    long_segments = [segment for segment in timeline_segments if (segment.src_out_s - segment.src_in_s) >= 6.0]
    if long_segments and not broll_overlays:
        findings.append(
            {
                "priority": "low",
                "issue": "missing_broll_cover",
                "section": "Global",
                "clip_id": "",
                "timeline_in_s": 0.0,
                "details": {
                    "long_segments": len(long_segments),
                },
                "recommended_action": "Add B-roll overlays for pacing and visual variety.",
            }
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(
        key=lambda item: (
            priority_order.get(item["priority"], 9),
            item.get("timeline_in_s", 0.0),
            item.get("clip_id", ""),
        )
    )

    high = sum(1 for finding in findings if finding["priority"] == "high")
    medium = sum(1 for finding in findings if finding["priority"] == "medium")
    low = sum(1 for finding in findings if finding["priority"] == "low")

    return {
        "summary": {
            "total": len(findings),
            "high": high,
            "medium": medium,
            "low": low,
            "review_required": high > 0 or medium > 0,
        },
        "findings": findings,
    }
