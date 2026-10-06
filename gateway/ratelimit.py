"""Fixed-window rate limiting backed by atomic SQLite counters.

Two independent scopes are checked per callback:

* ``user:{workspace}:{user}``      -> per-user limit
* ``workspace:{workspace}``        -> per-workspace aggregate limit

The counter increment is a single atomic ``INSERT ... ON CONFLICT DO UPDATE``
statement, so legitimate concurrent requests are never double-counted or
wrongly rejected. Limits use a one-minute aligned window.
"""
from __future__ import annotations

from dataclasses import dataclass

from gateway.db import Database
from gateway.errors import RateLimited

WINDOW_SECONDS = 60


@dataclass
class RateLimitConfig:
    user_limit_per_minute: int = 60
    workspace_limit_per_minute: int = 600


class RateLimiter:
    def __init__(self, db: Database, config: RateLimitConfig) -> None:
        self.db = db
        self.config = config

    def _consume(self, scope: str, now: int, limit: int) -> None:
        window = now // WINDOW_SECONDS * WINDOW_SECONDS
        row = self.db.query_one(
            """
            INSERT INTO rate_counters(scope, window, counter)
            VALUES (?, ?, 1)
            ON CONFLICT(scope, window)
            DO UPDATE SET counter = counter + 1
            RETURNING counter
            """,
            (scope, window),
        )
        count = row["counter"]
        if count > limit:
            raise RateLimited(
                f"rate limit exceeded for scope {scope}: {count}/{limit} "
                f"in window starting {window}"
            )

    def check_and_consume(self, workspace_id: str, user_id: str, now: int) -> None:
        """Consume one token; raise RateLimited with a scope-specific reason."""
        self._consume(f"user:{workspace_id}:{user_id}", now,
                      self.config.user_limit_per_minute)
        self._consume(f"workspace:{workspace_id}", now,
                      self.config.workspace_limit_per_minute)
