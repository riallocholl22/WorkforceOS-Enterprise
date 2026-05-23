import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import HTTPException, Request

_BUCKETS: dict[str, Deque[float]] = defaultdict(deque)


def _client_key(request: Request, scope: str) -> str:
    client = request.client.host if request.client else "unknown"
    user = request.headers.get("authorization", "anonymous")[-24:]
    return f"{scope}:{client}:{user}"


def check_rate_limit(request: Request, scope: str, limit: int = 30, window_seconds: int = 60) -> None:
    now = time.time()
    key = _client_key(request, scope)
    bucket = _BUCKETS[key]

    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()

    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait and try again.")

    bucket.append(now)


def ai_rate_limit(request: Request) -> None:
    check_rate_limit(request, "ai", limit=40, window_seconds=60)


def upload_rate_limit(request: Request) -> None:
    check_rate_limit(request, "upload", limit=10, window_seconds=300)


def login_rate_limit(request: Request) -> None:
    check_rate_limit(request, "login", limit=20, window_seconds=300)