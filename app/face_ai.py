from __future__ import annotations

import io
import os
import urllib.request
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from app import liveness

# Detection runs on a downscaled copy and the boxes are mapped back, which is
# 3-5x faster than detecting on a full phone-resolution frame with no measured
# loss in accuracy at selfie distance.
DETECT_MAX_SIDE = int(os.getenv("FACE_DETECT_MAX_SIDE", "640"))

# Models are baked into the image at build time. Downloading during a request
# would make the first check-in of a cold container hang on GitHub.
ALLOW_RUNTIME_DOWNLOAD = os.getenv("FACE_ALLOW_MODEL_DOWNLOAD", "false").strip().lower() in {"1", "true", "yes", "on"}

MODEL_DIR = Path(os.getenv("FACE_MODEL_DIR", Path(__file__).resolve().parent.parent / "models"))
DETECTOR = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
DETECTOR_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
RECOGNIZER_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"


_MODEL_STATE = threading.local()


class FaceAIError(Exception):
    pass


def ensure_models() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in ((DETECTOR, DETECTOR_URL), (RECOGNIZER, RECOGNIZER_URL)):
        if not path.exists() or path.stat().st_size < 100_000:
            try:
                urllib.request.urlretrieve(url, path)
            except Exception as exc:
                raise FaceAIError(
                    "Face AI model download হয়নি। Railway redeploy করুন অথবা server internet access পরীক্ষা করুন।"
                ) from exc


def models_present() -> bool:
    return all(path.exists() and path.stat().st_size >= 100_000 for path in (DETECTOR, RECOGNIZER))


def _models():
    cached = getattr(_MODEL_STATE, "models", None)
    if cached is not None:
        return cached
    if not models_present():
        if not ALLOW_RUNTIME_DOWNLOAD:
            raise FaceAIError(
                "Face AI model server-এ নেই। Redeploy করুন, অথবা জরুরি হলে "
                "FACE_ALLOW_MODEL_DOWNLOAD=true সেট করুন।"
            )
        ensure_models()
    try:
        detector = cv2.FaceDetectorYN.create(str(DETECTOR), "", (320, 320), 0.55, 0.30, 5000)
        recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER), "")
        _MODEL_STATE.models = (detector, recognizer)
        return _MODEL_STATE.models
    except Exception as exc:
        raise FaceAIError("Face AI model load হয়নি। Railway logs পরীক্ষা করুন।") from exc


def _decode_with_orientation(image_bytes: bytes) -> np.ndarray:
    """Decode JPEG/PNG and apply the phone camera's EXIF rotation."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as pil:
            pil = ImageOps.exif_transpose(pil).convert("RGB")
            # Huge phone images use unnecessary memory on Railway. Preserve detail but cap size.
            max_side = max(pil.size)
            if max_side > 1800:
                scale = 1800 / max_side
                pil = pil.resize((max(1, round(pil.width * scale)), max(1, round(pil.height * scale))), Image.Resampling.LANCZOS)
            rgb = np.asarray(pil)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise FaceAIError("ছবিটি পড়া যায়নি। WhatsApp থেকে নতুন selfie তুলে আবার পাঠান।")
        return image


def _enhance(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((enhanced_l, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def _detect_once(detector, image: np.ndarray):
    """Detect on a downscaled copy, then map boxes and landmarks back."""
    h, w = image.shape[:2]
    scale = min(1.0, DETECT_MAX_SIDE / float(max(h, w)))
    if scale < 1.0:
        small = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    else:
        small, scale = image, 1.0

    detector.setInputSize((small.shape[1], small.shape[0]))
    _, faces = detector.detect(small)
    if faces is None or len(faces) == 0 or scale == 1.0:
        return faces

    faces = np.asarray(faces, dtype=np.float32).copy()
    faces[:, :14] /= scale  # bounding box + five landmark pairs; column 14 is the score
    return faces


def _bbox_iou(a, b) -> float:
    ax, ay, aw, ah = [float(v) for v in a[:4]]
    bx, by, bw, bh = [float(v) for v in b[:4]]
    ax2, ay2, bx2, by2 = ax + aw, ay + ah, bx + bw, by + bh
    ix1, iy1, ix2, iy2 = max(ax, bx), max(ay, by), min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def _landmarks_are_plausible(face) -> bool:
    """Reject YuNet ghost boxes whose landmarks do not form a human face."""
    x, y, w, h = [float(v) for v in face[:4]]
    if w <= 0 or h <= 0:
        return False
    points = [(float(face[i]), float(face[i + 1])) for i in range(4, 14, 2)]
    margin_x, margin_y = w * 0.18, h * 0.18
    if any(px < x - margin_x or px > x + w + margin_x or py < y - margin_y or py > y + h + margin_y for px, py in points):
        return False
    right_eye, left_eye, nose, right_mouth, left_mouth = points
    eye_distance = abs(left_eye[0] - right_eye[0])
    mouth_distance = abs(left_mouth[0] - right_mouth[0])
    if eye_distance < w * 0.12 or mouth_distance < w * 0.08:
        return False
    eye_y = (right_eye[1] + left_eye[1]) / 2.0
    mouth_y = (right_mouth[1] + left_mouth[1]) / 2.0
    if not (eye_y - h * 0.12 <= nose[1] <= mouth_y + h * 0.12):
        return False
    return True


def _select_valid_faces(faces, image_shape):
    """Clean YuNet detections and return only distinct, meaningful people.

    YuNet can emit several overlapping boxes around one face, especially after
    contrast enhancement. This function removes tiny ghost detections, invalid
    landmark layouts and duplicate boxes before enforcing the one-person rule.
    """
    if faces is None or len(faces) == 0:
        return []
    h, w = image_shape[:2]
    image_area = float(max(1, w * h))
    candidates = []
    for face in faces:
        x, y, bw, bh = [float(v) for v in face[:4]]
        confidence = float(face[-1])
        area_ratio = (max(0.0, bw) * max(0.0, bh)) / image_area
        if confidence < 0.50:
            continue
        if bw < 42 or bh < 42 or area_ratio < 0.008:
            continue
        if x + bw < 0 or y + bh < 0 or x > w or y > h:
            continue
        if not _landmarks_are_plausible(face):
            continue
        candidates.append(face)

    # Non-maximum suppression: one real face often arrives as 2–4 overlapping boxes.
    candidates.sort(key=lambda f: (float(f[-1]), float(f[2]) * float(f[3])), reverse=True)
    distinct = []
    for face in candidates:
        if any(_bbox_iou(face, kept) >= 0.32 for kept in distinct):
            continue
        distinct.append(face)

    if not distinct:
        return []

    # Tiny background faces/posters should not make a close selfie fail. A second
    # person counts only when it is both confident and materially sized.
    primary = max(distinct, key=lambda f: float(f[2]) * float(f[3]))
    primary_area = float(primary[2]) * float(primary[3])
    meaningful = [primary]
    for face in distinct:
        if face is primary:
            continue
        area = float(face[2]) * float(face[3])
        area_ratio = area / image_area
        if float(face[-1]) >= 0.62 and area_ratio >= 0.018 and area >= primary_area * 0.28:
            meaningful.append(face)
    return meaningful


def _find_faces(detector, image: np.ndarray):
    """Try normal and enhanced images and choose the most credible result."""
    best_faces = []
    best_image = image
    best_score = -1.0
    for index, candidate in enumerate((image, None)):
        if candidate is None:
            # Only pay for CLAHE when the plain frame was not already good.
            if best_faces and best_score >= 1.05:
                break
            candidate = _enhance(image)
        raw_faces = _detect_once(detector, candidate)
        valid_faces = _select_valid_faces(raw_faces, candidate.shape)
        if not valid_faces:
            continue
        primary = max(valid_faces, key=lambda f: float(f[2]) * float(f[3]))
        area_ratio = (float(primary[2]) * float(primary[3])) / float(candidate.shape[0] * candidate.shape[1])
        # Do not reward a candidate merely for returning more boxes.
        score = float(primary[-1]) + min(area_ratio, 0.35) * 1.5 - max(0, len(valid_faces) - 1) * 0.10
        if score > best_score:
            best_score, best_faces, best_image = score, valid_faces, candidate
    return best_image, best_faces


def _blur_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _pose_from_face(face) -> tuple[str, float]:
    """Estimate coarse head yaw from YuNet landmarks.

    Returns (pose, yaw_score), where pose is straight/left/right and
    yaw_score is the normalized horizontal nose displacement.
    """
    # YuNet landmarks: right eye, left eye, nose, right mouth, left mouth.
    right_eye_x, right_eye_y = float(face[4]), float(face[5])
    left_eye_x, left_eye_y = float(face[6]), float(face[7])
    nose_x, nose_y = float(face[8]), float(face[9])
    eye_mid_x = (right_eye_x + left_eye_x) / 2.0
    eye_distance = max(abs(left_eye_x - right_eye_x), 1.0)
    yaw = (nose_x - eye_mid_x) / eye_distance
    # Thresholds intentionally moderate so employees do not need an extreme turn.
    if yaw <= -0.22:
        return "left", float(yaw)
    if yaw >= 0.22:
        return "right", float(yaw)
    return "straight", float(yaw)


def extract_embedding(image_bytes: bytes, required_pose: str | None = None):
    if not image_bytes:
        raise FaceAIError("খালি image পাওয়া গেছে। আবার selfie পাঠান।")

    image = _decode_with_orientation(image_bytes)
    h, w = image.shape[:2]
    if min(h, w) < 240:
        raise FaceAIError(f"ছবির resolution কম ({w}×{h})। কাছ থেকে পরিষ্কার selfie পাঠান।")

    detector, recognizer = _models()
    detected_image, faces = _find_faces(detector, image)

    if faces is None or len(faces) == 0:
        brightness = float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean())
        light_hint = "আলো কম। উজ্জ্বল জায়গায় দাঁড়ান।" if brightness < 65 else "ক্যামেরার সামনে সোজা তাকান এবং মুখটি একটু কাছে আনুন।"
        raise FaceAIError(
            f"কোনো মুখ পাওয়া যায়নি।\n📐 Image: {w}×{h}\n💡 {light_hint}\n⚠️ Gallery থেকে পুরোনো ছবি নয়, WhatsApp camera দিয়ে নতুন selfie দিন।"
        )

    valid = list(faces)
    if not valid:
        raise FaceAIError("মুখ খুব অস্পষ্ট। ভালো আলোতে ক্যামেরার কাছে এসে আবার selfie দিন।")
    if len(valid) > 1:
        raise FaceAIError(f"ছবিতে {len(valid)} জন আলাদা ব্যক্তি পাওয়া গেছে। শুধু নিজের একক selfie পাঠান।")

    face = max(valid, key=lambda item: float(item[-1]) + (float(item[2]) * float(item[3]) / float(w * h)))
    pose, yaw_score = _pose_from_face(face)
    x, y, bw, bh = [float(v) for v in face[:4]]
    ratio = (bw * bh) / float(w * h)
    confidence = float(face[-1])

    if ratio < 0.035:
        raise FaceAIError("মুখ অনেক দূরে। মুখ যেন ছবির অন্তত এক-চতুর্থাংশ জায়গা নেয় এমনভাবে selfie দিন।")

    if required_pose and required_pose not in {"any", pose}:
        pose_labels = {"straight": "সোজা সামনে", "left": "বাম দিকে", "right": "ডান দিকে"}
        expected = pose_labels.get(required_pose, required_pose)
        detected = pose_labels.get(pose, pose)
        raise FaceAIError(
            f"Liveness challenge মেলেনি।\nচাওয়া হয়েছিল: {expected} তাকাতে\nশনাক্ত হয়েছে: {detected}\n\nএখনই নতুন selfie তুলে আবার পাঠান।"
        )

    aligned = recognizer.alignCrop(detected_image, face)
    if aligned is None or aligned.size == 0:
        raise FaceAIError("মুখ align করা যায়নি। সামনে সোজা তাকিয়ে আবার selfie দিন।")

    blur = _blur_score(aligned)
    simple_mode = os.getenv("SIMPLE_FACE_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
    blur_min = min(float(os.getenv("FACE_BLUR_MIN", "42")), 25.0) if simple_mode else float(os.getenv("FACE_BLUR_MIN", "42"))
    if blur < blur_min:
        raise FaceAIError(f"ছবিটি ঝাপসা (quality {blur:.0f})। ফোন স্থির রেখে ভালো আলোতে আবার selfie দিন।")

    gray_aligned = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    brightness = float(gray_aligned.mean())
    if brightness < (35 if simple_mode else 48):
        raise FaceAIError("মুখে আলো কম। উজ্জ্বল জায়গায় দাঁড়িয়ে আবার selfie দিন।")
    if brightness > (238 if simple_mode else 225):
        raise FaceAIError("ছবিতে অতিরিক্ত আলো পড়েছে। আলো একটু কমিয়ে আবার selfie দিন।")

    feature = recognizer.feature(aligned)
    if feature is None:
        raise FaceAIError("Face feature তৈরি হয়নি। আবার চেষ্টা করুন।")
    feature = feature.flatten().astype(np.float32)
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-8:
        raise FaceAIError("Face feature তৈরি হয়নি। আবার চেষ্টা করুন।")
    feature /= norm

    # GPS is BURAQ's primary attendance boundary. Simple Face Mode avoids the
    # expensive passive anti-spoof pipeline, whose heuristic OpenCV operations
    # can fail on otherwise valid phone selfies. Identity matching and exact
    # reused-image detection still run afterwards.
    if simple_mode:
        spoof = liveness.LivenessResult(0.0, "live", {"simple_mode": 1.0}, False)
    else:
        try:
            spoof = liveness.analyse(detected_image, face)
        except Exception:
            spoof = liveness.LivenessResult(0.0, "review", {"analysis_error": 1.0}, False)

    size_score = min(100.0, ratio * 450.0)
    blur_score = min(100.0, blur / 2.0)
    quality = round(max(1.0, min(100.0, confidence * 45.0 + size_score * 0.35 + blur_score * 0.20)), 1)
    diagnostics = {
        "width": w,
        "height": h,
        "faces": len(valid),
        "confidence": round(confidence * 100.0, 1),
        "blur": round(blur, 1),
        "brightness": round(brightness, 1),
        "face_ratio": round(ratio * 100.0, 1),
        "pose": pose,
        "yaw_score": round(yaw_score, 3),
        "landmark_signature": [round((float(face[i]) - x) / max(bw, 1.0), 4) if i % 2 == 0 else round((float(face[i]) - y) / max(bh, 1.0), 4) for i in range(4, 14)],
        "liveness_score": spoof.score,
        "liveness_verdict": spoof.verdict,
        "liveness_components": spoof.components,
        "liveness_model": spoof.model_used,
    }
    return feature.tolist(), quality, diagnostics


def similarity(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-8))


def best_match(candidate, samples):
    scores = [similarity(candidate, sample) for sample in samples]
    return max(scores) if scores else 0.0


def gallery_score(candidate, samples) -> float:
    """Mean of the two closest enrolled samples.

    Taking the maximum lets a single lucky sample carry the decision, and the
    false-accept rate of max-of-N grows with the gallery. Averaging the top two
    keeps a genuine match while making an accidental one much harder.
    """
    scores = sorted((similarity(candidate, sample) for sample in samples), reverse=True)
    if not scores:
        return 0.0
    if len(scores) == 1:
        return float(scores[0])
    return float((scores[0] + scores[1]) / 2.0)


def best_impostor(candidate, rows) -> tuple[float, int | None]:
    """Closest match among *other* employees, for the margin check.

    `rows` are (employee_id, embedding) pairs. Verification that only compares
    against the claimed employee cannot tell siblings apart; requiring the
    claimed score to beat every other employee by a margin can.
    """
    best_score, best_employee = 0.0, None
    for employee_id, embedding in rows:
        score = similarity(candidate, embedding)
        if score > best_score:
            best_score, best_employee = score, employee_id
    return float(best_score), best_employee
