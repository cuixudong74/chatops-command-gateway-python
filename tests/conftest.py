import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.models import CommandRecord
from app.ratelimit import RateLimiter
from app.security import compute_signature
from app.service import GatewayService
from app.storage import Store

SECRET = "test-secret"


class RecordingExecutor:
    """Thread-safe stub executor recording every executed command."""

    def __init__(self, fail_commands: set[str] | None = None):
        self.calls: list[str] = []
        self.fail_commands = fail_commands or set()
        self._lock = threading.Lock()

    def execute(self, record: CommandRecord) -> dict:
        with self._lock:
            self.calls.append(record.command_id)
        if record.command_id in self.fail_commands:
            raise RuntimeError("boom")
        return {"ok": True, "action": record.action}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        workspace_secrets={"ws1": SECRET},
        max_clock_skew_seconds=60.0,
        submitters={"ws1": frozenset({"alice", "bob"})},
        approvers={"ws1": frozenset({"carol", "dave"})},
        rate_limit_max=5,
        rate_limit_window_seconds=60.0,
        db_path=str(tmp_path / "test.db"),
    )


@pytest.fixture
def executor():
    return RecordingExecutor()


@pytest.fixture
def service(settings, executor):
    return GatewayService(settings, Store(":memory:"), executor,
                          RateLimiter(settings.rate_limit_max,
                                      settings.rate_limit_window_seconds),
                          clock=time.time)


@pytest.fixture
def client(settings, service):
    return TestClient(create_app(service, settings))


def envelope(delivery_id="d1", command_id="c1", event_type="command_message",
             actor_id="alice", action="deploy", args=None, occurred_at=None):
    return {
        "delivery_id": delivery_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "command_id": command_id,
        "action": action,
        "args": args or {},
        "occurred_at": occurred_at if occurred_at is not None else time.time(),
    }


def post_callback(client, payload, workspace="ws1", secret=SECRET,
                  timestamp=None, signature=None, raw_body=None):
    ts = timestamp if timestamp is not None else str(time.time())
    body = raw_body if raw_body is not None else json.dumps(payload).encode()
    sig = signature if signature is not None else compute_signature(secret, ts, body)
    return client.post(
        "/callbacks",
        content=body,
        headers={
            "x-workspace-id": workspace,
            "x-timestamp": ts,
            "x-signature": sig,
            "content-type": "application/json",
        },
    )
