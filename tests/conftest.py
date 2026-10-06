"""Shared pytest fixtures: a gateway backed by a temp database."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings
from gateway.security import compute_signature


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "test.db"),
        signing_secret="test-secret",
        signature_tolerance_seconds=300,
        default_rate_per_minute=5,
        workspace_rate_per_minute=100,
        workspace_roles={
            "w1": {"admin": ["*"], "viewer": ["status"]},
        },
        approvers={"w1": ["boss", "boss2"]},
        require_different_approver=True,
        action_timeout_seconds=5.0,
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def service(client):
    return client.app.state.service


def signed_post(client, payload: dict, secret: str = "test-secret",
                timestamp: int | None = None):
    ts = str(int(time.time()) if timestamp is None else timestamp)
    body = json.dumps(payload).encode()
    sig = compute_signature(secret, ts, body)
    return client.post(
        "/callback",
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": sig},
    )


@pytest.fixture
def post(client):
    def _post(payload, **kw):
        return signed_post(client, payload, **kw)

    return _post


def make_command(event_id="e1", command_id="c1", user="alice", role="admin",
                 command="restart_service", workspace="w1", **extra):
    p = {
        "event_id": event_id,
        "type": "command",
        "workspace_id": workspace,
        "user_id": user,
        "role": role,
        "timestamp": int(time.time()),
        "command_id": command_id,
        "command_name": command,
        "args": {"host": "h1"},
    }
    p.update(extra)
    return p


def make_button(event_id, command_id="c1", user="boss", action="approve",
                workspace="w1"):
    return {
        "event_id": event_id,
        "type": "button",
        "workspace_id": workspace,
        "user_id": user,
        "timestamp": int(time.time()),
        "command_id": command_id,
        "action": action,
    }
