from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def _level(score: int) -> str:
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _business_impact(level: str, event_type: str) -> str:
    # Enterprise-friendly impact framing.
    if level in {"critical", "high"}:
        if event_type.startswith("auth."):
            return "Credential abuse risk: account takeover and unauthorized access to candidate data."
        if event_type.startswith("candidate."):
            return "Data exposure risk: bulk candidate export or scraping behavior."
        if event_type.startswith("admin."):
            return "Privilege risk: elevated permissions could enable tenant data leakage."
        return "Elevated operational risk: requires containment and investigation."
    if level == "medium":
        return "Moderate risk: investigate quickly to prevent escalation."
    return "Low risk: monitor and tighten controls if repeated."


def _attack_confidence(score: int, event_type: str, detected_threats: list[str]) -> float:
    # 0..1 heuristic confidence (not a guarantee).
    base = min(0.95, max(0.1, score / 100.0))
    if "token_misuse" in detected_threats:
        base = min(0.98, base + 0.08)
    if event_type == "auth.failed":
        base = min(0.9, base + 0.05)
    return round(base, 2)


def build_threat_response_plan(security_event: dict[str, Any]) -> dict[str, Any]:
    """
    Generate a recruiter/admin-readable response plan for a detected threat.
    Deterministic (no external model required).
    """
    event_type = str(security_event.get("event_type") or "unknown")
    score = int(security_event.get("risk_score") or 0)
    level = str(security_event.get("threat_level") or _level(score))
    detected = security_event.get("detected_threats") or []
    if not isinstance(detected, list):
        detected = []
    detected = [str(x) for x in detected if str(x)]

    source_ip = security_event.get("source_ip")
    user_id = security_event.get("user_id")

    mitigations: list[str] = []
    containments: list[dict[str, Any]] = []
    remediations: list[str] = []

    # Baseline guidance
    mitigations.append("Confirm whether this activity is expected for the user/workspace and time window.")
    mitigations.append("Review recent audit logs for correlated actions (exports, role changes, unusual API usage).")

    if event_type in {"auth.failed", "auth.lockout"}:
        containments.append(
            {
                "type": "temporary_account_lock",
                "label": "Temporarily lock the account (15 minutes)",
                "minutes": 15,
                "requires_confirmation": True,
                "rationale": "Stops credential stuffing / brute-force attempts while you investigate.",
            }
        )
        containments.append(
            {
                "type": "ip_blacklist_control",
                "label": "Block the source IP",
                "requires_confirmation": True,
                "rationale": "Reduces repeated attempts from a known suspicious source.",
            }
        )
        remediations.append("Require password reset for the user if multiple failures repeat across sessions.")
        remediations.append("Enable stricter login rate limits and consider MFA escalation.")

    if event_type == "auth.refresh_reuse" or "token_misuse" in detected:
        containments.append(
            {
                "type": "revoke_sessions",
                "label": "Invalidate sessions (revoke refresh tokens)",
                "requires_confirmation": True,
                "rationale": "Token reuse indicates session compromise or refresh-token replay.",
            }
        )
        remediations.append("Rotate JWT secret if broad token compromise is suspected (enterprise change control).")
        remediations.append("Review refresh token storage and replay protections.")

    if event_type == "candidate.mass_export" or "mass_download" in detected:
        containments.append(
            {
                "type": "throttle_user_control",
                "label": "Throttle user actions",
                "requires_confirmation": True,
                "rationale": "Limits exfiltration velocity while you confirm legitimacy.",
            }
        )
        remediations.append("Review export permissions and require admin approval for bulk exports.")
        remediations.append("Add anomaly alerts for export volume spikes.")

    if event_type == "admin.role_change" or "privilege_escalation_attempt" in detected:
        containments.append(
            {
                "type": "freeze_privileged_actions_control",
                "label": "Freeze privileged changes",
                "requires_confirmation": True,
                "rationale": "Prevents permission changes while you verify intent and audit trails.",
            }
        )
        remediations.append("Review admin activity for the last 24 hours and confirm change approvals.")

    if event_type == "interview.proctor_alert" or "interview_manipulation" in detected:
        containments.append(
            {
                "type": "flag_interview_session_control",
                "label": "Flag interview session for human review",
                "requires_confirmation": False,
                "rationale": "Maintains integrity by forcing manual review for suspicious signals.",
            }
        )
        remediations.append("Require identity verification steps for the candidate before proceeding.")

    # Always include a safe "Investigate" action.
    containments.append(
        {
            "type": "investigate",
            "label": "Investigate (collect evidence, correlate logs)",
            "requires_confirmation": False,
            "rationale": "Start with evidence-first triage to avoid false positives.",
        }
    )

    plan = {
        "generated_at": datetime.utcnow().isoformat(),
        "severity": level,
        "business_impact": _business_impact(level, event_type),
        "attack_confidence": _attack_confidence(score, event_type, detected),
        "affected_modules": _affected_modules(event_type),
        "mitigations": mitigations[:8],
        "containment_actions": containments[:10],
        "remediation_steps": remediations[:10],
        "explanation": _explanation(event_type, score, detected, source_ip, user_id),
    }
    return plan


def _affected_modules(event_type: str) -> list[str]:
    if event_type.startswith("auth."):
        return ["auth", "sessions"]
    if event_type.startswith("candidate."):
        return ["candidate_data", "exports"]
    if event_type.startswith("admin."):
        return ["rbac", "admin_controls"]
    if event_type.startswith("interview."):
        return ["interview", "proctoring"]
    return ["platform"]


def _explanation(event_type: str, score: int, threats: list[str], source_ip: Optional[str], user_id: Optional[int]) -> str:
    bits = []
    if event_type:
        bits.append(f"Event type: {event_type}.")
    bits.append(f"Risk score: {score}/100.")
    if threats:
        bits.append("Signals: " + ", ".join(threats[:6]) + ".")
    if source_ip:
        bits.append(f"Source IP: {source_ip}.")
    if user_id:
        bits.append(f"Affected user id: {user_id}.")
    bits.append("Recommendations are prioritized to reduce blast radius while you confirm intent.")
    return " ".join(bits)
