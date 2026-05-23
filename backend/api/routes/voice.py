from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.services.enterprise_service import increment_usage, log_audit_event
from backend.services.voice_service import enroll_voice_profile, verify_voice_profile


router = APIRouter(prefix="/voice", tags=["Voice"])


class VoicePayload(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    audio_base64: str = Field(..., min_length=20, max_length=8000000)
    sample_name: str = Field(default="", max_length=120)


@router.post("/enroll")
def enroll(req: VoicePayload, context: dict = Depends(get_current_user_context)):
    increment_usage(context.get("organization_id"), "ai_calls", 1)
    payload = enroll_voice_profile(context["user"].id, req.candidate_id, req.audio_base64, req.sample_name)
    log_audit_event(
        action="voice.enroll",
        entity_type="candidate",
        entity_id=req.candidate_id,
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
        details={"sample_name": req.sample_name},
    )
    return ok({
        "identity_verified": True,
        "confidence": 100.0,
        **payload,
    })


@router.post("/verify")
def verify(req: VoicePayload, context: dict = Depends(get_current_user_context)):
    increment_usage(context.get("organization_id"), "ai_calls", 1)
    payload = verify_voice_profile(context["user"].id, req.candidate_id, req.audio_base64)
    if not payload:
        raise HTTPException(status_code=400, detail="Voice verification failed")
    log_audit_event(
        action="voice.verify",
        entity_type="candidate",
        entity_id=req.candidate_id,
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
        details={"confidence": payload.get("confidence", 0)},
    )
    return ok(payload)
