from __future__ import annotations

import unittest

from ghostcut.assembler import assemble_timeline
from ghostcut.models import CandidateSegment
from ghostcut.story_template import StorySection


class AssemblerTests(unittest.TestCase):
    def test_assemble_timeline_generates_non_overlapping_segments(self) -> None:
        candidates = [
            CandidateSegment("clip-1", 0.0, 6.0, 0.6, 0.8, 0.7, 0.69, "welcome home"),
            CandidateSegment("clip-1", 6.0, 12.0, 0.55, 0.8, 0.7, 0.66, "open kitchen"),
            CandidateSegment("clip-2", 0.0, 6.0, 0.5, 0.9, 0.8, 0.68, "contact us"),
        ]
        sections = [
            StorySection("Hook", 0.0, 6.0, ("welcome",)),
            StorySection("CTA", 6.0, 12.0, ("contact",)),
        ]

        timeline, summary = assemble_timeline(candidates, sections)

        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0].timeline_in_s, 0.0)
        self.assertAlmostEqual(timeline[1].timeline_in_s, 6.0, places=3)
        self.assertEqual(len(summary), 2)


if __name__ == "__main__":
    unittest.main()
