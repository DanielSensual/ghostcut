from __future__ import annotations

import unittest

from ghostcut.models import AudioAnalysis, AudioIssueFlags, MediaClip, ShotSpan, Transcript, TranscriptWord
from ghostcut.scoring import build_candidate_segments


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clip = MediaClip(
            id="clip-1",
            source_path="/tmp/source.mp4",
            proxy_path="/tmp/proxy.mp4",
            audio_path="/tmp/audio.wav",
            duration_s=12.0,
            fps=30.0,
            has_audio=True,
            width=1280,
            height=720,
        )
        self.audio = AudioAnalysis(
            clip_id="clip-1",
            speech_segments=[(0.0, 12.0)],
            silence_segments=[],
            rms_db=-21.0,
            peak_db=-2.0,
            clarity_score=0.9,
            issues=AudioIssueFlags(),
        )
        self.transcript = Transcript(
            clip_id="clip-1",
            text="welcome to this beautiful home and open living space",
            words=[
                TranscriptWord("welcome", 0.0, 0.3, 0.95),
                TranscriptWord("beautiful", 1.0, 1.4, 0.95),
                TranscriptWord("home", 2.0, 2.3, 0.9),
                TranscriptWord("open", 3.0, 3.2, 0.9),
                TranscriptWord("living", 3.3, 3.7, 0.9),
            ],
            average_confidence=0.92,
        )
        self.shots = [
            ShotSpan(
                clip_id="clip-1",
                start_s=0.0,
                end_s=12.0,
                stability_score=0.8,
                blur_score=0.75,
                exposure_score=0.7,
            )
        ]

    def test_build_candidate_segments_returns_deterministic_sorted_output(self) -> None:
        first = build_candidate_segments([self.clip], [self.transcript], [self.audio], self.shots)
        second = build_candidate_segments([self.clip], [self.transcript], [self.audio], self.shots)

        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first[0].total_score, first[-1].total_score)

    def test_build_candidate_segments_uses_fallback_when_no_transcript(self) -> None:
        candidates = build_candidate_segments([self.clip], [], [self.audio], self.shots)
        self.assertTrue(candidates)
        self.assertTrue(all(abs(item.transcript_score - 0.5) < 1e-6 for item in candidates))


if __name__ == "__main__":
    unittest.main()
