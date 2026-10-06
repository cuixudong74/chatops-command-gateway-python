"""Requirement 4: concurrent ops keep state consistent; no double execution."""
import threading
import time

from app.models import CallbackEnvelope, EventType

from .conftest import RecordingExecutor


def _env(did, cid, etype, actor, action):
    return CallbackEnvelope(did, etype, "ws1", actor, cid, action, {})


def test_concurrent_approvals_single_execution(service, executor):
    service.handle_callback(_env("d0", "c1", EventType.COMMAND_MESSAGE,
                                 "alice", "deploy"))
    results, errors = [], []

    def approve(i):
        try:
            r = service.handle_callback(_env(
                f"dA{i}", "c1", EventType.BUTTON_INTERACTION,
                "carol" if i % 2 == 0 else "dave", "approve"))
            results.append(r)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=approve, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    winners = [r for r in results if not r["duplicate"]]
    losers = [r for r in results if r["duplicate"]]
    assert len(winners) == 1, "exactly one approver wins the race"
    assert len(losers) == 9
    assert executor.calls == ["c1"], "side effect ran exactly once"
    rec = service._store.get_command("c1")
    assert rec.status.value == "succeeded"
    assert rec.approved_by in ("carol", "dave")
    # audit trail shows one approved transition + conflict records, none lost
    events = [e.event for e in service._store.list_audit("c1")]
    assert events.count("decision_conflict") == 9


def test_concurrent_submit_and_approve(service, executor):
    barrier = threading.Barrier(2)
    out = {}

    def submit():
        barrier.wait()
        out["submit"] = service.handle_callback(
            _env("d0", "c1", EventType.COMMAND_MESSAGE, "alice", "deploy"))

    def approve():
        barrier.wait()
        deadline = time.monotonic() + 5.0
        i = 0
        while time.monotonic() < deadline:
            r = service.handle_callback(_env(
                f"dA{i}", "c1", EventType.BUTTON_INTERACTION, "carol", "approve"))
            i += 1
            if r["status"] == "succeeded":
                out["approve"] = r
                return
            time.sleep(0.01)  # platform-style retry backoff

    t1, t2 = threading.Thread(target=submit), threading.Thread(target=approve)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert service._store.get_command("c1").status.value == "succeeded"
    assert executor.calls == ["c1"]


def test_slow_command_does_not_block_others(settings):
    class SlowExecutor(RecordingExecutor):
        def execute(self, record):
            if record.command_id == "slow":
                time.sleep(0.5)
            return super().execute(record)

    executor = SlowExecutor()
    from app.ratelimit import RateLimiter
    from app.service import GatewayService
    from app.storage import Store
    svc = GatewayService(settings, Store(":memory:"), executor,
                         RateLimiter(1000, 60))

    svc.handle_callback(_env("d0", "slow", EventType.COMMAND_MESSAGE, "alice", "deploy"))
    svc.handle_callback(_env("d1", "fast", EventType.COMMAND_MESSAGE, "bob", "restart"))

    started = time.monotonic()
    t = threading.Thread(target=lambda: svc.handle_callback(
        _env("dA", "slow", EventType.BUTTON_INTERACTION, "carol", "approve")))
    t.start()
    time.sleep(0.05)  # let the slow execution begin
    r = svc.handle_callback(
        _env("dB", "fast", EventType.BUTTON_INTERACTION, "dave", "approve"))
    fast_elapsed = time.monotonic() - started
    t.join()

    assert r["status"] == "succeeded"
    assert fast_elapsed < 0.5, "fast command was blocked by the slow one"
    assert sorted(executor.calls) == ["fast", "slow"]
