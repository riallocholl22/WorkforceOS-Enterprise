import asyncio
import base64
import json
import secrets
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.api.rate_limit import ai_rate_limit
from backend.api.responses import ok
from backend.db.database import ChatHistory, SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.services.ai_feedback_service import ai_feedback_service
from backend.services.ai_service import ask_ai, stream_ai
from backend.services.auth_service import get_user_context_from_token
from backend.services.enterprise_service import log_audit_event
from backend.services.ai_memory_service import memory_context_note

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - dependency fallback
    cv2 = None
    np = None

router = APIRouter(prefix="/ai", tags=["AI"], dependencies=[Depends(ai_rate_limit)])

# Pending copilot actions (confirmation-gated). Stored in-process as a short-lived cache.
# This keeps the feature lightweight and avoids introducing a new persistence layer.
PENDING_COPILOT_ACTIONS: dict[str, dict[str, Any]] = {}
PENDING_ACTION_TTL_SEC = 15 * 60


class SummaryRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    score: float = 0.0
    skills: List[str] = Field(default_factory=list)


class ChatCandidateRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    candidate_id: str = ""
    score: float = 0.0
    skills: List[str] = Field(default_factory=list)


class CompareRequest(BaseModel):
    candidate1: Dict[str, Any] = Field(default_factory=dict)
    candidate2: Dict[str, Any] = Field(default_factory=dict)


class ShortlistRequest(BaseModel):
    candidates: List[Dict[str, Any]] = Field(..., min_length=1)


class DecisionRequest(BaseModel):
    candidates: List[Dict[str, Any]] = Field(..., min_length=1)
    job_description: str = Field(default="", max_length=10000)


class FeedbackRequest(BaseModel):
    resume_text: str = Field(..., min_length=10, max_length=20000)
    job_description: str = Field(default="", max_length=10000)


class ChatAssistantRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class InterviewLiveRequest(BaseModel):
    image_base64: str = Field(default="", max_length=4000000)
    audio_base64: str = Field(default="", max_length=4000000)
    session_id: str = Field(default="", max_length=120)
    current_question: str = Field(default="", max_length=2000)
    candidate_response: str = Field(default="", max_length=10000)


class InterviewEvaluateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    candidate_id: str = Field(..., min_length=1, max_length=120)
    match_score: float = 0.0
    interview_responses: List[Dict[str, Any]] = Field(default_factory=list)
    behavioral_metrics: Dict[str, Any] = Field(default_factory=dict)


def format_skills(skills: List[str]) -> str:
    return ", ".join(skills) if skills else "N/A"


def _stored_candidates(limit: int = 25) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(Candidate).order_by(Candidate.created_at.desc()).limit(limit).all()
        return [
            {
                "candidate_id": row.candidate_id,
                "skills": row.skills or [],
                "score": 0,
                "text_snippet": (row.text or "")[:240],
            }
            for row in rows
        ]
    finally:
        db.close()


def _fallback_chat(message: str, candidates: List[Dict[str, Any]]) -> str:
    msg = message.lower()
    frustrated = any(term in msg for term in ["frustrated", "annoyed", "stuck", "broken", "not working", "error", "failed", "confused"])
    excited = any(term in msg for term in ["great", "nice", "awesome", "excited", "perfect", "it worked"])
    tone_prefix = (
        "I know this can be frustrating. Let's make it smaller and handle the next practical step.\n\n"
        if frustrated
        else "Nice, that is a good moment to build on.\n\n"
        if excited
        else ""
    )
    if _is_disallowed_ai_request(msg):
        return (
            "I can't help with malware, credential theft, unauthorized access, secret extraction, or evading security controls.\n\n"
            "I can help defensively instead: hardening FastAPI, reviewing auth flows, building incident response steps, or writing safe detection logic."
        )
    if msg.startswith("/compare"):
        if len(candidates) >= 2:
            top = sorted(candidates, key=lambda c: c.get("match_score", c.get("score", 0)) or 0, reverse=True)[:3]
            return (
                tone_prefix
                + "**Candidate comparison**\n\n"
                + "\n".join(
                    f"- **{item.get('candidate_id', 'Candidate')}**: score {item.get('match_score', item.get('score', 0))}; "
                    f"matched skills: {', '.join(item.get('matched_skills', item.get('skills', []))[:5]) or 'not available'}; "
                    f"gaps: {', '.join(item.get('missing_skills', [])[:4]) or 'none visible'}"
                    for item in top
                )
                + "\n\nMy read: prioritize the highest score only if the missing skills are trainable and the role risk is acceptable. For senior or high-risk roles, probe every critical gap before shortlisting."
            )
        return tone_prefix + "Upload and match at least two candidates, then use `/compare` again and I will give you a ranked comparison with strengths, gaps, and next steps."
    if msg.startswith("/shortlist"):
        if candidates:
            top = sorted(candidates, key=lambda c: c.get("match_score", c.get("score", 0)) or 0, reverse=True)[:5]
            lines = "\n".join(
                f"- **{item.get('candidate_id', 'Candidate')}**: {item.get('match_score', item.get('score', 0))}%"
                + (f" | reason: {item.get('explanation')}" if item.get("explanation") else "")
                for item in top
            )
            return (
                tone_prefix
                + "**Suggested shortlist (based on current match signals)**\n\n"
                + lines
                + "\n\nIf you tell me your threshold (for example `>= 75`) I can recommend who to approve, who to keep as a backup, and what to probe in interviews."
            )
        return tone_prefix + "Run matching first so I can suggest a shortlist from real resume and job signals."
    if msg.startswith("/explain shortlist"):
        return (
            tone_prefix
            + "A good shortlist is evidence-first: match score and skill overlap are the starting signal, then you sanity-check risk.\n\n"
            + "My default rubric:\n"
            + "1. Role-critical skills present (or trainable gaps).\n"
            + "2. Recent experience relevance (projects, scope, ownership).\n"
            + "3. Clear impact and decision-making.\n"
            + "4. Interview probes aligned to the top gaps.\n\n"
            + "If you paste the job requirements (or run matching), I will explain why each shortlisted candidate made it and what could disqualify them."
        )
    if msg.startswith("/reject"):
        return tone_prefix + "Tell me the minimum threshold (for example `score < 55` or `missing critical skills`) and I will flag the weak matches and suggest who to reject versus who to keep as backups."
    if msg.startswith("/generate shortlist report"):
        return tone_prefix + "I can draft a recruiter-facing shortlist report (top candidates, strengths, risks, interview probes, and recommendation). Share the job description and the shortlisted candidate IDs."
    if msg.startswith("/interview"):
        return (
            tone_prefix
            + "**Interview question set**\n\n"
            "1. Walk me through a project that best matches this role.\n"
            "2. What technical tradeoff did you make recently, and why?\n"
            "3. How would you debug a production issue with limited logs?\n"
            "4. Tell me about a time you influenced a team decision.\n"
            "5. What would you learn first in your first 30 days here?\n\n"
            "Use follow-ups to probe depth, ownership, communication, and risk awareness. Keep the same scoring rubric across candidates so the final decision stays fair."
        )
    if msg.startswith("/pipeline"):
        return (
            tone_prefix
            + "**Hiring pipeline guidance**\n\n"
            "- Review sourcing volume and candidate quality first.\n"
            "- Use AI matching to shortlist candidates with explainable skill overlap.\n"
            "- Run structured interviews with consistent scoring.\n"
            "- Track conversion, time-to-hire, interview pass rate, and rejected-stage reasons.\n"
            "- Keep recruiter notes tied to objective evidence.\n\n"
            "The useful first distinction is whether the bottleneck is volume, quality, scheduling, or decision latency."
        )
    if msg.startswith("/debug"):
        return (
            "**Debug shortcut**\n\n"
            "Share the failing URL, request payload, response status, browser console error, and backend traceback. "
            "I will help isolate whether the issue is routing, CORS, auth, database state, frontend rendering, or provider configuration."
        )
    if any(term in msg for term in ["support", "contact", "help desk", "customer service"]):
        return tone_prefix + "For platform help or account support, contact **marialcholagudi@gmail.com**. I can also help debug workflows, explain API failures, or draft recruiting and enterprise operations steps here."
    if any(term in msg for term in ["cors", "network", "axios", "connection refused", "404", "not found", "blank"]):
        return tone_prefix + (
            "**Here is the fastest way to debug it:**\n\n"
            "1. Confirm FastAPI is running at `http://127.0.0.1:8000`.\n"
            "2. Open `/health` and `/openapi.json` to verify the backend is alive and routes are registered.\n"
            "3. Confirm the frontend API base uses the same backend origin.\n"
            "4. For CORS, allow the exact frontend origin and credentials.\n"
            "5. For 404s, check Swagger and make sure router prefixes are not duplicated.\n\n"
            "If the UI goes blank, keep rendering an empty/error state and log the failed endpoint, status code, and response body."
        )
    if any(term in msg for term in ["code", "debug", "bug", "fastapi", "react", "api"]):
        return (
            "**A solid debugging path:**\n\n"
            "- Reproduce the failure and capture the exact endpoint, payload, status code, and console traceback.\n"
            "- Check backend route registration in `/openapi.json`.\n"
            "- Verify auth headers and token expiry before blaming CORS.\n"
            "- Add a frontend fallback state so the component never renders blank.\n"
            "- For FastAPI, isolate dependencies first: auth dependency, DB session, then service logic.\n\n"
            "Paste the error or code snippet and I can walk through it precisely."
        )
    if any(term in msg for term in ["enterprise", "billing", "team", "security", "scheduling"]):
        return tone_prefix + (
            "**Enterprise workflow triage:**\n\n"
            "Start with workspace health, then verify team membership, billing plan state, security overview, and scheduling queues. "
            "If one module fails, keep the dashboard usable by rendering a partial state and surfacing the failed endpoint clearly."
        )
    if any(term in msg for term in ["sqlalchemy", "sql", "database", "postgres", "sqlite"]):
        return tone_prefix + (
            "For database issues, check the SQLAlchemy session lifecycle, migrations/table creation, query filters, and whether the request is scoped to the correct organization. "
            "Use parameterized ORM queries, avoid string-built SQL, and log failures without exposing sensitive data."
        )
    if any(term in msg for term in ["mongo", "mongodb", "nosql"]):
        return tone_prefix + (
            "For MongoDB, validate payload shape before querying, reject operator injection keys like `$where`, and use explicit query builders. "
            "Check connection URI, server selection timeout, indexes, and tenant scoping."
        )
    if any(term in msg for term in ["deploy", "production", "devops", "docker", "server"]):
        return tone_prefix + (
            "For deployment, verify environment variables, run health checks, keep secrets server-side, enable HTTPS at the edge, and use structured logs for startup, requests, auth failures, and AI provider errors. "
            "A clean production path is: build frontend, run FastAPI behind a reverse proxy, configure CORS to the deployed frontend origin, and monitor `/health`."
        )
    if "best" in msg or "top" in msg:
        if candidates:
            best = max(candidates, key=lambda c: c.get("match_score", c.get("score", 0)) or 0)
            return tone_prefix + f"The strongest visible candidate is **{best.get('candidate_id')}** with score **{best.get('match_score', best.get('score', 0))}**. I would still review matched skills, missing skills, and role-critical gaps before making the final call."
        return tone_prefix + "Run matching first so I can identify the strongest candidates from actual resume and job signals."
    if "why" in msg or "explain" in msg:
        return tone_prefix + "Candidates are ranked using resume/job text similarity, explicit skill overlap, and experience signals. The match panel shows matched skills, missing skills, and a plain-language explanation so you can defend the shortlist instead of treating the score as a black box."
    if "interview" in msg:
        return tone_prefix + "Use the AI Interview view to generate role-based questions, capture answers, and score the interview consistently. For stronger signal, ask one technical depth question, one tradeoff question, and one collaboration question for every finalist."
    return tone_prefix + (
        "I can help with AI recruitment workflows, candidate matching, interview automation, FastAPI and React debugging, deployment, database issues, and defensive cybersecurity. "
        "Tell me what you are trying to solve, and I will give you a practical next step."
    )


def _is_disallowed_ai_request(message: str) -> bool:
    blocked_terms = [
        "malware",
        "ransomware",
        "keylogger",
        "steal password",
        "steal credentials",
        "credential theft",
        "phishing kit",
        "bypass login",
        "bypass authentication",
        "dump tokens",
        "exfiltrate",
        "reverse shell",
        "privilege escalation exploit",
        "disable antivirus",
        "evade detection",
        "prompt injection to reveal",
        "ignore previous instructions",
        "show me secrets",
        "leak api key",
    ]
    return any(term in message for term in blocked_terms)


def _fallback_chat_payload(message: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    skills = sorted({
        skill
        for candidate in candidates
        for skill in candidate.get("skills", [])
        if isinstance(skill, str) and skill
    })[:12]
    missing_skills = sorted({
        skill
        for candidate in candidates
        for skill in candidate.get("missing_skills", [])
        if isinstance(skill, str) and skill
    })[:12]
    base_response = _fallback_chat(message, candidates)
    trust_note = (
        "\n\n**Why I'm recommending this**\n"
        "- Reasoning: I'm using the current candidate list, visible match scores, recruiter context, and workflow state available in this session.\n"
        "- Confidence: Medium when candidate/job evidence is present; lower when the workspace has limited data.\n"
        "- Uncertainty: Treat this as decision support, not an automated hiring decision.\n"
        "- Suggested action: Validate critical gaps with structured interviews and keep recruiter notes tied to evidence."
    )
    response = base_response if "Why I'm recommending this" in base_response else base_response + trust_note
    return {
        "response": response,
        "reply": response,
        "skills": skills,
        "missing_skills": missing_skills,
        "suggestions": [
            "Review the explainable match panel before shortlisting.",
            "Interview candidates with strong matched skills and manageable gaps.",
        ],
        "recommended_roles": ["Recruiter Assistant"],
        "mode": "local-fallback",
    }


def _safe_ai(prompt: str, candidates: List[Dict[str, Any]] | None = None) -> str:
    try:
        result = ask_ai([{"role": "user", "content": prompt}], candidates or [])
        return result or "AI response unavailable."
    except Exception:
        return "AI service is temporarily unavailable."


def _recent_chat_context(session_id: str = "default", limit: int = 8) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatHistory)
            .filter(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.id.desc())
            .limit(limit)
            .all()
        )
        return [{"role": row.role, "content": row.content} for row in reversed(rows)]
    finally:
        db.close()


def _remember_chat(role: str, content: str, session_id: str = "default") -> None:
    db = SessionLocal()
    try:
        db.add(ChatHistory(session_id=session_id, role=role, content=content[:4000]))
        db.commit()
    finally:
        db.close()


def _chat_history_for_model(message: str, session_id: str) -> list[dict]:
    recent = _recent_chat_context(session_id, limit=14)
    if recent and recent[-1]["role"] == "user" and recent[-1]["content"] == message:
        return recent
    return recent + [{"role": "user", "content": message}]


def _conversation_context_note(client_context: Dict[str, Any]) -> str:
    """
    Convert client context into a human-readable memory note.
    This avoids dumping raw JSON into the conversation while keeping the model grounded.
    """
    if not isinstance(client_context, dict):
        return ""

    # Allow-list keys to prevent prompt injection via context.
    safe_context = {
        key: client_context.get(key)
        for key in (
            "view",
            "candidate_count",
            "job_count",
            "workspace",
            "active_job",
            "match_count",
            "top_candidate_id",
            "top_candidate_score",
            "shortlisted_candidates",
            "shortlisted_count",
            "active_candidate_id",
            "active_job_id",
            "viewed_candidate_ids",
            "recruiter_preferences",
        )
        if key in client_context
    }
    if not safe_context:
        return ""

    # Resolve a few candidate ids to names for a more natural experience.
    def _lookup_names(ids: list[str]) -> dict[str, str]:
        wanted = [str(x).strip() for x in (ids or []) if str(x).strip()]
        wanted = wanted[:12]
        if not wanted:
            return {}
        db = SessionLocal()
        try:
            rows = db.query(Candidate).filter(Candidate.candidate_id.in_(wanted)).all()
            out = {}
            for row in rows:
                name = (getattr(row, "candidate_name", None) or "").strip()
                if name:
                    out[str(row.candidate_id)] = name
            return out
        except Exception:
            return {}
        finally:
            db.close()

    shortlisted = safe_context.get("shortlisted_candidates") if isinstance(safe_context.get("shortlisted_candidates"), list) else []
    viewed = safe_context.get("viewed_candidate_ids") if isinstance(safe_context.get("viewed_candidate_ids"), list) else []
    name_map = _lookup_names([*shortlisted, safe_context.get("active_candidate_id") or "", *viewed])

    workspace = str(safe_context.get("workspace") or "").strip()
    view = str(safe_context.get("view") or "").strip()
    active_job = str(safe_context.get("active_job") or "").strip()
    active_candidate_id = str(safe_context.get("active_candidate_id") or "").strip()
    active_candidate_label = name_map.get(active_candidate_id) or active_candidate_id

    lines: list[str] = []
    if workspace:
        lines.append(f"Workspace: {workspace}.")
    if view:
        lines.append(f"Screen: {view}.")
    if active_job:
        lines.append(f"Active role context: {active_job[:260]}.")
    if active_candidate_id:
        lines.append(f"Active candidate: {active_candidate_label}.")
    if shortlisted:
        short_labels = []
        for cid in shortlisted[:8]:
            label = name_map.get(str(cid)) or str(cid)
            if label and label not in short_labels:
                short_labels.append(label)
        if short_labels:
            lines.append("Shortlist: " + ", ".join(short_labels[:8]) + ".")
    prefs = safe_context.get("recruiter_preferences")
    if isinstance(prefs, dict) and prefs:
        # Keep this small; it's a hint, not a data dump.
        compact = {k: prefs.get(k) for k in ("threshold", "role_level", "must_haves") if k in prefs}
        if compact:
            lines.append("Recruiter preferences: " + json.dumps(compact, ensure_ascii=True) + ".")

    # Add internal AI memory (org-scoped), kept intentionally small.
    try:
        org_id = client_context.get("organization_id") or client_context.get("workspace_id") or client_context.get("tenant_id")
        org_id_int = int(org_id) if org_id is not None and str(org_id).strip().isdigit() else None
        mem = memory_context_note(organization_id=org_id_int, user_id=None, limit=8)
        if mem:
            lines.append(mem)
    except Exception:
        pass

    if not lines:
        return ""
    return "Context for this session (for continuity): " + " ".join(lines)


def _prune_pending_actions() -> None:
    now = time.time()
    expired = [token for token, item in PENDING_COPILOT_ACTIONS.items() if now - float(item.get("created_at", now)) > PENDING_ACTION_TTL_SEC]
    for token in expired:
        PENDING_COPILOT_ACTIONS.pop(token, None)


def _job_id_from_context(client_context: Dict[str, Any]) -> int | None:
    raw = (client_context or {}).get("active_job_id") or ""
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _org_id_from_ws_context(ws_context: Dict[str, Any]) -> int | None:
    # Mirror shortlist's fallback behavior to avoid "orphan workspace" dead-ends.
    org = ws_context.get("organization_id")
    if org is not None:
        try:
            return int(org)
        except Exception:
            return None
    user = ws_context.get("user")
    user_id = ws_context.get("user_id") or getattr(user, "id", None)
    try:
        return int(user_id) if user_id is not None else None
    except Exception:
        return None


def _candidate_in_scope(db, candidate_id: str, org_id: int | None) -> bool:
    query = db.query(Candidate).filter(Candidate.candidate_id == candidate_id)
    if org_id is not None:
        query = query.filter((Candidate.organization_id == org_id) | (Candidate.organization_id.is_(None)))
    return query.first() is not None


def _shortlist_set_status(
    *,
    candidate_id: str,
    org_id: int | None,
    job_id: int | None,
    status_value: str,
    user_email: str,
    user_id: int | None,
    recruiter_notes: str = "",
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        if not _candidate_in_scope(db, candidate_id, org_id):
            return {"ok": False, "message": "That candidate isn't available in this workspace."}

        entry = (
            db.query(ShortlistEntry)
            .filter(ShortlistEntry.organization_id == org_id, ShortlistEntry.candidate_id == candidate_id, ShortlistEntry.job_id == job_id)
            .first()
        )
        mode = "updated"
        if not entry:
            entry = ShortlistEntry(
                organization_id=org_id,
                job_id=job_id,
                candidate_id=candidate_id,
                status=status_value,
                recruiter_notes=recruiter_notes or "",
                shortlist_score=0,
                confidence=0,
                ai_reason="",
                created_by=user_email[:200],
            )
            db.add(entry)
            mode = "created"
        else:
            entry.status = status_value
            if recruiter_notes:
                entry.recruiter_notes = recruiter_notes
        db.commit()

        try:
            log_audit_event(
                action=f"copilot.shortlist.{status_value}",
                entity_type="candidate",
                entity_id=candidate_id,
                organization_id=org_id,
                user_id=user_id,
                details={"job_id": job_id, "mode": mode},
            )
        except Exception:
            pass

        return {"ok": True, "mode": mode}
    finally:
        db.close()


def _shortlist_add_or_remove(
    *,
    candidate_id: str,
    org_id: int | None,
    job_id: int | None,
    action: str,
    user_email: str,
    user_id: int | None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        if not _candidate_in_scope(db, candidate_id, org_id):
            return {"ok": False, "message": "That candidate isn't available in this workspace."}

        if action == "add":
            existing = (
                db.query(ShortlistEntry)
                .filter(ShortlistEntry.organization_id == org_id, ShortlistEntry.candidate_id == candidate_id, ShortlistEntry.job_id == job_id)
                .first()
            )
            if existing:
                existing.status = existing.status or "shortlisted"
                db.commit()
                mode = "updated"
            else:
                entry = ShortlistEntry(
                    organization_id=org_id,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    status="shortlisted",
                    shortlist_score=0,
                    confidence=0,
                    ai_reason="",
                    recruiter_notes="",
                    created_by=user_email[:200],
                )
                db.add(entry)
                db.commit()
                mode = "created"
            try:
                log_audit_event(
                    action="copilot.shortlist.add",
                    entity_type="candidate",
                    entity_id=candidate_id,
                    organization_id=org_id,
                    user_id=user_id,
                    details={"job_id": job_id, "mode": mode},
                )
            except Exception:
                pass
            return {"ok": True, "mode": mode}

        if action == "remove":
            q = db.query(ShortlistEntry).filter(ShortlistEntry.organization_id == org_id, ShortlistEntry.candidate_id == candidate_id)
            if job_id is not None:
                q = q.filter(ShortlistEntry.job_id == job_id)
            deleted = q.delete(synchronize_session=False)
            db.commit()
            try:
                log_audit_event(
                    action="copilot.shortlist.remove",
                    entity_type="candidate",
                    entity_id=candidate_id,
                    organization_id=org_id,
                    user_id=user_id,
                    details={"job_id": job_id, "deleted": int(deleted)},
                )
            except Exception:
                pass
            return {"ok": True, "deleted": int(deleted)}

        return {"ok": False, "message": "Unsupported shortlist action."}
    finally:
        db.close()


def _maybe_handle_copilot_command(
    *,
    message: str,
    ws_context: Dict[str, Any],
    client_context: Dict[str, Any],
) -> str | None:
    """
    Handle a small set of recruiter operations via explicit slash commands.
    Requires confirmation for state-changing actions.
    """
    text = (message or "").strip()
    if not text.startswith("/"):
        return None

    _prune_pending_actions()

    org_id = _org_id_from_ws_context(ws_context)
    user = ws_context.get("user")
    user_id = ws_context.get("user_id") or getattr(user, "id", None)
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except Exception:
        user_id_int = None
    user_email = str(ws_context.get("email") or getattr(user, "username", "") or "")
    job_id = _job_id_from_context(client_context)

    # Confirmation flow.
    if text.lower().startswith("/confirm"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        item = PENDING_COPILOT_ACTIONS.pop(token, None)
        if not item:
            return "That confirmation token isn't valid anymore. Re-run the command and I'll re-generate it."
        if user_id_int is not None and item.get("user_id") not in {None, user_id_int}:
            return "That confirmation token belongs to a different session. Re-run the command from this session."
        action = item.get("action")
        candidate_id = item.get("candidate_id")
        job_id = item.get("job_id")
        if action in {"add", "remove"}:
            res = _shortlist_add_or_remove(candidate_id=candidate_id, org_id=org_id, job_id=job_id, action=action, user_email=user_email, user_id=user_id_int)
            if not res.get("ok"):
                return str(res.get("message") or "Shortlist action failed.")
            if action == "remove":
                return f"Done. Removed **{candidate_id}** from the shortlist."
            return f"Done. Added **{candidate_id}** to the shortlist."
        if action in {"approved", "rejected"}:
            res = _shortlist_set_status(candidate_id=candidate_id, org_id=org_id, job_id=job_id, status_value=action, user_email=user_email, user_id=user_id_int)
            if not res.get("ok"):
                return str(res.get("message") or "Shortlist update failed.")
            return f"Done. Marked **{candidate_id}** as **{action}**."
        return "That action is no longer supported. Re-run your command."

    parts = text.split()
    cmd = parts[0].lower()

    if cmd == "/shortlist" and len(parts) >= 3 and parts[1].lower() in {"add", "remove", "approve", "reject"}:
        op = parts[1].lower()
        candidate_id = parts[2].strip()
        if not candidate_id:
            return "Which candidate should I act on? Example: `/shortlist add candidate_123`"

        # Sensitive: require confirmation for any mutation.
        token = secrets.token_urlsafe(10)
        action = "add" if op == "add" else "remove" if op == "remove" else "approved" if op == "approve" else "rejected"
        PENDING_COPILOT_ACTIONS[token] = {
            "created_at": time.time(),
            "user_id": user_id_int,
            "org_id": org_id,
            "action": action,
            "candidate_id": candidate_id,
            "job_id": job_id,
        }
        job_note = f" for job `{job_id}`" if job_id is not None else ""
        verb = "add" if action == "add" else "remove" if action == "remove" else action
        return f"I can **{verb}** candidate `{candidate_id}`{job_note}. Reply with `/confirm {token}` to proceed."

    if cmd == "/email" and len(parts) >= 3 and parts[1].lower() == "draft":
        candidate_id = parts[2].strip()
        return (
            f"Here's a recruiter-ready outreach draft for `{candidate_id}`:\n\n"
            "Subject: Quick chat about your background\n\n"
            "Hi [Name],\n\n"
            "I reviewed your resume and there are a couple of signals that look relevant for the role we're hiring for. "
            "Would you be open to a 15-minute call this week to confirm fit and walk through your recent work?\n\n"
            "Best,\n"
            f"{(user_email.split('@', 1)[0] or 'Recruiter').title()}\n"
        )

    if cmd == "/report" and len(parts) >= 2 and parts[1].lower() in {"shortlist", "pipeline"}:
        org_id = _org_id_from_ws_context(ws_context)
        job_id = _job_id_from_context(client_context)
        db = SessionLocal()
        try:
            q = db.query(ShortlistEntry).filter(ShortlistEntry.organization_id == org_id)
            if job_id is not None:
                q = q.filter(ShortlistEntry.job_id == job_id)
            rows = q.order_by(ShortlistEntry.created_at.desc()).limit(200).all()
            if not rows:
                return "No shortlist entries found yet. Run matching, then add candidates to the shortlist (or use auto-shortlist)."

            candidate_ids = [r.candidate_id for r in rows]
            candidates = db.query(Candidate).filter(Candidate.candidate_id.in_(candidate_ids)).all()
            name_map = {c.candidate_id: (getattr(c, "candidate_name", None) or "").strip() for c in candidates}

            approved = [r for r in rows if (r.status or "") == "approved"]
            rejected = [r for r in rows if (r.status or "") == "rejected"]
            shortlisted = [r for r in rows if (r.status or "") != "rejected"]

            lines = []
            for r in rows[:12]:
                label = name_map.get(r.candidate_id) or r.candidate_id
                score = int(r.shortlist_score or 0)
                status = (r.status or "shortlisted").lower()
                reason = (r.ai_reason or "").strip()
                reason = (reason[:140] + "...") if len(reason) > 140 else reason
                lines.append(f"- **{label}** ({score}%) [{status}]" + (f"\n  {reason}" if reason else ""))

            header = "**Shortlist report**" + (f" (job `{job_id}`)" if job_id is not None else "")
            return (
                header
                + "\n\n"
                + f"Totals: {len(rows)} tracked | {len(shortlisted)} shortlisted | {len(approved)} approved | {len(rejected)} rejected\n\n"
                + "Top entries:\n"
                + "\n".join(lines)
                + "\n\nNext best action: approve the top 2-3 candidates with manageable gaps, then schedule screens using their interview focus areas."
            )
        finally:
            db.close()

    return None


def _history_with_context(message: str, session_id: str, client_context: Dict[str, Any]) -> list[dict]:
    history = _chat_history_for_model(message, session_id)
    note = _conversation_context_note(client_context)
    if note:
        history.insert(max(len(history) - 1, 0), {"role": "user", "content": note})
    return history


async def _ws_send(websocket: WebSocket, event: str, **payload) -> None:
    await websocket.send_text(json.dumps({"event": event, **payload}))


def _chunk_text(text: str, size: int = 28):
    words = text.split(" ")
    current = ""
    for word in words:
        next_value = f"{current} {word}".strip()
        if len(next_value) >= size:
            yield next_value + " "
            current = ""
        else:
            current = next_value
    if current:
        yield current


def process_face_analysis(image_base64: str) -> Dict[str, Any]:
    if not image_base64:
        return {"face_detected": False, "attention": "unknown", "confidence": 0.0}
    if cv2 is None or np is None:
        return {"face_detected": False, "attention": "opencv_unavailable", "confidence": 0.0}

    try:
        payload = image_base64.split(",", 1)[1] if "," in image_base64 else image_base64
        raw = base64.b64decode(payload)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return {"face_detected": False, "attention": "unknown", "confidence": 0.0}

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
        if len(faces) == 0:
            return {"face_detected": False, "attention": "no_face", "confidence": 0.0}

        x, y, w, h = faces[0]
        img_h, img_w = img.shape[:2]
        center_x = x + w / 2
        center_y = y + h / 2
        distance = ((center_x - img_w / 2) ** 2 + (center_y - img_h / 2) ** 2) ** 0.5
        max_distance = ((img_w / 2) ** 2 + (img_h / 2) ** 2) ** 0.5 or 1
        face_ratio = (w * h) / max(1, img_w * img_h)
        confidence = round(min(1.0, face_ratio * 8), 2)
        attention = "focused" if distance < max_distance * 0.35 and face_ratio > 0.04 else "distracted"
        return {"face_detected": True, "attention": attention, "confidence": confidence}
    except Exception:
        return {"face_detected": False, "attention": "unknown", "confidence": 0.0}


def generate_interview_question(session_id: str, current_question: str, candidate_response: str) -> Dict[str, str]:
    questions = [
        {"question": "Can you walk me through a challenging project and the tradeoffs you made?", "type": "technical"},
        {"question": "How do you debug a production issue when the root cause is unclear?", "type": "problem_solving"},
        {"question": "Describe a time you learned a new tool quickly for a project.", "type": "learning"},
        {"question": "How do you handle disagreement inside a team?", "type": "teamwork"},
        {"question": "What would you want to improve in your first 90 days in this role?", "type": "career"},
    ]
    if not hasattr(generate_interview_question, "indexes"):
        generate_interview_question.indexes = {}
    index = generate_interview_question.indexes.get(session_id or "default", 0)
    if index >= len(questions):
        return {"question": "Thank you. The structured interview is complete.", "type": "complete"}
    generate_interview_question.indexes[session_id or "default"] = index + 1
    return questions[index]


def analyze_response(response: str, question: str) -> float:
    if not response.strip():
        return 0.0
    score = 45.0
    words = response.split()
    if len(words) >= 40:
        score += 20
    elif len(words) >= 15:
        score += 10
    else:
        score -= 15
    lower = response.lower()
    score += min(20, sum(word in lower for word in ["because", "example", "impact", "tradeoff", "measured", "team", "learned"]) * 4)
    return round(max(0, min(100, score)), 1)


def calculate_final_evaluation(match_score: float, responses: List[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    response_scores = [analyze_response(r.get("response", ""), r.get("question", "")) for r in responses]
    communication = sum(response_scores) / len(response_scores) if response_scores else 0
    attention = float(metrics.get("attention_score", 70))
    confidence = float(metrics.get("confidence_score", 70))
    final_score = round((match_score * 0.35) + (communication * 0.35) + (attention * 0.15) + (confidence * 0.15), 1)
    recommendation = "Strong Hire" if final_score >= 80 else "Consider" if final_score >= 60 else "Needs Review"
    return {
        "final_score": final_score,
        "breakdown": {"technical": round(match_score, 1), "communication": round(communication, 1), "attention": round(attention, 1), "confidence": round(confidence, 1)},
        "recommendation": recommendation,
        "insights": [
            f"Match score contributed {round(match_score, 1)}/100.",
            f"Average response quality was {round(communication, 1)}/100.",
            "Use the final decision endpoint to compare this candidate against the shortlist.",
        ],
    }


@router.post("/summary")
def ai_summary(req: SummaryRequest, current_user: str = Depends(get_current_user)):
    prompt = f"Summarize candidate {req.candidate_id}, score {req.score}, skills {format_skills(req.skills)}. Include strengths, risks, and recommendation."
    return ok({"summary": _safe_ai(prompt, [{"candidate_id": req.candidate_id, "score": req.score, "skills": req.skills}])})


@router.post("/chat")
def chat_assistant(req: ChatAssistantRequest, current_user: str = Depends(get_current_user)):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    candidates = req.candidates or _stored_candidates()
    session_id = str(req.context.get("session_id") or current_user or "default")
    _remember_chat("user", req.message, session_id)
    if _is_disallowed_ai_request(req.message.lower()):
        payload = _fallback_chat_payload(req.message, candidates)
        _remember_chat("assistant", payload["response"], session_id)
        return ok(payload)
    response = ask_ai(_history_with_context(req.message, session_id, req.context), candidates)
    if response.startswith("__AI_PROVIDER_NOT_CONFIGURED__") or response.startswith("AI service is temporarily"):
        payload = _fallback_chat_payload(req.message, candidates)
        _remember_chat("assistant", payload["response"], session_id)
        return ok(payload)
    _remember_chat("assistant", response, session_id)
    return ok({
        "response": response,
        "reply": response,
        "skills": [],
        "missing_skills": [],
        "suggestions": [],
        "recommended_roles": [],
        "mode": "openai",
    })


@router.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    context = get_user_context_from_token(token)
    if not context:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await _ws_send(websocket, "ready")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await _ws_send(websocket, "error", message="Invalid chat payload.")
                continue

            message = str(payload.get("message") or "").strip()
            if not message:
                await _ws_send(websocket, "error", message="Please type a question for the assistant.")
                continue

            candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
            if not candidates:
                candidates = _stored_candidates()
            client_context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            # Attach server-trusted org/workspace identifiers for internal memory lookups.
            client_context.setdefault("organization_id", context.get("organization_id"))
            client_context.setdefault("workspace_id", context.get("workspace_id"))
            client_context.setdefault("tenant_id", context.get("tenant_id"))
            session_id = str(client_context.get("session_id") or context.get("email") or "default")

            _remember_chat("user", message, session_id)
            await _ws_send(websocket, "start", session_id=session_id)

            if _is_disallowed_ai_request(message.lower()):
                response = _fallback_chat_payload(message, candidates)["response"]
                for chunk in _chunk_text(response):
                    await _ws_send(websocket, "token", token=chunk)
                    await asyncio.sleep(0.035)
                _remember_chat("assistant", response, session_id)
                await _ws_send(websocket, "done", mode="safety")
                continue

            # Explicit recruiter operations (confirmation-gated).
            command_response = _maybe_handle_copilot_command(
                message=message,
                ws_context=context,
                client_context=client_context,
            )
            if command_response:
                for chunk in _chunk_text(command_response):
                    await _ws_send(websocket, "token", token=chunk)
                    await asyncio.sleep(0.02)
                _remember_chat("assistant", command_response, session_id)
                await _ws_send(websocket, "done", mode="copilot-actions")
                continue

            full_response = ""
            provider_failed = False
            for chunk in stream_ai(_history_with_context(message, session_id, client_context), candidates):
                if chunk in {"__AI_PROVIDER_NOT_CONFIGURED__", "__AI_PROVIDER_UNAVAILABLE__"}:
                    provider_failed = True
                    break
                full_response += chunk
                await _ws_send(websocket, "token", token=chunk)
                await asyncio.sleep(0.012)

            if provider_failed or not full_response.strip():
                full_response = _fallback_chat_payload(message, candidates)["response"]
                for chunk in _chunk_text(full_response):
                    await _ws_send(websocket, "token", token=chunk)
                    await asyncio.sleep(0.035)

            _remember_chat("assistant", full_response, session_id)
            await _ws_send(websocket, "done", mode="openai" if not provider_failed else "local-fallback")

    except WebSocketDisconnect:
        return
    except Exception:
        await _ws_send(websocket, "error", message="The assistant stream failed gracefully. Please retry.")


@router.post("/candidate-chat")
def chat_candidate(req: ChatCandidateRequest, current_user: str = Depends(get_current_user)):
    prompt = f"Candidate {req.candidate_id}, score {req.score}, skills {format_skills(req.skills)}. Recruiter asks: {req.message}"
    return ok({"reply": _safe_ai(prompt, [{"candidate_id": req.candidate_id, "score": req.score, "skills": req.skills}])})


@router.post("/compare")
def compare_candidates(req: CompareRequest, current_user: str = Depends(get_current_user)):
    prompt = f"Compare these candidates and recommend next action: {req.candidate1} versus {req.candidate2}"
    return ok({"result": _safe_ai(prompt, [req.candidate1, req.candidate2])})


@router.post("/feedback")
def ai_feedback(req: FeedbackRequest, current_user: str = Depends(get_current_user)):
    return ok({"feedback": ai_feedback_service.generate_feedback(req.resume_text, req.job_description)})


@router.post("/shortlist")
def auto_shortlist(req: ShortlistRequest, current_user: str = Depends(get_current_user)):
    if not req.candidates:
        raise HTTPException(status_code=400, detail="No candidates provided")
    ranked = sorted(req.candidates, key=lambda c: c.get("match_score", c.get("score", 0)) or 0, reverse=True)
    suggestions = [
        {"candidate_id": c.get("candidate_id"), "reason": c.get("explanation") or f"Score {c.get('match_score', c.get('score', 0))}"}
        for c in ranked[:3]
    ]
    return ok({"suggestions": suggestions, "result": suggestions})


@router.post("/decision")
def recruiter_decision(req: DecisionRequest, current_user: str = Depends(get_current_user)):
    if not req.candidates:
        raise HTTPException(status_code=400, detail="At least one candidate is required")
    ranked = sorted(req.candidates, key=lambda c: c.get("match_score", c.get("score", 0)) or 0, reverse=True)
    best = ranked[0]
    shortlist = ranked[:3]
    reasoning = best.get("explanation") or f"{best.get('candidate_id')} has the highest visible match score."
    return ok({
        "best_candidate": best,
        "reasoning": reasoning,
        "shortlist_suggestions": [
            {"candidate_id": c.get("candidate_id"), "score": c.get("match_score", c.get("score", 0)), "reason": c.get("explanation") or "Strong relative match."}
            for c in shortlist
        ],
    })


@router.post("/interview/live")
def interview_live(req: InterviewLiveRequest, current_user: str = Depends(get_current_user)):
    face_data = process_face_analysis(req.image_base64)
    question_data = generate_interview_question(req.session_id, req.current_question, req.candidate_response)
    response_score = analyze_response(req.candidate_response, req.current_question) if req.candidate_response else 0.0
    alerts = []
    if req.image_base64 and not face_data["face_detected"]:
        alerts.append("No face detected - ensure candidate is visible.")
    if face_data.get("attention") == "distracted":
        alerts.append("Candidate appears off-center or distracted.")
    return ok({**face_data, "response_score": response_score, "current_question": req.current_question, "next_question": question_data["question"], "question_type": question_data["type"], "alerts": alerts})


@router.post("/interview/evaluate")
def interview_evaluate(req: InterviewEvaluateRequest, current_user: str = Depends(get_current_user)):
    if not req.session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    return ok(calculate_final_evaluation(req.match_score, req.interview_responses, req.behavioral_metrics))
