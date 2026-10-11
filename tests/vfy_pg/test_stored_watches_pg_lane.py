"""PG-lane verification pack — stored_watches quarantine (fix e8a61da3).

Commission Priority 2: PostgreSQL coverage the developer's SQLite-only
runs (``tests/job_queue/test_retry_supersede_watch_recovery.py``) could
not prove. Runs against a DISPOSABLE local PostgreSQL 16 cluster only.

Disposable-cluster contract (foreign-cluster refusal, fail closed):

* ``PG_TEST_HOST`` must be ``127.0.0.1`` and ``PG_TEST_PORT`` within
  15000-19999 — anything else fails before any connection is opened.
* Cluster lifecycle (initdb/pg_ctl/createdb/teardown) is owned by the
  orchestrating wrapper ``test/packs/storedwatches_pg_integration_test.sh``
  and its caller; this module never starts or stops servers.

Five tests (REAL production code paths — no mocking of units under test):

1. ``create_all`` index parity on PG (pg_indexes + pg_constraint).
2. Migration ``.sql`` parity + double-apply idempotency (fresh 2nd DB).
3. ON CONFLICT first-store-wins (the hook's raw INSERT shape).
4. JSONB CAST path via the REAL ``RetryTurn._rearm_watchers_in_txn``
   driven through a real ``RetryTurn.run`` inside ``engine.begin()``
   (mirrors tests/job_queue/test_retry_supersede_watch_recovery.py).
5. ``StoredWatchRepository`` roundtrip: aged scan / CAS / provenance /
   defensive JSON parse.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

pytestmark = pytest.mark.postgres

# ---------------------------------------------------------------------------
# Metadata registration — same module set as tests/postgres/conftest.py, so
# SQLModel.metadata.create_all builds the production-shaped PG schema.
# ---------------------------------------------------------------------------
import daemon.repositories.instance.models  # noqa: F401,E402
import daemon.repositories.message_queue.models  # noqa: F401,E402
import daemon.repositories.task.models  # noqa: F401,E402
import daemon.repositories.event.models  # noqa: F401,E402
import daemon.repositories.db_connection.models  # noqa: F401,E402
import daemon.repositories.job_queue.models  # noqa: F401,E402
import daemon.repositories.job_queue.watcher_models  # noqa: F401,E402
import daemon.repositories.job_queue.stored_watch_models  # noqa: F401,E402
import daemon.repositories.mcp_server.models  # noqa: F401,E402
import daemon.repositories.infra.models  # noqa: F401,E402
import daemon.repositories.project.models  # noqa: F401,E402
import daemon.repositories.source.models  # noqa: F401,E402
import daemon.migrations.models  # noqa: F401,E402

from daemon.repositories.job_queue.stored_watch_models import (  # noqa: E402
    StoredWatch,
)
from daemon.repositories.job_queue.stored_watch_repository import (  # noqa: E402
    StoredWatchRepository,
)
from daemon.repositories.job_queue.watcher_models import (  # noqa: E402
    ALL_WATCHABLE_EVENTS,
)
from daemon.services.timestamps import now_utc_naive  # noqa: E402
from daemon.services.turn_transitions import RetryTurn  # noqa: E402

# ---------------------------------------------------------------------------
# Connection — disposable cluster coordinates (wrapper exports PG_TEST_*).
# Trust auth: no password anywhere.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261011_000001_create_stored_watches.sql"
)

PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "15533"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_vfy_sw")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
MIG_DB = f"{PG_DB}_mig"

PG_URL = f"postgresql+psycopg://{PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}"
PG_URL_MIG = f"postgresql+psycopg://{PG_USER}@{PG_HOST}:{PG_PORT}/{MIG_DB}"
PG_URL_ADMIN = f"postgresql+psycopg://{PG_USER}@{PG_HOST}:{PG_PORT}/postgres"

# The 5 declared names — MUST stay byte-identical to both the model's
# __table_args__ (stored_watch_models.py) and create_stored_watches.sql.
DECLARED_INDEX_NAMES = {
    "uq_stored_watches_original_work_watcher",
    "idx_stored_watches_original_work_id",
    "idx_stored_watches_retry_work_id",
    "idx_stored_watches_watcher_instance_id",
    "idx_stored_watches_status_created",
}
# PG implements the model's PRIMARY KEY as its own index — expected
# infrastructure, not part of the 5 declared names.
EXPECTED_PKEY_INDEX = "stored_watches_pkey"
DECLARED_COLUMNS = {
    "stored_watch_id",
    "original_work_id",
    "retry_work_id",
    "watcher_instance_id",
    "original_watch_events",
    "mission_instance_id",
    "work_instance_id",
    "status",
    "created_at",
    "updated_at",
}


def _assert_disposable_target() -> None:
    """Fail closed unless pointed at the disposable local cluster."""
    if PG_HOST != "127.0.0.1":
        pytest.fail(
            f"REFUSED: PG_TEST_HOST must be 127.0.0.1 (disposable-cluster "
            f"contract), got {PG_HOST!r}"
        )
    if not (15000 <= PG_PORT <= 19999):
        pytest.fail(
            f"REFUSED: PG_TEST_PORT must be within 15000-19999 "
            f"(never 5432/5433/foreign), got {PG_PORT}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pg_engine():
    """Primary engine — full production schema via create_all."""
    _assert_disposable_target()
    eng = create_engine(PG_URL, pool_pre_ping=True)
    # Fresh-schema idempotency: disposable DB starts empty; drop_all is a
    # no-op there and makes re-runs safe.
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _truncate_all(pg_engine):
    """Per-test isolation — mirrors tests/postgres/conftest.py TRUNCATE."""
    names = ", ".join(
        f'"{t.name}"' for t in reversed(SQLModel.metadata.sorted_tables)
    )
    with pg_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(scope="module")
def mig_engine():
    """Second FRESH database for the migration .sql statements.

    Only the FK prerequisite (``instances`` — the migration's
    ``REFERENCES instances(instance_id)``) is created there; the
    migration itself must produce ``stored_watches`` from nothing.
    """
    _assert_disposable_target()
    admin = create_engine(PG_URL_ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{MIG_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{MIG_DB}"'))
    eng = create_engine(PG_URL_MIG, pool_pre_ping=True)
    SQLModel.metadata.tables["instances"].create(eng)
    yield eng
    eng.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{MIG_DB}" WITH (FORCE)'))
    admin.dispose()


# ---------------------------------------------------------------------------
# Seed helpers (production-shaped; PG enforces the FKs SQLite left off)
# ---------------------------------------------------------------------------
def _seed_instance(
    eng,
    instance_id: str,
    *,
    parent_id: str | None = None,
    status: str = "running",
) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instances (instance_id, parent_id, agent_id, "
                "agent_dir, status, project_id, created_at, updated_at, "
                "version, last_activity_at) VALUES "
                "(:iid, :pid, 'developer', 'agents/developer', :status, "
                "'p', '2026-10-11T07:00:00', '2026-10-11T07:00:00', 1, "
                "'2026-10-11T08:00:00')"
            ),
            {"iid": instance_id, "pid": parent_id, "status": status},
        )


def _seed_task(eng, *, work_id: str, instance_id: str) -> None:
    # PG: boolean columns take FALSE literals — integer 0 does NOT
    # assignment-cast to boolean on PostgreSQL (SQLite tolerated it).
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task (task_type, instance_id, message_id, "
                "status, retry_count, created_at, cancel_requested, "
                "retry_scheduled, work_id, is_deferred, is_background) "
                "VALUES ('process_message', :iid, 'm1', 'running', 0, "
                "'2026-10-11T08:00:00', FALSE, FALSE, :wid, FALSE, FALSE)"
            ),
            {"iid": instance_id, "wid": work_id},
        )


def _seed_watch(
    eng, *, job_id: str, watcher: str, events: list[str]
) -> None:
    """Seed a job_watchers row — explicit CAST, mirroring the hook's
    dialect-aware JSONB write style."""
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO job_watchers (watch_id, job_id, instance_id, "
                "watch_events, created_at) VALUES "
                "(:wid, :jid, :inst, CAST(:ev AS JSONB), '2026-10-11T08:00:00')"
            ),
            {
                "wid": f"watch-{job_id}-{watcher}",
                "jid": job_id,
                "inst": watcher,
                "ev": json.dumps(list(events)),
            },
        )


def _watch_events(eng, job_id: str) -> list[str]:
    """Raw read of job_watchers.watch_events (PG driver returns a list)."""
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT watch_events FROM job_watchers WHERE job_id = :jid"),
            {"jid": job_id},
        ).fetchone()
    if row is None:
        return []
    raw = row.watch_events
    return list(raw) if isinstance(raw, list) else json.loads(raw)


def _stored_rows(eng) -> list:
    with eng.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT original_work_id, retry_work_id, "
                    "watcher_instance_id, mission_instance_id, "
                    "work_instance_id, status, original_watch_events "
                    "FROM stored_watches ORDER BY created_at"
                )
            )
            .mappings()
            .all()
        )


def _pg_index_names(eng) -> set[str]:
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'stored_watches'"
            )
        ).fetchall()
    return {r.indexname for r in rows}


def _pg_index_defs(eng) -> dict[str, str]:
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'stored_watches'"
            )
        ).fetchall()
    return {r.indexname: r.indexdef for r in rows}


def _pg_uq_constraints(eng) -> list[str]:
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'stored_watches'::regclass AND contype = 'u'"
            )
        ).fetchall()
    return [r.conname for r in rows]


def _pg_column_names(eng) -> set[str]:
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'stored_watches'"
            )
        ).fetchall()
    return {r.column_name for r in rows}


_EVIDENCE_PATH = Path("/tmp/storedwatches_pg_evidence.jsonl")


def _emit_evidence(tag: str, payload: dict) -> None:
    """Append JSONL evidence OUTSIDE the repo tree (report input)."""
    import json as _json

    with _EVIDENCE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(
            _json.dumps({"tag": tag, **payload}, default=str) + "\n"
        )


def _pg_column_types(eng) -> dict[str, str]:
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'stored_watches'"
            )
        ).fetchall()
    return {r.column_name: r.data_type for r in rows}


def _insert_stored_watch(
    eng,
    *,
    stored_watch_id: str,
    original_work_id: str,
    retry_work_id: str,
    watcher_instance_id: str,
    events: list[str],
    mission_instance_id: str,
    work_instance_id: str,
    status: str,
    created_at,
):
    """Raw INSERT with explicit JSONB CAST — the hook's PG insert shape."""
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO stored_watches ("
                "stored_watch_id, original_work_id, retry_work_id, "
                "watcher_instance_id, original_watch_events, "
                "mission_instance_id, work_instance_id, status, "
                "created_at, updated_at) VALUES ("
                ":stored_watch_id, :original_work_id, :retry_work_id, "
                ":watcher_instance_id, CAST(:original_events AS JSONB), "
                ":mission_instance_id, :work_instance_id, :status, "
                ":now, :now)"
            ),
            {
                "stored_watch_id": stored_watch_id,
                "original_work_id": original_work_id,
                "retry_work_id": retry_work_id,
                "watcher_instance_id": watcher_instance_id,
                "original_events": json.dumps(list(events)),
                "mission_instance_id": mission_instance_id,
                "work_instance_id": work_instance_id,
                "status": status,
                "now": created_at,
            },
        )


# ===========================================================================
# Test 1 — create_all index parity on PG
# ===========================================================================
class TestCreateAllIndexParity:
    def test_exactly_declared_names_no_default_family(self, pg_engine):
        eng = pg_engine
        names = _pg_index_names(eng)
        extra = names - DECLARED_INDEX_NAMES - {EXPECTED_PKEY_INDEX}
        missing = DECLARED_INDEX_NAMES - names
        assert not extra, (
            f"create_all produced UNDECLARED indexes on stored_watches: "
            f"{sorted(extra)} (full set: {sorted(names)})"
        )
        assert not missing, (
            f"create_all MISSED declared indexes: {sorted(missing)} "
            f"(full set: {sorted(names)})"
        )
        assert len(names) == 6  # 5 declared + pkey
        # NO ix_* default-family index on stored_watches.
        assert not [n for n in names if n.startswith("ix_")], (
            f"default-family ix_* indexes leaked: {sorted(names)}"
        )
        # The unique fence exists as a TABLE CONSTRAINT on the create_all
        # path (SQLModel UniqueConstraint → PG UNIQUE constraint).
        uq = _pg_uq_constraints(eng)
        assert uq == ["uq_stored_watches_original_work_watcher"], uq
        # ...and PG implements that constraint as the uq-named index.
        defs = _pg_index_defs(eng)
        assert "(original_work_id, watcher_instance_id)" in defs[
            "uq_stored_watches_original_work_watcher"
        ], defs["uq_stored_watches_original_work_watcher"]
        # Composite index covers (status, created_at) in order.
        assert "(status, created_at)" in defs[
            "idx_stored_watches_status_created"
        ], defs["idx_stored_watches_status_created"]
        # Column-name parity with the declared shape.
        cols = _pg_column_names(eng)
        assert cols == DECLARED_COLUMNS, (
            f"column drift: extra={sorted(cols - DECLARED_COLUMNS)} "
            f"missing={sorted(DECLARED_COLUMNS - cols)}"
        )
        _emit_evidence(
            "create_all_path",
            {
                "index_names": sorted(names),
                "index_defs": defs,
                "column_types": _pg_column_types(eng),
                "uq_constraints": uq,
            },
        )
        # JSONBType resolves to real JSONB on PG (not json/text).
        with eng.begin() as conn:
            dt = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'stored_watches' "
                    "AND column_name = 'original_watch_events'"
                )
            ).scalar()
        assert dt == "jsonb", f"original_watch_events data_type={dt!r}"

    def test_jsonb_type_on_job_watchers_too(self, pg_engine):
        """job_watchers.watch_events is JSONB on PG — the CAST target the
        hook writes through must exist with the expected type."""
        with pg_engine.begin() as conn:
            dt = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'job_watchers' "
                    "AND column_name = 'watch_events'"
                )
            ).scalar()
        assert dt == "jsonb", f"job_watchers.watch_events data_type={dt!r}"


# ===========================================================================
# Test 2 — migration .sql parity + double-apply idempotency on PG
# ===========================================================================
def _migration_up_statements() -> list[str]:
    raw = _MIGRATION_SQL.read_text(encoding="utf-8")
    assert "-- UP" in raw and "-- DOWN" in raw, "migration markers missing"
    up_block = raw.split("-- UP", 1)[1].split("-- DOWN", 1)[0]
    stmts = []
    for chunk in up_block.split(";"):
        lines = [
            ln
            for ln in chunk.splitlines()
            if not ln.strip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            stmts.append(stmt)
    return stmts


class TestMigrationParityAndIdempotency:
    def test_up_block_creates_same_5_indexes_and_is_idempotent(
        self, mig_engine
    ):
        eng = mig_engine
        stmts = _migration_up_statements()
        assert len(stmts) == 6, (
            f"expected 6 UP statements (1 CREATE TABLE + 5 CREATE INDEX), "
            f"got {len(stmts)}: {stmts}"
        )
        assert sum("CREATE TABLE" in s for s in stmts) == 1
        # NOTE: substring "CREATE INDEX" misses "CREATE UNIQUE INDEX" —
        # count by the INDEX keyword instead.
        assert sum("INDEX" in s.upper() for s in stmts) == 5

        # First apply — on a fresh DB with only the FK prerequisite.
        with eng.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))

        after_first = _pg_index_names(eng)
        extra = after_first - DECLARED_INDEX_NAMES - {EXPECTED_PKEY_INDEX}
        missing = DECLARED_INDEX_NAMES - after_first
        assert not extra, f"migration produced extra indexes: {sorted(extra)}"
        assert not missing, f"migration missed indexes: {sorted(missing)}"
        assert not [n for n in after_first if n.startswith("ix_")]
        # Index DEFINITIONS match the create_all path (not just names).
        defs = _pg_index_defs(eng)
        for name in DECLARED_INDEX_NAMES:
            assert name in defs, f"missing indexdef for {name}"
        assert "(status, created_at)" in defs[
            "idx_stored_watches_status_created"
        ]
        assert "(original_work_id, watcher_instance_id)" in defs[
            "uq_stored_watches_original_work_watcher"
        ]
        # FINDING EVIDENCE: the migration carries the unique fence as a
        # UNIQUE INDEX (no pg_constraint entry), while create_all makes a
        # UNIQUE CONSTRAINT — same enforcement, different catalog home.
        assert _pg_uq_constraints(eng) == [], (
            "migration path unexpectedly created a table-level UNIQUE "
            "CONSTRAINT (expected UNIQUE INDEX only)"
        )
        # Type evidence: the .sql says `JSON` (its SQLite-first
        # rendering) which PG accepts as plain `json`, while the model's
        # JSONBType resolves to `jsonb` on PG. Production PG never runs
        # the .sql (create_all owns the PG path) — divergence recorded,
        # not asserted equal.
        mig_types = _pg_column_types(eng)
        _emit_evidence(
            "migration_path",
            {
                "index_defs": defs,
                "column_types": mig_types,
                "uq_constraints": _pg_uq_constraints(eng),
            },
        )
        assert mig_types["original_watch_events"] in ("json", "jsonb"), (
            mig_types
        )
        # Column-name parity vs the create_all path lives in
        # test_column_name_parity_migration_vs_create_all (needs both
        # engines — module-scoped fixtures).

        # DOUBLE APPLY — IF NOT EXISTS semantics must make the second
        # pass a silent no-op (no error, index set unchanged).
        with eng.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))  # must not raise
        after_second = _pg_index_names(eng)
        assert after_second == after_first, (
            f"index set drifted after double apply: "
            f"{sorted(after_first)} -> {sorted(after_second)}"
        )

    def test_column_name_parity_migration_vs_create_all(
        self, mig_engine, pg_engine
    ):
        """The .sql and the model must agree on the column SET."""
        mig_cols = _pg_column_names(mig_engine)
        model_cols = _pg_column_names(pg_engine)
        assert mig_cols == model_cols == DECLARED_COLUMNS, (
            f"migration-only columns: {sorted(mig_cols - model_cols)}; "
            f"model-only columns: {sorted(model_cols - mig_cols)}"
        )


# ===========================================================================
# Test 3 — ON CONFLICT first-store-wins on PG
# ===========================================================================
class TestOnConflictFirstStoreWins:
    def test_duplicate_insert_second_is_noop(self, pg_engine):
        eng = pg_engine
        # FK prerequisites the PG lane enforces (SQLite left them off):
        # stored_watches.watcher_instance_id -> instances.instance_id.
        _seed_instance(eng, "root-1")
        _seed_instance(eng, "worker-1", parent_id="root-1")
        _seed_instance(eng, "watcher-1")

        hook_shape_sql = text(
            "INSERT INTO stored_watches ("
            "stored_watch_id, original_work_id, retry_work_id, "
            "watcher_instance_id, original_watch_events, "
            "mission_instance_id, work_instance_id, status, "
            "created_at, updated_at) VALUES ("
            ":stored_watch_id, :original_work_id, :retry_work_id, "
            ":watcher_instance_id, CAST(:original_events AS JSONB), "
            ":mission_instance_id, :work_instance_id, :status, "
            ":now, :now) "
            "ON CONFLICT (original_work_id, watcher_instance_id) DO NOTHING"
        )
        base_params = {
            "original_work_id": "W1",
            "retry_work_id": "W2",
            "watcher_instance_id": "watcher-1",
            "original_events": json.dumps(["task_completed"]),
            "mission_instance_id": "root-1",
            "work_instance_id": "worker-1",
            "status": "armed",
            "now": now_utc_naive(),
        }
        with eng.begin() as conn:
            first = conn.execute(
                hook_shape_sql,
                {**base_params, "stored_watch_id": str(uuid.uuid4())},
            )
            second = conn.execute(
                hook_shape_sql,
                {
                    **base_params,
                    "stored_watch_id": str(uuid.uuid4()),
                    "retry_work_id": "W2-alt",  # a duplicate deferral
                },
            )
        assert first.rowcount == 1, f"first insert rowcount={first.rowcount}"
        assert second.rowcount == 0, (
            f"second insert MUST be a no-op (DO NOTHING), "
            f"rowcount={second.rowcount}"
        )
        rows = _stored_rows(eng)
        assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}"
        # First store wins: the ORIGINAL retry_work_id survives.
        assert rows[0]["retry_work_id"] == "W2"
        assert rows[0]["original_watch_events"] == ["task_completed"]


# ===========================================================================
# Test 4 — JSONB CAST path via the REAL hook (RetryTurn, engine.begin())
# ===========================================================================
_CHILD_KWARGS = {
    "task_type": "process_message",
    "message_id": None,
    "retry_count": 1,
    "next_retry_at": None,
    "created_at": "2026-10-11T08:00:00",
    "cancel_requested": False,
    "retry_scheduled": False,
    "is_deferred": False,
    "is_background": False,
}


class TestRealHookJsonbCastPath:
    def test_retryturn_rearm_writes_jsonb_and_quarantine(self, pg_engine):
        eng = pg_engine
        _seed_instance(eng, "root-1")  # mission ROOT (parent_id = NULL)
        _seed_instance(eng, "worker-1", parent_id="root-1")
        _seed_instance(eng, "watcher-1")  # FK target for job_watchers
        _seed_task(eng, work_id="W1", instance_id="worker-1")
        _seed_watch(
            eng,
            job_id="W1",
            watcher="watcher-1",
            events=list(ALL_WATCHABLE_EVENTS),
        )

        # REAL production path: RetryTurn.run inside engine.begin().
        with eng.begin() as conn:
            result = RetryTurn(
                parent_work_id="W1",
                child_work_id="W2",
                task_repo=None,
                instance_id="worker-1",
                child_kwargs=dict(_CHILD_KWARGS),
                rearm_watchers=True,
            ).run(conn)

        # Side-effect tokens prove the hook RAN (not just the re-key).
        assert result.cross_turn_side_effects == (
            "migrate_job_watchers",
            "rearm_stored_watches",
        ), result.cross_turn_side_effects

        # 1. job_watchers.watch_events CONTAINS mission_terminal — the PG
        #    driver returns the JSONB as a parsed list; the CAST(:v AS
        #    JSONB) write round-tripped.
        events_on_child = _watch_events(eng, "W2")
        assert "mission_terminal" in events_on_child, events_on_child
        # Add-only + dedup + order-preserving on the original list.
        assert events_on_child == list(ALL_WATCHABLE_EVENTS) + [
            "mission_terminal"
        ], events_on_child
        # F6 re-key moved the row off the parent (atomic same txn).
        assert _watch_events(eng, "W1") == []

        # 2. Quarantine row exists with the full re-key linkage.
        rows = _stored_rows(eng)
        assert len(rows) == 1, f"expected 1 quarantine row, got {len(rows)}"
        row = rows[0]
        assert row["original_work_id"] == "W1"
        assert row["retry_work_id"] == "W2"
        assert row["watcher_instance_id"] == "watcher-1"
        # Mission-root walk resolved worker -> root via instances.parent_id.
        assert row["mission_instance_id"] == "root-1"
        assert row["work_instance_id"] == "worker-1"
        assert row["status"] == "armed"

        # 3. original_watch_events roundtrips list -> CAST(JSONB) -> list,
        #    preserving the PRE-ARMING list (audit fidelity: no
        #    mission_terminal in the original set).
        original = row["original_watch_events"]
        assert isinstance(original, list), type(original)
        assert original == list(ALL_WATCHABLE_EVENTS), original
        assert "mission_terminal" not in original


# ===========================================================================
# Test 5 — StoredWatchRepository roundtrip on PG
# ===========================================================================
class TestStoredWatchRepositoryRoundtrip:
    def test_aged_scan_cas_provenance_and_defensive_parse(self, pg_engine):
        eng = pg_engine
        _seed_instance(eng, "root-1")
        _seed_instance(eng, "worker-1", parent_id="root-1")
        _seed_instance(eng, "watcher-1")
        _seed_instance(eng, "watcher-2")

        now = now_utc_naive()

        def ins(sw_id, orig, retry, watcher, status, age_hours):
            _insert_stored_watch(
                eng,
                stored_watch_id=sw_id,
                original_work_id=orig,
                retry_work_id=retry,
                watcher_instance_id=watcher,
                events=["task_completed"],
                mission_instance_id="root-1",
                work_instance_id="worker-1",
                status=status,
                created_at=now - timedelta(hours=age_hours),
            )

        ins("sw-old-1", "P1", "C1", "watcher-1", "armed", 8)
        ins("sw-old-2", "P2", "C2", "watcher-2", "armed", 7)
        ins("sw-young", "P3", "C3", "watcher-1", "armed", 1)
        ins("sw-rel", "P4", "C4", "watcher-2", "released", 9)

        repo = StoredWatchRepository(engine=eng)

        # --- list_aged_armed: only ARMED rows past 6h, oldest first ---
        aged = repo.list_aged_armed(older_than_seconds=21600)
        assert [r.stored_watch_id for r in aged] == ["sw-old-1", "sw-old-2"], (
            [r.stored_watch_id for r in aged]
        )
        # JSONB roundtrip through the repository materializer.
        assert aged[0].original_watch_events == ["task_completed"]
        assert aged[0].status == "armed"
        assert aged[0].original_work_id == "P1"

        # --- limit respected (time-boxed scan), oldest first ---
        limited = repo.list_aged_armed(older_than_seconds=21600, limit=1)
        assert [r.stored_watch_id for r in limited] == ["sw-old-1"]

        # --- explicit now override: shifting now forward past the 6h
        #     window ages the young row in deterministically (override
        #     now must exceed row age + window: 1h + 6h -> use +6h) ---
        shifted = repo.list_aged_armed(
            older_than_seconds=21600, now=now + timedelta(hours=6)
        )
        shifted_ids = [r.stored_watch_id for r in shifted]
        assert shifted_ids == ["sw-old-1", "sw-old-2", "sw-young"], (
            shifted_ids
        )
        assert "sw-rel" not in shifted_ids  # released never scanned

        # --- transition_status CAS: exactly-once ---
        assert repo.transition_status("sw-old-1", "released") is True
        assert repo.transition_status("sw-old-1", "released") is False
        with eng.begin() as conn:
            st = conn.execute(
                text(
                    "SELECT status FROM stored_watches "
                    "WHERE stored_watch_id = 'sw-old-1'"
                )
            ).scalar()
        assert st == "released"
        # Released row drops out of the aged scan.
        assert "sw-old-1" not in [
            r.stored_watch_id
            for r in repo.list_aged_armed(older_than_seconds=21600)
        ]

        # --- get_by_original_work: provenance lookup ---
        rows = repo.get_by_original_work("P2")
        assert len(rows) == 1
        assert rows[0].retry_work_id == "C2"
        assert rows[0].watcher_instance_id == "watcher-2"
        assert rows[0].status == "armed"
        assert repo.get_by_original_work("never-existed") == []

    def test_row_to_model_defensive_parse_of_malformed_json(self, pg_engine):
        """PG's JSONB structurally REJECTS malformed input at INSERT, so
        the str-branch defense is exercised at the unit boundary against
        the REAL ``_row_to_model`` (SQLite-shape defense; must never
        raise, malformed JSON -> [])."""
        now = now_utc_naive()

        def stub_row(events_raw):
            return SimpleNamespace(
                stored_watch_id="sw-x",
                original_work_id="o",
                retry_work_id="r",
                watcher_instance_id="w",
                original_watch_events=events_raw,
                mission_instance_id="m",
                work_instance_id="wi",
                status="armed",
                created_at=now,
                updated_at=now,
            )

        malformed = StoredWatchRepository._row_to_model(
            stub_row("{definitely not json")
        )
        assert malformed.original_watch_events == []
        assert isinstance(malformed, StoredWatch)
        assert malformed.stored_watch_id == "sw-x"

        none_events = StoredWatchRepository._row_to_model(stub_row(None))
        assert none_events.original_watch_events == []

        list_events = StoredWatchRepository._row_to_model(
            stub_row(["task_completed"])
        )
        assert list_events.original_watch_events == ["task_completed"]
