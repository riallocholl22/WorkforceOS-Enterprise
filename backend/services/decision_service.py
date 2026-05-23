from typing import Any, Dict, List


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def make_decision(
    match_score: float,
    interview_score: float = 0,
    communication_score: float = 0,
    proctor_score: float = 100,
    missing_skills: List[str] | None = None,
) -> Dict[str, Any]:
    missing = missing_skills or []
    weighted = (
        (_clamp(match_score) * 0.45)
        + (_clamp(interview_score) * 0.30)
        + (_clamp(communication_score) * 0.15)
        + (_clamp(proctor_score) * 0.10)
    )
    confidence = _clamp(weighted)

    risk_flags = []
    reasoning = []

    if missing:
        reasoning.append(f"Missing skills: {', '.join(missing[:5])}")
        if len(missing) >= 3:
            risk_flags.append("skills_gap")

    if proctor_score < 60:
        risk_flags.append("integrity_risk")
        reasoning.append("Interview integrity signals need human review.")

    if interview_score and interview_score < 50:
        risk_flags.append("low_interview_score")
        reasoning.append("Interview performance was below the preferred threshold.")

    if communication_score and communication_score < 50:
        risk_flags.append("communication_gap")
        reasoning.append("Communication clarity was inconsistent.")

    if confidence >= 85 and proctor_score >= 70 and len(missing) <= 2:
        decision = "hire"
        reasoning.insert(0, "Strong combined fit across matching, interview performance, and integrity.")
    elif confidence >= 60:
        decision = "consider"
        reasoning.insert(0, "Candidate shows promise but still needs recruiter review on a few dimensions.")
    else:
        decision = "reject"
        reasoning.insert(0, "Current evidence does not support progression without significant improvement.")

    return {
        "decision": decision,
        "confidence": confidence,
        "reasoning": reasoning[:6],
        "risk_flags": risk_flags,
    }
