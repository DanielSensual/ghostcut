from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from ghostcut.export_fcpxml import export_fcpxml
from ghostcut.models import MediaClip, TimelineSegment


class FCPXMLTests(unittest.TestCase):
    def test_export_fcpxml_contains_assets_and_spine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.mp4"
            source_path.write_bytes(b"fake")

            clips = [
                MediaClip(
                    id="clip-1",
                    source_path=str(source_path.resolve()),
                    proxy_path=str((root / "proxy.mp4").resolve()),
                    audio_path=str((root / "audio.wav").resolve()),
                    duration_s=10.0,
                    fps=30.0,
                    has_audio=True,
                    width=1280,
                    height=720,
                )
            ]
            timeline = [
                TimelineSegment(
                    section="Hook",
                    clip_id="clip-1",
                    src_in_s=1.0,
                    src_out_s=5.0,
                    timeline_in_s=0.0,
                )
            ]

            output_path = root / "edit.fcpxml"
            xml_string = export_fcpxml(clips, timeline, output_path)
            self.assertTrue(output_path.exists())
            self.assertIn("<fcpxml", xml_string)

            tree = ET.parse(output_path)
            root_elem = tree.getroot()
            resources = root_elem.find("resources")
            self.assertIsNotNone(resources)
            assets = resources.findall("asset") if resources is not None else []
            self.assertEqual(len(assets), 1)
            media_reps = assets[0].findall("media-rep")
            self.assertEqual(len(media_reps), 1)
            self.assertTrue(media_reps[0].attrib["src"].startswith("file://"))

            spine = root_elem.find("./library/event/project/sequence/spine")
            self.assertIsNotNone(spine)
            clips_nodes = spine.findall("asset-clip") if spine is not None else []
            self.assertEqual(len(clips_nodes), 1)


if __name__ == "__main__":
    unittest.main()
