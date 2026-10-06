"""Configuration for the gateway."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # HMAC shared secrets per workspace (platform signs callbacks with these).
    workspace_secrets: dict[str, str] = field(default_factory=dict)
    # Max age (seconds) of a callback timestamp before it is rejected as stale.
    max_clock_skew_seconds: float = 300.0
    # RBAC: workspace_id -> set of user ids allowed to *submit* commands.
    submitters: dict[str, frozenset[str]] = field(default_factory=dict)
    # RBAC: workspace_id -> set of user ids allowed to *approve* commands.
    approvers: dict[str, frozenset[str]] = field(default_factory=dict)
    # Rate limits: max requests per window per (workspace, user).
    rate_limit_max: int = 20
    rate_limit_window_seconds: float = 60.0
    # SQLite database path; ":memory:" for tests.
    db_path: str = "gateway.db"

    @staticmethod
    def from_env() -> "Settings":
        secrets: dict[str, str] = {}
        raw = os.environ.get("GATEWAY_WORKSPACE_SECRETS", "")
        for pair in raw.split(","):
            if ":" in pair:
                ws, secret = pair.split(":", 1)
                secrets[ws.strip()] = secret.strip()
        return Settings(
            workspace_secrets=secrets,
            db_path=os.environ.get("GATEWAY_DB_PATH", "gateway.db"),
        )
