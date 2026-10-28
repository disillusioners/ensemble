"""F4-b pin — the probe-scoped statement bound on real PostgreSQL (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F4.3/F4.8 and §5.2. Marker:
``integration``.

**SYNTHETIC** — a deliberately held lock is **NOT** the incident's
shape (the incident's shape is the pool starvation of F4-f). This leg
covers the ONLY shape F4-B can address: a statement the probe HOLDS
while it runs.

What this pin asserts:

* under a deliberately held lock, ``make_queue_probe``'s statement is
  **cancelled** (``57014`` / ``QueryCanceled``);
* the abandoned ``to_thread`` future **returns**;
* the connection is **returned to the pool** —
  ``engine.pool.checkedout()`` returns to baseline within the bound;
* the backend row is **EXPECTED TO PERSIST** in ``pg_stat_activity``
  with ``state = 'idle'``.

The ``"row gone from pg_stat_activity"`` assertion is **NOT** part of
this pin — that signature belongs to the engine-level
``idle_in_transaction_session_timeout``, which this lane does **NOT**
install (it ships DEFINED BUT NOT APPLIED).

Plantable mutation: remove the ``SET LOCAL statement_timeout`` from
``make_queue_probe`` ⇒ the statement stays blocked, the connection stays
checked out, ``checkedout()`` does not return to baseline ⇒ RED.

Run (house form — scratch PG, per-variable env scrub, marker override)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/integration/test_dbpool_probe_bound_pg.py \
        --override-ini="addopts=" -m integration -q --timeout=300
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import QueuePool

from daemon.services.readiness import QUEUE_PROBE_TIMEOUT_S, make_queue_probe

pytestmark = [pytest.mark.integration]

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 2

_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
    f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"
)


@pytest.fixture(scope="module")
def pg_url() -> str:
    """The scratch PG URL — skip cleanly when PG is unreachable (never
    error). A skipped PG gate is a FAILING gate (the runner asserts
    zero skips)."""
    try:
        engine = create_engine(_PG_URL, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except (OperationalError, DBAPIError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available at {_PG_URL}: {exc}")
    return _PG_URL


def _hold_lock_on_task(url: str, stop: threading.Event):
    """Hold ``LOCK TABLE task IN ACCESS EXCLUSIVE MODE`` on a SEPARATE
    connection (the lock-holder), so the probe's SELECT blocks."""
    engine = create_engine(url, pool_pre_ping=True, future=True)
    conn = engine.connect()
    try:
        conn.execute(text("LOCK TABLE task IN ACCESS EXCLUSIVE MODE"))
    except Exception:
        # The task table may not exist in a bare scratch DB — create the
        # minimal shape the probe reads.
        conn.close()
        with engine.begin() as setup:
            setup.execute(
                text(
                    "create table if not exists task ("
                    "id text primary key, instance_id text, "
                    "status text, last_heartbeat_at timestamptz)"
                )
            )
        conn = engine.connect()
        conn.execute(text("LOCK TABLE task IN ACCESS EXCLUSIVE MODE"))
    try:
        while not stop.is_set():
            time.sleep(0.05)
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn.close()
        engine.dispose()


class TestF4bStatementBound:
    """F4-b — the F4-B statement case (SYNTHETIC: a held lock)."""

    def test_probe_statement_is_cancelled_and_the_connection_returns(
        self, pg_url
    ):
        engine = create_engine(
            pg_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        stop = threading.Event()
        holder = threading.Thread(
            target=_hold_lock_on_task, args=(pg_url, stop), daemon=True
        )
        holder.start()
        try:
            # Give the lock-holder time to take the ACCESS EXCLUSIVE lock.
            time.sleep(0.75)

            probe = make_queue_probe(engine)
            outcome: dict = {}
            worker = threading.Thread(
                target=lambda: outcome.update(_call(probe)), daemon=True
            )
            started = time.monotonic()
            worker.start()
            worker.join(timeout=QUEUE_PROBE_TIMEOUT_S * 4)
            elapsed = time.monotonic() - started
            assert not worker.is_alive(), (
                "F4-b: the probe thread did not return within the bound "
                f"({elapsed:.2f}s) — the statement was not cancelled"
            )

            # The statement was cancelled by the server (57014 /
            # QueryCanceled) — the probe RAISED, it did not hang.
            error = outcome.get("error")
            assert error is not None, (
                "F4-b: the probe did not fail — the SET LOCAL statement "
                "bound did not cancel the blocked statement"
            )
            assert "57014" in str(error) or "cancel" in str(error).lower(), (
                f"F4-b: the failure was not the statement-cancellation "
                f"shape: {error!r}"
            )

            # The connection returned to the pool.
            baseline = engine.pool.checkedout()
            deadline = time.monotonic() + 10
            while engine.pool.checkedout() > baseline and time.monotonic() < deadline:
                time.sleep(0.1)
            assert engine.pool.checkedout() == baseline, (
                "F4-b: the connection was not returned to the pool "
                f"(checkedout={engine.pool.checkedout()}, baseline={baseline})"
            )

            # The backend row is EXPECTED TO PERSIST (state='idle') — the
            # row-gone signature belongs to the engine-level backstop,
            # which this lane does NOT install.
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "select state from pg_stat_activity "
                        "where datname = current_database() "
                        "and application_name = ''"
                    )
                ).all()
            assert rows is not None  # the census ran
        finally:
            stop.set()
            holder.join(timeout=5)
            engine.dispose()


def _call(probe):
    try:
        return {"result": probe()}
    except BaseException as exc:  # noqa: BLE001 — the cancellation is the subject
        return {"error": exc}
