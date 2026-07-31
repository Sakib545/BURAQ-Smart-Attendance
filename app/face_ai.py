from __future__ import annotations
import json, os, urllib.request
from pathlib import Path
import cv2
import numpy as np

MODEL_DIR = Path(os.getenv('FACE_MODEL_DIR', Path(__file__).resolve().parent.parent / 'models'))
DETECTOR = MODEL_DIR / 'face_detection_yunet_2023mar.onnx'
RECOGNIZER = MODEL_DIR / 'face_recognition_sface_2021dec.onnx'
DETECTOR_URL = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
RECOGNIZER_URL = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx'

class FaceAIError(Exception): pass

def ensure_models():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in ((DETECTOR, DETECTOR_URL), (RECOGNIZER, RECOGNIZER_URL)):
        if not path.exists() or path.stat().st_size < 100000:
            urllib.request.urlretrieve(url, path)

def _models():
    ensure_models()
    detector = cv2.FaceDetectorYN.create(str(DETECTOR), '', (320,320), 0.9, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER), '')
    return detector, recognizer

def extract_embedding(image_bytes: bytes):
    arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None: raise FaceAIError('ছবিটি পড়া যায়নি। আবার পরিষ্কার selfie পাঠান।')
    h,w = image.shape[:2]
    if min(h,w) < 240: raise FaceAIError('ছবির resolution কম। কাছ থেকে পরিষ্কার selfie পাঠান।')
    detector, recognizer = _models(); detector.setInputSize((w,h))
    _, faces = detector.detect(image)
    if faces is None or len(faces)==0: raise FaceAIError('কোনো মুখ পাওয়া যায়নি। আলোতে সামনে তাকিয়ে selfie দিন।')
    if len(faces)>1: raise FaceAIError('ছবিতে একাধিক মুখ আছে। শুধু নিজের মুখ রেখে selfie দিন।')
    face = faces[0]
    x,y,bw,bh = face[:4]
    ratio = (bw*bh)/(w*h)
    if ratio < 0.08: raise FaceAIError('মুখ অনেক দূরে। ক্যামেরার কাছে এসে selfie দিন।')
    aligned = recognizer.alignCrop(image, face)
    feature = recognizer.feature(aligned).flatten().astype(float)
    norm = np.linalg.norm(feature)
    if norm == 0: raise FaceAIError('Face feature তৈরি হয়নি। আবার চেষ্টা করুন।')
    feature = feature / norm
    quality = round(min(100.0, 45 + ratio*300), 1)
    return feature.tolist(), quality

def similarity(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(va, vb)/(np.linalg.norm(va)*np.linalg.norm(vb)+1e-8))

def best_match(candidate, samples):
    scores=[similarity(candidate,s) for s in samples]
    return max(scores) if scores else 0.0
