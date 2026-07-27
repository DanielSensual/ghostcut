from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class MediaClip:
    id: str
    source_path: str
    proxy_path: str
    audio_path: str
    duration_s: float
    fps: float
    has_audio: bool
    width: int
    height: int
    layer: str = "a-roll"  # a-roll | b-roll | drone


@dataclass(slots=True)
class VisionAnalysis:
    clip_id: str
    scene_label: str          # kitchen, living_room, bedroom, bathroom, exterior, pool, aerial, etc.
    quality_score: float      # 0.0-1.0 from vision model
    composition_notes: str    # description of framing quality
    phash: str                # perceptual hash hex string
    is_duplicate_of: str = ""  # clip_id of original if duplicate


@dataclass(slots=True)
class TranscriptWord:
    text: str
    start_s: float
    end_s: float
    confidence: float


@dataclass(slots=True)
class Transcript:
    clip_id: str
    text: str
    words: list[TranscriptWord]
    average_confidence: float


@dataclass(slots=True)
class ShotSpan:
    clip_id: str
    start_s: float
    end_s: float
    stability_score: float
    blur_score: float
    exposure_score: float


@dataclass(slots=True)
class AudioIssueFlags:
    clipping_risk: bool = False
    low_rms: bool = False
    long_silence: bool = False
    missing_audio: bool = False


@dataclass(slots=True)
class AudioAnalysis:
    clip_id: str
    speech_segments: list[tuple[float, float]]
    silence_segments: list[tuple[float, float]]
    rms_db: float | None
    peak_db: float | None
    clarity_score: float
    issues: AudioIssueFlags = field(default_factory=AudioIssueFlags)


@dataclass(slots=True)
class CandidateSegment:
    clip_id: str
    start_s: float
    end_s: float
    transcript_score: float
    audio_score: float
    visual_score: float
    total_score: float
    transcript_text: str = ""
    layer: str = "a-roll"
    vision_score: float = 0.5
    scene_label: str = ""


@dataclass(slots=True)
class TimelineSegment:
    section: str
    clip_id: str
    src_in_s: float
    src_out_s: float
    timeline_in_s: float


@dataclass(slots=True)
class BrollOverlay:
    section: str
    base_clip_id: str
    clip_id: str
    src_in_s: float
    src_out_s: float
    timeline_in_s: float
    timeline_out_s: float
    reason: str


@dataclass(slots=True)
class RunReport:
    warnings: list[str]
    dropped_clips: list[str]
    no_audio_clips: list[str]
    confidence_summary: dict[str, float]
    output_paths: dict[str, str]


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): dataclass_to_dict(item) for key, item in value.items()}
    return value
