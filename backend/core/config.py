import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


# =========================================================
# ENVIRONMENT
# =========================================================

ENVIRONMENT = os.getenv(
    "ENVIRONMENT",
    os.getenv("APP_ENV", "development")
).lower()

DEBUG = os.getenv(
    "DEBUG",
    "false"
).lower() in {"1", "true", "yes", "on"}

PRODUCTION = ENVIRONMENT in {"production", "prod"}


# =========================================================
# DEFAULT ORIGINS
# =========================================================

DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


DEFAULT_TRUSTED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "testserver",
]


# =========================================================
# HELPERS
# =========================================================

def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.lower() in {
        "1",
        "true",
        "yes",
        "on"
    }


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)

    if not raw:
        return default

    items = [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]

    return list(dict.fromkeys(items))


# =========================================================
# APP CONFIG
# =========================================================

@lru_cache
def get_environment() -> str:
    return ENVIRONMENT


@lru_cache
def is_debug() -> bool:
    return DEBUG


@lru_cache
def is_production() -> bool:
    return PRODUCTION


# =========================================================
# CORS
# =========================================================

@lru_cache
def get_cors_origins() -> list[str]:
    origins = _csv_env(
        "CORS_ORIGINS",
        DEFAULT_CORS_ORIGINS if not PRODUCTION else []
    )

    return list(dict.fromkeys(origins))


# =========================================================
# TRUSTED HOSTS
# =========================================================

@lru_cache
def get_trusted_hosts() -> list[str]:
    hosts = _csv_env(
        "TRUSTED_HOSTS",
        DEFAULT_TRUSTED_HOSTS
    )

    return hosts or DEFAULT_TRUSTED_HOSTS


# =========================================================
# DOCS PATHS
# =========================================================

@lru_cache
def get_docs_paths() -> set[str]:
    raw = os.getenv(
        "DOCS_CSP_PATHS",
        "/docs,/redoc,/openapi.json"
    )

    return {
        path.strip()
        for path in raw.split(",")
        if path.strip()
    }


def is_docs_path(path: str) -> bool:
    docs_paths = get_docs_paths()

    return (
        path in docs_paths
        or path.startswith("/docs/")
        or path.startswith("/redoc/")
    )


# =========================================================
# CSP POLICY GENERATOR
# =========================================================

def _build_csp(directives: dict[str, str]) -> str:
    return "; ".join(
        [
            f"{key} {value}"
            for key, value in directives.items()
        ]
    )


# =========================================================
# DOCS CSP
# =========================================================

def _docs_csp() -> str:
    directives = {
        "default-src": "'self'",

        "script-src": (
            "'self' "
            "'unsafe-inline' "
            "'unsafe-eval' "
            "https://cdn.jsdelivr.net"
        ),

        "style-src": (
            "'self' "
            "'unsafe-inline' "
            "https://cdn.jsdelivr.net"
        ),

        "img-src": (
            "'self' "
            "data: "
            "https://fastapi.tiangolo.com "
            "https://cdn.jsdelivr.net"
        ),

        "font-src": (
            "'self' "
            "data: "
            "https://cdn.jsdelivr.net"
        ),

        "connect-src": (
            "'self' "
            "http://127.0.0.1:8000 "
            "http://localhost:8000 "
            "https://cdn.jsdelivr.net"
        ),

        "object-src": "'none'",

        "base-uri": "'self'",

        "frame-ancestors": "'none'",

        "form-action": "'self'",
    }

    return _build_csp(directives)


# =========================================================
# APPLICATION CSP
# =========================================================

def _application_csp() -> str:

    script_sources = [
        "'self'"
    ]

    style_sources = [
        "'self'"
    ]

    image_sources = [
        "'self'",
        "data:"
    ]

    connect_sources = [
        "'self'",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ]

    # -----------------------------------------------------
    # Development Relaxations
    # -----------------------------------------------------

    if not PRODUCTION:

        if _bool_env(
            "CSP_ALLOW_DEV_INLINE_SCRIPTS",
            True
        ):
            script_sources.append("'unsafe-inline'")

        if _bool_env(
            "CSP_ALLOW_DEV_INLINE_STYLES",
            True
        ):
            style_sources.append("'unsafe-inline'")

    directives = {
        "default-src": "'self'",

        "script-src": " ".join(script_sources),

        "style-src": " ".join(style_sources),

        "img-src": " ".join(image_sources),

        "font-src": "'self' data:",

        "connect-src": " ".join(connect_sources),

        "object-src": "'none'",

        "base-uri": "'self'",

        "frame-ancestors": "'none'",

        "form-action": "'self'",
    }

    return _build_csp(directives)


# =========================================================
# PUBLIC API
# =========================================================

def get_csp_policy(path: str = "/") -> str:
    if is_docs_path(path):
        return _docs_csp()

    return _application_csp()


# =========================================================
# SECURITY CONFIG
# =========================================================

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(self), "
        "microphone=(self), "
        "geolocation=(self)"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "X-XSS-Protection": "0",
    "X-Permitted-Cross-Domain-Policies": "none",
}


# =========================================================
# JWT CONFIG
# =========================================================

JWT_SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY",
    "CHANGE_THIS_IN_PRODUCTION"
)

JWT_ALGORITHM = os.getenv(
    "JWT_ALGORITHM",
    "HS256"
)

ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "60"
    )
)

REFRESH_TOKEN_EXPIRE_DAYS = int(
    os.getenv(
        "REFRESH_TOKEN_EXPIRE_DAYS",
        "7"
    )
)


# =========================================================
# DATABASE CONFIG
# =========================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./ai_recruitment.db"
)

MONGODB_URL = os.getenv(
    "MONGODB_URL",
    ""
)

MONGODB_DATABASE = os.getenv(
    "MONGODB_DATABASE",
    "ai_recruitment"
)


# =========================================================
# API CONFIG
# =========================================================

API_NAME = os.getenv(
    "API_NAME",
    "AI Recruitment Enterprise API"
)

API_VERSION = os.getenv(
    "API_VERSION",
    "1.0.0"
)

API_PREFIX = os.getenv(
    "API_PREFIX",
    "/api/v1"
)


# =========================================================
# LOGGING CONFIG
# =========================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
).upper()

ENABLE_REQUEST_LOGGING = _bool_env(
    "ENABLE_REQUEST_LOGGING",
    True
)

ENABLE_SECURITY_LOGGING = _bool_env(
    "ENABLE_SECURITY_LOGGING",
    True
)