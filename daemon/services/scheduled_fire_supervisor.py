"""Scheduled-fire supervisor (fix/post-outage-recovery SCOPE b3, Addendum A.4).

The execution receipt makes the repair path DECIDABLE. This always-on service
consumes it: a fire whose receipt says ``never-started`` / ``failed-to-dispatch``
(or a ``dispatched`` row stale past a deadline) is re-driven — a lost scheduled
turn is never silently left as ``queued`` with nothing to consume.

Design (house pattern: interval-driven, first tick IMMEDIATE, fail-loud)
-----------------------------------------------------------------------
* **Selection is 100% DB-derived** — one indexed query over
  ``schedule_executions`` (``select_supervisor_candidates``), bounded by a
  ``triggered_at`` lookback so legacy rows (which predate the receipt
  contract) are NEVER re-driven en masse.
* **Re-drive is an ACTION, not a status** — the CAS stamp
  (``claim_execution_redrive``) advances the row to ``dispatched``; the
  actual re-drive goes through the F2 make-claimable + wake path via the
  injected callback. There is NO direct claim: ``claim_pending_task``'s
  pending→running UPDATE stays the single consumer CAS (at-most-one turn).
* **Observation-safe** — a task already RUNNING on the instance at the
  deadline is a slow start, NEVER double-driven (``find_running_by_instance``).
* **Dead-lineage-safe** — a terminal instance is terminalized as
  ``failed-to-dispatch`` with a LOUD reason; no resurrection.
* **Bounded** — re-drives per execution are capped (default 2); a row that
  went stale AGAIN after its re-drive surfaces LOUDLY and then terminalizes
  instead of looping forever.
* **Never-wedge** — three-layer isolation (per-candidate → sweep → loop)
  with the structured ``[SCHEDULED_SUPERVISOR_*]`` ERROR markers (the
  reconciler's N4-corrected marker discipline: distinct markers per failure
  class, never a mislabeled "DB error" for a CAS/refusal loss).

Kill-switch
-----------
``ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR=0`` disables the lane (default ON).
Precedent: ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`` /
``ENSEMBLE_HARD_REBOOT_BOOT_PASS`` (both default ON, ``=0`` disables). The
interval and deadline/lookback/cap knobs are read per-sweep from the
environment with defensive coercion (same shape as MissionStrandHealer).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

# ── Kill-switch + knob defaults ────────────────────────────────────────────
KILL_SWITCH_ENV = "ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR"
INTERVAL_ENV = "ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR_INTERVAL_SECONDS"
DEADLINE_ENV = "ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR_STALE_DEADLINE_SECONDS"
LOOKBACK_ENV = "ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR_LOOKBACK_HOURS"
MAX_REDRIVES_ENV = "ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR_MAX_REDRIVES"

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_STALE_DEADLINE_SECONDS = 180       # A.4 default, range [120, 300]
MIN_STALE_DEADLINE_SECONDS = 120
MAX_STALE_DEADLINE_SECONDS = 300
DEFAULT_LOOKBACK_HOURS = 24                # legacy rows never flood the lane
DEFAULT_MAX_REDRIVES = 2
DEFAULT_BATCH_LIMIT = 200

_TERMINAL_INSTANCE_STATUSES = ("terminated", "completed", "error", "failed")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[SCHEDULED_SUPERVISOR] non-numeric %s=%r — using default %d",
            name, raw, default,
        )
        return default


def kill_switch_enabled() -> bool:
    """Default ON; ``=0`` (or false/no/off) disables."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass
class SupervisorResult:
    """Per-sweep totals. Every counter zero-initialized for clean fast paths."""

    candidates: int = 0
    #: never-started / failed-to-dispatch fires re-driven.
    redriven: int = 0
    #: dispatched-stale fires whose pending task was found + woken.
    recovered: int = 0
    #: slow start — task RUNNING at the deadline, NOT re-driven.
    skipped_processing: int = 0
    #: dead lineage — terminalized failed-to-dispatch loudly.
    terminal_dead_lineage: int = 0
    #: re-drive cap exhausted — terminalized loudly, no loop.
    terminal_cap: int = 0
    #: CAS lost (row gone / already stamped this tick / terminal).
    skipped_cas_lost: int = 0
    #: callback refused / found no work — surfaced, re-armed next tick.
    skipped_redrive_refused: int = 0
    skipped_kill_switch: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    mode: str = "unknown"


class ScheduledFireSupervisor:
    """Always-on re-drive supervisor for scheduled-fire receipts."""

    def __init__(
        self,
        manager: "InstanceManager",
        *,
        source_repo: Any | None = None,
        task_repo: Any | None = None,
        instance_repo: Any | None = None,
        redrive_callback: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        self._manager = manager
        self._source_repo = source_repo if source_repo is not None else getattr(manager, "_source_repository", None)
        self._task_repo = task_repo if task_repo is not None else getattr(manager, "_task_repo", None)
        self._instance_repo = instance_repo if instance_repo is not None else getattr(manager, "_instance_repository", None)
        self._redrive_callback = redrive_callback
        self._interval_seconds = interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        self._task: asyncio.Task | None = None
        self._stopping = False

    # ── Lifecycle (mirrors WatchReconcileSweepService) ─────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="scheduled-fire-supervisor")

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        # First tick IMMEDIATE (house fast-on-boot catch-up), then interval.
        while not self._stopping:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop-level isolation
                logger.error(
                    "[SCHEDULED_SUPERVISOR_LOOP_ERROR] %s: %s",
                    type(exc).__name__, exc,
                )
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _log_db_error(pass_name: str, result: SupervisorResult, exc: BaseException, **ctx: Any) -> None:
        detail = " ".join(f"{k}={v}" for k, v in ctx.items())
        logger.error(
            "[SCHEDULED_SUPERVISOR_DB_ERROR] pass=%s errors=%d %s: %s: %s",
            pass_name, result.errors, detail, type(exc).__name__, exc,
        )

    def _instance_status(self, instance_id: str) -> str | None:
        """Best-effort live instance status (None = unresolvable)."""
        if self._instance_repo is None:
            return None
        try:
            row = self._instance_repo.get(instance_id)
        except Exception:  # noqa: BLE001
            return None
        return getattr(row, "status", None) if row is not None else None

    def _task_is_processing(self, instance_id: str) -> bool:
        """True iff a RUNNING task exists (slow start — never double-drive)."""
        if self._task_repo is None:
            return False
        try:
            return self._task_repo.find_running_by_instance(instance_id) is not None
        except Exception:  # noqa: BLE001 — unresolvable ⇒ not a blocker
            return False

    async def _redrive(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Invoke the injected re-drive action (F2 make-claimable + wake)."""
        if self._redrive_callback is None:
            return {"status": "no_callback"}
        return await self._redrive_callback(candidate)

    # ── Main sweep ─────────────────────────────────────────────────────────

    async def sweep_once(self) -> SupervisorResult:
        """One bounded re-drive sweep. Never raises out of the caller."""
        result = SupervisorResult()
        if not kill_switch_enabled():
            result.skipped_kill_switch = 1
            result.mode = "off"
            logger.info(
                "[SCHEDULED_SUPERVISOR] OFF: %s=0 — lane skipped", KILL_SWITCH_ENV
            )
            return result
        result.mode = "on"

        if self._source_repo is None:
            result.errors += 1
            self._log_db_error("wiring", result, RuntimeError("source_repo unavailable"))
            return result

        deadline = _env_int(DEADLINE_ENV, DEFAULT_STALE_DEADLINE_SECONDS)
        deadline = max(MIN_STALE_DEADLINE_SECONDS, min(MAX_STALE_DEADLINE_SECONDS, deadline))
        lookback_h = max(1, _env_int(LOOKBACK_ENV, DEFAULT_LOOKBACK_HOURS))
        max_redrives = max(1, _env_int(MAX_REDRIVES_ENV, DEFAULT_MAX_REDRIVES))
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(seconds=deadline)).isoformat()
        lookback_cutoff = (now - timedelta(hours=lookback_h)).isoformat()
        tick_stamp = now.isoformat()

        loop_started = time.monotonic()
        try:
            try:
                candidates = await asyncio.to_thread(
                    self._source_repo.select_supervisor_candidates,
                    stale_before, lookback_cutoff, DEFAULT_BATCH_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 — fail loudly, never raise
                result.errors += 1
                self._log_db_error("selection", result, exc)
                return result
            result.candidates = len(candidates)

            for candidate in candidates:
                try:
                    await self._process_candidate(candidate, tick_stamp, max_redrives, result)
                except Exception as exc:  # noqa: BLE001 — per-candidate isolation
                    result.errors += 1
                    self._log_db_error(
                        "candidate", result, exc,
                        execution=str(candidate.get("execution_id"))[:8],
                    )
        except Exception as exc:  # noqa: BLE001 — sweep-level isolation
            result.errors += 1
            self._log_db_error("sweep", result, exc)
        finally:
            result.duration_seconds = time.monotonic() - loop_started

        logger.info(
            "[SCHEDULED_SUPERVISOR] summary: candidates=%d redriven=%d "
            "recovered=%d skipped_processing=%d dead_lineage=%d cap_terminal=%d "
            "cas_lost=%d refused=%d errors=%d duration_s=%.3f mode=%s",
            result.candidates, result.redriven, result.recovered,
            result.skipped_processing, result.terminal_dead_lineage,
            result.terminal_cap, result.skipped_cas_lost,
            result.skipped_redrive_refused, result.errors,
            result.duration_seconds, result.mode,
        )
        return result

    async def _process_candidate(
        self,
        candidate: dict[str, Any],
        tick_stamp: str,
        max_redrives: int,
        result: SupervisorResult,
    ) -> None:
        execution_id = candidate.get("execution_id")
        instance_id = candidate.get("instance_id")
        status = candidate.get("status")
        if not execution_id:
            return

        # (1) Observation-safe: a task RUNNING at the deadline is a slow
        #     start — never double-drive it.
        if instance_id and self._task_is_processing(instance_id):
            result.skipped_processing += 1
            logger.info(
                "[SCHEDULED_SUPERVISOR_SLOW_START] execution=%s instance=%s "
                "— task RUNNING at deadline, NOT re-driven",
                str(execution_id)[:8], str(instance_id)[:8],
            )
            return

        # (2) Dead-lineage-safe: a terminal instance is never resurrected.
        if instance_id:
            inst_status = self._instance_status(instance_id)
            if inst_status in _TERMINAL_INSTANCE_STATUSES:
                try:
                    await asyncio.to_thread(
                        self._source_repo.terminalize_execution_redrive_failed,
                        execution_id,
                        f"dead_lineage: instance {instance_id} is {inst_status}",
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors += 1
                    self._log_db_error("dead_lineage_write", result, exc)
                result.terminal_dead_lineage += 1
                logger.error(
                    "[SCHEDULED_SUPERVISOR_DEAD_LINEAGE] execution=%s "
                    "instance=%s status=%s — terminalized failed-to-dispatch, "
                    "NO resurrection",
                    str(execution_id)[:8], str(instance_id)[:8], inst_status,
                )
                return

        # (3) CAS stamp — at-most-one re-drive per reason (per tick).
        try:
            claim = await asyncio.to_thread(
                self._source_repo.claim_execution_redrive,
                execution_id, tick_stamp, max_redrives,
            )
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            self._log_db_error("claim", result, exc)
            return
        if claim == "cap-exceeded":
            try:
                await asyncio.to_thread(
                    self._source_repo.terminalize_execution_redrive_failed,
                    execution_id,
                    f"redrive_cap: exceeded {max_redrives} re-drives",
                )
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                self._log_db_error("cap_write", result, exc)
            result.terminal_cap += 1
            logger.error(
                "[SCHEDULED_SUPERVISOR_CAP_TERMINAL] execution=%s "
                "redrive_count>=%d — terminalized failed-to-dispatch, no loop",
                str(execution_id)[:8], max_redrives,
            )
            return
        if claim != "claimed":
            result.skipped_cas_lost += 1
            return

        # (4) The ACTION — F2 make-claimable + wake (never a direct claim).
        outcome = await self._redrive(candidate)
        out_status = str(outcome.get("status", "")) if isinstance(outcome, dict) else ""
        if out_status in ("pending_redrive", "redriven", "dispatched", "resuming"):
            if status == "dispatched":
                result.recovered += 1
            else:
                result.redriven += 1
            logger.info(
                "[SCHEDULED_SUPERVISOR_REDRIVE] execution=%s instance=%s "
                "task=%s — found task on instance (make-claimable + wake, "
                "single consumer CAS)",
                str(execution_id)[:8],
                str(outcome.get("instance_id") or instance_id)[:8],
                str(outcome.get("task_id"))[:8],
            )
        else:
            result.skipped_redrive_refused += 1
            logger.error(
                "[SCHEDULED_SUPERVISOR_REDRIVE_REFUSED] execution=%s "
                "outcome=%r — re-drive did not claim work; row stamped and "
                "re-armed (bounded by cap)",
                str(execution_id)[:8], outcome,
            )


__all__ = [
    "ScheduledFireSupervisor",
    "SupervisorResult",
    "KILL_SWITCH_ENV",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_STALE_DEADLINE_SECONDS",
    "DEFAULT_LOOKBACK_HOURS",
    "DEFAULT_MAX_REDRIVES",
    "kill_switch_enabled",
]
