from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import ShotSpan, Transcript


@dataclass(slots=True)
class TimeSpan:
    start_s: float
    end_s: float


@dataclass(slots=True)
class ShotTags:
    label: str
    confidence: float


class Transcriber(Protocol):
    def transcribe(self, audio_path: str, language: str) -> Transcript:
        """Return transcript data for one audio file."""


class VisionTagger(Protocol):
    def tag(self, clip_path: str, shot_spans: list[ShotSpan]) -> list[ShotTags]:
        """Return tags for each shot span."""
