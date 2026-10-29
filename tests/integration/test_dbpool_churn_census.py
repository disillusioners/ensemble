"""F7-e pin — the churn census: occupancy returns to baseline (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §4.1(a)/§4.4 and §5.2; phase3-plan.md
T3.1/AC24; decisions D-48/D-50. Marker: ``integration`` (D-23 — the
pack builds the daemon's OWN manager engine via
``create_postgres_engine`` and observes ``pg_stat_activity`` on a
DEDICATED scratch database the test owns).

**This is the pin that would have caught the incident:** the 2026-10-26
incident held 15/15 manager slots with only 6 backends in
``idle in transaction`` — an idle-in-txn-only census was GREEN on it
(P-10). The assertion that REDs on that shape is the IN-MEMORY
occupancy invariant: ``manager.engine.pool.checkedout()`` must return
to its baseline after every churn cycle.

── THE D-50 CENSUS SCOPE — EVERY pool-creation site ──────────────────

The census scope is EVERY pool-creation site in ``daemon/`` — **11
real sites** (12 enumerated, 1 comment false-positive), per
``BASELINE.md`` T3(a). The three engines this lane NAMED
(``ensemble-manager`` / ``ensemble-repair`` /
``ensemble-checkpoint-adapter``) are the ATTRIBUTION SUBSET, not the
census boundary:

| # | Site (base ``19fc11c8b`` line; marker re-verified at HEAD) | Kind |
|---|---|---|
| 1 | ``daemon/persistence.py:316`` — ``AsyncConnectionPool`` | P2 saver pool |
| 2 | ``daemon/persistence.py:355`` — ``asyncpg.create_pool`` | checkpoint adapter |
| 3 | ``daemon/__main__.py:237`` — ``create_engine`` | boot preflight |
| 4 | ``daemon/repositories/factory.py:184`` — SQLite | dialect-local |
| 5 | ``daemon/repositories/factory.py:215`` — dialect-agnostic | config-driven |
| 6 | ``daemon/repositories/factory.py:323`` — **MANAGER ENGINE** (``pool_size=5``) | the lane's site 1 |
| 7 | ``daemon/tools/ens_db_tools.py:633`` — repair engine (``pool_size=2``) | 5 slots |
| 8 | ``daemon/tools/ens_db_tools.py:691`` — vacuum ``NullPool`` | one-shot |
| 9 | ``daemon/services/vacuum_engine.py:79`` — vacuum ``NullPool`` | one-shot |
| 10 | ``daemon/services/db_pool_manager.py:261`` — ``create_pool`` | external DBs |
| 11 | ``daemon/services/wedge_capture/db_capture.py:195`` — ``psycopg.connect`` | one-shot, NO pool |
| — | ``daemon/repositories/factory.py:289`` — the COMMENT false-positive | not a site |

Attribution (D-24-amended/D-49): by ``application_name`` where present
(conninfo channel), else by scratch-DB isolation — every backend in
the scratch database belongs to this test.

The measured invariant (D-48): THREE-state occupancy + ``xact_age_s``
residence. The census count is REPORTED and BOUNDED — **never asserted
== 0**. λ (the borrow-arrival rate) was NOT measured (no timestamped
borrow-arrival series exists in the Phase-0 evidence) ⇒ the
``peak_occupancy ≤ λ × budget`` comparison is marked **UNMEASURABLE**
and NEVER asserted — ``peak_occupancy`` is reported as a disclosure
metric only.

Plantable mutation: hold 2 manager slots across the churn cycle ⇒
``pool.checkedout()`` does not return to baseline ⇒ RED.

Run (house form; the ``PG_TEST_USER`` role needs CREATEDB — the pack
creates and drops its own scratch database per D-23)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/integration/test_dbpool_churn_census.py \
        --override-ini="addopts=" -m integration -q --timeout=900
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlmodel import Session as SQLModelSession

from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.repositories.factory import create_postgres_engine
from daemon.services.readiness import MANAGER_POOL_CONFIGURED_CAPACITY

pytestmark = [pytest.mark.integration]

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The D-50 census scope, machine-checked: every real pool-creation
#: site's DISTINCTIVE marker must still exist in the tree (a drifted
#: site REDs the scope note instead of silently narrowing the
#: census). (file, marker) — the marker is unique to that site's
#: construction shape.
CENSUS_SCOPE_SITES: tuple[tuple[str, str], ...] = (
    ("daemon/persistence.py", "AsyncConnectionPool"),
    ("daemon/persistence.py", "asyncpg.create_pool"),
    ("daemon/__main__.py", "create_engine"),
    ("daemon/repositories/factory.py", "sqlite"),
    ("daemon/repositories/factory.py", "create_engine"),
    ("daemon/repositories/factory.py", "pool_size=5"),
    ("daemon/tools/ens_db_tools.py", "REPAIR_POOL_SIZE"),
    ("daemon/tools/ens_db_tools.py", "NullPool"),
    ("daemon/services/vacuum_engine.py", "NullPool"),
    ("daemon/services/db_pool_manager.py", "create_pool"),
    ("daemon/services/wedge_capture/db_capture.py", "psycopg.connect"),
)

_THREE_STATE_SQL = text(
    """
    SELECT
        state,
        application_name,
        CASE WHEN xact_start IS NULL THEN NULL
             ELSE extract(epoch FROM (now() - xact_start)) END AS xact_age_s
    FROM pg_stat_activity
    WHERE datname = :datname
      AND backend_start > :run_start
    """
)


def _census_three_state(conn, *, datname: str, run_start: datetime) -> dict[str, int]:
    rows = (
        conn.execute(_THREE_STATE_SQL, {"datname": datname, "run_start": run_start})
        .mappings()
        .all()
    )
    report = {"idle_no_xact": 0, "idle_in_transaction": 0, "active": 0, "other": 0}
    for row in rows:
        state = row["state"]
        if state == "idle" and row["xact_age_s"] is None:
            report["idle_no_xact"] += 1
        elif state == "idle in transaction":
            report["idle_in_transaction"] += 1
        elif state == "active":
            report["active"] += 1
        else:
            report["other"] += 1
    return report


class _ScratchDb:
    """A dedicated scratch database the test OWNS (created + dropped)."""

    def __init__(self) -> None:
        self.name = f"dbpool_churn_{uuid.uuid4().hex[:12]}"
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
        self.engine = create_postgres_engine(
            EnsembleConfig(
                database="postgres",
                postgres=PostgresConfig(
                    host=_PG_HOST,
                    port=_PG_PORT,
                    db=self.name,
                    user=_PG_USER,
                    password=_PG_PASSWORD,
                ),
            )
        )

    def three_state(self) -> dict[str, int]:
        with self.maintenance.connect() as conn:
            return _census_three_state(
                conn, datname=self.name, run_start=self.run_start
            )

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
    """The owned scratch database — or a skip when PG is unreachable."""
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
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS churn_cycle ("
                    "id serial primary key, note text)"
                )
            )
        yield db
    finally:
        db.dispose()


def _churn_round(engine, *, round_id: int, cycles: int = 6) -> None:
    """One churn round: ``cycles`` read/write borrows through the REAL
    manager engine (the resume-burst shape — sequential short
    transactions, each fully returned)."""
    for i in range(cycles):
        with SQLModelSession(engine) as session:
            session.execute(
                text(
                    f"INSERT INTO churn_cycle (note) "
                    f"VALUES ('round-{round_id}-cycle-{i}')"
                )
            )
            session.execute(text("SELECT count(*) FROM churn_cycle"))
            session.commit()


class TestF7eOccupancy:
    """F7-e — occupancy returns to baseline after the churn (D-48(a))."""

    @pytest.mark.timeout(600)
    def test_churn_returns_occupancy_to_baseline(self, scratch_db):
        engine = scratch_db.engine
        baseline = engine.pool.checkedout()
        samples: list[tuple[int, dict[str, int]]] = []
        stop = threading.Event()

        def _sampler() -> None:
            # §4.4: pg_stat_activity sampled every 250 ms alongside
            # the in-memory occupancy — best-effort, never fatal.
            while not stop.is_set():
                try:
                    samples.append(
                        (engine.pool.checkedout(), scratch_db.three_state())
                    )
                except Exception:  # noqa: BLE001 — sampling is best-effort
                    pass
                time.sleep(0.25)

        sampler = threading.Thread(target=_sampler, daemon=True)
        sampler.start()
        try:
            for round_id in range(3):
                _churn_round(engine, round_id=round_id)
                # A realistic inter-burst gap (resume bursts are not
                # back-to-back) — also guarantees the 250 ms sampler
                # catches the window (non-vacuity).
                time.sleep(0.35)
        finally:
            stop.set()
            sampler.join(timeout=5)

        # THE assertion that would have caught the incident (15/15
        # slots held): after the churn, the pool's occupancy is back
        # at its baseline.
        after = engine.pool.checkedout()
        assert after == baseline, (
            f"F7-e: checkedout()={after} did not return to "
            f"baseline={baseline} after the churn — the occupancy "
            "defect shape is present"
        )
        # Non-vacuity: the sampler actually sampled during the churn.
        assert len(samples) >= 3, (
            f"F7-e: the sampler caught only {len(samples)} samples — "
            "the census is vacuous"
        )
        # The census count is REPORTED and BOUNDED — never asserted
        # == 0 (P-10). peak_occupancy is a DISCLOSURE metric; the
        # λ × budget comparison is UNMEASURABLE (λ was not measured)
        # and is never asserted.
        peak_occupancy = max(s[0] for s in samples)
        peak_census = max(sum(s[1].values()) for s in samples)
        final_report = scratch_db.three_state()
        assert peak_census <= MANAGER_POOL_CONFIGURED_CAPACITY, (
            f"F7-e: the census reported {peak_census} backends in the "
            "scratch DB at peak — above the manager pool's configured "
            f"capacity ({MANAGER_POOL_CONFIGURED_CAPACITY})"
        )
        # Reported, bounded — NEVER asserted == 0 (P-10): pooled-but-
        # open connections legitimately sit 'idle' with no transaction
        # after the churn; that is occupancy the census REPORTS.
        assert sum(final_report.values()) <= MANAGER_POOL_CONFIGURED_CAPACITY, (
            f"F7-e: the post-churn census is out of bound: "
            f"{final_report}"
        )

    @pytest.mark.timeout(600)
    def test_two_held_slots_across_the_churn_do_not_return_to_baseline(
        self, scratch_db
    ):
        # The incident demonstration + the mutation leg: hold 2
        # manager slots across a churn round — exactly the occupancy
        # defect class. The guard's occupancy assertion MUST fire
        # (this is the detection proof; the committed healthy-cycle
        # assertions above are the ones that RED under this plant).
        engine = scratch_db.engine
        baseline = engine.pool.checkedout()
        held = [engine.connect() for _ in range(2)]
        try:
            for conn in held:
                conn.execute(text("SELECT 1"))  # autobegin: occupy a slot
            _churn_round(engine, round_id=99, cycles=3)
            during = engine.pool.checkedout()
            report = scratch_db.three_state()
            assert during == baseline + 2, (
                "F7-e: holding 2 slots across the churn did not hold "
                f"2 slots (checkedout={during}, baseline={baseline})"
            )
            assert (
                report["idle_in_transaction"] >= 1
                or report["idle_no_xact"] >= 1
            ), (
                f"F7-e: the three-state census sees NONE of the held "
                f"slots (report={report}) — the census is vacuous "
                "against the occupancy class"
            )
        finally:
            for conn in held:
                conn.rollback()
                conn.close()
        # Once released, occupancy returns — the guard discriminates
        # a held-slot defect from a permanent leak.
        assert engine.pool.checkedout() == baseline, (
            "F7-e: occupancy did not return to baseline after the "
            "held slots were released"
        )

    @pytest.mark.timeout(120)
    def test_census_scope_covers_every_pool_creation_site(self):
        # D-50: the census scope is EVERY pool-creation site — the
        # 11 real sites' distinctive markers must exist in the tree,
        # so a drifted/narrowed census scope REDs instead of silently
        # losing a pool. (The factory.py:289 comment false-positive is
        # deliberately NOT a row — it is not a site.)
        missing = [
            (rel, marker)
            for rel, marker in CENSUS_SCOPE_SITES
            if marker not in (_REPO_ROOT / rel).read_text()
        ]
        assert not missing, (
            "F7-e/D-50: census-scope site markers missing from the "
            f"tree — the census scope has drifted: {missing}"
        )
