from __future__ import annotations

import unittest

from ghostcut.vision_analysis import detect_duplicates


class DuplicateDetectionTests(unittest.TestCase):
    def test_similar_hashes_are_duplicates(self) -> None:
        duplicates = detect_duplicates({"a": 0xFFFF, "b": 0xFFFE, "c": 0x0000})
        self.assertEqual(duplicates, {"b": "a"})

    def test_failed_hashes_are_not_treated_as_duplicates(self) -> None:
        duplicates = detect_duplicates({"a": None, "b": None, "c": 0x1234})
        self.assertEqual(duplicates, {})


if __name__ == "__main__":
    unittest.main()
