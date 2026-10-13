"""C7 remediation (council 2026-10-12) - preserve_in_flight re-applied
at DELETE time. Real in-memory SQLite; table/column names match the
production capture SQL exactly (message_queue.message_id, task.status,
task.auto_continued_at, task.message_id per the task model :207,
instances.status). Kill-switch helper defaults ON (env unset).
"""
from __future__ import annotations

from sqlalchemy import create_engine, text

from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)

_DDL = [
    "CREATE TABLE message_queue (message_id VARCHAR PRIMARY KEY, "
    "instance_id VARCHAR, payload VARCHAR, created_at TIMESTAMP)",
    "CREATE TABLE task (task_id VARCHAR PRIMARY KEY, instance_id VARCHAR, "
    "status VARCHAR, auto_continued_at TIMESTAMP, message_id VARCHAR)",
    "CREATE TABLE instances (instance_id VARCHAR PRIMARY KEY, status VARCHAR)",
]


def _mk():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _DDL:
            conn.exec_driver_sql(ddl)
    return SQLModelMessageQueueRepository(engine=engine), engine


def _seed(engine, msg, task, status, auto_continued=None, instance_status="completed"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO instances (instance_id, status) VALUES (:i, :s)"),
            {"i": task, "s": instance_status})
        conn.execute(text(
            "INSERT INTO task (task_id, instance_id, status, "
            "auto_continued_at, message_id) VALUES (:t, :i, :st, :ac, :m)"),
            {"t": task, "i": task, "st": status, "ac": auto_continued,
             "m": msg})
        conn.execute(text(
            "INSERT INTO message_queue (message_id, instance_id, payload) "
            "VALUES (:m, :i, :p)"), {"m": msg, "i": task, "p": "x"})


def _live(engine):
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(
            text("SELECT message_id FROM message_queue")).fetchall()}


def test_row_becoming_inflight_after_capture_survives_delete():
    """THE C7 pin: a row dispatched between capture and DELETE must
    survive (the old bare IN-DELETE destroyed it mid-flight)."""
    repo, engine = _mk()
    _seed(engine, "m-survivor", "t1", "queued")
    _seed(engine, "m-doomed", "t2", "queued")
    # capture moment: both rows doomed-eligible (tasks queued); time
    # passes; t1 is dispatched.
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE task SET status = 'running' WHERE task_id = 't1'"))
    # DELETE fires with the capture-time list.
    deleted = repo.delete_doomed_ids(
        ["m-survivor", "m-doomed"], preserve_in_flight=True)
    assert deleted == 1, f"expected exactly 1 row deleted, got {deleted}"
    assert _live(engine) == {"m-survivor"}, (
        "mid-flight message m-survivor was destroyed by the DELETE "
        "(preserve predicate missing at fire time)")


def test_still_doomed_row_is_deleted():
    repo, engine = _mk()
    _seed(engine, "m1", "t1", "queued")
    assert repo.delete_doomed_ids(["m1"], preserve_in_flight=True) == 1
    assert _live(engine) == set()


def test_running_at_capture_survives():
    repo, engine = _mk()
    _seed(engine, "m-live", "t1", "running")
    assert repo.delete_doomed_ids(["m-live"], preserve_in_flight=True) == 0
    assert _live(engine) == {"m-live"}


def test_no_preserve_keeps_bare_delete():
    repo, engine = _mk()
    _seed(engine, "m1", "t1", "running")
    _seed(engine, "m2", "t2", "queued")
    assert repo.delete_doomed_ids(["m1", "m2"], preserve_in_flight=False) == 2
    assert _live(engine) == set()


def test_arm3_auto_continued_survives():
    repo, engine = _mk()
    _seed(engine, "m-ac", "t1", "queued",
          auto_continued="2026-10-12 00:00:00", instance_status="running")
    assert repo.delete_doomed_ids(["m-ac"], preserve_in_flight=True) == 0
    assert _live(engine) == {"m-ac"}
