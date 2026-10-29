"""T1 ROW IDENTITY — the sampler stamps ``bucket_ts`` at SAMPLE time.

WHY THIS MODULE EXISTS (the round-3 G3 item):

    The flusher used to stamp ``bucket_ts`` at DRAIN time, at second
    resolution. Every row drained in one flush window therefore shared
    ONE ``(bucket_ts, interval_s)`` key, and the stacked-tier insert
    (``ON CONFLICT (bucket_ts, interval_s) DO NOTHING`` on PostgreSQL /
    ``INSERT OR IGNORE`` on SQLite) swallowed all but the FIRST row —
    with ``flush.errors == 0`` and no log line. The raw tier silently
    lost ~50 % of the real samples (75 % at the 15 s floor).

    The fix is producer-side and DDL-free: the SAMPLER stamps the row
    at SAMPLE time, so two samples at least one second apart get
    distinct keys.

What is pinned HERE (no PG, no /proc, no network):

  * the row carries a ``bucket_ts`` and the stamp is NAIVE-UTC digits
    (D9 — never a tz-aware bind);
  * a multi-tick window yields MULTIPLE distinct rows (the pre-fix
    behaviour was ONE row per window);
  * the GRANULARITY is stated, not promised: one row per SAMPLE
    SECOND — two samples inside the same second still collide;
  * the DEGRADED SENTINEL carries the drop count as of ITS OWN drop,
    and the window's TRUE drop count is ``MAX(missed_samples)`` over
    the window's rows — the count is never reset below the truth, so
    it stays recoverable from T1 alone after the reader recovers;
  * the RAW retention sweep covers the raw tier's EFFECTIVE interval
    range (``0 < interval_s < 60``) — it used to be pinned to
    ``interval_s = 30``, so at a 15 s configuration the raw rows were
    never swept — and it never touches the minute tier, the hour tier
    or the sentinel;
  * the AST column audit detects REMOVALS too (the hard-coded expected
    column SET).

FENCE DISCIPLINE: file SQLite under pytest ``tmp_path``; the platform
read is MOCKED (``read_proc_snapshot``); no live ``/proc``, no DB.
"""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from daemon.models.resource_monitor import ResourceDaemonBucket
from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing
from daemon.services.resource_monitor import flusher as flusher_mod
from daemon.services.resource_monitor import sampler as sampler_mod
from daemon.services.resource_monitor.proc_readers import (
    DaemonProcSnapshot,
    ProcSnapshot,
)
from daemon.services.resource_monitor.sampler import (
    DEGRADED_SENTINEL_INTERVAL_S,
    Sampler,
)
from daemon.services.timestamps import now_utc_naive

_BUCKET_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _snapshot() -> ProcSnapshot:
    """A synthetic daemon snapshot (the platform read is mocked)."""
    return ProcSnapshot(
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


def _sampler(ring: TierRing, *, instance_ring: TierRing | None = None):
    return Sampler(
        sample_seconds=30,
        self_cpu_budget_pct=1.0,
        self_rss_budget_mb=50,
        detail_level="minimal",
        proc_root="/nonexistent-proc-root-for-rm-t1-tests",
        cgroup_root="/nonexistent-cgroup-root-for-rm-t1-tests",
        ring=ring,
        instance_ring=instance_ring,
    )


def _stamp_sequence(monkeypatch, stamps: list[str]) -> None:
    """Pin the sampler's ``bucket_ts`` stamp to a fixed sequence.

    The stamp helper is the sampler's own seam (``_bucket_ts_now``);
    pinning it makes the SECOND-RESOLUTION semantics deterministic and
    fast (no sleeping) while still proving that the SAMPLER (not the
    flusher) is the producer of the stamp.
    """
    it = iter(stamps)

    def _next() -> str:
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - defensive
            return stamps[-1]

    monkeypatch.setattr(sampler_mod, "_bucket_ts_now", _next)


def _sqlite_engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 't1.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ResourceDaemonBucket.__table__.create(engine, checkfirst=True)
    return engine


def _flusher(engine, ring: TierRing, *, now=None) -> Flusher:
    return Flusher(
        engine=engine,
        raw_ring=ring,
        flush_batch_seconds=60,
        retention=RetentionPolicy(
            raw_minutes=2880, minute_hours=336, hour_days=180
        ),
        instance_ring=TierRing(tier="instance", maxlen=16),
        now_utc_naive=now,
    )


def _rows(engine, sql: str, params: dict | None = None) -> list[tuple]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql), params or {}).fetchall())


# ──────────────────────────────────────────────────────────────────────────
# The stamp itself (D9)
# ──────────────────────────────────────────────────────────────────────────


class TestBucketTsStamp:
    def test_the_sampler_row_carries_a_bucket_ts(self, monkeypatch):
        ring = TierRing(tier="raw", maxlen=16)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: _snapshot()
        )
        _stamp_sequence(monkeypatch, ["2026-10-29 10:00:01"])
        s = _sampler(ring)
        assert s.run_once() is not None
        row = ring.drain()[0]
        assert row["bucket_ts"] == "2026-10-29 10:00:01"

    def test_the_stamp_is_naive_utc_digits(self):
        """D9 — the stamp holds UTC digits, never an offset."""
        stamp = sampler_mod._bucket_ts_now()
        assert _BUCKET_TS_RE.match(stamp), (
            f"bucket_ts {stamp!r} is not naive-UTC ISO digits"
        )
        # The sanctioned helper produces the SAME digits (D9).
        assert stamp == now_utc_naive().strftime("%Y-%m-%d %H:%M:%S") or (
            stamp == (now_utc_naive() + timedelta(seconds=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
        # No timezone suffix / offset is ever carried.
        assert "+" not in stamp and "Z" not in stamp


# ──────────────────────────────────────────────────────────────────────────
# G3 — a multi-tick window yields MULTIPLE rows (T-2: the granularity)
# ──────────────────────────────────────────────────────────────────────────


class TestMultiTickWindow:
    def test_three_samples_land_as_three_distinct_rows(
        self, tmp_path, monkeypatch
    ):
        """The pre-fix behaviour was ONE row per flush window."""
        engine = _sqlite_engine(tmp_path)
        ring = TierRing(tier="raw", maxlen=16)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: _snapshot()
        )
        _stamp_sequence(
            monkeypatch,
            [
                "2026-10-29 10:00:01",
                "2026-10-29 10:00:02",
                "2026-10-29 10:00:03",
            ],
        )
        s = _sampler(ring)
        for _ in range(3):
            assert s.run_once() is not None
        assert len(ring) == 3
        assert len({r["bucket_ts"] for r in ring.snapshot()}) == 3

        flusher = _flusher(engine, ring)
        result = flusher.run_once()
        assert flusher.counters.errors == 0
        raw = _rows(
            engine,
            "SELECT bucket_ts FROM resource_daemon_bucket "
            "WHERE interval_s = 30 ORDER BY bucket_ts",
        )
        assert len(raw) == 3, (
            "the raw tier collapsed a three-sample window into "
            f"{len(raw)} row(s) — the drain-time bucket_ts stamp is back"
        )
        assert [r[0] for r in raw] == [
            "2026-10-29 10:00:01",
            "2026-10-29 10:00:02",
            "2026-10-29 10:00:03",
        ]
        assert result["rows_written"] >= 3

    def test_two_samples_in_the_same_second_still_collide(
        self, tmp_path, monkeypatch
    ):
        """The GRANULARITY, stated honestly (T-2).

        The stamp is SECOND-resolution: two samples inside the same
        second share the ``(bucket_ts, interval_s)`` key and the
        ON CONFLICT insert keeps the first. The raw tier is therefore
        ONE ROW PER SAMPLE SECOND — not a guaranteed one-row-per-tick
        store at a sub-second cadence.
        """
        engine = _sqlite_engine(tmp_path)
        ring = TierRing(tier="raw", maxlen=16)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: _snapshot()
        )
        _stamp_sequence(
            monkeypatch,
            ["2026-10-29 10:00:01", "2026-10-29 10:00:01"],
        )
        s = _sampler(ring)
        for _ in range(2):
            assert s.run_once() is not None
        flusher = _flusher(engine, ring)
        flusher.run_once()
        raw = _rows(
            engine,
            "SELECT bucket_ts FROM resource_daemon_bucket "
            "WHERE interval_s = 30",
        )
        assert len(raw) == 1, (
            "two samples in the SAME second are expected to collapse "
            "onto one key (second-resolution granularity)"
        )


# ──────────────────────────────────────────────────────────────────────────
# T-1 / G1 — the drop count is recoverable as MAX(missed_samples)
# ──────────────────────────────────────────────────────────────────────────


class TestDegradedSentinelDropCount:
    def _drive_failures(self, monkeypatch, count: int):
        ring = TierRing(tier="raw", maxlen=64)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        _stamp_sequence(
            monkeypatch,
            [f"2026-10-29 11:00:{i:02d}" for i in range(1, count + 1)],
        )
        s = _sampler(ring)
        for _ in range(count):
            assert s.run_once() is None
        return s, ring

    def test_each_sentinel_carries_the_count_as_of_its_own_drop(
        self, tmp_path, monkeypatch
    ):
        """G1 — the surviving row is NOT the window's total.

        With the sampler-side stamp every sentinel of a window lands as
        its OWN row, and each row carries the count as of ITS OWN drop:
        1, 2, 3, 4 — NOT "4" on the first row and nothing else.
        """
        engine = _sqlite_engine(tmp_path)
        ring = TierRing(tier="raw", maxlen=64)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        _stamp_sequence(
            monkeypatch,
            [f"2026-10-29 11:00:{i:02d}" for i in range(1, 5)],
        )
        s = _sampler(ring)
        for _ in range(4):
            assert s.run_once() is None
        assert s.counters.missed_samples == 4

        flusher = _flusher(engine, ring)
        flusher.run_once()
        assert flusher.counters.errors == 0
        sentinels = _rows(
            engine,
            "SELECT bucket_ts, missed_samples FROM resource_daemon_bucket "
            "WHERE interval_s = :marker ORDER BY bucket_ts",
            {"marker": DEGRADED_SENTINEL_INTERVAL_S},
        )
        assert len(sentinels) == 4, (
            "the four drops of one window collapsed into "
            f"{len(sentinels)} row(s) — the drop evidence is lost"
        )
        assert [int(r[1]) for r in sentinels] == [1, 2, 3, 4]
        # THE RECOVERY PATH: the window's TRUE drop count is the MAX.
        assert max(int(r[1]) for r in sentinels) == 4

    def test_the_true_drop_count_is_the_max_over_the_window(
        self, tmp_path, monkeypatch
    ):
        """T-1 — MAX(missed_samples) across the window's rows."""
        engine = _sqlite_engine(tmp_path)
        s, ring = self._drive_failures(monkeypatch, 3)
        flusher = _flusher(engine, ring)
        flusher.run_once()
        sentinels = _rows(
            engine,
            "SELECT missed_samples FROM resource_daemon_bucket "
            "WHERE interval_s = :marker",
            {"marker": DEGRADED_SENTINEL_INTERVAL_S},
        )
        assert max(int(r[0]) for r in sentinels) == (
            s.counters.missed_samples
        )

    def test_the_count_is_not_reset_below_the_truth_after_recovery(
        self, tmp_path, monkeypatch
    ):
        """T-1 — once the reader recovers the count must not be reset.

        The counter is cumulative and is NEVER reset, so the next REAL
        row carries the same running total — the MAX over the window's
        rows therefore still reports the TRUE number of drops.
        """
        engine = _sqlite_engine(tmp_path)
        ring = TierRing(tier="raw", maxlen=64)
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: None
        )
        _stamp_sequence(
            monkeypatch,
            [
                "2026-10-29 11:00:01",
                "2026-10-29 11:00:02",
                "2026-10-29 11:00:03",
                "2026-10-29 11:00:04",
            ],
        )
        s = _sampler(ring)
        for _ in range(3):
            assert s.run_once() is None
        # The reader recovers.
        monkeypatch.setattr(
            sampler_mod, "read_proc_snapshot", lambda *a, **k: _snapshot()
        )
        assert s.run_once() is not None
        assert s.counters.missed_samples == 3, (
            "the drop counter was reset by the recovery — the count is "
            "no longer recoverable from T1"
        )

        flusher = _flusher(engine, ring)
        flusher.run_once()
        window = _rows(
            engine,
            "SELECT missed_samples FROM resource_daemon_bucket "
            "WHERE interval_s IN (0, 30)",
        )
        assert max(int(r[0]) for r in window) == 3


# ──────────────────────────────────────────────────────────────────────────
# T-3 — the raw retention sweep covers the effective interval range
# ──────────────────────────────────────────────────────────────────────────


class TestRawRetentionSweep:
    def _seed(self, engine, *, bucket_ts: str, interval_s: int) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_daemon_bucket "
                    "(bucket_ts, interval_s, rss_kb) "
                    "VALUES (:ts, :iv, 1)"
                ),
                {"ts": bucket_ts, "iv": interval_s},
            )

    def test_the_raw_sweep_covers_the_effective_interval_range(
        self, tmp_path
    ):
        """A raw row at a NON-30 interval is swept at the raw cutoff.

        The sweep used to be pinned to ``interval_s = 30``, so at a 15 s
        configuration the raw rows were NEVER swept (unbounded growth).
        """
        engine = _sqlite_engine(tmp_path)
        old = (now_utc_naive() - timedelta(days=3)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self._seed(engine, bucket_ts=old, interval_s=15)
        ring = TierRing(tier="raw", maxlen=8)
        flusher = _flusher(engine, ring)
        flusher.run_once()
        left = _rows(
            engine,
            "SELECT interval_s FROM resource_daemon_bucket "
            "WHERE interval_s = 15",
        )
        assert left == [], (
            "the raw tier row at interval_s=15 survived the raw "
            "retention sweep — the sweep is still pinned to 30"
        )

    def test_the_raw_sweep_never_touches_the_other_tiers(
        self, tmp_path
    ):
        """The minute/hour tiers and the sentinel are NOT swept raw."""
        engine = _sqlite_engine(tmp_path)
        old = (now_utc_naive() - timedelta(days=3)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for interval_s in (60, 3600):
            self._seed(engine, bucket_ts=old, interval_s=interval_s)
        ring = TierRing(tier="raw", maxlen=8)
        flusher = _flusher(engine, ring)
        flusher.run_once()
        survivors = {
            int(r[0])
            for r in _rows(
                engine,
                "SELECT DISTINCT interval_s FROM resource_daemon_bucket",
            )
        }
        assert survivors == {60, 3600}, (
            "the raw sweep erased a roll-up tier (or the sentinel): "
            f"{survivors!r}"
        )

    def test_the_sentinel_keeps_its_own_sweep(self, tmp_path):
        """The sentinel is swept by ITS OWN sweep (raw cutoff), not by
        accident — and a RECENT sentinel row survives the window."""
        engine = _sqlite_engine(tmp_path)
        old = (now_utc_naive() - timedelta(days=3)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        recent = now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")
        self._seed(engine, bucket_ts=old, interval_s=0)
        self._seed(engine, bucket_ts=recent, interval_s=0)
        ring = TierRing(tier="raw", maxlen=8)
        flusher = _flusher(engine, ring)
        flusher.run_once()
        left = _rows(
            engine,
            "SELECT bucket_ts FROM resource_daemon_bucket "
            "WHERE interval_s = 0",
        )
        assert len(left) == 1
        assert left[0][0] == recent, (
            "the expired sentinel survived — the sentinel sweep is "
            "missing or its cutoff is wrong"
        )


# ──────────────────────────────────────────────────────────────────────────
# m8 — the AST column audit must detect REMOVALS too
# ──────────────────────────────────────────────────────────────────────────


def _flusher_bound_columns() -> dict[str, set[str]]:
    """The bound-column set per table, derived from the FLUSHER SOURCE."""
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


class TestColumnAuditDetectsRemovals:
    def test_the_bound_column_set_is_the_hardcoded_expected_set(self):
        """m8 — a REMOVED column must fail the audit.

        The AST-derived set detects an ADDED column (the derived set
        grows) but a REMOVED column simply drops OUT of the derived set
        and the audit stayed green (proved: removing ``pss_kb`` from
        the flusher's bind list left the audit passing). The expected
        set is therefore HARD-CODED here and compared for EQUALITY.
        """
        from tests.postgres.test_resource_monitor_flusher_bind_pg import (
            _EXPECTED_BOUND_COLUMNS,
        )

        bound = _flusher_bound_columns()
        assert bound == {
            table: set(cols) for table, cols in _EXPECTED_BOUND_COLUMNS.items()
        }, (
            "the flusher's bound-column set drifted from the hardcoded "
            "expected set (an added OR a removed column)"
        )
