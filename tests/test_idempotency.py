"""Requirement 2: retries/duplicates/out-of-order never double-execute."""
from .conftest import envelope, post_callback


def _submit(client, delivery="d1", command="c1"):
    return post_callback(client, envelope(delivery_id=delivery, command_id=command)).json()


def _approve(client, delivery, command="c1", actor="carol"):
    return post_callback(client, envelope(
        delivery_id=delivery, command_id=command,
        event_type="button_interaction", actor_id=actor, action="approve")).json()


def test_same_delivery_repeated_is_duplicate(client, executor):
    assert _submit(client)["status"] == "pending_approval"
    for _ in range(3):
        resp = _submit(client)
        assert resp["duplicate"] is True
        assert resp["status"] == "pending_approval"
    _approve(client, "dA")
    assert executor.calls == ["c1"]  # exactly one side effect


def test_same_command_new_delivery_ids_still_idempotent(client, executor):
    _submit(client, "d1")
    # Platform retries the same logical command under fresh delivery ids.
    for d in ("d2", "d3", "d4"):
        resp = _submit(client, d)
        assert resp["duplicate"] is True
    _approve(client, "dA")
    assert executor.calls == ["c1"]


def test_duplicate_approval_delivery_executes_once(client, executor):
    _submit(client)
    first = _approve(client, "dA")
    assert first["status"] == "succeeded" and first["duplicate"] is False
    # exact same delivery retried by the platform
    again = _approve(client, "dA")
    assert again["duplicate"] is True
    # different approver, new delivery, after completion
    late = _approve(client, "dB", actor="dave")
    assert late["duplicate"] is True
    assert executor.calls == ["c1"]


def test_out_of_order_approval_before_command(client, executor):
    # Approval arrives before the command message (reordering in transit).
    early = _approve(client, "dA")
    assert early["status"] == "unknown"  # no state corrupted, nothing executed
    _submit(client, "d1")
    # The retried approval now lands correctly.
    ok = _approve(client, "dA2")
    assert ok["status"] == "succeeded"
    assert executor.calls == ["c1"]


def test_first_vs_duplicate_results_distinguishable(client):
    first = _submit(client, "d1")
    dup = _submit(client, "d1")
    assert first["duplicate"] is False
    assert dup["duplicate"] is True
    assert first["status"] == dup["status"]  # same observable state
