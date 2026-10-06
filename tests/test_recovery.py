"""Requirement 6: recovery after restart at any step."""
import threading
import time

from fastapi.testclient import TestClient

from gateway.actions import ActionResult, register_action
from gateway.app import create_app
from tests.conftest import make_button, make_command


def _new_client(settings):
    app = create_app(settings)
    return TestClient(app)


def test_pending_approval_survives_restart(settings, post, client):
    post(make_command(event_id="rp", command_id="rp-c"))
    # Restart the whole service (new app instance, same database file).
    client.__exit__(None, None, None)
    c2 = _new_client(settings)
    c2.__enter__()
    try:
        status = c2.get("/commands/rp-c").json()
        assert status["status"] == "PENDING_APPROVAL"
        # Approval still possible after restart and leads to execution.
        from tests.conftest import signed_post
        r = signed_post(c2, make_button("rp-b", command_id="rp-c", user="boss"))
        assert r.status_code == 200
        time.sleep(0.2)
        assert c2.get("/commands/rp-c").json()["status"] == "SUCCEEDED"
    finally:
        c2.__exit__(None, None, None)


def test_executing_command_resumes_after_crash(settings, post, client):
    """A command left EXECUTING by a crash is rolled back and re-executed."""
    call_count = threading.Event()
    calls = []
    lock = threading.Lock()

    def crashy_then_ok(command_name, args, command_id):
        # Simulate: the first process "applied the side effect" but crashed
        # before persisting. On the resumed attempt the handler is invoked
        # again and must idempotently report success.
        with lock:
            calls.append(command_id)
        return ActionResult(True, {"attempt": len(calls)})

    register_action("crash_cmd", crashy_then_ok)
    post(make_command(event_id="rx", command_id="rx-c", command="crash_cmd"))
    post(make_button("rx-b", command_id="rx-c", user="boss"))
    time.sleep(0.3)
    assert client.get("/commands/rx-c").json()["status"] == "SUCCEEDED"

    # Simulate a crash mid-execution: flip status back to EXECUTING.
    service = client.app.state.service
    service.db.execute("UPDATE commands SET status='EXECUTING' WHERE command_id='rx-c'")
    client.__exit__(None, None, None)

    c2 = _new_client(settings)
    c2.__enter__()
    try:
        time.sleep(0.5)
        status = c2.get("/commands/rx-c").json()
        assert status["status"] == "SUCCEEDED"
        audit = c2.get("/commands/rx-c").json()["audit"]
        events = [a["event"] for a in audit]
        assert "recovered_executing" in events
        assert "recover_resume_execution" in events
        # Handler was re-invoked at least once (idempotent retry).
        assert len(calls) >= 2
    finally:
        c2.__exit__(None, None, None)


def test_terminal_command_not_retriggered(settings, post, client):
    post(make_command(event_id="rt", command_id="rt-c"))
    post(make_button("rt-b", command_id="rt-c", user="boss"))
    time.sleep(0.2)
    assert client.get("/commands/rt-c").json()["status"] == "SUCCEEDED"
    started_audit_before = [
        a for a in client.get("/commands/rt-c").json()["audit"]
        if a["event"] == "execution_started"
    ]
    client.__exit__(None, None, None)

    c2 = _new_client(settings)
    c2.__enter__()
    try:
        time.sleep(0.3)
        status = c2.get("/commands/rt-c").json()
        assert status["status"] == "SUCCEEDED"
        starts = [a for a in status["audit"]
                  if a["event"] == "execution_started"]
        assert len(starts) == len(started_audit_before) == 1
    finally:
        c2.__exit__(None, None, None)
