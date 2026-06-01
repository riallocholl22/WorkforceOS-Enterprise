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
    create_mfa_challenge,
    get_user_by_email,
    get_test_mfa_otp,
    issue_account_verification,
    issue_email_verification,
    issue_password_reset,
    log_auth_event,
    mask_phone,
    mfa_test_mode_enabled,
    microsoft_oauth_config,
    register_user_record,
    reset_password,
    resend_mfa_challenge,
    revoke_all_sessions,
    revoke_refresh_token,
    rotate_refresh_token,
    serialize_user,
    set_user_mfa,
    switch_user_workspace,
    verify_email_code,
    verify_phone_code,
    validate_password_policy,
    verify_mfa_challenge,
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
    phone: Optional[str] = Field(default=None, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=4000)


class EmailVerificationRequest(BaseModel):
    email: str = Field(..., max_length=320)
    code: str = Field(default="", max_length=4000)
    verification_code: Optional[str] = Field(default=None, max_length=4000)


class PhoneVerificationRequest(BaseModel):
    phone: str = Field(..., max_length=32)
    code: str = Field(default="", max_length=4000)


class VerificationCodeRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=320)
    phone: Optional[str] = Field(default=None, max_length=32)
    channel: str = Field(default="email", pattern="^(email|sms)$")


class MfaVerifyRequest(BaseModel):
    challenge_token: str = Field(..., min_length=20, max_length=4000)
    otp: str = Field(..., min_length=6, max_length=12)


class MfaResendRequest(BaseModel):
    challenge_token: str = Field(..., min_length=20, max_length=4000)


class MfaSetupRequest(BaseModel):
    enabled: bool = True


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


def _validate_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    normalized = re.sub(r"[\s().-]+", "", phone.strip())
    if not re.fullmatch(r"\+?[1-9]\d{7,14}", normalized):
        raise HTTPException(status_code=400, detail="Invalid phone number")
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
        secrets.token_urlsafe(24),
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
    phone = _validate_phone(req.phone)
    try:
        validate_password_policy(req.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = register_user_record(
        email,
        req.password,
        organization_name=req.organization_name,
        phone=phone,
    )
    if not user:
        raise HTTPException(status_code=400, detail="User already exists")

    verification = issue_email_verification(email)
    create_notification(
        user_id=user.id,
        organization_id=user.organization_id,
        kind="email_verification",
        subject="Verify your AI Recruit account",
        message="Your workspace is ready. Use the 6-digit verification code sent to your email within 10 minutes.",
        metadata={"expires_at": verification["expires_at"] if verification else None},
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
        "verification_token": verification["token"] if verification and mfa_test_mode_enabled() else None,
        "verification_expires_at": verification["expires_at"] if verification else None,
        "verification_channel": verification["channel"] if verification else "email",
    })


def _register_role(req: AuthRequest, role: str):
    email = _validate_email(req.email)
    phone = _validate_phone(req.phone)
    try:
        validate_password_policy(req.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = register_user_record(
        email,
        req.password,
        organization_name=req.organization_name,
        role=role,
        phone=phone,
    )
    if not user:
        raise HTTPException(status_code=400, detail="User already exists")
    verification = issue_email_verification(email)
    create_notification(
        user_id=user.id,
        organization_id=user.organization_id,
        kind="email_verification",
        subject="WorkforceOS Account Verification",
        message="Your 6-digit verification code expires in 10 minutes. If you did not create this account, ignore this message and contact security.",
        metadata={"expires_at": verification["expires_at"] if verification else None},
    )
    log_auth_event("registration", user=user, details={"role": role})
    return ok({
        "message": "Registration successful. Verify your email to activate the account.",
        "user": serialize_user(user),
        "verification_token": verification["token"] if verification and mfa_test_mode_enabled() else None,
        "verification_expires_at": verification["expires_at"] if verification else None,
        "verification_channel": verification["channel"] if verification else "email",
    })


@router.post("/applicant/register")
def register_applicant(req: AuthRequest):
    return _register_role(req, "applicant")


@router.post("/recruiter/register")
def register_recruiter(req: AuthRequest):
    return _register_role(req, "recruiter")


@router.post("/interviewer/register")
def register_interviewer(req: AuthRequest):
    return _register_role(req, "interviewer")


def _login_with_mfa(req: AuthRequest, request: Request, account_type: Optional[str] = None):
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
                log_auth_event(
                    "account_lockout",
                    user=existing,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    details={"reason": getattr(existing, "locked_reason", None)},
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
        log_auth_event(
            "login_failure",
            email=email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"attempts": failed_count + 1},
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    role = getattr(user, "role", "") or ""
    if account_type == "applicant" and role != "applicant":
        raise HTTPException(status_code=403, detail="Use Admin / Recruiter Login for this account.")
    if account_type == "admin_recruiter" and role == "applicant":
        raise HTTPException(status_code=403, detail="Use Applicant Login for this account.")

    if not (user.email_verified or getattr(user, "phone_verified", False)):
        log_auth_event(
            "login_failure",
            user=user,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"reason": "email_not_verified"},
        )
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Your account is not verified yet. Verify your email or phone to continue.",
                "code": "account_not_verified",
                "resend_endpoint": "/auth/resend-verification-code",
                "email": user.username,
                "phone": mask_phone(getattr(user, "phone", None)),
            },
        )

    _failed_login_attempts.pop(key, None)
    bootstrap_user_if_needed(user.username)
    user = get_user_by_email(email) or user
    challenge = create_mfa_challenge(
        user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        delivery_channel="sms" if getattr(user, "phone_verified", False) else "email",
    )
    return ok({
        "message": "MFA verification required",
        "mfa_required": True,
        "mfa_token": challenge.get("challenge_token") if challenge else None,
        "masked_destination": (challenge or {}).get("masked_phone") or (challenge or {}).get("masked_email"),
        "challenge": challenge,
        "user": serialize_user(user),
    })


@router.post("/login")
def login(req: AuthRequest, request: Request, response: Response):
    return _login_with_mfa(req, request, account_type="admin_recruiter")


@router.post("/applicant/login")
def applicant_login(req: AuthRequest, request: Request, response: Response):
    return _login_with_mfa(req, request, account_type="applicant")


@router.post("/mfa/verify")
def mfa_verify(req: MfaVerifyRequest, request: Request, response: Response):
    payload = verify_mfa_challenge(
        req.challenge_token,
        req.otp,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA code")
    _set_auth_cookies(response, payload)
    log_auth_event(
        "login_success",
        email=payload.get("user", {}).get("email"),
        organization_id=payload.get("user", {}).get("organization_id"),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return ok({"message": "Login successful", **payload})


@router.post("/mfa/resend")
def mfa_resend(req: MfaResendRequest, request: Request):
    payload = resend_mfa_challenge(
        req.challenge_token,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA challenge")
    if payload.get("cooldown_seconds"):
        raise HTTPException(status_code=429, detail={"message": "Please wait before requesting another code.", **payload})
    return ok(payload)


@router.get("/testing/otp/{challenge_token}")
def testing_mfa_otp(challenge_token: str):
    if not mfa_test_mode_enabled():
        raise HTTPException(status_code=404, detail="Test MFA OTP endpoint is disabled")
    payload = get_test_mfa_otp(challenge_token)
    if not payload:
        raise HTTPException(status_code=404, detail="MFA test OTP not found or expired")
    return ok({
        "otp": payload["otp"],
        "expires_at": payload["expires_at"],
    })


@router.post("/mfa/setup")
def mfa_setup(req: MfaSetupRequest, context: dict = Depends(get_current_user_context)):
    result = set_user_mfa(context["user"].id, req.enabled)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return ok({"message": "MFA enabled" if req.enabled else "MFA disabled", "user": result})


@router.post("/mfa/disable")
def mfa_disable(context: dict = Depends(get_current_user_context)):
    result = set_user_mfa(context["user"].id, False)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return ok({"message": "MFA disabled", "user": result})


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
    issued = issue_account_verification(email=email, channel="email")
    if issued:
        user = get_user_by_email(email)
        if user:
            create_notification(
                user_id=user.id,
                organization_id=user.organization_id,
                kind="email_verification",
                subject="Verify your email",
                message="Use the verification token to confirm your account.",
                metadata={"expires_at": issued["expires_at"]},
            )
    return ok({
        "message": "If the account exists, a verification token has been issued.",
        "verification_token": issued["token"] if issued else None,
    })


@router.post("/send-verification-code")
def send_verification_code(req: VerificationCodeRequest):
    email = _validate_email(req.email) if req.email else None
    phone = _validate_phone(req.phone) if req.phone else None
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Email or phone is required")
    issued = issue_account_verification(email=email, phone=phone, channel=req.channel)
    if not issued:
        raise HTTPException(status_code=404, detail="Email not found" if email else "Phone not found")
    return ok({
        "message": f"A verification code has been sent to your {'phone' if issued['channel'] == 'sms' else 'email'}.",
        "verification_token": issued["token"] if mfa_test_mode_enabled() else None,
        "expires_at": issued["expires_at"],
        "channel": issued["channel"],
        "masked_destination": issued["masked_destination"],
    })


@router.post("/resend-verification-code")
def resend_verification_code(req: VerificationCodeRequest):
    email = _validate_email(req.email) if req.email else None
    phone = _validate_phone(req.phone) if req.phone else None
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Email or phone is required")
    issued = issue_account_verification(email=email, phone=phone, channel=req.channel, enforce_cooldown=True)
    if not issued:
        raise HTTPException(status_code=404, detail="Email not found" if email else "Phone not found")
    if issued.get("cooldown_seconds"):
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Please wait before requesting another verification code.",
                "code": "verification_resend_cooldown",
                "cooldown_seconds": issued["cooldown_seconds"],
            },
        )
    return ok({
        "message": f"A verification code has been sent to your {'phone' if issued['channel'] == 'sms' else 'email'}.",
        "verification_token": issued["token"] if mfa_test_mode_enabled() else None,
        "expires_at": issued["expires_at"],
        "channel": issued["channel"],
        "masked_destination": issued["masked_destination"],
    })


@router.post("/verify-email")
def verify_email(req: EmailVerificationRequest, request: Request):
    email = _validate_email(req.email)
    result = verify_email_code(email, req.code or req.verification_code or "")
    if not result.get("verified"):
        reason = result.get("reason")
        message_by_reason = {
            "missing_code": "Missing verification code",
            "email_not_found": "Email not found",
            "expired": "Verification expired",
            "invalid_code": "Invalid verification code",
            "too_many_attempts": "Too many attempts. Please request a new code.",
        }
        log_auth_event(
            "email_verification",
            email=email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"verification": "failure", "reason": reason},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": message_by_reason.get(reason, "Invalid verification code"),
                "code": reason or "invalid_code",
            },
        )
    log_auth_event(
        "email_verification",
        email=email,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"verification": "success"},
    )
    return ok({"message": "Email verified successfully", "user": result.get("user")})


@router.post("/verify-phone")
def verify_phone(req: PhoneVerificationRequest, request: Request):
    phone = _validate_phone(req.phone)
    result = verify_phone_code(phone, req.code)
    if not result.get("verified"):
        reason = result.get("reason")
        message_by_reason = {
            "missing_code": "Missing verification code",
            "phone_not_found": "Phone not found",
            "expired": "Verification expired",
            "invalid_code": "Invalid verification code",
            "too_many_attempts": "Too many attempts. Please request a new code.",
        }
        log_auth_event(
            "phone_verification",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"verification": "failure", "reason": reason, "phone": mask_phone(phone)},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": message_by_reason.get(reason, "Invalid verification code"),
                "code": reason or "invalid_code",
            },
        )
    log_auth_event(
        "phone_verification",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"verification": "success", "phone": mask_phone(phone)},
    )
    return ok({"message": "Phone verified successfully", "user": result.get("user")})


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
