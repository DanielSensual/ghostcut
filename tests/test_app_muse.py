from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import muse  # noqa: E402


def project() -> dict:
    words = [["The", 10.0, 10.2], ["home", 10.2, 10.6], ["is", 10.7, 10.8], ["almost", 10.9, 11.3],
             ["2,400", 11.3, 12.0], ["square", 12.0, 12.4], ["feet,", 12.4, 12.9], ["with", 13.6, 13.8],
             ["three", 13.8, 14.1], ["to", 14.1, 14.2], ["four", 14.2, 14.6], ["bedrooms.", 14.6, 15.3]]
    return {
        "title": "Test tour", "duration": 60.0,
        "sections": [{"name": "Tour", "t": 0.0, "d": 60.0}],
        "talk": [{"id": "t1", "t": 0.0, "d": 60.0, "topic": "exterior", "src": "A.MP4", "in": 0.0}],
        "broll": [{"id": "b1", "tag": "R1", "src": "R1", "t": 5.0, "d": 3.0, "topic": "", "reason": ""},
                  {"id": "b2", "tag": "R2", "src": "R2", "t": 20.0, "d": 3.0, "topic": "", "reason": ""}],
        "cards": [{"id": "c1", "t": 11.3, "d": 4.0, "caption": "MODEL HOME", "lines": ["ALMOST 2,400 SQ FT"]}],
        "transcript": [{"t": 10.0, "e": 15.3, "text": " ".join(w[0] for w in words), "w": words}],
        "library": [{"tag": "R1", "used": True}, {"tag": "R2", "used": True},
                    {"tag": "D9", "used": False, "what": "drone over the lake", "best": [0.0, 6.0]},
                    {"tag": "V0", "used": False, "what": "people by the fence", "skip": True, "use": "skip: people"},
                    {"tag": "W3", "used": False, "what": "promo page"}],
        "neverUse": {"W3": "misleading promo"},
        "findings": [],
    }


def op(o: str, **kw) -> dict:
    return {"op": o, "target": "", "t": -1, "d": -1, "src": "", "lines": [], "caption": "", "why": "test", **kw}


class NumbersTests(unittest.TestCase):
    def test_reads_digits_words_and_thousands(self) -> None:
        self.assertEqual(muse.numbers_in("ALMOST 2,400 SQ FT"), {2400.0})
        self.assertEqual(muse.numbers_in("3-4 BEDROOMS"), {3.0, 4.0})
        self.assertEqual(muse.numbers_in("three to four bedrooms"), {3.0, 4.0})
        self.assertIn(512000.0, muse.numbers_in("about 512k"))


class CheckTests(unittest.TestCase):
    def test_card_numbers_must_be_said_nearby(self) -> None:
        p = project()
        ok = muse.validate(p, [op("add_card", t=13.8, d=3.0, lines=["3-4 BEDROOMS"])])[0]
        self.assertFalse(ok["blocked"])
        bad = muse.validate(p, [op("add_card", t=13.8, d=3.0, lines=["6 BEDROOMS"])])[0]
        self.assertTrue(bad["blocked"])
        self.assertIn("6", next(c["text"] for c in bad["checks"] if not c["ok"]))

    def test_cut_must_start_and_end_between_words(self) -> None:
        p = project()
        inside = muse.validate(p, [op("cut_span", t=11.5, d=1.0)])[0]
        self.assertTrue(inside["blocked"])
        between = muse.validate(p, [op("cut_span", t=12.95, d=0.6)])[0]
        self.assertFalse(between["blocked"])

    def test_inserts_keep_their_gap(self) -> None:
        p = project()
        touching = muse.validate(p, [op("move_broll", target="b2", t=8.02, d=2.0)])[0]
        self.assertTrue(touching["blocked"])
        clear = muse.validate(p, [op("move_broll", target="b2", t=8.2, d=2.0)])[0]
        self.assertFalse(clear["blocked"])

    def test_skip_and_never_use_shots_are_blocked(self) -> None:
        p = project()
        self.assertTrue(muse.validate(p, [op("swap_broll", target="b1", src="V0")])[0]["blocked"])
        self.assertTrue(muse.validate(p, [op("swap_broll", target="b1", src="W3")])[0]["blocked"])
        self.assertTrue(muse.validate(p, [op("swap_broll", target="b1", src="NOPE")])[0]["blocked"])
        self.assertFalse(muse.validate(p, [op("swap_broll", target="b1", src="D9")])[0]["blocked"])

    def test_unknown_target_is_blocked(self) -> None:
        self.assertTrue(muse.validate(project(), [op("drop_broll", target="b99")])[0]["blocked"])
        self.assertTrue(muse.validate(project(), [op("drop_card", target="b1")])[0]["blocked"])


class ApplyTests(unittest.TestCase):
    def test_edits_are_pending_and_reversible(self) -> None:
        base = project()
        edits = [{"id": "e1", "ops": [op("drop_broll", target="b1"), op("swap_broll", target="b2", src="D9")]},
                 {"id": "e2", "ops": [op("add_card", t=13.8, d=3.0, lines=["3-4 BEDROOMS"])]}]
        p = muse.apply(base, edits)
        self.assertEqual(p["pending"], 3)
        self.assertEqual(p["broll"][0]["pending"], "drop")
        self.assertEqual((p["broll"][1]["tag"], p["broll"][1]["was_tag"]), ("D9", "R2"))
        self.assertEqual(p["cards"][-1]["pending"], "add")
        self.assertEqual(muse.apply(base, edits[:1])["pending"], 2)  # undo = apply without the edit
        self.assertNotIn("pending", base["broll"][0])  # the base cut is never modified

    def test_context_lists_items_by_id(self) -> None:
        ctx = muse.context(project(), 11.0)
        self.assertIn("b1 BROLL 5.0-8.0 R1", ctx)
        self.assertIn('c1 CARD 11.3-15.3 "ALMOST 2,400 SQ FT"', ctx)
        self.assertIn("V0 [0.0-0.0] people by the fence · SKIP", ctx)


if __name__ == "__main__":
    unittest.main()
