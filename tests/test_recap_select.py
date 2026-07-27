from __future__ import annotations

import unittest

try:
    from ghostcut.recap.render import _beat_offset, _fade_plan
    from ghostcut.recap.select import RecapCandidate, choose_picks, slot_pattern
    HAS_RECAP = True
except ImportError:
    HAS_RECAP = False


class _StubEngine:
    """Face engine stand-in: every pair of embeddings is dissimilar."""

    def cosine(self, a, b) -> float:
        return 0.0


@unittest.skipUnless(HAS_RECAP, "recap extra not installed")
class SlotPatternTests(unittest.TestCase):
    def test_pattern_sums_to_requested_bars(self) -> None:
        for duration, bar in ((20.0, 1.812), (30.0, 1.812), (15.0, 2.0), (60.0, 1.85)):
            pattern = slot_pattern(duration, bar)
            self.assertAlmostEqual(sum(pattern), round(duration / bar), delta=0.01)

    def test_pattern_shape(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        self.assertEqual(pattern[:3], [1.0, 1.0, 2.0])   # intro + chorus hit
        self.assertEqual(pattern[-1], 2.0)               # closer
        self.assertIn(0.5, pattern)                      # quick cuts present

    def test_minimum_pattern(self) -> None:
        pattern = slot_pattern(5.0, 1.8)                 # absurdly short request
        self.assertGreaterEqual(sum(pattern), 7)


@unittest.skipUnless(HAS_RECAP, "recap extra not installed")
class ChoosePicksTests(unittest.TestCase):
    def _candidates(self, count: int) -> list:
        return [
            RecapCandidate(t=10.0 * (i + 1), motion=50.0 - i, face_score=0.5)
            for i in range(count)
        ]

    def test_one_pick_per_slot_chorus_gets_best(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        candidates = self._candidates(20)
        picks = choose_picks(candidates, pattern, engine=_StubEngine())
        self.assertEqual(len(picks), len(pattern))
        chorus_slot = pattern.index(2.0)
        best = max(candidates, key=lambda c: c.score(50.0))
        self.assertIs(picks[chorus_slot], best)

    def test_excluded_candidates_never_picked(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        candidates = self._candidates(20)
        banned = [candidates[0], candidates[3]]
        for candidate in banned:
            candidate.excluded = True
        picks = choose_picks(candidates, pattern, engine=_StubEngine())
        for candidate in banned:
            self.assertFalse(any(pick is candidate for pick in picks))

    def test_pool_too_small_raises(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        with self.assertRaises(ValueError):
            choose_picks(self._candidates(3), pattern, engine=_StubEngine())

    def test_whip_pans_readmitted_when_pool_short(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        candidates = self._candidates(len(pattern))
        candidates[-1].rejected = "whip_pan"
        picks = choose_picks(candidates, pattern, engine=_StubEngine())
        self.assertEqual(len(picks), len(pattern))

    def test_candidates_preserve_source(self) -> None:
        # picks from a merged multi-event pool keep their per-file source
        pattern = slot_pattern(20.0, 1.812)
        candidates = self._candidates(20)
        for i, candidate in enumerate(candidates):
            candidate.source = "event_a.mov" if i % 2 == 0 else "event_b.mov"
            candidate.cluster_id = i
        picks = choose_picks(candidates, pattern, engine=_StubEngine(), strategy="cast")
        self.assertTrue(all(p.source in ("event_a.mov", "event_b.mov") for p in picks))
        self.assertEqual(picks[0].to_json()["source"], picks[0].source)

    def test_cast_strategy_maximizes_distinct_clusters(self) -> None:
        pattern = slot_pattern(20.0, 1.812)
        # 20 candidates but only clusters 0..4 among the strongest; weaker
        # candidates carry fresh clusters that coverage should still reach
        candidates = self._candidates(20)
        for i, candidate in enumerate(candidates):
            candidate.cluster_id = i % 5 if i < 10 else i
        picks = choose_picks(candidates, pattern, engine=_StubEngine(), strategy="cast")
        clusters = [p.cluster_id for p in picks]
        self.assertEqual(len(set(clusters)), len(clusters))  # no repeats at all


@unittest.skipUnless(HAS_RECAP, "recap extra not installed")
class RenderPlanTests(unittest.TestCase):
    def test_fade_plan_boundaries(self) -> None:
        plan = _fade_plan(12, transitions=True)
        self.assertEqual(sorted(plan), [1, 10, 11])
        self.assertEqual(plan[11], 0.5)

    def test_fade_plan_disabled(self) -> None:
        self.assertEqual(_fade_plan(12, transitions=False), {})
        self.assertEqual(_fade_plan(3, transitions=True), {})

    def test_beat_offset(self) -> None:
        self.assertEqual(_beat_offset(0.5), 1.0)
        self.assertEqual(_beat_offset(1.0), 2.0)
        self.assertEqual(_beat_offset(2.0), 2.0)


if __name__ == "__main__":
    unittest.main()
