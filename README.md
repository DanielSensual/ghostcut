# 👻 GhostCut

[![CI](https://github.com/DanielSensual/ghostcut/actions/workflows/ci.yml/badge.svg)](https://github.com/DanielSensual/ghostcut/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

**AI-assisted video editing that ends in your NLE, not a black box.**

GhostCut is an open-source editing pipeline built on ffmpeg and pure Python (the core has zero required dependencies). It does the tedious 80% — ingest, proxies, analysis, shot selection, rough assembly — and hands you editable timelines, finished beat-synced reels, or viral-style captions.

Three tools in one repo:

1. **Rough-cut pipeline** — ingest footage → analyze → assemble an A-roll cut → export **Final Cut Pro FCPXML** and **OTIO** linked to your full-res sources
2. **`ghostcut recap`** — a finished beat-synced music recap reel from raw event footage: beat-grid fitting, motion-peak selection, face-aware casting, stabilization, crossfade render
3. **Ghost Captions** — word-by-word highlight captions (CapCut/Hormozi style) as burned-in video or `.ass` subtitles for FCP/Premiere/Resolve

Built by [Ghost AI Systems](https://ghostaisystems.com).

## Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026): what is new

**GhostCut Director** is the hackathon project. It turns a shoot day into an organized, cut, B-rolled video and
explains every decision, using Meta's Muse models.

**Pre-existing (before the event, not part of the entry's new work):**
- GhostCut itself: developed privately from 2026-02-11 (that history is not published), public since 2026-07-27 (`8472e7b`, v0.2.0),
  last pre-event commit 2026-08-27 (`53004e6`). Everything described below this section — the rough-cut
  pipeline, recap reels, Ghost Captions, and the FCPXML/OTIO exporters and data classes that Director reuses.

**Built during the event:**
- [`guestlist.meta`](https://github.com/DanielSensual/guestlist) (first commit 2026-10-26 14:46 ET): cached Meta
  Model API clients, the spend ledger and its hard budget stop.
- `scripts/director_spike_takes.py` (2026-10-27): Spark take-log spike scored against a hand log.
- `scripts/director_test_run.py` (committed 2026-10-27/28, changed since): organize, cut, refine, B-roll planning
  and render, QC, cold open, fact cards, music mix; plus fixes to the pre-existing exporter (2026-10-27).
- `app/` (2026-10-29): the Director Mac app — engine, Muse edit proposals with mechanical checks, an
  accept/undo edit layer, transcript, timeline UI — and `tests/test_app_muse.py`.

**Meta models used:** `muse-spark-1.3` (take logs, B-roll plans, QC, footage inventory, Muse edits),
`muse-wonder-1.0` (QC and an opening review) and `muse-voice-transcribe-1.0` (one diarization check).
Director does not use SAM 3.1 or SAM Audio.
**Non-Meta tools:** local Whisper (`mlx-whisper`), ffmpeg, pywebview, and in the sample film ElevenLabs
(music and voice isolation). The brand art in `docs/brand/` was made with ChatGPT image generation. Development
used an AI coding assistant (Claude Code; see commit trailers).

**Not built yet:** rendering accepted Muse edits, the live shoot-date import scan, the Deliver export
buttons, SAM Audio remastering.
**Not in this repo yet:** the work-dir prep (360p proxy, `clip_map.json`, Whisper SRT and words) that `organize`
reads; the Spark footage inventory, the Wonder opening review and the Voice Transcribe check were one-off calls
during development.
**Sample project:** the app is shown with a project built locally by `app/make_demo.py` from a finished client job.
It is not in the repo (`app/ui/demo/` is gitignored), so a fresh clone opens the app with no project. The sample's
opening chat, review findings, media bins and spend rows are hand-written examples, labeled as such in the UI.

**Running Director:** macOS on Apple Silicon (mlx-whisper, the system Avenir Next font, pywebview), ffmpeg, and
`pip install numpy pillow pywebview mlx-whisper`. Meta calls go through
[guestlist](https://github.com/DanielSensual/guestlist) (`pip install -e` a checkout of it) with a Meta Model API
key in `MODEL_API_KEY`; `mix --isolate` also needs `ELEVENLABS_API_KEY`. Without guestlist the app still opens and
Muse answers with an error.
Design, status and how to run it: [docs/APP-DESIGN.md](docs/APP-DESIGN.md).

## Requirements

- `ffmpeg` + `ffprobe` on PATH (macOS/Linux; GhostCut Director needs macOS on Apple Silicon, see above)
- Python ≥ 3.11
- Optional: Node.js for Ghost Captions, `[recap]` extra for recap reels

## Install

```bash
git clone https://github.com/DanielSensual/ghostcut.git
cd ghostcut
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # core: zero dependencies
pip install -e ".[recap]"     # + opencv/numpy for recap reels
pip install -e ".[openai]"    # + OpenAI Whisper API transcription (optional)
```

## Rough-cut pipeline

```bash
ghostcut run --input /path/to/footage --output /path/to/project
# or step by step:
ghostcut ingest   --input /path/to/footage --output /path/to/project
ghostcut analyze  --project /path/to/project --transcriber null --lang en
ghostcut assemble --project /path/to/project --target-duration 90
ghostcut export   --project /path/to/project
```

Clips are classified a-roll / b-roll / drone from folder and filename conventions, proxied (H.264 30fps), analyzed for silence, audio issues, shot boundaries, and visual quality, then assembled against a story template. Everything lands as inspectable JSON plus `timeline/edit.fcpxml` and `timeline/edit.otio.json`; low-confidence decisions go to `reports/review_queue.json` for human review.

## Beat-synced recap reels

```bash
ghostcut recap \
  --footage event-cam-a.mov --footage event-cam-b.mov \
  --song track.wav \
  --output recap.mp4 \
  --duration 20 \
  --exclude-clip person-to-leave-out.mov
```

What it does under the hood:

- **Beat grid** — comb-fit BPM + phase locally around the chorus (auto-detected, or pin with `--chorus-at`), so cuts land on real downbeats instead of drifting from an intro-extrapolated grid
- **Motion peaks** — frame-diff scan of proxies surfaces dips, spins, and high-energy moments; whip-pans are rejected via phase correlation
- **Face-aware casting** — YuNet frontality scoring avoids backs-to-camera picks; `--exclude-clip` uses SFace embeddings to keep a specific person out of the edit (repeatable)
- **Stabilization** — two-pass vid.stab with tuned parameters (disable with `--no-stabilize`)
- **Render** — shots offset so their motion peak hits a beat, crossfades centered on cut boundaries, `--versions N --strategies cast,classic,energy` renders differently-edited alternatives

Face-detection models download automatically to `~/.cache/ghostcut/models/` on first use.

## Ghost Captions

Word-by-word highlight captions with an active-word color-and-scale pop:

```bash
# Local word timestamps (recommended): any Whisper with word timestamps, e.g.
mlx_whisper --word-timestamps True final-cut.mp4   # accepts {words:[...]} or segments[].words

node scripts/ghost-captions.cjs --video final-cut.mp4 --words words.json --output captioned.mp4
node scripts/ghost-captions.cjs --video final-cut.mp4 --words words.json --ass-only --output captions.ass   # for FCP/Resolve
```

Styles: `captions`, `hormozi`, `neon`, `fire`, `ghost`, `bold`, `minimal` — plus `--highlight` color presets, `--grouping` words per group (default 5 for `captions`, 3 otherwise), `--position`, `--pop` scale. Without `--words` it transcribes via the OpenAI Whisper API (`OPENAI_API_KEY` required); with `--words` it runs fully offline. Run captions on the **final** cut, not source clips — trims shift word timing.

## Output structure

```
manifest/media_manifest.json      analysis/transcript.json
analysis/shots.json               analysis/segment_scores.json
timeline/timeline_plan.json       timeline/broll_plan.json
timeline/edit.fcpxml              timeline/edit.otio.json
reports/review_queue.json         reports/run_report.json
```

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Runs in well under a second with no media files and no ffmpeg required; recap tests self-skip unless the `[recap]` extra is installed. CI runs both configurations.

## Status & roadmap

Honest state of things: the rough-cut pipeline is reliable infrastructure, not creative polish — scoring is ffmpeg-derived heuristics, story structure is template + keyword bias, b-roll overlay planning is heuristic, and audio is analyzed (silence/clipping/RMS) but not yet cleaned. The recap module is the most evolved path and produces finished output. Not built yet:

- [ ] Semantic scene labeling routed into scoring (adapter exists for GPT-4o experiments)
- [ ] Rendered audio cleanup stems
- [ ] FCPXML/OTIO conform verification tool
- [ ] `ghostcut.toml` config file for reproducible runs
- [ ] Caption transcription via local Whisper built into the CLI

Issues and PRs welcome.

## License

[MIT](LICENSE) © 2026 Daniel Castillo (Ghost AI Systems). The GhostCut name and the artwork in `docs/brand/` are
not covered by the MIT License; see [docs/brand/NOTICE.md](docs/brand/NOTICE.md).
