"""Requirement 1: source/signature verification."""
import json
import time

from gateway.security import compute_signature


def test_valid_signature_accepted(client, post):
    r = post({"event_id": "x", "type": "command", "workspace_id": "w1",
              "user_id": "alice", "role": "admin",
              "timestamp": int(time.time()), "command_id": "cs",
              "command_name": "restart_service"})
    assert r.status_code == 200


def test_missing_signature_rejected_and_logged(client):
    r = client.post("/callback", content=b"{}",
                    headers={"X-Timestamp": str(int(time.time()))})
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "invalid_signature"
    assert "request_id" in body
    logs = client.get("/security-log").json()
    assert logs[0]["request_id"] == body["request_id"]
    assert logs[0]["reason"] == "invalid_signature"


def test_bad_signature_rejected(client):
    ts = str(int(time.time()))
    r = client.post("/callback", content=b"{}",
                    headers={"X-Timestamp": ts, "X-Signature": "forged"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_signature"


def test_wrong_secret_rejected(client):
    ts = str(int(time.time()))
    body = json.dumps({"event_id": "y"}).encode()
    sig = compute_signature("attacker-secret", ts, body)
    r = client.post("/callback", content=body,
                    headers={"X-Timestamp": ts, "X-Signature": sig})
    assert r.status_code == 401


def test_expired_timestamp_rejected(client):
    old = int(time.time()) - 10_000
    body = b"{}"
    ts = str(old)
    sig = compute_signature("test-secret", ts, body)
    r = client.post("/callback", content=body,
                    headers={"X-Timestamp": ts, "X-Signature": sig})
    assert r.status_code == 401
    assert r.json()["error"] == "timestamp_expired"


def test_replayed_request_rejected(client):
    # A captured valid request replayed far outside the tolerance window.
    ts = str(int(time.time()) - 9999)
    body = b'{"event_id":"old"}'
    sig = compute_signature("test-secret", ts, body)
    r = client.post("/callback", content=body,
                    headers={"X-Timestamp": ts, "X-Signature": sig})
    assert r.status_code == 401
    assert r.json()["error"] == "timestamp_expired"


def test_signature_failure_does_not_affect_valid_request(client, post):
    # Illegal request first.
    bad = client.post("/callback", content=b"{}",
                      headers={"X-Timestamp": str(int(time.time())),
                               "X-Signature": "nope"})
    assert bad.status_code == 401
    # A legal request right after still succeeds.
    r = post({"event_id": "ok1", "type": "command", "workspace_id": "w1",
              "user_id": "a", "role": "admin", "timestamp": int(time.time()),
              "command_id": "okc", "command_name": "status"})
    assert r.status_code == 200
