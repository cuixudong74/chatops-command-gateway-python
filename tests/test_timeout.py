"""Requirement 4/6: action timeout rolls back to APPROVED and retries.

A timeout means the outcome is unknown, so the command must never be marked
FAILED; it returns to APPROVED and is retried. The handler is idempotent on
command_id, so being invoked more than once is safe.
"""
import threading
import time

from gateway.actions import ActionResult, register_action
from tests.conftest import make_button, make_command


def test_timeout_then_retry_succeeds(client, post, settings):
    calls = []
    lock = threading.Lock()

    def flaky(command_name, args, command_id):
        with lock:
            n = len(calls) + 1
            calls.append(n)
        if n == 1:
            # First attempt deliberately exceeds the timeout. The thread
            # keeps sleeping afterwards (simulating an unknown outcome),
            # while the command is already being retried.
            time.sleep(1.0)
        return ActionResult(True, {"attempt": n})

    register_action("flaky_cmd", flaky)
    settings.action_timeout_seconds = 0.2
    post(make_command(event_id="ft", command_id="ft-c",
                      command="flaky_cmd"))
    post(make_button("ft-b", command_id="ft-c", user="boss"))
    # First attempt times out -> rollback + retry; second attempt is fast.
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get("/commands/ft-c").json()
        if status["status"] == "SUCCEEDED":
            break
        time.sleep(0.05)
    status = client.get("/commands/ft-c").json()
    assert status["status"] == "SUCCEEDED", status["status"]
    assert len(calls) >= 2
    audit = client.get("/commands/ft-c").json()["audit"]
    assert any(a["event"] == "execution_timeout_retry" for a in audit)
