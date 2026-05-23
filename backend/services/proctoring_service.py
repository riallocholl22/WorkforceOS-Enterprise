import base64
import time
from typing import Any, Dict

import numpy as np

try:
    import cv2
    _opencv_error = ""
except Exception as e:  # pragma: no cover - optional dependency behavior
    cv2 = None
    _opencv_error = f"{type(e).__name__}: {str(e)[:200]}"


CASCADE_PATH = None
EYE_CASCADE_PATH = None
if cv2 is not None:  # pragma: no branch
    try:
        CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    except Exception:
        CASCADE_PATH = None
    try:
        EYE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_eye.xml"
    except Exception:
        EYE_CASCADE_PATH = None


_FACE_DETECTOR = None
_EYE_DETECTOR = None
_TRACK: dict[str, dict[str, Any]] = {}
_TRACK_TTL_SECONDS = 12 * 60


def proctor_health() -> Dict[str, Any]:
    """
    Interview vision health snapshot.
    (Camera access is client-side; this reports server-side vision readiness.)
    """
    ok = cv2 is not None
    version = ""
    try:
        version = getattr(cv2, "__version__", "") if cv2 is not None else ""
    except Exception:
        version = ""

    contrib = False
    try:
        if cv2 is not None:
            contrib = bool(getattr(cv2, "face", None) is not None) or bool(getattr(cv2, "xfeatures2d", None) is not None)
    except Exception:
        contrib = False

    return {
        "opencv_available": ok,
        "opencv_version": version or None,
        "opencv_error": None if ok else (_opencv_error or "opencv_unavailable"),
        "opencv_contrib_available": contrib,
        "face_cascade_available": bool(CASCADE_PATH),
        "eye_cascade_available": bool(EYE_CASCADE_PATH),
        "guidance": None if ok else "Server vision analysis is unavailable (OpenCV could not be loaded). Install OpenCV and required OS libraries, then restart the API to enable realtime proctoring signals.",
    }


def _get_detectors():
    global _FACE_DETECTOR, _EYE_DETECTOR
    if cv2 is None:
        return None, None
    if _FACE_DETECTOR is None:
        _FACE_DETECTOR = cv2.CascadeClassifier(CASCADE_PATH) if CASCADE_PATH else None
    if _EYE_DETECTOR is None:
        _EYE_DETECTOR = cv2.CascadeClassifier(EYE_CASCADE_PATH) if EYE_CASCADE_PATH else None
    return _FACE_DETECTOR, _EYE_DETECTOR


def _decode_image(image_base64: str):
    raw = image_base64.split(",", 1)[-1]
    data = base64.b64decode(raw)
    arr = np.frombuffer(data, dtype=np.uint8)
    if cv2 is None:
        return None
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _prune_track(now: float) -> None:
    # Prevent unbounded growth (best-effort; in-memory only).
    stale = [k for k, v in _TRACK.items() if (now - float(v.get("ts") or 0)) > _TRACK_TTL_SECONDS]
    for k in stale[:200]:
        _TRACK.pop(k, None)


def _movement_score(session_id: str, *, center: tuple[float, float], face_area_ratio: float, now: float) -> tuple[float, list[str]]:
    """
    Compute a stability score based on face center drift between frames.
    Returns (stability_score 0..100, alerts).
    """
    alerts: list[str] = []
    if not session_id:
        return 70.0, alerts

    prev = _TRACK.get(session_id) or {}
    prev_center = prev.get("center")
    prev_area = float(prev.get("area_ratio") or 0.0)

    _TRACK[session_id] = {"ts": now, "center": center, "area_ratio": float(face_area_ratio)}

    if not prev_center:
        return 75.0, alerts

    dx = float(center[0]) - float(prev_center[0])
    dy = float(center[1]) - float(prev_center[1])
    dist = (dx * dx + dy * dy) ** 0.5  # normalized by frame dims already

    # If face size suddenly changes, treat as unstable framing (moving closer/farther).
    area_delta = abs(float(face_area_ratio) - prev_area)

    stability = 100.0
    if dist > 0.22:
        alerts.append("head_movement_high")
        stability -= min(45.0, dist * 140.0)
    elif dist > 0.12:
        alerts.append("head_movement_medium")
        stability -= min(25.0, dist * 120.0)

    if area_delta > 0.08:
        alerts.append("framing_change")
        stability -= min(18.0, area_delta * 120.0)

    return max(0.0, min(100.0, round(stability, 2))), alerts


def analyze_frame(image_base64: str, *, session_id: str | None = None, candidate_id: str | None = None) -> Dict[str, Any]:
    if cv2 is None:
        return {
            "opencv_available": False,
            "opencv_error": _opencv_error or "opencv_unavailable",
            "attention_score": 0.0,
            "engagement_score": 0.0,
            "visibility_score": 0.0,
            "behavioral_confidence": 0.0,
            "emotion": "unknown",
            "cheating_flag": False,
            "alerts": ["opencv_unavailable"],
            "face_count": 0,
            "integrity_score": 0.0,
        }

    image = _decode_image(image_base64)
    if image is None:
        return {
            "opencv_available": True,
            "attention_score": 0.0,
            "engagement_score": 0.0,
            "visibility_score": 0.0,
            "behavioral_confidence": 0.0,
            "emotion": "unknown",
            "cheating_flag": True,
            "alerts": ["invalid_frame"],
            "face_count": 0,
            "integrity_score": 0.0,
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector, eye_detector = _get_detectors()
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(70, 70)) if detector is not None else []
    face_count = len(faces)
    alerts = []

    if face_count == 0:
        alerts.append("no_face")
    elif face_count > 1:
        alerts.append("multiple_faces")

    h, w = image.shape[:2]
    frame_center_x = w / 2.0
    frame_center_y = h / 2.0

    # Base scores
    attention_score = 0.0
    engagement_score = 0.0
    visibility_score = 0.0
    behavioral_confidence = 0.0

    eye_count = 0
    lighting_score = 0.0
    framing_score = 0.0
    sharpness_score = 0.0
    stability_score = 70.0

    if face_count == 1:
        x, y, fw, fh = faces[0]
        face_area_ratio = (float(fw) * float(fh)) / max(1.0, float(w) * float(h))
        center_x = x + (fw / 2.0)
        center_y = y + (fh / 2.0)

        # Normalize center drift (0..~1)
        drift_x = abs(center_x - frame_center_x) / max(frame_center_x, 1.0)
        drift_y = abs(center_y - frame_center_y) / max(frame_center_y, 1.0)
        drift_ratio = max(drift_x, drift_y)

        # Framing: prefer face area ratio ~ 0.12 - 0.45
        framing_score = 100.0
        if drift_ratio > 0.35:
            alerts.append("attention_drift")
            framing_score -= min(55.0, drift_ratio * 120.0)
        elif drift_ratio > 0.20:
            alerts.append("framing_off_center")
            framing_score -= min(25.0, drift_ratio * 95.0)

        if face_area_ratio < 0.06:
            alerts.append("face_too_far")
            framing_score -= 28.0
        elif face_area_ratio > 0.55:
            alerts.append("face_too_close")
            framing_score -= 18.0

        framing_score = max(0.0, min(100.0, framing_score))

        # Eyes: quick check on upper face ROI
        if eye_detector is not None:
            roi = gray[y : y + int(fh * 0.7), x : x + fw]
            try:
                eyes = eye_detector.detectMultiScale(roi, scaleFactor=1.15, minNeighbors=6, minSize=(18, 18))
                eye_count = int(len(eyes))
            except Exception:
                eye_count = 0
        if eye_count == 0:
            alerts.append("eyes_not_detected")

        # Stability (needs session id to be meaningful)
        now = time.time()
        _prune_track(now)
        stability_score, move_alerts = _movement_score(
            str(session_id or ""),
            center=(float(center_x) / max(1.0, float(w)), float(center_y) / max(1.0, float(h))),
            face_area_ratio=float(face_area_ratio),
            now=now,
        )
        alerts.extend(move_alerts)

    # Lighting quality
    brightness = float(np.mean(gray)) if gray.size else 0.0
    lighting_score = 100.0
    if brightness < 35:
        alerts.append("low_light")
        lighting_score -= 45.0
    elif brightness < 55:
        alerts.append("dim_light")
        lighting_score -= 22.0
    elif brightness > 210:
        alerts.append("overexposed")
        lighting_score -= 16.0
    lighting_score = max(0.0, min(100.0, lighting_score))

    # Sharpness / blur detection (variance of Laplacian)
    try:
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        var = float(lap.var()) if lap is not None else 0.0
        # Typical thresholds vary by camera; keep gentle.
        sharpness_score = 100.0
        if var < 35:
            alerts.append("blurry_frame")
            sharpness_score -= 45.0
        elif var < 75:
            alerts.append("slightly_blurry")
            sharpness_score -= 18.0
        sharpness_score = max(0.0, min(100.0, sharpness_score))
    except Exception:
        sharpness_score = 70.0

    # Aggregate scores
    if face_count == 1:
        # Attention rewards: face present + eyes + stable + centered.
        eye_bonus = 8.0 if eye_count >= 1 else -10.0
        attention_score = 70.0 + eye_bonus + (stability_score - 75.0) * 0.35 + (framing_score - 75.0) * 0.35
        attention_score = max(0.0, min(100.0, attention_score))

        visibility_score = 0.42 * framing_score + 0.26 * lighting_score + 0.18 * sharpness_score + 0.14 * stability_score
        engagement_score = 0.55 * attention_score + 0.25 * stability_score + 0.20 * (100.0 if eye_count >= 1 else 55.0)
        behavioral_confidence = 0.65 * visibility_score + 0.35 * engagement_score
    elif face_count > 1:
        attention_score = 25.0
        visibility_score = 45.0
        engagement_score = 35.0
        behavioral_confidence = 30.0
    else:
        attention_score = 10.0
        visibility_score = 10.0
        engagement_score = 15.0
        behavioral_confidence = 10.0

    attention_score = round(max(0.0, min(100.0, attention_score)), 2)
    engagement_score = round(max(0.0, min(100.0, engagement_score)), 2)
    visibility_score = round(max(0.0, min(100.0, visibility_score)), 2)
    behavioral_confidence = round(max(0.0, min(100.0, behavioral_confidence)), 2)

    cheating_flag = any(flag in alerts for flag in ["multiple_faces", "no_face"])

    if cheating_flag and attention_score < 40:
        emotion = "distracted"
    elif attention_score >= 75:
        emotion = "focused"
    else:
        emotion = "neutral"

    integrity_score = round(max(0.0, min(100.0, (attention_score * 0.65 + visibility_score * 0.35) - (14.0 if cheating_flag else 0.0))), 2)

    return {
        "opencv_available": True,
        "opencv_version": getattr(cv2, "__version__", None),
        "session_id": str(session_id) if session_id else None,
        "candidate_id": str(candidate_id) if candidate_id else None,
        "attention_score": attention_score,
        "engagement_score": engagement_score,
        "visibility_score": visibility_score,
        "behavioral_confidence": behavioral_confidence,
        "emotion": emotion,
        "cheating_flag": cheating_flag,
        "alerts": sorted(set([str(a) for a in alerts if a])),
        "face_count": face_count,
        "eye_count": eye_count,
        "lighting_score": round(lighting_score, 2),
        "framing_score": round(framing_score, 2),
        "sharpness_score": round(sharpness_score, 2),
        "stability_score": round(stability_score, 2),
        "integrity_score": integrity_score,
    }
