from __future__ import annotations

import re
from pathlib import Path

from .ffmpeg_tools import extract_silence_segments, run_command
from .models import AudioAnalysis, AudioIssueFlags, MediaClip


RMS_PATTERN = re.compile(r"RMS level dB:\s*(-?[\d\.]+)")
PEAK_PATTERN = re.compile(r"Peak level dB:\s*(-?[\d\.]+)")


def _invert_silence_to_speech(
    silence_segments: list[tuple[float, float]], duration_s: float
) -> list[tuple[float, float]]:
    if duration_s <= 0:
        return []

    normalized = sorted(
        (max(0.0, start), min(duration_s, end)) for start, end in silence_segments if end > start
    )
    speech: list[tuple[float, float]] = []
    cursor = 0.0

    for start, end in normalized:
        if start > cursor:
            speech.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < duration_s:
        speech.append((cursor, duration_s))

    return [(start, end) for start, end in speech if end - start >= 0.25]


def _parse_db_stats(stderr: str) -> tuple[float | None, float | None]:
    rms_values = [float(value) for value in RMS_PATTERN.findall(stderr)]
    peak_values = [float(value) for value in PEAK_PATTERN.findall(stderr)]

    avg_rms = sum(rms_values) / len(rms_values) if rms_values else None
    peak_db = max(peak_values) if peak_values else None
    return avg_rms, peak_db


def _score_clarity(rms_db: float | None, peak_db: float | None, silence_segments: list[tuple[float, float]]) -> tuple[float, AudioIssueFlags]:
    flags = AudioIssueFlags()
    score = 1.0

    if rms_db is not None and rms_db < -35.0:
        flags.low_rms = True
        score -= 0.25
    if peak_db is not None and peak_db > -1.0:
        flags.clipping_risk = True
        score -= 0.2
    if any((end - start) >= 2.5 for start, end in silence_segments):
        flags.long_silence = True
        score -= 0.2

    return max(0.0, min(1.0, score)), flags


def analyze_audio_for_clip(clip: MediaClip) -> AudioAnalysis:
    if not clip.has_audio or not clip.audio_path or not Path(clip.audio_path).exists():
        flags = AudioIssueFlags(missing_audio=True)
        return AudioAnalysis(
            clip_id=clip.id,
            speech_segments=[],
            silence_segments=[],
            rms_db=None,
            peak_db=None,
            clarity_score=0.0,
            issues=flags,
        )

    silence_run = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            clip.audio_path,
            "-af",
            "silencedetect=noise=-35dB:d=0.35",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    silence_segments = extract_silence_segments(silence_run.stderr)

    astats_run = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            clip.audio_path,
            "-af",
            "astats=metadata=1:reset=1",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )

    rms_db, peak_db = _parse_db_stats(astats_run.stderr)
    clarity_score, flags = _score_clarity(rms_db, peak_db, silence_segments)
    speech_segments = _invert_silence_to_speech(silence_segments, clip.duration_s)

    return AudioAnalysis(
        clip_id=clip.id,
        speech_segments=speech_segments,
        silence_segments=silence_segments,
        rms_db=rms_db,
        peak_db=peak_db,
        clarity_score=clarity_score,
        issues=flags,
    )


def analyze_audio(clips: list[MediaClip]) -> list[AudioAnalysis]:
    return [analyze_audio_for_clip(clip) for clip in clips]
