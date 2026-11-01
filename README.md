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
- Render v1 (2026-10-30): `app/render.py`, `render_plan.py`, `render_voice.py`, `render_verify.py`, the render
  endpoints and UI in the app, and `tests/test_render_*.py`, `test_engine_render.py`,
  `test_make_demo_timeline.py`. It re-renders only the sections that accepted edits touch, rebuilds the voice
  sample-exact from the section audio and the cached voice isolation (no paid call), redoes the fact cards, music
  mix and phone copy, and promotes the new version only after its measured checks pass. Revert goes back one version.
- Multicam sync (2026-11-01): `src/ghostcut/sync.py`, the `ghostcut sync` command and `tests/test_sync.py`. It
  places clips from several cameras on one timeline by timecode, capture clock, audio waveform and Whisper words,
  cross-checks the methods and reports clock drift. No Meta model is involved in sync.
- Multicam angle choice (2026-11-01): `src/ghostcut/angles.py`, the `ghostcut angles` command and
  `tests/test_angles.py`. Muse Spark looks at a frame from every camera on each spoken line and picks the angle;
  mechanical checks overrule it, and the cut renders under one uncut audio track. **Designed by Meta's Muse Code
  agent on Muse Wonder and built by Muse Code on Muse Spark**, then reviewed and fixed by Claude Code; who did what
  is in [docs/muse-code/BUILD-LOG.md](docs/muse-code/BUILD-LOG.md).
- Per-camera grading (2026-11-01): `src/ghostcut/grade.py`, the `ghostcut grade` command, `angles --grade` and
  `tests/test_grade.py`. Converts S-Log3 and iPhone HLG to Rec.709 with LUTs generated from the published curves,
  matches every camera's exposure and white balance to a reference, and can ask Muse Spark to check the match (it
  reports, it never adjusts). Designed by Muse Code on Muse Wonder, built by Muse Code on Muse Spark, reviewed, fixed
  and live-tested by Claude Code (same build log).

**Meta models used:** `muse-spark-1.3` (take logs, B-roll plans, QC, footage inventory, Muse edits, multicam
angle picks, grade checks), `muse-wonder-1.0` (QC and an opening review) and `muse-voice-transcribe-1.0` (one diarization check).
**Meta tools used:** the Muse Code agent (design and build of `ghostcut angles` and `ghostcut grade`).
Director does not use SAM 3.1 or SAM Audio.
**Non-Meta tools:** local Whisper (`mlx-whisper`), ffmpeg, pywebview, and in the sample film ElevenLabs
(music and voice isolation). The brand art in `docs/brand/` was made with ChatGPT image generation. Development
used an AI coding assistant (Claude Code; see commit trailers).

**Not built yet:** cutting talk (Render v1 can't, so Muse blocks `cut_span`), the multicam cut and grade in the Director app and
its FCPXML, PQ (HDR10) footage in `grade`, the live shoot-date import scan, the
Deliver export buttons, SAM Audio remastering.
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
Muse answers with an error. Render needs no key: `python app/render.py plan` shows what the next render would do,
`run` renders it, `revert` goes back (the app's Render button does the same).
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

## Multicam sync

```bash
pip install -e ".[sync]"      # numpy, for waveform sync
ghostcut sync a-cam/C0001.MP4 b-cam/C0412.MP4 iphone/IMG_0081.MOV --out project/sync.json
```

Every clip gets an offset on one timeline (the longest clip sits at 0; change it with `--reference`), measured
up to four ways and cross-checked:

- **timecode** — start timecode from the file or Sony's XML sidecar, NTSC and drop-frame aware. Exact only when the
  cameras were jammed; free-running timecode is reported and flagged when it disagrees.
- **clock** — the capture time in the metadata (the iPhone's capture date, not the copy time). Coarse; it narrows
  a search or places a silent clip, flagged for a check by eye.
- **waveform** — log speech envelopes cross-correlated at 10 ms, refined to 1 ms, then re-measured in 20 s windows
  to report clock drift (ppm) and the offset at the clip's first frame.
- **whisper** — three-word phrases that occur once in each transcript vote for an offset (local `mlx_whisper`, or
  `--transcripts DIR` of word-timed Whisper JSON).

A waveform peak can be confident and wrong, so when Whisper (or, for a weak peak, timecode or the clock) disagrees,
the waveform is searched again inside the window that method allows. `sync.json` keeps every estimate, every
pairwise check and every warning, plus each camera's make, model, lens and gamma (`s-log3-cine`, `hlg`, …) for
grading. A clip the reference barely hears is tried against the clips already placed.

Then cut between the angles:

```bash
ghostcut angles --sync project/sync.json --words project/sync-transcripts/C0001-<size>.json \
  --out project/angles.json --render project/multicam.mp4
```

The words of the audio clip (the reference unless `--audio B`) are split into lines. For each line, every camera
that covers it gives one frame, and Muse Spark picks the angle with a reason (needs
[guestlist](https://github.com/DanielSensual/guestlist) and `MODEL_API_KEY`; `--no-muse` alternates cameras by rule
instead). Then the checks decide: a camera that does not cover the line is overruled, a shot shorter than
`--min-shot` (2 s) joins a neighbour that covers it, cuts sit between words on whole frames, and a span no camera
covers is black, never skipped. The render goes shot by shot under one uncut audio track.

Grade the cameras to match before the render:

```bash
ghostcut grade --sync project/sync.json --out project/grade/grade.json [--lut A=house-look.cube] [--check]
ghostcut angles ... --grade project/grade/grade.json --render project/multicam.mp4
```

S-Log3 (S-Gamut3 / S-Gamut3.Cine) and HLG get a 33-point LUT generated from the published curves (Sony's S-Log3,
ITU-R BT.2100 HLG with BT.2408 exposure) and gamut matrices derived from the primaries, with a soft highlight
shoulder; Rec.709 needs none; PQ is not supported yet. `--lut LABEL=PATH` uses your own LUT for a camera instead.
Then each camera's exposure and white balance are matched to the reference (`--reference`, default the sync
reference) from centre-frame samples at moments both cameras recorded, as pure gains (black stays black), clamped
and reported before and after. `--check` asks Muse Spark which camera still stands out; it only reports.

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
