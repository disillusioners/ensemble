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
    ``information_schema`` type AND against a HARD-CODED expected type,
    so a future drift between "what Python binds" and "what PG stores"
    fails loudly instead of rolling a flush window back;
  * the DEGRADED SENTINEL row (the B1 evidence path for a dropped
    sample) lands in T1 with its marker and its NULL metrics.

B7a (mutation-blind audit): the bound-column list is DERIVED FROM THE
FLUSHER SOURCE (an AST parse of ``_dialect_insert`` call sites), not
hand-copied, and the expected PG type per column is HARD-CODED as a
literal. The previous shape derived BOTH the column list and the
expected type from the same model the schema was created from — a
model mutation (e.g. flipping ``estimated`` from Boolean to Integer)
therefore left the audit PASSING while five sibling columns failed.

B7b (unread columns): every bound T2 column is SELECTed back and
value-asserted (10 of the 14 were never read back), the written
``graph_cache_present=False`` is asserted, ``bucket_ts`` is
value-asserted, and the ``estimated`` DEFAULT-TRUE path (the flusher's
``r.get("estimated", True)``) is exercised by a row that OMITS the
key entirely.

Fence discipline (HARD ALLOWLIST, and it runs BEFORE any DDL):

  * The allowlist is the repo's own hard set — ``{"ensemble_dev_test_only",
    "ensemble_test_only"}`` — with NO substring/name heuristic (a name
    like ``ensemble_prod_test`` is NOT scratch and is refused).
  * The fence runs BEFORE the target database is touched. This module
    OVERRIDES the shared ``tests/postgres/conftest.py`` ``pg_engine``
    fixture: the shared one performs ``SQLModel.metadata.drop_all`` on
    the TARGET database at session setup, so a fence that ran inside a
    test body would refuse AFTER the tables of a live database were
    already gone. The override puts the allowlist check on the only
    code path that reaches the database, and the explicit
    ``_scratch_db_fence`` dependency makes the ORDERING a property of
    the fixture graph rather than of fixture-resolution luck.

Run with the REGISTERED pack script (never by hand — the script owns
the env and the marker flags)::

    bash test/packs/resource_monitor_flusher_bind_pg_test.sh
"""

from __future__ import annotations

import ast
import os
from datetime import datetime, timezone
from pathlib import Path

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
from daemon.services.resource_monitor import flusher as flusher_mod
from daemon.services.resource_monitor import proc_readers as proc_readers_mod
from daemon.services.resource_monitor.proc_readers import (
    DaemonProcSnapshot,
    ProcSnapshot,
)
from daemon.services.resource_monitor.sampler import (
    DEGRADED_SENTINEL_INTERVAL_S,
    Sampler,
)
from tests.postgres.conftest import PG_URL, _probe_postgres

pytestmark = pytest.mark.postgres

# ─────────────────────────────────────────────────────────────────────────
# Scratch-DB fence — the repo's HARD allowlist
# ─────────────────────────────────────────────────────────────────────────
#
# The same set the resource-monitor unit suites fence on
# (``tests/test_resource_monitor_sampler.py`` and siblings). NO
# substring heuristic: the previous shape accepted any name containing
# "test" or "scratch", which would have admitted ``ensemble_prod_test``.

_ALLOWED_SCRATCH_DBS = frozenset(
    {"ensemble_dev_test_only", "ensemble_test_only"}
)


def _effective_pg_test_db() -> str:
    return (os.environ.get("PG_TEST_DB") or "").strip()


def _tripwire_check() -> None:
    """Fail-closed guard — raises BEFORE any SQL reaches the database.

    This is the fence the module docstring promises. It is wired as an
    explicit DEPENDENCY of this module's ``pg_engine`` fixture, so it
    runs before the engine exists — i.e. before the session-scoped
    ``drop_all`` that the shared conftest fixture performs on the
    target database.
    """
    db = _effective_pg_test_db()
    if db not in _ALLOWED_SCRATCH_DBS:
        raise RuntimeError(
            f"refused: PG_TEST_DB={db!r} is not in the hard scratch "
            f"allowlist {sorted(_ALLOWED_SCRATCH_DBS)} — this module "
            "drops every table of the target database at session "
            "setup, so it only runs against a sanctioned scratch DB"
        )


# ─────────────────────────────────────────────────────────────────────────
# The bind map — derived from the FLUSHER SOURCE (B7a)
# ─────────────────────────────────────────────────────────────────────────
#
# The audit compares each column the flusher binds against
#   (a) the LIVE PG ``information_schema`` type, and
#   (b) a HARD-CODED expected type (the literal below), and
#   (c) the model's declared type.
# (b) is what makes the audit mutation-blind: the model can no longer
# hide a drift, because the expectation does not come from the model.

#: The expected PostgreSQL ``information_schema.columns.data_type`` per
#: bound column — HARDCODED, never derived from the model.
_EXPECTED_PG_TYPES: dict[str, dict[str, str]] = {
    "resource_daemon_bucket": {
        "bucket_ts": "text",
        "interval_s": "integer",
        "rss_kb": "integer",
        "pss_kb": "integer",
        "anon_kb": "integer",
        "swap_kb": "integer",
        "vsize_kb": "integer",
        "hw_peak_kb": "integer",
        "threads": "integer",
        "fds": "integer",
        "cpu_user_usec": "bigint",
        "cpu_sys_usec": "bigint",
        "cg_current_kb": "integer",
        "cg_anon_kb": "integer",
        "cg_file_kb": "integer",
        "cg_slab_kb": "integer",
        "cg_peak_kb": "integer",
        "child_rss_total_kb": "integer",
        "attributed_bytes": "bigint",
        "unattributed_kb": "integer",
        "coverage_pct": "double precision",
        "sample_cost_ms": "double precision",
        "sampler_lag_ms": "double precision",
        "missed_samples": "integer",
    },
    "resource_instance_sample": {
        "bucket_ts": "text",
        "instance_id": "text",
        "agent_id": "character varying",
        "status": "character varying",
        "scan_kind": "character varying",
        "charged_bytes": "bigint",
        "deep_bytes": "bigint",
        "graph_cache_present": "boolean",
        "in_flight_tasks": "integer",
        "all_state_messages_len": "integer",
        "child_rss_kb": "integer",
        "child_cpu_user_usec": "bigint",
        "cpu_share_usec_est": "bigint",
        "estimated": "boolean",
    },
    "resource_pid_map": {
        "pid": "integer",
        "pgid": "integer",
        "start_time": "integer",
        "instance_id": "character varying",
        "agent_id": "character varying",
        "kind": "character varying",
        "command": "character varying",
        "detail": "character varying",
        "parent_pid": "integer",
        "spawned_at": "text",
        "exited_at": "character varying",
        "exit_code": "integer",
        "exit_kind": "character varying",
        "peak_rss_kb": "integer",
        "cpu_user_usec": "bigint",
    },
}

#: SQLAlchemy type class -> the ``information_schema`` type PG reports.
_SA_TYPE_TO_PG = {
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


def _flusher_bound_columns() -> dict[str, set[str]]:
    """Derive the bound-column set per table FROM THE FLUSHER SOURCE.

    An AST parse of ``_dialect_insert`` call sites — the single place
    the flusher declares the columns of an INSERT. The raw tier, the
    minute tier and the hour tier all target ``resource_daemon_bucket``
    with DIFFERENT column lists, so the per-table set is the union.

    Deriving the list from the source (instead of hand-copying it)
    means a column the flusher starts binding is audited
    automatically, and a column it stops binding drops out of the
    audit — the hand-copied list silently went stale.
    """
    source = Path(flusher_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bound: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "_dialect_insert"
        ):
            continue
        if len(node.args) < 2:
            continue
        table = node.args[0]
        columns = node.args[1]
        if not (
            isinstance(table, ast.Constant)
            and isinstance(table.value, str)
        ):
            continue
        if not isinstance(columns, ast.List):
            continue
        names = {
            element.value
            for element in columns.elts
            if isinstance(element, ast.Constant)
            and isinstance(element.value, str)
        }
        bound.setdefault(table.value, set()).update(names)
    return bound


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


def _instance_row(
    *,
    estimated: bool | None = True,
    graph_cache_present: bool | None = False,
) -> dict:
    """The sampler's T2 row shape (the ``ALLOWED_T2_ROW_KEYS`` set).

    ``estimated=None`` OMITS the key entirely — the flusher's
    ``r.get("estimated", True)`` default path.
    """
    row = {
        "bucket_ts": _now_iso(),
        "instance_id": "inst-pg-bind",
        "agent_id": "agent-test",
        "status": "idle",
        "scan_kind": "counter",
        "charged_bytes": 12345,
        "deep_bytes": 6789,
        "graph_cache_present": graph_cache_present,
        "in_flight_tasks": 1,
        "all_state_messages_len": 3,
        "child_rss_kb": 8192,
        "child_cpu_user_usec": 100_000,
        "cpu_share_usec_est": 4321,
    }
    if estimated is not None:
        row["estimated"] = estimated
    return row


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
# Session fixture — the HARD-allowlist fence, then the scratch engine
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _scratch_db_fence():
    """The HARD-allowlist fence — the FIRST thing that runs.

    It is an explicit DEPENDENCY of this module's ``pg_engine``
    fixture, so pytest creates it BEFORE the engine exists — i.e.
    before any SQL (and before the session-scoped ``drop_all``) can
    reach the target database.
    """
    _tripwire_check()
    return True


@pytest.fixture(scope="session")
def pg_engine(_scratch_db_fence):
    """The scratch engine — OVERRIDES the shared conftest fixture.

    WHY THE OVERRIDE: the shared ``tests/postgres/conftest.py``
    ``pg_engine`` runs ``SQLModel.metadata.drop_all(engine)`` on the
    TARGET database at session setup. A fence that ran inside a test
    body — or inside a function-scoped fixture that depends on
    ``pg_engine`` — refuses AFTER the tables of a live database are
    already gone. Overriding the fixture puts the allowlist check on
    the only code path that touches the database; the explicit
    ``_scratch_db_fence`` dependency makes the ordering a property of
    the fixture graph rather than of fixture-resolution luck.
    """
    engine = _probe_postgres(PG_URL)
    if engine is None:
        pytest.skip(f"PostgreSQL not available at {PG_URL}")
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        try:
            SQLModel.metadata.drop_all(engine)
        finally:
            engine.dispose()


@pytest.fixture
def scratch_engine(pg_engine):
    """Fresh schema for the four resource_monitor tables."""
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
    def test_the_bound_column_list_is_derived_from_the_flusher_source(self):
        """The audited list IS the flusher's list (no hand-copy).

        The audit is only as good as the list it checks. A hand-copied
        list silently goes stale: a column the flusher starts binding
        is never audited, and a column it stops binding stays in the
        audit. This leg proves the derivation sees the flusher's own
        INSERT column lists.
        """
        bound = _flusher_bound_columns()
        assert "resource_daemon_bucket" in bound
        assert "resource_instance_sample" in bound
        assert "resource_pid_map" in bound
        # The raw tier + the minute tier + the hour tier all target
        # the daemon-bucket table; their union carries every column
        # the flusher binds for it.
        assert "cg_anon_kb" in bound["resource_daemon_bucket"]
        assert "attributed_bytes" in bound["resource_daemon_bucket"]
        assert "estimated" in bound["resource_instance_sample"]
        assert "graph_cache_present" in bound["resource_instance_sample"]
        assert "peak_rss_kb" in bound["resource_pid_map"]

    def test_every_bound_column_matches_the_hardcoded_pg_type(
        self, scratch_engine
    ):
        """The flusher's bind map == the hardcoded PG column types.

        This is the audit that would have caught the v0.25.0 defect
        class at the SOURCE (a Python ``bool`` bound into an
        ``INTEGER`` column) instead of at flush time.

        The expectation is a LITERAL (not the model's declared type):
        a model mutation — e.g. flipping ``estimated`` from Boolean to
        Integer — must now FAIL this test instead of silently
        redefining the truth.
        """
        bound = _flusher_bound_columns()
        tables = _model_tables()
        mismatches: list[str] = []
        for table_name, columns in bound.items():
            expected_map = _EXPECTED_PG_TYPES.get(table_name)
            assert expected_map is not None, (
                f"{table_name}: the flusher binds a table with no "
                "hardcoded expected-type map — extend _EXPECTED_PG_TYPES"
            )
            model_table = tables.get(table_name)
            assert model_table is not None, (
                f"{table_name}: bound by the flusher but absent from the "
                "model registry"
            )
            for column in sorted(columns):
                expected = expected_map.get(column)
                assert expected is not None, (
                    f"{table_name}.{column}: the flusher binds it but the "
                    "hardcoded expected-type map has no entry"
                )
                # (b) the MODEL's declared type must agree with the
                # hardcoded truth (this is the leg a model mutation
                # used to hide behind).
                declared = model_table.columns[column].type
                declared_pg = _SA_TYPE_TO_PG.get(
                    type(declared).__name__.upper()
                )
                if declared_pg != expected:
                    mismatches.append(
                        f"{table_name}.{column}: model declares "
                        f"{declared_pg!r}, hardcoded expectation is "
                        f"{expected!r}"
                    )
                # (a) the LIVE PG type must agree with the hardcoded
                # truth.
                live = _column_type(scratch_engine, table_name, column)
                if live != expected:
                    mismatches.append(
                        f"{table_name}.{column}: hardcoded expectation is "
                        f"{expected!r}, live PG says {live!r}"
                    )
        assert not mismatches, (
            "flusher bind map drifted from the hardcoded PG column "
            "types:\n  " + "\n  ".join(mismatches)
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


class TestEveryBoundT2ColumnIsReadBack:
    """B7b — 10 of the 14 bound T2 columns were never read back.

    A column that is written but never read back is a column whose
    bind can be wrong without anything noticing. This class SELECTs
    EVERY column the flusher binds into T2 and value-asserts it.
    """

    _T2_COLUMNS = (
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
    )

    def test_every_bound_t2_column_lands_with_its_value(self, scratch_engine):
        """All 14 bound T2 columns are read back and value-asserted."""
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        row = _instance_row(estimated=True, graph_cache_present=False)
        instance_ring.append(row)

        flusher = _flusher(scratch_engine, ring, instance_ring)
        flusher.run_once()
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            stored = c.execute(
                text(
                    "SELECT bucket_ts, instance_id, agent_id, status, "
                    "scan_kind, charged_bytes, deep_bytes, "
                    "graph_cache_present, in_flight_tasks, "
                    "all_state_messages_len, child_rss_kb, "
                    "child_cpu_user_usec, cpu_share_usec_est, estimated "
                    "FROM resource_instance_sample"
                )
            ).fetchall()
        assert len(stored) == 1
        values = dict(zip(self._T2_COLUMNS, stored[0]))

        # ``bucket_ts`` — VALUE-asserted (it used to be selected and
        # never checked).
        assert values["bucket_ts"] == row["bucket_ts"]
        for column in self._T2_COLUMNS:
            if column == "bucket_ts":
                continue
            expected = row[column]
            if column == "graph_cache_present":
                # The WRITTEN False must read back as False (not None,
                # not 0) — the "written but never asserted" defect.
                assert values[column] is False, (
                    "graph_cache_present=False must read back as False, "
                    f"got {values[column]!r}"
                )
                continue
            assert values[column] == expected, (
                f"{column}: expected {expected!r}, read back "
                f"{values[column]!r}"
            )
        assert values["estimated"] is True

    def test_a_row_without_estimated_takes_the_default_true_path(
        self, scratch_engine
    ):
        """The flusher's ``r.get("estimated", True)`` default is pinned.

        The key is OMITTED from the row entirely — the flusher's
        default must land ``True`` in the NOT NULL boolean column.
        """
        ring = TierRing(tier="raw", maxlen=16)
        instance_ring = TierRing(tier="instance", maxlen=16)
        row = _instance_row(estimated=None, graph_cache_present=False)
        assert "estimated" not in row
        instance_ring.append(row)

        flusher = _flusher(scratch_engine, ring, instance_ring)
        flusher.run_once()
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            stored = c.execute(
                text(
                    "SELECT estimated, graph_cache_present "
                    "FROM resource_instance_sample"
                )
            ).fetchall()
        assert len(stored) == 1
        estimated, graph_cache_present = stored[0]
        assert estimated is True, (
            "the omitted-estimated default must land True, got "
            f"{estimated!r}"
        )
        assert graph_cache_present is False


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

    def test_a_failed_read_leaves_the_degraded_sentinel_in_t1(
        self, scratch_engine, monkeypatch
    ):
        """B1 — the DB evidence path for a DROPPED sample.

        A permanently dark host must leave DB evidence of the drops:
        the DEGRADED SENTINEL row (``interval_s = 0``, every daemon
        metric NULL, ``missed_samples`` carrying the running drop
        total). The sentinel must NOT pollute the sample tiers.
        """
        from daemon.services.resource_monitor import sampler as sampler_mod

        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
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
        )
        assert sampler.run_once() is None
        assert sampler.counters.missed_samples == 1

        flusher = _flusher(scratch_engine, ring, instance_ring)
        flusher.run_once()
        assert flusher.counters.errors == 0

        with scratch_engine.connect() as c:
            sentinels = c.execute(
                text(
                    "SELECT interval_s, rss_kb, anon_kb, threads, "
                    "coverage_pct, missed_samples, sample_cost_ms "
                    "FROM resource_daemon_bucket WHERE interval_s = 0"
                )
            ).fetchall()
            tiers = [
                int(r[0])
                for r in c.execute(
                    text(
                        "SELECT DISTINCT interval_s "
                        "FROM resource_daemon_bucket"
                    )
                ).fetchall()
            ]
        assert len(sentinels) == 1, (
            "the degraded sentinel did not reach T1 — a permanently dark "
            "host would carry NO database evidence of the drops"
        )
        (
            interval_s,
            rss_kb,
            anon_kb,
            threads,
            coverage_pct,
            missed,
            _cost,
        ) = sentinels[0]
        assert interval_s == DEGRADED_SENTINEL_INTERVAL_S
        assert rss_kb is None
        assert anon_kb is None
        assert threads is None
        assert coverage_pct is None
        assert missed == 1
        # The sentinel is NOT a sample tier — it must not reach the
        # minute/hour roll-up.
        assert tiers == [0], (
            f"the degraded sentinel polluted the sample tiers: {tiers!r}"
        )
