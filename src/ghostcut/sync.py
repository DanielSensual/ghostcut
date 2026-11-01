"""Multicam sync: put every camera on one timeline, measured more than one way.

Each method gives its own estimate of where a clip starts on the shared timeline:

- ``timecode`` — the start timecode in the file (Sony writes it on the metadata track, and in the XML sidecar).
  Frame-exact when the cameras were jammed to one clock; seconds or hours off when each one free-runs.
- ``clock`` — the capture time in the metadata (the iPhone's is network time; a camera's is whatever it was set
  to). Coarse: only used to narrow a search or when nothing better exists.
- ``waveform`` — the speech envelopes of the clips' own audio, cross-correlated (needs numpy: the ``[sync]`` extra).
  Millisecond-exact when the peak is real.
- ``whisper`` — the same words heard by two cameras: three-word phrases that occur once in each transcript.

The estimates are checked against each other. A waveform peak can be confident and wrong (a short clip latches
onto any loud section), so when another method disagrees, the waveform is searched again inside the window the
other method allows, and the report keeps every estimate and every disagreement.

Convention: a clip's ``offset_s`` is where its first sample sits on the timeline, so clip time ``t`` is timeline
time ``t + offset_s``. The reference clip sits at 0.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import coerce_float, parse_fraction, run_command, snap_frame_rate

try:
    import numpy as np
except ImportError:  # waveform sync needs the [sync] extra; the other methods are stdlib-only
    np = None  # type: ignore[assignment]

SR = 8000                 # decode rate for waveform sync: speech band only, memory stays small
COARSE_HOP_S = 0.010      # 10 ms envelope for the global search
FINE_HOP_S = 0.001        # 1 ms envelope for the refinement and the drift windows
R_MIN = 0.45              # waveform correlation below this is not trusted
MARGIN_MIN = 0.08         # best peak must beat the best peak more than 1 s away by this much
DRIFT_WINDOW_S = 20.0
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"

# Precision of each method when it is right (seconds). Agreement = within the larger of the two plus one frame.
PRECISION = {"timecode": 0.0, "clock": 2.0, "waveform": 0.002, "whisper": 0.15}


@dataclass(slots=True)
class CameraInfo:
    path: str
    make: str = ""
    model: str = ""
    lens: str = ""
    fps: float = 0.0
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    has_audio: bool = False
    gamma: str = ""          # Sony CaptureGammaEquation (e.g. s-log3-cine) or the stream's color transfer
    timecode: str = ""       # start timecode, "HH:MM:SS:FF" (";" before the frames = drop-frame)
    tc_rate: float = 0.0     # nominal timecode frame rate (60 for "13:37:55:42" on a 59.94p clip)
    captured_at: str = ""    # ISO capture time with zone

    @property
    def name(self) -> str:
        return " ".join(part for part in (self.make, self.model) if part) or Path(self.path).parent.name


@dataclass(slots=True)
class OffsetEstimate:
    method: str
    offset_s: float
    confidence: float        # 0..1, the method's own view of itself
    precision_s: float
    against: str = ""        # path of the placed clip this was measured against
    detail: dict = field(default_factory=dict)


@dataclass(slots=True)
class ClipSync:
    camera: CameraInfo
    label: str = ""                     # A, B, C… in the order clips were given (the reference is A)
    offset_s: float | None = None
    method: str = ""                    # which estimate was used ("reference" for the reference clip)
    against: str = ""
    estimates: list[OffsetEstimate] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)   # pairwise agreement between estimates
    drift_ppm: float | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- metadata: which camera, timecode, capture time

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sony_sidecar(path: Path) -> Path | None:
    for candidate in (path.with_name(f"{path.stem}M01.XML"), path.with_name(f"{path.stem}M01.xml")):
        if candidate.exists():
            return candidate
    return None


def decode_sony_ltc(value: str) -> str:
    """Sony XML LtcChange value: BCD bytes frames, seconds, minutes, hours ("61553713" -> "13:37:55:21").
    The high bits carry flags (drop-frame, field), so they are masked off."""
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", value or ""):
        return ""
    ff, ss, mm, hh = (int(value[i:i + 2], 16) for i in range(0, 8, 2))
    digits = [format(ff & 0x3F, "x"), format(ss & 0x7F, "x"), format(mm & 0x7F, "x"), format(hh & 0x3F, "x")]
    if not all(d.isdigit() for d in digits):
        return ""
    f, s, m, h = (int(d) for d in digits)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def parse_sony_xml(text: str) -> dict:
    """Make, model, lens, gamma, capture time and start timecode from a Sony NonRealTimeMeta sidecar."""
    out: dict = {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "Device":
            out["make"] = el.get("manufacturer", "")
            out["model"] = el.get("modelName", "")
        elif tag == "Lens":
            out["lens"] = el.get("modelName", "")
        elif tag == "CreationDate":
            out["captured_at"] = el.get("value", "")
        elif tag == "Item" and el.get("name") == "CaptureGammaEquation":
            out["gamma"] = el.get("value", "")
        elif tag == "LtcChangeTable":
            out["tc_rate"] = coerce_float(el.get("tcFps"))
        elif tag == "LtcChange" and el.get("frameCount") == "0":
            out["timecode"] = decode_sony_ltc(el.get("value", ""))
    return out


_TRANSFER = {"arib-std-b67": "hlg", "smpte2084": "pq", "bt709": "rec709"}


def camera_from_probe(path: Path, probe: dict, sidecar_text: str = "") -> CameraInfo:
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})
    ftags = fmt.get("tags", {}) or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    info = CameraInfo(
        path=str(path),
        fps=parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_s=coerce_float(fmt.get("duration")),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        make=ftags.get("com.apple.quicktime.make") or ftags.get("make") or "",
        model=ftags.get("com.apple.quicktime.model") or ftags.get("model") or "",
        gamma=_TRANSFER.get(video.get("color_transfer", ""), video.get("color_transfer", "") or ""),
        # the iPhone's own capture time; creation_time can be the time the file was copied
        captured_at=ftags.get("com.apple.quicktime.creationdate") or ftags.get("creation_time") or "",
    )
    tc = ftags.get("timecode") or next((s.get("tags", {}).get("timecode") for s in streams
                                        if s.get("tags", {}).get("timecode")), "")
    if tc:
        info.timecode = tc
        info.tc_rate = _nominal_rate(info.fps)
    sony = parse_sony_xml(sidecar_text) if sidecar_text else {}
    for key in ("make", "model", "lens", "gamma", "captured_at"):
        if sony.get(key):
            setattr(info, key, sony[key])
    if sony.get("timecode") and not info.timecode:
        info.timecode, info.tc_rate = sony["timecode"], sony.get("tc_rate") or _nominal_rate(info.fps)
    return info


def probe_camera(path: Path) -> CameraInfo:
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:format_tags:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,color_transfer"
        ":stream_tags=timecode", "-of", "json", str(path)])
    sidecar = sony_sidecar(path)
    return camera_from_probe(path, json.loads(result.stdout),
                             sidecar.read_text(encoding="utf-8", errors="replace") if sidecar else "")


def _nominal_rate(fps: float) -> float:
    return float(round(fps)) if fps > 0 else 0.0


def timecode_seconds(tc: str, nominal: float, fps: float) -> float | None:
    """Real seconds since 00:00:00:00. Timecode counts frames at the round rate (24/30/60), so on a 23.976/29.97/
    59.94 clip each labelled second lasts 1.001 s; drop-frame labels (";") also skip numbers each minute."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})([:;.,])(\d{2})", (tc or "").strip())
    if not m or nominal <= 0:
        return None
    h, mi, s, sep, f = int(m[1]), int(m[2]), int(m[3]), m[4], int(m[5])
    rate = round(nominal)
    frames = (h * 3600 + mi * 60 + s) * rate + f
    drop_frame = sep in ";,"
    if drop_frame:  # 2 labels (4 at 60) skipped each minute except every tenth
        minutes = h * 60 + mi
        frames -= 2 * rate // 30 * (minutes - minutes // 10)
    ntsc = drop_frame or (fps > 0 and snap_frame_rate(fps).denominator == 1001)
    return frames / (rate * 1000 / 1001 if ntsc else rate)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)  # iPhone writes -0400
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


# ---------------------------------------------------------------- method 1 and 2: timecode, capture clock

def timecode_estimate(ref: CameraInfo, cam: CameraInfo, ref_offset: float = 0.0) -> OffsetEstimate | None:
    if not (ref.timecode and cam.timecode):
        return None
    a = timecode_seconds(ref.timecode, ref.tc_rate or _nominal_rate(ref.fps), ref.fps)
    b = timecode_seconds(cam.timecode, cam.tc_rate or _nominal_rate(cam.fps), cam.fps)
    if a is None or b is None:
        return None
    delta = b - a
    day = timecode_seconds("24:00:00:00", cam.tc_rate or _nominal_rate(cam.fps), cam.fps) or 86400.0
    if delta > day / 2:
        delta -= day
    elif delta < -day / 2:
        delta += day
    frame = 1 / cam.fps if cam.fps else 0.0
    return OffsetEstimate("timecode", ref_offset + delta, 0.5, max(PRECISION["timecode"], frame), ref.path,
                          {"ref_tc": ref.timecode, "tc": cam.timecode,
                           "note": "exact only if the cameras were jammed to one timecode source"})


def clock_estimate(ref: CameraInfo, cam: CameraInfo, ref_offset: float = 0.0) -> OffsetEstimate | None:
    a, b = _parse_time(ref.captured_at), _parse_time(cam.captured_at)
    if a is None or b is None:
        return None
    return OffsetEstimate("clock", ref_offset + (b - a).total_seconds(), 0.2, PRECISION["clock"], ref.path,
                          {"ref_time": ref.captured_at, "time": cam.captured_at,
                           "note": "camera clocks are often minutes apart; used only to narrow a search"})


# ---------------------------------------------------------------- method 3: waveform (speech envelope)

def decode_audio(path: Path | str, sr: int = SR) -> "np.ndarray":
    """Mono speech band at `sr`, float32. Band-limited in ffmpeg so rumble and hiss don't drive the envelope."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                           "-af", "highpass=f=200,lowpass=f=3400", "-f", "f32le", "-"],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode audio from {path}: {proc.stderr.decode()[-300:]}")
    return np.frombuffer(proc.stdout, np.float32)


def envelope(x: "np.ndarray", sr: int, hop_s: float) -> "np.ndarray":
    """Log RMS per hop. Log compression is what lets a lav and a camera mic three metres away correlate."""
    hop = max(1, int(round(sr * hop_s)))
    n = len(x) // hop * hop
    if not n:
        return np.zeros(0)
    frames = x[:n].astype(np.float64).reshape(-1, hop)
    return np.log(np.sqrt((frames ** 2).mean(1)) + 1e-4)


def _z(e: "np.ndarray") -> "np.ndarray":
    s = e.std()
    return (e - e.mean()) / s if s > 0 else e * 0.0


def _pearson(a: "np.ndarray", b: "np.ndarray") -> float:
    a, b = a - a.mean(), b - b.mean()
    d = float(np.sqrt((a @ a) * (b @ b)))
    return float(a @ b / d) if d > 0 else 0.0


def _overlap(la: int, lb: int, lag: int) -> tuple[int, int]:
    """Indices i of b (camera) whose partner a[i+lag] (reference) exists."""
    return max(0, -lag), min(lb, la - lag)


def _r_at(ea: "np.ndarray", eb: "np.ndarray", lag: int) -> float:
    i0, i1 = _overlap(len(ea), len(eb), lag)
    return _pearson(ea[i0 + lag:i1 + lag], eb[i0:i1]) if i1 - i0 > 2 else -1.0


def correlate(ea: "np.ndarray", eb: "np.ndarray", min_overlap: int, hop_s: float = COARSE_HOP_S,
              window: tuple[int, int] | None = None) -> tuple[int, float, float]:
    """Global lag search by FFT: (lag, r at lag, margin over the best lag more than 1 s away). lag L means
    camera sample i lines up with reference sample i+L. `window` (lo, hi) limits the lags considered."""
    za, zb = _z(ea), _z(eb)
    n = 1 << int(np.ceil(np.log2(len(za) + len(zb) + 1)))
    c = np.fft.irfft(np.fft.rfft(za, n) * np.conj(np.fft.rfft(zb, n)), n)
    lags = np.arange(n)
    lags = np.where(lags > n // 2, lags - n, lags)
    counts = np.minimum(len(zb), len(za) - lags) - np.maximum(0, -lags)
    valid = counts >= min_overlap
    if window is not None:
        valid &= (lags >= window[0]) & (lags <= window[1])
    if not valid.any():
        return 0, -1.0, 0.0
    score = np.where(valid, c / np.maximum(counts, 1), -np.inf)
    best = int(np.argmax(score))
    lag = int(lags[best])
    # re-score the strongest few candidates with an exact Pearson over their overlap
    top = np.argsort(score)[-8:]
    cands = sorted({int(lags[i]) for i in top if np.isfinite(score[i])} | {lag})
    rs = {L: _r_at(ea, eb, L) for L in cands}
    lag = max(rs, key=rs.get)
    far = valid & (np.abs(lags - lag) > 1.0 / hop_s)
    margin = 1.0
    if far.any():
        far_idx = np.argsort(np.where(far, score, -np.inf))[-8:]
        far_r = max((_r_at(ea, eb, int(lags[i])) for i in far_idx if far[i]), default=-1.0)
        margin = rs[lag] - far_r
    return lag, rs[lag], margin


def refine(ea: "np.ndarray", eb: "np.ndarray", lag: int, radius: int) -> tuple[int, float]:
    best = (lag, -1.0)
    for L in range(lag - radius, lag + radius + 1):
        r = _r_at(ea, eb, L)
        if r > best[1]:
            best = (L, r)
    return best


def drift(ea: "np.ndarray", eb: "np.ndarray", lag: int, hop_s: float = FINE_HOP_S,
          window_s: float = DRIFT_WINDOW_S, radius_s: float = 0.04) -> dict:
    """Lag re-measured in windows across the overlap. A straight-line fit against the clip's own time gives the
    lag at its first frame and the drift: ppm > 0 means the clip's clock runs fast against the reference."""
    w, rad = int(window_s / hop_s), int(radius_s / hop_s)
    i0, i1 = _overlap(len(ea), len(eb), lag)
    points = []
    for s in range(i0, i1 - w + 1, w):
        best = (lag, -1.0)
        for L in range(lag - rad, lag + rad + 1):
            if s + L < 0 or s + w + L > len(ea):
                continue
            r = _pearson(ea[s + L:s + w + L], eb[s:s + w])
            if r > best[1]:
                best = (L, r)
        if best[1] >= R_MIN:
            points.append(((s + w / 2) * hop_s, best[0] * hop_s, best[1]))
    out: dict = {"windows": [{"t": round(t, 2), "lag_s": round(l, 4), "r": round(r, 3)} for t, l, r in points]}
    if len(points) >= 3:
        t = np.array([p[0] for p in points])
        lg = np.array([p[1] for p in points])
        slope, intercept = np.polyfit(t, lg, 1)
        out["ppm"] = round(-float(slope) * 1e6, 1)
        out["lag_at_start_s"] = round(float(intercept), 4)
        out["spread_s"] = round(float((lg - (slope * t + intercept)).std()), 4)
    return out


def waveform_estimate(ref_audio: "np.ndarray", cam_audio: "np.ndarray", sr: int = SR,
                      hint: OffsetEstimate | None = None, ref_offset: float = 0.0,
                      ref_path: str = "") -> OffsetEstimate | None:
    """Global envelope correlation at 10 ms, refined at 1 ms. With `hint`, only lags within the hint's precision
    (plus half a second) are searched: the guided pass used when the global peak and another method disagree."""
    ca, cb = envelope(ref_audio, sr, COARSE_HOP_S), envelope(cam_audio, sr, COARSE_HOP_S)
    if len(ca) < 50 or len(cb) < 50:
        return None
    shorter = min(len(ca), len(cb))
    min_overlap = int(min(10.0 / COARSE_HOP_S, 0.5 * shorter))
    window = None
    if hint is not None:
        centre = (hint.offset_s - ref_offset) / COARSE_HOP_S
        reach = (hint.precision_s + 0.5) / COARSE_HOP_S
        window = (int(centre - reach), int(centre + reach))
    lag, r, margin = correlate(ca, cb, min_overlap, COARSE_HOP_S, window)
    if r < 0:
        return None
    fa, fb = envelope(ref_audio, sr, FINE_HOP_S), envelope(cam_audio, sr, FINE_HOP_S)
    scale = int(round(COARSE_HOP_S / FINE_HOP_S))
    fine, fr = refine(fa, fb, lag * scale, scale * 2)
    d = drift(fa, fb, fine)
    offset = fine * FINE_HOP_S  # the lag that fits the whole overlap best: the middle of it, when clocks drift
    if "lag_at_start_s" in d and abs(d["lag_at_start_s"] - offset) > FINE_HOP_S:
        offset = d["lag_at_start_s"]
    confidence = max(0.0, min(1.0, (fr - R_MIN) / (1 - R_MIN))) * (1.0 if margin >= MARGIN_MIN else 0.4)
    detail = {"r": round(fr, 4), "margin": round(margin, 4), "guided_by": hint.method if hint else "",
              "whole_overlap_offset_s": round(ref_offset + fine * FINE_HOP_S, 4), "drift": d}
    return OffsetEstimate("waveform", ref_offset + offset, round(confidence, 3), PRECISION["waveform"],
                          ref_path, detail)


# ---------------------------------------------------------------- method 4: whisper (the same words)

def _norm(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower())


def load_words(path: Path | str) -> list[tuple[str, float]]:
    """(word, start_s) from Whisper JSON: {"segments": [{"words": [...]}]} or {"words": [...]}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("words") or [w for seg in data.get("segments", []) for w in seg.get("words", [])]
    out = []
    for w in raw:
        text = _norm(str(w.get("word") or w.get("text") or ""))
        if text:
            out.append((text, float(w.get("start") or 0.0)))
    return out


def match_words(ref_words: list[tuple[str, float]], cam_words: list[tuple[str, float]], n: int = 3,
                width_s: float = 0.3) -> dict | None:
    """Phrases of n words that occur exactly once in each transcript vote for an offset; the densest 0.3 s cluster
    of votes wins. Misheard words just fail to vote, they don't pull the answer.

    Overlapping phrases from one sentence are one piece of evidence, not several: `moments` counts runs of matched
    phrases in the reference (one misheard word doesn't break a run). One sentence can match by coincidence (an
    interviewer's stock question asked in two different interviews); separate stretches of speech can't."""
    def grams(words: list[tuple[str, float]]) -> dict[tuple, tuple[float, int]]:
        seen: dict[tuple, list[tuple[float, int]]] = {}
        for i in range(len(words) - n + 1):
            seen.setdefault(tuple(w for w, _ in words[i:i + n]), []).append((words[i][1], i))
        return {g: hits[0] for g, hits in seen.items() if len(hits) == 1}

    ga, gb = grams(ref_words), grams(cam_words)
    votes = sorted((ga[g][0] - gb[g][0], ga[g][1]) for g in ga.keys() & gb.keys())
    if not votes:
        return None
    best_i, best_j = 0, 0
    j = 0
    for i in range(len(votes)):
        while votes[i][0] - votes[j][0] > width_s:
            j += 1
        if i - j > best_i - best_j:
            best_i, best_j = i, j
    cluster = [d for d, _ in votes[best_j:best_i + 1]]
    moments, last = 0, None
    for idx in sorted(i for _, i in votes[best_j:best_i + 1]):
        if last is None or idx - last > n:
            moments += 1
        last = idx
    mid = cluster[len(cluster) // 2] if len(cluster) % 2 else sum(cluster[len(cluster) // 2 - 1:len(cluster) // 2 + 1]) / 2
    return {"offset_s": mid, "votes": len(cluster), "moments": moments, "anchors": len(votes),
            "spread_s": round(max(cluster) - min(cluster), 3)}


def whisper_estimate(ref_words: list[tuple[str, float]], cam_words: list[tuple[str, float]],
                     ref_offset: float = 0.0, ref_path: str = "") -> OffsetEstimate | None:
    m = match_words(ref_words, cam_words)
    if m is None or m["moments"] < 2:
        return None
    share = m["votes"] / m["anchors"]
    confidence = min(1.0, m["moments"] / 4) * min(1.0, share / 0.5)
    return OffsetEstimate("whisper", ref_offset + m["offset_s"], round(confidence, 3), PRECISION["whisper"], ref_path,
                          {k: v for k, v in m.items() if k != "offset_s"})


def transcribe_words(path: Path, cache_dir: Path, model: str = WHISPER_MODEL) -> list[tuple[str, float]] | None:
    """Word-timed transcript of a clip's audio with local mlx_whisper (no API spend), cached by file name + size."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{path.stem}-{path.stat().st_size}"
    out = cache_dir / f"{key}.json"
    if not out.exists():
        if shutil.which("mlx_whisper") is None:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / f"{key}.wav"
            run_command(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            run_command(["mlx_whisper", str(wav), "--model", model, "--word-timestamps", "True",
                         "--condition-on-previous-text", "False", "--output-format", "json",
                         "--output-dir", tmp])
            shutil.move(str(Path(tmp) / f"{key}.json"), out)
    return load_words(out)


# ---------------------------------------------------------------- combining the methods

def agree(a: OffsetEstimate, b: OffsetEstimate, frame_s: float) -> bool:
    return abs(a.offset_s - b.offset_s) <= max(a.precision_s, b.precision_s) + frame_s


def choose(estimates: list[OffsetEstimate], frame_s: float) -> tuple[OffsetEstimate | None, list[dict], list[str]]:
    """Pick the most precise estimate that something else backs up; report every pairwise check.

    A waveform result counts when its peak is clear (r and margin) or another method agrees with it. Whisper
    counts when it has enough votes. Timecode is used alone only when nothing measured from the audio exists, and
    the capture clock never is, unless it is the only thing there is (flagged)."""
    checks, warnings = [], []
    for i, a in enumerate(estimates):
        for b in estimates[i + 1:]:
            checks.append({"a": a.method, "b": b.method, "delta_s": round(b.offset_s - a.offset_s, 4),
                           "agree": agree(a, b, frame_s)})
    by = {e.method: e for e in estimates}
    wave, words, tc, clock = by.get("waveform"), by.get("whisper"), by.get("timecode"), by.get("clock")

    def backed(e: OffsetEstimate) -> bool:
        return any(agree(e, o, frame_s) for o in estimates if o is not e and o.method != "clock")

    if wave and wave.detail.get("r", 0) >= R_MIN and (wave.detail.get("margin", 0) >= MARGIN_MIN or backed(wave)):
        pick = wave
    elif words and words.confidence >= 0.3:
        pick = words
        if wave:
            warnings.append("waveform peak not trusted (weak or contradicted); using the Whisper word match")
        if words.detail.get("waveform_r_there", 1.0) < R_MIN:
            warnings.append("the sound doesn't line up at the Whisper offset (one camera's audio may be poor): "
                            "check by eye")
    elif tc:
        pick = tc
        warnings.append("placed by timecode alone: right only if the cameras were jammed")
    elif clock:
        pick = clock
        warnings.append("placed by the capture clock alone (±2 s at best): check by eye")
    elif words and "waveform_r_there" in words.detail:
        return None, checks, [
            f"Whisper matched {words.detail['moments']} moment(s) at {words.offset_s:+.3f} s but the sound doesn't "
            f"line up there (r {words.detail['waveform_r_there']:.2f}): probably the same words said at different "
            "times, so the clip is not placed"]
    else:
        return None, checks, ["no method could place this clip"]
    for e in estimates:
        if e is not pick and e.method in ("timecode", "whisper") and not agree(pick, e, frame_s):
            warnings.append(f"{e.method} disagrees with {pick.method} by {e.offset_s - pick.offset_s:+.3f} s")
    return pick, checks, warnings


# ---------------------------------------------------------------- the whole set

AudioLoader = Callable[[str], "np.ndarray"]
WordLoader = Callable[[str], "list[tuple[str, float]] | None"]


def sync_clips(cameras: list[CameraInfo], methods: tuple[str, ...] = ("timecode", "clock", "waveform", "whisper"),
               reference: int = 0, audio: AudioLoader | None = None, words: WordLoader | None = None,
               log: Callable[[str], None] = lambda _m: None) -> list[ClipSync]:
    """Place every clip relative to the reference. A clip that can't be placed against the reference is tried
    against the clips already placed (an iPhone the A-cam barely hears may be clear against the B-cam)."""
    if "waveform" in methods and np is None:
        log('waveform sync needs numpy: pip install -e ".[sync]" — continuing without it')
        methods = tuple(m for m in methods if m != "waveform")
    audio_cache: dict[str, "np.ndarray"] = {}
    words_cache: dict[str, list | None] = {}

    def get_audio(p: str) -> "np.ndarray":
        if p not in audio_cache:
            audio_cache[p] = (audio or decode_audio)(p)
        return audio_cache[p]

    def get_words(p: str) -> list | None:
        if p not in words_cache:
            words_cache[p] = words(p) if words else None
        return words_cache[p]

    results = [ClipSync(camera=c) for c in cameras]
    order = [reference] + [i for i in range(len(cameras)) if i != reference]
    for rank, idx in enumerate(order):
        results[idx].label = chr(ord("A") + rank) if rank < 26 else str(rank + 1)
    ref = results[reference]
    ref.offset_s, ref.method = 0.0, "reference"
    placed = [reference]

    for idx in order[1:]:
        clip = results[idx]
        frame_s = 1 / clip.camera.fps if clip.camera.fps else 1 / 30
        best: tuple | None = None
        for anchor_idx in placed:
            anchor = results[anchor_idx]
            log(f"  {clip.label} {Path(clip.camera.path).name} against {anchor.label}")
            ests = _estimate_pair(anchor, clip, methods, get_audio, get_words, log)
            pick, checks, warnings = choose(ests, frame_s)
            score = (pick.confidence if pick else -1) + (1 if pick and pick.method in ("waveform", "whisper") else 0)
            if best is None or score > best[0]:
                best = (score, pick, ests, checks, warnings, anchor)
            if pick and pick.method == "waveform" and pick.confidence >= 0.5:
                break
        _, pick, ests, checks, warnings, anchor = best
        clip.estimates, clip.checks, clip.warnings = ests, checks, warnings
        if pick:
            clip.offset_s, clip.method, clip.against = round(pick.offset_s, 4), pick.method, anchor.camera.path
            if pick.method == "waveform":
                clip.drift_ppm = pick.detail.get("drift", {}).get("ppm")
                overlap = min(clip.camera.duration_s, anchor.camera.duration_s)
                if clip.drift_ppm is not None and abs(clip.drift_ppm) * 1e-6 * overlap > frame_s / 2:
                    clip.warnings.append(f"clocks drift {clip.drift_ppm:+.0f} ppm: "
                                         f"{abs(clip.drift_ppm) * 1e-6 * overlap * 1000:.0f} ms over the clip")
            placed.append(idx)
    return results


def _estimate_pair(anchor: ClipSync, clip: ClipSync, methods: tuple[str, ...], get_audio, get_words,
                   log) -> list[OffsetEstimate]:
    a, c, base = anchor.camera, clip.camera, anchor.offset_s or 0.0
    ests: list[OffsetEstimate] = []
    if "timecode" in methods and (e := timecode_estimate(a, c, base)):
        ests.append(e)
    if "clock" in methods and (e := clock_estimate(a, c, base)):
        ests.append(e)
    if "whisper" in methods:
        wa, wc = get_words(a.path), get_words(c.path)
        if wa and wc and (e := whisper_estimate(wa, wc, base, a.path)):
            ests.append(e)
    if "waveform" in methods and a.has_audio and c.has_audio:
        ra, rc = get_audio(a.path), get_audio(c.path)
        wave = waveform_estimate(ra, rc, hint=None, ref_offset=base, ref_path=a.path)
        frame_s = 1 / c.fps if c.fps else 1 / 30
        hints = [e for e in ests if e.method == "whisper"] or [e for e in ests if e.method in ("timecode", "clock")]
        if wave and hints and not any(agree(wave, h, frame_s) for h in hints):
            weak = wave.detail["margin"] < MARGIN_MIN or wave.detail["r"] < R_MIN
            if weak or hints[0].method == "whisper":
                guided = waveform_estimate(ra, rc, hint=hints[0], ref_offset=base, ref_path=a.path)
                if guided and hints[0].method == "whisper":
                    # both clips have sound: if the words line up but the sound doesn't, the words were probably
                    # said twice (same question, different take), so Whisper alone no longer places the clip
                    hints[0].detail["waveform_r_there"] = guided.detail["r"]
                    if guided.detail["r"] < R_MIN:
                        hints[0].confidence = round(hints[0].confidence / 2, 3)
                # a clear global peak only gives way to a guided one that correlates about as well
                if guided and guided.detail["r"] >= R_MIN and (weak or guided.detail["r"] >= wave.detail["r"] - 0.02):
                    log(f"    waveform re-searched near {hints[0].method}: "
                        f"{wave.offset_s:+.3f} s -> {guided.offset_s:+.3f} s")
                    guided.detail["unguided_offset_s"] = round(wave.offset_s, 4)
                    wave = guided
        if wave:
            ests.append(wave)
    return ests
