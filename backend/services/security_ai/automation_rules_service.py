from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import or_

from backend.db.database import SessionLocal
from backend.models.enterprise import SecurityAutomationRule


def serialize_rule(row: SecurityAutomationRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "name": row.name,
        "enabled": bool(row.enabled),
        "trigger": row.trigger,
        "conditions": row.conditions or {},
        "actions": row.actions or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_security_rules(organization_id: Optional[int], limit: int = 50) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityAutomationRule)
        if organization_id is not None:
            q = q.filter(or_(SecurityAutomationRule.organization_id == organization_id, SecurityAutomationRule.organization_id.is_(None)))
        rows = q.order_by(SecurityAutomationRule.created_at.desc()).limit(limit).all()
        return [serialize_rule(r) for r in rows]
    finally:
        db.close()


def create_security_rule(
    organization_id: Optional[int],
    *,
    name: str,
    trigger: str = "security.event",
    enabled: bool = True,
    conditions: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = SecurityAutomationRule(
            organization_id=organization_id,
            name=(name or "Rule")[:160],
            enabled=bool(enabled),
            trigger=(trigger or "security.event")[:120],
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


def update_security_rule(rule_id: int, organization_id: Optional[int], patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = db.query(SecurityAutomationRule).filter(SecurityAutomationRule.id == int(rule_id))
        if organization_id is not None:
            q = q.filter(or_(SecurityAutomationRule.organization_id == organization_id, SecurityAutomationRule.organization_id.is_(None)))
        row = q.first()
        if not row:
            return None
        if "name" in patch:
            row.name = str(patch.get("name") or row.name)[:160]
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


def delete_security_rule(rule_id: int, organization_id: Optional[int]) -> bool:
    db = SessionLocal()
    try:
        q = db.query(SecurityAutomationRule).filter(SecurityAutomationRule.id == int(rule_id))
        if organization_id is not None:
            q = q.filter(or_(SecurityAutomationRule.organization_id == organization_id, SecurityAutomationRule.organization_id.is_(None)))
        row = q.first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def evaluate_security_rules(
    organization_id: Optional[int],
    *,
    trigger: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """
    Security rule evaluation.

    Supported condition keys (best-effort):
    - event_type: exact match
    - event_type_prefix: startswith
    - min_risk_score: int
    - threat_levels: [low|medium|high|critical]
    - auto_execute: bool (if true, actions will be executed by the caller)
    """
    rules = list_security_rules(organization_id)
    applicable = [r for r in rules if r.get("enabled") and r.get("trigger") == trigger]

    event_type = str(event.get("event_type") or "")
    threat_level = str(event.get("threat_level") or "")
    risk_score = int(event.get("risk_score") or 0)

    fired: list[dict[str, Any]] = []
    for rule in applicable:
        cond = rule.get("conditions") or {}
        if not isinstance(cond, dict):
            continue
        if cond.get("event_type") and str(cond.get("event_type")) != event_type:
            continue
        if cond.get("event_type_prefix") and not event_type.startswith(str(cond.get("event_type_prefix"))):
            continue
        if cond.get("min_risk_score") is not None and risk_score < int(cond.get("min_risk_score") or 0):
            continue
        levels = cond.get("threat_levels")
        if isinstance(levels, list) and levels:
            if threat_level not in {str(x) for x in levels}:
                continue
        fired.append(rule)

    actions: list[dict[str, Any]] = []
    auto_execute = False
    for rule in fired:
        cond = rule.get("conditions") or {}
        if isinstance(cond, dict) and bool(cond.get("auto_execute")):
            auto_execute = True
        acts = (rule.get("actions") or {}).get("actions") if isinstance(rule.get("actions"), dict) else None
        if isinstance(acts, list):
            for a in acts:
                if isinstance(a, dict) and a.get("type"):
                    actions.append(a)

    return {
        "trigger": trigger,
        "rules_evaluated": len(applicable),
        "rules_fired": [{"id": r.get("id"), "name": r.get("name")} for r in fired][:10],
        "actions": actions[:20],
        "auto_execute": auto_execute,
    }


def ensure_default_security_rules(organization_id: Optional[int]) -> list[dict[str, Any]]:
    """
    Seed a few safe defaults so Security Center feels operational out of the box.
    Defaults are recommendation-first (auto_execute = False).
    """
    existing = list_security_rules(organization_id, limit=10)
    if existing:
        return existing

    defaults = [
        {
            "name": "Auth abuse containment",
            "conditions": {"event_type_prefix": "auth.", "min_risk_score": 70, "threat_levels": ["high", "critical"], "auto_execute": False},
            "actions": {"actions": [{"type": "lock_account", "minutes": 15}, {"type": "revoke_sessions"}]},
        },
        {
            "name": "Token misuse response",
            "conditions": {"event_type": "auth.refresh_reuse", "min_risk_score": 60, "auto_execute": False},
            "actions": {"actions": [{"type": "revoke_sessions"}]},
        },
        {
            "name": "Bulk export investigation",
            "conditions": {"event_type": "candidate.mass_export", "min_risk_score": 55, "auto_execute": False},
            "actions": {"actions": [{"type": "throttle_user_control"}, {"type": "investigate"}]},
        },
    ]

    created = []
    for d in defaults:
        created.append(
            create_security_rule(
                organization_id,
                name=d["name"],
                trigger="security.event",
                enabled=True,
                conditions=d["conditions"],
                actions=d["actions"],
            )
        )
    return created
