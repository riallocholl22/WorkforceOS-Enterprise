import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from backend.db.database import SessionLocal
from backend.models.enterprise import (
    AuditLog,
    Notification,
    SecurityEvent,
    SecurityIncident,
    SecurityResponseAction,
)
from backend.services.auth_service import get_user_context_from_token
from backend.services.enterprise_service import list_audit_logs, list_notifications


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enterprise", tags=["Ops"])


def _ws_urlsafe(obj):
    # Ensure payload stays JSON-serializable and safe (no ORM instances).
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _friendly_op_summary(event: dict) -> dict:
    """
    Convert a raw audit/notification record into a recruiter-friendly activity feed item.
    """
    kind = event.get("kind") or "event"
    action = event.get("action") or ""
    subject = event.get("subject") or ""
    message = event.get("message") or ""
    details = event.get("details") or event.get("metadata") or {}
    created_at = event.get("created_at")

    title = subject or action or kind
    body = message

    if action.startswith("shortlist."):
        candidate_id = event.get("entity_id") or details.get("candidate_id") or "candidate"
        title = "Candidate shortlist updated"
        body = f"{candidate_id} was {action.split('.', 1)[1].replace('_', ' ')}."

    if action.startswith("billing."):
        title = "Billing event"
        body = action.replace(".", " ").replace("_", " ").title()

    if action.startswith("auth."):
        title = "Authentication event"
        body = action.replace(".", " ").replace("_", " ").title()

    if action.startswith("resume.") or action.startswith("pipeline.") or action.startswith("upload."):
        title = "Resume processing"
        body = action.replace(".", " ").replace("_", " ").title()

    if action.startswith("match.") or action.startswith("ranking."):
        title = "Candidate ranking"
        body = action.replace(".", " ").replace("_", " ").title()

    # Emit a stable, UI-friendly shape.
    return {
        "id": event.get("id"),
        "type": "notification" if "kind" in event else "audit",
        "title": title[:120],
        "body": (body or "").strip()[:600],
        "severity": details.get("severity") or details.get("threat_level") or "info",
        "created_at": created_at,
        "details": details if isinstance(details, dict) else {},
    }


def _build_recommendations(audit_events: list[dict], notifications: list[dict]) -> list[dict]:
    """
    Lightweight, deterministic "AI ops" recommendations from recent activity.
    (No external model required; keeps the product feeling alive.)
    """
    recs: list[dict] = []

    # Heuristics for resume extraction reliability.
    extraction_failures = [
        e for e in notifications
        if (e.get("kind") in {"error", "warning"} and "resume" in (e.get("subject") or "").lower())
        or "resume_extraction_failed" in json.dumps(e.get("metadata") or {})
    ]
    if extraction_failures:
        recs.append(
            {
                "title": "Resume extraction reliability",
                "body": "Some resumes were processed with low confidence. Consider requesting a text-based PDF/DOCX, or enabling OCR server-side for scanned documents.",
                "priority": "high" if len(extraction_failures) >= 2 else "medium",
                "confidence": 0.78 if len(extraction_failures) >= 2 else 0.66,
                "why": f"{len(extraction_failures)} resume-related warnings/errors were detected recently.",
                "impact": "Candidate ranking may underrepresent strong applicants if extraction quality is weak.",
                "suggested_action": "Review affected resumes, request cleaner files, and rerun matching before final shortlist decisions.",
                "uncertainty": "The signal is based on recent processing metadata, not a manual resume audit.",
            }
        )

    shortlist_changes = [e for e in audit_events if str(e.get("action") or "").startswith("shortlist.")]
    if shortlist_changes:
        recs.append(
            {
                "title": "Shortlist momentum",
                "body": "Shortlist activity is trending. Consider scheduling technical screens for the top two matches and adding recruiter notes while context is fresh.",
                "priority": "medium",
                "confidence": 0.62,
                "why": f"{len(shortlist_changes)} shortlist events detected in recent activity.",
                "impact": "Fast shortlist movement can improve hiring velocity if interview scheduling keeps pace.",
                "suggested_action": "Approve priority candidates, assign interview owners, and capture evidence-backed recruiter notes.",
                "uncertainty": "Momentum does not guarantee fit; validate role-critical gaps before advancing candidates.",
            }
        )

    suspicious = [e for e in audit_events if str(e.get("action") or "").startswith("auth.failed")]
    if len(suspicious) >= 3:
        recs.append(
            {
                "title": "Security posture",
                "body": "Multiple failed logins were detected recently. Consider enabling stricter rate limits and reviewing team access for this workspace.",
                "priority": "high",
                "confidence": 0.74,
                "why": f"{len(suspicious)} failed login events detected recently.",
                "impact": "Repeated authentication failures can indicate account takeover attempts or user friction.",
                "suggested_action": "Review audit logs, confirm affected users, and tighten rate limits if the pattern continues.",
                "uncertainty": "Failed logins can also come from stale passwords or SSO transition issues.",
            }
        )

    if not recs:
        recs.append(
            {
                "title": "Next best action",
                "body": "Run a match on your latest job description, then let the system auto-shortlist candidates above 75% to kick off screening.",
                "priority": "low",
                "confidence": 0.55,
                "why": "No urgent operational risks detected in recent activity.",
                "impact": "The workspace is stable, but hiring intelligence improves once fresh candidate and role signals arrive.",
                "suggested_action": "Run matching on the latest role and convert qualified candidates into a reviewable shortlist.",
                "uncertainty": "Low activity limits predictive confidence until more workflow evidence accumulates.",
            }
        )

    return recs[:4]


def _ops_coordination_heartbeat(audit_events: list[dict], notifications: list[dict]) -> dict:
    activity = len(audit_events) + len(notifications)
    shortlist = len([e for e in audit_events if str(e.get("action") or "").startswith("shortlist.")])
    ranking = len([e for e in audit_events if str(e.get("action") or "").startswith(("match.", "ranking."))])
    auth_failed = len([e for e in audit_events if str(e.get("action") or "").startswith("auth.failed")])
    if auth_failed >= 3:
        return {
            "title": "Security signal is influencing operations",
            "body": f"{auth_failed} failed authentication events are being watched while hiring workflows continue.",
            "severity": "warning",
        }
    if shortlist:
        return {
            "title": "Recruiter workflow is advancing",
            "body": f"{shortlist} shortlist movements are synchronized with executive risk and scheduling intelligence.",
            "severity": "info",
        }
    if ranking:
        return {
            "title": "Ranking intelligence is current",
            "body": f"{ranking} ranking signals are feeding candidate prioritization and AI recommendations.",
            "severity": "info",
        }
    return {
        "title": "WorkforceOS continuity check",
        "body": f"Authenticated realtime stream is healthy across {activity} recent operating signals.",
        "severity": "info",
    }


def _security_coordination_heartbeat(events: list[dict], incidents: list[dict], actions: list[dict]) -> dict:
    open_incidents = len([item for item in incidents if str(item.get("status") or "").lower() not in {"resolved", "closed"}])
    high_risk = len([item for item in events if str(item.get("threat_level") or "").lower() in {"high", "critical"}])
    pending_actions = len([item for item in actions if str(item.get("status") or "").lower() in {"pending", "queued"}])
    if high_risk:
        return {
            "title": "Security AI is watching elevated risk",
            "body": f"{high_risk} elevated threat signals are correlated with session, token, and candidate-data controls.",
            "severity": "warning",
        }
    if open_incidents:
        return {
            "title": "Incident workflow remains active",
            "body": f"{open_incidents} incident workflows are synchronized with containment and audit guidance.",
            "severity": "info",
        }
    return {
        "title": "Security control plane healthy",
        "body": f"Realtime security stream is healthy with {pending_actions} pending response actions.",
        "severity": "info",
    }


async def _ws_send(websocket: WebSocket, event: str, **payload) -> None:
    await websocket.send_text(json.dumps({"event": event, **payload}, default=_ws_urlsafe))


def _friendly_security_item(kind: str, row: dict) -> dict:
    """
    Convert security ORM rows into a stable, UI-friendly live feed item.
    """
    kind = kind or "security"
    created_at = row.get("created_at") or row.get("updated_at")
    severity = row.get("severity") or row.get("threat_level") or "info"

    title = "Security update"
    body = ""

    if kind == "event":
        title = f"Threat signal: {row.get('event_type') or 'security.event'}"
        body = (row.get("recommended_action") or "monitor").replace("_", " ")
    elif kind == "incident":
        title = row.get("title") or "Security incident"
        body = row.get("summary") or ""
    elif kind == "action":
        title = f"Response action: {row.get('action_type') or 'action'}"
        body = row.get("status") or ""

    return {
        "id": row.get("id") or row.get("incident_id"),
        "type": f"security_{kind}",
        "title": str(title)[:120],
        "body": str(body).strip()[:600],
        "severity": str(severity or "info"),
        "created_at": created_at,
        "details": row,
    }


@router.websocket("/ops/stream")
async def ops_stream(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    context = get_user_context_from_token(token)
    if not context:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = context.get("organization_id")
    user = context.get("user")
    user_id = getattr(user, "id", None)

    await websocket.accept()
    await _ws_send(websocket, "ready")

    # Snapshot (seed the feed immediately).
    audit_snapshot = list_audit_logs(org_id, limit=25)
    notif_snapshot = list_notifications(int(user_id), org_id, limit=25) if user_id else []
    combined = sorted(
        [*audit_snapshot, *notif_snapshot],
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    await _ws_send(
        websocket,
        "snapshot",
        audit=audit_snapshot,
        notifications=notif_snapshot,
        feed=[_friendly_op_summary(item) for item in combined[:30]],
        recommendations=_build_recommendations(audit_snapshot, notif_snapshot),
    )

    last_audit_id = max([int(item.get("id") or 0) for item in audit_snapshot] or [0])
    last_notif_id = max([int(item.get("id") or 0) for item in notif_snapshot] or [0])
    recent_audit = audit_snapshot
    recent_notifs = notif_snapshot

    poll_interval = 1.6
    last_heartbeat = datetime.utcnow()
    try:
        while True:
            # Allow clients to send "ping" / "ack" messages without blocking the poll loop.
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=poll_interval)
                if raw.strip() == "ping":
                    await _ws_send(websocket, "pong")
            except asyncio.TimeoutError:
                pass

            db = SessionLocal()
            try:
                # Audit events for org
                audit_query = db.query(AuditLog)
                if org_id is not None:
                    audit_query = audit_query.filter(AuditLog.organization_id == org_id)
                audit_rows = (
                    audit_query.filter(AuditLog.id > last_audit_id)
                    .order_by(AuditLog.id.asc())
                    .limit(30)
                    .all()
                )
                new_audits = [
                    {
                        "id": row.id,
                        "action": row.action,
                        "entity_type": row.entity_type,
                        "entity_id": row.entity_id,
                        "details": row.details or {},
                        "user_id": row.user_id,
                        "organization_id": row.organization_id,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in audit_rows
                ]

                # Notifications scoped to user + org
                notif_query = db.query(Notification).filter(Notification.user_id == int(user_id)) if user_id else None
                if notif_query is not None and org_id is not None:
                    notif_query = notif_query.filter(Notification.organization_id == org_id)
                notif_rows = (
                    notif_query.filter(Notification.id > last_notif_id).order_by(Notification.id.asc()).limit(30).all()
                    if notif_query is not None
                    else []
                )
                new_notifs = [
                    {
                        "id": row.id,
                        "organization_id": row.organization_id,
                        "user_id": row.user_id,
                        "kind": row.kind,
                        "subject": row.subject,
                        "message": row.message,
                        "status": row.status,
                        "metadata": row.metadata_json or {},
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in notif_rows
                ]

            finally:
                db.close()

            if new_audits:
                last_audit_id = max(last_audit_id, max(int(item.get("id") or 0) for item in new_audits))
                for item in new_audits[-15:]:
                    await _ws_send(websocket, "ops_event", item=_friendly_op_summary(item), raw=item)

            if new_notifs:
                last_notif_id = max(last_notif_id, max(int(item.get("id") or 0) for item in new_notifs))
                for item in new_notifs[-15:]:
                    await _ws_send(websocket, "ops_event", item=_friendly_op_summary(item), raw=item)

            if new_audits or new_notifs:
                # Refresh recommendations when something changes.
                recent_audit = list_audit_logs(org_id, limit=25)
                recent_notifs = list_notifications(int(user_id), org_id, limit=25) if user_id else []
                await _ws_send(
                    websocket,
                    "recommendations",
                    recommendations=_build_recommendations(recent_audit, recent_notifs),
                )
                last_heartbeat = datetime.utcnow()
            elif (datetime.utcnow() - last_heartbeat).total_seconds() >= 20:
                await _ws_send(
                    websocket,
                    "heartbeat",
                    status="ok",
                    generated_at=datetime.utcnow().isoformat(),
                    telemetry={
                        "stream": "ops",
                        "mode": "authenticated_websocket",
                        "poll_interval_seconds": poll_interval,
                        "last_audit_id": last_audit_id,
                        "last_notification_id": last_notif_id,
                        "continuity": "healthy",
                    },
                    coordination=_ops_coordination_heartbeat(recent_audit, recent_notifs),
                )
                last_heartbeat = datetime.utcnow()

    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("ops_stream_failed")
        try:
            await _ws_send(websocket, "error", message="Live ops stream failed gracefully. Reconnecting should recover.")
        except Exception:
            return


@router.websocket("/security/stream")
async def security_stream(websocket: WebSocket):
    """
    Live security monitoring stream for the Security Center.
    Sends: snapshot + incremental updates for events/incidents/actions.
    """
    token = websocket.query_params.get("token", "")
    context = get_user_context_from_token(token)
    if not context:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = context.get("organization_id")
    user = context.get("user")
    user_id = getattr(user, "id", None)

    await websocket.accept()
    await _ws_send(websocket, "ready")

    # Snapshot
    db = SessionLocal()
    try:
        ev_q = db.query(SecurityEvent)
        inc_q = db.query(SecurityIncident)
        act_q = db.query(SecurityResponseAction)
        if org_id is not None:
            ev_q = ev_q.filter(SecurityEvent.organization_id == org_id)
            inc_q = inc_q.filter(SecurityIncident.organization_id == org_id)
            act_q = act_q.filter(SecurityResponseAction.organization_id == org_id)

        ev_rows = ev_q.order_by(SecurityEvent.id.desc()).limit(30).all()
        inc_rows = inc_q.order_by(SecurityIncident.updated_at.desc()).limit(25).all()
        act_rows = act_q.order_by(SecurityResponseAction.id.desc()).limit(25).all()

        events = [
            {
                "id": r.id,
                "organization_id": r.organization_id,
                "user_id": r.user_id,
                "source_ip": r.source_ip,
                "event_type": r.event_type,
                "risk_score": r.risk_score,
                "threat_level": r.threat_level,
                "detected_threats": r.detected_threats or [],
                "recommended_action": r.recommended_action,
                "details": r.details or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in ev_rows
        ]
        incidents = [
            {
                "incident_id": r.id,
                "organization_id": r.organization_id,
                "status": r.status,
                "severity": r.severity,
                "confidence": r.confidence,
                "incident_type": r.incident_type,
                "title": r.title,
                "summary": r.summary,
                "source_ip": r.source_ip,
                "user_id": r.user_id,
                "affected_modules": r.affected_modules or [],
                "response_plan": r.response_plan or {},
                "containment": r.containment or {},
                "timeline": (r.timeline or [])[-50:],
                "related_event_ids": r.related_event_ids or [],
                "owner_user_id": r.owner_user_id,
                "notes": r.notes or "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in inc_rows
        ]
        actions = [
            {
                "id": r.id,
                "organization_id": r.organization_id,
                "incident_id": r.incident_id,
                "event_id": r.event_id,
                "actor_user_id": r.user_id,
                "action_type": r.action_type,
                "status": r.status,
                "reason": r.reason or "",
                "payload": r.payload or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in act_rows
        ]

        combined = []
        combined.extend([_friendly_security_item("event", e) for e in events])
        combined.extend([_friendly_security_item("incident", i) for i in incidents])
        combined.extend([_friendly_security_item("action", a) for a in actions])
        combined = sorted(combined, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:50]

        await _ws_send(websocket, "snapshot", events=events, incidents=incidents, actions=actions, feed=combined)

        last_event_id = max([int(item.get("id") or 0) for item in events] or [0])
        last_action_id = max([int(item.get("id") or 0) for item in actions] or [0])
        latest_events = events
        latest_incidents = incidents
        latest_actions = actions
        # Keep a real DateTime cursor for incident updates (updated_at changes without id changing).
        last_incident_updated_dt = max(
            [r.updated_at or r.created_at for r in inc_rows if (r.updated_at or r.created_at) is not None] or [datetime(1970, 1, 1)]
        )
    finally:
        db.close()

    poll_interval = 1.4
    last_heartbeat = datetime.utcnow()
    try:
        while True:
            # Keepalive
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=poll_interval)
                if raw.strip() == "ping":
                    await _ws_send(websocket, "pong")
            except asyncio.TimeoutError:
                pass

            db = SessionLocal()
            try:
                ev_q = db.query(SecurityEvent)
                inc_q = db.query(SecurityIncident)
                act_q = db.query(SecurityResponseAction)
                if org_id is not None:
                    ev_q = ev_q.filter(SecurityEvent.organization_id == org_id)
                    inc_q = inc_q.filter(SecurityIncident.organization_id == org_id)
                    act_q = act_q.filter(SecurityResponseAction.organization_id == org_id)

                ev_rows = (
                    ev_q.filter(SecurityEvent.id > int(last_event_id))
                    .order_by(SecurityEvent.id.asc())
                    .limit(30)
                    .all()
                )
                act_rows = (
                    act_q.filter(SecurityResponseAction.id > int(last_action_id))
                    .order_by(SecurityResponseAction.id.asc())
                    .limit(30)
                    .all()
                )

                # Incidents update in-place; use updated_at cursor.
                inc_rows = (
                    inc_q.filter(SecurityIncident.updated_at.isnot(None))
                    .filter(SecurityIncident.updated_at > last_incident_updated_dt)
                    .order_by(SecurityIncident.updated_at.asc())
                    .limit(25)
                    .all()
                )

                new_events = [
                    {
                        "id": r.id,
                        "organization_id": r.organization_id,
                        "user_id": r.user_id,
                        "source_ip": r.source_ip,
                        "event_type": r.event_type,
                        "risk_score": r.risk_score,
                        "threat_level": r.threat_level,
                        "detected_threats": r.detected_threats or [],
                        "recommended_action": r.recommended_action,
                        "details": r.details or {},
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in ev_rows
                ]
                new_actions = [
                    {
                        "id": r.id,
                        "organization_id": r.organization_id,
                        "incident_id": r.incident_id,
                        "event_id": r.event_id,
                        "actor_user_id": r.user_id,
                        "action_type": r.action_type,
                        "status": r.status,
                        "reason": r.reason or "",
                        "payload": r.payload or {},
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                    }
                    for r in act_rows
                ]
                new_incidents = [
                    {
                        "incident_id": r.id,
                        "organization_id": r.organization_id,
                        "status": r.status,
                        "severity": r.severity,
                        "confidence": r.confidence,
                        "incident_type": r.incident_type,
                        "title": r.title,
                        "summary": r.summary,
                        "source_ip": r.source_ip,
                        "user_id": r.user_id,
                        "affected_modules": r.affected_modules or [],
                        "response_plan": r.response_plan or {},
                        "containment": r.containment or {},
                        "timeline": (r.timeline or [])[-50:],
                        "related_event_ids": r.related_event_ids or [],
                        "owner_user_id": r.owner_user_id,
                        "notes": r.notes or "",
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                    }
                    for r in inc_rows
                ]

            finally:
                db.close()

            if new_events:
                last_event_id = max(last_event_id, max(int(item.get("id") or 0) for item in new_events))
                latest_events = [*new_events, *latest_events][:50]
                for item in new_events[-20:]:
                    await _ws_send(websocket, "security_event", item=item, feed_item=_friendly_security_item("event", item))

            if new_actions:
                last_action_id = max(last_action_id, max(int(item.get("id") or 0) for item in new_actions))
                latest_actions = [*new_actions, *latest_actions][:50]
                for item in new_actions[-20:]:
                    await _ws_send(websocket, "security_action", item=item, feed_item=_friendly_security_item("action", item))

            if new_incidents:
                # Advance updated_at cursor (use ORM rows to avoid parsing ISO strings).
                last_incident_updated_dt = max(
                    [last_incident_updated_dt, *[r.updated_at or r.created_at for r in inc_rows if (r.updated_at or r.created_at) is not None]]
                )
                incident_by_id = {int(item.get("incident_id") or 0): item for item in latest_incidents}
                for item in new_incidents:
                    incident_by_id[int(item.get("incident_id") or 0)] = item
                latest_incidents = list(incident_by_id.values())[:50]
                for item in new_incidents[-20:]:
                    await _ws_send(websocket, "security_incident", item=item, feed_item=_friendly_security_item("incident", item))

            if new_events or new_actions or new_incidents:
                last_heartbeat = datetime.utcnow()
            elif (datetime.utcnow() - last_heartbeat).total_seconds() >= 20:
                await _ws_send(
                    websocket,
                    "heartbeat",
                    status="ok",
                    generated_at=datetime.utcnow().isoformat(),
                    telemetry={
                        "stream": "security",
                        "mode": "authenticated_websocket",
                        "poll_interval_seconds": poll_interval,
                        "last_event_id": last_event_id,
                        "last_action_id": last_action_id,
                        "continuity": "healthy",
                    },
                    coordination=_security_coordination_heartbeat(latest_events, latest_incidents, latest_actions),
                )
                last_heartbeat = datetime.utcnow()

    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("security_stream_failed")
        try:
            await _ws_send(websocket, "error", message="Live security stream failed gracefully. Reconnecting should recover.")
        except Exception:
            return
