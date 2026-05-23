from fastapi import APIRouter, Depends
from pydantic import BaseModel
from backend.api.dependencies import get_current_user
from backend.api.rate_limit import ai_rate_limit
from backend.api.responses import ok
from backend.services.ai_service import ask_ai

router = APIRouter(
    prefix="/chat",
    tags=["AI Chat"],
    dependencies=[Depends(ai_rate_limit)],
)

class ChatRequest(BaseModel):
    message: str
    candidate_id: str = ""
    skills: list = []
    score: float = 0


@router.post("/candidate")
def chat_candidate(req: ChatRequest, current_user: str = Depends(get_current_user)):

    prompt = f"""
Candidate: {req.candidate_id}
Score: {req.score}
Skills: {', '.join(req.skills)}

Question:
{req.message}
"""

    result = ask_ai([{"role": "user", "content": prompt}], [])

    return ok({"reply": result})
