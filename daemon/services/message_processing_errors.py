"""Shared error handling for message processing failures.

Phase 0 of the CorrelationManager migration. The WorkerPool path
(``daemon/services/task_processor.py``) and the JobQueue path
(``daemon/services/message_job_handler.py``) historically diverged in
their error side-effects: the WorkerPool path wrote an error event to
the DB, published an instance lifecycle event with status="error", and
sent an error report to the parent instance; the JobQueue path only
called ``complete_job(FAILED)`` and dropped the other three side-effects
on the floor. That divergence left parents stuck in
WAITING_CHILDREN and dropped the per-error DB record needed by
downstream observability.

This module is the single source of truth for those side-effects. Both
paths call :func:`handle_message_processing_error` so a root instance
that fails during message processing produces the same external
state regardless of which dispatcher picked the job up.

Design notes:

- **Shared function, not inheritance.** ``ProcessMessageProcessor`` and
  ``MessageJobHandler`` are different classes with no meaningful
  common base for this concern; a free function is cleaner than
  refactoring both into a shared base.

- **Pure helpers first.** :func:`_truncate_error` and
  :func:`_classify_error_type` are pure (no I/O, no shared state) so
  they are safe to import from any context and trivially testable.

- **Idempotent best-effort.** Each side-effect is wrapped in its own
  try/except. If ``_send_error_report`` fails, we still want the
  lifecycle event to fire; if the lifecycle event publish fails, we
  still want the job to be marked FAILED. The error itself is what
  the caller cares about — best-effort reporting is layered on top.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from daemon.constants import MAX_ERROR_LEN

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _truncate_error(error: str, max_len: int = MAX_ERROR_LEN) -> str:
    """Truncate error message, stripping HTML if present.

    Args:
        error: The raw error string (may contain HTML).
        max_len: Maximum length of the returned string. ``"..."`` is
            appended when truncation occurs.

    Returns:
        The cleaned, length-bounded error string.
    """
    if "<" in error and ">" in error:
        error = error.replace("<", " <").replace(">", "> ")
        error = re.sub(r"<[^>]+>", "", error)
        error = " ".join(error.split())
    if len(error) > max_len:
        return error[:max_len] + "..."
    return error


def _classify_error_type(e: Exception) -> str:
    """Classify an exception into an error_type string for ``_send_error_report``.

    Pure function: no I/O, no shared state, no side-effects. Safe to
    import from any context and trivially testable.

    Args:
        e: The exception to classify.

    Returns:
        Error type string (e.g., ``"payload_too_large"``,
        ``"timeout_exhausted"``). The default is ``"execution_error"``
        for any unrecognised exception.
    """
    import openai
    import httpx

    exc_type = type(e)
    exc_name = exc_type.__name__

    # API status errors (includes 413, 401, 403, 404, 400, etc.)
    if isinstance(e, openai.APIStatusError):
        status = getattr(e, 'status_code', None)
        if status == 413:
            return "payload_too_large"
        if status == 401:
            return "authentication_error"
        if status == 403:
            return "forbidden"
        if status == 404:
            return "endpoint_not_found"
        if status == 400:
            return "bad_request"
        if status == 429:
            return "rate_limit"
        if status and 500 <= status < 600:
            return "server_error"
        return f"api_error_{status}" if status else "api_error"

    # Timeout errors
    if isinstance(e, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return "timeout_exhausted"

    # Context length errors
    if exc_name == "ContextLengthExceededError":
        return "context_length_exceeded"

    # Circuit breaker errors
    if exc_name == "CircuitOpenError":
        return "circuit_breaker_open"

    # Connection errors
    if isinstance(e, (openai.APIConnectionError, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "connection_error"

    # Bad request (non-context)
    if isinstance(e, openai.BadRequestError):
        return "bad_request"

    # Validation errors
    if exc_name in ("LLMResponseValidationError", "APIResponseValidationError"):
        return "validation_error"

    # Transient API errors (shouldn't reach here, but just in case)
    if exc_name == "TransientAPIError":
        return "transient_error"

    # Non-status transient channels (bare APIError pattern-match,
    # 200-body ValueError, stream shape — plan work unit 6,
    # docs/plans/transient-channel-retry-widening.md). Same type the
    # TransientAPIError path produces so parents see
    # transient_error/warning after L1 exhaustion instead of
    # invalid_data/execution_error.
    if exc_name == "TransientLLMError":
        return "transient_error"

    # Infrastructure / processing errors (Category C from error catalog)
    if isinstance(e, KeyError):
        return "instance_not_found"
    if isinstance(e, ValueError):
        return "invalid_data"
    if isinstance(e, RuntimeError):
        return "runtime_error"

    # Default
    return "execution_error"


async def handle_message_processing_error(
    instance_manager: Any,
    instance_id: str,
    error: Exception,
    message_id: str | None = None,
    job_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """Unified error handling for message processing failures.

    Both WorkerPool (``ProcessMessageProcessor.process``) and JobQueue
    (``MessageJobHandler.handle``) call this so a root instance that
    fails during message processing produces the same external state
    regardless of which dispatcher picked the job up.

    Performs, in order, the same three side-effects the WorkerPool
    path already had:

    1. **Error event in DB.** Persisted via the EventBus
       (``create_error_event``) or, as a fallback, the EventRepository
       directly. This is what downstream observability keys off of.
    2. **Lifecycle event publish.** ``status="error"`` event routed
       through ``InstanceManager._publish_instance_lifecycle_event``,
       which fans out to the EventBus and wakes
       ``JobFeedbackObserver`` to fire ``notify_watchers`` for any
       linked job. The parent_id is resolved from the instance
       repository so observers can identify the parent.
    3. **Error report to parent.** ``_send_error_report`` performs the
       atomic DB update (child → ERROR, parent counter decrement,
       hierarchy cleanup) and enqueues an ``internal_error_report:``
       message to the parent's queue, which is what actually
       unblocks a parent stuck in ``WAITING_CHILDREN``.

    If ``job_id`` is provided (JobQueue path), the job is also marked
    ``FAILED`` via ``JobQueueService.complete_job``. The WorkerPool
    path completes its own tasks via ``TaskRepository.complete_task``
    and does NOT call this with ``job_id`` set.

    Each side-effect is best-effort: a failure in one is logged and
    does not prevent the others from running. The original exception
    (``error``) is the caller's concern; this helper does not raise.

    Args:
        instance_manager: The ``InstanceManager`` facade. Must expose
            ``_event_bus``, ``_events_service``, ``_instance_repository``,
            ``_publish_instance_lifecycle_event``, and
            ``_send_error_report``. Optionally ``_job_queue_service``
            when ``job_id`` is provided.
        instance_id: The instance that failed during message
            processing.
        error: The exception that was raised.
        message_id: Optional message ID that triggered the error. Used
            by ``_send_error_report`` for dedup and metadata.
        job_id: JobQueue path identifier. If provided, the job is
            marked FAILED via ``JobQueueService.complete_job``.
        task_id: WorkerPool path identifier. Currently included for
            symmetry/logging only — task completion is handled by the
            WorkerPool itself, not here.
    """
    error_msg = _truncate_error(str(error))
    error_type = _classify_error_type(error)
    error_data: dict[str, Any] = {
        "error": error_msg,
        "error_type": error_type,
    }
    if message_id:
        error_data["message_id"] = message_id
    if job_id:
        error_data["job_id"] = job_id
    if task_id:
        error_data["task_id"] = task_id

    try:
        logger.error(
            f"handle_message_processing_error: instance={instance_id[:8]}... "
            f"job_id={str(job_id)[:8] if job_id else 'none'}... "
            f"task_id={str(task_id)[:8] if task_id else 'none'}... "
            f"error_type={error_type} error={error_msg}",
            exc_info=True,
        )
    except (TypeError, AttributeError):
        logger.error(
            f"handle_message_processing_error: instance={instance_id}... "
            f"job_id={job_id} task_id={task_id} "
            f"error_type={error_type} error={error_msg}",
            exc_info=True,
        )

    # 1. Error event in DB
    if getattr(instance_manager, "_event_bus", None):
        try:
            await instance_manager._event_bus.create_error_event(
                instance_id=instance_id,
                error=error_data,
            )
        except Exception as ebus_err:
            logger.warning(
                f"handle_message_processing_error: failed to write error event "
                f"via event_bus for {instance_id[:8]}...: {ebus_err}"
            )
    else:
        # Fallback: write directly via EventRepository if event_bus is missing
        event_repo = getattr(instance_manager, "_event_repo", None)
        if event_repo is not None:
            try:
                await asyncio.to_thread(
                    event_repo.create_event,
                    instance_id=instance_id,
                    kind="error",
                    data=error_data,
                    message_id=message_id,
                )
            except Exception as repo_err:
                logger.warning(
                    f"handle_message_processing_error: failed to write error event "
                    f"via event_repo for {instance_id[:8]}...: {repo_err}"
                )

    # 2. Lifecycle event publish (triggers JobFeedbackObserver cascade)
    publish = getattr(instance_manager, "_publish_instance_lifecycle_event", None)
    if publish is not None:
        try:
            instance_repo = getattr(instance_manager, "_instance_repository", None)
            parent_id = None
            if instance_repo is not None:
                try:
                    meta = instance_repo.get(instance_id)
                    parent_id = getattr(meta, "parent_id", None) if meta else None
                except Exception as meta_err:
                    logger.debug(
                        f"handle_message_processing_error: failed to read "
                        f"parent_id for {instance_id[:8]}... (non-fatal): {meta_err}"
                    )
            await publish(
                instance_id=instance_id,
                status="error",
                error=error_msg,
                parent_id=parent_id,
            )
        except Exception as lifecycle_err:
            logger.warning(
                f"handle_message_processing_error: failed to publish lifecycle "
                f"event for {instance_id[:8]}...: {lifecycle_err}"
            )

    # 3. Error report to parent (triggers parent cascade)
    send_report = getattr(instance_manager, "_send_error_report", None)
    if send_report is not None:
        try:
            await send_report(
                instance_id=instance_id,
                error=error_msg,
                error_type=error_type,
                message_id=message_id,
            )
        except Exception as report_err:
            logger.warning(
                f"handle_message_processing_error: failed to send error report "
                f"to parent for {instance_id[:8]}...: {report_err}"
            )

    # 4. Job completion (JobQueue path only)
    if job_id:
        job_service = getattr(instance_manager, "_job_queue_service", None)
        if job_service is not None:
            try:
                from daemon.services.job_queue_service import DemandState
                await job_service.complete_job(
                    job_id,
                    demand_state=DemandState.FAILED,
                    error=error_msg,
                )
            except Exception as complete_err:
                logger.warning(
                    f"handle_message_processing_error: failed to complete job "
                    f"{str(job_id)[:8]}... as FAILED: {complete_err}"
                )