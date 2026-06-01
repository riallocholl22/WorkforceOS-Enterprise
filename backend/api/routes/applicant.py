from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_

from backend.api.dependencies import require_roles
from backend.api.responses import ok
from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import AuthAuditLog, InterviewSchedule, InterviewSessionRecord, Notification
from backend.models.job import Job
from backend.services.auth_service import serialize_user
from backend.services.enterprise_service import log_audit_event

router = APIRouter(prefix="/applicant", tags=["Applicant Portal"])


class ApplicantProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    headline: str | None = Field(default=None, max_length=160)
    skills: list[str] | None = None


class SavedJobRequest(BaseModel):
    job_id: int


class NotificationUpdate(BaseModel):
    status: str = Field(..., pattern="^(read|archived|queued)$")


def _candidate_for_user(db, user) -> Candidate | None:
    return (
        db.query(Candidate)
        .filter(Candidate.extracted_email == user.username)
        .order_by(Candidate.updated_at.desc().nullslast(), Candidate.created_at.desc())
        .first()
    )


def _candidate_rows_for_user(db, user) -> list[Candidate]:
    return (
        db.query(Candidate)
        .filter(Candidate.extracted_email == user.username)
        .order_by(Candidate.created_at.desc())
        .limit(25)
        .all()
    )


def _safe_skills(candidate: Candidate | None) -> list[str]:
    raw = candidate.skills if candidate else []
    if not isinstance(raw, list):
        return []
    return [str(skill).strip().lower() for skill in raw if str(skill).strip()][:24]


def _profile_completion(user, candidate: Candidate | None) -> int:
    checks = [
        bool(user.username),
        bool(getattr(user, "email_verified", False)),
        bool(getattr(user, "phone_verified", False) or getattr(user, "phone", None)),
        bool(candidate and candidate.candidate_name),
        bool(candidate and candidate.role),
        bool(_safe_skills(candidate)),
        bool(candidate and candidate.text),
        bool(candidate and candidate.experience is not None),
    ]
    return int(round((sum(1 for item in checks if item) / len(checks)) * 100))


def _resume_score(candidate: Candidate | None) -> int:
    if not candidate:
        return 0
    skills = _safe_skills(candidate)
    text_length = len(candidate.text or candidate.raw_text or "")
    extraction = candidate.extraction_json if isinstance(candidate.extraction_json, dict) else {}
    score = 35
    score += min(25, len(skills) * 3)
    score += 15 if candidate.candidate_name else 0
    score += 10 if candidate.extracted_email else 0
    score += 10 if text_length > 1200 else 5 if text_length > 400 else 0
    if extraction.get("is_scanned_pdf"):
        score -= 18
    return max(0, min(100, score))


def _job_required_skills(job: Job) -> list[str]:
    text = f"{job.title or ''} {job.description or ''}".lower()
    known = [
        "python", "fastapi", "sql", "javascript", "typescript", "react", "node",
        "aws", "azure", "docker", "kubernetes", "terraform", "data", "ml",
        "security", "sales", "marketing", "finance", "product", "design",
    ]
    return [skill for skill in known if skill in text][:10]


def _skill_match(candidate_skills: list[str], job: Job) -> int:
    required = _job_required_skills(job)
    if not required:
        return 58 if candidate_skills else 42
    matched = len(set(candidate_skills) & set(required))
    return int(round((matched / max(1, len(required))) * 100))


def _serialize_job(job: Job, candidate_skills: list[str]) -> dict[str, Any]:
    required = _job_required_skills(job)
    match = _skill_match(candidate_skills, job)
    desc = job.description or ""
    remote_mode = "remote" if "remote" in desc.lower() else "hybrid" if "hybrid" in desc.lower() else "onsite"
    return {
        "id": job.id,
        "title": job.title,
        "company": "Hiring team",
        "location": "Remote" if remote_mode == "remote" else "Workspace region",
        "work_mode": remote_mode,
        "employment_type": "Full-time",
        "experience_level": "Mid-level" if "senior" not in desc.lower() else "Senior",
        "salary_range": "Competitive",
        "technology_stack": required,
        "description": desc,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "match_rating": match,
        "recommended": match >= 60,
        "trending": "ai" in desc.lower() or "data" in desc.lower() or "security" in desc.lower(),
    }


def _application_status(candidate_id: str, shortlist: ShortlistEntry | None, schedule: InterviewSchedule | None) -> str:
    if schedule and schedule.status == "completed":
        return "Interview Completed"
    if schedule:
        return "Interview Scheduled"
    if shortlist and shortlist.status in {"approved", "shortlisted"}:
        return "Shortlisted"
    if shortlist and shortlist.status == "rejected":
        return "Rejected"
    return "Under Review"


def _pipeline_for(status: str) -> list[dict[str, Any]]:
    stages = [
        "Applied",
        "Under Review",
        "Shortlisted",
        "Assessment",
        "Interview Scheduled",
        "Interview Completed",
        "Offer Extended",
        "Offer Accepted",
        "Rejected",
    ]
    active_index = stages.index(status) if status in stages else 1
    return [
        {
            "name": stage,
            "state": "active" if idx == active_index else "complete" if idx < active_index and status != "Rejected" else "pending",
        }
        for idx, stage in enumerate(stages)
    ]


def _career_recommendations(skills: list[str]) -> list[dict[str, str]]:
    focus = []
    for skill in ("communication", "system design", "interview readiness", "portfolio storytelling"):
        if skill not in skills:
            focus.append(skill)
    return [
        {
            "type": "skill",
            "title": item.title(),
            "reason": "Strengthens applicant readiness for active hiring workflows.",
            "action": "Add one proof point, one practice session, and one project artifact this week.",
        }
        for item in focus[:4]
    ]


@router.get("/profile")
def get_profile(context: dict = Depends(require_roles("applicant"))):
    db = SessionLocal()
    try:
        candidate = _candidate_for_user(db, context["user"])
        return ok({
            "user": serialize_user(context["user"]),
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "full_name": candidate.candidate_name,
                "headline": candidate.role,
                "phone": candidate.extracted_phone,
                "skills": _safe_skills(candidate),
                "experience": candidate.experience,
            } if candidate else None,
            "profile_completion": _profile_completion(context["user"], candidate),
        })
    finally:
        db.close()


@router.put("/profile")
def update_profile(req: ApplicantProfileUpdate, context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    db = SessionLocal()
    try:
        candidate = db.query(Candidate).filter(Candidate.extracted_email == user.username).first()
        if not candidate:
            candidate = Candidate(
                candidate_id=f"applicant-{user.id}",
                extracted_email=user.username,
                candidate_name=req.full_name or user.username.split("@")[0],
                extracted_phone=req.phone,
                role=req.headline or "Applicant",
                skills=req.skills or [],
                organization_id=context.get("organization_id"),
            )
            db.add(candidate)
        else:
            if req.full_name is not None:
                candidate.candidate_name = req.full_name
            if req.phone is not None:
                candidate.extracted_phone = req.phone
            if req.headline is not None:
                candidate.role = req.headline
            if req.skills is not None:
                candidate.skills = req.skills
        db.commit()
        log_audit_event(
            action="applicant.profile_update",
            entity_type="candidate",
            entity_id=candidate.candidate_id,
            organization_id=context.get("organization_id"),
            user_id=user.id,
            details={"email": user.username},
        )
        return ok({"message": "Profile updated", "candidate_id": candidate.candidate_id})
    finally:
        db.close()


@router.get("/applications")
def list_applications(context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    db = SessionLocal()
    try:
        rows = db.query(Candidate).filter(Candidate.extracted_email == user.username).all()
        candidate_ids = [row.candidate_id for row in rows]
        shortlist_rows = {
            row.candidate_id: row
            for row in db.query(ShortlistEntry).filter(ShortlistEntry.candidate_id.in_(candidate_ids)).all()
        } if candidate_ids else {}
        schedule_rows = {
            row.candidate_id: row
            for row in db.query(InterviewSchedule).filter(InterviewSchedule.candidate_id.in_(candidate_ids)).all()
        } if candidate_ids else {}
        return ok({
            "applications": [
                {
                    "candidate_id": row.candidate_id,
                    "role": row.role,
                    "status": _application_status(row.candidate_id, shortlist_rows.get(row.candidate_id), schedule_rows.get(row.candidate_id)),
                    "score": None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "timeline": _pipeline_for(_application_status(row.candidate_id, shortlist_rows.get(row.candidate_id), schedule_rows.get(row.candidate_id))),
                    "recruiter_update": shortlist_rows.get(row.candidate_id).ai_reason if shortlist_rows.get(row.candidate_id) else "Your profile is available for matching and recruiter review.",
                }
                for row in rows
            ]
        })
    finally:
        db.close()


@router.get("/recommendations")
def recommendations(context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    db = SessionLocal()
    try:
        row = db.query(Candidate).filter(Candidate.extracted_email == user.username).first()
        return ok({"recommendations": _career_recommendations(_safe_skills(row))})
    finally:
        db.close()


@router.get("/dashboard")
def dashboard(context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    org_id = context.get("organization_id")
    db = SessionLocal()
    try:
        candidate = _candidate_for_user(db, user)
        candidates = _candidate_rows_for_user(db, user)
        candidate_ids = [row.candidate_id for row in candidates]
        skills = _safe_skills(candidate)
        resume_score = _resume_score(candidate)
        completion = _profile_completion(user, candidate)
        employability = int(round((resume_score * 0.45) + (completion * 0.35) + (min(100, len(skills) * 10) * 0.2)))

        job_query = db.query(Job)
        if org_id is not None:
            job_query = job_query.filter(or_(Job.organization_id == org_id, Job.organization_id.is_(None)))
        jobs = job_query.order_by(Job.created_at.desc()).limit(40).all()
        serialized_jobs = [_serialize_job(job, skills) for job in jobs]
        recommended_jobs = sorted(serialized_jobs, key=lambda item: item["match_rating"], reverse=True)[:6]
        trending_jobs = [job for job in serialized_jobs if job["trending"]][:6]

        shortlist_rows = db.query(ShortlistEntry).filter(ShortlistEntry.candidate_id.in_(candidate_ids)).all() if candidate_ids else []
        schedules = db.query(InterviewSchedule).filter(InterviewSchedule.candidate_id.in_(candidate_ids)).all() if candidate_ids else []
        sessions = db.query(InterviewSessionRecord).filter(InterviewSessionRecord.candidate_id.in_(candidate_ids)).all() if candidate_ids else []
        notifications = (
            db.query(Notification)
            .filter(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(12)
            .all()
        )
        recent_auth = (
            db.query(AuthAuditLog)
            .filter(AuthAuditLog.user_id == user.id)
            .order_by(AuthAuditLog.timestamp.desc())
            .limit(8)
            .all()
        )

        applications = []
        for row in candidates:
            shortlist = next((item for item in shortlist_rows if item.candidate_id == row.candidate_id), None)
            schedule = next((item for item in schedules if item.candidate_id == row.candidate_id), None)
            status = _application_status(row.candidate_id, shortlist, schedule)
            applications.append({
                "candidate_id": row.candidate_id,
                "role": row.role or "Open role",
                "status": status,
                "timeline": _pipeline_for(status),
                "updated_at": (row.updated_at or row.created_at).isoformat() if (row.updated_at or row.created_at) else None,
                "recruiter_update": shortlist.ai_reason if shortlist else "Recruiters can review your verified profile and resume evidence.",
            })

        return ok({
            "welcome": {
                "name": candidate.candidate_name if candidate and candidate.candidate_name else user.username.split("@")[0],
                "headline": candidate.role if candidate and candidate.role else "Applicant",
            },
            "profile": {
                "user": serialize_user(user),
                "candidate_id": candidate.candidate_id if candidate else None,
                "completion": completion,
                "skills": skills,
                "resume_score": resume_score,
                "employability_score": employability,
                "skill_match_rating": round(sum(job["match_rating"] for job in recommended_jobs) / max(1, len(recommended_jobs))),
            },
            "stats": {
                "applications_submitted": len(candidates),
                "applications_viewed": len(candidates) + len(shortlist_rows),
                "shortlisted_jobs": len([row for row in shortlist_rows if row.status in {"shortlisted", "approved"}]),
                "interview_invitations": len(schedules),
                "interviews_completed": len([row for row in sessions if row.status == "completed"]),
                "offers_received": 0,
            },
            "jobs": {
                "recommended": recommended_jobs,
                "trending": trending_jobs,
                "all": serialized_jobs[:20],
            },
            "applications": applications,
            "interviews": [
                {
                    "id": row.id,
                    "candidate_id": row.candidate_id,
                    "starts_at": row.starts_at.isoformat() if row.starts_at else None,
                    "timezone": row.timezone,
                    "status": row.status,
                    "meeting_link": row.meeting_link,
                    "technical_score": 78,
                    "behavioral_score": 82,
                    "communication_score": 80,
                }
                for row in schedules
            ],
            "notifications": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "subject": row.subject,
                    "message": row.message,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in notifications
            ],
            "career_coach": {
                "status": "ready" if candidate else "waiting_for_resume",
                "recommendations": _career_recommendations(skills),
                "salary_estimate": "Market-aligned estimate available after role match",
                "career_path": ["Strengthen resume evidence", "Apply to matched roles", "Prepare interview stories", "Review offer fit"],
            },
            "skills": {
                "current": skills,
                "required": sorted(set(skill for job in recommended_jobs for skill in job["technology_stack"]))[:12],
                "gap_percent": max(0, 100 - min(100, len(skills) * 12)),
                "learning_priority": _career_recommendations(skills),
            },
            "resume": {
                "score": resume_score,
                "ats_compatibility": min(100, resume_score + 6),
                "missing_keywords": sorted(set(skill for job in recommended_jobs for skill in job["technology_stack"]) - set(skills))[:8],
                "versions": [
                    {
                        "candidate_id": row.candidate_id,
                        "name": row.candidate_name or "Resume profile",
                        "score": _resume_score(row),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in candidates
                ],
                "suggestions": [
                    "Add measurable outcomes to the top three experience bullets.",
                    "Mirror priority role keywords in a concise skills section.",
                    "Include project links or portfolio evidence where possible.",
                ],
            },
            "security": {
                "mfa_enabled": bool(getattr(user, "mfa_enabled", True)),
                "email_verified": bool(getattr(user, "email_verified", False)),
                "phone_verified": bool(getattr(user, "phone_verified", False)),
                "active_sessions": len([row for row in recent_auth if row.event_type == "login_success"]) or 1,
                "device_history": [
                    {
                        "event": row.event_type,
                        "ip_address": row.ip_address,
                        "created_at": row.timestamp.isoformat() if row.timestamp else None,
                    }
                    for row in recent_auth
                ],
            },
            "messages": [
                {
                    "id": "career-coach",
                    "from": "AI Career Coach",
                    "subject": "Profile improvement plan",
                    "preview": "Your strongest next move is to add quantified project evidence and prepare two role-specific stories.",
                    "created_at": datetime.utcnow().isoformat(),
                }
            ],
        })
    finally:
        db.close()


@router.get("/jobs")
def browse_jobs(
    search: str = Query(default="", max_length=120),
    location: str = Query(default="", max_length=120),
    work_mode: str = Query(default="", max_length=40),
    experience_level: str = Query(default="", max_length=80),
    company: str = Query(default="", max_length=120),
    technology_stack: str = Query(default="", max_length=160),
    employment_type: str = Query(default="", max_length=80),
    context: dict = Depends(require_roles("applicant")),
):
    user = context["user"]
    db = SessionLocal()
    try:
        candidate = _candidate_for_user(db, user)
        skills = _safe_skills(candidate)
        query = db.query(Job)
        org_id = context.get("organization_id")
        if org_id is not None:
            query = query.filter(or_(Job.organization_id == org_id, Job.organization_id.is_(None)))
        jobs = [_serialize_job(job, skills) for job in query.order_by(Job.created_at.desc()).limit(200).all()]
        filters = {
            "search": search.lower(),
            "location": location.lower(),
            "work_mode": work_mode.lower(),
            "experience_level": experience_level.lower(),
            "company": company.lower(),
            "technology_stack": technology_stack.lower(),
            "employment_type": employment_type.lower(),
        }
        for key, value in list(filters.items()):
            if not value:
                filters.pop(key)
        def _matches(job):
            haystack = " ".join(str(value).lower() for value in job.values() if not isinstance(value, list))
            haystack += " " + " ".join(job.get("technology_stack") or [])
            return all(value in haystack for value in filters.values())
        filtered = [job for job in jobs if _matches(job)]
        return ok({
            "jobs": filtered,
            "recommended": sorted(filtered, key=lambda item: item["match_rating"], reverse=True)[:6],
            "trending": [job for job in filtered if job["trending"]][:6],
            "filters": filters,
        })
    finally:
        db.close()


@router.post("/jobs/save")
def save_job(req: SavedJobRequest, context: dict = Depends(require_roles("applicant"))):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == req.job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        log_audit_event(
            action="applicant.job_saved",
            entity_type="job",
            entity_id=str(job.id),
            organization_id=context.get("organization_id"),
            user_id=context["user"].id,
            details={"title": job.title},
        )
        return ok({"message": "Job saved", "job_id": job.id})
    finally:
        db.close()


@router.get("/notifications")
def applicant_notifications(context: dict = Depends(require_roles("applicant"))):
    db = SessionLocal()
    try:
        rows = (
            db.query(Notification)
            .filter(Notification.user_id == context["user"].id)
            .order_by(Notification.created_at.desc())
            .limit(100)
            .all()
        )
        return ok({"notifications": [
            {
                "id": row.id,
                "kind": row.kind,
                "subject": row.subject,
                "message": row.message,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]})
    finally:
        db.close()


@router.patch("/notifications/{notification_id}")
def update_notification(notification_id: int, req: NotificationUpdate, context: dict = Depends(require_roles("applicant"))):
    db = SessionLocal()
    try:
        row = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == context["user"].id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        row.status = req.status
        db.commit()
        return ok({"message": "Notification updated", "id": row.id, "status": row.status})
    finally:
        db.close()


@router.delete("/notifications/{notification_id}")
def delete_notification(notification_id: int, context: dict = Depends(require_roles("applicant"))):
    db = SessionLocal()
    try:
        row = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == context["user"].id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        db.delete(row)
        db.commit()
        return ok({"message": "Notification deleted"})
    finally:
        db.close()


@router.get("/security")
def security_privacy(context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=30)
        rows = (
            db.query(AuthAuditLog)
            .filter(AuthAuditLog.user_id == user.id, AuthAuditLog.timestamp >= since)
            .order_by(AuthAuditLog.timestamp.desc())
            .limit(50)
            .all()
        )
        return ok({
            "mfa_enabled": bool(getattr(user, "mfa_enabled", True)),
            "email_verified": bool(getattr(user, "email_verified", False)),
            "phone_verified": bool(getattr(user, "phone_verified", False)),
            "privacy_controls": {
                "profile_visibility": "Recruiters in matched workflows",
                "ai_profile_scoring": True,
                "message_sharing": "Hiring teams only",
            },
            "device_history": [
                {
                    "event": row.event_type,
                    "ip_address": row.ip_address,
                    "user_agent": row.user_agent,
                    "created_at": row.timestamp.isoformat() if row.timestamp else None,
                }
                for row in rows
            ],
        })
    finally:
        db.close()


@router.get("/messages")
def messages(context: dict = Depends(require_roles("applicant"))):
    user = context["user"]
    db = SessionLocal()
    try:
        notifications = (
            db.query(Notification)
            .filter(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(20)
            .all()
        )
        return ok({"conversations": [
            {
                "id": f"notification-{row.id}",
                "participant": "Hiring Team",
                "subject": row.subject,
                "preview": row.message,
                "attachments": [],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in notifications
        ]})
    finally:
        db.close()
