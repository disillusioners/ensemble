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
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


#: Marker that introduces the machine-readable trigger block appended to
#: every blueprinter job message.
#:
#: The block is the ONLY channel through which the blueprinter can
#: obtain the coordinator's lease token: the job→agent delivery path
#: forwards only the reserved ``DISPATCH_CHAIN_KEY`` in the message
#: metadata, and the blueprinter has no job-reading tool. Putting the
#: payload in the message BODY is what makes the agent-side release
#: seam reachable at all.
TRIGGER_BLOCK_MARKER = "[blueprint-trigger]"


def build_trigger_block(
    *,
    trigger_type: str,
    run_token: str | None = None,
    blueprint_id: str | None = None,
) -> str:
    """Render the machine-readable trigger block for a blueprinter job.

    The lease token travels under the key ``lease_token`` — NOT
    ``run_token``. Two unrelated tokens are called "run_token" in the
    agent's context: the coordinator's lease token (this one) and the
    pending-queue claim token minted by ``blueprint_claim_pending``. The
    distinct name keeps them unambiguous.

    Args:
        trigger_type: The trigger that produced this job (e.g.
            ``"rebuild"``, ``"incremental"``, ``"single"``).
        run_token: The coordinator lease token, when the job holds a
            lease. ``None`` for triggers that bypass the coordinator.
        blueprint_id: The target blueprint id for the ``"single"``
            trigger mode, when known.

    Returns:
        The block text (marker + one JSON line).
    """
    payload = {
        "trigger": trigger_type,
        "lease_token": run_token,
        "blueprint_id": blueprint_id,
    }
    return f"{TRIGGER_BLOCK_MARKER}\n{json.dumps(payload, sort_keys=True)}"


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
    blueprint_id: str | None = None,
) -> str:
    """Look up the background queue and enqueue a blueprinter job.

    Returns the ``job_id`` of the enqueued job. Raises
    :class:`BlueprintEnqueueError` when the queue lookup or enqueue
    fails — callers should release any coordinator lease they hold.

    Args:
        job_queue_service: The JobQueueService instance.
        project_id: Project whose blueprints are being built.
        trigger_type: Metadata ``trigger`` value (e.g. ``"rebuild"``).
        message: The agent prompt body sent to the blueprinter job. The
            machine-readable ``[blueprint-trigger]`` block is APPENDED
            to it (see :func:`build_trigger_block`).
        run_token: Optional lease token from the C7 coordinator. When
            provided, it is stored in the job metadata (the observer
            needs it at terminalization) AND delivered to the agent in
            the ``[blueprint-trigger]`` block at the end of the message
            body (the agent needs it to release the lease itself).
        job_id: Optional explicit JobItem UUID. When provided, it is
            forwarded to ``enqueue(job_id=...)`` so the enqueued job's
            id matches the lease stored on the project row by
            ``coordinator.try_claim(job_id=...)``. When ``None``,
            ``enqueue()`` generates its own UUID.
        source: Job ``source`` value. ``"admin-endpoint"`` for the REST
            router path (default), ``"auto-scan"`` for the scan service.
        blueprint_id: Optional target blueprint id for the
            ``"single"`` trigger mode. When set, the blueprinter uses
            ``blueprint_get(blueprint_id)`` to fetch the live record at
            the start of its run (we do NOT embed content or file_refs
            in the metadata — that would create a stale-snapshot race).

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
    if blueprint_id:
        # Target blueprint id for the "single" trigger mode. The blueprinter
        # fetches the live record via blueprint_get(blueprint_id) at the
        # start of its run; we deliberately do NOT embed content or file_refs
        # here (that would create a stale-snapshot race by the time the
        # worker runs).
        metadata["blueprint_id"] = blueprint_id

    # The trigger block goes at the END of the message body — the message
    # IS the agent's prompt, so the existing instruction text is left
    # untouched and the payload is appended below it. Every trigger mode
    # gets one (rebuild / incremental / single / auto-scan), including
    # the modes whose message bodies carry no trigger marker of their
    # own.
    message = (
        f"{message.rstrip()}\n\n"
        f"{build_trigger_block(trigger_type=trigger_type, run_token=run_token, blueprint_id=blueprint_id)}"
    )

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
