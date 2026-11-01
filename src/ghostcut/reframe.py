"""Vertical reframe: SAM 3.1 tracks the on-camera subject through a finished 16:9 cut, and a
smoothed 9:16 crop follows them.

Pipeline: a small constant-rate proxy of the cut → SAM 3.1 "person" concept pass in windows
(guestlist, optional) → shot boundaries (scene detection, or the shots from angles.json) → per shot,
the subject = the person who is on screen most and largest → their box centre per sample, gaps
interpolated → zero-phase smoothing inside the shot (a cut is allowed to jump, a shot never pans
abruptly) → one crop x per output frame, sent to ffmpeg's crop filter with sendcmd.

A shot without a clear subject (B-roll, an empty room, a passer-by) is cropped at the centre.
Without guestlist (--no-sam) every shot is centred, so the command still produces a vertical cut.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import write_json
from .ffmpeg_tools import extract_scene_change_pts, probe_media, run_command

PROXY_FPS = 5           # SAM bills per frame; 5 fps tracks a seated or walking speaker fine
PROXY_SHORT_SIDE = 540
SCENE_THRESHOLD = 0.30  # ffmpeg scene score that counts as a cut
SMOOTH_S = 0.6          # Gaussian sigma of the camera path, seconds
MIN_PRESENCE = 0.5      # the subject must be on screen for at least half the shot's samples
MIN_HEIGHT = 0.25       # ... and stand at least a quarter of the frame tall (not a passer-by)
OUT_W, OUT_H = 1080, 1920
SAM_USD_PER_FRAME = 0.20 / 1000
SAM_MIN_FRAMES = 50     # per request; windows are 10 s, so 50 frames at 5 fps


class SamUnavailable(RuntimeError):
    """guestlist (the Meta API client) cannot be imported."""


@dataclass(slots=True)
class Box:
    sample: int                      # proxy frame index
    object_id: str
    box: tuple[float, float, float, float]  # left, top, right, bottom in source pixels
    confidence: float | None = None

    @property
    def centre_x(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


@dataclass(slots=True)
class ShotFrame:
    id: int
    start_s: float
    end_s: float
    subject: str | None = None
    presence: float = 0.0
    method: str = "centre"           # "subject" or "centre"
    reason: str = ""


@dataclass(slots=True)
class ReframePlan:
    source: str
    width: int
    height: int
    fps: float
    duration_s: float
    crop_w: int
    crop_h: int
    shots: list[ShotFrame]
    centres: list[float]             # smoothed subject centre x per proxy sample, source pixels
    sample_fps: float = PROXY_FPS
    method: str = "sam"
    spend_usd: float = 0.0
    frames_billed: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- geometry

def crop_size(width: int, height: int) -> tuple[int, int]:
    """Largest 9:16 window inside the frame, even-sized for yuv420p."""
    crop_h = height - height % 2
    crop_w = int(round(crop_h * 9 / 16 / 2)) * 2
    if crop_w > width:
        crop_w = width - width % 2
        crop_h = int(round(crop_w * 16 / 9 / 2)) * 2
    return crop_w, crop_h


def crop_x(centre: float, width: int, crop_w: int) -> int:
    """Left edge of a crop centred on `centre`, kept inside the frame."""
    return int(round(min(max(centre - crop_w / 2, 0), width - crop_w)))


# ---------------------------------------------------------------- shots

def cuts_from_angles(path: Path) -> list[float]:
    """Shot starts after the first, from an angles.json (exact cut times)."""
    data = json.loads(Path(path).read_text())
    starts = sorted(float(shot["start_s"]) for shot in data.get("shots", []))
    return [s for s in starts if s > 0]


def detect_cuts(video: Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Hard cuts by ffmpeg scene score (run on the proxy: small and constant-rate)."""
    result = run_command(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
                          "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"])
    return sorted(set(round(t, 3) for t in extract_scene_change_pts(result.stderr)))


def refine_cut(video: Path, around_s: float, window_s: float = 0.5,
               threshold: float = SCENE_THRESHOLD) -> float:
    """A proxy cut is only good to one proxy frame (0.2 s): find the exact frame in the full-rate cut."""
    start = max(0.0, around_s - window_s)
    result = run_command(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{2 * window_s:.3f}",
                          "-i", str(video), "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
                          "-an", "-f", "null", "-"])
    found = extract_scene_change_pts(result.stderr)
    if not found:
        return around_s
    return round(start + min(found, key=lambda t: abs(start + t - around_s)), 4)


def build_shots(cuts: list[float], duration_s: float, min_shot_s: float = 0.25) -> list[ShotFrame]:
    """Cut times → shots covering [0, duration). Cuts closer than `min_shot_s` collapse into one."""
    edges = [0.0]
    for cut in sorted(cuts):
        if min_shot_s <= cut <= duration_s - min_shot_s and cut - edges[-1] >= min_shot_s:
            edges.append(cut)
    edges.append(duration_s)
    return [ShotFrame(i, edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def samples_in(shot: ShotFrame, sample_fps: float, total: int) -> range:
    """Proxy samples whose time falls inside the shot."""
    first = max(0, math.ceil(shot.start_s * sample_fps - 1e-6))
    last = min(total, math.ceil(shot.end_s * sample_fps - 1e-6))
    return range(first, max(first, last))


# ---------------------------------------------------------------- subject

def choose_subject(boxes: list[Box], samples: range, frame_h: int) -> tuple[str | None, float, str]:
    """The person on screen most, and largest, in this shot; None when nobody qualifies."""
    if not samples:
        return None, 0.0, "empty shot"
    inside = [b for b in boxes if b.sample in samples]
    if not inside:
        return None, 0.0, "no person detected"
    by_id: dict[str, list[Box]] = {}
    for b in inside:
        by_id.setdefault(b.object_id, []).append(b)

    def median_height(items: list[Box]) -> float:
        heights = sorted(b.height for b in items)
        return heights[len(heights) // 2]

    scored = []
    for oid, items in by_id.items():
        presence = len({b.sample for b in items}) / len(samples)
        scored.append((presence * median_height(items), oid, presence, median_height(items)))
    scored.sort(reverse=True)
    _, oid, presence, height = scored[0]
    if presence < MIN_PRESENCE:
        return None, presence, f"best person on screen {presence:.0%} of the shot"
    if height < MIN_HEIGHT * frame_h:
        return None, presence, f"best person only {height / frame_h:.0%} of the frame tall"
    return oid, presence, f"{len(by_id)} person(s) seen; following the most present"


def subject_centres(boxes: list[Box], subject: str, samples: range) -> list[float | None]:
    """Box centre per sample for one subject (None where SAM lost them)."""
    by_sample = {b.sample: b.centre_x for b in boxes if b.object_id == subject and b.sample in samples}
    return [by_sample.get(s) for s in samples]


def fill_gaps(values: list[float | None], default: float) -> list[float]:
    """Linear interpolation across gaps; the ends hold the nearest known value."""
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return [default] * len(values)
    out = [0.0] * len(values)
    for i in range(len(values)):
        if values[i] is not None:
            out[i] = float(values[i])
            continue
        before = max((k for k in known if k < i), default=None)
        after = min((k for k in known if k > i), default=None)
        if before is None:
            out[i] = float(values[after])
        elif after is None:
            out[i] = float(values[before])
        else:
            w = (i - before) / (after - before)
            out[i] = float(values[before]) * (1 - w) + float(values[after]) * w
    return out


def smooth(values: list[float], sigma_samples: float) -> list[float]:
    """Zero-phase Gaussian smoothing with edge renormalisation (no lag, no pull toward zero)."""
    if sigma_samples <= 0 or len(values) < 3:
        return list(values)
    radius = max(1, int(round(3 * sigma_samples)))
    kernel = [math.exp(-0.5 * (k / sigma_samples) ** 2) for k in range(-radius, radius + 1)]
    out = []
    for i in range(len(values)):
        acc = weight = 0.0
        for k, w in zip(range(-radius, radius + 1), kernel):
            j = i + k
            if 0 <= j < len(values):
                acc += values[j] * w
                weight += w
        out.append(acc / weight)
    return out


def plan_centres(boxes: list[Box], shots: list[ShotFrame], total_samples: int, *,
                 width: int, height: int, sample_fps: float = PROXY_FPS,
                 smooth_s: float = SMOOTH_S) -> list[float]:
    """One smoothed centre per proxy sample; each shot is chosen and smoothed on its own."""
    centres = [width / 2] * total_samples
    for shot in shots:
        samples = samples_in(shot, sample_fps, total_samples)
        subject, presence, reason = choose_subject(boxes, samples, height)
        shot.presence = round(presence, 3)
        shot.reason = reason
        if subject is None:
            shot.subject, shot.method = None, "centre"
            continue
        shot.subject, shot.method = subject, "subject"
        track = fill_gaps(subject_centres(boxes, subject, samples), width / 2)
        for s, value in zip(samples, smooth(track, smooth_s * sample_fps)):
            centres[s] = value
    return centres


def frame_crop_xs(plan: ReframePlan, frame_count: int) -> list[int]:
    """Crop x for every output frame: linear between samples, never across a cut."""
    xs = []
    starts = [shot.start_s for shot in plan.shots]
    for f in range(frame_count):
        t = f / plan.fps
        shot = plan.shots[max(0, sum(1 for s in starts if s <= t) - 1)]
        samples = samples_in(shot, plan.sample_fps, len(plan.centres))
        if not samples:
            xs.append(crop_x(plan.width / 2, plan.width, plan.crop_w))
            continue
        pos = min(max(t * plan.sample_fps, samples.start), samples.stop - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, samples.stop - 1)
        w = pos - lo
        centre = plan.centres[lo] * (1 - w) + plan.centres[hi] * w
        xs.append(crop_x(centre, plan.width, plan.crop_w))
    return xs


# ---------------------------------------------------------------- ffmpeg

def proxy_command(src: Path, dst: Path, fps: int = PROXY_FPS, short_side: int = PROXY_SHORT_SIDE) -> list[str]:
    scale = f"scale='if(gt(iw,ih),-2,{short_side})':'if(gt(iw,ih),{short_side},-2)':flags=bicubic"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", "-an",
            "-vf", f"{scale},fps={fps},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-g", str(fps * 2), "-movflags", "+faststart", "-map_metadata", "-1", str(dst)]


def sendcmd_script(xs: list[int], fps: float, target: str = "crop@rf") -> str:
    """One command per frame where the crop moves (sendcmd applies it from that time on)."""
    lines = []
    previous = None
    for f, x in enumerate(xs):
        if x != previous:
            lines.append(f"{f / fps:.6f} {target} x {x};")
            previous = x
    return "\n".join(lines) + "\n"


def render_command(src: Path, out: Path, script: Path, plan: ReframePlan, first_x: int) -> list[str]:
    vf = (f"sendcmd=f='{script}',crop@rf=w={plan.crop_w}:h={plan.crop_h}:x={first_x}:y=0,"
          f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,format=yuv420p")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy",
            "-movflags", "+faststart", str(out)]


# ---------------------------------------------------------------- SAM

def estimate_usd(duration_s: float, sample_fps: float = PROXY_FPS, window_s: float = 10.0) -> float:
    """Windowed passes bill each window (50-frame minimum) plus 1 s overlaps."""
    windows = max(1, math.ceil(duration_s / (window_s - 1.0)))
    frames = max(duration_s * sample_fps + windows * sample_fps, windows * SAM_MIN_FRAMES)
    return round(frames * SAM_USD_PER_FRAME, 4)


def track_people(proxy: Path, *, frames: int, sample_fps: float, width: int, height: int,
                 max_usd: float, workdir: Path) -> tuple[list[Box], float, int, list[str]]:
    """SAM 3.1 "person" concept pass over the proxy; boxes scaled back to source pixels."""
    try:
        from guestlist.windows import segment_windowed
    except ImportError as error:
        raise SamUnavailable(str(error)) from error
    result = segment_windowed(proxy, "person", fps=sample_fps, frames=frames,
                              max_usd_per_call=max_usd, workdir=workdir)
    sam_w, sam_h = result.frame_size or (1, 1)
    sx, sy = width / sam_w, height / sam_h
    boxes = [Box(d.frame, d.object_id, (d.box[0] * sx, d.box[1] * sy, d.box[2] * sx, d.box[3] * sy), d.confidence)
             for d in result.detections]
    warnings = []
    if result.saturated_spans():
        warnings.append(f"{len(result.saturated_spans())} SAM window(s) hit the 16-person cap")
    return boxes, round(result.usd, 4), result.frames_billed, warnings


# ---------------------------------------------------------------- entry point

def run(*, video: Path, out_json: Path, render_path: Path | None = None, shots_path: Path | None = None,
        no_sam: bool = False, max_usd: float = 2.0, smooth_s: float = SMOOTH_S) -> dict:
    video = Path(video)
    info = probe_media(video)
    stream = next(s for s in info["streams"] if s.get("codec_type") == "video")
    width, height = int(stream["width"]), int(stream["height"])
    num, _, den = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate")).partition("/")
    fps = float(num) / float(den or 1)
    duration = float(info["format"]["duration"])
    if height > width:
        raise ValueError(f"{video.name} is already vertical ({width}x{height})")
    crop_w, crop_h = crop_size(width, height)
    workdir = Path(out_json).parent / f"{video.stem}.reframe"
    workdir.mkdir(parents=True, exist_ok=True)

    proxy = workdir / "proxy.mp4"
    run_command(proxy_command(video, proxy))
    total = int(math.floor(duration * PROXY_FPS))
    cuts = cuts_from_angles(shots_path) if shots_path else [refine_cut(video, t) for t in detect_cuts(proxy)]
    shots = build_shots(cuts, duration)
    warnings: list[str] = []
    method, spend, billed, boxes = "centre", 0.0, 0, []
    if not no_sam:
        estimate = estimate_usd(duration)
        if estimate > max_usd:
            raise ValueError(f"SAM pass estimated at ${estimate:.2f}, over the ${max_usd:.2f} cap (--max-usd)")
        try:
            boxes, spend, billed, sam_warnings = track_people(proxy, frames=total, sample_fps=PROXY_FPS, width=width,
                                                              height=height, max_usd=max_usd, workdir=workdir / "windows")
            warnings += sam_warnings
            method = "sam"
        except SamUnavailable as error:
            warnings.append(f"SAM unavailable ({error}); every shot centred")
    centres = plan_centres(boxes, shots, total, width=width, height=height, smooth_s=smooth_s)
    plan = ReframePlan(str(video), width, height, fps, duration, crop_w, crop_h, shots, centres,
                       method=method, spend_usd=spend, frames_billed=billed, warnings=warnings)
    payload = asdict(plan)
    payload["centres"] = [round(c, 1) for c in centres]
    if render_path:
        frame_count = int(round(duration * fps))
        xs = frame_crop_xs(plan, frame_count)
        script = workdir / "crop.cmd"
        script.write_text(sendcmd_script(xs, fps))
        run_command(render_command(video, Path(render_path), script, plan, xs[0] if xs else 0))
        payload["render"] = {"path": str(render_path), "size": [OUT_W, OUT_H], "frames": frame_count,
                             "crop_moves": script.read_text().count("\n")}
    write_json(Path(out_json), payload)
    return payload
