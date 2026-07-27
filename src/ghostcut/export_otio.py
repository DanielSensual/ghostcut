from __future__ import annotations

import json
from pathlib import Path

from .models import BrollOverlay, MediaClip, TimelineSegment


def _to_rational_time(seconds: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "value": round(seconds * fps),
        "rate": float(fps),
    }


def _to_time_range(start_s: float, duration_s: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _to_rational_time(start_s, fps),
        "duration": _to_rational_time(duration_s, fps),
    }


def _make_external_ref(source_path: str, duration_s: float, fps: float) -> dict:
    uri = Path(source_path).resolve().as_uri()
    return {
        "OTIO_SCHEMA": "ExternalReference.1",
        "target_url": uri,
        "available_range": _to_time_range(0.0, duration_s, fps),
        "metadata": {},
    }


def _clip_node(name: str, source_path: str, src_in_s: float, src_out_s: float, fps: float, duration_s: float, metadata: dict) -> dict:
    return {
        "OTIO_SCHEMA": "Clip.2",
        "name": name,
        "media_reference": _make_external_ref(source_path, duration_s, fps),
        "source_range": _to_time_range(src_in_s, max(0.0, src_out_s - src_in_s), fps),
        "metadata": metadata,
    }


def _gap_node(duration_s: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "Gap",
        "source_range": _to_time_range(0.0, max(0.0, duration_s), fps),
        "metadata": {},
    }


def _build_v1_track(clips_by_id: dict[str, MediaClip], timeline_segments: list[TimelineSegment], fps: float) -> dict:
    children: list[dict] = []
    for segment in sorted(timeline_segments, key=lambda item: item.timeline_in_s):
        clip = clips_by_id.get(segment.clip_id)
        if not clip:
            continue
        children.append(
            _clip_node(
                name=f"{segment.section} | {Path(clip.source_path).name}",
                source_path=clip.source_path,
                src_in_s=segment.src_in_s,
                src_out_s=segment.src_out_s,
                fps=fps,
                duration_s=clip.duration_s,
                metadata={
                    "ghostcut": {
                        "section": segment.section,
                        "role": "a_roll",
                        "timeline_in_s": segment.timeline_in_s,
                    }
                },
            )
        )

    return {
        "OTIO_SCHEMA": "Track.1",
        "name": "V1",
        "kind": "Video",
        "children": children,
        "metadata": {},
    }


def _build_v2_track(clips_by_id: dict[str, MediaClip], overlays: list[BrollOverlay], fps: float) -> dict:
    children: list[dict] = []
    cursor = 0.0

    for overlay in sorted(overlays, key=lambda item: item.timeline_in_s):
        clip = clips_by_id.get(overlay.clip_id)
        if not clip:
            continue

        if overlay.timeline_in_s > cursor:
            children.append(_gap_node(overlay.timeline_in_s - cursor, fps))
            cursor = overlay.timeline_in_s

        clip_duration = overlay.timeline_out_s - overlay.timeline_in_s
        children.append(
            _clip_node(
                name=f"BROLL | {Path(clip.source_path).name}",
                source_path=clip.source_path,
                src_in_s=overlay.src_in_s,
                src_out_s=overlay.src_out_s,
                fps=fps,
                duration_s=clip.duration_s,
                metadata={
                    "ghostcut": {
                        "section": overlay.section,
                        "role": "b_roll",
                        "reason": overlay.reason,
                        "base_clip_id": overlay.base_clip_id,
                        "timeline_in_s": overlay.timeline_in_s,
                    }
                },
            )
        )
        cursor += clip_duration

    return {
        "OTIO_SCHEMA": "Track.1",
        "name": "V2",
        "kind": "Video",
        "children": children,
        "metadata": {},
    }


def export_otio(
    clips: list[MediaClip],
    timeline_segments: list[TimelineSegment],
    output_path: Path,
    broll_overlays: list[BrollOverlay] | None = None,
    project_name: str = "GhostCut Auto Edit",
) -> dict:
    clips_by_id = {clip.id: clip for clip in clips}
    dominant_fps = 30.0
    fps_values = [round(clip.fps) for clip in clips if clip.fps > 0]
    if fps_values:
        dominant_fps = float(sorted(fps_values)[len(fps_values) // 2])

    tracks = [_build_v1_track(clips_by_id, timeline_segments, dominant_fps)]
    if broll_overlays:
        tracks.append(_build_v2_track(clips_by_id, broll_overlays, dominant_fps))

    timeline_payload = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": project_name,
        "global_start_time": _to_rational_time(0.0, dominant_fps),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "Tracks",
            "children": tracks,
            "metadata": {},
        },
        "metadata": {
            "ghostcut": {
                "fps": dominant_fps,
            }
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(timeline_payload, indent=2, sort_keys=True), encoding="utf-8")
    return timeline_payload
