from __future__ import annotations

import unittest

try:
    import numpy as np

    from ghostcut.recap.beat import _coarse_bpm, _energy_window, _refine_grid, _SR
    HAS_RECAP = True
except ImportError:
    HAS_RECAP = False
    _SR = 0  # placeholder so the skipped class body still evaluates


@unittest.skipUnless(HAS_RECAP, "recap extra not installed")
class BeatAnalysisTests(unittest.TestCase):
    BPM = 128.0
    FPS = _SR / 256

    def _click_envelope(self, duration_s: float, first_beat_s: float = 0.4) -> "np.ndarray":
        env = np.zeros(int(duration_s * self.FPS))
        beat = 60.0 / self.BPM
        t = first_beat_s
        while t < duration_s:
            env[int(t * self.FPS)] = 1.0
            t += beat
        return env

    def test_coarse_bpm_finds_tempo(self) -> None:
        env = self._click_envelope(60.0)
        bpm = _coarse_bpm(env, self.FPS)
        self.assertAlmostEqual(bpm, self.BPM, delta=1.5)

    def test_coarse_bpm_doubles_half_tempo(self) -> None:
        # only every second beat present -> raw detection at 64 must double
        env = np.zeros(int(60.0 * self.FPS))
        t = 0.4
        while t < 60.0:
            env[int(t * self.FPS)] = 1.0
            t += 2 * 60.0 / self.BPM
        bpm = _coarse_bpm(env, self.FPS)
        self.assertGreaterEqual(bpm, 95.0)

    def test_refine_grid_recovers_phase(self) -> None:
        first_beat = 0.73
        env = self._click_envelope(40.0, first_beat_s=first_beat)
        bpm, grid_start = _refine_grid(env, self.FPS, self.BPM, 0.0, 40.0)
        self.assertAlmostEqual(bpm, self.BPM, delta=0.5)
        beat = 60.0 / bpm
        offset = (grid_start - first_beat) % beat
        self.assertLess(min(offset, beat - offset), 0.05)

    def test_energy_window_finds_loud_section(self) -> None:
        quiet = np.random.default_rng(7).normal(0, 100, _SR * 90)
        loud_start = 50
        samples = quiet.copy()
        samples[_SR * loud_start:_SR * (loud_start + 25)] *= 8.0
        window_start = _energy_window(samples)
        self.assertAlmostEqual(window_start, loud_start, delta=4.0)


if __name__ == "__main__":
    unittest.main()
