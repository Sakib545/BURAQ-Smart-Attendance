import cv2
import numpy as np

from app.duplicate_detector import DuplicateThresholds, ahash, dhash, phash, detect_duplicate, make_fingerprint


def image_bytes(seed=1):
    rng=np.random.default_rng(seed)
    image=rng.integers(0,256,(120,120,3),dtype=np.uint8)
    ok,data=cv2.imencode(".jpg",image)
    assert ok
    return data.tobytes()


def test_hashes_are_stable_and_64_bit():
    payload=image_bytes()
    assert len(phash(payload))==len(ahash(payload))==len(dhash(payload))==16
    assert phash(payload)==phash(payload)


def test_exact_image_is_rejected():
    fp=make_fingerprint(image_bytes(),[1.0,0.0],{"pose":"straight","yaw_score":0,"landmark_signature":[.2,.3]})
    old={"id":7,**fp,"embedding":"[1.0,0.0]","landmarks":"[0.2,0.3]"}
    result=detect_duplicate(fp,[old],DuplicateThresholds())
    assert result.decision=="reject"
    assert result.matched_fingerprint_id==7


def test_new_image_is_accepted():
    first=make_fingerprint(image_bytes(1),[1.0,0.0],{"pose":"left","yaw_score":-.4,"landmark_signature":[.1,.2]})
    second=make_fingerprint(image_bytes(2),[0.0,1.0],{"pose":"right","yaw_score":.4,"landmark_signature":[.8,.9]})
    old={"id":2,**first,"embedding":"[1.0,0.0]","landmarks":"[0.1,0.2]"}
    assert detect_duplicate(second,[old],DuplicateThresholds()).decision=="accept"


def test_same_person_new_photo_is_not_rejected():
    first=make_fingerprint(image_bytes(11),[1.0,0.0],{"pose":"straight","yaw_score":0.01,"landmark_signature":[.2,.3]})
    second=make_fingerprint(image_bytes(22),[.999,.045],{"pose":"straight","yaw_score":0.03,"landmark_signature":[.23,.28]})
    old={"id":9,**first,"embedding":"[1.0,0.0]","landmarks":"[0.2,0.3]"}
    assert detect_duplicate(second,[old],DuplicateThresholds()).decision == "accept"
