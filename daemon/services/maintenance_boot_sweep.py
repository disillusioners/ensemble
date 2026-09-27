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


__all__ = ["sweep_interrupted_running_runs"]