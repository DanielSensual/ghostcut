from __future__ import annotations

import unittest

from ghostcut.ffmpeg_tools import extract_scene_change_pts, extract_silence_segments, seconds_to_fcpxml_time


class FFmpegParsingTests(unittest.TestCase):
    def test_extract_silence_segments(self) -> None:
        stderr = """
        [silencedetect @ 0x1] silence_start: 0.500
        [silencedetect @ 0x1] silence_end: 1.250 | silence_duration: 0.750
        [silencedetect @ 0x1] silence_start: 2.000
        [silencedetect @ 0x1] silence_end: 3.500 | silence_duration: 1.500
        """
        self.assertEqual(extract_silence_segments(stderr), [(0.5, 1.25), (2.0, 3.5)])

    def test_extract_silence_segments_trailing_to_eof(self) -> None:
        stderr = """
        [silencedetect @ 0x1] silence_start: 0.500
        [silencedetect @ 0x1] silence_end: 1.250 | silence_duration: 0.750
        [silencedetect @ 0x1] silence_start: 4.000
        """
        self.assertEqual(extract_silence_segments(stderr), [(0.5, 1.25)])
        self.assertEqual(
            extract_silence_segments(stderr, duration_s=5.0),
            [(0.5, 1.25), (4.0, 5.0)],
        )

    def test_extract_scene_change_pts(self) -> None:
        stderr = "showinfo pts_time:0.333\nshowinfo pts_time:1.667\n"
        self.assertEqual(extract_scene_change_pts(stderr), [0.333, 1.667])

    def test_seconds_to_fcpxml_time(self) -> None:
        self.assertEqual(seconds_to_fcpxml_time(2.0, 30.0), "60/30s")
        self.assertEqual(seconds_to_fcpxml_time(0.0, 30.0), "0/30s")


if __name__ == "__main__":
    unittest.main()
