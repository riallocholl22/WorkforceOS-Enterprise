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
    AuthAuditLog,
    EmailVerificationToken,
    Membership,
    MfaChallenge,
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
    phone = Column(String, nullable=True, index=True)
    phone_verified = Column(Boolean, default=False, nullable=False)
    mfa_enabled = Column(Boolean, default=True, nullable=False)
    failed_login_count = Column(Integer, default=0, nullable=False)


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
_TEST_MFA_OTP_CACHE: dict[str, dict] = {}


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
            "phone": "VARCHAR",
            "phone_verified": "BOOLEAN DEFAULT 0",
            "mfa_enabled": "BOOLEAN DEFAULT 1",
            "failed_login_count": "INTEGER DEFAULT 0",
        }
        with engine.begin() as connection:
            for column_name, ddl in additions.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {ddl}"))
    inspector = inspect(engine)
    if "email_verification_tokens" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("email_verification_tokens")}
        additions = {
            "channel": "VARCHAR DEFAULT 'email'",
            "destination": "VARCHAR",
            "attempts": "INTEGER DEFAULT 0",
            "max_attempts": "INTEGER DEFAULT 5",
            "locked_until": "DATETIME",
            "last_sent_at": "DATETIME",
        }
        with engine.begin() as connection:
            for column_name, ddl in additions.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE email_verification_tokens ADD COLUMN {column_name} {ddl}"))


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


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}"


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not local or not domain:
        return email
    return f"{local[:2]}{'*' * max(2, len(local) - 2)}@{domain}"


def mfa_test_mode_enabled() -> bool:
    environment = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).lower()
    if environment in {"production", "prod", "staging"}:
        return False
    if environment == "test":
        return True
    return os.getenv("E2E_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}


def _store_test_mfa_otp(challenge_token_hash: str, otp: str, expires_at: datetime, user_id: int) -> None:
    if not mfa_test_mode_enabled():
        return
    now = datetime.utcnow()
    expired_keys = [
        key for key, value in _TEST_MFA_OTP_CACHE.items()
        if value.get("expires_at") and value["expires_at"] < now
    ]
    for key in expired_keys:
        _TEST_MFA_OTP_CACHE.pop(key, None)
    _TEST_MFA_OTP_CACHE[challenge_token_hash] = {
        "otp": otp,
        "expires_at": expires_at,
        "user_id": user_id,
        "created_at": now,
    }


def get_test_mfa_otp(challenge_token: str) -> Optional[dict]:
    if not mfa_test_mode_enabled():
        return None
    cached = _TEST_MFA_OTP_CACHE.get(_hash_token(challenge_token))
    if not cached or cached["expires_at"] < datetime.utcnow():
        return None
    return {
        "otp": cached["otp"],
        "expires_at": cached["expires_at"].isoformat(),
        "user_id": cached["user_id"],
    }


def log_auth_event(
    event_type: str,
    user: Optional[User] = None,
    email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    organization_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    ensure_auth_tables()
    payload = details or {}
    db: Session = SessionLocal()
    try:
        db.add(
            AuthAuditLog(
                event_type=event_type,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:500] or None,
                role=getattr(user, "role", None),
                user_id=getattr(user, "id", None),
                organization_id=organization_id if organization_id is not None else getattr(user, "organization_id", None),
                details={**payload, "email": email or getattr(user, "username", None)},
            )
        )
        db.commit()
    finally:
        db.close()

    try:
        from backend.services.enterprise_service import log_audit_event
        from backend.services.security_ai.threat_service import monitor_event

        audit_action = f"auth.{event_type}"
        log_audit_event(
            action=audit_action,
            entity_type="auth",
            entity_id=str(getattr(user, "id", None) or email or "anonymous"),
            organization_id=organization_id if organization_id is not None else getattr(user, "organization_id", None),
            user_id=getattr(user, "id", None),
            details={**payload, "source_ip": ip_address, "user_agent": user_agent},
        )
        if event_type in {"login_failure", "otp_failure", "account_lockout", "suspicious_activity", "otp_abuse"}:
            monitor_event(
                "auth.lockout" if event_type == "account_lockout" else "auth.failed",
                organization_id=organization_id if organization_id is not None else getattr(user, "organization_id", None),
                user_id=getattr(user, "id", None),
                source_ip=ip_address,
                details={**payload, "auth_event": event_type, "email": email or getattr(user, "username", None)},
            )
    except Exception:
        pass


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
        "phone": mask_phone(getattr(user, "phone", None)),
        "organization_id": organization_id if organization_id is not None else user.organization_id,
        "role": role or user.role or "recruiter",
        "email_verified": bool(user.email_verified),
        "phone_verified": bool(getattr(user, "phone_verified", False)),
        "mfa_enabled": bool(getattr(user, "mfa_enabled", True)),
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
    phone: Optional[str] = None,
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
            phone=phone,
            phone_verified=False,
            mfa_enabled=True,
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
    return issue_account_verification(email=email, channel="email")


def issue_account_verification(email: Optional[str] = None, phone: Optional[str] = None, channel: str = "email", enforce_cooldown: bool = False) -> Optional[dict]:
    ensure_auth_tables()
    channel = "sms" if channel == "sms" else "email"
    normalized_email = (email or "").strip().lower()
    normalized_phone = (phone or "").strip() or None
    db: Session = SessionLocal()
    try:
        user = None
        if normalized_email:
            user = db.query(User).filter(User.username == normalized_email).first()
        if not user and normalized_phone:
            user = db.query(User).filter(User.phone == normalized_phone).first()
        if not user:
            return None

        if channel == "sms" and not getattr(user, "phone", None):
            channel = "email"
        destination = user.phone if channel == "sms" else user.username
        now = datetime.utcnow()
        if enforce_cooldown:
            latest = db.query(EmailVerificationToken).filter(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.channel == channel,
                EmailVerificationToken.used_at.is_(None),
            ).order_by(EmailVerificationToken.created_at.desc()).first()
            if latest and latest.last_sent_at and (now - latest.last_sent_at).total_seconds() < 60:
                return {
                    "cooldown_seconds": 60 - int((now - latest.last_sent_at).total_seconds()),
                    "email": user.username,
                    "phone": mask_phone(getattr(user, "phone", None)),
                    "channel": channel,
                }

        raw_token = _generate_otp()
        expires_at = now + timedelta(minutes=10)
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                channel=channel,
                destination=destination,
                expires_at=expires_at,
                attempts=0,
                max_attempts=5,
                last_sent_at=now,
            )
        )
        db.commit()
        log_auth_event("verification_code_sent", user=user, details={"channel": channel, "destination": mask_email(destination) if channel == "email" else mask_phone(destination)})
        return {
            "token": raw_token,
            "otp": raw_token,
            "expires_at": expires_at.isoformat(),
            "email": user.username,
            "phone": mask_phone(getattr(user, "phone", None)),
            "channel": channel,
            "masked_destination": mask_email(destination) if channel == "email" else mask_phone(destination),
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


def verify_email_code(email: str, raw_token: str) -> dict:
    return verify_account_verification_code(email=email, code=raw_token, channel="email")


def verify_phone_code(phone: str, raw_token: str) -> dict:
    return verify_account_verification_code(phone=phone, code=raw_token, channel="sms")


def verify_account_verification_code(email: Optional[str] = None, phone: Optional[str] = None, code: str = "", channel: str = "email") -> dict:
    ensure_auth_tables()
    normalized_email = (email or "").strip().lower()
    normalized_phone = (phone or "").strip()
    code = (code or "").strip()
    channel = "sms" if channel == "sms" else "email"
    if not code:
        return {"verified": False, "reason": "missing_code"}
    db: Session = SessionLocal()
    try:
        user = None
        if normalized_email:
            user = db.query(User).filter(User.username == normalized_email).first()
        if not user and normalized_phone:
            user = db.query(User).filter(User.phone == normalized_phone).first()
        if not user:
            return {"verified": False, "reason": "email_not_found" if channel == "email" else "phone_not_found"}

        row = db.query(EmailVerificationToken).filter(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.channel == channel,
            EmailVerificationToken.token_hash == _hash_token(code),
        ).first()
        if not row:
            latest = db.query(EmailVerificationToken).filter(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.channel == channel,
                EmailVerificationToken.used_at.is_(None),
            ).order_by(EmailVerificationToken.created_at.desc()).first()
            if latest:
                latest.attempts = int(latest.attempts or 0) + 1
                if latest.attempts >= int(latest.max_attempts or 5):
                    latest.locked_until = datetime.utcnow() + timedelta(minutes=10)
                db.commit()
                log_auth_event("verification_failed", user=user, details={"attempts": latest.attempts, "channel": channel, "reason": "invalid_code"})
            return {"verified": False, "reason": "invalid_code"}
        if row.used_at:
            return {"verified": False, "reason": "invalid_code"}
        now = datetime.utcnow()
        if row.locked_until and row.locked_until > now:
            return {"verified": False, "reason": "too_many_attempts"}
        if row.expires_at < datetime.utcnow():
            log_auth_event("verification_failed", user=user, details={"reason": "expired", "channel": channel})
            return {"verified": False, "reason": "expired"}
        if row.attempts >= row.max_attempts:
            row.locked_until = now + timedelta(minutes=10)
            db.commit()
            log_auth_event("verification_failed", user=user, details={"reason": "too_many_attempts", "channel": channel})
            return {"verified": False, "reason": "too_many_attempts"}

        if channel == "sms":
            user.phone_verified = True
        else:
            user.email_verified = True
        row.used_at = datetime.utcnow()
        db.commit()
        log_auth_event("phone_verified" if channel == "sms" else "email_verified", user=user, details={"channel": channel})
        return {"verified": True, "reason": "verified", "user": serialize_user(user)}
    finally:
        db.close()


def record_failed_account_verification(email: Optional[str] = None, phone: Optional[str] = None, code: str = "", channel: str = "email") -> None:
    ensure_auth_tables()
    normalized_email = (email or "").strip().lower()
    normalized_phone = (phone or "").strip()
    db: Session = SessionLocal()
    try:
        user = None
        if normalized_email:
            user = db.query(User).filter(User.username == normalized_email).first()
        if not user and normalized_phone:
            user = db.query(User).filter(User.phone == normalized_phone).first()
        if not user:
            return
        row = db.query(EmailVerificationToken).filter(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.channel == ("sms" if channel == "sms" else "email"),
            EmailVerificationToken.token_hash == _hash_token((code or "").strip()),
        ).first()
        if not row or row.used_at:
            return
        row.attempts = int(row.attempts or 0) + 1
        if row.attempts >= int(row.max_attempts or 5):
            row.locked_until = datetime.utcnow() + timedelta(minutes=10)
        db.commit()
        log_auth_event("verification_failed", user=user, details={"attempts": row.attempts, "channel": channel})
    finally:
        db.close()


def create_mfa_challenge(
    user: User,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    delivery_channel: str = "email",
) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        hydrated = db.query(User).filter(User.id == user.id).first()
        if not hydrated:
            return None

        otp = _generate_otp()
        challenge_token = secrets.token_urlsafe(32)
        now = datetime.utcnow()
        challenge = MfaChallenge(
            user_id=hydrated.id,
            email=hydrated.username,
            phone=getattr(hydrated, "phone", None),
            otp_hash=_hash_token(otp),
            challenge_token_hash=_hash_token(challenge_token),
            expires_at=now + timedelta(minutes=10),
            attempts=0,
            max_attempts=5,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:500] or None,
            last_sent_at=now,
            delivery_channel=delivery_channel if delivery_channel in {"email", "sms"} else "email",
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        _store_test_mfa_otp(challenge.challenge_token_hash, otp, challenge.expires_at, hydrated.id)

        try:
            from backend.services.enterprise_service import create_notification

            if not mfa_test_mode_enabled():
                create_notification(
                    user_id=hydrated.id,
                    organization_id=hydrated.organization_id,
                    kind="mfa_otp",
                    subject="WorkforceOS Login Verification",
                    message="Use this one-time code to complete login. It expires in 10 minutes. If you did not request it, change your password and contact your company admin.",
                    metadata={"expires_at": challenge.expires_at.isoformat(), "channel": challenge.delivery_channel},
                )
        except Exception:
            pass

        log_auth_event(
            "otp_sent",
            user=hydrated,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"challenge_id": challenge.id, "channel": challenge.delivery_channel},
        )
        return {
            "challenge_token": challenge_token,
            "expires_at": challenge.expires_at.isoformat(),
            "delivery_channel": challenge.delivery_channel,
            "masked_email": mask_email(hydrated.username),
            "masked_phone": mask_phone(getattr(hydrated, "phone", None)),
            "test_otp_available": mfa_test_mode_enabled(),
        }
    finally:
        db.close()


def verify_mfa_challenge(challenge_token: str, otp: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        challenge = db.query(MfaChallenge).filter(
            MfaChallenge.challenge_token_hash == _hash_token(challenge_token)
        ).first()
        now = datetime.utcnow()
        if not challenge or challenge.consumed_at:
            log_auth_event("otp_failure", ip_address=ip_address, user_agent=user_agent, details={"reason": "invalid_challenge"})
            return None

        user = db.query(User).filter(User.id == challenge.user_id).first()
        if not user:
            return None

        if challenge.expires_at < now:
            log_auth_event("otp_failure", user=user, ip_address=ip_address, user_agent=user_agent, details={"reason": "expired"})
            return None

        if challenge.attempts >= challenge.max_attempts:
            user.locked_until = now + timedelta(minutes=15)
            user.locked_reason = "mfa_attempt_limit"
            db.commit()
            log_auth_event("account_lockout", user=user, ip_address=ip_address, user_agent=user_agent, details={"reason": "mfa_attempt_limit"})
            return None

        if not hmac.compare_digest(challenge.otp_hash, _hash_token((otp or "").strip())):
            challenge.attempts += 1
            if challenge.attempts >= challenge.max_attempts:
                user.locked_until = now + timedelta(minutes=15)
                user.locked_reason = "mfa_attempt_limit"
            db.commit()
            log_auth_event("otp_failure", user=user, ip_address=ip_address, user_agent=user_agent, details={"attempts": challenge.attempts})
            return None

        challenge.consumed_at = now
        user.failed_login_count = 0
        db.commit()
        db.refresh(user)
        log_auth_event("otp_verified", user=user, ip_address=ip_address, user_agent=user_agent, details={"challenge_id": challenge.id})
        return _token_bundle_for_user(user)
    finally:
        db.close()


def resend_mfa_challenge(challenge_token: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        challenge = db.query(MfaChallenge).filter(
            MfaChallenge.challenge_token_hash == _hash_token(challenge_token)
        ).first()
        now = datetime.utcnow()
        if not challenge or challenge.consumed_at or challenge.expires_at < now:
            return None
        if challenge.last_sent_at and (now - challenge.last_sent_at).total_seconds() < 60:
            return {"cooldown_seconds": 60 - int((now - challenge.last_sent_at).total_seconds())}

        otp = _generate_otp()
        challenge.otp_hash = _hash_token(otp)
        challenge.last_sent_at = now
        challenge.expires_at = now + timedelta(minutes=10)
        db.commit()
        _store_test_mfa_otp(challenge.challenge_token_hash, otp, challenge.expires_at, challenge.user_id)
        user = db.query(User).filter(User.id == challenge.user_id).first()
        if user:
            try:
                from backend.services.enterprise_service import create_notification

                if not mfa_test_mode_enabled():
                    create_notification(
                        user_id=user.id,
                        organization_id=user.organization_id,
                        kind="mfa_otp",
                        subject="WorkforceOS Login Verification",
                        message="Use this one-time code to complete login. It expires in 10 minutes. If you did not request it, contact your company admin.",
                        metadata={"expires_at": challenge.expires_at.isoformat(), "channel": challenge.delivery_channel},
                    )
            except Exception:
                pass
            log_auth_event("otp_sent", user=user, ip_address=ip_address, user_agent=user_agent, details={"resend": True})
        return {
            "message": "A new verification code has been sent.",
            "expires_at": challenge.expires_at.isoformat(),
            "test_otp_available": mfa_test_mode_enabled(),
        }
    finally:
        db.close()


def set_user_mfa(user_id: int, enabled: bool) -> Optional[dict]:
    ensure_auth_tables()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        user.mfa_enabled = enabled
        db.commit()
        db.refresh(user)
        log_auth_event("mfa_setup" if enabled else "mfa_disable", user=user)
        return serialize_user(user)
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
        resolved_role = "applicant" if (user.role or "").lower() == "applicant" else development_claim_role(user.role, payload.get("role"))
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
