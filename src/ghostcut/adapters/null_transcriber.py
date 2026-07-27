from __future__ import annotations

from ..models import Transcript


class NullTranscriber:
    def transcribe(self, audio_path: str, language: str) -> Transcript:
        clip_id = audio_path.rsplit("/", 1)[-1].replace(".wav", "")
        return Transcript(
            clip_id=clip_id,
            text="",
            words=[],
            average_confidence=0.0,
        )
