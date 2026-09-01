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


class LinkageContractError(RuntimeError):
    """Raised when the documented ``Task.work_id == JobItem.job_id``
    linkage contract is violated on a job-driven dispatch.

    Fix A (constitution Phase 0 + approach-comparison.md row A):
    on the job-driven path, ``work_id`` is required (it is the
    JobItem's ``job_id``) and the dispatch result's ``job_id`` MUST
    equal the driving JobItem's ``job_id`` (the dispatched Task's
    ``work_id`` must match the JobItem's handle so recovery lookups
    keyed by ``work_id`` hit the right row). Mismatches on the
    job-driven path now raise instead of silently warning — this
    closes the auto-mint fail-open handle (D4) that allowed the
    2026-08-31 f1-misfire incident to slip through.

    Internal paths (agent-to-agent send_message, cascade-resume,
    child reports — no JobItem) legitimately self-mint and the
    ``enforce=False`` default keeps those callers working unchanged.
    """

    def __init__(
        self,
        *,
        source: str,
        expected_job_id: str,
        actual_job_id: str,
    ) -> None:
        self.source = source
        self.expected_job_id = expected_job_id
        self.actual_job_id = actual_job_id
        super().__init__(
            f"{source}: LINKAGE CONTRACT VIOLATION (job-driven path, "
            f"enforce=True) — driving JobItem {expected_job_id[:8]}... "
            f"but minted Task work_id {actual_job_id[:8]}... "
            f"(Task.work_id != JobItem.job_id). Recovery lookups keyed "
            f"by work_id will miss this Task; investigate the dispatch "
            f"path."
        )


def _assert_linkage_contract(
    result: AsyncMessageResult | None,
    job_id: str,
    *,
    source: str,
    logger: logging.Logger,
    enforce: bool = False,
) -> None:
    """Shared linkage-contract tripwire (f1-misfire batch, council W1).

    Every JobItem-driven ``enqueue_message`` dispatch MUST pass
    ``work_id=job_id`` so the driving Task links to its JobItem (the
    documented ``Task.work_id == JobItem.job_id`` contract). The
    dispatch result's ``job_id`` IS the minted Task's ``work_id`` — a
    mismatch against the driving JobItem means the mint re-keyed the
    Task and recovery surfaces (Pattern-f1 ``get_by_work_id``,
    the work resolver) will miss it.

    Two enforcement modes:

    * **WARN-only (default).** The legacy mode (pre-Fix-A). Mismatches
      log a WARNING and the dispatch proceeds. Used by every legacy
      internal call site that has not yet migrated to the job-driven
      path — keeps the current observable behaviour intact.
    * **Enforce (``enforce=True``).** Fix A enforcement mode. Mismatches
      raise :class:`LinkageContractError` instead of warning. Reserved
      for the four JOB-DRIVEN call sites that pass an explicit
      ``work_id`` from a JobItem (the observer + three JobProcessor
      re-spawn sites + ``enqueue_message_job``).

    Single home for the tripwire semantics shared by
    ``JobFeedbackObserver._trigger_next_job`` and
    ``JobProcessor._process_next_job`` (main TASK dispatch +
    the crash-recovery / orphan-resume re-spawn sites).
    """
    if result is None or not getattr(result, "job_id", None):
        return
    if result.job_id == job_id:
        return
    if enforce:
        raise LinkageContractError(
            source=source,
            expected_job_id=job_id,
            actual_job_id=result.job_id,
        )
    logger.warning(
        f"{source}: LINKAGE CONTRACT VIOLATION — task-job dispatch "
        f"for JobItem {job_id[:8]}... minted Task work_id "
        f"{result.job_id[:8]}... (Task.work_id != JobItem.job_id). "
        f"Recovery lookups keyed by work_id will miss this Task; "
        f"investigate the dispatch path."
    )


__all__ = ["AsyncMessageResult", "LinkageContractError", "_assert_linkage_contract"]
