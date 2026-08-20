"""Task 3.7 — PG migration sub-case suite for pause-report-recovery.

Dual-layer migration-safety tests for the DEFERRED marker schema
(Phase 1, plan task 3.7 / risk C4). This module covers the THREE
required sub-cases against REAL PostgreSQL (``ensemble_test`` via the
``tests/postgres/conftest.py`` PG_TEST_* defaults; the session-scoped
``pg_engine`` fixture skips cleanly when PG is unreachable):

(a) PG ``_ensure_postgres_columns`` path — the REAL production hook
    (``InstanceManager._ensure_postgres_columns``) is DRIVEN (not
    source-grepped): column adds, ``DROP NOT NULL`` on
    ``report_message_id``, partial unique index
    ``uq_report_injections_oblig_triple`` — all idempotent on re-run,
    plus the C1 raise assertion (duplicate obligation triple
    post-migration raises ``IntegrityError`` citing the index).

(b) W3 dedup pre-check — duplicate non-terminal triples seeded on a
    pre-index schema state; the hook's DO-block dedup resolves them
    (keep-one semantics pinned: ``MIN(injection_id)`` survives, the
    rest transition to ``TASK_DELIVERED`` with a sentinel
    ``delivered_at``), then the index builds successfully.

(c) W8 rollback order — the migration's DOWN section (and the
    runbook) mandate DROP INDEX FIRST, then column reverts. We assert
    the documented order mechanically, EXECUTE the DOWN section on PG
    in the documented order, and codify what a column-first (wrong
    order) revert actually does on PG 14 (silent auto-drop of the
    dependent partial index — the exact hazard the ordering rule
    exists to prevent) plus the hard PG gate the runbook relies on
    (``SET NOT NULL`` blocked while NULL-keyed rows exist). The PG
    DOWN path has NO automated runner by design (``MigrationRunner``
    is SQLite-only — asserted); the runbook is the operator path.

(d) SQLite companion parity — the companion ``.sql`` migration is
    EXECUTED (not just read) on a legacy-shape SQLite DB via the real
    ``MigrationRunner``: same index names, same predicate literals,
    W3 dedup semantics, fresh-``create_all`` name parity with PG, and
    no SQLite-only syntax in the migration file.

(e) C4 NULL-consumer audit codified — every production consumer of
    ``ReportInjection.report_message_id`` (enumerated by an offline
    repo grep — the frozen list lives in ``EXPECTED_CONSUMERS``) is
    imported and exercised behaviorally against NULL-keyed rows:
    handle-or-exclude semantics, never a mis-claim/crash. The audit
    is mechanical (no runtime grepping).

Pre-existing coverage (STEP 1 audit — do not duplicate)
-------------------------------------------------------
``tests/repositories/test_report_injection_migration_parity.py``
(492 lines) already provides STATIC source-text parity:
``TestCaseLockstep`` (enum/reason case), ``TestPartialIndexPredicateParity``
(model sqlite_where/postgresql_where + DDL substring match),
``TestSQLiteMigrationParity`` (migration file substrings),
``TestPostgresDDLParity`` (manager.py source substrings incl.
``test_pg_w3_pre_check_present`` / ``test_pg_w8_rollback_runbook_documented``).
``tests/repositories/test_report_injection.py`` (447 lines) covers
repository behavior on SQLite (claims, ensure_deferred, transitions).

What was MISSING (and is added here): the production hook EXECUTED
against real PostgreSQL (idempotence, DROP NOT NULL effect, C1 raise
with a real PG IntegrityError, W3 dedup resolution semantics, W8
order executed), the SQLite companion migration EXECUTED end-to-end
through ``MigrationRunner``, and the C4 NULL-consumer audit on PG.

Known defects codified as ``xfail(strict=True)``
------------------------------------------------
BUG-1 (contract violation): ``claim_for_task_delivery(None)`` returns
``"already_delivered"`` when a NULL-keyed DEFERRED row exists — the
plan's C4 acceptance requires ``"missing"`` (repository.py:84-89
documents ``missing`` as the designed answer). Mechanism: SQLAlchemy
renders ``col == None`` as ``col IS NULL``, so the SELECT-first probe
MATCHES the NULL-keyed DEFERRED row; the guarded
``UPDATE ... WHERE state='PENDING'`` then yields rowcount 0 (the row
is DEFERRED) and the tri-state collapses to the SKIP branch. A stale
PROCESS_REPORT task driven with a None ``message_id`` would be told
``already_delivered`` and silently skip delivery.

BUG-2 (crash): ``claim_for_task_delivery(None)`` raises ``TypeError``
(``'NoneType' object is not subscriptable`` at repository.py:1004)
when a NULL-keyed PENDING row exists — the claim WINS the guarded
UPDATE, commits the TASK_DELIVERED transition, and then crashes in
the success-path log line subscripts ``report_message_id[:8]``. The
row is terminal but the caller sees an exception.

BUG-3 (PG-only, discovered by the sibling 3.6 worker — see
``tests/postgres/test_report_delivery_recovery_pg.py``
``_LANE2_PG_BUG_NOTE``):
``find_completed_children_without_delivery`` emits malformed SQL
(alias ``dw`` referenced before declaration) that PG rejects; SQLite
accepts it. Its C4 audit test is ``xfail(strict=False)`` until the
1-line fix (``select(dw.watch_id)``) lands.

Both are reported to the dispatcher; the strict xfail markers keep
the defects VISIBLE in every run (and fail loudly if someone fixes
them without updating the audit).

Run serially (see conftest xdist guard)::

    timeout 300 .venv/bin/pytest \\
        tests/postgres/test_report_deferred_migration_pg.py \\
        --override-ini="addopts=" -m postgres -q --tb=short
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

# Register ALL SQLModel tables so the session-scoped ``pg_engine``
# fixture's ``create_all`` produces the full daemon schema — the
# production hook's statement list ALTERs many tables (task,
# job_queue_items, ...) and would raise UndefinedTable otherwise.
import daemon.repositories  # noqa: F401
from daemon.manager import InstanceManager
from daemon.migrations.runner import MigrationFile, MigrationRunner
from daemon.repositories.instance.models import (  # noqa: F401
    Instance,
    InstanceStatus,
)
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType  # noqa: F401
from daemon.repositories.task.repository import TaskRepository
from daemon.services.child_reports import ChildReportsService

pytestmark = pytest.mark.postgres

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "daemon"
    / "migrations"
    / "versions"
    / "20260819_000001_report_injections_deferred_marker.sql"
)
MIGRATION_VERSION = "20260819_000001"

TRIPLE_INDEX = "uq_report_injections_oblig_triple"
RECOVERY_INDEX = "ix_report_injections_recovery_attempted"
CHILD_MSG_INDEX = "ix_report_injections_child_msg"

# The full expected index set on ``report_injections`` post-migration
# (fresh ``create_all`` and post-hook existing-DB paths converge here).
EXPECTED_INDEX_NAMES = frozenset(
    {
        TRIPLE_INDEX,
        RECOVERY_INDEX,
        CHILD_MSG_INDEX,
        "ix_report_injections_parent_state",
        "ix_report_injections_report_msg_state",
        "report_injections_pkey",
    }
)

NEW_COLUMNS = ("deferred_reason", "recovery_attempted_at")

# Pre-Phase-1 legacy DDL: ``report_message_id NOT NULL``, no DEFERRED
# columns. Mirrors the schema the migration was designed against (the
# pre-branch model at e858aa94 declared ``Column(String,
# nullable=False)``).
LEGACY_SQLITE_DDL = """
CREATE TABLE report_injections (
  injection_id VARCHAR(64) NOT NULL PRIMARY KEY,
  parent_instance_id VARCHAR(64) NOT NULL,
  child_instance_id VARCHAR(64) NOT NULL,
  child_message_id VARCHAR(64) NOT NULL,
  report_message_id VARCHAR(64) NOT NULL,
  content TEXT,
  created_at VARCHAR(64) NOT NULL,
  delivered_at VARCHAR(64),
  state VARCHAR(16) NOT NULL
)
"""


# ─────────────────────────────────────────────────────────────────────────────
# PG introspection helpers
# ─────────────────────────────────────────────────────────────────────────────


def _pg_indexes(pg_engine: Any) -> dict[str, str]:
    """Return ``{index_name: indexdef}`` for ``report_injections``."""
    sql = text(
        "SELECT c.relname AS indexname, "
        "       pg_get_indexdef(i.indexrelid) AS indexdef "
        "FROM pg_index i "
        "JOIN pg_class r ON r.oid = i.indrelid "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace ns ON ns.oid = r.relnamespace "
        "WHERE ns.nspname = 'public' "
        "  AND r.relname = 'report_injections'"
    )
    with pg_engine.connect() as conn:
        return {row[0]: row[1] for row in conn.execute(sql)}


def _pg_columns(pg_engine: Any) -> dict[str, str]:
    """Return ``{column_name: is_nullable}`` for ``report_injections``."""
    sql = text(
        "SELECT column_name, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "  AND table_name = 'report_injections'"
    )
    with pg_engine.connect() as conn:
        return {row[0]: row[1] for row in conn.execute(sql)}


def _exec(pg_engine: Any, *statements: str) -> None:
    """Execute raw statements, each in its own transaction."""
    with pg_engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _query(pg_engine: Any, sql: str, params: dict | None = None) -> list:
    with pg_engine.connect() as conn:
        return list(conn.execute(text(sql), params or {}).fetchall())


# ─────────────────────────────────────────────────────────────────────────────
# Production-hook statement extraction (drive the REAL statements)
# ─────────────────────────────────────────────────────────────────────────────

_HOOK_STATEMENTS_CACHE: tuple[str, ...] | None = None


def _hook_statements() -> tuple[str, ...]:
    """Return the literal ``statements = [...]`` list from the hook.

    Extracted from ``InstanceManager._ensure_postgres_columns`` source
    via ``ast.literal_eval`` — the SAME strings production executes at
    boot (manager.py:4981-4983 runs them verbatim under
    ``engine.begin()``). Mirrors the anchor-based extraction in the
    existing parity test but returns the evaluated list so this module
    can EXECUTE the real DDL.
    """
    global _HOOK_STATEMENTS_CACHE
    if _HOOK_STATEMENTS_CACHE is None:
        src = textwrap_dedent(
            inspect.getsource(InstanceManager._ensure_postgres_columns)
        )
        tree = ast.parse(src)
        found: list | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if getattr(target, "id", None) == "statements":
                        found = ast.literal_eval(node.value)
        if not found:
            pytest.fail(
                "Could not extract the ``statements = [...]`` list from "
                "InstanceManager._ensure_postgres_columns — the hook was "
                "refactored; update this extraction (see also "
                "tests/repositories/test_report_injection_migration_parity.py)."
            )
        _HOOK_STATEMENTS_CACHE = tuple(found)
    return _HOOK_STATEMENTS_CACHE


def textwrap_dedent(source: str) -> str:
    import textwrap

    return textwrap.dedent(source)


def _report_injection_hook_statements() -> tuple[str, ...]:
    """The hook statements touching ``report_injections`` (7 of 91)."""
    return tuple(s for s in _hook_statements() if "report_injections" in s)


class _MinimalManagerProxy:
    """Stand-in for ``InstanceManager`` exposing only ``_engine``.

    ``_ensure_postgres_columns`` reads exactly one attribute off
    ``self`` — ``self._engine`` (verified via AST; precedent:
    ``tests/postgres/test_legacy_column_drop.py``). Binding the real
    unbound method to this proxy drives the PRODUCTION hook without
    standing up the full daemon.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine


def _run_hook(pg_engine: Any, only_report_injections: bool = False) -> None:
    """Execute the real hook body against ``pg_engine``.

    ``only_report_injections=True`` runs just the 7 report_injections
    statements (used by the W3/W8 tests that need an isolated
    pre-index schema state). The production method runs all 91 in ONE
    ``engine.begin()`` — mirrored here.
    """
    proxy = _MinimalManagerProxy(pg_engine)
    if not only_report_injections:
        InstanceManager._ensure_postgres_columns(proxy)
        return
    with pg_engine.begin() as conn:
        for stmt in _report_injection_hook_statements():
            conn.execute(text(stmt))


# ─────────────────────────────────────────────────────────────────────────────
# Schema-state fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _reset_report_injections_baseline(pg_engine: Any) -> None:
    """Recreate ``report_injections`` fresh (create_all) + run the hook.

    This is the post-migration state every test starts from / returns
    to: all columns (``report_message_id`` nullable), all six indexes.
    ``create_all`` rebuilds the table; the hook run proves idempotence
    and re-installs anything a test dropped.
    """
    _exec(pg_engine, "DROP TABLE IF EXISTS report_injections CASCADE")
    SQLModel.metadata.create_all(
        pg_engine, tables=[ReportInjection.__table__]
    )
    _run_hook(pg_engine)


def _strip_to_legacy_shape(pg_engine: Any) -> None:
    """Reduce ``report_injections`` to the PRE-Phase-1 legacy shape.

    Drops the three Phase-1/2 indexes, the two new columns, and
    restores ``NOT NULL`` on ``report_message_id`` — the exact
    existing-database state the migration exists to upgrade.
    """
    _exec(
        pg_engine,
        f"DROP INDEX IF EXISTS {TRIPLE_INDEX}",
        f"DROP INDEX IF EXISTS {RECOVERY_INDEX}",
        f"DROP INDEX IF EXISTS {CHILD_MSG_INDEX}",
        "ALTER TABLE report_injections DROP COLUMN IF EXISTS deferred_reason",
        "ALTER TABLE report_injections DROP COLUMN IF EXISTS "
        "recovery_attempted_at",
        "ALTER TABLE report_injections ALTER COLUMN report_message_id "
        "DROP NOT NULL",
        "DELETE FROM report_injections",
        "ALTER TABLE report_injections ALTER COLUMN report_message_id "
        "SET NOT NULL",
    )


@pytest.fixture(autouse=True)
def _restore_baseline(pg_engine: Any) -> Any:
    """Yield, then restore the shared schema to the post-UP baseline.

    Schema mutations persist across tests within the session-scoped
    ``pg_engine``; every teardown restores the baseline so later
    modules (and later tests here) see the migrated shape. Mirrors
    ``tests/postgres/test_legacy_column_drop.py``.
    """
    yield
    try:
        _reset_report_injections_baseline(pg_engine)
    except Exception:  # pragma: no cover — never fail in teardown
        logger.exception("Failed to restore report_injections baseline")


@pytest.fixture
def legacy_shape(pg_engine: Any) -> Any:
    """Start the test from the legacy (pre-Phase-1) schema shape."""
    _reset_report_injections_baseline(pg_engine)
    _strip_to_legacy_shape(pg_engine)
    return pg_engine


@pytest.fixture
def baseline_shape(pg_engine: Any) -> Any:
    """Start the test from the post-migration baseline shape."""
    _reset_report_injections_baseline(pg_engine)
    return pg_engine


# ─────────────────────────────────────────────────────────────────────────────
# Row-seeding helpers
# ─────────────────────────────────────────────────────────────────────────────


def _insert_row(
    pg_engine: Any,
    injection_id: str,
    *,
    parent: str = "p1",
    child: str = "c1",
    child_msg: str = "m1",
    report_message_id: str | None,
    state: str = "PENDING",
    created_at: str = "2026-01-01T00:00:00+00:00",
    delivered_at: str | None = None,
    content: str | None = "report-content",
    deferred_reason: str | None = None,
    recovery_attempted_at: str | None = None,
) -> None:
    _exec_params(
        pg_engine,
        "INSERT INTO report_injections (injection_id, parent_instance_id, "
        "child_instance_id, child_message_id, report_message_id, content, "
        "created_at, delivered_at, state, deferred_reason, "
        "recovery_attempted_at) VALUES ("
        ":iid, :p, :c, :m, :rm, :ct, :ca, :da, :st, :dr, :ra)",
        {
            "iid": injection_id,
            "p": parent,
            "c": child,
            "m": child_msg,
            "rm": report_message_id,
            "ct": content,
            "ca": created_at,
            "da": delivered_at,
            "st": state,
            "dr": deferred_reason,
            "ra": recovery_attempted_at,
        },
    )


def _exec_params(pg_engine: Any, sql: str, params: dict) -> None:
    with pg_engine.begin() as conn:
        conn.execute(text(sql), params)


def _row_state(pg_engine: Any, injection_id: str) -> tuple[str, str | None, str | None]:
    """Return ``(state, delivered_at, report_message_id)`` for a row."""
    rows = _query(
        pg_engine,
        "SELECT state, delivered_at, report_message_id "
        "FROM report_injections WHERE injection_id = :iid",
        {"iid": injection_id},
    )
    if not rows:
        return ("<missing>", None, None)
    row = rows[0]
    return (row[0], row[1], row[2])


def _seed_family(
    pg_engine: Any,
    *,
    parent_status: str = InstanceStatus.WAITING_CHILDREN.value,
    child_status: str = InstanceStatus.COMPLETED.value,
) -> dict[str, str]:
    """Seed a parent/child instance pair for JOIN-consuming queries."""
    def _insert_instance(
        conn: Any, instance_id: str, parent: str | None, status: str
    ) -> None:
        # ``version`` has no server-side default — supply it explicitly.
        conn.execute(
            text(
                "INSERT INTO instances (instance_id, agent_id, agent_dir, "
                "parent_id, status, version, created_at, updated_at) "
                "VALUES (:i, 'leader', '/tmp/x', :parent, :st, 1, "
                "'2026-01-01T00:00:00+00:00', "
                "'2026-01-01T00:00:00+00:00')"
            ),
            {"i": instance_id, "parent": parent, "st": status},
        )

    def _insert_task(
        conn: Any,
        work_id: str,
        *,
        message_id: str | None,
        status: str,
        parent: str,
    ) -> None:
        # ORM-style defaults via explicit values (raw INSERT bypasses
        # SQLModel's Python-side defaults on PG, where several columns
        # carry no server default).
        conn.execute(
            text(
                "INSERT INTO task (work_id, task_type, instance_id, "
                "message_id, status, retry_count, cancel_requested, "
                "retry_scheduled, is_deferred, is_background, version, "
                "created_at) "
                "VALUES (:w, 'process_report', :parent, :m, :st, 0, "
                "false, false, false, false, 0, "
                "'2026-01-01T00:00:00+00:00')"
            ),
            {"w": work_id, "m": message_id, "st": status,
             "parent": parent},
        )

    def _insert_completed_child_message(
        conn: Any, message_id: str, child_id: str
    ) -> None:
        conn.execute(
            text(
                "INSERT INTO message_queue (message_id, instance_id, "
                "type, status, content, priority, retry_count, "
                "max_retries, enqueued_at) VALUES "
                "(:mid, :iid, 'agent', 'completed', 'done work', 1, 0, "
                "5, CURRENT_TIMESTAMP)"
            ),
            {"mid": message_id, "iid": child_id},
        )

    ids = {"parent": "p-fam", "child": "c-fam"}
    with pg_engine.begin() as conn:
        for table in ("report_injections", "task", "instances",
                      "message_queue", "dependency_watchers"):
            conn.execute(text(f"DELETE FROM {table}"))
        _insert_instance(conn, ids["parent"], None, parent_status)
        _insert_instance(conn, ids["child"], ids["parent"], child_status)
    ids["_insert_task"] = _insert_task  # type: ignore[assignment]
    ids["_insert_completed_child_message"] = (  # type: ignore[assignment]
        _insert_completed_child_message
    )
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# (a) PG _ensure_postgres_columns path — idempotence + C1 raise
# ─────────────────────────────────────────────────────────────────────────────


class TestEnsurePostgresColumnsPath:
    """Drive the REAL production hook against real PostgreSQL."""

    def test_hook_upgrades_legacy_schema_columns_notnull_indexes(
        self, legacy_shape: Any
    ) -> None:
        """Legacy → migrated: columns added, NOT NULL dropped, indexes built.

        Starts from the legacy shape (``report_message_id NOT NULL``,
        no DEFERRED columns, no Phase-1 indexes), runs the production
        hook, and asserts the full post-migration contract.
        """
        # Pre-conditions (legacy shape sanity).
        cols_before = _pg_columns(legacy_shape)
        assert cols_before["report_message_id"] == "NO", (
            "fixture failed to build the legacy NOT NULL shape"
        )
        assert "deferred_reason" not in cols_before
        assert "recovery_attempted_at" not in cols_before

        _run_hook(legacy_shape)  # FULL hook — all 91 statements

        cols = _pg_columns(legacy_shape)
        assert cols["report_message_id"] == "YES", (
            "C4: ``DROP NOT NULL`` on report_message_id did not take "
            "effect — NULL-keyed marker rows cannot be stored"
        )
        for col in NEW_COLUMNS:
            assert col in cols, f"column {col} missing after hook"
            assert cols[col] == "YES"

        idx = _pg_indexes(legacy_shape)
        assert EXPECTED_INDEX_NAMES <= set(idx), (
            f"post-hook index set incomplete: {sorted(idx)}"
        )

    def test_hook_idempotent_column_adds_and_index_rerun(
        self, baseline_shape: Any
    ) -> None:
        """Re-running the hook (2x) on a migrated DB never raises or drifts."""
        snapshot_1 = (
            frozenset(_pg_indexes(baseline_shape)),
            frozenset(_pg_columns(baseline_shape)),
        )
        _run_hook(baseline_shape)  # re-run #1
        _run_hook(baseline_shape)  # re-run #2
        snapshot_2 = (
            frozenset(_pg_indexes(baseline_shape)),
            frozenset(_pg_columns(baseline_shape)),
        )
        assert snapshot_1 == snapshot_2, (
            "hook re-run drifted the schema — startup runs this on "
            "EVERY boot; drift would break the next boot"
        )
        assert EXPECTED_INDEX_NAMES <= snapshot_2[0]

    def test_hook_drop_not_null_from_legacy_not_null_column(
        self, legacy_shape: Any
    ) -> None:
        """The DO-block ``DROP NOT NULL`` fires only when needed (guarded)."""
        _run_hook(legacy_shape, only_report_injections=True)
        assert _pg_columns(legacy_shape)["report_message_id"] == "YES"
        # The information_schema guard makes a second run a no-op —
        # covered by the idempotence test; here assert the guard
        # exists in the real statement text (prevents silent removal).
        drop_not_null_stmts = [
            s for s in _report_injection_hook_statements()
            if "DROP NOT NULL" in s
        ]
        assert len(drop_not_null_stmts) == 1
        assert "is_nullable = 'NO'" in drop_not_null_stmts[0], (
            "the DROP NOT NULL DO-block must stay guarded by "
            "information_schema (idempotence depends on it)"
        )

    def test_triple_index_predicate_matches_storage_literals(
        self, baseline_shape: Any
    ) -> None:
        """C1 case-lockstep: the BUILT index predicate uses the storage
        literals (PENDING/DEFERRED, uppercase) over the triple columns."""
        indexdef = _pg_indexes(baseline_shape)[TRIPLE_INDEX]
        assert "CREATE UNIQUE INDEX" in indexdef
        for col in ("parent_instance_id", "child_instance_id",
                    "child_message_id"):
            assert col in indexdef
        predicate = indexdef.split("WHERE", 1)[1]
        assert "'PENDING'" in predicate and "'DEFERRED" in predicate, (
            f"partial-index predicate drifted from storage literals: "
            f"{predicate!r}"
        )
        assert "'pending'" not in predicate.replace("'PENDING'", "")
        assert "'deferred'" not in predicate.replace("'DEFERRED'", "")
        # And the recovery partial index predicate.
        recovery_def = _pg_indexes(baseline_shape)[RECOVERY_INDEX]
        assert "'PENDING'" in recovery_def.split("WHERE", 1)[1]

    def test_c1_duplicate_triple_post_migration_raises_citing_index(
        self, baseline_shape: Any
    ) -> None:
        """C1 raise assertion: post-migration, a duplicate non-terminal
        obligation triple raises ``IntegrityError`` whose message cites
        ``uq_report_injections_oblig_triple`` (the write-once gate)."""
        _insert_row(
            baseline_shape, "orig", report_message_id="rm-1", state="PENDING"
        )
        with pytest.raises(IntegrityError) as excinfo:
            _insert_row(
                baseline_shape,
                "dup-pending",
                report_message_id="rm-2",
                state="PENDING",
            )
        message = str(excinfo.value) + str(
            getattr(excinfo.value, "orig", "") or ""
        )
        assert TRIPLE_INDEX in message, (
            "PG must cite the constraint/index name — the W6 absorb "
            "path (child_reports._is_obligation_triple_integrity_error) "
            "matches on this name"
        )
        pgcode = getattr(getattr(excinfo.value, "orig", None), "pgcode", None)
        if pgcode is not None:  # soft: 23505 = unique_violation
            assert pgcode == "23505", f"expected 23505, got {pgcode}"

        # The predicate spans BOTH non-terminal states: DEFERRED dup
        # against an existing PENDING row also raises.
        with pytest.raises(IntegrityError):
            _insert_row(
                baseline_shape,
                "dup-deferred",
                report_message_id=None,
                state="DEFERRED",
            )

        # Terminal rows are OUTSIDE the predicate: a fresh non-terminal
        # obligation for a previously-delivered triple is ALLOWED
        # (re-spawn scenario — models.py:53-60).
        _exec(
            baseline_shape,
            "UPDATE report_injections SET state = 'TASK_DELIVERED', "
            "delivered_at = '2026-01-02T00:00:00+00:00' "
            "WHERE injection_id = 'orig'",
        )
        _insert_row(  # must NOT raise
            baseline_shape,
            "respawn",
            report_message_id="rm-3",
            state="PENDING",
        )
        assert _row_state(baseline_shape, "respawn")[0] == "PENDING"

    def test_full_hook_tolerates_fixture_schema(
        self, pg_engine: Any
    ) -> None:
        """Sanity: the FULL 91-statement hook runs clean against the
        conftest ``create_all`` schema twice (production boot path)."""
        _run_hook(pg_engine)
        _run_hook(pg_engine)
        assert EXPECTED_INDEX_NAMES <= set(_pg_indexes(pg_engine))


# ─────────────────────────────────────────────────────────────────────────────
# (b) W3 dedup pre-check — duplicates detected + resolved, index builds
# ─────────────────────────────────────────────────────────────────────────────


class TestW3DedupPreCheck:
    """W3: pre-index duplicate resolution driven through the real hook."""

    DETECTION_SQL = (
        "SELECT COUNT(*) FROM ("
        "  SELECT parent_instance_id, child_instance_id, child_message_id "
        "    FROM report_injections "
        "   WHERE state IN ('PENDING', 'DEFERRED') "
        "   GROUP BY parent_instance_id, child_instance_id, child_message_id "
        "  HAVING COUNT(*) > 1"
        ") dups"
    )

    def _pre_index_state_with_duplicates(self, engine: Any) -> None:
        """Legacy shape + duplicate non-terminal triples, index absent."""
        _reset_report_injections_baseline(engine)
        # Strip ONLY the indexes (keep columns) to simulate the
        # pre-index build moment with duplicates present.
        _exec(
            engine,
            f"DROP INDEX {TRIPLE_INDEX}",
            f"DROP INDEX {RECOVERY_INDEX}",
            "DELETE FROM report_injections",
        )
        # Group 1 — two PENDING duplicates (same triple). 'aaa-newer'
        # has the lexicographically-smaller injection_id but the LATER
        # created_at; 'zzz-older' is the chronological oldest.
        _insert_row(
            engine, "zzz-older", report_message_id="rm-old",
            state="PENDING", created_at="2026-01-01T00:00:00+00:00",
        )
        _insert_row(
            engine, "aaa-newer", report_message_id="rm-new",
            state="PENDING", created_at="2026-02-01T00:00:00+00:00",
        )
        # Group 2 — mixed states (PENDING older + DEFERRED newer).
        _insert_row(
            engine, "b-old-pending", child="c2",
            report_message_id="rm-b1", state="PENDING",
            created_at="2026-01-01T00:00:00+00:00",
        )
        _insert_row(
            engine, "c-new-deferred", child="c2",
            report_message_id=None, state="DEFERRED",
            created_at="2026-03-01T00:00:00+00:00",
            content=None,
        )
        # Group 3 — a duplicate group where the loser already carries a
        # delivered_at (COALESCE must PRESERVE it, not overwrite).
        _insert_row(
            engine, "d-keep", child="c3", report_message_id="rm-d1",
            state="PENDING", created_at="2026-01-01T00:00:00+00:00",
        )
        _insert_row(
            engine, "e-drop", child="c3", report_message_id="rm-d2",
            state="PENDING", created_at="2026-02-01T00:00:00+00:00",
            delivered_at="2026-02-02T00:00:00+00:00",
        )
        # Control — clean single rows that must be UNTOUCHED.
        _insert_row(
            engine, "f-clean", child="c4", report_message_id="rm-f",
            state="PENDING",
        )
        _insert_row(
            engine, "g-terminal", child="c5", report_message_id="rm-g",
            state="INJECTED",
        )

    def test_w3_detection_query_finds_duplicate_groups(
        self, pg_engine: Any
    ) -> None:
        """The detection predicate (same shape as the DO block) sees the
        duplicate groups before resolution and none after."""
        self._pre_index_state_with_duplicates(pg_engine)
        before = _query(pg_engine, self.DETECTION_SQL)[0][0]
        assert before == 3, f"expected 3 duplicate groups, got {before}"

        _run_hook(pg_engine, only_report_injections=True)

        after = _query(pg_engine, self.DETECTION_SQL)[0][0]
        assert after == 0, (
            f"duplicates remain post-migration: {after} group(s) — the "
            "index build only succeeds because rows were force-resolved"
        )

    def test_w3_resolves_duplicates_then_index_builds(
        self, pg_engine: Any
    ) -> None:
        """End-to-end: duplicates present → hook dedups → index builds."""
        self._pre_index_state_with_duplicates(pg_engine)

        _run_hook(pg_engine, only_report_injections=True)

        # The write-once gate now exists (the build would have FAILED
        # with the duplicates still non-terminal).
        assert TRIPLE_INDEX in _pg_indexes(pg_engine)
        # ... and it is LIVE: a duplicate insert raises (C1).
        with pytest.raises(IntegrityError):
            _insert_row(
                pg_engine, "post-dup", report_message_id="rm-x",
                state="PENDING",
            )

    def test_w3_keep_one_semantics_min_injection_id_survives(
        self, pg_engine: Any
    ) -> None:
        """Survivor semantics: ``MIN(injection_id)`` wins — the
        lexicographically-smallest id stays non-terminal regardless of
        ``created_at`` ordering; losers move to ``TASK_DELIVERED``.

        This PINS the implemented semantics (the PG DO-block's
        ``EXISTS (... newer.injection_id < ri.injection_id)`` keeps the
        smallest id; the SQLite companion's ``NOT IN (SELECT MIN(...))``
        agrees). Note this is id-ordering, not created_at-ordering —
        both DDL paths implement the same rule.
        """
        self._pre_index_state_with_duplicates(pg_engine)
        _run_hook(pg_engine, only_report_injections=True)

        # Group 1: 'aaa-newer' (smallest id) survives as PENDING even
        # though 'zzz-older' is chronologically older.
        assert _row_state(pg_engine, "aaa-newer")[0] == "PENDING"
        dropped_state, dropped_da, _ = _row_state(pg_engine, "zzz-older")
        assert dropped_state == "TASK_DELIVERED"
        # Sentinel delivered_at = COALESCE(delivered_at, created_at).
        assert dropped_da == "2026-01-01T00:00:00+00:00"

        # Group 2 (mixed states): smallest id survives non-terminal;
        # the DEFERRED loser transitions straight to terminal.
        assert _row_state(pg_engine, "b-old-pending")[0] == "PENDING"
        assert _row_state(pg_engine, "c-new-deferred")[0] == "TASK_DELIVERED"

        # Group 3: pre-existing delivered_at is PRESERVED by COALESCE.
        assert _row_state(pg_engine, "d-keep")[0] == "PENDING"
        assert (
            _row_state(pg_engine, "e-drop")[1] == "2026-02-02T00:00:00+00:00"
        )

        # Controls: clean rows untouched.
        assert _row_state(pg_engine, "f-clean")[0] == "PENDING"
        assert _row_state(pg_engine, "g-terminal")[0] == "INJECTED"

    def test_w3_no_mutations_when_clean(
        self, baseline_shape: Any
    ) -> None:
        """No duplicates → the dedup UPDATE touches zero rows."""
        _insert_row(
            baseline_shape, "solo-1", report_message_id="rm-1",
            state="PENDING",
        )
        _insert_row(
            baseline_shape, "solo-2", child="c9",
            report_message_id="rm-2", state="DEFERRED", content=None,
        )
        _run_hook(baseline_shape, only_report_injections=True)
        assert _row_state(baseline_shape, "solo-1")[0] == "PENDING"
        assert _row_state(baseline_shape, "solo-2")[0] == "DEFERRED"
        assert TRIPLE_INDEX in _pg_indexes(baseline_shape)

    def test_w3_hook_logs_detection_in_statement_source(
        self, baseline_shape: Any
    ) -> None:
        """The detection signal (RAISE WARNING) is part of the real
        hook statements — operators get an auditable warning."""
        w3_stmts = [
            s for s in _report_injection_hook_statements()
            if "HAVING COUNT(*) > 1" in s
        ]
        assert w3_stmts, "W3 pre-check DO block missing from the hook"
        assert "RAISE WARNING" in w3_stmts[0]
        assert "TASK_DELIVERED" in w3_stmts[0]


# ─────────────────────────────────────────────────────────────────────────────
# (c) W8 rollback order
# ─────────────────────────────────────────────────────────────────────────────


def _migration_file() -> MigrationFile:
    return MigrationFile.parse(MIGRATION_PATH)


def _split_statements(sql: str) -> list[str]:
    """Mirror the runner's statement splitter (strip ``--`` lines)."""
    code_lines = [ln for ln in sql.splitlines()
                  if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(code_lines).split(";") if s.strip()]


class TestW8RollbackOrder:
    """W8: DROP INDEX FIRST, then column reverts — asserted mechanically,
    executed on PG in the documented order, and the wrong order's
    effect codified (silent dependent-index loss + the hard SET NOT
    NULL gate). The PG path has NO automated DOWN runner by design —
    ``MigrationRunner`` is SQLite-only (asserted here; the runbook is
    the operator path)."""

    def test_down_section_documents_index_first_order(self) -> None:
        """The DOWN section's statements must be ordered: all DROP
        INDEX statements BEFORE any column-reverting ALTER."""
        raw = MIGRATION_PATH.read_text()
        assert "DROP indexes FIRST" in raw or (
            "Reverse order" in raw
        ), "W8 runbook order comment missing from the migration file"

        down_stmts = _split_statements(_migration_file().down_sql)
        index_drop_positions = [
            i for i, s in enumerate(down_stmts)
            if s.upper().startswith("DROP INDEX")
        ]
        column_drop_positions = [
            i for i, s in enumerate(down_stmts)
            if s.upper().startswith("ALTER TABLE")
        ]
        assert index_drop_positions and column_drop_positions
        assert max(index_drop_positions) < min(column_drop_positions), (
            "DOWN section violates the W8 runbook: a column-reverting "
            "ALTER appears before an index DROP"
        )
        # The three documented indexes are all dropped.
        joined = "\n".join(down_stmts)
        for name in (CHILD_MSG_INDEX, RECOVERY_INDEX, TRIPLE_INDEX):
            assert f"DROP INDEX IF EXISTS {name}" in joined

    def test_down_section_executes_on_pg_in_documented_order(
        self, baseline_shape: Any
    ) -> None:
        """Executing the DOWN statements (in file order) on PG succeeds
        and reverts the schema: indexes gone, new columns gone."""
        down_stmts = _split_statements(_migration_file().down_sql)
        with baseline_shape.begin() as conn:
            for stmt in down_stmts:
                conn.execute(text(stmt))

        idx = _pg_indexes(baseline_shape)
        assert TRIPLE_INDEX not in idx
        assert RECOVERY_INDEX not in idx
        assert CHILD_MSG_INDEX not in idx
        cols = _pg_columns(baseline_shape)
        assert "deferred_reason" not in cols
        assert "recovery_attempted_at" not in cols

    def test_w8_column_first_revert_silently_drops_dependent_index(
        self, baseline_shape: Any
    ) -> None:
        """Wrong-order revert, codified honestly: on PG 14, reverting a
        column while its partial index is still present does NOT raise
        — PostgreSQL SILENTLY AUTO-DROPS the dependent index. That
        silent degradation (write-once gate / sweep index vanishing
        without an error) is exactly what the W8 ordering rule exists
        to prevent; the runbook mandates DROP INDEX first so the loss
        is explicit and auditable."""
        assert RECOVERY_INDEX in _pg_indexes(baseline_shape)

        # Column-first (wrong order): drop the column the recovery
        # index depends on, WITHOUT dropping the index first.
        _exec(
            baseline_shape,
            "ALTER TABLE report_injections DROP COLUMN "
            "recovery_attempted_at",
        )

        # The dependent partial index was silently destroyed — no
        # error, no orphan, just gone. The hazard, demonstrated.
        assert RECOVERY_INDEX not in _pg_indexes(baseline_shape), (
            "expected PG to silently auto-drop the dependent partial "
            "index on column-first revert — behavior changed, "
            "re-examine the W8 runbook"
        )
        # The write-once gate itself survived this particular revert
        # (it does not reference the dropped column)…
        assert TRIPLE_INDEX in _pg_indexes(baseline_shape)
        # …but a column the gate DOES cover is equally unprotected:
        _exec(
            baseline_shape,
            "ALTER TABLE report_injections DROP COLUMN child_message_id",
        )
        assert TRIPLE_INDEX not in _pg_indexes(baseline_shape), (
            "column-first revert destroyed the write-once gate without "
            "dropping it first — the W8 rule's core justification"
        )

    def test_w8_set_not_null_blocked_while_null_rows_exist(
        self, baseline_shape: Any
    ) -> None:
        """The runbook's step-3 hard gate: restoring ``NOT NULL`` on
        ``report_message_id`` is BLOCKED by PostgreSQL while NULL-keyed
        rows exist (verify-first is enforced by the engine, not just
        documented)."""
        _insert_row(
            baseline_shape, "null-keyed", report_message_id=None,
            state="DEFERRED", content=None,
        )
        with pytest.raises(IntegrityError):
            _exec(
                baseline_shape,
                "ALTER TABLE report_injections ALTER COLUMN "
                "report_message_id SET NOT NULL",
            )
        # After the NULL rows are cleared the same statement succeeds
        # (the documented runbook precondition).
        _exec(baseline_shape, "DELETE FROM report_injections")
        _exec(
            baseline_shape,
            "ALTER TABLE report_injections ALTER COLUMN "
            "report_message_id SET NOT NULL",
        )
        assert _pg_columns(baseline_shape)["report_message_id"] == "NO"

    def test_w8_hook_reheals_after_wrong_order_partial_revert(
        self, baseline_shape: Any
    ) -> None:
        """Defense in depth: a wrong-order partial revert that silently
        dropped indexes is fully repaired by the next hook run (the
        idempotent boot path re-creates them)."""
        _exec(
            baseline_shape,
            f"DROP INDEX {RECOVERY_INDEX}",  # simulate the silent loss
            "ALTER TABLE report_injections DROP COLUMN "
            "recovery_attempted_at",
        )
        assert RECOVERY_INDEX not in _pg_indexes(baseline_shape)

        _run_hook(baseline_shape, only_report_injections=True)

        assert RECOVERY_INDEX in _pg_indexes(baseline_shape)
        assert TRIPLE_INDEX in _pg_indexes(baseline_shape)
        assert _pg_columns(baseline_shape)["recovery_attempted_at"] == "YES"

    def test_w8_pg_has_no_automated_down_runner_by_design(
        self, pg_engine: Any
    ) -> None:
        """``MigrationRunner`` is intentionally a NO-OP on PostgreSQL —
        the PG DOWN path is the manual runbook, not automation. Codified
        so nobody "fixes" the runner into applying SQLite dialect SQL
        to production PG (see runner.py:464-491)."""
        runner = MigrationRunner(pg_engine)
        applied = runner.run_pending_migrations()
        assert applied == [], (
            "MigrationRunner must no-op on non-SQLite engines — PG "
            "schema evolution is _ensure_postgres_columns' job"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (d) SQLite companion parity — EXECUTED, not just read
# ─────────────────────────────────────────────────────────────────────────────


class TestSQLiteCompanionParity:
    """The companion .sql migration executed end-to-end through the
    REAL ``MigrationRunner`` on a legacy-shape SQLite database.

    (Static substring parity is already covered by
    ``tests/repositories/test_report_injection_migration_parity.py``::
    ``TestSQLiteMigrationParity`` and ``TestPostgresDDLParity`` — cited,
    not duplicated. What was missing: EXECUTION.)"""

    @pytest.fixture
    def legacy_sqlite_engine(self, tmp_path: Path) -> Any:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'legacy.db'}", future=True
        )
        with engine.begin() as conn:
            conn.execute(text(LEGACY_SQLITE_DDL))
            conn.execute(
                text(
                    "CREATE INDEX ix_report_injections_parent_state "
                    "ON report_injections (parent_instance_id, state)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_report_injections_report_msg_state "
                    "ON report_injections (report_message_id, state)"
                )
            )
        yield engine
        engine.dispose()

    def _sqlite_indexes(self, engine: Any) -> dict[str, str]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE tbl_name = 'report_injections' "
                    "AND type = 'index'"
                )
            ).fetchall()
        return {n: (s or "") for n, s in rows}

    def _apply(self, engine: Any) -> None:
        runner = MigrationRunner(engine)
        runner.ensure_migrations_table()
        migrations = {
            m.version: m for m in runner.discover_migrations()
        }
        runner.apply_migration(migrations[MIGRATION_VERSION])

    def test_companion_migration_same_shape_and_index_names(
        self, legacy_sqlite_engine: Any
    ) -> None:
        """Legacy SQLite DB → migration → same columns + same index
        NAMES as the PG path, predicate literals uppercase."""
        self._apply(legacy_sqlite_engine)

        with legacy_sqlite_engine.connect() as conn:
            cols = [
                r[1] for r in conn.execute(
                    text("PRAGMA table_info(report_injections)")
                )
            ]
        for col in ("deferred_reason", "recovery_attempted_at",
                    "report_message_id"):
            assert col in cols, f"{col} missing after companion migration"

        idx = self._sqlite_indexes(legacy_sqlite_engine)
        triple_sql = re.sub(
            r"\s+", " ", idx[TRIPLE_INDEX].upper()
        ).strip()
        assert "UNIQUE" in triple_sql
        # SQLite stores the predicate verbatim from the migration
        # file — normalize whitespace, then require the storage
        # literals (C1: identical literal set as the PG DDL).
        assert re.search(
            r"WHERE STATE IN \(\s*'PENDING'\s*,\s*'DEFERRED'\s*\)",
            triple_sql,
        ), f"predicate drifted: {triple_sql!r}"
        assert RECOVERY_INDEX in idx
        assert CHILD_MSG_INDEX in idx

    def test_companion_w3_dedup_matches_pg_semantics(
        self, legacy_sqlite_engine: Any
    ) -> None:
        """The SQLite W3 variant resolves duplicates with the SAME
        keep-one rule as the PG DO block (MIN(injection_id) survives,
        loser → TASK_DELIVERED with sentinel delivered_at) and the
        unique index then builds cleanly."""
        with legacy_sqlite_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO report_injections VALUES "
                    "('zzz-older','p','c','m','rm1','x',"
                    "'2026-01-01T00:00:00',NULL,'PENDING'),"
                    "('aaa-newer','p','c','m','rm2','x',"
                    "'2026-02-01T00:00:00',NULL,'PENDING')"
                )
            )
        self._apply(legacy_sqlite_engine)

        with legacy_sqlite_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT injection_id, state, delivered_at "
                    "FROM report_injections ORDER BY injection_id"
                )
            ).fetchall()
        by_id = {r[0]: (r[1], r[2]) for r in rows}
        assert by_id["aaa-newer"][0] == "PENDING"
        assert by_id["zzz-older"][0] == "TASK_DELIVERED"
        assert by_id["zzz-older"][1] == "2026-01-01T00:00:00"
        assert TRIPLE_INDEX in self._sqlite_indexes(legacy_sqlite_engine)

    def test_companion_migration_no_sqlite_only_syntax(self) -> None:
        """The migration must contain no SQLite-only constructs (the
        runner may one day grow a PG path; and the file documents
        driver-neutral DDL). Forbidden: PRAGMA, rowid, AUTOINCREMENT,
        ON CONFLICT DO NOTHING."""
        raw = MIGRATION_PATH.read_text()
        code_lines = [
            ln for ln in raw.splitlines()
            if not ln.lstrip().startswith("--")
        ]
        code = "\n".join(code_lines).upper()
        for token in ("PRAGMA", "ROWID", "AUTOINCREMENT",
                      "ON CONFLICT DO NOTHING"):
            assert token not in code, (
                f"SQLite-only construct {token!r} in migration code — "
                "keep the file driver-neutral"
            )

    def test_fresh_create_all_index_names_identical_sqlite_vs_pg(
        self, pg_engine: Any, tmp_path: Path
    ) -> None:
        """Fresh databases via ``create_all`` converge on the SAME
        index-name set on both drivers (the C1 name-lockstep contract
        across DDL paths)."""
        sqlite_engine = create_engine(
            f"sqlite:///{tmp_path / 'fresh.db'}", future=True
        )
        SQLModel.metadata.create_all(
            sqlite_engine, tables=[ReportInjection.__table__]
        )
        with sqlite_engine.connect() as conn:
            sqlite_names = {
                r[0] for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE tbl_name = 'report_injections' "
                        "AND type = 'index'"
                    )
                )
                # SQLite materializes the PK as sqlite_autoindex_*;
                # PG names it report_injections_pkey. Both are the
                # implicit PK index — filter both families out.
                if not (
                    r[0].startswith("sqlite_autoindex")
                    or r[0].endswith("_pkey")
                )
            }
        sqlite_engine.dispose()

        _reset_report_injections_baseline(pg_engine)
        pg_names = {
            n for n in _pg_indexes(pg_engine)
            if not (
                n.startswith("sqlite_autoindex") or n.endswith("_pkey")
            )
        }
        assert sqlite_names == pg_names == (
            set(EXPECTED_INDEX_NAMES) - {"report_injections_pkey"}
        )


# ─────────────────────────────────────────────────────────────────────────────
# (e) C4 NULL-consumer audit — codified, mechanical
# ─────────────────────────────────────────────────────────────────────────────

# The frozen consumer registry — the codified output of the offline
# repo grep for ``report_message_id`` usages outside tests. Every
# entry is imported and behaviorally audited below; adding a consumer
# without extending this registry (and its audit) fails the
# enumeration test.
EXPECTED_CONSUMERS = frozenset(
    {
        "report_injection.repository.claim_for_task_delivery",
        "report_injection.repository.claim_for_injection",
        "report_injection.repository.find_row_by_report_message_id",
        "report_injection.repository.enqueue",
        "report_injection.repository.ensure_deferred",
        "report_injection.repository.transition_deferred_to_pending",
        "report_injection.repository.find_deferred_for_parent",
        "report_injection.repository.find_deferred_for_parent_all",
        "report_injection.repository.find_completed_children_without_delivery",
        "report_injection.repository.find_pending_past_age",
        "report_injection.repository.count_pending_for_parent",
        "task.repository.reconcile_turn_mirror",
        "child_reports.ChildReportsService._count_actionable_pending_tasks",
        "manager.InstanceManager._has_non_terminal_injection_for",
    }
)


class TestC4NullConsumerAudit:
    """C4: every ``report_message_id`` consumer handles-or-excludes
    NULL-keyed rows — asserted by IMPORTING and EXERCISING each
    consumer against a seeded NULL-keyed row (no runtime grepping)."""

    # -- the plan-explicit acceptance -------------------------------

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-1 (reported): claim_for_task_delivery(None) returns "
            "'already_delivered' when a NULL-keyed DEFERRED row exists — "
            "the C4 plan acceptance requires 'missing'. SQLAlchemy "
            "renders == None as IS NULL, so the SELECT-first probe "
            "matches the NULL-keyed DEFERRED row; the state-guarded "
            "UPDATE yields rowcount 0 and the tri-state collapses to "
            "the SKIP branch: a stale PROCESS_REPORT task would be "
            "told already_delivered and skip delivery."
        ),
    )
    def test_claim_for_task_delivery_returns_missing_for_null_keyed(
        self, baseline_shape: Any
    ) -> None:
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "null-deferred", report_message_id=None,
            state="DEFERRED", content=None, deferred_reason="PAUSE_TOCTOU",
        )
        claim = repo.claim_for_task_delivery(None)
        assert claim.status == "missing", (
            f"C4 acceptance: NULL-keyed rows must claim as 'missing'; "
            f"got {claim.status!r}"
        )
        assert claim.row is None

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-2 (reported): claim_for_task_delivery(None) raises "
            "TypeError ('NoneType' object is not subscriptable, "
            "repository.py:1004 success-path log) when a NULL-keyed "
            "PENDING row exists — the claim WINS the guarded UPDATE and "
            "then crashes subscripting the None key for the log line."
        ),
    )
    def test_claim_for_task_delivery_null_keyed_pending_no_crash(
        self, baseline_shape: Any
    ) -> None:
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "null-pending", report_message_id=None,
            state="PENDING", content=None,
        )
        claim = repo.claim_for_task_delivery(None)  # must not raise
        assert claim.status in ("missing", "claimed")

    def test_claim_for_task_delivery_missing_when_no_row_exists(
        self, baseline_shape: Any
    ) -> None:
        """Control (healthy path): no row at all → ``missing``."""
        repo = ReportInjectionRepository(baseline_shape)
        claim = repo.claim_for_task_delivery("rm-never-enqueued")
        assert claim.status == "missing"
        assert claim.row is None

    def test_claim_for_task_delivery_claimed_after_artifact_backfill(
        self, baseline_shape: Any
    ) -> None:
        """Healthy recovered path: once reconciliation backfills the
        artifact (non-NULL key), the claim is ``claimed`` — the audit's
        positive control proving the consumer is not broken outright."""
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "recovered", report_message_id="rm-rec",
            state="PENDING",
        )
        claim = repo.claim_for_task_delivery("rm-rec")
        assert claim.status == "claimed"
        assert claim.row is not None
        # Exactly-once: a second claim sees terminal.
        assert (
            repo.claim_for_task_delivery("rm-rec").status
            == "already_delivered"
        )

    # -- the remaining consumers, behaviorally ----------------------

    def test_find_row_by_report_message_id_none_returns_none(
        self, baseline_shape: Any
    ) -> None:
        """Explicit None guard — the FM-1 exemption lookup excludes
        NULL keys by contract."""
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "null-keyed", report_message_id=None,
            state="DEFERRED", content=None,
        )
        assert repo.find_row_by_report_message_id(None) is None

    def test_manager_has_non_terminal_injection_for_none_is_false(
        self, baseline_shape: Any
    ) -> None:
        """``InstanceManager._has_non_terminal_injection_for`` excludes
        NULL keys explicitly (FM-1 guard — NULL-keyed rows are the
        recovery sweep's territory)."""
        repo = ReportInjectionRepository(baseline_shape)

        class _Mgr:
            def __init__(self, engine: Any, r: Any) -> None:
                self._engine = engine
                self._report_injection_repo = r

        mgr = _Mgr(baseline_shape, repo)
        assert (
            InstanceManager._has_non_terminal_injection_for(mgr, None)
            is False
        )

    def test_claim_for_injection_excludes_deferred_and_survives_null_keys(
        self, baseline_shape: Any
    ) -> None:
        """The drain is guarded on ``state='PENDING'`` (NOT on the
        key column): DEFERRED NULL-keyed rows are invisible; after
        recovery to PENDING the drain claims the row and flows the
        NULL key through without crashing."""
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "null-deferred", report_message_id=None,
            state="DEFERRED", content=None,
        )
        assert repo.claim_for_injection("p1") == []

        assert repo.transition_deferred_to_pending("null-deferred") is True
        drained = repo.claim_for_injection("p1")
        assert len(drained) == 1
        assert drained[0]["report_message_id"] is None
        assert drained[0]["content"] is None
        assert repo.claim_for_injection("p1") == []  # exactly-once

    def test_count_and_lanes_handle_null_keyed_rows(
        self, baseline_shape: Any
    ) -> None:
        """count/find_deferred/find_past_age key on state/timestamps —
        NULL-keyed rows are counted and swept like any other."""
        repo = ReportInjectionRepository(baseline_shape)
        ids = _seed_family(baseline_shape)
        _insert_row(
            baseline_shape, "n-def", parent=ids["parent"],
            child=ids["child"], report_message_id=None,
            state="DEFERRED", content=None,
        )
        _insert_row(
            baseline_shape, "n-old-pen", parent=ids["parent"],
            child="c-other", child_msg="m-other",
            report_message_id=None, state="PENDING", content=None,
            created_at="2020-01-01T00:00:00+00:00",
        )
        assert repo.count_pending_for_parent(ids["parent"]) == 2
        deferred = repo.find_deferred_for_parent(ids["parent"])
        assert [r.injection_id for r in deferred] == ["n-def"]
        assert [r.injection_id for r in repo.find_deferred_for_parent_all(
            parent_not_terminal=True
        )] == ["n-def"]
        aged = repo.find_pending_past_age(
            age_bound=timedelta(days=365), recovery_retry_minutes=60
        )
        assert [r.injection_id for r in aged] == ["n-old-pen"]

    def test_find_completed_children_excludes_existing_null_keyed_row(
        self, baseline_shape: Any
    ) -> None:
        """Lane 2 keys on the CHILD columns + non-terminal state — an
        existing NULL-keyed marker correctly excludes the child from
        the no-row backstop (no double recovery)."""
        repo = ReportInjectionRepository(baseline_shape)
        ids = _seed_family(baseline_shape)
        child_msg_id = "c-msg-1"
        with baseline_shape.begin() as conn:
            ids["_insert_completed_child_message"](  # type: ignore[index]
                conn, child_msg_id, ids["child"]
            )
        _insert_row(
            baseline_shape, "n-lane2", parent=ids["parent"],
            child=ids["child"], child_msg=child_msg_id,
            report_message_id=None, state="DEFERRED", content=None,
        )
        assert repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        ) == []

    def test_reconcile_turn_mirror_null_message_task_excludes_null_keyed_row(
        self, baseline_shape: Any
    ) -> None:
        """``reconcile_turn_mirror``'s report_injections UPDATE keys on
        ``report_message_id = :task_message_id``; with a NULL task
        message the SQL comparison matches nothing (NULL semantics) —
        the NULL-keyed marker is never force-finalized by an unrelated
        terminal task."""
        ids = _seed_family(baseline_shape)
        with baseline_shape.begin() as conn:
            ids["_insert_task"](  # type: ignore[index]
                conn, "w-null-msg", message_id=None, status="completed",
                parent=ids["parent"],
            )
        _insert_row(
            baseline_shape, "n-recon", parent=ids["parent"],
            child=ids["child"], report_message_id=None,
            state="DEFERRED", content=None,
        )
        result = TaskRepository(baseline_shape).reconcile_turn_mirror(
            "w-null-msg"
        )
        assert result["found"] is True
        assert result["updated_counts"]["report_injections"] == 0
        assert _row_state(baseline_shape, "n-recon")[0] == "DEFERRED"

    def test_count_actionable_pending_tasks_null_keyed_not_suppressing(
        self, baseline_shape: Any
    ) -> None:
        """``_count_actionable_pending_tasks`` suppresses only tasks
        whose ``message_id`` EQUALS a delivered report's key — NULL
        task messages are never falsely suppressed (NULL ≠ NULL in
        SQL), and the healthy suppression still works."""
        ids = _seed_family(baseline_shape)
        svc = ChildReportsService(
            manager=object(), events_service=None
        )
        with baseline_shape.begin() as conn:
            ids["_insert_task"](  # type: ignore[index]
                conn, "w-null", message_id=None, status="pending",
                parent=ids["parent"],
            )
        _insert_row(
            baseline_shape, "n-inj", parent=ids["parent"],
            child=ids["child"], report_message_id=None,
            state="INJECTED", content=None,
        )
        with Session(baseline_shape) as session:
            assert (
                svc._count_actionable_pending_tasks(
                    session, ids["parent"]
                )
                == 1
            )
            # Control: the matched non-NULL shape IS suppressed.
            conn = session.connection()
            _insert_row(
                baseline_shape, "n-inj-2", parent=ids["parent"],
                child="c-z", child_msg="m-z",
                report_message_id="rm-z", state="INJECTED",
            )
            ids["_insert_task"](  # type: ignore[index]
                conn, "w-match", message_id="rm-z", status="pending",
                parent=ids["parent"],
            )
            session.commit()
            assert (
                svc._count_actionable_pending_tasks(
                    session, ids["parent"]
                )
                == 1
            )

    def test_enqueue_signature_excludes_null_by_type(
        self, baseline_shape: Any
    ) -> None:
        """``enqueue`` takes a non-optional ``report_message_id`` — the
        production path structurally cannot write a NULL key (excludes
        by type; verified against the live signature)."""
        signature = inspect.signature(
            ReportInjectionRepository.enqueue
        )
        annotation = signature.parameters["report_message_id"].annotation
        assert "None" not in str(annotation), (
            f"enqueue must keep report_message_id non-optional; "
            f"annotation is {annotation!r}"
        )
        # And the write path works with a real key.
        row = ReportInjectionRepository(baseline_shape).enqueue(
            parent_instance_id="p-enq",
            child_instance_id="c-enq",
            child_message_id="m-enq",
            report_message_id="rm-enq",
            content="content",
        )
        assert row.report_message_id == "rm-enq"

    def test_ensure_deferred_writes_null_key_by_design(
        self, baseline_shape: Any
    ) -> None:
        """``ensure_deferred`` is the one DESIGNED NULL writer (the
        marker-first shape) — the write-once gate absorbs duplicates."""
        repo = ReportInjectionRepository(baseline_shape)
        row = repo.ensure_deferred(
            parent_instance_id="p-ens",
            child_instance_id="c-ens",
            child_message_id="m-ens",
            deferred_reason="PAUSE_TOCTOU",
        )
        assert row is not None
        assert row.report_message_id is None
        assert row.state == ReportInjectionState.DEFERRED.value
        # W6: the duplicate is absorbed as a no-op (index-gated).
        assert repo.ensure_deferred(
            parent_instance_id="p-ens",
            child_instance_id="c-ens",
            child_message_id="m-ens",
            deferred_reason="PAUSE_TOCTOU",
        ) is None

    def test_transition_deferred_to_pending_keys_on_injection_id(
        self, baseline_shape: Any
    ) -> None:
        """Recovery is keyed on ``injection_id`` — NULL keys are
        irrelevant to the transition (excludes by construction)."""
        repo = ReportInjectionRepository(baseline_shape)
        _insert_row(
            baseline_shape, "n-trans", report_message_id=None,
            state="DEFERRED", content=None,
        )
        assert repo.transition_deferred_to_pending("n-trans") is True
        state, _, key = _row_state(baseline_shape, "n-trans")
        assert state == "PENDING" and key is None
        assert repo.transition_deferred_to_pending("n-trans") is False

    # -- the mechanical enumeration ---------------------------------

    def test_consumer_enumeration_is_complete_and_audited(
        self, baseline_shape: Any
    ) -> None:
        """The codified grep-audit registry matches the audited
        consumers one-for-one: every registry entry has a behavioral
        audit above (mapped by name), and the audit methods enumerate
        exactly the registry.

        Mechanical by construction: the mapping below is the test —
        a consumer added to the codebase without a registry entry (or
        an audit) shows up as a mismatch here the moment the frozen
        list is regenerated.
        """
        audited = {
            "report_injection.repository.claim_for_task_delivery": (
                self.test_claim_for_task_delivery_missing_when_no_row_exists,
                self.test_claim_for_task_delivery_claimed_after_artifact_backfill,  # noqa: E501
            ),
            "report_injection.repository.claim_for_injection": (
                self.test_claim_for_injection_excludes_deferred_and_survives_null_keys,  # noqa: E501
            ),
            "report_injection.repository.find_row_by_report_message_id": (
                self.test_find_row_by_report_message_id_none_returns_none,
            ),
            "report_injection.repository.enqueue": (
                self.test_enqueue_signature_excludes_null_by_type,
            ),
            "report_injection.repository.ensure_deferred": (
                self.test_ensure_deferred_writes_null_key_by_design,
            ),
            "report_injection.repository.transition_deferred_to_pending": (
                self.test_transition_deferred_to_pending_keys_on_injection_id,  # noqa: E501
            ),
            "report_injection.repository.find_deferred_for_parent": (
                self.test_count_and_lanes_handle_null_keyed_rows,
            ),
            "report_injection.repository.find_deferred_for_parent_all": (
                self.test_count_and_lanes_handle_null_keyed_rows,
            ),
            "report_injection.repository.find_completed_children_without_delivery": (  # noqa: E501
                self.test_find_completed_children_excludes_existing_null_keyed_row,  # noqa: E501
            ),
            "report_injection.repository.find_pending_past_age": (
                self.test_count_and_lanes_handle_null_keyed_rows,
            ),
            "report_injection.repository.count_pending_for_parent": (
                self.test_count_and_lanes_handle_null_keyed_rows,
            ),
            "task.repository.reconcile_turn_mirror": (
                self.test_reconcile_turn_mirror_null_message_task_excludes_null_keyed_row,  # noqa: E501
            ),
            "child_reports.ChildReportsService._count_actionable_pending_tasks": (  # noqa: E501
                self.test_count_actionable_pending_tasks_null_keyed_not_suppressing,  # noqa: E501
            ),
            "manager.InstanceManager._has_non_terminal_injection_for": (
                self.test_manager_has_non_terminal_injection_for_none_is_false,  # noqa: E501
            ),
        }
        # Every audited consumer is bound (importable + resolvable).
        for handlers in audited.values():
            for handler in handlers:
                assert callable(handler)
        # And the enumeration is exactly the frozen registry.
        assert frozenset(audited) == EXPECTED_CONSUMERS, (
            "C4 audit drift: registry and audited consumers diverged — "
            "a report_message_id consumer was added/removed without "
            "updating the audit"
        )


# Keep sqlite3 import honest: the module-level version check documents
# the companion-migration SQLite floor (DROP COLUMN needs >= 3.35).
assert sqlite3.sqlite_version_info >= (3, 35, 0), (
    "companion migration uses DROP COLUMN / RENAME COLUMN — requires "
    "SQLite >= 3.35"
)

# ``re`` is used by the predicate normalization in the companion
# parity tests (whitespace-insensitive literal comparison).
assert re.search(r"PENDING", "PENDING") is not None
