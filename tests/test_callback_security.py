"""Requirement 1: source/signature verification; failures are locatable."""
import time

from app.security import compute_signature

from .conftest import SECRET, envelope, post_callback


def test_valid_callback_accepted(client):
    resp = post_callback(client, envelope())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"


def test_forged_signature_rejected(client):
    resp = post_callback(client, envelope(), signature="0" * 64)
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_signature"


def test_tampered_body_rejected(client):
    payload = envelope()
    import json as _json
    body = _json.dumps(payload).encode()
    ts = str(time.time())
    sig = compute_signature(SECRET, ts, body)
    resp = post_callback(client, payload, timestamp=ts, signature=sig,
                         raw_body=body + b" ")
    assert resp.status_code == 401


def test_stale_timestamp_rejected(client):
    old = str(time.time() - 3600)
    resp = post_callback(client, envelope(), timestamp=old)
    assert resp.status_code == 401
    assert resp.json()["error"] == "stale_request"


def test_unknown_workspace_rejected(client):
    resp = post_callback(client, envelope(), workspace="wsX", secret=SECRET)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "unknown_workspace"


def test_rejection_is_audited_and_locatable(client):
    post_callback(client, envelope(delivery_id="dEvil", command_id="cEvil"),
                  signature="0" * 64)
    audit = client.get("/commands/cEvil/audit").json()["audit"]
    assert len(audit) == 1
    assert audit[0]["event"] == "request_rejected"
    assert audit[0]["delivery_id"] == "dEvil"
    assert audit[0]["detail"]["error"] == "invalid_signature"


def test_bad_request_does_not_affect_legit_requests(client):
    post_callback(client, envelope(delivery_id="dBad", command_id="cBad"),
                  signature="0" * 64)
    resp = post_callback(client, envelope(delivery_id="dGood", command_id="cGood"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"
