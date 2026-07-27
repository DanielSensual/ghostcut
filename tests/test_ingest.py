from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ghostcut.ingest import discover_media


class IngestDiscoveryTests(unittest.TestCase):
    def test_discover_media_filters_and_sorts_supported_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b_clip.mp4").write_text("x", encoding="utf-8")
            (root / "a_clip.mov").write_text("x", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "z_clip.m4v").write_text("x", encoding="utf-8")
            (root / "ignore.txt").write_text("x", encoding="utf-8")

            discovered = discover_media(root)
            names = [path.name for path in discovered]
            self.assertEqual(names, ["a_clip.mov", "b_clip.mp4", "z_clip.m4v"])

    def test_discover_media_raises_for_missing_directory(self) -> None:
        missing = Path("/tmp/ghostcut-missing-dir")
        if missing.exists():
            self.skipTest("Expected missing directory path already exists unexpectedly")

        with self.assertRaises(FileNotFoundError):
            discover_media(missing)


if __name__ == "__main__":
    unittest.main()
