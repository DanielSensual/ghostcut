"""Build the prototype's demo project (app/ui/demo/) from a finished Director job.

    python app/make_demo.py --video final_phone.mp4 --voice voice_only.mp4 --music score.wav \
        --facts facts.json --transcript whisper.json --inventory inventory.json --title "My tour" \
        --section "Intro=:0:12.813" --section "Community=<work dir>:12.813:199.9" ... \
        [--takes NAME=<work dir>] [--extra findings_and_chat.json]

Each --section is NAME=WORKDIR:START:LENGTH (WORKDIR may be empty). A work dir contributes its newest
`*_broll.fcpxml` (talk clips + B-roll), `broll_map.json` (tags → files), `broll_plan.json`
(shot descriptions + why each insert was placed) and, with --takes, its take log. --transcript is Whisper JSON
(word timestamps) of the finished cut, so every time in the project is on the final timeline.
Everything written lands in app/ui/demo/, which is gitignored: demo footage and names stay local.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np

OUT = Path(__file__).parent / "ui" / "demo"


def secs(v: str | None) -> float:
    if not v:
        return 0.0
    return float(Fraction(v.rstrip("s")))


def fcpxml_clips(path: Path) -> tuple[list[dict], list[dict], float]:
    """Talk clips on the spine and B-roll connected above them, in the file's own timeline seconds."""
    root = ET.parse(path).getroot()
    spine = root.find(".//spine")
    talk, broll, end = [], [], 0.0
    for el in spine:
        if el.tag != "asset-clip":
            continue
        off, start, dur = secs(el.get("offset")), secs(el.get("start")), secs(el.get("duration"))
        topic, _, src = el.get("name", "").rpartition(" | ")
        talk.append({"t": off, "d": dur, "src": src, "in": start, "topic": topic})
        end = max(end, off + dur)
        for ch in el.findall("asset-clip"):
            if ch.get("lane") == "1":
                name = ch.get("name", "").split(" | ")
                broll.append({"t": off + secs(ch.get("offset")) - start, "d": secs(ch.get("duration")),
                              "src": name[-1], "in": secs(ch.get("start")), "topic": name[1] if len(name) > 2 else ""})
    return talk, broll, end


def envelope(media: str, step: float = 0.5) -> list[float]:
    """RMS per step seconds, mapped from -60..0 dBFS to 0..1."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", media, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = int(8000 * step)
    a = a[: len(a) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((a ** 2).mean(1)) + 1e-9)
    return [round(float(x), 2) for x in np.clip((db + 60) / 60, 0, 1)]


def srt_lines(path: Path) -> dict[int, str]:
    out = {}
    for block in path.read_text().strip().split("\n\n"):
        rows = block.splitlines()
        if len(rows) >= 3 and rows[0].strip().isdigit():
            out[int(rows[0])] = " ".join(rows[2:])
    return out


def takes(work: Path, section: str) -> tuple[list[dict], dict]:
    log = json.loads((work / "take_log.json").read_text())
    clip_map = json.loads((work / "clip_map.json").read_text())
    lines = srt_lines(work / "all_takes.srt")
    rows = []
    for s in log.get("segments", []):
        clip = next((c for c in clip_map if c["offset"] <= s["t_start"] < c["offset"] + c["duration"]), None)
        text = " ".join(lines.get(i, "") for i in range(s["first_line"], s["last_line"] + 1))
        rows.append({"section": section, "kind": s["kind"], "topic": s.get("topic", ""), "group": s.get("group", ""),
                     "pick": s.get("pick_in_group", False), "reason": s.get("reason", ""),
                     "conf": s.get("confidence"), "clip": clip["clip"].rsplit(".", 1)[0][-4:] if clip else "",
                     "in": round(s["t_start"] - clip["offset"], 2) if clip else s["t_start"],
                     "d": round(s["t_end"] - s["t_start"], 2), "text": text[:400]})
    return rows, {k: log.get(k) for k in ("model", "effort", "usd", "seconds")}


def transcript(path: Path) -> list[dict]:
    """Whisper JSON → [{t, e, text, w: [[word, start, end], ...]}] on the final timeline."""
    out = []
    for s in json.loads(path.read_text())["segments"]:
        words = [[w["word"].strip(), round(w["start"], 2), round(w["end"], 2)] for w in s.get("words", [])]
        out.append({"t": round(s["start"], 2), "e": round(s["end"], 2), "text": s["text"].strip(), "w": words})
    return out


def shot_library(works: list[Path], inventory: Path | None) -> tuple[dict, dict]:
    """tag → {tag, what, room, q, skip, file, best}; and clip filename → tag."""
    lib, tag_of = {}, {}
    for work in works:
        bm = work / "broll_map.json"
        for e in json.loads(bm.read_text()) if bm.exists() else []:
            tag_of[e["clip"]] = e["tag"]
            lib.setdefault(e["tag"], {"tag": e["tag"], "file": e["source"], "dur": e.get("duration")})
        plan = work / "broll_plan.json"
        for s in json.loads(plan.read_text()).get("raw", {}).get("shots", []) if plan.exists() else []:
            if s["tag"] in lib:
                lib[s["tag"]].update(what=s.get("description", ""), room=s.get("room", ""), q=s.get("quality"),
                                     best=[s.get("best_start", 0), s.get("best_end", 0)])
    if inventory and inventory.exists():
        for s in json.loads(inventory.read_text()).get("data", {}).get("catalog", []):
            if s["tag"] in lib:
                use = s.get("use_for", "")
                lib[s["tag"]].update(what=s.get("what", ""), use=use, q=s.get("quality"),
                                     skip="skip" in use.lower(),
                                     best=[s.get("best_start", 0), s.get("best_end", 0)])
    return lib, tag_of


def placement_reasons(work: Path) -> dict[str, list[str]]:
    plan = work / "broll_plan.json"
    out: dict[str, list[str]] = {}
    for p in json.loads(plan.read_text()).get("placed", []) if plan.exists() else []:
        out.setdefault(p["tag"], []).append(p.get("reason", ""))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--voice")
    ap.add_argument("--music")
    ap.add_argument("--facts")
    ap.add_argument("--transcript")
    ap.add_argument("--inventory")
    ap.add_argument("--title", default="Untitled project")
    ap.add_argument("--section", action="append", default=[])
    ap.add_argument("--takes", action="append", default=[], help="NAME=WORKDIR for take logs")
    ap.add_argument("--extra", help="JSON merged into the project (findings, chat, media bins)")
    ap.add_argument("--skip-media", action="store_true", help="reuse the thumbnails and poster already in demo/")
    ap.add_argument("--poster-at", type=float, default=0.0, help="seconds into the video for the poster frame")
    args = ap.parse_args()

    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    video = OUT / "video.mp4"
    if video.is_symlink() or video.exists():
        video.unlink()
    video.symlink_to(os.path.abspath(args.video))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                args.video], capture_output=True, text=True).stdout)
    if not args.skip_media:
        # Poster: shown before the video loads (pick a title frame with --poster-at).
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{args.poster_at:.2f}", "-i", args.video, "-frames:v", "1", "-q:v", "3",
                        str(OUT / "poster.jpg")], check=True)
        # One thumbnail every 2 s; clips pick the frame nearest their midpoint.
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.video, "-vf", "fps=1/2,scale=192:108",
                        "-q:v", "5", str(OUT / "thumbs" / "%04d.jpg")], check=True)

    specs = []
    for spec in args.section:
        name, _, rest = spec.partition("=")
        work, start, length = rest.rsplit(":", 2)
        specs.append((name, Path(work) if work else None, float(start), float(length)))
    lib, tag_of = shot_library([w for _, w, _, _ in specs if w], Path(args.inventory) if args.inventory else None)

    sections, talk, broll = [], [], []
    for name, work, start, length in specs:
        sections.append({"name": name, "t": start, "d": length})
        fx = max(work.glob("*_broll.fcpxml"), key=lambda f: f.stat().st_mtime, default=None) if work else None
        if work and not fx:
            raise SystemExit(f"{work}: no *_broll.fcpxml (run the broll step first)")
        if fx:
            t, b, end = fcpxml_clips(fx)
            k = length / end if end else 1.0  # the B-roll pass retimes slightly; fit the section
            reasons = placement_reasons(work)
            for c in t:
                talk.append({**c, "t": round(start + c["t"] * k, 2), "d": round(c["d"] * k, 2), "section": name})
            for c in b:
                tag = tag_of.get(c["src"], Path(c["src"]).stem)
                why = reasons.get(tag, [])
                broll.append({**c, "tag": tag, "t": round(start + c["t"] * k, 2), "d": round(c["d"] * k, 2),
                              "section": name, "reason": why.pop(0) if why else ""})
        else:
            talk.append({"t": start, "d": length, "src": "cold open", "in": 0, "topic": name, "section": name})
    for prefix, items in (("t", talk), ("b", broll)):
        for i, it in enumerate(items, 1):
            it["id"] = f"{prefix}{i}"
    used = {b["tag"] for b in broll}
    for tag, e in lib.items():
        e["used"] = tag in used

    take_rows, take_meta = [], {}
    for spec in args.takes:
        name, _, work = spec.partition("=")
        rows, take_meta = takes(Path(work), name)
        take_rows += rows

    cards = []
    if args.facts:
        for i, c in enumerate(json.loads(Path(args.facts).read_text())["cards"], 1):
            first = min(at for _, at in c["lines"])
            cards.append({"id": f"c{i}", "t": first, "d": round(c["until"] - first, 2),
                          "caption": c.get("caption", ""), "lines": [txt for txt, _ in c["lines"]]})

    project = {"title": args.title, "duration": round(dur, 3), "sections": sections, "talk": talk,
               "broll": broll, "cards": cards, "takes": take_rows, "takeMeta": take_meta,
               "library": sorted(lib.values(), key=lambda e: e["tag"]),
               "transcript": transcript(Path(args.transcript)) if args.transcript else [],
               "env": {"mix": envelope(args.video)}, "envStep": 0.5}
    if args.voice:
        project["env"]["voice"] = envelope(args.voice)
    if args.music:
        project["env"]["music"] = envelope(args.music)
    if args.extra:
        project.update(json.loads(Path(args.extra).read_text()))
    (OUT / "project.json").write_text(json.dumps(project))
    (OUT / "project.js").write_text("window.PROJECT = " + json.dumps(project) + ";\n")
    print(json.dumps({"sections": len(sections), "talk": len(talk), "broll": len(broll), "cards": len(cards),
                      "takes": len(take_rows), "library": len(lib), "words": sum(len(s["w"]) for s in project["transcript"]),
                      "duration": round(dur, 1)}))


if __name__ == "__main__":
    main()
