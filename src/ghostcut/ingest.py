from __future__ import annotations

import hashlib
from pathlib import Path

from .config import ProjectPaths, SUPPORTED_EXTENSIONS, ensure_project_dirs, write_json
from .ffmpeg_tools import ToolingError, extract_media_stats, probe_media, require_ffmpeg_binaries, run_command
from .models import MediaClip, dataclass_to_dict


def classify_layer(source_path: Path) -> str:
    """Classify a clip as a-roll, b-roll, or drone based on directory and filename."""
    parts_lower = [p.lower() for p in source_path.parts]
    # Directory-based classification
    for part in parts_lower:
        if part in ("a roll", "a-roll", "aroll", "a_roll"):
            return "a-roll"
        if part in ("b roll", "b-roll", "broll", "b_roll"):
            return "b-roll"
        if "drone" in part or "dji" in part or "aerial" in part:
            return "drone"
    # Filename-based classification
    stem = source_path.stem.upper()
    if stem.startswith("DJI_") or stem.startswith("DJI "):
        return "drone"
    if stem.startswith("C9") or stem.startswith("C8") or stem.startswith("C7"):
        return "b-roll"
    return "a-roll"


def discover_media(input_dir: Path) -> list[Path]:
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    candidates = [
        path
        for path in input_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not path.name.startswith("._")  # macOS resource forks
    ]
    return sorted(candidates)


def _make_clip_id(source_path: Path, input_dir: Path) -> str:
    relative = source_path.relative_to(input_dir).as_posix().lower()
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]
    stem = source_path.stem.lower().replace(" ", "-")
    return f"{stem}-{digest}"


def _build_clip_record(source_path: Path, input_dir: Path, paths: ProjectPaths) -> MediaClip:
    probe = probe_media(source_path)
    stats = extract_media_stats(probe)
    clip_id = _make_clip_id(source_path, input_dir)
    proxy_path = paths.proxies_dir / f"{clip_id}.mp4"
    audio_path = paths.audio_dir / f"{clip_id}.wav"

    return MediaClip(
        id=clip_id,
        source_path=str(source_path.resolve()),
        proxy_path=str(proxy_path.resolve()),
        audio_path=str(audio_path.resolve()),
        duration_s=float(stats["duration_s"]),
        fps=float(stats["fps"]),
        has_audio=bool(stats["has_audio"]),
        width=int(stats["width"]),
        height=int(stats["height"]),
        layer=classify_layer(source_path),
    )


def _generate_proxy(source: Path, proxy: Path) -> None:
    proxy.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(source),
            "-vf",
            "scale='if(gt(iw,ih),1280,-2)':'if(gt(iw,ih),-2,1280)',fps=30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-vsync",
            "cfr",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(proxy),
        ]
    )


def _extract_audio(proxy: Path, output_wav: Path) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(proxy),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
    )


def ingest_media(input_dir: Path, paths: ProjectPaths, *, dry_run: bool = False) -> dict:
    require_ffmpeg_binaries()
    ensure_project_dirs(paths)

    discovered = discover_media(input_dir)
    if not discovered:
        raise FileNotFoundError(
            f"No supported media files found in {input_dir}. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    clips: list[MediaClip] = []
    warnings: list[str] = []
    dropped_clips: list[str] = []

    for source_path in discovered:
        try:
            clip = _build_clip_record(source_path, input_dir, paths)
        except ToolingError as error:
            raise RuntimeError(f"Unreadable media file: {source_path} ({error})") from error

        clips.append(clip)
        if not clip.has_audio:
            warnings.append(f"Missing audio stream: {clip.source_path}")

        if dry_run:
            continue

        try:
            _generate_proxy(Path(clip.source_path), Path(clip.proxy_path))
            if clip.has_audio:
                _extract_audio(Path(clip.proxy_path), Path(clip.audio_path))
        except ToolingError as error:
            raise RuntimeError(f"Failed during proxy/audio generation for {clip.source_path}: {error}") from error

    valid_clips = [clip for clip in clips if clip.source_path not in dropped_clips]
    if not valid_clips:
        raise RuntimeError("All discovered clips were dropped during ingest.")

    fps_values = {round(clip.fps, 3) for clip in valid_clips if clip.fps > 0}
    if len(fps_values) > 1:
        warnings.append("Mixed frame rates detected; proxies normalized to 30fps CFR.")

    payload = {
        "input_dir": str(input_dir.resolve()),
        "clips": dataclass_to_dict(valid_clips),
        "warnings": sorted(set(warnings)),
        "dropped_clips": sorted(set(dropped_clips)),
    }
    write_json(paths.manifest_file, payload)
    return payload
