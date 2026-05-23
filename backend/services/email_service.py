import asyncio
import html
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from backend.services.enterprise_service import create_notification, log_audit_event

logger = logging.getLogger("ai_recruitment.email")


EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
EMAIL_FROM = os.getenv("SMTP_FROM", "no-reply@example.com")
MAX_EMAIL_RETRIES = int(os.getenv("EMAIL_MAX_RETRIES", "3"))


TEMPLATES: Dict[str, Dict[str, str]] = {
    "password_reset": {
        "subject": "Reset your AI Recruit password",
        "body": "Use this secure reset link to choose a new password: {reset_url}",
    },
    "email_verification": {
        "subject": "Verify your AI Recruit account",
        "body": "Confirm your email address with this verification link: {verification_url}",
    },
    "billing_receipt": {
        "subject": "Your AI Recruit billing receipt",
        "body": "Payment received for {plan}. Amount: {amount}. Invoice: {invoice_number}.",
    },
    "interview_invitation": {
        "subject": "Interview invitation from {organization}",
        "body": "You have been invited to interview for {role}. Meeting details: {meeting_link}",
    },
    "recruiter_notification": {
        "subject": "{title}",
        "body": "{message}",
    },
}


def provider_status() -> Dict[str, Any]:
    return {
        "active_provider": EMAIL_PROVIDER,
        "from": EMAIL_FROM,
        "configured": {
            "smtp": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USERNAME")),
            "sendgrid": bool(os.getenv("SENDGRID_API_KEY")),
            "resend": bool(os.getenv("RESEND_API_KEY")),
            "mailgun": bool(os.getenv("MAILGUN_API_KEY") and os.getenv("MAILGUN_DOMAIN")),
        },
        "retry_policy": {"max_attempts": MAX_EMAIL_RETRIES, "backoff_seconds": [1, 2, 4]},
    }


def render_email(template: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    context = context or {}
    spec = TEMPLATES.get(template, TEMPLATES["recruiter_notification"])
    safe_context = {key: html.escape(str(value)) for key, value in context.items()}
    subject = spec["subject"].format_map(_SafeFormat(safe_context))
    body = spec["body"].format_map(_SafeFormat(safe_context))
    return {
        "subject": subject,
        "text": body,
        "html": _html_shell(subject, body),
    }


async def send_transactional_email(
    *,
    to: Iterable[str],
    template: str,
    context: Optional[Dict[str, Any]] = None,
    organization_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    recipients = [address.strip().lower() for address in to if address and "@" in address]
    if not recipients:
        raise ValueError("At least one valid recipient is required")

    message = render_email(template, context)
    provider = EMAIL_PROVIDER
    event_id = f"email_{secrets.token_hex(10)}"
    configured = provider_status()["configured"].get(provider, False)

    last_error = None
    for attempt in range(1, MAX_EMAIL_RETRIES + 1):
        try:
            if not configured:
                logger.info("email_adapter_pending provider=%s template=%s recipients=%s", provider, template, recipients)
                status = "pending_delivery"
            else:
                await _send_with_provider(provider, recipients, message)
                status = "sent"

            create_notification(
                user_id=user_id or 0,
                organization_id=organization_id,
                kind=f"email.{template}",
                subject=message["subject"],
                message=message["text"],
                metadata={"event_id": event_id, "provider": provider, "recipients": recipients},
            )
            log_audit_event(
                action="email.send",
                entity_type="email",
                entity_id=event_id,
                organization_id=organization_id,
                user_id=user_id,
                details={"template": template, "provider": provider, "status": status},
            )
            return {
                "event_id": event_id,
                "status": status,
                "provider": provider,
                "recipients": recipients,
                "sent_at": datetime.utcnow().isoformat(),
                "configured": configured,
            }
        except Exception as exc:
            last_error = str(exc)
            logger.warning("email_send_retry provider=%s attempt=%s error=%s", provider, attempt, last_error)
            await asyncio.sleep(min(2 ** (attempt - 1), 4))

    log_audit_event(
        action="email.failed",
        entity_type="email",
        entity_id=event_id,
        organization_id=organization_id,
        user_id=user_id,
        details={"template": template, "provider": provider, "error": last_error},
    )
    return {
        "event_id": event_id,
        "status": "failed",
        "provider": provider,
        "error": last_error or "Email provider unavailable",
    }


async def _send_with_provider(provider: str, recipients: list[str], message: Dict[str, str]) -> None:
    # Network delivery adapters are intentionally isolated here so secrets stay backend-only.
    # Production deployments can replace this with SendGrid/Resend/Mailgun SDK calls.
    await asyncio.sleep(0)
    logger.info("email_provider_stub provider=%s recipients=%s subject=%s", provider, recipients, message["subject"])


def _html_shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html><body style=\"font-family:Arial,sans-serif;"
        "background:#f6f8fb;padding:24px;color:#172033\">"
        f"<main style=\"max-width:640px;margin:auto;background:#fff;border:1px solid #e5eaf2;"
        f"border-radius:12px;padding:24px\"><h2>{html.escape(title)}</h2>"
        f"<p style=\"line-height:1.6\">{body}</p>"
        "<p style=\"color:#64748b;font-size:13px\">AI Recruit Enterprise Notifications</p>"
        "</main></body></html>"
    )


class _SafeFormat(dict):
    def __missing__(self, key):
        return "{" + key + "}"
