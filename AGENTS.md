# GhostCut — Agent Guide

AI-assisted video editing pipeline. Pure Python ≥3.11 (stdlib-only core, no required deps) + ffmpeg/ffprobe on PATH. One Node script (`scripts/ghost-captions.cjs`) for captions.

## Zero to running

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                                      # core has zero dependencies
python -m unittest discover -s tests -p 'test_*.py'   # <1s, needs no ffmpeg or media
```

`pip install -e .[recap]` adds opencv+numpy for the recap module. The integration test and recap tests self-skip when their prerequisites (sample media, extras) are absent — a green run with skips is normal.

## Architecture

Two pipelines share one package:

- **Rough-cut** (`cli.py: run/ingest/analyze/assemble/export`): `ingest.py` (discovery, proxy/audio extraction, layer classification) → `shot_analysis.py` + `audio_analysis.py` + `scoring.py` → `assembler.py` + `story_template.py` + `broll.py` → `export_fcpxml.py` / `export_otio.py`. `review.py` collects low-confidence decisions. All artifacts are JSON files under the project dir (see `config.py` for paths).
- **Recap** (`cli.py: recap`, package `recap/`): `beat.py` (BPM/phase comb-fit) → `motion.py` (frame-diff peaks, whip-pan rejection) → `faces.py` (YuNet frontality, SFace embeddings) → `casting.py`/`select.py` (slot patterns, person exclusion) → `stabilize.py` (vid.stab two-pass) → `render.py` (beat-offset cuts, xfade).
- `adapters/` — pluggable transcribers (`null`, `openai`). `models.py` holds dataclasses; artifacts serialize via `dataclass_to_dict`.
- `scripts/ghost-captions.cjs` — self-contained Node caption engine (Whisper words JSON → ASS karaoke-pop → ffmpeg burn). Treat as stable; extend via new style presets rather than rewrites.

## Conventions

- **Core stays dependency-free.** Heavy or optional features go behind extras with guarded imports (`try: import cv2 … except ImportError`) and `@unittest.skipUnless` tests — copy the pattern in `tests/test_recap_beat.py`.
- API-keyed features (OpenAI transcriber) read env vars only; never write keys into code, docs, or examples.
- `examples/` is gitignored — never commit media files or client footage. Tests must not require media to pass.
- Type-annotated, `from __future__ import annotations`, dataclasses over dicts at module boundaries.

## Hard-won domain gotchas (do not relearn these)

- **Rosetta/Intel ffmpeg builds can silently corrupt h264 encode/decode** (macroblock garbage, intermittent). `recap/stabilize.py` therefore pipes rawvideo/y4m through the vidstab binary and lets the native ffmpeg do all codec work — keep that structure.
- vidstabtransform: cap `maxshift` below the oversize-crop margin and use `crop=black`; `crop=keep` ghost-smears when subjects fool the detector. Proven params: 25% oversize, `smoothing=30`, `maxshift=110`, `maxangle=0.05`, `optzoom=0`.
- `settb=AVTB` on both inputs before `xfade`, or you get timebase-mismatch errors.
- Fit BPM/phase **locally in the section you're cutting** (chorus), not from the song intro — grids drift. `--chorus-at` exists for manual pinning.
- ffmpeg filtergraphs: a labeled pad is consumed once — `split`/`asplit` before reusing (e.g. sidechain + mix).
- Captions: time against the FINAL cut, never source clips. Whisper on long/quiet audio hallucinates repeated lines — trust the first coherent occurrence.
- SFace cosine similarity on real event footage: same person ≥ ~0.41, strangers ≤ ~0.32; the 0.365 exclusion threshold sits deliberately between.
- drawtext/libfreetype cannot render color emoji — keep emoji out of burned titles.

## Verify before you claim done

`python -m unittest discover -s tests -p 'test_*.py'` must be green. For behavior changes in ffmpeg-facing code, run the relevant command against a short real clip and eyeball the output — filter-graph regressions rarely show up in unit tests.
