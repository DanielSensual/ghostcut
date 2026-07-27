from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..models import Transcript, TranscriptWord


class OpenAITranscriber:
    def __init__(self, model: str = "gpt-4o-mini-transcribe") -> None:
        self.model = model

    def transcribe(self, audio_path: str, language: str) -> Transcript:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        try:
            from openai import OpenAI
        except Exception as error:  # pragma: no cover - optional dependency guard
            raise RuntimeError("OpenAI SDK is not installed. Run: pip install -e .[openai]") from error

        client = OpenAI(api_key=api_key)
        clip_id = Path(audio_path).stem

        with Path(audio_path).open("rb") as handle:
            response = client.audio.transcriptions.create(
                model=self.model,
                file=handle,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )

        words = self._parse_words(response)
        text = getattr(response, "text", "") or ""
        avg_conf = sum(word.confidence for word in words) / len(words) if words else 0.0
        return Transcript(clip_id=clip_id, text=text, words=words, average_confidence=avg_conf)

    def _parse_words(self, response: Any) -> list[TranscriptWord]:
        raw_words = getattr(response, "words", None)
        if raw_words is None and isinstance(response, dict):
            raw_words = response.get("words")

        parsed: list[TranscriptWord] = []
        for raw_word in raw_words or []:
            if isinstance(raw_word, dict):
                text = str(raw_word.get("word") or raw_word.get("text") or "").strip()
                start_s = float(raw_word.get("start") or 0.0)
                end_s = float(raw_word.get("end") or start_s)
                confidence = float(raw_word.get("confidence") or 0.9)
            else:
                text = str(getattr(raw_word, "word", "") or getattr(raw_word, "text", "")).strip()
                start_s = float(getattr(raw_word, "start", 0.0) or 0.0)
                end_s = float(getattr(raw_word, "end", start_s) or start_s)
                confidence = float(getattr(raw_word, "confidence", 0.9) or 0.9)

            if not text:
                continue
            parsed.append(
                TranscriptWord(
                    text=text,
                    start_s=max(0.0, start_s),
                    end_s=max(start_s, end_s),
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )
        return parsed
