import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import AuditLog, Notification
from backend.models.job import Job
from backend.services.candidate_intelligence_service import candidate_graph
from backend.services.decision_service import make_decision
from backend.services.matcher import rank_candidates_tfidf
from backend.services.automation_rules_service import evaluate_rules, list_rules
from backend.services.predictive_intelligence_service import predict_candidate_outcomes
from backend.services.autonomous_ops_service import autonomous_insights
from backend.services.enterprise_service import get_workspace_overview


logger = logging.getLogger(__name__)


def _latest_job(db, organization_id: Optional[int]) -> Optional[Job]:
    query = db.query(Job)
    if organization_id is not None:
        query = query.filter(or_(Job.organization_id == organization_id, Job.organization_id.is_(None)))
    return query.order_by(Job.created_at.desc()).first()


def _candidate_row(db, candidate_id: str, organization_id: Optional[int]) -> Optional[Candidate]:
    query = db.query(Candidate).filter(Candidate.candidate_id == candidate_id)
    if organization_id is not None:
        query = query.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
    return query.first()


def _risk_score(match_score: float, match_confidence: float, extraction_confidence: float, missing_skills: list[str]) -> dict[str, Any]:
    # 0 (low risk) -> 100 (high risk)
    risk = 0.0
    risk += max(0.0, 55.0 - float(match_score or 0)) * 0.75
    risk += max(0.0, 0.60 - float(match_confidence or 0)) * 60.0
    risk += max(0.0, 0.45 - float(extraction_confidence or 0)) * 80.0
    risk += min(28.0, len(missing_skills or []) * 4.5)
    return {
        "risk_score": round(min(100.0, max(0.0, risk)), 2),
        "drivers": [
            *(["missing_skills"] if missing_skills else []),
            *(["low_match_confidence"] if (match_confidence or 0) < 0.55 else []),
            *(["low_extraction_confidence"] if (extraction_confidence or 0) < 0.45 else []),
            *(["low_match_score"] if (match_score or 0) < 55 else []),
        ][:6],
    }


def _readiness_scores(match_score: float, match_confidence: float, experience_score: float, missing_skills: list[str]) -> dict[str, Any]:
    gap_penalty = min(22.0, len(missing_skills or []) * 3.0)
    hiring = (float(match_score or 0) * 0.62) + (float(match_confidence or 0) * 100.0 * 0.23) + (float(experience_score or 0) * 0.15) - gap_penalty
    interview = (float(match_score or 0) * 0.55) + (float(match_confidence or 0) * 100.0 * 0.25) + (float(experience_score or 0) * 0.20) - (gap_penalty * 0.7)
    return {
        "hiring_readiness": round(min(100.0, max(0.0, hiring)), 2),
        "interview_readiness": round(min(100.0, max(0.0, interview)), 2),
    }


def _next_actions(candidate_id: str, recommendation: str, decision: dict[str, Any], missing_skills: list[str], extraction_confidence: float) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    decision_label = (decision or {}).get("decision") or ""

    if extraction_confidence and extraction_confidence < 0.45:
        actions.append(
            {
                "title": "Confirm resume text quality",
                "body": "Extraction confidence was low. Ask for a text-based PDF/DOCX or copy-paste resume text before final decisions.",
                "kind": "quality",
                "priority": "high",
                "confidence": 0.86,
                "reasoning": "Resume extraction quality is below the operating threshold for high-trust candidate decisions.",
                "operational_impact": "Reduces the risk of advancing or rejecting a candidate using incomplete evidence.",
                "uncertainty": "OCR quality, formatting, or scanned pages may be limiting the extracted signal.",
                "suggested_action": "Request a text-based resume or enable OCR before final review.",
            }
        )

    if decision_label in {"hire", "consider"} or recommendation in {"Strong Match", "Recommended"}:
        actions.append(
            {
                "title": "Schedule technical screening",
                "body": "Move the candidate into a structured technical screen with targeted questions from the interview plan.",
                "kind": "interview",
                "priority": "high" if recommendation == "Strong Match" else "medium",
                "confidence": 0.78 if recommendation == "Strong Match" else 0.66,
                "reasoning": "The candidate has enough role-fit evidence to justify a structured validation step.",
                "operational_impact": "Keeps qualified candidates moving while the evidence is still fresh for recruiters.",
                "uncertainty": "Interview signal may change if must-have gaps are validated as blockers.",
                "suggested_action": "Schedule a technical screen and focus questions on the highest-impact role requirements.",
            }
        )
        actions.append(
            {
                "title": "Add recruiter notes",
                "body": "Capture 2-3 bullets on strengths, risks, and what to validate next so the team can align quickly.",
                "kind": "collaboration",
                "priority": "medium",
                "confidence": 0.72,
                "reasoning": "Decision quality improves when recruiter context is captured before handoff.",
                "operational_impact": "Improves hiring-manager alignment and reduces repeated candidate review work.",
                "uncertainty": "Notes should be updated after interview evidence arrives.",
                "suggested_action": "Record strengths, risks, and the next validation question in the candidate profile.",
            }
        )

    if missing_skills:
        actions.append(
            {
                "title": "Validate key gaps early",
                "body": f"Confirm exposure to: {', '.join(missing_skills[:5])}. If transferable, probe with concrete project examples.",
                "kind": "risk",
                "priority": "medium",
                "confidence": 0.69,
                "reasoning": "Missing skills are visible in the match evidence and should be validated before advancement.",
                "operational_impact": "Prevents late-stage interview waste caused by unresolved must-have gaps.",
                "uncertainty": "The candidate may have transferable experience that is not explicit in the resume.",
                "suggested_action": "Ask targeted screening questions about the top missing skills.",
            }
        )

    if decision_label == "reject" and recommendation == "Not Recommended":
        actions.append(
            {
                "title": "Close the loop",
                "body": "If this is a real applicant pipeline, send a polite rejection note and record the reason for reporting.",
                "kind": "workflow",
                "priority": "low",
                "confidence": 0.64,
                "reasoning": "The decision engine and recommendation both indicate low fit for the current role.",
                "operational_impact": "Maintains candidate experience and preserves auditable decision records.",
                "uncertainty": "A different role or clearer job context may change fit assessment.",
                "suggested_action": "Close the candidate respectfully or move them to a more relevant role pool.",
            }
        )

    # Ensure something meaningful always exists.
    if not actions:
        actions.append(
            {
                "title": "Run a quick screen",
                "body": "Use the recruiter summary to drive a 15 minute screen focused on role alignment and key must-haves.",
                "kind": "interview",
                "priority": "medium",
                "confidence": 0.58,
                "reasoning": "There is not enough evidence for a stronger automated recommendation.",
                "operational_impact": "Creates a lightweight validation step without overcommitting interview capacity.",
                "uncertainty": "More job context or candidate evidence may produce a clearer recommendation.",
                "suggested_action": "Run a short screen and capture the outcome in WorkforceOS.",
            }
        )
    return actions[:6]


def candidate_360(
    candidate_id: str,
    organization_id: Optional[int],
    job_description: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        candidate = _candidate_row(db, candidate_id, organization_id)
        if not candidate:
            return {
                "candidate_id": candidate_id,
                "status": "not_found",
                "message": "Candidate not found in this workspace.",
            }

        job = None
        job_desc = (job_description or "").strip()
        if job_id:
            job = db.query(Job).filter(Job.id == int(job_id)).first()
            if job and (organization_id is None or job.organization_id in {organization_id, None}):
                job_desc = (job.description or "").strip()
        if not job_desc:
            job = _latest_job(db, organization_id)
            job_desc = (job.description if job else "") or ""

        candidate_payload = {
            "candidate_id": candidate.candidate_id,
            "candidate_name": getattr(candidate, "candidate_name", None),
            "name_confidence": (getattr(candidate, "name_confidence", None) or 0) / 100 if getattr(candidate, "name_confidence", None) is not None else 0.0,
            "name_source": getattr(candidate, "name_source", None),
            "skills": candidate.skills or [],
            "text": candidate.text or "",
            "raw_text": candidate.raw_text or "",
            "experience": {"min": int(candidate.experience or 0)},
            "text_snippet": ((candidate.raw_text or "") or (candidate.text or ""))[:300],
        }

        match = None
        if job_desc.strip():
            ranked = rank_candidates_tfidf(job_desc, [candidate_payload])
            match = ranked[0] if ranked else None
        else:
            match = {
                "candidate_id": candidate.candidate_id,
                "match_score": 0,
                "confidence": 0,
                "recommendation": "Needs Review",
                "recommendation_reason": "No job description provided; collect role requirements before final scoring.",
                "missing_skills": [],
                "matched_skills": [],
                "recruiter_summary": "Job context is ready for intake. Add a job description to generate recruiter-grade fit analysis.",
            }

        extraction_confidence = 0.0
        try:
            extraction_confidence = float((candidate.extraction_json or {}).get("confidence") or 0.0)
        except Exception:
            extraction_confidence = 0.0

        match_score = float((match or {}).get("match_score") or (match or {}).get("score") or 0.0)
        match_conf = float((match or {}).get("confidence") or 0.0)
        experience_score = float((match or {}).get("experience_score") or 0.0)
        missing_skills = (match or {}).get("missing_skills") or []

        decision = make_decision(match_score=match_score, missing_skills=missing_skills)
        risk = _risk_score(match_score, match_conf, extraction_confidence, missing_skills)
        readiness = _readiness_scores(match_score, match_conf, experience_score, missing_skills)
        predictions = predict_candidate_outcomes(
            match_score=match_score,
            match_confidence=match_conf,
            missing_skills=missing_skills,
            experience_score=experience_score,
            extraction_confidence=extraction_confidence,
        )
        actions = _next_actions(candidate.candidate_id, (match or {}).get("recommendation") or "", decision, missing_skills, extraction_confidence)

        rule_count = len(list_rules(organization_id))
        automation_preview = evaluate_rules(
            organization_id,
            trigger="match.completed",
            context={
                "candidate_id": candidate.candidate_id,
                "match_score": match_score,
                "missing_skills": missing_skills,
                "confidence": match_conf,
            },
        )
        if rule_count == 0:
            automation_preview["note"] = "Automation is in recommendation-first mode. Create rules when the workspace is ready for automatic shortlist or next-step actions."

        # Shortlist state (latest / any job)
        shortlist_rows = (
            db.query(ShortlistEntry)
            .filter(ShortlistEntry.candidate_id == candidate.candidate_id)
            .filter(or_(ShortlistEntry.organization_id == organization_id, ShortlistEntry.organization_id.is_(None)))
            .order_by(ShortlistEntry.created_at.desc())
            .limit(10)
            .all()
        )
        shortlist = [
            {
                "job_id": row.job_id,
                "status": row.status,
                "shortlist_score": row.shortlist_score,
                "confidence": row.confidence,
                "ai_reason": row.ai_reason,
                "recruiter_notes": row.recruiter_notes,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in shortlist_rows
        ]

        graph = candidate_graph(candidate.candidate_id, organization_id) if organization_id is not None else candidate_graph(candidate.candidate_id, None)

        return {
            "status": "ok",
            "candidate_id": candidate.candidate_id,
            "job_context": {
                "job_id": getattr(job, "id", None),
                "job_title": getattr(job, "title", None),
                "job_description_snippet": (job_desc or "").strip()[:700],
            },
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "candidate_name": getattr(candidate, "candidate_name", None),
                "name_confidence": (getattr(candidate, "name_confidence", None) or 0) / 100 if getattr(candidate, "name_confidence", None) is not None else 0.0,
                "name_source": getattr(candidate, "name_source", None),
                "extracted_email": getattr(candidate, "extracted_email", None),
                "extracted_phone": getattr(candidate, "extracted_phone", None),
                "skills": candidate.skills or [],
                "experience_years": int(candidate.experience or 0),
                "resume_extraction": candidate.extraction_json or {},
                "text_snippet": ((candidate.raw_text or "") or (candidate.text or ""))[:360],
                "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
            },
            "match_intelligence": {k: v for k, v in (match or {}).items() if k not in {"text", "raw_text"}},
            "decision_engine": decision,
            "risk": risk,
            "readiness": readiness,
            "predictions": predictions,
            "next_actions": actions,
            "automation_preview": automation_preview,
            "candidate_graph": graph,
            "shortlist": shortlist,
        }
    finally:
        db.close()


def executive_brief(organization_id: Optional[int], user_id: Optional[int] = None, days: int = 14) -> dict[str, Any]:
    """
    Executive-friendly intelligence brief using evidence-weighted signals from audit logs + notifications + stored objects.
    Keeps the product feeling "alive" even when an external model is unavailable.
    """
    horizon = max(1, min(90, int(days or 14)))
    since = datetime.utcnow() - timedelta(days=horizon)

    db = SessionLocal()
    try:
        audit_query = db.query(AuditLog)
        if organization_id is not None:
            audit_query = audit_query.filter(AuditLog.organization_id == organization_id)
        audit_rows = audit_query.filter(AuditLog.created_at >= since).order_by(AuditLog.created_at.desc()).limit(120).all()

        notif_query = db.query(Notification)
        if user_id is not None:
            notif_query = notif_query.filter(Notification.user_id == int(user_id))
        if organization_id is not None:
            notif_query = notif_query.filter(Notification.organization_id == organization_id)
        notif_rows = notif_query.filter(Notification.created_at >= since).order_by(Notification.created_at.desc()).limit(120).all()

        # Basic counts / signals
        shortlist_events = [row for row in audit_rows if (row.action or "").startswith("shortlist.")]
        interview_events = [row for row in audit_rows if (row.action or "").startswith("interview.")]
        match_events = [row for row in audit_rows if (row.action or "").startswith(("match.", "ranking."))]
        upload_events = [row for row in audit_rows if (row.action or "").startswith("resume.")]
        schedule_events = [row for row in audit_rows if (row.action or "").startswith("scheduling.")]
        auth_failed = [row for row in audit_rows if (row.action or "").startswith("auth.failed")]

        top_actions = {}
        for row in audit_rows:
            action = (row.action or "").split(":", 1)[0]
            top_actions[action] = top_actions.get(action, 0) + 1

        risks: list[str] = []
        if len(auth_failed) >= 3:
            risks.append("Elevated failed-login volume (review team access and rate-limits).")
        if not shortlist_events:
            risks.append("Shortlisting velocity is low (pipeline may be stuck at screening).")

        wins: list[str] = []
        if shortlist_events:
            wins.append(f"{len(shortlist_events)} shortlist events in the last {horizon} days.")
        if interview_events:
            wins.append(f"{len(interview_events)} interview-related events in the last {horizon} days.")

        next_steps: list[str] = []
        next_steps.append("Confirm job must-haves are explicit in descriptions to improve match precision.")
        next_steps.append("Auto-shortlist candidates above 75% and schedule screens within 48 hours to keep momentum.")
        if len(auth_failed) >= 3:
            next_steps.append("Review recent failed logins and strengthen enforcement with MFA and stricter throttles.")

        workspace = get_workspace_overview(organization_id)
        auto = autonomous_insights(organization_id)
        total_activity = len(upload_events) + len(match_events) + len(shortlist_events) + len(schedule_events) + len(interview_events)
        pipeline_health = min(
            100,
            max(
                18,
                34
                + len(match_events) * 7
                + len(shortlist_events) * 6
                + len(schedule_events) * 8
                + len(interview_events) * 7
                - len(auth_failed) * 4,
            ),
        )
        delay_probability = min(92, max(8, 68 - len(shortlist_events) * 6 - len(schedule_events) * 8 - len(interview_events) * 5 + max(0, len(upload_events) - len(match_events)) * 5))
        recruiter_productivity = min(100, max(20, 42 + len(shortlist_events) * 7 + len(schedule_events) * 8 + len(interview_events) * 6))
        ai_effectiveness = min(100, max(24, 48 + len(match_events) * 7 + len(shortlist_events) * 5 + len(notif_rows) * 2))
        confidence = round(max(0.46, min(0.93, 0.50 + min(24, total_activity) / 60)), 2)
        uncertainty = (
            "Higher confidence: recent upload, ranking, shortlist, schedule, and interview events provide enough signal for directional forecasting."
            if total_activity >= 8
            else "Moderate uncertainty: forecasts will strengthen as recruiters record more ranking, shortlist, scheduling, and interview outcomes."
        )
        operating_summary = {
            "velocity_trend": "accelerating" if total_activity >= 8 else "calibrating" if total_activity else "early_signal",
            "recruiter_productivity_index": recruiter_productivity,
            "ai_effectiveness_index": ai_effectiveness,
            "workforce_forecast": {
                "staffing_shortage": max(0, int((workspace or {}).get("totals", {}).get("jobs", 0) or 0) - len(shortlist_events)),
                "delay_probability": delay_probability,
                "offer_acceptance_probability": min(90, max(18, 38 + len(interview_events) * 6 + len(schedule_events) * 4)),
                "pipeline_health": pipeline_health,
            },
            "confidence": confidence,
            "uncertainty": uncertainty,
            "boardroom_readout": (
                f"Hiring operations are {'accelerating' if total_activity >= 8 else 'calibrating'} with "
                f"{pipeline_health}% pipeline health, {delay_probability}% delay risk, and "
                f"{ai_effectiveness}% AI effectiveness across the last {horizon} days."
            ),
            "recommended_actions": next_steps[:4],
            "reasoning": "The brief weighs audit events, recruiter workflow motion, notifications, and autonomous operations priorities.",
        }

        return {
            "status": "ok",
            "window_days": horizon,
            "highlights": wins[:6] or ["Workspace is in early-signal mode. Upload resumes and run matching to establish hiring velocity."],
            "risks": risks[:6] or ["Current operating signals do not require escalation."],
            "next_steps": next_steps[:6],
            "activity": {
                "resume_uploads": len(upload_events),
                "match_runs": len(match_events),
                "shortlist_events": len(shortlist_events),
                "scheduling_events": len(schedule_events),
                "interview_events": len(interview_events),
                "notifications": len(notif_rows),
                "auth_failed": len(auth_failed),
            },
            "operating_summary": operating_summary,
            "ai_effectiveness_report": {
                "index": ai_effectiveness,
                "reasoning": "AI effectiveness rises when matching, shortlist, and operational notification loops are actively used.",
                "confidence": confidence,
                "uncertainty": uncertainty,
                "suggested_action": "Keep candidate ranking, shortlist review, and interview outcomes inside WorkforceOS to improve decision quality.",
            },
            "top_actions": sorted(top_actions.items(), key=lambda kv: kv[1], reverse=True)[:8],
            "autonomous_insights": auto,
            "workforce_analytics": (workspace or {}).get("workforce_analytics") if isinstance(workspace, dict) else {},
        }
    finally:
        db.close()
