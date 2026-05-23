from datetime import datetime
from typing import Any, Dict, List

from backend.db.database import SessionLocal
from backend.models.enterprise import Assessment


DEFAULT_QUESTIONS = [
    {"id": "mcq_1", "type": "mcq", "prompt": "Which practice improves API reliability?", "answer": "automated tests", "points": 20},
    {"id": "scenario_1", "type": "scenario", "prompt": "Describe how you would debug a production incident.", "keywords": ["logs", "monitoring", "rollback", "root cause"], "points": 30},
    {"id": "code_1", "type": "coding", "prompt": "Write a function that validates an email address.", "keywords": ["regex", "strip", "lower"], "points": 50},
]


def create_assessment(organization_id: int | None, candidate_id: str, title: str, assessment_type: str = "mixed") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        assessment = Assessment(
            organization_id=organization_id,
            candidate_id=candidate_id,
            assessment_type=assessment_type,
            title=title,
            questions=DEFAULT_QUESTIONS,
            status="issued",
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)
        return serialize_assessment(assessment)
    finally:
        db.close()


def submit_assessment(assessment_id: int, submissions: List[Dict[str, Any]], organization_id: int | None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(Assessment).filter(Assessment.id == assessment_id)
        if organization_id is not None:
            query = query.filter(Assessment.organization_id == organization_id)
        assessment = query.first()
        if not assessment:
            return {}
        score, plagiarism = evaluate_submissions(assessment.questions or [], submissions)
        assessment.submissions = submissions
        assessment.score = score
        assessment.plagiarism_score = plagiarism
        assessment.status = "completed"
        assessment.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(assessment)
        return serialize_assessment(assessment)
    finally:
        db.close()


def evaluate_submissions(questions: List[Dict[str, Any]], submissions: List[Dict[str, Any]]) -> tuple[int, int]:
    answers = {item.get("id"): str(item.get("answer", "")).lower() for item in submissions}
    score = 0
    repeated_answers = 0
    seen = set()
    for question in questions:
        answer = answers.get(question.get("id"), "")
        if answer in seen and answer:
            repeated_answers += 1
        seen.add(answer)
        points = int(question.get("points", 0))
        if question.get("type") == "mcq":
            if question.get("answer", "").lower() in answer:
                score += points
        else:
            keywords = question.get("keywords", [])
            hits = sum(1 for keyword in keywords if keyword.lower() in answer)
            score += round(points * (hits / max(len(keywords), 1)))
    plagiarism = min(100, repeated_answers * 25)
    return max(0, min(100, int(score))), plagiarism


def serialize_assessment(assessment: Assessment) -> Dict[str, Any]:
    return {
        "id": assessment.id,
        "candidate_id": assessment.candidate_id,
        "assessment_type": assessment.assessment_type,
        "title": assessment.title,
        "questions": assessment.questions or [],
        "submissions": assessment.submissions or [],
        "score": assessment.score,
        "plagiarism_score": assessment.plagiarism_score,
        "status": assessment.status,
        "created_at": assessment.created_at.isoformat() if assessment.created_at else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
    }
