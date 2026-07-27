from __future__ import annotations

import unittest

from ghostcut.broll import plan_broll_overlays
from ghostcut.models import CandidateSegment, MediaClip, ShotSpan, TimelineSegment


class BrollPlannerTests(unittest.TestCase):
    def test_plan_broll_overlays_generates_overlay_for_long_segment(self) -> None:
        clips = [
            MediaClip(
                id="a-roll",
                source_path="/tmp/a.mp4",
                proxy_path="/tmp/a.proxy.mp4",
                audio_path="/tmp/a.wav",
                duration_s=20.0,
                fps=30.0,
                has_audio=True,
                width=1920,
                height=1080,
            ),
            MediaClip(
                id="b-roll",
                source_path="/tmp/b.mp4",
                proxy_path="/tmp/b.proxy.mp4",
                audio_path="",
                duration_s=20.0,
                fps=30.0,
                has_audio=False,
                width=1920,
                height=1080,
            ),
        ]

        timeline_segments = [
            TimelineSegment(
                section="Hook",
                clip_id="a-roll",
                src_in_s=0.0,
                src_out_s=8.0,
                timeline_in_s=0.0,
            )
        ]

        shot_spans = [
            ShotSpan("a-roll", 0.0, 8.0, 0.7, 0.7, 0.7),
            ShotSpan("b-roll", 1.0, 10.0, 0.9, 0.8, 0.8),
        ]

        candidates = [
            CandidateSegment(
                clip_id="a-roll",
                start_s=0.0,
                end_s=8.0,
                transcript_score=0.4,
                audio_score=0.8,
                visual_score=0.7,
                total_score=0.61,
                transcript_text="welcome to this home",
            )
        ]

        overlays = plan_broll_overlays(clips, timeline_segments, shot_spans, candidates)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0].clip_id, "b-roll")
        self.assertGreater(overlays[0].timeline_out_s, overlays[0].timeline_in_s)


if __name__ == "__main__":
    unittest.main()
