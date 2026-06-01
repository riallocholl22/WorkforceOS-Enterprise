import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.config import get_csp_policy, get_environment, is_docs_path
from backend.services.auth_service import decode_token_payload


logger = logging.getLogger("ai_recruitment.security")


class AuthenticationContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.auth = None
        authorization = request.headers.get("Authorization", "")
        token_source = None

        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            token_source = "bearer"
            request.state.auth = decode_token_payload(token)
        elif request.cookies.get("access_token"):
            token_source = "cookie"
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                csrf_cookie = request.cookies.get("csrf_token")
                csrf_header = request.headers.get("X-CSRF-Token")
                if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                    return JSONResponse(
                        {"success": False, "error": {"message": "CSRF validation failed"}},
                        status_code=403,
                    )
            request.state.auth = decode_token_payload(request.cookies["access_token"])

        if token_source and request.state.auth is None:
            logger.warning(
                "auth_context_invalid_token source=%s path=%s",
                token_source,
                request.url.path,
            )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        environment = get_environment()
        csp_policy = get_csp_policy(path)
        csp_mode = "docs" if is_docs_path(path) else "application"

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self)")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers["Content-Security-Policy"] = csp_policy
        response.headers.setdefault("X-CSP-Mode", csp_mode)
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if is_docs_path(path):
            response.headers.setdefault("Cache-Control", "no-store")
            logger.info(
                "csp_applied mode=%s environment=%s path=%s policy=%s",
                csp_mode,
                environment,
                path,
                csp_policy,
            )
        else:
            logger.debug(
                "csp_applied mode=%s environment=%s path=%s",
                csp_mode,
                environment,
                path,
            )
        return response
