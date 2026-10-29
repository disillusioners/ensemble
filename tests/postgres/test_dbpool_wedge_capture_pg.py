"""F5-b pin — the wedge-capture read path on REAL PostgreSQL (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F5.1–F5.3 and §5.2; phase2-plan
T2.9 / AC20 / AC21 / D-52. The FIX (the ``tasks``→``task`` rename)
landed in Phase 1 (T1.13, commit cf1890f61); THIS pack executes the
PG correctness leg at gate G2.6. Marker: ``postgres`` (auto-applied
by ``tests/postgres/conftest.py``).

What this pin asserts (against a real scratch PG whose ``task`` table
carries ``auto_continued_at`` — the column added by
``daemon/manager.py``'s ensure-columns path):

* **(i)** the executing-turns read returns the seeded RUNNING row —
  with NO ``relation "tasks" does not exist`` (the rename);
* **(ii)** the SUBSEQUENT read on the same connection does NOT fail
  with ``current transaction is aborted`` — the F5 cascade signature
  (DR-34: at base "step 1 fails ``UndefinedTable: relation tasks does
  not exist``, steps 2-3 fail ``InFailedSqlTransaction``") no longer
  reproduces: the first read succeeds, so the connection's
  transaction is never aborted BY IT;
* **(iii)** the resume-history read returns its seeded row without
  error on the same one-shot connection as the executing-turns read
  (driven directly on the two F5-owned reads — see the scoping note).

**SCOPING NOTE — a pre-existing, OUT-OF-LANE defect is disclosed (STOP
item per the dispatch's spec-vs-tree rule):** the capture path's
SECOND read — the store census (``_read_store_census``,
``daemon/services/wedge_capture/db_capture.py:347``) — is BORN-BROKEN
on real PostgreSQL: its SQL selects ``c.n_live_tup`` from
``pg_class``, but ``n_live_tup`` is a ``pg_stat_user_tables`` column
and does not exist on ``pg_class`` in any PG version ⇒ the census
ALWAYS fails with ``UndefinedColumn`` on a real server (measured on
the scratch PG16: ``column c.n_live_tup does not exist``), aborting
the capture connection's transaction and cascading
``InFailedSqlTransaction`` into the THIRD read (resume history) in
the full ``capture_db_snapshot`` path. The contract's F5.3 says "No
other change to the module" and F5-d says the census "still works" —
that premise is FALSE at tree reality (the census never worked on a
real server; at BASE its failure was indistinguishable from the
rename cascade because BOTH read-1 and the census aborted the
transaction). This pack therefore (a) asserts the census failure mode
is NOT the F5 cascade signature, (b) drives the two F5-OWNED reads
directly on one connection for (iii), and (c) does NOT touch the
census SQL — the born-broken census is reported to the dispatcher as
a spec-vs-tree STOP item, owner = the wedge-capture lane.

BASE-vs-HEAD: at BASE (``FROM tasks``) the executing-turns read fails
swallowed (``[]``/0), the connection's transaction ABORTS, and the
census reports ``failed:InFailedSqlTransaction`` (the cascade) ⇒
(i), (ii) and (iii) all RED. At HEAD: (i) the row round-trips,
(ii) the census's failure — when the scratch DB carries no checkpoint
tables it succeeds; the failure mode is never the cascade — and
(iii) both F5-owned reads return their seeded rows.

Plantable mutation: re-introduce ``FROM tasks`` / ``tasks t`` in
``daemon/services/wedge_capture/db_capture.py`` ⇒ (i) loses the row,
(ii) flips to the cascade signature, (iii) read-1 returns nothing and
read-2 aborts ⇒ RED.

Run (house form — scratch PG, per-variable env scrub, marker override)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/postgres/test_dbpool_wedge_capture_pg.py \
        --override-ini="addopts=" -m postgres -q --timeout=300
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

# Register the task table BEFORE ``create_all`` runs (conftest already
# imports it; kept explicit for the pack's own readability).
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.task.models import Task
from daemon.services.wedge_capture.db_capture import (
    STATUS_OK,
    _read_executing_turns,
    _read_resume_history,
    capture_db_snapshot,
    resolve_dsn,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

_RUNNING_WORK_ID = "f5b-wedge-capture-running"
_RESUMED_WORK_ID = "f5b-wedge-capture-resumed"


def _seed(pg_engine) -> None:
    """Seed one RUNNING turn + one auto-continued (resumed) turn."""
    with Session(pg_engine) as session:
        session.add(
            Task(
                work_id=_RUNNING_WORK_ID,
                instance_id="f5b-inst-running",
                status="running",
            )
        )
        session.add(
            Task(
                work_id=_RESUMED_WORK_ID,
                instance_id="f5b-inst-resumed",
                status="completed",
                auto_continued_at=datetime(2026, 10, 28, 12, 0, 0),
            )
        )
        session.commit()


def _capture(pg_engine) -> dict:
    dsn = resolve_dsn(pg_engine)
    assert dsn and dsn.startswith("postgresql://")
    return capture_db_snapshot(dsn, boot_epoch="f5b-boot-epoch")


class TestF5bWedgeCaptureReadPath:
    """F5-b — the reads target ``task`` and the F5 cascade is gone."""

    def test_executing_turns_read_returns_the_running_row(self, pg_engine):
        _seed(pg_engine)
        payload = _capture(pg_engine)
        assert payload["status"] == STATUS_OK, payload.get("reason")
        work_ids = [t["work_id"] for t in payload["executing_turns"]]
        assert _RUNNING_WORK_ID in work_ids
        assert payload["executing_turns_count"] >= 1

    def test_subsequent_read_not_the_f5_cascade_signature(self, pg_engine):
        """(ii) — the read AFTER the executing-turns read on the SAME
        connection must not fail with 'current transaction is aborted'
        (the F5 cascade signature — DR-34's steps 2-3). The F5-owned
        first read succeeds, so nothing it did can abort the
        transaction. (The census's OWN born-broken failure is
        disclosed in the module docstring — out of lane.)"""
        _seed(pg_engine)
        payload = _capture(pg_engine)
        assert payload["status"] == STATUS_OK, payload.get("reason")
        census_status = payload["checkpoint_store_census"].get("status")
        assert census_status != "failed:InFailedSqlTransaction", (
            "F5-b: the read after the executing-turns read failed with "
            "the aborted-transaction cascade — the tasks→task rename "
            "did not land"
        )
        # And the F5 root error is absent from the whole payload.
        dumped = repr(payload)
        assert 'relation "tasks" does not exist' not in dumped

    def test_both_f5_reads_on_one_connection_return_their_rows(
        self, pg_engine
    ):
        """(iii) — the two F5-OWNED reads (executing turns + resume
        history) driven DIRECTLY on ONE raw psycopg connection: at
        HEAD both return their seeded rows; at BASE the first read
        aborts the transaction and the second returns nothing."""
        import psycopg

        _seed(pg_engine)
        dsn = resolve_dsn(pg_engine)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cursor:
                turns, count = _read_executing_turns(cursor)
                assert _RUNNING_WORK_ID in [t["work_id"] for t in turns]
                assert count >= 1
                # The SUBSEQUENT F5-owned read on the SAME connection.
                history = _read_resume_history(cursor)
                assert _RESUMED_WORK_ID in [
                    r["work_id"] for r in history
                ], (
                    "F5-b: the resume-history read did not return its "
                    "row on the same connection — the cascade"
                )
                resumed = next(
                    r
                    for r in history
                    if r["work_id"] == _RESUMED_WORK_ID
                )
                assert resumed["auto_continued_at"] is not None
