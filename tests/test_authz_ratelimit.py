"""Requirement 5: permission & rate limits; rejection reasons distinguishable."""
from .conftest import envelope, post_callback


def test_unauthorized_submitter_rejected(client):
    resp = post_callback(client, envelope(actor_id="mallory"))
    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"


def test_unauthorized_approver_rejected(client):
    post_callback(client, envelope(delivery_id="d1", command_id="c1"))
    resp = post_callback(client, envelope(
        delivery_id="d2", command_id="c1", event_type="button_interaction",
        actor_id="alice", action="approve"))  # alice may submit, not approve
    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"
    # command untouched
    assert client.get("/commands/c1").json()["status"] == "pending_approval"


def test_rate_limit_rejects_with_distinct_reason(client):
    # limit is 5 per window per (workspace, user)
    for i in range(5):
        resp = post_callback(client, envelope(delivery_id=f"d{i}",
                                              command_id=f"c{i}"))
        assert resp.status_code == 200
    resp = post_callback(client, envelope(delivery_id="d6", command_id="c6"))
    assert resp.status_code == 429
    assert resp.json()["error"] == "rate_limited"


def test_rate_limit_does_not_hurt_other_users(client):
    for i in range(5):
        post_callback(client, envelope(delivery_id=f"d{i}", command_id=f"c{i}",
                                       actor_id="alice"))
    assert post_callback(client, envelope(delivery_id="dx", command_id="cx",
                                          actor_id="alice")).status_code == 429
    # bob (same workspace) is unaffected
    resp = post_callback(client, envelope(delivery_id="dy", command_id="cy",
                                          actor_id="bob"))
    assert resp.status_code == 200


def test_duplicate_deliveries_do_not_consume_quota(client):
    post_callback(client, envelope(delivery_id="d1", command_id="c1"))
    # hammering the same delivery 20 times must not trip the limiter
    for _ in range(20):
        resp = post_callback(client, envelope(delivery_id="d1", command_id="c1"))
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is True
    # and the legit quota is still intact
    resp = post_callback(client, envelope(delivery_id="d2", command_id="c2"))
    assert resp.status_code == 200


def test_rejection_reasons_are_distinguishable(client):
    forbidden = post_callback(client, envelope(actor_id="mallory"))
    stale = post_callback(client, envelope(), timestamp="1")
    forged = post_callback(client, envelope(), signature="0" * 64)
    codes = {r.json()["error"] for r in (forbidden, stale, forged)}
    assert codes == {"permission_denied", "stale_request", "invalid_signature"}
