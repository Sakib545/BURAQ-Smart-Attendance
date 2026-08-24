import numpy as np
import cv2

from app import liveness
from app.duplicate_detector import DuplicateThresholds, detect_duplicate
from app.face_ai import best_impostor, gallery_score


def _synthetic_face(seed=3):
    rng = np.random.default_rng(seed)
    canvas = np.zeros((400, 400, 3), np.uint8)
    canvas[:] = (120, 150, 195)
    cv2.ellipse(canvas, (200, 200), (110, 140), 0, 0, 360, (110, 140, 185), -1)
    return np.clip(canvas + rng.normal(0, 7, canvas.shape), 0, 255).astype(np.uint8)


BOX = [90.0, 60.0, 220.0, 280.0]


def test_screen_grid_raises_moire_score():
    real = _synthetic_face()
    screen = cv2.GaussianBlur(real, (5, 5), 0).astype(np.int16)
    grid = np.zeros_like(screen)
    grid[::3, :, :] = 16
    grid[:, ::3, :] += 12
    screen = np.clip(screen + grid, 0, 255).astype(np.uint8)

    assert liveness.analyse(screen, BOX).components["moire"] > liveness.analyse(real, BOX).components["moire"]


def test_image_frame_alone_is_not_treated_as_a_bezel():
    assert liveness.analyse(_synthetic_face(), BOX).components["border"] == 0.0


def test_held_photo_scores_higher_than_plain_capture():
    real = _synthetic_face()
    photo = cv2.GaussianBlur(real, (7, 7), 0)
    cv2.rectangle(photo, (40, 26), (362, 376), (30, 30, 30), 5)
    assert liveness.analyse(photo, BOX).score > liveness.analyse(real, BOX).score


def test_gallery_score_ignores_a_single_lucky_sample():
    candidate = [1.0, 0.0, 0.0]
    gallery = [[0.99, 0.14, 0.0], [0.60, 0.80, 0.0], [0.30, 0.95, 0.0]]
    assert gallery_score(candidate, gallery) < max(
        np.dot(candidate, g) / np.linalg.norm(g) for g in gallery
    )


def test_best_impostor_reports_the_closest_other_employee():
    score, employee_id = best_impostor([1.0, 0.0, 0.0], [(7, [0.80, 0.60, 0.0]), (9, [0.20, 0.98, 0.0])])
    assert employee_id == 7
    assert 0.79 < score < 0.81


def test_repeated_stance_does_not_look_like_a_reused_image():
    base = {"phash": "00" * 8, "ahash": "00" * 8, "dhash": "00" * 8, "embedding": [1.0, 0.0],
            "pose": "straight", "yaw": 0.02,
            "landmarks": [0.3, 0.3, 0.7, 0.3, 0.5, 0.5, 0.35, 0.7, 0.65, 0.7]}
    prior = [dict(base, id=1)]

    different_photo = dict(base, phash="0f" * 8, ahash="3c" * 8, dhash="5a" * 8)
    assert detect_duplicate(different_photo, prior, DuplicateThresholds()).decision == "accept"
    assert detect_duplicate(dict(base), prior, DuplicateThresholds()).decision == "reject"
