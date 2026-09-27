"""Boot-time sweep of orphaned ``running`` rows — AM-7 / T8.

Lifespan-start CAS — flips every ``maintenance_runs`` row with
``status='running'`` to ``status='interrupted'`` with
``error_json.code='run_interrupted'``. Unconditional, NO age gate (a
boot-time ``running`` row is an orphan by definition under the
single-daemon assumption; an age gate would orphan young rows).

Pattern precedent: ``plane_sync_watchdog_service.py::fail_stale_syncing``
(``daemon/services/plane_sync_watchdog_service.py:299-316``) — same
crash-wedge CAS, same rowcount-guarded summary line.

Boot-sweep-only — there is NO live stale-running watchdog in v1
(unlike PlaneSync, no live foreign process can own the row). Resume
is rejected by design: prune is retention-idempotent (re-run
converges; per-pair independence means a partial pass finishes next
run).

W1 (v3 fix pass 2026-09-27): the boot call site wraps the sweep in
``run_boot_sweep_with_retry`` — a failed sweep leaves a stale
``running`` row that 409-wedges cleanup, so transient boot-time DB
hiccups get bounded retries; the exhaustion path logs the manual
heal line (see ``docs/runbooks/maintenance-console.md``). The sweep
call site is in ``manager.initialize()`` immediately BEFORE
``_maintenance_service.start()`` so the auto cycle's first tick can
never race the sweep (W2 — structural ordering, not the loop's
60s initial sleep).
"""

from __future__ import annotations

import logging

from daemon.repositories.maintenance_runs import MaintenanceRunsRepository

logger = logging.getLogger(__name__)


async def sweep_interrupted_running_runs(
    runs_repo: MaintenanceRunsRepository,
    section: str = "checkpoint-cleanup",
) -> int:
    """Flip every ``running`` row in ``section`` to ``interrupted``.

    Returns the rowcount (the rowcount guard). Unconditional, no age
    gate (AM-7). Emits exactly ONE summary log line — including when
    the count is 0 (test 35/61 pin "one summary log line"; a silent
    zero-run boot is indistinguishable from a sweep that never ran).
    """
    import asyncio

    interrupted = await asyncio.to_thread(
        runs_repo.cas_running_to_interrupted, section
    )
    logger.info(
        "maintenance boot sweep: %d interrupted run(s) in section=%s",
        interrupted,
        section,
    )
    return interrupted


async def run_boot_sweep_with_retry(
    runs_repo: MaintenanceRunsRepository,
    section: str = "checkpoint-cleanup",
    *,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> int | None:
    """Boot-site wrapper [W1, v3 fix pass]: bounded retry around the sweep.

    Why: a single failed boot sweep leaves a stale ``running`` row
    that 409-wedges ALL cleanup (dry-run + execute) until the next
    successful boot. A transient DB hiccup at boot therefore becomes
    a feature-wide outage. The wrapper retries the sweep
    ``attempts`` times with linear backoff (``backoff_seconds`` ×
    attempt) and never raises into the boot path; on exhaustion it
    logs ONE ERROR line naming the runbook heal.

    Returns the swept rowcount, or ``None`` when every attempt
    failed (the ERROR line is the operator trace). The one-summary-
    log-line invariant holds: ``sweep_interrupted_running_runs``
    logs its INFO summary exactly once on the successful attempt.
    """
    import asyncio

    for attempt in range(1, attempts + 1):
        try:
            return await sweep_interrupted_running_runs(runs_repo, section)
        except Exception as exc:  # noqa: BLE001 — boot must stay non-fatal
            if attempt < attempts:
                logger.warning(
                    "maintenance boot sweep failed (attempt %d/%d): "
                    "%s: %s — retrying in %.1fs",
                    attempt, attempts, type(exc).__name__, exc,
                    backoff_seconds * attempt,
                )
                await asyncio.sleep(backoff_seconds * attempt)
            else:
                logger.error(
                    "maintenance boot sweep FAILED after %d attempts "
                    "(%s: %s) — stale running rows may 409-wedge cleanup "
                    "until the next successful boot. Manual heal: "
                    "UPDATE maintenance_runs SET status='interrupted' "
                    "WHERE status='running'  (see "
                    "docs/runbooks/maintenance-console.md)",
                    attempts, type(exc).__name__, exc,
                    exc_info=True,
                )
    return None


__all__ = ["sweep_interrupted_running_runs", "run_boot_sweep_with_retry"]
