from typing import Any, Dict, Optional

from backend.models.enterprise import Subscription
from backend.db.database import SessionLocal


PLAN_LIMITS: Dict[str, Dict[str, int]] = {
    "free": {"ai_tokens": 25000, "recruiter_seats": 1, "workspaces": 1, "candidates": 100, "voice_interviews": 5},
    "pro": {"ai_tokens": 500000, "recruiter_seats": 10, "workspaces": 3, "candidates": 5000, "voice_interviews": 150},
    "enterprise": {"ai_tokens": 5000000, "recruiter_seats": 250, "workspaces": 100, "candidates": 250000, "voice_interviews": 5000},
}

FEATURES: Dict[str, set[str]] = {
    "free": {"candidate_management", "resume_upload", "basic_ai_matching", "chat_assistant"},
    "pro": {"candidate_management", "resume_upload", "basic_ai_matching", "chat_assistant", "semantic_search", "billing", "analytics", "voice_interviews"},
    "enterprise": {"candidate_management", "resume_upload", "basic_ai_matching", "chat_assistant", "semantic_search", "billing", "analytics", "voice_interviews", "audit_logs", "multi_workspace", "advanced_security"},
}


def subscription_snapshot(organization_id: Optional[int]) -> Dict[str, Any]:
    if organization_id is None:
        return _snapshot("free", {}, organization_id)

    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            return _snapshot("free", {}, organization_id)
        usage = subscription.usage_json or {}
        return _snapshot(subscription.plan or "free", usage, organization_id, subscription.status)
    finally:
        db.close()


def can_use_feature(organization_id: Optional[int], feature: str) -> Dict[str, Any]:
    snapshot = subscription_snapshot(organization_id)
    allowed = feature in snapshot["features"] and snapshot["status"] in {"active", "trialing"}
    return {
        "allowed": allowed,
        "feature": feature,
        "plan": snapshot["plan"],
        "status": snapshot["status"],
        "reason": None if allowed else "Feature is not available for the current subscription plan.",
        "subscription": snapshot,
    }


def _snapshot(plan: str, usage: Dict[str, Any], organization_id: Optional[int], status: str = "active") -> Dict[str, Any]:
    normalized = plan if plan in PLAN_LIMITS else "free"
    limits = PLAN_LIMITS[normalized]
    return {
        "organization_id": organization_id,
        "plan": normalized,
        "status": status,
        "features": sorted(FEATURES[normalized]),
        "limits": limits,
        "usage": usage,
        "remaining": {
            key: max(0, int(limit) - int(usage.get(key, 0) or 0))
            for key, limit in limits.items()
        },
        "trial": {"enabled": status == "trialing", "expiration_enforced": True},
    }
