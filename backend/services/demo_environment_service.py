from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate, ShortlistEntry
from backend.models.enterprise import AuditLog, InterviewSchedule, Notification
from backend.models.job import Job


DEMO_JOBS = [
    {
        "title": "Senior Backend Platform Engineer",
        "description": (
            "Build Python FastAPI services for enterprise hiring workflows. Must have Python, FastAPI, SQL, "
            "PostgreSQL, Docker, Kubernetes, AWS, observability, REST APIs, and experience with secure multi-tenant systems."
        ),
    },
    {
        "title": "AI Interview Intelligence Lead",
        "description": (
            "Own AI interview evaluation quality, LLM-assisted scoring, prompt safety, analytics, recruiter calibration, "
            "Python, machine learning, NLP, SQL, and structured assessment design."
        ),
    },
    {
        "title": "Recruiting Operations Manager",
        "description": (
            "Lead recruiter workflows, hiring analytics, stakeholder communication, executive reporting, interview scheduling, "
            "pipeline conversion, and candidate experience for high-growth engineering teams."
        ),
    },
]


DEMO_CANDIDATES = [
    {
        "candidate_id": "demo-amina-okafor",
        "candidate_name": "Amina Okafor",
        "role": "Senior Backend Engineer",
        "experience": 7,
        "skills": ["python", "fastapi", "postgresql", "sql", "docker", "kubernetes", "aws", "observability", "rest"],
        "text": (
            "Senior backend engineer with 7 years building Python FastAPI APIs, PostgreSQL data models, Docker and Kubernetes "
            "deployments on AWS. Led migration from monolith to microservices, improved API latency by 42%, mentored three engineers, "
            "and partnered with product and security teams on multi-tenant access controls."
        ),
    },
    {
        "candidate_id": "demo-lucas-meyer",
        "candidate_name": "Lucas Meyer",
        "role": "Machine Learning Engineer",
        "experience": 5,
        "skills": ["python", "machine learning", "nlp", "llm", "sql", "pytorch", "airflow", "aws"],
        "text": (
            "ML engineer focused on NLP, model evaluation, LLM workflows, and production analytics. Built candidate screening classifiers, "
            "designed evaluation datasets, monitored drift, and worked with recruiters to calibrate model outputs against hiring rubrics."
        ),
    },
    {
        "candidate_id": "demo-nadia-shah",
        "candidate_name": "Nadia Shah",
        "role": "Recruiting Operations Manager",
        "experience": 8,
        "skills": ["data analysis", "sql", "stakeholder communication", "analytics", "interview scheduling"],
        "text": (
            "Recruiting operations manager with 8 years supporting engineering hiring. Built executive hiring reports, improved recruiter "
            "response SLA from 36 hours to 12 hours, coordinated interview panels across timezones, and coached recruiters on candidate experience."
        ),
    },
    {
        "candidate_id": "demo-ethan-kim",
        "candidate_name": "Ethan Kim",
        "role": "Frontend Platform Engineer",
        "experience": 4,
        "skills": ["typescript", "react", "next js", "graphql", "css", "observability"],
        "text": (
            "Frontend engineer with React, TypeScript, Next.js, GraphQL, accessibility, and performance optimization experience. "
            "Partnered with backend teams on API contracts and delivered recruiter-facing workflow tools."
        ),
    },
]


def _org_candidate_id(base_id: str, organization_id: int) -> str:
    return f"{base_id}-org-{organization_id}"


def seed_enterprise_demo_environment(organization_id: Optional[int], user_id: Optional[int] = None) -> dict[str, Any]:
    if organization_id is None:
        return {"status": "skipped", "reason": "organization_required"}

    db = SessionLocal()
    try:
        created = {"jobs": 0, "candidates": 0, "shortlist": 0, "schedules": 0, "events": 0}
        now = datetime.utcnow()

        jobs: list[Job] = []
        for item in DEMO_JOBS:
            job = (
                db.query(Job)
                .filter(Job.organization_id == organization_id, Job.title == item["title"])
                .first()
            )
            if not job:
                job = Job(title=item["title"], description=item["description"], organization_id=organization_id)
                db.add(job)
                db.flush()
                created["jobs"] += 1
            jobs.append(job)

        for item in DEMO_CANDIDATES:
            scoped_candidate_id = _org_candidate_id(item["candidate_id"], organization_id)
            candidate = (
                db.query(Candidate)
                .filter(Candidate.candidate_id == scoped_candidate_id)
                .first()
            )
            if not candidate:
                candidate = Candidate(
                    candidate_id=scoped_candidate_id,
                    candidate_name=item["candidate_name"],
                    name_confidence=96,
                    name_source="demo_profile",
                    extracted_email=f"{item['candidate_id'].replace('demo-', '').replace('-', '.')}+{organization_id}@example.com",
                    text=item["text"].lower(),
                    raw_text=item["text"],
                    skills=item["skills"],
                    role=item["role"],
                    experience=item["experience"],
                    organization_id=organization_id,
                    extraction_json={"confidence": 0.94, "parser_used": "demo_enterprise_profile", "is_demo": True},
                )
                db.add(candidate)
                db.flush()
                created["candidates"] += 1

        if jobs:
            amina_id = _org_candidate_id("demo-amina-okafor", organization_id)
            lucas_id = _org_candidate_id("demo-lucas-meyer", organization_id)
            nadia_id = _org_candidate_id("demo-nadia-shah", organization_id)
            shortlist_specs = [
                (amina_id, jobs[0].id, 91, "Strong backend platform alignment; schedule architecture screen."),
                (lucas_id, jobs[1].id, 87, "Strong AI evaluation background; validate production ML operations."),
                (nadia_id, jobs[2].id, 89, "Excellent recruiting operations and executive reporting fit."),
            ]
            for candidate_id, job_id, score, reason in shortlist_specs:
                existing = (
                    db.query(ShortlistEntry)
                    .filter(
                        ShortlistEntry.organization_id == organization_id,
                        ShortlistEntry.job_id == job_id,
                        ShortlistEntry.candidate_id == candidate_id,
                    )
                    .first()
                )
                if not existing:
                    db.add(
                        ShortlistEntry(
                            organization_id=organization_id,
                            job_id=job_id,
                            candidate_id=candidate_id,
                            status="shortlisted",
                            shortlist_score=score,
                            confidence=86,
                            ai_reason=reason,
                            recruiter_notes="Demo environment: ready for recruiter review.",
                            created_by="demo-seed",
                        )
                    )
                    created["shortlist"] += 1

        schedule_existing = db.query(InterviewSchedule).filter(
            InterviewSchedule.organization_id == organization_id,
            InterviewSchedule.candidate_id == _org_candidate_id("demo-amina-okafor", organization_id),
        ).first()
        if not schedule_existing:
            db.add(
                InterviewSchedule(
                    organization_id=organization_id,
                    candidate_id=_org_candidate_id("demo-amina-okafor", organization_id),
                    interviewer_email="maria.recruiting@example.com",
                    starts_at=now + timedelta(days=1, hours=3),
                    timezone="Africa/Nairobi",
                    provider="google_meet",
                    meeting_link="https://meet.google.com/demo-amina-platform-screen",
                    reminders=[
                        {"offset_minutes": 1440, "channel": "email", "status": "scheduled"},
                        {"offset_minutes": 60, "channel": "slack", "status": "scheduled"},
                    ],
                )
            )
            created["schedules"] += 1

        event_specs = [
            ("resume.uploaded", "candidate", _org_candidate_id("demo-amina-okafor", organization_id), {"source": "demo_seed", "confidence": 0.94}),
            ("match.completed", "job", str(jobs[0].id if jobs else "demo"), {"top_candidate": _org_candidate_id("demo-amina-okafor", organization_id), "score": 91}),
            ("shortlist.added", "candidate", _org_candidate_id("demo-amina-okafor", organization_id), {"severity": "info", "reason": "high_backend_alignment"}),
            ("scheduling.created", "candidate", _org_candidate_id("demo-amina-okafor", organization_id), {"provider": "google_meet", "timezone": "Africa/Nairobi"}),
            ("interview.completed", "candidate", _org_candidate_id("demo-lucas-meyer", organization_id), {"score": 84, "confidence": 0.82}),
        ]
        for offset, (action, entity_type, entity_id, details) in enumerate(event_specs):
            exists = db.query(AuditLog).filter(
                AuditLog.organization_id == organization_id,
                AuditLog.action == action,
                AuditLog.entity_id == entity_id,
            ).first()
            if not exists:
                db.add(
                    AuditLog(
                        organization_id=organization_id,
                        user_id=user_id,
                        action=action,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        details=details,
                        created_at=now - timedelta(hours=offset * 3),
                    )
                )
                created["events"] += 1

        notif_exists = db.query(Notification).filter(
            Notification.organization_id == organization_id,
            Notification.subject == "Demo environment ready",
        ).first()
        if not notif_exists:
            db.add(
                Notification(
                    organization_id=organization_id,
                    user_id=user_id or 0,
                    kind="ai_ops",
                    subject="Demo environment ready",
                    message="Enterprise demo data is active: jobs, candidates, shortlist, scheduling, and operational signals are ready for review.",
                    status="queued",
                    metadata_json={"created": created},
                )
            )

        db.commit()
        return {
            "status": "ready",
            "created": created,
            "summary": "Enterprise demo environment is populated with realistic hiring operations.",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
