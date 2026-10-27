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
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from daemon.services.mission_record_service import (
    mint_boot_epoch_if_none_fail_open,
)
from daemon.services import promote_txn_hold

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

#: The VERBATIM env value read by the most recent
#: :func:`_auto_continue_enabled` call (wedge-capture D16 / RK-29).
#:
#: The boot record needs the raw value as a transcript — an operator
#: reading ``=false`` must see that the pass read ``"false"`` and
#: therefore ran ON. The value is captured by the SINGLE env read the
#: pass already performs, so the recorder NEVER re-reads the
#: environment and NEVER re-derives the mode (A8's single-read rule).
_last_auto_continue_raw: str | None = None


def _auto_continue_enabled() -> bool:
    """Per-boot kill-switch read.

    Returns ``True`` unless the operator has explicitly set
    ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0``. The default is ON
    — the deliverable is zero-user-action; the kill-switch exists for
    operator opt-out, NOT default-off (D13 / ADR-044).

    The verbatim raw value is recorded in
    :data:`_last_auto_continue_raw` as a side effect of this SAME
    read — the wedge-capture boot record transcribes it without a
    second environment read.
    """
    global _last_auto_continue_raw
    raw = os.environ.get(AUTO_CONTINUE_KILL_SWITCH_ENV, "1")
    _last_auto_continue_raw = raw
    return raw != "0"


def _last_auto_continue_raw_value() -> str | None:
    """The verbatim env value read by the most recent kill-switch read."""
    return _last_auto_continue_raw


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

    CARRIER FIELDS (wedge-capture D16 / A8 / F-09):

    * ``auto_continue_mode`` — the EFFECTIVE mode this pass read:
      ``"on"``, ``"off"`` or ``"unknown"``. The pass reads the
      kill-switch ONCE at its first line and carries the result HERE;
      the wedge-capture boot record consumes this field and NEVER
      re-reads the environment (A8's single-read rule).
    * ``auto_continue_raw_env`` — the VERBATIM env value the pass
      read, transcribed from the SAME read (RK-29: ``=false`` /
      ``=off`` / ``=no`` all mean ON, and an operator must be able to
      SEE that).
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
    # ── wedge-capture carrier fields (additive; default "unknown") ──
    auto_continue_mode: str = "unknown"
    auto_continue_raw_env: str | None = None
    # ── A3 additive fields (soak-gate-mcp-warmup) ────────────────────
    #: Candidates DEFERRED by the promote-txn hold (the A3 seam). The
    #: hold DEFERS, never drops: the candidates are parked in the hold
    #: module's per-seam list and released through THIS pass's own
    #: checkpoint-resume path when the promote transaction closes.
    deferred_pipeline_busy: int = 0
    #: The deferred candidates' work ids (observability).
    deferred_work_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stagger cadence (Δ5 / D22)
# ---------------------------------------------------------------------------

#: After every ``STAGGER_EVERY`` resumes scheduled, the loop sleeps
#: ``STAGGER_SLEEP_SECONDS`` to bound boot-time LLM-stampede. Observed
#: N=33 → 6 stagger pauses → ~12 s sleep + ~2 s scheduling ≈ 14 s total.
STAGGER_EVERY: int = 5
STAGGER_SLEEP_SECONDS: float = 2.0


# ---------------------------------------------------------------------------
# A3 release handlers (soak-gate-mcp-warmup)
# ---------------------------------------------------------------------------
#
# The hold module DEFERS the resume burst while a promote transaction is
# live and RELEASES it through the ORIGINAL seam — the checkpoint-resume
# path, never a cold re-dispatch. The handlers below re-issue EXACTLY
# what the pass would have issued at defer time.


def _make_boot_continue_release_handler(manager, task_repo, boot_epoch):
    """Build the seam-1 release handler (the boot auto-continue pass).

    Re-issues the silent checkpoint resume for every deferred candidate
    and stamps the a′ CAS marker (``task.auto_continued_at``) exactly
    as the pass itself does (stamped AFTER the schedule returns
    ``{"status": "resuming"}``).
    """

    async def _release(candidates):
        for cand in candidates:
            payload = cand.payload
            instance_id = payload.get("instance_id")
            work_id = payload.get("work_id")
            task_id = payload.get("task_id")
            try:
                resume = await manager._schedule_explicit_handle_resume(
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
                if (
                    resume is None
                    or not isinstance(resume, dict)
                    or resume.get("status") != "resuming"
                ):
                    logger.info(
                        "[BOOT_CONTINUE] RELEASED-but-refused instance=%s "
                        "work_id=%s: resume refused (resume=%r)",
                        str(instance_id)[:8], str(work_id)[:8], resume,
                    )
                    continue
                if task_id is not None and boot_epoch is not None:
                    try:
                        await asyncio.to_thread(
                            task_repo.mark_task_auto_continued,
                            task_id, boot_epoch,
                        )
                    except Exception as stamp_exc:  # noqa: BLE001
                        logger.warning(
                            "[BOOT_CONTINUE] RELEASED stamp failed "
                            "instance=%s work_id=%s: %s",
                            str(instance_id)[:8], str(work_id)[:8],
                            stamp_exc,
                        )
                logger.info(
                    "[BOOT_CONTINUE] RELEASED instance=%s work_id=%s "
                    "(checkpoint-resume path)",
                    str(instance_id)[:8], str(work_id)[:8],
                )
            except Exception as exc:  # noqa: BLE001 — one candidate never blocks the rest
                logger.warning(
                    "[BOOT_CONTINUE] RELEASED failed instance=%s "
                    "work_id=%s: %s: %s",
                    str(instance_id)[:8], str(work_id)[:8],
                    type(exc).__name__, exc,
                )

    return _release


def _make_orphan_sweep_release_handler(manager):
    """Build the seam-4 release handler (the task-less orphan sweep).

    Re-issues the silent checkpoint resume for every deferred orphan
    through the SAME resume primitive the sweep uses.
    """

    async def _release(candidates):
        for cand in candidates:
            payload = cand.payload
            instance_id = payload.get("instance_id")
            work_id = payload.get("work_id")
            try:
                resume = await manager._schedule_explicit_handle_resume(
                    instance_id=instance_id,
                    message="",
                    silent=True,
                    images=None,
                    target_work_id=work_id,
                    selected_suspension_reason=None,
                    handle_work_id=work_id,
                    route_outcome="boot_continue_orphan_sweep",
                    image_refs=None,
                )
                logger.info(
                    "[BOOT_CONTINUE] orphan-sweep RELEASED instance=%s "
                    "work_id=%s (resume=%r)",
                    str(instance_id)[:8], str(work_id)[:8],
                    (resume or {}).get("status")
                    if isinstance(resume, dict) else resume,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[BOOT_CONTINUE] orphan-sweep RELEASED failed "
                    "instance=%s: %s: %s",
                    str(instance_id)[:8], type(exc).__name__, exc,
                )

    return _release


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
    #     The SINGLE environment read of this pass — the effective mode
    #     and its verbatim value ride along in the result (wedge-capture
    #     D16 / A8's single-read rule). The recorder NEVER re-reads.
    enabled = _auto_continue_enabled()
    result.auto_continue_mode = "on" if enabled else "off"
    result.auto_continue_raw_env = _last_auto_continue_raw_value()
    if not enabled:
        result.skipped_kill_switch = 1
        logger.info(
            "[BOOT_CONTINUE] OFF: %s=0 — pass skipped (StaleTaskRecovery "
            "backstop continues to own orphans) mode=%s",
            AUTO_CONTINUE_KILL_SWITCH_ENV,
            result.auto_continue_mode,
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

    # ── A3: register the release handlers (soak-gate-mcp-warmup) ─────
    # The hold module releases the deferred candidates through the
    # ORIGINAL seams — the checkpoint-resume path, never a cold
    # dispatch. The handlers re-issue EXACTLY what the pass would have
    # done (resume + the a′ CAS stamp).
    promote_txn_hold.register_release_handler(
        promote_txn_hold.SEAM_BOOT_CONTINUE,
        _make_boot_continue_release_handler(manager, task_repo, boot_epoch),
    )
    promote_txn_hold.register_release_handler(
        promote_txn_hold.SEAM_ORPHAN_SWEEP,
        _make_orphan_sweep_release_handler(manager),
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
                #
                # ── A3 SEAM 1 (soak-gate-mcp-warmup) ─────────────────
                # A promote transaction is live ⇒ DEFER (never drop).
                # The boot pass runs EXACTLY ONCE per boot, so a
                # "re-evaluate next pass" release would fall through to
                # StaleTaskRecovery's force-cancel-retry and LOSE the
                # checkpoint resume. The hold module owns the release
                # (bounded re-consult → release through THIS pass's own
                # checkpoint-resume path).
                if promote_txn_hold.promote_txn_live():
                    promote_txn_hold.defer_resume(
                        promote_txn_hold.SEAM_BOOT_CONTINUE,
                        {
                            "instance_id": instance_id,
                            "work_id": work_id,
                            "task_id": task.id,
                            "boot_epoch": (
                                boot_epoch.isoformat()
                                if boot_epoch is not None
                                else None
                            ),
                        },
                    )
                    result.deferred_pipeline_busy += 1
                    result.deferred_work_ids.append(work_id)
                    logger.info(
                        "[BOOT_CONTINUE] DEFERRED instance=%s work_id=%s: "
                        "reason=%s (promote txn live — the hold releases "
                        "it through the checkpoint-resume path)",
                        instance_id[:8], work_id[:8],
                        promote_txn_hold.HOLD_REASON,
                    )
                    continue

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
                    "[BOOT_CONTINUE] instance=%s work_id=%s epoch=%s mode=%s",
                    instance_id[:8], work_id[:8], boot_epoch.isoformat(),
                    result.auto_continue_mode,
                )

                # ── Proposal B PR1 — boot-sweep epoch mint (lane 14) ──
                # MINT-IF-NONE: the silent checkpoint resume founds the
                # epoch boundary that "no message, no clear at all" left
                # missing (map §5 gap 5) — ``founding_source='boot'``,
                # ``founding_message_id=NULL`` (the HONEST null: no
                # message exists). An OPEN epoch continues untouched
                # (resume is delivery, not founding); only a stranded
                # instance gets the boundary row. RECORD-SIDE ONLY,
                # FAIL-OPEN (the pass never wedges — the mint failure is
                # a log line and the boot proceeds unchanged).
                try:
                    await asyncio.to_thread(
                        mint_boot_epoch_if_none_fail_open,
                        getattr(manager, "mission_record_service", None),
                        instance_id,
                    )
                except Exception as mint_exc:  # noqa: BLE001 — never wedge
                    logger.warning(
                        "[BOOT_CONTINUE] mission-record mint failed for "
                        "instance=%s: %s: %s (boot proceeds)",
                        instance_id[:8],
                        type(mint_exc).__name__,
                        mint_exc,
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
# ---------------------------------------------------------------------------
# Task-less orphan sweep (D13 follow-up)
# ---------------------------------------------------------------------------
#
# The existing ``continue_running_instances_after_restart`` pass covers
# instances that have a surviving ``task`` row in ``status='running'``.
# The orphan class this pass addresses is the **complement** —
# instances whose ``status='running'`` but whose ``task`` row was lost
# (PROCESSING rolled back at restart, paused-then-killed, OOM-killed
# mid-tool-call, etc.). Without a boot reader for this class, an
# instance is frozen in ``running`` + checkpoint-alive + no-task
# forever — exactly the leader turn ``897011bb`` and rcc mission
# ``6b1630e7`` RCAs of 2026-10-19.
#
# See ``.agents/shared/planning/hard-reboot-recovery/decisions.md``
# (D13 / D18 / NG-8) for the mandate.
#
# The pass reuses:
#
#   * The kill-switch ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`` (the
#     operator toggle is shared with the AutoContinue pass because the
#     two passes are the SAME logical action — auto-continue RUNNING
#     instances after restart — for disjoint sub-classes of the orphan
#     state).
#   * ``_schedule_explicit_handle_resume`` (the graph-resume
#     primitive; kwargs-only, idempotent at LangGraph level via
#     ``astream(None)``).
#   * ``boot_epoch`` (process-global generation barrier; never
#     persisted across restarts).
#
# Safety narrowing vs. the commission-recommended shape:
#
#   * **Status clause: ``running`` only** — NOT
#     ``running + idle``. The narrowing is DOUBLE-safe:
#
#     (1) An ``idle`` instance's ONLY writer is the INSERT at
#         ``instance_lifecycle.py:5353`` (spawn-time status). An
#         idle instance has never run → no LangGraph checkpoint →
#         the pass's ``_get_checkpoint_last_write_naive_utc``
#         returns None → ``skipped_no_checkpoint`` fires (first
#         exclusion).
#
#     (2) Even in the edge case where an instance reaches ``idle``
#         with a surviving checkpoint (transient state between
#         turn-end and the next dispatch), the ``ckpt_ts <
#         boot_epoch`` natural-immunity clause excludes it (second
#         exclusion).
#
#     The parked-parent concern named in the commission binds to
#     ``WAITING_CHILDREN`` (a distinct status value, see
#     ``daemon/repositories/instance/models.py:30-31``), which is
#     already excluded by the task-axis ``NOT IN`` selector.
#     Regression tests: ``tests/unit/services/test_boot_orphan_sweep.py``
#     :: ``test_parked_parent_waiting_children_not_swept`` +
#     ``test_idle_not_swept``.
#
#   * **Cross-clock caveat (A#3 advisory, 2026-10-19):** the
#     ``boot_epoch`` is captured via DB-side ``now()`` /
#     ``CURRENT_TIMESTAMP`` (``daemon/services/boot_epoch.py:96-123``,
#     SQL-side clock), while the checkpoint ``ts`` is a Python-side
#     UTC string written by LangGraph at checkpoint commit
#     (``langgraph/checkpoint/base/__init__.py:820,839``). On the
#     canonical host (daemon + PG same box) the two clocks are
#     exact; on a split-box install (PG on a different host than
#     the daemon process) the two clocks may skew in EITHER
#     direction:
#
#       * DB clock runs ahead of the Python clock → a
#         CURRENT-generation checkpoint's ``ts`` may read as
#         pre-epoch → the sweep would re-sweep a current-generation
#         orphan. Not silent-resume-unsafe (the
#         ``_schedule_explicit_handle_resume`` dedup gate
#         ``_graph_tasks[instance_id]`` still fires; the resume is
#         idempotent at the LangGraph level) — the effect is a
#         wasted resume call, not a corruption.
#       * DB clock runs BEHIND the Python clock → a
#         PREVIOUS-generation checkpoint's ``ts`` may read as
#         at-or-after epoch → the sweep would skip a genuine
#         prior-generation orphan. Fail-closed — the orphan stays
#         frozen until StaleTaskRecovery's backstop.
#
#     The commission accepted the skew hazard; the task-axis
#     terminal-COMPLETED exclusion (B1, above) makes the residual
#     risk strictly smaller than pre-fix (a mis-sweep on the
#     finalize-gap class was the actual data-loss hazard).
#
#   * **Checkpoint last-write < boot_epoch** — natural immunity, NOT a
#     new column. After a successful resume the LangGraph superstep
#     loop rewrites the checkpoint with a fresh ``ts`` (at-or-after
#     ``boot_epoch``). The selector excludes any instance whose
#     checkpoint was written by the CURRENT process, so a second boot
#     in the SAME generation does not re-sweep. A NEW restart produces
#     a fresh ``boot_epoch``; the prior-generation checkpoint's ``ts``
#     is older, so any new orphan from a later restart IS swept.
#
#   * **Per-instance failure isolation** — the per-candidate loop
#     wraps each instance in a ``try/except``; a sweep exception on
#     one instance does NOT abort the rest (Layer 1 of the three-layer
#     never-wedge envelope; the sweep-level ``try/except`` is Layer 2;
#     the lifespan-level envelope at ``daemon/api.py:2361`` is Layer 3).
#
# Observability contract — always-emitted summary line so the boot
# pass is visible even when zero orphans exist (N=0): the line is
# the ``[BOOT_CONTINUE] orphan-sweep summary`` line at the end of
# this function. Per-instance detail is INFO; per-instance failures
# are WARNING with the exception class + reason.


async def _get_checkpoint_last_write_naive_utc(
    manager, instance_id: str
) -> "datetime | None":
    """Read the LangGraph checkpoint's last-write timestamp.

    Returns the naive-UTC ``datetime`` parsed from
    ``CheckpointTuple.checkpoint['ts']`` (ISO 8601 with the
    ``+00:00`` suffix stripped), or ``None`` if no checkpoint
    exists or the read fails.

    The naive-UTC frame matches the ``auto_continued_at`` /
    ``boot_epoch`` storage frame used by the companion
    ``find_auto_continue_candidates`` pass.
    """
    try:
        config = {"configurable": {"thread_id": instance_id}}
        ckpt_tuple = await manager.checkpointer.raw_saver.aget_tuple(  # type: ignore[attr-defined]
            config
        )
    except Exception as exc:  # noqa: BLE001
        # Defensive — checkpoint read is async IO; a failure is
        # logged but does NOT abort the sweep. The orphan falls to
        # the no-checkpoint skip counter; StaleTaskRecovery owns
        # the eventual reap at boot+10min.
        logger.info(
            "[BOOT_CONTINUE] orphan-sweep checkpoint-read failed "
            "instance=%s: %s: %s",
            instance_id[:8], type(exc).__name__, exc,
        )
        return None
    if ckpt_tuple is None:
        return None
    ts_iso = (ckpt_tuple.checkpoint or {}).get("ts")
    if not ts_iso:
        return None
    try:
        # ISO 8601 ``ts`` is timezone-aware (e.g. ``2026-10-19T12:00:00.123456+00:00``).
        # Strip the tz suffix and return naive-UTC digits — matches
        # the storage frame of ``boot_epoch`` (see
        # ``daemon/services/boot_epoch.py:88,114``).
        parsed = datetime.fromisoformat(ts_iso)
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    except Exception:
        return None


async def sweep_orphaned_live_turns(
    manager: "InstanceManager",
    boot_epoch: datetime | None,
) -> ContinueResult:
    """Boot-time task-less orphan sweep.

    Sister pass to :func:`continue_running_instances_after_restart`;
    covers the disjoint sub-class of RUNNING instances whose ``task``
    row was lost. The two passes share the same kill-switch, the
    same resume primitive, and the same boot-epoch generation
    barrier; they are NEVER run in parallel within one boot (the
    existing pass executes first at ``daemon/api.py:2365``; this
    pass runs immediately after).

    Args:
        manager: The ``InstanceManager`` (``daemon/manager.py:425``).
        boot_epoch: The process-global boot epoch captured at
            daemon startup. ``None`` short-circuits with
            ``skipped_no_boot_epoch``.

    Returns:
        ``ContinueResult`` — the same shape as
        :func:`continue_running_instances_after_restart`. The pass
        emits a single ``[BOOT_CONTINUE] orphan-sweep summary`` line
        at INFO with the per-boot totals so the operator can verify
        the sweep ran.
    """
    result = ContinueResult()

    # (1) Kill-switch (shared with AutoContinue — operator toggle
    #     is the same logical action).
    #     The SINGLE environment read of this sweep — the effective
    #     mode and its verbatim value ride along in the result
    #     (wedge-capture D16 / A8's single-read rule).
    enabled = _auto_continue_enabled()
    result.auto_continue_mode = "on" if enabled else "off"
    result.auto_continue_raw_env = _last_auto_continue_raw_value()
    if not enabled:
        result.skipped_kill_switch = 1
        logger.info(
            "[BOOT_CONTINUE] orphan-sweep OFF: %s=0 — pass skipped "
            "(StaleTaskRecovery backstop continues to own orphans) mode=%s",
            AUTO_CONTINUE_KILL_SWITCH_ENV,
            result.auto_continue_mode,
        )
        return result

    # (2) boot_epoch None → SKIP (mirrors AutoContinue).
    if boot_epoch is None:
        result.skipped_no_boot_epoch = 1
        logger.warning(
            "[BOOT_CONTINUE] orphan-sweep SKIPPED: boot_epoch=None "
            "— StaleTaskRecovery backstop will catch orphans at "
            "boot+10min (never-fail-closed)"
        )
        return result

    # (3) Repo handle (defensive — same shape as AutoContinue).
    task_repo = manager._task_repo  # type: ignore[attr-defined]
    assert task_repo is not None, (
        "Boot orphan sweep requires _task_repo to be wired by "
        "initialize() before boot subsystems run"
    )

    # ── A3: register the release handlers (idempotent) ───────────────
    # The orphan sweep may run without the boot pass (it is a sister
    # pass); registering here makes the seam-4 release path available
    # either way. The registration is idempotent (last wins, same
    # shape).
    promote_txn_hold.register_release_handler(
        promote_txn_hold.SEAM_BOOT_CONTINUE,
        _make_boot_continue_release_handler(manager, task_repo, boot_epoch),
    )
    promote_txn_hold.register_release_handler(
        promote_txn_hold.SEAM_ORPHAN_SWEEP,
        _make_orphan_sweep_release_handler(manager),
    )

    loop_started = time.monotonic()
    try:
        # (4) Selection — read-only repo method.
        try:
            instance_ids = await asyncio.to_thread(
                task_repo.find_orphaned_live_turns, boot_epoch=boot_epoch
            )
        except Exception as exc:
            result.errors += 1
            logger.warning(
                "[BOOT_CONTINUE] orphan-sweep selection failed "
                "(orphans caught by StaleTaskRecovery at boot+10min): "
                "%s: %s",
                type(exc).__name__, exc,
            )
            result.duration_seconds = time.monotonic() - loop_started
            return result

        result.candidates = len(instance_ids)

        # (5) Per-instance loop with per-instance try/except (Layer 1).
        for instance_id in instance_ids:
            try:
                # (5-prime) In-loop task re-check (A#2 advisory,
                #      2026-10-19) — closes the SELECT→schedule TOCTOU
                #      window. Between the repo selection above and
                #      this loop iteration, a concurrent boot / sibling
                #      pass / outbox may have minted a fresh task row
                #      for this instance. Scheduling against such an
                #      instance would double-turn (the resume and the
                #      new task would race for the same checkpoint).
                #      Mirrors the defense-in-depth shape the existing
                #      ``claim_pending_task`` guard uses; keeps the
                #      QUEUED admission arm exactly as the repo
                #      selection wrote it (the wake-only JobRecovery
                #      class is still owned by the QUEUED arm — see
                #      job_processor.py:1249-1296).
                if await asyncio.to_thread(
                    task_repo.has_instance_busy, instance_id
                ):
                    result.skipped_resume_refused += 1
                    logger.info(
                        "[BOOT_CONTINUE] orphan-sweep SKIPPED "
                        "instance=%s: task re-check found a busy "
                        "task row (SELECT→schedule TOCTOU; "
                        "companion pass owns it now)",
                        instance_id[:8],
                    )
                    continue
                # (5a) Read checkpoint last-write; filter on
                #      ``last_write < boot_epoch`` (natural immunity).
                ckpt_ts = await _get_checkpoint_last_write_naive_utc(
                    manager, instance_id
                )
                if ckpt_ts is None:
                    result.skipped_no_checkpoint += 1
                    logger.info(
                        "[BOOT_CONTINUE] orphan-sweep SKIPPED "
                        "instance=%s: no checkpoint (next boot "
                        "retries; STR backstop at boot+10min if "
                        "persistent)",
                        instance_id[:8],
                    )
                    continue
                if ckpt_ts >= boot_epoch:
                    # The checkpoint was written by the current
                    # process — either a previous pass already swept
                    # this orphan, or the orphan is a pre-existing
                    # pass-managed resume. Skip.
                    result.skipped_resume_refused += 1
                    logger.info(
                        "[BOOT_CONTINUE] orphan-sweep SKIPPED "
                        "instance=%s: checkpoint last-write "
                        "(%s) >= boot_epoch (%s) — natural "
                        "immunity / pre-existing resume",
                        instance_id[:8],
                        ckpt_ts.isoformat(),
                        boot_epoch.isoformat(),
                    )
                    continue

                # (5b) Schedule the silent resume. The orphan has no
                #      surviving ``task`` row, so the resume path
                #      receives a synthetic ``work_id``. The resume
                #      primitive's dedup gate is
                #      ``_graph_tasks[instance_id]``
                #      (manager.py:12036-12046), NOT a ``work_id``
                #      lookup, so a fresh work_id is safe. The
                #      terminalizer at
                #      ``manager.py:12736-12777`` looks up the Task
                #      row by ``old_job_id`` and no-ops when no row
                #      exists — natural safety.
                synthetic_wid = (
                    f"orphan-sweep-{instance_id[:8]}-"
                    f"{int(time.monotonic() * 1000)}"
                )
                # ── A3 SEAM 4 (soak-gate-mcp-warmup) ─────────────────
                # A promote transaction is live ⇒ DEFER (never drop).
                # These orphans are TASK-LESS, so StaleTaskRecovery
                # cannot reap them: an unreleased deferral would strand
                # them for the whole boot. The hold module releases them
                # through THIS pass's own resume primitive.
                if promote_txn_hold.promote_txn_live():
                    promote_txn_hold.defer_resume(
                        promote_txn_hold.SEAM_ORPHAN_SWEEP,
                        {
                            "instance_id": instance_id,
                            "work_id": synthetic_wid,
                        },
                    )
                    result.deferred_pipeline_busy += 1
                    result.deferred_work_ids.append(synthetic_wid)
                    logger.info(
                        "[BOOT_CONTINUE] orphan-sweep DEFERRED "
                        "instance=%s: reason=%s (promote txn live — the "
                        "hold releases it through the resume primitive)",
                        instance_id[:8], promote_txn_hold.HOLD_REASON,
                    )
                    continue

                resume = await manager._schedule_explicit_handle_resume(  # type: ignore[attr-defined]
                    instance_id=instance_id,
                    message="",
                    silent=True,
                    images=None,
                    target_work_id=synthetic_wid,
                    selected_suspension_reason=None,
                    handle_work_id=synthetic_wid,
                    route_outcome="boot_continue_orphan_sweep",
                    image_refs=None,
                )

                if (
                    resume is None
                    or not isinstance(resume, dict)
                    or resume.get("status") != "resuming"
                ):
                    result.skipped_resume_refused += 1
                    logger.info(
                        "[BOOT_CONTINUE] orphan-sweep SKIPPED "
                        "instance=%s: resume refused (resume=%r)",
                        instance_id[:8], resume,
                    )
                    continue

                result.scheduled += 1
                logger.info(
                    "[BOOT_CONTINUE] orphan-sweep instance=%s "
                    "epoch=%s ckpt_ts=%s mode=%s",
                    instance_id[:8],
                    boot_epoch.isoformat(),
                    ckpt_ts.isoformat(),
                    result.auto_continue_mode,
                )

                # ── Proposal B PR1 — boot-sweep epoch mint (lane 14) ──
                # MINT-IF-NONE: the silent checkpoint resume founds the
                # epoch boundary that "no message, no clear at all" left
                # missing (map §5 gap 5) — ``founding_source='boot'``,
                # ``founding_message_id=NULL`` (the HONEST null: no
                # message exists). An OPEN epoch continues untouched
                # (resume is delivery, not founding); only a stranded
                # instance gets the boundary row. RECORD-SIDE ONLY,
                # FAIL-OPEN (the pass never wedges — the mint failure is
                # a log line and the boot proceeds unchanged).
                try:
                    await asyncio.to_thread(
                        mint_boot_epoch_if_none_fail_open,
                        getattr(manager, "mission_record_service", None),
                        instance_id,
                    )
                except Exception as mint_exc:  # noqa: BLE001 — never wedge
                    logger.warning(
                        "[BOOT_CONTINUE] mission-record mint failed for "
                        "instance=%s: %s: %s (boot proceeds)",
                        instance_id[:8],
                        type(mint_exc).__name__,
                        mint_exc,
                    )

                # Stagger cadence (mirrors AutoContinue Δ5 / D22).
                if result.scheduled % STAGGER_EVERY == 0:
                    await asyncio.sleep(STAGGER_SLEEP_SECONDS)

            except Exception as exc:
                # Per-instance exception (Layer 1) — never raise.
                result.errors += 1
                logger.warning(
                    "[BOOT_CONTINUE] orphan-sweep instance=%s "
                    "failed: %s: %s",
                    instance_id[:8],
                    type(exc).__name__, exc,
                )
                continue
    except Exception as exc:
        # Sweep-level exception (Layer 2) — never raise.
        result.errors += 1
        logger.warning(
            "Boot orphan sweep failed (orphans caught by "
            "StaleTaskRecovery at boot+10min): %s: %s",
            type(exc).__name__, exc,
        )
    finally:
        result.duration_seconds = time.monotonic() - loop_started

    # (6) Observability summary line — always emitted (even when N=0)
    #     so the operator can verify the sweep ran.
    logger.info(
        "[BOOT_CONTINUE] orphan-sweep summary: candidates=%d "
        "scheduled=%d skipped_no_checkpoint=%d skipped_resume_refused=%d "
        "skipped_kill_switch=%d skipped_no_boot_epoch=%d "
        "errors=%d duration_s=%.3f boot_epoch=%s mode=%s",
        result.candidates,
        result.scheduled,
        result.skipped_no_checkpoint,
        result.skipped_resume_refused,
        result.skipped_kill_switch,
        result.skipped_no_boot_epoch,
        result.errors,
        result.duration_seconds,
        boot_epoch.isoformat(),
        result.auto_continue_mode,
    )
    return result