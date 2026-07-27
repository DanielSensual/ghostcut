from __future__ import annotations

import unittest

from ghostcut.models import (
    AudioAnalysis,
    AudioIssueFlags,
    BrollOverlay,
    CandidateSegment,
    MediaClip,
    TimelineSegment,
    Transcript,
)
from ghostcut.review import build_review_queue


class ReviewQueueTests(unittest.TestCase):
    def test_build_review_queue_flags_low_confidence_segments(self) -> None:
        clips = [
            MediaClip("clip-1", "/tmp/a.mp4", "/tmp/a.proxy.mp4", "/tmp/a.wav", 10.0, 30.0, True, 1920, 1080)
        ]
        timeline = [TimelineSegment("Hook", "clip-1", 0.0, 6.0, 0.0)]
        candidates = [
            CandidateSegment("clip-1", 0.0, 6.0, 0.2, 0.4, 0.5, 0.34, "")
        ]
        transcripts = [Transcript("clip-1", "", [], 0.4)]
        audio = [
            AudioAnalysis(
                "clip-1",
                speech_segments=[(0.0, 6.0)],
                silence_segments=[],
                rms_db=-42.0,
                peak_db=-0.5,
                clarity_score=0.35,
                issues=AudioIssueFlags(clipping_risk=True, low_rms=True),
            )
        ]

        review = build_review_queue(clips, timeline, candidates, transcripts, audio, broll_overlays=[])
        self.assertGreater(review["summary"]["total"], 0)
        self.assertTrue(review["summary"]["review_required"])

    def test_build_review_queue_handles_existing_broll(self) -> None:
        clips = [MediaClip("clip-1", "/tmp/a.mp4", "/tmp/a.proxy.mp4", "", 8.0, 30.0, False, 1920, 1080)]
        timeline = [TimelineSegment("CTA", "clip-1", 0.0, 8.0, 0.0)]
        candidates = [CandidateSegment("clip-1", 0.0, 8.0, 0.6, 0.6, 0.6, 0.6, "")]
        transcripts = [Transcript("clip-1", "", [], 0.0)]
        audio = [AudioAnalysis("clip-1", [], [], None, None, 0.0, issues=AudioIssueFlags(missing_audio=True))]
        overlays = [BrollOverlay("CTA", "clip-1", "clip-1", 1.0, 3.0, 1.0, 3.0, "pacing_cutaway")]

        review = build_review_queue(clips, timeline, candidates, transcripts, audio, overlays)
        issues = [item["issue"] for item in review["findings"]]
        self.assertNotIn("missing_broll_cover", issues)


if __name__ == "__main__":
    unittest.main()
