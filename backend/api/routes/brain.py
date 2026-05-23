from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user_context, require_roles
from backend.api.responses import ok
from backend.services.hiring_brain_service import candidate_360, executive_brief
from backend.services.automation_rules_service import create_rule, delete_rule, list_rules, update_rule
from backend.services.demo_environment_service import seed_enterprise_demo_environment
from backend.services.knowledge_graph_service import workspace_knowledge_graph
from backend.services.workforce_os_service import workforce_operating_system


router = APIRouter(prefix="/enterprise/brain", tags=["AI Decision Brain"])


class Candidate360Request(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=120)
    job_description: str = Field(default="", max_length=10000)
    job_id: Optional[int] = None


class AutomationRuleCreateRequest(BaseModel):
    name: str = Field(default="Rule", max_length=120)
    enabled: bool = True
    trigger: str = Field(default="match.completed", max_length=120)
    conditions: dict = Field(default_factory=dict)
    actions: dict = Field(default_factory=dict)


class AutomationRulePatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    enabled: Optional[bool] = None
    trigger: Optional[str] = Field(default=None, max_length=120)
    conditions: Optional[dict] = None
    actions: Optional[dict] = None


@router.get("/candidates/{candidate_id}")
def get_candidate_360(
    candidate_id: str,
    job_description: str = Query(default="", max_length=10000),
    job_id: Optional[int] = Query(default=None),
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter", "hiring_manager")),
):
    org_id = context.get("organization_id")
    return ok(candidate_360(candidate_id, org_id, job_description=job_description, job_id=job_id))


@router.post("/candidates/360")
def post_candidate_360(
    req: Candidate360Request,
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter", "hiring_manager")),
):
    org_id = context.get("organization_id")
    return ok(candidate_360(req.candidate_id, org_id, job_description=req.job_description, job_id=req.job_id))


@router.get("/executive-brief")
def get_executive_brief(
    days: int = Query(default=14, ge=1, le=90),
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    user = context.get("user")
    user_id = getattr(user, "id", None)
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    return ok(executive_brief(org_id, user_id=user_id, days=int(days)))


@router.get("/workforce-os")
def get_workforce_operating_system(
    days: int = Query(default=14, ge=1, le=90),
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter", "hiring_manager")),
):
    org_id = context.get("organization_id")
    user = context.get("user")
    user_id = getattr(user, "id", None)
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    return ok(workforce_operating_system(org_id, user_id=user_id, days=int(days)))


@router.post("/demo-environment")
def create_demo_environment(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    user = context.get("user")
    user_id = getattr(user, "id", None)
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    return ok(seed_enterprise_demo_environment(org_id, user_id=user_id))


@router.get("/knowledge-graph")
def get_knowledge_graph(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter", "hiring_manager")),
):
    """
    Lightweight enterprise knowledge graph (candidates/jobs/skills relationships).
    Used for AI reasoning + future UI visualization.
    """
    org_id = context.get("organization_id")
    return ok(workspace_knowledge_graph(org_id))


@router.get("/automation/rules")
def get_automation_rules(
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    return ok({"rules": list_rules(org_id)})


@router.post("/automation/rules")
def create_automation_rule(
    req: AutomationRuleCreateRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    rule = create_rule(
        org_id,
        name=req.name,
        trigger=req.trigger,
        conditions=req.conditions if isinstance(req.conditions, dict) else {},
        actions=req.actions if isinstance(req.actions, dict) else {},
        enabled=bool(req.enabled),
    )
    return ok({"rule": rule})


@router.patch("/automation/rules/{rule_id}")
def patch_automation_rule(
    rule_id: int,
    req: AutomationRulePatchRequest,
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_rule(rule_id, org_id, patch)
    if not updated:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return ok({"rule": updated})


@router.delete("/automation/rules/{rule_id}")
def remove_automation_rule(
    rule_id: int,
    context: dict = Depends(require_roles("super_admin", "company_admin", "recruiter")),
):
    org_id = context.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    deleted = delete_rule(rule_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return ok({"deleted": True})
