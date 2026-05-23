from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.db.database import SessionLocal
from backend.models.job import Job

router = APIRouter(tags=["Jobs"])

class JobRequest(BaseModel):
    title: str
    description: str


@router.post("/create-job")
@router.post("/jobs/create")
def create_job(req: JobRequest, context: dict = Depends(get_current_user_context)):

    db = SessionLocal()

    try:
        if not req.title.strip() or not req.description.strip():
            raise HTTPException(status_code=400, detail="Title and description are required")

        job = Job(
            title=req.title.strip(),
            description=req.description.strip(),
            organization_id=context.get("organization_id"),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        return ok({
            "id": job.id,
            "title": job.title,
            "description": job.description,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        })
    finally:
        db.close()


@router.get("/jobs")
def get_jobs(context: dict = Depends(get_current_user_context)):

    db = SessionLocal()
    try:
        query = db.query(Job)
        if context.get("organization_id") is not None:
            query = query.filter(Job.organization_id == context.get("organization_id"))
        jobs = query.order_by(Job.created_at.desc()).all()
        return ok([
            {
                "id": job.id,
                "title": job.title,
                "description": job.description,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
            for job in jobs
        ])
    finally:
        db.close()
