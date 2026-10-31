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

**SCOPING NOTE — the previously-disclosed born-broken census defect
was CLOSED by the R-10 rider (wi-78967dba, 2026-10-31): the store
census now LEFT JOINs pg_stat_user_tables (``s.n_live_tup`` — the
column never existed on pg_class), so the census is POPULATED on
real PG and the InFailedSqlTransaction cascade into the third read
is gone. The pack's (ii) test re-pointed from "failure mode is not
the F5 cascade" to "census POPULATED" (deterministic ``reltuples``
via ANALYZE; NULL-safe ``n_live_tup``; ``total_bytes`` >= one page
for a seeded table). Plantable mutation: restore ``c.n_live_tup``
in ``daemon/services/wedge_capture/db_capture.py`` ⇒ (ii) flips to
``failed:UndefinedColumn`` ⇒ RED. The original STOP-item history
(owner = wedge-capture lane, no active owner lane) is recorded in
the v0.27.13 staged-release composition metadata.

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


def _seed_checkpoint_tables(pg_engine) -> None:
    """R-10 (wi-78967dba): minimal-but-real census targets. The
    langgraph checkpoint tables are NOT part of the SQLModel
    metadata (the conftest's ``create_all`` never makes them), so
    the pack creates minimal shapes for the four CHECKPOINT_TABLES
    names, seeds rows, and ANALYZEs (``reltuples`` is the
    deterministic ANALYZE figure; ``n_live_tup`` is the stats-
    collector estimate — exercised NULL-safely)."""
    from sqlalchemy import text

    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "thread_id TEXT NOT NULL, "
                "checkpoint BYTEA, "
                "metadata JSONB)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS checkpoint_blobs ("
                "thread_id TEXT NOT NULL, "
                "channel TEXT NOT NULL, "
                "version BIGINT NOT NULL, "
                "type TEXT NOT NULL, "
                "blob BYTEA)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS checkpoint_writes ("
                "thread_id TEXT NOT NULL, "
                "checkpoint_ns TEXT NOT NULL, "
                "checkpoint_id TEXT NOT NULL, "
                "task_id TEXT NOT NULL, "
                "idx INTEGER NOT NULL, "
                "channel TEXT NOT NULL, "
                "type TEXT NOT NULL, "
                "blob BYTEA)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS checkpoint_migrations ("
                "version INTEGER NOT NULL, "
                "name TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO checkpoints (thread_id) VALUES "
                "('f5b-thread-1'), ('f5b-thread-2')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO checkpoint_blobs "
                "(thread_id, channel, version, type, blob) VALUES "
                "('f5b-thread-1', '__root__', 1, 'msgpack', "
                "'\\x00'::bytea)"
            )
        )
        conn.execute(text("ANALYZE checkpoints"))
        conn.execute(text("ANALYZE checkpoint_blobs"))


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
        """(ii) — R-10 re-point (wi-78967dba): the store census is
        POPULATED on real PG. The pre-fix ``c.n_live_tup`` from
        pg_class ALWAYS failed UndefinedColumn (born-broken); now the
        LEFT JOIN pg_stat_user_tables shape populates per-table
        entries with NULL-safe values (``reltuples`` deterministic
        via ANALYZE; ``n_live_tup`` the stats-collector estimate —
        int or None; ``total_bytes`` >= one page for a seeded table),
        the cascade signature is absent, and the read AFTER the
        census on the same connection still works."""
        _seed(pg_engine)
        _seed_checkpoint_tables(pg_engine)
        payload = _capture(pg_engine)
        assert payload["status"] == STATUS_OK, payload.get("reason")
        census = payload["checkpoint_store_census"]
        assert "status" not in census, (
            "the census must be populated, not failed: "
            f"{census!r} — the R-10 LEFT JOIN is missing (plant: "
            "restore c.n_live_tup ⇒ UndefinedColumn)"
        )
        assert "failed:UndefinedColumn" not in repr(census)
        # Populated: per-table entries for the seeded census targets
        # with the three documented value keys.
        for key in ("checkpoints", "checkpoint_blobs"):
            assert key in census, census
            entry = census[key]
            assert set(entry) >= {
                "reltuples", "n_live_tup", "total_bytes"
            }, entry
            assert entry["reltuples"] is None or (
                entry["reltuples"] >= 1
            ), entry
            assert entry["n_live_tup"] is None or (
                entry["n_live_tup"] >= 0
            ), entry  # NULL-safe consumer path exercised
            assert entry["total_bytes"] is None or (
                entry["total_bytes"] >= 8192
            ), entry  # one page for a table with rows
        # The summary keys the consumer derives (:398-400).
        assert "checkpoints_rows" in census
        assert "checkpoint_blobs_rows" in census
        assert "checkpoint_blobs_bytes" in census
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
