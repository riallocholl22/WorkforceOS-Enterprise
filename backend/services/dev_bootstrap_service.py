import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.core.config import get_environment, is_production
from backend.db.database import SessionLocal
from backend.models.enterprise import AuditLog, Membership, Organization, Subscription
from backend.services.auth_service import User, ensure_auth_tables
from backend.services.enterprise_service import DEFAULT_LIMITS

logger = logging.getLogger("ai_recruitment.dev_bootstrap")


DEFAULT_ORG_NAME = os.getenv("DEV_DEFAULT_ORG_NAME", "AI Recruit Development Workspace")
DEFAULT_ORG_SLUG = os.getenv("DEV_DEFAULT_ORG_SLUG", "ai-recruit-development")
DEFAULT_ADMIN_ROLE = "super_admin"


def dev_bootstrap_enabled() -> bool:
    if is_production():
        return False
    return os.getenv("DEV_BOOTSTRAP_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def run_development_bootstrap() -> Dict[str, Any]:
    if not dev_bootstrap_enabled():
        logger.info("dev_bootstrap_skipped environment=%s", get_environment())
        return {"enabled": False, "reason": "disabled_or_production"}

    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        admin_user = _select_development_admin(db)
        if not admin_user:
            logger.info("dev_bootstrap_waiting_for_first_user")
            return {"enabled": True, "status": "waiting_for_first_user"}

        organization = _ensure_default_organization(db)
        _assign_development_admin(db, admin_user, organization)
        _ensure_enterprise_subscription(db, organization)
        _audit_bootstrap(db, admin_user, organization)
        db.commit()

        logger.info(
            "dev_bootstrap_complete user_id=%s email=%s organization_id=%s role=%s",
            admin_user.id,
            admin_user.username,
            organization.id,
            DEFAULT_ADMIN_ROLE,
        )
        return {
            "enabled": True,
            "status": "ready",
            "user_id": admin_user.id,
            "email": admin_user.username,
            "organization_id": organization.id,
            "role": DEFAULT_ADMIN_ROLE,
        }
    except Exception:
        db.rollback()
        logger.exception("dev_bootstrap_failed")
        raise
    finally:
        db.close()


def bootstrap_user_if_needed(email: str) -> Dict[str, Any]:
    if not dev_bootstrap_enabled() or not email:
        return {"enabled": False}

    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == email.lower()).first()
        if not user:
            return {"enabled": True, "status": "user_not_found"}

        has_membership = db.query(Membership).filter(
            Membership.user_id == user.id,
            Membership.is_active == True,  # noqa: E712
        ).first()
        if has_membership and user.organization_id and user.role in {"super_admin", "company_admin"}:
            return {"enabled": True, "status": "already_assigned", "organization_id": user.organization_id, "role": user.role}

        organization = _ensure_default_organization(db)
        _assign_development_admin(db, user, organization)
        _ensure_enterprise_subscription(db, organization)
        _audit_bootstrap(db, user, organization)
        db.commit()
        return {
            "enabled": True,
            "status": "assigned",
            "organization_id": organization.id,
            "role": DEFAULT_ADMIN_ROLE,
        }
    except Exception:
        db.rollback()
        logger.exception("dev_user_bootstrap_failed email=%s", email)
        raise
    finally:
        db.close()


def development_claim_role(user_role: Optional[str], token_role: Optional[str]) -> Optional[str]:
    if dev_bootstrap_enabled() and user_role in {"super_admin", "company_admin"}:
        return user_role
    return token_role or user_role


def _select_development_admin(db: Session) -> Optional[User]:
    explicit_email = os.getenv("DEV_ADMIN_EMAIL", "").strip().lower()
    if explicit_email:
        user = db.query(User).filter(User.username == explicit_email).first()
        if user:
            return user

    return db.query(User).order_by(User.id.asc()).first()


def _ensure_default_organization(db: Session) -> Organization:
    organization = db.query(Organization).filter(Organization.slug == DEFAULT_ORG_SLUG).first()
    if organization:
        organization.is_active = True
        organization.plan = "enterprise"
        return organization

    organization = Organization(
        name=DEFAULT_ORG_NAME,
        slug=DEFAULT_ORG_SLUG,
        plan="enterprise",
        is_active=True,
    )
    db.add(organization)
    db.flush()
    return organization


def _assign_development_admin(db: Session, user: User, organization: Organization) -> None:
    user.organization_id = organization.id
    user.role = DEFAULT_ADMIN_ROLE
    user.email_verified = True

    membership = db.query(Membership).filter(
        Membership.user_id == user.id,
        Membership.organization_id == organization.id,
    ).first()
    if not membership:
        membership = Membership(
            user_id=user.id,
            organization_id=organization.id,
            role=DEFAULT_ADMIN_ROLE,
            is_active=True,
        )
        db.add(membership)
    else:
        membership.role = DEFAULT_ADMIN_ROLE
        membership.is_active = True


def _ensure_enterprise_subscription(db: Session, organization: Organization) -> None:
    subscription = db.query(Subscription).filter(Subscription.organization_id == organization.id).first()
    if not subscription:
        subscription = Subscription(
            organization_id=organization.id,
            plan="enterprise",
            status="active",
            usage_json={"ai_calls": 0, "candidate_uploads": 0, "interviews_this_month": 0},
            limits_json=DEFAULT_LIMITS["enterprise"],
        )
        db.add(subscription)
    else:
        subscription.plan = "enterprise"
        subscription.status = "active"
        subscription.limits_json = subscription.limits_json or DEFAULT_LIMITS["enterprise"]


def _audit_bootstrap(db: Session, user: User, organization: Organization) -> None:
    existing = db.query(AuditLog).filter(
        AuditLog.organization_id == organization.id,
        AuditLog.user_id == user.id,
        AuditLog.action == "dev.bootstrap_admin",
    ).first()
    if existing:
        return
    db.add(
        AuditLog(
            organization_id=organization.id,
            user_id=user.id,
            action="dev.bootstrap_admin",
            entity_type="organization",
            entity_id=str(organization.id),
            details={
                "environment": get_environment(),
                "role": DEFAULT_ADMIN_ROLE,
                "permissions": [
                    "organization_owner",
                    "enterprise_admin",
                    "billing_access",
                    "scheduling_access",
                    "team_management_access",
                    "analytics_access",
                ],
            },
        )
    )
