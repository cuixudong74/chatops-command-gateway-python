"""Per (workspace, user) sliding-window rate limiting."""
from __future__ import annotations

import threading


class RateLimiter:
    """Sliding-window counter keyed by (workspace, user).

    Each key is independent, so throttling one noisy user never affects
    other users or other workspaces (no collateral damage).
    """

    def __init__(self, max_requests: int, window_seconds: float):
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = {}

    def check(self, workspace_id: str, user_id: str, now: float) -> bool:
        """True if allowed (and records the hit); False if over limit."""
        key = (workspace_id, user_id)
        with self._lock:
            hits = self._hits.setdefault(key, [])
            cutoff = now - self._window
            # prune expired entries (lists stay small: bounded by self._max)
            while hits and hits[0] <= cutoff:
                hits.pop(0)
            if len(hits) >= self._max:
                return False
            hits.append(now)
            return True
