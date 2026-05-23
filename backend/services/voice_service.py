import base64
import io
import math
import wave
from typing import Any, Dict, Optional

import numpy as np

from backend.db.database import SessionLocal
from backend.models.enterprise import VoiceProfile


def _decode_waveform(audio_base64: str) -> tuple[np.ndarray, int]:
    raw = base64.b64decode(audio_base64.split(",", 1)[-1])
    with wave.open(io.BytesIO(raw), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frames = wav_file.readframes(frame_count)

    if sample_width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        data /= 32768.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    return data, frame_rate


def extract_voice_features(audio_base64: str) -> Dict[str, Any]:
    samples, sample_rate = _decode_waveform(audio_base64)
    if samples.size == 0:
        return {"duration": 0.0, "rms": 0.0, "zcr": 0.0, "spectral_centroid": 0.0}

    duration = float(samples.size / max(sample_rate, 1))
    rms = float(np.sqrt(np.mean(samples ** 2)))
    zero_crossings = np.where(np.diff(np.signbit(samples)))[0]
    zcr = float(len(zero_crossings) / max(samples.size, 1))

    fft_values = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / max(sample_rate, 1))
    spectral_centroid = float(np.sum(freqs * fft_values) / max(np.sum(fft_values), 1e-8))

    return {
        "duration": round(duration, 3),
        "rms": round(rms, 5),
        "zcr": round(zcr, 5),
        "spectral_centroid": round(spectral_centroid, 3),
    }


def _feature_distance(current: Dict[str, Any], reference: Dict[str, Any]) -> float:
    keys = ["duration", "rms", "zcr", "spectral_centroid"]
    total = 0.0
    for key in keys:
        total += (float(current.get(key, 0)) - float(reference.get(key, 0))) ** 2
    return math.sqrt(total)


def enroll_voice_profile(user_id: int, candidate_id: str, audio_base64: str, sample_name: str = "") -> Dict[str, Any]:
    features = extract_voice_features(audio_base64)
    db = SessionLocal()
    try:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id,
            VoiceProfile.candidate_id == candidate_id,
        ).first()
        if profile:
            profile.feature_vector = features
            profile.sample_name = sample_name or profile.sample_name
        else:
            profile = VoiceProfile(
                user_id=user_id,
                candidate_id=candidate_id,
                sample_name=sample_name,
                feature_vector=features,
            )
            db.add(profile)
        db.commit()
        db.refresh(profile)
        return {
            "candidate_id": candidate_id,
            "features": features,
            "enrolled": True,
        }
    finally:
        db.close()


def verify_voice_profile(user_id: int, candidate_id: str, audio_base64: str) -> Dict[str, Any]:
    features = extract_voice_features(audio_base64)
    db = SessionLocal()
    try:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id,
            VoiceProfile.candidate_id == candidate_id,
        ).first()
        if not profile:
            return {
                "identity_verified": False,
                "confidence": 0.0,
                "features": features,
                "message": "No enrolled voice profile found",
            }

        distance = _feature_distance(features, profile.feature_vector or {})
        confidence = round(max(0.0, min(100.0, 100.0 - (distance * 20.0))), 2)
        return {
            "identity_verified": confidence >= 60.0,
            "confidence": confidence,
            "features": features,
            "message": "Voice verification completed",
        }
    finally:
        db.close()
