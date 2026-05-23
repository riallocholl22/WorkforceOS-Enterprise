from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_

from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.db.database import SessionLocal
from backend.db.models import Candidate
from backend.models.job import Job
from backend.services.decision_service import make_decision
from backend.services.matcher import rank_candidates_tfidf
from backend.services.predictive_intelligence_service import predict_from_match_payload
from backend.services.enterprise_service import log_audit_event
from backend.services.ai_memory_service import record_match_snapshot
from backend.services.autonomous_ops_service import maybe_emit_autonomous_notifications
from backend.services.parser import extract_skills, extract_text


router = APIRouter(tags=["Ranking"])


def _candidate_id_from_path(path: Path) -> str:
    return path.stem


def _latest_job_description(organization_id: int | None = None) -> str:
    db = SessionLocal()
    try:
        query = db.query(Job)
        if organization_id is not None:
            query = query.filter(Job.organization_id == organization_id)
        job = query.order_by(Job.created_at.desc()).first()
        return job.description if job else ""
    finally:
        db.close()


def _stored_candidates(organization_id: int | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(Candidate)
        if organization_id is not None:
            query = query.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
        candidates = query.order_by(Candidate.created_at.desc()).all()
        return [
            {
                "candidate_id": candidate.candidate_id,
                "candidate_name": getattr(candidate, "candidate_name", None),
                "name_confidence": (getattr(candidate, "name_confidence", None) or 0) / 100 if getattr(candidate, "name_confidence", None) is not None else 0.0,
                "name_source": getattr(candidate, "name_source", None),
                "skills": candidate.skills or [],
                "resume_extraction": candidate.extraction_json or {},
                "text": candidate.text or "",
                "raw_text": (candidate.raw_text or "")[:4000],
                "role": candidate.role,
                "experience": {"min": candidate.experience or 0},
                "text_snippet": ((candidate.raw_text or "") or (candidate.text or ""))[:300],
            }
            for candidate in candidates
        ]
    finally:
        db.close()


def _sample_candidates() -> list[dict]:
    resume_dir = Path("resumes")
    if not resume_dir.exists():
        return []

    candidates = []

    for resume_path in sorted(resume_dir.glob("*.pdf")):
        text = extract_text(str(resume_path))
        skills = extract_skills(text)

        candidates.append(
            {
                "candidate_id": _candidate_id_from_path(resume_path),
                "skills": skills,
                "text": text,
                "experience": {"min": 0},
                "text_snippet": text[:300],
            }
        )

    return candidates


@router.get("/rank-candidates")
def rank_candidates(
    job_description: Optional[str] = Query(default=None),
    context: dict = Depends(get_current_user_context),
):
    requested_description = job_description if isinstance(job_description, str) else None
    organization_id = context.get("organization_id")
    description = (requested_description or _latest_job_description(organization_id)).strip()

    if not description:
        raise HTTPException(
            status_code=400,
            detail="Create a job or provide job_description before ranking candidates",
        )

    candidates = _stored_candidates(organization_id) or _sample_candidates()

    rankings = rank_candidates_tfidf(description, candidates)

    for candidate in rankings:
        # Deterministic predictive intelligence (keeps UI rich even without an external model).
        extraction_conf = 0.0
        try:
            extraction_conf = float(((candidate.get("resume_extraction") or {}).get("confidence")) or 0.0)
        except Exception:
            extraction_conf = 0.0
        candidate["predictions"] = predict_from_match_payload(candidate, extraction_confidence=extraction_conf)
        candidate.pop("text", None)
        candidate.pop("raw_text", None)
        candidate["decision_intelligence"] = make_decision(
            match_score=candidate.get("match_score", 0),
            interview_score=0,
            communication_score=0,
            proctor_score=100,
            missing_skills=candidate.get("missing_skills", []),
        )

    # Activity timeline: record that ranking was generated (no raw text stored).
    try:
        user = context.get("user")
        user_id = context.get("user_id") or getattr(user, "id", None)
        org_id = context.get("organization_id")
        log_audit_event(
            action="match.completed",
            entity_type="job",
            entity_id=str((org_id or "workspace")),
            organization_id=org_id,
            user_id=user_id,
            details={
                "ranked": len(rankings),
                "top_candidate_id": rankings[0].get("candidate_id") if rankings else None,
                "top_score": rankings[0].get("match_score", rankings[0].get("score")) if rankings else None,
            },
        )
        # Hidden AI working memory: store a compact snapshot for continuity.
        record_match_snapshot(
            organization_id=org_id,
            user_id=int(user_id) if user_id is not None else None,
            job_description=description,
            rankings=rankings,
        )
        maybe_emit_autonomous_notifications(
            organization_id=org_id,
            user_id=int(user_id) if user_id is not None else None,
        )
    except Exception:
        pass

    return ok({
        "job_description": description,
        "candidates": rankings,
    })
