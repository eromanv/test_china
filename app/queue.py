"""Очередь на таблице tasks. Время приходит снаружи, чтобы тесты не спали."""

import logging

from app.db import transaction

log = logging.getLogger(__name__)


def enqueue(db, event_id, now):
    db.execute(
        """
        INSERT INTO tasks (
            event_id, status, attempts, next_run_at, locked_until,
            last_error, created_at, updated_at
        )
        VALUES (?, 'pending', 0, ?, NULL, NULL, ?, ?)
        """,
        (event_id, now, now, now),
    )


def claim_task(db, now, lease, max_attempts):
    """Захватить одну готовую задачу. Вернуть (id, locked_until) или None.

    Задача в pending, next_run_at уже наступил, аренда пустая или истекла.
    Истёкшая аренда — потерянная попытка упавшего воркера: attempts += 1,
    и при достижении лимита задача становится failed, а не зависает.
    """
    with transaction(db):
        while True:
            row = db.execute(
                """
                SELECT id, attempts, locked_until
                FROM tasks
                WHERE status = 'pending'
                  AND next_run_at <= ?
                  AND (locked_until IS NULL OR locked_until < ?)
                ORDER BY next_run_at, id
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None

            attempts = row["attempts"]
            if row["locked_until"] is not None:
                attempts += 1
                if attempts >= max_attempts:
                    db.execute(
                        """
                        UPDATE tasks
                        SET status = 'failed',
                            attempts = ?,
                            locked_until = NULL,
                            last_error = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (attempts, "lease expired", now, row["id"]),
                    )
                    log.warning("task %s failed: lease expired", row["id"])
                    continue

            locked_until = now + lease
            db.execute(
                """
                UPDATE tasks
                SET attempts = ?, locked_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempts, locked_until, now, row["id"]),
            )
            return row["id"], locked_until


def complete(db, task_id, status, now, locked_until):
    if status not in ("success", "skipped"):
        raise ValueError(f"unexpected status: {status}")

    with transaction(db):
        cur = db.execute(
            """
            UPDATE tasks
            SET status = ?, locked_until = NULL, updated_at = ?
            WHERE id = ? AND status = 'pending' AND locked_until = ?
            """,
            (status, now, task_id, locked_until),
        )
        return cur.rowcount == 1


def fail(db, task_id, error, now, max_attempts, locked_until):
    """attempts += 1. Пока их меньше лимита — pending и пауза 2**attempts секунд."""
    with transaction(db):
        row = db.execute(
            """
            SELECT attempts
            FROM tasks
            WHERE id = ? AND status = 'pending' AND locked_until = ?
            """,
            (task_id, locked_until),
        ).fetchone()
        if row is None:
            return "stale"
        attempts = row["attempts"] + 1
        if attempts < max_attempts:
            db.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    attempts = ?,
                    next_run_at = ?,
                    locked_until = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (attempts, now + 2**attempts, error, now, task_id),
            )
            return "pending"
        db.execute(
            """
            UPDATE tasks
            SET status = 'failed',
                attempts = ?,
                locked_until = NULL,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (attempts, error, now, task_id),
        )
        return "failed"
