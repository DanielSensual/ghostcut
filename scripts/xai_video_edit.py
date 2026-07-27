#!/usr/bin/env python3
"""
video_edit.py — xAI Grok Imagine Video Editor CLI
──────────────────────────────────────────────────
Usage:
    python video_edit.py <source> --prompt "Change my hoodie to red" [options]

Source can be:
    • A Streamable URL:  https://streamable.com/abc123
    • A local .mp4 file: ~/Downloads/clip.mp4

Options:
    -s, --start     Start time in seconds (default: 0)
    -d, --duration  Clip duration in seconds (default: 8, max 8.7)
    -p, --prompt    Edit prompt (required)
    -o, --output    Output directory (default: ~/Downloads/Videos)
    --no-upscale    Skip the 1080p upscale step
    --no-trim       Skip trimming (use source as-is, must be ≤8.7s)
"""

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
if not XAI_API_KEY:
    print("  ❌  XAI_API_KEY environment variable is required")
    sys.exit(1)
XAI_API_BASE = "https://api.x.ai/v1"
LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
USER_AGENT = "GrokImagine/1.0"
MAX_EDIT_DURATION = 8.7  # xAI hard limit for video editing


# ─── Helpers ─────────────────────────────────────────────────────────────────


def header(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}\n")


def step(icon: str, msg: str):
    print(f"  {icon}  {msg}")


def fail(msg: str):
    print(f"\n  ❌  {msg}\n")
    sys.exit(1)


def run_ffmpeg(args: list[str], label: str = "ffmpeg") -> str:
    """Run ffmpeg and return stderr output (where ffmpeg logs)."""
    cmd = ["ffmpeg", "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{label} failed:\n{result.stderr[-500:]}")
    return result.stderr


def get_video_duration(filepath: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"ffprobe failed on {filepath}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_video_resolution(filepath: str) -> tuple[int, int]:
    """Get video width x height using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return (0, 0)
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return (0, 0)
    return (streams[0].get("width", 0), streams[0].get("height", 0))


# ─── Step 1: Resolve Source ──────────────────────────────────────────────────


def resolve_source(source: str, work_dir: str) -> str:
    """Download from Streamable or copy local file to work_dir. Returns path."""
    source = source.strip()

    # Local file
    if os.path.isfile(os.path.expanduser(source)):
        local_path = os.path.expanduser(source)
        dest = os.path.join(work_dir, "source.mp4")
        shutil.copy2(local_path, dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        step("📁", f"Local file: {local_path} ({size_mb:.1f} MB)")
        return dest

    # Streamable URL
    streamable_match = re.search(r"streamable\.com/(\w+)", source)
    if streamable_match:
        video_id = streamable_match.group(1)
        step("🌐", f"Streamable video ID: {video_id}")

        # Fetch the Streamable page to extract the direct video URL
        page_url = f"https://streamable.com/{video_id}"
        req = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8")
        except Exception as e:
            fail(f"Failed to fetch Streamable page: {e}")

        # Extract direct mp4 URL from page source
        mp4_match = re.search(r'"url"\s*:\s*"(https://[^"]*\.mp4[^"]*)"', html)
        if not mp4_match:
            # Fallback: construct CDN URL directly
            direct_url = f"https://cdn-cf-east.streamable.com/video/mp4/{video_id}.mp4"
            step("⚠️", "Could not extract direct URL, trying CDN fallback")
        else:
            direct_url = mp4_match.group(1).replace("\\u0026", "&")

        step("📥", f"Downloading from Streamable...")
        dest = os.path.join(work_dir, "source.mp4")
        try:
            req = urllib.request.Request(direct_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                with open(dest, "wb") as f:
                    total = 0
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
        except Exception as e:
            fail(f"Download failed: {e}")

        size_mb = os.path.getsize(dest) / (1024 * 1024)
        duration = get_video_duration(dest)
        step("✅", f"Downloaded: {size_mb:.1f} MB, {duration:.1f}s")
        return dest

    # Direct URL
    if source.startswith("http"):
        step("📥", f"Downloading from URL...")
        dest = os.path.join(work_dir, "source.mp4")
        try:
            req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
        except Exception as e:
            fail(f"Download failed: {e}")

        size_mb = os.path.getsize(dest) / (1024 * 1024)
        step("✅", f"Downloaded: {size_mb:.1f} MB")
        return dest

    fail(f"Cannot resolve source: {source}")


# ─── Step 2: Trim ───────────────────────────────────────────────────────────


def trim_video(source: str, start: float, duration: float, work_dir: str) -> str:
    """Trim video to specified segment. Returns path to trimmed file."""
    output = os.path.join(work_dir, "trimmed.mp4")

    step("✂️", f"Trimming: start={start}s, duration={duration}s")

    if duration > MAX_EDIT_DURATION:
        step("⚠️", f"Duration {duration}s exceeds xAI max ({MAX_EDIT_DURATION}s), capping")
        duration = MAX_EDIT_DURATION

    run_ffmpeg([
        "-ss", str(start),
        "-i", source,
        "-t", str(duration),
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-an",
        output,
    ], "Trim")

    size_mb = os.path.getsize(output) / (1024 * 1024)
    actual_dur = get_video_duration(output)
    w, h = get_video_resolution(output)
    step("✅", f"Trimmed: {actual_dur:.1f}s, {w}x{h}, {size_mb:.1f} MB")
    return output


# ─── Step 3: Upload to Litterbox ─────────────────────────────────────────────


def upload_to_litterbox(filepath: str) -> str:
    """Upload file to Litterbox (temp Catbox host). Returns public URL."""
    step("📤", "Uploading to Litterbox (72h expiry)...")

    filename = os.path.basename(filepath)
    mime_type = mimetypes.guess_type(filepath)[0] or "video/mp4"

    with open(filepath, "rb") as f:
        file_data = f.read()

    boundary = "----VideoEditBoundary"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="reqtype"\r\n\r\n',
        b"fileupload\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="time"\r\n\r\n',
        b"72h\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_data,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])

    req = urllib.request.Request(
        LITTERBOX_API,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            url = r.read().decode("utf-8").strip()
    except Exception as e:
        fail(f"Litterbox upload failed: {e}")

    if not url or not url.startswith("http"):
        fail(f"Litterbox returned invalid URL: {url!r}")

    # Validate upload integrity
    try:
        head_req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(head_req, timeout=10) as r:
            content_length = int(r.headers.get("Content-Length", 0))
            if content_length == 0:
                fail(f"Upload verification failed: Content-Length is 0 for {url}")
            step("✅", f"Upload verified: {url} ({content_length / (1024*1024):.1f} MB)")
    except urllib.error.HTTPError as e:
        fail(f"Upload verification failed: HTTP {e.code} for {url}")

    return url


# ─── Step 4: Submit to xAI ──────────────────────────────────────────────────


def submit_edit(video_url: str, prompt: str) -> str:
    """Submit video edit to xAI and poll for result. Returns output video URL."""
    step("🎬", f"Submitting to xAI: \"{prompt}\"")

    payload = json.dumps({
        "model": "grok-imagine-video",
        "prompt": prompt,
        "video_url": video_url,
    })

    req = urllib.request.Request(
        f"{XAI_API_BASE}/videos/generations",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")[:500]
        fail(f"xAI API error {e.code}: {body}")

    request_id = result.get("request_id") or result.get("id")

    # Check if video is immediately available
    immediate_url = result.get("video", {}).get("url")
    if immediate_url:
        step("✅", f"Video ready immediately!")
        return immediate_url

    if not request_id:
        fail(f"No request_id in response: {json.dumps(result, indent=2)}")

    step("⏳", f"Processing (request: {request_id[:12]}...)")

    # Poll for completion
    start_time = time.time()
    for i in range(200):  # up to 10 minutes
        req = urllib.request.Request(
            f"{XAI_API_BASE}/videos/{request_id}",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))

            url = data.get("video", {}).get("url")
            if url:
                elapsed = time.time() - start_time
                step("✅", f"Edit complete in {elapsed:.0f}s")
                return url

            status = data.get("status", data.get("state", "processing"))
            if status in ("failed", "error"):
                fail(f"xAI edit failed: {data.get('error', json.dumps(data, indent=2))}")

            elapsed = time.time() - start_time
            print(f"     ⏳ {elapsed:.0f}s elapsed...", end="\r")

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")[:300]
            fail(f"Poll error {e.code}: {body}")

        time.sleep(3)

    fail("Timed out waiting for xAI (10 minutes)")


# ─── Step 5: Download Result ────────────────────────────────────────────────


def download_result(video_url: str, work_dir: str) -> str:
    """Download the edited video from xAI. Returns local path."""
    step("📥", "Downloading edited video from xAI...")

    dest = os.path.join(work_dir, "edited_720p.mp4")
    req = urllib.request.Request(video_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        fail(f"Download failed: {e}")

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    w, h = get_video_resolution(dest)
    step("✅", f"Downloaded: {w}x{h}, {size_mb:.1f} MB")
    return dest


# ─── Step 6: Upscale ────────────────────────────────────────────────────────


def upscale_video(source: str, work_dir: str) -> str:
    """Upscale video to 1080p using ffmpeg Lanczos filter."""
    w, h = get_video_resolution(source)
    if w == 0 or h == 0:
        step("⚠️", "Could not detect resolution, skipping upscale")
        return source

    # Determine target: for portrait 9:16, target height=1920; for landscape, height=1080
    if h > w:
        # Portrait (9:16) — scale height to 1920
        target_h = 1920
        if h >= target_h:
            step("ℹ️", f"Already {w}x{h}, no upscale needed")
            return source
        scale_filter = f"scale=-2:{target_h}:flags=lanczos"
        step("🔍", f"Upscaling {w}x{h} → ~{int(w * target_h / h)}x{target_h} (Lanczos)")
    else:
        # Landscape (16:9) — scale height to 1080
        target_h = 1080
        if h >= target_h:
            step("ℹ️", f"Already {w}x{h}, no upscale needed")
            return source
        scale_filter = f"scale=-2:{target_h}:flags=lanczos"
        step("🔍", f"Upscaling {w}x{h} → ~{int(w * target_h / h)}x{target_h} (Lanczos)")

    output = os.path.join(work_dir, "edited_1080p.mp4")
    run_ffmpeg([
        "-i", source,
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "slow",
        output,
    ], "Upscale")

    size_mb = os.path.getsize(output) / (1024 * 1024)
    new_w, new_h = get_video_resolution(output)
    step("✅", f"Upscaled: {new_w}x{new_h}, {size_mb:.1f} MB")
    return output


# ─── Step 7: Save Output ────────────────────────────────────────────────────


def save_output(source: str, output_dir: str, prompt: str) -> str:
    """Copy final video to output directory with descriptive name."""
    os.makedirs(output_dir, exist_ok=True)

    # Generate filename from prompt
    slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower().strip())[:40].strip("_")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"xai_edit_{slug}_{timestamp}.mp4"
    dest = os.path.join(output_dir, filename)

    shutil.copy2(source, dest)
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    step("💾", f"Saved: {dest} ({size_mb:.1f} MB)")
    return dest


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="xAI Grok Imagine Video Editor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python video_edit.py "https://streamable.com/abc123" \\
    -s 9 -d 8 -p "Change my hoodie to red"

  python video_edit.py ~/Downloads/clip.mp4 \\
    -p "Make the car blue" --no-trim
        """,
    )
    parser.add_argument("source", help="Streamable URL or local .mp4 file")
    parser.add_argument("-p", "--prompt", required=True, help="Edit prompt")
    parser.add_argument("-s", "--start", type=float, default=0, help="Start time (seconds)")
    parser.add_argument("-d", "--duration", type=float, default=8, help="Duration (seconds, max 8.7)")
    parser.add_argument("-o", "--output", default=os.path.expanduser("~/Downloads/Videos"), help="Output directory")
    parser.add_argument("--no-upscale", action="store_true", help="Skip 1080p upscale")
    parser.add_argument("--no-trim", action="store_true", help="Skip trimming")

    args = parser.parse_args()

    header("xAI Grok Imagine Video Editor")

    # Create temp work directory
    work_dir = tempfile.mkdtemp(prefix="xai_video_edit_")
    step("📂", f"Workspace: {work_dir}")

    try:
        # Step 1: Resolve source
        header("Step 1 — Resolve Source")
        source_path = resolve_source(args.source, work_dir)

        # Step 2: Trim
        if args.no_trim:
            duration = get_video_duration(source_path)
            if duration > MAX_EDIT_DURATION:
                fail(f"Video is {duration:.1f}s but xAI max is {MAX_EDIT_DURATION}s. Use --start/-s and --duration/-d to trim.")
            step("⏭️", f"Skipping trim (source is {duration:.1f}s)")
            trimmed_path = source_path
        else:
            header("Step 2 — Trim")
            trimmed_path = trim_video(source_path, args.start, args.duration, work_dir)

        # Step 3: Upload
        header("Step 3 — Upload")
        public_url = upload_to_litterbox(trimmed_path)

        # Step 4: Submit to xAI
        header("Step 4 — xAI Edit")
        result_url = submit_edit(public_url, args.prompt)

        # Step 5: Download result
        header("Step 5 — Download Result")
        edited_path = download_result(result_url, work_dir)

        # Step 6: Upscale
        if args.no_upscale:
            step("⏭️", "Skipping upscale")
            final_path = edited_path
        else:
            header("Step 6 — Upscale to 1080p")
            final_path = upscale_video(edited_path, work_dir)

        # Step 7: Save
        header("Step 7 — Save")
        output_path = save_output(final_path, args.output, args.prompt)

        # Summary
        header("Done! 🎉")
        step("🔗", f"xAI URL:  {result_url}")
        step("💾", f"Local:    {output_path}")
        w, h = get_video_resolution(output_path)
        step("📐", f"Resolution: {w}x{h}")

    finally:
        # Cleanup work directory
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
