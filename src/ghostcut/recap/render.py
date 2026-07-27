"""Beat-locked assembly: segments, crossfades, and the final render.

Every cut sits on the song's bar grid, and each shot is offset so its motion
peak lands on a beat inside its slot. Crossfades are centered on the musical
boundary — both sides contribute half the fade from extra source material —
so transitions never move a cut off the grid.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..ffmpeg_tools import ToolingError, extract_media_stats, probe_media
from .beat import BeatGrid
from .select import RecapCandidate
from .stabilize import conform_segment, extract_segment, find_vidstab_ffmpeg, stabilize_segment

REEL_W, REEL_H = 1080, 1920
REEL_FPS = 30
LEAD_IN_BARS = 2.0            # song enters this many bars before the chorus
FADE_SMALL, FADE_CLOSER = 0.3, 0.5


def _beat_offset(bars: float) -> float:
    """Beats into the slot where the shot's motion peak should land."""
    return 1.0 if bars < 1.0 else 2.0


def _fade_plan(n_segments: int, transitions: bool) -> dict[int, float]:
    """boundary index (between seg i-1 and seg i) -> fade duration."""
    if not transitions or n_segments < 4:
        return {}
    return {1: FADE_SMALL, n_segments - 2: FADE_SMALL, n_segments - 1: FADE_CLOSER}


def assemble_reel(
    picks: list[RecapCandidate],
    pattern: list[float],
    grid: BeatGrid,
    *,
    footage_path: str | None = None,
    song_path: str,
    output_path: str,
    work_dir: Path,
    stabilize: bool = True,
    transitions: bool = True,
) -> dict:
    if len(picks) != len(pattern):
        raise ValueError(f"{len(picks)} picks for {len(pattern)} slots")

    n = len(picks)
    durations = [bars * grid.bar_s for bars in pattern]
    total = sum(durations)
    boundaries = [sum(durations[:k]) for k in range(n)]  # start of each slot
    fades = _fade_plan(n, transitions)

    # each pick pulls from its own source (multi-event recaps); fall back to a
    # single footage_path for picks that predate source tagging.
    _duration_cache: dict[str, float] = {}

    def source_duration(path: str) -> float:
        if path not in _duration_cache:
            value = float(
                extract_media_stats(probe_media(Path(path))).get("duration_s") or 0.0
            )
            if value <= 0.0:
                raise ToolingError(f"Could not determine footage duration: {path}")
            _duration_cache[path] = value
        return _duration_cache[path]

    song_start = max(0.0, grid.chorus_downbeat_s - LEAD_IN_BARS * grid.bar_s)
    if grid.song_duration_s - song_start < total + 0.2:
        song_start = max(0.0, grid.song_duration_s - total - 0.2)

    stab_ffmpeg = find_vidstab_ffmpeg() if stabilize else None
    work_dir.mkdir(parents=True, exist_ok=True)

    segment_files: list[str] = []
    for i, (pick, bars, slot_dur) in enumerate(zip(picks, pattern, durations)):
        source = pick.source or footage_path
        if not source:
            raise ToolingError(f"Pick {i} has no source footage")
        head = fades.get(i, 0.0) / 2.0
        tail = fades.get(i + 1, 0.0) / 2.0
        seg_dur = slot_dur + head + tail
        src_start = pick.t - _beat_offset(bars) * grid.beat_s - head
        src_start = max(0.0, min(src_start, source_duration(source) - seg_dur - 0.2))

        raw = str(work_dir / f"raw_{i:02d}.mp4")
        seg = str(work_dir / f"seg_{i:02d}.mp4")
        do_stab = bool(stab_ffmpeg) and not pick.static
        extract_segment(
            source, src_start, seg_dur, raw,
            width=REEL_W, height=REEL_H, oversize=do_stab,
        )
        if do_stab:
            stabilize_segment(
                raw, seg, stab_ffmpeg=stab_ffmpeg,
                width=REEL_W, height=REEL_H, duration=seg_dur, fps=REEL_FPS,
            )
        else:
            conform_segment(raw, seg, width=REEL_W, height=REEL_H, duration=seg_dur, fps=REEL_FPS)
        segment_files.append(seg)

    # blocks of hard-cut segments, split where crossfades sit
    blocks: list[list[int]] = [[0]]
    for i in range(1, n):
        if i in fades:
            blocks.append([i])
        else:
            blocks[-1].append(i)

    graph: list[str] = []
    labels: list[str] = []
    for b, indices in enumerate(blocks):
        label = f"blk{b}"
        if len(indices) == 1:
            graph.append(f"[{indices[0]}:v]settb=AVTB[{label}]")
        else:
            joined = "".join(f"[{i}:v]" for i in indices)
            graph.append(f"{joined}concat=n={len(indices)}:v=1:a=0,settb=AVTB[{label}]")
        labels.append(label)

    accumulated = labels[0]
    for b in range(1, len(blocks)):
        boundary_index = blocks[b][0]
        fade = fades[boundary_index]
        offset = boundaries[boundary_index] - fade / 2.0
        out_label = f"x{b}"
        graph.append(
            f"[{accumulated}][{labels[b]}]xfade=transition=fade:"
            f"duration={fade:.3f}:offset={offset:.3f}[{out_label}]"
        )
        accumulated = out_label

    graph.append(
        f"[{accumulated}]fade=t=in:st=0:d=0.25,"
        f"fade=t=out:st={total - 1.2:.3f}:d=1.2[vout]"
    )
    graph.append(
        f"[{n}:a]atrim=start={song_start:.3f}:end={song_start + total:.3f},"
        f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.15,"
        f"afade=t=out:st={total - 2.5:.3f}:d=2.5[aout]"
    )

    command = ["ffmpeg", "-y", "-v", "error"]
    for seg in segment_files:
        command += ["-i", seg]
    command += ["-i", song_path]
    command += [
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolingError(f"Final assembly failed: {result.stderr.strip()}")

    return {
        "output": output_path,
        "duration_s": round(total, 3),
        "segments": n,
        "stabilized": bool(stab_ffmpeg),
        "stab_ffmpeg": stab_ffmpeg or "",
        "transitions": sorted(fades),
        "song_start_s": round(song_start, 3),
        "bpm": grid.bpm,
    }
