from datetime import datetime
from enum import Enum
import logging
import traceback
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from backend.api.dependencies import (
    get_current_user_context,
    require_roles,
)

from backend.api.responses import ok

from backend.services.assessment_service import (
    create_assessment,
    submit_assessment,
)

from backend.services.billing_service import (
    cancel_subscription,
    create_checkout_session,
    generate_invoice,
    handle_provider_webhook,
    initiate_mpesa_stk_push,
    record_usage_event,
    subscription_overview,
    update_plan,
    verify_payment,
)

from backend.services.candidate_intelligence_service import (
    candidate_graph,
    skill_demand,
)

from backend.services.compliance_service import (
    detect_bias,
    record_consent,
    revoke_consent,
)

from backend.services.decision_service import make_decision

from backend.services.enterprise_service import (
    ensure_subscription,
    get_workspace_overview,
    increment_usage,
    list_audit_logs,
    list_notifications,
    log_audit_event,
)

from backend.services.workflow_service import (
    evaluate_workflow,
    notify_workflow_action,
    persist_workflow_run,
)

from backend.services.scheduling_service import (
    list_schedules,
    schedule_interview,
)

from backend.services.security_ai import (
    analyze_event,
    monitor_event,
    security_overview,
)
from backend.services.security_ai.actions_service import (
    execute_security_action,
    list_incident_actions,
)
from backend.services.security_ai.automation_rules_service import (
    create_security_rule,
    delete_security_rule,
    list_security_rules,
    update_security_rule,
)
from backend.services.security_ai.control_center import security_controls_snapshot
from backend.services.security_ai.incident_service import (
    get_incident,
    list_incidents,
    update_incident_status,
)

from backend.services.team_service import (
    accept_invitation,
    invite_member,
    list_team,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Enterprise"]
)


# =========================================================
# ENUMS
# =========================================================

class PlanEnum(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TeamRoleEnum(str, Enum):
    RECRUITER = "recruiter"
    INTERVIEWER = "interviewer"
    HIRING_MANAGER = "hiring_manager"
    CANDIDATE = "candidate"
    COMPANY_ADMIN = "company_admin"
    SUPER_ADMIN = "super_admin"


class ConsentStatusEnum(str, Enum):
    GRANTED = "granted"
    REVOKED = "revoked"


# =========================================================
# HELPERS
# =========================================================

def require_organization(context: dict) -> str:
    org_id = context.get("organization_id")

    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization"
        )

    return str(org_id)


def safe_execute(operation_name: str, fn):
    try:
        return fn()

    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            f"[ENTERPRISE ERROR] {operation_name}: {str(e)}"
        )

        logger.error(traceback.format_exc())

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{operation_name} failed"
        )


def audit(
    action: str,
    entity_type: str,
    entity_id: str,
    org_id: str,
    user_id: str,
    metadata: dict | None = None
):
    try:
        log_audit_event(
            action,
            entity_type,
            entity_id,
            org_id,
            user_id,
            metadata or {},
        )
    except Exception as e:
        logger.warning(f"Audit log failed: {str(e)}")


# =========================================================
# REQUEST MODELS
# =========================================================

class WorkflowRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    match_score: float = Field(..., ge=0, le=100)
    job_id: str | None = Field(default=None, max_length=120)


class DecisionRequest(BaseModel):
    match_score: float = Field(default=0, ge=0, le=100)
    interview_score: float = Field(default=0, ge=0, le=100)
    communication_score: float = Field(default=0, ge=0, le=100)
    proctor_score: float = Field(default=100, ge=0, le=100)
    missing_skills: list[str] = Field(default_factory=list)


class PlanRequest(BaseModel):
    plan: PlanEnum
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|yearly)$")
    currency: str = Field(default="KES", min_length=3, max_length=3)


class CheckoutRequest(BaseModel):
    provider: str = Field(..., pattern="^(paypal|flutterwave|paystack|paddle)$")
    plan: PlanEnum
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|yearly)$")
    currency: str = Field(default="KES", min_length=3, max_length=3)
    idempotency_key: str | None = Field(default=None, max_length=160)


class MpesaStkRequest(BaseModel):
    phone: str = Field(..., min_length=9, max_length=20)
    plan: PlanEnum
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|yearly)$")
    currency: str = Field(default="KES", min_length=3, max_length=3)
    destination_phone: str | None = Field(default=None, max_length=20)
    idempotency_key: str | None = Field(default=None, max_length=160)


class VerifyPaymentRequest(BaseModel):
    reference: str = Field(..., min_length=6, max_length=120)


class InviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    role: TeamRoleEnum = TeamRoleEnum.RECRUITER

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        cleaned = value.strip().lower()
        if "@" not in cleaned or "." not in cleaned.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email address")
        return cleaned


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=24, max_length=4000)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("Password too short")

        return value


class SecurityEventRequest(BaseModel):
    event_type: str = Field(..., max_length=80)
    source_ip: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)


class SecurityIncidentActionRequest(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = Field(default=False)


class SecurityIncidentNoteRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)


class SecurityRuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    enabled: bool = Field(default=True)
    trigger: str = Field(default="security.event", max_length=120)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: dict[str, Any] = Field(default_factory=dict)


class SecurityRulePatchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    enabled: bool | None = Field(default=None)
    trigger: str | None = Field(default=None, max_length=120)
    conditions: dict[str, Any] | None = Field(default=None)
    actions: dict[str, Any] | None = Field(default=None)


class BiasRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


class ConsentRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    consent_type: str = Field(..., min_length=1, max_length=80)
    status: ConsentStatusEnum = ConsentStatusEnum.GRANTED
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssessmentCreateRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    title: str = Field(default="Skill Assessment", max_length=160)
    assessment_type: str = Field(default="mixed", max_length=40)


class AssessmentSubmitRequest(BaseModel):
    submissions: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    interviewer_email: str = Field(..., min_length=3, max_length=320)
    starts_at: datetime
    timezone: str = Field(default="UTC", max_length=80)
    provider: str = Field(default="manual", max_length=40)

    @field_validator("interviewer_email")
    @classmethod
    def validate_interviewer_email(cls, value):
        cleaned = value.strip().lower()
        if "@" not in cleaned or "." not in cleaned.rsplit("@", 1)[-1]:
            raise ValueError("Invalid interviewer email address")
        return cleaned


# =========================================================
# WORKSPACE
# =========================================================

@router.get("/workspace")
async def workspace(
    context: dict = Depends(get_current_user_context)
):
    org_id = context.get("organization_id")

    if not org_id:
        return ok({
            "workspace": None,
            "subscription": {
                "plan": "free",
                "status": "inactive",
            }
        })

    workspace_data = safe_execute(
        "workspace_overview",
        lambda: get_workspace_overview(org_id)
    )

    subscription = safe_execute(
        "subscription_fetch",
        lambda: ensure_subscription(org_id)
    )

    return ok({
        "workspace": workspace_data,
        "subscription": subscription,
    })


# =========================================================
# TEAM
# =========================================================

@router.get("/team")
async def team(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "recruiter"
        )
    )
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "list_team",
            lambda: list_team(org_id)
        )
    )


@router.post("/team/invite")
async def team_invite(
    req: InviteRequest,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin"
        )
    )
):
    org_id = require_organization(context)

    payload = safe_execute(
        "invite_member",
        lambda: invite_member(
            org_id,
            context["user"].id,
            req.email,
            req.role.value
        )
    )

    audit(
        "team.invite",
        "organization",
        org_id,
        org_id,
        str(context["user"].id),
        {
            "email": req.email,
            "role": req.role.value,
        }
    )

    return ok(payload)


@router.post("/team/accept")
async def team_accept(
    req: AcceptInviteRequest,
    request: Request
):
    payload = safe_execute(
        "accept_invitation",
        lambda: accept_invitation(
            req.token,
            req.password
        )
    )

    return ok(payload)


# =========================================================
# NOTIFICATIONS
# =========================================================

@router.get("/notifications")
async def notifications(
    context: dict = Depends(get_current_user_context)
):
    return ok(
        safe_execute(
            "notifications",
            lambda: list_notifications(
                context["user"].id,
                context.get("organization_id")
            )
        )
    )


# =========================================================
# BILLING
# =========================================================

@router.get("/billing")
async def billing(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "recruiter"
        )
    )
):
    org_id = context.get("organization_id")

    if not org_id:
        return ok({
            "plan": "free",
            "status": "inactive",
            "message": "No organization assigned"
        })

    return ok(
        safe_execute(
            "billing_overview",
            lambda: subscription_overview(org_id)
        )
    )


@router.post("/billing/plan")
async def billing_plan(
    req: PlanRequest,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin"
        )
    )
):
    org_id = require_organization(context)

    payload = safe_execute(
        "billing_plan_update",
        lambda: update_plan(org_id, req.plan.value)
    )

    audit(
        "billing.plan_change",
        "subscription",
        org_id,
        org_id,
        str(context["user"].id),
        {"plan": req.plan.value},
    )

    return ok(payload)


@router.post("/billing/checkout")
async def billing_checkout(
    req: CheckoutRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin")),
):
    org_id = int(require_organization(context))
    payload = safe_execute(
        "billing_checkout_create",
        lambda: create_checkout_session(
            org_id,
            int(context["user"].id),
            req.provider,
            req.plan.value,
            req.billing_cycle,
            req.currency.upper(),
            req.idempotency_key,
        ),
    )
    audit("billing.checkout_create", "payment", payload.get("reference", "pending"), str(org_id), str(context["user"].id), payload)
    return ok(payload)


@router.post("/billing/mpesa/stk-push")
async def billing_mpesa_stk_push(
    req: MpesaStkRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin")),
):
    org_id = int(require_organization(context))
    payload = await initiate_mpesa_stk_push(
        org_id,
        int(context["user"].id),
        req.phone,
        req.plan.value,
        req.billing_cycle,
        req.currency.upper(),
        req.idempotency_key,
        req.destination_phone,
    )
    audit("billing.mpesa_stk_push", "payment", payload.get("reference", "pending"), str(org_id), str(context["user"].id), payload)
    return ok(payload)


@router.post("/billing/verify")
async def billing_verify_payment(
    req: VerifyPaymentRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = int(require_organization(context))
    return ok(safe_execute("billing_payment_verify", lambda: verify_payment(org_id, req.reference)))


@router.get("/billing/invoices")
async def billing_invoices(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = int(require_organization(context))
    return ok(safe_execute("billing_invoices", lambda: subscription_overview(org_id).get("invoices", [])))


@router.get("/billing/payments")
async def billing_payments(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = int(require_organization(context))
    return ok(safe_execute("billing_payments", lambda: subscription_overview(org_id).get("payments", [])))


@router.get("/billing/analytics")
async def billing_analytics_route(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = int(require_organization(context))
    return ok(safe_execute("billing_analytics", lambda: subscription_overview(org_id).get("analytics", {})))


@router.post("/billing/cancel")
async def billing_cancel(
    context: dict = Depends(require_roles("super_admin", "company_admin")),
):
    org_id = int(require_organization(context))
    payload = safe_execute("billing_cancel", lambda: cancel_subscription(org_id))
    audit("billing.subscription_cancel", "subscription", str(org_id), str(org_id), str(context["user"].id))
    return ok(payload)


@router.post("/billing/webhooks/mpesa", include_in_schema=True)
async def billing_mpesa_callback(request: Request):
    payload = await request.json()
    return ok(await handle_provider_webhook("mpesa", payload, dict(request.headers)))


@router.post("/billing/webhooks/paypal", include_in_schema=True)
async def billing_paypal_webhook(request: Request):
    payload = await request.json()
    return ok(await handle_provider_webhook("paypal", payload, dict(request.headers)))


@router.post("/billing/webhooks/flutterwave", include_in_schema=True)
async def billing_flutterwave_webhook(request: Request):
    payload = await request.json()
    return ok(await handle_provider_webhook("flutterwave", payload, dict(request.headers)))


@router.post("/billing/webhooks/paystack", include_in_schema=True)
async def billing_paystack_webhook(request: Request):
    payload = await request.json()
    return ok(await handle_provider_webhook("paystack", payload, dict(request.headers)))


@router.post("/billing/webhooks/paddle", include_in_schema=True)
async def billing_paddle_webhook(request: Request):
    payload = await request.json()
    return ok(await handle_provider_webhook("paddle", payload, dict(request.headers)))


@router.post("/billing/invoice")
async def billing_invoice(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "recruiter"
        )
    )
):
    org_id = require_organization(context)

    payload = safe_execute(
        "generate_invoice",
        lambda: generate_invoice(org_id)
    )

    audit(
        "billing.invoice_generate",
        "invoice",
        org_id,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


# =========================================================
# ANALYTICS
# =========================================================

@router.get("/analytics/overview")
async def analytics_overview(
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    increment_usage(org_id, "ai_calls", 1)

    return ok(
        safe_execute(
            "analytics_overview",
            lambda: get_workspace_overview(org_id)
        )
    )


# =========================================================
# SECURITY
# =========================================================

@router.get("/security/overview")
async def security_dashboard(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "recruiter"
        )
    )
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "security_overview",
            lambda: security_overview(org_id)
        )
    )


@router.post("/security/analyze")
async def security_analyze(
    req: SecurityEventRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "security_analyze",
        lambda: monitor_event(
            req.event_type,
            org_id,
            context["user"].id,
            req.source_ip,
            req.details
        )
    )

    audit(
        "security.analyze",
        "security_event",
        req.event_type,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


@router.get("/security/incidents")
async def security_incidents(
    status: str | None = None,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    items = safe_execute("security_incidents", lambda: list_incidents(org_id, status=status, limit=60))
    return ok({"incidents": items, "count": len(items)})


@router.get("/security/incidents/{incident_id}")
async def security_incident_detail(
    incident_id: int,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    incident = safe_execute("security_incident_get", lambda: get_incident(org_id, int(incident_id)))
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return ok(incident)


@router.get("/security/incidents/{incident_id}/actions")
async def security_incident_actions(
    incident_id: int,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    actions = safe_execute("security_incident_actions", lambda: list_incident_actions(org_id, int(incident_id), limit=120))
    return ok({"actions": actions, "count": len(actions)})


def _security_action_requires_admin(action_type: str) -> bool:
    """
    Safety-first server-side enforcement:
    recruiters can investigate/record placeholders; account/session enforcement requires admin-level roles.
    """
    action_type = (action_type or "").strip().lower()
    return action_type in {
        "revoke_sessions",
        "force_logout",
        "lock_account",
        "temporary_account_lock",
        "unlock_account",
    }


@router.post("/security/incidents/{incident_id}/actions")
async def security_execute_action(
    incident_id: int,
    req: SecurityIncidentActionRequest,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    role = str(context.get("role") or "").lower()

    action_type = (req.action_type or "").strip()
    if not action_type:
        raise HTTPException(status_code=422, detail="action_type is required")

    if _security_action_requires_admin(action_type) and role not in {"super_admin", "company_admin", "hiring_manager"}:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Insufficient permissions",
                "code": "insufficient_permissions",
                "reason": "security_action_requires_admin",
                "guidance": "Ask a company admin to execute containment actions like lockouts or session revocation.",
            },
        )

    # Require explicit confirmation for sensitive actions.
    if action_type.lower() in {"revoke_sessions", "force_logout", "lock_account", "temporary_account_lock"} and not req.confirm:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Confirmation required",
                "code": "confirmation_required",
                "guidance": "Re-submit the action with confirm=true after you review the response plan.",
            },
        )

    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)

    payload = safe_execute(
        "security_execute_action",
        lambda: execute_security_action(
            organization_id=org_id,
            incident_id=int(incident_id),
            event_id=None,
            actor_user_id=int(actor_user_id) if actor_user_id is not None else None,
            action_type=action_type,
            payload=req.payload or {},
            reason=req.reason or "security_center_action",
        ),
    )

    audit(
        "security.action_execute",
        "security_incident",
        str(incident_id),
        str(org_id),
        str(actor_user_id) if actor_user_id is not None else "unknown",
        {"action_type": action_type, "ok": bool(payload.get("ok")), "action_id": payload.get("action_id")},
    )

    return ok(payload)


@router.post("/security/incidents/{incident_id}/contain")
async def security_contain_incident(
    incident_id: int,
    req: SecurityIncidentNoteRequest | None = None,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    note = req.note if req else ""
    incident = safe_execute(
        "security_incident_contain",
        lambda: update_incident_status(org_id, int(incident_id), status="contained", owner_user_id=int(actor_user_id) if actor_user_id else None, notes=note),
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    audit("security.incident_contain", "security_incident", str(incident_id), str(org_id), str(actor_user_id), {})
    return ok(incident)


@router.post("/security/incidents/{incident_id}/resolve")
async def security_resolve_incident(
    incident_id: int,
    req: SecurityIncidentNoteRequest | None = None,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    note = req.note if req else ""
    incident = safe_execute(
        "security_incident_resolve",
        lambda: update_incident_status(org_id, int(incident_id), status="resolved", owner_user_id=int(actor_user_id) if actor_user_id else None, notes=note),
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    audit("security.incident_resolve", "security_incident", str(incident_id), str(org_id), str(actor_user_id), {})
    return ok(incident)


@router.post("/security/incidents/{incident_id}/notes")
async def security_incident_notes(
    incident_id: int,
    req: SecurityIncidentNoteRequest,
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    incident = safe_execute(
        "security_incident_note",
        lambda: update_incident_status(org_id, int(incident_id), status="open", owner_user_id=int(actor_user_id) if actor_user_id else None, notes=req.note),
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    audit(
        "security.incident_note",
        "security_incident",
        str(incident_id),
        str(org_id),
        str(actor_user_id),
        {"note_len": len(req.note or "")},
    )
    return ok(incident)


@router.get("/security/automation/rules")
async def security_rules(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    org_id = int(require_organization(context))
    rules = safe_execute("security_rules_list", lambda: list_security_rules(org_id, limit=60))
    return ok({"rules": rules, "count": len(rules)})


@router.post("/security/automation/rules")
async def security_rules_create(
    req: SecurityRuleCreateRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin", "hiring_manager")),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)

    rule = safe_execute(
        "security_rules_create",
        lambda: create_security_rule(
            org_id,
            name=req.name,
            trigger=req.trigger,
            enabled=req.enabled,
            conditions=req.conditions,
            actions=req.actions,
        ),
    )

    audit("security.rule_create", "security_rule", str(rule.get("id")), str(org_id), str(actor_user_id), {"name": req.name})
    return ok(rule)


@router.patch("/security/automation/rules/{rule_id}")
async def security_rules_patch(
    rule_id: int,
    req: SecurityRulePatchRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin", "hiring_manager")),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    rule = safe_execute("security_rules_patch", lambda: update_security_rule(int(rule_id), org_id, patch))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    audit("security.rule_update", "security_rule", str(rule_id), str(org_id), str(actor_user_id), {"patch_keys": list(patch.keys())})
    return ok(rule)


@router.delete("/security/automation/rules/{rule_id}")
async def security_rules_delete(
    rule_id: int,
    context: dict = Depends(require_roles("super_admin", "company_admin", "hiring_manager")),
):
    org_id = int(require_organization(context))
    actor_user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    ok_del = safe_execute("security_rules_delete", lambda: delete_security_rule(int(rule_id), org_id))
    if not ok_del:
        raise HTTPException(status_code=404, detail="Rule not found")

    audit("security.rule_delete", "security_rule", str(rule_id), str(org_id), str(actor_user_id), {})
    return ok({"deleted": True, "rule_id": int(rule_id)})


@router.get("/security/controls")
async def security_controls(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin",
            "hiring_manager",
            "recruiter",
        )
    ),
):
    # Snapshot is mostly process-config (not tenant data), but keep it auth-protected anyway.
    _ = require_organization(context)
    return ok(safe_execute("security_controls", lambda: security_controls_snapshot()))


# =========================================================
# SCHEDULING
# =========================================================

@router.get("/scheduling")
async def scheduling_list(
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "list_schedules",
            lambda: list_schedules(org_id)
        )
    )


@router.post("/scheduling")
async def scheduling_create(
    req: ScheduleRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "schedule_interview",
        lambda: schedule_interview(
            org_id,
            req.candidate_id,
            req.interviewer_email,
            req.starts_at,
            req.timezone,
            req.provider,
        )
    )

    audit(
        "scheduling.create",
        "schedule",
        req.candidate_id,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


# =========================================================
# DECISION INTELLIGENCE
# =========================================================

@router.post("/decision/intelligence", status_code=status.HTTP_200_OK)
async def decision_intelligence(
    req: DecisionRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "decision_intelligence",
        lambda: make_decision(
            req.match_score,
            req.interview_score,
            req.communication_score,
            req.proctor_score,
            req.missing_skills,
        )
    )

    audit(
        "decision.evaluate",
        "organization",
        org_id,
        org_id,
        str(context["user"].id),
        {"decision": payload.get("decision"), "confidence": payload.get("confidence")},
    )

    return ok(payload)


# =========================================================
# WORKFLOWS
# =========================================================

@router.post("/workflows/evaluate", status_code=status.HTTP_200_OK)
async def workflow_evaluate(
    req: WorkflowRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "workflow_evaluate",
        lambda: evaluate_workflow(
            req.match_score,
            req.candidate_id,
            org_id,
            req.job_id,
        )
    )

    persisted = safe_execute(
        "workflow_persist",
        lambda: persist_workflow_run(payload)
    )

    safe_execute(
        "workflow_notify",
        lambda: notify_workflow_action(context["user"].id, persisted)
    )

    audit(
        "workflow.evaluate",
        "candidate",
        req.candidate_id,
        org_id,
        str(context["user"].id),
        {"decision": persisted.get("decision"), "actions": persisted.get("actions", [])},
    )

    return ok(persisted)


# =========================================================
# CANDIDATE INTELLIGENCE
# =========================================================

@router.get("/candidates/{candidate_id}/intelligence", status_code=status.HTTP_200_OK)
async def candidate_intelligence(
    candidate_id: str,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "candidate_intelligence",
            lambda: candidate_graph(candidate_id, org_id)
        )
    )


@router.get("/skills/demand", status_code=status.HTTP_200_OK)
async def skills_demand(
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "skill_demand",
            lambda: skill_demand(org_id)
        )
    )


# =========================================================
# ASSESSMENTS
# =========================================================

@router.post("/assessments")
async def assessment_create(
    req: AssessmentCreateRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "create_assessment",
        lambda: create_assessment(
            org_id,
            req.candidate_id,
            req.title,
            req.assessment_type
        )
    )

    audit(
        "assessment.create",
        "assessment",
        req.candidate_id,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


@router.post("/assessments/{assessment_id}/submit")
async def assessment_submit_route(
    assessment_id: str,
    req: AssessmentSubmitRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "submit_assessment",
        lambda: submit_assessment(
            assessment_id,
            req.submissions,
            org_id
        )
    )

    audit(
        "assessment.submit",
        "assessment",
        assessment_id,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


# =========================================================
# COMPLIANCE
# =========================================================

@router.post("/compliance/bias")
async def compliance_bias(
    req: BiasRequest,
    context: dict = Depends(get_current_user_context)
):
    return ok(
        safe_execute(
            "detect_bias",
            lambda: detect_bias(req.text)
        )
    )


@router.post("/compliance/bias-check", include_in_schema=False)
async def compliance_bias_check(
    req: BiasRequest,
    context: dict = Depends(get_current_user_context)
):
    return await compliance_bias(req, context)


@router.post("/compliance/consent")
async def compliance_consent(
    req: ConsentRequest,
    context: dict = Depends(get_current_user_context)
):
    org_id = require_organization(context)

    payload = safe_execute(
        "record_consent",
        lambda: record_consent(
            org_id,
            req.candidate_id,
            req.consent_type,
            req.status.value,
            req.metadata
        )
    )

    audit(
        "consent.record",
        "consent",
        req.candidate_id,
        org_id,
        str(context["user"].id),
    )

    return ok(payload)


# =========================================================
# AUDIT LOGS
# =========================================================

@router.get("/audit-logs")
async def audit_logs(
    context: dict = Depends(
        require_roles(
            "super_admin",
            "company_admin"
        )
    )
):
    org_id = require_organization(context)

    return ok(
        safe_execute(
            "audit_logs",
            lambda: list_audit_logs(org_id)
        )
    )
