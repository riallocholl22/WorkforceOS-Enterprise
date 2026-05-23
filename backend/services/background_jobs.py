import os
import secrets
from datetime import datetime
from typing import Any, Dict, Optional


JOBS: Dict[str, Dict[str, Any]] = {}


def job_system_status() -> Dict[str, Any]:
    return {
        "mode": os.getenv("JOB_QUEUE_MODE", "in_process"),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "celery_configured": bool(os.getenv("CELERY_BROKER_URL")),
        "rq_configured": bool(os.getenv("RQ_REDIS_URL") or os.getenv("REDIS_URL")),
        "queued_jobs": len([job for job in JOBS.values() if job["status"] == "queued"]),
        "supported_jobs": [
            "resume.parse",
            "ai.match",
            "invoice.generate",
            "email.send",
            "analytics.generate",
            "interview.process",
            "ai.report.generate",
        ],
    }


def enqueue_job(
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    organization_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    job_id = f"job_{secrets.token_hex(10)}"
    now = datetime.utcnow().isoformat()
    job = {
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "attempts": 0,
        "max_attempts": int(os.getenv("JOB_MAX_ATTEMPTS", "3")),
        "organization_id": organization_id,
        "user_id": user_id,
        "payload": payload or {},
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "queue_backend": os.getenv("JOB_QUEUE_MODE", "in_process"),
    }
    JOBS[job_id] = job
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return JOBS.get(job_id)


def mark_job(job_id: str, status: str, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    job = JOBS.get(job_id)
    if not job:
        return None
    job["status"] = status
    job["result"] = result
    job["error"] = error
    job["updated_at"] = datetime.utcnow().isoformat()
    if status == "retrying":
        job["attempts"] += 1
    return job
