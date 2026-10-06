"""Requirement 2: retries/duplicates never repeat side effects."""
import time

from tests.conftest import make_button, make_command


def test_duplicate_command_delivered_many_times_runs_once(client, post):
    payload = make_command(event_id="dup", command_id="cmd-dup")
    results = [post(payload) for _ in range(10)]
    assert all(r.status_code == 200 for r in results)
    first = results[0].json()
    assert first["status"] == "accepted"
    assert all(r.json()["status"] == "duplicate" for r in results[1:])

    # Exactly one command row and one submission audit entry.
    status = client.get("/commands/cmd-dup").json()
    submits = [a for a in status["audit"] if a["event"] == "command_submitted"]
    dupes = [a for a in status["audit"] if a["event"] == "duplicate_callback"]
    assert len(submits) == 1
    assert len(dupes) == 9


def test_duplicate_approval_ignored(client, post):
    post(make_command(event_id="c", command_id="cc"))
    approve = make_button("b1", command_id="cc", user="boss")
    r1 = post(approve)
    r2 = post(approve)
    assert r1.json()["status"] == "ok"
    assert r2.json()["status"] == "duplicate"
    time.sleep(0.2)
    status = client.get("/commands/cc").json()
    assert status["status"] == "SUCCEEDED"
    # Exactly one execution.
    starts = [a for a in status["audit"] if a["event"] == "execution_started"]
    assert len(starts) == 1


def test_first_vs_duplicate_distinguishable(client, post):
    payload = make_command(event_id="dd", command_id="cd")
    first = post(payload).json()
    second = post(payload).json()
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["command_id"] == "cd"
    assert "not repeated" in second["message"]


def test_same_command_id_different_event_not_recreated(client, post):
    p1 = make_command(event_id="ev-a", command_id="same-cmd", user="alice")
    p2 = make_command(event_id="ev-b", command_id="same-cmd", user="alice")
    r1 = post(p1)
    r2 = post(p2)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["status"] == "accepted"
    assert r2.json()["status"] == "already_exists"
    # Both are accepted as callbacks, but only one command row/execution exists.
    post(make_button("ev-ap", command_id="same-cmd", user="boss"))
    import time as _t

    _t.sleep(0.2)
    status = client.get("/commands/same-cmd").json()
    submits = [a for a in status["audit"] if a["event"] == "command_submitted"]
    resubmits = [
        a for a in status["audit"] if a["event"] == "command_resubmit_ignored"
    ]
    starts = [a for a in status["audit"] if a["event"] == "execution_started"]
    assert len(submits) == 1
    assert len(resubmits) == 1
    assert len(starts) == 1
    assert status["status"] == "SUCCEEDED"
