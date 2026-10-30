"""Один проход по очереди и цикл воркера. now подменяется в тестах."""

import json
import logging
import time

from flask import current_app

from app.db import get_db
from app.handlers import UnknownEventType
from app.queue import claim_task, complete, fail

log = logging.getLogger(__name__)


def process_once(now=None):
    """Обработать одну задачу. Вернуть её id или None, если очередь пуста."""
    db = get_db()
    if now is None:
        now = current_app.config["CLOCK"]()
    task_id = claim_task(
        db,
        now,
        current_app.config["LEASE_SECONDS"],
        current_app.config["MAX_ATTEMPTS"],
    )
    if task_id is None:
        return None

    row = db.execute(
        """
        SELECT e.event_id, e.type, e.payload
        FROM tasks t
        JOIN events e ON e.event_id = t.event_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    event = {
        "event_id": row["event_id"],
        "type": row["type"],
        "payload": json.loads(row["payload"]),
    }
    handler = current_app.config["EVENT_HANDLER"]
    try:
        handler(event)
    except UnknownEventType as exc:
        log.info("task %s skipped: %s", task_id, exc)
        complete(db, task_id, "skipped", now)
    except Exception as exc:
        log.warning("task %s failed: %s", task_id, exc)
        fail(db, task_id, str(exc), now, current_app.config["MAX_ATTEMPTS"])
    else:
        complete(db, task_id, "success", now)
    return task_id


def run_worker(poll_interval):
    while True:
        if process_once() is None:
            time.sleep(poll_interval)
