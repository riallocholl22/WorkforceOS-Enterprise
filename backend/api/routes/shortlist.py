from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from backend.api.dependencies import require_roles
from backend.api.responses import ok
from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.services.enterprise_service import log_audit_event
from backend.services.matcher import rank_candidates_tfidf
from backend.services.ai_memory_service import record_shortlist_action


router = APIRouter(tags=["Shortlist"])

SHORTLIST_ALLOWED_ROLES = ("super_admin", "company_admin", "recruiter", "hiring_manager")


def _serialize(entry: ShortlistEntry) -> dict[str, Any]:
    return {
        "candidate_id": entry.candidate_id,
        "job_id": entry.job_id,
        "organization_id": entry.organization_id,
        "status": entry.status,
        "shortlist_score": entry.shortlist_score,
        "confidence": entry.confidence,
        "ai_reason": entry.ai_reason or "",
        "recruiter_notes": entry.recruiter_notes or "",
        "created_by": entry.created_by or "",
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _org_id(context: dict) -> Optional[int]:
    if not isinstance(context, dict):
        return None
    org_id = context.get("organization_id")
    if org_id is not None:
        return org_id
    user = context.get("user") if isinstance(context.get("user"), dict) else {}
    user_id = user.get("id")
    # Fallback: user-scoped workspace when organization_id is missing.
    return int(user_id) if isinstance(user_id, int) else None


class ShortlistCreateRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    job_id: Optional[int] = None
    recruiter_notes: str = Field(default="", max_length=3000)
    shortlist_score: int = Field(default=0, ge=0, le=100)
    confidence: int = Field(default=0, ge=0, le=100)
    ai_reason: str = Field(default="", max_length=6000)


class ShortlistAutoRequest(BaseModel):
    job_id: Optional[int] = None
    job_description: str = Field(default="", max_length=10000)
    threshold: int = Field(default=75, ge=0, le=100)
    max_candidates: int = Field(default=12, ge=1, le=50)


class ShortlistDecisionRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    job_id: Optional[int] = None
    recruiter_notes: str = Field(default="", max_length=3000)


@router.get("/shortlist")
def list_shortlist(
    job_id: Optional[int] = None,
    status: Optional[str] = None,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    org_id = _org_id(context)
    db = SessionLocal()
    try:
        query = db.query(ShortlistEntry).filter(ShortlistEntry.organization_id == org_id)
        if job_id is not None:
            query = query.filter(ShortlistEntry.job_id == job_id)
        if status:
            query = query.filter(ShortlistEntry.status == status)
        rows = query.order_by(ShortlistEntry.created_at.desc()).limit(200).all()
        return ok(
            {
                "entries": [_serialize(row) for row in rows],
                "summary": {
                    "total": len(rows),
                    "approved": sum(1 for row in rows if row.status == "approved"),
                    "rejected": sum(1 for row in rows if row.status == "rejected"),
                },
            }
        )
    finally:
        db.close()


@router.get("/shortlist/analytics")
def shortlist_analytics(
    job_id: Optional[int] = None,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    org_id = _org_id(context)
    db = SessionLocal()
    try:
        query = db.query(ShortlistEntry).filter(ShortlistEntry.organization_id == org_id)
        if job_id is not None:
            query = query.filter(ShortlistEntry.job_id == job_id)
        rows = query.all()
        if not rows:
            return ok(
                {
                    "totals": {"total": 0, "approved": 0, "rejected": 0, "shortlisted": 0},
                    "approval_rate": 0.0,
                    "average_match_score": 0.0,
                    "top_skills": [],
                }
            )

        approved = [row for row in rows if row.status == "approved"]
        rejected = [row for row in rows if row.status == "rejected"]
        shortlisted = [row for row in rows if row.status not in {"rejected"}]
        avg_score = sum(int(row.shortlist_score or 0) for row in rows) / max(1, len(rows))
        approval_rate = (len(approved) / max(1, (len(approved) + len(rejected)))) * 100

        candidate_ids = [row.candidate_id for row in rows]
        candidates = db.query(Candidate).filter(Candidate.candidate_id.in_(candidate_ids)).all()
        skill_counts: dict[str, int] = {}
        for candidate in candidates:
            for skill in (candidate.skills or []):
                if not isinstance(skill, str) or not skill:
                    continue
                key = skill.strip()
                skill_counts[key] = skill_counts.get(key, 0) + 1
        top_skills = sorted(skill_counts.items(), key=lambda item: item[1], reverse=True)[:12]

        return ok(
            {
                "totals": {
                    "total": len(rows),
                    "approved": len(approved),
                    "rejected": len(rejected),
                    "shortlisted": len(shortlisted),
                },
                "approval_rate": round(approval_rate, 2),
                "average_match_score": round(avg_score, 2),
                "top_skills": [{"skill": skill, "count": count} for skill, count in top_skills],
            }
        )
    finally:
        db.close()


@router.post("/shortlist")
def create_shortlist(
    req: ShortlistCreateRequest,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    org_id = _org_id(context)
    user_email = str(context.get("email") or "")
    user_id = context.get("user_id")
    db = SessionLocal()
    try:
        existing = (
            db.query(ShortlistEntry)
            .filter(
                ShortlistEntry.organization_id == org_id,
                ShortlistEntry.job_id == req.job_id,
                ShortlistEntry.candidate_id == req.candidate_id,
            )
            .first()
        )
        if existing:
            existing.recruiter_notes = req.recruiter_notes or existing.recruiter_notes
            existing.shortlist_score = req.shortlist_score or existing.shortlist_score
            existing.confidence = req.confidence or existing.confidence
            existing.ai_reason = req.ai_reason or existing.ai_reason
            existing.status = existing.status or "shortlisted"
            db.commit()
            log_audit_event("shortlist.update", "candidate", req.candidate_id, org_id, user_id, {"job_id": req.job_id})
            try:
                record_shortlist_action(
                    organization_id=org_id,
                    user_id=int(user_id) if user_id is not None else None,
                    candidate_id=req.candidate_id,
                    job_id=req.job_id,
                    action="update",
                    details={"shortlist_score": req.shortlist_score, "confidence": req.confidence},
                )
            except Exception:
                pass
            return ok({"entry": _serialize(existing), "mode": "updated"})

        entry = ShortlistEntry(
            organization_id=org_id,
            job_id=req.job_id,
            candidate_id=req.candidate_id,
            status="shortlisted",
            shortlist_score=req.shortlist_score,
            confidence=req.confidence,
            ai_reason=req.ai_reason,
            recruiter_notes=req.recruiter_notes,
            created_by=user_email,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        log_audit_event("shortlist.add", "candidate", req.candidate_id, org_id, user_id, {"job_id": req.job_id})
        try:
            record_shortlist_action(
                organization_id=org_id,
                user_id=int(user_id) if user_id is not None else None,
                candidate_id=req.candidate_id,
                job_id=req.job_id,
                action="add",
                details={"shortlist_score": req.shortlist_score, "confidence": req.confidence},
            )
        except Exception:
            pass
        return ok({"entry": _serialize(entry), "mode": "created"})
    finally:
        db.close()


@router.post("/shortlist/add")
def add_to_shortlist(
    req: ShortlistCreateRequest,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    return create_shortlist(req, context)


@router.delete("/shortlist/{candidate_id}")
def delete_from_shortlist(
    candidate_id: str,
    job_id: Optional[int] = None,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    org_id = _org_id(context)
    user_id = context.get("user_id")
    db = SessionLocal()
    try:
        query = db.query(ShortlistEntry).filter(
            ShortlistEntry.organization_id == org_id,
            ShortlistEntry.candidate_id == candidate_id,
        )
        if job_id is not None:
            query = query.filter(ShortlistEntry.job_id == job_id)
        deleted = query.delete(synchronize_session=False)
        db.commit()
        log_audit_event("shortlist.remove", "candidate", candidate_id, org_id, user_id, {"job_id": job_id, "deleted": deleted})
        try:
            record_shortlist_action(
                organization_id=org_id,
                user_id=int(user_id) if user_id is not None else None,
                candidate_id=candidate_id,
                job_id=job_id,
                action="remove",
                details={"deleted": int(deleted)},
            )
        except Exception:
            pass
        return ok({"deleted": deleted})
    finally:
        db.close()


@router.get("/jobs/{job_id}/shortlist")
def shortlist_for_job(
    job_id: int,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    return list_shortlist(job_id=job_id, status=None, context=context)


@router.post("/shortlist/auto")
def auto_shortlist(
    req: ShortlistAutoRequest,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    org_id = _org_id(context)
    user_email = str(context.get("email") or "")
    user_id = context.get("user_id")
    description = (req.job_description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="job_description is required for auto-shortlist")

    db = SessionLocal()
    try:
        candidate_rows = db.query(Candidate).filter(Candidate.organization_id == org_id).all()
        candidates = [
            {
                "candidate_id": row.candidate_id,
                "skills": row.skills or [],
                "text": row.text or "",
                "experience": {"min": row.experience or 0},
            }
            for row in candidate_rows
        ]
        ranked = rank_candidates_tfidf(description, candidates)
        selected = [item for item in ranked if (item.get("match_score") or 0) >= req.threshold][: req.max_candidates]

        created: list[dict[str, Any]] = []
        for item in selected:
            candidate_id = str(item.get("candidate_id") or "")
            if not candidate_id:
                continue
            existing = (
                db.query(ShortlistEntry)
                .filter(
                    ShortlistEntry.organization_id == org_id,
                    ShortlistEntry.job_id == req.job_id,
                    ShortlistEntry.candidate_id == candidate_id,
                )
                .first()
            )
            if existing:
                existing.shortlist_score = int(item.get("match_score") or 0)
                existing.confidence = int(min(100, max(0, (item.get("tfidf_score") or 0))))
                existing.ai_reason = str(item.get("explanation") or existing.ai_reason or "")
                existing.status = existing.status or "shortlisted"
                created.append(_serialize(existing))
                continue

            entry = ShortlistEntry(
                organization_id=org_id,
                job_id=req.job_id,
                candidate_id=candidate_id,
                status="shortlisted",
                shortlist_score=int(item.get("match_score") or 0),
                confidence=int(min(100, max(0, (item.get("tfidf_score") or 0)))),
                ai_reason=str(item.get("explanation") or ""),
                recruiter_notes="",
                created_by=user_email,
            )
            db.add(entry)
            db.flush()
            created.append(_serialize(entry))

        db.commit()
        log_audit_event(
            "shortlist.auto",
            "job",
            str(req.job_id or "adhoc"),
            org_id,
            user_id,
            {"threshold": req.threshold, "selected": len(created)},
        )
        try:
            record_shortlist_action(
                organization_id=org_id,
                user_id=int(user_id) if user_id is not None else None,
                candidate_id=str(req.job_id or "adhoc"),
                job_id=req.job_id,
                action="auto",
                details={"threshold": req.threshold, "selected": len(created)},
                entity_type="job",
            )
        except Exception:
            pass
        return ok({"entries": created, "threshold": req.threshold, "count": len(created)})
    finally:
        db.close()


@router.post("/enterprise/shortlist/approve")
def approve_shortlist(
    req: ShortlistDecisionRequest,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    return _set_status(req, "approved", context)


@router.post("/enterprise/shortlist/reject")
def reject_shortlist(
    req: ShortlistDecisionRequest,
    context: dict = Depends(require_roles(*SHORTLIST_ALLOWED_ROLES)),
):
    return _set_status(req, "rejected", context)


def _set_status(req: ShortlistDecisionRequest, status: str, context: dict):
    org_id = _org_id(context)
    user_id = context.get("user_id")
    db = SessionLocal()
    try:
        entry = (
            db.query(ShortlistEntry)
            .filter(
                ShortlistEntry.organization_id == org_id,
                ShortlistEntry.candidate_id == req.candidate_id,
                ShortlistEntry.job_id == req.job_id,
            )
            .first()
        )
        if not entry:
            # Create entry if missing, so the enterprise workflow doesn't dead-end.
            entry = ShortlistEntry(
                organization_id=org_id,
                job_id=req.job_id,
                candidate_id=req.candidate_id,
                status=status,
                recruiter_notes=req.recruiter_notes,
                shortlist_score=0,
                confidence=0,
                ai_reason="",
                created_by=str(context.get("email") or ""),
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            log_audit_event(f"shortlist.{status}", "candidate", req.candidate_id, org_id, user_id, {"job_id": req.job_id, "mode": "created"})
            try:
                record_shortlist_action(
                    organization_id=org_id,
                    user_id=int(user_id) if user_id is not None else None,
                    candidate_id=req.candidate_id,
                    job_id=req.job_id,
                    action=status,
                    details={"mode": "created"},
                )
            except Exception:
                pass
            return ok({"entry": _serialize(entry), "mode": "created"})

        entry.status = status
        if req.recruiter_notes:
            entry.recruiter_notes = req.recruiter_notes
        db.commit()
        log_audit_event(f"shortlist.{status}", "candidate", req.candidate_id, org_id, user_id, {"job_id": req.job_id})
        try:
            record_shortlist_action(
                organization_id=org_id,
                user_id=int(user_id) if user_id is not None else None,
                candidate_id=req.candidate_id,
                job_id=req.job_id,
                action=status,
                details={"mode": "updated"},
            )
        except Exception:
            pass
        return ok({"entry": _serialize(entry), "mode": "updated"})
    finally:
        db.close()
