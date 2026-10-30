"""Заглушка обработки. Неизвестный type — ошибка, по которой задача станет skipped."""

import logging
import random

from flask import current_app

log = logging.getLogger(__name__)


class HandlerError(Exception):
    pass


class UnknownEventType(Exception):
    pass


def _payment_succeeded(event):
    amount = event["payload"].get("amount")
    log.info("payment %s succeeded, amount=%s", event["event_id"], amount)


def _payment_failed(event):
    log.info("payment %s failed", event["event_id"])


def _user_registered(event):
    log.info("user registered, event %s", event["event_id"])


HANDLERS = {
    "payment_succeeded": _payment_succeeded,
    "payment_failed": _payment_failed,
    "user_registered": _user_registered,
}


def handle(event):
    handler = HANDLERS.get(event["type"])
    if handler is None:
        raise UnknownEventType(event["type"])
    rate = current_app.config["FAIL_RATE"]
    if rate > 0 and random.random() < rate:
        raise HandlerError(f"simulated failure for {event['type']}")
    handler(event)
