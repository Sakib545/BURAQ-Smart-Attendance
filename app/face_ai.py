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


def _find_faces(detector, image: np.ndarray):
    """Try normal and enhanced images; tolerate difficult light and phone orientation."""
    attempts = [image, _enhance(image)]
    best = None
    best_image = image
    for candidate in attempts:
        faces = _detect_once(detector, candidate)
        if faces is not None and len(faces):
            if best is None or len(faces) > len(best) or float(np.max(faces[:, -1])) > float(np.max(best[:, -1])):
                best, best_image = faces, candidate
    return best_image, best


def _blur_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_embedding(image_bytes: bytes):
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

    # Keep only detections with reasonable confidence, then enforce exactly one person.
    valid = [face for face in faces if float(face[-1]) >= 0.55]
    if not valid:
        raise FaceAIError("মুখ খুব অস্পষ্ট। ভালো আলোতে ক্যামেরার কাছে এসে আবার selfie দিন।")
    if len(valid) > 1:
        raise FaceAIError(f"ছবিতে {len(valid)}টি মুখ পাওয়া গেছে। শুধু নিজের একক selfie পাঠান।")

    face = max(valid, key=lambda item: float(item[-1]))
    x, y, bw, bh = [float(v) for v in face[:4]]
    ratio = (bw * bh) / float(w * h)
    confidence = float(face[-1])

    if ratio < 0.035:
        raise FaceAIError("মুখ অনেক দূরে। মুখ যেন ছবির অন্তত এক-চতুর্থাংশ জায়গা নেয় এমনভাবে selfie দিন।")

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
    }
    return feature.tolist(), quality, diagnostics


def similarity(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-8))


def best_match(candidate, samples):
    scores = [similarity(candidate, sample) for sample in samples]
    return max(scores) if scores else 0.0
