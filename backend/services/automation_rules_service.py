from datetime import datetime
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.models.enterprise import AutomationRule


def serialize_rule(rule: AutomationRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "organization_id": rule.organization_id,
        "name": rule.name,
        "enabled": bool(rule.enabled),
        "trigger": rule.trigger,
        "conditions": rule.conditions or {},
        "actions": rule.actions or {},
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


def list_rules(organization_id: Optional[int]) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(AutomationRule)
        if organization_id is not None:
            query = query.filter(or_(AutomationRule.organization_id == organization_id, AutomationRule.organization_id.is_(None)))
        rows = query.order_by(AutomationRule.created_at.desc()).limit(50).all()
        return [serialize_rule(row) for row in rows]
    finally:
        db.close()


def create_rule(
    organization_id: Optional[int],
    name: str,
    trigger: str,
    conditions: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = AutomationRule(
            organization_id=organization_id,
            name=(name or "Rule")[:120],
            enabled=bool(enabled),
            trigger=(trigger or "match.completed")[:120],
            conditions=conditions or {},
            actions=actions or {},
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return serialize_rule(row)
    finally:
        db.close()


def update_rule(rule_id: int, organization_id: Optional[int], patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(AutomationRule).filter(AutomationRule.id == int(rule_id))
        if organization_id is not None:
            query = query.filter(or_(AutomationRule.organization_id == organization_id, AutomationRule.organization_id.is_(None)))
        row = query.first()
        if not row:
            return None

        if "name" in patch:
            row.name = str(patch.get("name") or "Rule")[:120]
        if "enabled" in patch:
            row.enabled = bool(patch.get("enabled"))
        if "trigger" in patch:
            row.trigger = str(patch.get("trigger") or row.trigger)[:120]
        if "conditions" in patch and isinstance(patch.get("conditions"), dict):
            row.conditions = patch.get("conditions") or {}
        if "actions" in patch and isinstance(patch.get("actions"), dict):
            row.actions = patch.get("actions") or {}

        db.commit()
        db.refresh(row)
        return serialize_rule(row)
    finally:
        db.close()


def delete_rule(rule_id: int, organization_id: Optional[int]) -> bool:
    db = SessionLocal()
    try:
        query = db.query(AutomationRule).filter(AutomationRule.id == int(rule_id))
        if organization_id is not None:
            query = query.filter(or_(AutomationRule.organization_id == organization_id, AutomationRule.organization_id.is_(None)))
        row = query.first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def evaluate_rules(
    organization_id: Optional[int],
    trigger: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Deterministic rule evaluation.

    Supported condition keys:
    - min_score: float
    - max_missing_skills: int
    - min_confidence: float (0..1)
    """
    rules = list_rules(organization_id)
    applicable = [r for r in rules if r.get("enabled") and r.get("trigger") == trigger]

    score = float(context.get("match_score") or 0.0)
    missing = context.get("missing_skills") or []
    confidence = float(context.get("confidence") or 0.0)

    fired: list[dict[str, Any]] = []
    for rule in applicable:
        conditions = rule.get("conditions") or {}
        min_score = float(conditions.get("min_score") or 0.0)
        max_missing = conditions.get("max_missing_skills")
        min_conf = float(conditions.get("min_confidence") or 0.0)

        if score < min_score:
            continue
        if max_missing is not None and len(missing) > int(max_missing):
            continue
        if confidence < min_conf:
            continue
        fired.append(rule)

    actions = []
    for rule in fired:
        acts = (rule.get("actions") or {}).get("actions") or []
        if isinstance(acts, list):
            actions.extend([str(a) for a in acts if str(a)])

    # De-dupe while stable.
    seen = set()
    actions_out = []
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        actions_out.append(action)

    return {
        "trigger": trigger,
        "rules_evaluated": len(applicable),
        "rules_fired": [{"id": r.get("id"), "name": r.get("name")} for r in fired][:10],
        "actions": actions_out[:20],
    }

