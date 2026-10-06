"""Requirement 4: concurrent operations keep state consistent."""
import threading
import time

from gateway.actions import register_action
from tests.conftest import make_button, make_command


def test_concurrent_approvals_only_one_succeeds(client, post):
    """Many approvers at once: command is approved exactly once."""
    post(make_command(event_id="e", command_id="cc", user="alice"))
    outcomes = []
    barrier = threading.Barrier(8)

    def approve(i):
        barrier.wait()
        r = post(make_button(f"b{i}", command_id="cc",
                             user="boss" if i % 2 == 0 else "boss2"))
        outcomes.append(r.json())

    threads = [threading.Thread(target=approve, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [o for o in outcomes if o.get("status") == "ok"
           and o.get("command_status") == "APPROVED"]
    assert len(oks) == 1, outcomes
    # The rest were ignored because state had already moved.
    ignored = [o for o in outcomes if o.get("status") == "ignored"]
    assert len(ignored) == 7
    time.sleep(0.3)
    status = client.get("/commands/cc").json()
    assert status["status"] == "SUCCEEDED"
    starts = [a for a in status["audit"] if a["event"] == "execution_started"]
    assert len(starts) == 1


def test_slow_action_does_not_block_other_commands(client, post, settings):
    started = threading.Event()
    finish = threading.Event()

    def slow_handler(command_name, args, command_id):
        started.set()
        finish.wait(timeout=5)
        from gateway.actions import ActionResult

        return ActionResult(True, {"done": True})

    register_action("slow_cmd", slow_handler)
    try:
        post(make_command(event_id="slow-e", command_id="slow-c",
                          command="slow_cmd"))
        post(make_button("slow-b", command_id="slow-c", user="boss"))
        # Wait until the slow action is actually running.
        assert started.wait(timeout=2)

        # While it is blocked, an unrelated fast command completes fully.
        post(make_command(event_id="fast-e", command_id="fast-c",
                          command="status", user="bob"))
        post(make_button("fast-b", command_id="fast-c", user="boss"))
        time.sleep(0.3)
        fast = client.get("/commands/fast-c").json()
        assert fast["status"] == "SUCCEEDED"
        slow = client.get("/commands/slow-c").json()
        assert slow["status"] == "EXECUTING"
    finally:
        finish.set()
    time.sleep(0.3)
    assert client.get("/commands/slow-c").json()["status"] == "SUCCEEDED"


def test_concurrent_command_retries_serialised(client, post):
    payload = make_command(event_id="one", command_id="one-c")
    results = []

    def send():
        results.append(post(payload).json()["status"])

    threads = [threading.Thread(target=send) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("accepted") == 1
    assert results.count("duplicate") == 5
