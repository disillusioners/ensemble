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
  (``claim_execution_redrive``) advances ONLY the re-drive COUNT
  (``redriven_at`` + ``redrive_count``); the actual re-drive goes through
  the F2 make-claimable + wake path via the injected callback. The row's
  status moves to ``dispatched`` ONLY AFTER a successful re-drive (and
  only with a non-NULL instance handle — A.1: ``dispatched`` means a turn
  started). There is NO direct claim: ``claim_pending_task``'s
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

#: The TRUTHFUL re-drive outcomes (the injected callback's ``status``).
#: Everything else is a REFUSAL: the row keeps its prior truthful status,
#: the re-drive is re-armed, and the cap bounds it.
#:
#: * ``pending_redrive``  — the F2 make-claimable + wake found the durable
#:   task and made it claimable (the turn starts).
#: * ``redriven``         — the adapter re-dispatched the fire (the
#:   instance-less leg) — a turn started.
#: * ``dispatched``       — the re-drive reported a durable dispatch.
#: * ``resuming``         — the paused-resume leg scheduled a graph resume.
#: * ``already_resuming`` — a resume is ALREADY in flight (the work IS
#:   covered — never a refusal).
#: * ``wake_enqueued``    — the WAITING_CHILDREN wake leg enqueued a turn.
_REDRIVE_ACCEPTED_STATUSES = frozenset(
    {
        "pending_redrive",
        "redriven",
        "dispatched",
        "resuming",
        "already_resuming",
        "wake_enqueued",
    }
)

#: The prior-status classes the supervisor distinguishes. ``dispatched`` and
#: ``triggered`` are the STALE class (today's open states — the fire's
#: receipt is open); ``never-started`` / ``failed-to-dispatch`` are the
#: never-began class.
_STALE_OPEN_STATUSES = frozenset({"dispatched", "triggered"})


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


def _caller_cancel_requested() -> bool:
    """True iff the CURRENT task itself has a pending cancel request.

    House pattern: ``daemon/mcp/stdio_wrapper.py::_is_caller_cancelled``
    (and ``daemon/mcp/managed_session.py``'s cancel-scope guard) —
    ``asyncio.CancelledError`` is a ``BaseException`` since 3.8, so a
    cancel raised INSIDE a nested await (an inner future cancelled by
    another party) escapes every ``except Exception`` net. The
    discriminator lets a periodic sweep tell the two apart:

    * ``cancelling() > 0`` — THIS task was cancelled (shutdown) ⇒ the
      CancelledError must PROPAGATE (never swallow a shutdown);
    * ``cancelling() == 0`` — an inner cancellation escaped ⇒ the sweep
      continues (one candidate's cancel never aborts the sweep).

    NIT-3 (fix/post-outage-recovery b4).
    """
    task = None
    try:
        task = asyncio.current_task()
    except RuntimeError:  # pragma: no cover — no running loop (a plain thread)
        return False
    if task is None:
        return False
    try:
        return task.cancelling() > 0
    except AttributeError:  # pragma: no cover — pre-3.11 fallback
        return bool(getattr(task, "_must_cancel", False))


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
    #: NIT-3 — an INNER CancelledError escaped a candidate's re-drive.
    #: Logged LOUD (never swallowed silently), the candidate is skipped,
    #: and the sweep CONTINUES. An outer cancel (shutdown) propagates.
    skipped_redrive_cancelled: int = 0
    #: NIT-1 — the instance-less leg found LIVE delivery evidence and
    #: refused to double-drive it.
    skipped_delivery_in_flight: int = 0
    #: NIT-1 — the instance-less leg found a LANDED delivery (a done
    #: JobItem terminalized 'completed') and healed the receipt to
    #: ``executed`` instead of re-dispatching a SECOND delivery.
    healed_receipt: int = 0
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

    @staticmethod
    def _log_cas_error(pass_name: str, result: SupervisorResult, exc: BaseException, **ctx: Any) -> None:
        """The CAS-leg marker — SPLIT from the DB marker.

        A failed CAS arm is NOT a database failure: the arm can raise for
        a signature/logic reason (a mis-bound call site) as well as for a
        DB reason. Labelling every claim failure ``[..._DB_ERROR]``
        mislabels the CAS class — the exact blindness the reconciler's
        N4-corrected marker discipline forbids (distinct markers per
        failure class).
        """
        detail = " ".join(f"{k}={v}" for k, v in ctx.items())
        logger.error(
            "[SCHEDULED_SUPERVISOR_CAS_ERROR] pass=%s errors=%d %s: %s: %s",
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

    async def _heal_landed_receipt(
        self,
        execution_id: str,
        landed: list[dict[str, Any]],
        result: SupervisorResult,
    ) -> bool:
        """Close a receipt whose delivery LANDED but whose dispatched
        write was LOST (NIT-1).

        The JobItem evidence says the delivery reached ``done`` with
        ``terminal_reason='completed'`` — the truth is ``executed``, so
        the row is closed to that terminal state instead of being
        re-dispatched as a SECOND delivery of the same execution. The
        instance handle rides along from the evidence row when one is
        available (it is allowed to stay NULL on this heal — the row is
        TERMINAL, so the A.1 open-state handle rule does not apply).
        """
        healer = getattr(
            self._source_repo, "heal_execution_receipt_executed", None
        )
        if healer is None:
            return False
        instance_id = next(
            (
                row.get("instance_id")
                for row in landed
                if row.get("instance_id")
            ),
            None,
        )
        try:
            healed = await asyncio.to_thread(
                healer, execution_id, "completed", instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, re-armed
            result.errors += 1
            self._log_db_error("receipt_heal", result, exc)
            return False
        if healed is None:
            return False
        result.healed_receipt += 1
        logger.error(
            "[SCHEDULED_SUPERVISOR_LANDED_DELIVERY_HEALED] execution=%s "
            "instance=%s — the delivery LANDED (done/completed JobItem) "
            "but the dispatched-receipt write was lost; the receipt is "
            "healed to ``executed`` and NO second delivery is minted "
            "(NIT-1 closed)",
            str(execution_id)[:8],
            str(instance_id or "")[:8],
        )
        return True

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
                    await self._process_candidate(
                        candidate, tick_stamp, max_redrives, result,
                        lookback_cutoff,
                    )
                except asyncio.CancelledError:
                    # NIT-3: CancelledError is a BaseException — it escapes
                    # the ``except Exception`` below. Distinguish (house
                    # pattern: ``daemon/mcp/managed_session.py`` /
                    # ``stdio_wrapper._is_caller_cancelled``):
                    #   * THIS task was cancelled (shutdown) ⇒ PROPAGATE;
                    #   * an inner cancellation escaped the candidate's
                    #     re-drive ⇒ log LOUD (never swallowed silently),
                    #     count it, and CONTINUE with the remaining
                    #     candidates — one candidate's cancel never
                    #     aborts the sweep.
                    if _caller_cancel_requested():
                        raise
                    result.skipped_redrive_cancelled += 1
                    logger.error(
                        "[SCHEDULED_SUPERVISOR_REDRIVE_CANCELLED] "
                        "execution=%s — a CancelledError escaped the "
                        "candidate's re-drive while this sweep task is NOT "
                        "being cancelled; the candidate is skipped and the "
                        "sweep CONTINUES (the cancel is NOT swallowed "
                        "silently)",
                        str(candidate.get("execution_id"))[:8],
                    )
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
            "cas_lost=%d refused=%d cancelled=%d errors=%d duration_s=%.3f mode=%s",
            result.candidates, result.redriven, result.recovered,
            result.skipped_processing, result.terminal_dead_lineage,
            result.terminal_cap, result.skipped_cas_lost,
            result.skipped_redrive_refused, result.skipped_redrive_cancelled,
            result.errors,
            result.duration_seconds, result.mode,
        )
        return result

    async def _process_candidate(
        self,
        candidate: dict[str, Any],
        tick_stamp: str,
        max_redrives: int,
        result: SupervisorResult,
        lookback_cutoff: str | None = None,
    ) -> None:
        execution_id = candidate.get("execution_id")
        instance_id = candidate.get("instance_id")
        status = candidate.get("status")
        if not execution_id:
            return

        # (0) NIT-1 — the DELIVERY-EVIDENCE gate (instance-less rows only).
        #     An instance-less 'triggered' row whose dispatched-receipt
        #     write was LOST (fail-soft) carries no handle, so legs (1)
        #     and (2) below are both skipped and the re-dispatch would
        #     mint a SECOND delivery for the same execution (measured
        #     enqueues 1→2 on a recurring fire, which carries no
        #     idempotency key). The JobItem rows ARE the durable
        #     delivery evidence — the fire path stamps
        #     ``metadata.scheduler.execution_id`` on every scheduled
        #     enqueue — so the probe answers "did this execution already
        #     deliver?" cheaply (source-bounded + lookback-bounded +
        #     LIMIT). Adjudication:
        #       * a LIVE JobItem (queued/active) ⇒ the delivery is IN
        #         FLIGHT — never double-drive it (the same protection
        #         leg (1) gives instance-bearing rows);
        #       * a DONE JobItem terminalized 'completed' ⇒ the delivery
        #         LANDED — heal the receipt to ``executed`` (the truth)
        #         and do NOT re-dispatch;
        #       * anything else (no evidence / a dead or non-completed
        #         closure) ⇒ the re-drive IS the recovery intent —
        #         proceed, the cap bounds it.
        if not instance_id and self._source_repo is not None and hasattr(
            self._source_repo, "find_scheduler_delivery_evidence"
        ):
            try:
                evidence = await asyncio.to_thread(
                    self._source_repo.find_scheduler_delivery_evidence,
                    execution_id,
                    lookback_cutoff or "",
                )
            except Exception as exc:  # noqa: BLE001 — fail-OPEN on the probe
                # A probe failure must not blind the lane: log LOUD and
                # proceed exactly as before the gate existed (the cap
                # still bounds the re-drive).
                logger.warning(
                    "[SCHEDULED_SUPERVISOR_EVIDENCE_PROBE_FAILED] "
                    "execution=%s — %s: %s (gate fail-open, re-drive "
                    "proceeds under the cap)",
                    str(execution_id)[:8], type(exc).__name__, exc,
                )
                evidence = []
            if evidence:
                live = [
                    row for row in evidence
                    if str(row.get("admission_state")) in
                    ("queued", "active")
                ]
                landed = [
                    row for row in evidence
                    if str(row.get("admission_state")) == "done"
                    and str(row.get("terminal_reason")) == "completed"
                ]
                if live:
                    result.skipped_delivery_in_flight += 1
                    logger.info(
                        "[SCHEDULED_SUPERVISOR_DELIVERY_IN_FLIGHT] "
                        "execution=%s — %d live JobItem(s) for this "
                        "execution; NOT re-dispatched (never double-drive "
                        "an in-flight delivery)",
                        str(execution_id)[:8], len(live),
                    )
                    return
                if landed:
                    healed = await self._heal_landed_receipt(
                        execution_id, landed, result,
                    )
                    if healed:
                        return

        # (1) Observation-safe: a task RUNNING at the deadline is a slow
        #     start — never double-drive it.
        #     NOTE (instance-less rows): the processing-check needs an
        #     INSTANCE handle, so an instance-less row skips it. That gap
        #     is covered by the ADAPTER's own slot discipline post-fix
        #     (the re-dispatch acquires the execution semaphore — a fire
        #     still holding the slot makes the re-drive time out and
        #     refuse), so a concurrent fire is never double-dispatched.
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
        #     COUNT-ONLY: the stamp advances redriven_at + redrive_count;
        #     the STATUS is never written here (the A.1 invariant — a
        #     claimed-then-refused row keeps its prior truthful status).
        try:
            claim = await asyncio.to_thread(
                self._source_repo.claim_execution_redrive,
                execution_id, tick_stamp, max_redrives,
            )
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            self._log_cas_error("claim", result, exc,
                                execution=str(execution_id)[:8])
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
            # CAS LOST (row gone / already stamped this tick / terminal) —
            # its own marker, never a "DB error" (the N4-corrected marker
            # discipline: distinct markers per failure class).
            result.skipped_cas_lost += 1
            logger.info(
                "[SCHEDULED_SUPERVISOR_CAS_LOST] execution=%s — the re-drive "
                "CAS was lost (row gone / already stamped this tick / "
                "terminal); no action taken",
                str(execution_id)[:8],
            )
            return

        # (4) The ACTION — F2 make-claimable + wake (never a direct claim).
        outcome = await self._redrive(candidate)
        out_status = str(outcome.get("status", "")) if isinstance(outcome, dict) else ""
        if out_status in _REDRIVE_ACCEPTED_STATUSES:
            # (5) The TRUTHFUL ADVANCE — only a successful re-drive may move
            #     the row to the open ``dispatched`` state, and only with a
            #     NON-NULL instance handle (A.1). A refused/failed re-drive
            #     leaves the prior truthful status in place.
            await self._advance_to_dispatched(candidate, outcome, result)
            task_wake = out_status == "pending_redrive" or bool(
                outcome.get("task_id")
            )
            if status in _STALE_OPEN_STATUSES and task_wake:
                result.recovered += 1
            else:
                result.redriven += 1
            logger.info(
                "[SCHEDULED_SUPERVISOR_REDRIVE] execution=%s instance=%s "
                "task=%s — re-drive accepted (make-claimable + wake / "
                "adapter re-dispatch; single consumer CAS)",
                str(execution_id)[:8],
                str(outcome.get("instance_id") or instance_id)[:8],
                str(outcome.get("task_id"))[:8],
            )
        else:
            result.skipped_redrive_refused += 1
            logger.error(
                "[SCHEDULED_SUPERVISOR_REDRIVE_REFUSED] execution=%s "
                "outcome=%r — re-drive did not claim work; the PRIOR "
                "truthful status is kept (count+1, bounded by the cap)",
                str(execution_id)[:8], outcome,
            )

    async def _advance_to_dispatched(
        self,
        candidate: dict[str, Any],
        outcome: dict[str, Any],
        result: SupervisorResult,
    ) -> bool:
        """Move a RE-DRIVEN row to the open ``dispatched`` state.

        The A.1 invariant: ``dispatched`` means a turn started, so the row
        carries a NON-NULL ``instance_id``. The handle comes from the
        re-drive's own dispatch (the adapter's receipt write used the same
        value); when the re-drive cannot name one, the row keeps its prior
        truthful status and the re-drive is re-armed (the cap bounds it).

        A delivery that already TERMINATED before this advance (the
        delivery-completed callback is bounded to the open states, so a
        callback that landed while the row sat in a re-drive-able state was
        skipped by it) is closed as ``executed`` instead of resting open
        forever — the outcome is the truth and must not be dropped.
        """
        execution_id = candidate.get("execution_id")
        instance_id = outcome.get("instance_id") or candidate.get("instance_id")
        if not execution_id or not instance_id:
            logger.warning(
                "[SCHEDULED_SUPERVISOR_ADVANCE_REFUSED] execution=%s — the "
                "re-drive named no instance handle; the row keeps its prior "
                "truthful status (a 'dispatched' receipt never carries a "
                "NULL instance_id)",
                str(execution_id)[:8],
            )
            return False
        job_item_id = outcome.get("job_item_id")
        terminal_outcome = await self._delivered_outcome(job_item_id)
        advance = getattr(
            self._source_repo, "advance_execution_redrive_dispatched", None
        )
        if advance is None:
            logger.error(
                "[SCHEDULED_SUPERVISOR_ADVANCE_UNAVAILABLE] execution=%s — "
                "the source repo exposes no advance_execution_redrive_"
                "dispatched; the row keeps its prior truthful status",
                str(execution_id)[:8],
            )
            return False
        try:
            row = await asyncio.to_thread(
                advance, execution_id, instance_id, outcome=terminal_outcome,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, never raise
            result.errors += 1
            self._log_db_error("advance_write", result, exc,
                               execution=str(execution_id)[:8])
            return False
        return row is not None

    async def _delivered_outcome(self, job_item_id: Any) -> str | None:
        """The terminal outcome of the delivered turn, if it already ended.

        Read from the TASK row (the work_id == the job_item_id) — the
        delivery-completed callback is bounded to the open receipt states,
        so a callback that landed while the row was still in a
        re-drive-able state was SKIPPED by it; without this read the
        outcome would be dropped and the receipt would rest open forever.
        """
        if not job_item_id or self._task_repo is None:
            return None
        try:
            task = await asyncio.to_thread(
                self._task_repo.get_by_work_id, job_item_id,
            )
        except Exception:  # noqa: BLE001 — unresolvable ⇒ no outcome
            return None
        status = getattr(task, "status", None)
        if status == "completed":
            return "completed"
        if status == "failed":
            return "error"
        if status == "cancelled":
            return "cancelled"
        return None


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
