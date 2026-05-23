from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.api.responses import ok
from backend.api.routes.ai import process_face_analysis
from backend.services.proctoring_service import analyze_frame

router = APIRouter(prefix="/face", tags=["Face Verification"])


class FaceVerifyRequest(BaseModel):
    image_base64: str = Field(..., min_length=20, max_length=4000000)
    candidate_id: str = Field(default="", max_length=120)


@router.post("/verify")
def verify_face(req: FaceVerifyRequest, current_user: str = Depends(get_current_user)):
    analysis = process_face_analysis(req.image_base64)
    proctor = analyze_frame(req.image_base64)
    verified = bool(analysis.get("face_detected") or proctor.get("face_count"))
    return ok({
        "verified": verified,
        "candidate_id": req.candidate_id,
        "face_detected": analysis.get("face_detected", bool(proctor.get("face_count"))),
        "attention": analysis.get("attention", "unknown"),
        "confidence": analysis.get("confidence", 0.0),
        "attention_score": proctor.get("attention_score", 0),
        "emotion": proctor.get("emotion", "neutral"),
        "cheating_flag": proctor.get("cheating_flag", False),
        "alerts": proctor.get("alerts", []),
        "message": "Face verified" if verified else "No face detected in the capture",
    })
