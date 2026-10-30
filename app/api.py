import json

from flask import Blueprint, current_app, request

from app.db import get_db, transaction
from app.queue import enqueue

bp = Blueprint("api", __name__)

STATUSES = ("pending", "success", "failed", "skipped")


def _non_empty_str(value):
    return isinstance(value, str) and value.strip() != ""


def _error(message):
    return {"error": message}, 400


@bp.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return _error("тело запроса должно быть JSON-объектом")
    event_id = data.get("event_id")
    event_type = data.get("type")
    payload = data.get("payload")
    if not _non_empty_str(event_id):
        return _error("event_id должен быть непустой строкой")
    if not _non_empty_str(event_type):
        return _error("type должен быть непустой строкой")
    if not isinstance(payload, dict):
        return _error("payload должен быть объектом")

    event_id = event_id.strip()
    event_type = event_type.strip()
    now = current_app.config["CLOCK"]()
    db = get_db()
    with transaction(db):
        cur = db.execute(
            """
            INSERT INTO events (event_id, type, payload, received_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event_id, event_type, json.dumps(payload, ensure_ascii=False), now),
        )
        created = cur.rowcount == 1
        if created:
            enqueue(db, event_id, now)
    if created:
        return {"status": "queued", "event_id": event_id}, 200
    return {"status": "duplicate", "event_id": event_id}, 200


@bp.get("/stats")
def stats():
    db = get_db()
    events_by_type = {
        row["type"]: row["n"]
        for row in db.execute("SELECT type, COUNT(*) AS n FROM events GROUP BY type")
    }
    tasks_by_status = {status: 0 for status in STATUSES}
    for row in db.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
        tasks_by_status[row["status"]] = row["n"]
    return {"events_by_type": events_by_type, "tasks_by_status": tasks_by_status}
