import secrets
from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import HTTPException

from backend.db.database import SessionLocal
from backend.models.enterprise import Membership, TeamInvitation
from backend.services.auth_service import User, register_user_record
from backend.services.enterprise_service import create_notification, log_audit_event


ALLOWED_ROLES = {"super_admin", "company_admin", "recruiter", "interviewer", "hiring_manager", "candidate"}


def invite_member(organization_id: int, invited_by_user_id: int, email: str, role: str) -> Dict[str, Any]:
    role = role.lower().strip()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Unsupported team role")

    token = secrets.token_urlsafe(24)
    db = SessionLocal()
    try:
        invitation = TeamInvitation(
            organization_id=organization_id,
            invited_by_user_id=invited_by_user_id,
            email=email.lower().strip(),
            role=role,
            token=token,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()
        db.refresh(invitation)
        create_notification(
            user_id=invited_by_user_id,
            organization_id=organization_id,
            kind="team_invitation",
            subject="Team invitation created",
            message=f"Invitation sent for {invitation.email} as {role}.",
            metadata={"invitation_token": token},
        )
        log_audit_event("team.invite", "invitation", str(invitation.id), organization_id, invited_by_user_id, {"email": invitation.email, "role": role})
        return serialize_invitation(invitation)
    finally:
        db.close()


def accept_invitation(token: str, password: str = "changeme123") -> Dict[str, Any]:
    db = SessionLocal()
    try:
        invitation = db.query(TeamInvitation).filter(TeamInvitation.token == token).first()
        if not invitation or invitation.status != "pending" or invitation.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired invitation")

        user = db.query(User).filter(User.username == invitation.email).first()
    finally:
        db.close()

    if not user:
        user = register_user_record(invitation.email, password, organization_name=None, role=invitation.role, email_verified=True)

    db = SessionLocal()
    try:
        invitation = db.query(TeamInvitation).filter(TeamInvitation.token == token).first()
        membership = db.query(Membership).filter(
            Membership.user_id == user.id,
            Membership.organization_id == invitation.organization_id,
        ).first()
        if not membership:
            membership = Membership(
                user_id=user.id,
                organization_id=invitation.organization_id,
                role=invitation.role,
                is_active=True,
            )
            db.add(membership)
        invitation.status = "accepted"
        invitation.accepted_at = datetime.utcnow()
        db.commit()
        return {"email": invitation.email, "role": invitation.role, "organization_id": invitation.organization_id, "status": invitation.status}
    finally:
        db.close()


def list_team(organization_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        rows = db.query(Membership, User).join(User, Membership.user_id == User.id).filter(
            Membership.organization_id == organization_id,
            Membership.is_active == True,  # noqa: E712
        ).all()
        invitations = db.query(TeamInvitation).filter(TeamInvitation.organization_id == organization_id).order_by(TeamInvitation.created_at.desc()).limit(50).all()
        return {
            "members": [
                {
                    "user_id": user.id,
                    "email": user.username,
                    "role": membership.role,
                    "email_verified": bool(user.email_verified),
                    "created_at": membership.created_at.isoformat() if membership.created_at else None,
                }
                for membership, user in rows
            ],
            "invitations": [serialize_invitation(invitation) for invitation in invitations],
        }
    finally:
        db.close()


def serialize_invitation(invitation: TeamInvitation) -> Dict[str, Any]:
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "token": invitation.token,
        "status": invitation.status,
        "expires_at": invitation.expires_at.isoformat() if invitation.expires_at else None,
        "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
    }
