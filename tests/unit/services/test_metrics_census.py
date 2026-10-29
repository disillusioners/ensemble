"""P0 T7 / T10 — the PostgreSQL census sampler (catalog §9, arch §5 A8).

Pins the census DISCIPLINE with a FAKE psycopg connection (no database
is touched in this leg):

* a **direct, non-pooled** ``psycopg.connect`` — never a pool;
* ``application_name`` / ``statement_timeout`` / ``connect_timeout``
  applied on every census connection;
* the **self-exclusion rule** — the census excludes ONLY its own
  ``application_name``; ``wedge-capture`` rows stay in ``other`` (they
  must NOT be excluded — that would break the sum-ties invariant);
* **one round trip** per census;
* the query text is **never exported** (the SELECT list carries
  aggregates only);
* the **never-raises** posture (a failure renders no snapshot and
  leaves the last-success timestamp stale);
* the **kill-switch gates the DB work** — with the switch off the
  sampler performs NO database work;
* the closed fragment→site map — one unit test per site (6).
"""

from __future__ import annotations

import logging
import re
import sys
import types

import pytest

from daemon.services import metrics_census as mc


# ─── the fake psycopg ──────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.cursors = [_FakeCursor(rows or [])]
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursors[-1]

    def close(self) -> None:
        self.closed = True


class _RecordingConnect:
    """The fake connection factory — records the DSN it was handed."""

    def __init__(self, rows: list[tuple] | None = None, error: Exception | None = None):
        self.dsns: list[str] = []
        self.connections: list[_FakeConnection] = []
        self._rows = rows or []
        self._error = error

    def __call__(self, dsn: str) -> _FakeConnection:
        self.dsns.append(dsn)
        if self._error is not None:
            raise self._error
        conn = _FakeConnection(self._rows)
        self.connections.append(conn)
        return conn


def _row(kind: str, key: str, n: int, age: float = 0.0) -> tuple:
    return (kind, key, n, age)


# ─── the connection discipline ─────────────────────────────────────────────


def test_census_connects_directly_with_no_pool(monkeypatch):
    """The census opens a DIRECT psycopg connection — never a pool."""
    captured: dict[str, object] = {}

    class _FakePsycopg(types.ModuleType):
        def connect(self, dsn, **kwargs):  # noqa: ANN001
            captured["dsn"] = dsn
            captured["kwargs"] = kwargs
            return _FakeConnection()

    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg("psycopg"))
    conn = mc.connect_census("postgresql://u:p@localhost:5432/db")
    assert conn is not None
    assert captured["dsn"] == "postgresql://u:p@localhost:5432/db"
    kwargs = captured["kwargs"]
    assert kwargs["application_name"] == "ensemble-metrics-census"
    assert kwargs["connect_timeout"] == mc.CENSUS_CONNECT_TIMEOUT_S == 3
    assert kwargs["options"] == "-c statement_timeout=5000"
    # No pool API is used at all.
    assert "connection_class" not in kwargs
    assert "max_size" not in kwargs


def test_statement_timeout_is_five_seconds():
    assert mc.CENSUS_STATEMENT_TIMEOUT_MS == 5000


def test_cadence_floor_is_fifteen_seconds():
    assert mc.DEFAULT_CENSUS_INTERVAL_S == 15
    assert mc.MIN_CENSUS_INTERVAL_S == 15
    sampler = mc.CensusSampler(interval_s=1, dsn="postgresql://x")
    assert sampler.interval_s == 15


def test_dsn_is_driver_stripped():
    assert (
        mc._strip_driver("postgresql+psycopg://u:p@h:5432/db")
        == "postgresql://u:p@h:5432/db"
    )
    assert mc._strip_driver("postgresql://u:p@h:5432/db") == (
        "postgresql://u:p@h:5432/db"
    )


# ─── the SQL: one round trip, server-side map, no query-text export ───────


def test_census_is_one_round_trip():
    connect = _RecordingConnect(rows=[_row("state", "idle", 3)])
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    sampler.collect()
    assert len(connect.connections) == 1
    cursor = connect.connections[0].cursor()
    assert len(cursor.executed) == 1
    assert connect.connections[0].closed is True


def test_census_sql_never_selects_the_query_text():
    """The backend's statement text must NEVER leave PostgreSQL."""
    sql = mc.CENSUS_SQL.lower()
    # Every occurrence of "query" belongs to an ILIKE pattern.
    ilike_occurrences = len(re.findall(r"query ilike", sql))
    total_occurrences = len(re.findall(r"\bquery\b", sql))
    assert total_occurrences == ilike_occurrences
    assert "pg_stat_activity.query" not in sql


def test_census_sql_excludes_only_the_census_itself():
    sql = mc.CENSUS_SQL
    assert mc.CENSUS_APPLICATION_NAME in sql
    # The wedge-capture rows are NOT excluded — they count under
    # ``other`` (excluding them would break the sum-ties invariant).
    assert "wedge-capture" not in sql
    assert sql.count(f"<> {mc.CENSUS_APPLICATION_NAME!r}") == 2


def test_census_sql_evaluates_the_site_map_server_side():
    sql = mc.CENSUS_SQL.lower()
    assert "case" in sql
    assert "group by 2" in sql
    # The map is evaluated in SQL, not in Python.
    assert "extract(epoch from (now() - state_change))" in sql


# ─── the closed fragment→site map (one test per site) ─────────────────────


@pytest.mark.parametrize(
    "site,pattern",
    [
        ("instance_parent_lookup", "%from instances where instances.id =%"),
        ("instance_read", "%from instance_hierarchy%"),
        ("instance_read", "%from instances%"),
        ("job_queue_read", "%from job_queue_items%"),
        ("checkpoint_saver", "%from checkpoint_blobs%"),
        ("checkpoint_saver", "%from checkpoints%"),
        ("agent_db_tool", "%repair_log%"),
    ],
)
def test_every_site_pattern_is_pinned_in_the_sql(site, pattern):
    assert pattern in mc.CENSUS_SQL
    assert f"'{site}'" in mc.CENSUS_SQL


def test_the_site_set_is_closed_and_includes_the_residual():
    assert mc.CENSUS_SITES == (
        "instance_parent_lookup",
        "instance_read",
        "job_queue_read",
        "checkpoint_saver",
        "agent_db_tool",
        "other",
    )
    assert mc.CENSUS_RESIDUAL_SITE == "other"


def test_the_specific_site_is_matched_before_the_general_one():
    """``instance_parent_lookup`` must be evaluated BEFORE
    ``instance_read`` — otherwise the general pattern swallows it."""
    sql = mc.CENSUS_SQL
    assert sql.index("'instance_parent_lookup'") < sql.index("'instance_read'")


def test_every_site_has_at_least_one_pinned_pattern():
    pinned = {site for site, _patterns in mc.CENSUS_SITE_PATTERNS}
    assert pinned == set(mc.CENSUS_SITES) - {mc.CENSUS_RESIDUAL_SITE}


# ─── the snapshot ─────────────────────────────────────────────────────────


def test_snapshot_keeps_every_site_and_never_drops_the_residual():
    connect = _RecordingConnect(
        rows=[
            _row("state", "idle", 4),
            _row("state", "idle in transaction", 2),
            _row("site", "instance_read", 1, 12.5),
            _row("site", "other", 1, 3.25),
        ]
    )
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    snapshot = sampler.collect()
    assert snapshot is not None
    assert snapshot.state_counts == {"idle": 4, "idle in transaction": 2}
    assert set(snapshot.idle_in_txn_by_site) == set(mc.CENSUS_SITES)
    assert snapshot.idle_in_txn_by_site["instance_read"] == 1
    assert snapshot.idle_in_txn_max_age_by_site["instance_read"] == 12.5
    assert snapshot.idle_in_txn_by_site["other"] == 1
    # The residual is never dropped ⇒ the total equals the sum.
    assert snapshot.idle_in_txn_total == sum(snapshot.idle_in_txn_by_site.values())
    assert snapshot.last_success_epoch is not None


def test_snapshot_never_drops_a_site_that_reads_zero():
    connect = _RecordingConnect(rows=[_row("site", "other", 0)])
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    snapshot = sampler.collect()
    assert snapshot is not None
    for site in mc.CENSUS_SITES:
        assert snapshot.idle_in_txn_by_site[site] == 0


# ─── the never-raises posture ─────────────────────────────────────────────


def test_a_failed_connection_never_raises(caplog):
    connect = _RecordingConnect(error=RuntimeError("connection refused"))
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    with caplog.at_level(logging.WARNING, logger=mc.logger.name):
        snapshot = sampler.collect()
    assert snapshot is None
    assert "census failed" in caplog.text
    assert sampler.snapshot is None


def test_a_failed_query_never_raises():
    class _BoomCursor(_FakeCursor):
        def execute(self, sql: str) -> None:
            raise RuntimeError("statement timeout")

    class _BoomConnection(_FakeConnection):
        def cursor(self) -> _FakeCursor:
            return _BoomCursor([])

    def _connect(dsn: str) -> _FakeConnection:
        return _BoomConnection()

    sampler = mc.CensusSampler(dsn="postgresql://x", connect=_connect)
    assert sampler.collect() is None


def test_a_failure_leaves_the_last_success_stale():
    """A failed census keeps the PREVIOUS snapshot and its timestamp."""
    rows = [_row("state", "idle", 1)]
    good = _RecordingConnect(rows=rows)
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=good)
    first = sampler.collect()
    assert first is not None
    sampler._snapshot = first
    bad = _RecordingConnect(error=RuntimeError("boom"))
    sampler._connect = bad
    assert sampler.collect() is None
    # The stored snapshot is unchanged — its last-success stamp is
    # STALE, never bumped by a failure.
    assert sampler.snapshot is first
    assert sampler.snapshot.last_success_epoch == first.last_success_epoch


def test_no_dsn_means_no_census():
    sampler = mc.CensusSampler(dsn=None, connect=_RecordingConnect())
    sampler._dsn = None
    monkey = __import__("os").environ
    monkey.pop("POSTGRES_URL", None)
    monkey.pop("POSTGRES_HOST", None)
    assert sampler.collect() is None


# ─── the kill-switch gates the DB work ────────────────────────────────────


def test_the_gate_stops_the_sampler_doing_db_work(monkeypatch):
    """With the switch OFF the sampler performs NO database work."""
    from daemon.services import metrics_gate

    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    connect = _RecordingConnect(rows=[_row("state", "idle", 1)])
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    assert sampler._gate() is False
    # A direct collect() is an explicit call — the GATE is what the tick
    # loop consults.
    sampler.start()
    try:
        import time

        time.sleep(0.2)
    finally:
        detached = sampler.stop()
        if detached is not None:
            detached.join(timeout=5.0)
    assert connect.dsns == []  # NO connection attempt while OFF


def test_the_gate_allows_db_work_when_on(monkeypatch):
    from daemon.services import metrics_gate

    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    connect = _RecordingConnect(rows=[_row("state", "idle", 1)])
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=connect)
    assert sampler._gate() is True


def test_the_gate_reads_the_shared_module_by_default(monkeypatch):
    from daemon.services import metrics_gate

    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    sampler = mc.CensusSampler(dsn="postgresql://x")
    assert sampler._gate() is False
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    assert sampler._gate() is True


# ─── the sampler lifecycle ────────────────────────────────────────────────


def test_the_sampler_runs_on_a_dedicated_daemon_thread():
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=_RecordingConnect())
    assert sampler.start() is True
    try:
        assert sampler._thread is not None
        assert sampler._thread.daemon is True
        assert sampler._thread.name == "metrics-census"
        # NOT the listener thread and NOT the event loop.
        assert sampler._thread is not threading_current()
    finally:
        detached = sampler.stop()
        if detached is not None:
            detached.join(timeout=5.0)


def threading_current():
    import threading

    return threading.current_thread()


def test_stop_is_detached():
    sampler = mc.CensusSampler(dsn="postgresql://x", connect=_RecordingConnect())
    sampler.start()
    detached = sampler.stop()
    assert detached is not None
    assert detached.daemon is True
    detached.join(timeout=5.0)


def test_the_sampler_holds_its_snapshot_in_memory_only():
    """P0 registers NO F6 families — the snapshot is the deliverable."""
    sampler = mc.CensusSampler(
        dsn="postgresql://x",
        connect=_RecordingConnect(rows=[_row("state", "idle", 1)]),
    )
    sampler.collect()
    assert isinstance(sampler.snapshot, mc.CensusSnapshot)
    # No metric registration happened in this module.
    assert not hasattr(mc, "register_gauge")
    assert "ensemble_pg_" not in mc.CENSUS_SQL
