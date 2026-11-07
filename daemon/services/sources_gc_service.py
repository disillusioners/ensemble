"""Sources-GC sweep — unused terminal scheduler schedules (source-gc, board wi-ed230eb4).

Periodic background service that reaps ``source_configs`` rows that are
disabled scheduler schedules past the terminal-retention window,
modeled on the proven ``TmpImageCleanupService`` family shape
(``__init__`` / ``start()`` / ``stop()`` / ``sweep_once()`` / ``_run()``:
first tick IMMEDIATE, ``_task``/``_stopping`` flags, idempotent start,
graceful cancel+await, defensive ``except Exception`` → WARNING +
continue).

**The fence lives at the REPO layer, not here.** The collectable
predicate — ``source_type == 'scheduler'`` (hardcoded, no widenable
parameter) ``AND enabled == False AND status IN ('stopped','cancelled')
AND age > retention_days`` — is enforced by
``SQLModelSourceRepository.gc_find_candidates`` /
``gc_archive_and_delete`` (phase4 §5). This service only drives ticks;
it can never widen the predicate. ``with_for_update`` + the in-txn
four-leg re-assert (AM-1) make the delete race-safe against a
concurrent resume.

**Deletion needs NO adapter stop/unregister (advisory E fact):** the
registry's boot filter already skips terminal rows
(``daemon/sources/registry.py:312-314`` — ``status in {stopped,
cancelled}`` never start an adapter), so a GC'd source can never have
a live adapter in the registry. There is nothing to stop, and no
unregister call is owed.

**Dry-run mode** (``dry_run=True``): every tick reports the candidate
set + counters and performs NO writes — the SP-1 promote recipe's
observe pass. Destructive mode logs only when there is something to
report (work, skips, or a deferred remainder); no-op ticks stay
SILENT (tmp-image log-noise discipline).

**Operator receipt:** the FIRST destructive sweep that collects rows
logs a one-time marker (``first destructive sweep performed at <iso>
(retention=<N>d, collected=<N>)``) — the operator's post-promote
acknowledgment that the GC is live and working. During the SP-1
promote recipe the operator watches for ``remainder=0 deferred`` as
the completion signal before restoring the retention knob to 14.

Recovery after a reap is MANUAL FROM THE ARCHIVE
(``schedule_executions_archive`` / ``source_configs_archive``); no
restore surface ships in v1 (D2 ratification condition 2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from daemon.services.timestamps import now_utc_iso

logger = logging.getLogger(__name__)


# Reap-log sample cap — bounded so a mass-collect tick (first sweep
# after the SP-1 promote recipe) cannot flood the log line with ids
# (house name per the tmp-image precedent,
# ``tmp_image_cleanup_service.py:84``).
_REAP_SAMPLE_IDS_MAX = 5

# Per-row reason lines cap for the dry-run receipt (§8.2 — "≤5 per-row
# reason lines").
_DRY_RUN_ROW_LINES_MAX = 5

# Companion probe limit used to size the deferred remainder. The
# binding ``gc_find_candidates`` signature returns only the tick's
# batch window, so the service re-queries with a large limit to count
# the full eligible set (dry-run: before the tick, for
# ``remainder = total - window``; destructive: after the tick, for the
# honest still-eligible count). Implementation detail recorded in the
# lane implementation-notes.
_TOTAL_PROBE_LIMIT = 100_000

_SECONDS_PER_DAY = 86400


def _schedule_shape(config: dict[str, Any]) -> str:
    """Derive the ``one-shot|cron`` display shape from config keys.

    ``run_at`` present → one-shot; otherwise cron (the writer emits
    ``run_at`` / ``schedule`` / ``interval_seconds`` — scheduler.py
    :511/:513; interval rows fall in the recurring bucket).
    """
    if isinstance(config, dict) and config.get("run_at"):
        return "one-shot"
    return "cron"


class SourcesGcService:
    """Periodic sweep that archives + deletes unused terminal scheduler rows.

    Args:
        source_repo: The ``SQLModelSourceRepository`` whose GC methods
            enforce the collectable predicate (the manager's
            ``_source_repository`` — the same accessor the sources
            router uses). The service never talks to the DB directly.
        enabled: Kill-switch (``SERVICES_SOURCES_GC_ENABLED``).
            ``False`` makes ``start()`` log the disabled line and
            schedule NO task — the panic-off posture for a
            terminalizing-write lane (the strand-heal
            counter-precedent class; the tmp-image HARD POLICY does
            not apply here because this sweep deletes CONFIG ROWS,
            not regenerable files).
        interval_seconds: Tick cadence. Floor 1; out-of-range config
            values FAIL FAST AT BOOT via ``Field(ge=1)``.
        retention_days: Terminal-retention window. Default 14. The
            SP-1 promote recipe temporarily sets 7 (floor 7 —
            resume-after-reap is a bare ``ValueError``;
            scheduling_service.py:989-991); very large = effectively
            disabled.
        batch_cap: Max rows collected per tick (oldest first;
            remainder defers to the next tick). Default 25.
        dry_run: Report-only mode — every tick logs the candidate
            set + counters and writes NOTHING. Shipped default
            ``False`` (destructive-on-promote per LEADER RULING 2);
            the one-boot live observe rides the SP-1 recipe.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent; first
          tick runs IMMEDIATELY (sweep before the first sleep).
        * ``stop()`` — cancel + await the asyncio task. Safe to call
          when the task was never started (silent no-op).
    """

    def __init__(
        self,
        source_repo: Any,
        *,
        interval_seconds: int,
        retention_days: int,
        batch_cap: int,
        dry_run: bool,
        enabled: bool,
    ) -> None:
        self._source_repo = source_repo
        self._enabled = bool(enabled)
        self._dry_run = bool(dry_run)
        self._interval_seconds = max(1, int(interval_seconds))
        self._retention_days = max(1, int(retention_days))
        self._batch_cap = max(1, int(batch_cap))
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        # One-time first-destructive receipt marker (§8.4). Fires on
        # the first destructive tick that COLLECTED rows — a no-op
        # first tick stays silent (§8.5) and does not consume it.
        self._first_destructive_logged: bool = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def interval_seconds(self) -> int:
        """Current sweep interval (seconds). Read-only."""
        return self._interval_seconds

    @property
    def retention_days(self) -> int:
        """Current retention window (days). Read-only."""
        return self._retention_days

    @property
    def batch_cap(self) -> int:
        """Current per-tick collection cap. Read-only."""
        return self._batch_cap

    @property
    def dry_run(self) -> bool:
        """Whether the sweep is in report-only mode. Read-only."""
        return self._dry_run

    # ------------------------------------------------------------------
    # Lifecycle — mirrors TmpImageCleanupService
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — a second call while a task is already running is
        a no-op. The FIRST tick runs immediately: ``_run`` calls
        ``sweep_once()`` BEFORE the first ``asyncio.sleep`` (same
        first-tick-immediate family shape as the tmp-image sweep).

        When ``enabled=False`` this logs the disabled line and
        schedules NO task — no sweeps will run (§8.1).
        """
        if not self._enabled:
            logger.info(
                "[SourcesGC] disabled "
                "(SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run"
            )
            return
        if self._task is not None and not self._task.done():
            logger.debug("SourcesGcService: start() while running — no-op")
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="SourcesGcService"
        )
        logger.info(
            f"[SourcesGC] started: enabled={self._enabled} "
            f"dry_run={self._dry_run} interval={self._interval_seconds}s "
            f"retention={self._retention_days}d "
            f"batch_cap={self._batch_cap}"
        )

    async def stop(self) -> None:
        """Cancel the sweep task and await its cancellation.

        Safe to call when the task was never started — silent no-op
        (matches the tmp-image shutdown shape).
        """
        if self._task is None:
            return
        self._stopping = True
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            # CancelledError is the normal shutdown path; Exception is
            # a defensive catch for an unexpected shutdown raise.
            pass
        logger.info("[SourcesGC] stopped")

    async def _run(self) -> None:
        """Periodic tick loop — exits on ``stop()`` cancellation.

        FIRST TICK IMMEDIATE: ``sweep_once()`` runs BEFORE the first
        ``asyncio.sleep``. ``asyncio.sleep`` raises
        ``CancelledError`` promptly on ``stop()``; the ``_stopping``
        flag is the defensive guard for the path between the sweep
        return and the next sleep.
        """
        try:
            while not self._stopping:
                await self.sweep_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            # Normal shutdown path via stop() — exit cleanly.
            return
        except Exception as loop_err:  # noqa: BLE001
            # Defensive: an unexpected loop error must not crash the
            # daemon. Log loudly and exit; a later start() re-spawns.
            logger.error(
                f"SourcesGcService: unexpected loop error: "
                f"{type(loop_err).__name__}: {loop_err} — exiting",
                exc_info=True,
            )
            return

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def sweep_once(self) -> dict[str, Any]:
        """Run a single GC tick. Returns the sweep report dict.

        Public deterministic seam so tests can exercise a single tick
        without spawning the asyncio task. ``asyncio.to_thread`` wraps
        BOTH repo calls (AM-10 — sync SQL on the event loop is the
        exact class v0.27.14 cured; precedent:
        ``scheduling_service.py:882/:1114``).

        Report keys (superset — §4 names the required three):
        ``ok`` · ``dry_run`` · ``candidates`` (ids) · ``collected`` ·
        ``archived_configs`` · ``archived_executions`` ·
        ``mappings`` · ``processed`` · ``skipped_unparseable`` ·
        ``observed_error_rows`` · ``remainder`` · ``duration_s`` ·
        ``sample`` · ``rows`` (per-source ``GcRowResult`` dicts) ·
        ``error`` (tick-level failure text, else ``None``).
        """
        sweep_started = time.monotonic()
        report: dict[str, Any] = {
            "ok": False,
            "dry_run": self._dry_run,
            "candidates": [],
            "collected": 0,
            "archived_configs": 0,
            "archived_executions": 0,
            "mappings": 0,
            "processed": 0,
            "skipped_unparseable": 0,
            "observed_error_rows": 0,
            "remainder": 0,
            "duration_s": 0.0,
            "sample": [],
            "rows": [],
            "error": None,
        }
        now = datetime.now(timezone.utc)
        try:
            candidates, skipped_unparseable = await asyncio.to_thread(
                self._source_repo.gc_find_candidates,
                retention_days=self._retention_days,
                now=now,
                limit=self._batch_cap,
            )
            observed_error_rows = await asyncio.to_thread(
                self._source_repo.gc_count_error_rows
            )
        except Exception as sweep_err:  # noqa: BLE001
            # Defensive: a tick-level repo failure must not crash the
            # sweep loop. WARNING + bail — the next tick will retry.
            report["error"] = f"{type(sweep_err).__name__}: {sweep_err}"
            logger.warning(
                f"[SourcesGC] sweep failed: "
                f"{type(sweep_err).__name__}: {sweep_err} — "
                "next tick will retry",
                exc_info=True,
            )
            report["duration_s"] = time.monotonic() - sweep_started
            return report

        candidate_ids = [c.source_id for c in candidates]
        report["candidates"] = candidate_ids
        report["skipped_unparseable"] = skipped_unparseable
        report["observed_error_rows"] = observed_error_rows

        if self._dry_run:
            # §8.2 — dry-run ticks ALWAYS log (counts + skip counters +
            # remainder + ≤5 per-row reason lines). NO writes here.
            total_probe, _ = await asyncio.to_thread(
                self._source_repo.gc_find_candidates,
                retention_days=self._retention_days,
                now=now,
                limit=_TOTAL_PROBE_LIMIT,
            )
            remainder = max(0, len(total_probe) - len(candidates))
            report["remainder"] = remainder
            logger.info(
                f"[SourcesGC] dry-run: candidates={len(candidates)} "
                f"skipped_unparseable={skipped_unparseable} "
                f"observed_error_rows={observed_error_rows} "
                f"remainder={remainder} deferred"
            )
            for candidate in candidates[:_DRY_RUN_ROW_LINES_MAX]:
                try:
                    config = json.loads(candidate.config_json or "{}")
                except (TypeError, ValueError):
                    config = {}
                logger.info(
                    f"[SourcesGC] dry-run row: "
                    f"({candidate.source_id}, {candidate.name}, "
                    f"{_schedule_shape(config)}, {candidate.status}, "
                    f"{candidate.age_days:.1f}d)"
                )
            report["ok"] = True
            report["duration_s"] = time.monotonic() - sweep_started
            return report

        # ---- Destructive path: per-source isolate, aggregate ----
        per_row_errors = 0
        for candidate in candidates:
            try:
                row_result = await asyncio.to_thread(
                    self._source_repo.gc_archive_and_delete,
                    source_id=candidate.source_id,
                    now=now,
                    retention_days=self._retention_days,
                    dry_run=False,
                )
            except Exception as row_err:  # noqa: BLE001
                # §8.6 — per-row defensive catch: WARNING
                # (source_id, exception-type) + continue. The per-source
                # transaction isolates the blast radius; the row is
                # retried next tick.
                per_row_errors += 1
                logger.warning(
                    f"[SourcesGC] row {candidate.source_id} failed: "
                    f"{type(row_err).__name__} — continuing"
                )
                continue
            report["rows"].append(
                {
                    "source_id": row_result.source_id,
                    "deleted": row_result.deleted,
                    "dry_run": row_result.dry_run,
                    "archived_config": row_result.archived_config,
                    "archived_executions": row_result.archived_executions,
                    "deleted_mappings": row_result.deleted_mappings,
                    "deleted_processed": row_result.deleted_processed,
                    "deleted_executions": row_result.deleted_executions,
                    "skipped_reason": row_result.skipped_reason,
                    "error": row_result.error,
                }
            )
            if row_result.deleted:
                report["collected"] += 1
                report["archived_configs"] += 1
                report["archived_executions"] += row_result.archived_executions
                report["mappings"] += row_result.deleted_mappings
                report["processed"] += row_result.deleted_processed
                if len(report["sample"]) < _REAP_SAMPLE_IDS_MAX:
                    report["sample"].append(row_result.source_id)

        # Honest deferred count: re-probe the eligible set AFTER the
        # tick's work (still-eligible rows include per-row failures and
        # raced skips — they are retried next tick).
        try:
            post_probe, _ = await asyncio.to_thread(
                self._source_repo.gc_find_candidates,
                retention_days=self._retention_days,
                now=now,
                limit=_TOTAL_PROBE_LIMIT,
            )
            report["remainder"] = len(post_probe)
        except Exception:  # noqa: BLE001
            # The probe is telemetry, not the work — a probe failure
            # must not fail the tick that already collected.
            report["remainder"] = -1

        work_done = (
            report["collected"] > 0
            or report["archived_executions"] > 0
            or report["mappings"] > 0
            or report["processed"] > 0
        )
        skips_seen = (
            skipped_unparseable > 0
            or per_row_errors > 0
            or any(r.get("skipped_reason") for r in report["rows"])
        )
        if work_done or skips_seen or report["remainder"] > 0:
            # §8.3 — destructive ticks log IFF work or skips or
            # remainder. No-op ticks stay SILENT (§8.5).
            logger.info(
                f"[SourcesGC] collected={report['collected']} "
                f"archived_configs={report['archived_configs']} "
                f"archived_executions={report['archived_executions']} "
                f"mappings={report['mappings']} "
                f"processed={report['processed']} "
                f"skipped_unparseable={skipped_unparseable} "
                f"observed_error_rows={observed_error_rows} "
                f"remainder={report['remainder']} deferred "
                f"duration_s={time.monotonic() - sweep_started:.3f} "
                f"sample=[{', '.join(report['sample'])}]"
            )
            if work_done and not self._first_destructive_logged:
                self._first_destructive_logged = True
                logger.info(
                    f"[SourcesGC] first destructive sweep performed at "
                    f"{now_utc_iso()} (retention={self._retention_days}d, "
                    f"collected={report['collected']})"
                )

        report["ok"] = True
        report["duration_s"] = time.monotonic() - sweep_started
        return report