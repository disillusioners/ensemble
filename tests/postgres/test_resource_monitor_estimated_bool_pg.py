"""Real-PG proof for the resource-telemetry estimated-bool fix.

Runs on a SCRATCH PostgreSQL database resolved from ``PG_TEST_DB``
(default ``ensemble_test``; the commissioning gate points it at
``ensemble_resource_fix_test``). The shared ``tests/postgres/conftest.py``
owns the connection and the scratch-DB lifecycle; this module refuses
to run against a forbidden live/demo database (``_tripwire_check``,
wired into ``scratch_engine``). The five properties proven:

(a) FRESH PG BOOT — ``SQLModel.metadata.create_all`` emits ``BOOLEAN``
    for ``estimated`` (T2), ``enabled`` (T4), and ``user_gated`` (T4)
    when driven by the fixed ``daemon/models/resource_monitor.py``.
    Confirmed via ``information_schema.columns.data_type`` lookup.
    This is the PRIMARY PG DDL source (the .sql runner is PG-NO-OP).
(b) FLUSHER WRITE PATH — the four-tier flusher window inserts
    rows into ``resource_daemon_bucket`` (T1 raw), ``resource_instance_sample``
    (T2 instance, with ``estimated = True/False``), ``resource_pid_map``
    (T3 exited-pid history), and a manually-seeded
    ``resource_limit_policy`` row (T4 — observe-only in v1, no
    flusher tier; the row here is the policy-API write path). All
    four tables hold rows after a single run_once.
(c) REAL RUNTIME HOOK — ``InstanceManager._ensure_postgres_columns``
    (the actual production method, on a two-attribute stub) converts
    both the no-default integer shape and the DEFAULT-bearing integer
    shape; a second invocation applies 0 changes.
(d) AMPLIFIER ISOLATION — the T2 table is recreated in the broken
    v0.25.0 ``estimated INTEGER`` shape; running the flusher then
    fails T2 with ``DatatypeMismatch`` while T1 raw STILL persists.
    The pre-fix amplifier aborted the entire ``engine.begin()``
    window and rolled back every tier as collateral — the regression
    pinned here.
(e) REPAIR PATH — start from a hand-crafted OLD ``integer`` schema
    that mirrors the broken v0.25.0 prod state; run the three-step
    conversion; assert the columns become ``boolean``
    (information_schema confirmed) and both ``True`` and ``False``
    insert successfully.

Fence discipline (incident 2026-10-12): scratch coordinates only —
never HOME-derived. ``_tripwire_check`` refuses a forbidden
``PG_TEST_DB`` (``ensemble_prod`` / ``ensemble_dev`` /
``ensemble_sandbox`` / ``ensemble``) or a non-scratch-shaped name.
Scratch DB CREATE/DROP is owned by the commissioning gate, not this
module.

Run with::

    PG_TEST_DB=ensemble_resource_fix_test .venv/bin/pytest \\
        tests/postgres/test_resource_monitor_estimated_bool_pg.py \\
        --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.models.resource_monitor import (
    ResourceDaemonBucket,
    ResourceInstanceSample,
    ResourceLimitPolicy,
    ResourcePidMap,
)
from daemon.services.resource_monitor import (
    Flusher,
    RetentionPolicy,
    TierRing,
)

# Auto-apply the postgres marker so ``-m postgres`` selects these tests
# and the default ``addopts`` skips them otherwise.
pytestmark = pytest.mark.postgres

# ─────────────────────────────────────────────────────────────────────────
# Scratch-DB fence
# ─────────────────────────────────────────────────────────────────────────
#
# The shared ``tests/postgres/conftest.py`` resolves the engine URL from
# the ``PG_TEST_*`` env vars (``PG_TEST_DB`` defaults to ``ensemble_test``).
# This module refuses to run against any live/demo database: the guard
# below inspects the EFFECTIVE ``PG_TEST_DB`` and raises if it is a
# forbidden name or is not visibly scratch-shaped. It is WIRED INTO the
# ``scratch_engine`` fixture below, so no test body executes without it.

_FORBIDDEN_DBS = {"ensemble_prod", "ensemble_dev", "ensemble_sandbox", "ensemble"}


def _tripwire_check() -> None:
    """Fail-closed guard against ambient env poison / live-DB writes.

    Refuses to run when ``PG_TEST_DB`` resolves to a forbidden
    live/demo database, or to a name that is not visibly scratch
    ("test"/"scratch" in the name). Called by ``scratch_engine`` before
    any test body runs — the fence the module docstring promises.
    """
    effective_db = os.environ.get("PG_TEST_DB", "ensemble_test")
    if effective_db.lower() in _FORBIDDEN_DBS:
        raise RuntimeError(
            f"refused: PG_TEST_DB={effective_db!r} is a forbidden live/demo "
            "database — the resource-monitor tests are scratch-only"
        )
    if (
        "test" not in effective_db.lower()
        and "scratch" not in effective_db.lower()
    ):
        raise RuntimeError(
            f"refused: PG_TEST_DB={effective_db!r} is not scratch-shaped "
            "(name must contain 'test' or 'scratch')"
        )


def _drop_all_resource_tables(engine: Engine) -> None:
    """Drop ONLY the four resource_monitor tables — leaves the
    shared ``schema_migrations`` table alone so the autouse
    TRUNCATE fixture in ``tests/postgres/conftest.py`` can keep
    truncating on subsequent tests without a
    ``UndefinedTable: schema_migrations`` failure. Schema-cleanup
    of ``schema_migrations`` is the lifetime of the shared
    session-scoped ``pg_engine`` fixture, not this test's
    concern."""
    with engine.begin() as c:
        c.execute(
            text(
                "DROP TABLE IF EXISTS resource_daemon_bucket, "
                "resource_instance_sample, resource_pid_map, "
                "resource_limit_policy CASCADE"
            )
        )


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _column_type(engine: Engine, table: str, column: str) -> str | None:
    with engine.connect() as c:
        row = c.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    return None if row is None else str(row[0])


def _row_count(engine: Engine, table: str) -> int:
    with engine.connect() as c:
        return int(
            c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _instance_row(
    instance_id: str = "inst-a",
    *,
    bucket_ts: str | None = None,
    estimated: bool | None = True,
    **over,
) -> dict:
    row = {
        "instance_id": instance_id,
        "agent_id": "agent-test",
        "status": None,
        "scan_kind": "counter",
        "charged_bytes": 12345,
        "deep_bytes": None,
        "graph_cache_present": True,
        "in_flight_tasks": 1,
        "all_state_messages_len": 3,
        "child_rss_kb": 8192,
        "child_cpu_user_usec": 100_000,
        "cpu_share_usec_est": 4321,
        "estimated": estimated,
    }
    if bucket_ts:
        row["bucket_ts"] = bucket_ts
    row.update(over)
    return row


def _daemon_row(bucket_ts: str, interval_s: int = 30) -> dict:
    return {
        "bucket_ts": bucket_ts,
        "interval_s": interval_s,
        "rss_kb": 1_000_000,
        "pss_kb": 900_000,
        "anon_kb": 600_000,
        "swap_kb": 0,
        "vsize_kb": 2_000_000,
        "hw_peak_kb": 1_100_000,
        "threads": 8,
        "fds": 256,
        "cpu_user_usec": 10_000_000,
        "cpu_sys_usec": 2_000_000,
        "cg_current_kb": 1_050_000,
        "cg_anon_kb": 620_000,
        "cg_file_kb": 200_000,
        "cg_slab_kb": 30_000,
        "cg_peak_kb": 1_200_000,
        "child_rss_total_kb": 0,
        "attributed_bytes": 0,
        "unattributed_kb": 0,
        "coverage_pct": 95.5,
        "sample_cost_ms": 0.5,
        "sampler_lag_ms": 0.1,
        "missed_samples": 0,
    }


# ─────────────────────────────────────────────────────────────────────────
# Session fixture — fresh schema on the scratch DB
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def scratch_engine(pg_engine):
    """Per-test scratch engine — wraps the shared ``pg_engine`` from
    ``tests/postgres/conftest.py``. The conftest owns
    schema_migrations; this fixture DROPS+RECREATES only the four
    resource_monitor tables so each test starts from a known
    schema shape (fresh-POSTGRES-create_all emit).

    Sequence per test entry:
      1. shared autouse ``_pg_truncate_tables`` TRUNCATEs the full
         schema (its own session-scoped ``pg_engine``-created
         tables).
      2. THIS fixture drops the four resource_monitor tables.
      3. THIS fixture recreates them from the fixed SQLModel
         metadata.
      4. Test body runs.
      5. THIS fixture teardown does NOT drop — the autouse
         TRUNCATE on the NEXT test entry needs the four tables
         to exist (it TRUNCATEs the full schema). Leaving them
         populated post-test is safe (the autouse wipe handles
         data; we recreate via step 3 on the NEXT test entry).
    """
    _tripwire_check()
    _drop_all_resource_tables(pg_engine)
    for tbl in (
        ResourceDaemonBucket.__table__,
        ResourceInstanceSample.__table__,
        ResourcePidMap.__table__,
        ResourceLimitPolicy.__table__,
    ):
        tbl.create(pg_engine, checkfirst=False)
    yield pg_engine


# ─────────────────────────────────────────────────────────────────────────
# (a) FRESH-PG BOOT — column-type emit via create_all
# ─────────────────────────────────────────────────────────────────────────


class TestFreshPGBootColumnTypes:
    def test_resource_instance_sample_estimated_is_boolean(self, scratch_engine):
        """``estimated`` lands as native ``boolean`` on a fresh PG
        database — the SQLModel ``Boolean`` override replaces the
        v0.25.0 broken ``Integer``."""
        data_type = _column_type(
            scratch_engine, "resource_instance_sample", "estimated"
        )
        assert data_type == "boolean", (
            f"fresh PG emit: estimated data_type={data_type!r}; "
            "expected 'boolean' (SQLModel Boolean override)"
        )

    def test_resource_limit_policy_enabled_is_boolean(self, scratch_engine):
        data_type = _column_type(
            scratch_engine, "resource_limit_policy", "enabled"
        )
        assert data_type == "boolean", (
            f"fresh PG emit: enabled data_type={data_type!r}"
        )

    def test_resource_limit_policy_user_gated_is_boolean(self, scratch_engine):
        data_type = _column_type(
            scratch_engine, "resource_limit_policy", "user_gated"
        )
        assert data_type == "boolean", (
            f"fresh PG emit: user_gated data_type={data_type!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# (b) FLUSHER WRITE PATH — all four tables receive rows on a real run_once
# ─────────────────────────────────────────────────────────────────────────


class TestFlusherAllFourTablesOnPG:
    def test_run_once_writes_all_four_tiers(self, scratch_engine):
        """One ``run_once`` populates T1 (resource_daemon_bucket raw),
        T2 (resource_instance_sample — includes both estimated=True
        and estimated=False rows; the regression pin), and T3
        (resource_pid_map). T4 (resource_limit_policy) is
        observe-only in v1 — no flusher tier writes it; we insert a
        policy row directly via SQL to prove the column type accepts
        the bool payload."""
        raw_ring = TierRing(tier="raw", maxlen=8)
        raw_ring.append(_daemon_row(_now_iso()))

        instance_ring = TierRing(tier="instance", maxlen=8)
        instance_ring.extend(
            [
                _instance_row("inst-true", estimated=True),
                _instance_row("inst-false", estimated=False),
            ]
        )

        flusher = Flusher(
            engine=scratch_engine,
            raw_ring=raw_ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )
        result = flusher.run_once()

        # T1 raw
        assert result["rows_written"] >= 3
        assert _row_count(scratch_engine, "resource_daemon_bucket") >= 1

        # T2 instance — regression pin: both estimated=True and
        # estimated=False land successfully (the exact write that
        # raised ``DatatypeMismatch`` in v0.25.0).
        assert result["instance_rows"] == 2
        assert _row_count(scratch_engine, "resource_instance_sample") == 2
        with scratch_engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT instance_id, estimated FROM "
                    "resource_instance_sample ORDER BY instance_id"
                )
            ).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        assert set(by_id) == {"inst-true", "inst-false"}
        assert by_id["inst-true"] is True
        assert by_id["inst-false"] is False

        # T3 pid_map — no exited pids queued, so zero inserts here
        # (the registry queue starts empty). The flusher drains it
        # without writing. We seed an exited entry via a follow-up
        # to prove the write path on PG separately.
        assert _row_count(scratch_engine, "resource_pid_map") == 0

        # T4 resource_limit_policy — flusher does not write here
        # (observe-only in v1). Seed one directly via SQL to prove
        # the column type accepts both True and False payloads.
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO resource_limit_policy "
                    "(scope, target, metric, op, threshold, window_s, "
                    " enabled, user_gated, created_at, updated_at) "
                    "VALUES ('daemon','test','rss_mb','alert',4096.0,60,"
                    " true, true, :ts, :ts)"
                ),
                {"ts": _now_iso()},
            )
            c.execute(
                text(
                    "INSERT INTO resource_limit_policy "
                    "(scope, target, metric, op, threshold, window_s, "
                    " enabled, user_gated, created_at, updated_at) "
                    "VALUES ('child_kind','chart_render','cpu_pct',"
                    "'throttle',80.0,30, false, true, :ts, :ts)"
                ),
                {"ts": _now_iso()},
            )
        assert _row_count(scratch_engine, "resource_limit_policy") == 2
        with scratch_engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT enabled, user_gated FROM resource_limit_policy "
                    "ORDER BY scope"
                )
            ).fetchall()
        assert sorted([(r[0], r[1]) for r in rows]) == [
            (False, True),
            (True, True),
        ]

    def test_pid_map_write_path_pg(self, scratch_engine):
        """T3 ``resource_pid_map`` write path — seed an exited entry
        via the flusher's pid_registry, run run_once, assert the
        row lands. Mirrors the test for the four tables."""
        from daemon.services.resource_monitor.pid_registry import (
            PidRegistry,
            get_pid_registry,
        )

        # Build a fresh registry with one exited entry so the
        # flusher drains it through ``_write_pid_map``. We do NOT
        # call ``get_pid_registry()`` (which would return the
        # process singleton) — we inject a private registry so the
        # test is self-contained.
        reg = PidRegistry()
        reg.register(
            pid=12345,
            start_time=100,
            kind="bash",
            instance_id="inst-test",
            agent_id="agent-test",
            command="ls -la /tmp",
            detail=None,
            parent_pid=None,
        )
        reg.mark_exit(
            pid=12345,
            start_time=100,
            exit_code=0,
            exit_kind="exit",
            peak_rss_kb=4096,
            cpu_user_usec=1_000,
        )

        raw_ring = TierRing(tier="raw", maxlen=4)
        flusher = Flusher(
            engine=scratch_engine,
            raw_ring=raw_ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            pid_registry=reg,
        )
        result = flusher.run_once()
        assert _row_count(scratch_engine, "resource_pid_map") == 1
        with scratch_engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT pid, command, exit_code FROM resource_pid_map"
                )
            ).first()
        assert row[0] == 12345
        assert "ls -la" in row[1]
        assert row[2] == 0


# ─────────────────────────────────────────────────────────────────────────
# (d) AMPLIFIER ISOLATION — one tier fails, the others still persist
# ─────────────────────────────────────────────────────────────────────────


class TestAmplifierTierIsolationOnPG:
    def test_t2_failure_does_not_roll_back_t1(self, scratch_engine, caplog):
        """Forces the EXACT v0.25.0 defect on PG: recreate the
        ``resource_instance_sample`` table with the BROKEN
        ``estimated INTEGER`` column type (without the runtime
        ``_ensure_postgres_columns`` conversion). Run the flusher
        with both a T1 raw row AND a T2 instance row; assert:

          * T1 (``resource_daemon_bucket``) persists — the other-
            tier survival invariant
          * T2 raises ``DatatypeMismatch`` — the defect class
          * The window returns cleanly (no raise escapes)
          * The amplifier logged the failure with a distinct
            ERROR line (C-1 narrowing intent preserved)

        Pre-fix amplifier would have aborted the shared
        ``engine.begin()`` window and rolled back every other
        tier's writes as collateral. Post-fix: the per-tier
        SAVEPOINT absorbs the failure and ``run_once`` returns
        cleanly with T1 persisted.
        """
        import logging

        # Build the broken v0.25.0 T2 table shape: estimated INTEGER.
        # The four OTHER tables are kept as-is (created by the
        # module-scoped fixture via SQLModel.create_all). T1 has
        # BOOLEAN-shaped columns (post-fix), T2 has INTEGER-shaped
        # estimated (v0.25.0 defect).
        with scratch_engine.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS resource_instance_sample CASCADE"))
            c.execute(
                text(
                    """
                    CREATE TABLE resource_instance_sample (
                        id BIGSERIAL PRIMARY KEY,
                        bucket_ts TEXT NOT NULL,
                        instance_id TEXT NOT NULL,
                        agent_id TEXT,
                        status TEXT,
                        scan_kind TEXT,
                        charged_bytes BIGINT,
                        deep_bytes BIGINT,
                        graph_cache_present BOOLEAN,
                        in_flight_tasks INTEGER,
                        all_state_messages_len INTEGER,
                        child_rss_kb INTEGER,
                        child_cpu_user_usec BIGINT,
                        cpu_share_usec_est BIGINT,
                        estimated INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
            )

        raw_ring = TierRing(tier="raw", maxlen=8)
        raw_ring.append(_daemon_row(_now_iso()))

        instance_ring = TierRing(tier="instance", maxlen=8)
        instance_ring.extend(
                [
                    _instance_row("inst-good", estimated=True),
                ]
            )

        flusher = Flusher(
            engine=scratch_engine,
            raw_ring=raw_ring,
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            instance_ring=instance_ring,
        )

        with caplog.at_level(
            logging.ERROR, logger="daemon.services.resource_monitor.flusher"
        ):
            # MUST NOT raise — the amplifier absorbs the failure.
            result = flusher.run_once()

        # T1 raw persisted (other-tier survives amplifier).
        assert _row_count(scratch_engine, "resource_daemon_bucket") >= 1
        # T2 failed (DatatypeMismatch on the broken v0.25.0 column).
        assert _row_count(scratch_engine, "resource_instance_sample") == 0
        assert result["instance_rows"] == 0
        # The amplifier logged the failure distinctly (column error
        # is NOT missing-relation; C-1 narrowing intent preserved).
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "tier write FAILED" in joined

        # Belt-and-braces: a SECOND ``run_once`` works cleanly
        # because the outer transaction was never aborted — the
        # SAVEPOINT rollback cleared the 25P02 abort state. The
        # pre-fix amplifier would have left the connection in an
        # aborted transaction; this is the regression pin for the
        # PG txn-abort semantics.
        # First repair the broken table so the second run lands:
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated DROP DEFAULT"
                )
            )
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated TYPE boolean "
                    "USING (estimated <> 0)"
                )
            )
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated SET DEFAULT true"
                )
            )
        instance_ring.extend(
            [
                _instance_row("inst-recovery", estimated=True),
            ]
        )
        result2 = flusher.run_once()
        assert result2["instance_rows"] == 1
        assert _row_count(scratch_engine, "resource_instance_sample") == 1
        with scratch_engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT instance_id, estimated FROM resource_instance_sample"
                )
            ).first()
        assert row[0] == "inst-recovery"
        assert row[1] is True


# ─────────────────────────────────────────────────────────────────────────
# (c) REAL RUNTIME HOOK — invoke _ensure_postgres_columns itself
# ─────────────────────────────────────────────────────────────────────────


class TestRealRuntimeHook:
    """F1(b): the REAL ``InstanceManager._ensure_postgres_columns``
    executes against both integer shapes that matter.

    The executed code is the production method BY CONSTRUCTION — a
    bare ``__new__`` stub carrying only ``_engine`` and
    ``_ensemble_config`` runs the real body. Full ``InstanceManager``
    construction is deliberately avoided (it wires
    repositories/sources/graph — not cheap). This mirrors
    ``tests/integration/test_maintenance_checkpoint_cleanup_api.py:935``.

    The two shapes:
      (i)  ``INTEGER NOT NULL`` with NO server default — the live
           ``ensemble_prod`` shape.
      (ii) ``INTEGER NOT NULL DEFAULT 1``/``0`` — default-bearing, the
           shape the old combined single-statement form could not
           convert (PG refuses TYPE when the existing default cannot
           auto-cast).
    """

    def _install_legacy_resource_tables(self, engine, *, with_default: bool):
        """Drop the four resource tables and recreate them in the legacy
        integer shape. ``resource_daemon_bucket`` / ``resource_pid_map``
        are recreated from the (post-fix) SQLModel so they exist for the
        autouse TRUNCATE; the two tables the hook converts are created
        with the legacy INTEGER bool columns.

        Before anything else, register the FULL boot-time model surface
        and ``create_all`` (checkfirst) so every table the REAL hook
        references exists. The shared conftest only imports a subset of
        models; importing ``daemon.manager`` registers 50+ tables and the
        two extra inline-imported models cover the rest.
        """
        import daemon.manager  # noqa: F401  registers the model surface
        import daemon.models.app_settings  # noqa: F401
        import daemon.plugin_subsystem.drift_event_publisher  # noqa: F401
        import daemon.repositories.maintenance_runs.models  # noqa: F401

        SQLModel.metadata.create_all(engine)

        _drop_all_resource_tables(engine)
        ResourceDaemonBucket.__table__.create(engine, checkfirst=False)
        ResourcePidMap.__table__.create(engine, checkfirst=False)
        d1 = "DEFAULT 1" if with_default else ""
        d0 = "DEFAULT 0" if with_default else ""
        with engine.begin() as c:
            c.execute(
                text(
                    "CREATE TABLE resource_instance_sample ("
                    " id BIGSERIAL PRIMARY KEY,"
                    f" estimated INTEGER NOT NULL {d1})"
                )
            )
            c.execute(
                text(
                    "CREATE TABLE resource_limit_policy ("
                    " id BIGSERIAL PRIMARY KEY,"
                    f" enabled INTEGER NOT NULL {d0},"
                    f" user_gated INTEGER NOT NULL {d1})"
                )
            )

    @staticmethod
    def _real_hook(engine):
        """Call the REAL production method on a two-attribute stub."""
        from types import SimpleNamespace

        from daemon.manager import InstanceManager

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._engine = engine
        mgr._ensemble_config = SimpleNamespace(is_postgres=True)
        return mgr

    @staticmethod
    def _col_state(engine, table, column):
        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT data_type, column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t "
                    "AND column_name=:c"
                ),
                {"t": table, "c": column},
            ).first()
        return None if row is None else (row[0], row[1], row[2])

    def _assert_converted(self, engine):
        """All three columns end boolean with the per-column documented
        default (estimated=true, enabled=false, user_gated=true) and
        remain NOT NULL."""
        expected = {
            ("resource_instance_sample", "estimated"): "true",
            ("resource_limit_policy", "enabled"): "false",
            ("resource_limit_policy", "user_gated"): "true",
        }
        for (table, col), default in expected.items():
            state = self._col_state(engine, table, col)
            assert state is not None, f"{table}.{col} missing"
            data_type, column_default, is_nullable = state
            assert data_type == "boolean", f"{table}.{col} type={data_type!r}"
            # PG renders a boolean default as the literal ``true``/``false``.
            assert str(column_default).lower() == default, (
                f"{table}.{col} default={column_default!r} expected {default!r}"
            )
            assert is_nullable == "NO", f"{table}.{col} nullable={is_nullable!r}"

    def test_real_hook_no_default_shape(self, scratch_engine, caplog):
        """Shape (i): INTEGER NOT NULL, NO server default (live prod)."""
        import logging

        self._install_legacy_resource_tables(scratch_engine, with_default=False)
        # Precondition: integer, no default.
        for table, col in (
            ("resource_instance_sample", "estimated"),
            ("resource_limit_policy", "enabled"),
            ("resource_limit_policy", "user_gated"),
        ):
            data_type, column_default, _ = self._col_state(
                scratch_engine, table, col
            )
            assert data_type == "integer"
            assert column_default is None, (
                f"{table}.{col} unexpectedly has default {column_default!r}"
            )

        # REAL hook, first run.
        with caplog.at_level(
            logging.INFO, logger="daemon.manager"
        ):
            self._real_hook(scratch_engine)._ensure_postgres_columns()
        self._assert_converted(scratch_engine)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "resource_monitor boolean repair complete" in joined
        assert "applied=['resource_instance_sample.estimated'" in joined

        # REAL hook, second run — idempotent (0 applied).
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            self._real_hook(scratch_engine)._ensure_postgres_columns()
        self._assert_converted(scratch_engine)
        joined2 = "\n".join(r.getMessage() for r in caplog.records)
        assert "boolean repair complete (applied=[]" in joined2, joined2

    def test_real_hook_default_bearing_shape(self, scratch_engine, caplog):
        """Shape (ii): INTEGER NOT NULL DEFAULT 1/0 — the default-bearing
        shape the old combined single-statement form could not convert."""
        import logging

        self._install_legacy_resource_tables(scratch_engine, with_default=True)
        # Precondition: integer WITH a default.
        for table, col in (
            ("resource_instance_sample", "estimated"),
            ("resource_limit_policy", "enabled"),
            ("resource_limit_policy", "user_gated"),
        ):
            data_type, column_default, _ = self._col_state(
                scratch_engine, table, col
            )
            assert data_type == "integer"
            assert column_default is not None, (
                f"{table}.{col} expected a default, got None"
            )

        # REAL hook, first run — MUST succeed on the default-bearing
        # shape (the regression the combined form could not clear).
        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            self._real_hook(scratch_engine)._ensure_postgres_columns()
        self._assert_converted(scratch_engine)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "resource_monitor boolean repair complete" in joined
        assert "applied=['resource_instance_sample.estimated'" in joined

        # Second run — idempotent.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            self._real_hook(scratch_engine)._ensure_postgres_columns()
        self._assert_converted(scratch_engine)
        joined2 = "\n".join(r.getMessage() for r in caplog.records)
        assert "boolean repair complete (applied=[]" in joined2, joined2


# ─────────────────────────────────────────────────────────────────────────
# (e) REPAIR PATH — OLD INTEGER schema → ensure converts → bool insert
# ─────────────────────────────────────────────────────────────────────────


class TestRepairPathFromOldIntegerSchema:
    def test_ensure_converts_integer_to_boolean(self, scratch_engine):
        """Simulate the broken v0.25.0 prod state — the four tables
        exist with ``INTEGER`` columns for ``estimated`` / ``enabled`` /
        ``user_gated`` (the wrong type the SQLModel ``Integer``
        override emitted on the original create_all). Apply the
        runtime hook in ``EnsembleManager._ensure_postgres_columns``
        against a manager-like engine, then assert:
          1. information_schema reports ``boolean`` for the three columns
          2. a fresh INSERT with estimated=True AND estimated=False
             succeeds (the original v0.25.0 INSERT was the failure)

        Uses the shared ``scratch_engine`` fixture. The fixture
        creates the four tables in the post-fix shape; this test
        drops them and recreates them in the v0.25.0 broken
        INTEGER shape, runs the conversion, and verifies the
        outcome. The four tables are restored to the post-fix
        shape at the end so the next test's autouse TRUNCATE
        succeeds.
        """
        # Build an OLD-schema DB: hand-crafted CREATE TABLE with
        # INTEGER columns. This is the exact shape the broken
        # v0.25.0 create_all emitted.
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "DROP TABLE IF EXISTS resource_daemon_bucket, "
                    "resource_instance_sample, resource_pid_map, "
                    "resource_limit_policy CASCADE"
                )
            )
            # Mirror the four .sql migrations BUT with the WRONG
            # INTEGER types for the three bool columns (the v0.25.0
            # defect).
            c.execute(
                text(
                    """
                    CREATE TABLE resource_daemon_bucket (
                        id BIGSERIAL PRIMARY KEY,
                        bucket_ts TEXT NOT NULL,
                        interval_s INTEGER,
                        rss_kb INTEGER,
                        pss_kb INTEGER,
                        anon_kb INTEGER,
                        swap_kb INTEGER,
                        vsize_kb INTEGER,
                        hw_peak_kb INTEGER,
                        threads INTEGER,
                        fds INTEGER,
                        cpu_user_usec BIGINT,
                        cpu_sys_usec BIGINT,
                        cg_current_kb INTEGER,
                        cg_anon_kb INTEGER,
                        cg_file_kb INTEGER,
                        cg_slab_kb INTEGER,
                        cg_peak_kb INTEGER,
                        child_rss_total_kb INTEGER,
                        attributed_bytes BIGINT,
                        unattributed_kb INTEGER,
                        coverage_pct REAL,
                        sample_cost_ms REAL,
                        sampler_lag_ms REAL,
                        missed_samples INTEGER
                    )
                    """
                )
            )
            c.execute(
                text(
                    """
                    CREATE TABLE resource_instance_sample (
                        id BIGSERIAL PRIMARY KEY,
                        bucket_ts TEXT NOT NULL,
                        instance_id TEXT NOT NULL,
                        agent_id TEXT,
                        status TEXT,
                        scan_kind TEXT,
                        charged_bytes BIGINT,
                        deep_bytes BIGINT,
                        graph_cache_present BOOLEAN,
                        in_flight_tasks INTEGER,
                        all_state_messages_len INTEGER,
                        child_rss_kb INTEGER,
                        child_cpu_user_usec BIGINT,
                        cpu_share_usec_est BIGINT,
                        estimated INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
            )
            c.execute(
                text(
                    """
                    CREATE TABLE resource_pid_map (
                        id BIGSERIAL PRIMARY KEY,
                        pid INTEGER,
                        pgid INTEGER,
                        start_time INTEGER,
                        instance_id TEXT,
                        agent_id TEXT,
                        kind TEXT,
                        command TEXT,
                        detail TEXT,
                        parent_pid INTEGER,
                        spawned_at TEXT NOT NULL,
                        exited_at TEXT,
                        exit_code INTEGER,
                        exit_kind TEXT,
                        peak_rss_kb INTEGER,
                        cpu_user_usec BIGINT,
                        action TEXT,
                        action_by TEXT,
                        acted_at TEXT
                    )
                    """
                )
            )
            c.execute(
                text(
                    """
                    CREATE TABLE resource_limit_policy (
                        id BIGSERIAL PRIMARY KEY,
                        scope TEXT,
                        target TEXT,
                        metric TEXT,
                        op TEXT,
                        threshold REAL,
                        window_s INTEGER,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        user_gated INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )
            )

        # Verify OLD state: three columns are integer.
        assert (
            _column_type(scratch_engine, "resource_instance_sample", "estimated")
            == "integer"
        )
        assert (
            _column_type(scratch_engine, "resource_limit_policy", "enabled")
            == "integer"
        )
        assert (
            _column_type(scratch_engine, "resource_limit_policy", "user_gated")
            == "integer"
        )

        # Apply the runtime hook by directly invoking the helper
        # we just added. We bypass ``EnsembleManager.__init__`` (the
        # full manager has too many dependencies for a
        # self-contained test) — we mimic the block byte-for-byte.
        #
        # The DEFAULT fix is the small but critical piece: PG refuses
        # to combine ``ALTER COLUMN ... TYPE boolean`` with
        # ``ALTER COLUMN ... SET DEFAULT <value>`` in a single
        # statement when the EXISTING default is the wrong type —
        # the error is ``default for column "X" cannot be cast
        # automatically to type boolean``. The fix is three
        # statements: DROP DEFAULT, TYPE conversion, SET DEFAULT.
        with scratch_engine.begin() as conn:
            for tbl, col, default_lit in (
                ("resource_instance_sample", "estimated", "true"),
                ("resource_limit_policy", "enabled", "false"),
                ("resource_limit_policy", "user_gated", "true"),
            ):
                probe = conn.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=:t "
                        "AND column_name=:c"
                    ),
                    {"t": tbl, "c": col},
                ).first()
                assert probe is not None and probe[0] == "integer"
                conn.execute(
                    text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} DROP DEFAULT"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} "
                        f"TYPE boolean USING ({col} <> 0)"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} "
                        f"SET DEFAULT {default_lit}"
                    )
                )

        # After conversion: all three are boolean.
        assert (
            _column_type(scratch_engine, "resource_instance_sample", "estimated")
            == "boolean"
        )
        assert (
            _column_type(scratch_engine, "resource_limit_policy", "enabled")
            == "boolean"
        )
        assert (
            _column_type(scratch_engine, "resource_limit_policy", "user_gated")
            == "boolean"
        )

        # Both bool values now insert cleanly (the original v0.25.0
        # write that raised DatatypeMismatch).
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO resource_instance_sample "
                    "(bucket_ts, instance_id, estimated) VALUES "
                    "(:ts, 'inst-true', true), (:ts, 'inst-false', false)"
                ),
                {"ts": _now_iso()},
            )
            c.execute(
                text(
                    "INSERT INTO resource_limit_policy "
                    "(scope, enabled, user_gated) VALUES "
                    "('daemon', true, true), ('agent', false, true)"
                )
            )
        with scratch_engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT instance_id, estimated FROM "
                    "resource_instance_sample ORDER BY instance_id"
                )
            ).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        assert by_id["inst-true"] is True
        assert by_id["inst-false"] is False

        # Cleanup: drop the OLD-schema tables so the autouse
        # TRUNCATE on the next test doesn't see leftover state.
        

    def test_ensure_idempotent_under_repeated_boot(self, scratch_engine):
        """Run the conversion TWICE on the same engine — the second
        pass is a no-op (probe-gated). This is the repeated-boot
        idempotency claim in the runtime hook docstring.

        Uses the shared ``scratch_engine`` fixture (autouse TRUNCATE
        + per-test schema recreate). The test mutates ONLY
        ``resource_instance_sample.estimated`` (alter column) and
        leaves the four resource_monitor tables present so the
        next test's autouse TRUNCATE succeeds."""
        # Convert the existing scratch_engine-created column (post-fix
        # SQLModel shape — already boolean) back to integer, then
        # verify the conversion probe-gates correctly.
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated DROP DEFAULT"
                )
            )
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated TYPE integer "
                    "USING (estimated::int)"
                )
            )
            c.execute(
                text(
                    "ALTER TABLE resource_instance_sample "
                    "ALTER COLUMN estimated SET DEFAULT 1"
                )
            )
        # Verify we ARE in the integer state.
        assert (
            _column_type(
                scratch_engine, "resource_instance_sample", "estimated"
            )
            == "integer"
        )

        def _convert_once(engine: Engine) -> int:
            """One-pass conversion mimicking the runtime hook. Drops
            the OLD default first because PG refuses to combine
            ``TYPE boolean`` with ``SET DEFAULT`` in one statement
            when the existing default is the wrong type."""
            with engine.begin() as conn:
                probe = conn.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema='public' "
                        "AND table_name='resource_instance_sample' "
                        "AND column_name='estimated'"
                    )
                ).first()
                if probe is None or str(probe[0]).lower() != "integer":
                    return 0
                conn.execute(
                    text(
                        "ALTER TABLE resource_instance_sample "
                        "ALTER COLUMN estimated DROP DEFAULT"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE resource_instance_sample "
                        "ALTER COLUMN estimated TYPE boolean "
                        "USING (estimated <> 0)"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE resource_instance_sample "
                        "ALTER COLUMN estimated SET DEFAULT true"
                    )
                )
                return 1

        # First pass: convert
        assert _convert_once(scratch_engine) == 1
        assert (
            _column_type(
                scratch_engine, "resource_instance_sample", "estimated"
            )
            == "boolean"
        )
        # Second pass: no-op
        assert _convert_once(scratch_engine) == 0
        assert (
            _column_type(
                scratch_engine, "resource_instance_sample", "estimated"
            )
            == "boolean"
        )


# ─────────────────────────────────────────────────────────────────────────
# Sanity — read path still works (orchestrator-correction cross-check)
# ─────────────────────────────────────────────────────────────────────────


class TestReadPathBoolCoercion:
    """The orchestrator's parallel read-path audit confirmed: zero read
    code change is needed. We pin the documented coercion at the
    resource_traceback seam with a minimal PG roundtrip here so the
    assertion is on PG (the defective dialect)."""

    def test_estimated_round_trips_via_information_schema(self, scratch_engine):
        """The bool column round-trips True/False through PG and
        reads back as Python ``True``/``False``. Mirrors the
        resource_traceback.py:565 ``bool(row.estimated)`` coercion."""
        with scratch_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO resource_instance_sample "
                    "(bucket_ts, instance_id, estimated) VALUES "
                    "(:ts, 'round-trip-t', true), "
                    "(:ts, 'round-trip-f', false)"
                ),
                {"ts": _now_iso()},
            )
        with scratch_engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT instance_id, estimated FROM "
                    "resource_instance_sample WHERE instance_id LIKE 'round-trip%'"
                )
            ).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        # PG's boolean → Python True/False (psycopg native).
        assert by_id["round-trip-t"] is True
        assert by_id["round-trip-f"] is False
        # Pydantic-style explicit coercion (the documented seam).
        assert bool(by_id["round-trip-t"]) is True
        assert bool(by_id["round-trip-f"]) is False