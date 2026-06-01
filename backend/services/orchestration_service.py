from __future__ import annotations

import hashlib
import math
import os
import time
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Deque, Dict, Optional


class EnterpriseEventBus:
    """In-process event fabric for deterministic telemetry and bounded buffers."""

    def __init__(self, max_events: int = 600):
        self._events: Deque[dict[str, Any]] = deque(maxlen=max_events)
        self._metrics: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def publish(self, topic: str, event: str, payload: Optional[dict[str, Any]] = None, *, severity: str = "info") -> dict[str, Any]:
        now = time.time()
        item = {
            "id": f"evt_{int(now * 1000)}_{hashlib.sha1(f'{topic}:{event}:{now}'.encode()).hexdigest()[:8]}",
            "topic": topic,
            "event": event,
            "severity": severity,
            "payload": payload or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        with self._lock:
            self._events.appendleft(item)
            metric = self._metrics.setdefault(topic, {"published": 0, "last_event": None, "last_seen": 0.0})
            metric["published"] += 1
            metric["last_event"] = event
            metric["last_seen"] = now
        return item

    def heartbeat(self, stream: str, *, latency_ms: Optional[float] = None, clients: int = 1) -> None:
        now = time.time()
        with self._lock:
            metric = self._metrics.setdefault(f"stream.{stream}", {"published": 0})
            metric.update({
                "status": "healthy",
                "last_seen": now,
                "latency_ms": latency_ms,
                "clients": max(0, int(clients or 0)),
            })

    def snapshot(self, limit: int = 80) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            metrics = {k: dict(v) for k, v in self._metrics.items()}
            events = list(self._events)[:limit]
        queue_depth = len(events)
        stale_streams = [
            name for name, metric in metrics.items()
            if name.startswith("stream.") and now - float(metric.get("last_seen") or 0) > 70
        ]
        return {
            "mode": os.getenv("EVENT_BUS_MODE", "in_process_bounded"),
            "queue_depth": queue_depth,
            "buffer_limit": self._events.maxlen,
            "events": events,
            "metrics": metrics,
            "stale_streams": stale_streams,
            "backpressure": {
                "status": "watch" if queue_depth > int((self._events.maxlen or 1) * 0.8) else "clear",
                "queue_utilization": round(queue_depth / max(1, int(self._events.maxlen or 1)), 3),
            },
        }


class ProctorFrameOrchestrator:
    """Adaptive frame gate with dedupe, cooldown, smoothing, and retry coordination."""

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._min_interval = float(os.getenv("PROCTOR_MIN_INTERVAL_SECONDS", "4.5"))
        self._max_interval = float(os.getenv("PROCTOR_MAX_INTERVAL_SECONDS", "24"))

    @staticmethod
    def _hash_frame(image_base64: str) -> str:
        raw = (image_base64 or "").split(",", 1)[-1]
        return hashlib.sha256(raw[:6000].encode("utf-8", errors="ignore")).hexdigest()[:20]

    @staticmethod
    def _smooth(prev: Optional[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
        if not prev:
            return current
        smoothed = dict(current)
        for key in ("attention_score", "engagement_score", "visibility_score", "behavioral_confidence", "integrity_score"):
            if key in current:
                old = float(prev.get(key) or current.get(key) or 0)
                new = float(current.get(key) or 0)
                smoothed[key] = round((old * 0.58) + (new * 0.42), 2)
        return smoothed

    @staticmethod
    def _stabilize_alerts(state: dict[str, Any], alerts: list[str], now: float) -> list[str]:
        counts = state.setdefault("alert_counts", {})
        last_emit = state.setdefault("alert_last_emit", {})
        stable: list[str] = []
        current = {str(a) for a in alerts if a}
        for alert in list(counts.keys()):
            if alert not in current:
                counts[alert] = max(0, int(counts.get(alert) or 0) - 1)
        for alert in current:
            counts[alert] = min(4, int(counts.get(alert) or 0) + 1)
            cooldown = 18.0 if alert in {"low_light", "dim_light", "blurry_frame", "eyes_not_detected"} else 8.0
            if counts[alert] >= 2 or alert in {"multiple_faces", "invalid_frame", "opencv_unavailable"}:
                if now - float(last_emit.get(alert) or 0) >= cooldown:
                    stable.append(alert)
                    last_emit[alert] = now
        return sorted(set(stable))

    def process(
        self,
        *,
        session_id: str,
        candidate_id: str,
        image_base64: str,
        analyzer: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        now = time.time()
        frame_hash = self._hash_frame(image_base64)
        with self._lock:
            state = self._sessions.setdefault(session_id, {"failures": 0, "next_interval": self._min_interval})
            elapsed = now - float(state.get("last_processed_at") or 0)
            cached = dict(state.get("last_result") or {})
            duplicate = cached and frame_hash == state.get("last_frame_hash")
            min_interval = float(state.get("next_interval") or self._min_interval)
            if cached and (duplicate or elapsed < min_interval):
                cached.update({
                    "throttled": True,
                    "duplicate_frame": duplicate,
                    "retry_after_seconds": round(max(0.4, min_interval - elapsed), 2),
                    "next_sample_seconds": round(min(self._max_interval, min_interval + (1.5 if duplicate else 0)), 2),
                    "orchestration": "buffered_cached_result",
                })
                return cached

        try:
            raw = analyzer(image_base64, session_id=session_id, candidate_id=candidate_id)
        except Exception as exc:
            with self._lock:
                state = self._sessions.setdefault(session_id, {})
                state["failures"] = int(state.get("failures") or 0) + 1
                state["next_interval"] = min(self._max_interval, self._min_interval * math.pow(1.7, state["failures"]))
                cached = dict(state.get("last_result") or {})
            cached.update({
                "degraded": True,
                "error": str(exc)[:160],
                "alerts": sorted(set([*(cached.get("alerts") or []), "vision_processing_degraded"])),
                "retry_after_seconds": round(float(state.get("next_interval") or self._max_interval), 2),
                "orchestration": "graceful_degradation",
            })
            return cached

        with self._lock:
            state = self._sessions.setdefault(session_id, {})
            previous = state.get("last_result")
            result = self._smooth(previous, raw)
            result["raw_alerts"] = list(raw.get("alerts") or [])
            result["alerts"] = self._stabilize_alerts(state, list(raw.get("alerts") or []), now)
            severe = any(a in {"multiple_faces", "no_face", "invalid_frame"} for a in result["alerts"])
            low_quality = any(a in {"low_light", "dim_light", "blurry_frame"} for a in result["raw_alerts"])
            if severe:
                next_interval = max(self._min_interval, 6.5)
            elif low_quality:
                next_interval = min(self._max_interval, 10.5)
            elif float(result.get("visibility_score") or 0) >= 72 and float(result.get("attention_score") or 0) >= 72:
                next_interval = min(self._max_interval, 12.0)
            else:
                next_interval = max(self._min_interval, 7.5)
            state.update({
                "last_processed_at": now,
                "last_frame_hash": frame_hash,
                "last_result": result,
                "failures": 0,
                "next_interval": next_interval,
            })
        result.update({
            "throttled": False,
            "duplicate_frame": False,
            "next_sample_seconds": round(next_interval, 2),
            "orchestration": "processed_with_stabilization",
        })
        return result

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            sessions = list(self._sessions.values())
        active = [s for s in sessions if now - float(s.get("last_processed_at") or 0) < 180]
        return {
            "active_sessions": len(active),
            "tracked_sessions": len(sessions),
            "min_interval_seconds": self._min_interval,
            "max_interval_seconds": self._max_interval,
            "stabilization": "adaptive_throttle_dedupe_confidence_smoothing",
            "retry_policy": "exponential_backoff_with_cached_degradation",
        }


event_bus = EnterpriseEventBus()
proctor_orchestrator = ProctorFrameOrchestrator()
