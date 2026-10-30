import time

from app.db import get_db
from app.queue import claim_task, complete, fail
from app.worker import process_once
from tests.conftest import event_body, task_row


def test_successful_processing(client, app):
    client.post("/webhook", json=event_body("ok-1"))

    with app.app_context():
        task_id = process_once(now=1_000)

    row = task_row(app, task_id)
    assert task_id is not None
    assert row["status"] == "success"
    assert row["locked_until"] is None


def test_three_failures_then_failed_and_not_retried(client, app):
    app.fail_state["fail"] = True
    client.post("/webhook", json=event_body("fail-1"))

    now = 1_000
    with app.app_context():
        for expected in (1, 2, 3):
            task_id = process_once(now=now)
            assert task_id is not None
            row = get_db().execute(
                "SELECT status, attempts, next_run_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            assert row["attempts"] == expected
            if expected < 3:
                assert row["status"] == "pending"
                assert row["next_run_at"] == now + 2**expected
                now = row["next_run_at"]
            else:
                assert row["status"] == "failed"

        assert process_once(now=now + 10_000) is None


def test_backoff_skips_until_next_run_at(client, app):
    app.fail_state["fail"] = True
    client.post("/webhook", json=event_body("back-1"))

    with app.app_context():
        assert process_once(now=100) is not None
        row = get_db().execute(
            "SELECT status, attempts, next_run_at, locked_until FROM tasks"
        ).fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        assert row["locked_until"] is None
        assert row["next_run_at"] == 102

        assert process_once(now=101) is None
        task_id = process_once(now=102)
        assert task_id is not None
        row = get_db().execute(
            "SELECT attempts, next_run_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        assert row["attempts"] == 2
        assert row["next_run_at"] == 106


def test_expired_lease_is_claimed_again(client, app):
    client.post("/webhook", json=event_body("lease-1"))
    lease = app.config["LEASE_SECONDS"]

    with app.app_context():
        db = get_db()
        claim = claim_task(db, now=1_000, lease=lease, max_attempts=3)
        assert claim is not None
        task_id, _locked_until = claim
        assert process_once(now=1_000 + lease) is None
        assert process_once(now=1_000 + lease + 1) == task_id
        row = db.execute(
            "SELECT status, attempts FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

    assert row["status"] == "success"
    assert row["attempts"] == 1


def test_repeated_lease_expiry_ends_failed(client, app):
    client.post("/webhook", json=event_body("lease-dead"))
    lease = 10

    with app.app_context():
        db = get_db()
        assert claim_task(db, now=0, lease=lease, max_attempts=3) is not None
        assert claim_task(db, now=11, lease=lease, max_attempts=3) is not None
        assert claim_task(db, now=22, lease=lease, max_attempts=3) is not None
        assert claim_task(db, now=33, lease=lease, max_attempts=3) is None
        row = db.execute("SELECT status, attempts, last_error FROM tasks").fetchone()
        assert process_once(now=10_000) is None

    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"] == "lease expired"


def test_stale_worker_cannot_overwrite_newer_result(client, app):
    client.post("/webhook", json=event_body("race-1"))

    with app.app_context():
        db = get_db()
        first_claim = claim_task(db, now=0, lease=30, max_attempts=3)
        assert first_claim is not None
        task_id, first_lock = first_claim

        second_claim = claim_task(db, now=31, lease=30, max_attempts=3)
        assert second_claim is not None
        _, second_lock = second_claim
        assert complete(db, task_id, "success", 31, second_lock)

        assert fail(db, task_id, "late failure", 32, 3, first_lock) == "stale"
        row = db.execute(
            "SELECT status, attempts, last_error FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

    assert row["status"] == "success"
    assert row["attempts"] == 1
    assert row["last_error"] is None


def test_backoff_starts_when_handler_finishes(client, app):
    app.clock.now = 100

    def slow_failure(_event):
        app.clock.now = 110
        raise RuntimeError("slow failure")

    app.config["EVENT_HANDLER"] = slow_failure
    client.post("/webhook", json=event_body("slow-1"))

    with app.app_context():
        task_id = process_once()
        row = get_db().execute(
            "SELECT status, attempts, next_run_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["next_run_at"] == 112


def test_handler_timeout_eventually_fails_task(client, app):
    def hanging_handler(_event):
        time.sleep(0.1)

    app.config["EVENT_HANDLER"] = hanging_handler
    app.config["HANDLER_TIMEOUT_SECONDS"] = 0.01
    client.post("/webhook", json=event_body("timeout-1"))

    with app.app_context():
        assert process_once(now=100) is not None
        assert process_once(now=102) is not None
        task_id = process_once(now=106)
        row = get_db().execute(
            "SELECT status, attempts, last_error FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"] == "handler timed out after 0.01 seconds"
