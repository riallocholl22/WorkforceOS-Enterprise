from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api.responses import error


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
        return JSONResponse(
            status_code=422,
            content=error("Validation failed", "validation_error"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=error("Internal server error", "internal_server_error"),
        )
