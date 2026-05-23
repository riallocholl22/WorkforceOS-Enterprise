from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from backend.db.database import SessionLocal
from backend.models.enterprise import AuditLog, SecurityEvent
from backend.services.security_ai.incident_service import ingest_security_event
from backend.services.security_ai.response_engine import build_threat_response_plan
from backend.services.security_ai.automation_rules_service import ensure_default_security_rules, evaluate_security_rules
from backend.services.security_ai.actions_service import execute_security_action
from backend.services.enterprise_service import create_notification, log_audit_event
from backend.services.security_ai.control_center import security_controls_snapshot
from backend.services.security_ai.incident_service import list_incidents
from backend.services.security_ai.automation_rules_service import list_security_rules


def _level(score: int) -> str:
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def analyze_event(
    event_type: str,
    organization_id: Optional[int] = None,
    user_id: Optional[int] = None,
    source_ip: Optional[str] = None,
    details: Optional[dict] = None,
) -> Dict[str, Any]:
    details = details or {}
    threats = []
    score = 5

    if event_type in {"auth.failed", "auth.lockout"}:
        score += 35
        threats.append("brute_force_signal")
    if event_type == "auth.refresh_reuse":
        score += 60
        threats.append("token_misuse")
    if event_type == "admin.role_change":
        score += 35
        threats.append("privilege_escalation_attempt")
    if event_type == "candidate.mass_export":
        score += 55
        threats.append("mass_download")
    if event_type == "interview.proctor_alert":
        score += 25
        threats.append("interview_manipulation")
    if details.get("requests_per_minute", 0) > 120:
        score += 45
        threats.append("abnormal_request_frequency")
    if details.get("impossible_travel"):
        score += 70
        threats.append("impossible_travel")
    if details.get("bot_score", 0) > 80:
        score += 45
        threats.append("bot_activity")

    score = max(0, min(100, score))
    level = _level(score)
    action = "monitor"
    if level == "medium":
        action = "require_mfa"
    elif level == "high":
        action = "revoke_tokens_and_notify_admin"
    elif level == "critical":
        action = "lock_account_block_ip_notify_admin"

    return {
        "risk_score": score,
        "threat_level": level,
        "detected_threats": sorted(set(threats)) or ["none"],
        "recommended_action": action,
        "source_ip": source_ip,
        "event_type": event_type,
        "details": details,
        "organization_id": organization_id,
        "user_id": user_id,
    }


def record_security_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        row = SecurityEvent(
            organization_id=payload.get("organization_id"),
            user_id=payload.get("user_id"),
            source_ip=payload.get("source_ip"),
            event_type=payload.get("event_type", "unknown"),
            risk_score=int(payload.get("risk_score", 0)),
            threat_level=payload.get("threat_level", "low"),
            detected_threats=payload.get("detected_threats", []),
            recommended_action=payload.get("recommended_action", "monitor"),
            details=payload.get("details", {}),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return serialize_security_event(row)
    finally:
        db.close()


def monitor_event(event_type: str, organization_id: Optional[int] = None, user_id: Optional[int] = None, source_ip: Optional[str] = None, details: Optional[dict] = None) -> Dict[str, Any]:
    # Ensure baseline automation rules exist for the org.
    try:
        ensure_default_security_rules(organization_id)
    except Exception:
        pass

    event = record_security_event(analyze_event(event_type, organization_id, user_id, source_ip, details))

    # Build response plan + incident workflow.
    try:
        plan = build_threat_response_plan(event)
        incident = ingest_security_event({**event, "response_plan": plan}, organization_id)
    except Exception:
        plan = {}
        incident = None

    # Evaluate automation rules (recommendation-first by default).
    try:
        rule_eval = evaluate_security_rules(organization_id, trigger="security.event", event=event)
    except Exception:
        rule_eval = {"trigger": "security.event", "rules_evaluated": 0, "rules_fired": [], "actions": [], "auto_execute": False}

    # Auto-execute only if a rule explicitly requests it (and only for safe actions).
    executed: list[dict] = []
    if rule_eval.get("auto_execute") and isinstance(rule_eval.get("actions"), list):
        for a in rule_eval["actions"][:5]:
            if not isinstance(a, dict) or not a.get("type"):
                continue
            action_type = str(a.get("type"))
            if action_type not in {"revoke_sessions", "lock_account", "temporary_account_lock"}:
                continue
            try:
                res = execute_security_action(
                    organization_id=organization_id,
                    incident_id=(incident or {}).get("incident_id") if isinstance(incident, dict) else None,
                    event_id=event.get("id"),
                    actor_user_id=user_id,
                    action_type=("revoke_sessions" if action_type == "revoke_sessions" else "lock_account"),
                    payload={
                        "target_user_id": event.get("user_id"),
                        "minutes": int(a.get("minutes") or 15),
                    },
                    reason="security_automation_rule",
                )
                executed.append(res)
            except Exception:
                continue

    # Notify on high severity incidents.
    try:
        if str(event.get("threat_level") or "").lower() in {"high", "critical"} and user_id:
            create_notification(
                user_id=int(user_id),
                organization_id=organization_id,
                kind="security_alert",
                subject="security.incident",
                message=str((plan or {}).get("explanation") or "Security incident detected."),
                metadata={"event": event, "incident": incident, "automation": rule_eval},
            )
    except Exception:
        pass

    return {
        **event,
        "response_plan": plan,
        "incident": incident,
        "automation": rule_eval,
        "executed_actions": executed,
    }


def security_overview(organization_id: Optional[int] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=7)
        query = db.query(SecurityEvent)
        audit_query = db.query(AuditLog)
        if organization_id is not None:
            query = query.filter(SecurityEvent.organization_id == organization_id)
            audit_query = audit_query.filter(AuditLog.organization_id == organization_id)
        events = query.order_by(SecurityEvent.created_at.desc()).limit(100).all()
        recent = [event for event in events if event.created_at and event.created_at >= since]
        threat_counts = Counter(event.threat_level for event in recent)
        ip_counts = Counter(event.source_ip for event in recent if event.source_ip)
        max_risk = max([event.risk_score for event in recent], default=0)
        incidents = list_incidents(organization_id, status=None, limit=25) if organization_id is not None else []
        rules = list_security_rules(organization_id, limit=25) if organization_id is not None else []
        return {
            "risk_score": max_risk,
            "threat_level": _level(max_risk),
            "detected_threats": sorted({threat for event in recent for threat in (event.detected_threats or []) if threat != "none"}),
            "recommended_action": "review_security_timeline" if max_risk >= 40 else "monitor",
            "security_events": [serialize_security_event(event) for event in events[:25]],
            "attack_timeline": [serialize_security_event(event) for event in recent[:25]],
            "incidents": incidents,
            "control_center": security_controls_snapshot(),
            "automation_rules": rules,
            "blocked_attack_summaries": [
                {"threat_level": level, "count": count}
                for level, count in threat_counts.items()
            ],
            "suspicious_ips": [
                {"ip": ip, "count": count}
                for ip, count in ip_counts.most_common(10)
            ],
            "audit_events_last_7_days": audit_query.filter(AuditLog.created_at >= since).count(),
        }
    finally:
        db.close()


def serialize_security_event(event: SecurityEvent) -> Dict[str, Any]:
    return {
        "id": event.id,
        "organization_id": event.organization_id,
        "user_id": event.user_id,
        "source_ip": event.source_ip,
        "event_type": event.event_type,
        "risk_score": event.risk_score,
        "threat_level": event.threat_level,
        "detected_threats": event.detected_threats or [],
        "recommended_action": event.recommended_action,
        "details": event.details or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
