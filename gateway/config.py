"""Gateway configuration.

Settings can be constructed directly in code/tests or loaded from
environment variables (prefix ``GATEWAY_``). Complex map settings use JSON.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Runtime configuration for the gateway."""

    database_path: str = "gateway.db"
    signing_secret: str = "dev-secret"
    signature_tolerance_seconds: int = 300
    default_rate_per_minute: int = 60
    workspace_rate_per_minute: int = 600
    # workspace -> role -> allowed command names ("*" wildcard allowed).
    workspace_roles: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # workspace -> list of approver user ids.
    approvers: dict[str, list[str]] = field(default_factory=dict)
    require_different_approver: bool = True
    action_timeout_seconds: float = 30.0
    recovery_poll_seconds: float = 2.0


def _env(name: str, default):
    return os.environ.get(f"GATEWAY_{name.upper()}", default)


def load_settings() -> Settings:
    """Load settings from environment variables."""

    def _json(name: str, default):
        raw = _env(name, None)
        return json.loads(raw) if raw else default

    return Settings(
        database_path=_env("database_path", "gateway.db"),
        signing_secret=_env("signing_secret", "dev-secret"),
        signature_tolerance_seconds=int(_env("signature_tolerance_seconds", 300)),
        default_rate_per_minute=int(_env("default_rate_per_minute", 60)),
        workspace_rate_per_minute=int(_env("workspace_rate_per_minute", 600)),
        workspace_roles=_json("workspace_roles", {}),
        approvers=_json("approvers", {}),
        require_different_approver=_env("require_different_approver", "true")
        .lower()
        == "true",
        action_timeout_seconds=float(_env("action_timeout_seconds", 30.0)),
        recovery_poll_seconds=float(_env("recovery_poll_seconds", 2.0)),
    )
