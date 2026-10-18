"""``flusher`` — batched ``executemany`` writes + roll-down + retention
sweep for the resource monitor.

Architecture §2.1 (binding):

  * ONE flusher thread (NOT asyncio). Separate session per flush
    window — never reuses the sampler session, never holds the
    DB while the sampler ticks.
  * Bounded drop-oldest RAM buffer (~512 rows / ~64 KB) — overflow
    beyond the bound drops the oldest row (the sampler keeps the
    authoritative ring; the flusher's buffer is for the brief
    window between ``drain()`` and ``executemany``).
  * ONE ``executemany`` transaction per flush window — exactly one
    BEGIN / COMMIT pair per window. AC8 asserts this via session
    instrumentation.
  * Roll-down raw → minute → hour rides the same flush window.
  * Retention sweep (D8) runs post-window: PER-TIER DELETEs — the
    three tiers are stacked in ONE table (deviation #1) and each
    DELETE carries its ``interval_s`` predicate (30/60/3600), so a
    lower tier's cutoff can never erase a higher tier's rows.

P1 boundary (D13): T1 attribution columns are written NULL/0; the
flusher does NOT compute attribution.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .pid_registry import (
    EXIT_KIND_UNKNOWN,
    PidRegistry,
    PidRegistryEntry,
    get_pid_registry,
)
from .ring import TierRing
from .rollup import (
    RetentionPolicy,
    cutoff_iso,
    retention_cutoffs,
    rollup_to_hour,
    rollup_to_minute,
)

logger = logging.getLogger(__name__)


# Retention window for exited rows in ``resource_pid_map``.
# 7 days keeps a useful forensic window for "why did this process
# exit?" debugging without unbounded disk growth. Live rows
# (``exited_at IS NULL``) are NEVER swept — the registry owns them.
# Constant lives here (not in :class:`ResourceMonitorConfig`)
# because pid-map history is a forensic lane, not an operator knob.
_PID_MAP_HISTORY_DAYS = 7


# Bounded drop-oldest buffer for the in-flight rows between
# ``drain()`` and the next ``executemany``. The buffer is bytes-
# shaped (a list of dicts); the cap is rows, not bytes, because
# each row is a small dict (≤ ~250 B per the §2.5 budget).
FLUSH_BUFFER_CAP = 512


# ── Flusher counters (read by /self-cost + storage tests) ───────────────────


@dataclass
class FlusherCounters:
    """Per-flush-window counters. Updated at the END of every window.

    The /self-cost endpoint reads ``flush.batches`` / ``flush.bytes``
    / ``flush.errors`` directly off this dataclass.
    """

    batches: int = 0
    bytes_written: int = 0
    errors: int = 0
    last_window_end: Optional[float] = None
    last_window_rows: int = 0


# ── Flusher ─────────────────────────────────────────────────────────────────


class Flusher:
    """ONE flusher thread per process.

    Lifecycle:

      * ``start()`` — spawn the thread (daemon=True).
      * ``stop()`` — set the stop Event; the thread exits at the
        next flush boundary. Join happens at the lifespan level
        (T7) on a detached thread, NEVER on the loop.

    The flusher is test-friendly: ``run_once()`` advances the
    flusher by ONE flush window WITHOUT spawning the thread. T9 /
    T10 drive flushes via ``run_once()`` to keep the test
    hermetic.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        raw_ring: TierRing,
        flush_batch_seconds: int,
        retention: RetentionPolicy,
        flusher_counters: Optional[FlusherCounters] = None,
        pid_registry: Optional[PidRegistry] = None,
        wall_clock: Callable[[], float] = time.monotonic,
        now_utc_naive: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.engine = engine
        self.raw_ring = raw_ring
        self.flush_batch_seconds = flush_batch_seconds
        self.retention = retention
        self.counters = flusher_counters or FlusherCounters()
        # D4 — pid_registry hookup. Default = the process singleton;
        # tests inject a fresh registry via ``reset_pid_registry_for_tests``
        # or pass one explicitly. The flusher DRAINS the queue
        # (moves ownership of the entries to its INSERT batch);
        # callers MUST NOT keep references past the flush.
        self._pid_registry = pid_registry or get_pid_registry()
        self._wall_clock = wall_clock  # injectable for tests
        self._now_utc_naive = now_utc_naive or _default_now_utc_naive

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Bounded drop-oldest buffer: rows popped off the raw ring
        # that haven't been written yet. Holds at most FLUSH_BUFFER_CAP.
        self._buffer: list[dict] = []
        # F3: set after the FIRST missing-table write degradation has
        # been logged — later windows stay silent (one clear line ties
        # the degrade to its cause; file_change_monitor precedent).
        self._missing_table_warned = False

    # ── Public lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="resource-monitor-flusher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Single-window driver ──────────────────────────────────────────

    def run_once(self) -> dict:
        """Run ONE flush window.

        Returns a small dict summarizing the window: ``rows_written``,
        ``minute_rows``, ``hour_rows``, ``swept``.

        Each window executes EXACTLY ONE ``engine.begin()`` block
        that contains:

          * The bounded-buffer ``executemany`` for raw rows.
          * The minute roll-up INSERT.
          * The hour roll-up INSERT.
          * The retention sweep DELETEs.

        Tests (AC8) count BEGIN/COMMIT via session instrumentation
        to assert exactly one transaction per window.
        """
        # Drain the raw ring into the bounded buffer (drop-oldest
        # if the buffer is over capacity).
        drained = self.raw_ring.drain()
        for row in drained:
            # Stamp the bucket_ts on entry — the sampler writes the
            # row without a timestamp; the flusher stamps it on
            # admission. Naive-UTC (D9).
            row = dict(row)
            row.setdefault("bucket_ts", self._now_utc_naive().strftime(
                "%Y-%m-%d %H:%M:%S"
            ))
            self._buffer.append(row)
        # Drop-oldest if the in-flight buffer over the cap.
        if len(self._buffer) > FLUSH_BUFFER_CAP:
            dropped = len(self._buffer) - FLUSH_BUFFER_CAP
            self._buffer = self._buffer[-FLUSH_BUFFER_CAP:]
            logger.warning(
                "resource-monitor flusher buffer overflow: dropped %d "
                "oldest rows (cap=%d)",
                dropped,
                FLUSH_BUFFER_CAP,
            )

        # Compute retention cutoffs (epoch seconds) for the sweep.
        # The sweep runs on every window — even when the buffer is
        # empty (retention is independent of new writes).
        now_epoch = int(self._now_utc_naive().replace(
            tzinfo=timezone.utc
        ).timestamp())
        raw_cut, min_cut, hr_cut = retention_cutoffs(
            now_epoch_s=now_epoch, policy=self.retention
        )

        rows_written = 0
        swept = {"raw": 0, "minute": 0, "hour": 0, "instance": 0,
                 "pid_map": 0}
        minute_rows: list[dict] = []
        hour_rows: list[dict] = []
        # D4 — drained pid-map history (one read per window; the
        # registry queues the rows in memory between ticks so we
        # can batch them here).
        pid_history: list[PidRegistryEntry] = []
        try:
            with self.engine.begin() as conn:
                # 1. Raw tier — ONE executemany for the bounded buffer.
                if self._buffer:
                    # Roll-up is only meaningful when there are rows
                    # to roll. An empty buffer skips the writes (no
                    # transaction content) but the sweep still runs.
                    minute_rows = rollup_to_minute(self._buffer)
                    hour_rows = rollup_to_hour(minute_rows)
                    rows_written += self._write_raw(conn, self._buffer)
                    # 2. Minute tier — INSERT the roll-up rows.
                    if minute_rows:
                        rows_written += self._write_minute(conn, minute_rows)
                    # 3. Hour tier — INSERT the roll-up rows.
                    if hour_rows:
                        rows_written += self._write_hour(conn, hour_rows)
                # 4. Retention sweep — DELETE rows older than the tier
                # cutoffs. The three tiers are STACKED in ONE table
                # (deviation #1: resource_daemon_bucket discriminated
                # by interval_s — 30 raw / 60 minute / 3600 hour), so
                # each DELETE carries its interval_s predicate: a
                # tier-blind raw sweep would erase minute (14 d) and
                # hour (180 d) rows (review F1). T2 instance rows are
                # raw-only (no roll-down) — swept at the raw cutoff
                # with no interval discrimination (§2.5 raw tier).
                swept["raw"] = self._sweep(
                    conn, "resource_daemon_bucket", raw_cut, interval_s=30
                )
                swept["minute"] = self._sweep(
                    conn, "resource_daemon_bucket", min_cut, interval_s=60
                )
                swept["hour"] = self._sweep(
                    conn, "resource_daemon_bucket", hr_cut, interval_s=3600
                )
                swept["instance"] = self._sweep(
                    conn, "resource_instance_sample", raw_cut
                )
                # 5. D4 — pid_map history write + sweep (additive,
                # rides the SAME transaction; resource_pid_map has
                # no UNIQUE constraint on the non-tier row shape so
                # _write_pid_map uses ON CONFLICT DO NOTHING as a
                # safe idempotent no-op — see _dialect_insert). The
                # sweep uses the SAME per-tier pattern but for the
                # ``exited_at IS NOT NULL`` predicate: live rows
                # (``exited_at IS NULL``) are NEVER touched.
                pid_history = self._pid_registry.drain_history()
                rows_written += self._write_pid_map(conn, pid_history)
                pid_map_cut_epoch = now_epoch - (_PID_MAP_HISTORY_DAYS * 86400)
                swept["pid_map"] = self._sweep(
                    conn,
                    "resource_pid_map",
                    pid_map_cut_epoch,
                    extra_predicate="exited_at IS NOT NULL",
                    ts_column="exited_at",
                )

            self.counters.batches += 1
            self.counters.bytes_written += sum(
                len(str(r)) for r in self._buffer
            )
            self.counters.last_window_end = self._wall_clock()
            self.counters.last_window_rows = rows_written
        except Exception as exc:
            self.counters.errors += 1
            logger.error(
                "resource-monitor flusher window failed: %s", exc, exc_info=True
            )
            raise
        finally:
            self._buffer.clear()

        return {
            "rows_written": rows_written,
            "minute_rows": len(minute_rows),
            "hour_rows": len(hour_rows),
            "swept": swept,
        }

    # ── SQL helpers (the four operations in the single window) ────────

    def _write_raw(self, conn: Any, rows: list[dict]) -> int:
        """Bulk-insert raw rows. P1: only the T1 ``resource_daemon_bucket``
        table (the sampler ring is daemon-side; T2 instance rows are
        deferred to P2 per the phase-boundary choice).
        """
        params = [
            {
                "bucket_ts": r["bucket_ts"],
                "interval_s": r.get("interval_s"),
                "rss_kb": r.get("rss_kb"),
                "pss_kb": r.get("pss_kb"),
                "anon_kb": r.get("anon_kb"),
                "swap_kb": r.get("swap_kb"),
                "vsize_kb": r.get("vsize_kb"),
                "hw_peak_kb": r.get("hw_peak_kb"),
                "threads": r.get("threads"),
                "fds": r.get("fds"),
                "cpu_user_usec": r.get("cpu_user_usec"),
                "cpu_sys_usec": r.get("cpu_sys_usec"),
                "cg_current_kb": r.get("cg_current_mb", 0) * 1024
                    if r.get("cg_current_mb") is not None else None,
                "cg_anon_kb": r.get("cg_anon_mb", 0) * 1024
                    if r.get("cg_anon_mb") is not None else None,
                "cg_file_kb": r.get("cg_file_mb", 0) * 1024
                    if r.get("cg_file_mb") is not None else None,
                "cg_slab_kb": r.get("cg_slab_mb", 0) * 1024
                    if r.get("cg_slab_mb") is not None else None,
                "cg_peak_kb": r.get("cg_peak_mb", 0) * 1024
                    if r.get("cg_peak_mb") is not None else None,
                "child_rss_total_kb": r.get("child_rss_total_kb"),
                "attributed_bytes": r.get("attributed_bytes"),
                "unattributed_kb": r.get("unattributed_kb"),
                "coverage_pct": r.get("coverage_pct"),
                "sample_cost_ms": r.get("sample_cost_ms"),
                "sampler_lag_ms": r.get("sampler_lag_ms"),
                "missed_samples": r.get("missed_samples"),
            }
            for r in rows
        ]
        # Use INSERT ... ON CONFLICT DO NOTHING on PG so the
        # UNIQUE(bucket_ts) constraint makes the writes
        # idempotent across retries. SQLite uses INSERT OR IGNORE.
        sql = self._dialect_insert(
            "resource_daemon_bucket",
            [
                "bucket_ts", "interval_s", "rss_kb", "pss_kb", "anon_kb",
                "swap_kb", "vsize_kb", "hw_peak_kb", "threads", "fds",
                "cpu_user_usec", "cpu_sys_usec", "cg_current_kb",
                "cg_anon_kb", "cg_file_kb", "cg_slab_kb", "cg_peak_kb",
                "child_rss_total_kb", "attributed_bytes",
                "unattributed_kb", "coverage_pct", "sample_cost_ms",
                "sampler_lag_ms", "missed_samples",
            ],
            idempotent=True,
        )
        return self._tier_write(conn, sql, params)

    def _write_minute(self, conn: Any, rows: list[dict]) -> int:
        """Roll-up rows go to a separate roll-up table OR the same T1
        table with a marker. P1 stores minute rows in
        ``resource_daemon_bucket`` with ``interval_s=60`` and a
        raw_count helper — keeps the table count to the spec 4.
        """
        params = [
            {
                "bucket_ts": r["bucket_ts"],
                "interval_s": 60,
                "rss_kb": r.get("rss_kb"),
                "cpu_user_usec": r.get("cpu_user_usec"),
                "cpu_sys_usec": r.get("cpu_sys_usec"),
                "missed_samples": r.get("missed_samples", 0),
                "anon_kb": r.get("anon_kb"),
                "threads": r.get("threads"),
                "fds": r.get("fds"),
                "sample_cost_ms": r.get("sample_cost_ms"),
                "attributed_bytes": r.get("attributed_bytes"),
                "unattributed_kb": r.get("unattributed_kb"),
                "coverage_pct": r.get("coverage_pct"),
            }
            for r in rows
        ]
        sql = self._dialect_insert(
            "resource_daemon_bucket",
            [
                "bucket_ts", "interval_s", "rss_kb", "cpu_user_usec",
                "cpu_sys_usec", "missed_samples", "anon_kb", "threads",
                "fds", "sample_cost_ms", "attributed_bytes",
                "unattributed_kb", "coverage_pct",
            ],
            idempotent=True,
        )
        return self._tier_write(conn, sql, params)

    def _write_hour(self, conn: Any, rows: list[dict]) -> int:
        params = [
            {
                "bucket_ts": r["bucket_ts"],
                "interval_s": 3600,
                "rss_kb": r.get("rss_kb"),
                "cpu_user_usec": r.get("cpu_user_usec"),
                "cpu_sys_usec": r.get("cpu_sys_usec"),
                "missed_samples": r.get("missed_samples", 0),
                "anon_kb": r.get("anon_kb"),
                "threads": r.get("threads"),
                "fds": r.get("fds"),
                "sample_cost_ms": r.get("sample_cost_ms"),
                "attributed_bytes": r.get("attributed_bytes"),
                "unattributed_kb": r.get("unattributed_kb"),
                "coverage_pct": r.get("coverage_pct"),
            }
            for r in rows
        ]
        sql = self._dialect_insert(
            "resource_daemon_bucket",
            [
                "bucket_ts", "interval_s", "rss_kb", "cpu_user_usec",
                "cpu_sys_usec", "missed_samples", "anon_kb", "threads",
                "fds", "sample_cost_ms", "attributed_bytes",
                "unattributed_kb", "coverage_pct",
            ],
            idempotent=True,
        )
        return self._tier_write(conn, sql, params)

    def _sweep(
        self,
        conn: Any,
        table: str,
        cutoff_epoch_s: int,
        interval_s: Optional[int] = None,
        extra_predicate: Optional[str] = None,
        ts_column: str = "bucket_ts",
    ) -> int:
        """DELETE rows strictly older than the cutoff. Returns rowcount.

        ``interval_s`` restricts the DELETE to ONE stacked tier
        (deviation #1: raw=30 / minute=60 / hour=3600 share the
        ``resource_daemon_bucket`` table) — without it a lower tier's
        cutoff would erase higher-tier rows (review F1). ``None``
        sweeps the whole table (raw-only tables such as T2
        ``resource_instance_sample``).

        ``extra_predicate`` is AND-ed onto the WHERE for tables that
        need a structural guard beyond the timestamp cutoff. The
        pid-map sweep uses ``exited_at IS NOT NULL`` so live rows
        (which the registry owns in RAM) are NEVER touched — the
        partial UNIQUE ``WHERE exited_at IS NULL`` only applies to
        INSERTs, not DELETEs, so this predicate is required.

        ``ts_column`` selects the timestamp column the cutoff
        compares against (default ``bucket_ts``; the pid-map table
        uses ``exited_at`` — its only time column).

        Missing table → 0 (a fresh boot has no rows to sweep; a
        ``no such table`` / ``relation does not exist`` error is a
        benign first-boot state, never a flusher failure).
        """
        where = f"{ts_column} < :cutoff_iso"
        params: dict = {"cutoff_iso": cutoff_iso(cutoff_epoch_s)}
        if interval_s is not None:
            where += " AND interval_s = :interval_s"
            params["interval_s"] = interval_s
        if extra_predicate:
            where += f" AND {extra_predicate}"
        try:
            result = conn.execute(
                text(f"DELETE FROM {table} WHERE {where}"),
                params,
            )
        except Exception as exc:
            err = str(exc).lower()
            if "no such table" in err or "does not exist" in err:
                logger.debug(
                    "resource-monitor sweep: table %s absent (first boot "
                    "before create_all) — skipping",
                    table,
                )
                return 0
            raise
        return int(result.rowcount or 0)

    def _write_pid_map(
        self, conn: Any, entries: list[PidRegistryEntry]
    ) -> int:
        """Bulk-insert exited pid_map rows (D4 — batched executemany).

        One INSERT per window for every exited entry the registry
        drained this tick. No-op when the list is empty (the sweep
        still runs). Missing table ⇒ 0 (first-boot benign).

        D14 alignment: the entry payload carries scalars/ids only.
        ``command`` was sanitized at the seam caller (executable /
        argv-shape, never raw env/secret-bearing text). The
        flusher does NOT redact.
        """
        if not entries:
            return 0
        params = [
            {
                "pid": e.pid,
                "pgid": e.pgid,
                "start_time": e.start_time,
                "instance_id": e.instance_id,
                "agent_id": e.agent_id,
                "kind": e.kind,
                "command": e.command,
                "detail": e.detail,
                "parent_pid": e.parent_pid,
                "spawned_at": e.spawned_at,
                "exited_at": e.exited_at,
                "exit_code": e.exit_code,
                "exit_kind": e.exit_kind or EXIT_KIND_UNKNOWN,
                "peak_rss_kb": e.peak_rss_kb,
                "cpu_user_usec": e.cpu_user_usec,
            }
            for e in entries
        ]
        sql = self._dialect_insert(
            "resource_pid_map",
            [
                "pid", "pgid", "start_time", "instance_id", "agent_id",
                "kind", "command", "detail", "parent_pid", "spawned_at",
                "exited_at", "exit_code", "exit_kind", "peak_rss_kb",
                "cpu_user_usec",
            ],
            idempotent=True,
        )
        return self._tier_write(conn, sql, params)

    def _tier_write(self, conn: Any, sql: str, params: list[dict]) -> int:
        """Execute one tier INSERT with first-flush missing-table
        tolerance (review F3 — the tolerance ``_sweep`` already had,
        extended to the write paths).

        Missing table → 0 + ONE warning (the first degradation logs
        the cause — the schema does not exist yet; create_all /
        migrations run later in the boot — file_change_monitor
        precedent). Any other error propagates.
        """
        try:
            conn.execute(text(sql), params)
        except Exception as exc:
            err = str(exc).lower()
            if "no such table" in err or "does not exist" in err:
                if not self._missing_table_warned:
                    self._missing_table_warned = True
                    logger.warning(
                        "resource-monitor flusher degraded: tier tables "
                        "absent on first flush (schema not created yet) — "
                        "writes are skipped until create_all runs "
                        "(resource_daemon_bucket missing)"
                    )
                return 0
            raise
        return len(params)

    def _dialect_insert(
        self, table: str, columns: list[str], *, idempotent: bool
    ) -> str:
        """Build a dialect-aware INSERT (idempotent OR fail-on-conflict).

        SQLite: ``INSERT OR IGNORE INTO ... VALUES ...`` when
        idempotent, plain ``INSERT INTO ...`` otherwise.
        PostgreSQL: ``INSERT ... ON CONFLICT (...) DO NOTHING`` when
        idempotent, plain ``INSERT INTO ...`` otherwise.

        The compound unique target ``(bucket_ts, interval_s)`` is
        the idempotency key for tier-table writes (T1 / rollup).
        For other tables (T3 / T4) the conflict target is omitted
        because those tables have no UNIQUE constraint.
        """
        url = str(self.engine.url)
        is_sqlite = "sqlite" in url
        cols = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        if not idempotent:
            return (
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
            )
        # Tier tables use the compound (bucket_ts, interval_s)
        # unique key; other tables (pid_map / limit_policy) have
        # no unique constraint, so ON CONFLICT DO NOTHING is a
        # safe idempotent no-op (no conflict possible).
        if "bucket" in table or "instance_sample" in table:
            conflict_target = "(bucket_ts, interval_s)"
        else:
            conflict_target = "(_NONEXISTENT)"
        if is_sqlite:
            return (
                f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
            )
        return (
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT {conflict_target} DO NOTHING"
        )

    # ── Loop (background thread) ──────────────────────────────────────

    def _run_loop(self) -> None:
        """The thread body — one flush window per interval."""
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                # ``run_once`` already logged + counted the error.
                logger.debug(
                    "resource-monitor flusher loop caught: %s", exc
                )
            interval = self.flush_batch_seconds
            slept = 0.0
            while slept < interval:
                if self._stop_event.wait(timeout=0.1):
                    return
                slept += 0.1


# ── Helpers ─────────────────────────────────────────────────────────────────


def _default_now_utc_naive() -> datetime:
    """Return the current instant as a NAIVE ``datetime`` holding UTC digits.

    Mirrors ``daemon.services.timestamps.now_utc_naive``; importing
    it here would form a daemon.services.timestamps ↔ daemon
    .services.resource_monitor cycle on cold load (mirrors the
    service_tool models A12 deferred-import pattern).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = [
    "FLUSH_BUFFER_CAP",
    "FlusherCounters",
    "Flusher",
]