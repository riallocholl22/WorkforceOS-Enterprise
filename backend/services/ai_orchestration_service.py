import os
import json
from typing import Any, Dict

from backend.services.ai_service import ask_ai
from backend.services.enterprise_service import get_workspace_overview
from backend.services.security_ai import security_overview
from backend.services.knowledge_graph_service import workspace_knowledge_graph


def ai_provider_status() -> Dict[str, Any]:
    return {
        "active_provider": os.getenv("AI_PROVIDER", "openai").lower(),
        "configured": {
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "local_llm": bool(os.getenv("LOCAL_LLM_URL")),
        },
        "routing": {
            "fallback_enabled": True,
            "cost_optimization": "enabled",
            "streaming_supported": True,
        },
        "models": {
            "openai": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet"),
            "local_llm": os.getenv("LOCAL_LLM_MODEL", "local-recruiter-copilot"),
        },
    }


def _select_agent(message: str, context: Dict[str, Any]) -> str:
    msg = (message or "").lower().strip()
    if msg.startswith("/agent "):
        name = msg.split(" ", 1)[1].strip().split()[0]
        return name or "recruiter"
    if any(k in msg for k in ("security", "suspicious", "threat", "breach", "attack", "mfa", "token")):
        return "security"
    if any(k in msg for k in ("analytics", "funnel", "conversion", "velocity", "bottleneck", "roi", "forecast")):
        return "analytics"
    if any(k in msg for k in ("shortlist", "approve", "reject", "workflow", "automation", "threshold")):
        return "workflow"
    if any(k in msg for k in ("graph", "relationships", "knowledge graph", "similar candidates", "clusters")):
        return "graph"
    return str(context.get("agent") or "recruiter")


def _context_snapshot(agent: str, context: Dict[str, Any]) -> Dict[str, Any]:
    org_id = context.get("organization_id") or context.get("workspace_id") or context.get("tenant_id")
    try:
        org_id_int = int(org_id) if org_id is not None else None
    except Exception:
        org_id_int = None

    if agent == "analytics":
        overview = get_workspace_overview(org_id_int)
        return {"organization_id": org_id_int, "workspace_overview": overview}
    if agent == "security":
        return {"organization_id": org_id_int, "security_overview": security_overview(org_id_int)}
    if agent == "graph":
        return {"organization_id": org_id_int, "knowledge_graph": workspace_knowledge_graph(org_id_int, max_candidates=40, max_jobs=12)}
    if agent == "workflow":
        overview = get_workspace_overview(org_id_int)
        return {"organization_id": org_id_int, "workflow": (overview or {}).get("workforce_analytics") or {}}
    return {"organization_id": org_id_int}


def route_ai_request(message: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context or {}
    agent = _select_agent(message, context)
    snapshot = _context_snapshot(agent, context)

    system = (
        "You are an enterprise AI recruitment operating system. "
        "Respond like a senior recruiter ops strategist: concise, confident, and decision-oriented. "
        "When asked for analytics/security/workflow/graph insights, ground your answer in the provided JSON snapshot. "
        "Avoid generic advice; provide 3-5 concrete next actions and explain why."
    )
    response = ask_ai(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "Context snapshot (JSON): " + json.dumps(snapshot, ensure_ascii=True)[:6000]},
            {"role": "user", "content": message},
        ],
        candidates=context.get("candidates"),
    )
    return {
        "provider": ai_provider_status()["active_provider"],
        "response": response,
        "fallback_safe": not bool(os.getenv("OPENAI_API_KEY")),
        "agent": agent,
    }
