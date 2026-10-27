"""GhostCut Director test run: a folder of on-camera clips -> Spark take log -> cut YouTube draft + FCPXML + SRT.

Inputs come from a work dir prepared by the proxy step (all_takes_proxy.mp4, clip_map.json, all_takes.srt):

    ~/Projects/guestlist/.venv/bin/python scripts/director_test_run.py organize <workdir>
    ~/Projects/guestlist/.venv/bin/python scripts/director_test_run.py cut <workdir>

organize calls muse-spark-1.3 once (cached by video hash + prompt), cut/export are local and free.
Spark judges delivery from picture + sound; Whisper lines supply every time (mode B from the Day-1 spike).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from director_spike_takes import DEVELOPER, LINES_SCHEMA, parse_srt  # noqa: E402

PROMPT = """These are a real-estate agent's raw on-camera clips for ONE YouTube house tour, joined end to end in shooting
order. She walks the home and talks to camera about the community, each room and its features. She often records the
same line or piece several times; some attempts trail off, stumble, restart, or include crew talk and setup.

Clip boundaries (seconds in this joined video): {clips}

Below is a numbered transcript (Whisper, may mishear words; [start-end] in seconds). Watch and listen to judge delivery,
eye line and energy; use the transcript only to locate things. Segment the WHOLE video in order: every transcript line
belongs to exactly one segment. Give each segment's first_line and last_line numbers instead of times.
- keeper_take: usable to-camera content for the tour.
- alternate_take: another attempt at a piece that is covered by a better attempt (same group id, pick_in_group false).
- flub_or_false_start, off_camera_or_pause, aside_to_editor (talk to crew/editor), setup_or_technical, dead_air_or_still.
For every group of attempts, mark exactly one pick_in_group true: the cleanest, most confident full delivery.
Every ungrouped keeper_take has pick_in_group true. topic = the room or subject in a few words.

TRANSCRIPT:
{transcript}"""


def load(work: Path):
    clip_map = json.loads((work / "clip_map.json").read_text())
    lines = parse_srt(work / "all_takes.srt")
    return clip_map, lines


def organize(work: Path, effort: str) -> None:
    from guestlist.meta import spark

    clip_map, lines = load(work)
    clips = "; ".join(f"{c['clip']} {c['offset']:.1f}-{c['offset'] + c['proxy_duration']:.1f}" for c in clip_map)
    transcript = "\n".join(f"{n} [{a:.1f}-{b:.1f}] {t}" for n, a, b, t in lines)
    r = spark.video_json(work / "all_takes_proxy.mp4", schema=LINES_SCHEMA, name="take_log_lines", developer=DEVELOPER,
                         prompt=PROMPT.format(clips=clips, transcript=transcript), effort=effort,
                         max_output_tokens=24000, max_usd=3.0)
    by_n = {n: (a, b) for n, a, b, _ in lines}
    segments, rejected = [], []
    for s in r.data["segments"]:
        first, last = by_n.get(s["first_line"]), by_n.get(s["last_line"])
        if not first or not last or s["first_line"] > s["last_line"]:
            rejected.append(s)  # kept for review instead of silently dropped
            continue
        segments.append({"t_start": first[0], "t_end": last[1], **s})
    segments.sort(key=lambda s: s["t_start"])
    out = {"model": spark.DEFAULT_MODEL, "effort": effort, "usd": round(r.usd, 4), "seconds": round(r.seconds, 1),
           "cached": r.cached, "summary": r.data["recording_summary"], "segments": segments, "rejected": rejected}
    (work / "take_log.json").write_text(json.dumps(out, indent=2))
    picks = [s for s in segments if s["kind"] == "keeper_take" and s["pick_in_group"]]
    print(json.dumps({"usd": out["usd"], "seconds": out["seconds"], "cached": r.cached, "segments": len(segments),
                      "picks": len(picks), "picked_s": round(sum(s["t_end"] - s["t_start"] for s in picks), 1),
                      "rejected": len(rejected)}, indent=2))


_CUE_WORDS = {"one", "two", "three", "four", "five", "go", "action", "rolling", "camera", "camera's", "cameras",
              "all", "right", "okay", "ok", "and", "yeah", "speed", "set", "ready", "1", "2", "3", "4", "5"}


def _norm(word: str) -> str:
    return word.strip().strip(".,!?\"'").lower()


def trim_cues(a: float, b: float, words: list[dict]) -> tuple[float, float]:
    """Move a take's edges past spoken countdowns / roll cues ("camera's rolling, three, two, one, go").

    Head: inside the first 8 s, if a run of cue words ends in "go"/"action", start at the next word. Tail: inside the
    last 6 s, if a cue run starts with "camera(s) rolling" or a 3-2-1 count, end before it. Mechanical, word-timed.
    """
    inside = [w for w in words if a - 0.05 <= w["start"] < b]
    head = [w for w in inside if w["start"] < a + 8.0]
    cut_at = None
    for i, w in enumerate(head):
        if _norm(w["word"]) in ("go", "action") and all(_norm(x["word"]) in _CUE_WORDS for x in head[max(0, i - 3):i]):
            cut_at = i
    if cut_at is not None and cut_at + 1 < len(inside):
        a = inside[cut_at + 1]["start"] - 0.08
    tail = [w for w in inside if w["start"] > b - 6.0]
    for i, w in enumerate(tail):
        n = _norm(w["word"])
        nxt = _norm(tail[i + 1]["word"]) if i + 1 < len(tail) else ""
        if (n.startswith("camera") and nxt == "rolling") or (n == "three" and nxt == "two"):
            run = tail[i:]
            if all(_norm(x["word"]) in _CUE_WORDS for x in run):
                b = (tail[i - 1]["end"] if i else w["start"]) + 0.05
            break
    return a, b


def to_pieces(segments: list[dict], clip_map: list[dict], lines, words: list[dict] | None = None) -> list[dict]:
    """Picked keeper spans in joined time -> per-source-clip pieces, padded into the surrounding silence."""
    starts = sorted(a for _, a, _, _ in lines)
    ends = sorted(b for _, _, b, _ in lines)
    pieces = []
    for s in segments:
        if s["kind"] != "keeper_take" or not s["pick_in_group"]:
            continue
        a, b = s["t_start"], s["t_end"]
        if words:
            a, b = trim_cues(a, b, words)
        if words:  # pad only into the silence next to the nearest spoken word
            prev_end = max((w["end"] for w in words if w["end"] <= a + 0.1), default=a - 1.0)
            next_start = min((w["start"] for w in words if w["start"] >= b - 0.1), default=b + 1.0)
        else:
            prev_end = max((e for e in ends if e <= a + 1e-6), default=a - 1.0)
            next_start = min((x for x in starts if x >= b - 1e-6), default=b + 1.0)
        a = max(a - 0.15, (prev_end + a) / 2)
        b = min(b + 0.30, (b + next_start) / 2)
        for c in clip_map:
            lo, hi = c["offset"], c["offset"] + c["proxy_duration"]
            x, y = max(a, lo), min(b, hi)
            spoken = sum(1 for w in words or [] if x <= w["start"] < y)
            fragment = x > a or y < b
            if y - x >= (2.0 if fragment else 0.4) and not (fragment and words and spoken < 4):  # drop crumbs
                pieces.append({"clip": c["clip"], "source": c["source"], "src_in": round(x - lo, 3),
                               "src_out": round(min(y - lo, c["duration"]), 3), "topic": s["topic"],
                               "reason": s["reason"], "first_line": s["first_line"], "last_line": s["last_line"]})
    if words:
        _drop_repeats(pieces, clip_map, words)
    return pieces


def _drop_repeats(pieces: list[dict], clip_map: list[dict], words: list[dict], n: int = 4) -> None:
    """If a piece ends with a line the next piece opens with (two takes of one line), end it before the repeat."""
    offset = {c["clip"]: c["offset"] for c in clip_map}

    def span(p):
        lo = offset[p["clip"]]
        return [w for w in words if lo + p["src_in"] - 0.05 <= w["start"] < lo + p["src_out"]]

    skip = {"the", "this", "a", "an", "that"}  # "welcome to the / this kitchen" is one line

    for cur, nxt in zip(pieces, pieces[1:]):
        tail = [w for w in span(cur)[-25:] if _norm(w["word"]) not in skip]
        head = [_norm(w["word"]) for w in span(nxt)[:25] if _norm(w["word"]) not in skip]
        grams = {tuple(head[i:i + n]) for i in range(len(head) - n + 1)}
        for i in range(len(tail) - n + 1):
            if tuple(_norm(w["word"]) for w in tail[i:i + n]) in grams:
                end = (tail[i - 1]["end"] if i else tail[i]["start"]) + 0.05 - offset[cur["clip"]]
                if end - cur["src_in"] >= 2.0:
                    cur["src_out"] = round(end, 3)
                break


def cut(work: Path) -> None:
    from ghostcut.export_fcpxml import export_fcpxml
    from ghostcut.models import MediaClip, TimelineSegment

    clip_map, lines = load(work)
    log = json.loads((work / "take_log.json").read_text())
    words_file = work / "all_takes_words.json"
    words = None
    if words_file.exists():
        words = [w for s in json.loads(words_file.read_text())["segments"] for w in s.get("words", [])]
    pieces = to_pieces(log["segments"], clip_map, lines, words)
    out = work / "render"
    out.mkdir(exist_ok=True)

    # Render each piece at 1080p, native 59.94, 10 ms audio fades so cuts don't click.
    listing = []
    for i, p in enumerate(pieces):
        f = out / f"piece_{i:03d}.mp4"
        dur = p["src_out"] - p["src_in"]
        if not f.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{p['src_in']:.3f}", "-t", f"{dur:.3f}", "-i", p["source"],
                            "-vf", "scale=1920:1080,format=yuv420p", "-r", "60000/1001", "-c:v", "libx264", "-preset", "fast",
                            "-crf", "19", "-af", f"afade=t=in:d=0.01,afade=t=out:st={max(0, dur - 0.01):.3f}:d=0.01",
                            "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(f)], check=True)
        listing.append(f"file '{f}'")
    (out / "list.txt").write_text("\n".join(listing) + "\n")
    joined = out / "joined.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(out / "list.txt"),
                    "-c", "copy", str(joined)], check=True)
    final = work / "ghostcut_test_v2.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1:LRA=11",
                    "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)], check=True)

    # Timeline (same picks) for Final Cut / Resolve, and captions in output time.
    clips, segs, srt, t = {}, [], [], 0.0
    for p in pieces:
        c = next(c for c in clip_map if c["clip"] == p["clip"])
        clips.setdefault(p["clip"], MediaClip(p["clip"], p["source"], "", "", c["duration"], 60000 / 1001, True, 3840, 2160))
        segs.append(TimelineSegment(p["topic"], p["clip"], p["src_in"], p["src_out"], round(t, 3)))
        lo = c["offset"]
        for n, a, b, text in lines:
            if p["first_line"] <= n <= p["last_line"]:
                x, y = max(a - lo, p["src_in"]), min(b - lo, p["src_out"])
                if y > x:
                    srt.append((t + x - p["src_in"], t + y - p["src_in"], text))
        t += p["src_out"] - p["src_in"]
    export_fcpxml(list(clips.values()), segs, work / "ghostcut_test_v2.fcpxml",
                  project_name="GhostCut test v2")

    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    (work / "ghostcut_test_v2.srt").write_text(
        "\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(srt, 1)))
    (work / "pieces.json").write_text(json.dumps(pieces, indent=1))
    print(json.dumps({"pieces": len(pieces), "duration_s": round(t, 1), "video": str(final)}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["organize", "cut"])
    ap.add_argument("work", type=Path)
    ap.add_argument("--effort", default="medium")
    args = ap.parse_args()
    organize(args.work, args.effort) if args.step == "organize" else cut(args.work)


if __name__ == "__main__":
    main()
