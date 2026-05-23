import re
import secrets
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user, get_current_user_context
from backend.api.responses import ok
from backend.core.config import get_environment
from backend.services.auth_service import (
    PasswordPolicyError,
    authenticate_user_record,
    build_login_response,
    get_user_by_email,
    issue_email_verification,
    issue_password_reset,
    microsoft_oauth_config,
    register_user_record,
    reset_password,
    revoke_all_sessions,
    revoke_refresh_token,
    rotate_refresh_token,
    serialize_user,
    switch_user_workspace,
    validate_password_policy,
    verify_email_token,
)
from backend.services.dev_bootstrap_service import bootstrap_user_if_needed
from backend.services.enterprise_service import create_notification, log_audit_event

router = APIRouter(prefix="/auth", tags=["Authentication"])
_failed_login_attempts: dict[str, int] = {}
MAX_FAILED_LOGIN_ATTEMPTS = 5


class AuthRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=1, max_length=128)
    organization_name: Optional[str] = Field(default=None, max_length=120)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=4000)


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=4000)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=4000)
    new_password: str = Field(..., min_length=8, max_length=128)


class WorkspaceSwitchRequest(BaseModel):
    organization_id: int


class MicrosoftCallbackRequest(BaseModel):
    email: str = Field(..., max_length=320)
    display_name: str = Field(default="Microsoft User", max_length=120)


def _validate_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=400, detail="Invalid email")
    return normalized


def _rate_limit_key(email: str) -> str:
    return email.lower().strip()


def _cookie_secure() -> bool:
    return get_environment() in {"production", "prod", "staging"}


def _set_auth_cookies(response: Response, payload: dict) -> None:
    cookie_args = {
        "httponly": True,
        "secure": _cookie_secure(),
        "samesite": "lax",
        "path": "/",
    }
    if payload.get("access_token"):
        response.set_cookie(
            "access_token",
            payload["access_token"],
            max_age=int(payload.get("expires_in", 3600)),
            **cookie_args,
        )
    if payload.get("refresh_token"):
        response.set_cookie(
            "refresh_token",
            payload["refresh_token"],
            max_age=60 * 60 * 24 * 14,
            **cookie_args,
        )
    response.set_cookie(
        "csrf_token",
        "csrf-placeholder",
        max_age=60 * 60 * 24 * 14,
        httponly=False,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for name in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(name, path="/")


def _oauth_config(provider: str) -> dict:
    prefix = provider.upper()
    client_id = os.getenv(f"{prefix}_CLIENT_ID", "")
    redirect_uri = os.getenv(f"{prefix}_REDIRECT_URI", "")
    configured = bool(client_id and redirect_uri)
    scopes = {
        "google": "openid email profile",
        "github": "read:user user:email",
    }
    auth_hosts = {
        "google": "https://accounts.google.com/o/oauth2/v2/auth",
        "github": "https://github.com/login/oauth/authorize",
    }
    authorization_url = None
    if configured:
        authorization_url = (
            f"{auth_hosts[provider]}?client_id={client_id}"
            f"&redirect_uri={redirect_uri}&response_type=code"
            f"&scope={scopes[provider].replace(' ', '%20')}"
        )
    return {
        "provider": provider,
        "configured": configured,
        "authorization_url": authorization_url,
        "security": {
            "state_required": True,
            "backend_token_exchange": True,
            "frontend_secret_exposure": False,
        },
    }


@router.post("/register")
def register(req: AuthRequest):
    email = _validate_email(req.email)
    try:
        validate_password_policy(req.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = register_user_record(
        email,
        req.password,
        organization_name=req.organization_name,
    )
    if not user:
        raise HTTPException(status_code=400, detail="User already exists")

    verification = issue_email_verification(email)
    create_notification(
        user_id=user.id,
        organization_id=user.organization_id,
        kind="email_verification",
        subject="Verify your AI Recruit account",
        message="Your workspace is ready. Use the verification token to confirm your email address.",
        metadata={"verification_token": verification["token"] if verification else None},
    )
    log_audit_event(
        action="auth.register",
        entity_type="user",
        entity_id=str(user.id),
        organization_id=user.organization_id,
        user_id=user.id,
        details={"email": user.username},
    )

    return ok({
        "message": "Registration successful",
        "user": serialize_user(user),
        "verification_token": verification["token"] if verification else None,
    })


@router.post("/login")
def login(req: AuthRequest, request: Request, response: Response):
    email = _validate_email(req.email)
    key = _rate_limit_key(email)
    failed_count = _failed_login_attempts.get(key, 0)
    if failed_count >= MAX_FAILED_LOGIN_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Please try again later.")

    # Security containment: refuse login if the account is temporarily locked.
    existing = get_user_by_email(email)
    if existing:
        try:
            if getattr(existing, "locked_until", None) and existing.locked_until > datetime.utcnow():
                log_audit_event(
                    action="auth.lockout",
                    entity_type="user",
                    entity_id=str(existing.id),
                    organization_id=getattr(existing, "organization_id", None),
                    user_id=existing.id,
                    details={
                        "email": existing.username,
                        "source_ip": request.client.host if request.client else None,
                        "locked_until": existing.locked_until.isoformat(),
                        "reason": getattr(existing, "locked_reason", None),
                    },
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Account temporarily locked due to security risk.",
                        "code": "account_locked",
                        "guidance": "Wait for the lock window to expire, or ask a company admin to unlock the account in Security Center.",
                        "locked_until": existing.locked_until.isoformat(),
                    },
                )
        except HTTPException:
            raise
        except Exception:
            pass

    user = authenticate_user_record(email, req.password)
    if not user:
        _failed_login_attempts[key] = failed_count + 1
        log_audit_event(
            action="auth.login_failed",
            entity_type="user",
            entity_id=email,
            organization_id=None,
            user_id=None,
            details={
                "email": email,
                "source_ip": request.client.host if request.client else None,
                "attempts": failed_count + 1,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _failed_login_attempts.pop(key, None)
    bootstrap_user_if_needed(user.username)
    user = get_user_by_email(email) or user
    payload = build_login_response(user)
    _set_auth_cookies(response, payload)
    log_audit_event(
        action="auth.login",
        entity_type="user",
        entity_id=str(user.id),
        organization_id=user.organization_id,
        user_id=user.id,
        details={"email": user.username},
    )
    return ok({
        "message": "Login successful",
        **payload,
    })


@router.post("/refresh")
def refresh_tokens(req: RefreshRequest, request: Request, response: Response):
    refresh_token = req.refresh_token
    if refresh_token == "cookie-refresh-placeholder":
        refresh_token = request.cookies.get("refresh_token", "")
    payload = rotate_refresh_token(refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    _set_auth_cookies(response, payload)
    return ok(payload)


@router.post("/logout")
def logout(response: Response, req: Optional[RefreshRequest] = None, context: dict = Depends(get_current_user_context)):
    if req and req.refresh_token:
        revoke_refresh_token(req.refresh_token)
    revoke_all_sessions(context["user"].id)
    _clear_auth_cookies(response)
    log_audit_event(
        action="auth.logout",
        entity_type="user",
        entity_id=str(context["user"].id),
        organization_id=context.get("organization_id"),
        user_id=context["user"].id,
        details={},
    )
    return ok({"message": "Logout successful"})


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    email = _validate_email(req.email)
    issued = issue_password_reset(email)
    if issued:
        user = get_user_by_email(email)
        if user:
            create_notification(
                user_id=user.id,
                organization_id=user.organization_id,
                kind="password_reset",
                subject="Password reset requested",
                message="Use the reset token to choose a new password.",
                metadata={"reset_token": issued["token"]},
            )
    return ok({
        "message": "If the account exists, a reset token has been issued.",
        "reset_token": issued["token"] if issued else None,
    })


@router.post("/reset-password")
def reset_password_route(req: ResetPasswordRequest):
    try:
        did_reset = reset_password(req.token, req.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not did_reset:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    return ok({"message": "Password reset successful"})


@router.post("/send-verification")
def send_verification(req: ForgotPasswordRequest):
    email = _validate_email(req.email)
    issued = issue_email_verification(email)
    if issued:
        user = get_user_by_email(email)
        if user:
            create_notification(
                user_id=user.id,
                organization_id=user.organization_id,
                kind="email_verification",
                subject="Verify your email",
                message="Use the verification token to confirm your account.",
                metadata={"verification_token": issued["token"]},
            )
    return ok({
        "message": "If the account exists, a verification token has been issued.",
        "verification_token": issued["token"] if issued else None,
    })


@router.post("/verify-email")
def verify_email(req: TokenRequest):
    if not verify_email_token(req.token):
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    return ok({"message": "Email verified successfully"})


@router.get("/microsoft/url")
def microsoft_url():
    return ok(microsoft_oauth_config())


@router.post("/microsoft/callback")
def microsoft_callback(req: MicrosoftCallbackRequest, response: Response):
    email = _validate_email(req.email)
    user = get_user_by_email(email)
    if not user:
        placeholder_password = f"Ms-{secrets.token_urlsafe(18)}1"
        user = register_user_record(
            email,
            placeholder_password,
            organization_name=f"{req.display_name} Workspace",
            role="company_admin",
            email_verified=True,
        )
    payload = build_login_response(user)
    _set_auth_cookies(response, payload)
    return ok({
        "message": "Microsoft sign-in successful",
        "configured": microsoft_oauth_config()["configured"],
        **payload,
    })


@router.get("/google/url")
def google_url():
    return ok(_oauth_config("google"))


@router.post("/google/callback")
def google_callback(req: MicrosoftCallbackRequest, response: Response):
    # Placeholder callback keeps token exchange backend-only until Google credentials are configured.
    email = _validate_email(req.email)
    user = get_user_by_email(email)
    if not user:
        placeholder_password = f"Google-{secrets.token_urlsafe(18)}1"
        user = register_user_record(
            email,
            placeholder_password,
            organization_name=f"{req.display_name} Workspace",
            role="company_admin",
            email_verified=True,
        )
    payload = build_login_response(user)
    _set_auth_cookies(response, payload)
    return ok({"message": "Google sign-in successful", "configured": _oauth_config("google")["configured"], **payload})


@router.get("/github/url")
def github_url():
    return ok(_oauth_config("github"))


@router.post("/github/callback")
def github_callback(req: MicrosoftCallbackRequest, response: Response):
    # Placeholder callback keeps token exchange backend-only until GitHub credentials are configured.
    email = _validate_email(req.email)
    user = get_user_by_email(email)
    if not user:
        placeholder_password = f"Github-{secrets.token_urlsafe(18)}1"
        user = register_user_record(
            email,
            placeholder_password,
            organization_name=f"{req.display_name} Workspace",
            role="company_admin",
            email_verified=True,
        )
    payload = build_login_response(user)
    _set_auth_cookies(response, payload)
    return ok({"message": "GitHub sign-in successful", "configured": _oauth_config("github")["configured"], **payload})


@router.post("/switch-workspace")
def switch_workspace(req: WorkspaceSwitchRequest, context: dict = Depends(get_current_user_context)):
    payload = switch_user_workspace(context["user"].id, req.organization_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Workspace membership not found")
    return ok(payload)


@router.get("/me")
def me(current_user: str = Depends(get_current_user)):
    user = get_user_by_email(current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return ok(serialize_user(user))
