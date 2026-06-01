import logging
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.services.auth_service import get_user_context_from_token, verify_access_token


bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger("ai_recruitment.rbac")


def _raw_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials

    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def get_current_user(
    token: str = Depends(_raw_bearer_token),
) -> str:
    user_email = verify_access_token(token)

    if not user_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_email


def get_current_user_context(
    request: Request,
    token: str = Depends(_raw_bearer_token),
) -> dict:
    context = get_user_context_from_token(token)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = str(context.get("role") or "").lower()
    if role == "applicant":
        path = getattr(request.url, "path", "")
        applicant_allowed_prefixes = (
            "/auth/me",
            "/auth/logout",
            "/auth/mfa/",
            "/applicant",
            "/interview",
            "/resume",
            "/parse-resume",
            "/upload-resume",
        )
        if not path.startswith(applicant_allowed_prefixes):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Applicant accounts can access applicant and interview workflows only.",
                    "code": "applicant_scope_restricted",
                },
            )

    return context


def require_roles(*roles: str):
    # Backward-compatible role aliases (older tokens / configs may say "admin").
    role_alias = {
        "admin": "company_admin",
    }
    allowed = {role_alias.get(role.lower(), role.lower()) for role in roles}

    def _dependency(request: Request, context: dict = Depends(get_current_user_context)) -> dict:
        role_raw = (context.get("role") or "").lower()
        role = role_alias.get(role_raw, role_raw)
        if allowed and role not in allowed:
            user = context.get("user")
            user_id = context.get("user_id") or getattr(user, "id", None)
            email = context.get("email") or getattr(user, "username", None)
            org_id = context.get("organization_id")
            tenant_id = context.get("tenant_id") or context.get("workspace_id") or org_id
            allowed_roles = sorted(allowed)
            logger.warning(
                "rbac_denied path=%s user_id=%s email=%s role=%s org_id=%s tenant_id=%s allowed=%s",
                getattr(request.url, "path", ""),
                user_id,
                email,
                role,
                org_id,
                tenant_id,
                ",".join(allowed_roles),
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Insufficient permissions",
                    "code": "insufficient_permissions",
                    "reason": "role_not_allowed",
                    "guidance": "Ask a company admin to confirm your workspace role, then refresh your session.",
                    "allowed_roles": allowed_roles,
                },
            )

        # Useful for troubleshooting without drowning production logs.
        logger.debug(
            "rbac_allowed path=%s user_id=%s role=%s org_id=%s",
            getattr(request.url, "path", ""),
            context.get("user_id") or getattr(context.get("user"), "id", None),
            role,
            context.get("organization_id"),
        )
        return context

    return _dependency
