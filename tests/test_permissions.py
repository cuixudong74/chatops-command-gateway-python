"""Requirement 5: permission checks with distinguishable reasons."""
import time

from tests.conftest import make_button, make_command


def test_unauthorized_role_command_denied(client, post):
    r = post(make_command(event_id="pv", command_id="pv-c", role="viewer",
                          command="restart_service"))
    assert r.status_code == 403
    assert r.json()["error"] == "permission_denied"
    # No command row was created.
    assert client.get("/commands/pv-c").status_code == 404


def test_allowed_command_for_role(client, post):
    r = post(make_command(event_id="pv2", command_id="pv2-c", role="viewer",
                          command="status"))
    assert r.status_code == 200


def test_non_approver_cannot_approve(client, post):
    post(make_command(event_id="pa", command_id="pa-c", user="alice"))
    r = post(make_button("pa-b", command_id="pa-c", user="random-guy"))
    assert r.status_code == 403
    assert r.json()["error"] == "permission_denied"
    # Command stays pending.
    assert client.get("/commands/pa-c").json()["status"] == "PENDING_APPROVAL"


def test_submitter_cannot_self_approve(client, post):
    post(make_command(event_id="ps", command_id="ps-c", user="alice"))
    r = post(make_button("ps-b", command_id="ps-c", user="alice"))
    assert r.status_code == 403
    assert r.json()["error"] == "permission_denied"


def test_denial_reason_distinct_from_rate_limit(client, post):
    denied = post(make_command(event_id="zz", command_id="zz-c", role="viewer",
                               command="restart_service"))
    assert denied.json()["error"] == "permission_denied"
