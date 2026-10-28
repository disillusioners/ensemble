"""F7-a / F7-b pins — the db-pool idle-in-transaction regression guard.

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §4 (the invariant, the attribution
method, the census columns) and §5.2; phase3-plan.md T3.1/AC24;
decisions D-23/D-24/D-48/D-49/D-50. Marker: ``integration`` (D-23 —
the guard must build the DAEMON'S OWN manager engine via
``create_postgres_engine`` and observe ``pg_stat_activity`` filtered
by ``datname`` on a DEDICATED scratch database the test owns; the
``tests/postgres/`` shared fixture does not give the test a database
it can observe).

── THE Δ3 CALL-SITE → BUDGET-CONSTANT DECLARATION TABLE (ONE table) ──

Δ3(iv) pins the declaration location: ONE table mapping call-site →
budget constant. ``tests/unit/tools/test_dbpool_residence_static.py``
(F7-a-static) parses THIS table and asserts every row's constant
resolves to an existing constant in ``daemon/constants.py``.

| call-site                                      | budget constant (daemon.constants)                    | derivation |
|------------------------------------------------|-------------------------------------------------------|------------|
| guard read/write cycle (this pack)             | ``DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S``             | 62.5x the measured MAX healthy residence (0.0008 s, n=40 — ``BASELINE.md`` T2(d)); > 3 orders above p95 (0.0007 s) |
| churn census cycle (``test_dbpool_churn_census.py``) | ``DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S``        | the DEFAULT budget — same derivation |
| deferred-report reconcile P2 (``daemon/manager.py``) | ``MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S``     | NOT-DERIVABLE at pin-writing (Δ3(iii)/DR-11 — the real fetch latency is unmeasured); measured post-F8 by the TESTER; scoped OUT while the sentinel is negative — never guessed |

── THE MEASURED INVARIANT (D-48/DR-35 — both halves, co-equal) ──

* **(a) Occupancy — THREE states, never a count of zero.** The census
  reports occupancy across (i) ``idle`` with ``xact_start IS NULL``
  (checked out, no statement yet — the incident's dominant shape),
  (ii) ``idle in transaction``, (iii) ``active``. An idle-in-txn-only
  census is vacuous against two of the three shapes (the incident was
  15/15 slots held with only 6 idle-in-txn — P-10). The census count
  is REPORTED and BOUNDED — **never asserted == 0**. The occupancy
  assertion is ``manager.engine.pool.checkedout()`` returning to its
  baseline after every cycle (in-memory, no DB round trip).
* **(b) Residence — ``xact_age_s = now() − xact_start``** against the
  operation's own hold budget. The old metric
  ``state_change − xact_start`` is FROZEN at the last statement's
  completion — a leaked backend shows the SAME sub-millisecond value
  as a healthy one, so a budget written against it can never fire on
  the leak (it appears in this pack ONLY inside the frozen-metric
  discriminator control, which proves that vacuity). The 2.0 s
  placeholder is WITHDRAWN; the λ (borrow-arrival) bound was never
  measured and is never asserted.

HARD BANS honored (D-48): no ``state_change − xact_start`` metric in
the residence comparison; no 2.0 s literal; no
``peak_occupancy ≤ λ × budget`` assertion; no census ``count == 0``
assertion.

Attribution (D-24-amended/D-49): by ``application_name`` where
present (the manager engine carries ``ensemble-manager`` on the
CONNINFO channel since F2), else by scratch-DB isolation — every
backend in the scratch database belongs to this test. The census
scope note (D-50 — the 11 real pool-creation sites + the
``factory.py:289`` comment false-positive) is documented in the
sibling churn pack's header.

Plantable mutations:

* **F7-a** — hold a manager-pool connection across a dwell that
  exceeds the cycle's declared budget ⇒ the ``xact_age_s`` assertion
  REDs (a long ``state_age_s`` alone does NOT — that is the point of
  the metric).
* **F7-b** — re-introduce a retention path (a checked-out connection
  holding an open transaction that is never returned) ⇒ the occupancy
  (``checkedout()`` not back to baseline) and three-state census
  assertions RED.

Run (house form — scratch PG, per-variable env scrub, marker
override; the ``PG_TEST_USER`` role needs CREATEDB — the pack
creates and drops its own scratch database per D-23)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/integration/test_db_pool_idle_in_transaction_guard.py \
        --override-ini="addopts=" -m integration -q --timeout=300
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from daemon.constants import (
    DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S,
    MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S,
)
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import create_postgres_engine

pytestmark = [pytest.mark.integration]

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 7

_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

#: The D-50 census-scope reference (documentation constant — the
#: churn pack's header carries the full 11-site table).
CENSUS_SCOPE_NOTE = (
    "census scope = EVERY pool-creation site (11 real + 1 comment "
    "false-positive at factory.py:289 — BASELINE.md T3(a)); the "
    "three named engines are the attribution subset, not the boundary"
)


# ── the census helper (the reference query shape — raw common.py:80-92) ────

_CENSUS_SQL = text(
    """
    SELECT
        pid,
        state,
        wait_event_type,
        xact_start,
        query_start,
        state_change,
        backend_start,
        application_name,
        left(query, 200) AS last_query,
        CASE WHEN xact_start IS NULL THEN NULL
             ELSE extract(epoch FROM (now() - xact_start)) END AS xact_age_s,
        CASE WHEN state_change IS NULL THEN NULL
             ELSE extract(epoch FROM (now() - state_change)) END AS state_age_s
    FROM pg_stat_activity
    WHERE datname = :datname
      AND backend_start > :run_start
    """
)


def _run_census(maintenance_conn, *, datname: str, run_start: datetime) -> list[dict]:
    """Census every backend in the scratch database.

    Captures (contract §4.2 item 4): state, wait_event_type,
    xact_start, query_start, state_change, backend_start,
    application_name, xact_age_s (= ``now() − xact_start`` — the
    D-48 metric) and state_age_s, with the ``backend_start >
    run_start`` BASE filter prescribed by test-strategy.md §1.5
    implemented in the SQL.
    """
    rows = maintenance_conn.execute(
        _CENSUS_SQL, {"datname": datname, "run_start": run_start}
    ).mappings().all()
    return [dict(row) for row in rows]


def _residence_buckets(rows: list[dict]) -> dict[str, int]:
    """Residence-time BUCKETS (reported, never asserted == 0)."""
    buckets = {
        "<0.001s": 0,
        "0.001-0.01s": 0,
        "0.01-0.1s": 0,
        "0.1-1s": 0,
        "1-10s": 0,
        ">10s": 0,
        "no-xact": 0,
    }
    for row in rows:
        age = row["xact_age_s"]
        if age is None:
            buckets["no-xact"] += 1
        elif age < 0.001:
            buckets["<0.001s"] += 1
        elif age < 0.01:
            buckets["0.001-0.01s"] += 1
        elif age < 0.1:
            buckets["0.01-0.1s"] += 1
        elif age < 1.0:
            buckets["0.1-1s"] += 1
        elif age < 10.0:
            buckets["1-10s"] += 1
        else:
            buckets[">10s"] += 1
    return buckets


def _residence_violations(
    rows: list[dict], budget_s: float
) -> list[tuple[int, float]]:
    """Backends whose ``xact_age_s`` exceeds the operation's budget.

    THE D-48 metric: ``xact_age_s`` (= ``now() − xact_start``,
    computed in the census SQL) — never ``state_change − xact_start``
    (frozen at the last statement; a leaked backend is invisible to
    it — see the frozen-metric discriminator test). A NEGATIVE budget
    is the Δ3 NOT-DERIVABLE sentinel: the site is scoped OUT (no
    assertion) rather than judged against a guessed number.
    """
    if budget_s < 0:
        return []  # NOT-DERIVABLE — scoped out (Δ3(iii))
    return [
        (row["pid"], float(row["xact_age_s"]))
        for row in rows
        if row["xact_age_s"] is not None and float(row["xact_age_s"]) > budget_s
    ]


def _report_census(rows: list[dict]) -> dict[str, int]:
    """Report (never assert) the three-state census counts — P-10."""
    report: dict[str, int] = {
        "idle_no_xact": 0,
        "idle_in_transaction": 0,
        "active": 0,
        "other": 0,
    }
    for row in rows:
        state = row["state"]
        if state == "idle" and row["xact_start"] is None:
            report["idle_no_xact"] += 1
        elif state == "idle in transaction":
            report["idle_in_transaction"] += 1
        elif state == "active":
            report["active"] += 1
        else:
            report["other"] += 1
    return report


# ── the scratch-database fixture (D-23/D-24) ─────────────────────────────


class _ScratchDb:
    """A dedicated scratch database the test OWNS (created + dropped)."""

    def __init__(self) -> None:
        self.name = f"dbpool_guard_{uuid.uuid4().hex[:12]}"
        self.run_start = datetime.now(timezone.utc)
        self.maintenance = create_engine(
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}",
            pool_pre_ping=True,
            future=True,
        )
        with self.maintenance.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f'CREATE DATABASE "{self.name}"'))
        # The daemon's OWN manager engine, pointed at the scratch DB
        # (D-23: the engine's connect args + pool knobs are what is
        # under test — NOT a hand-rolled create_engine).
        self.config = EnsembleConfig(
            database="postgres",
            postgres=PostgresConfig(
                host=_PG_HOST,
                port=_PG_PORT,
                db=self.name,
                user=_PG_USER,
                password=_PG_PASSWORD,
            ),
        )
        self.engine = create_postgres_engine(self.config)

    def census(self) -> list[dict]:
        with self.maintenance.connect() as conn:
            return _run_census(conn, datname=self.name, run_start=self.run_start)

    def dispose(self) -> None:
        try:
            self.engine.dispose()
        finally:
            try:
                with self.maintenance.connect() as conn:
                    conn.execution_options(isolation_level="AUTOCOMMIT")
                    conn.execute(
                        text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
                    )
            finally:
                self.maintenance.dispose()


@pytest.fixture(scope="module")
def scratch_db():
    """The owned scratch database — or a skip when PG is unreachable.

    G3.2's zero-skips rule is asserted at GATE time (the gate provides
    a reachable PG); this skip is the unreachable-PG disclosure, not a
    pass.
    """
    probe = create_engine(
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}",
        pool_pre_ping=True,
        future=True,
    )
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, DBAPIError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available at {_PG_HOST}:{_PG_PORT}: {exc}")
    finally:
        probe.dispose()
    db = _ScratchDb()
    try:
        # The guard's cycle table — the scratch DB is empty by design;
        # the guard owns its schema.
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS guard_cycle ("
                    "id serial primary key, note text)"
                )
            )
        yield db
    finally:
        db.dispose()


def _baseline_checkedout(engine) -> int:
    """The pool's resting occupancy (0 connections checked out)."""
    return engine.pool.checkedout()


def _read_write_cycle(engine, *, note: str) -> None:
    """The guard's own read/write cycle through the REAL manager engine.

    Budget (declaration table row 1): DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S.
    Borrow a session, INSERT + SELECT + commit, close — the healthy
    shape every cycle must return from.
    """
    from sqlmodel import Session as SQLModelSession

    with SQLModelSession(engine) as session:
        session.execute(text(f"INSERT INTO guard_cycle (note) VALUES ('{note}')"))
        session.execute(text("SELECT count(*) FROM guard_cycle"))
        session.commit()


# ── F7-a — the residence metric ───────────────────────────────────────────


class TestF7aResidenceMetric:
    """F7-a — ``xact_age_s`` against the operation's own hold budget."""

    @pytest.mark.timeout(120)
    def test_residence_assertion_uses_xact_age_s_and_the_declared_constant(
        self, scratch_db
    ):
        # The guard's residence comparison is bound to the Δ3 constant
        # (structure, never a literal — DR-20) and the metric is
        # xact_age_s (the census column), not any state_change-derived
        # expression.
        assert DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S > 0, (
            "F7-a: the guard's default residence budget must be a real "
            "positive constant (the 2.0 s placeholder is WITHDRAWN, not "
            "the constant itself)"
        )
        assert MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S < 0, (
            "F7-a: the reconcile budget must still carry the Δ3 "
            "NOT-DERIVABLE sentinel until the TESTER measures post-F8"
        )
        healthy_rows = scratch_db.census()
        violations = _residence_violations(
            healthy_rows, DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S
        )
        assert violations == [], (
            f"F7-a: residence violations on a healthy pool: {violations}"
        )

    @pytest.mark.timeout(120)
    def test_healthy_cycles_return_occupancy_to_baseline(self, scratch_db):
        engine = scratch_db.engine
        baseline = _baseline_checkedout(engine)
        for i in range(5):
            _read_write_cycle(engine, note=f"cycle-{i}")
        after = engine.pool.checkedout()
        census_rows = scratch_db.census()
        report = _report_census(census_rows)
        # The occupancy assertion — THIS is the invariant that would
        # have caught the incident (15/15 slots held).
        assert after == baseline, (
            f"F7-a/F7-e shape: checkedout()={after} did not return to "
            f"baseline={baseline} after the cycle"
        )
        # The census count is REPORTED and BOUNDED — never asserted
        # == 0 (P-10: the incident held 15/15 slots with only 6
        # idle-in-txn; a count==0 assertion is vacuous against the
        # occupancy class).
        total = sum(report.values())
        assert total <= 15, (
            f"F7-a: the census reported {total} backends in the scratch "
            "DB — above the manager pool's configured capacity (15); "
            f"report={report}"
        )

    @pytest.mark.timeout(120)
    def test_deliberate_long_xact_age_exceeds_the_operation_budget(
        self, scratch_db
    ):
        # The mutation that proves this pin alive: a DELIBERATE long
        # xact_age_s (a held transaction), NOT a long state_age_s.
        engine = scratch_db.engine
        baseline = _baseline_checkedout(engine)
        conn = engine.connect()
        try:
            # Autobegin + one statement, then HOLD (no further
            # statements — the exact leak shape: the backend sits
            # 'idle in transaction' while the client dwells).
            conn.execute(text("SELECT 1"))
            time.sleep(DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S + 0.5)
            rows = scratch_db.census()
            held = [r for r in rows if r["state"] == "idle in transaction"]
            assert held, (
                "F7-a: the held transaction is not visible as 'idle in "
                "transaction' in the census — the guard is blind to the "
                "leak shape"
            )
            violations = _residence_violations(
                rows, DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S
            )
            assert violations, (
                "F7-a: a deliberate xact_age_s above the operation's "
                "budget produced NO residence violation — the pin is "
                "vacuous"
            )
            assert engine.pool.checkedout() == baseline + 1, (
                "F7-a: the held connection must occupy exactly one slot "
                "above baseline while the transaction is open"
            )
        finally:
            conn.rollback()
            conn.close()
        assert engine.pool.checkedout() == baseline, (
            "F7-a: occupancy did not return to baseline after the held "
            "connection was returned"
        )

    @pytest.mark.timeout(120)
    def test_frozen_metric_is_vacuous_on_the_leak_xact_age_is_not(
        self, scratch_db
    ):
        # The D-48 discriminator, measured: state_change − xact_start
        # is FROZEN at the last statement's completion — the held
        # backend below shows a sub-budget frozen value while its
        # xact_age_s exceeds the budget. A guard written against the
        # frozen expression would be GREEN on this exact leak.
        engine = scratch_db.engine
        conn = engine.connect()
        try:
            conn.execute(text("SELECT 1"))
            time.sleep(DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S + 0.5)
            rows = scratch_db.census()
            held = [r for r in rows if r["state"] == "idle in transaction"]
            assert held, "F7-a: precondition — the held backend is visible"
            row = held[0]
            xact_age_s = float(row["xact_age_s"])
            # The FROZEN expression is ``state_change − xact_start``
            # (D-48): both endpoints are set when the last statement
            # completed, so the value NEVER grows during the hold —
            # this backend shows the same sub-budget value a healthy
            # one would.
            frozen_value_s = (
                row["state_change"] - row["xact_start"]
            ).total_seconds()
            assert xact_age_s > DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S, (
                "F7-a: xact_age_s must exceed the budget on the held "
                f"backend (observed {xact_age_s}s)"
            )
            assert frozen_value_s <= DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S, (
                "F7-a: the control's precondition broke — the frozen "
                "expression GREW with the hold (a statement ran during "
                "the dwell); the frozen-metric vacuity cannot be "
                f"demonstrated in this run (frozen={frozen_value_s}s)"
            )
        finally:
            conn.rollback()
            conn.close()


# ── F7-b — the retention path ─────────────────────────────────────────────


class TestF7bRetentionDetected:
    """F7-b — a retention path is DETECTABLE (never silently green)."""

    @pytest.mark.timeout(120)
    def test_checked_out_connection_holding_a_transaction_is_detected(
        self, scratch_db
    ):
        # The retention shape: a connection checked out, a transaction
        # open, the client gone quiet. The guard's instruments (the
        # three-state census + in-memory occupancy + attribution) must
        # all see it.
        engine = scratch_db.engine
        baseline = _baseline_checkedout(engine)
        conn = engine.connect()
        retained = False
        try:
            conn.execute(text("INSERT INTO guard_cycle (note) VALUES ('retained')"))
            retained = True
            time.sleep(DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S + 0.3)
            rows = scratch_db.census()
            report = _report_census(rows)
            assert report["idle_in_transaction"] >= 1, (
                f"F7-b: the retention is invisible to the census "
                f"(report={report}) — re-introducing a retention path "
                "would be silently green"
            )
            # Attribution (D-24-amended/D-49): by application_name
            # where present — the manager engine carries its name on
            # the CONNINFO channel (F2); the scratch-DB isolation
            # already guarantees every row is this test's.
            manager_rows = [
                r
                for r in rows
                if r["application_name"] == "ensemble-manager"
            ]
            assert manager_rows, (
                "F7-b: no backend carries the manager engine's "
                "application_name 'ensemble-manager' — the conninfo "
                "attribution channel (F2/D-49) is not reaching "
                "pg_stat_activity"
            )
            assert engine.pool.checkedout() == baseline + 1, (
                "F7-b: the retained connection must hold exactly one "
                "slot above baseline"
            )
            violations = _residence_violations(
                rows, DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S
            )
            assert violations, (
                "F7-b: the retained transaction exceeds the budget but "
                "produced no residence violation"
            )
        finally:
            if retained:
                conn.rollback()
            conn.close()

    @pytest.mark.timeout(120)
    def test_returned_connection_with_open_transaction_is_cleaned(
        self, scratch_db
    ):
        # The F3-class defect shape: the client returns the connection
        # WITHOUT an explicit rollback. The manager engine's explicit
        # pool_reset_on_return="rollback" (F2) must clean it: the slot
        # returns to the pool, occupancy returns to baseline, and the
        # next borrower sees NO open transaction.
        engine = scratch_db.engine
        baseline = _baseline_checkedout(engine)
        conn = engine.connect()
        try:
            conn.execute(text("SELECT 1"))
        finally:
            conn.close()  # NO explicit rollback — the defect shape
        assert engine.pool.checkedout() == baseline, (
            "F7-b: the returned connection did not go back to the pool"
        )
        # Next borrower: a fresh statement must not raise "current
        # transaction is aborted" and must observe NO open state.
        with engine.connect() as next_conn:
            state = next_conn.execute(
                text("SELECT current_setting('transaction_isolation')")
            ).scalar()
            assert state, "F7-b: the next borrower's statement failed"
        # Post-return census: the pooled backend is 'idle' with
        # xact_start NULL (the measured T2 (c2) shape) — reported,
        # never asserted == 0.
        rows = scratch_db.census()
        report = _report_census(rows)
        assert report["idle_in_transaction"] == 0, (
            "F7-b: a backend sat 'idle in transaction' AFTER the "
            "connection was returned — pool_reset_on_return='rollback' "
            f"did not clean it (report={report})"
        )


# ── the three-state census itself (D-48 (a)) ──────────────────────────────


class TestThreeStateCensus:
    """The census observes all THREE measured PG states."""

    @pytest.mark.timeout(120)
    def test_census_reports_all_three_states_with_every_column(
        self, scratch_db
    ):
        import threading

        engine = scratch_db.engine
        baseline = _baseline_checkedout(engine)
        # (i) idle, checked out, no statement yet: borrow and hold
        # without executing anything.
        silent = engine.connect()
        # (ii) idle in transaction: borrow + autobegin + hold.
        txn_holder = engine.connect()
        txn_holder.execute(text("SELECT 1"))
        # (iii) active: a pg_sleep statement in flight on a third
        # connection while the census runs.
        active_conn = engine.connect()
        sleep_done = threading.Event()

        def _run_sleep() -> None:
            try:
                active_conn.execute(text("SELECT pg_sleep(1.0)")).scalar()
            finally:
                sleep_done.set()

        sleeper = threading.Thread(target=_run_sleep, daemon=True)
        sleeper.start()
        try:
            time.sleep(0.2)  # let the pg_sleep statement land server-side
            rows = scratch_db.census()
            report = _report_census(rows)
            assert report["idle_no_xact"] >= 1, (
                f"census: state (i) idle/xact_start-NULL not observed "
                f"(report={report}) — the census is blind to the "
                "incident's dominant shape"
            )
            assert report["idle_in_transaction"] >= 1, (
                f"census: state (ii) idle-in-transaction not observed "
                f"(report={report})"
            )
            assert report["active"] >= 1, (
                f"census: state (iii) active not observed "
                f"(report={report}) — the F4-B probe shape is invisible"
            )
            # Every contracted column is present and non-null where
            # the state requires it (contract §4.2 item 4).
            for row in rows:
                for column in (
                    "state",
                    "wait_event_type",
                    "xact_start",
                    "query_start",
                    "state_change",
                    "backend_start",
                    "application_name",
                    "xact_age_s",
                    "state_age_s",
                    "last_query",
                ):
                    assert column in row, (
                        f"census: column {column!r} missing — the "
                        "census must capture every contracted column"
                    )
                if row["state"] == "idle in transaction":
                    assert row["xact_start"] is not None, (
                        "census: an idle-in-transaction backend with "
                        "NULL xact_start contradicts the measured state "
                        "matrix"
                    )
                    assert row["xact_age_s"] is not None, (
                        "census: xact_age_s must be computed for every "
                        "backend with an open transaction"
                    )
            buckets = _residence_buckets(rows)
            assert sum(buckets.values()) == len(rows), (
                "census: the residence buckets must partition the "
                "census exactly"
            )
        finally:
            sleeper.join(timeout=5)
            sleep_done.wait(timeout=5)
            active_conn.close()
            txn_holder.rollback()
            txn_holder.close()
            silent.close()
        assert engine.pool.checkedout() == baseline, (
            "census: occupancy did not return to baseline after the "
            "three-state probes"
        )
