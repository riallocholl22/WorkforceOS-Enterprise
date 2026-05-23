from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import AuditLog, Membership, Notification, Organization, Subscription, WorkflowRun
from backend.models.job import Job
from backend.models.report import Report


DEFAULT_LIMITS = {
    "free": {"candidate_uploads": 25, "interviews_per_month": 10, "recruiter_seats": 2, "ai_calls": 200},
    "pro": {"candidate_uploads": 500, "interviews_per_month": 200, "recruiter_seats": 15, "ai_calls": 5000},
    "enterprise": {"candidate_uploads": 5000, "interviews_per_month": 5000, "recruiter_seats": 250, "ai_calls": 50000},
}


def create_notification(user_id: int, kind: str, subject: str, message: str, organization_id: Optional[int] = None, metadata: Optional[dict] = None) -> dict:
    db = SessionLocal()
    try:
        notification = Notification(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            subject=subject,
            message=message,
            status="queued",
            metadata_json=metadata or {},
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)
        return serialize_notification(notification)
    finally:
        db.close()


def serialize_notification(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "organization_id": notification.organization_id,
        "user_id": notification.user_id,
        "kind": notification.kind,
        "subject": notification.subject,
        "message": notification.message,
        "status": notification.status,
        "metadata": notification.metadata_json or {},
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


def list_notifications(user_id: int, organization_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(Notification).filter(Notification.user_id == user_id)
        if organization_id is not None:
            query = query.filter(Notification.organization_id == organization_id)
        rows = query.order_by(Notification.created_at.desc()).limit(limit).all()
        return [serialize_notification(row) for row in rows]
    finally:
        db.close()


def log_audit_event(action: str, entity_type: str, entity_id: str, organization_id: Optional[int] = None, user_id: Optional[int] = None, details: Optional[dict] = None):
    db = SessionLocal()
    try:
        db.add(
            AuditLog(
                organization_id=organization_id,
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id),
                details=details or {},
            )
        )
        db.commit()
    finally:
        db.close()


def list_audit_logs(organization_id: Optional[int], limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(AuditLog)
        if organization_id is not None:
            query = query.filter(AuditLog.organization_id == organization_id)
        rows = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [
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
            for row in rows
        ]
    finally:
        db.close()


def ensure_subscription(organization_id: int, plan: str = "free") -> dict:
    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            subscription = Subscription(
                organization_id=organization_id,
                plan=plan,
                status="active",
                usage_json={"ai_calls": 0, "candidate_uploads": 0, "interviews_this_month": 0},
                limits_json=DEFAULT_LIMITS.get(plan, DEFAULT_LIMITS["free"]),
            )
            db.add(subscription)
            db.commit()
            db.refresh(subscription)
        return serialize_subscription(subscription)
    finally:
        db.close()


def serialize_subscription(subscription: Subscription) -> dict:
    return {
        "organization_id": subscription.organization_id,
        "plan": subscription.plan,
        "status": subscription.status,
        "usage": subscription.usage_json or {},
        "limits": subscription.limits_json or {},
        "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None,
    }


def increment_usage(organization_id: Optional[int], metric: str, amount: int = 1):
    if organization_id is None:
        return
    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            subscription = Subscription(
                organization_id=organization_id,
                plan="free",
                status="active",
                usage_json={},
                limits_json=DEFAULT_LIMITS["free"],
            )
            db.add(subscription)
            db.flush()
        usage = dict(subscription.usage_json or {})
        usage[metric] = int(usage.get(metric, 0)) + amount
        subscription.usage_json = usage
        db.commit()
    finally:
        db.close()


def get_workspace_overview(organization_id: Optional[int]) -> dict:
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == organization_id).first() if organization_id else None
        candidate_query = db.query(Candidate)
        job_query = db.query(Job)
        report_query = db.query(Report)
        member_query = db.query(Membership)
        workflow_query = db.query(WorkflowRun)
        if organization_id is not None:
            candidate_query = candidate_query.filter(Candidate.organization_id == organization_id)
            job_query = job_query.filter(Job.organization_id == organization_id)
            report_query = report_query.filter(Report.organization_id == organization_id) if hasattr(Report, "organization_id") else report_query
            member_query = member_query.filter(Membership.organization_id == organization_id, Membership.is_active == True)  # noqa: E712
            workflow_query = workflow_query.filter(WorkflowRun.organization_id == organization_id)

        candidates = candidate_query.all()
        jobs = job_query.all()
        workflows = workflow_query.all()
        shortlist_rows = (
            db.query(ShortlistEntry)
            .filter(ShortlistEntry.organization_id == organization_id)
            .order_by(ShortlistEntry.created_at.desc())
            .limit(2000)
            .all()
            if organization_id is not None
            else []
        )

        skill_counter = Counter()
        for candidate in candidates:
            skill_counter.update(candidate.skills or [])

        recent_window = datetime.utcnow() - timedelta(days=30)
        recent_candidates = [candidate for candidate in candidates if candidate.created_at and candidate.created_at.replace(tzinfo=None) >= recent_window]

        decisions = Counter(workflow.decision for workflow in workflows)
        conversion = 0.0
        if workflows:
            conversion = round((decisions.get("hire", 0) / len(workflows)) * 100, 2)

        # Activity-derived operational analytics (enterprise feel, no external AI required).
        audit_window = datetime.utcnow() - timedelta(days=30)
        audit_query = db.query(AuditLog)
        if organization_id is not None:
            audit_query = audit_query.filter(AuditLog.organization_id == organization_id)
        audit_rows = audit_query.filter(AuditLog.created_at >= audit_window).order_by(AuditLog.created_at.desc()).limit(1200).all()

        action_counts = Counter((row.action or "").split(":", 1)[0] for row in audit_rows)
        by_user = Counter(int(row.user_id or 0) for row in audit_rows if row.user_id)

        shortlist_total = len(shortlist_rows)
        shortlisted = len([r for r in shortlist_rows if (r.status or "") not in {"rejected"}])
        approved = len([r for r in shortlist_rows if (r.status or "") == "approved"])
        rejected = len([r for r in shortlist_rows if (r.status or "") == "rejected"])
        shortlist_conversion = round((approved / max(1, shortlisted)) * 100.0, 2) if shortlisted else 0.0

        # Bottleneck heuristics.
        bottlenecks: list[dict] = []
        resume_uploads_30d = int(action_counts.get("resume.uploaded", 0))
        match_runs_30d = int(action_counts.get("match.completed", 0))
        shortlist_events_30d = sum(int(action_counts.get(k, 0)) for k in ("shortlist.add", "shortlist.auto", "shortlist.update"))
        approvals_30d = int(action_counts.get("shortlist.approved", 0)) + int(action_counts.get("shortlist.approve", 0))

        if resume_uploads_30d and not match_runs_30d:
            bottlenecks.append({"name": "ranking_not_run", "severity": "high", "message": "Resumes were uploaded, but matching hasn't been run recently. Run a match to generate shortlist-ready intelligence."})
        if match_runs_30d and not shortlist_events_30d:
            bottlenecks.append({"name": "shortlist_stalled", "severity": "medium", "message": "Matches are being generated, but shortlist actions are low. Consider enabling auto-shortlist rules for strong matches."})
        if shortlisted and not approved and rejected:
            bottlenecks.append({"name": "approval_friction", "severity": "medium", "message": "Many candidates are being rejected with few approvals. Re-check must-haves and calibrate the threshold."})

        if not bottlenecks and not resume_uploads_30d and not candidates:
            bottlenecks.append({"name": "empty_pipeline", "severity": "low", "message": "No recent pipeline activity yet. Start by creating a job and uploading resumes."})

        # Recruiter-facing analytics narrative.
        narrative_bits: list[str] = []
        if resume_uploads_30d:
            narrative_bits.append(f"{resume_uploads_30d} resume uploads in the last 30 days.")
        if match_runs_30d:
            narrative_bits.append(f"{match_runs_30d} match runs generated.")
        if shortlist_total:
            narrative_bits.append(f"{shortlist_total} shortlist entries tracked (approval rate {shortlist_conversion}%).")
        if not narrative_bits:
            narrative_bits.append("No recent activity signals yet. Create a job, upload resumes, and run matching to activate intelligence.")

        ai_summary = " ".join(narrative_bits)[:700]

        return {
            "organization": {
                "id": org.id if org else organization_id,
                "name": org.name if org else "Personal Workspace",
                "slug": org.slug if org else "personal-workspace",
                "plan": org.plan if org else "free",
            },
            "totals": {
                "candidates": len(candidates),
                "jobs": len(jobs),
                "reports": report_query.count(),
                "members": member_query.count(),
                "recent_uploads": len(recent_candidates),
            },
            "funnel": {
                "applied": len(candidates),
                "screened": decisions.get("consider", 0) + decisions.get("hire", 0) + decisions.get("reject", 0),
                "shortlisted": decisions.get("consider", 0) + decisions.get("hire", 0),
                "hired": decisions.get("hire", 0),
            },
            "hiring_success_rate": conversion,
            "time_to_hire_prediction_days": 14 if decisions.get("hire", 0) else 21,
            "skill_demand_trends": [
                {"skill": skill, "count": count}
                for skill, count in skill_counter.most_common(8)
            ],
            "workflow_decisions": dict(decisions),
            "workforce_analytics": {
                "window_days": 30,
                "activity": {
                    "resume_uploads": resume_uploads_30d,
                    "match_runs": match_runs_30d,
                    "shortlist_events": shortlist_events_30d,
                    "shortlist_total": shortlist_total,
                    "shortlisted": shortlisted,
                    "approved": approved,
                    "rejected": rejected,
                },
                "shortlist_conversion_rate": shortlist_conversion,
                "recruiter_productivity": [
                    {"user_id": user_id, "events": count}
                    for user_id, count in by_user.most_common(8)
                    if user_id
                ],
                "bottlenecks": bottlenecks[:6],
                "ai_explanation": ai_summary,
            },
        }
    finally:
        db.close()
