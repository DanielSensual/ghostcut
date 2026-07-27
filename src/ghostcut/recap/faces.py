"""Face intelligence: frontality scoring and person exclusion.

YuNet (detection) scores candidates on "faces visible and facing the camera"
so stable-but-backs-to-camera footage stops winning. SFace (recognition)
builds an embedding reference set from an exclusion clip so specific people
can be kept out of the final edit.

Models are downloaded once from the opencv_zoo repo into ~/.cache/ghostcut.
"""

from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from ..ffmpeg_tools import ToolingError

_MODEL_DIR = Path.home() / ".cache" / "ghostcut" / "models"
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
_MODELS = {
    "yunet": (
        "face_detection_yunet_2023mar.onnx",
        f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        200_000,
    ),
    "sface": (
        "face_recognition_sface_2021dec.onnx",
        f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        30_000_000,
    ),
}
# cosine similarity for "same person": single-frame calibration on club
# footage put true matches at >=0.41 and strangers at <=0.32; matching is
# max-over-many-comparisons so the threshold sits above the midpoint
EXCLUDE_COSINE = 0.40
# a face filling ~0.4% of the frame is typical for full-body dance framing;
# faces that size or larger get full weight in face_score
_TYPICAL_FACE_FRAC = 0.004
# identity matching only trusts confident, reasonably sized faces — small
# background heads and TV screens produce garbage embeddings that inflate
# max-cosine statistics
_MATCH_MIN_SCORE = 0.85
_MATCH_MIN_WIDTH_FRAC = 0.05


def ensure_models() -> dict[str, Path]:
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, (filename, url, min_bytes) in _MODELS.items():
        target = _MODEL_DIR / filename
        if not target.exists() or target.stat().st_size < min_bytes:
            try:
                urllib.request.urlretrieve(url, target)
            except OSError as error:
                raise ToolingError(f"Could not download {key} model from {url}: {error}") from error
            if target.stat().st_size < min_bytes:
                target.unlink(missing_ok=True)
                raise ToolingError(f"Downloaded {key} model is truncated; retry when online.")
        paths[key] = target
    return paths


def frame_at(video_path: str, t: float, *, width: int = 960) -> np.ndarray | None:
    """Decode one BGR frame at time t (None when decoding fails)."""
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video_path,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
         "-vcodec", "png", "-"],
        capture_output=True,
    )
    if process.returncode != 0 or not process.stdout:
        return None
    data = np.frombuffer(process.stdout, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class FaceEngine:
    """Wraps YuNet + SFace with lazy model download."""

    def __init__(self) -> None:
        models = ensure_models()
        self._detector = cv2.FaceDetectorYN.create(str(models["yunet"]), "", (320, 320), 0.6)
        self._recognizer = cv2.FaceRecognizerSF.create(str(models["sface"]), "")

    def detect(self, bgr: np.ndarray) -> np.ndarray:
        """Rows of [x, y, w, h, 5x(lm_x, lm_y), score]; empty array when none."""
        h, w = bgr.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(bgr)
        return faces if faces is not None else np.empty((0, 15), dtype=np.float32)

    @staticmethod
    def _frontality(row: np.ndarray) -> float:
        right_eye = row[4:6]
        left_eye = row[6:8]
        nose = row[8:10]
        eye_dist = float(np.hypot(*(left_eye - right_eye)))
        if eye_dist < 1.0:
            return 0.0
        mid_x = (right_eye[0] + left_eye[0]) / 2.0
        offset = abs(float(nose[0]) - mid_x) / eye_dist
        return float(np.clip(1.0 - 2.0 * offset, 0.0, 1.0))

    def face_score(self, bgr: np.ndarray) -> float:
        """0..1: how much camera-facing face presence the frame has."""
        h, w = bgr.shape[:2]
        total = 0.0
        for row in self.detect(bgr):
            area_frac = (row[2] * row[3]) / (w * h)
            size_term = min(1.0, area_frac / _TYPICAL_FACE_FRAC)
            total += float(row[14]) * self._frontality(row) * size_term
        return float(min(1.0, total))

    def embeddings(self, bgr: np.ndarray, *, quality_gate: bool = False) -> list[np.ndarray]:
        vectors = []
        frame_w = bgr.shape[1]
        for row in self.detect(bgr):
            if quality_gate and (
                float(row[14]) < _MATCH_MIN_SCORE
                or row[2] < _MATCH_MIN_WIDTH_FRAC * frame_w
            ):
                continue
            crop = self._recognizer.alignCrop(bgr, row)
            vectors.append(self._recognizer.feature(crop).copy())
        return vectors

    def cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(
            self._recognizer.match(
                np.asarray(a, dtype=np.float32),
                np.asarray(b, dtype=np.float32),
                cv2.FaceRecognizerSF_FR_COSINE,
            )
        )

    def build_reference(self, video_path: str, *, sample_every_s: float = 1.0) -> list[np.ndarray]:
        """All face embeddings sampled across an exclusion clip."""
        process = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True,
        )
        try:
            duration = float(process.stdout.strip())
        except ValueError as error:
            raise ToolingError(f"Could not probe exclusion clip: {video_path}") from error

        collected: list[np.ndarray] = []
        t = 0.5
        while t < duration - 0.2:
            frame = frame_at(video_path, t)
            if frame is not None:
                collected.extend(self.embeddings(frame, quality_gate=True))
            t += sample_every_s
        # prune near-duplicates so max-over-references stays well behaved
        reference: list[np.ndarray] = []
        for vector in collected:
            if all(self.cosine(vector, kept) < 0.9 for kept in reference):
                reference.append(vector)
        return reference

    def match_reference(self, bgr: np.ndarray, reference: list[np.ndarray]) -> float:
        """Best cosine between quality-gated faces in the frame and the reference."""
        best = 0.0
        for vector in self.embeddings(bgr, quality_gate=True):
            for ref in reference:
                best = max(best, self.cosine(vector, ref))
        return best

    def matches_reference(self, bgr: np.ndarray, reference: list[np.ndarray]) -> bool:
        if not reference:
            return False
        return self.match_reference(bgr, reference) >= EXCLUDE_COSINE
