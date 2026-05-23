import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from passlib.context import CryptContext
from passlib.exc import MissingBackendError
from sqlalchemy import Boolean, Column, DateTime, Integer, String, inspect, or_, text
from sqlalchemy.orm import Session

from backend.db.database import Base, SessionLocal, engine
from backend.models.enterprise import (
    EmailVerificationToken,
    Membership,
    Organization,
    PasswordResetToken,
    RefreshToken,
)

load_dotenv()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    organization_id = Column(Integer, nullable=True, index=True)
    role = Column(String, default="recruiter", nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    refresh_token_version = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True, index=True)
    locked_reason = Column(String, nullable=True)


JWT_SECRET = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "60"))
JWT_REFRESH_EXPIRATION_DAYS = int(os.getenv("JWT_REFRESH_EXPIRATION_DAYS", "14"))
JWT_VERIFICATION_EXPIRATION_HOURS = int(os.getenv("JWT_VERIFICATION_EXPIRATION_HOURS", "48"))
JWT_RESET_EXPIRATION_HOURS = int(os.getenv("JWT_RESET_EXPIRATION_HOURS", "2"))
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
MICROSOFT_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", "")
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    argon2__memory_cost=int(os.getenv("ARGON2_MEMORY_COST", "65536")),
    argon2__time_cost=int(os.getenv("ARGON2_TIME_COST", "3")),
    argon2__parallelism=int(os.getenv("ARGON2_PARALLELISM", "4")),
)


class PasswordPolicyError(ValueError):
    pass


def validate_password_policy(password: str) -> None:
    if not password or len(password) < 8:
        raise PasswordPolicyError("Password must be at least 8 characters long")
    if len(password) > 128:
        raise PasswordPolicyError("Password must be 128 characters or fewer")
    if password.lower() in {"password", "password123", "admin1234", "changeme123"}:
        raise PasswordPolicyError("Password is too common")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise PasswordPolicyError("Password must include letters and numbers")


def ensure_auth_tables():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "users" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("users")}
        additions = {
            "organization_id": "INTEGER",
            "role": "VARCHAR DEFAULT 'recruiter'",
            "email_verified": "BOOLEAN DEFAULT 0",
            "refresh_token_version": "INTEGER DEFAULT 0",
            "locked_until": "DATETIME",
            "locked_reason": "VARCHAR",
        }
        with engine.begin() as connection:
            for column_name, ddl in additions.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {ddl}"))


def hash_password(password: str) -> str:
    validate_password_policy(password)
    try:
        return pwd_context.hash(password)
    except MissingBackendError:
        bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return bcrypt_context.hash(password)


def _legacy_sha256_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("$argon2") or password_hash.startswith("$2"):
        try:
            return pwd_context.verify(password, password_hash)
        except Exception:
            return False

    return hmac.compare_digest(password_hash, _legacy_sha256_password(password))


def password_hash_needs_upgrade(password_hash: str) -> bool:
    return not password_hash.startswith("$argon2")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    parts = [part for part in lowered.split("-") if part]
    return "-".join(parts)[:80] or f"org-{secrets.token_hex(3)}"


def _encode_token(payload: dict) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def decode_token_payload(token: str) -> Optional[dict]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        actual_signature = _base64url_decode(encoded_signature)

        if not hmac.compare_digest(expected_signature, actual_signature):
            return None

        payload = json.loads(_base64url_decode(encoded_payload))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None

        return payload
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None, extra_claims: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + (expires_delta or timedelta(minutes=JWT_EXPIRATION_MINUTES))
    payload = {
        "sub": subject,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return _encode_token(payload)


def verify_access_token(token: str) -> Optional[str]:
    payload = decode_token_payload(token)
    if not payload or payload.get("typ", "access") != "access":
        return None
    return payload.get("sub")


def get_user_by_email(email: str) -> Optional[User]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        return db.query(User).filter(User.username == email.lower()).first()
    finally:
        db.close()


def get_user_by_id(user_id: int) -> Optional[User]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        return db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()


def _ensure_workspace(db: Session, user: User, organization_name: Optional[str] = None, role: str = "company_admin"):
    if user.organization_id:
        membership = db.query(Membership).filter(
            Membership.user_id == user.id,
            Membership.organization_id == user.organization_id,
            Membership.is_active == True,  # noqa: E712
        ).first()
        if membership:
            return membership

        # If the org already exists but the membership row is missing (legacy data),
        # attach the user to their existing organization instead of creating a new one.
        existing_org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if existing_org:
            effective_role = (user.role or role or "recruiter")[:80]
            membership = Membership(
                user_id=user.id,
                organization_id=user.organization_id,
                role=effective_role,
                is_active=True,
            )
            db.add(membership)
            return membership

    base_name = organization_name or f"{(user.username or 'team').split('@')[0].title()} Workspace"
    org_name = base_name
    slug = _slugify(base_name)
    unique_slug = slug
    suffix = 1
    while db.query(Organization).filter(
        or_(Organization.slug == unique_slug, Organization.name == org_name)
    ).first():
        suffix += 1
        unique_slug = f"{slug}-{suffix}"
        org_name = f"{base_name} {suffix}"

    organization = Organization(name=org_name, slug=unique_slug, plan="free")
    db.add(organization)
    db.flush()

    user.organization_id = organization.id
    user.role = user.role or role

    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=role,
        is_active=True,
    )
    db.add(membership)
    return membership


def ensure_user_workspace(user_id: int) -> Optional[User]:
    """
    Ensure the user has an organization + active membership.

    This prevents "orphan" accounts that can authenticate but have no workspace context,
    which breaks org-scoped modules like shortlist, analytics, and enterprise ops.
    """
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            return None
        did_change = False

        # Fast path: org + membership already exist.
        if user.organization_id:
            membership = db.query(Membership).filter(
                Membership.user_id == user.id,
                Membership.organization_id == user.organization_id,
                Membership.is_active == True,  # noqa: E712
            ).first()
            if membership:
                if membership.role and user.role != membership.role:
                    user.role = membership.role
                    did_change = True
                if did_change:
                    db.commit()
                    db.refresh(user)
                return user

        # Slow path: create or repair workspace/membership.
        membership = _ensure_workspace(db, user, organization_name=None, role=(user.role or "company_admin"))
        if membership and membership.role and user.role != membership.role:
            user.role = membership.role
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def get_user_memberships(user_id: int) -> list[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        rows = db.query(Membership, Organization).join(
            Organization,
            Membership.organization_id == Organization.id,
        ).filter(
            Membership.user_id == user_id,
            Membership.is_active == True,  # noqa: E712
        ).all()
        return [
            {
                "organization_id": organization.id,
                "organization_name": organization.name,
                "organization_slug": organization.slug,
                "role": membership.role,
                "plan": organization.plan,
            }
            for membership, organization in rows
        ]
    finally:
        db.close()


def _token_bundle_for_user(user: User, organization_id: Optional[int] = None, role: Optional[str] = None) -> dict:
    org_id = organization_id if organization_id is not None else user.organization_id
    current_role = role or user.role or "recruiter"
    access_token = create_access_token(
        user.username,
        extra_claims={
            "uid": user.id,
            "user_id": user.id,
            "organization_id": org_id,
            # Aliases used by enterprise clients (workspace/tenant terminology).
            "workspace_id": org_id,
            "tenant_id": org_id,
            "role": current_role,
            "email_verified": bool(user.email_verified),
        },
    )
    refresh_token = create_refresh_token(user, organization_id=org_id, role=current_role)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRATION_MINUTES * 60,
        "user": serialize_user(user, organization_id=org_id, role=current_role),
    }


def serialize_user(user: User, organization_id: Optional[int] = None, role: Optional[str] = None) -> dict:
    memberships = get_user_memberships(user.id)
    return {
        "id": user.id,
        "email": user.username,
        "organization_id": organization_id if organization_id is not None else user.organization_id,
        "role": role or user.role or "recruiter",
        "email_verified": bool(user.email_verified),
        "memberships": memberships,
    }


def register_user(email: str, password: str) -> bool:
    return register_user_record(email, password) is not None


def register_user_record(
    email: str,
    password: str,
    organization_name: Optional[str] = None,
    role: str = "company_admin",
    email_verified: bool = False,
) -> Optional[User]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        normalized_email = email.lower()
        existing = db.query(User).filter(User.username == normalized_email).first()
        if existing:
            return None

        user = User(
            username=normalized_email,
            password=hash_password(password),
            role=role,
            email_verified=email_verified,
        )
        db.add(user)
        db.flush()
        _ensure_workspace(db, user, organization_name=organization_name, role=role)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def authenticate_user(email: str, password: str) -> bool:
    return authenticate_user_record(email, password) is not None


def authenticate_user_record(email: str, password: str) -> Optional[User]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        normalized_email = email.lower()
        user = db.query(User).filter(User.username == normalized_email).first()
        if not user:
            return None

        # Temporary lockouts (security containment).
        try:
            if user.locked_until and user.locked_until > datetime.utcnow():
                return None
        except Exception:
            pass

        is_valid = verify_password(password, user.password)
        if is_valid and password_hash_needs_upgrade(user.password):
            user.password = hash_password(password)
            db.commit()
            db.refresh(user)

        return user if is_valid else None
    finally:
        db.close()


def create_refresh_token(user: User, organization_id: Optional[int] = None, role: Optional[str] = None) -> str:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=JWT_REFRESH_EXPIRATION_DAYS)
        raw = secrets.token_urlsafe(48)
        payload = {
            "sub": user.username,
            "uid": user.id,
            "organization_id": organization_id if organization_id is not None else user.organization_id,
            "role": role or user.role or "recruiter",
            "typ": "refresh",
            "ver": int(user.refresh_token_version or 0),
            "jti": raw,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = _encode_token(payload)
        db.add(
            RefreshToken(
                user_id=user.id,
                token_hash=_hash_token(token),
                expires_at=expires_at.replace(tzinfo=None),
            )
        )
        db.commit()
        return token
    finally:
        db.close()


def revoke_refresh_token(token: str):
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(token)).first()
        if row and not row.revoked_at:
            row.revoked_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def rotate_refresh_token(token: str) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        payload = decode_token_payload(token)
        if not payload or payload.get("typ") != "refresh":
            return None

        hashed = _hash_token(token)
        stored = db.query(RefreshToken).filter(RefreshToken.token_hash == hashed).first()
        if not stored or stored.revoked_at or stored.expires_at < datetime.utcnow():
            return None

        user = db.query(User).filter(User.id == int(payload.get("uid", 0))).first()
        if not user:
            return None

        if int(payload.get("ver", -1)) != int(user.refresh_token_version or 0):
            return None

        stored.revoked_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
    finally:
        db.close()

    fresh_user = get_user_by_id(int(payload.get("uid", 0)))
    if not fresh_user:
        return None
    return _token_bundle_for_user(
        fresh_user,
        organization_id=payload.get("organization_id"),
        role=payload.get("role"),
    )


def revoke_all_sessions(user_id: int):
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return
        user.refresh_token_version = int(user.refresh_token_version or 0) + 1
        db.commit()
    finally:
        db.close()


def lock_user_temporarily(user_id: int, minutes: int = 15, reason: str = "") -> Optional[dict]:
    """
    Safe lock: sets locked_until and revokes refresh tokens to force re-auth.
    """
    ensure_auth_tables()
    minutes = int(minutes or 15)
    minutes = max(1, min(240, minutes))  # hard safety cap: 4 hours
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            return None
        user.locked_until = datetime.utcnow() + timedelta(minutes=minutes)
        user.locked_reason = (reason or "security_containment")[:200]
        user.refresh_token_version = int(user.refresh_token_version or 0) + 1
        db.commit()
        return {
            "user_id": user.id,
            "locked_until": user.locked_until.isoformat() if user.locked_until else None,
            "reason": user.locked_reason,
        }
    finally:
        db.close()


def unlock_user(user_id: int) -> bool:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            return False
        user.locked_until = None
        user.locked_reason = None
        db.commit()
        return True
    finally:
        db.close()


def issue_password_reset(email: str) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == email.lower()).first()
        if not user:
            return None

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=JWT_RESET_EXPIRATION_HOURS)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                expires_at=expires_at,
            )
        )
        db.commit()
        return {
            "token": raw_token,
            "expires_at": expires_at.isoformat(),
            "email": user.username,
        }
    finally:
        db.close()


def reset_password(raw_token: str, new_password: str) -> bool:
    validate_password_policy(new_password)
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == _hash_token(raw_token)
        ).first()
        if not row or row.used_at or row.expires_at < datetime.utcnow():
            return False

        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            return False

        user.password = hash_password(new_password)
        user.refresh_token_version = int(user.refresh_token_version or 0) + 1
        row.used_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def issue_email_verification(email: str) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == email.lower()).first()
        if not user:
            return None

        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=JWT_VERIFICATION_EXPIRATION_HOURS)
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                expires_at=expires_at,
            )
        )
        db.commit()
        return {
            "token": raw_token,
            "expires_at": expires_at.isoformat(),
            "email": user.username,
        }
    finally:
        db.close()


def verify_email_token(raw_token: str) -> bool:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        row = db.query(EmailVerificationToken).filter(
            EmailVerificationToken.token_hash == _hash_token(raw_token)
        ).first()
        if not row or row.used_at or row.expires_at < datetime.utcnow():
            return False

        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            return False

        user.email_verified = True
        row.used_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def switch_user_workspace(user_id: int, organization_id: int) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        membership = db.query(Membership).filter(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
            Membership.is_active == True,  # noqa: E712
        ).first()
        if not user or not membership:
            return None

        user.organization_id = organization_id
        user.role = membership.role
        db.commit()
        db.refresh(user)
        return _token_bundle_for_user(user, organization_id=organization_id, role=membership.role)
    finally:
        db.close()


def get_user_context_from_token(token: str) -> Optional[dict]:
    payload = decode_token_payload(token)
    if not payload or payload.get("typ") != "access":
        return None

    user = get_user_by_id(int(payload.get("uid", 0))) if payload.get("uid") else get_user_by_email(payload.get("sub", ""))
    if not user:
        return None

    # Ensure the authenticated user always has an org + active membership.
    # This prevents "empty organization" states that break org-scoped modules.
    user = ensure_user_workspace(user.id) or user
    try:
        from backend.services.dev_bootstrap_service import bootstrap_user_if_needed, development_claim_role

        bootstrap_user_if_needed(user.username)
        user = get_user_by_id(user.id) or user
        resolved_role = development_claim_role(user.role, payload.get("role"))
    except Exception:
        resolved_role = payload.get("role") or user.role or "recruiter"

    resolved_org_id = payload.get("organization_id") or payload.get("workspace_id") or user.organization_id

    return {
        "user": user,
        "user_id": user.id,
        "email": user.username,
        "claims": payload,
        "organization_id": resolved_org_id,
        "workspace_id": resolved_org_id,
        "tenant_id": resolved_org_id,
        "role": resolved_role or "recruiter",
    }


def build_login_response(user: User) -> dict:
    hydrated = ensure_user_workspace(user.id) or user
    return _token_bundle_for_user(hydrated)


def microsoft_oauth_config() -> dict:
    configured = bool(MICROSOFT_CLIENT_ID and MICROSOFT_REDIRECT_URI)
    url = None
    if configured:
        url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
            f"?client_id={MICROSOFT_CLIENT_ID}"
            f"&response_type=code&redirect_uri={MICROSOFT_REDIRECT_URI}"
            "&scope=openid%20email%20profile"
        )
    return {
        "configured": configured,
        "authorization_url": url,
    }
