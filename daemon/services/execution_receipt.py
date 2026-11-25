"""Delivery-completed callback for the Addendum A execution receipt.

The adapter observes enqueues, never completions — the missing
delivery-completed callback the ``_last_execution_status`` docstring names
(``scheduling_service.py:640-643``). This module is that callback's write
side: when the spawned Task/JobItem reaches a terminal state, the observer
calls the factory-produced coroutine, which closes the matching receipt
(``dispatched`` → ``executed`` + outcome) keyed by the durable
``job_item_id`` handle.

BOUNDED / FAIL-SOFT (never wedges the completing job)
-----------------------------------------------------
* one indexed SELECT + one guarded UPDATE on the source repo (no graph,
  no enqueue, no cross-service calls);
* every exception is caught and logged — the callback NEVER raises into
  the observer's terminal cascade;
* the repo's ``record_execution_terminal_by_job`` is idempotent and keyed
  by the OPEN states, so a duplicate/late call is a no-op.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from daemon.repositories.source.repository import SQLModelSourceRepository

logger = logging.getLogger(__name__)

#: The observer-injected callback signature: (job_item_id, terminal_status).
ExecutionReceiptCallback = Callable[[str, str], Awaitable[dict[str, Any]]]


def make_execution_receipt_callback(
    source_repo: "SQLModelSourceRepository | Any",
) -> ExecutionReceiptCallback:
    """Build the bounded, fail-soft delivery-completed callback.

    Args:
        source_repo: The source repository exposing
            :meth:`record_execution_terminal_by_job`.

    Returns:
        An async callback ``(job_item_id, terminal_status) -> dict``. The
        result is an observability payload (``closed`` / ``error``), never
        an exception — the caller cannot be wedged by a receipt failure.
    """
    async def _on_job_terminal(job_item_id: str, terminal_status: str) -> dict[str, Any]:
        try:
            row = await asyncio.to_thread(
                source_repo.record_execution_terminal_by_job,
                job_item_id,
                terminal_status,
            )
            closed = row is not None
            if closed:
                logger.info(
                    "[EXECUTION_RECEIPT] closed job=%s outcome=%s",
                    str(job_item_id)[:8], terminal_status,
                )
            return {"closed": closed, "outcome": terminal_status, "job_item_id": job_item_id}
        except Exception as exc:  # noqa: BLE001 — fail-soft, never wedge
            logger.warning(
                "[EXECUTION_RECEIPT_WRITE_FAILED] job=%s outcome=%s: %s: %s",
                str(job_item_id)[:8], terminal_status,
                type(exc).__name__, exc,
            )
            return {
                "closed": False,
                "outcome": terminal_status,
                "job_item_id": job_item_id,
                "error": str(exc),
            }

    return _on_job_terminal


__all__ = ["ExecutionReceiptCallback", "make_execution_receipt_callback"]
