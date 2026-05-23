from fastapi import APIRouter, Depends

from backend.api.dependencies import get_current_user
from backend.api.rate_limit import ai_rate_limit
from backend.api.routes.face import FaceVerifyRequest, verify_face
from backend.api.routes.ai import (
    ChatAssistantRequest,
    ChatCandidateRequest,
    CompareRequest,
    FeedbackRequest,
    ShortlistRequest,
    SummaryRequest,
    ai_summary,
    ai_feedback,
    auto_shortlist,
    chat_assistant,
    chat_candidate,
    compare_candidates,
)


router = APIRouter(tags=["Compatibility"], dependencies=[Depends(ai_rate_limit)])


@router.post("/ai-summary")
def legacy_ai_summary(req: SummaryRequest, current_user: str = Depends(get_current_user)):
    return ai_summary(req, current_user)


@router.post("/chat")
def legacy_chat(req: ChatAssistantRequest, current_user: str = Depends(get_current_user)):
    return chat_assistant(req, current_user)


@router.post("/api/chat")
def api_chat(req: ChatAssistantRequest, current_user: str = Depends(get_current_user)):
    return chat_assistant(req, current_user)


@router.post("/chat-candidate")
def legacy_chat_candidate(req: ChatCandidateRequest, current_user: str = Depends(get_current_user)):
    return chat_candidate(req, current_user)


@router.post("/ai-feedback")
def legacy_ai_feedback(req: FeedbackRequest, current_user: str = Depends(get_current_user)):
    return ai_feedback(req, current_user)


@router.post("/compare-candidates")
def legacy_compare_candidates(req: CompareRequest, current_user: str = Depends(get_current_user)):
    return compare_candidates(req, current_user)


@router.post("/auto-shortlist")
def legacy_auto_shortlist(req: ShortlistRequest, current_user: str = Depends(get_current_user)):
    return auto_shortlist(req, current_user)


@router.post("/face-verify")
def legacy_face_verify(req: FaceVerifyRequest, current_user: str = Depends(get_current_user)):
    return verify_face(req, current_user)
