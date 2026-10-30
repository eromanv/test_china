from app.worker import process_once
from tests.conftest import event_body


def test_stats_counts_types_and_statuses(client, app):
    empty = client.get("/stats")
    assert empty.status_code == 200
    assert empty.get_json() == {
        "events_by_type": {},
        "tasks_by_status": {
            "pending": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        },
    }

    client.post("/webhook", json=event_body("a", "payment_succeeded"))
    client.post("/webhook", json=event_body("b", "payment_succeeded"))
    client.post("/webhook", json=event_body("c", "user_registered"))
    client.post("/webhook", json=event_body("d", "other"))

    with app.app_context():
        assert process_once(now=10_000) is not None
        app.fail_state["fail"] = True
        assert process_once(now=10_000) is not None
        app.fail_state["fail"] = False
        assert process_once(now=10_000) is not None
        assert process_once(now=10_000) is not None

    resp = client.get("/stats")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "events_by_type": {
            "payment_succeeded": 2,
            "user_registered": 1,
            "other": 1,
        },
        "tasks_by_status": {
            "pending": 1,
            "success": 2,
            "failed": 0,
            "skipped": 1,
        },
    }
