from typing import Any, Dict, Optional

from backend.db.database import SessionLocal
from backend.models.enterprise import WorkflowRun
from backend.services.enterprise_service import create_notification
from backend.services.automation_rules_service import evaluate_rules


def evaluate_workflow(match_score: float, candidate_id: str, organization_id: Optional[int] = None, job_id: Optional[str] = None) -> Dict[str, Any]:
    score = float(match_score or 0)

    # First: recruiter-configurable automation rules.
    automation = evaluate_rules(
        organization_id,
        trigger="match.completed",
        context={
            "candidate_id": candidate_id,
            "match_score": score,
            # missing_skills/confidence can be added later without breaking API.
            "missing_skills": [],
            "confidence": 0.0,
        },
    )
    actions = automation.get("actions") or []

    # Fallback: default workflow behavior when no rule fires.
    if not actions:
        if score > 85:
            decision = "shortlist"
            actions = ["shortlist", "invite_interview"]
        elif score < 50:
            decision = "reject"
            actions = ["reject"]
        else:
            decision = "review"
            actions = ["manual_review"]
    else:
        # Normalize a decision label from the selected actions.
        if "reject" in actions:
            decision = "reject"
        elif "shortlist" in actions or "invite_interview" in actions:
            decision = "shortlist"
        else:
            decision = "review"

    return {
        "candidate_id": candidate_id,
        "job_id": job_id,
        "match_score": round(score, 2),
        "decision": decision,
        "actions": actions,
        "organization_id": organization_id,
        "automation": automation,
    }


def persist_workflow_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        run = WorkflowRun(
            organization_id=payload.get("organization_id"),
            candidate_id=str(payload.get("candidate_id", "")),
            job_id=str(payload.get("job_id", "")) or None,
            decision=payload.get("decision", "review"),
            status="completed",
            details=payload,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return {
            **payload,
            "workflow_run_id": run.id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
    finally:
        db.close()


def notify_workflow_action(user_id: Optional[int], payload: Dict[str, Any]):
    if not user_id:
        return None

    decision = payload.get("decision", "review")
    action_text = ", ".join(payload.get("actions", [])) or decision
    return create_notification(
        user_id=user_id,
        organization_id=payload.get("organization_id"),
        kind="workflow",
        subject=f"Workflow decision: {decision}",
        message=f"Candidate {payload.get('candidate_id')} triggered workflow actions: {action_text}.",
        metadata=payload,
    )
