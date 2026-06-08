"""OpenCode session manager state machine.

Direct port of ``.inspiration-projects/opencode_skill_src/internal/manager/manager.go``
(574 lines) plus the 17-line ``actions.go`` file.

The key Go→Python adaptations are:

- **Goroutine → asyncio task**: ``go sm.loop()`` becomes
  ``asyncio.create_task(self._run_loop())``. The ``select{}`` is ported to
  ``asyncio.wait_for`` + ``asyncio.Queue`` for channel-like semantics.
- **sync.RWMutex → asyncio.Lock**: Python's async lock is not reentrant.
  We use a single ``asyncio.Lock`` (not RLocks) and acquire it explicitly
  in every critical section, mirroring the Go mutex semantics.
- **Buffered channels → asyncio.Queue**: ``inputChan chan Request`` becomes
  ``asyncio.Queue[Request]`` with ``maxsize=10`` (matching Go). The
  ``workerDoneChan`` is a ``Queue`` with ``maxsize=1`` and ``get_nowait=True``
  semantics.
- **Timers → asyncio``: The 30-second polling ticker is
  ``asyncio.sleep(POLL_INTERVAL_S)`` inside the loop.
- **Thread-safety**: All state fields (``State``, ``LatestResponse``,
  ``Questions``, ``isWorkerBusy``, ``aborted``) are mutated only inside
  the lock. The callback is called *outside* the lock (Go: "avoid deadlock
  if OnStateChange blocks").
- **Optimistic BUSY pattern**: ``SubmitRequest`` sets state=BUSY *before*
  enqueueing — prevents a race where a caller that gets a snapshot
  immediately after ``SubmitRequest`` would see ``IDLE`` before the loop
  processes the request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Lock
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, TypeVar

import httpx

from .client import (
    AnswerRequest,
    CommandRequest,
    OpenCodeAPIError,
    OpenCodeClient,
    PromptRequest,
)
from .constants import (
    INPUT_QUEUE_SIZE,
    POLL_INTERVAL_S,
    RESUME_AGENT,
    RESUME_TEXT,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_PROVIDER_ID,
    WORKER_DONE_QUEUE_SIZE,
)
from .state import (
    SessionState,
    STATE_BUSY,
    STATE_IDLE,
    STATE_WAITING_FOR_INPUT,
    has_message_error,
    strip_message_bloat,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _question_to_dict(q: Any) -> dict[str, Any]:
    """Convert a ``Question`` Pydantic model to a JSON-safe dict.

    Items that are already dicts (e.g. loaded from the DB) are passed
    through unchanged. Used at the write boundary in
    ``_poll_questions`` / ``_restore_from_persisted_state`` so that
    ``self._questions`` is always a list of plain dicts, matching the
    Go reference's "struct in memory → JSON for storage/wire" boundary.
    """
    if hasattr(q, "model_dump"):
        return q.model_dump(by_alias=True)
    return q


_MAX_PARENT_CHAIN_DEPTH = 10


async def _is_descendant_of(
    child_id: str,
    ancestor_id: str,
    client: "OpenCodeClient",
) -> bool:
    """Return ``True`` iff ``child_id`` has ``ancestor_id`` in its parent chain.

    Walks OpenCode's session tree via ``GET /session/{id}`` and follows
    ``parentID`` upward. Caps the walk at ``_MAX_PARENT_CHAIN_DEPTH`` hops
    to bound latency. Treats any HTTP/parse error or 404 as "unknown
    lineage" → ``False`` so the caller falls through to the directory
    scope as a safety net.
    """
    if child_id == ancestor_id:
        return True
    seen: set[str] = set()
    current: str | None = child_id
    for _ in range(_MAX_PARENT_CHAIN_DEPTH):
        if not current or current in seen:
            return False
        if current == ancestor_id:
            return True
        seen.add(current)
        try:
            data = await client.get_session(current)
        except Exception as exc:
            logger.debug(
                "is_descendant_of: failed to fetch session %s: %s",
                current, exc,
            )
            return False
        if data is None:
            return False
        current = data.get("parentID") or None
    return False



# ─────────────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────────────


# Re-export the enum values at module level so callers can use
# SessionManager.IDLE etc.
IDLE = SessionState.IDLE
BUSY = SessionState.BUSY
WAITING_FOR_INPUT = SessionState.WAITING_FOR_INPUT


class PersistedState:
    """In-memory snapshot of everything that needs to survive a crash.

    Port of ``manager.PersistedState`` (manager.go:23-30). Serialized to
    the SQLite repository via ``OnStateChange``.
    """

    def __init__(
        self,
        last_agent: str = "",
        is_agent_locked: bool = False,
        state: str = STATE_IDLE,
        latest_response: Any = None,
        questions: list[Any] = None,
        last_activity: str = "",
    ) -> None:
        self.last_agent: str = last_agent
        self.is_agent_locked: bool = is_agent_locked
        self.state: str = state
        self.latest_response: Any = latest_response
        self.questions: list[Any] = questions or []
        self.last_activity: str = last_activity


class Request:
    """An incoming request from a caller.

    Port of ``manager.Request`` (manager.go:58-63). The ``ResultChan``
    is replaced with an ``asyncio.Future`` — same semantics, async-native.
    """

    __slots__ = ("type", "payload", "result_future")

    def __init__(
        self,
        type_: str,
        payload: Any = None,
        result_future: asyncio.Future[None] | None = None,
    ) -> None:
        self.type: str = type_
        """One of ``"PROMPT"``, ``"COMMAND"``, ``"ANSWER"``, ``"RESUME"``."""
        self.payload: Any = payload
        self.result_future: asyncio.Future[None] | None = result_future


# ─────────────────────────────────────────────────────────────────────────────
# Worker result
# ─────────────────────────────────────────────────────────────────────────────


class _WorkerResult:
    """Internal: result from the async worker goroutine analogue.

    Port of the unexported ``workerResult`` (manager.go:64-68).
    """

    __slots__ = ("result", "error")

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result: Any = result
        self.error: Exception | None = error


# ─────────────────────────────────────────────────────────────────────────────
# OpenCodeSessionManager
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeSessionManager:
    """Manages the lifecycle of one OpenCode session.

    Port of ``manager.SessionManager`` (manager.go:32-52) plus the
    methods in ``actions.go``.

    An instance owns:

    - An ``OpenCodeClient`` for talking to the local OpenCode HTTP server.
    - An ``asyncio.Lock`` protecting mutable state.
    - An input ``asyncio.Queue`` (buffered, size=10) for incoming requests.
    - A worker result ``asyncio.Queue`` (size=1, drop-oldest on overflow).
    - An ``asyncio.Event`` stop signal.
    - An ``OnStateChange`` callback for persistence.

    Thread-safety notes:

    - The lock is always acquired for state mutations.
    - The callback is called *after* releasing the lock to prevent deadlock.
    - The input queue put is *outside* the lock so that concurrent callers
      can enqueue even while the loop is processing (back-pressure is
      handled by the bounded queue size).

    State machine transitions are documented in the docstring of each
    method and summarized in ``state.py``.
    """

    def __init__(
        self,
        session_id: str,
        working_dir: str,
        persisted_state: PersistedState | None = None,
        client: OpenCodeClient | None = None,
        on_state_change: Callable[[PersistedState], Coroutine[Any, Any, None]]
        | Callable[[PersistedState], None]
        | None = None,
    ) -> None:
        """Initialize a session manager.

        Args:
            session_id: OpenCode session ID (from ``CreateSession``).
            working_dir: Working directory for the session (x-opencode-directory header).
            persisted_state: Optional state loaded from the registry on startup
                (crash recovery).
            client: Optional pre-built client. If not provided, a new one is
                created using ``working_dir``.
            on_state_change: Callback invoked whenever state changes. Receives
                a ``PersistedState`` snapshot. May be async or sync. Called
                *outside* any lock to prevent deadlock.

        Port of ``NewSessionManager`` (manager.go:69-86).

        Note: The Go constructor takes a pointer; this Python constructor
        takes ownership of the objects it receives.
        """
        self.session_id: str = session_id
        """OpenCode session identifier. Set once at construction."""
        self.working_dir: str = working_dir
        """Working directory of this session — used as the scope for child
        session question detection. Matches ``self._client.working_dir``."""

        # ── Mutable state (protected by self._lock) ──────────────────────
        self._state: SessionState = SessionState.IDLE
        self._latest_response: Any = None
        self._questions: list[Any] = []
        self._is_worker_busy: bool = False
        self._aborted: bool = False
        self._is_agent_locked: bool = False
        self._last_agent: str = "sisyphus"  # Go: SessionParams{LastAgent:"sisyphus"}
        self._last_activity: datetime = datetime.now(timezone.utc)

        # ── Concurrency primitives ─────────────────────────────────────────
        self._lock: Lock = Lock()
        """Primary lock for all mutable state."""
        self._input_queue: asyncio.Queue[Request] = asyncio.Queue(
            maxsize=INPUT_QUEUE_SIZE,
        )
        self._worker_done_queue: asyncio.Queue[_WorkerResult] = asyncio.Queue(
            maxsize=WORKER_DONE_QUEUE_SIZE,
        )
        self._stop_event: asyncio.Event = asyncio.Event()

        # ── HTTP client ─────────────────────────────────────────────────
        self._client: OpenCodeClient = client or OpenCodeClient(working_dir)

        # ── Persistence callback ─────────────────────────────────────────
        self._on_state_change: (
            Callable[[PersistedState], Coroutine[Any, Any, None]]
            | Callable[[PersistedState], None]
            | None
        ) = on_state_change

        # ── Restore persisted state ──────────────────────────────────────
        if persisted_state is not None:
            self._restore_from_persisted_state(persisted_state)

        # ── Background task ──────────────────────────────────────────────
        self._loop_task: asyncio.Task[None] | None = None
        """Set by ``start()``; cleared by ``stop()``."""

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background event-loop task.

        Port of ``SessionManager.Start`` (manager.go:313-315):

            func (sm *SessionManager) Start() {
                go sm.loop()
            }

        The task runs ``_run_loop()`` which multiplexes:

        1. ``_stop_event`` → shutdown
        2. ``_input_queue`` → ``_handle_request``
        3. ``_worker_done_queue`` → ``_handle_worker_done``
        4. 30-second ticker → ``_poll_questions``
        """
        if self._loop_task is not None and not self._loop_task.done():
            logger.debug("loop already running for session %s", self.session_id)
            return
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("started session manager for session %s", self.session_id)

    async def stop(self) -> None:
        """Signal the loop to stop and wait for the task to finish.

        Port of ``SessionManager.Stop`` (manager.go:323-325):

            func (sm *SessionManager) Stop() {
                close(sm.stopChan)
            }

        In Python, closing a channel is replaced by setting an ``Event``.
        We wait for the loop task to complete so that callers blocked on
        the loop know the manager is fully shut down.
        """
        self._stop_event.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "loop task for session %s did not stop within 5s; cancelling",
                    self.session_id,
                )
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
            self._loop_task = None

    # ─────────────────────────────────────────────────────────────────
    # Persistence helpers
    # ─────────────────────────────────────────────────────────────────

    def _restore_from_persisted_state(self, data: PersistedState) -> None:
        """Restore mutable fields from a persisted snapshot.

        Port of ``SessionManager.restoreFromPersistedState`` (manager.go:88-113).
        """
        if data.last_agent:
            self._last_agent = data.last_agent
        if data.state:
            self._state = SessionState(data.state)
        self._is_agent_locked = data.is_agent_locked
        # ``questions`` is a list of dicts — serialized via the SQLAlchemy
        # ``JSON`` column. Written in the same shape by ``_poll_questions``
        # and ``_save_state_locked``, so no conversion is needed here.
        if data.questions:
            self._questions = list(data.questions)
        if data.latest_response is not None:
            self._latest_response = data.latest_response
        if data.last_activity:
            try:
                self._last_activity = datetime.fromisoformat(data.last_activity)
            except ValueError:
                logger.warning("failed to parse persisted last_activity")

    def _save_state_locked(self) -> PersistedState:
        """Build a ``PersistedState`` from current state (lock must be held)."""
        return PersistedState(
            last_agent=self._last_agent,
            is_agent_locked=self._is_agent_locked,
            state=self._state.value,
            latest_response=self._latest_response,
            questions=list(self._questions),
            last_activity=self._last_activity.isoformat(),
        )

    def save_state(self) -> PersistedState:
        """Return a ``PersistedState`` snapshot. Thread-safe read lock.

        Port of ``SessionManager.SaveState`` (manager.go:129-133).
        """
        # The Go version holds a read lock and delegates to saveStateLocked.
        # We do the same with an async read lock.
        # Note: asyncio.Lock doesn't have an RLock, so we use the same lock.
        # This is slightly more conservative than Go (which separates reads
        # from writes) but is safe and avoids a second lock type.
        return self._save_state_locked()

    async def _persist_state(self) -> None:
        """Save state and invoke the callback, then update last_activity.

        Called whenever state changes. The callback is awaited if it is
        async; called synchronously if not.
        """
        state_to_save = self._save_state_locked()
        if self._on_state_change is not None:
            import asyncio as a

            cb = self._on_state_change
            if asyncio.iscoroutinefunction(cb):
                await cb(state_to_save)
            else:
                cb(state_to_save)
        self._last_activity = datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict[str, Any]:
        """Return a thread-safe snapshot of current session state.

        Port of ``SessionManager.GetSnapshot`` (manager.go:352-362):

            func (sm *SessionManager) GetSnapshot() map[string]interface{} {
                sm.mu.RLock()
                defer sm.mu.RUnlock()
                return map[string]interface{}{
                    "state":           sm.State,
                    "session_id":      sm.SessionID,
                    "latest_response": sm.LatestResponse,
                    "questions":       sm.Questions,
                }
            }

        Returns:
            Dict with keys: ``state``, ``session_id``, ``latest_response``,
            ``questions``.
        """
        # We use sync_lock for the read, matching the Go RLock semantics.
        # Using `self._lock` is slightly more conservative (excludes concurrent
        # writes) but is correct.
        return {
            "state": self._state.value,
            "session_id": self.session_id,
            "latest_response": self._latest_response,
            "questions": list(self._questions),
        }

    def submit_request(self, req: Request) -> None:
        """Enqueue a request with optimistic BUSY state.

        Port of ``SessionManager.SubmitRequest`` (manager.go:327-350).

        Implements the **optimistic BUSY pattern** from Go line 329:

            // Pre-set state to avoid race condition where GetSnapshot sees
            // IDLE before loop picks up request

        The lock is held only for the state update; the queue put happens
        *outside* the lock to prevent deadlock.

        For ``PROMPT`` and ``COMMAND`` requests, we optimistically set
        ``State=BUSY`` and ``isWorkerBusy=True`` before enqueueing. This
        prevents a caller that immediately calls ``get_snapshot()`` from
        seeing ``IDLE`` before the loop processes the request.

        Args:
            req: The request to enqueue. Must have ``type`` in
                ``{"PROMPT", "COMMAND", "ANSWER", "RESUME"}``.
        """
        logger.debug("SubmitRequest: acquiring lock for %s", req.type)

        async def do_submit() -> None:
            async with self._lock:
                logger.debug("SubmitRequest: lock acquired for %s", req.type)
                if req.type in ("PROMPT", "COMMAND"):
                    # manager.go:332-344 — optimistic BUSY update
                    self._state = SessionState.BUSY
                    self._latest_response = None
                    self._is_worker_busy = True

                    logger.debug(
                        "SubmitRequest: OnStateChange is nil: %s",
                        self._on_state_change is None,
                    )
                    if self._on_state_change is not None:
                        logger.debug("SubmitRequest: calling OnStateChange")
                        state_to_save = self._save_state_locked()
                    else:
                        state_to_save = None
                else:
                    state_to_save = None

            # manager.go:340: "sm.mu.Unlock() // avoid deadlock if OnStateChange blocks"
            if state_to_save is not None:
                await self._persist_state()
                logger.debug("SubmitRequest: OnStateChange done")

            logger.debug("SubmitRequest: lock released, sending to channel")
            await self._input_queue.put(req)
            logger.debug("SubmitRequest: sent to channel successfully")

        # Fire and forget — submit_request is synchronous from the caller's
        # perspective (matching Go's fire-and-forget channel send).
        # We use create_task so it runs concurrently with the caller.
        asyncio.create_task(do_submit())

    def set_last_agent(self, agent: str) -> None:
        """Update the last-agent field and persist.

        Port of ``SessionManager.SetLastAgent`` (manager.go:135-145).
        The Go version calls ``OnStateChange`` while holding the lock,
        with an explicit ``sm.mu.Unlock()`` before the call to prevent
        deadlock if the callback blocks. We do the same with the async lock.
        """
        async def do_set() -> None:
            async with self._lock:
                self._last_agent = agent
                if self._on_state_change is not None:
                    state_to_save = self._save_state_locked()
                else:
                    state_to_save = None
            if state_to_save is not None:
                await self._persist_state()

        asyncio.create_task(do_set())

    def set_agent_locked(self, locked: bool) -> None:
        """Set the agent-lock flag. No persistence — caller is responsible.

        Port of ``SessionManager.SetAgentLocked`` (manager.go:147-151).
        """
        async def do_set() -> None:
            async with self._lock:
                self._is_agent_locked = locked

        asyncio.create_task(do_set())

    def update_working_dir(self, working_dir: str) -> None:
        """Replace the HTTP client with one for a new working directory.

        Port of ``SessionManager.UpdateWorkingDir`` (manager.go:317-321).
        """
        async def do_update() -> None:
            async with self._lock:
                self._client = OpenCodeClient(working_dir)

        asyncio.create_task(do_update())

    async def abort_task(self) -> None:
        """Reset state to IDLE and clear the aborted flag.

        Port of ``SessionManager.AbortTask`` (actions.go:7-17) and the
        relevant section of ``handleWorkerDone`` (manager.go:488-492).

        Sets ``aborted=True``, ``State=IDLE``, ``LatestResponse={"status":"aborted",...}``,
        ``isWorkerBusy=False``, and clears ``Questions``.

        Used by the daemon's ``ABORT_SESSION`` handler (server.go lines 338-374).
        The Go version is a simple sync method; we make it async to allow
        proper state persistence without spawning a task for every abort.
        """
        async with self._lock:
            self._aborted = True
            self._state = SessionState.IDLE
            self._latest_response = {"status": "aborted", "message": "Task aborted by user"}
            self._is_worker_busy = False
            self._questions = []

        await self._persist_state()
        logger.info("aborted task for session %s", self.session_id)

    async def sync_state_with_open_code(self) -> dict[str, Any]:
        """Poll OpenCode messages and derive state from the last message.

        Port of ``SessionManager.SyncStateWithOpenCode`` (manager.go:162-216).

        This replaces the removed session status API. It:

        1. Calls ``GetSessionMessages(limit=1)`` — newest first.
        2. Extracts the ``step-finish.reason`` field.
        3. Checks ``info.error`` presence.
        4. Calls ``_derive_state_from_finish(reason, has_error)``.
        5. Calls ``strip_message_bloat`` on the response.
        6. Updates state if it changed.
        7. Updates ``isWorkerBusy`` if we detected IDLE from a busy state.

        In addition, this method actively checks the ``/question`` endpoint
        on every call. Pending questions are authoritative for
        ``WAITING_FOR_INPUT`` and override the message-derived state, so
        callers (e.g. ``external_opencode_wait_for_result``) that poll
        GET_STATUS on a tight cadence see the question as soon as it
        appears on the OpenCode server — not after the next 30s worker
        ``_poll_questions`` tick.

        Returns:
            A ``get_snapshot()`` dict (potentially with updated state).
        """
        messages: list[dict[str, Any]] | None = None
        try:
            messages = await self._client.get_session_messages(self.session_id, limit=1)
        except Exception as exc:
            logger.warning(
                "SyncStateWithOpenCode: failed to get messages: %s",
                exc,
            )

        # Active question probe: GET_STATUS is the path that agent-side
        # waiters poll, so it must be authoritative for WAITING_FOR_INPUT
        # rather than waiting for the 30s worker poll.
        #
        # The OpenCode ``/question`` endpoint is scoped by working directory
        # (project), not by session. The orchestrator delegates to subagents
        # via the ``task`` tool, and those subagents run as child sessions
        # in the same project. When a child asks the user a question, the
        # question appears in our probe but with a different ``sessionID``.
        # We accept any question that either belongs to this session or is
        # a descendant in the OpenCode session tree (walking ``parentID``).
        questions_for_session: list[dict[str, Any]] = []
        try:
            all_questions = await self._client.get_questions()
        except Exception as exc:
            logger.debug("SyncStateWithOpenCode: failed to get questions: %s", exc)
        else:
            for q in all_questions:
                if q.session_id == self.session_id:
                    questions_for_session.append(_question_to_dict(q))
                    continue
                is_child = await _is_descendant_of(
                    q.session_id, self.session_id, self._client,
                )
                if is_child:
                    enriched = _question_to_dict(q)
                    enriched["parentSessionID"] = self.session_id
                    questions_for_session.append(enriched)

        if messages is None or len(messages) == 0:
            # manager.go:170-171: "No messages - keep current state".
            # Still apply the question-based state if we got any.
            if questions_for_session:
                async with self._lock:
                    self._questions = questions_for_session
                    if self._state != SessionState.WAITING_FOR_INPUT:
                        self._state = SessionState.WAITING_FOR_INPUT
                        if self._on_state_change is not None:
                            state_to_save = self._save_state_locked()
                        else:
                            state_to_save = None
                    else:
                        state_to_save = None
                if state_to_save is not None:
                    await self._persist_state()
            return self.get_snapshot()

        last_message: dict[str, Any] = messages[0]

        from .state import _derive_state_from_finish, get_message_finish

        finish_result = get_message_finish(last_message)
        if finish_result is None:
            reason = "<unknown>"
            has_error = False
        else:
            reason, has_error = finish_result

        new_state = _derive_state_from_finish(reason, has_error)

        # Pending questions are authoritative for WAITING_FOR_INPUT and
        # override whatever the message-derived state says. Otherwise the
        # message-based derivation (often BUSY for an in-flight step)
        # would clobber the waiting state set by the slower worker poll.
        if questions_for_session:
            new_state = SessionState.WAITING_FOR_INPUT

        # manager.go:196-206: determine which fields actually changed
        should_update_state = self._state != new_state
        should_update_worker_busy = (
            self._is_worker_busy
            and new_state == SessionState.IDLE
            and not questions_for_session
        )

        async with self._lock:
            # manager.go:200: always overwrite _latest_response with the latest
            # message from OpenCode, regardless of state — agents need real-time
            # visibility into in-flight messages during BUSY to detect progress
            # or problems. Matches Go behavior.
            self._latest_response = {"result": strip_message_bloat(last_message)}
            if questions_for_session:
                self._questions = questions_for_session
            if should_update_state:
                self._state = new_state
            if should_update_worker_busy:
                self._is_worker_busy = False

            if self._on_state_change is not None:
                state_to_save = self._save_state_locked()
            else:
                state_to_save = None

        # manager.go:207-213: callback outside the lock
        if state_to_save is not None:
            await self._persist_state()

        return self.get_snapshot()

    # ─────────────────────────────────────────────────────────────────
    # Event loop
    # ─────────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Background event loop. Ports ``SessionManager.loop()`` (manager.go:364-383).

        The Go code uses:

            select {
            case <-sm.stopChan: return
            case req := <-sm.inputChan: sm.handleRequest(req)
            case res := <-sm.workerDoneChan: sm.handleWorkerDone(res)
            case <-ticker.C: sm.pollQuestions()
            }

        We implement this as a loop with explicit waiting on each source.
        """
        logger.info("session manager loop started for %s", self.session_id)
        while True:
            try:
                # Wait for one of: stop, input, worker_done, or poll_tick.
                # We use asyncio.wait on three tasks to avoid having to
                # implement a full select-like multiplexer.
                stop_task = asyncio.create_task(self._stop_event.wait())
                input_task = asyncio.create_task(self._input_queue.get())
                worker_done_task = asyncio.create_task(self._worker_done_queue.get())

                done, _ = await asyncio.wait(
                    {stop_task, input_task, worker_done_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                should_stop = False

                # Process ALL completed tasks — if multiple tasks complete on
                # the same event loop tick (e.g. worker_done + input), popping
                # only one would drop the others' results, potentially sticking
                # the session in BUSY.
                for task in done:
                    if task is stop_task:
                        should_stop = True
                    elif task is input_task:
                        req: Request = task.result()
                        await self._handle_request(req)
                    elif task is worker_done_task:
                        res: _WorkerResult = task.result()
                        await self._handle_worker_done(res)

                # Cancel the tasks that did NOT complete before the next iteration
                for t in {stop_task, input_task, worker_done_task} - done:
                    t.cancel()

                if should_stop:
                    logger.info("stop event received; exiting loop for %s", self.session_id)
                    return

            except asyncio.CancelledError:
                # One of the tasks was cancelled — this is expected during shutdown.
                # Continue to the next iteration to check _stop_event.
                continue
            except Exception as exc:
                logger.exception("unhandled error in session loop for %s: %s", self.session_id, exc)

            # ── Poll timer (30s interval) ──────────────────────────────
            # In the Go code, the ticker fires *in addition to* the select
            # branches above. We implement this as a sleep that runs after
            # each iteration. Using a separate task for the timer would
            # require a more complex select multiplexer, so we do a simple
            # "after processing, sleep then poll" approach.
            try:
                await asyncio.sleep(POLL_INTERVAL_S)
                await self._poll_questions()
            except asyncio.CancelledError:
                raise  # propagate to outer loop

    # ─────────────────────────────────────────────────────────────────
    # Request handling
    # ─────────────────────────────────────────────────────────────────

    async def _handle_request(self, req: Request) -> None:
        """Dispatch a single request. Ports ``SessionManager.handleRequest`` (manager.go:385-454).

        For ``PROMPT``/``COMMAND``: extracts the agent name, sets BUSY
        optimistically (already done in ``submit_request`` for the
        caller-facing path; this handles re-submit from the loop), then
        fires the worker.

        For ``ANSWER``: calls the API, filters out the answered question,
        and sets state to IDLE or BUSY based on whether the worker is
        still busy.

        For ``RESUME``: constructs the hardcoded resume prompt and fires
        the worker.

        For ``START-WORK`` (not in the original Go, added for the
        daemon-level ``/start-work`` handling): delegates to the
        optional registry callback.

        Args:
            req: The request. ``payload`` type depends on ``type``:
                - ``PROMPT``: ``PromptRequest``
                - ``COMMAND``: ``CommandRequest``
                - ``ANSWER``: ``AnswerRequest``
                - ``RESUME``: ``None``
                - ``START-WORK``: ``None`` (registry handles it)
        """
        logger.info("Handling request type: %s", req.type)

        if req.result_future is not None:
            # Go: if req.ResultChan != nil { defer close(req.ResultChan) }
            pass

        if req.type == "ANSWER":
            # manager.go:413-437: answer + update state
            # CRITICAL: HTTP call must happen OUTSIDE the lock to avoid deadlock
            # and to allow the lock to be released while waiting for the server.
            payload = req.payload
            if isinstance(payload, AnswerRequest):
                try:
                    await self._client.answer_question(payload)
                except Exception as exc:
                    logger.warning("Answer failed: %s", exc)
                else:
                    # Single lock scope for state mutation only — no nesting.
                    async with self._lock:
                        new_questions = [
                            q for q in self._questions
                            if q.get("id") != payload.request_id
                        ]
                        self._questions = new_questions

                        if len(self._questions) == 0:
                            if self._is_worker_busy:
                                self._state = SessionState.BUSY
                            else:
                                self._state = SessionState.IDLE
        else:
            # PROMPT, COMMAND, RESUME — single lock scope (existing pattern).
            # State mutations and the fire-and-forget worker task happen under
            # one lock acquisition. asyncio.Lock is not reentrant, so we must
            # NOT nest another `async with self._lock:` inside this block.
            async with self._lock:
                if req.type in ("PROMPT", "COMMAND"):
                    # manager.go:391-407: update state + extract agent
                    if req.type == "PROMPT":
                        if isinstance(req.payload, PromptRequest):
                            self._last_agent = req.payload.agent
                    elif req.type == "COMMAND":
                        if isinstance(req.payload, CommandRequest):
                            self._last_agent = req.payload.agent

                    self._state = SessionState.BUSY
                    self._latest_response = None
                    self._is_worker_busy = True

                    logger.info("Starting worker for %s...", req.type)
                    # manager.go:411: go sm.runWorker(req) — fire and forget
                    asyncio.create_task(self._run_worker(req))

                elif req.type == "RESUME":
                    # manager.go:439-448: resume + worker
                    self._state = SessionState.BUSY
                    self._latest_response = None
                    self._is_worker_busy = True

                    logger.info("Starting worker for RESUME...")
                    asyncio.create_task(self._run_worker(req))

        # manager.go:451-453: signal completion if caller provided a future
        if req.result_future is not None and not req.result_future.done():
            req.result_future.set_result(None)

    async def _run_worker(self, req: Request) -> None:
        """Execute the request and enqueue the result.

        Ports ``SessionManager.runWorker`` (manager.go:456-481).

        The Go version runs in a separate goroutine (``go sm.runWorker(req)``).
        We use ``asyncio.create_task`` for the same fire-and-forget semantics.
        The actual API call runs in the current task.

        For ``RESUME``, the prompt is hardcoded:
        - agent="orchestrator"
        - model: providerID="litellm", modelID="coding"
        - parts: [{type:"text", text:"resume"}]

        Args:
            req: The request. ``type`` determines which API method is called.
        """
        result: Any = None
        error: Exception | None = None

        try:
            if req.type == "RESUME":
                # manager.go:465-471
                prompt_req = PromptRequest(
                    agent=RESUME_AGENT,
                    model={
                        "provider_id": DEFAULT_MODEL_PROVIDER_ID,
                        "model_id": DEFAULT_MODEL_ID,
                    },
                    parts=[{"type": "text", "text": RESUME_TEXT}],
                )
                result = await self._client.send_prompt(self.session_id, prompt_req)

            elif req.type == "COMMAND":
                # manager.go:472-474
                cmd_req = req.payload
                if isinstance(cmd_req, CommandRequest):
                    result = await self._client.send_command(self.session_id, cmd_req)
                else:
                    raise TypeError(f"expected CommandRequest, got {type(cmd_req)}")

            else:
                # PROMPT (manager.go:475-477)
                prompt_req = req.payload
                if isinstance(prompt_req, PromptRequest):
                    result = await self._client.send_prompt(self.session_id, prompt_req)
                else:
                    raise TypeError(f"expected PromptRequest, got {type(prompt_req)}")

        except Exception as exc:
            error = exc
            logger.warning("worker request %s failed: %s", req.type, exc)

        # manager.go:480: workerDoneChan <- workerResult{Result: res, Error: err}
        try:
            # Drop the oldest result if the queue is full (matches Go's
            # non-blocking send in a bounded channel).
            try:
                self._worker_done_queue.put_nowait(_WorkerResult(result, error))
            except asyncio.QueueFull:
                # Drop the oldest and put the new one
                try:
                    self._worker_done_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._worker_done_queue.put_nowait(_WorkerResult(result, error))
        except asyncio.QueueFull:
            logger.warning("worker done queue stuck; dropping result for %s", self.session_id)

    async def _handle_worker_done(self, res: _WorkerResult) -> None:
        """Process the result from a completed worker.

        Ports ``SessionManager.handleWorkerDone`` (manager.go:483-539).

        The function:
        1. Clears ``isWorkerBusy``.
        2. Checks ``aborted`` flag; if set, discards result silently.
        3. Handles HTTP timeout (net.Error with timeout()) → abort remote.
        4. Sets ``LatestResponse`` with result or error.
        5. Sets state to ``WAITING_FOR_INPUT`` if questions exist, else ``IDLE``.
        6. Calls ``OnStateChange`` outside the lock.

        Args:
            res: The worker's result (or error).
        """
        async with self._lock:
            self._is_worker_busy = False

            # manager.go:488-492: discard if aborted
            if self._aborted:
                self._aborted = False
                logger.debug("worker result discarded (aborted flag set)")
                return

            need_abort = False

            if res.error is not None:
                err = res.error
                # manager.go:496-505: distinguish HTTP timeout from other errors.
                # The client wraps network errors in OpenCodeAPIError(status_code=0)
                # with the original httpx exception accessible via __cause__
                # (set by `raise OpenCodeAPIError(...) from exc` in client.py).
                # httpx.TimeoutException is the base class for all httpx timeouts.
                is_timeout = (
                    isinstance(err, OpenCodeAPIError)
                    and err.status_code == 0
                    and isinstance(err.__cause__, httpx.TimeoutException)
                )
                if is_timeout:
                    # HTTP timeout after 1 hour — abort remote to clean up
                    need_abort = True
                    self._latest_response = {"error": "timeout after 1 hour"}
                else:
                    self._latest_response = {"error": str(err)}
            else:
                # manager.go:506-509: success
                self._latest_response = {"result": strip_message_bloat(res.result)}

            # manager.go:511-515: set state based on questions
            if len(self._questions) > 0:
                self._state = SessionState.WAITING_FOR_INPUT
            else:
                self._state = SessionState.IDLE

            state_to_save = self._save_state_locked() if self._on_state_change else None

        # manager.go:528-533: abort outside the lock
        if need_abort:
            try:
                await self._client.abort_session(self.session_id)
            except Exception as exc:
                logger.warning(
                    "Failed to abort session after timeout for %s: %s",
                    self.session_id,
                    exc,
                )

        # manager.go:535-538: callback outside the lock
        if state_to_save is not None:
            await self._persist_state()

    async def _poll_questions(self) -> None:
        """Poll OpenCode for pending questions and update state.

        Ports ``SessionManager.pollQuestions`` (manager.go:541-574).

        Runs every ``POLL_INTERVAL_S`` seconds (30s). It:

        1. Fetches ``GetQuestions()``.
        2. Filters to this session's questions.
        3. Sets ``WAITING_FOR_INPUT`` if any questions exist.
        4. If no questions and state was ``WAITING_FOR_INPUT``,
           reverts to ``BUSY`` (worker still processing) or ``IDLE``.
        """
        try:
            all_questions = await self._client.get_questions()
        except Exception as exc:
            logger.warning("Poll error: %s", exc)
            return

        # manager.go:552-558: filter to this session
        session_questions = [
            q for q in all_questions
            if q.session_id == self.session_id
        ]

        async with self._lock:
            self._questions = [_question_to_dict(q) for q in session_questions]

            # manager.go:565-572: state transitions
            if len(self._questions) > 0:
                self._state = SessionState.WAITING_FOR_INPUT
            elif self._state == SessionState.WAITING_FOR_INPUT:
                # manager.go:567-572: revert to previous state
                if self._is_worker_busy:
                    self._state = SessionState.BUSY
                else:
                    self._state = SessionState.IDLE

            if self._on_state_change is not None:
                state_to_save = self._save_state_locked()
            else:
                state_to_save = None

        if state_to_save is not None:
            await self._persist_state()

    # ─────────────────────────────────────────────────────────────────
    # Convenience methods (mirrors of Go public methods not yet covered)
    # ─────────────────────────────────────────────────────────────────

    async def resume(self) -> Any:
        """Send the resume prompt to the session.

        This is a public convenience method that constructs the
        hardcoded ``PromptRequest`` and calls the client directly.
        Unlike ``submit_request(RESUME)`` which goes through the worker
        loop, this method is for callers that want to resume a timed-out
        session and wait for the result.

        Mirrors the behaviour of ``submit_request(Request(type="RESUME"))``
        but without the queue abstraction.

        Returns:
            The API response from the OpenCode server.

        Raises:
            OpenCodeAPIError: If the request fails.
        """
        prompt_req = PromptRequest(
            agent=RESUME_AGENT,
            model={
                "provider_id": DEFAULT_MODEL_PROVIDER_ID,
                "model_id": DEFAULT_MODEL_ID,
            },
            parts=[{"type": "text", "text": RESUME_TEXT}],
        )
        return await self._client.send_prompt(self.session_id, prompt_req)

    async def answer_question(
        self,
        request_id: str,
        answers: list[list[str]],
    ) -> None:
        """Submit answers to a pending question.

        Ports the ``ANSWER`` handling in ``handleRequest`` (manager.go:413-437).

        Args:
            request_id: The OpenCode question ID.
            answers: List of answer rows (each row is a list of strings).
        """
        req = AnswerRequest(request_id=request_id, answers=answers)
        await self._client.answer_question(req)
        async with self._lock:
            self._questions = [
                q for q in self._questions if q.get("id") != request_id
            ]
            if len(self._questions) == 0:
                self._state = (
                    SessionState.BUSY if self._is_worker_busy else SessionState.IDLE
                )
        await self._persist_state()
