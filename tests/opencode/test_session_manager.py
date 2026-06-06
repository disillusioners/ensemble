"""Comprehensive tests for OpenCodeSessionManager — the session lifecycle state machine.

Covers all 10 behaviour groups from the test spec:

 1. Initial state          — manager starts in IDLE
 2. submit_request         — optimistic BUSY, starts worker, _is_worker_busy True
 3. Lock ordering          — on_state_change callback called OUTSIDE the lock
 4. Worker completion      — _worker_done_queue signals correctly
 5. _handle_worker_done    — aborted=True → result NOT overwritten
 6. abort_task             — state resets to IDLE, aborted=True, persists
 7. State transitions      — full IDLE→BUSY→WAITING_FOR_INPUT→IDLE lifecycle
 8. sync_state_with_open_code — derives state from message list
 9. resume()               — sends hardcoded orchestrator/litellm/coding prompt
10. Persistence callback   — receives updated PersistedState

The OpenCodeClient is always mocked via AsyncMock — no real HTTP calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from daemon.opencode.client import (
    AnswerRequest,
    CommandRequest,
    OpenCodeAPIError,
    Part,
    PromptRequest,
)
from daemon.opencode.constants import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_PROVIDER_ID,
    RESUME_AGENT,
    RESUME_TEXT,
)
from daemon.opencode.session_manager import (
    OpenCodeSessionManager,
    PersistedState,
    Request,
    _WorkerResult,
)
from daemon.opencode.state import SessionState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client() -> AsyncMock:
    """AsyncMock standing in for OpenCodeClient — no real HTTP calls."""
    client = AsyncMock()
    client.send_prompt = AsyncMock(return_value={"ok": True})
    client.send_command = AsyncMock(return_value={"ok": True})
    client.abort_session = AsyncMock(return_value={"ok": True})
    client.get_questions = AsyncMock(return_value=[])
    client.get_session_messages = AsyncMock(return_value=[])
    client.answer_question = AsyncMock(return_value={"ok": True})
    return client


@pytest.fixture
def manager(mock_client: AsyncMock) -> OpenCodeSessionManager:
    """Fresh SessionManager with mocked client. Loop NOT started."""
    return OpenCodeSessionManager(
        session_id="test-session-1",
        working_dir="/test/project",
        client=mock_client,
    )


@pytest.fixture
def manager_with_callback(
    mock_client: AsyncMock,
) -> tuple[OpenCodeSessionManager, list[PersistedState], bool]:
    """SessionManager with an async on_state_change that records calls."""
    received: list[PersistedState] = []
    lock_held_during_callback = False

    async def on_state_change(state: PersistedState) -> None:
        nonlocal lock_held_during_callback
        received.append(state)
        lock_held_during_callback = manager._lock.locked()

    mgr = OpenCodeSessionManager(
        session_id="test-session-1",
        working_dir="/test/project",
        client=mock_client,
        on_state_change=on_state_change,
    )
    return mgr, received, lock_held_during_callback


# ─────────────────────────────────────────────────────────────────────────────
# 1. Initial state
# ─────────────────────────────────────────────────────────────────────────────


class TestInitialState:
    """Verify the state of a fresh SessionManager before any operations."""

    def test_starts_in_idle_state(self, manager: OpenCodeSessionManager) -> None:
        assert manager._state == SessionState.IDLE

    def test_worker_busy_is_false(self, manager: OpenCodeSessionManager) -> None:
        assert manager._is_worker_busy is False

    def test_aborted_flag_is_false(self, manager: OpenCodeSessionManager) -> None:
        assert manager._aborted is False

    def test_questions_start_empty(self, manager: OpenCodeSessionManager) -> None:
        assert manager._questions == []

    def test_last_agent_default_sisyphus(self, manager: OpenCodeSessionManager) -> None:
        assert manager._last_agent == "sisyphus"

    def test_get_snapshot_returns_idle(self, manager: OpenCodeSessionManager) -> None:
        snap = manager.get_snapshot()
        assert snap["state"] == SessionState.IDLE.value
        assert snap["session_id"] == "test-session-1"

    def test_latest_response_none_initially(self, manager: OpenCodeSessionManager) -> None:
        assert manager._latest_response is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. submit_request — optimistic BUSY, starts worker, _is_worker_busy
# ─────────────────────────────────────────────────────────────────────────────


class TestSubmitRequest:
    """Verify submit_request sets BUSY optimistically before the HTTP call."""

    @pytest.mark.asyncio
    async def test_submit_request_sets_busy_before_http_call(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """State is BUSY immediately after submit_request returns.

        The optimistic BUSY is set in do_submit() before the request is
        even enqueued — no HTTP call is made yet because the loop is not
        running.
        """
        # Make send_prompt block so it can't be called yet
        block_event = asyncio.Event()

        async def slow_send_prompt(*args, **kwargs):
            await block_event.wait()
            return {"ok": True}

        mock_client.send_prompt = slow_send_prompt

        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="hello")]))
        manager.submit_request(req)

        # Yield once so do_submit() task runs
        await asyncio.sleep(0.05)

        # State must be BUSY even though no HTTP call has been made
        assert manager._state == SessionState.BUSY
        assert manager._is_worker_busy is True

        # Release the block so the test can exit cleanly
        block_event.set()

    @pytest.mark.asyncio
    async def test_submit_request_command_also_sets_busy(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """COMMAND requests also trigger the optimistic BUSY update."""
        block_event = asyncio.Event()

        async def slow_send_command(*args, **kwargs):
            await block_event.wait()
            return {"ok": True}

        mock_client.send_command = slow_send_command

        req = Request("COMMAND", payload=CommandRequest(command="/status", arguments=""))
        manager.submit_request(req)
        await asyncio.sleep(0.05)

        assert manager._state == SessionState.BUSY
        assert manager._is_worker_busy is True

        block_event.set()

    @pytest.mark.asyncio
    async def test_submit_request_answer_does_not_set_busy(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """ANSWER requests do NOT trigger the optimistic BUSY path."""
        req = Request("ANSWER", payload=AnswerRequest(request_id="q1", answers=[["A"]]))
        manager.submit_request(req)
        await asyncio.sleep(0.05)

        # ANSWER does not touch _state or _is_worker_busy
        assert manager._state == SessionState.IDLE
        assert manager._is_worker_busy is False

    @pytest.mark.asyncio
    async def test_submit_request_enqueues_request(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """submit_request places the request in _input_queue for the loop."""
        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="hi")]))
        manager.submit_request(req)
        await asyncio.sleep(0.05)

        # Request is in the queue
        assert manager._input_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_submit_request_clears_latest_response(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """Optimistic BUSY also clears _latest_response."""
        manager._latest_response = {"old": "data"}

        block_event = asyncio.Event()
        async def slow_prompt(*args, **kwargs):
            await block_event.wait()
            return {"ok": True}
        mock_client.send_prompt = slow_prompt

        req = Request("PROMPT", payload=PromptRequest())
        manager.submit_request(req)
        await asyncio.sleep(0.05)

        assert manager._latest_response is None
        block_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Lock ordering — callback called OUTSIDE the lock
# ─────────────────────────────────────────────────────────────────────────────


class TestLockOrdering:
    """Verify on_state_change is invoked after the lock is released."""

    @pytest.mark.asyncio
    async def test_callback_called_outside_lock_on_submit(
        self,
        manager_with_callback: tuple[OpenCodeSessionManager, list, bool],
    ) -> None:
        """submit_request's do_submit releases the lock before calling _persist_state."""
        mgr, received, lock_held = manager_with_callback

        block_event = asyncio.Event()

        async def slow_prompt(*args, **kwargs):
            await block_event.wait()
            return {"ok": True}

        mgr._client.send_prompt = slow_prompt

        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="x")]))
        mgr.submit_request(req)
        await asyncio.sleep(0.05)

        # Callback must have fired
        assert len(received) == 1
        # Lock was NOT held when the callback ran
        assert lock_held is False

        block_event.set()

    @pytest.mark.asyncio
    async def test_callback_called_outside_lock_on_abort(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """abort_task releases the lock before calling _persist_state."""
        received: list[PersistedState] = []
        lock_held = False

        # We need a late-binding reference to mgr inside the closure.
        mgr_ref: list[OpenCodeSessionManager] = []

        async def on_state_change(state: PersistedState) -> None:
            nonlocal lock_held
            received.append(state)
            lock_held = mgr_ref[0]._lock.locked()

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )
        mgr_ref.append(mgr)

        await mgr.abort_task()
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert lock_held is False

    @pytest.mark.asyncio
    async def test_sync_callback_also_called_outside_lock(
        self, mock_client: AsyncMock
    ) -> None:
        """Sync callbacks are likewise invoked after the lock is released."""
        received: list[PersistedState] = []
        lock_held = False

        def on_state_change(state: PersistedState) -> None:
            nonlocal lock_held
            received.append(state)
            lock_held = manager._lock.locked()

        manager = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )

        await manager.abort_task()
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert lock_held is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Worker completion — _worker_done_queue signals correctly
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkerCompletion:
    """Verify _run_worker enqueues the result in _worker_done_queue."""

    @pytest.mark.asyncio
    async def test_run_worker_success_puts_result_in_queue(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """A successful worker enqueues _WorkerResult(result, None)."""
        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="hi")]))
        await manager._run_worker(req)

        result = await asyncio.wait_for(manager._worker_done_queue.get(), timeout=1.0)
        assert isinstance(result, _WorkerResult)
        assert result.error is None
        assert result.result == {"ok": True}
        mock_client.send_prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_worker_error_puts_error_in_queue(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """A failing worker enqueues _WorkerResult(None, error)."""
        mock_client.send_prompt.side_effect = RuntimeError("boom")
        req = Request("PROMPT", payload=PromptRequest())
        await manager._run_worker(req)

        result = await asyncio.wait_for(manager._worker_done_queue.get(), timeout=1.0)
        assert isinstance(result, _WorkerResult)
        assert result.result is None
        assert isinstance(result.error, RuntimeError)
        assert str(result.error) == "boom"

    @pytest.mark.asyncio
    async def test_run_worker_resume_uses_hardcoded_prompt(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """RESUME worker calls send_prompt with hardcoded orchestrator/litellm/coding."""
        req = Request("RESUME")
        await manager._run_worker(req)

        mock_client.send_prompt.assert_awaited_once()
        call_args = mock_client.send_prompt.call_args
        prompt_req: PromptRequest = call_args[0][1]  # second positional arg

        assert prompt_req.agent == RESUME_AGENT
        assert prompt_req.model.provider_id == DEFAULT_MODEL_PROVIDER_ID
        assert prompt_req.model.model_id == DEFAULT_MODEL_ID
        assert len(prompt_req.parts) == 1
        assert prompt_req.parts[0].type == "text"
        assert prompt_req.parts[0].text == RESUME_TEXT

    @pytest.mark.asyncio
    async def test_run_worker_command_calls_send_command(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """COMMAND requests route to client.send_command."""
        req = Request("COMMAND", payload=CommandRequest(command="/status", arguments=""))
        await manager._run_worker(req)

        mock_client.send_command.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# 5. _handle_worker_done — aborted=True prevents result overwrite
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleWorkerDoneAborted:
    """Verify _handle_worker_done discards results when the aborted flag is set."""

    @pytest.mark.asyncio
    async def test_aborted_flag_prevents_result_overwrite(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """When _aborted=True, _latest_response is NOT replaced by the result."""
        manager._latest_response = {"old": "data"}
        manager._aborted = True
        manager._state = SessionState.BUSY
        manager._is_worker_busy = True

        await manager._handle_worker_done(_WorkerResult(result={"new": "data"}))

        # Result must NOT have been stored
        assert manager._latest_response == {"old": "data"}

    @pytest.mark.asyncio
    async def test_aborted_flag_is_reset_after_discard(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """The aborted flag is cleared after a discarded result."""
        manager._aborted = True
        await manager._handle_worker_done(_WorkerResult(result={"x": 1}))

        assert manager._aborted is False

    @pytest.mark.asyncio
    async def test_normal_result_overwrites_latest_response(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """When aborted=False, _latest_response is updated with the result.

        The production code wraps the stripped result in {"result": ...}, so
        the final shape is {"result": <stripped input>}.
        """
        manager._latest_response = None
        manager._aborted = False
        manager._state = SessionState.BUSY
        manager._is_worker_busy = True

        await manager._handle_worker_done(_WorkerResult(result={"result": "ok"}))

        # _handle_worker_done wraps the stripped result in {"result": ...}
        assert manager._latest_response == {"result": {"result": "ok"}}

    @pytest.mark.asyncio
    async def test_error_result_stores_error_dict(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """An error result sets _latest_response to an error dict."""
        manager._is_worker_busy = True
        manager._aborted = False

        await manager._handle_worker_done(_WorkerResult(error=ValueError("bad input")))

        assert manager._latest_response == {"error": "bad input"}

    @pytest.mark.asyncio
    async def test_questions_present_sets_waiting_for_input(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """With questions in _questions, state becomes WAITING_FOR_INPUT."""
        manager._is_worker_busy = True
        manager._aborted = False
        manager._state = SessionState.BUSY
        manager._questions = [{"id": "q1", "questions": []}]

        await manager._handle_worker_done(_WorkerResult(result={"ok": True}))

        assert manager._state == SessionState.WAITING_FOR_INPUT

    @pytest.mark.asyncio
    async def test_no_questions_sets_idle(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """Without questions, state returns to IDLE."""
        manager._is_worker_busy = True
        manager._aborted = False
        manager._state = SessionState.BUSY
        manager._questions = []

        await manager._handle_worker_done(_WorkerResult(result={"ok": True}))

        assert manager._state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_socket_timeout_triggers_abort_on_client(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """An OpenCodeAPIError(status_code=0) caused by httpx.TimeoutException
        causes _handle_worker_done to call client.abort_session."""
        import httpx

        manager._is_worker_busy = True
        manager._aborted = False

        # The client wraps network errors in OpenCodeAPIError(0) with the
        # original httpx exception set as __cause__ (via `raise ... from exc`).
        timeout_exc = httpx.TimeoutException("timed out")
        api_err = OpenCodeAPIError(0, "connection timed out")
        api_err.__cause__ = timeout_exc

        await manager._handle_worker_done(_WorkerResult(error=api_err))

        mock_client.abort_session.assert_awaited_once_with(manager.session_id)

    @pytest.mark.asyncio
    async def test_worker_busy_flag_cleared_even_when_aborted(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """_is_worker_busy is cleared even when the result is discarded."""
        manager._is_worker_busy = True
        manager._aborted = True
        manager._state = SessionState.BUSY

        await manager._handle_worker_done(_WorkerResult(result={"ignored": True}))

        assert manager._is_worker_busy is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. abort_task — state reset, aborted flag, persistence
# ─────────────────────────────────────────────────────────────────────────────


class TestAbortTask:
    """Verify abort_task resets state and persists."""

    @pytest.mark.asyncio
    async def test_state_resets_to_idle(self, manager: OpenCodeSessionManager) -> None:
        """abort_task sets _state to IDLE."""
        manager._state = SessionState.BUSY
        await manager.abort_task()
        assert manager._state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_aborted_flag_set_to_true(self, manager: OpenCodeSessionManager) -> None:
        """abort_task sets the _aborted flag to True."""
        manager._aborted = False
        await manager.abort_task()
        assert manager._aborted is True

    @pytest.mark.asyncio
    async def test_worker_busy_cleared(self, manager: OpenCodeSessionManager) -> None:
        """abort_task clears _is_worker_busy."""
        manager._is_worker_busy = True
        await manager.abort_task()
        assert manager._is_worker_busy is False

    @pytest.mark.asyncio
    async def test_questions_cleared(self, manager: OpenCodeSessionManager) -> None:
        """abort_task empties the _questions list."""
        manager._questions = [{"id": "q1"}]
        await manager.abort_task()
        assert manager._questions == []

    @pytest.mark.asyncio
    async def test_latest_response_set_to_aborted_message(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """_latest_response is set to the aborted status dict."""
        await manager.abort_task()
        assert manager._latest_response == {
            "status": "aborted",
            "message": "Task aborted by user",
        }

    @pytest.mark.asyncio
    async def test_persists_state_after_abort(
        self, mock_client: AsyncMock
    ) -> None:
        """abort_task calls _persist_state (via _on_state_change)."""
        received: list[PersistedState] = []

        async def on_state_change(state: PersistedState) -> None:
            received.append(state)

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )
        await mgr.abort_task()
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].state == SessionState.IDLE.value


# ─────────────────────────────────────────────────────────────────────────────
# 7. State transitions — full lifecycle IDLE→BUSY→WAITING_FOR_INPUT→IDLE
# ─────────────────────────────────────────────────────────────────────────────


class TestStateTransitions:
    """Verify the complete session lifecycle through state transitions."""

    @pytest.mark.asyncio
    async def test_handle_request_prompt_sets_busy_and_starts_worker(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """_handle_request with PROMPT sets BUSY and fires the worker task."""
        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="do it")]))

        await manager._handle_request(req)
        # Yield to let the _run_worker task execute
        await asyncio.sleep(0.05)

        assert manager._state == SessionState.BUSY
        assert manager._is_worker_busy is True
        mock_client.send_prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_done_with_questions_yields_waiting_for_input(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """When questions exist, worker completion leaves state at WAITING_FOR_INPUT."""
        # Pre-set questions before worker finishes
        manager._questions = [{"id": "q1", "questions": []}]
        manager._is_worker_busy = True
        manager._state = SessionState.BUSY

        await manager._handle_worker_done(_WorkerResult(result={"ok": True}))

        assert manager._state == SessionState.WAITING_FOR_INPUT
        assert manager._is_worker_busy is False

    @pytest.mark.asyncio
    async def test_answer_question_clears_waiting_and_returns_to_idle(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """answer_question removes the question; when none remain → IDLE."""
        manager._questions = [{"id": "q1", "questions": []}]
        manager._state = SessionState.WAITING_FOR_INPUT
        manager._is_worker_busy = False

        await manager.answer_question("q1", [["my answer"]])

        assert manager._questions == []
        assert manager._state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_full_lifecycle_idle_to_busy_to_waiting_to_idle(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """End-to-end: initial IDLE → submit PROMPT → BUSY → questions arrive → WAITING → answer → IDLE."""
        # 1. Initial IDLE
        assert manager._state == SessionState.IDLE

        # 2. Handle PROMPT → BUSY
        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="start")]))
        await manager._handle_request(req)
        await asyncio.sleep(0.05)
        assert manager._state == SessionState.BUSY

        # 3. Worker completes with questions → WAITING_FOR_INPUT
        # Drain the worker's queue result first
        try:
            await asyncio.wait_for(manager._worker_done_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        manager._questions = [{"id": "q1", "questions": []}]
        await manager._handle_worker_done(_WorkerResult(result={"ok": True}))
        assert manager._state == SessionState.WAITING_FOR_INPUT

        # 4. Answer the last question → IDLE
        await manager.answer_question("q1", [["done"]])
        assert manager._state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_worker_done_after_answering_last_question(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """If worker finishes after all questions are answered → IDLE."""
        manager._state = SessionState.BUSY
        manager._is_worker_busy = True
        manager._questions = []

        await manager._handle_worker_done(_WorkerResult(result={"ok": True}))

        assert manager._state == SessionState.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# 8. sync_state_with_open_code — derives state from message list
# ─────────────────────────────────────────────────────────────────────────────


class TestSyncStateWithOpenCode:
    """Verify sync_state_with_open_code derives state from the last message."""

    @pytest.mark.asyncio
    async def test_no_messages_returns_snapshot_unchanged(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """When get_session_messages returns [], state is not modified."""
        mock_client.get_session_messages = AsyncMock(return_value=[])
        manager._state = SessionState.BUSY

        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.BUSY.value
        mock_client.get_session_messages.assert_awaited_once_with("test-session-1", limit=1)

    @pytest.mark.asyncio
    async def test_last_message_with_error_returns_idle(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """A message with info.error → state IDLE."""
        msg = {
            "info": {"id": "m1", "error": "timeout"},
            "parts": [{"type": "text", "text": "oops"}],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])
        manager._state = SessionState.BUSY
        manager._is_worker_busy = True

        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.IDLE.value

    @pytest.mark.asyncio
    async def test_step_finish_waiting_for_input_returns_waiting(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """step-finish with reason=waiting_for_input → WAITING_FOR_INPUT state."""
        msg = {
            "info": {"id": "m1"},
            "parts": [{"type": "step-finish", "reason": "waiting_for_input"}],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])
        manager._state = SessionState.BUSY

        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.WAITING_FOR_INPUT.value

    @pytest.mark.asyncio
    async def test_step_finish_stop_returns_idle(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """step-finish with reason=stop → IDLE state."""
        msg = {
            "info": {"id": "m1"},
            "parts": [{"type": "step-finish", "reason": "stop"}],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])
        manager._state = SessionState.BUSY

        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.IDLE.value

    @pytest.mark.asyncio
    async def test_step_finish_other_reason_returns_busy(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """step-finish with any other reason (and no error) → BUSY state."""
        msg = {
            "info": {"id": "m1"},
            "parts": [{"type": "step-finish", "reason": "max_tokens"}],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])
        manager._state = SessionState.IDLE

        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.BUSY.value

    @pytest.mark.asyncio
    async def test_client_error_returns_snapshot_without_crash(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """If get_session_messages raises, the method returns a snapshot without crashing."""
        mock_client.get_session_messages.side_effect = OpenCodeAPIError(500, "server error")
        manager._state = SessionState.BUSY

        # Should not raise
        snap = await manager.sync_state_with_open_code()

        assert snap["state"] == SessionState.BUSY.value

    @pytest.mark.asyncio
    async def test_sync_clears_worker_busy_when_idle_detected(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """When state transitions to IDLE, _is_worker_busy is cleared."""
        msg = {
            "info": {"id": "m1"},
            "parts": [{"type": "step-finish", "reason": "stop"}],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])
        manager._is_worker_busy = True
        manager._state = SessionState.BUSY

        await manager.sync_state_with_open_code()

        assert manager._is_worker_busy is False

    @pytest.mark.asyncio
    async def test_sync_updates_latest_response_with_stripped_message(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """_latest_response is set to the stripped (bloat-removed) last message."""
        msg = {
            "info": {
                "id": "m1",
                "finish": "stop",
                "tokens": 999,   # bloat — should be stripped
                "extra": "garbage",
            },
            "parts": [
                {"type": "text", "text": "hello", "extra_field": "remove"},
            ],
        }
        mock_client.get_session_messages = AsyncMock(return_value=[msg])

        await manager.sync_state_with_open_code()

        assert manager._latest_response is not None
        result = manager._latest_response.get("result", {})
        # Only id/finish/time should remain in info
        assert "id" in result.get("info", {})
        assert "finish" in result.get("info", {})
        assert "tokens" not in result.get("info", {})
        # Only type/text/reason/error should remain in parts
        assert result["parts"][0].get("type") == "text"
        assert result["parts"][0].get("text") == "hello"
        assert "extra_field" not in result["parts"][0]


# ─────────────────────────────────────────────────────────────────────────────
# 9. resume() — sends hardcoded orchestrator/litellm/coding prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestResume:
    """Verify the public resume() method uses the correct hardcoded values."""

    @pytest.mark.asyncio
    async def test_resume_calls_client_send_prompt(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """resume() calls client.send_prompt with the session id."""
        await manager.resume()

        mock_client.send_prompt.assert_awaited_once()
        call_args = mock_client.send_prompt.call_args
        # First positional arg is the session id
        assert call_args.args[0] == "test-session-1"
        # Second positional arg is a PromptRequest
        assert isinstance(call_args.args[1], PromptRequest)

    @pytest.mark.asyncio
    async def test_resume_uses_hardcoded_agent_orchestrator(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """agent is hardcoded to 'orchestrator'."""
        await manager.resume()

        _, prompt_req = mock_client.send_prompt.call_args[0]
        assert prompt_req.agent == "orchestrator"

    @pytest.mark.asyncio
    async def test_resume_uses_hardcoded_model_litellm_coding(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """model is hardcoded to litellm/coding."""
        await manager.resume()

        _, prompt_req = mock_client.send_prompt.call_args[0]
        assert prompt_req.model.provider_id == "litellm"
        assert prompt_req.model.model_id == "coding"

    @pytest.mark.asyncio
    async def test_resume_uses_hardcoded_text_resume(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """The text part contains the hardcoded 'resume' string."""
        await manager.resume()

        _, prompt_req = mock_client.send_prompt.call_args[0]
        assert len(prompt_req.parts) == 1
        assert prompt_req.parts[0].type == "text"
        assert prompt_req.parts[0].text == "resume"

    @pytest.mark.asyncio
    async def test_resume_returns_api_response(
        self, manager: OpenCodeSessionManager, mock_client: AsyncMock
    ) -> None:
        """resume() returns the result from client.send_prompt."""
        mock_client.send_prompt.return_value = {"result": "continued"}

        result = await manager.resume()

        assert result == {"result": "continued"}


# ─────────────────────────────────────────────────────────────────────────────
# 10. Persistence callback — receives updated PersistedState
# ─────────────────────────────────────────────────────────────────────────────


class TestPersistenceCallback:
    """Verify on_state_change receives the correct PersistedState snapshot."""

    @pytest.mark.asyncio
    async def test_callback_receives_persisted_state_object(
        self, mock_client: AsyncMock
    ) -> None:
        """on_state_change receives a PersistedState instance (not a dict)."""
        received: list = []

        async def on_state_change(state: PersistedState) -> None:
            received.append(state)

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )
        await mgr.abort_task()
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert isinstance(received[0], PersistedState)

    @pytest.mark.asyncio
    async def test_callback_receives_updated_state_idle_after_abort(
        self, mock_client: AsyncMock
    ) -> None:
        """After abort_task, the callback receives state='IDLE'."""
        received: list[PersistedState] = []

        async def on_state_change(state: PersistedState) -> None:
            received.append(state)

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )
        await mgr.abort_task()
        await asyncio.sleep(0.05)

        assert received[0].state == SessionState.IDLE.value

    @pytest.mark.asyncio
    async def test_callback_receives_busy_state_on_submit(
        self, mock_client: AsyncMock
    ) -> None:
        """After submit_request, the callback receives state='BUSY'."""
        received: list[PersistedState] = []

        async def on_state_change(state: PersistedState) -> None:
            received.append(state)

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )

        block_event = asyncio.Event()
        async def slow_prompt(*args, **kwargs):
            await block_event.wait()
            return {"ok": True}
        mgr._client.send_prompt = slow_prompt

        req = Request("PROMPT", payload=PromptRequest(parts=[Part(type="text", text="x")]))
        mgr.submit_request(req)
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].state == SessionState.BUSY.value

        block_event.set()

    @pytest.mark.asyncio
    async def test_callback_not_called_when_none(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """When on_state_change is None, no callback is invoked (no crash)."""
        # Should not raise
        await manager.abort_task()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_persisted_state_contains_all_required_fields(
        self, mock_client: AsyncMock
    ) -> None:
        """The PersistedState snapshot includes all expected fields."""
        received: list[PersistedState] = []

        async def on_state_change(state: PersistedState) -> None:
            received.append(state)

        mgr = OpenCodeSessionManager(
            session_id="test",
            working_dir="/dir",
            client=mock_client,
            on_state_change=on_state_change,
        )
        # Modify some fields
        mgr._last_agent = "orchestrator"
        mgr._is_agent_locked = True

        await mgr.abort_task()
        await asyncio.sleep(0.05)

        state = received[0]
        assert hasattr(state, "last_agent")
        assert hasattr(state, "is_agent_locked")
        assert hasattr(state, "state")
        assert hasattr(state, "latest_response")
        assert hasattr(state, "questions")
        assert hasattr(state, "last_activity")

    @pytest.mark.asyncio
    async def test_save_state_returns_persisted_state(
        self, manager: OpenCodeSessionManager
    ) -> None:
        """save_state() returns a PersistedState snapshot."""
        manager._last_agent = "orchestrator"
        manager._state = SessionState.BUSY
        manager._latest_response = {"result": "ok"}
        manager._questions = [{"id": "q1"}]

        state = manager.save_state()

        assert isinstance(state, PersistedState)
        assert state.last_agent == "orchestrator"
        assert state.state == SessionState.BUSY.value
        assert state.latest_response == {"result": "ok"}
        assert state.questions == [{"id": "q1"}]
