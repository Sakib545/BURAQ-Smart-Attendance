import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from app.face_ai import _select_valid_faces


def face(x, y, w, h, score=0.9):
    # YuNet: box + right eye, left eye, nose, right mouth, left mouth + score
    return np.array([
        x, y, w, h,
        x + w * 0.35, y + h * 0.35,
        x + w * 0.65, y + h * 0.35,
        x + w * 0.50, y + h * 0.55,
        x + w * 0.38, y + h * 0.75,
        x + w * 0.62, y + h * 0.75,
        score,
    ], dtype=np.float32)


def test_overlapping_boxes_are_one_person():
    detections = np.stack([
        face(100, 80, 260, 320, 0.94),
        face(108, 86, 252, 312, 0.88),
        face(96, 75, 270, 330, 0.81),
    ])
    result = _select_valid_faces(detections, (600, 500, 3))
    assert len(result) == 1


def test_tiny_background_ghost_is_ignored():
    detections = np.stack([
        face(100, 70, 270, 340, 0.94),
        face(430, 30, 45, 50, 0.70),
    ])
    result = _select_valid_faces(detections, (600, 500, 3))
    assert len(result) == 1


def test_two_materially_sized_people_are_kept():
    detections = np.stack([
        face(30, 90, 190, 250, 0.95),
        face(270, 100, 180, 240, 0.91),
    ])
    result = _select_valid_faces(detections, (600, 500, 3))
    assert len(result) == 2
