import logging
import os
import time
from pathlib import Path

import click
from flask import Flask, current_app

from app import handlers
from app.db import close_db, init_db

ROOT = Path(__file__).resolve().parent.parent


def create_app(config=None):
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config.from_mapping(
        DATABASE=os.environ.get("DATABASE", str(ROOT / "instance" / "events.db")),
        LEASE_SECONDS=30,
        HANDLER_TIMEOUT_SECONDS=_env_float("HANDLER_TIMEOUT", 20),
        MAX_ATTEMPTS=3,
        FAIL_RATE=_env_float("FAIL_RATE", 0.2),
        CLOCK=time.time,
        EVENT_HANDLER=handlers.handle,
    )
    if config:
        app.config.update(config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    from app.api import bp

    app.register_blueprint(bp)
    app.teardown_appcontext(close_db)
    _register_cli(app)
    return app


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _register_cli(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Создать таблицы events и tasks."""
        init_db()
        click.echo(f"initialized {current_app.config['DATABASE']}")

    @app.cli.command("run-worker")
    @click.option("--once", is_flag=True, help="Обработать одну задачу и выйти.")
    @click.option("--poll-interval", default=1.0, show_default=True, type=float)
    def run_worker_command(once, poll_interval):
        """Забрать задачи из очереди и обработать."""
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        from app.worker import process_once, run_worker

        if once:
            task_id = process_once()
            click.echo("idle" if task_id is None else f"processed {task_id}")
            return
        try:
            run_worker(poll_interval)
        except KeyboardInterrupt:
            click.echo("stopped")
