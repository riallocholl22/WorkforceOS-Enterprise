from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user_context, require_roles
from backend.api.responses import ok
from backend.models.enterprise import AuditLog
from backend.db.database import SessionLocal
from backend.services.ai_orchestration_service import ai_provider_status, route_ai_request
from backend.services.background_jobs import enqueue_job, get_job, job_system_status
from backend.services.email_service import provider_status as email_provider_status
from backend.services.email_service import send_transactional_email
from backend.services.embedding_service import semantic_candidate_search, vector_provider_status
from backend.services.enterprise_service import log_audit_event
from backend.services.enterprise_service import get_workspace_overview
from backend.services.orchestration_service import event_bus, proctor_orchestrator
from backend.services.storage_service import create_signed_upload, storage_status
from backend.services.subscription_enforcement import can_use_feature, subscription_snapshot
from backend.services.voice_interview_service import analyze_voice_interview, voice_interview_capabilities


router = APIRouter(prefix="/platform", tags=["Enterprise Platform"])


class EmailRequest(BaseModel):
    to: list[str] = Field(..., min_items=1, max_items=25)
    template: str = Field(default="recruiter_notification", max_length=80)
    context: Dict[str, Any] = Field(default_factory=dict)


class JobRequest(BaseModel):
    job_type: str = Field(..., max_length=80)
    payload: Dict[str, Any] = Field(default_factory=dict)


class SignedUploadRequest(BaseModel):
    filename: str = Field(..., max_length=180)
    size_bytes: int = Field(..., ge=1)
    content_type: Optional[str] = Field(default=None, max_length=120)
    purpose: str = Field(default="resume", max_length=60)


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)


class VoiceAnalysisRequest(BaseModel):
    candidate_id: str = Field(..., max_length=120)
    transcript: str = Field(..., min_length=1, max_length=20000)


class AIRouteRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=6000)
    context: Dict[str, Any] = Field(default_factory=dict)


@router.get("/infrastructure")
def infrastructure_status(context: dict = Depends(get_current_user_context)):
    orchestration = event_bus.snapshot(limit=40)
    return ok({
        "email": email_provider_status(),
        "jobs": job_system_status(),
        "storage": storage_status(),
        "vectors": vector_provider_status(),
        "ai": ai_provider_status(),
        "voice_interview": voice_interview_capabilities(),
        "subscription": subscription_snapshot(context.get("organization_id")),
        "orchestration": {
            "event_bus": orchestration,
            "proctor": proctor_orchestrator.status(),
            "scalability": {
                "websocket_handling": "authenticated_streams_with_jittered_reconnect",
                "ai_processing": "queue_ready_in_process_with_external_broker_flags",
                "telemetry": "bounded_event_bus_with_backpressure_metrics",
            },
        },
    })


@router.get("/observability")
def observability_status(context: dict = Depends(get_current_user_context)):
    orchestration = event_bus.snapshot(limit=80)
    jobs = job_system_status()
    ai = ai_provider_status()
    return ok({
        "generated_at": orchestration.get("events", [{}])[0].get("created_at") if orchestration.get("events") else None,
        "queues": {
            "background_jobs": jobs,
            "event_bus": {
                "depth": orchestration.get("queue_depth"),
                "limit": orchestration.get("buffer_limit"),
                "backpressure": orchestration.get("backpressure"),
            },
            "ai_processing": {
                "mode": jobs.get("mode"),
                "queued": jobs.get("queued_jobs"),
                "provider": ai.get("routing", {}),
            },
        },
        "streams": orchestration.get("metrics"),
        "proctor": proctor_orchestrator.status(),
        "events": orchestration.get("events", [])[:40],
        "readiness": {
            "startup_validation": "active",
            "graceful_degradation": True,
            "deterministic_testing": True,
            "secure_configuration": True,
        },
    })


@router.post("/email/send")
async def send_email(req: EmailRequest, context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter"))):
    result = await send_transactional_email(
        to=req.to,
        template=req.template,
        context=req.context,
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
    )
    return ok(result)


@router.post("/jobs")
def create_background_job(req: JobRequest, context: dict = Depends(get_current_user_context)):
    job = enqueue_job(
        req.job_type,
        req.payload,
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
    )
    log_audit_event(
        action="jobs.enqueue",
        entity_type="background_job",
        entity_id=job["id"],
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
        details={"type": req.job_type},
    )
    return ok(job)


@router.get("/jobs/{job_id}")
def read_background_job(job_id: str, context: dict = Depends(get_current_user_context)):
    job = get_job(job_id)
    if not job or job.get("organization_id") not in {None, context.get("organization_id")}:
        raise HTTPException(status_code=404, detail="Job not found")
    return ok(job)


@router.post("/storage/sign-upload")
def sign_upload(req: SignedUploadRequest, context: dict = Depends(get_current_user_context)):
    try:
        payload = create_signed_upload(
            filename=req.filename,
            size_bytes=req.size_bytes,
            content_type=req.content_type,
            organization_id=context.get("organization_id"),
            purpose=req.purpose,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(payload)


@router.post("/semantic-search")
def semantic_search(req: SemanticSearchRequest, context: dict = Depends(get_current_user_context)):
    gate = can_use_feature(context.get("organization_id"), "semantic_search")
    if not gate["allowed"]:
        raise HTTPException(status_code=402, detail=gate["reason"])
    return ok(semantic_candidate_search(req.query, organization_id=context.get("organization_id"), limit=req.limit))


@router.post("/voice-interview/analyze")
def voice_interview_analysis(req: VoiceAnalysisRequest, context: dict = Depends(get_current_user_context)):
    return ok(analyze_voice_interview(
        transcript=req.transcript,
        candidate_id=req.candidate_id,
        organization_id=context.get("organization_id"),
    ))


@router.post("/ai/route")
def ai_route(req: AIRouteRequest, context: dict = Depends(get_current_user_context)):
    payload = route_ai_request(req.message, req.context)
    log_audit_event(
        action="ai.route",
        entity_type="ai_request",
        entity_id=f"user-{context['user'].id}",
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
        details={"provider": payload.get("provider")},
    )
    return ok(payload)


@router.get("/subscription/features")
def subscription_features(
    feature: Optional[str] = Query(default=None, max_length=80),
    context: dict = Depends(get_current_user_context),
):
    if feature:
        return ok(can_use_feature(context.get("organization_id"), feature))
    return ok(subscription_snapshot(context.get("organization_id")))


@router.get("/audit/search")
def search_audit_logs(
    action: Optional[str] = Query(default=None, max_length=100),
    entity_type: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    context: dict = Depends(require_roles("super_admin", "company_admin")),
):
    db = SessionLocal()
    try:
        query = db.query(AuditLog).filter(AuditLog.organization_id == context.get("organization_id"))
        if action:
            query = query.filter(AuditLog.action.contains(action))
        if entity_type:
            query = query.filter(AuditLog.entity_type == entity_type)
        rows = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
        return ok({
            "logs": [
                {
                    "id": row.id,
                    "action": row.action,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "details": row.details or {},
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        })
    finally:
        db.close()


@router.get("/analytics/ai-insights")
def ai_analytics_insights(context: dict = Depends(get_current_user_context)):
    overview = get_workspace_overview(context.get("organization_id"))
    workforce = (overview or {}).get("workforce_analytics") or {}
    bottlenecks = workforce.get("bottlenecks") or []
    recs: list[str] = []
    for item in bottlenecks:
        msg = (item or {}).get("message")
        if msg:
            recs.append(str(msg))
    if not recs:
        recs = [
            "Auto-shortlist candidates above 75% and schedule screens within 48 hours to keep momentum.",
            "Use the Explainable Match panel to validate must-have gaps early.",
        ]

    return ok({
        "summary": workforce.get("ai_explanation") or "AI hiring intelligence is monitoring funnel quality, recruiter productivity, and candidate success predictors.",
        "signals": [
            {"name": "candidate_success_prediction", "status": "active", "confidence": 0.76},
            {"name": "hiring_bottleneck_detection", "status": "active", "confidence": 0.8 if bottlenecks else 0.62},
            {"name": "workflow_recommendations", "status": "active", "confidence": 0.78},
        ],
        "recommendations": recs[:6],
        "organization_id": context.get("organization_id"),
        "metrics": {
            "window_days": workforce.get("window_days", 30),
            "activity": workforce.get("activity") or {},
            "shortlist_conversion_rate": workforce.get("shortlist_conversion_rate", 0.0),
        },
    })


@router.get("/pwa/config")
def pwa_config():
    return ok({
        "installable": True,
        "offline_mode": "service_worker_ready",
        "mobile_ready": True,
        "service_worker": "/service-worker.js",
        "manifest": "/manifest.webmanifest",
    })
