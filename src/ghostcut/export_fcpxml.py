from __future__ import annotations

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .ffmpeg_tools import seconds_to_fcpxml_time, snap_frame_rate
from .models import BrollOverlay, MediaClip, TimelineSegment

# Cross-dissolve duration in seconds added between different sections.
_DISSOLVE_DURATION = 0.5


def _dominant_fps(clips: list[MediaClip]) -> float:
    fps_values = [snap_frame_rate(clip.fps) for clip in clips if clip.fps > 0]
    if not fps_values:
        return 30.0
    return float(Counter(fps_values).most_common(1)[0][0])


def _majority_orientation(clips: list[MediaClip]) -> tuple[int, int]:
    if not clips:
        return (1920, 1080)
    portrait = sum(1 for clip in clips if clip.height > clip.width)
    if portrait >= (len(clips) / 2):
        return (1080, 1920)
    return (1920, 1080)


def _prettify(element: ET.Element) -> str:
    ET.indent(element, space="  ")
    xml_bytes = ET.tostring(element, encoding="utf-8", xml_declaration=True)
    xml_text = xml_bytes.decode("utf-8")
    return xml_text.replace("?>", "?>\n<!DOCTYPE fcpxml>", 1)


def _find_overlays_for_segment(
    segment: TimelineSegment,
    overlays: list[BrollOverlay],
) -> list[BrollOverlay]:
    """Return overlays whose timeline range falls within a spine segment."""
    seg_start = segment.timeline_in_s
    seg_end = seg_start + (segment.src_out_s - segment.src_in_s)
    matches = []
    for overlay in overlays:
        if overlay.timeline_in_s >= seg_start and overlay.timeline_out_s <= seg_end + 0.01:
            matches.append(overlay)
    return matches


def export_fcpxml(
    clips: list[MediaClip],
    timeline_segments: list[TimelineSegment],
    output_path: Path,
    broll_overlays: list[BrollOverlay] | None = None,
    project_name: str = "GhostCut Auto Edit",
) -> str:
    clip_by_id = {clip.id: clip for clip in clips}
    used_clip_ids = []
    for segment in timeline_segments:
        if segment.clip_id in clip_by_id and segment.clip_id not in used_clip_ids:
            used_clip_ids.append(segment.clip_id)
    for overlay in broll_overlays or []:
        if overlay.clip_id in clip_by_id and overlay.clip_id not in used_clip_ids:
            used_clip_ids.append(overlay.clip_id)

    used_clips = [clip_by_id[clip_id] for clip_id in used_clip_ids]
    fps = _dominant_fps(used_clips or clips)
    width, height = _majority_orientation(used_clips or clips)

    root = ET.Element("fcpxml", attrib={"version": "1.11"})
    resources = ET.SubElement(root, "resources")

    format_id = "r1"
    ET.SubElement(
        resources,
        "format",
        attrib={
            "id": format_id,
            "name": "GhostCutFormat",
            "frameDuration": seconds_to_fcpxml_time(1.0 / fps, fps),
            "width": str(width),
            "height": str(height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    # Register a cross-dissolve effect resource
    dissolve_effect_id = "dissolve1"
    ET.SubElement(
        resources,
        "effect",
        attrib={
            "id": dissolve_effect_id,
            "name": "Cross Dissolve",
            "uid": ".../Transitions.localized/Dissolves.localized/Cross Dissolve.localized/Cross Dissolve.motr",
        },
    )

    # Each source keeps its own rate and size; the sequence format is only reused when they match.
    sequence_key = (snap_frame_rate(fps), width, height)
    format_ids: dict[tuple, str] = {sequence_key: format_id}

    def source_format(clip: MediaClip) -> str:
        if clip.fps <= 0 or clip.width <= 0 or clip.height <= 0:
            return format_id
        key = (snap_frame_rate(clip.fps), clip.width, clip.height)
        if key not in format_ids:
            format_ids[key] = f"f{len(format_ids)}"
            ET.SubElement(
                resources,
                "format",
                attrib={
                    "id": format_ids[key],
                    "frameDuration": seconds_to_fcpxml_time(1.0 / clip.fps, clip.fps),
                    "width": str(clip.width),
                    "height": str(clip.height),
                    "colorSpace": "1-1-1 (Rec. 709)",
                },
            )
        return format_ids[key]

    asset_ref: dict[str, str] = {}
    for idx, clip in enumerate(used_clips, start=2):
        asset_id = f"r{idx}"
        asset_ref[clip.id] = asset_id
        asset_format = source_format(clip)
        asset_fps = clip.fps if asset_format != format_id else fps
        source_uri = Path(clip.source_path).resolve().as_uri()
        asset_el = ET.SubElement(
            resources,
            "asset",
            attrib={
                "id": asset_id,
                "name": Path(clip.source_path).name,
                "uid": clip.id,
                "start": "0s",
                "duration": seconds_to_fcpxml_time(clip.duration_s, asset_fps),
                "hasVideo": "1",
                "hasAudio": "1" if clip.has_audio else "0",
                "format": asset_format,
            },
        )
        ET.SubElement(
            asset_el,
            "media-rep",
            attrib={
                "kind": "original-media",
                "src": source_uri,
            },
        )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", attrib={"name": "GhostCut"})
    project = ET.SubElement(event, "project", attrib={"name": project_name})

    total_duration = 0.0
    for segment in timeline_segments:
        total_duration = max(total_duration, segment.timeline_in_s + (segment.src_out_s - segment.src_in_s))
    for overlay in broll_overlays or []:
        total_duration = max(total_duration, overlay.timeline_out_s)

    sequence = ET.SubElement(
        project,
        "sequence",
        attrib={
            "format": format_id,
            "duration": seconds_to_fcpxml_time(total_duration, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    # Build spine: interleave asset-clips and cross-dissolve transitions
    previous_section: str | None = None

    for i, segment in enumerate(timeline_segments):
        clip = clip_by_id.get(segment.clip_id)
        if not clip:
            continue
        ref = asset_ref.get(segment.clip_id)
        if not ref:
            continue

        duration_s = max(0.0, segment.src_out_s - segment.src_in_s)

        # Insert cross-dissolve when section changes
        # (the effect is referenced by a child filter-video; <transition> itself has no ref attribute)
        if previous_section is not None and segment.section != previous_section:
            transition = ET.SubElement(
                spine,
                "transition",
                attrib={
                    "name": "Cross Dissolve",
                    "offset": seconds_to_fcpxml_time(segment.timeline_in_s - _DISSOLVE_DURATION / 2, fps),
                    "duration": seconds_to_fcpxml_time(_DISSOLVE_DURATION, fps),
                },
            )
            ET.SubElement(transition, "filter-video", attrib={"ref": dissolve_effect_id, "name": "Cross Dissolve"})

        # Determine audio role based on layer
        clip_layer = getattr(clip, "layer", "a-roll")
        audio_role = "dialogue" if clip_layer == "a-roll" and clip.has_audio else "effects"

        asset_clip = ET.SubElement(
            spine,
            "asset-clip",
            attrib={
                "name": f"{segment.section} | {Path(clip.source_path).name}",
                "ref": ref,
                "offset": seconds_to_fcpxml_time(segment.timeline_in_s, fps),
                "start": seconds_to_fcpxml_time(segment.src_in_s, fps),
                "duration": seconds_to_fcpxml_time(duration_s, fps),
                "audioRole": audio_role,
            },
        )

        # DTD order: adjustments → connected clips → markers
        # 1) Attach B-roll overlays as connected clips (children of the spine item)
        seg_overlays = _find_overlays_for_segment(segment, broll_overlays or [])
        for overlay in seg_overlays:
            broll_clip = clip_by_id.get(overlay.clip_id)
            if not broll_clip:
                continue
            broll_ref = asset_ref.get(overlay.clip_id)
            if not broll_ref:
                continue

            overlay_duration = max(0.0, overlay.src_out_s - overlay.src_in_s)
            # Offset relative to the parent clip's start
            local_offset = overlay.timeline_in_s - segment.timeline_in_s

            connected = ET.SubElement(
                asset_clip,
                "asset-clip",
                attrib={
                    "name": f"B-Roll | {overlay.section} | {Path(broll_clip.source_path).name}",
                    "ref": broll_ref,
                    "lane": "1",
                    "offset": seconds_to_fcpxml_time(segment.src_in_s + local_offset, fps),
                    "start": seconds_to_fcpxml_time(overlay.src_in_s, fps),
                    "duration": seconds_to_fcpxml_time(overlay_duration, fps),
                    "audioRole": "effects",
                },
            )

            # Mute B-roll audio — adjust-volume is an EMPTY element with amount attr
            ET.SubElement(
                connected,
                "adjust-volume",
                attrib={"amount": "-96dB"},
            )

        # 2) Section marker AFTER connected clips (DTD requires this order)
        ET.SubElement(
            asset_clip,
            "marker",
            attrib={
                "start": seconds_to_fcpxml_time(segment.src_in_s, fps),
                "duration": seconds_to_fcpxml_time(1.0 / fps, fps),
                "value": segment.section,
            },
        )

        previous_section = segment.section

    xml_output = _prettify(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_output, encoding="utf-8")
    return xml_output
