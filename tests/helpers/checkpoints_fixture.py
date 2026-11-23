"""``checkpoints`` table fixture support for the E7 evidence leg.

The never-dispatched-husk detector's E7 leg (D1) probes the LangGraph
``checkpoints`` table for the candidate's thread (``thread_id ==
instance_id``). In production the table ALWAYS exists — it is created by
the LangGraph checkpointer's ``setup()`` — so the leg is evaluated and
the detector reports ``evidence_legs == "ok"``.

The SQLModel metadata does NOT model that table (it belongs to the
LangGraph checkpointer, not to the ensemble's ORM), so a fixture that
builds its fleet with ``SQLModel.metadata.create_all(engine)`` does NOT
get it. Without this helper the E7 leg degrades to "unavailable" — the
detector correctly refuses to flag anything and reports
``evidence_legs == "degraded"`` — which is the RIGHT production
behaviour but makes the full-flagging path untestable.

This helper creates the table in a fixture. It is ENABLING, not
weakening: the production code under test probes a table the fixture
now provides, exactly as production does. It changes no assertion.

The DDL follows the LangGraph PostgreSQL schema (as deployed in
``ensemble_prod``); the SQLite variant uses TEXT for the JSONB columns
because SQLite has no JSONB type. Only ``thread_id`` is read by the
probe, so both variants answer the same question.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


#: The LangGraph ``checkpoints`` DDL, per dialect.
_CHECKPOINTS_DDL: dict[str, str] = {
    "postgresql": (
        "CREATE TABLE IF NOT EXISTS checkpoints ("
        "thread_id TEXT NOT NULL, "
        "checkpoint_ns TEXT NOT NULL DEFAULT '', "
        "checkpoint_id TEXT NOT NULL, "
        "parent_checkpoint_id TEXT, "
        "type TEXT, "
        "checkpoint JSONB NOT NULL, "
        "metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
    ),
    "sqlite": (
        "CREATE TABLE IF NOT EXISTS checkpoints ("
        "thread_id TEXT NOT NULL, "
        "checkpoint_ns TEXT NOT NULL DEFAULT '', "
        "checkpoint_id TEXT NOT NULL, "
        "parent_checkpoint_id TEXT, "
        "type TEXT, "
        "checkpoint TEXT NOT NULL, "
        "metadata TEXT NOT NULL DEFAULT '{}', "
        "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
    ),
}


def create_checkpoints_table(engine: Any) -> None:
    """Create the LangGraph ``checkpoints`` table on ``engine``.

    Idempotent (``IF NOT EXISTS``) — a fixture that already has the
    table (e.g. a real deployment DSN) is left untouched.
    """
    dialect = getattr(engine.dialect, "name", "") or ""
    ddl = _CHECKPOINTS_DDL.get(dialect)
    if ddl is None:
        raise ValueError(f"unsupported dialect for the checkpoints table: {dialect!r}")
    with engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()


def insert_checkpoint_row(engine: Any, thread_id: str, checkpoint_id: str) -> None:
    """Insert ONE checkpoint row for ``thread_id`` — the E7 proof shape.

    A checkpoint row is the LangGraph evidence that a turn EXECUTED for
    that thread. The payload columns are irrelevant to the probe (it
    reads ``thread_id`` only); the row exists so the leg can see it.
    """
    payload = "{}"
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
                "VALUES (:thread_id, '', :checkpoint_id, 'json', :payload, :payload)"
            ),
            {"thread_id": thread_id, "checkpoint_id": checkpoint_id, "payload": payload},
        )
        conn.commit()
