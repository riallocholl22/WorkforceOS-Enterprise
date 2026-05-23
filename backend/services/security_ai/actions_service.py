from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.models.enterprise import SecurityIncident, SecurityResponseAction
from backend.services.auth_service import revoke_all_sessions, lock_user_temporarily, unlock_user


def _now() -> datetime:
    return datetime.utcnow()


def _mark_action(row: SecurityResponseAction, status: str, *, error: str = "") -> None:
    row.status = status
    row.executed_at = _now()
    if error:
        row.reason = (row.reason or "") + ("\n" if row.reason else "") + error[:1200]


def lock_account(user_id: int, minutes: int, reason: str = "") -> dict[str, Any]:
    """
    Temporary lockout + session revocation (safe, time-bounded).
    """
    info = lock_user_temporarily(int(user_id), minutes=int(minutes or 15), reason=reason or "security_containment")
    if not info:
        return {"status": "failed", "reason": "user_not_found"}
    return {"status": "executed", "locked_until": info.get("locked_until"), "reason": info.get("reason")}


def execute_security_action(
    *,
    organization_id: Optional[int],
    incident_id: int | None,
    event_id: int | None,
    actor_user_id: Optional[int],
    action_type: str,
    payload: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """
    Execute a defensive action (safe-by-default).
    Always records an action row for auditability.
    """
    action_type = (action_type or "").strip()[:80]
    payload = payload or {}

    db = SessionLocal()
    row = None
    try:
        row = SecurityResponseAction(
            organization_id=organization_id,
            incident_id=incident_id,
            event_id=event_id,
            user_id=actor_user_id,
            action_type=action_type,
            status="queued",
            reason=reason[:2000],
            payload=payload,
            created_at=_now(),
        )
        db.add(row)
        db.flush()

        result: dict[str, Any] = {"status": "skipped", "details": {}}

        if action_type in {"revoke_sessions", "force_logout"}:
            target_user_id = payload.get("target_user_id")
            if target_user_id is None:
                _mark_action(row, "failed", error="target_user_id is required")
                db.commit()
                return {"ok": False, "message": "target_user_id is required", "action_id": row.id}
            revoke_all_sessions(int(target_user_id))
            result = {"status": "executed", "details": {"target_user_id": int(target_user_id)}}
            _mark_action(row, "executed")

        elif action_type in {"temporary_account_lock", "lock_account"}:
            target_user_id = payload.get("target_user_id")
            minutes = int(payload.get("minutes") or 15)
            if target_user_id is None:
                _mark_action(row, "failed", error="target_user_id is required")
                db.commit()
                return {"ok": False, "message": "target_user_id is required", "action_id": row.id}
            info = lock_account(int(target_user_id), minutes=minutes, reason=reason)
            result = {"status": info.get("status"), "details": {"target_user_id": int(target_user_id), "minutes": minutes, **info}}
            _mark_action(row, "executed")

        elif action_type in {"unlock_account"}:
            target_user_id = payload.get("target_user_id")
            if target_user_id is None:
                _mark_action(row, "failed", error="target_user_id is required")
                db.commit()
                return {"ok": False, "message": "target_user_id is required", "action_id": row.id}
            ok_unlock = unlock_user(int(target_user_id))
            result = {"status": "executed" if ok_unlock else "failed", "details": {"target_user_id": int(target_user_id)}}
            _mark_action(row, "executed" if ok_unlock else "failed")

        elif action_type in {"ip_blacklist_control", "throttle_user_control", "freeze_privileged_actions_control", "flag_interview_session_control"}:
            # Adapter-backed controls may be enforced at the gateway/IdP/SIEM layer; record auditable intent here.
            result = {"status": "recorded", "details": {"ip": payload.get("ip")}}
            _mark_action(row, "executed")

        elif action_type in {"investigate"}:
            result = {"status": "recorded", "details": payload}
            _mark_action(row, "executed")

        else:
            _mark_action(row, "failed", error="unsupported_action_type")
            db.commit()
            return {"ok": False, "message": "Unsupported action type", "action_id": row.id}

        # Update incident containment progress if provided.
        if incident_id is not None:
            incident = db.query(SecurityIncident).filter(SecurityIncident.id == int(incident_id)).first()
            if incident and (organization_id is None or incident.organization_id in {organization_id, None}):
                containment = dict(incident.containment or {})
                actions = containment.get("actions") if isinstance(containment.get("actions"), list) else []
                actions.append(
                    {
                        "ts": _now().isoformat(),
                        "action_id": row.id,
                        "type": action_type,
                        "status": row.status,
                        "actor_user_id": actor_user_id,
                        "payload": payload,
                    }
                )
                containment["actions"] = actions[-60:]
                if incident.status == "open" and row.status == "executed":
                    containment["status"] = "in_progress"
                incident.containment = containment
                incident.updated_at = _now()

        db.commit()
        return {"ok": True, "action_id": row.id, "result": result}
    finally:
        db.close()


def list_incident_actions(organization_id: Optional[int], incident_id: int, limit: int = 80) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityResponseAction).filter(SecurityResponseAction.incident_id == int(incident_id))
        if organization_id is not None:
            q = q.filter(or_(SecurityResponseAction.organization_id == organization_id, SecurityResponseAction.organization_id.is_(None)))
        rows = q.order_by(SecurityResponseAction.id.desc()).limit(limit).all()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row.id,
                    "organization_id": row.organization_id,
                    "incident_id": row.incident_id,
                    "event_id": row.event_id,
                    "actor_user_id": row.user_id,
                    "action_type": row.action_type,
                    "status": row.status,
                    "reason": row.reason or "",
                    "payload": row.payload or {},
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "executed_at": row.executed_at.isoformat() if row.executed_at else None,
                }
            )
        return out
    finally:
        db.close()
