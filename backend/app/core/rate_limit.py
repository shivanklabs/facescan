"""Rate limiting for sensitive endpoints (upload, face search). In-process sliding windows;
no Redis. A multi-process deployment needs a shared store (Postgres) later.
"""

import threading
import time
from collections import deque

from fastapi import Request

from app.core.errors import TooManyRequestsError


class RateLimiter:
    """At most `limit` calls per `window_seconds` per client address."""

    def __init__(self, name: str, limit: int, window_seconds: float) -> None:
        """Purpose: configure one limiter. Side effects: none."""
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit >= 1 and window_seconds > 0 required")
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Purpose: count one call for key. Raises TooManyRequestsError over the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise TooManyRequestsError(f"too many {self.name} requests; try again later")
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory: drop idle keys
                for idle in [k for k, v in self._hits.items() if not v]:
                    del self._hits[idle]

    def __call__(self, request: Request) -> None:
        """Purpose: FastAPI dependency keyed on the client address."""
        client = request.client.host if request.client else "unknown"
        self.check(client)
