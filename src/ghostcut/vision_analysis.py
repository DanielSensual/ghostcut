from __future__ import annotations

import base64
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

from .ffmpeg_tools import run_command
from .models import MediaClip, VisionAnalysis

# Scene labels the vision model should output — matches story section keywords
SCENE_LABELS = (
    "kitchen", "living_room", "bedroom", "bathroom", "exterior",
    "pool", "aerial", "hallway", "garage", "dining_room",
    "foyer", "patio", "laundry", "office", "other",
)

# Similarity threshold: 0-64 hamming distance scale, lower = more similar
_DUPLICATE_THRESHOLD = 10  # out of 64 bits


def extract_keyframes(clip: MediaClip, frames_dir: Path, interval: float = 2.0) -> list[Path]:
    """Extract keyframes from a clip's proxy at the given interval (seconds)."""
    clip_frames_dir = frames_dir / clip.id
    clip_frames_dir.mkdir(parents=True, exist_ok=True)

    # Check if frames already extracted
    existing = sorted(clip_frames_dir.glob("*.jpg"))
    if existing:
        return existing

    proxy = Path(clip.proxy_path)
    if not proxy.exists():
        return []

    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i", str(proxy),
            "-vf", f"fps=1/{interval}",
            "-q:v", "3",
            "-frames:v", "50",  # cap at 50 frames per clip
            str(clip_frames_dir / "frame_%04d.jpg"),
        ],
        check=False,
    )

    return sorted(clip_frames_dir.glob("*.jpg"))


def _compute_phash(frame_path: Path) -> int | None:
    """Compute a 64-bit perceptual hash using FFmpeg to resize + grayscale."""
    # Use subprocess directly — stdout is raw binary pixels, not text
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i", str(frame_path),
            "-vf", "scale=8:8,format=gray",
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )

    raw_bytes = proc.stdout
    if len(raw_bytes) < 64:
        return None

    pixels = list(raw_bytes[:64])
    avg = sum(pixels) / 64.0

    # Build 64-bit hash: 1 if pixel > average, 0 otherwise
    hash_val = 0
    for i, px in enumerate(pixels):
        if px > avg:
            hash_val |= (1 << i)

    return hash_val


def _phash_hex(hash_val: int) -> str:
    return f"{hash_val:016x}"


def _hamming_distance(h1: int, h2: int) -> int:
    """Count differing bits between two hashes."""
    xor = h1 ^ h2
    count = 0
    while xor:
        count += xor & 1
        xor >>= 1
    return count


def compute_clip_phash(frames: list[Path]) -> str:
    """Compute a representative perceptual hash for a clip using its middle keyframe."""
    if not frames:
        return "0" * 16
    # Use the middle frame as representative
    mid = frames[len(frames) // 2]
    hashed = _compute_phash(mid)
    return _phash_hex(hashed if hashed is not None else 0)


def detect_duplicates(
    clip_hashes: dict[str, int | None],
) -> dict[str, str]:
    """Find duplicate clips by comparing perceptual hashes. Returns {duplicate_id: original_id}."""
    duplicates: dict[str, str] = {}
    clip_ids = list(clip_hashes.keys())

    for i in range(len(clip_ids)):
        if clip_ids[i] in duplicates:
            continue
        hash_i = clip_hashes[clip_ids[i]]
        if hash_i is None:
            continue
        for j in range(i + 1, len(clip_ids)):
            if clip_ids[j] in duplicates:
                continue
            hash_j = clip_hashes[clip_ids[j]]
            if hash_j is None:
                continue
            dist = _hamming_distance(hash_i, hash_j)
            if dist <= _DUPLICATE_THRESHOLD:
                duplicates[clip_ids[j]] = clip_ids[i]

    return duplicates


def _classify_scene_openai(frame_paths: list[Path]) -> tuple[str, float, str]:
    """Use OpenAI GPT-4o to classify a scene with cinematic assessment."""
    try:
        import openai
    except ImportError:
        return "other", 0.5, "openai not installed"

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "other", 0.5, "no API key"

    # Pick up to 3 frames: start, middle, end
    selected = []
    if len(frame_paths) >= 3:
        selected = [frame_paths[0], frame_paths[len(frame_paths) // 2], frame_paths[-1]]
    else:
        selected = frame_paths[:3]

    # Encode frames as base64 — use detail:high for GPT-4o's full resolution analysis
    image_contents = []
    for fp in selected:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        image_contents.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        })

    labels_str = ", ".join(SCENE_LABELS)
    prompt = (
        "You are a senior Director of Photography reviewing footage from a luxury real estate property tour. "
        "These keyframes are from the SAME video clip. Analyze them like a seasoned editor would.\n\n"
        "Return a SINGLE JSON object with these fields:\n"
        f"- scene: the primary room/location — one of [{labels_str}]\n"
        "- quality: float 0.0-1.0 based on CINEMATIC merit:\n"
        "  * 0.9-1.0: Hero shot. Magazine-worthy framing, golden hour or dramatic lighting, "
        "leading lines, perfectly balanced composition\n"
        "  * 0.7-0.89: Strong B-roll. Clean composition, good natural light, "
        "professional-grade but not exceptional\n"
        "  * 0.5-0.69: Serviceable. Acceptable exposure and focus, but flat lighting, "
        "awkward framing, or cluttered background\n"
        "  * 0.3-0.49: Weak. Unflattering angles, harsh shadows, motion blur, "
        "distracting elements in frame\n"
        "  * 0.0-0.29: Unusable. Out of focus, overexposed, shaky, or captured mid-movement\n"
        "- mood: the visual mood (e.g., 'warm and inviting', 'bright and airy', 'moody and dramatic', "
        "'clinical', 'cozy intimate')\n"
        "- composition: describe the shot composition style (e.g., 'wide establishing', "
        "'medium close-up on countertop', 'low-angle hero', 'handheld tracking', "
        "'static tripod', 'slow pan reveal')\n"
        "- notes: practical editing recommendation — would you use this as an opener, "
        "a detail insert, a transition filler, or skip it entirely? Be specific.\n\n"
        "Be RUTHLESSLY honest about quality. Most real estate video footage is mediocre — "
        "only truly exceptional shots should score above 0.85.\n"
    )

    client = openai.OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        *image_contents,
                    ],
                }
            ],
            max_tokens=400,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.startswith("```")]
            text = "\n".join(lines).strip()

        # Parse JSON response
        data = json.loads(text)

        # Handle array response (take first element)
        if isinstance(data, list):
            data = data[0] if data else {}

        scene = str(data.get("scene", "other")).lower().replace(" ", "_")
        if scene not in SCENE_LABELS:
            scene = "other"
        quality = max(0.0, min(1.0, float(data.get("quality", 0.5))))

        # Build rich notes from all the cinematic fields
        mood = data.get("mood", "")
        composition = data.get("composition", "")
        edit_notes = data.get("notes", "")
        notes_parts = []
        if mood:
            notes_parts.append(f"Mood: {mood}")
        if composition:
            notes_parts.append(f"Shot: {composition}")
        if edit_notes:
            notes_parts.append(edit_notes)
        notes = " | ".join(notes_parts) if notes_parts else ""

        return scene, quality, notes
    except Exception as e:
        return "other", 0.5, f"vision error: {e}"


def _classify_scene_null(frames: list[Path]) -> tuple[str, float, str]:
    """Fallback: no classification, neutral score."""
    return "other", 0.5, "no vision model"


def analyze_vision(
    clips: list[MediaClip],
    frames_dir: Path,
    vision_backend: str = "null",
) -> list[VisionAnalysis]:
    """Run vision analysis on all clips: keyframe extraction, hashing, scene classification."""
    results: list[VisionAnalysis] = []
    clip_frames: dict[str, list[Path]] = {}
    clip_hashes_int: dict[str, int | None] = {}

    # Step 1: Extract keyframes and compute hashes
    print(f"  Vision: extracting keyframes for {len(clips)} clips...")
    for clip in clips:
        frames = extract_keyframes(clip, frames_dir)
        clip_frames[clip.id] = frames

        if frames:
            mid = frames[len(frames) // 2]
            clip_hashes_int[clip.id] = _compute_phash(mid)
        else:
            clip_hashes_int[clip.id] = None

    # Step 2: Detect duplicates by perceptual hash similarity
    print(f"  Vision: detecting duplicates across {len(clip_hashes_int)} clips...")
    duplicates = detect_duplicates(clip_hashes_int)

    # Step 3: Classify scenes
    classify_fn = _classify_scene_openai if vision_backend == "openai" else _classify_scene_null
    total = len(clips)

    for idx, clip in enumerate(clips):
        frames = clip_frames.get(clip.id, [])
        raw_hash = clip_hashes_int.get(clip.id)
        phash_hex = _phash_hex(raw_hash if raw_hash is not None else 0)
        dup_of = duplicates.get(clip.id, "")

        if vision_backend == "openai" and frames:
            print(f"  Vision: classifying {idx + 1}/{total} — {clip.id[:20]}...")
            scene, quality, notes = classify_fn(frames)
        else:
            scene, quality, notes = _classify_scene_null(frames)

        # Penalize duplicates
        if dup_of:
            quality *= 0.3
            notes = f"DUPLICATE of {dup_of}. {notes}"

        results.append(
            VisionAnalysis(
                clip_id=clip.id,
                scene_label=scene,
                quality_score=round(quality, 4),
                composition_notes=notes,
                phash=phash_hex,
                is_duplicate_of=dup_of,
            )
        )

    dup_count = len(duplicates)
    print(f"  Vision: complete. {dup_count} duplicates found across {total} clips.")
    return results
