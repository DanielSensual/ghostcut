from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ghostcut.reframe import (
    Box,
    ReframePlan,
    ShotFrame,
    build_shots,
    choose_subject,
    crop_size,
    crop_x,
    cuts_from_angles,
    estimate_usd,
    fill_gaps,
    frame_crop_xs,
    plan_centres,
    render_command,
    samples_in,
    sendcmd_script,
    smooth,
)

W, H = 1920, 1080


def person(sample: int, oid: str, cx: float, height: float = 700, width: float = 400) -> Box:
    top = H - height
    return Box(sample, oid, (cx - width / 2, top, cx + width / 2, H))


class GeometryTests(unittest.TestCase):
    def test_crop_size_is_even_nine_by_sixteen(self) -> None:
        self.assertEqual(crop_size(1920, 1080), (608, 1080))
        self.assertEqual(crop_size(3840, 2160), (1216, 2160))

    def test_crop_x_stays_inside_the_frame(self) -> None:
        self.assertEqual(crop_x(960, W, 608), 656)
        self.assertEqual(crop_x(10, W, 608), 0)
        self.assertEqual(crop_x(1915, W, 608), W - 608)


class ShotTests(unittest.TestCase):
    def test_build_shots_covers_the_cut_and_drops_near_duplicates(self) -> None:
        shots = build_shots([5.0, 5.1, 12.0, 59.95], 60.0)
        self.assertEqual([(s.start_s, s.end_s) for s in shots], [(0.0, 5.0), (5.0, 12.0), (12.0, 60.0)])

    def test_cuts_from_angles_skips_the_opening_shot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "angles.json"
            path.write_text(json.dumps({"shots": [{"start_s": 0.0}, {"start_s": 15.98}, {"start_s": 23.15}]}))
            self.assertEqual(cuts_from_angles(path), [15.98, 23.15])

    def test_samples_in_maps_time_to_proxy_frames(self) -> None:
        self.assertEqual(samples_in(ShotFrame(0, 0.0, 2.0), 5, 100), range(0, 10))
        self.assertEqual(samples_in(ShotFrame(1, 2.0, 3.1), 5, 100), range(10, 16))
        self.assertEqual(samples_in(ShotFrame(2, 19.0, 30.0), 5, 100), range(95, 100))


class SubjectTests(unittest.TestCase):
    def test_follows_the_most_present_largest_person(self) -> None:
        boxes = [person(s, "speaker", 600) for s in range(10)] + [person(s, "walker", 1500, 500) for s in (2, 3)]
        subject, presence, _ = choose_subject(boxes, range(10), H)
        self.assertEqual(subject, "speaker")
        self.assertEqual(presence, 1.0)

    def test_passer_by_alone_is_not_followed(self) -> None:
        boxes = [person(s, "walker", 1500) for s in (1, 2)]
        subject, _, reason = choose_subject(boxes, range(10), H)
        self.assertIsNone(subject)
        self.assertIn("20%", reason)

    def test_tiny_background_figure_is_not_followed(self) -> None:
        boxes = [person(s, "far", 1500, height=120, width=50) for s in range(10)]
        subject, _, reason = choose_subject(boxes, range(10), H)
        self.assertIsNone(subject)
        self.assertIn("tall", reason)

    def test_empty_shot_has_no_subject(self) -> None:
        self.assertEqual(choose_subject([], range(0), H)[0], None)


class PathTests(unittest.TestCase):
    def test_fill_gaps_interpolates_and_holds_the_ends(self) -> None:
        self.assertEqual(fill_gaps([None, 100.0, None, 300.0, None], 960), [100.0, 100.0, 200.0, 300.0, 300.0])
        self.assertEqual(fill_gaps([None, None], 960), [960, 960])

    def test_smooth_has_no_lag_and_keeps_a_constant(self) -> None:
        for value in smooth([500.0] * 20, 3):
            self.assertAlmostEqual(value, 500.0, places=6)
        ramp = [float(i) for i in range(41)]
        self.assertAlmostEqual(smooth(ramp, 3)[20], 20.0, places=6)

    def test_smooth_removes_jitter(self) -> None:
        jitter = [600.0 + (25 if i % 2 else -25) for i in range(30)]
        out = smooth(jitter, 3)
        self.assertLess(max(out[5:25]) - min(out[5:25]), 5)

    def test_each_shot_is_planned_on_its_own(self) -> None:
        shots = build_shots([2.0], 4.0)
        boxes = [person(s, "a", 500) for s in range(10)] + [person(s, "b", 1400) for s in range(10, 20)]
        centres = plan_centres(boxes, shots, 20, width=W, height=H)
        self.assertTrue(all(abs(c - 500) < 1e-6 for c in centres[:10]))
        self.assertTrue(all(abs(c - 1400) < 1e-6 for c in centres[10:]))
        self.assertEqual([s.method for s in shots], ["subject", "subject"])

    def test_shot_without_subject_is_centred(self) -> None:
        shots = build_shots([], 2.0)
        centres = plan_centres([], shots, 10, width=W, height=H)
        self.assertEqual(centres, [W / 2] * 10)
        self.assertEqual(shots[0].method, "centre")


class FrameTests(unittest.TestCase):
    def plan(self, centres: list[float], cuts: list[float], duration: float) -> ReframePlan:
        return ReframePlan("cut.mp4", W, H, 30.0, duration, 608, 1080, build_shots(cuts, duration), centres)

    def test_crop_jumps_at_a_cut_and_never_inside_a_shot(self) -> None:
        plan = self.plan([500.0] * 10 + [1400.0] * 10, [2.0], 4.0)
        xs = frame_crop_xs(plan, 120)
        self.assertEqual(xs[59], crop_x(500, W, 608))
        self.assertEqual(xs[60], crop_x(1400, W, 608))
        steps = [abs(b - a) for a, b in zip(xs, xs[1:])]
        self.assertEqual(sum(1 for s in steps if s > 50), 1)

    def test_crop_interpolates_between_samples(self) -> None:
        plan = self.plan([400.0, 500.0, 600.0, 700.0, 800.0], [], 1.0)
        xs = frame_crop_xs(plan, 30)
        self.assertEqual(xs[0], crop_x(400, W, 608))
        self.assertEqual(xs[3], crop_x(450, W, 608))

    def test_sendcmd_only_writes_moves(self) -> None:
        text = sendcmd_script([10, 10, 12, 12, 12, 30], 30.0)
        self.assertEqual(text.splitlines(), ["0.000000 crop@rf x 10;", "0.066667 crop@rf x 12;",
                                             "0.166667 crop@rf x 30;"])

    def test_render_command_crops_scales_and_keeps_audio(self) -> None:
        plan = self.plan([960.0], [], 1.0)
        cmd = render_command(Path("in.mp4"), Path("out.mp4"), Path("/tmp/crop.cmd"), plan, 656)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("sendcmd=f='/tmp/crop.cmd'", vf)
        self.assertIn("crop@rf=w=608:h=1080:x=656:y=0", vf)
        self.assertIn("scale=1080:1920", vf)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")


class CostTests(unittest.TestCase):
    def test_estimate_counts_overlaps_and_minimums(self) -> None:
        self.assertEqual(estimate_usd(60.0), 0.07)    # 7 windows: 300 + 35 frames, raised to the 350-frame minimum
        self.assertLess(estimate_usd(420.0), 0.5)     # a 7-minute cut stays well under a dollar


if __name__ == "__main__":
    unittest.main()
