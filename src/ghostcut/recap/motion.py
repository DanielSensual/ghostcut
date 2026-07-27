"""Motion analysis: peak dance moments and whip-pan detection.

Motion peaks (frame-difference energy spikes) surface the dips, spins, and
hair whips worth featuring. Phase correlation separates the two ways a frame
can move: the whole frame shifting together is a camera move (whip-pan,
reject), local motion on a steady background is dancing (keep).
"""

from __future__ import annotations

import subprocess

import numpy as np

from ..ffmpeg_tools import ToolingError, extract_media_stats, probe_media


def _decode_gray(
    video_path: str, *, width: int, fps: float, start: float | None = None, duration: float | None = None
) -> tuple[np.ndarray, int, int]:
    stats = extract_media_stats(probe_media(video_path))
    src_w = int(stats.get("width") or 0)
    src_h = int(stats.get("height") or 0)
    if not src_w or not src_h:
        raise ToolingError(f"Could not probe dimensions: {video_path}")
    height = max(2, round(width * src_h / src_w / 2) * 2)

    command = ["ffmpeg", "-v", "error"]
    if start is not None:
        command += ["-ss", f"{start:.3f}"]
    if duration is not None:
        command += ["-t", f"{duration:.3f}"]
    command += [
        "-i", video_path,
        "-vf", f"fps={fps},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.run(command, capture_output=True).stdout
    count = len(raw) // (width * height)
    if count < 2:
        raise ToolingError(f"Decoded too few frames from {video_path}")
    frames = np.frombuffer(raw, dtype=np.uint8)[: count * width * height]
    return frames.reshape(count, height, width).astype(np.float32), width, height


def motion_curve(video_path: str, *, width: int = 120, fps: float = 12.0) -> tuple[np.ndarray, float]:
    frames, _, _ = _decode_gray(video_path, width=width, fps=fps)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    kernel = np.ones(5) / 5.0
    return np.convolve(diff, kernel, mode="same"), fps


def find_motion_peaks(
    video_path: str,
    *,
    count: int = 40,
    min_gap_s: float = 8.0,
    skip_head_s: float = 1.0,
    skip_tail_s: float = 3.0,
) -> list[dict]:
    """Top motion peaks as [{"t": seconds, "motion": strength}], time-sorted."""
    curve, fps = motion_curve(video_path)
    times = (np.arange(len(curve)) + 1) / fps
    peaks: list[tuple[float, float]] = []
    for i in np.argsort(curve)[::-1]:
        t = float(times[i])
        if t < skip_head_s or t > times[-1] - skip_tail_s:
            continue
        if all(abs(t - p) >= min_gap_s for p, _ in peaks):
            peaks.append((t, float(curve[i])))
        if len(peaks) >= count:
            break
    peaks.sort()
    return [{"t": round(t, 2), "motion": round(m, 2)} for t, m in peaks]


def _global_shift(a: np.ndarray, b: np.ndarray, window: np.ndarray) -> tuple[int, int]:
    fa = np.fft.fft2(a * window)
    fb = np.fft.fft2(b * window)
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-9
    corr = np.abs(np.fft.ifft2(cross))
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = a.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return int(dx), int(dy)


def frame_brightness(video_path: str, t: float) -> float:
    """Mean luma (0..255) of a downscaled frame at time t; 255 if decode fails."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video_path,
         "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    ).stdout
    if not raw:
        return 255.0
    return float(np.frombuffer(raw, dtype=np.uint8).mean())


def pan_metrics(video_path: str, t: float, *, span_s: float = 0.5) -> dict:
    """How camera-driven is the motion around t?

    pan_ratio ~1.0 means a global shift explains nearly all frame difference
    (camera whip); ~0.0 means the motion is local (someone dancing).
    """
    frames, w, h = _decode_gray(
        video_path, width=240, fps=12.0, start=max(0.0, t - span_s / 2), duration=span_s
    )
    window = np.outer(np.hanning(h), np.hanning(w))
    ratios, vectors = [], []
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        raw = np.abs(b - a).mean()
        if raw < 1e-3:
            continue
        dx, dy = _global_shift(a, b, window)
        aligned = np.roll(np.roll(b, -dy, axis=0), -dx, axis=1)
        residual = np.abs(aligned - a).mean()
        ratios.append(max(0.0, 1.0 - residual / raw))
        vectors.append((float(dx), float(dy)))
    if not ratios:
        return {"pan_ratio": 0.0, "shift_px": 0.0, "coherence": 0.0}
    magnitudes = [np.hypot(dx, dy) for dx, dy in vectors]
    mean_mag = float(np.mean(magnitudes))
    # a pan shifts consistently in one direction; camera tracking a dancer
    # oscillates, so the vector mean cancels while magnitudes stay high
    mean_vec = np.mean(vectors, axis=0)
    coherence = float(np.hypot(*mean_vec) / mean_mag) if mean_mag > 1e-6 else 0.0
    return {
        "pan_ratio": round(float(np.mean(ratios)), 3),
        "shift_px": round(mean_mag, 2),
        "coherence": round(coherence, 3),
    }
