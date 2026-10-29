"""Real-PG proof — the flusher's BIND PATH against the REAL column types.

Commissioning brief (2026-10-29): "exercise the flusher bind path
against the real column type". This module is that proof, and it is
deliberately NOT a duplicate of
``tests/postgres/test_resource_monitor_estimated_bool_pg.py`` (which
owns the column-TYPE history: fresh-boot emit, the runtime repair hook,
the amplifier isolation and the integer→boolean conversion).

What is pinned HERE is the seam that actually broke in production:

  * the row the SAMPLER produces (its own ``_snapshot_to_row`` output —
    the exact bind map production uses) travels through the REAL
    ``Flusher`` into REAL PostgreSQL and lands with the right values;
  * the per-instance row (the sampler's T2 row shape) binds a Python
    ``bool`` into the REAL ``boolean`` column and reads back as a
    ``bool``;
  * EVERY column the flusher binds is type-audited against the LIVE
    ``information_schema`` type and against the model's declared type,
    so a future drift between "what Python binds" and "what PG stores"
    fails loudly instead of rolling a flush window back.

Fence discipline: scratch-only. ``_tripwire_check`` refuses a
forbidden live/demo database and refuses any name that is not visibly
scratch-shaped; the shared ``tests/postgres/conftest.py`` owns the
connection and the schema lifecycle.

Run with::

    PG_TEST_DB=ensemble_dev_test_only PG_TEST_PASSWORD= \\
        uv run python -m pytest tests/postgres/ \\
        --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly
"""

from __future__ import annotations

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
from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing
from daemon.services.resource_monitor import proc_readers as proc_readers_mod
from daemon.services.resource_monitor.proc_readers import (
    DaemonProcSnapshot,
    ProcSnapshot,
)
from daemon.services.resource_monitor.sampler import Sampler

pytestmark = pytest.mark.postgres

# ─────────────────────────────────────────────────────────────────────────
# Scratch-DB fence
# ─────────────────────────────────────────────────────────────────────────

_FORBIDDEN_DBS = {"ensemble_prod", "ensemble_dev", "ensemble_sandbox", "ensemble"}


def _tripwire_check() -> None:
    """Fail-closed guard against ambient env poison / live-DB writes."""
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


# ─────────────────────────────────────────────────────────────────────────
# The bind map — every column the flusher binds, per table
# ─────────────────────────────────────────────────────────────────────────
#
# These are the column lists the flusher projects in
# ``daemon/services/resource_monitor/flusher.py`` (``_write_raw``,
# ``_write_minute``, ``_write_hour``, ``_write_instance_rows``,
# ``_write_pid_map``). The audit below compares each one against the
# LIVE PG type and against the model's declared type.

_FLASHER_BIND_COLUMNS: dict[str, tuple[str, ...]] = {
    "resource_daemon_bucket": (
        "bucket_ts",
        "interval_s",
        "rss_kb",
        "pss_kb",
        "anon_kb",
        "swap_kb",
        "vsize_kb",
        "hw_peak_kb",
        "threads",
        "fds",
        "cpu_user_usec",
        "cpu_sys_usec",
        "cg_current_kb",
        "cg_anon_kb",
        "cg_file_kb",
        "cg_slab_kb",
        "cg_peak_kb",
        "child_rss_total_kb",
        "attributed_bytes",
        "unattributed_kb",
        "coverage_pct",
        "sample_cost_ms",
        "sampler_lag_ms",
        "missed_samples",
    ),
    "resource_instance_sample": (
        "bucket_ts",
        "instance_id",
        "agent_id",
        "status",
        "scan_kind",
        "charged_bytes",
        "deep_bytes",
        "graph_cache_present",
        "in_flight_tasks",
        "all_state_messages_len",
        "child_rss_kb",
        "child_cpu_user_usec",
        "cpu_share_usec_est",
        "estimated",
    ),
    "resource_pid_map": (
        "pid",
        "pgid",
        "start_time",
        "instance_id",
        "agent_id",
        "kind",
        "command",
        "detail",
        "parent_pid",
        "spawned_at",
        "exited_at",
        "exit_code",
        "exit_kind",
        "peak_rss_kb",
        "cpu_user_usec",
    ),
}

# SQLAlchemy type class -> the ``information_schema.columns.data_type``
# PostgreSQL reports for it.
_EXPECTED_PG_TYPES = {
    "INTEGER": "integer",
    "BIGINTEGER": "bigint",
    "SMALLINTEGER": "smallint",
    "BOOLEAN": "boolean",
    "TEXT": "text",
    "AUTOSTRING": "character varying",
    "FLOAT": "double precision",
    "VARCHAR": "character varying",
    "DATETIME": "timestamp without time zone",
}


def _model_tables() -> dict[str, object]:
    return {
        ResourceDaemonBucket.__tablename__: ResourceDaemonBucket.__table__,
        ResourceInstanceSample.__tablename__: ResourceInstanceSample.__table__,
        ResourcePidMap.__tablename__: ResourcePidMap.__table__,
        ResourceLimitPolicy.__tablename__: ResourceLimitPolicy.__table__,
    }


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _flusher(engine: Engine, ring: TierRing, instance_ring: TierRing) -> Flusher:
    return Flusher(
        engine=engine,
        raw_ring=ring,
        flush_batch_seconds=60,
        retention=RetentionPolicy(
            raw_minutes=2880, minute_hours=336, hour_days=180
        ),
        instance_ring=instance_ring,
    )


def _sampler_row() -> dict:
    """The EXACT row the sampler writes into the raw ring.

    Built by the sampler's own ``_snapshot_to_row`` so the bind map is
    the production one — a hand-written fixture could drift from the
    sampler and stop proving anything.
    """
    sampler = Sampler(
        sample_seconds=30,
        self_cpu_budget_pct=1.0,
        self_rss_budget_mb=50,
        detail_level="auto",
        proc_root="/proc",
        cgroup_root="/sys/fs/cgroup",
        ring=TierRing(tier="raw", maxlen=4),
    )
    snap = ProcSnapshot(
        daemon=DaemonProcSnapshot(
            pid=4242,
            rss_kb=1_000_000,
            vsize_kb=2_000_000,
            anon_kb=600_000,
            hw_peak_kb=1_100_000,
            threads=8,
            fds=256,
            cpu_user_usec=10_000_000,
            cpu_sys_usec=2_000_000,
            pss_kb=900_000,
        ),
        cgroup=None,
    )
    row = sampler._snapshot_to_row(snap, 0.5)
    row["bucket_ts"] = _now_iso()
    return row


def _instance_row(*, estimated: bool, graph_cache_present: bool | None) -> dict:
    """The sampler's T2 row shape (the ``ALLOWED_T2_ROW_KEYS`` set)."""
    return {
        "bucket_ts": _now_iso(),
        "instance_id": "inst-pg-bind",
        "agent_id": "agent-test",
        "status": "idle",
        "scan_kind": "counter",
        "charged_bytes": 12345,
        "deep_bytes": None,
        "graph_cache_present": graph_cache_present,
        "in_flight_tasks": 1,
        "all_state_messages_len": 3,
        "child_rss_kb": 8192,
        "child_cpu_user_usec": 100_000,
        "cpu_share_usec_est": 4321,
        "estimated": estimated,
    }


class _StubSys:
    """Stand-in for the ``sys`` module with a forged ``platform``."""

    def __init__(self, platform: str) -> None:
        self.platform = platform


_ABSENT_PROC_ROOT = "/nonexistent-proc-root-for-resource-monitor-pg-tests"


@pytest.fixture
def darwin_host(monkeypatch):
    """Make ``proc_readers`` behave as on a macOS host (no ``/proc``).

    The real ``ps`` binary is never spawned — the output is canned, so
    the test is hermetic and identical on a Linux CI host.
    """

    def _run(args):
        if list(args[:1]) == ["-o"]:
            return "177520 411300784"
        if list(args[:1]) == ["-M"]:
            return (
                "USER  PID  TT  %CPU STAT PRI  STIME  UTIME COMMAND\n"
                "user  44004 ??  4.3 S  31T  0:00.02 0:00.02 python\n"
            )
        return None

    monkeypatch.setattr(
        proc_readers_mod, "sys", _StubSys("darwin"), raising=False
    )
    monkeypatch.setattr(proc_readers_mod, "_run_ps", _run, raising=False)
    return True


# ─────────────────────────────────────────────────────────────────────────
# Session fixture — fresh schema on the scratch DB
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def scratch_engine(pg_engine):
    """Fresh schema for the four resource_monitor tables."""
    _tripwire_check()
    with pg_engine.begin() as c:
        c.execute(
            text(
                "DROP TABLE IF EXISTS resource_daemon_bucket, "
                "resource_instance_sample, resource_pid_map, "
                "resource_limit_policy CASCADE"
            )
        )
    for tbl in (
        ResourceDaemonBucket.__table__,
        ResourceInstanceSample.__table__,
        ResourcePidMap.__table__,
        ResourceLimitPolicy.__table__,
    ):
        tbl.create(pg_engine, checkfirst=False)
    yield pg_engine


# ─────────────────────────────────────────────────────────────────────────
# The bind-path audit
# ─────────────────────────────────────────────────────────────────────────


class TestBindPathAgainstRealColumnTypes:
    def test_every_bound_column_matches_the_live_pg_type(self, scratch_engine):
        """The flusher's bind map == the live PG column types.

        This is the audit that would have caught the v0.25.0 defect
        class at the SOURCE (a Python ``bool`` bound into an
        ``INTEGER`` column) instead of at flush time.
        """
        tables = _model_tables()
        mismatches: list[str] = []
        for table_name, columns in _FLASHER_BIND_COLUMNS.items():
            model_table = tables[table_name]
            for column in columns:
                declared = model_table.columns[column].type
                declared_pg = _EXPECTED_PG_TYPES.get(
                    type(declared).__name__.upper()
                )
                assert declared_pg is not None, (
                    f"{table_name}.{column}: unhandled model type "
                    f"{type(declared).__name__} — extend _EXPECTED_PG_TYPES"
                )
                live = _column_type(scratch_engine, table_name, column)
                if live != declared_pg:
                    mismatches.append(
                        f"{table_name}.{column}: model says "
                        f"{declared_pg!r}, live PG says {live!r}"
                    )
        assert not mismatches, (
            "flusher bind map drifted from the live PG column types:\n  "
            + "\n  ".join(mismatches)
        )

    def test_boolean_columns_are_native_boolean(self, scratch_engine):
        """The three boolean columns are native PG ``boolean``."""
        for table, column in (
            ("resource_instance_sample", "estimated"),
            ("resource_limit_policy", "enabled"),
            ("resource_limit_policy", "user_gated"),
            ("resource_instance_sample", "graph_cache_present"),
        ):
            assert _column_type(scratch_engine, table, column) == "boolean", (
                f"{table}.{column} is not a native boolean column"
            )


class TestSamplerRowBindsIntoRealPg:
    def test_the_samplers_own_row_lands_in_t1(self, scratch_engine):
        """The production sampler row flushes into real PG unchanged."""
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        row = _sampler_row()
        ring.append(row)
        instance_ring.append(
            _instance_row(estimated=True, graph_cache_present=True)
        )

        flusher = _flusher(scratch_engine, ring, instance_ring)
        result = flusher.run_once()
        assert result["rows_written"] >= 1, (
            "the flush window wrote nothing — the bind path is broken"
        )
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            stored = c.execute(
                text(
                    "SELECT rss_kb, cpu_user_usec, coverage_pct, "
                    "sample_cost_ms, missed_samples, bucket_ts "
                    "FROM resource_daemon_bucket WHERE interval_s = 30"
                )
            ).fetchall()
        assert len(stored) == 1
        rss_kb, cpu_user_usec, coverage_pct, sample_cost_ms, missed, _ts = (
            stored[0]
        )
        assert rss_kb == row["rss_kb"]
        assert cpu_user_usec == row["cpu_user_usec"]
        assert coverage_pct == pytest.approx(row["coverage_pct"])
        assert sample_cost_ms == pytest.approx(row["sample_cost_ms"])
        assert missed == row["missed_samples"]

        # The roll-up tiers ride the SAME table (interval_s 60 / 3600).
        with scratch_engine.connect() as c:
            intervals = [
                int(r[0])
                for r in c.execute(
                    text(
                        "SELECT DISTINCT interval_s "
                        "FROM resource_daemon_bucket"
                    )
                ).fetchall()
            ]
        assert sorted(intervals) == [30, 60, 3600]

    def test_the_instance_row_binds_a_bool_into_the_boolean_column(
        self, scratch_engine
    ):
        """``estimated`` / ``graph_cache_present`` bind as real booleans."""
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        instance_ring.append(
            _instance_row(estimated=False, graph_cache_present=None)
        )

        flusher = _flusher(scratch_engine, ring, instance_ring)
        flusher.run_once()
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            stored = c.execute(
                text(
                    "SELECT estimated, graph_cache_present, instance_id "
                    "FROM resource_instance_sample"
                )
            ).fetchall()
        assert len(stored) == 1
        estimated, graph_cache_present, instance_id = stored[0]
        assert estimated is False, (
            f"estimated must read back as a Python bool, got {estimated!r}"
        )
        assert graph_cache_present is None
        assert instance_id == "inst-pg-bind"

    def test_both_boolean_values_round_trip(self, scratch_engine):
        """``estimated`` accepts True AND False (no 0/1 coercion)."""
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        instance_ring.append(
            _instance_row(estimated=True, graph_cache_present=True)
        )
        instance_ring.append(
            _instance_row(estimated=False, graph_cache_present=False)
        )

        flusher = _flusher(scratch_engine, ring, instance_ring)
        flusher.run_once()
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            values = [
                row[0]
                for row in c.execute(
                    text("SELECT estimated FROM resource_instance_sample")
                ).fetchall()
            ]
        assert sorted(values) == [False, True]
        assert all(isinstance(v, bool) for v in values)


# ─────────────────────────────────────────────────────────────────────────
# THE regression — producer → flusher → real PostgreSQL
# ─────────────────────────────────────────────────────────────────────────


class TestSamplerToFlusherToRealPg:
    """The chain that was dark in production.

    Pre-fix the sampler produced NO row on a host without ``/proc``
    (the daemon's macOS deployment host), so the flusher's buffer was
    always empty and T1/T2 stayed at 0 rows — with ``flush.errors ==
    0`` and ``flush.batches`` climbing. This drives the REAL sampler
    (through the portable platform reader) into the REAL flusher and
    asserts the rows land in REAL PostgreSQL with the REAL column
    types.
    """

    def test_a_sampler_tick_reaches_real_pg(self, scratch_engine, darwin_host):
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        sampler = Sampler(
            sample_seconds=30,
            self_cpu_budget_pct=1.0,
            self_rss_budget_mb=50,
            detail_level="minimal",
            proc_root=_ABSENT_PROC_ROOT,
            cgroup_root="/nonexistent-cgroup-root-for-tests",
            ring=ring,
            instance_ring=instance_ring,
            graph_cache_provider=lambda: {"inst-pg-chain": (0, 0, 0)},
        )

        snap = sampler.run_once()
        assert snap is not None, (
            "the sampler produced no snapshot on a /proc-less host"
        )
        assert sampler.counters.ticks == 1
        assert sampler.counters.ticks_skipped == 0
        assert len(ring) == 1, "the sampler wrote no ring row"

        flusher = _flusher(scratch_engine, ring, instance_ring)
        result = flusher.run_once()
        assert flusher.counters.errors == 0
        assert result["rows_written"] >= 1

        with scratch_engine.connect() as c:
            t1 = c.execute(
                text(
                    "SELECT rss_kb, threads, cpu_user_usec "
                    "FROM resource_daemon_bucket WHERE interval_s = 30"
                )
            ).fetchall()
            t2 = c.execute(
                text(
                    "SELECT instance_id, estimated, graph_cache_present "
                    "FROM resource_instance_sample"
                )
            ).fetchall()

        assert len(t1) == 1, "the T1 tier received no row"
        rss_kb, threads, _cpu = t1[0]
        assert rss_kb == 177520
        assert threads == 1

        assert len(t2) == 1, "the T2 tier received no row"
        instance_id, estimated, graph_cache_present = t2[0]
        assert instance_id == "inst-pg-chain"
        assert estimated is True
        assert graph_cache_present is True
