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
from backend.services.security_ai.capabilities import capability_for_event, capability_matrix, event_options


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
    capability = capability_for_event(event_type)

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
    if capability:
        threats.extend(capability.get("detections") or [])
        score += _capability_score(event_type, details)

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
        "capability": _capability_summary(capability),
        "ai_reasoning": _ai_reasoning(event_type, score, threats, details, capability),
        "confidence": _confidence(score, details, capability),
        "uncertainty": _uncertainty(details, capability),
        "operational_impact": (capability or {}).get("impact") or "Security signal requires monitoring and correlation.",
        "mitre_attack": (capability or {}).get("mitre", []),
        "organization_id": organization_id,
        "user_id": user_id,
    }


def _capability_score(event_type: str, details: dict[str, Any]) -> int:
    score = 0
    if event_type == "email.phishing":
        score += 24
        score += 18 if details.get("attachment_scan") in {"malicious", "suspicious"} else 0
        score += 18 if details.get("sender_reputation") in {"poor", "unknown"} else 0
        score += 12 if details.get("suspicious_domain") else 0
    elif event_type == "network.home_anomaly":
        score += 24 + min(24, int(details.get("unknown_device_count") or 0) * 6)
        score += 18 if details.get("unauthorized_access") else 0
    elif event_type == "endpoint.windows_event":
        score += 28
        score += 24 if details.get("privilege_escalation") or details.get("event_id") in {4670, 4672, 7045} else 0
    elif event_type == "siem.alert":
        score += 28 + min(28, int(details.get("alert_count") or 0) * 3)
        score += 14 if details.get("asset_criticality") == "high" else 0
    elif event_type == "intel.ioc_report":
        score += 18 + min(32, int(details.get("ioc_count") or 0) * 2)
    elif event_type == "network.malware_traffic":
        score += 52
        score += 18 if details.get("beacon_interval") or details.get("destination_reputation") == "malicious" else 0
    elif event_type == "endpoint.powershell":
        score += 38
        score += 24 if details.get("encoded_command") or details.get("download_cradle") else 0
        score += 12 if details.get("execution_policy_bypass") else 0
    elif event_type == "dns.suspicious":
        score += 34
        score += 22 if details.get("beaconing") or float(details.get("query_entropy") or 0) > 7 else 0
    elif event_type == "ids.suricata_alert":
        score += 36
        score += 18 if details.get("signature_category") in {"trojan", "exploit", "malware"} else 0
    elif event_type == "siem.splunk_detection":
        score += 32
        score += 18 if details.get("notable_event") or details.get("risk_object") else 0
    elif event_type == "edr.wazuh_alert":
        score += 34
        score += 16 if details.get("file_integrity") or details.get("process_event") else 0
    elif event_type == "endpoint.ransomware_behavior":
        score += 72
        score += 18 if details.get("shadow_copy_delete") or details.get("lateral_movement") else 0
    elif event_type == "endpoint.usb_malware":
        score += 45
        score += 18 if details.get("new_executable") or details.get("autorun_artifact") else 0
    elif event_type == "auth.failed_correlation":
        score += 48
        score += min(28, int(details.get("multi_source_failures") or 0) * 4)
        score += 18 if details.get("success_after_failures") else 0
    elif event_type == "web.attack":
        score += 42
        score += 18 if details.get("payload_signature") or details.get("path_probe") else 0
    elif event_type == "intel.mitre_mapping":
        score += 22
    elif event_type == "insider.behavior":
        score += 46
        score += 20 if details.get("after_hours_access") or details.get("export_volume", 0) > 50 else 0
    elif event_type == "endpoint.triage":
        score += 30 + min(35, int(details.get("host_severity") or 0))
    elif event_type == "ir.playbook":
        score += 20
        score += 20 if details.get("containment_state") in {"blocked", "failed", "pending"} else 0
    return score


def _capability_summary(capability: dict[str, Any] | None) -> dict[str, Any] | None:
    if not capability:
        return None
    return {
        "id": capability.get("id"),
        "name": capability.get("name"),
        "domain": capability.get("domain"),
        "tier": capability.get("tier"),
    }


def _confidence(score: int, details: dict[str, Any], capability: dict[str, Any] | None) -> int:
    evidence = len([value for value in (details or {}).values() if value not in (None, "", [], {})])
    base = min(94, max(42, score + evidence * 4))
    if capability:
        base = min(96, base + 5)
    if evidence <= 1:
        base = max(35, base - 12)
    return int(base)


def _uncertainty(details: dict[str, Any], capability: dict[str, Any] | None) -> str:
    evidence = len([value for value in (details or {}).values() if value not in (None, "", [], {})])
    if not capability:
        return "Unknown event type; AI reasoning is limited to generic risk signals."
    if evidence <= 1:
        return "Limited evidence. Correlate with identity, endpoint, DNS, and SIEM context before containment."
    if evidence <= 3:
        return "Moderate evidence. Confidence improves if endpoint and identity telemetry agree."
    return "Evidence coverage is strong; continue validating business intent before disruptive actions."


def _ai_reasoning(event_type: str, score: int, threats: list[str], details: dict[str, Any], capability: dict[str, Any] | None) -> str:
    label = (capability or {}).get("name") or event_type.replace(".", " ")
    signal_count = len([value for value in (details or {}).values() if value not in (None, "", [], {})])
    top = ", ".join(sorted(set(threats))[:4]) or "baseline telemetry"
    return f"{label} scored {score}/100 from {signal_count} evidence fields. Primary signals: {top}."


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
            details={
                **(payload.get("details", {}) or {}),
                "capability": payload.get("capability"),
                "ai_reasoning": payload.get("ai_reasoning"),
                "confidence": payload.get("confidence"),
                "uncertainty": payload.get("uncertainty"),
                "operational_impact": payload.get("operational_impact"),
                "mitre_attack": payload.get("mitre_attack", []),
            },
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
        serialized_events = [serialize_security_event(event) for event in events[:25]]
        return {
            "risk_score": max_risk,
            "threat_level": _level(max_risk),
            "detected_threats": sorted({threat for event in recent for threat in (event.detected_threats or []) if threat != "none"}),
            "recommended_action": "review_security_timeline" if max_risk >= 40 else "monitor",
            "security_events": serialized_events,
            "attack_timeline": [serialize_security_event(event) for event in recent[:25]],
            "incidents": incidents,
            "control_center": security_controls_snapshot(),
            "automation_rules": rules,
            "soc_capabilities": capability_matrix(serialized_events),
            "security_event_options": event_options(),
            "ai_security_brief": _security_brief(max_risk, incidents, serialized_events),
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
        "capability": (event.details or {}).get("capability"),
        "ai_reasoning": (event.details or {}).get("ai_reasoning"),
        "confidence": (event.details or {}).get("confidence"),
        "uncertainty": (event.details or {}).get("uncertainty"),
        "operational_impact": (event.details or {}).get("operational_impact"),
        "mitre_attack": (event.details or {}).get("mitre_attack", []),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _security_brief(max_risk: int, incidents: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    open_incidents = [item for item in incidents if str(item.get("status") or "").lower() not in {"resolved", "closed"}]
    high_events = [item for item in events if str(item.get("threat_level") or "").lower() in {"high", "critical"}]
    if high_events:
        summary = f"{len(high_events)} high-priority security signals require coordinated review."
    elif open_incidents:
        summary = f"{len(open_incidents)} incident workflows are active with no critical escalation pressure."
    else:
        summary = "Security operations are synchronized; no active critical response pressure detected."
    return {
        "summary": summary,
        "risk_score": max_risk,
        "open_incidents": len(open_incidents),
        "confidence": min(96, max(55, 60 + len(events) * 2 + len(incidents) * 3)),
        "uncertainty": "Confidence depends on connected endpoint, DNS, SIEM, and identity telemetry coverage.",
        "executive_recommendation": "Keep response automation recommendation-first unless critical identity or ransomware signals appear.",
    }
