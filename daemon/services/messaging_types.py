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


__all__ = ["AsyncMessageResult"]
