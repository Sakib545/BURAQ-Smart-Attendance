"""v9.5 layered duplicate-selfie detection.

The detector deliberately combines several weak signals instead of treating a
single image hash as proof.  This makes resized/compressed WhatsApp images
detectable while allowing genuine new selfies from the same employee.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable

import cv2
import numpy as np

from app.face_ai import similarity


@dataclass(frozen=True)
class DuplicateThresholds:
    accept_below: float = 0.76
    reject_at: float = 0.91
    hash_weight: float = 0.55
    face_weight: float = 0.10
    pose_weight: float = 0.15
    landmark_weight: float = 0.20
    # Pose and landmark agreement only count as evidence once the images
    # themselves already look alike. See detect_duplicate for why.
    corroboration_gate: float = 0.72


@dataclass(frozen=True)
class DuplicateResult:
    score: float
    decision: str
    matched_fingerprint_id: int | None
    hash_score: float = 0.0
    face_score: float = 0.0
    pose_score: float = 0.0
    landmark_score: float = 0.0


def _gray(image_bytes: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("Invalid image")
    return image


def _bits_to_hex(bits: np.ndarray) -> str:
    """Return a fixed-width hash; bytes.hex preserves leading zero bytes."""
    return np.packbits(bits.astype(np.uint8)).tobytes().hex()


def ahash(image_bytes: bytes, size: int = 8) -> str:
    image = cv2.resize(_gray(image_bytes), (size, size), interpolation=cv2.INTER_AREA)
    return _bits_to_hex((image >= image.mean()).flatten())


def dhash(image_bytes: bytes, size: int = 8) -> str:
    image = cv2.resize(_gray(image_bytes), (size + 1, size), interpolation=cv2.INTER_AREA)
    return _bits_to_hex((image[:, 1:] >= image[:, :-1]).flatten())


def phash(image_bytes: bytes, size: int = 8) -> str:
    image = cv2.resize(_gray(image_bytes), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(image)[:size, :size]
    median = np.median(low.flatten()[1:])
    return _bits_to_hex((low >= median).flatten())


def hamming_similarity(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    distance = (int(left, 16) ^ int(right, 16)).bit_count()
    return max(0.0, 1.0 - distance / (len(left) * 4))


def make_fingerprint(image_bytes: bytes, embedding, diagnostics: dict) -> dict:
    return {
        "phash": phash(image_bytes), "ahash": ahash(image_bytes), "dhash": dhash(image_bytes),
        "embedding": list(embedding), "pose": diagnostics.get("pose", "unknown"),
        "yaw": float(diagnostics.get("yaw_score", 0.0)),
        "landmarks": list(diagnostics.get("landmark_signature", [])),
    }


def _json(value, fallback):
    if value is None or value == "": return fallback
    if isinstance(value, (list, dict)): return value
    try: return json.loads(value)
    except (TypeError, ValueError): return fallback


def compare(candidate: dict, previous: dict) -> tuple[float, float, float, float]:
    hashes = [hamming_similarity(candidate[name], previous.get(name, "")) for name in ("phash", "ahash", "dhash")]
    hash_score = sum(hashes) / len(hashes)
    old_embedding = _json(previous.get("embedding"), [])
    face_score = max(0.0, min(1.0, similarity(candidate["embedding"], old_embedding))) if old_embedding else 0.0
    yaw_gap = abs(float(candidate.get("yaw", 0.0)) - float(previous.get("yaw", 0.0)))
    pose_score = max(0.0, 1.0 - yaw_gap / 0.55)
    a, b = candidate.get("landmarks", []), _json(previous.get("landmarks"), [])
    if a and b and len(a) == len(b):
        landmark_score = max(0.0, 1.0 - float(np.mean(np.abs(np.asarray(a) - np.asarray(b)))) / 0.20)
    else:
        landmark_score = pose_score
    return hash_score, face_score, pose_score, landmark_score


def detect_duplicate(candidate: dict, previous_rows: Iterable[dict], thresholds: DuplicateThresholds) -> DuplicateResult:
    full_weights = np.asarray([thresholds.hash_weight, thresholds.face_weight, thresholds.pose_weight, thresholds.landmark_weight], dtype=float)
    full_weights = full_weights / max(full_weights.sum(), 1e-9)
    # When the images are plainly different, pose and landmark agreement say
    # nothing: an employee who stands in the same spot every morning scores high
    # on both every single day. Adding them unconditionally pushed honest,
    # regular staff towards the review threshold over time.
    base_weights = np.asarray([thresholds.hash_weight, thresholds.face_weight, 0.0, 0.0], dtype=float)
    base_weights = base_weights / max(base_weights.sum(), 1e-9)

    best = DuplicateResult(0.0, "accept", None)
    for row in previous_rows:
        components = compare(candidate, row)
        weights = full_weights if components[0] >= thresholds.corroboration_gate else base_weights
        score = float(np.dot(weights, np.asarray(components)))
        # Exact/near-exact image evidence is decisive. Face similarity alone is
        # deliberately weak because every genuine selfie of the same employee
        # should have a similar identity embedding.
        if components[0] >= 0.985:
            score = max(score, 0.98)
        elif components[0] >= 0.94 and components[2] >= 0.90 and components[3] >= 0.90:
            score = max(score, 0.93)
        if score > best.score:
            best = DuplicateResult(round(score, 6), "accept", int(row["id"]), *[round(x, 6) for x in components])
    decision = "reject" if best.score >= thresholds.reject_at else "pending" if best.score >= thresholds.accept_below else "accept"
    return DuplicateResult(best.score, decision, best.matched_fingerprint_id, best.hash_score, best.face_score, best.pose_score, best.landmark_score)
