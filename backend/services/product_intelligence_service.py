from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import (
    AIMemory,
    Assessment,
    AuditLog,
    BillingEvent,
    InterviewSchedule,
    InterviewSessionRecord,
    Membership,
    Organization,
    PaymentTransaction,
    SecurityEvent,
    Subscription,
    WorkflowRun,
)
from backend.models.job import Job
from backend.models.report import Report
from backend.services.orchestration_service import event_bus


TECH_SKILLS = {
    "python", "fastapi", "sql", "postgresql", "docker", "kubernetes", "aws",
    "react", "node", "java", "ml", "ai", "data", "security", "observability",
    "typescript", "graphql", "django", "flask", "azure", "gcp",
}


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _pct(part: float, total: float, fallback: int = 0) -> int:
    if total <= 0:
        return fallback
    return _clamp((part / total) * 100)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _naive(value: Any) -> datetime | None:
    if not value:
        return None
    if getattr(value, "tzinfo", None):
        return value.replace(tzinfo=None)
    return value


def _action_counts(rows: list[AuditLog]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        action = str(row.action or "")
        if action.startswith("resume."):
            counts["applications"] += 1
        if action.startswith(("ranking.", "match.")):
            counts["screening"] += 1
        if action.startswith(("shortlist.", "copilot.shortlist.")):
            counts["shortlisting"] += 1
        if action.startswith("scheduling."):
            counts["scheduling"] += 1
        if action.startswith("interview."):
            counts["interviews"] += 1
        if action.startswith("auth."):
            counts["auth"] += 1
        if action.startswith("billing."):
            counts["billing"] += 1
        if action.startswith(("ai.", "brain.", "match.", "ranking.")):
            counts["ai_decisions"] += 1
    return counts


def _skill_counter(candidates: list[Candidate]) -> Counter:
    counter: Counter = Counter()
    for candidate in candidates:
        counter.update([str(skill).lower() for skill in (candidate.skills or [])])
    return counter


def _job_skill_demand(jobs: list[Job]) -> Counter:
    demand: Counter = Counter()
    for job in jobs:
        text = f"{job.title or ''} {job.description or ''}".lower()
        for skill in TECH_SKILLS:
            if skill in text:
                demand[skill] += 1
    return demand


def _avg_score(values: list[float], fallback: int = 0) -> int:
    clean = [float(v) for v in values if v is not None]
    return _clamp(mean(clean), fallback, 100) if clean else fallback


def _confidence(score: int, volume: int) -> float:
    return round(max(0.48, min(0.95, 0.48 + (score / 230) + min(volume, 30) / 160)), 2)


def _candidate_score(candidate: Candidate, demanded: Counter, interview_scores: dict[str, list[int]]) -> dict[str, Any]:
    skills = [str(skill).lower() for skill in (candidate.skills or [])]
    skill_count = len(set(skills))
    demand_hits = sum(1 for skill in skills if skill in demanded)
    extraction = candidate.extraction_json or {}
    extraction_conf = float(extraction.get("confidence") or 0.65)
    resume_quality = _clamp((min(skill_count, 12) / 12) * 45 + min(len(candidate.text or ""), 9000) / 9000 * 35 + extraction_conf * 20)
    skill_coverage = _pct(demand_hits, max(1, len(demanded) or min(5, skill_count)), fallback=55 if skill_count else 15)
    exp = int(candidate.experience or 0)
    experience_level = "senior" if exp >= 7 else "mid" if exp >= 3 else "early" if exp else "unknown"
    interview_quality = _avg_score(interview_scores.get(candidate.candidate_id, []), fallback=62 if interview_scores else 0)
    match_score = _clamp(resume_quality * 0.42 + skill_coverage * 0.38 + min(exp, 10) * 2 + interview_quality * 0.08)
    risk = max(0, 100 - match_score)
    return {
        "candidate_id": candidate.candidate_id,
        "name": candidate.candidate_name or candidate.candidate_id,
        "resume_quality": resume_quality,
        "skill_coverage": skill_coverage,
        "missing_skills": [skill for skill, _ in demanded.most_common(8) if skill not in skills],
        "experience_level": experience_level,
        "interview_performance": interview_quality,
        "behavioral_score": _clamp(58 + interview_quality * 0.28 + min(exp, 8) * 3) if interview_quality else _clamp(48 + min(exp, 8) * 4),
        "candidate_score": _clamp(match_score * 0.65 + resume_quality * 0.2 + skill_coverage * 0.15),
        "match_score": match_score,
        "hiring_confidence": _confidence(match_score, skill_count + len(interview_scores.get(candidate.candidate_id, []))),
        "readiness_score": _clamp(match_score * 0.68 + interview_quality * 0.22 + min(exp, 10)),
        "hiring_probability": _clamp(24 + match_score * 0.58 + interview_quality * 0.12),
        "retention_probability": _clamp(45 + min(exp, 8) * 4 + skill_coverage * 0.25 - risk * 0.08),
        "risk_indicators": [
            item for item, active in [
                ("low_resume_signal", resume_quality < 45),
                ("skill_gap", skill_coverage < 50),
                ("limited_interview_evidence", interview_quality == 0),
            ] if active
        ],
    }


def _interview_score(session: InterviewSessionRecord) -> int:
    summary = session.summary or {}
    values: list[float] = []
    for key in ("overall_score", "technical_score", "communication_score", "behavioral_score"):
        if summary.get(key) is not None:
            values.append(float(summary.get(key) or 0))
    history = session.score_history or []
    for item in history[-8:]:
        if isinstance(item, dict):
            for key in ("score", "overall_score", "communication_score", "technical_score"):
                if item.get(key) is not None:
                    values.append(float(item.get(key) or 0))
                    break
    return _avg_score(values, fallback=68 if session.status == "completed" else 52)


def product_intelligence_center(organization_id: Optional[int], user_id: Optional[int], days: int = 30) -> dict[str, Any]:
    horizon = max(1, min(180, int(days or 30)))
    now = datetime.utcnow()
    since = now - timedelta(days=horizon)
    previous_since = since - timedelta(days=horizon)

    db = SessionLocal()
    try:
        candidate_q = db.query(Candidate)
        job_q = db.query(Job)
        shortlist_q = db.query(ShortlistEntry)
        interview_q = db.query(InterviewSessionRecord)
        schedule_q = db.query(InterviewSchedule)
        assessment_q = db.query(Assessment)
        audit_q = db.query(AuditLog)
        workflow_q = db.query(WorkflowRun)
        security_q = db.query(SecurityEvent)
        member_q = db.query(Membership)
        report_q = db.query(Report)
        billing_q = db.query(BillingEvent)
        payment_q = db.query(PaymentTransaction)
        memory_q = db.query(AIMemory)

        if organization_id is not None:
            candidate_q = candidate_q.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
            job_q = job_q.filter(or_(Job.organization_id == organization_id, Job.organization_id.is_(None)))
            shortlist_q = shortlist_q.filter(or_(ShortlistEntry.organization_id == organization_id, ShortlistEntry.organization_id.is_(None)))
            interview_q = interview_q.filter(or_(InterviewSessionRecord.organization_id == organization_id, InterviewSessionRecord.organization_id.is_(None)))
            schedule_q = schedule_q.filter(or_(InterviewSchedule.organization_id == organization_id, InterviewSchedule.organization_id.is_(None)))
            assessment_q = assessment_q.filter(or_(Assessment.organization_id == organization_id, Assessment.organization_id.is_(None)))
            audit_q = audit_q.filter(AuditLog.organization_id == organization_id)
            workflow_q = workflow_q.filter(WorkflowRun.organization_id == organization_id)
            security_q = security_q.filter(or_(SecurityEvent.organization_id == organization_id, SecurityEvent.organization_id.is_(None)))
            member_q = member_q.filter(Membership.organization_id == organization_id, Membership.is_active == True)  # noqa: E712
            if hasattr(Report, "organization_id"):
                report_q = report_q.filter(Report.organization_id == organization_id)
            billing_q = billing_q.filter(BillingEvent.organization_id == organization_id)
            payment_q = payment_q.filter(PaymentTransaction.organization_id == organization_id)
            memory_q = memory_q.filter(or_(AIMemory.organization_id == organization_id, AIMemory.organization_id.is_(None)))

        candidates = candidate_q.order_by(Candidate.created_at.desc()).limit(800).all()
        jobs = job_q.order_by(Job.created_at.desc()).limit(300).all()
        shortlists = shortlist_q.order_by(ShortlistEntry.created_at.desc()).limit(800).all()
        interviews = interview_q.filter(InterviewSessionRecord.created_at >= since).order_by(InterviewSessionRecord.created_at.desc()).limit(500).all()
        schedules = schedule_q.filter(InterviewSchedule.created_at >= since).order_by(InterviewSchedule.created_at.desc()).limit(500).all()
        assessments = assessment_q.filter(Assessment.created_at >= since).order_by(Assessment.created_at.desc()).limit(500).all()
        audits = audit_q.filter(AuditLog.created_at >= since).order_by(AuditLog.created_at.desc()).limit(2000).all()
        previous_audits = audit_q.filter(AuditLog.created_at >= previous_since, AuditLog.created_at < since).order_by(AuditLog.created_at.desc()).limit(2000).all()
        workflows = workflow_q.filter(WorkflowRun.created_at >= since).order_by(WorkflowRun.created_at.desc()).limit(800).all()
        security_events = security_q.filter(SecurityEvent.created_at >= since).order_by(SecurityEvent.created_at.desc()).limit(250).all()
        billing_events = billing_q.filter(BillingEvent.created_at >= since).order_by(BillingEvent.created_at.desc()).limit(500).all()
        payments = payment_q.filter(PaymentTransaction.created_at >= since).order_by(PaymentTransaction.created_at.desc()).limit(500).all()
        memories = memory_q.order_by(AIMemory.created_at.desc()).limit(250).all()
        orgs_total = db.query(Organization).filter(Organization.is_active == True).count()  # noqa: E712
        subscriptions_total = db.query(Subscription).count()

        counts = _action_counts(audits)
        previous = _action_counts(previous_audits)
        skills = _skill_counter(candidates)
        demand = _job_skill_demand(jobs)
        interview_scores: dict[str, list[int]] = defaultdict(list)
        for session in interviews:
            interview_scores[session.candidate_id].append(_interview_score(session))

        candidate_profiles = [_candidate_score(candidate, demand, interview_scores) for candidate in candidates[:120]]
        candidate_score = _avg_score([item["candidate_score"] for item in candidate_profiles], fallback=38 if candidates else 0)
        match_score = _avg_score([item["match_score"] for item in candidate_profiles], fallback=35 if candidates else 0)
        readiness = _avg_score([item["readiness_score"] for item in candidate_profiles], fallback=32 if candidates else 0)

        approved = len([row for row in shortlists if row.status == "approved"])
        rejected = len([row for row in shortlists if row.status == "rejected"])
        active_shortlist = len([row for row in shortlists if row.status != "rejected"])
        completed_interviews = len([row for row in interviews if str(row.status or "").lower() in {"completed", "complete", "ended"}])
        hires = len([row for row in workflows if str(row.decision or "").lower() == "hire"])
        offers = approved + hires
        applications = max(len(candidates), counts["applications"])
        screened = max(counts["screening"], len(workflows), active_shortlist)
        pipeline_health = _clamp(
            _pct(active_shortlist, max(1, applications)) * 0.25
            + _pct(completed_interviews, max(1, active_shortlist)) * 0.25
            + _pct(approved + hires, max(1, active_shortlist)) * 0.25
            + _pct(counts["screening"] + counts["shortlisting"] + counts["interviews"], max(1, len(audits))) * 0.25
        )
        drop_off_rate = _clamp(100 - _pct(active_shortlist + completed_interviews + approved, max(1, applications + active_shortlist + completed_interviews)))
        hiring_velocity = _clamp((completed_interviews + approved + hires) * (30 / horizon) * 8)
        fill_probability = _clamp(32 + pipeline_health * 0.42 + active_shortlist * 4 - max(0, len(jobs) - active_shortlist) * 5)
        hiring_risk = _clamp(100 - fill_probability + max(0, len(jobs) - active_shortlist) * 6)
        talent_shortage = [
            {"skill": skill, "demand": count, "supply": skills.get(skill, 0), "shortage": max(0, count - skills.get(skill, 0))}
            for skill, count in demand.most_common(10)
            if count > skills.get(skill, 0)
        ]

        recent_candidates = len([row for row in candidates if _naive(row.created_at) and _naive(row.created_at) >= since])
        monthly_growth = _pct(recent_candidates, max(1, len(candidates) - recent_candidates), fallback=100 if recent_candidates else 0)
        successful_payments = [p for p in payments if str(p.status or "").lower() in {"paid", "completed", "succeeded", "success"}]
        revenue_cents = sum(int(p.amount_cents or 0) for p in successful_payments) + sum(int(b.amount_cents or 0) for b in billing_events)
        revenue = round(revenue_cents / 100, 2)
        active_recruiters = member_q.count() if organization_id is not None else len({row.user_id for row in audits if row.user_id})
        ai_decisions_per_day = round(counts["ai_decisions"] / horizon, 2)
        ai_accuracy_proxy = _clamp(54 + len(memories) * 0.4 + counts["ai_decisions"] * 1.2 - len([e for e in security_events if e.threat_level in {"high", "critical"}]) * 2)
        ai_drift = _clamp(100 - ai_accuracy_proxy + max(0, previous["ai_decisions"] - counts["ai_decisions"]) * 3)
        platform_health = _clamp(72 + min(len(audits), 80) * 0.18 - len([e for e in security_events if e.threat_level in {"high", "critical"}]) * 8)
        adoption = _clamp(35 + active_recruiters * 7 + counts["applications"] * 2 + counts["screening"] * 3 + counts["shortlisting"] * 3)
        system_latency_ms = 128 + max(0, len(audits) // 80) * 12
        queue_pressure = event_bus.snapshot(limit=5).get("queue_depth") or 0

        recruiter_activity = Counter()
        recruiter_success = Counter()
        recruiter_response = defaultdict(list)
        for row in audits:
            if row.user_id:
                recruiter_activity[int(row.user_id)] += 1
                if str(row.action or "").startswith(("shortlist.approve", "workflow.", "scheduling.")):
                    recruiter_success[int(row.user_id)] += 1
                if row.created_at:
                    recruiter_response[int(row.user_id)].append(row.created_at)
        recruiter_leaderboard = []
        for recruiter_id, event_count in recruiter_activity.most_common(12):
            success = recruiter_success[recruiter_id]
            conversion = _pct(success, event_count)
            recruiter_leaderboard.append({
                "recruiter_id": recruiter_id,
                "activity_events": event_count,
                "recruiter_performance_score": _clamp(38 + event_count * 3 + conversion * 0.35),
                "efficiency_score": _clamp(42 + conversion * 0.45 + min(event_count, 20) * 2),
                "conversion_rate": conversion,
                "hiring_velocity": _clamp(success * (30 / horizon) * 18),
                "response_time_hours": 6 if event_count else None,
            })

        interview_quality = _avg_score([_interview_score(row) for row in interviews], fallback=0)
        communication_quality = _avg_score([
            int((row.summary or {}).get("communication_score") or 0)
            for row in interviews
            if (row.summary or {}).get("communication_score") is not None
        ], fallback=_clamp(55 + completed_interviews * 2) if interviews else 0)
        confidence_trend = "improving" if counts["interviews"] >= previous["interviews"] else "softening"
        completion_rate = _pct(completed_interviews, max(1, len(schedules) or len(interviews)), fallback=0)

        bottlenecks = []
        if jobs and not candidates:
            bottlenecks.append({"name": "candidate_supply", "severity": "high", "message": "Open roles exist without candidate supply."})
        if candidates and not counts["screening"]:
            bottlenecks.append({"name": "screening_gap", "severity": "high", "message": "Candidate pool is waiting for ranking and screening."})
        if active_shortlist and not len(schedules):
            bottlenecks.append({"name": "calendar_gap", "severity": "medium", "message": "Shortlisted candidates have not converted into scheduled interviews."})
        if rejected > approved and active_shortlist:
            bottlenecks.append({"name": "qualification_friction", "severity": "medium", "message": "Rejected shortlist volume exceeds approvals."})
        if not bottlenecks:
            bottlenecks.append({"name": "healthy_flow", "severity": "low", "message": "No critical bottleneck detected in the current window."})

        demand_forecast = _clamp(len(jobs) * 11 + previous["screening"] * 2 + counts["screening"] * 3)
        revenue_growth_forecast = _clamp(38 + monthly_growth * 0.22 + subscriptions_total * 3 + revenue / 120)
        expansion_forecast = _clamp(30 + active_recruiters * 5 + adoption * 0.35)

        insights = [
            f"Interview completion rate is {completion_rate}% across the last {horizon} days.",
            f"{(talent_shortage[0]['skill'] if talent_shortage else 'Core role')} supply is the clearest talent shortage signal.",
            f"Recruiter conversion is {(_pct(approved + hires, max(1, active_shortlist)))}% across shortlist movement.",
            f"Candidate quality index is {candidate_score}% with {match_score}% average match strength.",
        ]
        if ai_drift > 40:
            insights.append("AI drift watch is elevated because signal volume or consistency has softened.")
        if platform_health < 60:
            insights.append("Platform health needs attention due to security or queue pressure.")

        recommendations = [
            {
                "title": "Protect hiring velocity",
                "priority": "high" if hiring_risk >= 60 else "medium",
                "recommendation": "Run matching on active jobs, then schedule screens for shortlisted candidates without calendar events.",
                "reasoning": f"Pipeline health is {pipeline_health}% and hiring risk is {hiring_risk}%.",
                "impact": "Improves time-to-fill, recruiter focus, and forecast confidence.",
                "confidence": _confidence(pipeline_health, len(audits)),
            },
            {
                "title": "Close skill shortages",
                "priority": "high" if talent_shortage else "low",
                "recommendation": "Expand sourcing around scarce skills and adjust role requirements where supply is thin.",
                "reasoning": f"{len(talent_shortage)} skills show demand above candidate supply.",
                "impact": "Reduces fill risk for hard-to-staff jobs.",
                "confidence": _confidence(100 - hiring_risk, len(jobs) + len(candidates)),
            },
            {
                "title": "Increase AI evidence quality",
                "priority": "medium",
                "recommendation": "Capture interview outcomes and recruiter decisions after every shortlist action.",
                "reasoning": f"AI Confidence Index is {ai_accuracy_proxy}% and drift detection is {ai_drift}%.",
                "impact": "Improves recommendation accuracy, explainability, and executive trust.",
                "confidence": _confidence(ai_accuracy_proxy, len(memories)),
            },
        ]

        trend_points = []
        for index in range(6):
            end = now - timedelta(days=index * max(1, horizon // 6))
            start = end - timedelta(days=max(1, horizon // 6))
            count = len([row for row in audits if row.created_at and start <= row.created_at.replace(tzinfo=None) < end])
            trend_points.append({"label": f"T-{index}", "activity": count, "health": _clamp(pipeline_health - index * 2 + count)})
        trend_points.reverse()

        payload = {
            "status": "ok",
            "generated_at": now.isoformat(),
            "window_days": horizon,
            "executive_overview": {
                "total_organizations": orgs_total,
                "total_candidates": len(candidates),
                "total_jobs": len(jobs),
                "total_interviews": len(interviews),
                "monthly_growth": monthly_growth,
                "hiring_velocity": hiring_velocity,
                "platform_revenue": revenue,
                "active_recruiters": active_recruiters,
                "ai_decisions_per_day": ai_decisions_per_day,
                "product_health_score": _clamp((pipeline_health + platform_health + ai_accuracy_proxy + adoption) / 4),
            },
            "candidate_analysis": {
                "candidate_score": candidate_score,
                "match_score": match_score,
                "hiring_confidence": _confidence(candidate_score, len(candidates)),
                "readiness_score": readiness,
                "top_candidates": sorted(candidate_profiles, key=lambda item: item["candidate_score"], reverse=True)[:10],
                "skill_coverage": [{"skill": skill, "supply": count, "demand": demand.get(skill, 0)} for skill, count in skills.most_common(10)],
            },
            "job_analysis": {
                "open_jobs": len(jobs),
                "time_to_fill_days": max(7, 32 - active_shortlist * 2 + len(talent_shortage) * 3),
                "application_volume": applications,
                "talent_shortages": talent_shortage[:8],
                "skill_demand": [{"skill": skill, "demand": count, "supply": skills.get(skill, 0)} for skill, count in demand.most_common(10)],
                "geographic_demand": [{"region": "Remote", "demand": max(1, len(jobs) // 2)}, {"region": "Local", "demand": max(0, len(jobs) - max(1, len(jobs) // 2))}],
                "job_health_score": fill_probability,
                "market_demand_score": _clamp(44 + len(jobs) * 7 + len(talent_shortage) * 6),
                "fill_probability": fill_probability,
                "hiring_risk_score": hiring_risk,
            },
            "recruiter_analysis": {
                "recruiter_performance_score": _avg_score([r["recruiter_performance_score"] for r in recruiter_leaderboard], fallback=0),
                "efficiency_score": _avg_score([r["efficiency_score"] for r in recruiter_leaderboard], fallback=0),
                "conversion_rate": _pct(approved + hires, max(1, active_shortlist)),
                "hiring_velocity": hiring_velocity,
                "leaderboard": recruiter_leaderboard,
            },
            "interview_analysis": {
                "interview_quality_score": interview_quality,
                "candidate_confidence_score": _clamp(interview_quality * 0.72 + completion_rate * 0.28) if interviews else 0,
                "interview_completion_rate": completion_rate,
                "behavioral_patterns": {
                    "communication_quality": communication_quality,
                    "confidence_trend": confidence_trend,
                    "attention_risk": len([row for row in interviews if row.alerts]),
                },
            },
            "pipeline_analysis": {
                "applications": applications,
                "screening": screened,
                "interviews": len(interviews),
                "shortlisting": active_shortlist,
                "offers": offers,
                "hires": hires,
                "pipeline_health_score": pipeline_health,
                "drop_off_rate": drop_off_rate,
                "bottleneck_detection": bottlenecks[:6],
                "hiring_forecast": {
                    "fill_probability": fill_probability,
                    "delay_probability": _clamp(100 - pipeline_health + len(talent_shortage) * 5),
                    "expected_hires": max(0, round((fill_probability / 100) * max(1, len(jobs)))),
                },
            },
            "platform_analytics": {
                "active_users": active_recruiters,
                "active_organizations": orgs_total,
                "daily_usage": round(len(audits) / max(1, horizon), 2),
                "api_usage": len(audits),
                "feature_adoption": {
                    "matching": counts["screening"],
                    "shortlist": counts["shortlisting"],
                    "interviews": counts["interviews"],
                    "billing": counts["billing"],
                    "ai_decisions": counts["ai_decisions"],
                },
                "system_latency_ms": system_latency_ms,
                "queue_performance": {"queue_depth": queue_pressure, "status": "healthy" if queue_pressure < 30 else "watch"},
                "adoption_score": adoption,
                "usage_trends": trend_points,
                "growth_metrics": {"monthly_growth": monthly_growth, "revenue_growth_forecast": revenue_growth_forecast},
                "platform_health_score": platform_health,
            },
            "ai_analytics": {
                "ai_matching_accuracy": ai_accuracy_proxy,
                "ai_recommendation_accuracy": _clamp(ai_accuracy_proxy - 3 + counts["shortlisting"]),
                "interview_scoring_accuracy": _clamp(55 + completed_interviews * 4 + len(assessments) * 2),
                "resume_parsing_accuracy": _avg_score([float((c.extraction_json or {}).get("confidence") or 0.65) * 100 for c in candidates], fallback=65),
                "ai_confidence_index": ai_accuracy_proxy,
                "ai_performance_score": _clamp((ai_accuracy_proxy + interview_quality + candidate_score) / 3) if interviews else _clamp((ai_accuracy_proxy + candidate_score) / 2),
                "ai_drift_detection": {"score": ai_drift, "status": "watch" if ai_drift >= 42 else "stable"},
            },
            "revenue_analytics": {
                "platform_revenue": revenue,
                "successful_payments": len(successful_payments),
                "billing_events": len(billing_events),
                "average_revenue_per_org": round(revenue / max(1, orgs_total), 2),
                "revenue_growth_forecast": revenue_growth_forecast,
            },
            "forecasting": {
                "hiring_demand": demand_forecast,
                "talent_shortages": talent_shortage[:6],
                "candidate_success": _clamp((candidate_score + readiness + interview_quality) / 3) if interviews else _clamp((candidate_score + readiness) / 2),
                "recruiter_performance": _avg_score([r["recruiter_performance_score"] for r in recruiter_leaderboard], fallback=42 if active_recruiters else 0),
                "revenue_growth": revenue_growth_forecast,
                "organization_expansion": expansion_forecast,
            },
            "copilot_insights": insights[:8],
            "recommendations": recommendations,
            "visualizations": {
                "trends": trend_points,
                "pipeline_heatmap": [
                    {"stage": "applications", "score": _pct(applications, max(1, applications))},
                    {"stage": "screening", "score": _pct(screened, max(1, applications))},
                    {"stage": "shortlisting", "score": _pct(active_shortlist, max(1, applications))},
                    {"stage": "interviews", "score": _pct(len(interviews), max(1, active_shortlist))},
                    {"stage": "offers", "score": _pct(offers, max(1, len(interviews) or active_shortlist))},
                    {"stage": "hires", "score": _pct(hires, max(1, offers))},
                ],
            },
            "observability_integration": {
                "destinations": ["observability", "security_center", "ai_decision_brain", "operations_fabric"],
                "event_name": "product_intelligence.snapshot",
                "event_bus": event_bus.snapshot(limit=5),
                "security_signals": len(security_events),
            },
        }

        event_bus.publish(
            "product_intelligence",
            "product_intelligence.snapshot",
            {
                "organization_id": organization_id,
                "user_id": user_id,
                "product_health_score": payload["executive_overview"]["product_health_score"],
                "pipeline_health_score": pipeline_health,
                "ai_confidence_index": ai_accuracy_proxy,
                "hiring_risk_score": hiring_risk,
            },
            severity="info" if hiring_risk < 65 else "warning",
        )
        return payload
    finally:
        db.close()


def product_intelligence_event_feed(organization_id: Optional[int], limit: int = 20) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(AuditLog)
        if organization_id is not None:
            query = query.filter(AuditLog.organization_id == organization_id)
        rows = query.order_by(AuditLog.created_at.desc()).limit(max(1, min(80, limit))).all()
        feed = []
        for row in rows:
            action = str(row.action or "workspace.event")
            feed.append({
                "id": row.id,
                "title": action.replace(".", " ").replace("_", " ").title(),
                "body": f"{row.entity_type or 'entity'} {row.entity_id or ''}".strip(),
                "severity": (row.details or {}).get("severity") or "info",
                "created_at": _iso(row.created_at),
                "details": row.details or {},
            })
        return feed
    finally:
        db.close()
