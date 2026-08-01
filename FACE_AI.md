# Face AI

## Pipeline

```
selfie bytes
  -> EXIF-corrected decode, capped at 1800px
  -> YuNet detection on a 640px copy, boxes mapped back to full resolution
  -> ghost-box cleanup: confidence, size, landmark plausibility, NMS
  -> pose check against the liveness challenge
  -> SFace alignCrop, blur and brightness gates
  -> presentation-attack analysis (app/liveness.py)
  -> 128-d embedding, L2 normalised
  -> gallery score (mean of top 2) + impostor margin
  -> duplicate-image check (app/duplicate_detector.py)
  -> face_events row
```

## What changed and why

**Enrolment now has a quality floor.** `FACE_ENROLL_QUALITY_MIN` (default 55)
blocks weak samples from entering the gallery. An enrolment sample is permanent;
one blurry registration selfie makes every later verification harder for that
employee, forever. Verification keeps its own lower floor, `FACE_QUALITY_MIN`.

**Enrolment asks for three specific angles.** Sample 1 straight, 2 slightly
left, 3 slightly right, enforced by `required_pose`. Previously the instruction
was only in the message text, so most employees sent three identical straight
shots and the gallery had no tolerance for head turn.

**Matching averages the two closest samples** instead of taking the maximum.
Max-of-N lets one lucky sample decide, and its false-accept rate grows with the
gallery size.

**Verification requires a margin.** The claimed employee must beat the closest
*other* employee by `FACE_MARGIN_MIN` (default 0.06). Comparing only against the
claimed employee cannot separate siblings or lookalikes. The cross-employee
gallery is cached for five minutes and invalidated on new enrolment.

**Detection runs on a 640px copy.** Boxes and landmarks are scaled back before
`alignCrop`, so recognition still uses full resolution. The CLAHE pass is now
skipped when the plain frame already produced a confident detection.

**Models no longer download during a request.** They are fetched in the Docker
build. If they are missing at runtime the request fails with a clear message
instead of hanging on GitHub. Set `FACE_ALLOW_MODEL_DOWNLOAD=true` only as an
emergency escape hatch.

**Duplicate detection gates pose and landmark evidence.** Those two scores are
naturally high for any employee who stands in the same place each morning, and
adding them unconditionally pushed regular staff towards the review threshold
over months. They now count only once `hash_score` already exceeds
`DUPLICATE_CORROBORATION_GATE` (0.72). Measured on the same input: an honest
repeat that used to score 0.735 now scores 0.577, while a genuinely reused image
still scores 1.000.

## Liveness

`app/liveness.py` scores four passive signals — moire, specular highlights,
texture and colour flatness, and straight bezel-like lines around the subject —
and blends them into one score from 0 (looks live) to 1 (looks reproduced).

Be realistic about what this buys you. These are heuristics, not a trained
detector. They will catch a casually held phone screen or printout and they will
occasionally misfire on a genuine selfie in hard light. That is why only a
confident reading blocks anyone:

| Score | Verdict | Effect |
| --- | --- | --- |
| `>= LIVENESS_REJECT_AT` (0.72) | spoof | attendance refused |
| `>= LIVENESS_REVIEW_AT` (0.52) | review | allowed, logged as `verified_liveness_review` |
| below | live | allowed |

Watch the logged distribution for a couple of weeks before tightening either
number.

### Adding the ONNX classifier

The passive signals get much stronger when combined with a trained
anti-spoofing model. The pipeline already has the hook; it needs the file.

1. Get an anti-spoofing ONNX model that takes an 80×80 BGR face crop and emits
   class scores. The MiniFASNet models from the Silent-Face-Anti-Spoofing
   project are the usual choice; they are small enough to commit.
2. Put it at `models/antispoof.onnx`.
3. Set `ANTISPOOF_MODEL=models/antispoof.onnx`.

The wrapper reads a three-class head as print / live / replay and a two-class
head as live / spoof. `ANTISPOOF_WEIGHT` (default 0.60) sets how much the model
counts against the passive signals; `ANTISPOOF_INPUT` sets the crop size.

If the file is absent or fails to load, the module silently falls back to the
passive signals. Deployment never breaks because a model is missing.

## Telemetry

Every enrolment and verification writes to `face_events`: scores, margin,
quality, blur, brightness, face ratio, pose, liveness components, decision,
reason and elapsed time. Logging failures are swallowed so telemetry can never
stop an employee from checking in.

```bash
python scripts/face_tuning_report.py --days 30
```

The report shows where accepted and rejected attempts actually sit and suggests
threshold moves. Until it has a few weeks of real traffic, any change to
`FACE_MATCH_THRESHOLD` is guesswork.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `FACE_MATCH_THRESHOLD` | 0.48 | Minimum gallery score to accept. OpenCV's own SFace reference is 0.363, so this is already strict |
| `FACE_MARGIN_MIN` | 0.06 | Required lead over the closest other employee |
| `FACE_QUALITY_MIN` | 45 | Quality floor at verification |
| `FACE_ENROLL_QUALITY_MIN` | 55 | Quality floor at enrolment |
| `FACE_ENROLL_SAMPLES` | 3 | Selfies required to complete registration |
| `FACE_DETECT_MAX_SIDE` | 640 | Detection working resolution |
| `FACE_BLUR_MIN` | 42 | Laplacian variance floor on the aligned crop |
| `FACE_ALLOW_MODEL_DOWNLOAD` | false | Emergency runtime model fetch |
| `LIVENESS_REJECT_AT` | 0.72 | Spoof score that refuses attendance |
| `LIVENESS_REVIEW_AT` | 0.52 | Spoof score that flags for review |
| `ANTISPOOF_MODEL` | *(empty)* | Path to the optional ONNX classifier |
| `ANTISPOOF_WEIGHT` | 0.60 | Model weight in the blend |
| `DUPLICATE_CORROBORATION_GATE` | 0.72 | Image similarity above which pose and landmark evidence counts |

## Known limits

- Passive liveness will not stop a determined attacker with a high-quality
  display. Add the ONNX model, and consider requiring two frames at different
  poses within one session if the risk justifies it.
- The impostor gallery is loaded in full. At a few thousand employees this
  should move to a vector index rather than a linear scan.
- There is no template updating. Beards, glasses and time will slowly raise
  false rejections; re-enrolment is currently manual through the HR panel.
