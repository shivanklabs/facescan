"""In-process rate limiting for sign-in, upload, invite and search endpoints (per client
address, sliding window). Counters live on the app instance (app.state), so each app starts
fresh. Guest face searches are additionally limited per user and per event in PostgreSQL
(app/modules/matching/service.py), which holds across processes.
"""

import threading
import time
from collections import deque

from fastapi import Request

from app.core.errors import TooManyRequestsError

Window = dict[str, deque[float]]


class RateLimitStore:
    """Counters of one app: limiter name -> client key -> hit times."""

    def __init__(self) -> None:
        """Purpose: empty counters. Side effects: none."""
        self.windows: dict[str, Window] = {}
        self.lock = threading.Lock()


class RateLimiter:
    """At most `limit` calls per `window_seconds` per client address."""

    def __init__(self, name: str, limit: int, window_seconds: float) -> None:
        """Purpose: configure one limiter. Side effects: none."""
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit >= 1 and window_seconds > 0 required")
        self.name = name
        self.limit = limit
        self.window = window_seconds

    def check(self, store: RateLimitStore, key: str) -> None:
        """Purpose: count one call for key. Raises TooManyRequestsError over the limit."""
        now = time.monotonic()
        with store.lock:
            window = store.windows.setdefault(self.name, {})
            hits = window.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise TooManyRequestsError(f"too many {self.name} requests; try again later")
            hits.append(now)
            if len(window) > 10_000:  # bound memory: drop idle keys
                for idle in [k for k, v in window.items() if not v]:
                    del window[idle]

    def __call__(self, request: Request) -> None:
        """Purpose: FastAPI dependency keyed on the client address."""
        store: RateLimitStore = request.app.state.rate_limits
        client = request.client.host if request.client else "unknown"
        self.check(store, client)
