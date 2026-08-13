"""Path-agnostic pipeline for message processing.

Phase 5 of the CorrelationManager migration. The daemon has two physical
dispatchers that both process user/internal messages against an
instance's langgraph thread:

1. **WorkerPool path** —
   :class:`daemon.services.task_processor.ProcessMessageProcessor` is
   driven by worker threads in ``worker_pool.py`` polling the ``task``
   table. Triggered by ``enqueue_message`` (used by
   ``child_reports._create_completion_report`` for completion reports
   from child instances).

2. **JobQueue path** —
   :class:`daemon.services.message_job_handler.MessageJobHandler` is
   driven by ``JobProcessor._process_loop`` polling the
   ``job_queue_items`` table.

Before Phase 5, the two paths each contained near-identical copies of
the same six shared stages: building the ``_do_process`` closure,
acquiring the Execution Gate lease, marking the message COMPLETED,
resolving the dispatch source and dispatching externally, checking
child completion, and error reporting. Path-specific differences lived
inline in each copy.

This module is the **single source of truth** for those shared stages.
The pipeline takes a :class:`ProcessingContext` and a
:class:`PipelineCallbacks` and produces a :class:`ProcessingResult`.
Path-specific behaviour (how to back off on contention, how to
complete a job vs. a task, how to discriminate pause-vs-terminate
cancellation, what to emit on skip) is supplied as callbacks.

Design notes
------------

- **No behavioral changes.** This is a structural refactor. The
  pipeline must produce identical observable behaviour to what each
  path currently does independently. Any divergence should be fixed
  in a follow-up.

- **Pure pipeline, path-specific callbacks.** The pipeline performs
  the shared stages. Callbacks let each path inject its own behaviour
  at the boundaries: contention handling (jittered backoff for
  WorkerPool; atomic_transition + bus-notify for JobQueue),
  cancellation discrimination (WorkerPool re-raises;
  JobQueue completes the job), and error side-effect hook for
  after the shared error helper runs.

- **Defensive defaults.** The pipeline uses the defensive pattern
  observed in the JobQueue path for ``queue_repository.complete``
  and ``dispatch_completed`` (try/except with warn-log) rather than
  the trust-the-call pattern in the WorkerPool path, because the
  discovery report explicitly recommends the JQ pattern as the
  unified behaviour. Both paths are now equivalent in failure
  handling for these two side-effects.

- **Error handler takes either ``task_id`` or ``job_id``.** The
  pipeline accepts an ``error_handler_id`` dict that the caller
  populates with the appropriate key for its path. This avoids
  adding a single non-required parameter to the pipeline's
  constructor just to hold one of two mutually exclusive IDs.

- **CancellationToken threading.** WorkerPool receives the
  ``cancellation_token`` from the caller (``TaskProcessor.run_task``)
  and threads it through ``_do_process``. JobQueue creates a
  ``CancellationTokenSource`` internally and uses ``cts.token`` for
  the same purpose. The pipeline accepts the token in
  :class:`ProcessingContext` (``cancellation_token`` field, default
  None) so each path supplies the right value: WorkerPool passes
  through the caller's token; JobQueue passes ``cts.token``. The
  pipeline does NOT create its own token — cancellation ownership
  stays with the dispatcher (the path that knows how to translate
  cancellation into the right terminal action).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from daemon.cancellation import OperationCancelledError
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.message_processing_errors import (
    handle_message_processing_error,
)

if TYPE_CHECKING:
    from daemon.cancellation import CancellationToken
    from daemon.manager import MessageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input / output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProcessingContext:
    """Path-agnostic input for message processing.

    Carries everything the pipeline needs to call
    ``manager._process_message_with_tracking`` plus the metadata
    required for the shared post-processing stages
    (mark-completed, dispatch-completed, child-completion-check).

    Path-specific fields:

    - ``cancellation_token``: WorkerPool passes the caller's token
      through; JobQueue passes ``cts.token`` from its internally
      created ``CancellationTokenSource``. The pipeline does NOT
      create or own the token — cancellation ownership stays with
      the dispatcher.

    - ``retry_count``: Read by the pipeline and threaded into
      ``_process_message_with_tracking`` as the ``retry_count`` kwarg.
      WorkerPool reads it from ``task.retry_count``; JobQueue reads
      it from ``job.job_metadata`` with a fallback to
      ``job.retry_count``. The pipeline does not interpret the value.

    Fields default to safe values so callers can construct a context
    incrementally.
    """

    instance_id: str
    message_id: str
    message: str
    retry_count: int = 0
    message_source: str | None = None
    silent: bool = False
    images: list[str] | None = None
    resume_mode: bool = False
    cancellation_token: Optional["CancellationToken"] = None
    task_context: str | None = None  # Pre-formatted [SYSTEM CONTEXT: Task Context] block from send_message(context=...)


@dataclass
class ProcessingResult:
    """Path-agnostic output of message processing.

    ``success`` covers the happy path (graph produced a result AND
    the post-processing side-effects ran without raising a
    dispatcher-fatal error). ``should_defer`` is set by
    ``on_contention`` when a callback successfully re-queued the work
    (e.g. JobQueue's atomic_transition to PENDING, WorkerPool's
    requeue_task_with_backoff) — the dispatcher can short-circuit
    its own post-actions in that case.

    ``error`` is set when an exception bubbled out of the pipeline
    after ``handle_message_processing_error`` ran; the dispatcher
    decides whether to re-raise, log, or swallow.
    """

    success: bool
    result_content: str | None = None
    error: Exception | None = None
    should_defer: bool = False


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


# Type aliases for callbacks. Each callback is async and optional; the
# pipeline applies a default behaviour when a callback is None.
OnSuccessCb = Callable[["ProcessingResult"], Awaitable[None]]
OnErrorCb = Callable[["ProcessingResult"], Awaitable[None]]
OnContentionCb = Callable[[Exception], Awaitable[Optional["ProcessingResult"]]]
OnCancelCb = Callable[[Exception], Awaitable[Optional["ProcessingResult"]]]


@dataclass
class PipelineCallbacks:
    """Optional callbacks for path-specific behaviour at pipeline boundaries.

    Every callback is optional. The pipeline applies the documented
    default when a callback is ``None``. Callbacks that take an
    exception are expected to be idempotent and best-effort: the
    pipeline does not retry failed callbacks.

    Callbacks
    ---------

    ``on_success``
        Called after the happy path completes and the post-processing
        stages have run. The WorkerPool path uses this to call
        ``TaskRepository.complete_task`` (it does NOT do this in the
        pipeline because ``complete_task`` lives on a different repo
        than ``queue_repository``).

    ``on_error``
        Called after ``handle_message_processing_error`` runs. The
        JobQueue path uses this to also complete the job as FAILED
        (already covered by ``handle_message_processing_error`` when
        ``job_id`` is passed, so this is a no-op for JobQueue). The
        WorkerPool path uses this for nothing today — the error
        helper handles the side-effects.

    ``on_contention``
        Defensive callback for any future gate that signals contention.
        Under the asyncio.Lock gate this is never invoked — the second
        caller blocks on the same event loop until the holder releases.
        If invoked, the callback receives the gate's exception and
        returns a :class:`ProcessingResult` (typically
        ``success=False, should_defer=True``) OR ``None`` to signal
        "use the pipeline default of re-raising the exception".

        The WorkerPool path supplies a callback to log per-instance
        throttled summaries and call ``requeue_task_with_backoff``.
        The JobQueue path supplies a callback to call
        ``atomic_transition(PROCESSING→PENDING)`` and notify the
        dispatch bus. If ``on_contention`` is ``None``, the pipeline
        re-raises — matching the pre-Phase-5 behaviour for code paths
        that did not customise contention handling.

    ``on_cancel``
        Called when ``OperationCancelledError`` or
        ``asyncio.CancelledError`` bubbles out of the pipeline. The
        callback receives the exception and returns a
        :class:`ProcessingResult` (typically
        ``success=False, should_defer=True`` for pause) OR ``None``
        to re-raise.

        The WorkerPool path uses this to log "task paused" and
        re-raise. The JobQueue path uses this for the
        pause-vs-terminate discrimination: if the instance is
        ``PAUSED``, complete the job as PAUSED-leave-PROCESSING
        (don't complete); otherwise complete the job as CANCELLED
        and re-raise. If ``on_cancel`` is ``None``, the pipeline
        re-raises.
    """

    on_success: OnSuccessCb | None = None
    on_error: OnErrorCb | None = None
    on_contention: OnContentionCb | None = None
    on_cancel: OnCancelCb | None = None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class MessageProcessingPipeline:
    """Path-agnostic message processing pipeline.

    Encapsulates the six shared stages that both the WorkerPool
    (``ProcessMessageProcessor.process``) and the JobQueue
    (``MessageJobHandler.handle``) perform identically:

    1. Build the ``_do_process`` closure wrapping
       ``manager._process_message_with_tracking``.
    2. Call ``execution_gate.run`` with the supplied ``holder_id`` /
       ``holder_kind`` to serialise ``graph.astream`` per instance.
    3. Defensive ``on_contention`` dispatch (never invoked under the
       asyncio.Lock gate).
    4. Mark the message COMPLETED via ``queue_repository.complete``.
    5. Resolve the dispatch source (internal_report →
       original_source) and call ``source_dispatcher.dispatch_completed``
       (best-effort, JQ guard applied: skip when no valid external
       source).
    6. Check child completion via
       ``manager._process_child_completion_and_notify_parent``
       (best-effort).

    Error reporting is unified via
    :func:`handle_message_processing_error`, which writes the error
    event to the DB, publishes the lifecycle event, sends the error
    report to the parent, and (when ``job_id`` is provided) marks
    the JobQueue job as FAILED.

    Path-specific behaviour lives in :class:`PipelineCallbacks`. The
    pipeline is constructed once per dispatcher and reused for the
    lifetime of the dispatcher.
    """

    def __init__(
        self,
        execution_gate: Any,
        manager: Any,
        source_dispatcher: Any | None = None,
        queue_repository: Any | None = None,
    ) -> None:
        """Initialize the pipeline.

        Args:
            execution_gate: The :class:`ExecutionGateService` instance
                (typically ``manager.execution_gate``). Required —
                the lock acquisition is the pipeline's first shared
                stage.
            manager: The ``InstanceManager`` facade. The pipeline
                uses ``manager._process_message_with_tracking``,
                ``manager._instance_repository``, and
                ``manager._process_child_completion_and_notify_parent``.
            source_dispatcher: Optional
                :class:`daemon.sources.dispatcher.ResponseDispatcher`.
                When ``None``, dispatch is skipped silently (matches
                the WorkerPool behaviour in tests that omit the
                dispatcher).
            queue_repository: Optional message queue repository. Must
                expose ``complete(message_id)``. When ``None``, the
                mark-completed stage is skipped with a warn-log
                (matches the WorkerPool behaviour in tests that
                omit the repo).
        """
        self._execution_gate = execution_gate
        self._manager = manager
        self._source_dispatcher = source_dispatcher
        self._queue_repository = queue_repository

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        context: ProcessingContext,
        holder_id: str,
        holder_kind: str,
        callbacks: PipelineCallbacks,
        error_handler_id: dict[str, str] | None = None,
    ) -> ProcessingResult:
        """Execute the shared pipeline stages for a single message.

        Stages 1–6 (see class docstring) run identically for both
        paths. Path-specific behaviour is supplied via ``callbacks``.

        Args:
            context: The path-agnostic input describing the message
                to process.
            holder_id: Stable identifier for this caller (e.g.
                ``f"task:{task.id}"`` or ``f"message_job:{job_id}"``).
                Accepted by the Execution Gate for diagnostic logging;
                the asyncio.Lock gate does not verify ownership on
                release.
            holder_kind: A short string tag identifying the caller for
                diagnostics (e.g. ``"task"`` or ``"message_job"``).
                The asyncio.Lock gate ignores the value; it is kept
                for log/SSE payloads.
            callbacks: Path-specific behaviour at pipeline boundaries.
                All callbacks are optional; see :class:`PipelineCallbacks`.
            error_handler_id: Optional dict passed to
                :func:`handle_message_processing_error` as kwargs.
                Populate with the appropriate key for the calling
                path: ``{"task_id": str(task.id)}`` for WorkerPool,
                ``{"job_id": job.job_id}`` for JobQueue. When ``None``,
                the error helper runs without a task/job ID (e.g. for
                ad-hoc tests).

        Returns:
            A :class:`ProcessingResult` describing the outcome:

            - Happy path: ``success=True``, ``result_content`` set
              from the langgraph result.
            - Contention handled by ``on_contention``: whatever the
              callback returned (typically
              ``success=False, should_defer=True``).
            - Cancellation handled by ``on_cancel``: whatever the
              callback returned.
            - Generic exception: ``success=False``, ``error`` set,
              ``handle_message_processing_error`` already ran.

        Raises:
            OperationCancelledError: When ``on_cancel`` is ``None``
                and the pipeline catches
                ``OperationCancelledError``.
            asyncio.CancelledError: When ``on_cancel`` is ``None``
                and the pipeline catches ``asyncio.CancelledError``.
        """
        # ---- Stage 1: build the _do_process closure ----
        # The closure captures ``context`` so the caller doesn't need
        # to thread the parameters into the work_fn body. The
        # closure does NOT capture the cancellation token directly —
        # it reads ``context.cancellation_token`` at call time so
        # a late-arriving cancellation (e.g. a new dispatcher that
        # re-uses the pipeline instance) sees the latest value.
        async def _do_process() -> "MessageResult":
            return await self._manager._process_message_with_tracking(
                instance_id=context.instance_id,
                message=context.message,
                message_id=context.message_id,
                cancellation_token=context.cancellation_token,
                is_retry=context.resume_mode,
                retry_count=context.retry_count,
                message_source=context.message_source,
                images=context.images,
                silent=context.silent,
                task_context=context.task_context,
            )

        # ---- Stage 1.5: claim the message (READY -> PROCESSING) ----
        # The downstream ``complete()`` and ``fail()`` guards require the
        # message to be in ``processing`` status — without this transition
        # the message stays in READY forever and ``complete()`` silently
        # no-ops, leaving ``pending_count`` permanently non-zero. That in
        # turn breaks the ``send_message`` in-progress guard in
        # ``daemon/tools/instance.py`` which checks ``get_queue_stats()``.
        # We claim before acquiring the lock so a "stale retry" check
        # doesn't race a still-READY message past the lock boundary.
        # The claim is best-effort: if it fails (message already
        # PROCESSING/COMPLETED/FAILED — e.g. concurrent actor) we still
        # proceed, since the downstream transitions remain safe.
        await self._claim_message(context.message_id)

        # ---- Stage 2: acquire lock + run work_fn ----
        # Under the asyncio.Lock gate the only exit path is the
        # work_fn result. The second caller blocks on the same event
        # loop until the holder releases; there is no contention
        # return path.
        gate_outcome: "MessageResult | None" = await self._execution_gate.run(
            instance_id=context.instance_id,
            holder_id=holder_id,
            holder_kind=holder_kind,
            work_fn=_do_process,
        )

        # ---- Stages 3-6: shared post-processing (inside try/except) ----
        try:
            # Stage 4: mark message COMPLETED. The discovery report
            # recommends the defensive JQ pattern (try/except +
            # warn-log) as the unified behaviour, so both paths now
            # treat a complete() failure as non-fatal.
            await self._mark_message_completed(context.message_id)

            # Stage 5: resolve dispatch source and dispatch. JQ guard
            # applied: skip when the resolved source is missing or
            # still starts with ``internal_``.
            await self._dispatch_completed(context, gate_outcome)

            # Stage 5.5: post-turn parent status transition.
            # Bug fix: when an instance (typically a parent that spawned
            # children) just finished a graph turn, status was left at
            # RUNNING even though the LLM is done and the instance is
            # only waiting on more child reports. The frontend then
            # showed "running" until the last child reported, which
            # was misleading. If the bus still tracks pending watchers
            # for this instance, transition RUNNING → WAITING_CHILDREN
            # and emit the matching SSE.
            #
            # Best-effort + race-safe: uses ``transition_status_if``
            # with ``allowed_from=(RUNNING,)`` so a concurrent
            # terminal write (ERROR/TERMINATED/FAILED from another
            # path) wins. Best-effort on DB/SSE failures — the message
            # itself already succeeded, and the next bus event or
            # child completion will resync the status.
            await self._maybe_transition_to_waiting_children(
                context.instance_id
            )

            # Stage 6: child completion check. Best-effort: a
            # failure here MUST NOT fail the message — the message
            # itself was processed successfully. Mirrors the
            # behaviour in both source paths.
            #
            # Defensive PAUSED re-check (question() tool bug, 2026-07-21):
            # The question() tool's pause cascade may commit PAUSED to the
            # DB during an ``asyncio.shield`` + ``task.cancel()`` timing
            # window that overlaps with this pipeline's post-processing.
            # Without this guard, child completion would overwrite PAUSED
            # with COMPLETED (the root-cause overwrite in the bug). We
            # query the fresh DB status right before invoking child
            # completion — any in-memory cached status is unreliable
            # because the cascade commits outside the task that owns the
            # cached state. If the instance is PAUSED, skip child
            # completion entirely; the next message (resume) will run
            # child completion with a clean state.
            if await self._is_instance_paused(context.instance_id):
                logger.info(
                    f"MessageProcessingPipeline: skipping child completion "
                    f"for {context.instance_id[:8]}... — instance is "
                    f"PAUSED (likely question() tool pause committed "
                    f"during shielded finally block)"
                )
            else:
                await self._check_child_completion(
                    context.instance_id, context.message_id
                )

        except OperationCancelledError as e:
            return await self._handle_cancel(e, callbacks)
        except asyncio.CancelledError as e:
            return await self._handle_cancel(e, callbacks)
        except Exception as e:
            # Generic error: run the unified error helper so DB
            # event + lifecycle event + parent report all fire
            # regardless of which dispatcher picked the job up.
            # Then defer to ``on_error`` for path-specific
            # post-actions (e.g. WorkerPool re-raises; JobQueue
            # would have already had its job marked FAILED by
            # the error helper when ``job_id`` was passed).
            await self._run_error_handler(context, e, error_handler_id)
            if callbacks.on_error is not None:
                try:
                    await callbacks.on_error(
                        ProcessingResult(success=False, error=e)
                    )
                except Exception as cb_err:
                    logger.warning(
                        f"MessageProcessingPipeline: on_error callback "
                        f"raised (non-fatal): {cb_err}"
                    )
            return ProcessingResult(success=False, error=e)

        # ---- Happy path return ----
        # gate_outcome.content is what the dispatcher (WorkerPool/JobQueue)
        # will use to populate task/job completion payloads.
        processing_result = ProcessingResult(
            success=True,
            result_content=gate_outcome.content if gate_outcome else None,
        )
        if callbacks.on_success is not None:
            try:
                await callbacks.on_success(processing_result)
            except Exception as cb_err:
                logger.warning(
                    f"MessageProcessingPipeline: on_success callback "
                    f"raised (non-fatal): {cb_err}"
                )
        return processing_result

    # ------------------------------------------------------------------
    # Internal helpers (the shared post-processing stages)
    # ------------------------------------------------------------------

    async def _claim_message(self, message_id: str | None) -> None:
        """Stage 1.5: transition message READY (or RETRYING-due) -> PROCESSING.

        The downstream ``complete()`` and ``fail()`` guards require
        ``status='processing'`` so the row is mutable; without this step
        the message stays in READY forever (neither WorkerPool nor JobQueue
        dispatchers transitioned it on their own) and ``complete()``
        silently no-ops. That in turn left ``pending_count`` permanently
        non-zero, which broke the ``send_message`` in-progress guard in
        ``daemon/tools/instance.py``.

        Best-effort by design: a failed claim (already PROCESSING /
        COMPLETED / FAILED — e.g. concurrent actor) does NOT block
        processing; the existing downstream guards still handle those
        statuses correctly. When ``queue_repository`` is ``None`` (e.g.
        tests) we skip with a debug log — matches the WorkerPool behaviour
        in tests that omit the repo.
        """
        if not message_id:
            return
        if self._queue_repository is None:
            logger.debug(
                "MessageProcessingPipeline: queue_repository not wired; "
                "skipping message claim"
            )
            return
        try:
            claimed = await asyncio.to_thread(
                self._queue_repository.claim_specific, message_id
            )
            if claimed is None:
                # Either already PROCESSING/COMPLETED/FAILED (concurrent
                # actor) or not in the queue at all (legacy test paths).
                # Both are safe to ignore — downstream stages still work.
                logger.debug(
                    f"MessageProcessingPipeline: claim_specific returned "
                    f"None for message {message_id} (already advanced or "
                    f"missing); continuing"
                )
        except Exception as e:
            logger.warning(
                f"MessageProcessingPipeline: failed to claim message "
                f"{message_id} as processing: {e}"
            )

    async def _mark_message_completed(self, message_id: str | None) -> None:
        """Stage 4: mark the queue message COMPLETED.

        Follows the JQ pattern: wrap in try/except + warn-log so a
        failure here does not fail the message. When
        ``queue_repository`` is ``None`` (e.g. tests), skip with a
        debug log — matches the WorkerPool behaviour in tests that
        omit the repo.
        """
        if not message_id:
            return
        if self._queue_repository is None:
            logger.debug(
                "MessageProcessingPipeline: queue_repository not wired; "
                "skipping message complete()"
            )
            return
        try:
            await asyncio.to_thread(self._queue_repository.complete, message_id)
        except Exception as e:
            logger.warning(
                f"MessageProcessingPipeline: failed to mark message "
                f"{message_id} as completed: {e}"
            )

    async def _dispatch_completed(
        self,
        context: ProcessingContext,
        result: "MessageResult | None",
    ) -> None:
        """Stage 5: resolve dispatch source and dispatch externally.

        Mirrors the JQ implementation exactly: ``internal_report:``,
        ``internal_error_report:``, and ``internal_agent:job_event:``
        sources resolve to the instance's ``original_source`` from
        instance metadata. The JQ guard (skip when the resolved
        source is missing or still starts with ``internal_``) is
        applied to prevent re-dispatching internal noise.
        """
        if self._source_dispatcher is None:
            return
        if result is None:
            return

        dispatch_source = context.message_source
        is_internal_report = (
            context.message_source is not None
            and (
                context.message_source.startswith("internal_report:")
                or context.message_source.startswith("internal_error_report:")
                or context.message_source.startswith("internal_agent:job_event:")
            )
        )
        if is_internal_report:
            try:
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get,
                    context.instance_id,
                )
            except Exception as e:
                logger.warning(
                    f"MessageProcessingPipeline: failed to read instance "
                    f"metadata for dispatch resolution "
                    f"{context.instance_id[:8]}...: {e}"
                )
                instance_meta = None
            if (
                instance_meta is not None
                and getattr(instance_meta, "instance_metadata", None) is not None
            ):
                dispatch_source = instance_meta.instance_metadata.get(
                    "original_source"
                )
            # JQ guard: skip when the resolved source is missing or
            # still internal. This prevents a child agent from
            # re-dispatching internal noise back to the parent's
            # external source.
            if not dispatch_source or (
                isinstance(dispatch_source, str)
                and dispatch_source.startswith("internal_")
            ):
                logger.debug(
                    f"MessageProcessingPipeline: no valid external "
                    f"source for instance {context.instance_id[:8]}... "
                    f"(message_source={context.message_source}); "
                    f"skipping dispatch"
                )
                return

        if not dispatch_source:
            return

        try:
            await self._source_dispatcher.dispatch_completed(
                instance_id=context.instance_id,
                message_id=context.message_id,
                source=dispatch_source,
                content=result.content or "",
                message_type="final",
            )
        except Exception as e:
            logger.error(
                f"MessageProcessingPipeline: error dispatching to external "
                f"source: {e}",
                exc_info=True,
            )
            # Don't fail the task — dispatch is best-effort.

    async def _check_child_completion(
        self,
        instance_id: str,
        message_id: str | None,
    ) -> None:
        """Stage 6: child completion check.

        Best-effort: a failure here MUST NOT fail the message. The
        child completion notification is what unblocks a parent
        stuck in ``WAITING_CHILDREN``; a transient failure there
        is recoverable on the next message, so we log and move on.
        """
        try:
            checker = getattr(
                self._manager, "_process_child_completion_and_notify_parent", None
            )
            if checker is not None:
                await checker(instance_id, message_id)
        except Exception as e:
            logger.error(
                f"MessageProcessingPipeline: child completion check "
                f"failed for {instance_id[:8]}...: {e}",
                exc_info=True,
            )
            # Don't fail the task — the message was processed successfully.

    async def _is_instance_paused(self, instance_id: str) -> bool:
        """Defensive PAUSED re-check: query fresh instance status from DB.

        Context: the ``question()`` tool's pause cascade can commit
        ``PAUSED`` to the DB during an ``asyncio.shield`` + ``task.cancel()``
        timing window. The pipeline's child-completion step runs on the
        same event loop and can therefore race the cascade. In-memory
        instance state is stale in that race because the cascade commits
        outside the task that owns the cached state — only a fresh DB
        read can reliably detect ``PAUSED``.

        Best-effort by design: any DB read failure returns ``False``
        (do not skip) so normal processing continues. The guard is a
        belt-and-suspenders check for a known bug; a flaky status probe
        must never wedge the message path.

        Returns:
            ``True`` if the instance is currently ``PAUSED`` in the DB;
            ``False`` otherwise — including when the repository is
            unavailable, the read fails, or the instance row is missing.
        """
        repo = getattr(self._manager, "_instance_repository", None)
        if repo is None:
            return False
        try:
            instance = await asyncio.to_thread(repo.get, instance_id)
        except Exception as e:
            logger.warning(
                f"MessageProcessingPipeline: PAUSED re-check failed for "
                f"{instance_id[:8]}... (non-fatal, proceeding): {e}"
            )
            return False
        if instance is None:
            return False
        return instance.status == InstanceStatus.PAUSED.value

    async def _maybe_transition_to_waiting_children(
        self,
        instance_id: str,
    ) -> None:
        """Stage 5.5: transition RUNNING → WAITING_CHILDREN after a turn.

        Bug fix: previously, when an instance finished a graph turn
        (typically after consuming a child completion report) and the
        bus still tracked pending watchers for it, the instance was
        left at ``RUNNING`` until either the next child reported OR
        the bus finally drained (and the lifecycle observer drove a
        terminal transition). The frontend then displayed "running"
        for an instance that was effectively idle waiting on more
        child reports.

        This stage runs once per pipeline success path, immediately
        after ``_dispatch_completed``. It queries the bus for
        pending watchers on ``instance_id`` and, if any exist,
        performs an atomic ``transition_status_if`` from
        ``RUNNING`` to ``WAITING_CHILDREN``. The atomic predicate
        prevents clobbering a concurrent terminal write
        (``ERROR``/``TERMINATED``/``FAILED`` from another path).

        Behavioural notes:
          * Best-effort: any failure here is logged at WARNING and
            swallowed — the message itself already succeeded, and
            the bus / lifecycle observer will eventually resync the
            status via the normal terminal path.
          * Idempotent: if the instance is already ``WAITING_CHILDREN``
            (no transition fires) the helper simply emits no SSE.
          * No-op when the bus is uninitialised (matches the rest
            of the codebase, which treats ``get_dependency_bus() is
            None`` as a configuration bug rather than degrading).
        """
        try:
            bus = get_dependency_bus()
            if bus is None:
                return
            pending = await bus.count_pending_for_target(instance_id)
            if pending <= 0:
                return
            repo = getattr(self._manager, "_instance_repository", None)
            if repo is None:
                return
            updated = await asyncio.to_thread(
                repo.transition_status_if,
                instance_id,
                InstanceStatus.WAITING_CHILDREN.value,
                (InstanceStatus.RUNNING.value,),
            )
            if updated is None:
                # Concurrent writer changed the status between our
                # check and the atomic UPDATE (e.g. a terminal write
                # raced us). Don't emit a stale SSE — the other path
                # owns the status now.
                logger.debug(
                    f"MessageProcessingPipeline: skip WAITING_CHILDREN "
                    f"transition for {instance_id[:8]}... (status no longer RUNNING)"
                )
                return
            live_hub = getattr(self._manager, "_live_hub", None)
            if live_hub is not None:
                try:
                    await live_hub.stream_status_change(
                        instance_id,
                        InstanceStatus.WAITING_CHILDREN.value,
                        agent_id=getattr(updated, "agent_id", None),
                    )
                except Exception as sse_err:
                    logger.warning(
                        f"MessageProcessingPipeline: failed to emit "
                        f"WAITING_CHILDREN SSE for {instance_id[:8]}...: "
                        f"{sse_err}"
                    )
            logger.info(
                f"Parent instance {instance_id[:8]}... transitioned to "
                f"WAITING_CHILDREN after graph turn (bus_pending={pending})"
            )
            # Fire the ``in_progress`` job-event notification so an
            # orchestrator that ``watch_job``-ed this parent's work sees
            # the ``⟳`` event while children resolve. Before this hook,
            # the parent's intermediate "spawned child, now waiting"
            # state produced no ``instance_lifecycle`` event the
            # observer consumed, so the ``in_progress`` branch was
            # never reached and watchers never got the progress event.
            observer = getattr(self._manager, "_job_feedback_observer", None)
            if observer is not None and hasattr(observer, "emit_in_progress_if_job"):
                try:
                    await observer.emit_in_progress_if_job(instance_id)
                except Exception as ip_err:
                    logger.warning(
                        f"MessageProcessingPipeline: in_progress notify "
                        f"failed for {instance_id[:8]}... (non-fatal): "
                        f"{ip_err}"
                    )
        except Exception as e:
            logger.warning(
                f"MessageProcessingPipeline: WAITING_CHILDREN transition "
                f"failed for {instance_id[:8]}... (non-fatal): {e}",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Boundary handlers (contention / cancel)
    # ------------------------------------------------------------------

    async def _handle_contention(
        self,
        exc: Exception,
        callbacks: PipelineCallbacks,
    ) -> ProcessingResult:
        """Defensive contention handler. Never invoked under the
        asyncio.Lock gate (the second caller blocks on the same event
        loop) but kept as a safety net for any future gate that does
        signal contention. Delegates to ``on_contention`` if supplied,
        otherwise re-raises the exception.
        """
        if callbacks.on_contention is None:
            raise exc
        try:
            cb_result = await callbacks.on_contention(exc)
        except Exception as cb_err:
            logger.warning(
                f"MessageProcessingPipeline: on_contention callback "
                f"raised (re-raising original): {cb_err}"
            )
            raise exc
        if cb_result is None:
            # Callback signaled "use the default" — re-raise the
            # original exception to preserve pre-Phase-5 behaviour.
            raise exc
        return cb_result

    async def _handle_cancel(
        self,
        exc: BaseException,
        callbacks: PipelineCallbacks,
    ) -> ProcessingResult:
        """Delegate cancellation handling to ``on_cancel`` or re-raise.

        Covers both ``OperationCancelledError`` (token cancelled) and
        ``asyncio.CancelledError`` (task cancelled). The callback
        decides whether the cancellation is pause (leave
        PROCESSING for resume) or terminate (mark CANCELLED).
        """
        if callbacks.on_cancel is None:
            raise exc
        try:
            cb_result = await callbacks.on_cancel(exc)
        except Exception as cb_err:
            logger.warning(
                f"MessageProcessingPipeline: on_cancel callback "
                f"raised (re-raising original): {cb_err}"
            )
            raise exc
        if cb_result is None:
            raise exc
        return cb_result

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    async def _run_error_handler(
        self,
        context: ProcessingContext,
        error: Exception,
        error_handler_id: dict[str, str] | None,
    ) -> None:
        """Run the unified error helper with the right id kwargs.

        The WorkerPool path passes ``task_id``; the JobQueue path
        passes ``job_id``. The pipeline accepts either via
        ``error_handler_id`` and forwards to
        :func:`handle_message_processing_error`. A ``None`` value
        (ad-hoc tests) skips the id argument entirely.
        """
        kwargs: dict[str, Any] = {
            "instance_manager": self._manager,
            "instance_id": context.instance_id,
            "error": error,
            "message_id": context.message_id,
        }
        if error_handler_id:
            kwargs.update(error_handler_id)
        try:
            await handle_message_processing_error(**kwargs)
        except Exception as helper_err:
            # The error helper is documented as best-effort and
            # should not raise, but defend against it anyway: a
            # secondary failure during error reporting must not
            # mask the original error.
            logger.warning(
                f"MessageProcessingPipeline: handle_message_processing_error "
                f"raised (non-fatal): {helper_err}"
            )
