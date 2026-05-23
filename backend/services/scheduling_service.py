from datetime import datetime, timedelta
from typing import Any, Dict

from backend.db.database import SessionLocal
from backend.models.enterprise import InterviewSchedule


def schedule_interview(
    organization_id: int | None,
    candidate_id: str,
    interviewer_email: str,
    starts_at: datetime,
    timezone: str = "UTC",
    provider: str = "manual",
) -> Dict[str, Any]:
    meeting_link = f"https://meet.ai-recruit.local/{candidate_id}/{int(starts_at.timestamp())}"
    reminders = [
        {"offset_minutes": 1440, "channel": "email", "status": "pending"},
        {"offset_minutes": 60, "channel": "email", "status": "pending"},
    ]
    db = SessionLocal()
    try:
        row = InterviewSchedule(
            organization_id=organization_id,
            candidate_id=candidate_id,
            interviewer_email=interviewer_email,
            starts_at=starts_at,
            timezone=timezone,
            provider=provider,
            meeting_link=meeting_link,
            reminders=reminders,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return serialize_schedule(row)
    finally:
        db.close()


def list_schedules(organization_id: int | None) -> list[Dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(InterviewSchedule)
        if organization_id is not None:
            query = query.filter(InterviewSchedule.organization_id == organization_id)
        rows = query.order_by(InterviewSchedule.starts_at.asc()).limit(50).all()
        return [serialize_schedule(row) for row in rows]
    finally:
        db.close()


def serialize_schedule(row: InterviewSchedule) -> Dict[str, Any]:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "interviewer_email": row.interviewer_email,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "timezone": row.timezone,
        "provider": row.provider,
        "meeting_link": row.meeting_link,
        "status": row.status,
        "reminders": row.reminders or [],
    }
