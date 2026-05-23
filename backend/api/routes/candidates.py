from fastapi import APIRouter, Depends
from sqlalchemy import or_

from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.db.database import SessionLocal
from backend.db.models import Candidate


router = APIRouter(tags=["Candidates"])


def serialize_candidate(candidate: Candidate):
    return {
        "id": candidate.id,
        "candidate_id": candidate.candidate_id,
        "candidate_name": getattr(candidate, "candidate_name", None),
        "name_confidence": (getattr(candidate, "name_confidence", None) or 0) / 100 if getattr(candidate, "name_confidence", None) is not None else 0.0,
        "name_source": getattr(candidate, "name_source", None),
        "extracted_email": getattr(candidate, "extracted_email", None),
        "extracted_phone": getattr(candidate, "extracted_phone", None),
        "skills": candidate.skills or [],
        "role": candidate.role,
        "experience": candidate.experience or 0,
        "text_snippet": (candidate.text or "")[:300],
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
    }


@router.get("/candidates")
def get_candidates(context: dict = Depends(get_current_user_context)):
    db = SessionLocal()
    try:
        query = db.query(Candidate)
        if context.get("organization_id") is not None:
            query = query.filter(or_(Candidate.organization_id == context.get("organization_id"), Candidate.organization_id.is_(None)))
        candidates = query.order_by(Candidate.created_at.desc()).all()
        return ok([serialize_candidate(candidate) for candidate in candidates])
    finally:
        db.close()
