from collections import Counter
from typing import Any, Dict, List

from backend.db.database import SessionLocal
from backend.db.models import Candidate
from backend.models.job import Job
from backend.services.matcher import rank_candidates_tfidf


def candidate_graph(candidate_id: str, organization_id: int | None = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(Candidate).filter(Candidate.candidate_id == candidate_id)
        if organization_id is not None:
            query = query.filter(Candidate.organization_id == organization_id)
        candidate = query.first()
        if not candidate:
            return {
                "candidate_id": candidate_id,
                "skills_graph": {},
                "career_timeline": [],
                "best_roles": [],
                "performance_prediction": 0,
                "retention_prediction": 0,
            }

        skills = candidate.skills or []
        text = candidate.text or ""
        skill_graph = {
            skill: {
                "weight": min(100, 45 + text.lower().count(skill.lower()) * 15),
                "related": [other for other in skills if other != skill][:4],
            }
            for skill in skills
        }

        jobs_query = db.query(Job)
        if organization_id is not None:
            jobs_query = jobs_query.filter(Job.organization_id == organization_id)
        jobs = jobs_query.order_by(Job.created_at.desc()).limit(10).all()
        role_candidates = [
            {
                "candidate_id": candidate.candidate_id,
                "skills": skills,
                "text": text,
                "experience": {"min": candidate.experience or 0},
            }
        ]
        best_roles = []
        for job in jobs:
            ranked = rank_candidates_tfidf(job.description, role_candidates)
            score = ranked[0]["match_score"] if ranked else 0
            best_roles.append({"role": job.title, "score": score, "job_id": job.id})
        best_roles.sort(key=lambda item: item["score"], reverse=True)

        experience = candidate.experience or 0
        performance = min(100, 45 + len(skills) * 5 + experience * 4)
        retention = min(100, 55 + min(experience, 8) * 4 + (10 if len(skills) >= 4 else 0))

        return {
            "candidate_id": candidate.candidate_id,
            "skills_graph": skill_graph,
            "career_timeline": build_career_timeline(text, experience),
            "best_roles": best_roles[:5],
            "performance_prediction": round(performance, 2),
            "retention_prediction": round(retention, 2),
        }
    finally:
        db.close()


def build_career_timeline(text: str, experience: int) -> List[Dict[str, Any]]:
    milestones = []
    lowered = (text or "").lower()
    if "intern" in lowered or "junior" in lowered:
        milestones.append({"stage": "early-career", "signal": "junior or internship experience"})
    if "lead" in lowered or "architect" in lowered or "manager" in lowered:
        milestones.append({"stage": "leadership", "signal": "leadership or architecture language detected"})
    if experience:
        milestones.append({"stage": "experience", "signal": f"{experience} years of experience"})
    return milestones or [{"stage": "profile", "signal": "timeline will improve as more structured resume data is added"}]


def skill_demand(organization_id: int | None = None) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(Candidate)
        if organization_id is not None:
            query = query.filter(Candidate.organization_id == organization_id)
        counter = Counter()
        for candidate in query.all():
            counter.update(candidate.skills or [])
        return [{"skill": skill, "count": count} for skill, count in counter.most_common(20)]
    finally:
        db.close()
