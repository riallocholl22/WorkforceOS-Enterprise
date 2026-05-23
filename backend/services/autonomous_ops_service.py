from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.models.enterprise import AIMemory, AuditLog, Notification
from backend.services.enterprise_service import create_notification


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def autonomous_insights(organization_id: Optional[int]) -> dict[str, Any]:
    """
    Proactive recruiter intelligence derived from recent activity.

    Output is designed for:
    - dashboard "AI priorities"
    - ops stream recommendations
    - executive brief highlights
    """
    if organization_id is None:
        return {"status": "skipped", "reason": "no_org", "alerts": [], "priorities": []}

    since_7d = datetime.utcnow() - timedelta(days=7)
    since_30d = datetime.utcnow() - timedelta(days=30)

    db = SessionLocal()
    try:
        audit_q = db.query(AuditLog).filter(AuditLog.organization_id == organization_id)
        audits_7d = audit_q.filter(AuditLog.created_at >= since_7d).order_by(AuditLog.created_at.desc()).limit(1200).all()

        # Memory snapshots for match trend analysis.
        mem_q = db.query(AIMemory).filter(or_(AIMemory.organization_id == organization_id, AIMemory.organization_id.is_(None)))
        snapshots = (
            mem_q.filter(AIMemory.kind == "match.snapshot", AIMemory.created_at >= since_30d)
            .order_by(AIMemory.id.desc())
            .limit(12)
            .all()
        )

        def _count(prefix: str) -> int:
            return sum(1 for row in audits_7d if str(row.action or "").startswith(prefix))

        uploads = _count("resume.uploaded")
        matches = _count("match.completed")
        shortlist_events = _count("shortlist.") + _count("copilot.shortlist.")
        scheduling = _count("scheduling.")
        interviews = _count("interview.")

        alerts: list[dict[str, Any]] = []
        priorities: list[dict[str, Any]] = []

        # Pipeline stall detection.
        if uploads >= 3 and matches == 0:
            alerts.append(
                {
                    "code": "pipeline_stall_matching",
                    "severity": "high",
                    "title": "Matching hasn't been run recently",
                    "body": "Resumes are being uploaded, but ranking hasn't been generated in the last 7 days. Run a match to surface shortlist-ready candidates.",
                    "confidence": 0.9,
                    "why": f"{uploads} resume uploads in 7d with 0 match runs.",
                }
            )
            priorities.append(
                {
                    "title": "Run matching now",
                    "body": "Generate match results for your latest role to unblock the pipeline.",
                    "priority": "high",
                    "confidence": 0.85,
                    "why": f"Uploads are flowing ({uploads} in 7d) but matching hasn't been run yet.",
                }
            )

        if matches >= 1 and shortlist_events == 0:
            alerts.append(
                {
                    "code": "pipeline_stall_shortlist",
                    "severity": "medium",
                    "title": "Shortlisting is stalled",
                    "body": "Matches are being generated, but there are no shortlist actions recently. Consider auto-shortlisting strong matches and adding quick recruiter notes.",
                    "confidence": 0.78,
                    "why": f"{matches} match runs in 7d with 0 shortlist actions.",
                }
            )
            priorities.append(
                {
                    "title": "Auto-shortlist strong matches",
                    "body": "Enable auto-shortlist rules (>= 75%) and review the top 5.",
                    "priority": "medium",
                    "confidence": 0.74,
                    "why": "Match results exist, but no shortlist momentum yet.",
                }
            )

        if shortlist_events and scheduling == 0 and interviews == 0:
            alerts.append(
                {
                    "code": "pipeline_stall_scheduling",
                    "severity": "medium",
                    "title": "Interview scheduling delays detected",
                    "body": "Shortlist activity exists, but there are no scheduling/interview events recently. Consider scheduling screens within 48 hours to keep momentum.",
                    "confidence": 0.72,
                    "why": f"{shortlist_events} shortlist events with 0 scheduling/interview events in 7d.",
                }
            )
            priorities.append(
                {
                    "title": "Schedule screens within 48 hours",
                    "body": "Move top candidates into a structured technical screen.",
                    "priority": "medium",
                    "confidence": 0.7,
                    "why": "Shortlist activity exists, but interview motion hasn't started yet.",
                }
            )

        # Candidate pool quality trend (from last two match snapshots).
        if len(snapshots) >= 2:
            def _avg_top(row: AIMemory) -> float:
                payload = row.payload if isinstance(row.payload, dict) else {}
                top = payload.get("top") if isinstance(payload.get("top"), list) else []
                scores = []
                for item in top[:5]:
                    try:
                        scores.append(float(item.get("match_score") or 0))
                    except Exception:
                        pass
                return sum(scores) / max(1, len(scores))

            avg_now = _avg_top(snapshots[0])
            avg_prev = _avg_top(snapshots[1])
            delta = avg_now - avg_prev
            if avg_prev >= 40 and delta <= -10:
                alerts.append(
                    {
                        "code": "candidate_quality_decreasing",
                        "severity": "medium",
                        "title": "Candidate quality is decreasing for this role",
                        "body": "Recent match snapshots show a noticeable drop in top scores. Consider broadening must-haves or adjusting sourcing channels.",
                        "details": {"avg_now": round(avg_now, 1), "avg_prev": round(avg_prev, 1), "delta": round(delta, 1)},
                        "confidence": 0.68,
                        "why": f"Top-5 average dropped from {round(avg_prev, 1)} to {round(avg_now, 1)}.",
                    }
                )
                priorities.append(
                    {
                        "title": "Broaden requirements slightly",
                        "body": "Convert 1-2 'must-haves' into 'nice-to-haves' and re-run matching.",
                        "priority": "medium",
                        "confidence": 0.66,
                        "why": "Recent match snapshots indicate a quality dip.",
                    }
                )

        # Always provide a minimal next step if quiet.
        if not priorities:
            priorities.append(
                {
                    "title": "Next best action",
                    "body": "Run matching, shortlist top candidates, then generate a Candidate 360 before scheduling interviews.",
                    "priority": "low",
                    "confidence": 0.6,
                    "why": "No urgent risks detected in the last 7 days.",
                }
            )

        for item in priorities:
            item.setdefault("reasoning", item.get("why") or item.get("body") or "WorkforceOS is weighing recent hiring activity and workflow momentum.")
            item.setdefault("operational_impact", "Improves recruiter focus, pipeline velocity, and executive forecast quality.")
            item.setdefault("suggested_action", item.get("body") or "Review the current workflow and take the next recruiter action.")
            item.setdefault("uncertainty", "Recommendation confidence depends on how much recent workflow evidence exists.")

        for item in alerts:
            item.setdefault("reasoning", item.get("why") or item.get("body") or "WorkforceOS detected an operating signal that may affect hiring velocity.")
            item.setdefault("operational_impact", "May affect time-to-screen, shortlist throughput, or staffing-plan risk.")
            item.setdefault("suggested_action", item.get("body") or "Review the alert and choose a recruiter-safe next action.")
            item.setdefault("uncertainty", "Alerts are based on platform telemetry and should be validated against recruiter context.")

        return {
            "status": "ok",
            "generated_at": datetime.utcnow().isoformat(),
            "window_days": 7,
            "signals": {
                "resume_uploads": uploads,
                "match_runs": matches,
                "shortlist_events": shortlist_events,
                "scheduling_events": scheduling,
                "interview_events": interviews,
            },
            "alerts": alerts[:6],
            "priorities": priorities[:6],
        }
    finally:
        db.close()


def maybe_emit_autonomous_notifications(
    *,
    organization_id: Optional[int],
    user_id: Optional[int],
    max_per_day: int = 2,
) -> None:
    """
    Emit a small number of proactive notifications (deduped) so the platform feels alive.
    """
    if organization_id is None or not user_id:
        return

    insights = autonomous_insights(organization_id)
    if insights.get("status") != "ok":
        return

    alerts = insights.get("alerts") if isinstance(insights.get("alerts"), list) else []
    if not alerts:
        return

    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=1)
        sent = (
            db.query(Notification)
            .filter(Notification.organization_id == organization_id, Notification.user_id == int(user_id), Notification.created_at >= since)
            .count()
        )
        if sent >= int(max_per_day):
            return

        for alert in alerts[: max(0, int(max_per_day) - sent)]:
            code = str(alert.get("code") or "alert")
            dedupe_key = f"autonomous:{code}"

            # Dedupe: do not repeat same alert too frequently.
            recent_same = (
                db.query(Notification)
                .filter(Notification.organization_id == organization_id, Notification.user_id == int(user_id), Notification.subject == dedupe_key)
                .order_by(Notification.created_at.desc())
                .first()
            )
            if recent_same and recent_same.created_at and recent_same.created_at >= datetime.utcnow() - timedelta(hours=12):
                continue

            create_notification(
                user_id=int(user_id),
                organization_id=organization_id,
                kind="ai_ops",
                subject=dedupe_key,
                message=str(alert.get("body") or "AI ops alert"),
                metadata={"alert": alert, "insights": insights.get("signals") or {}},
            )
    finally:
        db.close()
