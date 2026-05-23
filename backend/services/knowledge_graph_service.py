from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.db.models import Candidate
from backend.models.job import Job
from backend.services.matcher import extract_required_skills


def workspace_knowledge_graph(
    organization_id: Optional[int],
    *,
    max_candidates: int = 60,
    max_jobs: int = 20,
) -> dict[str, Any]:
    """
    Enterprise knowledge graph (lightweight).

    This is intentionally small and JSON-friendly:
    - nodes: candidate/job/skill
    - edges: has_skill / requires_skill

    It enables "relationship intelligence" without introducing a heavy graph database.
    """
    db = SessionLocal()
    try:
        cand_q = db.query(Candidate)
        job_q = db.query(Job)
        if organization_id is not None:
            cand_q = cand_q.filter(or_(Candidate.organization_id == organization_id, Candidate.organization_id.is_(None)))
            job_q = job_q.filter(Job.organization_id == organization_id)

        candidates = cand_q.order_by(Candidate.created_at.desc()).limit(max_candidates).all()
        jobs = job_q.order_by(Job.created_at.desc()).limit(max_jobs).all()

        skill_counter = Counter()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # Candidate nodes + candidate->skill edges
        for row in candidates:
            cid = str(row.candidate_id)
            nodes.append(
                {
                    "id": f"candidate:{cid}",
                    "type": "candidate",
                    "candidate_id": cid,
                    "label": (getattr(row, "candidate_name", None) or cid),
                }
            )
            for skill in (row.skills or [])[:60]:
                if not isinstance(skill, str) or not skill.strip():
                    continue
                s = skill.strip()
                skill_counter[s] += 1
                edges.append({"type": "has_skill", "from": f"candidate:{cid}", "to": f"skill:{s.lower()}"})

        # Job nodes + job->skill edges (required skills derived from job description + known skills)
        known_skills = [s for s, _ in skill_counter.most_common(250)]
        for job in jobs:
            jid = str(job.id)
            nodes.append(
                {
                    "id": f"job:{jid}",
                    "type": "job",
                    "job_id": job.id,
                    "label": job.title or f"Job {jid}",
                }
            )
            required = extract_required_skills(job.description or "", known_skills)[:40]
            for s in required:
                edges.append({"type": "requires_skill", "from": f"job:{jid}", "to": f"skill:{s.lower()}"})

        # Skill nodes (top N only, to keep payload small)
        top_skills = [s for s, _ in skill_counter.most_common(120)]
        for skill in top_skills:
            nodes.append({"id": f"skill:{skill.lower()}", "type": "skill", "skill": skill, "label": skill})

        # De-dupe nodes by id while preserving order.
        seen = set()
        nodes_out = []
        for n in nodes:
            nid = n.get("id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            nodes_out.append(n)

        return {
            "organization_id": organization_id,
            "nodes": nodes_out[:500],
            "edges": edges[:1500],
            "summary": {
                "candidates": len(candidates),
                "jobs": len(jobs),
                "skills": len(top_skills),
                "top_skills": [{"skill": s, "count": int(skill_counter[s])} for s in top_skills[:12]],
            },
        }
    finally:
        db.close()

