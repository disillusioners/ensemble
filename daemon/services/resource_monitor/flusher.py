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

P4 addition: an optional ``anomaly_gate`` callable runs AFTER the
transaction — the anomaly pass piggybacks THIS thread behind its own
monotonic-interval gate (:mod:`daemon.services.resource_anomaly`),
with its own skip-on-overrun budget. The gate never delays sampling
or flushing and never raises into the flusher; ``None`` (default) =
P1/P2/P3 behavior unchanged.

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


def is_missing_relation_error(exc: BaseException) -> bool:
    """True when ``exc`` is a RELATION-missing error (first-boot benign).

    Hardening (P2 review C-1): the tolerance matches ONLY the
    "table/relation does not exist" class —

      * SQLite: ``no such table: <name>`` (OperationalError);
      * PostgreSQL: ``relation "<name>" does not exist``
        (SQLSTATE 42P01 / UndefinedTable);
      * SQLAlchemy/DBAPI class name ``UndefinedTable`` (dialect
        portability belt).

    The bare ``"does not exist"`` substring must NOT be matched
    on its own — PostgreSQL column errors (SQLSTATE 42703,
    ``column "x" of relation "y" does not exist``) carry the same
    phrase AND name the word ``relation``, and swallowing those
    turned a real grammar bug into silent data loss (the
    ``_NONEXISTENT`` ON CONFLICT incident). The match therefore
    requires the message to NOT be a column error (42703 always
    names ``column``); column errors propagate from every
    tolerance site that uses this helper.
    """
    err = str(exc).lower()
    if "no such table" in err:  # SQLite dialect
        return True
    if (
        "does not exist" in err
        and "column" not in err
        and "no such column" not in err
    ):
        # PG 42P01: relation "x" does not exist.
        return True
    return type(exc).__name__.lower() == "undefinedtable"


def _extract_sqlstate(exc: BaseException) -> Optional[str]:
    """Best-effort SQLSTATE extraction for diagnostic logging.

    Postgres errors expose ``.diag.sqlstate`` (psycopg3) or
    ``.pgcode`` (psycopg2) or ``.sqlstate`` (DBAPI fallback). On
    non-Postgres dialects the return is ``None`` — the diagnostic
    log line falls back to the exception class name. The helper
    never raises; a nested-dive failure returns ``None`` so the
    flusher never escalates a logging error into a write error (the
    defensive catch logs the exception TYPE at DEBUG so a
    persistently broken dialect stays diagnosable).
    """
    try:
        orig = getattr(exc, "orig", None)
        if orig is not None:
            diag = getattr(orig, "diag", None)
            if diag is not None:
                code = getattr(diag, "sqlstate", None)
                if code:
                    return str(code)
            code = getattr(orig, "pgcode", None)
            if code:
                return str(code)
            code = getattr(orig, "sqlstate", None)
            if code:
                return str(code)
        # Final fallback — the DBAPI-level attribute. Normalize to
        # ``str`` here too: a non-string code (e.g. an int) would
        # otherwise reach the log's ``%s`` as-is while every other
        # branch returns a string, so the same SQLSTATE could be
        # rendered two different ways across branches.
        code = getattr(exc, "sqlstate", None)
        return str(code) if code else None
    except Exception as catch_exc:  # pragma: no cover - defensive
        logger.debug(
            "resource-monitor flusher: SQLSTATE extraction raised %s: %s",
            type(catch_exc).__name__,
            str(catch_exc)[:200],
        )
        return None


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
        instance_ring: Optional[TierRing] = None,
        anomaly_gate: Optional[
            Callable[[list[dict], list[dict]], None]
        ] = None,
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
        # P2 T10 — per-instance rows (``resource_instance_sample``).
        # Optional; when absent the flusher skips the T2 write (the
        # P1 behavior). Drained inside the SAME engine.begin() window.
        self._instance_ring = instance_ring
        self._wall_clock = wall_clock  # injectable for tests
        self._now_utc_naive = now_utc_naive or _default_now_utc_naive

        # P4 — the anomaly pass piggybacks THIS thread (§2.1: ONE
        # sampler + ONE flusher; no new thread). The gate is a
        # callable ``(minute_rows, instance_rows) -> None`` built by
        # the facade (``AnomalyScheduler``): it ingests every window
        # and fires the detection pass behind its monotonic-interval
        # gate with its own skip-on-overrun budget. The flusher only
        # hands it the rows it already computed and guards against
        # gate failure — sampling and flushing are never delayed by
        # the pass. ``None`` (the default) = anomaly layer absent
        # (P1/P2/P3 behavior, byte-identical run_once semantics).
        self._anomaly_gate = anomaly_gate

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
        instance_rows_written = 0
        # P4 — the drained instance rows are kept (boundedly — replaced
        # every window, ≤ instance_cap+1 small dicts) so the anomaly
        # gate AFTER the transaction can fold them into its bucketed
        # store. Same for the minute roll-up rows already computed.
        instance_rows: list[dict] = []
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
                # rides the SAME transaction). P2 review C-1:
                # _write_pid_map is a PLAIN INSERT on both dialects —
                # the partial UNIQUE (WHERE exited_at IS NULL) cannot
                # serve as a column-list ON CONFLICT target, and
                # drain-once semantics make an upsert unnecessary
                # (see _write_pid_map / _dialect_insert). The
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
                # 6. P2 T10 — per-instance tier write (additive, same
                # window): drain the sampler's instance ring and
                # INSERT into resource_instance_sample. Raw-tier only
                # (no roll-down; swept below at the raw cutoff).
                if self._instance_ring is not None:
                    instance_rows = self._instance_ring.drain()
                    for row in instance_rows:
                        row.setdefault(
                            "bucket_ts",
                            self._now_utc_naive().strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        )
                    instance_rows_written = self._write_instance_rows(
                        conn, instance_rows
                    )
                    rows_written += instance_rows_written

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

        # P4 — anomaly gate (AFTER the transaction; the pass never
        # holds the DB window open). Never raises into the flusher: a
        # gate failure is logged and the window result is unaffected.
        if self._anomaly_gate is not None:
            try:
                self._anomaly_gate(minute_rows, instance_rows)
            except Exception as exc:
                logger.warning(
                    "resource-monitor anomaly gate failed: %s", exc
                )

        return {
            "rows_written": rows_written,
            "instance_rows": instance_rows_written,
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
        RELATION-missing error (``no such table`` / ``relation
        does not exist`` / UndefinedTable) is a benign first-boot
        state, never a flusher failure — see
        :func:`is_missing_relation_error` for the narrowed match.
        C-1 hardening: that HELPER is a CLASSIFIER, not the swallow
        policy — it does not classify a column error (42703) as
        relation-missing, so a column error takes the NON-tolerated
        branch below, which the amplifier hardening then ABSORBS
        (logged + savepoint rollback + return 0) rather than
        re-raising.

        Final-gate FINDING #2: like ``_tier_write``, the DELETE
        runs inside a SAVEPOINT — on PostgreSQL a tolerated 42P01
        would otherwise abort the surrounding transaction and the
        NEXT statement in the same window would die with 25P02
        (``InFailedSqlTransaction``); the savepoint rollback
        clears the aborted state so the window degrades to 0 and
        continues instead of raising.

        Amplifier hardening (2026-10-19): per the ``_tier_write``
        contract, a non-missing-relation error here ALSO rolls
        back the savepoint and returns 0 (with a logged error)
        instead of re-raising — the four retention sweeps run
        inside the same ``engine.begin()`` window as the tier
        writes and must not abort the window on a non-fatal
        error.
        """
        where = f"{ts_column} < :cutoff_iso"
        params: dict = {"cutoff_iso": cutoff_iso(cutoff_epoch_s)}
        if interval_s is not None:
            where += " AND interval_s = :interval_s"
            params["interval_s"] = interval_s
        if extra_predicate:
            where += f" AND {extra_predicate}"
        nested = conn.begin_nested()
        try:
            result = conn.execute(
                text(f"DELETE FROM {table} WHERE {where}"),
                params,
            )
        except Exception as exc:
            nested.rollback()
            if is_missing_relation_error(exc):
                logger.debug(
                    "resource-monitor sweep: table %s absent (first boot "
                    "before create_all) — skipping",
                    table,
                )
                return 0
            sqlstate = _extract_sqlstate(exc)
            logger.error(
                "resource-monitor flusher sweep FAILED on %s — "
                "savepoint rolled back, window continues "
                "(sqlstate=%s, exc=%s: %s)",
                table,
                sqlstate or "-",
                type(exc).__name__,
                str(exc)[:200],
            )
            # MUST surface on the ``flush.errors`` metric. The
            # swallow path keeps the window alive, so the window's
            # own ``except`` (which is the only OTHER place that
            # increments the counter) never fires — without this
            # increment a dead sweep path reports ``flush.errors =
            # 0`` while ``flush.batches`` climbs, which is the same
            # silent-failure blindness the savepoint hardening was
            # meant to remove, now on the metric axis
            # (``/self-cost`` reads the counter directly).
            self.counters.errors += 1
            return 0
        nested.commit()
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

        Dialect note (disclosed, P2 review C-1): ``idempotent=False``
        — a PLAIN INSERT on BOTH dialects. The pid_map's only unique
        index is the PARTIAL ``uq_resource_pid_map_pid_active …
        WHERE exited_at IS NULL`` — a partial index cannot serve as
        a column-list ``ON CONFLICT`` target on PostgreSQL (the
        attempted ``ON CONFLICT (_NONEXISTENT)`` grammar raised
        42703 on every exited-pid flush). Drain-once semantics make
        an upsert unnecessary anyway: ``drain_history`` pops each
        exited entry exactly once (the same rationale as the T2
        write below), so a duplicate INSERT cannot recur within a
        process lifetime.
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
            idempotent=False,
        )
        return self._tier_write(conn, sql, params)

    def _write_instance_rows(
        self, conn: Any, rows: list[dict]
    ) -> int:
        """Bulk-insert P2 T2 ``resource_instance_sample`` rows.

        Additive tier-write (P2 T10): the sampler's instance ring is
        drained here and written in ONE executemany inside the same
        ``engine.begin()`` window as T1. No-op when the list is empty;
        missing table ⇒ 0 via the shared ``_tier_write`` tolerance.

        D14: the row payload carries ids/counters/durations ONLY —
        the sampler's ``ALLOWED_T2_ROW_KEYS`` guard enforces the
        shape upstream; this INSERT projects exactly the T2 columns.

        Dialect note (disclosed): ``idempotent=False`` — a PLAIN
        INSERT. T2 has NO unique constraint (its §2.4 indexes are
        non-unique), so a conflict-target INSERT would be invalid on
        PostgreSQL; duplicate protection comes from the drain
        (rows leave the ring exactly once).
        """
        if not rows:
            return 0
        params = [
            {
                "bucket_ts": r.get("bucket_ts"),
                "instance_id": r.get("instance_id"),
                "agent_id": r.get("agent_id"),
                "status": r.get("status"),
                "scan_kind": r.get("scan_kind"),
                "charged_bytes": r.get("charged_bytes"),
                "deep_bytes": r.get("deep_bytes"),
                "graph_cache_present": r.get("graph_cache_present"),
                "in_flight_tasks": r.get("in_flight_tasks"),
                "all_state_messages_len": r.get("all_state_messages_len"),
                "child_rss_kb": r.get("child_rss_kb"),
                "child_cpu_user_usec": r.get("child_cpu_user_usec"),
                "cpu_share_usec_est": r.get("cpu_share_usec_est"),
                "estimated": bool(r.get("estimated", True)),
            }
            for r in rows
        ]
        sql = self._dialect_insert(
            "resource_instance_sample",
            [
                "bucket_ts", "instance_id", "agent_id", "status",
                "scan_kind", "charged_bytes", "deep_bytes",
                "graph_cache_present", "in_flight_tasks",
                "all_state_messages_len", "child_rss_kb",
                "child_cpu_user_usec", "cpu_share_usec_est", "estimated",
            ],
            idempotent=False,
        )
        return self._tier_write(conn, sql, params)

    def _tier_write(self, conn: Any, sql: str, params: list[dict]) -> int:
        """Execute one tier INSERT with first-flush missing-table
        tolerance (review F3 — the tolerance ``_sweep`` already had,
        extended to the write paths).

        Missing table → 0 + ONE warning (the first degradation logs
        the cause — the schema does not exist yet; create_all /
        migrations run later in the boot — file_change_monitor
        precedent). Any other error ALSO rolls back the per-tier
        SAVEPOINT and logs distinctly, but NEVER escapes the flush
        window (amplifier hardening, 2026-10-19). The triggering
        incident was a ``DatatypeMismatch`` (SQLSTATE 42804: column
        "estimated" is INTEGER but the bound expression is boolean):
        a single tier's column-type error raised out of
        ``_tier_write`` and aborted the shared ``engine.begin()``
        window, rolling back every other tier's writes as collateral.
        Per-tier savepoints now isolate the failure and the function
        returns 0 so the next tier can still write.

        C-1 hardening: "missing" matches the RELATION-missing class
        ONLY (:func:`is_missing_relation_error`) — a PostgreSQL
        column error (42703, ``column ... does not exist``) is a
        real grammar bug and is NOT lumped in with first-boot noise:
        it takes the non-tolerated branch, whose ERROR line names
        the SQLSTATE and the exception class (``exc=``), so a
        regression stays distinguishable from first-boot noise in
        the log.

        Final-gate FINDING #2 (PG txn-abort): on PostgreSQL a
        failed statement ABORTS the surrounding transaction
        (SQLSTATE 25P02 on the NEXT statement,
        ``InFailedSqlTransaction``) — so the bare tolerance above
        caught the 42P01 but the same-window sweep then died with
        25P02 (NOT missing-relation class, correctly NOT
        swallowed) and the whole window RAISED instead of
        degrading to 0. The statement therefore runs inside a
        SAVEPOINT (:meth:`Connection.begin_nested`): a tolerated
        error rolls back ONLY the savepoint (which clears the
        aborted state on PG) and the window continues; a
        non-tolerated error ALSO rolls back the savepoint and the
        function returns 0 — the next tier's savepoint starts on a
        clean transaction state because the outer ``engine.begin()``
        window never saw the failure. SQLite is unaffected either
        way (no mid-txn abort) — the savepoint is dialect-portable
        (both supported dialects emit SAVEPOINT / RELEASE
        natively).
        """
        nested = conn.begin_nested()
        try:
            conn.execute(text(sql), params)
        except Exception as exc:
            nested.rollback()
            if is_missing_relation_error(exc):
                if not self._missing_table_warned:
                    self._missing_table_warned = True
                    logger.warning(
                        "resource-monitor flusher degraded: tier tables "
                        "absent on first flush (schema not created yet) — "
                        "writes are skipped until create_all runs "
                        "(resource_daemon_bucket missing)"
                    )
                return 0
            # Amplifier hardening (2026-10-19): any non-missing-relation
            # error (column type mismatch, integrity violation,
            # constraint conflict, etc.) used to re-raise, escape the
            # outer ``engine.begin()`` window, and roll back every
            # OTHER tier's writes as collateral. Log + return 0 here
            # so the per-tier savepoint absorbs the failure and the
            # remaining tiers continue. The error is logged with the
            # SQLSTATE class so a regression is distinguishable from
            # first-boot noise.
            sqlstate = _extract_sqlstate(exc)
            logger.error(
                "resource-monitor flusher tier write FAILED — "
                "savepoint rolled back, other tiers continue "
                "(sqlstate=%s, exc=%s: %s). First 200 chars of SQL: %s",
                sqlstate or "-",
                type(exc).__name__,
                str(exc)[:200],
                sql[:200],
            )
            # MUST surface on the ``flush.errors`` metric. The
            # swallow path keeps the window alive, so the window's
            # own ``except`` (which is the only OTHER place that
            # increments the counter) never fires — without this
            # increment a dead tier write path reports
            # ``flush.errors = 0`` while ``flush.batches`` climbs,
            # which is the same silent-failure blindness the
            # savepoint hardening was meant to remove, now on the
            # metric axis (``/self-cost`` reads the counter
            # directly). The sibling tiers' rows still persist —
            # the failure is ISOLATED, not silent.
            self.counters.errors += 1
            return 0
        nested.commit()
        return len(params)

    def _dialect_insert(
        self, table: str, columns: list[str], *, idempotent: bool
    ) -> str:
        """Build a dialect-aware INSERT (idempotent OR fail-on-conflict).

        SQLite: ``INSERT OR IGNORE INTO ... VALUES ...`` when
        idempotent, plain ``INSERT INTO ...`` otherwise.
        PostgreSQL: ``INSERT ... ON CONFLICT (bucket_ts, interval_s)
        DO NOTHING`` when idempotent, plain ``INSERT INTO ...``
        otherwise.

        The compound unique target ``(bucket_ts, interval_s)`` is
        the idempotency key for tier-table writes (T1 / rollup).

        P2 review C-1: idempotent inserts are VALID ONLY for the
        stacked-tier bucket table. The former ``ON CONFLICT
        (_NONEXISTENT)`` fallback for other tables was invalid
        PostgreSQL grammar (42703 on every flush of an exited pid
        row — silently degraded to data loss by the then-bare
        "does not exist" tolerance). A pid_map / T2 insert is now
        ALWAYS a plain INSERT (drain-once semantics; the callers
        pass ``idempotent=False``), and any future ``idempotent=True``
        call against a table with no real compound target fails
        LOUD here at SQL-generation time instead of silently at
        flush time.
        """
        url = str(self.engine.url)
        is_sqlite = "sqlite" in url
        cols = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        if not idempotent:
            return (
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
            )
        if "bucket" not in table:
            raise ValueError(
                f"_dialect_insert: idempotent=True is only valid for the "
                f"stacked-tier bucket table (got {table!r}); tables without "
                f"the (bucket_ts, interval_s) unique key must use a plain "
                f"INSERT (idempotent=False — drain-once semantics, C-1)"
            )
        conflict_target = "(bucket_ts, interval_s)"
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
    "is_missing_relation_error",
]