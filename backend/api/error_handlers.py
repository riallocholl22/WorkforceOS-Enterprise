from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import json
import logging

from backend.api.responses import error

logger = logging.getLogger("ai_recruitment.validation")


def _redact_payload(value):
    sensitive = {"password", "new_password", "token", "refresh_token", "access_token", "otp", "verification_code", "challenge_token"}
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in sensitive else _redact_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


async def _request_payload_for_log(request: Request):
    raw = await request.body()
    if not raw:
        return {}
    try:
        return _redact_payload(json.loads(raw.decode("utf-8")))
    except Exception:
        return {"raw": raw[:500].decode("utf-8", errors="replace")}


def _missing_validation_fields(errors):
    missing = []
    for item in errors:
        if item.get("type") == "missing":
            loc = item.get("loc") or []
            missing.append(".".join(str(part) for part in loc if part != "body"))
    return missing


def register_error_handlers(app: FastAPI):
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            message = str(exc.detail.get("message") or "Request failed")
            code = exc.detail.get("code")
            meta = {k: v for k, v in exc.detail.items() if k not in {"message", "code"}}
            payload = error(message, str(code) if code else None)
            if meta:
                payload["error"]["meta"] = meta
            return JSONResponse(
                status_code=exc.status_code,
                content=payload,
                headers=exc.headers,
            )

        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error(detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        payload = await _request_payload_for_log(request)
        logger.warning(
            "request_validation_failed path=%s method=%s payload=%s failures=%s missing_fields=%s",
            request.url.path,
            request.method,
            payload,
            errors,
            _missing_validation_fields(errors),
        )
        response = error("Validation failed", "validation_error")
        response["error"]["meta"] = {
            "failures": errors,
            "missing_fields": _missing_validation_fields(errors),
        }
        return JSONResponse(
            status_code=422,
            content=response,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=error("Internal server error", "internal_server_error"),
        )
