"""Presentation attack detection for attendance selfies.

The pose challenge in `face_ai` only proves that a face turned; it does not
prove the face was in front of the camera. This module looks for the traces a
photo or a phone screen leaves behind:

* moire — a screen's pixel grid beats against the camera sensor and shows up as
  periodic energy in the Fourier domain
* specular — screens and glossy prints throw small blown-out highlights
* flatness — reproduced skin loses micro-texture and colour variation
* border — a held photo or a phone bezel puts long straight lines around the
  subject

None of these is conclusive on its own, which is why they are combined and why
every score is logged. Treat the thresholds as a starting point and tune them
from `scripts/face_tuning_report.py` once real attempts have accumulated.

An optional ONNX classifier can be dropped in for a much stronger signal; see
FACE_AI.md. Without it the module still runs on the passive signals alone.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ANTISPOOF_MODEL = os.getenv("ANTISPOOF_MODEL", "").strip()
ANTISPOOF_WEIGHT = float(os.getenv("ANTISPOOF_WEIGHT", "0.60"))
ANTISPOOF_INPUT = int(os.getenv("ANTISPOOF_INPUT", "80"))

REJECT_AT = float(os.getenv("LIVENESS_REJECT_AT", "0.72"))
REVIEW_AT = float(os.getenv("LIVENESS_REVIEW_AT", "0.52"))

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class LivenessResult:
    """`score` runs 0 (looks live) to 1 (looks reproduced)."""

    score: float
    verdict: str  # live | review | spoof
    components: dict[str, float] = field(default_factory=dict)
    model_used: bool = False


# ---------------------------------------------------------------------------
# Passive signals
# ---------------------------------------------------------------------------

def _face_crop(image: np.ndarray, box) -> np.ndarray:
    h, w = image.shape[:2]
    x, y, bw, bh = [int(round(float(v))) for v in box[:4]]
    pad_x, pad_y = int(bw * 0.08), int(bh * 0.08)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(w, x + bw + pad_x), min(h, y + bh + pad_y)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return image
    return image[y0:y1, x0:x1]


def _moire_score(crop: np.ndarray) -> float:
    """Periodic mid-frequency energy, which a display grid produces and skin does not."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = cv2.resize(gray, (192, 192), interpolation=cv2.INTER_AREA)
    gray = gray * cv2.createHanningWindow((192, 192), cv2.CV_32F)

    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(gray)))
    spectrum = np.log1p(spectrum)

    centre = np.array(spectrum.shape) // 2
    yy, xx = np.ogrid[:spectrum.shape[0], :spectrum.shape[1]]
    radius = np.sqrt((yy - centre[0]) ** 2 + (xx - centre[1]) ** 2)

    mid = (radius > 24) & (radius <= 68)
    total = spectrum[radius > 6].sum()
    if total <= 0:
        return 0.0
    mid_ratio = float(spectrum[mid].sum() / total)

    # A grid does not just add energy, it adds *peaks*. Compare the strongest
    # mid-band bins against that band's own median.
    band = spectrum[mid]
    median = float(np.median(band)) or 1e-6
    peak_ratio = float(np.percentile(band, 99.5) / median)

    score = (mid_ratio - 0.30) / 0.28 * 0.5 + (peak_ratio - 1.25) / 0.60 * 0.5
    return float(np.clip(score, 0.0, 1.0))


def _specular_score(crop: np.ndarray) -> float:
    """Small blown-out, low-saturation blobs — glass glare rather than skin sheen."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    glare = ((value > 248) & (saturation < 42)).astype(np.uint8)
    if glare.sum() == 0:
        return 0.0
    glare = cv2.morphologyEx(glare, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fraction = float(glare.sum()) / float(glare.size)
    return float(np.clip((fraction - 0.004) / 0.045, 0.0, 1.0))


def _flatness_score(crop: np.ndarray) -> float:
    """Reproduced skin loses fine texture and colour spread."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    high_pass = cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_64F)
    texture = float(high_pass.var())

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation_spread = float(hsv[:, :, 1].std())

    texture_flat = np.clip((110.0 - texture) / 90.0, 0.0, 1.0)
    colour_flat = np.clip((26.0 - saturation_spread) / 18.0, 0.0, 1.0)
    return float(0.6 * texture_flat + 0.4 * colour_flat)


def _border_score(image: np.ndarray, box) -> float:
    """Long straight lines framing the subject: a photo edge or a phone bezel."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(cv2.resize(image, (320, int(320 * h / max(w, 1)))), cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 170, apertureSize=3)

    # Canny fires along the frame of the image itself. A real bezel or photo
    # edge sits inside the frame, so drop the outermost band before looking.
    inset_y = max(4, int(edges.shape[0] * 0.045))
    inset_x = max(4, int(edges.shape[1] * 0.045))
    edges[:inset_y, :] = 0
    edges[-inset_y:, :] = 0
    edges[:, :inset_x] = 0
    edges[:, -inset_x:] = 0

    # Ignore the face itself; a bezel is outside it.
    scale = 320.0 / max(w, 1)
    fx, fy, fw, fh = [int(round(float(v) * scale)) for v in box[:4]]
    cv2.rectangle(edges, (max(0, fx - 6), max(0, fy - 6)), (fx + fw + 6, fy + fh + 6), 0, -1)

    min_length = int(min(edges.shape) * 0.55)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=90,
                            minLineLength=max(40, min_length), maxLineGap=12)
    if lines is None:
        return 0.0

    straight = 0
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        if angle < 8 or angle > 172 or abs(angle - 90) < 8:
            straight += 1
    return float(np.clip(straight / 9.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Optional ONNX classifier
# ---------------------------------------------------------------------------

def _load_model():
    if not ANTISPOOF_MODEL:
        return None
    path = Path(ANTISPOOF_MODEL)
    if not path.exists():
        return None
    with _MODEL_LOCK:
        net = _MODEL_CACHE.get("net")
        if net is None:
            net = cv2.dnn.readNetFromONNX(str(path))
            _MODEL_CACHE["net"] = net
        return net


def _model_spoof_probability(crop: np.ndarray) -> float | None:
    """Return P(spoof) from the ONNX classifier, or None when unavailable.

    Written defensively: different anti-spoofing exports emit two or three
    classes. A three-class head is the MiniFASNet convention
    (print / live / replay); two classes are read as (live / spoof).
    """
    net = _load_model()
    if net is None:
        return None
    try:
        blob = cv2.dnn.blobFromImage(crop, scalefactor=1.0 / 255.0,
                                     size=(ANTISPOOF_INPUT, ANTISPOOF_INPUT),
                                     mean=(0, 0, 0), swapRB=False, crop=False)
        net.setInput(blob)
        raw = np.asarray(net.forward()).flatten().astype(np.float64)
        if raw.size < 2:
            return None
        shifted = raw - raw.max()
        probs = np.exp(shifted) / np.exp(shifted).sum()
        if raw.size >= 3:
            return float(1.0 - probs[1])
        return float(probs[1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

PASSIVE_WEIGHTS = {"moire": 0.34, "specular": 0.20, "flatness": 0.30, "border": 0.16}


def analyse(image: np.ndarray, face_box) -> LivenessResult:
    crop = _face_crop(image, face_box)

    components = {
        "moire": round(_moire_score(crop), 4),
        "specular": round(_specular_score(crop), 4),
        "flatness": round(_flatness_score(crop), 4),
        "border": round(_border_score(image, face_box), 4),
    }
    passive = sum(components[name] * weight for name, weight in PASSIVE_WEIGHTS.items())

    model_probability = _model_spoof_probability(crop)
    if model_probability is None:
        score = passive
        model_used = False
    else:
        components["model"] = round(model_probability, 4)
        score = ANTISPOOF_WEIGHT * model_probability + (1.0 - ANTISPOOF_WEIGHT) * passive
        model_used = True

    score = float(np.clip(score, 0.0, 1.0))
    verdict = "spoof" if score >= REJECT_AT else "review" if score >= REVIEW_AT else "live"
    return LivenessResult(round(score, 4), verdict, components, model_used)
