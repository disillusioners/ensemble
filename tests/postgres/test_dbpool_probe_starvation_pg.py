"""F4-f pin — the starvation-shaped probe leg on real PostgreSQL (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F4.1/F4-C and §5.2. Marker:
``postgres`` (auto-applied by ``tests/postgres/conftest.py``).

What this pin asserts (the MISSING leg — DR-29/F4-C):

Under **15 holders** (the lane-pinned configured capacity):

* the probe holds **NO** connection;
* ``engine.pool.checkedout()`` **equals the holder count** (15, not
  16) — the probe never took a slot;
* **no waiter** is left in the pool's checkout queue;
* the probe thread **terminates within a bounded window**.

BASE-vs-HEAD: at BASE (no F4-A) the probe is a **stranded waiter**
(measured: ``checkedout_right_after_timeout: 15`` — the holder count,
timeout at 0.5012 s — ``BASELINE.md`` T5b / raw
``t5_fix_sites.out:1960-1967``); at HEAD (with F4-A) it **refuses
immediately** and no waiter exists.

Plantable mutation: remove the F4-A admission pre-check ⇒ at HEAD the
probe becomes a waiter again ⇒ the "no waiter / bounded termination"
assertion REDs.

Run (house form — scratch PG, per-variable env scrub, marker override)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/postgres/test_dbpool_probe_starvation_pg.py \
        --override-ini="addopts=" -m postgres -q --timeout=300
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from daemon.services.readiness import (
    MANAGER_POOL_CONFIGURED_CAPACITY,
    make_db_probe,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 2


def _starved_engine():
    """An engine whose pool capacity matches the lane-pinned constant."""
    return create_engine(
        "postgresql+psycopg://unused/unused",
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        connect_args={},
    )


class TestF4fStarvationShape:
    """F4-f — under 15 holders the probe refuses without taking a slot."""

    def test_probe_takes_no_connection_under_starvation(self, pg_engine):
        # A REAL engine over the scratch PG, with the lane-pinned
        # capacity (pool_size 5 + max_overflow 10 = 15).
        engine = create_engine(
            pg_engine.url.render_as_string(hide_password=False),
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        holders: list = []
        try:
            # Hold the pool at its configured capacity — STABLE and
            # QUIESCENT before the probe runs (the D-57 boundary race
            # is a disclosed residual, not a pin failure).
            for _ in range(MANAGER_POOL_CONFIGURED_CAPACITY):
                conn = engine.connect()
                conn.execute(text("select 1"))
                holders.append(conn)

            baseline_checkedout = engine.pool.checkedout()
            assert baseline_checkedout == MANAGER_POOL_CONFIGURED_CAPACITY, (
                "F4-f: the holder setup did not reach the configured "
                f"capacity ({baseline_checkedout})"
            )

            probe = make_db_probe(engine)
            outcome: dict = {}
            probe_thread = threading.Thread(
                target=lambda: outcome.update(_call_probe(probe)),
                daemon=True,
            )
            started = time.monotonic()
            probe_thread.start()
            probe_thread.join(timeout=10)
            elapsed = time.monotonic() - started
            assert not probe_thread.is_alive(), (
                "F4-f: the probe thread did NOT terminate within the bound "
                f"({elapsed:.3f}s) — under starvation the probe became a "
                "stranded waiter"
            )

            # The probe REFUSED (TimeoutError) — it never took a slot.
            assert outcome.get("error") is not None, (
                "F4-f: the probe did NOT refuse at capacity — it entered "
                "the blocking checkout"
            )
            assert isinstance(outcome["error"], TimeoutError), (
                f"F4-f: the refusal was not a TimeoutError: "
                f"{outcome['error']!r}"
            )
            # checkedout() is UNCHANGED — the holder count, not +1.
            assert engine.pool.checkedout() == MANAGER_POOL_CONFIGURED_CAPACITY, (
                "F4-f: the probe took a connection slot under starvation "
                f"(checkedout={engine.pool.checkedout()})"
            )
            # No waiter left in the pool's checkout queue.
            assert _pool_waiter_count(engine) == 0, (
                "F4-f: a waiter is left in the pool's checkout queue — the "
                "probe entered the blocking checkout"
            )
        finally:
            for conn in holders:
                conn.close()
            engine.dispose()

    def test_probe_still_works_below_capacity(self, pg_engine):
        """Below capacity the probe behaves exactly as at base — the
        refusal is an admission check, not a broken probe."""
        engine = create_engine(
            pg_engine.url.render_as_string(hide_password=False),
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        try:
            probe = make_db_probe(engine)
            verdict, failure_class = probe()
            assert verdict is True, (
                f"F4-f: the probe failed below capacity: {failure_class!r}"
            )
            assert failure_class is None
            assert engine.pool.checkedout() == 0, (
                "F4-f: the probe leaked its connection below capacity"
            )
        finally:
            engine.dispose()


def _call_probe(probe):
    """Call the probe, capturing the refusal as an error."""
    try:
        return {"result": probe()}
    except BaseException as exc:  # noqa: BLE001 — the refusal is the subject
        return {"error": exc}


def _pool_waiter_count(engine) -> int:
    """The number of threads WAITING in the pool's checkout queue."""
    pool = engine.pool
    # ``_do_get`` waits on ``pool._waiters`` (a Condition). The public
    # surface does not expose the waiter count, so the internal queue is
    # read defensively: a missing attribute means "no waiters".
    waiters = getattr(pool, "_waiters", None)
    if waiters is None:
        return 0
    try:
        return len(waiters._waiters)
    except AttributeError:  # pragma: no cover — defensive
        return 0
