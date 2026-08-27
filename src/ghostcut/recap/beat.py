"""Song beat analysis: BPM, bar grid, and chorus location.

Coarse BPM comes from autocorrelation of the onset envelope over the whole
song; the grid (period + phase) is then re-fit locally inside the strongest
energy window, because extrapolating a grid from the intro drifts audibly by
the time the chorus arrives.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from ..ffmpeg_tools import ToolingError

_SR = 22050
_HOP = 256


@dataclass(slots=True)
class BeatGrid:
    bpm: float
    beat_s: float
    bar_s: float
    chorus_downbeat_s: float
    song_duration_s: float


def _decode_mono(song_path: str) -> np.ndarray:
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", song_path,
         "-ac", "1", "-ar", str(_SR), "-f", "s16le", "-"],
        capture_output=True,
    )
    if process.returncode != 0 or not process.stdout:
        raise ToolingError(f"Could not decode audio: {song_path}")
    return np.frombuffer(process.stdout, dtype=np.int16).astype(np.float64)


def _onset_envelope(samples: np.ndarray) -> tuple[np.ndarray, float]:
    frames = len(samples) // _HOP
    energy = np.array([np.sum(samples[i * _HOP:(i + 1) * _HOP] ** 2) for i in range(frames)])
    onset = np.maximum(0.0, np.diff(energy))
    peak = onset.max()
    if peak > 0:
        onset = onset / peak
    return onset, _SR / _HOP


def _coarse_bpm(onset: np.ndarray, fps: float, lo: float = 60.0, hi: float = 150.0) -> float:
    centered = onset - onset.mean()
    ac = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
    min_lag = max(1, int(fps * 60.0 / hi))
    max_lag = int(fps * 60.0 / lo)
    lag = min_lag + int(np.argmax(ac[min_lag:max_lag]))
    bpm = 60.0 * fps / lag
    # autocorrelation often locks onto the half-tempo peak for dance music
    while bpm < 95.0:
        bpm *= 2.0
    return bpm


def _energy_window(samples: np.ndarray, window_s: float = 22.0) -> float:
    """Start (s) of the strongest sustained-energy window: the chorus."""
    win = _SR // 2
    frames = len(samples) // win
    rms = np.array([np.sqrt(np.mean(samples[i * win:(i + 1) * win] ** 2)) for i in range(frames)])
    span = int(window_s * 2)
    lo = int(frames * 0.20)
    hi = max(lo + 1, int(frames * 0.85) - span)
    means = np.array([rms[i:i + span].mean() for i in range(lo, hi)])
    return (lo + int(np.argmax(means))) * 0.5


def _refine_grid(
    onset: np.ndarray, fps: float, bpm_guess: float, lo_s: float, hi_s: float
) -> tuple[float, float]:
    """Comb-fit (bpm, first_beat_s) inside [lo_s, hi_s]."""
    seg = onset[int(lo_s * fps):int(hi_s * fps)]
    best_score, best_bpm, best_phase = -1.0, bpm_guess, 0.0
    for bpm in np.arange(bpm_guess - 4.0, bpm_guess + 4.0, 0.05):
        period = 60.0 / bpm * fps
        for phase in np.arange(0.0, period, period / 48.0):
            idx = np.arange(phase, len(seg), period).astype(int)
            score = seg[idx].sum() / max(1, len(idx))
            if score > best_score:
                best_score, best_bpm, best_phase = score, float(bpm), float(phase)
    return best_bpm, lo_s + best_phase / fps


def _downbeat(onset: np.ndarray, fps: float, first_beat_s: float, beat_s: float, hi_s: float) -> float:
    beats = np.arange(first_beat_s, hi_s, beat_s)
    if len(beats) == 0:
        return float(first_beat_s)
    strengths = []
    for b in beats:
        i = int(b * fps)
        strengths.append(onset[max(0, i - 3):i + 4].max() if i < len(onset) else 0.0)
    strengths = np.array(strengths)
    phase = int(np.argmax([strengths[k::4].sum() for k in range(4)]))
    return float(beats[phase])


def analyze_song(song_path: str, chorus_hint_s: float | None = None) -> BeatGrid:
    """Fit the beat grid; chorus_hint_s pins the section instead of the
    energy-based chorus search (the grid is refined locally around it)."""
    samples = _decode_mono(song_path)
    duration = len(samples) / _SR
    if duration < 2.0 or len(samples) < _SR * 2:
        raise ToolingError(f"Song is too short to analyze (need at least 2s): {song_path}")
    onset, fps = _onset_envelope(samples)

    bpm_coarse = _coarse_bpm(onset, fps)
    chorus_raw = chorus_hint_s if chorus_hint_s is not None else _energy_window(samples)

    lo = max(0.0, chorus_raw - 8.0)
    hi = min(duration, chorus_raw + 28.0)
    bpm, first_beat = _refine_grid(onset, fps, bpm_coarse, lo, hi)
    beat = 60.0 / bpm
    bar = 4.0 * beat

    db = _downbeat(onset, fps, first_beat, beat, hi)
    # snap the chorus start to the nearest downbeat of the refined grid
    k = round((chorus_raw - db) / bar)
    chorus_downbeat = db + k * bar

    return BeatGrid(
        bpm=round(bpm, 2),
        beat_s=round(beat, 4),
        bar_s=round(bar, 4),
        chorus_downbeat_s=round(chorus_downbeat, 3),
        song_duration_s=round(duration, 3),
    )
