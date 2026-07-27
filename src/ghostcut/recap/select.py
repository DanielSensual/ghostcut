"""Candidate scoring and slot assignment for recap reels.

Selection strategies:
- "classic": balanced face/motion score, no-repeat variety
- "energy":  motion-first, no-repeat variety
- "cast":    coverage objective — every distinct person/couple in the cast
             gets a shot before anyone repeats
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .casting import CastMember, assign_cluster
from .faces import EXCLUDE_COSINE, FaceEngine, frame_at
from .motion import find_motion_peaks, frame_brightness, pan_metrics

# a whip-pan is a LARGE global shift moving CONSISTENTLY in one direction.
# Shift alone over-rejects: a camera tracking a dancer also shifts, but it
# oscillates (low directional coherence), while a pan is coherent. Shift
# thresholds are px/frame at 240px analysis width.
PAN_SHIFT_PX = 12.0
PAN_COHERENCE = 0.85
PAN_SHIFT_HARD_PX = 40.0
# a shot whose darkest sampled frame (0..255 luma) is below this reads as a
# near-black flash on screen — reject it. Calibrated on real footage: a true
# black entrance measured ~2-10 luma while dim-but-watchable blue nightclub
# shots sat at ~30-36, so the line goes between them. Sampling reaches ~1 beat
# before the peak so dark segment *entrances* are caught, not just the apex.
MIN_BRIGHTNESS = 20.0
# picked shots with embedding cosine above this are probably the same couple
# (used only when cluster ids are unavailable)
VARIETY_COSINE = 0.50

STRATEGY_WEIGHTS = {
    "classic": (0.5, 0.5),   # (face, motion)
    "energy": (0.3, 0.7),
    "cast": (0.6, 0.4),
}


@dataclass(slots=True)
class RecapCandidate:
    t: float
    motion: float
    source: str = ""          # footage file this moment comes from
    brightness: float = 255.0  # darkest sampled luma across the shot window
    face_score: float = 0.0
    pan_ratio: float = 0.0
    shift_px: float = 0.0
    coherence: float = 0.0
    exclude_cosine: float = 0.0
    cluster_id: int | None = None
    excluded: bool = False
    rejected: str = ""
    synthetic: bool = False
    static: bool = False       # title cards / graphics: no stabilization crop
    embeddings: list = field(default_factory=list, repr=False)

    @property
    def usable(self) -> bool:
        return not self.excluded and not self.rejected

    def score(self, motion_max: float, weights: tuple[float, float] = (0.5, 0.5)) -> float:
        face_w, motion_w = weights
        motion_norm = self.motion / motion_max if motion_max > 0 else 0.0
        return face_w * self.face_score + motion_w * motion_norm

    def to_json(self) -> dict:
        return {
            "t": self.t, "motion": self.motion, "source": self.source,
            "brightness": round(self.brightness, 1),
            "face_score": round(self.face_score, 3),
            "pan_ratio": self.pan_ratio, "shift_px": self.shift_px,
            "coherence": self.coherence, "exclude_cosine": self.exclude_cosine,
            "cluster_id": self.cluster_id, "excluded": self.excluded,
            "rejected": self.rejected, "synthetic": self.synthetic,
        }


def build_candidates(
    footage_path: str,
    *,
    engine: FaceEngine,
    reference: list[np.ndarray],
    cast: list[CastMember] | None = None,
    max_peaks: int = 60,
    min_gap_s: float = 8.0,
) -> list[RecapCandidate]:
    """Score every motion peak: faces, whip-pan, exclusion, cast identity."""
    candidates: list[RecapCandidate] = []
    for peak in find_motion_peaks(footage_path, count=max_peaks, min_gap_s=min_gap_s):
        candidate = RecapCandidate(t=peak["t"], motion=peak["motion"], source=footage_path)
        metrics = pan_metrics(footage_path, candidate.t)
        candidate.pan_ratio = metrics["pan_ratio"]
        candidate.shift_px = metrics["shift_px"]
        candidate.coherence = metrics["coherence"]
        if candidate.shift_px > PAN_SHIFT_HARD_PX or (
            candidate.shift_px > PAN_SHIFT_PX and candidate.coherence > PAN_COHERENCE
        ):
            candidate.rejected = "whip_pan"
        # reject near-black shots; reach ~1 beat before the peak so a dark
        # segment entrance is caught, not only the (often lit) motion apex
        candidate.brightness = min(
            frame_brightness(footage_path, candidate.t + dt) for dt in (-0.8, -0.4, 0.0, 0.4)
        )
        if candidate.brightness < MIN_BRIGHTNESS:
            candidate.rejected = candidate.rejected or "too_dark"
        # faces blur at the motion apex itself; sample around it too
        frames = [
            f for dt in (-0.4, 0.0, 0.4)
            if (f := frame_at(footage_path, max(0.0, candidate.t + dt))) is not None
        ]
        if not frames:
            candidate.rejected = candidate.rejected or "decode_failed"
        else:
            candidate.face_score = max(engine.face_score(f) for f in frames)
            for f in frames:
                candidate.embeddings.extend(engine.embeddings(f))
            if reference:
                candidate.exclude_cosine = round(
                    max(
                        (engine.match_reference(f, reference) for f in frames),
                        default=0.0,
                    ), 3,
                )
                candidate.excluded = candidate.exclude_cosine >= EXCLUDE_COSINE
            if cast:
                candidate.cluster_id = assign_cluster(candidate.embeddings, cast, engine)
        candidates.append(candidate)
    return candidates


def augment_with_cast(
    candidates: list[RecapCandidate],
    cast: list[CastMember],
    footage_path: str | None = None,
) -> list[RecapCandidate]:
    """Add a candidate at each uncovered cast member's best appearance.

    Motion peaks favor whoever the camera followed; cast members the peaks
    missed get a synthetic candidate so coverage strategies can reach them.
    Each cast member carries its own source, so this works across events.
    """
    covered = {c.cluster_id for c in candidates if c.usable and c.cluster_id is not None}
    usable_motion = [c.motion for c in candidates if c.usable]
    baseline_motion = float(np.median(usable_motion)) if usable_motion else 1.0

    added: list[RecapCandidate] = []
    for member in cast:
        if member.excluded or member.cluster_id in covered:
            continue
        source = member.source or footage_path
        metrics = pan_metrics(source, member.best_t)
        if metrics["shift_px"] > PAN_SHIFT_HARD_PX or (
            metrics["shift_px"] > PAN_SHIFT_PX and metrics["coherence"] > PAN_COHERENCE
        ):
            continue
        added.append(
            RecapCandidate(
                t=member.best_t,
                motion=baseline_motion,
                source=source,
                face_score=member.best_face_score,
                pan_ratio=metrics["pan_ratio"],
                shift_px=metrics["shift_px"],
                coherence=metrics["coherence"],
                cluster_id=member.cluster_id,
                synthetic=True,
            )
        )
    return candidates + added


def slot_pattern(duration_s: float, bar_s: float) -> list[float]:
    """Bars per slot: [1,1] intro, 2-bar chorus hit, quick-cut fill, 2-bar closer."""
    total_bars = max(7, round(duration_s / bar_s))
    fill = total_bars - 6  # intro 2 + chorus 2 + closer 2
    pattern = [1.0, 1.0, 2.0]
    remaining = float(fill)
    while remaining >= 2.0:
        pattern += [0.5, 0.5, 1.0]
        remaining -= 2.0
    if remaining >= 1.5:
        pattern += [0.5, 1.0]
    elif remaining >= 1.0:
        pattern += [1.0]
    elif remaining >= 0.5:
        pattern += [0.5]
    pattern.append(2.0)
    return pattern


def _same_person(engine: FaceEngine, a: RecapCandidate, b: RecapCandidate) -> bool:
    if a.cluster_id is not None and b.cluster_id is not None:
        return a.cluster_id == b.cluster_id
    for va in a.embeddings:
        for vb in b.embeddings:
            if engine.cosine(va, vb) >= VARIETY_COSINE:
                return True
    return False


def _fair_share_quota(counts: dict[str, int], needed: int) -> dict[str, int]:
    """Slots reserved per source so every event appears in a month recap.

    Each source with footage gets at least 1 slot; the rest are handed out
    proportionally to how many candidates each source contributed. A tiny
    14s clip still earns its cut without swamping the main event.
    """
    if len(counts) <= 1:
        return {}
    total = sum(counts.values())
    quota = {src: max(1, round(needed * count / total)) for src, count in counts.items()}
    # never reserve more than the reel holds (chorus slot stays global)
    while sum(quota.values()) > needed - 1:
        biggest = max(quota, key=lambda s: quota[s])
        if quota[biggest] <= 1:
            break
        quota[biggest] -= 1
    return quota


def choose_picks(
    candidates: list[RecapCandidate],
    pattern: list[float],
    *,
    engine: FaceEngine,
    strategy: str = "classic",
    min_gap_s: float = 8.0,
    fair_sources: bool = True,
) -> list[RecapCandidate]:
    """One candidate per slot; the chorus slot gets the strongest shot.

    "cast" fills slots from distinct clusters before allowing repeats;
    the others rank by weighted score with a no-adjacent-repeat constraint.
    With multiple event sources, fair_sources guarantees each event is
    represented proportionally before the strategy fills the remainder.
    """
    weights = STRATEGY_WEIGHTS.get(strategy, STRATEGY_WEIGHTS["classic"])
    pool = [c for c in candidates if c.usable]
    needed = len(pattern)
    if len(pool) < needed:
        # relax: readmit non-excluded rejects rather than failing the edit
        pool += [c for c in candidates if not c.excluded and c.rejected]
    if len(pool) < needed:
        raise ValueError(
            f"Only {len(pool)} usable candidates for {needed} slots; "
            "lower --duration or raise --max-peaks."
        )

    motion_max = max(c.motion for c in pool)
    ranked = sorted(pool, key=lambda c: c.score(motion_max, weights), reverse=True)

    chorus_pick = ranked[0]
    chosen = [chorus_pick]

    # fair-share: reserve slots per event so every source appears. Salvage
    # non-excluded whip-pans too — a short clip that is ALL camera motion (an
    # orbit around one couple) has no "stable" moment, but stabilization
    # smooths the pan, so the event still earns its cut instead of vanishing.
    if fair_sources:
        salvage = [
            c for c in candidates
            if not c.excluded and (c.usable or c.rejected == "whip_pan")
        ]
        by_source: dict[str, list[RecapCandidate]] = {}
        for c in salvage:
            by_source.setdefault(c.source, []).append(c)
        quota = _fair_share_quota({s: len(v) for s, v in by_source.items()}, needed)
        if quota:
            # per source: clean usable first, then least-shaky pans
            for items in by_source.values():
                items.sort(key=lambda c: (
                    0 if c.usable else 1, -c.score(motion_max, weights), c.shift_px
                ))
            filled = {chorus_pick.source: 1}
            for src, items in by_source.items():
                for candidate in items:
                    if filled.get(src, 0) >= quota.get(src, 0) or len(chosen) >= needed:
                        break
                    if candidate in chosen:
                        continue
                    if any(candidate.source == p.source and abs(candidate.t - p.t) < min_gap_s
                           for p in chosen):
                        continue
                    chosen.append(candidate)
                    filled[src] = filled.get(src, 0) + 1

    def too_close(candidate: RecapCandidate) -> bool:
        # gap only matters within the same event (t is per-file)
        return any(
            candidate.source == p.source and abs(candidate.t - p.t) < min_gap_s
            for p in chosen
        )

    if strategy == "cast":
        used_clusters = {p.cluster_id for p in chosen}
        # pass 1: unseen clusters only; pass 2: fill remaining by rank
        for candidate in ranked[1:]:
            if len(chosen) == needed:
                break
            if candidate in chosen:
                continue
            if candidate.cluster_id is not None and candidate.cluster_id in used_clusters:
                continue
            if too_close(candidate):
                continue
            chosen.append(candidate)
            used_clusters.add(candidate.cluster_id)
    else:
        for candidate in ranked[1:]:
            if len(chosen) == needed:
                break
            if candidate in chosen:
                continue
            if too_close(candidate):
                continue
            if any(_same_person(engine, candidate, p) for p in chosen):
                continue
            chosen.append(candidate)

    # constraints too strict for the pool -> fill remaining by rank alone
    for candidate in ranked[1:]:
        if len(chosen) == needed:
            break
        if candidate not in chosen:
            chosen.append(candidate)

    chosen = chosen[:needed]
    rest = sorted((c for c in chosen if c is not chorus_pick), key=lambda c: c.t)
    chorus_slot = pattern.index(2.0)
    picks = rest[:chorus_slot] + [chorus_pick] + rest[chorus_slot:]
    return picks
