import importlib
import logging
import os
import secrets
import time
import traceback
from contextlib import asynccontextmanager
from typing import Iterable

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.error_handlers import register_error_handlers
from backend.api.responses import error, ok
from backend.api.routes import (
    ai,
    auth,
    brain,
    candidates,
    chat,
    compat,
    dashboard,
    enterprise,
    face,
    interview,
    job,
    ops,
    pipeline,
    platform,
    ranking,
    report,
    resume,
    shortlist,
    voice,
)
from backend.core.config import get_cors_origins, get_environment, get_trusted_hosts
from backend.core.security import AuthenticationContextMiddleware, SecurityHeadersMiddleware
from backend.db.database import SessionLocal, init_db
from backend.services.dev_bootstrap_service import run_development_bootstrap
from backend.services.startup_validation_service import validate_environment

load_dotenv()


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


logger = logging.getLogger("ai_recruitment")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started_at = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or secrets.token_hex(8)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request_failed request_id=%s method=%s path=%s duration_ms=%.2f",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Request-Duration-ms"] = f"{duration_ms:.2f}"
        logger.info(
            "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


class RateLimitPolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Policy", "tenant-aware-application-controls")
        return response


def _check_optional_dependency(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        logger.info("dependency_available module=%s", module_name)
        return True
    except Exception:
        logger.warning("dependency_missing module=%s", module_name)
        return False


def validate_runtime_dependencies() -> None:
    required = ["fastapi", "sqlalchemy", "dotenv", "passlib", "argon2"]
    optional = ["motor", "pymongo", "numpy", "sklearn", "matplotlib", "reportlab", "cv2", "websockets", "wsproto"]
    for module_name in required:
        _check_optional_dependency(module_name)
    for module_name in optional:
        _check_optional_dependency(module_name)


def websocket_runtime_status() -> dict:
    websocket_impl = None
    for module_name in ("websockets", "wsproto"):
        try:
            importlib.import_module(module_name)
            websocket_impl = module_name
            break
        except Exception:
            continue

    return {
        "status": "ok" if websocket_impl else "degraded",
        "provider": websocket_impl,
        "guidance": None if websocket_impl else "Install uvicorn[standard], websockets, or wsproto to enable live enterprise streams.",
    }


def validate_database() -> None:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    logger.info("database_connection_ok")


async def validate_mongodb() -> None:
    mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URI")
    if not mongo_url:
        logger.info("mongodb_not_configured")
        return

    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
        await client.admin.command("ping")
        client.close()
        logger.info("mongodb_connection_ok")
    except Exception:
        logger.exception("mongodb_connection_failed")


def log_registered_routes(app: FastAPI) -> None:
    logger.info("registered_routes_begin")
    for route in app.routes:
        methods = ",".join(sorted(getattr(route, "methods", []) or []))
        logger.info("route path=%s methods=%s name=%s", route.path, methods, route.name)
    logger.info("registered_routes_end")


def log_middleware_stack(app: FastAPI) -> None:
    logger.info("middleware_stack_begin")
    for index, middleware in enumerate(app.user_middleware, start=1):
        logger.info(
            "middleware order=%s class=%s options=%s",
            index,
            getattr(middleware.cls, "__name__", str(middleware.cls)),
            {
                "args": getattr(middleware, "args", ()),
                "kwargs": getattr(middleware, "kwargs", {}),
                "options": getattr(middleware, "options", {}),
            },
        )
    logger.info("middleware_stack_end")


def validate_middleware_stack(app: FastAPI) -> None:
    middleware_names = [
        getattr(middleware.cls, "__name__", str(middleware.cls))
        for middleware in app.user_middleware
    ]
    required = {
        "CORSMiddleware",
        "TrustedHostMiddleware",
        "GZipMiddleware",
        "SecurityHeadersMiddleware",
        "AuthenticationContextMiddleware",
        "RequestLoggingMiddleware",
        "RateLimitPolicyMiddleware",
    }
    missing = sorted(required - set(middleware_names))
    if missing:
        logger.warning("middleware_validation_missing missing=%s", ",".join(missing))

    if "CORSMiddleware" in middleware_names and middleware_names[0] != "CORSMiddleware":
        logger.warning(
            "middleware_validation_order cors_position=%s expected=outermost",
            middleware_names.index("CORSMiddleware") + 1,
        )
    else:
        logger.info("middleware_validation_ok middleware=%s", ",".join(middleware_names))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "startup_begin service=ai-recruitment-api environment=%s debug=%s",
        get_environment(),
        os.getenv("DEBUG", "false"),
    )
    env_validation = validate_environment()
    app.state.environment_validation = env_validation
    validate_runtime_dependencies()
    init_db()
    validate_database()
    bootstrap_result = run_development_bootstrap()
    logger.info("development_bootstrap status=%s", bootstrap_result)
    await validate_mongodb()
    validate_middleware_stack(app)
    log_middleware_stack(app)
    log_registered_routes(app)
    logger.info("startup_complete")
    try:
        yield
    finally:
        logger.info("shutdown_begin")
        logger.info("shutdown_complete")


app = FastAPI(
    title="WorkforceOS Enterprise API",
    description="Production-ready workforce intelligence, ranking, enterprise operations, scheduling, analytics, and security API.",
    version=os.getenv("API_VERSION", "1.0.0"),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

register_error_handlers(app)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception path=%s error=%s stack=%s",
        request.url.path,
        str(exc),
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content=error("Internal server error", "internal_server_error"),
    )


app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitPolicyMiddleware)
app.add_middleware(AuthenticationContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=get_trusted_hosts(),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Request-Duration-ms", "X-RateLimit-Policy"],
)


def include_routers(app: FastAPI, routers: Iterable) -> None:
    for router in routers:
        app.include_router(router)


include_routers(
    app,
    [
        auth.router,
        resume.router,
        pipeline.router,
        shortlist.router,
        chat.router,
        ai.router,
        brain.router,
        job.router,
        platform.router,
        report.router,
        ranking.router,
        compat.router,
        dashboard.router,
        candidates.router,
        interview.router,
        face.router,
        voice.router,
        ops.router,
    ],
)

app.include_router(enterprise.router, prefix="/enterprise")


@app.get("/", tags=["System"])
async def root():
    return ok(
        {
            "message": "WorkforceOS Enterprise API running",
            "status": "ok",
            "version": app.version,
            "environment": get_environment(),
        }
    )


@app.get("/health", tags=["System"])
async def health():
    database = "ok"
    try:
        validate_database()
    except Exception:
        logger.exception("health_database_check_failed")
        database = "error"

    service_status = "healthy" if database == "ok" else "degraded"
    return {
        "status": service_status,
        "service": "ai-recruitment-api",
        "version": app.version,
        "environment": get_environment(),
        "checks": {
            "database": database,
            "realtime": websocket_runtime_status(),
            "environment": getattr(app.state, "environment_validation", {"production_ready": True}),
        },
    }


@app.get("/api/health", tags=["System"])
async def api_health():
    return await health()


@app.get("/api/v1/health", tags=["System"])
async def versioned_health():
    return await health()
