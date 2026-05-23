from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_

from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.api.routes.ranking import rank_candidates
from backend.db.database import SessionLocal
from backend.db.models import Candidate
from backend.models.enterprise import Membership
from backend.models.job import Job
from backend.models.report import Report
from backend.services.autonomous_ops_service import autonomous_insights


router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
def dashboard_summary(context: dict = Depends(get_current_user_context)):
    db = SessionLocal()
    try:
        organization_id = context.get("organization_id")

        candidate_query = db.query(Candidate)
        job_query = db.query(Job)
        report_query = db.query(Report)
        member_query = db.query(Membership)

        if organization_id is not None:
            candidate_query = candidate_query.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
            job_query = job_query.filter(Job.organization_id == organization_id)
            if hasattr(Report, "organization_id"):
                report_query = report_query.filter(Report.organization_id == organization_id)
            member_query = member_query.filter(Membership.organization_id == organization_id, Membership.is_active == True)  # noqa: E712

        total_candidates = candidate_query.count()
        total_jobs = job_query.count()
        total_reports = report_query.count()
        total_members = member_query.count()
    finally:
        db.close()

    try:
        ranking_response = rank_candidates(job_description=None, context=context)
        rankings = ranking_response["data"]["candidates"]
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        rankings = []
    average_score = 0
    top_candidate = None
    top_score = 0
    skill_counter = Counter()

    if rankings:
        average_score = round(sum(item.get("score", 0) for item in rankings) / len(rankings), 2)
        top_candidate = rankings[0].get("candidate_id")
        top_score = rankings[0].get("score", 0)

        for item in rankings:
            skill_counter.update(item.get("matched_skills") or [])

    auto = autonomous_insights(context.get("organization_id"))

    return ok({
        "total_candidates": total_candidates or len(rankings),
        "total_jobs": total_jobs,
        "total_reports": total_reports,
        "total_members": total_members,
        "average_score": average_score,
        "top_candidate": top_candidate,
        "top_score": top_score,
        "top_skills": [
            {"skill": skill, "count": count}
            for skill, count in skill_counter.most_common(5)
        ],
        "autonomous_priorities": (auto.get("priorities") if isinstance(auto, dict) else []) or [],
        "autonomous_alerts": (auto.get("alerts") if isinstance(auto, dict) else []) or [],
    })
