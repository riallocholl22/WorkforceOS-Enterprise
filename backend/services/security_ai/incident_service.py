from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.models.enterprise import SecurityEvent, SecurityIncident
from backend.services.security_ai.response_engine import build_threat_response_plan
from backend.services.security_ai.capabilities import capability_for_event


def _now() -> datetime:
    return datetime.utcnow()


def _fingerprint(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": str(event.get("event_type") or "unknown"),
        "source_ip": (str(event.get("source_ip") or "").strip() or None),
        "user_id": int(event.get("user_id")) if event.get("user_id") is not None else None,
    }


def _incident_type(event_type: str) -> str:
    capability = capability_for_event(event_type)
    if capability:
        domain = str(capability.get("domain") or "platform")
        return {
            "email_security": "phishing_forensics",
            "network_security": "network_threat",
            "endpoint_security": "endpoint_threat",
            "siem": "siem_investigation",
            "threat_intel": "threat_intelligence",
            "ids": "ids_alert",
            "identity_security": "auth_abuse",
            "application_security": "web_attack",
            "behavioral_security": "insider_risk",
            "incident_response": "response_coordination",
        }.get(domain, f"{domain}_risk")[:80]
    if event_type.startswith("auth."):
        return "auth_abuse"
    if event_type.startswith("candidate."):
        return "data_exfiltration"
    if event_type.startswith("admin."):
        return "privilege_risk"
    if event_type.startswith("interview."):
        return "interview_integrity"
    return "platform_risk"


def _title(event_type: str, threat_level: str) -> str:
    capability = capability_for_event(event_type)
    human = str(capability.get("name")) if capability else event_type.replace(".", " ").replace("_", " ").title()
    prefix = "Critical" if threat_level == "critical" else "High" if threat_level == "high" else "Security"
    return f"{prefix}: {human}"


def ingest_security_event(security_event: dict[str, Any], organization_id: Optional[int]) -> dict[str, Any]:
    """
    Create or update a SecurityIncident based on a newly recorded SecurityEvent.
    Returns a JSON-safe incident summary for API responses.
    """
    if not isinstance(security_event, dict):
        return {"status": "skipped", "reason": "invalid_event"}

    fp = _fingerprint(security_event)
    event_type = fp["event_type"]
    source_ip = fp["source_ip"]
    user_id = fp["user_id"]
    threat_level = str(security_event.get("threat_level") or "low")
    risk_score = int(security_event.get("risk_score") or 0)

    incident_type = _incident_type(event_type)
    plan = build_threat_response_plan({**security_event, "event_type": event_type, "source_ip": source_ip, "user_id": user_id})

    db = SessionLocal()
    try:
        # Dedupe window: treat repeated events as the same incident for 24h.
        since = _now() - timedelta(hours=24)
        q = db.query(SecurityIncident).filter(SecurityIncident.organization_id == organization_id)
        q = q.filter(SecurityIncident.status.in_(["open", "contained"]))
        q = q.filter(SecurityIncident.created_at >= since)
        q = q.filter(SecurityIncident.incident_type == incident_type)
        if source_ip:
            q = q.filter(or_(SecurityIncident.source_ip == source_ip, SecurityIncident.source_ip.is_(None)))
        if user_id is not None:
            q = q.filter(or_(SecurityIncident.user_id == user_id, SecurityIncident.user_id.is_(None)))
        existing = q.order_by(SecurityIncident.updated_at.desc()).first()

        timeline_item = {
            "ts": _now().isoformat(),
            "event_id": security_event.get("id"),
            "event_type": event_type,
            "threat_level": threat_level,
            "risk_score": risk_score,
            "capability": (security_event.get("capability") or {}).get("name") if isinstance(security_event.get("capability"), dict) else None,
            "ai_reasoning": security_event.get("ai_reasoning") or (security_event.get("details") or {}).get("ai_reasoning"),
        }

        if existing:
            existing.updated_at = _now()
            existing.severity = threat_level
            existing.confidence = int(min(100, max(0, round(float(plan.get("attack_confidence") or 0) * 100))))
            existing.source_ip = existing.source_ip or source_ip
            existing.user_id = existing.user_id or user_id
            existing.title = existing.title or _title(event_type, threat_level)
            existing.summary = (existing.summary or plan.get("explanation") or "")[:2000]
            existing.affected_modules = plan.get("affected_modules") or existing.affected_modules or []
            existing.response_plan = plan or {}

            related = existing.related_event_ids or []
            if security_event.get("id") and security_event.get("id") not in related:
                related.append(security_event.get("id"))
            existing.related_event_ids = related[-60:]

            tl = existing.timeline or []
            tl.append(timeline_item)
            existing.timeline = tl[-120:]

            db.commit()
            db.refresh(existing)
            return serialize_incident(existing)

        incident = SecurityIncident(
            organization_id=organization_id,
            status="open",
            severity=threat_level,
            confidence=int(min(100, max(0, round(float(plan.get("attack_confidence") or 0) * 100)))),
            incident_type=incident_type,
            title=_title(event_type, threat_level),
            summary=(plan.get("explanation") or "")[:2000],
            source_ip=source_ip,
            user_id=user_id,
            affected_modules=plan.get("affected_modules") or [],
            response_plan=plan or {},
            containment={"status": "not_started", "actions": []},
            timeline=[timeline_item],
            related_event_ids=[security_event.get("id")] if security_event.get("id") else [],
            owner_user_id=None,
            notes="",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)
        return serialize_incident(incident)
    finally:
        db.close()


def serialize_incident(row: SecurityIncident) -> dict[str, Any]:
    return {
        "incident_id": row.id,
        "organization_id": row.organization_id,
        "status": row.status,
        "severity": row.severity,
        "confidence": row.confidence,
        "incident_type": row.incident_type,
        "title": row.title,
        "summary": row.summary,
        "source_ip": row.source_ip,
        "user_id": row.user_id,
        "affected_modules": row.affected_modules or [],
        "response_plan": row.response_plan or {},
        "containment": row.containment or {},
        "timeline": (row.timeline or [])[-50:],
        "related_event_ids": row.related_event_ids or [],
        "owner_user_id": row.owner_user_id,
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def list_incidents(organization_id: Optional[int], *, status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityIncident)
        if organization_id is not None:
            q = q.filter(SecurityIncident.organization_id == organization_id)
        if status:
            q = q.filter(SecurityIncident.status == status)
        rows = q.order_by(SecurityIncident.updated_at.desc()).limit(limit).all()
        return [serialize_incident(r) for r in rows]
    finally:
        db.close()


def get_incident(organization_id: Optional[int], incident_id: int) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityIncident).filter(SecurityIncident.id == int(incident_id))
        if organization_id is not None:
            q = q.filter(SecurityIncident.organization_id == organization_id)
        row = q.first()
        return serialize_incident(row) if row else None
    finally:
        db.close()


def update_incident_status(organization_id: Optional[int], incident_id: int, *, status: str, owner_user_id: Optional[int] = None, notes: str = "") -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityIncident).filter(SecurityIncident.id == int(incident_id))
        if organization_id is not None:
            q = q.filter(SecurityIncident.organization_id == organization_id)
        row = q.first()
        if not row:
            return None
        row.status = (status or row.status)[:40]
        if owner_user_id is not None:
            row.owner_user_id = int(owner_user_id)
        if notes:
            row.notes = (row.notes or "") + ("\n" if row.notes else "") + notes[:2000]
        row.updated_at = _now()
        if row.status == "resolved":
            row.resolved_at = _now()
        db.commit()
        db.refresh(row)
        return serialize_incident(row)
    finally:
        db.close()
