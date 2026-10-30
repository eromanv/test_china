import pytest

from app import create_app
from app.db import get_db, init_db
from app.handlers import UnknownEventType

KNOWN = {"payment_succeeded", "payment_failed", "user_registered"}


class Boom(Exception):
    pass


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def app(tmp_path):
    state = {"fail": False}
    clock = Clock()

    def handler(event):
        if event["type"] not in KNOWN:
            raise UnknownEventType(event["type"])
        if state["fail"]:
            raise Boom("boom")

    application = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "FAIL_RATE": 0,
            "CLOCK": clock,
            "EVENT_HANDLER": handler,
        }
    )
    application.fail_state = state
    application.clock = clock
    with application.app_context():
        init_db()
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def event_body(event_id="abc-123", type_="payment_succeeded", payload=None):
    return {
        "event_id": event_id,
        "type": type_,
        "payload": {"amount": 10} if payload is None else payload,
    }


def task_row(app, task_id=None):
    with app.app_context():
        db = get_db()
        if task_id is None:
            return db.execute("SELECT * FROM tasks").fetchone()
        return db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
