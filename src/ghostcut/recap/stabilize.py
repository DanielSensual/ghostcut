"""Per-segment vid.stab stabilization with codec isolation.

vid.stab (libvidstab) is not in Homebrew's default ffmpeg, so a static build
is used for the two vidstab passes. Static macOS builds are x86_64 and run
under Rosetta, where their h264 codecs intermittently corrupt frames — so the
static binary only ever sees uncompressed y4m pipes; the native ffmpeg does
all decoding and encoding.

Segments are stabilized slightly oversized, then center-cropped, with the
correction capped (maxshift) inside the crop margin so stabilization borders
can never enter the visible frame.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..ffmpeg_tools import ToolingError

_CACHED_STATIC = Path.home() / ".cache" / "ghostcut" / "bin" / "ffmpeg-vidstab"

# proven parameters for handheld dance-floor footage (60fps source)
OVERSIZE = 1.25
SMOOTHING = 30
MAXSHIFT = 110
MAXANGLE = 0.05


def find_vidstab_ffmpeg() -> str | None:
    """A binary whose filter set includes vidstab, or None."""
    override = os.environ.get("GHOSTCUT_VIDSTAB_FFMPEG")
    candidates = [override, "ffmpeg", str(_CACHED_STATIC)]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if not resolved:
            continue
        try:
            result = subprocess.run(
                [resolved, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "vidstabtransform" in result.stdout:
            return resolved
    return None


def _oversize_dims(width: int, height: int) -> tuple[int, int]:
    return (round(width * OVERSIZE / 2) * 2, round(height * OVERSIZE / 2) * 2)


def extract_segment(
    source: str, start: float, duration: float, out_path: str,
    *, width: int, height: int, oversize: bool = True,
) -> None:
    """Native-ffmpeg extraction; oversized when stabilization will follow.

    oversize=False extracts at the exact reel frame — for static/synthetic
    sources (title cards, flyers) where the stabilization crop would zoom in
    and cut off text.
    """
    big_w, big_h = _oversize_dims(width, height) if oversize else (width, height)
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration + 0.3:.3f}", "-i", source,
        "-vf",
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},setsar=1",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "14", out_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolingError(f"Segment extraction failed: {result.stderr.strip()}")


def stabilize_segment(
    raw_path: str,
    out_path: str,
    *,
    stab_ffmpeg: str,
    width: int,
    height: int,
    duration: float,
    fps: int = 30,
) -> None:
    """Two-pass vid.stab; falls back to a plain conform when analysis fails."""
    work = Path(raw_path).parent
    raw_name = Path(raw_path).name
    out_name = Path(out_path).name
    trf_name = Path(raw_path).with_suffix(".trf").name
    quoted_stab = shlex.quote(stab_ffmpeg)

    # Run against basenames in the work dir so spaces/colons in the full path
    # cannot break the shell pipeline or the vidstab filter argument.
    detect = (
        f"ffmpeg -v error -i {shlex.quote(raw_name)} -f yuv4mpegpipe - | "
        f"{quoted_stab} -v error -threads 1 -i - "
        f"-vf vidstabdetect=shakiness=8:accuracy=15:mincontrast=0.25:result={trf_name} "
        f"-f null -"
    )
    transform = (
        f"ffmpeg -v error -i {shlex.quote(raw_name)} -f yuv4mpegpipe - | "
        f"{quoted_stab} -v error -threads 1 -i - "
        f'-vf "vidstabtransform=input={trf_name}:smoothing={SMOOTHING}:maxshift={MAXSHIFT}:'
        f"maxangle={MAXANGLE}:optzoom=0:crop=black:interpol=bicubic,"
        f'crop={width}:{height},fps={fps},setpts=PTS-STARTPTS,trim=duration={duration:.3f}" '
        f"-f yuv4mpegpipe - | "
        f"ffmpeg -y -v error -i - -an -c:v libx264 -preset fast -crf 16 {shlex.quote(out_name)}"
    )
    for command in (detect, transform):
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, cwd=work
        )
        if result.returncode != 0:
            conform_segment(raw_path, out_path, width=width, height=height, duration=duration, fps=fps)
            return


def conform_segment(
    raw_path: str, out_path: str, *, width: int, height: int, duration: float, fps: int = 30
) -> None:
    """Center-crop conform without stabilization (also the failure fallback)."""
    command = [
        "ffmpeg", "-y", "-v", "error", "-i", raw_path,
        "-vf", f"crop={width}:{height},fps={fps},setpts=PTS-STARTPTS,trim=duration={duration:.3f}",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "16", out_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolingError(f"Segment conform failed: {result.stderr.strip()}")
