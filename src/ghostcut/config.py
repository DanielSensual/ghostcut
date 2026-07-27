from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".mov", ".mp4", ".m4v"}
DEFAULT_TARGET_DURATION = 90
MIN_TARGET_DURATION = 20
MAX_TARGET_DURATION = 300
MIN_SEGMENT_DURATION = 2.0
MAX_SEGMENT_DURATION = 8.0


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path
    manifest_dir: Path
    proxies_dir: Path
    audio_dir: Path
    analysis_dir: Path
    timeline_dir: Path
    reports_dir: Path

    manifest_file: Path
    transcript_file: Path
    shots_file: Path
    segment_scores_file: Path
    audio_analysis_file: Path
    timeline_plan_file: Path
    broll_plan_file: Path
    fcpxml_file: Path
    otio_file: Path
    run_report_file: Path
    review_queue_file: Path
    vision_analysis_file: Path
    frames_dir: Path


@dataclass(frozen=True, slots=True)
class RunSettings:
    input_dir: Path
    output_dir: Path
    target_duration: int = DEFAULT_TARGET_DURATION
    language: str = "en"
    transcriber: str = "null"
    dry_run: bool = False


def make_project_paths(root: Path) -> ProjectPaths:
    manifest_dir = root / "manifest"
    proxies_dir = root / "proxies"
    audio_dir = root / "audio"
    analysis_dir = root / "analysis"
    timeline_dir = root / "timeline"
    reports_dir = root / "reports"

    return ProjectPaths(
        root=root,
        manifest_dir=manifest_dir,
        proxies_dir=proxies_dir,
        audio_dir=audio_dir,
        analysis_dir=analysis_dir,
        timeline_dir=timeline_dir,
        reports_dir=reports_dir,
        manifest_file=manifest_dir / "media_manifest.json",
        transcript_file=analysis_dir / "transcript.json",
        shots_file=analysis_dir / "shots.json",
        segment_scores_file=analysis_dir / "segment_scores.json",
        audio_analysis_file=analysis_dir / "audio_analysis.json",
        timeline_plan_file=timeline_dir / "timeline_plan.json",
        broll_plan_file=timeline_dir / "broll_plan.json",
        fcpxml_file=timeline_dir / "edit.fcpxml",
        otio_file=timeline_dir / "edit.otio.json",
        run_report_file=reports_dir / "run_report.json",
        review_queue_file=reports_dir / "review_queue.json",
        vision_analysis_file=analysis_dir / "vision_analysis.json",
        frames_dir=root / "frames",
    )


def ensure_project_dirs(paths: ProjectPaths) -> None:
    for directory in (
        paths.root,
        paths.manifest_dir,
        paths.proxies_dir,
        paths.audio_dir,
        paths.analysis_dir,
        paths.timeline_dir,
        paths.reports_dir,
        paths.frames_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))
