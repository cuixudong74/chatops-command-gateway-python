"""Requirement 7: full-chain, immutable, queryable audit trail."""
import time

from tests.conftest import make_button, make_command


def test_audit_captures_full_chain(client, post):
    post(make_command(event_id="a1", command_id="a1-c", user="alice"))
    post(make_button("a2", command_id="a1-c", user="boss"))
    time.sleep(0.2)
    status = client.get("/commands/a1-c").json()
    events = [(a["event"], a["actor"], a["related_id"]) for a in status["audit"]]
    names = [e[0] for e in events]
    assert names == [
        "command_submitted",
        "command_approved",
        "execution_started",
        "execution_succeeded",
    ]
    # Timestamps and actors are populated.
    for entry in status["audit"]:
        assert entry["ts"] > 0
        assert entry["actor"]
    assert events[0][2] == "a1"
    assert events[1][2] == "a2"


def test_audit_entries_are_append_only(client, post, service):
    post(make_command(event_id="i1", command_id="i1-c"))
    time.sleep(0.1)
    before = service.audit.list_for_command("i1-c")
    assert len(before) == 1
    # Further operations only add entries; original entry is unchanged.
    post(make_button("i2", command_id="i1-c", user="boss"))
    time.sleep(0.2)
    after = service.audit.list_for_command("i1-c")
    assert len(after) > len(before)
    assert after[0] == before[0]
    # Audit rows expose no update/delete API; attempt direct tamper is
    # prevented by the append-only interface.
    assert not hasattr(service.audit, "update")
    assert not hasattr(service.audit, "delete")
