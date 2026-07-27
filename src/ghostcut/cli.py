from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.null_transcriber import NullTranscriber
from .adapters.openai_transcriber import OpenAITranscriber
from .assembler import assemble_timeline
from .audio_analysis import analyze_audio
from .broll import plan_broll_overlays
from .config import (
    DEFAULT_TARGET_DURATION,
    MAX_TARGET_DURATION,
    MIN_TARGET_DURATION,
    make_project_paths,
    read_json,
    write_json,
)
from .export_fcpxml import export_fcpxml
from .export_otio import export_otio
from .ffmpeg_tools import ToolingError
from .ingest import ingest_media
from .models import (
    AudioAnalysis,
    AudioIssueFlags,
    BrollOverlay,
    CandidateSegment,
    MediaClip,
    RunReport,
    ShotSpan,
    TimelineSegment,
    Transcript,
    TranscriptWord,
    VisionAnalysis,
    dataclass_to_dict,
)
from .review import build_review_queue
from .scoring import build_candidate_segments
from .shot_analysis import detect_shots
from .story_template import get_story_sections
from .vision_analysis import analyze_vision


def _load_clips(project_dir: Path) -> list[MediaClip]:
    paths = make_project_paths(project_dir)
    manifest = read_json(paths.manifest_file)
    clips_raw = manifest.get("clips", [])
    return [MediaClip(**item) for item in clips_raw]


def _load_audio_analysis(project_dir: Path) -> list[AudioAnalysis]:
    paths = make_project_paths(project_dir)
    payload = read_json(paths.audio_analysis_file)
    items = payload.get("clips", [])
    output: list[AudioAnalysis] = []
    for item in items:
        flags_payload = item.get("issues", {})
        issues = AudioIssueFlags(**flags_payload)
        output.append(
            AudioAnalysis(
                clip_id=item["clip_id"],
                speech_segments=[tuple(span) for span in item.get("speech_segments", [])],
                silence_segments=[tuple(span) for span in item.get("silence_segments", [])],
                rms_db=item.get("rms_db"),
                peak_db=item.get("peak_db"),
                clarity_score=float(item.get("clarity_score", 0.0)),
                issues=issues,
            )
        )
    return output


def _load_transcripts(project_dir: Path) -> list[Transcript]:
    paths = make_project_paths(project_dir)
    payload = read_json(paths.transcript_file)
    transcripts = []
    for item in payload.get("clips", []):
        words = [TranscriptWord(**word) for word in item.get("words", [])]
        transcripts.append(
            Transcript(
                clip_id=item["clip_id"],
                text=item.get("text", ""),
                words=words,
                average_confidence=float(item.get("average_confidence", 0.0)),
            )
        )
    return transcripts


def _load_shots(project_dir: Path) -> list[ShotSpan]:
    paths = make_project_paths(project_dir)
    payload = read_json(paths.shots_file)
    return [ShotSpan(**item) for item in payload.get("shots", [])]


def _load_candidates(project_dir: Path) -> list[CandidateSegment]:
    paths = make_project_paths(project_dir)
    payload = read_json(paths.segment_scores_file)
    return [CandidateSegment(**item) for item in payload.get("segments", [])]


def _load_timeline_segments(project_dir: Path) -> list[TimelineSegment]:
    paths = make_project_paths(project_dir)
    payload = read_json(paths.timeline_plan_file)
    return [TimelineSegment(**item) for item in payload.get("segments", [])]


def _load_broll_overlays(project_dir: Path) -> list[BrollOverlay]:
    paths = make_project_paths(project_dir)
    if not paths.broll_plan_file.exists():
        return []
    payload = read_json(paths.broll_plan_file)
    return [BrollOverlay(**item) for item in payload.get("overlays", [])]


def _validate_target_duration(value: int) -> None:
    if value < MIN_TARGET_DURATION or value > MAX_TARGET_DURATION:
        raise ValueError(
            f"Invalid target duration: {value}. Must be between {MIN_TARGET_DURATION} and {MAX_TARGET_DURATION}."
        )


def _resolve_transcriber(name: str, warnings: list[str]):
    if name == "null":
        return NullTranscriber()
    if name == "openai":
        try:
            return OpenAITranscriber()
        except Exception as error:
            warnings.append(f"OpenAI transcriber unavailable; falling back to null. {error}")
            return NullTranscriber()
    warnings.append(f"Unknown transcriber '{name}'; using null transcriber.")
    return NullTranscriber()


def command_ingest(args: argparse.Namespace) -> dict:
    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    paths = make_project_paths(output_dir)
    payload = ingest_media(input_dir, paths, dry_run=bool(args.dry_run))
    print(f"Ingest complete: {len(payload.get('clips', []))} clips")
    return payload


def _load_vision_analysis(project_dir: Path) -> list[VisionAnalysis]:
    paths = make_project_paths(project_dir)
    if not paths.vision_analysis_file.exists():
        return []
    payload = read_json(paths.vision_analysis_file)
    return [VisionAnalysis(**item) for item in payload.get("clips", [])]


def command_analyze(args: argparse.Namespace) -> dict:
    project_dir = Path(args.project).expanduser().resolve()
    paths = make_project_paths(project_dir)
    clips = _load_clips(project_dir)
    warnings: list[str] = []

    audio_payload = analyze_audio(clips)
    transcriber = _resolve_transcriber(args.transcriber, warnings)

    transcripts: list[Transcript] = []
    for clip in clips:
        if not clip.has_audio:
            transcripts.append(Transcript(clip_id=clip.id, text="", words=[], average_confidence=0.0))
            continue

        try:
            transcribed = transcriber.transcribe(clip.audio_path, args.lang)
            transcripts.append(
                Transcript(
                    clip_id=clip.id,
                    text=transcribed.text,
                    words=transcribed.words,
                    average_confidence=transcribed.average_confidence,
                )
            )
        except Exception as error:
            warnings.append(f"Transcript unavailable for {clip.id}: {error}")
            transcripts.append(Transcript(clip_id=clip.id, text="", words=[], average_confidence=0.0))

    shots = detect_shots(clips)

    # Vision analysis (keyframes + scene classification + duplicate detection)
    vision_backend = getattr(args, "vision", "null")
    vision_results = analyze_vision(clips, paths.frames_dir, vision_backend=vision_backend)
    write_json(paths.vision_analysis_file, {"clips": dataclass_to_dict(vision_results)})

    audio_warnings = []
    for item in audio_payload:
        if item.issues.missing_audio:
            audio_warnings.append(f"Missing audio stream: {item.clip_id}")
        if item.issues.clipping_risk:
            audio_warnings.append(f"Potential clipping detected: {item.clip_id}")
        if item.issues.low_rms:
            audio_warnings.append(f"Low RMS detected: {item.clip_id}")
        if item.issues.long_silence:
            audio_warnings.append(f"Long silence detected: {item.clip_id}")

    dup_count = sum(1 for v in vision_results if v.is_duplicate_of)
    write_json(paths.audio_analysis_file, {"clips": dataclass_to_dict(audio_payload)})
    write_json(paths.transcript_file, {"clips": dataclass_to_dict(transcripts)})
    write_json(paths.shots_file, {"shots": dataclass_to_dict(shots)})

    payload = {
        "audio_clips": len(audio_payload),
        "shots": len(shots),
        "vision_clips": len(vision_results),
        "duplicates_found": dup_count,
        "warnings": sorted(set(warnings + audio_warnings)),
    }
    print(f"Analyze complete: {payload['shots']} shots, {dup_count} duplicates detected")
    return payload


def command_assemble(args: argparse.Namespace) -> dict:
    target_duration = int(args.target_duration)
    _validate_target_duration(target_duration)

    project_dir = Path(args.project).expanduser().resolve()
    paths = make_project_paths(project_dir)

    clips = _load_clips(project_dir)
    transcripts = _load_transcripts(project_dir)
    audio_analysis = _load_audio_analysis(project_dir)
    shots = _load_shots(project_dir)
    vision_data = _load_vision_analysis(project_dir)

    candidates = build_candidate_segments(clips, transcripts, audio_analysis, shots, vision_data=vision_data)
    sections = get_story_sections(target_duration)
    timeline, section_summary = assemble_timeline(candidates, sections)
    broll_overlays = plan_broll_overlays(clips, timeline, shots, candidates)

    write_json(paths.segment_scores_file, {"segments": dataclass_to_dict(candidates)})
    write_json(paths.broll_plan_file, {"overlays": dataclass_to_dict(broll_overlays)})
    write_json(
        paths.timeline_plan_file,
        {
            "target_duration": target_duration,
            "sections": dataclass_to_dict(sections),
            "section_summary": section_summary,
            "segments": dataclass_to_dict(timeline),
            "broll_overlays": dataclass_to_dict(broll_overlays),
        },
    )

    total_duration = sum(item.src_out_s - item.src_in_s for item in timeline)
    payload = {
        "segments": len(timeline),
        "broll_overlays": len(broll_overlays),
        "duration_s": round(total_duration, 3),
        "section_summary": section_summary,
    }
    print(f"Assemble complete: {payload['segments']} segments / {payload['duration_s']}s")
    return payload


def command_export(args: argparse.Namespace) -> dict:
    project_dir = Path(args.project).expanduser().resolve()
    paths = make_project_paths(project_dir)
    clips = _load_clips(project_dir)
    timeline_segments = _load_timeline_segments(project_dir)
    broll_overlays = _load_broll_overlays(project_dir)
    export_fcpxml(clips, timeline_segments, paths.fcpxml_file, broll_overlays=broll_overlays)
    export_otio(clips, timeline_segments, paths.otio_file, broll_overlays=broll_overlays)
    print(f"Export complete: {paths.fcpxml_file} and {paths.otio_file}")
    return {
        "fcpxml_path": str(paths.fcpxml_file),
        "otio_path": str(paths.otio_file),
    }


def command_run(args: argparse.Namespace) -> dict:
    target_duration = int(args.target_duration)
    _validate_target_duration(target_duration)

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    paths = make_project_paths(output_dir)

    ingest_result = ingest_media(input_dir, paths, dry_run=bool(args.dry_run))
    analyze_result = command_analyze(
        argparse.Namespace(project=str(output_dir), transcriber=args.transcriber, vision=getattr(args, 'vision', 'null'), lang=args.lang)
    )
    assemble_result = command_assemble(
        argparse.Namespace(project=str(output_dir), target_duration=target_duration)
    )
    export_result = command_export(argparse.Namespace(project=str(output_dir)))
    clips_models = _load_clips(output_dir)
    transcripts = _load_transcripts(output_dir)
    audio_analysis = _load_audio_analysis(output_dir)
    timeline_segments = _load_timeline_segments(output_dir)
    candidate_segments = _load_candidates(output_dir)
    broll_overlays = _load_broll_overlays(output_dir)

    review_queue = build_review_queue(
        clips_models,
        timeline_segments,
        candidate_segments,
        transcripts,
        audio_analysis,
        broll_overlays,
    )
    write_json(paths.review_queue_file, review_queue)

    clips = ingest_result.get("clips", [])
    no_audio = [clip["id"] for clip in clips if not clip.get("has_audio")]

    report = RunReport(
        warnings=sorted(
            set(
                ingest_result.get("warnings", [])
                + analyze_result.get("warnings", [])
            )
        ),
        dropped_clips=sorted(set(ingest_result.get("dropped_clips", []))),
        no_audio_clips=no_audio,
        confidence_summary={
            "transcript_avg": 0.0,
            "audio_avg": 0.0,
            "visual_avg": 0.0,
            "segments": float(assemble_result.get("segments", 0)),
            "broll_overlays": float(assemble_result.get("broll_overlays", 0)),
            "review_findings": float(review_queue.get("summary", {}).get("total", 0)),
        },
        output_paths={
            "manifest": str(paths.manifest_file),
            "audio_analysis": str(paths.audio_analysis_file),
            "transcript": str(paths.transcript_file),
            "shots": str(paths.shots_file),
            "segment_scores": str(paths.segment_scores_file),
            "timeline_plan": str(paths.timeline_plan_file),
            "broll_plan": str(paths.broll_plan_file),
            "fcpxml": str(paths.fcpxml_file),
            "otio": str(paths.otio_file),
            "review_queue": str(paths.review_queue_file),
            "run_report": str(paths.run_report_file),
        },
    )

    # Confidence summary is computed from persisted segment scores.
    segment_payload = read_json(paths.segment_scores_file)
    segments = segment_payload.get("segments", [])
    if segments:
        report.confidence_summary["transcript_avg"] = round(
            sum(float(item.get("transcript_score", 0.0)) for item in segments) / len(segments),
            4,
        )
        report.confidence_summary["audio_avg"] = round(
            sum(float(item.get("audio_score", 0.0)) for item in segments) / len(segments),
            4,
        )
        report.confidence_summary["visual_avg"] = round(
            sum(float(item.get("visual_score", 0.0)) for item in segments) / len(segments),
            4,
        )

    write_json(paths.run_report_file, dataclass_to_dict(report))

    payload = {
        "ingest": ingest_result,
        "analyze": analyze_result,
        "assemble": assemble_result,
        "export": export_result,
        "review": review_queue,
        "report": dataclass_to_dict(report),
    }
    print("Run complete")
    return payload


def command_recap(args: argparse.Namespace) -> dict:
    try:
        from .recap import analyze_song, assemble_reel, choose_picks, slot_pattern
        from .recap.beat import BeatGrid
        from .recap.casting import cast_to_json, discover_cast
        from .recap.faces import FaceEngine
        from .recap.select import RecapCandidate, augment_with_cast, build_candidates
    except ImportError as error:
        raise RuntimeError(
            "Recap mode needs the [recap] extra: pip install -e .[recap]"
        ) from error
    from .ffmpeg_tools import extract_media_stats, probe_media

    footage_files = [str(Path(f).expanduser().resolve()) for f in args.footage]
    song = str(Path(args.song).expanduser().resolve())
    output = Path(args.output).expanduser().resolve()
    work_dir = (
        Path(args.work_dir).expanduser().resolve()
        if args.work_dir
        else output.parent / f"{output.stem}_recap_work"
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    beat_file = work_dir / "beat.json"
    candidates_file = work_dir / "candidates.json"
    cached = not args.fresh and candidates_file.exists()

    engine = FaceEngine()
    # beat analysis is per-song and cheap — always fresh so the footage
    # analysis cache can be reused across different songs
    chorus_hint = None
    if args.chorus_at:
        raw = str(args.chorus_at)
        if ":" in raw:
            minutes, seconds = raw.split(":", 1)
            chorus_hint = float(minutes) * 60 + float(seconds)
        else:
            chorus_hint = float(raw)
    grid = analyze_song(song, chorus_hint_s=chorus_hint)
    write_json(beat_file, dataclass_to_dict(grid))
    if cached:
        candidates = [RecapCandidate(**item) for item in read_json(candidates_file)["candidates"]]
        print(f"Reusing footage analysis from {work_dir} ({len(candidates)} candidates); --fresh to recompute")
    else:
        reference = []
        for clip in args.exclude_clip or []:
            clip_path = str(Path(clip).expanduser().resolve())
            vectors = engine.build_reference(clip_path)
            reference.extend(vectors)
            print(f"Exclusion reference: {len(vectors)} face samples from {Path(clip).name}")

        # analyze each event file independently; people are treated as distinct
        # per file (cluster_offset keeps ids unique) and candidates carry their
        # own source so the render pulls each clip from the right event.
        cast: list = []
        candidates: list = []
        for footage in footage_files:
            footage_duration = float(
                extract_media_stats(probe_media(Path(footage))).get("duration_s") or 0.0
            )
            file_cast = discover_cast(
                footage, footage_duration, engine=engine, reference=reference,
                cluster_offset=len(cast),
            )
            cast.extend(file_cast)
            file_candidates = build_candidates(
                footage, engine=engine, reference=reference, cast=file_cast,
                max_peaks=int(args.max_peaks),
            )
            file_candidates = augment_with_cast(file_candidates, file_cast)
            candidates.extend(file_candidates)
            print(
                f"  {Path(footage).name}: {len(file_cast)} people, "
                f"{len(file_candidates)} candidates"
            )
        excluded_cast = sum(1 for m in cast if m.excluded)
        print(
            f"Cast: {len(cast)} distinct people across {len(footage_files)} "
            f"event(s) ({excluded_cast} excluded)"
        )
        write_json(work_dir / "cast.json", {"cast": cast_to_json(cast)})
        write_json(candidates_file, {"candidates": [c.to_json() for c in candidates]})

    print(
        f"Song: {grid.bpm} BPM, bar {grid.bar_s}s, chorus downbeat at {grid.chorus_downbeat_s}s"
    )
    usable = sum(1 for c in candidates if c.usable)
    covered = len({c.cluster_id for c in candidates if c.usable and c.cluster_id is not None})
    print(
        f"Candidates: {len(candidates)} total -> {usable} usable, "
        f"{covered} distinct people covered "
        f"({sum(1 for c in candidates if c.excluded)} excluded, "
        f"{sum(1 for c in candidates if c.rejected == 'whip_pan')} whip-pans, "
        f"{sum(1 for c in candidates if c.synthetic)} cast-fill)"
    )

    strategies = [s.strip() for s in args.strategies.split(",")][: max(1, int(args.versions))]
    pattern = slot_pattern(float(args.duration), grid.bar_s)
    reports = {}
    for strategy in strategies:
        picks = choose_picks(candidates, pattern, engine=engine, strategy=strategy)
        if len(strategies) == 1:
            version_out = output
        else:
            version_out = output.with_name(f"{output.stem}_{strategy}{output.suffix}")
        write_json(
            work_dir / f"picks_{strategy}.json",
            {"pattern_bars": pattern, "strategy": strategy,
             "picks": [{"slot": i, "t": p.t, "face_score": p.face_score,
                        "motion": p.motion, "cluster_id": p.cluster_id}
                       for i, p in enumerate(picks)]},
        )
        report = assemble_reel(
            picks, pattern, grid,
            footage_path=footage_files[0], song_path=song, output_path=str(version_out),
            work_dir=work_dir / f"render_{strategy}",
            stabilize=not args.no_stabilize,
            transitions=not args.no_transitions,
        )
        distinct = len({p.cluster_id for p in picks if p.cluster_id is not None})
        print(
            f"[{strategy}] {version_out} ({report['duration_s']}s, "
            f"{report['segments']} cuts, {distinct} distinct people)"
        )
        if not report["stabilized"] and not args.no_stabilize:
            print("Warning: no vid.stab-capable ffmpeg found; rendered without stabilization.")
        reports[strategy] = report
    write_json(work_dir / "recap_report.json", reports)
    return reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GhostCut video editing infrastructure")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run ingest + analyze + assemble + export")
    run_parser.add_argument("--input", required=True, help="Input footage directory")
    run_parser.add_argument("--output", required=True, help="Output project directory")
    run_parser.add_argument("--target-duration", type=int, default=DEFAULT_TARGET_DURATION)
    run_parser.add_argument("--lang", default="en")
    run_parser.add_argument("--transcriber", default="null", choices=["null", "openai"])
    run_parser.add_argument("--vision", default="null", choices=["null", "openai"], help="Vision backend for scene classification")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=command_run)

    ingest_parser = sub.add_parser("ingest", help="Ingest media and build proxies")
    ingest_parser.add_argument("--input", required=True)
    ingest_parser.add_argument("--output", required=True)
    ingest_parser.add_argument("--dry-run", action="store_true")
    ingest_parser.set_defaults(func=command_ingest)

    analyze_parser = sub.add_parser("analyze", help="Analyze ingested proxy media")
    analyze_parser.add_argument("--project", required=True)
    analyze_parser.add_argument("--transcriber", default="null", choices=["null", "openai"])
    analyze_parser.add_argument("--vision", default="null", choices=["null", "openai"], help="Vision backend for scene classification")
    analyze_parser.add_argument("--lang", default="en")
    analyze_parser.set_defaults(func=command_analyze)

    assemble_parser = sub.add_parser("assemble", help="Assemble timeline from analysis")
    assemble_parser.add_argument("--project", required=True)
    assemble_parser.add_argument("--target-duration", type=int, default=DEFAULT_TARGET_DURATION)
    assemble_parser.set_defaults(func=command_assemble)

    export_parser = sub.add_parser("export", help="Export Final Cut Pro fcpxml")
    export_parser.add_argument("--project", required=True)
    export_parser.set_defaults(func=command_export)

    recap_parser = sub.add_parser("recap", help="Beat-synced music recap reel from event footage")
    recap_parser.add_argument(
        "--footage", required=True, action="append",
        help="Event footage video file (repeatable — one per event for a multi-event recap)",
    )
    recap_parser.add_argument("--song", required=True, help="Music track (wav/mp3)")
    recap_parser.add_argument("--output", required=True, help="Output .mp4 path")
    recap_parser.add_argument("--duration", type=float, default=20.0, help="Target reel length in seconds")
    recap_parser.add_argument(
        "--exclude-clip", action="append", default=[],
        help="Video of a person/couple to keep OUT of the edit (repeatable)",
    )
    recap_parser.add_argument("--max-peaks", type=int, default=60, help="Motion peak candidates to score")
    recap_parser.add_argument("--work-dir", default=None, help="Artifacts directory (default: beside output)")
    recap_parser.add_argument(
        "--versions", type=int, default=1,
        help="How many differently-edited versions to render (default 1)",
    )
    recap_parser.add_argument(
        "--strategies", default="cast,classic,energy",
        help="Comma-ordered selection strategies used when --versions > 1",
    )
    recap_parser.add_argument(
        "--fresh", action="store_true",
        help="Recompute analysis even when cached artifacts exist in the work dir",
    )
    recap_parser.add_argument(
        "--chorus-at", default=None,
        help="Pin the section start (seconds or m:ss) instead of auto-detecting the chorus",
    )
    recap_parser.add_argument("--no-stabilize", action="store_true")
    recap_parser.add_argument("--no-transitions", action="store_true")
    recap_parser.set_defaults(func=command_recap)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError, ToolingError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
