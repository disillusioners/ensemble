"""Shared blueprinter job enqueue helper.

Used by both the REST router (``/rebuild``, ``/update``) and the
daemon-side scan service (:class:`BlueprintScanService`) to enqueue
blueprinter jobs on the ``system_background_queue``.

Both surfaces resolve the background queue, build the job metadata, and
call ``JobQueueService.enqueue``. This module owns that logic once so the
two call-sites cannot drift.

The error contract: a single :class:`BlueprintEnqueueError` is raised on
any failure (missing service, missing queue, or enqueue exception). The
router wrapper converts it to an :class:`HTTPException` with the right
status code; the scan service catches it and releases the coordinator
lease.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class BlueprintEnqueueError(Exception):
    """Raised when the blueprinter job cannot be enqueued.

    The message is a plain string (no HTTP concerns) so both the router
    and the scan service can map it to their own error handling. The
    router checks for ``"not available"`` to pick a 503 vs 404 status.
    """


async def enqueue_blueprinter_job(
    job_queue_service: Any,
    project_id: str,
    trigger_type: str,
    message: str,
    run_token: str | None = None,
    job_id: str | None = None,
    source: str = "admin-endpoint",
) -> str:
    """Look up the background queue and enqueue a blueprinter job.

    Returns the ``job_id`` of the enqueued job. Raises
    :class:`BlueprintEnqueueError` when the queue lookup or enqueue
    fails — callers should release any coordinator lease they hold.

    Args:
        job_queue_service: The JobQueueService instance.
        project_id: Project whose blueprints are being built.
        trigger_type: Metadata ``trigger`` value (e.g. ``"rebuild"``).
        message: The agent prompt body sent to the blueprinter job.
        run_token: Optional lease token from the C7 coordinator. When
            provided, stored in the job metadata so the worker can call
            ``coordinator.release()``.
        job_id: Optional explicit JobItem UUID. When provided, it is
            forwarded to ``enqueue(job_id=...)`` so the enqueued job's
            id matches the lease stored on the project row by
            ``coordinator.try_claim(job_id=...)``. When ``None``,
            ``enqueue()`` generates its own UUID.
        source: Job ``source`` value. ``"admin-endpoint"`` for the REST
            router path (default), ``"auto-scan"`` for the scan service.

    Returns:
        The ``job_id`` string of the enqueued job.
    """
    if job_queue_service is None:
        raise BlueprintEnqueueError("JobQueueService not available")

    bg_queue = await asyncio.to_thread(
        job_queue_service._queue_repo.get_by_name,
        project_id,
        "system_background_queue",
    )
    if bg_queue is None:
        raise BlueprintEnqueueError(
            f"system_background_queue not found for project {project_id}"
        )

    metadata: dict[str, Any] = {"trigger": trigger_type, "source": source}
    if run_token:
        metadata["run_token"] = run_token

    try:
        job = await job_queue_service.enqueue(
            agent_id="blueprinter",
            message=message,
            source=source,
            project_id=project_id,
            priority=9,  # lowest priority — pure background
            queue_id=bg_queue.queue_id,
            metadata=metadata,
            job_id=job_id,  # forward so lease and queue agree (no-op when None)
        )
    except Exception as exc:
        raise BlueprintEnqueueError(f"enqueue failed: {exc}") from exc
    return job.job_id
