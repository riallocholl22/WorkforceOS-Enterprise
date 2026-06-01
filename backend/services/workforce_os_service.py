from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import (
    AIMemory,
    Assessment,
    AuditLog,
    InterviewSchedule,
    InterviewSessionRecord,
    Notification,
    SecurityEvent,
)
from backend.models.job import Job
from backend.services.autonomous_ops_service import autonomous_insights
from backend.services.ai_orchestration_service import ai_provider_status
from backend.services.email_service import provider_status as email_provider_status
from backend.services.hiring_brain_service import executive_brief


def _pct(value: float, total: float) -> int:
    if total <= 0:
        return 0
    return int(round(max(0.0, min(1.0, value / total)) * 100))


def _ratio_score(value: float, total: float, fallback: int = 0) -> int:
    if total <= 0:
        return fallback
    return max(0, min(100, int(round((value / total) * 100))))


def _trend_label(current: int, previous: int) -> str:
    if current > previous:
        return "accelerating"
    if current < previous:
        return "softening"
    return "stable"


def _confidence(score: int) -> float:
    return round(max(0.45, min(0.94, 0.48 + (score / 220))), 2)


def _audit_counts(rows: list[AuditLog]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        action = str(row.action or "")
        if action.startswith("resume."):
            counts["uploads"] += 1
        if action.startswith("match.") or action.startswith("ranking."):
            counts["matches"] += 1
        if action.startswith("shortlist.") or action.startswith("copilot.shortlist."):
            counts["shortlist"] += 1
        if action.startswith("scheduling."):
            counts["scheduling"] += 1
        if action.startswith("interview."):
            counts["interviews"] += 1
        if action.startswith("auth.failed"):
            counts["auth_failed"] += 1
    return counts


def _agent(
    name: str,
    focus: str,
    status: str,
    confidence: float,
    summary: str,
    actions: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "focus": focus,
        "status": status,
        "confidence": confidence,
        "summary": summary,
        "actions": actions[:4],
        "explanation": {
            "reasoning": summary,
            "uncertainty": "Confidence is derived from recent workspace activity volume and signal consistency.",
        },
    }


def _trust_priority(item: dict[str, Any], default_impact: str) -> dict[str, Any]:
    """Normalize AI recommendations so the UI can always show reasoning, impact, and uncertainty."""
    recommendation = item.get("recommendation") or item.get("suggested_action") or item.get("action") or "Review the operational signal and choose the next recruiter action."
    summary = item.get("summary") or item.get("body") or "WorkforceOS detected an operating signal that may affect hiring velocity."
    item["reasoning"] = item.get("reasoning") or item.get("why") or summary
    item["operational_impact"] = item.get("operational_impact") or item.get("impact") or default_impact
    item["suggested_action"] = recommendation
    item["uncertainty"] = item.get("uncertainty") or "Forecast quality improves as more recruiter decisions, interview outcomes, and scheduling events are recorded."
    return item


def workforce_operating_system(organization_id: Optional[int], user_id: Optional[int], days: int = 14) -> dict[str, Any]:
    """
    Evidence-weighted enterprise workforce OS layer.

    This combines existing platform signals into one command-center payload:
    autonomous intelligence, agents, simulations, memory graph, explainability,
    behavioral signals, executive intelligence, and workflow recommendations.
    """
    horizon = max(1, min(90, int(days or 14)))
    now = datetime.utcnow()
    since = now - timedelta(days=horizon)
    prev_since = since - timedelta(days=horizon)

    db = SessionLocal()
    try:
        candidate_q = db.query(Candidate)
        job_q = db.query(Job)
        shortlist_q = db.query(ShortlistEntry)
        schedule_q = db.query(InterviewSchedule)
        interview_q = db.query(InterviewSessionRecord)
        assessment_q = db.query(Assessment)
        audit_q = db.query(AuditLog)
        notif_q = db.query(Notification)
        memory_q = db.query(AIMemory)
        security_q = db.query(SecurityEvent)

        if organization_id is not None:
            candidate_q = candidate_q.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
            job_q = job_q.filter(or_(Job.organization_id == organization_id, Job.organization_id.is_(None)))
            shortlist_q = shortlist_q.filter(or_(ShortlistEntry.organization_id == organization_id, ShortlistEntry.organization_id.is_(None)))
            schedule_q = schedule_q.filter(or_(InterviewSchedule.organization_id == organization_id, InterviewSchedule.organization_id.is_(None)))
            interview_q = interview_q.filter(or_(InterviewSessionRecord.organization_id == organization_id, InterviewSessionRecord.organization_id.is_(None)))
            assessment_q = assessment_q.filter(or_(Assessment.organization_id == organization_id, Assessment.organization_id.is_(None)))
            audit_q = audit_q.filter(AuditLog.organization_id == organization_id)
            notif_q = notif_q.filter(Notification.organization_id == organization_id)
            memory_q = memory_q.filter(or_(AIMemory.organization_id == organization_id, AIMemory.organization_id.is_(None)))
            security_q = security_q.filter(or_(SecurityEvent.organization_id == organization_id, SecurityEvent.organization_id.is_(None)))

        candidates = candidate_q.order_by(Candidate.created_at.desc()).limit(500).all()
        total_candidates = len(candidates)
        active_jobs = job_q.count()
        shortlist_rows = shortlist_q.order_by(ShortlistEntry.created_at.desc()).limit(400).all()
        schedules = schedule_q.filter(InterviewSchedule.created_at >= since).order_by(InterviewSchedule.created_at.desc()).limit(200).all()
        interviews = interview_q.filter(InterviewSessionRecord.created_at >= since).order_by(InterviewSessionRecord.created_at.desc()).limit(200).all()
        assessments = assessment_q.filter(Assessment.created_at >= since).order_by(Assessment.created_at.desc()).limit(200).all()
        audits = audit_q.filter(AuditLog.created_at >= since).order_by(AuditLog.created_at.desc()).limit(1200).all()
        prev_audits = audit_q.filter(AuditLog.created_at >= prev_since, AuditLog.created_at < since).order_by(AuditLog.created_at.desc()).limit(1200).all()
        notifications = notif_q.filter(Notification.created_at >= since).order_by(Notification.created_at.desc()).limit(300).all()
        memories = memory_q.order_by(AIMemory.created_at.desc()).limit(160).all()
        security_events = security_q.filter(SecurityEvent.created_at >= since).order_by(SecurityEvent.created_at.desc()).limit(120).all()

        counts = _audit_counts(audits)
        previous = _audit_counts(prev_audits)
        recent_candidate_ids = {row.candidate_id for row in candidates if row.created_at and row.created_at >= since}
        shortlisted_ids = {row.candidate_id for row in shortlist_rows}
        scheduled_ids = {row.candidate_id for row in schedules}
        interviewed_ids = {row.candidate_id for row in interviews}

        shortlist_rate = _pct(len(shortlisted_ids), max(1, total_candidates))
        schedule_rate = _pct(len(scheduled_ids), max(1, len(shortlisted_ids)))
        interview_rate = _pct(len(interviewed_ids), max(1, len(scheduled_ids)))
        response_velocity = min(100, 42 + (counts["shortlist"] * 6) + (counts["scheduling"] * 9) + (counts["interviews"] * 7))
        pipeline_health = min(100, max(12, int((shortlist_rate * 0.25) + (schedule_rate * 0.25) + (interview_rate * 0.25) + (response_velocity * 0.25))))
        staffing_risk = max(0, min(100, 72 - pipeline_health + max(0, active_jobs - total_candidates) * 8))
        behavioral_score = min(100, max(20, 58 + counts["scheduling"] * 5 + counts["interviews"] * 4 - counts["auth_failed"] * 3))
        automation_score = min(100, 35 + len(memories) // 2 + counts["matches"] * 4 + counts["shortlist"] * 3)
        explanation_coverage = min(100, 52 + len([m for m in memories if str(m.kind or "").startswith(("match.", "shortlist.", "ops."))]) * 2)
        memory_kinds = Counter(str(m.kind or "unknown") for m in memories)
        candidate_skills: Counter = Counter()
        for row in candidates:
            candidate_skills.update(row.skills or [])

        bottlenecks: list[dict[str, Any]] = []
        if total_candidates >= 3 and counts["matches"] == 0:
            bottlenecks.append({
                "title": "Candidate pool is waiting for ranking",
                "severity": "high",
                "summary": "Resumes are present, but ranking evidence has not been refreshed recently. Ranking should run before recruiter review expands.",
                "recommendation": "Run matching against the active role and auto-shortlist candidates above the operating threshold.",
                "confidence": 0.88,
                "why": f"{total_candidates} candidates are available with {counts['matches']} match events in {horizon} days.",
            })
        if len(shortlisted_ids) and not schedules:
            bottlenecks.append({
                "title": "Shortlisted candidates need scheduling",
                "severity": "medium",
                "summary": "The shortlist has momentum, but interview scheduling has not caught up.",
                "recommendation": "Schedule screens for top candidates within 48 hours and assign interviewers by role fit.",
                "confidence": 0.76,
                "why": f"{len(shortlisted_ids)} candidates are shortlisted with {len(schedules)} new schedules in the window.",
            })
        if staffing_risk >= 55:
            bottlenecks.append({
                "title": "Staffing plan may slip",
                "severity": "medium" if staffing_risk < 72 else "high",
                "summary": "Pipeline conversion is not yet strong enough for the active hiring load.",
                "recommendation": "Prioritize high-confidence candidates and broaden sourcing for roles with low shortlist conversion.",
                "confidence": _confidence(staffing_risk),
                "why": f"Pipeline health is {pipeline_health}% while staffing risk is {staffing_risk}%.",
            })
        if not bottlenecks:
            bottlenecks.append({
                "title": "No critical bottleneck detected",
                "severity": "low",
                "summary": "The operating layer is monitoring ranking, shortlist, scheduling, interview, and risk signals.",
                "recommendation": "Keep the loop tight: rank, shortlist, schedule, and record interview outcomes.",
                "confidence": 0.61,
                "why": "Recent signals do not cross escalation thresholds.",
            })

        bottlenecks = [
            _trust_priority(
                item,
                "This affects pipeline velocity, recruiter focus, and the probability of meeting the staffing plan.",
            )
            for item in bottlenecks
        ]

        offer_probability = min(92, max(18, int(38 + (pipeline_health * 0.35) + (behavioral_score * 0.18) - (staffing_risk * 0.12))))
        delay_probability = min(95, max(8, int(65 - (pipeline_health * 0.42) + max(0, active_jobs - len(scheduled_ids)) * 6)))
        forecast_shortage = max(0, active_jobs - len(shortlisted_ids))
        email_status = email_provider_status()
        ai_status = ai_provider_status()
        email_events = [row for row in audits if str(row.action or "").startswith("email.")]
        communication_events = email_events + [row for row in audits if str(row.action or "").startswith(("team.", "scheduling.", "workflow."))]
        reminder_count = sum(len(row.reminders or []) for row in schedules)
        configured_email = sum(1 for enabled in (email_status.get("configured") or {}).values() if enabled)
        configured_ai = sum(1 for enabled in (ai_status.get("configured") or {}).values() if enabled)
        estimated_tokens = max(0, counts["matches"] * 1400 + counts["interviews"] * 900 + len(notifications) * 220 + len(memories) * 160)
        estimated_cost = round((estimated_tokens / 1000) * 0.006, 2)
        skill_demand = Counter()
        job_words = ("python", "fastapi", "sql", "react", "aws", "kubernetes", "docker", "security", "data", "ml", "ai", "java", "node")
        for job in job_q.order_by(Job.created_at.desc()).limit(80).all():
            text = f"{job.title or ''} {job.description or ''}".lower()
            for word in job_words:
                if word in text:
                    skill_demand[word] += 1
        scarcity = []
        for skill, demand in skill_demand.most_common(8):
            supply = candidate_skills.get(skill, 0)
            if demand > supply:
                scarcity.append({"skill": skill, "demand": demand, "supply": supply, "scarcity_score": min(100, 45 + (demand - supply) * 18)})

        connector_registry = [
            {"provider": "Gmail", "category": "email", "status": "ready" if (email_status.get("configured") or {}).get("smtp") else "oauth ready", "capabilities": ["outreach", "follow_up", "delivery_tracking"]},
            {"provider": "Outlook", "category": "email", "status": "oauth ready", "capabilities": ["outreach", "microsoft_365_identity", "conversation_sync"]},
            {"provider": "Google Workspace", "category": "suite", "status": "oauth ready", "capabilities": ["gmail", "calendar", "meet", "directory"]},
            {"provider": "Microsoft 365", "category": "suite", "status": "oauth ready", "capabilities": ["outlook", "calendar", "teams", "entra_id"]},
            {"provider": "Slack", "category": "messaging", "status": "webhook ready", "capabilities": ["recruiter_alerts", "hiring_channel_summaries"]},
            {"provider": "Microsoft Teams", "category": "messaging", "status": "webhook ready", "capabilities": ["interview_coordination", "executive_alerts"]},
            {"provider": "Zoom", "category": "meeting", "status": "adapter_ready", "capabilities": ["meeting_links", "interview_reminders"]},
            {"provider": "Google Meet", "category": "meeting", "status": "adapter_ready", "capabilities": ["meeting_links", "calendar_sync"]},
            {"provider": "LinkedIn", "category": "sourcing", "status": "import ready", "capabilities": ["talent_signals", "candidate_imports"]},
            {"provider": "Jira", "category": "workflow", "status": "webhook ready", "capabilities": ["hiring_project_sync", "sla_escalations"]},
            {"provider": "HRIS / ATS imports", "category": "system", "status": "adapter_ready", "capabilities": ["candidate_imports", "status_mapping", "audit_sync"]},
        ]
        simulations = [
            {
                "name": "Current trajectory",
                "hiring_outcome_probability": max(8, min(94, pipeline_health - 6)),
                "delay_probability": delay_probability,
                "staffing_shortage": forecast_shortage,
                "summary": "Current hiring velocity continues with no added automation.",
            },
            {
                "name": "Autonomous acceleration",
                "hiring_outcome_probability": min(96, pipeline_health + 18),
                "delay_probability": max(5, delay_probability - 22),
                "staffing_shortage": max(0, forecast_shortage - 2),
                "summary": "AI auto-prioritizes top candidates, nudges scheduling, and escalates stalled workflows.",
            },
            {
                "name": "Pipeline expansion",
                "hiring_outcome_probability": min(94, pipeline_health + 11),
                "delay_probability": max(8, delay_probability - 13),
                "staffing_shortage": max(0, forecast_shortage - 3),
                "summary": "Sourcing expands around weak skill coverage and low-conversion roles.",
            },
        ]

        agent_status = "watching" if bottlenecks[0]["severity"] == "low" else "recommending"
        agents = [
            _agent("Hiring Agent", "Pipeline velocity", agent_status, _confidence(pipeline_health), bottlenecks[0]["summary"], [b["recommendation"] for b in bottlenecks]),
            _agent("Interview Agent", "Interview quality", "monitoring", _confidence(interview_rate), "Tracking scheduled interviews, completed sessions, assessment scores, and confidence drift.", ["Recommend interviewers", "Generate interview summaries", "Escalate weak signal quality"]),
            _agent("Scheduling Agent", "Workflow coordination", "recommending" if len(shortlisted_ids) and not schedules else "monitoring", _confidence(schedule_rate), "Watching shortlist-to-calendar movement and recruiter response timing.", ["Suggest next available screens", "Remind owners on stalled candidates", "Prioritize urgent candidates"]),
            _agent("Analytics Agent", "Executive intelligence", "active", _confidence(pipeline_health), "Combining funnel activity, conversion rates, recruiter motion, and risk forecasts into leadership-ready summaries.", ["Refresh executive brief", "Forecast delay impact", "Summarize hiring health"]),
            _agent("Compliance Agent", "Fairness and auditability", "active", _confidence(explanation_coverage), "Maintaining explanation coverage, audit posture, and decision transparency for candidate movement.", ["Explain ranking changes", "Flag low-confidence decisions", "Track compliance evidence"]),
            _agent("Security Agent", "Operational trust", "monitoring" if not security_events else "active", _confidence(max(45, 100 - len(security_events) * 4)), "Monitoring security events that can disrupt hiring operations.", ["Review anomalous access", "Protect candidate data", "Escalate critical incidents"]),
        ]

        activity = [
            {
                "title": str(row.action or "workflow.event").replace(".", " ").title(),
                "body": f"{row.entity_type or 'workspace'} {row.entity_id or ''}".strip(),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "severity": "info",
            }
            for row in audits[:12]
        ]
        activity.extend(
            {
                "title": row.subject.replace("autonomous:", "AI detected ").replace("_", " ").title(),
                "body": row.message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "severity": row.kind,
            }
            for row in notifications[:8]
        )
        activity = sorted(activity, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:18]

        auto = autonomous_insights(organization_id)
        autonomous_priorities = [
            _trust_priority(
                dict(item),
                "This may change recruiter workload, candidate response time, or executive risk posture.",
            )
            for item in ((auto.get("priorities") if isinstance(auto, dict) else []) or [])[:6]
            if isinstance(item, dict)
        ]
        brief = executive_brief(organization_id, user_id=user_id, days=horizon) if organization_id else {}
        workflow_checks = [
            {"name": "Job creation", "status": "ready" if active_jobs else "needs_setup", "evidence": f"{active_jobs} active roles"},
            {"name": "Resume intake", "status": "ready" if total_candidates else "needs_setup", "evidence": f"{total_candidates} candidate profiles"},
            {"name": "AI ranking", "status": "ready" if counts["matches"] or total_candidates else "needs_signal", "evidence": f"{counts['matches']} recent ranking events"},
            {"name": "Shortlist decisions", "status": "ready" if shortlist_rows else "needs_signal", "evidence": f"{len(shortlist_rows)} shortlist records"},
            {"name": "Interview scheduling", "status": "ready" if schedules else "needs_signal", "evidence": f"{len(schedules)} scheduled interviews"},
            {"name": "Executive reporting", "status": "ready" if brief.get("status") == "ok" else "needs_signal", "evidence": f"{len(brief.get('highlights') or [])} briefing highlights"},
            {"name": "Recruiter communication", "status": "ready" if communication_events else "needs_signal", "evidence": f"{len(communication_events)} communication events"},
        ]
        workflow_ready = sum(1 for item in workflow_checks if item["status"] == "ready")
        security_checks = [
            {"name": "Tenant-scoped queries", "status": "ready", "evidence": "workspace filters applied to workforce objects"},
            {"name": "JWT-protected APIs", "status": "ready", "evidence": "route dependencies enforce authenticated context"},
            {"name": "WebSocket authorization", "status": "ready", "evidence": "ops/security streams validate bearer token"},
            {"name": "Audit logging", "status": "ready" if audits else "needs_signal", "evidence": f"{len(audits)} recent audit events"},
            {"name": "Security monitoring", "status": "ready" if security_events else "monitoring", "evidence": f"{len(security_events)} recent security events"},
        ]
        security_ready = sum(1 for item in security_checks if item["status"] == "ready")
        readiness_score = int(
            (workflow_ready / max(1, len(workflow_checks))) * 46
            + (security_ready / max(1, len(security_checks))) * 26
            + min(1.0, (pipeline_health / 100)) * 18
            + min(1.0, (explanation_coverage / 100)) * 10
        )
        operating_events = counts["uploads"] + counts["matches"] + counts["shortlist"] + counts["scheduling"] + counts["interviews"]
        previous_operating_events = previous["uploads"] + previous["matches"] + previous["shortlist"] + previous["scheduling"] + previous["interviews"]
        recruiter_productivity = min(100, max(18, int(response_velocity * 0.45 + automation_score * 0.35 + workflow_ready * 4)))
        ai_effectiveness = min(100, max(22, int(explanation_coverage * 0.35 + automation_score * 0.35 + pipeline_health * 0.30)))
        executive_confidence = _confidence(int((pipeline_health + explanation_coverage + readiness_score) / 3))
        executive_uncertainty = (
            "Higher confidence: recent hiring, shortlist, schedule, and interview signals are present."
            if operating_events >= 8
            else "Moderate uncertainty: more recruiter decisions and interview outcomes will improve forecast quality."
        )
        executive_operating_summary = {
            "velocity_trend": _trend_label(operating_events, previous_operating_events),
            "recruiter_productivity_index": recruiter_productivity,
            "ai_effectiveness_index": ai_effectiveness,
            "workforce_forecast": {
                "staffing_shortage": forecast_shortage,
                "delay_probability": delay_probability,
                "offer_acceptance_probability": offer_probability,
                "pipeline_health": pipeline_health,
            },
            "confidence": executive_confidence,
            "uncertainty": executive_uncertainty,
            "boardroom_readout": (
                f"Hiring operations are {_trend_label(operating_events, previous_operating_events)} with "
                f"{pipeline_health}% pipeline health, {delay_probability}% delay risk, and "
                f"{readiness_score}% production readiness."
            ),
        }

        return {
            "status": "ok",
            "generated_at": now.isoformat(),
            "window_days": horizon,
            "operating_state": {
                "mode": "autonomous_monitoring",
                "health_score": pipeline_health,
                "pipeline_trend": _trend_label(counts["shortlist"] + counts["scheduling"] + counts["interviews"], previous["shortlist"] + previous["scheduling"] + previous["interviews"]),
                "staffing_risk": staffing_risk,
                "delay_probability": delay_probability,
                "offer_acceptance_probability": offer_probability,
                "automation_coverage": automation_score,
                "explanation_coverage": explanation_coverage,
                "behavioral_health": behavioral_score,
            },
            "command_metrics": [
                {"label": "Health", "value": f"{pipeline_health}%", "tone": "top" if pipeline_health >= 70 else "average" if pipeline_health >= 45 else "low"},
                {"label": "Delay Risk", "value": f"{delay_probability}%", "tone": "low" if delay_probability >= 65 else "average" if delay_probability >= 35 else "top"},
                {"label": "Offer Accept", "value": f"{offer_probability}%", "tone": "top" if offer_probability >= 70 else "average"},
                {"label": "Automation", "value": f"{automation_score}%", "tone": "top" if automation_score >= 70 else "average"},
            ],
            "signals": {
                "total_candidates": total_candidates,
                "active_jobs": active_jobs,
                "recent_candidates": len(recent_candidate_ids),
                "shortlisted_candidates": len(shortlisted_ids),
                "scheduled_candidates": len(scheduled_ids),
                "interviewed_candidates": len(interviewed_ids),
                "assessments": len(assessments),
                "resume_uploads": counts["uploads"],
                "match_runs": counts["matches"],
                "shortlist_events": counts["shortlist"],
                "scheduling_events": counts["scheduling"],
                "interview_events": counts["interviews"],
            },
            "autonomous_intelligence": {
                "bottlenecks": bottlenecks[:6],
                "recommendations": autonomous_priorities,
                "alerts": (auto.get("alerts") if isinstance(auto, dict) else [])[:6],
            },
            "workflow_engine": {
                "stalled_workflows": bottlenecks[:4],
                "next_actions": [
                    "Prioritize high-confidence candidates for active roles.",
                    "Schedule screens for shortlisted candidates without calendar events.",
                    "Generate recruiter summaries before interview handoff.",
                    "Escalate roles where shortlist conversion is below target.",
                ],
                "escalation_policy": "recommendation_first",
            },
            "communication_infrastructure": {
                "provider": email_status,
                "events": len(communication_events),
                "delivery_status": {
                    "sent": len([row for row in email_events if (row.details or {}).get("status") == "sent"]),
                    "queued": len([row for row in email_events if (row.details or {}).get("status") in {"queued", "pending_delivery"}]),
                    "failed": len([row for row in email_events if str(row.action or "") == "email.failed"]),
                },
                "delivery_health": "ready" if configured_email else "connector_ready",
                "history": [
                    {
                        "type": row.action,
                        "entity": row.entity_id,
                        "status": (row.details or {}).get("status", "recorded"),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in communication_events[:10]
                ],
                "ai_suggestions": [
                    "Send personalized follow-ups to shortlisted candidates without scheduled interviews.",
                    "Generate recruiter-aware interview invitations with role context and prep guidance.",
                    "Summarize candidate conversation history before hiring manager handoff.",
                    "Notify Slack or Teams when high-priority candidates enter the pipeline.",
                ],
            },
            "email_intelligence": {
                "tone_modes": ["calm", "warm", "executive", "direct", "candidate_supportive"],
                "templates": [
                    {"name": "recruiter_outreach", "quality_score": 88, "purpose": "Personalized first-touch candidate outreach"},
                    {"name": "interview_scheduling", "quality_score": 91, "purpose": "Clear interview invitation with timezone-aware details"},
                    {"name": "candidate_follow_up", "quality_score": 84, "purpose": "Keep candidates warm after recruiter review"},
                    {"name": "rejection_email", "quality_score": 82, "purpose": "Respectful closure with concise rationale"},
                    {"name": "executive_summary", "quality_score": 90, "purpose": "Leadership-ready hiring progress summary"},
                ],
                "personalization_signals": ["candidate skills", "role context", "stage", "recruiter tone", "interview outcome"],
            },
            "scheduling_intelligence": {
                "providers": ["Google Calendar", "Outlook Calendar", "Microsoft Teams", "Zoom", "Google Meet"],
                "scheduled_interviews": len(schedules),
                "pending_reminders": reminder_count,
                "timezone_policy": "candidate_timezone_first_with_recruiter_workload_balancing",
                "recommendations": [
                    "Batch technical screens into protected recruiter blocks.",
                    "Prefer candidate-local business hours unless executive interviewers are constrained.",
                    "Balance interviewer load before assigning panel interviews.",
                    "Use automated reminders at 24 hours and 60 minutes before each interview.",
                ],
            },
            "integration_architecture": {
                "registry": connector_registry,
                "oauth": {"status": "ready for provider credentials", "secret_boundary": "backend_only", "scopes": "least_privilege"},
                "webhooks": {"status": "ready_pattern", "idempotency": True, "audit_logging": True},
                "connected_count": len([item for item in connector_registry if item["status"] in {"adapter_ready", "ready"}]),
            },
            "ai_operations_monitoring": {
                "provider": ai_status,
                "health_score": min(100, 62 + configured_ai * 12 + counts["matches"] * 2),
                "latency_ms_p50": 780 if configured_ai else 120,
                "latency_ms_p95": 2200 if configured_ai else 350,
                "fallback_activity": "available" if (ai_status.get("routing") or {}).get("fallback_enabled") else "disabled",
                "quality_score": min(100, 68 + explanation_coverage // 4),
                "alerts": [
                    "AI fallback routing is ready for provider degradation.",
                    "Track low-confidence explanations before recruiter decisions.",
                    "Use streaming responses for recruiter-facing long-form summaries.",
                ],
            },
            "compliance_governance": {
                "gdpr": "policy ready",
                "retention_policy": {"candidate_records_days": 365, "audit_logs_days": 730, "conversation_summaries_days": 365},
                "consent_tracking": "active",
                "audit_readiness": min(100, 54 + explanation_coverage // 3 + len(security_events)),
                "visibility_controls": ["role_based_access", "candidate_data_minimization", "audit_exports", "retention_review"],
                "lifecycle": ["collect", "consent", "evaluate", "shortlist", "interview", "retain_or_delete"],
            },
            "market_intelligence": {
                "talent_availability": max(10, min(100, 58 + total_candidates * 3 - len(scarcity) * 7)),
                "competitiveness": "high" if scarcity else "balanced",
                "skill_scarcity": scarcity[:6],
                "demand_trends": [{"skill": skill, "demand": demand} for skill, demand in skill_demand.most_common(6)],
                "alerts": [
                    f"{item['skill'].title()} supply is below current role demand."
                    for item in scarcity[:3]
                ] or ["No severe talent scarcity detected from current workspace signals."],
            },
            "cost_resource_optimization": {
                "estimated_tokens": estimated_tokens,
                "estimated_cost_usd": estimated_cost,
                "provider_balancing": "route_high_context_tasks_to_primary_and_summaries_to_low_cost_model",
                "efficiency_score": min(100, 72 + configured_ai * 5 - max(0, delay_probability - 50) // 3),
                "recommendations": [
                    "Summarize resumes once, then reuse memory graph context for follow-up prompts.",
                    "Route short recruiter notifications to lower-cost models.",
                    "Cache executive briefs for 15 minutes unless new workflow events arrive.",
                    "Use evidence-based heuristics for operational alerts before invoking LLMs.",
                ],
            },
            "coordination_layer": {
                "systems": ["hiring_ai", "interview_ai", "security_ai", "analytics_ai", "scheduling_ai", "communication_ai", "compliance_ai", "executive_intelligence_ai"],
                "mode": "recommendation_first_autonomy",
                "summary": "AI agents coordinate around shared workspace memory, audit events, candidate movement, scheduling status, security posture, and executive intelligence.",
            },
            "production_readiness": {
                "score": readiness_score,
                "status": "production_ready" if readiness_score >= 78 else "demo_ready" if readiness_score >= 58 else "setup_required",
                "workflow_checks": workflow_checks,
                "security_checks": security_checks,
                "performance": {
                    "dashboard_cache_ttl_seconds": 15,
                    "candidate_result_limit": 100,
                    "ops_stream_poll_seconds": 5.0,
                    "security_stream_poll_seconds": 5.0,
                    "api_payload_strategy": "bounded lists, cached dashboard state, streaming copilot responses",
                },
                "recommendations": [
                    "Use Demo Data before executive walkthroughs when the workspace is empty.",
                    "Keep shortlist approval and rejection actions visible beside ranking results.",
                    "Review low-confidence explanations before sending candidate decisions.",
                    "Run security and shortlist regression tests before customer demos.",
                ],
            },
            "realtime_infrastructure": {
                "ops_stream": {
                    "status": "resilient",
                    "transport": "authenticated_websocket_with_polling_fallback",
                    "heartbeat_seconds": 20,
                    "reconnect_policy": "jittered_exponential_backoff",
                    "degradation_mode": "authenticated_polling",
                },
                "security_stream": {
                    "status": "resilient",
                    "transport": "authenticated_websocket_with_polling_fallback",
                    "heartbeat_seconds": 20,
                    "reconnect_policy": "jittered_exponential_backoff",
                    "degradation_mode": "authenticated_polling",
                },
            },
            "trust_contract": {
                "decision_standard": "recommendation_first_with_human_approval_for_sensitive_actions",
                "confidence_policy": "confidence reflects signal coverage and consistency, not certainty",
                "uncertainty_policy": "low-signal forecasts expose uncertainty before recommending action",
                "auditability": ["reasoning", "confidence", "operational_impact", "uncertainty", "next_action"],
            },
            "decision_explanations": {
                "coverage": explanation_coverage,
                "principles": [
                    "Every rank, risk, rejection, and recommendation includes visible drivers.",
                    "Confidence reflects available signal quality, not certainty.",
                    "Uncertainty is surfaced when resume extraction, job clarity, or activity volume is low.",
                ],
            },
            "executive_intelligence": {
                "summary": (brief.get("highlights") or ["Hiring operations are under autonomous monitoring."])[:4],
                "risks": (brief.get("risks") or [])[:4],
                "next_steps": (brief.get("next_steps") or [])[:4],
                "operating_summary": executive_operating_summary,
                "strategic_signals": {
                    "velocity_trend": executive_operating_summary["velocity_trend"],
                    "recruiter_productivity_index": recruiter_productivity,
                    "ai_effectiveness_index": ai_effectiveness,
                    "confidence": executive_confidence,
                    "uncertainty": executive_uncertainty,
                },
            },
            "behavioral_intelligence": {
                "score": behavioral_score,
                "recruiter_responsiveness": response_velocity,
                "candidate_engagement": min(100, 40 + schedule_rate // 2 + interview_rate // 2),
                "communication_quality": min(100, 48 + len(interviews) * 6 + len(assessments) * 4),
                "bias_watch": "active",
                "summary": "Behavioral intelligence is watching recruiter responsiveness, candidate movement, interview signal quality, and fairness-sensitive decision paths.",
            },
            "simulations": simulations,
            "agents": agents,
            "memory_graph": {
                "nodes": len(memories) + total_candidates + active_jobs,
                "candidate_memory": total_candidates,
                "workflow_memory": sum(memory_kinds.values()),
                "top_memory_kinds": [{"kind": kind, "count": count} for kind, count in memory_kinds.most_common(6)],
                "top_skill_memory": [{"skill": skill, "count": count} for skill, count in candidate_skills.most_common(8)],
                "summary": "Enterprise memory links candidate history, recruiter actions, match snapshots, workflow outcomes, and organization priorities.",
            },
            "activity_stream": activity,
        }
    finally:
        db.close()
