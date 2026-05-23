from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import AIMemory, AuditLog


def _stable_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def write_memory(
    *,
    organization_id: Optional[int],
    user_id: Optional[int],
    kind: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = AIMemory(
            organization_id=organization_id,
            user_id=user_id,
            kind=(kind or "memory")[:120],
            entity_type=(entity_type or "")[:80] or None,
            entity_id=(str(entity_id)[:160] if entity_id else None),
            payload=payload or {},
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"id": row.id, "created_at": row.created_at.isoformat()}
    finally:
        db.close()


def _recent_memory_rows(
    organization_id: Optional[int],
    user_id: Optional[int] = None,
    *,
    limit: int = 16,
) -> list[AIMemory]:
    db = SessionLocal()
    try:
        q = db.query(AIMemory)
        if organization_id is not None:
            q = q.filter(or_(AIMemory.organization_id == organization_id, AIMemory.organization_id.is_(None)))
        if user_id is not None:
            q = q.filter(or_(AIMemory.user_id == user_id, AIMemory.user_id.is_(None)))
        return q.order_by(AIMemory.id.desc()).limit(limit).all()
    finally:
        db.close()


def _upsert_preference_profile(organization_id: Optional[int], user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Lightweight learning loop: infer a few recruiter preferences from real shortlist behavior.
    Stored as a compact memory record so copilot can reference it naturally.
    """
    db = SessionLocal()
    try:
        if organization_id is None:
            return {"status": "skipped", "reason": "no_org"}

        rows = db.query(ShortlistEntry).filter(ShortlistEntry.organization_id == organization_id).order_by(ShortlistEntry.created_at.desc()).limit(400).all()
        if not rows:
            profile = {"threshold": 75, "calibration": "default", "notes": "No shortlist history yet."}
        else:
            approved = [r for r in rows if (r.status or "") == "approved"]
            rejected = [r for r in rows if (r.status or "") == "rejected"]
            shortlisted = [r for r in rows if (r.status or "") != "rejected"]
            # Calibrate threshold from real approvals when possible.
            if len(approved) >= 3:
                avg = sum(int(r.shortlist_score or 0) for r in approved) / max(1, len(approved))
                # Conservative: set threshold a bit below the average approved score.
                threshold = int(max(50, min(90, round(avg - 6))))
                calibration = "learned_from_approvals"
            elif len(rejected) >= 6 and not approved:
                threshold = 78
                calibration = "raised_due_to_rejections"
            else:
                threshold = 75
                calibration = "default"

            profile = {
                "threshold": threshold,
                "calibration": calibration,
                "approval_rate": round((len(approved) / max(1, (len(approved) + len(rejected)))) * 100.0, 2),
                "sample_size": len(rows),
                "notes": "Preference profile is inferred from shortlist actions and will improve as decisions accumulate.",
            }

        # Write a new profile snapshot (keep last few; simplest and avoids migrations).
        write_memory(
            organization_id=organization_id,
            user_id=user_id,
            kind="preferences.profile",
            entity_type="organization",
            entity_id=str(organization_id),
            payload=profile,
        )
        return {"status": "ok", "profile": profile}
    finally:
        db.close()


def record_match_snapshot(
    *,
    organization_id: Optional[int],
    user_id: Optional[int],
    job_description: str,
    rankings: list[dict[str, Any]],
) -> None:
    """
    Store a compact snapshot of *why* the top results look the way they do.
    """
    jd_hash = _stable_hash(job_description or "")
    top = (rankings or [])[:8]
    snapshot = []
    for item in top:
        snapshot.append(
            {
                "candidate_id": item.get("candidate_id"),
                "candidate_name": item.get("candidate_name"),
                "match_score": item.get("match_score", item.get("score")),
                "confidence": item.get("confidence"),
                "recommendation": item.get("recommendation"),
                "matched_skills": (item.get("matched_skills") or [])[:10],
                "missing_skills": (item.get("missing_skills") or [])[:10],
                "reason": (item.get("recruiter_summary") or item.get("recommendation_reason") or item.get("explanation") or "")[:500],
            }
        )

    payload = {
        "job_hash": jd_hash,
        "job_description_snippet": (job_description or "").strip()[:400],
        "ranked_count": len(rankings or []),
        "top": snapshot,
    }
    write_memory(
        organization_id=organization_id,
        user_id=user_id,
        kind="match.snapshot",
        entity_type="job",
        entity_id=jd_hash,
        payload=payload,
    )

    # Update preference profile opportunistically.
    _upsert_preference_profile(organization_id, user_id)


def record_shortlist_action(
    *,
    organization_id: Optional[int],
    user_id: Optional[int],
    candidate_id: str,
    job_id: Optional[int],
    action: str,
    details: dict[str, Any] | None = None,
    entity_type: str = "candidate",
) -> None:
    payload = {
        "candidate_id": candidate_id,
        "job_id": job_id,
        "action": action,
        "details": details or {},
    }
    write_memory(
        organization_id=organization_id,
        user_id=user_id,
        kind="shortlist.decision",
        entity_type=(entity_type or "candidate"),
        entity_id=candidate_id,
        payload=payload,
    )
    _upsert_preference_profile(organization_id, user_id)


def memory_context_note(
    *,
    organization_id: Optional[int],
    user_id: Optional[int] = None,
    limit: int = 10,
) -> str:
    """
    Produce a small, safe, human-readable memory note for the copilot.
    """
    rows = _recent_memory_rows(organization_id, user_id, limit=limit)
    if not rows:
        return ""

    # Extract latest preference profile if present.
    pref = None
    for row in rows:
        if (row.kind or "") == "preferences.profile" and isinstance(row.payload, dict):
            pref = row.payload
            break

    pieces: list[str] = []
    if isinstance(pref, dict) and pref:
        threshold = pref.get("threshold")
        if threshold is not None:
            pieces.append(f"Learned preference: shortlist threshold around {threshold}%.")

    # Include the latest match snapshot headline.
    for row in rows:
        if (row.kind or "") == "match.snapshot" and isinstance(row.payload, dict):
            top = (row.payload.get("top") or []) if isinstance(row.payload.get("top"), list) else []
            if top:
                lead = top[0]
                label = lead.get("candidate_name") or lead.get("candidate_id")
                score = lead.get("match_score")
                reco = lead.get("recommendation") or ""
                if label:
                    pieces.append(f"Recent match: {label} led at {score}% ({reco}).")
            break

    if not pieces:
        # Safe fallback: don't dump raw payloads.
        pieces.append("Recent hiring memory is available (match snapshots and shortlist decisions).")

    return "AI working memory: " + " ".join(pieces[:3])
