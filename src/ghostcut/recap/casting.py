"""Cast discovery: who is in the footage, and when.

Motion peaks are biased toward whoever the camera followed closely, so
selection driven only by peaks over-features a few couples. Scanning the
whole footage and clustering face embeddings into identities produces a cast
list; selection can then treat variety as an objective (cover the cast)
instead of a constraint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .faces import FaceEngine, frame_at

# same person across club lighting: within-footage matches run lower than the
# clean-reference threshold, so clustering joins a touch more loosely
CLUSTER_COSINE = 0.42
# clusters seen fewer than this many times are detection noise
MIN_APPEARANCES = 3


@dataclass(slots=True)
class CastMember:
    cluster_id: int
    source: str = ""          # footage file this person was found in
    appearances: list[float] = field(default_factory=list)
    best_t: float = 0.0
    best_face_score: float = 0.0
    excluded: bool = False
    centroid: object = None  # np.ndarray; kept out of JSON


def discover_cast(
    footage_path: str,
    duration_s: float,
    *,
    engine: FaceEngine,
    reference: list[np.ndarray],
    sample_every_s: float = 2.0,
    cluster_offset: int = 0,
) -> list[CastMember]:
    """Scan the footage and cluster quality faces into identities.

    cluster_offset keeps cluster ids globally unique when discovering cast
    across several event files (people are treated as distinct per file).
    """
    members: list[CastMember] = []
    t = 1.0
    while t < duration_s - 1.0:
        frame = frame_at(footage_path, t)
        if frame is None:
            t += sample_every_s
            continue
        face_score = engine.face_score(frame)
        for vector in engine.embeddings(frame, quality_gate=True):
            best, best_cos = None, 0.0
            for member in members:
                cos = engine.cosine(vector, member.centroid)
                if cos > best_cos:
                    best, best_cos = member, cos
            if best is not None and best_cos >= CLUSTER_COSINE:
                # running centroid keeps clusters stable as lighting changes
                n = len(best.appearances)
                best.centroid = ((best.centroid * n + vector) / (n + 1)).astype(np.float32)
                best.appearances.append(round(t, 2))
                if face_score > best.best_face_score:
                    best.best_face_score = face_score
                    best.best_t = round(t, 2)
            else:
                members.append(
                    CastMember(
                        cluster_id=cluster_offset + len(members),
                        source=footage_path,
                        appearances=[round(t, 2)],
                        best_t=round(t, 2),
                        best_face_score=face_score,
                        centroid=vector.astype(np.float32),
                    )
                )
        t += sample_every_s

    cast = [m for m in members if len(m.appearances) >= MIN_APPEARANCES]
    for member in cast:
        member.excluded = any(
            engine.cosine(member.centroid.astype(np.float32), ref) >= CLUSTER_COSINE
            for ref in reference
        )
    return cast


def assign_cluster(embeddings: list, cast: list[CastMember], engine: FaceEngine) -> int | None:
    """Best-matching cast cluster for a candidate's face embeddings."""
    best_id, best_cos = None, 0.0
    for vector in embeddings:
        for member in cast:
            cos = engine.cosine(vector, member.centroid.astype(np.float32))
            if cos > best_cos:
                best_id, best_cos = member.cluster_id, cos
    return best_id if best_cos >= CLUSTER_COSINE else None


def cast_to_json(cast: list[CastMember]) -> list[dict]:
    return [
        {
            "cluster_id": m.cluster_id,
            "source": m.source,
            "appearances": len(m.appearances),
            "first_seen": m.appearances[0],
            "last_seen": m.appearances[-1],
            "best_t": m.best_t,
            "best_face_score": round(m.best_face_score, 3),
            "excluded": m.excluded,
        }
        for m in cast
    ]
