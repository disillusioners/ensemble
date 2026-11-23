"""Boot-consistency reconciler (fix/post-outage-recovery SCOPE b).

Adjudicated design — implement faithfully. This lane reconciles the durable
row-state of RUNNING instances at boot so that no instance is ever left in
``status='running'`` with nothing to consume and no row-state decision.

Post-outage RCA (2026-11-23 06:17–08:21Z outage): the boot-window queue
recovery/GC leg deletes the durable mq/task rows that RUNNING instance rows
depend on, while no instance-side boot seam reconciles the row state. The
trace is gone (Event rows survive) and the instance row stays RUNNING with
nothing to consume. This lane is that missing boot seam.

Structural invariants (pin-enforced, do not weaken)
----------------------------------------------------
* **100% PG-derived candidate set.** The ONLY candidate source is
  ``task_repo.find_boot_consistency_candidates(boot_epoch)`` — a single
  SQL query over instances/event/message_queue/task. There is NO in-memory
  candidate source: this module never reads ``_graph_tasks`` /
  ``_pending_injections`` / ``_live_children`` as candidate input.
* **No enqueue.** This lane never calls ``enqueue_message``; Tier 1
  re-mints rows via the repository and re-drives via the checkpoint-resume
  primitive only.
* **boot_epoch only.** No ``datetime.now`` in this module — the epoch is
  the sole time frame (naive-UTC). Timestamps on re-minted rows come from
  the durable Event.data stamp (repository-side), not wall clock here.
* **Single resume call site.** Exactly one textual
  ``_schedule_explicit_handle_resume(`` call, funnelled through
  :func:`_drive_resume`, shared by every re-drive disposition.
* **No resume_instance_cascade.** PAUSED rows are excluded at the
  selection subquery (``status='running'``) and here; this lane has no
  code path into the resume-cascade lane.

Dispositions
------------
* ``deleted_trace`` + Event.data.content non-empty → **Tier 1**: re-mint
  Task + MessageQueue rows from the durable Event, re-drive via
  ``_schedule_explicit_handle_resume(silent=True)``. Ghosts (no
  checkpoint) are dispatch-capable Tier 1.
* ``deleted_trace`` + Event absent / content empty → **Tier 2**:
  ``terminate_instance(terminal_reason='orphan_retired')`` with an
  ERROR-level log carrying surviving instance metadata. NEVER a silent
  skip.
* ``blocked_claim`` → continue-in-place re-drive against the stale RUNNING
  task's work_id. The stale RUNNING task is NEVER force-cancelled (STR owns
  reap via ``find_stale_running_tasks`` with the boot-epoch clamp).
* ``completed_task`` ROOT (``parent_id IS NULL``) → DEFER to
  ``mission_strand_healer`` (skip + ``deferred_to_healer`` log). No double
  disposition.
* ``completed_task`` NON-ROOT → finalize the instance to COMPLETED via the
  guarded terminalizer pattern (no resume).

CAS single-writer
-----------------
``instances.boot_re_drive_at`` is stamped AFTER a successful disposition
(rowcount==1 CAS, ``boot_re_drive_at IS NULL OR < boot_epoch``). Stamping
after means "marked" ≡ "re-driven" — no marked-but-never-re-driven window.
The residual gap (crash between disposition and stamp) re-arms next boot.

Kill-switch (rationale)
-----------------------
Reuses ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`` (default ON, ``=0``
disables, read per boot via ``os.environ`` through the shared
``_auto_continue_enabled`` helper). This is intentional: post-restart
recovery of interrupted turns is the SAME logical action as auto-continue,
so one operator switch covers both (one-switch mental model). Sibling
precedent: ``ENSEMBLE_HARD_REBOOT_BOOT_PASS`` (message_queue_boot_recovery,
default ON). To disable: set the env to ``=0`` in the daemon .env and
restart.

Fail loudly (doctrine)
----------------------
On DB/unreachable errors the lane logs at ERROR with the structured marker
``[BOOT_RECOVERY_DB_ERROR] pass=<name> errors=<n>``, increments ``errors``,
and NEVER raises out of the lifespan (three-layer never-wedge: per-candidate
→ sweep-level → lifespan envelope). The readyz composite gains a
``boot_recovery`` leg: ``errors>0`` ⇒ ``degraded`` (503-with-reason) while
the daemon still boots; ``errors==0`` ⇒ ``healthy``. The leg is published
through :func:`get_boot_recovery_state` and consumed by
``daemon/services/readiness.py``.

Adjacent gap (NOT expanded — documented only)
----------------------------------------------
``DependencyBus.start()`` raises to its caller in
``init_dependency_bus`` (``api.py``), but that caller catches at WARNING
and continues without the bus (``api.py:6230``). That is a log-and-continue
posture adjacent to boot fail-loudness. This lane does NOT change it — it
is noted here as a known adjacent gap for a future commission.

Ordering
--------
Wired in ``daemon/api.py`` immediately AFTER ``sweep_orphaned_live_turns``
in the lifespan BODY block. It is a sibling of the auto-continue /
orphan-sweep passes, shares their kill-switch, and never runs in parallel
with them within one boot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from daemon.services.auto_continue_boot_pass import (
    AUTO_CONTINUE_KILL_SWITCH_ENV,
    STAGGER_EVERY,
    STAGGER_SLEEP_SECONDS,
    _auto_continue_enabled,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager


# ---------------------------------------------------------------------------
# Result accounting (mirrors ContinueResult)
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    """Per-boot totals. Every counter zero-initialized for clean fast paths.

    ``errors`` is the boot-never-wedge / fail-loudly contract: every
    catchable failure increments it; the lane NEVER raises out. It also
    drives the readyz ``boot_recovery`` leg.
    """

    candidates: int = 0
    #: Tier 1 — Event content re-minted + silent resume scheduled.
    dispatched: int = 0
    #: Tier 2 — orphan retired via terminate_instance(orphan_retired).
    retired: int = 0
    #: blocked_claim — continue-in-place re-drive scheduled.
    redriven: int = 0
    #: completed_task non-root — finalized to COMPLETED.
    finalized: int = 0
    #: completed_task root — deferred to mission_strand_healer.
    deferred_to_healer: int = 0
    #: Ghost (no checkpoint) Tier-1 dispatches (subset of ``dispatched``).
    ghost_dispatched: int = 0
    skipped_no_content: int = 0
    skipped_resume_refused: int = 0
    skipped_disposition_refused: int = 0
    skipped_kill_switch: int = 0
    skipped_no_boot_epoch: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    #: The effective kill-switch mode this pass read ("on"/"off").
    mode: str = "unknown"


# ---------------------------------------------------------------------------
# Process-global readyz leg state
# ---------------------------------------------------------------------------

#: The last boot-consistency lane outcome for THIS process. The lane runs
#: once at boot; readiness reads this on every refresh tick. Defaults to
#: healthy so a boot where the lane never ran (older callers, tests) does
#: not degrade readyz.
_BOOT_RECOVERY_STATE: dict[str, Any] = {
    "ran": False,
    "ok": True,
    "status": "healthy",
    "errors": 0,
    "pass": None,
    "boot_epoch": None,
}


def get_boot_recovery_state() -> dict[str, Any]:
    """Return a COPY of the last lane outcome (readiness / tests / ops)."""
    return dict(_BOOT_RECOVERY_STATE)


def get_boot_recovery_leg() -> str:
    """The readyz leg value: ``"healthy"`` or ``"degraded"``."""
    return str(_BOOT_RECOVERY_STATE["status"])


def reset_boot_recovery_state() -> None:
    """Test/ops seam — restore the pre-boot healthy default."""
    global _BOOT_RECOVERY_STATE
    _BOOT_RECOVERY_STATE = {
        "ran": False,
        "ok": True,
        "status": "healthy",
        "errors": 0,
        "pass": None,
        "boot_epoch": None,
    }


def _publish_readyz_state(result: ReconcileResult, boot_epoch: datetime | None) -> None:
    """Publish the lane outcome to the process-global readyz leg."""
    global _BOOT_RECOVERY_STATE
    errors = result.errors
    _BOOT_RECOVERY_STATE = {
        "ran": True,
        "ok": errors == 0,
        "status": "degraded" if errors else "healthy",
        "errors": errors,
        "pass": "boot_consistency_reconciler",
        "boot_epoch": boot_epoch.isoformat() if boot_epoch is not None else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_db_error(pass_name: str, result: ReconcileResult, exc: BaseException, **ctx: Any) -> None:
    """ERROR-level fail-loudly log with the structured marker."""
    detail = " ".join(f"{k}={v}" for k, v in ctx.items())
    logger.error(
        "[BOOT_RECOVERY_DB_ERROR] pass=%s errors=%d %s: %s: %s",
        pass_name,
        result.errors,
        detail,
        type(exc).__name__,
        exc,
    )


def _parse_event_payload(event_data: Any) -> dict[str, Any] | None:
    """Parse an Event.data JSON string into a dict (None on bad/absent data)."""
    if not event_data:
        return None
    if isinstance(event_data, dict):
        return event_data
    if isinstance(event_data, str):
        try:
            parsed = json.loads(event_data)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def _candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    """Surviving instance metadata for the Tier-2 loud log."""
    raw = candidate.get("metadata")
    meta: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                meta = loaded
        except (ValueError, TypeError):
            pass
    elif isinstance(raw, dict):
        meta = raw
    return {
        "title": meta.get("title"),
        "initiative_message": meta.get("initiative_message"),
        "parent_id": candidate.get("parent_id"),
        "agent_id": candidate.get("agent_id"),
        "agent_version": candidate.get("agent_tag"),
        "version": candidate.get("version"),
        "created_at": candidate.get("created_at"),
    }


def _resume_accepted(resume: Any) -> bool:
    """True iff the resume primitive accepted the schedule."""
    return isinstance(resume, dict) and resume.get("status") == "resuming"


async def _drive_resume(
    manager: "InstanceManager",
    instance_id: str,
    work_id: str,
    route: str,
) -> Any:
    """THE single resume call site (see module structural invariant)."""
    return await manager._schedule_explicit_handle_resume(  # type: ignore[attr-defined]
        instance_id=instance_id,
        message="",
        silent=True,
        images=None,
        target_work_id=work_id,
        selected_suspension_reason=None,
        handle_work_id=work_id,
        route_outcome=route,
        image_refs=None,
    )


# ---------------------------------------------------------------------------
# Main lane
# ---------------------------------------------------------------------------


async def reconcile_boot_consistency(
    manager: "InstanceManager",
    boot_epoch: datetime | None,
) -> ReconcileResult:
    """Reconcile RUNNING instances with no durable work at boot.

    Args:
        manager: The ``InstanceManager``. Uses ``_task_repo`` (selection,
            CAS, re-mint, finalize), ``_has_checkpoint`` (ghost detection),
            ``_schedule_explicit_handle_resume`` (re-drive) and
            ``terminate_instance`` (Tier-2 retire).
        boot_epoch: Process-global boot epoch (naive-UTC). ``None`` ⇒
            skip counter (``skipped_no_boot_epoch``), never fail-closed —
            STR backstop owns.

    Returns:
        ``ReconcileResult`` — every counter zero-initialized in the fast
        paths. Never raises out of the lifespan.
    """
    result = ReconcileResult()
    try:
        # (1) Kill-switch (shared with auto-continue — same logical action).
        enabled = _auto_continue_enabled()
        result.mode = "on" if enabled else "off"
        if not enabled:
            result.skipped_kill_switch = 1
            logger.info(
                "[BOOT_RECOVERY] OFF: %s=0 — lane skipped (StaleTaskRecovery "
                "backstop owns orphans) mode=%s",
                AUTO_CONTINUE_KILL_SWITCH_ENV,
                result.mode,
            )
            return result

        # (2) boot_epoch None → SKIP (never fail-closed; STR backstop).
        if boot_epoch is None:
            result.skipped_no_boot_epoch = 1
            logger.warning(
                "[BOOT_RECOVERY] SKIPPED: boot_epoch=None — StaleTaskRecovery "
                "backstop will catch orphans at boot+10min (never-fail-closed)"
            )
            return result

        # (3) Repo handle (loud-fail assertion — wired by initialize()).
        task_repo = manager._task_repo  # type: ignore[attr-defined]
        assert task_repo is not None, (
            "Boot-consistency reconciler requires _task_repo to be wired by "
            "initialize() before boot subsystems run"
        )

        loop_started = time.monotonic()
        try:
            # (4) Selection — 100% DB-derived, single query.
            try:
                candidates = await asyncio.to_thread(
                    task_repo.find_boot_consistency_candidates, boot_epoch
                )
            except Exception as exc:  # noqa: BLE001 — fail loudly, never raise
                result.errors += 1
                _log_db_error("selection", result, exc)
                result.duration_seconds = time.monotonic() - loop_started
                return result

            result.candidates = len(candidates)

            dispositions = 0
            for candidate in candidates:
                try:
                    family = candidate.get("family")
                    instance_id = candidate.get("instance_id")
                    if not instance_id:
                        continue

                    # ── completed_task: healer deference / finalize ──────
                    if family == "completed_task":
                        if candidate.get("parent_id") is None:
                            result.deferred_to_healer += 1
                            logger.info(
                                "[BOOT_RECOVERY] deferred_to_healer "
                                "instance=%s (completed-task ROOT — owned by "
                                "mission_strand_healer; no disposition)",
                                str(instance_id)[:8],
                            )
                            continue
                        finalized = await asyncio.to_thread(
                            task_repo.finalize_boot_consistency_instance,
                            instance_id,
                            candidate.get("version"),
                        )
                        if not finalized:
                            result.skipped_disposition_refused += 1
                            logger.info(
                                "[BOOT_RECOVERY] SKIPPED finalize instance=%s: "
                                "CAS declined (concurrent terminalizer won)",
                                str(instance_id)[:8],
                            )
                            continue
                        stamped = await asyncio.to_thread(
                            task_repo.stamp_boot_re_drive,
                            instance_id, boot_epoch, False,  # finalized: no status guard
                        )
                        if not stamped:
                            result.skipped_disposition_refused += 1
                            continue
                        result.finalized += 1
                        dispositions += 1
                        logger.info(
                            "[BOOT_RECOVERY] finalized instance=%s epoch=%s",
                            str(instance_id)[:8], boot_epoch.isoformat(),
                        )

                    # ── deleted_trace: Tier 1 / Tier 2 ──────────────────
                    elif family == "deleted_trace":
                        payload = _parse_event_payload(candidate.get("event_data"))
                        content = (payload or {}).get("content")
                        if not payload or not isinstance(content, str) or not content.strip():
                            # Tier 2 — retire with a LOUD log. Never silent.
                            meta = _candidate_metadata(candidate)
                            logger.error(
                                "[BOOT_RECOVERY] Tier-2 RETIRE orphan_retired "
                                "instance=%s metadata=%s (event absent or "
                                "content empty — no durable trace to re-mint)",
                                str(instance_id)[:8], meta,
                            )
                            retired = await manager.terminate_instance(  # type: ignore[attr-defined]
                                instance_id, terminal_reason="orphan_retired"
                            )
                            if not retired:
                                result.skipped_disposition_refused += 1
                                logger.error(
                                    "[BOOT_RECOVERY] retire refused instance=%s "
                                    "(terminate_instance returned false)",
                                    str(instance_id)[:8],
                                )
                                continue
                            stamped = await asyncio.to_thread(
                                task_repo.stamp_boot_re_drive, instance_id, boot_epoch
                            )
                            if not stamped:
                                result.skipped_disposition_refused += 1
                                continue
                            result.retired += 1
                            dispositions += 1
                        else:
                            # Tier 1 — re-mint from the durable Event, then
                            # re-drive. Ghost detection is informational:
                            # ghosts WITH content are dispatch-capable.
                            has_ckpt = await manager._has_checkpoint(instance_id)  # type: ignore[attr-defined]
                            # Ghost detection is informational: a ghost WITH
                            # event content is still dispatch-capable Tier 1.
                            work_id = await asyncio.to_thread(
                                task_repo.remint_boot_consistency_work,
                                instance_id,
                                payload,
                            )
                            resume = await _drive_resume(
                                manager, instance_id, work_id,
                                "boot_consistency_deleted_trace",
                            )
                            if not _resume_accepted(resume):
                                result.skipped_resume_refused += 1
                                logger.info(
                                    "[BOOT_RECOVERY] SKIPPED Tier-1 instance=%s: "
                                    "resume refused (resume=%r)",
                                    str(instance_id)[:8], resume,
                                )
                                continue
                            stamped = await asyncio.to_thread(
                                task_repo.stamp_boot_re_drive, instance_id, boot_epoch
                            )
                            if not stamped:
                                result.skipped_resume_refused += 1
                                continue
                            result.dispatched += 1
                            if not has_ckpt:
                                result.ghost_dispatched += 1
                            dispositions += 1
                            logger.info(
                                "[BOOT_RECOVERY] Tier-1 dispatched instance=%s "
                                "work_id=%s ghost=%s epoch=%s",
                                str(instance_id)[:8], str(work_id)[:8],
                                not has_ckpt, boot_epoch.isoformat(),
                            )

                    # ── blocked_claim: continue-in-place re-drive ────────
                    elif family == "blocked_claim":
                        work_id = candidate.get("work_id")
                        if not work_id:
                            result.skipped_resume_refused += 1
                            continue
                        resume = await _drive_resume(
                            manager, instance_id, work_id,
                            "boot_consistency_blocked_claim",
                        )
                        if not _resume_accepted(resume):
                            result.skipped_resume_refused += 1
                            logger.info(
                                "[BOOT_RECOVERY] SKIPPED blocked_claim "
                                "instance=%s: resume refused (resume=%r)",
                                str(instance_id)[:8], resume,
                            )
                            continue
                        stamped = await asyncio.to_thread(
                            task_repo.stamp_boot_re_drive, instance_id, boot_epoch
                        )
                        if not stamped:
                            result.skipped_resume_refused += 1
                            continue
                        result.redriven += 1
                        dispositions += 1
                        logger.info(
                            "[BOOT_RECOVERY] blocked_claim re-driven instance=%s "
                            "work_id=%s epoch=%s (stale RUNNING task NOT cancelled)",
                            str(instance_id)[:8], str(work_id)[:8],
                            boot_epoch.isoformat(),
                        )

                    # Stagger cadence (mirrors the sibling passes).
                    if dispositions and dispositions % STAGGER_EVERY == 0:
                        await asyncio.sleep(STAGGER_SLEEP_SECONDS)

                except Exception as exc:  # noqa: BLE001 — per-candidate isolation
                    result.errors += 1
                    _log_db_error(
                        "candidate", result, exc,
                        instance=str(candidate.get("instance_id"))[:8],
                        family=candidate.get("family"),
                    )
                    continue
        except Exception as exc:  # noqa: BLE001 — sweep-level isolation
            result.errors += 1
            _log_db_error("sweep", result, exc)
        finally:
            result.duration_seconds = time.monotonic() - loop_started

        logger.info(
            "[BOOT_RECOVERY] summary: candidates=%d dispatched=%d retired=%d "
            "redriven=%d finalized=%d deferred_to_healer=%d "
            "ghost_dispatched=%d errors=%d duration_s=%.3f epoch=%s mode=%s "
            "readyz_leg=%s",
            result.candidates, result.dispatched, result.retired,
            result.redriven, result.finalized, result.deferred_to_healer,
            result.ghost_dispatched, result.errors, result.duration_seconds,
            boot_epoch.isoformat(), result.mode, get_boot_recovery_leg(),
        )
        return result
    finally:
        # Publish the readyz leg on EVERY return path (incl. kill-switch /
        # epoch-None fast paths — those are healthy, not degraded).
        _publish_readyz_state(result, boot_epoch)
