"""Requirement 5: rate limiting does not punish legitimate concurrency."""
import threading
import time

from tests.conftest import make_command


def test_over_limit_rejected_with_distinct_reason(client, post):
    # Fixture allows 5 user requests per minute.
    statuses = [
        post(make_command(event_id=f"rl{i}", command_id=f"rl-c{i}",
                          user="heavy", role="admin")).status_code
        for i in range(7)
    ]
    assert statuses.count(200) == 5
    limited = [s for s in statuses if s == 429]
    assert limited  # at least one rejection
    # The rejection body distinguishes the reason.
    r = post(make_command(event_id="rl8", command_id="rl-c8", user="heavy"))
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited"
    assert "user:w1:heavy" in r.json()["message"]


def test_other_user_not_affected(client, post):
    for i in range(6):
        post(make_command(event_id=f"h{i}", command_id=f"h-c{i}", user="heavy2"))
    # A different user still has a fresh budget.
    r = post(make_command(event_id="f", command_id="f-c", user="fresh"))
    assert r.status_code == 200


def test_concurrent_legit_requests_not_overshaped(client, post):
    """Exactly the configured number pass under concurrency."""
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def hit(i):
        barrier.wait()
        r = post(make_command(event_id=f"cc{i}", command_id=f"cc-c{i}",
                              user="burst"))
        with lock:
            results.append(r.status_code)

    ts = [threading.Thread(target=hit, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results.count(200) == 5
    assert results.count(429) == 5
