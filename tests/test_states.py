"""Requirement 3: legal state transitions only."""
import time

from tests.conftest import make_button, make_command


def _submit(client, post, command_id="sm"):
    r = post(make_command(event_id=command_id + "e", command_id=command_id))
    assert r.status_code == 200


def test_happy_path_states(client, post):
    _submit(client, post)
    assert client.get("/commands/sm").json()["status"] == "PENDING_APPROVAL"
    post(make_button("ap", command_id="sm", user="boss", action="approve"))
    time.sleep(0.2)
    assert client.get("/commands/sm").json()["status"] == "SUCCEEDED"


def test_reject_path_is_terminal(client, post):
    _submit(client, post)
    r = post(make_button("rj", command_id="sm", user="boss", action="reject"))
    assert r.json()["command_status"] == "REJECTED"
    time.sleep(0.2)
    status = client.get("/commands/sm").json()
    assert status["status"] == "REJECTED"
    # A later approve must not resurrect a rejected command.
    late = post(make_button("late", command_id="sm", user="boss"))
    assert late.json()["status"] == "ignored"
    assert client.get("/commands/sm").json()["status"] == "REJECTED"


def test_approve_terminal_command_ignored(client, post):
    _submit(client, post)
    post(make_button("ap", command_id="sm", user="boss"))
    time.sleep(0.2)
    late = post(make_button("ap2", command_id="sm", user="boss"))
    # Duplicate event is caught by idempotency; using a new event it is
    # ignored because the command is already terminal.
    assert late.json()["status"] in ("duplicate", "ignored")


def test_unknown_interaction_action_rejected(client, post):
    _submit(client, post, command_id="sx")
    weird = make_button("w", command_id="sx", action="explode")
    r = post(weird)
    assert r.status_code == 403
    assert r.json()["error"] == "approval_not_allowed"
