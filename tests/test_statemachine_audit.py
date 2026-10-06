"""Requirements 3 & 7: legal transitions only; complete tamper-proof audit."""
import sqlite3

import pytest

from app.errors import IllegalTransition
from app.models import CommandRecord, CommandStatus
from app.storage import Store

from .conftest import envelope, post_callback


def _rec(cid, status):
    return CommandRecord(cid, "ws1", "deploy", {}, status, "alice", 1.0, 1.0)


def test_illegal_jumps_rejected():
    store = Store(":memory:")
    store.insert_command(_rec("c1", CommandStatus.PENDING_APPROVAL))
    for dst in (CommandStatus.EXECUTING, CommandStatus.SUCCEEDED,
                CommandStatus.RECEIVED, CommandStatus.SUBMITTED):
        with pytest.raises(IllegalTransition):
            store.transition("c1", CommandStatus.PENDING_APPROVAL, dst, 2.0)
    # state untouched by failed attempts
    assert store.get_command("c1").status == CommandStatus.PENDING_APPROVAL


def test_terminal_states_are_final():
    store = Store(":memory:")
    for i, terminal in enumerate((CommandStatus.REJECTED, CommandStatus.SUCCEEDED,
                                  CommandStatus.FAILED)):
        cid = f"c{i}"
        store.insert_command(_rec(cid, terminal))
        for dst in CommandStatus:
            with pytest.raises(IllegalTransition):
                store.transition(cid, terminal, dst, 2.0)


def test_full_happy_path_chain():
    store = Store(":memory:")
    store.insert_command(_rec("c1", CommandStatus.RECEIVED))
    chain = [(CommandStatus.RECEIVED, CommandStatus.SUBMITTED),
             (CommandStatus.SUBMITTED, CommandStatus.PENDING_APPROVAL),
             (CommandStatus.PENDING_APPROVAL, CommandStatus.APPROVED),
             (CommandStatus.APPROVED, CommandStatus.EXECUTING),
             (CommandStatus.EXECUTING, CommandStatus.SUCCEEDED)]
    for src, dst in chain:
        rec = store.transition("c1", src, dst, 2.0)
        assert rec.status == dst


def test_reject_path(client, executor):
    post_callback(client, envelope(delivery_id="d1", command_id="c1"))
    resp = post_callback(client, envelope(
        delivery_id="d2", command_id="c1", event_type="button_interaction",
        actor_id="carol", action="reject"))
    assert resp.json()["status"] == "rejected"
    assert executor.calls == []
    # a later approval attempt cannot resurrect it
    resp = post_callback(client, envelope(
        delivery_id="d3", command_id="c1", event_type="button_interaction",
        actor_id="dave", action="approve"))
    assert resp.json()["status"] == "rejected"
    assert executor.calls == []


def test_audit_trail_complete_and_ordered(client):
    post_callback(client, envelope(delivery_id="d1", command_id="c1"))
    post_callback(client, envelope(
        delivery_id="d2", command_id="c1", event_type="button_interaction",
        actor_id="carol", action="approve"))
    audit = client.get("/commands/c1/audit").json()["audit"]
    transitions = [(a["detail"]["from"], a["detail"]["to"])
                   for a in audit if a["event"] == "transition"]
    assert transitions == [
        ("received", "submitted"),
        ("submitted", "pending_approval"),
        ("pending_approval", "approved"),
        ("approved", "executing"),
        ("executing", "succeeded"),
    ]
    for a in audit:
        assert a["ts"] > 0 and a["command_id"] == "c1" and a["seq"] > 0
    actors = {a["actor_id"] for a in audit}
    assert "alice" in actors and "carol" in actors


def test_audit_log_is_append_only(service):
    from app.models import AuditEvent
    service._store.append_audit(AuditEvent(0, 1.0, "c1", "d1", "u1", "e", {}))
    conn = service._store._conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE audit SET actor_id='mallory'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM audit")
    # original record intact
    rows = service._store.list_audit("c1")
    assert len(rows) == 1 and rows[0].actor_id == "u1"
