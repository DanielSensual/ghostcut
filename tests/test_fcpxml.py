from __future__ import annotations

import shutil
import subprocess
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

    def test_export_fcpxml_section_change_references_dissolve(self) -> None:
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
                TimelineSegment("Hook", "clip-1", 1.0, 5.0, 0.0),
                TimelineSegment("CTA", "clip-1", 5.0, 8.0, 4.0),
            ]

            output_path = root / "edit.fcpxml"
            export_fcpxml(clips, timeline, output_path)
            tree = ET.parse(output_path)
            root_elem = tree.getroot()
            resources = root_elem.find("resources")
            effects = resources.findall("effect") if resources is not None else []
            self.assertTrue(any(effect.attrib.get("id") == "dissolve1" for effect in effects))
            spine = root_elem.find("./library/event/project/sequence/spine")
            transitions = spine.findall("transition") if spine is not None else []
            self.assertEqual(len(transitions), 1)
            self.assertNotIn("ref", transitions[0].attrib)
            filters = transitions[0].findall("filter-video")
            self.assertEqual([f.attrib.get("ref") for f in filters], ["dissolve1"])
            _assert_valid_fcpxml(self, output_path)

    def test_export_fcpxml_keeps_ntsc_rates_and_source_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            a, b = root / "a.mov", root / "b.mov"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            clips = [
                MediaClip("a", str(a), "", "", 60.0, 23.976023976, True, 3840, 2160),
                MediaClip("b", str(b), "", "", 60.0, 23.976023976, True, 3840, 2160),
                MediaClip("c", str(b), "", "", 60.0, 59.94005994, True, 1920, 1080),
            ]
            timeline = [
                TimelineSegment("Hook", "a", 0.0, 2.0, 0.0),
                TimelineSegment("Hook", "b", 0.0, 2.0, 2.0),
                TimelineSegment("Hook", "c", 0.0, 2.0, 4.0),
            ]
            output_path = root / "edit.fcpxml"
            export_fcpxml(clips, timeline, output_path)
            root_elem = ET.parse(output_path).getroot()

            formats = {f.attrib["id"]: f.attrib for f in root_elem.findall("./resources/format")}
            self.assertEqual(formats["r1"]["frameDuration"], "1001/24000s")
            assets = {a.attrib["uid"]: a.attrib for a in root_elem.findall("./resources/asset")}
            # 4K sources keep their own 4K 23.976 format (the sequence is 1080p); the 59.94 source gets another
            self.assertEqual(assets["a"]["format"], assets["b"]["format"])
            self.assertEqual(formats[assets["a"]["format"]]["width"], "3840")
            self.assertEqual(formats[assets["a"]["format"]]["frameDuration"], "1001/24000s")
            self.assertNotEqual(assets["c"]["format"], assets["a"]["format"])
            self.assertEqual(formats[assets["c"]["format"]]["frameDuration"], "1001/60000s")
            spine_clips = root_elem.findall("./library/event/project/sequence/spine/asset-clip")
            self.assertEqual(spine_clips[1].attrib["offset"], "48048/24000s")
            _assert_valid_fcpxml(self, output_path)


_FCPXML_DTD = Path(
    "/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_11.dtd"
)


def _assert_valid_fcpxml(test: unittest.TestCase, path: Path) -> None:
    """Validate against Final Cut's own DTD when Final Cut and xmllint are installed."""
    if not _FCPXML_DTD.is_file() or shutil.which("xmllint") is None:
        return
    result = subprocess.run(
        ["xmllint", "--noout", "--dtdvalid", _FCPXML_DTD.as_uri(), str(path)],
        capture_output=True,
        text=True,
    )
    test.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
