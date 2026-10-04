"""Boot-time auto-continue of RUNNING instances after a daemon restart.

Feature: auto-continue-running-after-restart.

Phase 2 of the implementation plan. **Orchestration only** (AC2) — every
operation in this module is a call into an existing primitive; no new
messaging mechanics, no instance-status writes, no retry-count writes.

The pass continues every instance that was ``status='running'`` at the
moment the daemon died, by re-scheduling its interrupted turn via
``_schedule_explicit_handle_resume(silent=True)`` so the graph resumes
from its last LangGraph checkpoint with no HumanMessage injection.

Operational notes
-----------------

* **a′ semantics (D2 / architecture-recommendation.md Focus 1):** the
  CAS marker ``task.auto_continued_at`` is stamped ONLY AFTER
  ``_schedule_explicit_handle_resume`` returns ``{"status": "resuming"}``.
  Stamping-after-schedule means "marked" ≡ "continued" — no
  marked-but-never-resumed window. The residual gap (crash between
  schedule-return and stamp) re-schedules on the next boot, which is
  safe: ``astream(None)`` on an advanced checkpoint is idempotent at
  the LangGraph level and the ExecutionGate per-instance lock
  serializes.

* **RUNNING-only (D7 / G3):** strict ``status='running'`` task
  predicate. ``idle`` / ``queued`` instances have no interrupted
  in-flight turn; widening the predicate is out of scope.

* **PAUSED parked (R12):** PAUSED instance rows are excluded at
  TWO layers — the selection subquery (this module) AND the
  pre-existing ``enqueue_message`` / STR carve-outs (untouched).
  The pass has NO code path into ``resume_instance_cascade``.

* **StaleTaskRecovery co-existence (D12 / G8):** orthogonal, no
  changes. STR runs earlier in the boot sequence; the structural
  guard is the ``boot_epoch`` amnesty (so the just-started 60s
  loop cannot reap a pass-scheduled resume in the
  selection→schedule window — see R22). The miss case (resume
  scheduled but turn dies before heartbeat) is exactly STR's
  designed job.

* **WC bus-owned (D14 / AC3):** ``waiting_children`` instance rows
  are excluded at the selection subquery. The bus's
  ``DependencyBus.start()`` (``_warm_cache`` →
  ``_recover_fired_unsent`` with ``enqueued_at IS NULL`` C1 dedup →
  ``_sweep_orphan_watchers``, ``dependency_bus.py:1499-1560``)
  covers both child-completed-before-death and child-completes-
  after-reboot orderings. The pass never re-drives WC parents.

* **Δ1 / D18 (success-path terminalizer):** the resume flow
  itself does NOT complete the orphan Task — the orphan stays
  ``status='running'`` so the claim-guard
  (``repository.py:2230-2294``) blocks any sibling PENDING claim
  (the AC4 FIFO-behind-the-turn contract). A SUCCESSFUL resume
  flips the orphan to ``status='completed'`` via the
  call-site-gated terminalizer in
  ``_resume_processing_background``'s success branch
  (``manager.py:11666-11697``, ``except`` at ``:11699``). A
  FAILED resume flips the orphan to ``status='failed'`` via the
  pre-existing ``fail_task`` path. The shared ``complete_task`` /
  ``fail_task`` SQL stays byte-identical to pre-feature (D18 r3
  / D29 — the r2 fold's "AND auto_continued_at IS NOT NULL" guard
  was REJECTED because it would silently make worker-pool /
  task-processor completion a no-op).

* **No-heartbeat window (Δ3 / D24):** continued turns are
  STR-reapable at ``boot+10 min`` (CONFIG default;
  ``daemon/config.py:1271``). The resume path writes no heartbeats
  (architect-verified, R20). The reap is
  ``force_cancel_and_schedule_retry`` = checkpoint-continuation
  retry (idempotent). ``retry_count`` burn is the explicit D6
  exception for >10-min turns.

* **Sequential v1 (D10 / G6):** the per-candidate loop is
  sequential; no ``asyncio.gather`` over checkpoint probes.
  Wall-clock target <5 s for observed N≤33. The
  ``STAGGER_EVERY = 5`` / ``STAGGER_SLEEP_SECONDS = 2`` cadence
  (Δ5 / D22) bounds boot-time LLM-stampede cost at ~14 s total
  for N=33.

* **Kill-switch (AC9 / D13):** env-direct ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART``,
  default ON, ``=0`` disables. Read per boot (NOT cached) — the
  operator flips the env and restarts. Mirrors
  ``ENSEMBLE_POST_RESTART_ARM_NOTIFY`` exactly. ``os.environ.get``
  is the source of truth; no Pydantic field, no settings-router
  surface (consistent with the existing post-restart-arm-notify
  convention; the typed-config is for intervals, not kill-switch
  flips — see ``config.py:1633-1634``).

  To disable: set ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0``
  in ``<install_dir>/.env`` and restart the daemon. The
  StaleTaskRecovery backstop continues to own orphans.

Ordering contract (AC4)
------------------------

Three conditions that make double-fire structurally impossible
(the interleaving matrix in ``test_auto_continue_interleaving.py``
pins each row):

1. **Continue-in-place** — never force-cancel the orphan Task;
   the orphan stays ``status='running'``.
2. **Placement AFTER ``sweep_wake_records``** at ``api.py:1522`` so
   the wake's PENDING row exists before the resume is scheduled.
3. **Per-instance isolation** — claim-guard
   (``repository.py:2230-2294``) blocks the wake's claim while
   the orphan is RUNNING → wake lands FIFO-behind the continued
   turn.

Architecture references
-----------------------

* Plan: ``.agents/shared/planning/auto-continue-running-after-restart/``
* Architecture: ``architecture-recommendation.md`` Focus 1 + Focus 2 + Focus 3
* Decisions: ``decisions.md`` D1, D2, D3, D4, D5-D18, D19, D21, D22, D24, D29
* Risks: ``risk-register.md`` R1, R2, R4, R7, R12, R19, R20, R22
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

#: Operator kill-switch env var. Default ON; ``=0`` disables the pass.
#: Mirrors ``ENSEMBLE_POST_RESTART_ARM_NOTIFY`` (``daemon/tools/upgrade_journal.py:886``).
#: Read per boot (NOT cached) so an operator flip takes effect on the
#: next daemon restart.
AUTO_CONTINUE_KILL_SWITCH_ENV = "ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART"


def _auto_continue_enabled() -> bool:
    """Per-boot kill-switch read.

    Returns ``True`` unless the operator has explicitly set
    ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0``. The default is ON
    — the deliverable is zero-user-action; the kill-switch exists for
    operator opt-out, NOT default-off (D13 / ADR-044).
    """
    return os.environ.get(AUTO_CONTINUE_KILL_SWITCH_ENV, "1") != "0"


# ---------------------------------------------------------------------------
# Result accounting
# ---------------------------------------------------------------------------


@dataclass
class ContinueResult:
    """Boot auto-continue pass outcome (the per-boot totals).

    Mirrors ``WakeSweepResult`` (``upgrade_journal_sweep.py:350-374``):
    default-constructible, every counter zero-initialized so the
    kill-switch / empty-case fast paths return a clean struct without
    branches. The ``errors`` counter is the boot-never-wedge contract
    (R2) — every catchable failure increments it; the pass NEVER
    raises out of the lifespan block.

    Counters (every field exposed for logging and the P3 interleaving
    log contract assertion):

    * ``candidates`` — number of rows the selection predicate
      returned. ``>= scheduled + skipped_* + errors``.
    * ``scheduled`` — successful resume + CAS stamp (rowcount==1).
    * ``skipped_no_checkpoint`` — candidate with no LangGraph
      checkpoint (the orphan's checkpoint was lost; STR will
      re-inject at ``boot+10 min``).
    * ``skipped_resume_refused`` — ``_schedule_explicit_handle_resume``
      returned ``None`` or a non-``"resuming"`` status (incl. the
      in-process ``already_resuming`` dedup case at
      ``manager.py:10988-10998``). No stamp.
    * ``skipped_kill_switch`` — pass short-circuited because the
      operator flipped the kill-switch OFF.
    * ``skipped_no_boot_epoch`` — pass short-circuited because
      ``boot_epoch`` capture failed (Δ2 / D19). STR's normal
      age-gated backstop continues to own the orphan.
    * ``skipped_multi_task_instance`` — instance has >1 RUNNING
      task rows; the entire group is skipped (D21 / Δ4). STR
      will reap both via ``force_cancel_and_schedule_retry`` and
      the retry claims cleanly.
    * ``errors`` — per-instance or sweep-level exception. The
      pass NEVER raises out.
    * ``duration_seconds`` — wall-clock duration of the per-candidate
      loop (the metric for the M22 / Δ5 acceptance).
    """

    candidates: int = 0
    scheduled: int = 0
    skipped_no_checkpoint: int = 0
    skipped_resume_refused: int = 0
    skipped_kill_switch: int = 0
    skipped_no_boot_epoch: int = 0
    skipped_multi_task_instance: int = 0
    errors: int = 0
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Stagger cadence (Δ5 / D22)
# ---------------------------------------------------------------------------

#: After every ``STAGGER_EVERY`` resumes scheduled, the loop sleeps
#: ``STAGGER_SLEEP_SECONDS`` to bound boot-time LLM-stampede. Observed
#: N=33 → 6 stagger pauses → ~12 s sleep + ~2 s scheduling ≈ 14 s total.
STAGGER_EVERY: int = 5
STAGGER_SLEEP_SECONDS: float = 2.0


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


async def continue_running_instances_after_restart(
    manager: "InstanceManager",
    boot_epoch: datetime | None,
) -> ContinueResult:
    """Boot-time auto-continue pass — orchestration only.

    The pass is invoked once at lifespan startup, after the wake sweep
    and before the upgrade journal sweep's periodic tick. It is wired
    into ``daemon/api.py:1522`` (the envelope-try BODY level — sibling
    of the upgrade-install-dir ``if``, NOT nested inside it, so dev
    boots also run the pass; the kill-switch is the only legitimate
    way to disable on dev).

    Behavior:

      1. **Kill-switch OFF → return immediately** with
         ``skipped_kill_switch=1``. Zero repo calls, zero resume
         calls. Logged at INFO with the kill-switch env name so an
         operator can confirm the OFF state.

      2. **``boot_epoch is None`` → return immediately** with
         ``skipped_no_boot_epoch=1`` (Δ2 / D19). Logged at WARNING
         with the STR-backstop note. The aware-datetime fallback
         ``datetime.now(timezone.utc)`` is REJECTED: the comparison
         frame is naive-UTC (``_to_naive_utc`` in
         ``daemon/services/boot_epoch.py:88,114,136``,
         ``now_utc_naive()`` in ``daemon/services/timestamps.py:51``,
         ``_default_created_at_naive_utc`` in
         ``daemon/repositories/task/models.py:273``). An aware
         stamp silently corrupts epoch comparisons on SQLite
         (lexicographic ISO with ``+00:00``) and errors on PG
         TIMESTAMP.

      3. **Selection** — ``task_repo.find_auto_continue_candidates(boot_epoch)``
         (D2 / D17 / D21 — full instance-status exclusion set,
         ``cancel_requested=False`` filter, per-epoch re-arm
         predicate).

      4. **>1-candidate defense** (Δ4 / D21) — group candidates by
         ``instance_id``; any group with more than one member is
         skipped wholesale (logged WARNING with the task IDs and
         instance). STR will reap both via
         ``force_cancel_and_schedule_retry``.

      5. **Per-candidate loop** with per-instance ``try/except``
         (Layer 1) and a sweep-level ``try/except`` (Layer 2):

         a. ``_has_checkpoint(instance_id)`` — if False, log INFO
            skip, do NOT stamp. The orphan is re-considered on the
            next boot (next ``boot_epoch`` > current stamp /
            NULL).
         b. ``_schedule_explicit_handle_resume(silent=True, …)``
            with the orphan's ``work_id`` as both
            ``target_work_id`` and ``handle_work_id``, and
            ``route_outcome="boot_continue"`` (D16). The
            kwargs-only signature is at ``manager.py:10915``.
         c. If the return is ``None`` or not ``"resuming"`` (incl.
            the in-process ``already_resuming`` dedup case),
            log INFO skip, do NOT stamp.
         d. ``mark_task_auto_continued(task.id, boot_epoch)`` —
            CAS may return False (the row was stamped by a
            concurrent boot, or the row left ``status='running'``
            in the residual gap). Log INFO, do NOT count as
            scheduled, never raise.
         e. Increment ``result.scheduled``, log INFO
            ``[BOOT_CONTINUE] instance=<id8> work_id=<wid8> epoch=<iso>``.

      6. **Stagger** — after every ``STAGGER_EVERY`` resumes, sleep
         ``STAGGER_SLEEP_SECONDS``.

    The pass is invoked exactly once per boot from
    ``daemon/api.py:1522``. The lifespan block wraps the call in a
    third ``try/except`` (Layer 3) so a top-level exception never
    aborts the boot — the daemon's HTTP listener comes up regardless.

    Args:
        manager: The ``InstanceManager`` (``daemon/manager.py:425``).
            Used for: ``_task_repo`` (asserted non-None), and
            ``_schedule_explicit_handle_resume`` (kwargs-only).
        boot_epoch: The process-global boot epoch captured at
            daemon startup. ``None`` short-circuits the pass
            with ``skipped_no_boot_epoch`` (Δ2 / D19).

    Returns:
        ``ContinueResult`` — every counter zero-initialized in the
        kill-switch / ``boot_epoch=None`` fast paths so the caller
        can log a clean struct without branches.
    """
    result = ContinueResult()

    # (1) Kill-switch (per-boot read; mirrors arm-notify).
    if not _auto_continue_enabled():
        result.skipped_kill_switch = 1
        logger.info(
            "[BOOT_CONTINUE] OFF: %s=0 — pass skipped (StaleTaskRecovery "
            "backstop continues to own orphans)",
            AUTO_CONTINUE_KILL_SWITCH_ENV,
        )
        return result

    # (2) boot_epoch None → SKIP (Δ2 / D19). No fallback timestamp.
    if boot_epoch is None:
        result.skipped_no_boot_epoch = 1
        logger.warning(
            "[BOOT_CONTINUE] SKIPPED: boot_epoch=None — StaleTaskRecovery "
            "backstop will catch orphans at boot+10min (never-fail-closed)"
        )
        return result

    # (3) Repo handle (defensive — `manager._task_repo` MUST be wired
    # by `initialize()` before boot subsystems run; the assertion is
    # loud-fail not silent-None because the next line would NPE
    # otherwise).
    task_repo = manager._task_repo  # type: ignore[attr-defined]
    assert task_repo is not None, (
        "AutoContinue boot pass requires _task_repo to be wired by "
        "initialize() before boot subsystems run"
    )

    # Sweep-level guard (Layer 2) — wraps the entire loop.
    loop_started = time.monotonic()
    try:
        # (3) Selection (D2 / D17 / D21).
        try:
            candidates = await asyncio.to_thread(
                task_repo.find_auto_continue_candidates, boot_epoch
            )
        except Exception as exc:
            # Selection failure (DB error, broken predicate) is rare
            # but we NEVER raise out — count it, return, boot continues.
            result.errors += 1
            logger.warning(
                "[BOOT_CONTINUE] selection failed (orphans caught by "
                "StaleTaskRecovery at boot+10min): %s: %s",
                type(exc).__name__, exc,
            )
            result.duration_seconds = time.monotonic() - loop_started
            return result

        result.candidates = len(candidates)

        # (4) >1-candidate defense (D21 / Δ4) — group by instance_id;
        # any group with >1 members is skipped wholesale.
        from collections import defaultdict
        by_instance: dict[str, list] = defaultdict(list)
        for c in candidates:
            by_instance[c.instance_id].append(c)

        eligible: list = []
        for instance_id, group in by_instance.items():
            if len(group) > 1:
                task_ids = [t.id for t in group]
                result.skipped_multi_task_instance += len(group)
                logger.warning(
                    "[BOOT_CONTINUE] SKIPPED instance=%s: %d RUNNING tasks "
                    "(task_ids=%s) — STR backstop will reap and retry "
                    "claims cleanly",
                    instance_id[:8], len(group), task_ids,
                )
            else:
                eligible.extend(group)

        # (5) Per-candidate loop with per-instance try/except (Layer 1).
        for idx, task in enumerate(eligible):
            try:
                instance_id = task.instance_id
                work_id = task.work_id

                # (5a) Has the orphan's checkpoint survived the crash?
                has_ckpt = await manager._has_checkpoint(instance_id)  # type: ignore[attr-defined]
                if not has_ckpt:
                    result.skipped_no_checkpoint += 1
                    logger.info(
                        "[BOOT_CONTINUE] SKIPPED instance=%s work_id=%s: "
                        "no checkpoint (next boot retries; STR backstop "
                        "at boot+10min if persistent)",
                        instance_id[:8], work_id[:8],
                    )
                    continue

                # (5b) Schedule the silent resume against the orphan's
                # work_id. kwargs-only — see
                # ``daemon/manager.py:10915``.
                resume = await manager._schedule_explicit_handle_resume(  # type: ignore[attr-defined]
                    instance_id=instance_id,
                    message="",
                    silent=True,
                    images=None,
                    target_work_id=work_id,
                    selected_suspension_reason=None,
                    handle_work_id=work_id,
                    route_outcome="boot_continue",
                    image_refs=None,
                )

                # (5c) Schedule refused (None / not "resuming" / the
                # in-process already_resuming dedup case at
                # manager.py:10988-10998) — skip without stamping.
                if (
                    resume is None
                    or not isinstance(resume, dict)
                    or resume.get("status") != "resuming"
                ):
                    result.skipped_resume_refused += 1
                    logger.info(
                        "[BOOT_CONTINUE] SKIPPED instance=%s work_id=%s: "
                        "resume refused (resume=%r)",
                        instance_id[:8], work_id[:8], resume,
                    )
                    continue

                # (5d) Stamp AFTER schedule (D2 / a′ semantics). The
                # CAS may return False (the row was stamped by a
                # concurrent boot, or the row left status='running'
                # in the residual gap). Log INFO, do NOT count as
                # scheduled, never raise.
                stamped = await asyncio.to_thread(
                    task_repo.mark_task_auto_continued, task.id, boot_epoch
                )
                if not stamped:
                    logger.info(
                        "[BOOT_CONTINUE] SKIPPED instance=%s work_id=%s: "
                        "CAS declined (row stamped by a newer epoch or "
                        "left running-set)",
                        instance_id[:8], work_id[:8],
                    )
                    # Not counted as scheduled; not counted as
                    # skipped_* either — the CAS-decline is a
                    # benign idempotency outcome.
                    continue

                result.scheduled += 1
                logger.info(
                    "[BOOT_CONTINUE] instance=%s work_id=%s epoch=%s",
                    instance_id[:8], work_id[:8], boot_epoch.isoformat(),
                )

                # (6) Stagger (Δ5 / D22) — after every Nth resume,
                # sleep STAGGER_SLEEP_SECONDS to bound LLM-stampede.
                if result.scheduled % STAGGER_EVERY == 0:
                    await asyncio.sleep(STAGGER_SLEEP_SECONDS)

            except Exception as exc:
                # Per-instance exception (Layer 1) — never raise.
                # The next candidate is processed independently.
                result.errors += 1
                logger.warning(
                    "[BOOT_CONTINUE] instance=%s failed: %s: %s",
                    task.instance_id[:8],  # type: ignore[union-attr]
                    type(exc).__name__, exc,
                )
                continue
    except Exception as exc:
        # Sweep-level exception (Layer 2) — never raise. Return the
        # partial result so the caller can log it.
        result.errors += 1
        logger.warning(
            "AutoContinue boot sweep failed (orphans caught by "
            "StaleTaskRecovery at boot+10min): %s: %s",
            type(exc).__name__, exc,
        )
    finally:
        result.duration_seconds = time.monotonic() - loop_started

    return result
