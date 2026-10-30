"""Один проход по очереди и цикл воркера. now подменяется в тестах."""

import json
import logging
import queue
import threading
import time
from contextvars import copy_context

from flask import current_app

from app.db import get_db
from app.handlers import UnknownEventType
from app.queue import claim_task, complete, fail

log = logging.getLogger(__name__)


class HandlerTimeout(Exception):
    pass


def _call_handler(handler, event, timeout):
    """Вызвать обработчик с таймаутом, не блокируя цикл воркера навсегда."""
    result = queue.SimpleQueue()
    context = copy_context()

    def run():
        try:
            context.run(handler, event)
        except BaseException as exc:
            result.put((False, exc))
        else:
            result.put((True, None))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise HandlerTimeout(f"handler timed out after {timeout:g} seconds")
    ok, error = result.get()
    if not ok:
        raise error


def process_once(now=None):
    """Обработать одну задачу. Вернуть её id или None, если очередь пуста."""
    db = get_db()
    fixed_now = now is not None
    if now is None:
        now = current_app.config["CLOCK"]()
    claim = claim_task(
        db,
        now,
        current_app.config["LEASE_SECONDS"],
        current_app.config["MAX_ATTEMPTS"],
    )
    if claim is None:
        return None
    task_id, locked_until = claim

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
        _call_handler(
            handler,
            event,
            current_app.config["HANDLER_TIMEOUT_SECONDS"],
        )
    except UnknownEventType as exc:
        log.info("task %s skipped: %s", task_id, exc)
        finished_at = now if fixed_now else current_app.config["CLOCK"]()
        complete(db, task_id, "skipped", finished_at, locked_until)
    except Exception as exc:
        log.warning("task %s failed: %s", task_id, exc)
        finished_at = now if fixed_now else current_app.config["CLOCK"]()
        fail(
            db,
            task_id,
            str(exc),
            finished_at,
            current_app.config["MAX_ATTEMPTS"],
            locked_until,
        )
    else:
        finished_at = now if fixed_now else current_app.config["CLOCK"]()
        complete(db, task_id, "success", finished_at, locked_until)
    return task_id


def run_worker(poll_interval):
    while True:
        if process_once() is None:
            time.sleep(poll_interval)
