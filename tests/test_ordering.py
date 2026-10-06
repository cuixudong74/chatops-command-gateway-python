"""Requirement 2: out-of-order delivery (interaction before command)."""
from tests.conftest import make_button, make_command
import time


def test_approve_arrives_before_command_then_replayed(post, client):
    # 1. approval button delivered first; command not yet known.
    early = post(make_button("early-b", command_id="future-c", user="boss"))
    assert early.status_code == 200
    assert early.json()["status"] == "buffered"

    # 2. command message arrives later; buffered approval must replay and
    #    drive the command all the way to SUCCEEDED.
    r = post(make_command(event_id="future-e", command_id="future-c"))
    assert r.status_code == 200
    assert r.json()["buffered_interactions"] == 1
    time.sleep(0.3)
    status = client.get("/commands/future-c").json()
    assert status["status"] == "SUCCEEDED"


def test_duplicate_early_interaction_not_buffered_twice(post, client):
    btn = make_button("dup-b", command_id="future-c2", user="boss")
    post(btn)
    again = post(btn)
    assert again.json()["status"] == "duplicate"
    r = post(make_command(event_id="future-e2", command_id="future-c2"))
    assert r.json()["buffered_interactions"] == 1
