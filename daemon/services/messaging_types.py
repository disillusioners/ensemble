"""Leaf types for the instance messaging subsystem.

This module deliberately has NO heavyweight imports so it can be safely
imported from anywhere in the codebase without pulling in the
``daemon.manager`` import chain (which transitively depends on the
optional ``langchain_mcp_adapters`` package).

The single type declared here, :class:`AsyncMessageResult`, is the
return value of ``enqueue_message`` and is re-exported from
``daemon.manager`` for backward compatibility with consumers that
import it via ``from daemon.manager import AsyncMessageResult``.
"""

import logging
from dataclasses import dataclass


@dataclass
class AsyncMessageResult:
    """Result of async message enqueue.

    The ``job_id`` field carries the cross-system linkage handle
    (``Task.work_id`` == ``JobItem.job_id``) on the POC path; ``None``
    for callers that don't go through ``enqueue_message_job``.

    This dataclass is the canonical definition. Earlier code paths
    kept fallback copies at module level; those were removed because
    they broke type identity (different ``AsyncMessageResult`` classes
    compared unequal even when fields matched). All call sites and
    tests now resolve to this single class via the
    ``daemon.services.messaging_types`` import.
    """

    message_id: str
    instance_id: str
    status: str = "queued"
    job_id: str | None = None  # job_id of the enqueued MESSAGE job (None for non-JQ paths)
    queued: bool = False


def _assert_linkage_contract(
    result: AsyncMessageResult | None,
    job_id: str,
    *,
    source: str,
    logger: logging.Logger,
) -> None:
    """Shared linkage-contract tripwire (f1-misfire batch, council W1).

    Every JobItem-driven ``enqueue_message`` dispatch MUST pass
    ``work_id=job_id`` so the driving Task links to its JobItem (the
    documented ``Task.work_id == JobItem.job_id`` contract). The
    dispatch result's ``job_id`` IS the minted Task's ``work_id`` — a
    mismatch against the driving JobItem means the mint re-keyed the
    Task and recovery surfaces (Pattern-f1 ``get_by_work_id``,
    the work resolver) will miss it.

    WARN loudly; NEVER fail the dispatch — this is a
    future-regression detector, not a gate. Single home for the
    tripwire semantics shared by ``JobFeedbackObserver._trigger_next_job``
    and ``JobProcessor._process_next_job`` (main TASK dispatch +
    the crash-recovery / orphan-resume re-spawn sites).
    """
    if result is None or not getattr(result, "job_id", None):
        return
    if result.job_id == job_id:
        return
    logger.warning(
        f"{source}: LINKAGE CONTRACT VIOLATION — task-job dispatch "
        f"for JobItem {job_id[:8]}... minted Task work_id "
        f"{result.job_id[:8]}... (Task.work_id != JobItem.job_id). "
        f"Recovery lookups keyed by work_id will miss this Task; "
        f"investigate the dispatch path."
    )


__all__ = ["AsyncMessageResult"]
