from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any, Optional
from uuid import uuid4
from datetime import datetime
from pydantic import BaseModel, Field
from backend.api.dependencies import get_current_user, get_current_user_context
from backend.api.rate_limit import ai_rate_limit
from backend.api.responses import ok
from backend.services.decision_service import make_decision
from backend.services.enterprise_service import increment_usage, log_audit_event
from backend.services.orchestration_service import event_bus, proctor_orchestrator
from backend.services.proctoring_service import analyze_frame, proctor_health
from backend.services.workflow_service import evaluate_workflow, persist_workflow_run

try:
    from backend.services.interview_service import InterviewService
    interview_service = InterviewService()
except Exception:
    interview_service = None

router = APIRouter(prefix="/interview", dependencies=[Depends(get_current_user), Depends(ai_rate_limit)])
INTERVIEW_SESSIONS: Dict[str, Dict[str, Any]] = {}
INTERVIEW_SESSION_ALIASES: Dict[str, str] = {}
PROCTOR_MIN_INTERVAL_SECONDS = 4.5
PROCTOR_EVENT_LIMIT = 120
INTERVIEW_SESSION_LIMIT = 300

# Pydantic models for request/response
class GenerateQuestionsRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    experience_level: str = Field(default="mid", min_length=1, max_length=40)
    interview_type: str = Field(default="mixed", min_length=1, max_length=40)
    job_description: Optional[str] = None
    candidate_skills: Optional[List[str]] = None
    resume_text: Optional[str] = Field(default=None, max_length=20000)

class QuestionResponse(BaseModel):
    id: str
    question: str
    type: str
    difficulty: str
    order: int
    estimated_time: int
    scoring_criteria: Dict[str, float]

class GenerateQuestionsResponse(BaseModel):
    questions: List[QuestionResponse]
    total_questions: int
    estimated_duration: int

class RealtimeScoreRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=10000)
    time_taken: float = Field(default=0, ge=0)
    question_type: str = "technical"


class StartInterviewRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    experience_level: str = Field(default="mid", min_length=1, max_length=40)
    interview_type: str = Field(default="mixed", min_length=1, max_length=40)
    job_description: str = Field(default="", max_length=10000)
    candidate_skills: List[str] = Field(default_factory=list)
    resume_text: str = Field(default="", max_length=20000)


class AnswerInterviewRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    candidate_id: str = Field(..., min_length=1, max_length=120)
    answer: str = Field(..., min_length=1, max_length=10000)
    time_taken: float = Field(default=0, ge=0)
    transcript: str = Field(default="", max_length=10000)


class ResultInterviewRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)


class ProctorRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    candidate_id: str = Field(..., min_length=1, max_length=120)
    image_base64: str = Field(..., min_length=20, max_length=4000000)

class EvaluationResponse(BaseModel):
    score: float
    feedback: str
    strengths: List[str]
    improvements: List[str]
    time_efficiency: str

class RealtimeScoreResponse(BaseModel):
    evaluation: EvaluationResponse

class FinalScoreRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    questions: List[Dict[str, Any]] = Field(default_factory=list)
    answers: List[Dict[str, Any]] = Field(default_factory=list)
    behavioral_data: Dict[str, Any] = Field(default_factory=dict)

class BreakdownItem(BaseModel):
    overall: float
    technical: float
    behavioral: float
    communication: float
    problem_solving: float

class FinalScoreResponse(BaseModel):
    overall_score: float
    breakdown: BreakdownItem
    recommendation: str
    insights: List[str]
    question_evaluations: List[Dict[str, Any]]
    behavioral_analysis: Dict[str, Any]


def _fallback_final_score(request: FinalScoreRequest) -> Dict[str, Any]:
    answer_lengths = [len((answer.get("answer") or "").split()) for answer in request.answers]
    avg_length = sum(answer_lengths) / len(answer_lengths) if answer_lengths else 0
    score = 65 if avg_length >= 25 else 45 if avg_length else 0
    strengths = ["Completed structured interview responses"] if request.answers else []
    weaknesses = ["Add more specific examples and measurable outcomes"] if score < 70 else ["Manual review still recommended"]
    return {
        "overall_score": score,
        "score": score,
        "breakdown": {
            "overall": score,
            "technical": score,
            "behavioral": score,
            "communication": min(100, score + 5) if score else 0,
            "problem_solving": score,
        },
        "recommendation": "Consider" if score >= 60 else "Needs Review",
        "insights": weaknesses,
        "question_evaluations": [],
        "behavioral_analysis": request.behavioral_data or {},
        "strengths": strengths,
        "weaknesses": weaknesses,
        "feedback": "Fallback interview score generated from answer completeness and response detail.",
        "skills": [],
        "missing_skills": [],
        "suggestions": weaknesses,
        "recommended_roles": [],
        "mode": "fallback",
    }


def _normalize_final_result(result: Dict[str, Any]) -> Dict[str, Any]:
    breakdown = result.get("breakdown", {})
    technical = breakdown.get("technical", 0)
    communication = breakdown.get("communication", 0)
    overall = result.get("overall_score", 0)
    hire_threshold = 80 if overall > 10 else 8
    consider_threshold = 60 if overall > 10 else 6
    result["communication_score"] = communication
    result["technical_score"] = technical
    result["recommendation"] = "hire" if overall >= hire_threshold else "consider" if overall >= consider_threshold else "reject"
    result["strengths"] = result.get("strengths", []) or [
        insight for insight in result.get("insights", []) if "strong" in insight.lower() or "good" in insight.lower()
    ][:3]
    result["weaknesses"] = result.get("weaknesses", []) or [
        insight for insight in result.get("insights", []) if "room" in insight.lower() or "need" in insight.lower() or "gap" in insight.lower()
    ][:3]
    return result


def _build_recruiter_report(final_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a recruiter-ready narrative summary from deterministic signals.
    Keeps language calm and actionable (no raw debug).
    """
    overall = float(final_payload.get("overall_score") or 0)
    technical = float(final_payload.get("technical_score") or 0)
    communication = float(final_payload.get("communication_score") or 0)
    rec = str(final_payload.get("recommendation") or "consider")
    proctor = final_payload.get("proctor_summary") or {}

    proctor_score = float(proctor.get("proctor_score") or 0)
    attention = float(proctor.get("attention_score") or 0)
    visibility = float(proctor.get("visibility_score") or 0)
    engagement = float(proctor.get("engagement_score") or 0)
    alerts = proctor.get("alerts") or []
    if not isinstance(alerts, list):
        alerts = []

    strengths = list(final_payload.get("strengths") or [])[:4]
    concerns = list(final_payload.get("weaknesses") or [])[:4]

    # Add signal-based strength/concern helpers.
    if technical >= 7.5:
        strengths.append("Strong technical depth across the interview questions.")
    if communication >= 7.5:
        strengths.append("Clear communication and structured thinking.")
    if proctor_score >= 80:
        strengths.append("Good interview presence: stable attention and framing signals.")

    if technical <= 5.5 and overall > 0:
        concerns.append("Technical depth may need validation with a deeper screen or practical exercise.")
    if communication <= 5.5 and overall > 0:
        concerns.append("Communication could be improved with more structured answers and concrete examples.")
    if any(a in {"multiple_faces", "no_face"} for a in alerts):
        concerns.append("Proctoring flags indicate the interview should be reviewed for integrity signals.")

    # Avoid duplicates while keeping order.
    def _dedupe(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for x in items:
            sx = str(x or "").strip()
            if not sx or sx.lower() in seen:
                continue
            seen.add(sx.lower())
            out.append(sx)
        return out

    strengths = _dedupe(strengths)[:4]
    concerns = _dedupe(concerns)[:4]

    if rec == "hire":
        next_step = "Proceed to a deeper technical screen or onsite loop. Focus follow-ups on edge cases and production trade-offs."
    elif rec == "consider":
        next_step = "Run a focused technical screen (30-45 min) targeting missing depth areas, plus one behavioral calibration question."
    else:
        next_step = "If still interested, re-run a narrower interview focused on fundamentals. Otherwise, close the loop with a polite rejection."

    communication_summary = (
        "Communication was clear and structured."
        if communication >= 7.5
        else "Communication was acceptable but would benefit from more structure and concrete examples."
        if communication >= 6.0
        else "Communication signals were weak; recommend probing clarity and structure in a follow-up screen."
    )

    technical_summary = (
        "Technical depth was strong, including practical trade-offs."
        if technical >= 7.5
        else "Technical depth was moderate; validate with deeper probing on design, failure modes, and metrics."
        if technical >= 6.0
        else "Technical depth appears limited; recommend verifying fundamentals with a practical exercise."
    )

    presence_summary = (
        f"Attention {round(attention, 0)}%, visibility {round(visibility, 0)}%, engagement {round(engagement, 0)}%."
        if proctor
        else "Video analysis was not available; no proctoring signals included."
    )

    return {
        "headline": f"{rec.replace('_', ' ').title()} recommendation",
        "overall_summary": f"Overall score {overall}. Technical {technical}, communication {communication}.",
        "strengths": strengths,
        "concerns": concerns,
        "communication_summary": communication_summary,
        "technical_depth_summary": technical_summary,
        "presence_summary": presence_summary,
        "next_step_recommendation": next_step,
    }


def _prune_interview_sessions() -> None:
    if len(INTERVIEW_SESSIONS) <= INTERVIEW_SESSION_LIMIT:
        return
    protected = set(INTERVIEW_SESSION_ALIASES.values())
    ordered = sorted(
        INTERVIEW_SESSIONS.items(),
        key=lambda item: str(item[1].get("updated_at") or item[1].get("created_at") or ""),
    )
    for key, _ in ordered[: max(0, len(INTERVIEW_SESSIONS) - INTERVIEW_SESSION_LIMIT)]:
        if key in protected:
            continue
        INTERVIEW_SESSIONS.pop(key, None)


def _session_payload(session_id: str, candidate_id: Optional[str] = None) -> Dict[str, Any]:
    session = INTERVIEW_SESSIONS.get(session_id)
    if not session and session_id in INTERVIEW_SESSION_ALIASES:
        session = INTERVIEW_SESSIONS.get(INTERVIEW_SESSION_ALIASES[session_id])
    if not session and candidate_id:
        latest_id = INTERVIEW_SESSION_ALIASES.get(candidate_id)
        session = INTERVIEW_SESSIONS.get(latest_id or "")
    if not session:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return session


def _fallback_questions(request: GenerateQuestionsRequest) -> List[Dict[str, Any]]:
    seed_skills = request.candidate_skills or ["Python", "APIs", "Problem Solving"]
    base_questions = [
        {
            "id": "q_1",
            "question": f"Walk me through a project where you used {seed_skills[0]}. What problem were you solving?",
            "type": "technical",
            "difficulty": request.experience_level,
            "order": 1,
            "estimated_time": 180,
            "scoring_criteria": {"accuracy": 0.3, "depth": 0.3, "clarity": 0.2, "relevance": 0.2},
        },
        {
            "id": "q_2",
            "question": "Tell me about a time you had to resolve ambiguity or conflict in a team setting.",
            "type": "behavioral",
            "difficulty": "medium",
            "order": 2,
            "estimated_time": 180,
            "scoring_criteria": {"relevance": 0.3, "specificity": 0.3, "reflection": 0.2, "clarity": 0.2},
        },
        {
            "id": "q_3",
            "question": "Imagine a production issue appears after deployment. How would you investigate and stabilize it?",
            "type": "scenario-based",
            "difficulty": "hard",
            "order": 3,
            "estimated_time": 180,
            "scoring_criteria": {"accuracy": 0.25, "depth": 0.3, "clarity": 0.2, "relevance": 0.25},
        },
    ]
    return base_questions


def _next_question_payload(session: Dict[str, Any]) -> Dict[str, Any]:
    current_index = session.get("current_index", 0)
    questions = session.get("questions", [])
    if current_index >= len(questions):
        return {"completed": True, "question": None}
    return {"completed": False, "question": questions[current_index], "progress": current_index + 1, "total": len(questions)}


@router.post("/start")
async def start_interview(request: StartInterviewRequest):
    session_id = f"session_{request.candidate_id}_{uuid4().hex[:10]}"
    generate_request = GenerateQuestionsRequest(
        candidate_id=request.candidate_id,
        experience_level=request.experience_level,
        interview_type=request.interview_type,
        job_description=request.job_description,
        candidate_skills=request.candidate_skills,
        resume_text=request.resume_text,
    )

    generated = await generate_questions(generate_request)
    payload = generated["data"]
    INTERVIEW_SESSIONS[session_id] = {
        "session_id": session_id,
        "candidate_id": request.candidate_id,
        "status": "active",
        "questions": payload.get("questions", []),
        "answers": [],
        "current_index": 0,
        "job_description": request.job_description,
        "proctor_events": [],
        "live_scores": [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "orchestration": {
            "mode": "adaptive_frame_queue",
            "created_at": datetime.utcnow().isoformat(),
        },
    }
    INTERVIEW_SESSION_ALIASES[request.candidate_id] = session_id
    _prune_interview_sessions()
    event_bus.publish("interview", "session.started", {"session_id": session_id, "candidate_id": request.candidate_id})
    next_payload = _next_question_payload(INTERVIEW_SESSIONS[session_id])
    return ok({
        "session_id": session_id,
        "question": next_payload.get("question"),
        "progress": next_payload.get("progress", 0),
        "total": next_payload.get("total", 0),
        "status": "active",
    })


@router.post("/answer")
async def answer_interview(request: AnswerInterviewRequest):
    session = _session_payload(request.session_id, request.candidate_id)
    if session.get("candidate_id") != request.candidate_id:
        raise HTTPException(status_code=400, detail="Candidate does not match interview session")

    current_index = session.get("current_index", 0)
    questions = session.get("questions", [])
    if current_index >= len(questions):
        final_request = FinalScoreRequest(
            candidate_id=request.candidate_id,
            questions=questions,
            answers=session.get("answers", []),
            behavioral_data={},
        )
        return ok({"completed": True, "result": _normalize_final_result(_fallback_final_score(final_request))})

    current_question = questions[current_index]
    evaluation = interview_service.evaluate_answer(
        current_question.get("question", ""),
        request.answer,
        request.time_taken,
    ) if interview_service else {
        "score": 5,
        "feedback": "Fallback answer evaluation generated.",
        "strengths": ["Answered the question"],
        "improvements": ["Add more detail and examples"],
        "time_efficiency": "N/A",
    }
    live = interview_service.evaluate_live_answer(
        current_question.get("question", ""),
        request.transcript or request.answer,
    ) if interview_service else {
        "live_score": 50,
        "keywords_detected": [],
        "confidence_level": "medium",
        "good_signals": [],
        "risk_signals": [],
    }

    session["answers"].append({
        "question_id": current_question.get("id"),
        "question_text": current_question.get("question", ""),
        "answer": request.answer,
        "time_taken": request.time_taken,
        "evaluation": evaluation,
        "live": live,
    })
    session["updated_at"] = datetime.utcnow().isoformat()
    session.setdefault("live_scores", []).append(live.get("live_score", 0))

    follow_up = None
    if interview_service:
        follow_up = interview_service.generate_follow_up_question(
            current_question.get("question", ""),
            request.answer,
            float(evaluation.get("score", 0)),
            current_question.get("type", "technical"),
        )
    if follow_up:
        follow_up.update({
            "id": f"follow_up_{len(questions) + 1}",
            "order": current_index + 2,
            "estimated_time": 120,
            "scoring_criteria": current_question.get("scoring_criteria", {}),
        })
        session["questions"].insert(current_index + 1, follow_up)
        questions = session["questions"]

    session["current_index"] = current_index + 1
    next_payload = _next_question_payload(session)

    if next_payload.get("completed"):
        final_request = FinalScoreRequest(
            candidate_id=request.candidate_id,
            questions=session.get("questions", []),
            answers=session.get("answers", []),
            behavioral_data={
                "response_times": [answer.get("time_taken", 0) for answer in session.get("answers", [])],
                "answer_lengths": [len((answer.get("answer") or "").split()) for answer in session.get("answers", [])],
            },
        )
        result = await final_score(final_request)
        session["status"] = "completed"
        final_payload = _normalize_final_result(result["data"])
        final_payload["proctor_summary"] = _build_proctor_summary(session)
        final_payload["recruiter_report"] = _build_recruiter_report(final_payload)
        final_payload["decision_intelligence"] = make_decision(
            match_score=final_payload.get("overall_score", 0) * 10 if final_payload.get("overall_score", 0) <= 10 else final_payload.get("overall_score", 0),
            interview_score=final_payload.get("overall_score", 0) * 10 if final_payload.get("overall_score", 0) <= 10 else final_payload.get("overall_score", 0),
            communication_score=final_payload.get("communication_score", 0) * 10 if final_payload.get("communication_score", 0) <= 10 else final_payload.get("communication_score", 0),
            proctor_score=final_payload["proctor_summary"].get("proctor_score", 100),
            missing_skills=final_payload.get("missing_skills", []),
        )
        return ok({
            "completed": True,
            "score": evaluation.get("score", 0),
            "feedback": evaluation.get("feedback", ""),
            "confidence": round(min(1.0, max(0.0, float(evaluation.get("score", 0)) / 10)), 2),
            "live_score": live.get("live_score", 0),
            "keywords_detected": live.get("keywords_detected", []),
            "confidence_level": live.get("confidence_level", "low"),
            "result": final_payload,
        })

    return ok({
        "completed": False,
        "score": evaluation.get("score", 0),
        "feedback": evaluation.get("feedback", ""),
        "confidence": round(min(1.0, max(0.0, float(evaluation.get("score", 0)) / 10)), 2),
        "live_score": live.get("live_score", 0),
        "keywords_detected": live.get("keywords_detected", []),
        "confidence_level": live.get("confidence_level", "low"),
        "next_question": next_payload.get("question"),
        "progress": next_payload.get("progress", 0),
        "total": next_payload.get("total", 0),
    })


@router.get("/result")
async def interview_result(session_id: str):
    session = _session_payload(session_id)
    final_request = FinalScoreRequest(
        candidate_id=session.get("candidate_id", ""),
        questions=session.get("questions", []),
        answers=session.get("answers", []),
        behavioral_data={
            "response_times": [answer.get("time_taken", 0) for answer in session.get("answers", [])],
            "answer_lengths": [len((answer.get("answer") or "").split()) for answer in session.get("answers", [])],
        },
    )
    result = await final_score(final_request)
    final_payload = _normalize_final_result(result["data"])
    final_payload["proctor_summary"] = _build_proctor_summary(session)
    final_payload["recruiter_report"] = _build_recruiter_report(final_payload)
    final_payload["decision_intelligence"] = make_decision(
        match_score=final_payload.get("overall_score", 0) * 10 if final_payload.get("overall_score", 0) <= 10 else final_payload.get("overall_score", 0),
        interview_score=final_payload.get("overall_score", 0) * 10 if final_payload.get("overall_score", 0) <= 10 else final_payload.get("overall_score", 0),
        communication_score=final_payload.get("communication_score", 0) * 10 if final_payload.get("communication_score", 0) <= 10 else final_payload.get("communication_score", 0),
        proctor_score=final_payload["proctor_summary"].get("proctor_score", 100),
        missing_skills=final_payload.get("missing_skills", []),
    )
    return ok(final_payload)

@router.post("/generate")
@router.post("/generate-questions")
async def generate_questions(request: GenerateQuestionsRequest):
    """Generate personalized interview questions"""
    try:
        if not interview_service:
            questions = _fallback_questions(request)
        else:
            questions = interview_service.generate_questions(
                candidate_id=request.candidate_id,
                job_description=request.job_description,
                candidate_skills=request.candidate_skills,
                experience_level=request.experience_level,
                interview_type=request.interview_type
            )

        # Calculate estimated duration (3 minutes per question)
        estimated_duration = len(questions) * 180

        payload = GenerateQuestionsResponse(
            questions=questions,
            total_questions=len(questions),
            estimated_duration=estimated_duration
        ).model_dump()
        INTERVIEW_SESSIONS[request.candidate_id] = {
            "candidate_id": request.candidate_id,
            "status": "questions_generated",
            "questions": payload["questions"],
            "answers": [],
        }
        return ok(payload)

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to generate questions")

@router.post("/realtime-score")
async def realtime_score(request: RealtimeScoreRequest):
    """Evaluate individual answer in real-time"""
    try:
        if not interview_service:
            raise HTTPException(status_code=500, detail="Interview service not available")

        evaluation = interview_service.evaluate_answer(
            question=request.question,
            answer=request.answer,
            time_taken=request.time_taken
        )
        live = interview_service.evaluate_live_answer(
            request.question,
            request.answer,
        ) if interview_service else {
            "live_score": 50,
            "keywords_detected": [],
            "confidence_level": "medium",
            "good_signals": [],
            "risk_signals": [],
        }

        return ok({
            **RealtimeScoreResponse(evaluation=evaluation).model_dump(),
            **live,
        })

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to evaluate answer")

@router.post("/score")
async def final_score(request: FinalScoreRequest):
    """Calculate comprehensive final interview score"""
    try:
        if not interview_service:
            return ok(_fallback_final_score(request))

        result = interview_service.calculate_final_score(
            questions=request.questions,
            answers=request.answers,
            behavioral_data=request.behavioral_data
        )
        if not result or not result.get("breakdown"):
            result = _fallback_final_score(request)
        else:
            result["score"] = result.get("overall_score", 0)
            result["strengths"] = result.get("strengths", [])
            result["weaknesses"] = result.get("weaknesses", result.get("insights", []))
            result["feedback"] = result.get("feedback", result.get("recommendation", "Interview scoring complete."))
            result["skills"] = result.get("skills", [])
            result["missing_skills"] = result.get("missing_skills", [])
            result["suggestions"] = result.get("suggestions", result.get("insights", []))
            result["recommended_roles"] = result.get("recommended_roles", [])
            result["mode"] = "openai" if getattr(interview_service, "openai_service", None) else "fallback"
        result = _normalize_final_result(result)

        INTERVIEW_SESSIONS[request.candidate_id] = {
            "candidate_id": request.candidate_id,
            "status": "scored",
            "questions": request.questions,
            "answers": request.answers,
            "result": result,
        }
        return ok(result)

    except Exception:
        return ok(_normalize_final_result(_fallback_final_score(request)))

@router.get("/health")
async def interview_health():
    """Health check for interview service"""
    return ok({
        "status": "healthy",
        "service": "interview",
        "openai_available": interview_service.openai_service is not None if interview_service else False,
        "vision": proctor_health(),
        "orchestration": {
            **proctor_orchestrator.status(),
            "event_bus": event_bus.snapshot(limit=10).get("backpressure"),
        },
        # Camera access is browser-side; this flag signals server readiness for frame analysis.
        "camera_analysis": {
            "client_side": True,
            "server_side_frame_analysis": bool(proctor_health().get("opencv_available")),
        },
    })


@router.get("/status/{session_id}")
async def interview_status(session_id: str):
    """Polling-ready interview session state. Uses candidate/session id until persistent sessions are added."""
    resolved = INTERVIEW_SESSION_ALIASES.get(session_id, session_id)
    return ok(INTERVIEW_SESSIONS.get(resolved, {
        "candidate_id": session_id,
        "status": "not_started",
        "questions": [],
        "answers": [],
    }))


def _build_proctor_summary(session: Dict[str, Any]) -> Dict[str, Any]:
    events = session.get("proctor_events", [])
    if not events:
        return {
            "attention_score": 100.0,
            "visibility_score": 100.0,
            "engagement_score": 100.0,
            "cheating_flag": False,
            "alerts": [],
            "proctor_score": 100.0,
        }

    attention_scores = [event.get("attention_score", 0) for event in events]
    visibility_scores = [event.get("visibility_score", 0) for event in events]
    engagement_scores = [event.get("engagement_score", 0) for event in events]
    integrity_scores = [event.get("integrity_score", 0) for event in events]
    all_alerts = []
    cheating_flag = False
    for event in events:
        all_alerts.extend(event.get("alerts", []))
        cheating_flag = cheating_flag or bool(event.get("cheating_flag"))

    avg_attention = round(sum(attention_scores) / len(attention_scores), 2) if attention_scores else 0.0
    avg_visibility = round(sum(visibility_scores) / len(visibility_scores), 2) if visibility_scores else 0.0
    avg_engagement = round(sum(engagement_scores) / len(engagement_scores), 2) if engagement_scores else 0.0
    avg_integrity = round(sum(integrity_scores) / len(integrity_scores), 2) if integrity_scores else avg_attention

    # Use integrity as the main proctor score, with a small penalty for cheating flags.
    proctor_score = round(max(0.0, min(100.0, avg_integrity - (14.0 if cheating_flag else 0.0))), 2)
    return {
        "attention_score": avg_attention,
        "visibility_score": avg_visibility,
        "engagement_score": avg_engagement,
        "cheating_flag": cheating_flag,
        "alerts": sorted(set(all_alerts)),
        "proctor_score": proctor_score,
    }


@router.post("/proctor")
async def interview_proctor(request: ProctorRequest, context: dict = Depends(get_current_user_context)):
    session = _session_payload(request.session_id, request.candidate_id)
    if session.get("candidate_id") != request.candidate_id:
        raise HTTPException(status_code=400, detail="Candidate does not match interview session")

    analysis = proctor_orchestrator.process(
        session_id=request.session_id,
        candidate_id=request.candidate_id,
        image_base64=request.image_base64,
        analyzer=analyze_frame,
    )
    import time
    now = time.time()
    if not analysis.get("throttled"):
        increment_usage(context.get("organization_id"), "ai_calls", 1)
    session["last_proctor_at"] = now
    session["last_proctor_result"] = analysis
    events = session.setdefault("proctor_events", [])
    if not analysis.get("throttled"):
        events.append(analysis)
    if len(events) > PROCTOR_EVENT_LIMIT:
        del events[:-PROCTOR_EVENT_LIMIT]
    summary = _build_proctor_summary(session)
    event_bus.publish(
        "interview.proctor",
        "frame.throttled" if analysis.get("throttled") else "frame.processed",
        {
            "session_id": request.session_id,
            "candidate_id": request.candidate_id,
            "alerts": analysis.get("alerts", []),
            "next_sample_seconds": analysis.get("next_sample_seconds"),
        },
        severity="warning" if analysis.get("alerts") else "info",
    )
    if not analysis.get("throttled"):
        log_audit_event(
            action="interview.proctor",
            entity_type="interview_session",
            entity_id=request.session_id,
            organization_id=context.get("organization_id"),
            user_id=context["user"].id,
            details=analysis,
        )
    return ok({
        **analysis,
        "proctor_score": summary.get("proctor_score", analysis.get("integrity_score", 0)),
        "proctor_summary": summary,
    })
