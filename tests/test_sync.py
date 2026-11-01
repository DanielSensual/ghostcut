from __future__ import annotations

import unittest

from ghostcut.sync import (
    PRECISION,
    CameraInfo,
    OffsetEstimate,
    camera_from_probe,
    choose,
    clock_estimate,
    decode_sony_ltc,
    match_words,
    parse_sony_xml,
    sync_clips,
    timecode_estimate,
    timecode_seconds,
    whisper_estimate,
)

try:
    import numpy as np

    from ghostcut.sync import SR, waveform_estimate
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

SONY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
  <LtcChangeTable tcFps="30" halfStep="true">
    <LtcChange frameCount="0" value="61553713" status="increment"/>
    <LtcChange frameCount="16619" value="68B24213" status="end"/>
  </LtcChangeTable>
  <CreationDate value="2026-10-13T11:35:22-05:00"/>
  <Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1"/>
  <Lens modelName="E 28-200mm F2.8-5.6 A071"/>
  <AcquisitionRecord><Group name="CameraUnitMetadataSet">
    <Item name="CaptureGammaEquation" value="s-log3-cine"/>
  </Group></AcquisitionRecord>
</NonRealTimeMeta>"""


def _cam(path: str, **kw) -> CameraInfo:
    return CameraInfo(path=path, fps=kw.pop("fps", 23.976), duration_s=kw.pop("duration_s", 60.0), **kw)


class MetadataTests(unittest.TestCase):
    def test_sony_ltc_is_bcd_with_flag_bits_masked(self) -> None:
        self.assertEqual(decode_sony_ltc("61553713"), "13:37:55:21")
        self.assertEqual(decode_sony_ltc("68B24213"), "13:42:32:28")
        self.assertEqual(decode_sony_ltc("garbage"), "")

    def test_sony_sidecar_names_camera_lens_gamma_and_timecode(self) -> None:
        meta = parse_sony_xml(SONY_XML)
        self.assertEqual((meta["make"], meta["model"]), ("Sony", "ILME-FX3"))
        self.assertEqual(meta["gamma"], "s-log3-cine")
        self.assertEqual(meta["timecode"], "13:37:55:21")
        self.assertEqual(meta["tc_rate"], 30.0)
        self.assertEqual(meta["captured_at"], "2026-10-13T11:35:22-05:00")

    def test_iphone_uses_its_capture_date_not_the_copy_time(self) -> None:
        probe = {"format": {"duration": "14.3", "tags": {
            "creation_time": "2026-10-29T18:00:51.000000Z",
            "com.apple.quicktime.make": "Apple", "com.apple.quicktime.model": "iPhone 16 Pro",
            "com.apple.quicktime.creationdate": "2025-10-03T22:05:50-0400"}},
            "streams": [{"codec_type": "video", "avg_frame_rate": "68800/2867", "width": 1920, "height": 1080,
                         "color_transfer": "arib-std-b67"}, {"codec_type": "audio"}]}
        cam = camera_from_probe("IMG_5554.mov", probe)
        self.assertEqual(cam.name, "Apple iPhone 16 Pro")
        self.assertEqual(cam.captured_at, "2025-10-03T22:05:50-0400")
        self.assertEqual(cam.gamma, "hlg")
        self.assertTrue(cam.has_audio)
        self.assertEqual(cam.timecode, "")

    def test_ffprobe_timecode_and_sidecar_agree_on_the_same_instant(self) -> None:
        # 59.94p clip: ffprobe counts 60 labels per second, the sidecar 30 with a half-step flag
        self.assertAlmostEqual(timecode_seconds("13:37:55:42", 60, 59.94),
                               timecode_seconds("13:37:55:21", 30, 59.94), places=6)

    def test_drop_frame_counts_real_frames(self) -> None:
        # 00:01:00;02 is the first label after the skip: frame 1800
        self.assertAlmostEqual(timecode_seconds("00:01:00;02", 30, 29.97) * 30000 / 1001, 1800, places=3)

    def test_integer_rate_timecode_is_wall_seconds(self) -> None:
        self.assertEqual(timecode_seconds("01:00:00:00", 25, 25.0), 3600.0)


class TimecodeAndClockTests(unittest.TestCase):
    def test_timecode_offset_between_jammed_cameras(self) -> None:
        a = _cam("a.mp4", timecode="10:00:00:00", tc_rate=24)
        b = _cam("b.mp4", timecode="10:00:02:12", tc_rate=24)
        est = timecode_estimate(a, b)
        self.assertAlmostEqual(est.offset_s, 2.5 * 1.001, places=4)

    def test_timecode_wraps_midnight(self) -> None:
        a = _cam("a.mp4", timecode="23:59:59:00", tc_rate=24)
        b = _cam("b.mp4", timecode="00:00:01:00", tc_rate=24)
        self.assertAlmostEqual(timecode_estimate(a, b).offset_s, 2 * 1.001, places=3)

    def test_clock_reads_zones(self) -> None:
        a = _cam("a.mp4", captured_at="2026-11-01T11:35:22-05:00")
        b = _cam("b.mov", captured_at="2026-11-01T12:35:25-0400")
        self.assertAlmostEqual(clock_estimate(a, b).offset_s, 3.0)


def _words(text: str, start: float, step: float = 0.35) -> list[tuple[str, float]]:
    return [(w, start + i * step) for i, w in enumerate(text.split())]


SCRIPT = ("i always knew we would need some kind of video editor so when this challenge came up it felt "
          "like the perfect moment to build it two sony cameras and a phone on the side and the software "
          "lined them up and picked the angle for every line")


class WhisperTests(unittest.TestCase):
    def test_unique_phrases_vote_for_the_offset(self) -> None:
        ref = _words(SCRIPT, 5.0)
        cam = _words(SCRIPT, 1.8)
        for i in (7, 15, 23, 31):  # misheard words (crosstalk) only remove votes, and split the speech into runs
            cam[i] = ("kinda", cam[i][1])
        m = match_words(ref, cam)
        self.assertAlmostEqual(m["offset_s"], 3.2, places=3)
        self.assertEqual(m["moments"], 5)
        est = whisper_estimate(ref, cam)
        self.assertEqual(est.method, "whisper")
        self.assertGreater(est.confidence, 0.5)

    def test_no_shared_words_gives_nothing(self) -> None:
        self.assertIsNone(whisper_estimate(_words("one two three four", 0), _words("five six seven eight", 0)))

    def test_one_stock_question_in_two_interviews_is_not_a_match(self) -> None:
        # real case (event interviews): both cameras heard "what are you excited about the most this weekend",
        # asked to two different guests; four overlapping phrases of one sentence must count as one moment
        question = "what are you excited about the most this weekend"
        ref = _words(f"my name is sam i live here in town {question} probably the food", 0.0)
        cam = _words(f"i am alex one of the hosts from out of state {question} seeing everybody again", 10.0)
        m = match_words(ref, cam)
        self.assertGreaterEqual(m["votes"], 4)
        self.assertEqual(m["moments"], 1)
        self.assertIsNone(whisper_estimate(ref, cam))


class ChooseTests(unittest.TestCase):
    FRAME = 1 / 23.976

    def _wave(self, offset: float, r: float, margin: float) -> OffsetEstimate:
        return OffsetEstimate("waveform", offset, 0.9, PRECISION["waveform"], detail={"r": r, "margin": margin})

    def test_clear_waveform_wins_and_free_running_timecode_is_flagged(self) -> None:
        tc = OffsetEstimate("timecode", 7322.0, 0.5, self.FRAME)
        words = OffsetEstimate("whisper", 1.30, 0.8, PRECISION["whisper"])
        pick, checks, warnings = choose([tc, words, self._wave(1.234, 0.8, 0.3)], self.FRAME)
        self.assertEqual(pick.method, "waveform")
        self.assertTrue(any("timecode disagrees" in w for w in warnings))
        self.assertTrue(any(c["a"] == "whisper" and c["b"] == "waveform" and c["agree"] for c in checks))

    def test_ambiguous_waveform_needs_backing(self) -> None:
        words = OffsetEstimate("whisper", 9.0, 0.8, PRECISION["whisper"])
        pick, _, warnings = choose([words, self._wave(1.234, 0.6, 0.01)], self.FRAME)
        self.assertEqual(pick.method, "whisper")
        self.assertTrue(warnings)

    def test_words_the_sound_contradicts_place_nothing(self) -> None:
        words = OffsetEstimate("whisper", 9.0, 0.15, PRECISION["whisper"],
                               detail={"moments": 2, "waveform_r_there": 0.21})
        pick, _, warnings = choose([words, self._wave(-60.0, 0.2, 0.0)], self.FRAME)
        self.assertIsNone(pick)
        self.assertIn("same words said at different times", warnings[0])

    def test_clock_alone_is_used_but_flagged(self) -> None:
        pick, _, warnings = choose([OffsetEstimate("clock", 3.0, 0.2, 2.0)], self.FRAME)
        self.assertEqual(pick.method, "clock")
        self.assertIn("check by eye", warnings[0])

    def test_nothing_places_nothing(self) -> None:
        pick, _, warnings = choose([], self.FRAME)
        self.assertIsNone(pick)
        self.assertTrue(warnings)


def _speech(rng: "np.random.Generator", seconds: float) -> "np.ndarray":
    """Noise gated by 50 ms 'syllables' and smoothed: an envelope with speech-like structure."""
    n = int(seconds * SR)
    gate = np.repeat(rng.random(n // 400 + 1) > 0.55, 400)[:n].astype(float)
    gate = np.convolve(gate, np.ones(200) / 200, "same")
    return rng.standard_normal(n) * gate


@unittest.skipUnless(HAS_NUMPY, "sync extra (numpy) not installed")
class WaveformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(7)
        self.event = _speech(self.rng, 120)

    def _camera(self, start_s: float, seconds: float, gain: float = 1.0, noise: float = 0.0) -> "np.ndarray":
        a = int(start_s * SR)
        x = self.event[a:a + int(seconds * SR)] * gain
        return (x + self.rng.standard_normal(len(x)) * noise).astype(np.float32)

    def test_finds_the_offset_to_the_millisecond(self) -> None:
        ref = self._camera(10.0, 90)
        cam = self._camera(3.517, 100, gain=0.25, noise=0.05)  # rolled 6.483 s before the reference
        est = waveform_estimate(ref, cam)
        self.assertAlmostEqual(est.offset_s, -6.483, delta=0.002)
        self.assertGreater(est.detail["margin"], 0.08)
        self.assertGreater(est.confidence, 0.5)

    def test_measures_clock_drift(self) -> None:
        ref = self._camera(5.0, 100)
        cam = self._camera(0.0, 110)
        ppm = 300.0  # the camera's clock runs fast: resample the same audio a little longer
        n = int(len(cam) * (1 + ppm * 1e-6))
        cam = np.interp(np.linspace(0, len(cam) - 1, n), np.arange(len(cam)), cam).astype(np.float32)
        est = waveform_estimate(ref, cam)
        self.assertAlmostEqual(est.offset_s, -5.0, delta=0.005)
        self.assertAlmostEqual(est.detail["drift"]["ppm"], ppm, delta=60)

    def test_hint_limits_the_search(self) -> None:
        # the same 20 s phrase occurs twice in the event: unguided, either copy can win; the hint picks the right one
        phrase = _speech(self.rng, 20)
        a, b = int(30 * SR), int(80 * SR)
        self.event[a:a + len(phrase)] = phrase
        self.event[b:b + len(phrase)] = phrase
        ref = self._camera(0.0, 120)
        cam = self._camera(80.0, 25)  # mostly the second copy
        hint = OffsetEstimate("whisper", 80.0, 0.8, PRECISION["whisper"])
        est = waveform_estimate(ref, cam, hint=hint)
        self.assertAlmostEqual(est.offset_s, 80.0, delta=0.002)
        self.assertEqual(est.detail["guided_by"], "whisper")

    def test_sync_clips_places_every_camera_and_chains_through_placed_ones(self) -> None:
        clips = {"a.mp4": self._camera(10.0, 90), "b.mp4": self._camera(12.25, 80, gain=0.5, noise=0.03),
                 "c.mov": self._camera(7.5, 95, gain=0.3, noise=0.05)}
        cams = [_cam(p, has_audio=True, duration_s=len(x) / SR) for p, x in clips.items()]
        results = sync_clips(cams, methods=("waveform",), audio=lambda p: clips[p])
        offsets = {r.camera.path: r.offset_s for r in results}
        self.assertEqual([r.label for r in results], ["A", "B", "C"])
        self.assertEqual(results[0].method, "reference")
        self.assertAlmostEqual(offsets["b.mp4"], 2.25, delta=0.002)
        self.assertAlmostEqual(offsets["c.mov"], -2.5, delta=0.002)


if __name__ == "__main__":
    unittest.main()
