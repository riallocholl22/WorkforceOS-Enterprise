from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def _clamp01(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    return max(0.0, min(1.0, v))


def _pct01(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    return _clamp01(v / 100.0) if v > 1.0 else _clamp01(v)


def _bucket(conf: float) -> str:
    if conf >= 0.78:
        return "high"
    if conf >= 0.55:
        return "medium"
    return "low"


def _drivers(*items: str) -> list[str]:
    out: list[str] = []
    for item in items:
        item = str(item or "").strip()
        if not item or item in out:
            continue
        out.append(item)
    return out[:6]


def predict_candidate_outcomes(
    *,
    match_score: float,
    match_confidence: float,
    missing_skills: list[str] | None = None,
    experience_score: float = 0.0,
    extraction_confidence: float = 0.0,
) -> dict[str, Any]:
    """
    Deterministic "prediction" layer.

    Notes:
    - These are operational heuristics, not guarantees.
    - Kept model-free so the platform stays reliable even when an LLM is unavailable.
    """
    missing = missing_skills or []
    score01 = _pct01(match_score)
    conf01 = _pct01(match_confidence)  # match confidence in 0..1 already, but accept 0..100 too.
    exp01 = _pct01(experience_score)
    extract01 = _pct01(extraction_confidence)

    # Interview success: weighted toward match+experience, down-weighted by missing must-haves and low extraction quality.
    gap_penalty = min(0.22, len(missing) * 0.035)
    interview_success = (score01 * 0.58) + (conf01 * 0.18) + (exp01 * 0.18) + (extract01 * 0.06) - gap_penalty
    interview_success = _clamp01(interview_success)

    # Onboarding risk: inverse of readiness-like signals plus penalties.
    onboarding_risk = (1.0 - score01) * 0.48 + (1.0 - exp01) * 0.26 + max(0.0, 0.55 - conf01) * 0.22 + max(0.0, 0.45 - extract01) * 0.35
    onboarding_risk += min(0.25, len(missing) * 0.04)
    onboarding_risk = _clamp01(onboarding_risk)

    # Acceptance probability: conservative heuristic (fit + confidence, lightly reduced by risk).
    acceptance = (score01 * 0.40) + (conf01 * 0.25) + (exp01 * 0.15) + 0.12 - (onboarding_risk * 0.22)
    acceptance = _clamp01(acceptance)

    # Recruiter response likelihood: proxy for "will this move quickly" (fit + low risk).
    recruiter_response = _clamp01((score01 * 0.55) + (conf01 * 0.20) + ((1.0 - onboarding_risk) * 0.25))

    # Retention risk is an operational proxy derived from onboarding risk until richer HRIS history is connected.
    retention_risk = _clamp01((onboarding_risk * 0.65) + max(0.0, 0.6 - exp01) * 0.18)

    # Confidence trend for a single candidate: higher when signals are consistent and extraction is good.
    trend_conf = _clamp01((conf01 * 0.55) + (extract01 * 0.25) + (0.20 if len(missing) <= 2 else 0.05))

    drivers = _drivers(
        "high_match_score" if score01 >= 0.78 else "moderate_match_score" if score01 >= 0.55 else "low_match_score",
        "strong_experience_signal" if exp01 >= 0.70 else "limited_experience_signal" if exp01 <= 0.40 else "",
        "missing_must_haves" if missing else "",
        "low_extraction_confidence" if extract01 and extract01 < 0.45 else "",
    )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "interview_success_probability": {
            "value": round(interview_success, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Use this as a prioritization signal. Validate any listed gaps early in screening.",
        },
        "onboarding_risk": {
            "value": round(onboarding_risk, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Higher risk usually means more ramp time or missing role-critical exposure. Probe for concrete project evidence.",
        },
        "offer_acceptance_probability": {
            "value": round(acceptance, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Operational estimate based on fit signals. Calibrate with compensation range, location, and availability.",
        },
        "recruiter_response_likelihood": {
            "value": round(recruiter_response, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Higher likelihood means the profile is strong enough to justify fast follow-up.",
        },
        "retention_risk": {
            "value": round(retention_risk, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Retention risk is an operational proxy until tenure history, compensation, and role expectations are connected.",
        },
        "hiring_confidence_trend": {
            "value": round(trend_conf, 2),
            "confidence": _bucket(trend_conf),
            "drivers": drivers,
            "guidance": "Trend reflects how consistent the signals are (match, confidence, extraction).",
        },
    }


def predict_from_match_payload(match_payload: dict[str, Any], extraction_confidence: float = 0.0) -> dict[str, Any]:
    score = float(match_payload.get("match_score", match_payload.get("score", 0)) or 0)
    conf = match_payload.get("confidence", 0.0)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.0
    missing = match_payload.get("missing_skills") or []
    if not isinstance(missing, list):
        missing = []
    experience_score = float(match_payload.get("experience_score", 0.0) or 0.0)
    return predict_candidate_outcomes(
        match_score=score,
        match_confidence=conf,
        missing_skills=[str(s) for s in missing if isinstance(s, str)][:24],
        experience_score=experience_score,
        extraction_confidence=float(extraction_confidence or 0.0),
    )
