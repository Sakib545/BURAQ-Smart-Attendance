from __future__ import annotations

import io
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MODEL_DIR = Path(os.getenv("FACE_MODEL_DIR", Path(__file__).resolve().parent.parent / "models"))
DETECTOR = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
DETECTOR_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
RECOGNIZER_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"


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


def _models():
    ensure_models()
    try:
        detector = cv2.FaceDetectorYN.create(str(DETECTOR), "", (320, 320), 0.55, 0.30, 5000)
        recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER), "")
        return detector, recognizer
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
    h, w = image.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(image)
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
    attempts = [image, _enhance(image)]
    best_faces = []
    best_image = image
    best_score = -1.0
    for candidate in attempts:
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
    if yaw <= -0.18:
        return "left", float(yaw)
    if yaw >= 0.18:
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
    if blur < 35:
        raise FaceAIError("ছবিটি ঝাপসা। ফোন স্থির রেখে আবার selfie দিন।")

    feature = recognizer.feature(aligned)
    if feature is None:
        raise FaceAIError("Face feature তৈরি হয়নি। আবার চেষ্টা করুন।")
    feature = feature.flatten().astype(np.float32)
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-8:
        raise FaceAIError("Face feature তৈরি হয়নি। আবার চেষ্টা করুন।")
    feature /= norm

    size_score = min(100.0, ratio * 450.0)
    blur_score = min(100.0, blur / 2.0)
    quality = round(max(1.0, min(100.0, confidence * 45.0 + size_score * 0.35 + blur_score * 0.20)), 1)
    diagnostics = {
        "width": w,
        "height": h,
        "faces": len(valid),
        "confidence": round(confidence * 100.0, 1),
        "blur": round(blur, 1),
        "face_ratio": round(ratio * 100.0, 1),
        "pose": pose,
        "yaw_score": round(yaw_score, 3),
        "landmark_signature": [round((float(face[i]) - x) / max(bw, 1.0), 4) if i % 2 == 0 else round((float(face[i]) - y) / max(bh, 1.0), 4) for i in range(4, 14)],
    }
    return feature.tolist(), quality, diagnostics


def similarity(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-8))


def best_match(candidate, samples):
    scores = [similarity(candidate, sample) for sample in samples]
    return max(scores) if scores else 0.0
