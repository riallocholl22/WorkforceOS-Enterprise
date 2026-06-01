import logging
import os
from typing import Any, Dict, List

from backend.core.config import get_environment, is_production

logger = logging.getLogger("ai_recruitment.startup_validation")


def validate_environment() -> Dict[str, Any]:
    required = ["DATABASE_URL", "JWT_SECRET"]
    production_required = [
        "CORS_ORIGINS",
        "TRUSTED_HOSTS",
        "FRONTEND_URL",
        "OPENAI_API_KEY",
    ]
    provider_groups = {
        "mpesa": ["MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE", "MPESA_PASSKEY", "MPESA_CALLBACK_URL"],
        "paypal": ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"],
        "email": ["SMTP_FROM"],
    }

    missing: List[str] = []
    warnings: List[str] = []

    if is_production():
        missing.extend(name for name in required if not os.getenv(name))
        missing.extend(name for name in production_required if not os.getenv(name))
        if os.getenv("DEV_BOOTSTRAP_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
            missing.append("DEV_BOOTSTRAP_ENABLED must be false in production")
        if os.getenv("E2E_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}:
            missing.append("E2E_TEST_MODE must be false in production")
        if os.getenv("AUTH_EXPOSE_TEST_OTPS", "").lower() in {"1", "true", "yes", "on"}:
            missing.append("AUTH_EXPOSE_TEST_OTPS must be false in production")
    else:
        warnings.extend(name for name in required if not os.getenv(name))
        warnings.extend(name for name in production_required if not os.getenv(name))

    providers = {
        name: all(os.getenv(variable) for variable in variables)
        for name, variables in provider_groups.items()
    }

    if missing:
        logger.error("environment_validation_failed environment=%s missing=%s", get_environment(), ",".join(missing))
        if is_production():
            raise RuntimeError(f"Missing required production configuration: {', '.join(missing)}")
    if warnings:
        logger.warning("environment_validation_warnings environment=%s missing_optional=%s", get_environment(), ",".join(warnings))

    logger.info("environment_validation_complete providers=%s", providers)
    return {
        "environment": get_environment(),
        "missing_required": missing,
        "missing_optional": warnings,
        "providers": providers,
        "production_ready": not missing,
    }
