from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ghostcut.cli import main


class IntegrationTests(unittest.TestCase):
    def test_end_to_end_run_generates_expected_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        examples = repo_root / "examples"
        source_sample = next(
            (
                path
                for path in sorted(examples.glob("*.mp4")) + sorted(examples.glob("*.mov"))
                if path.is_file()
            ),
            examples / "sample.mp4",
        )
        if not source_sample.exists():
            self.skipTest("No sample media in examples/ (optional)")

        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            self.skipTest("ffmpeg/ffprobe not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            input_dir = temp_root / "input"
            output_dir = temp_root / "project"
            input_dir.mkdir(parents=True, exist_ok=True)

            copied = input_dir / source_sample.name
            shutil.copy2(source_sample, copied)

            exit_code = main(
                [
                    "run",
                    "--input",
                    str(input_dir),
                    "--output",
                    str(output_dir),
                    "--target-duration",
                    "20",
                    "--transcriber",
                    "null",
                ]
            )
            self.assertEqual(exit_code, 0)

            expected_files = [
                output_dir / "manifest" / "media_manifest.json",
                output_dir / "analysis" / "audio_analysis.json",
                output_dir / "analysis" / "transcript.json",
                output_dir / "analysis" / "shots.json",
                output_dir / "analysis" / "segment_scores.json",
                output_dir / "timeline" / "timeline_plan.json",
                output_dir / "timeline" / "broll_plan.json",
                output_dir / "timeline" / "edit.fcpxml",
                output_dir / "timeline" / "edit.otio.json",
                output_dir / "reports" / "review_queue.json",
                output_dir / "reports" / "run_report.json",
            ]
            for path in expected_files:
                self.assertTrue(path.exists(), f"Missing artifact: {path}")

            fcpxml_text = (output_dir / "timeline" / "edit.fcpxml").read_text(encoding="utf-8")
            self.assertIn("file://", fcpxml_text)
            self.assertIn(source_sample.name, fcpxml_text)


if __name__ == "__main__":
    unittest.main()
