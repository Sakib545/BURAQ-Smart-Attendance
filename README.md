# BURAQ Smart Attendance v6.1 — Face Detection Hotfix

This build fixes WhatsApp selfie detection failures seen in v6.0.

## Fixes
- Applies phone-camera EXIF orientation before face detection.
- Uses a lower, practical YuNet confidence threshold.
- Retries detection after low-light enhancement.
- Caps very large images to reduce Railway memory usage.
- Rejects multiple faces, blurry images and very distant faces.
- Shows useful face-quality progress during 3-selfie registration.
- Gives diagnostic image dimensions and lighting guidance when detection fails.
- Keeps Face AI matching, GPS verification, existing employees and attendance data.

## Employee instructions
Send each registration selfie as a separate WhatsApp image message. Do not send a collage. Use the WhatsApp camera, face the camera, and keep only one person in the frame.

## Railway
Replace the old repository files with this build and redeploy. Existing persistent database data is not intentionally deleted.

Required variables include:

```
OFFICE_LATITUDE=25.18892481916644
OFFICE_LONGITUDE=89.87014577946071
OFFICE_RADIUS_METERS=100
```
