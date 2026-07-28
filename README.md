# 👻 GhostCut

[![CI](https://github.com/DanielSensual/ghostcut/actions/workflows/ci.yml/badge.svg)](https://github.com/DanielSensual/ghostcut/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

**AI-assisted video editing that ends in your NLE, not a black box.**

GhostCut is an open-source editing pipeline built on ffmpeg and pure Python (zero required dependencies). It does the tedious 80% — ingest, proxies, analysis, shot selection, rough assembly — and hands you editable timelines, finished beat-synced reels, or viral-style captions.

Three tools in one repo:

1. **Rough-cut pipeline** — ingest footage → analyze → assemble an A-roll cut → export **Final Cut Pro FCPXML** and **OTIO** linked to your full-res sources
2. **`ghostcut recap`** — a finished beat-synced music recap reel from raw event footage: beat-grid fitting, motion-peak selection, face-aware casting, stabilization, crossfade render
3. **Ghost Captions** — word-by-word highlight captions (CapCut/Hormozi style) as burned-in video or `.ass` subtitles for FCP/Premiere/Resolve

Built by [Ghost AI Systems](https://ghostaisystems.com).

## Requirements

- `ffmpeg` + `ffprobe` on PATH (macOS/Linux)
- Python ≥ 3.11
- Optional: Node.js for Ghost Captions, `[recap]` extra for recap reels

## Install

```bash
git clone https://github.com/DanielSensual/ghostcut.git
cd ghostcut
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # core: zero dependencies
pip install -e .[recap]     # + opencv/numpy for recap reels
pip install -e .[openai]    # + OpenAI Whisper API transcription (optional)
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
mlx_whisper --word-timestamps True final-cut.mp4   # then flatten to {words:[{word,start,end}]}

node scripts/ghost-captions.cjs --video final-cut.mp4 --words words.json --output captioned.mp4
node scripts/ghost-captions.cjs --video final-cut.mp4 --ass-only --output captions.ass   # for FCP/Resolve
```

Styles: `captions`, `hormozi`, `neon`, `fire`, `ghost`, `bold`, `minimal` — plus `--highlight` color presets, `--grouping 2|3|4` words, `--position`, `--pop` scale. Without `--words` it transcribes via the OpenAI Whisper API (`OPENAI_API_KEY` required); with `--words` it runs fully offline. Run captions on the **final** cut, not source clips — trims shift word timing.

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

[MIT](LICENSE) © 2026 Daniel Castillo (Ghost AI Systems)
