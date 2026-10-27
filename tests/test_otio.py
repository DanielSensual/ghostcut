from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ghostcut.export_otio import export_otio
from ghostcut.models import BrollOverlay, MediaClip, TimelineSegment


class OTIOExportTests(unittest.TestCase):
    def test_export_otio_writes_timeline_with_v1_and_v2_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "a.mp4"
            source_b = root / "b.mp4"
            source_a.write_bytes(b"a")
            source_b.write_bytes(b"b")

            clips = [
                MediaClip("a", str(source_a), str(root / "a.proxy.mp4"), str(root / "a.wav"), 10.0, 30.0, True, 1920, 1080),
                MediaClip("b", str(source_b), str(root / "b.proxy.mp4"), "", 10.0, 30.0, False, 1920, 1080),
            ]
            timeline = [TimelineSegment("Hook", "a", 0.0, 5.0, 0.0)]
            overlays = [BrollOverlay("Hook", "a", "b", 1.0, 3.0, 1.0, 3.0, "pacing_cutaway")]

            output_path = root / "edit.otio.json"
            payload = export_otio(clips, timeline, output_path, broll_overlays=overlays)

            self.assertTrue(output_path.exists())
            self.assertEqual(payload["OTIO_SCHEMA"], "Timeline.1")

            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            tracks = loaded["tracks"]["children"]
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]["name"], "V1")
            self.assertEqual(tracks[1]["name"], "V2")

    def _clip(self, root: Path, fps: float = 30.0) -> MediaClip:
        source = root / "a.mp4"
        source.write_bytes(b"a")
        return MediaClip("a", str(source), "", "", 100.0, fps, True, 1920, 1080)

    def test_primary_track_keeps_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timeline = [TimelineSegment("Hook", "a", 0.0, 2.0, 0.0), TimelineSegment("Hook", "a", 10.0, 12.0, 5.0)]
            payload = export_otio([self._clip(root)], timeline, root / "edit.otio.json")

            children = payload["tracks"]["children"][0]["children"]
            self.assertEqual([c["OTIO_SCHEMA"] for c in children], ["Clip.2", "Gap.1", "Clip.2"])
            durations = [c["source_range"]["duration"]["value"] / c["source_range"]["duration"]["rate"] for c in children]
            self.assertEqual(durations, [2.0, 3.0, 2.0])

    def test_primary_track_rejects_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timeline = [TimelineSegment("Hook", "a", 0.0, 4.0, 0.0), TimelineSegment("Hook", "a", 10.0, 12.0, 2.0)]
            with self.assertRaises(ValueError):
                export_otio([self._clip(root)], timeline, root / "edit.otio.json")

    def test_ntsc_rate_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timeline = [TimelineSegment("Hook", "a", 0.0, 2.0, 0.0)]
            payload = export_otio([self._clip(root, fps=23.976)], timeline, root / "edit.otio.json")

            duration = payload["tracks"]["children"][0]["children"][0]["source_range"]["duration"]
            self.assertAlmostEqual(duration["rate"], 24000 / 1001, places=9)
            self.assertEqual(duration["value"], 48)


if __name__ == "__main__":
    unittest.main()
