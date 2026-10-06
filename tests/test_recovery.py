"""Requirement 6: restart recovery; no double side effects, no half state."""
import time

from app.models import CallbackEnvelope, CommandRecord, CommandStatus, EventType
from app.ratelimit import RateLimiter
from app.service import GatewayService
from app.storage import Store

from .conftest import RecordingExecutor


def _env(did, cid, etype, actor, action):
    return CallbackEnvelope(did, etype, "ws1", actor, cid, action, {})


def _service(settings, db_path, executor):
    return GatewayService(settings, Store(db_path), executor,
                          RateLimiter(1000, 60), clock=time.time)


def test_pending_approval_survives_restart(settings, tmp_path):
    db = str(tmp_path / "g.db")
    svc1 = _service(settings, db, RecordingExecutor())
    svc1.handle_callback(_env("d1", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    svc1._store.close()  # simulate shutdown

    svc2 = _service(settings, db, RecordingExecutor())
    resumed = svc2.recover()
    assert [r.command_id for r in resumed] == ["c1"]
    assert svc2._store.get_command("c1").status == CommandStatus.PENDING_APPROVAL
    # the approval flow continues where it left off
    r = svc2.handle_callback(_env("d2", "c1", EventType.BUTTON_INTERACTION,
                                  "carol", "approve"))
    assert r["status"] == "succeeded"


def test_crash_after_approval_recovers_and_executes_once(settings, tmp_path):
    db = str(tmp_path / "g.db")
    store = Store(db)
    # Simulate a crash right after APPROVED was persisted but before the
    # executor ran (or mid-execution): seed the durable state directly.
    store.insert_command(CommandRecord(
        "c1", "ws1", "deploy", {}, CommandStatus.APPROVED,
        "alice", 1.0, 1.0, approved_by="carol"))
    store.close()

    executor = RecordingExecutor()
    svc = _service(settings, db, executor)
    svc.recover()
    assert executor.calls == ["c1"]
    assert svc._store.get_command("c1").status == CommandStatus.SUCCEEDED
    # a second recover (e.g. crash loop) must not re-execute
    svc.recover()
    assert executor.calls == ["c1"]


def test_completed_commands_never_retriggered(settings, tmp_path):
    db = str(tmp_path / "g.db")
    executor1 = RecordingExecutor()
    svc1 = _service(settings, db, executor1)
    svc1.handle_callback(_env("d1", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    svc1.handle_callback(_env("d2", "c1", EventType.BUTTON_INTERACTION, "carol", "approve"))
    assert executor1.calls == ["c1"]
    svc1._store.close()

    executor2 = RecordingExecutor()
    svc2 = _service(settings, db, executor2)
    assert svc2.recover() == []          # nothing resumable
    assert executor2.calls == []         # no re-execution
    # duplicate deliveries after restart are still deduped (deliveries persisted)
    r = svc2.handle_callback(_env("d1", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    assert r["duplicate"] is True
    assert executor2.calls == []


def test_failed_execution_is_terminal(settings, tmp_path):
    db = str(tmp_path / "g.db")
    executor = RecordingExecutor(fail_commands={"c1"})
    svc = _service(settings, db, executor)
    svc.handle_callback(_env("d1", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    r = svc.handle_callback(_env("d2", "c1", EventType.BUTTON_INTERACTION, "carol", "approve"))
    assert r["status"] == "failed"
    rec = svc._store.get_command("c1")
    assert rec.result == {"error": "boom"}
    assert svc.recover() == []  # terminal: not retried blindly


def test_audit_trail_consistent_across_restart(settings, tmp_path):
    db = str(tmp_path / "g.db")
    svc1 = _service(settings, db, RecordingExecutor())
    svc1.handle_callback(_env("d1", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    svc1._store.close()

    svc2 = _service(settings, db, RecordingExecutor())
    svc2.recover()
    svc2.handle_callback(_env("d2", "c1", EventType.BUTTON_INTERACTION, "carol", "approve"))
    audit = svc2._store.list_audit("c1")
    seqs = [e.seq for e in audit]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    events = [e.event for e in audit]
    assert "command_created" in events and "transition" in events
