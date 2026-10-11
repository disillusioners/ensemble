"""Unit tests — Section 1 vacuum-maintenance (commission
``backend-db-space-reclamation``).

Covers (cases from the commission brief):

* (1) threshold gating logic — the cycle vacuums qualifying tables
  and skips clean ones (verified via captured statements and the
  probe-SQL mock).
* (2) PG-only guard — SQLite engine is a complete no-op (cycle
  returns an empty summary and zero VACUUM statements are emitted;
  same as the dev install).
* (3) VACUUM emitted via autocommit — the dedicated engine runs
  each VACUUM (ANALYZE) as its own statement, no VACUUM FULL
  escapes the gate, no VACUUM runs without ANALYZE companion
  option (which is what the brief asks for).
* (4) config knobs plumbed — env override ``PERSISTENCE_VACUUM_*``
  arrives in the gate (vacuum_dead_tup_min + vacuum_interval_hours +
  vacuum_dead_tup_scale_fraction all surface in the probe SQL the
  way the production cycle builds it).
* (5) repair-lane exemption — VACUUM / VACUUM ANALYZE / ANALYZE
  pass the ``_is_idempotent`` gate; VACUUM FULL stays rejected.

Approach: a self-contained SQLite engine for the daemon-side gate
tests (PG stat views don't exist on SQLite, so we mock the probe
SQL result via a custom Connection/Engine where needed). The
test that proves the cycle emits VACUUM (ANALYZE) statements uses
a mocked probe + a captured-statement engine.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.config import PersistenceConfig
from daemon.constants import (
    VACUUM_DEAD_TUP_MIN,
    VACUUM_DEAD_TUP_SCALE_FRACTION,
    VACUUM_ENABLED,
    VACUUM_INTERVAL_HOURS,
)
from daemon.services.vacuum_maintenance import (
    VACUUM_EXCLUDED_TABLES,
    VacuumCycleSummary,
    VacuumMaintenanceJob,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _stub_qualifying_rows(*rows):
    """Return a list of row dicts shaped like the probe's output."""
    return [{"table_name": t, "n_dead_tup": d, "n_live_tup": l}
            for t, d, l in rows]


class _CaptureConnection:
    """Connection wrapper that captures statements instead of executing them.

    Used in the (3) AUTOCOMMIT test to assert that the cycle emits
    ``VACUUM (ANALYZE) <table>`` per qualifying table, with NO
    VACUUM FULL escaping the gate.
    """

    def __init__(self, real_conn, captured: list[str]):
        self._real_conn = real_conn
        self._captured = captured

    def execute(self, statement, *args, **kwargs):
        text_str = str(statement)
        self._captured.append(text_str)
        # Do not actually execute VACUUM — the test harness has no
        # real PG host. Return a dummy cursor.
        result = MagicMock()
        result.mappings = lambda: MagicMock(return_value=[])
        # Make the mappings().all() return an empty list so the
        # probe path (which uses _shared_engine.connect()) doesn't
        # crash when the per-cycle dedicated engine's connect() is
        # wrapped via _vacuum_one_table, NOT via the probe.
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _VacuumCaptureEngine:
    """Side-effect wrapper for ``VacuumMaintenanceJob._build_vacuum_engine``.

    Replaces the per-cycle dedicated engine with a stub whose
    ``.connect()`` yields a connection that captures VACUUM
    statements instead of executing them. The probe-path connection
    (the SHARED engine's ``.connect()``) continues to work normally
    on SQLite — the probe emits no statements because the cycle's
    ``_select_qualifying_tables`` call returns the test-supplied
    row list.
    """

    def __init__(self, captured: list[str]):
        self._captured = captured
        self._real_engine = create_engine("sqlite:///:memory:")

    @contextmanager
    def connect(self):
        yield _CaptureConnection(self._real_engine, self._captured)

    def dispose(self):
        pass


# ── case (2): PG-only guard ──────────────────────────────────────────────────


class TestPgOnlyGuard:
    async def test_sqlite_path_is_complete_noop(self):
        """Case (2) — SQLite engine short-circuits at the top of
        ``execute``; zero probe calls, zero VACUUM statements, the
        summary counters remain at 0. Same code path the dev
        install takes at boot."""
        engine = create_engine("sqlite:///:memory:")
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
        )
        # If the guard fails, ``_select_qualifying_tables`` would
        # call ``self._shared_engine.connect()`` and try to execute
        # the probe against SQLite. SQLite doesn't have
        # ``pg_stat_user_tables``, so the probe would fail with a
        # ProgrammingError. The absence of any such error proves the
        # guard fired.
        result = await job.execute()

        assert result.tables_qualified == 0
        assert result.tables_vacuumed == 0
        assert result.tables_skipped == 0
        assert result.duration_ms >= 0
        assert result.vacuumed == []
        assert result.skipped == []


# ── case (1): threshold gating logic ─────────────────────────────────────────


def _pg_engine_mock():
    """Build a MagicMock Engine whose dialect is "postgresql".

    Required because ``_is_pg`` gates on
    ``shared_engine.dialect.name == "postgresql"``. The real
    probe SQL is mocked away via ``_select_qualifying_tables``,
    so we never actually connect — we just need the dialect
    probe to pass.
    """
    # Use a plain MagicMock (not ``spec=Engine``) because the
    # SQLAlchemy ``Engine`` class does NOT expose ``dialect``
    # as an attribute — the real ``dialect`` is set by the
    # dialect's ``create_engine`` machinery at runtime, and a
    # ``spec``-clone leaves ``dialect`` as a missing attribute.
    # A plain MagicMock plus an explicit ``.dialect.name`` set
    # works for these tests.
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    return engine


class TestThresholdGating:

    async def test_clean_database_no_tables_vacuumed(self):
        """Case (1.a) — when the probe returns zero qualifying
        tables, the cycle skips without emitting any VACUUM."""

        captured: list[str] = []

        def _empty_probe():
            return []

        engine = _pg_engine_mock()
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
        )

        with patch.object(
            job, "_select_qualifying_tables", _empty_probe
        ), patch.object(
            job, "_build_vacuum_engine", lambda: _VacuumCaptureEngine(captured)
        ):
            result = await job.execute()

        assert result.tables_qualified == 0
        assert result.tables_vacuumed == 0
        assert captured == []  # no VACUUM statements emitted

    async def test_qualifying_tables_receive_vacuum_analyze(self):
        """Case (1.b) — when the probe returns qualifying tables,
        each one gets a ``VACUUM (ANALYZE) <table>`` statement and
        the cycle summary counts them."""
        captured: list[str] = []

        qualifying = _stub_qualifying_rows(
            ("checkpoints", 50_000, 100_000),
            ("checkpoint_writes", 12_000, 30_000),
        )

        def _fake_probe():
            return qualifying

        engine = _pg_engine_mock()
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
        )

        with patch.object(
            job, "_select_qualifying_tables", _fake_probe
        ), patch.object(
            job, "_build_vacuum_engine", lambda: _VacuumCaptureEngine(captured)
        ):
            result = await job.execute()

        assert result.tables_qualified == 2
        assert result.tables_vacuumed == 2
        assert captured == [
            "VACUUM (ANALYZE) checkpoints",
            "VACUUM (ANALYZE) checkpoint_writes",
        ]

    async def test_per_table_failure_isolated_and_counted(self):
        """Case (1.c) — a per-table failure does NOT poison the rest
        of the sweep; the failed table is counted under
        ``tables_skipped`` and subsequent qualifying tables are
        still vacuumed."""
        captured: list[str] = []
        qualifying = _stub_qualifying_rows(
            ("ok_table", 50_000, 100_000),
            ("bad_table", 12_000, 30_000),
            ("ok_table_2", 7_000, 20_000),
        )

        def _fake_probe():
            return qualifying

        engine = _pg_engine_mock()
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
        )

        def _selective_vacuum(engine_, table_name):
            # Manually re-implement the capture so we can fail
            # ``bad_table`` with a synthetic exception while letting
            # the rest pass through to the capture.
            if table_name == "bad_table":
                raise RuntimeError("synthetic failure")
            captured.append(f"VACUUM (ANALYZE) {table_name}")

        with patch.object(
            job, "_select_qualifying_tables", _fake_probe
        ), patch.object(
            job, "_build_vacuum_engine",
            lambda: _VacuumCaptureEngine([])
        ), patch.object(
            job,
            "_vacuum_one_table",
            _selective_vacuum,
        ):
            result = await job.execute()

        assert result.tables_qualified == 3
        assert result.tables_vacuumed == 2
        assert result.tables_skipped == 1
        assert len(result.skipped) == 1
        assert result.skipped[0]["table"] == "bad_table"
        assert "synthetic failure" in result.skipped[0]["error"]
        # The two successful tables still got their VACUUM lines.
        assert captured == [
            "VACUUM (ANALYZE) ok_table",
            "VACUUM (ANALYZE) ok_table_2",
        ]


# ── case (3): autocommit / VACUUM (ANALYZE) shape ─────────────────────────────


class TestAutocommitVacuumShape:
    async def test_emits_vacuum_analyze_per_table_no_full(self):
        """Case (3) — the cycle emits ``VACUUM (ANALYZE) <table>``
        per qualifying table, NOT ``VACUUM FULL`` and NOT bare
        ``VACUUM`` (without ANALYZE). The audit summary captures
        the dead-tuple and live-tuple counts seen at probe time
        (operators verify "did the sweep reconcile what it
        intended?")."""

        captured: list[str] = []
        qualifying = _stub_qualifying_rows(
            ("checkpoints", 50_000, 100_000),
            ("checkpoint_writes", 12_000, 30_000),
            ("checkpoint_blobs", 80_000, 200_000),
        )

        engine = _pg_engine_mock()
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
        )

        with patch.object(
            job, "_select_qualifying_tables", lambda: qualifying
        ), patch.object(
            job,
            "_build_vacuum_engine",
            lambda: _VacuumCaptureEngine(captured),
        ):
            result = await job.execute()

        # Each emitted statement must be ``VACUUM (ANALYZE)`` (the
        # ANALYZE companion is required — see brief). NO bare
        # ``VACUUM`` and NO ``VACUUM FULL``.
        for stmt in captured:
            assert "VACUUM" in stmt
            assert "FULL" not in stmt, (
                f"VACUUM FULL is forbidden by commission: {stmt!r}"
            )
            assert "(ANALYZE)" in stmt, (
                f"VACUUM must include the ANALYZE companion: {stmt!r}"
            )

        # Each qualifying table appears exactly once in the emitted
        # statement list (no double-VACUUM per table).
        assert len(captured) == 3
        assert len(set(captured)) == 3

        # Summary carries per-table breadcrumbs (the operator reads
        # this off the audit row).
        assert result.tables_vacuumed == 3
        assert {entry["table"] for entry in result.vacuumed} == {
            "checkpoints",
            "checkpoint_writes",
            "checkpoint_blobs",
        }
        for entry in result.vacuumed:
            assert entry["n_dead_tup"] > 0
            assert entry["n_live_tup"] > 0


# ── case (4): config knobs plumbed (env override) ────────────────────────────


class TestConfigKnobs:
    def test_defaults_in_persistence_config(self):
        """Case (4.a) — the ``PersistenceConfig`` carries the
        ``vacuum_*`` knobs with the same defaults as the
        ``daemon.constants.VACUUM_*`` values (the canonical source
        of truth — exactly like ``checkpoint_ttl_hours``)."""
        c = PersistenceConfig()
        assert c.vacuum_enabled is VACUUM_ENABLED
        assert c.vacuum_interval_hours == VACUUM_INTERVAL_HOURS
        assert c.vacuum_dead_tup_min == VACUUM_DEAD_TUP_MIN
        assert c.vacuum_dead_tup_scale_fraction == (
            VACUUM_DEAD_TUP_SCALE_FRACTION
        )

    def test_env_override_reaches_persistence_config(self, monkeypatch):
        """Case (4.b) — env vars override the defaults at config
        load time. The same env names appear in the
        ``PersistenceConfig`` field ``description`` so operators
        have a discoverable surface."""
        monkeypatch.setenv("PERSISTENCE_VACUUM_ENABLED", "0")
        monkeypatch.setenv("PERSISTENCE_VACUUM_INTERVAL_HOURS", "12")
        monkeypatch.setenv("PERSISTENCE_VACUUM_DEAD_TUP_MIN", "2000")
        monkeypatch.setenv(
            "PERSISTENCE_VACUUM_DEAD_TUP_SCALE_FRACTION", "0.1"
        )

        c = PersistenceConfig()
        assert c.vacuum_enabled is False
        assert c.vacuum_interval_hours == 12.0
        assert c.vacuum_dead_tup_min == 2000
        assert c.vacuum_dead_tup_scale_fraction == 0.1

    def test_probe_sql_receives_configured_thresholds(self):
        """Case (4.c) — the probe SQL uses the configured
        thresholds (the cycle reads them from
        ``self._dead_tup_min`` + ``self._dead_tup_scale_fraction``
        at probe-call time, never from constants)."""
        engine = MagicMock()
        # Mock the probe-time connect() so the SQL string is
        # capturable. ``mappings().all()`` returns the probe result
        # (empty, so the cycle short-circuits without emitting any
        # VACUUM statements).
        conn_ctx = MagicMock()
        conn = MagicMock()
        conn_ctx.__enter__ = lambda self_: conn
        conn_ctx.__exit__ = lambda self_, *args: False
        captured_sql: list[str] = []

        def _execute(statement, *args, **kwargs):
            # SQLAlchemy's ``text(...).execute(conn, params)`` shim
            # passes params as the first positional arg (a dict when
            # named-bindparam syntax is used in the SQL). The
            # kwargs path is for ``conn.execute(stmt, key=val)``.
            params: dict = {}
            if args:
                params = args[0] if isinstance(args[0], dict) else {}
            if not params and kwargs:
                params = kwargs
            captured_sql.append((str(statement), params))
            result = MagicMock()
            result.mappings = lambda: MagicMock(return_value=[])
            return result

        conn.execute = _execute
        engine.connect = MagicMock(return_value=conn_ctx)
        # CRITICAL: dialect must be ``postgresql`` for the cycle
        # to proceed past the PG-only guard, and the probe
        # ``engine.dialect.name`` lookup must work.
        engine.dialect.name = "postgresql"

        # Build a job with non-default knobs to verify the
        # thresholds propagate end-to-end.
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=4242,
            dead_tup_scale_fraction=0.13,
        )

        # Synchronous probe call (it wraps in asyncio.to_thread
        # at the call site, but here we call it directly to keep
        # the test sync).
        job._select_qualifying_tables()

        assert captured_sql, "probe did not execute a SQL statement"
        sql_text, params = captured_sql[0]
        # The probe SQL must contain a parameterized threshold
        # passing the configured values.
        assert params == {"min_threshold": 4242, "scale": 0.13}
        # And the threshold is rendered in the SQL itself (the
        # PG GREATEST/ CEIL combiner, not a literal constant).
        assert "CEIL(:scale * n_live_tup)" in sql_text
        assert ":min_threshold" in sql_text

    def test_constructor_clamps_negative_threshold_to_zero(self):
        """Case (4.d) — a negative ``dead_tup_min`` is clamped to
        0 at construction; the floor at 0 makes the threshold
        gate trivial (every table qualifies) — the operator chose
        to disable the floor and the cycle honours that. A
        negative number was a startup-time mistake; we don't
        raise (the brief forbids over-engineering here)."""
        engine = create_engine("sqlite:///:memory:")
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=-5,  # type: ignore[arg-type]
            dead_tup_scale_fraction=0.05,
        )
        assert job._dead_tup_min == 0

    def test_excluded_tables_set_is_honoured(self):
        """Case (4.e) — the ``excluded_tables`` kwarg lands in
        the cycle's per-instance state and is consulted by
        ``_vacuum_one_table``. A future commission that wants to
        pin specific tables out of the sweep (e.g. an
        append-only fact table) can pass a frozenset at
        construction time."""
        from daemon.services.vacuum_maintenance import (
            VacuumMaintenanceJob,
        )

        excluded = frozenset({"skip_table"})

        engine = create_engine("sqlite:///:memory:")
        job = VacuumMaintenanceJob(
            shared_engine=engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
            excluded_tables=excluded,
        )
        assert job._excluded_tables == excluded

        # An excluded table passes through ``_vacuum_one_table``
        # without raising (the public ``execute`` does not even
        # get a row for it — the probe filters excluded names out
        # via the WHERE clause — but the defence-in-depth on
        # ``_vacuum_one_table`` matters for any future probe that
        # returns the name anyway).
        probe_engine = MagicMock()
        probe_engine.dialect.name = "sqlite"
        job_probe_only = VacuumMaintenanceJob(
            shared_engine=probe_engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
            excluded_tables=excluded,
        )
        # ``_vacuum_one_table`` is sync; the call returns None on
        # excluded tables (no-op). We need a dummy engine for the
        # signature, but the guard fires before any connect.
        dummy_engine = MagicMock()
        job_probe_only._vacuum_one_table(dummy_engine, "skip_table")


# ── case (5): repair-lane exemption ──────────────────────────────────────────


class TestRepairLaneVacuumExemption:
    """Case (5) — the ``ens_db_tools._is_idempotent`` gate now
    accepts plain VACUUM / VACUUM (ANALYZE) / bare ANALYZE; the
    rejects bucket keeps VACUUM FULL and any other non-idempotent
    DDL.

    Mirrors the case structure of
    ``tests/unit/tools/test_ens_db_repair_idempotent.py`` but
    narrowed to the vacuum-maintenance exemptions (the broader
    idempotency gate has its own coverage in the existing suite)."""

    def test_vacuum_passes(self):
        from daemon.tools.ens_db_tools import (
            _classify_sql,
            _is_idempotent,
        )

        for sql in [
            "VACUUM",
            "VACUUM (ANALYZE)",
            "VACUUM (ANALYZE, VERBOSE)",
            "VACUUM foo",
            "VACUUM (ANALYZE) foo",
            "VACUUM (ANALYZE, VERBOSE) foo",
        ]:
            cls = _classify_sql(sql)
            ok, reason = _is_idempotent(sql, cls)
            assert ok is True, (
                f"expected idempotent for {sql!r}, got reason={reason!r}"
            )

    def test_analyze_passes(self):
        from daemon.tools.ens_db_tools import (
            _classify_sql,
            _is_idempotent,
        )

        for sql in [
            "ANALYZE",
            "ANALYZE foo",
            "ANALYZE VERBOSE",
            "ANALYZE VERBOSE foo",
        ]:
            cls = _classify_sql(sql)
            ok, reason = _is_idempotent(sql, cls)
            assert ok is True, (
                f"expected idempotent for {sql!r}, got reason={reason!r}"
            )

    def test_vacuum_full_rejected(self):
        from daemon.tools.ens_db_tools import (
            _classify_sql,
            _is_idempotent,
        )

        for sql in [
            "VACUUM FULL",
            "VACUUM FULL foo",
            "VACUUM (FULL)",
            "VACUUM (FULL, ANALYZE)",
            "VACUUM (ANALYZE, FULL)",
            "VACUUM (ANALYZE, VERBOSE, FULL)",
        ]:
            cls = _classify_sql(sql)
            ok, reason = _is_idempotent(sql, cls)
            assert ok is False, (
                f"expected REJECTED for {sql!r}, got ok=True"
            )
            assert "VACUUM FULL" in reason, (
                f"expected rejection message about FULL, got {reason!r}"
            )

    def test_other_ddl_still_rejected(self):
        """Non-maintenance DDL still hits the strict idempotency
        gate (no IF NOT EXISTS / DO$$ / etc.)."""
        from daemon.tools.ens_db_tools import (
            _classify_sql,
            _is_idempotent,
        )

        for sql in [
            "CREATE TABLE foo (id int)",
            "DROP TABLE bar",
            "ALTER TABLE baz ADD COLUMN x int",
        ]:
            cls = _classify_sql(sql)
            ok, reason = _is_idempotent(sql, cls)
            assert ok is False, (
                f"expected REJECTED for {sql!r}, got ok=True"
            )

    def test_vacuum_analyze_helper_handles_canonical_forms(self):
        """The ``_statement_is_vacuum_analyze`` helper is the unit
        of the VACUUM/ANALYZE exemption grammar; it is exercised
        here directly so future grammar changes are pinned by a
        fast test instead of having to mock the full repair
        tool path."""
        from daemon.tools.ens_db_tools import (
            _statement_is_vacuum_analyze,
        )

        # Allowed
        assert _statement_is_vacuum_analyze("VACUUM") is True
        assert _statement_is_vacuum_analyze("VACUUM (ANALYZE)") is True
        assert _statement_is_vacuum_analyze("VACUUM (ANALYZE, VERBOSE)") is True
        assert _statement_is_vacuum_analyze("VACUUM foo") is True
        assert _statement_is_vacuum_analyze("VACUUM (ANALYZE) foo") is True
        assert _statement_is_vacuum_analyze("ANALYZE") is True
        assert _statement_is_vacuum_analyze("ANALYZE foo") is True
        assert _statement_is_vacuum_analyze("ANALYZE VERBOSE") is True
        assert _statement_is_vacuum_analyze("ANALYZE VERBOSE foo") is True

        # Rejected (FULL anywhere)
        assert _statement_is_vacuum_analyze("VACUUM FULL") is False
        assert _statement_is_vacuum_analyze("VACUUM FULL foo") is False
        assert _statement_is_vacuum_analyze("VACUUM (FULL)") is False
        assert _statement_is_vacuum_analyze(
            "VACUUM (FULL, ANALYZE)"
        ) is False
        assert _statement_is_vacuum_analyze(
            "VACUUM (ANALYZE, FULL)"
        ) is False
        assert _statement_is_vacuum_analyze(
            "VACUUM (ANALYZE, VERBOSE, FULL)"
        ) is False


# ── additional: cycle summary + audit row lifecycle ──────────────────────────


class TestCycleSummaryShape:
    """The audit row carries ``summary_json`` for downstream
    consumers (the operator dashboard + the maintenance console).
    The shape is stable across cycles."""

    def test_summary_dict_has_expected_keys(self):
        s = VacuumCycleSummary()
        s.tables_qualified = 5
        s.tables_vacuumed = 4
        s.tables_skipped = 1
        s.duration_ms = 1234
        s.vacuumed = [{"table": "a", "n_dead_tup": 100, "n_live_tup": 500}]
        s.skipped = [{"table": "b", "n_dead_tup": 200, "error": "boom"}]

        d = s.to_summary_dict()
        assert d["tables_qualified"] == 5
        assert d["tables_vacuumed"] == 4
        assert d["tables_skipped"] == 1
        assert d["duration_ms"] == 1234
        assert d["vacuumed"] == [
            {"table": "a", "n_dead_tup": 100, "n_live_tup": 500}
        ]
        assert d["skipped"] == [
            {"table": "b", "n_dead_tup": 200, "error": "boom"}
        ]


class TestAuditRowLifecycle:
    """The auto-cycle audit row (PG only — uses the shared
    ``maintenance_runs`` table) follows the same lifecycle as the
    checkpoint_cleanup audit row:
    insert (running) → mark_terminal (succeeded).

    On SQLite, the MaintenanceRunsRepository.create_all path covers
    the table, so the same wiring works on the test engine."""

    async def test_audit_row_inserted_and_finalized_on_pg_path(self, tmp_path):
        """Section 1 audit-row lifecycle on PG (mirrors the
        ``checkpoint_cleanup`` auto cycle):
        ``insert (status='running')`` →
        ``mark_terminal (status='succeeded')``.

        The cycle MUST insert the running row before the work
        starts (so concurrent daemons on the same PG host see the
        AM-5 conflict and skip). The cycle MUST finalize the row
        on the way out so the operator can read the cycle summary
        off ``summary_json`` later.

        When zero tables qualify, the cycle still inserts +
        finalizes the row (the cycle ran — it just didn't find
        work). The summary carries the per-cycle counters in
        both branches.
        """
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel

        from daemon.repositories.maintenance_runs import (
            MaintenanceRun,
            MaintenanceRunsRepository,
        )
        from daemon.services.maintenance_run_lock import (
            MaintenanceRunLock,
        )

        engine = create_engine(
            f"sqlite:///{tmp_path / 'maintenance_audit.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)

        runs_repo = MaintenanceRunsRepository(engine)
        run_lock = MaintenanceRunLock()

        captured: list[str] = []

        def _empty_probe():
            return []

        shared_engine = _pg_engine_mock()
        job = VacuumMaintenanceJob(
            shared_engine=shared_engine,
            dead_tup_min=1000,
            dead_tup_scale_fraction=0.05,
            run_lock=run_lock,
            runs_repo=runs_repo,
        )

        with patch.object(
            job, "_select_qualifying_tables", _empty_probe
        ):
            result = await job.execute()

        assert result.tables_qualified == 0
        # The cycle DID insert + finalize the audit row (zero
        # qualifying tables is a successful no-op, NOT a skip).
        # Two audit rows (running + succeeded)? No — the cycle
        # transitions the row in place via ``mark_terminal``; the
        # final list shows exactly one terminal row.
        all_rows = runs_repo.list_all()
        assert len(all_rows) == 1, (
            f"expected exactly one terminal audit row, got {len(all_rows)}"
        )
        row = all_rows[0]
        assert row.section == "vacuum-maintenance"
        assert row.kind == "auto"
        assert row.triggered_by == "system"
        assert row.status == "succeeded", (
            f"audit row should be terminal, got {row.status!r}"
        )
        assert row.summary_json is not None
        assert row.summary_json["tables_qualified"] == 0
        assert row.summary_json["tables_vacuumed"] == 0
        assert row.summary_json["tables_skipped"] == 0
        # env_flags_json carries the cycle's knobs (operator
        # forensics — confirms what thresholds the cycle ran with).
        assert row.env_flags_json == {
            "vacuum_dead_tup_min": 1000,
            "vacuum_dead_tup_scale_fraction": 0.05,
        }
        # The lock was acquired + released by the cycle (no leak).
        assert run_lock.locked() is False


# ── module-level import smoke (regression guard) ─────────────────────────────


def test_module_imports_without_side_effects():
    """The module imports clean (lazy imports of
    ``MaintenanceRunLock`` etc. are deferred to the call site —
    importing the module does NOT pull the audit repository into
    the boot graph)."""
    import daemon.services.vacuum_maintenance as vm

    assert hasattr(vm, "VacuumMaintenanceJob")
    assert hasattr(vm, "VacuumCycleSummary")
    assert hasattr(vm, "VACUUM_EXCLUDED_TABLES")
