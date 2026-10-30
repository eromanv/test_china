import json

import pytest

from app.db import get_db
from tests.conftest import event_body


def test_duplicate_webhook_keeps_one_event_and_one_task(client, app):
    first = client.post("/webhook", json=event_body())
    second = client.post("/webhook", json=event_body(payload={"amount": 99}))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json() == {"status": "queued", "event_id": "abc-123"}
    assert second.get_json() == {"status": "duplicate", "event_id": "abc-123"}

    with app.app_context():
        db = get_db()
        events = db.execute("SELECT event_id, type, payload FROM events").fetchall()
        tasks = db.execute("SELECT event_id, status FROM tasks").fetchall()

    assert len(events) == 1
    assert events[0]["event_id"] == "abc-123"
    assert json.loads(events[0]["payload"]) == {"amount": 10}
    assert len(tasks) == 1
    assert tasks[0]["event_id"] == "abc-123"
    assert tasks[0]["status"] == "pending"


@pytest.mark.parametrize(
    "send",
    [
        lambda client: client.post(
            "/webhook", data="not-json", content_type="application/json"
        ),
        lambda client: client.post(
            "/webhook",
            json={"type": "payment_succeeded", "payload": {}},
        ),
        lambda client: client.post(
            "/webhook",
            json={"event_id": "x", "type": "payment_succeeded", "payload": [1, 2]},
        ),
        lambda client: client.post(
            "/webhook", data="[]", content_type="application/json"
        ),
        lambda client: client.post(
            "/webhook",
            json={"event_id": "  ", "type": "payment_succeeded", "payload": {}},
        ),
        lambda client: client.post(
            "/webhook",
            json={"event_id": "x", "type": "payment_succeeded"},
        ),
    ],
)
def test_invalid_body_is_400(client, send):
    resp = send(client)
    assert resp.status_code == 400
    assert isinstance(resp.get_json()["error"], str)
    assert resp.get_json()["error"]
