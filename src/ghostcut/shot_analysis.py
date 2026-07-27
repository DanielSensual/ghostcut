from __future__ import annotations

import re
from pathlib import Path

from .ffmpeg_tools import extract_media_stats, extract_scene_change_pts, probe_media, run_command
from .models import MediaClip, ShotSpan

YAVG_PATTERN = re.compile(r"lavfi\.signalstats\.YAVG=([\d\.]+)")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _exposure_score(proxy_path: str) -> float:
    process = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            proxy_path,
            "-vf",
            "signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    payload = f"{process.stdout}\n{process.stderr}"
    yavg_values = [float(value) for value in YAVG_PATTERN.findall(payload)]
    if not yavg_values:
        return 0.6
    avg_y = sum(yavg_values) / len(yavg_values)
    return _clamp(1.0 - abs(avg_y - 128.0) / 128.0)


def _blur_score(proxy_path: str) -> float:
    probe = probe_media(Path(proxy_path))
    stats = extract_media_stats(probe)
    width = float(stats["width"])
    height = float(stats["height"])
    fps = float(stats["fps"])
    bitrate = float(stats.get("bitrate", 0.0))
    if width <= 0 or height <= 0 or fps <= 0 or bitrate <= 0:
        return 0.5

    bits_per_pixel = bitrate / (width * height * fps)
    # Heuristic: ~0.03 is muddy, ~0.25 is sharp for proxy-like footage.
    return _clamp((bits_per_pixel - 0.03) / 0.22)


def _stability_score(scene_changes: list[float], duration_s: float) -> float:
    if duration_s <= 0:
        return 0.5
    density = len(scene_changes) / duration_s
    return _clamp(1.0 - density * 0.2, 0.2, 1.0)


def detect_shots_for_clip(clip: MediaClip) -> list[ShotSpan]:
    proxy_path = Path(clip.proxy_path)
    if not proxy_path.exists():
        return []

    scene_run = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(proxy_path),
            "-filter:v",
            "select='gt(scene,0.35)',showinfo",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )

    scene_changes = sorted(
        point for point in extract_scene_change_pts(scene_run.stderr) if 0.0 < point < clip.duration_s
    )
    boundaries = [0.0, *scene_changes, clip.duration_s]
    exposure = _exposure_score(str(proxy_path))
    blur = _blur_score(str(proxy_path))
    clip_stability = _stability_score(scene_changes, clip.duration_s)

    shots: list[ShotSpan] = []
    for start_s, end_s in zip(boundaries[:-1], boundaries[1:]):
        if end_s - start_s < 0.3:
            continue
        length_scale = _clamp((end_s - start_s) / 4.0, 0.5, 1.0)
        shots.append(
            ShotSpan(
                clip_id=clip.id,
                start_s=round(start_s, 3),
                end_s=round(end_s, 3),
                stability_score=round(clip_stability * length_scale, 4),
                blur_score=round(blur, 4),
                exposure_score=round(exposure, 4),
            )
        )

    if not shots and clip.duration_s > 0:
        shots.append(
            ShotSpan(
                clip_id=clip.id,
                start_s=0.0,
                end_s=round(clip.duration_s, 3),
                stability_score=round(clip_stability, 4),
                blur_score=round(blur, 4),
                exposure_score=round(exposure, 4),
            )
        )
    return shots


def detect_shots(clips: list[MediaClip]) -> list[ShotSpan]:
    output: list[ShotSpan] = []
    for clip in clips:
        output.extend(detect_shots_for_clip(clip))
    return output
