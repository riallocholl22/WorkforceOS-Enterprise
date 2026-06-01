import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from backend.api.dependencies import get_current_user_context
from backend.api.responses import ok
from backend.services.auth_service import get_user_context_from_token
from backend.services.enterprise_service import log_audit_event
from backend.services.product_intelligence_service import (
    product_intelligence_center,
    product_intelligence_event_feed,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enterprise/product-intelligence", tags=["Product Intelligence"])


def _require_org(context: dict) -> int:
    org_id = context.get("organization_id")
    if org_id is None:
        raise HTTPException(status_code=400, detail="User is not assigned to an organization")
    return int(org_id)


def _ws_safe(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


async def _ws_send(websocket: WebSocket, event: str, **payload) -> None:
    await websocket.send_text(json.dumps({"event": event, **payload}, default=_ws_safe))


@router.get("/overview")
async def product_intelligence_overview(
    days: int = 30,
    context: dict = Depends(get_current_user_context),
):
    org_id = _require_org(context)
    user_id = context.get("user_id") or getattr(context.get("user"), "id", None)
    payload = product_intelligence_center(org_id, int(user_id) if user_id else None, days=days)
    log_audit_event(
        "product_intelligence.view",
        "organization",
        str(org_id),
        org_id,
        int(user_id) if user_id else None,
        {
            "window_days": payload.get("window_days"),
            "product_health_score": payload.get("executive_overview", {}).get("product_health_score"),
            "destinations": payload.get("observability_integration", {}).get("destinations", []),
        },
    )
    return ok(payload)


@router.get("/events")
async def product_intelligence_events(
    limit: int = 20,
    context: dict = Depends(get_current_user_context),
):
    org_id = _require_org(context)
    return ok({"events": product_intelligence_event_feed(org_id, limit=limit)})


@router.websocket("/stream")
async def product_intelligence_stream(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    context = get_user_context_from_token(token)
    if not context:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = context.get("organization_id")
    if org_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user = context.get("user")
    user_id = getattr(user, "id", None)
    days = 30
    try:
        days = max(1, min(180, int(websocket.query_params.get("days", "30"))))
    except ValueError:
        days = 30

    await websocket.accept()
    await _ws_send(websocket, "ready")

    try:
        snapshot = product_intelligence_center(int(org_id), int(user_id) if user_id else None, days=days)
        await _ws_send(websocket, "snapshot", data=snapshot, feed=product_intelligence_event_feed(int(org_id), limit=20))

        last_signature = json.dumps(snapshot.get("executive_overview", {}), sort_keys=True)
        last_heartbeat = datetime.utcnow()
        poll_interval = 10.0

        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=poll_interval)
                if raw.strip() == "ping":
                    await _ws_send(websocket, "pong", generated_at=datetime.utcnow().isoformat())
                    continue
            except asyncio.TimeoutError:
                pass

            next_snapshot = product_intelligence_center(int(org_id), int(user_id) if user_id else None, days=days)
            signature = json.dumps(next_snapshot.get("executive_overview", {}), sort_keys=True)
            if signature != last_signature:
                last_signature = signature
                await _ws_send(websocket, "snapshot", data=next_snapshot, feed=product_intelligence_event_feed(int(org_id), limit=20))
                last_heartbeat = datetime.utcnow()
            elif (datetime.utcnow() - last_heartbeat).total_seconds() >= 25:
                await _ws_send(
                    websocket,
                    "heartbeat",
                    generated_at=datetime.utcnow().isoformat(),
                    telemetry={
                        "stream": "product_intelligence",
                        "mode": "authenticated_websocket",
                        "poll_interval_seconds": poll_interval,
                        "continuity": "healthy",
                    },
                    executive_overview=next_snapshot.get("executive_overview", {}),
                    recommendations=next_snapshot.get("recommendations", [])[:3],
                )
                last_heartbeat = datetime.utcnow()

    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("product_intelligence_stream_failed")
        try:
            await _ws_send(websocket, "error", message="Product Intelligence stream failed gracefully. Reconnect to resume live analytics.")
        except Exception:
            return
