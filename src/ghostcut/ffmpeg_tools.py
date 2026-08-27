from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


class ToolingError(RuntimeError):
    """Raised when ffmpeg tooling fails."""


def require_ffmpeg_binaries() -> None:
    missing = [binary for binary in ("ffmpeg", "ffprobe") if shutil.which(binary) is None]
    if missing:
        joined = ", ".join(missing)
        raise ToolingError(f"Missing required binaries in PATH: {joined}")


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and process.returncode != 0:
        stderr = process.stderr.strip() or "(no stderr)"
        joined = " ".join(command)
        raise ToolingError(f"Command failed ({process.returncode}): {joined}\n{stderr}")
    return process


def probe_media(path: Path) -> dict:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def parse_fraction(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip()
    if not value or value == "0/0":
        return 0.0
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0
    numerator, denominator = value.split("/", 1)
    try:
        numerator_f = float(numerator)
        denominator_f = float(denominator)
    except ValueError:
        return 0.0
    if denominator_f == 0:
        return 0.0
    return numerator_f / denominator_f


def coerce_float(value: str | int | float | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return 0.0


def extract_media_stats(probe: dict) -> dict[str, float | int | bool]:
    streams = probe.get("streams", [])
    format_payload = probe.get("format", {})
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = parse_fraction((video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate"))
    width = int((video_stream or {}).get("width") or 0)
    height = int((video_stream or {}).get("height") or 0)
    duration_s = coerce_float(format_payload.get("duration"))
    bitrate = coerce_float(format_payload.get("bit_rate"))

    return {
        "duration_s": duration_s,
        "fps": fps,
        "width": width,
        "height": height,
        "has_audio": bool(audio_stream),
        "bitrate": bitrate,
    }


def extract_silence_segments(
    stderr: str, duration_s: float | None = None
) -> list[tuple[float, float]]:
    starts = [float(value) for value in re.findall(r"silence_start:\s*([\d\.]+)", stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([\d\.]+)", stderr)]
    segments: list[tuple[float, float]] = []
    for idx, start in enumerate(starts):
        if idx < len(ends):
            end = ends[idx]
        elif duration_s is not None and duration_s > start:
            # silencedetect omits silence_end when the file ends in silence
            end = duration_s
        else:
            continue
        if end > start:
            segments.append((start, end))
    return segments


def extract_scene_change_pts(stderr: str) -> list[float]:
    return [float(value) for value in re.findall(r"pts_time:([\d\.]+)", stderr)]


def seconds_to_fcpxml_time(seconds: float, fps: float) -> str:
    safe_fps = int(round(fps)) if fps and fps > 0 else 30
    frames = max(0, int(round(seconds * safe_fps)))
    return f"{frames}/{safe_fps}s"
