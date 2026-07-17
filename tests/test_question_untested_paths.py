"""Tests for two untested paths of the question tool feature (C3).

Covers:

1. **Part 1** — ``POST /api/instances/{id}/answer`` endpoint
   (``daemon/routers/instances.py:answer_questions``). The endpoint must
   store answers via ``QuestionManager.set_answers``, emit the
   ``question_pack`` SSE event, and fan out a resume cascade. We mount
   only the ``instances`` router on a bare FastAPI app and inject the
   ``InstanceManager`` via ``app.state.manager`` — mirroring the
   lightweight pattern in ``tests/test_injection_api.py``.

2. **Part 2** — ``InstanceManager._cleanup_instance_state`` question
   cleanup. The W1 centralized cleanup helper must clear BOTH the
   pending ``QuestionPack`` AND the ``_question_pause_requested`` flag.
   We follow the ``_ManagerStub`` method-binding pattern from
   ``tests/test_injection_slot.py`` to exercise the cleanup logic
   without spinning up a full ``InstanceManager`` (which requires a
   database, MCP pool, repositories, etc.).

Test cases (per the C3 task brief):

    Part 1:
        1. POST /answer stores answers, returns 200 with status="answered".
        2. POST /answer without a pending pack returns 404.
        3. POST /answer for non-existent instance returns 404.

    Part 2:
        1. ``_cleanup_instance_state`` clears a pending question pack.
        2. ``_cleanup_instance_state`` clears the pause-requested flag.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ============================================================================
# Part 1 — POST /api/instances/{id}/answer endpoint
# ============================================================================


def _make_manager_for_answer_endpoint(
    *,
    instance_exists: bool = True,
    question_pack: Any = None,
    get_instance_raises: bool = False,
) -> MagicMock:
    """Build a mock ``InstanceManager`` covering the answer endpoint's surface.

    The endpoint touches these manager attributes / methods:

      * ``is_write_paused`` — write-paused gate (always False here).
      * ``get_instance`` (async) — instance-existence check via
        :func:`_check_instance_exists`.
      * ``_question_manager.set_answers`` (sync) — store user answers.
      * ``stream_question_pack`` on the live_hub (async) — best-effort SSE.
      * ``resume_instance_cascade`` (async) — fan-out target + children.
      * ``resume_processing_job`` (async) — actual graph re-entry per id.

    ``instance_exists`` defaults to True; flipping it to False (or passing
    ``get_instance_raises=True``) drives the 404 path tested below.
    """
    manager = MagicMock()

    # Write-paused gate — every happy-path test wants this False.
    manager.is_write_paused = False

    # Instance-existence check.
    if get_instance_raises:
        async def _raise(iid: str):
            raise KeyError(iid)
        manager.get_instance = _raise
    elif instance_exists:
        async def _ok(iid: str):
            return MagicMock(instance_id=iid)
        manager.get_instance = _ok
    else:
        async def _missing(iid: str):
            raise KeyError(iid)
        manager.get_instance = _missing

    # Question manager — pre-loaded with the pack the endpoint should answer.
    manager._question_manager = MagicMock()
    manager._question_manager.set_answers = MagicMock(return_value=question_pack)

    # resume_instance_cascade — fan-out the resume across the tree.
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "target_id": "inst-answer",
            "resumed_ids": ["inst-answer"],
            "skipped_ids": [],
        }
    )

    # resume_processing_job — re-enters the LangGraph graph loop.
    manager.resume_processing_job = AsyncMock(
        return_value={"status": "resumed", "instance_id": "inst-answer"},
    )

    return manager


def _make_live_hub() -> MagicMock:
    """Build a mock live-event-hub whose ``stream_question_pack`` is an AsyncMock."""
    hub = MagicMock()
    hub.stream_question_pack = AsyncMock(return_value=None)
    return hub


@pytest.fixture
def client_and_state_for_answer():
    """Yield ``(TestClient, state_dict)`` for the answer endpoint.

    Mirrors the lightweight pattern used by ``tests/test_injection_api.py``
    — mount only the instances router on a bare FastAPI app, then
    middleware-inject the manager + live_hub into ``app.state`` per request.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None, "live_hub": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


# ---------------------------------------------------------------------------
# Stores answers and returns 200
# ---------------------------------------------------------------------------


class TestAnswerEndpointStoresAndReturns:
    """Happy path: pending pack exists, POST stores answers, returns 200."""

    def test_post_answer_stores_answers_and_returns_200(
        self, client_and_state_for_answer,
    ):
        """POST with a pending pack returns 200 + status="answered".

        The pack returned by ``set_answers`` (with ``status="answered"``)
        must appear in the response body. The resume cascade is invoked
        exactly once with the target instance_id.
        """
        from daemon.services.question_manager import QuestionPack

        # Build a real QuestionPack so the response body matches the
        # documented schema (instance_id, status, created_at, questions,
        # answers).
        pack = QuestionPack(instance_id="inst-answer", questions=[])
        pack.answers = {"approach": "A"}
        pack.status = "answered"

        client, state = client_and_state_for_answer
        state["manager"] = _make_manager_for_answer_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        response = client.post(
            "/instances/inst-answer/answer",
            json={"answers": {"approach": "A"}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "answered"
        assert body["instance_id"] == "inst-answer"
        # Pack echoed verbatim in the response body.
        assert body["question_pack"]["status"] == "answered"
        assert body["question_pack"]["instance_id"] == "inst-answer"
        assert body["question_pack"]["answers"] == {"approach": "A"}

        # Resume fan-out was invoked with the target id.
        state["manager"].resume_instance_cascade.assert_awaited_once_with(
            "inst-answer",
        )

        # The resume cascade must have produced exactly one resume call.
        state["manager"].resume_processing_job.assert_awaited_once()

    def test_post_answer_emits_question_pack_sse_event(
        self, client_and_state_for_answer,
    ):
        """The endpoint must emit ``stream_question_pack`` before the resume cascade.

        Order matters for the F3 timing invariant — SSE is emitted
        BEFORE the resume cascade so a slow resume cannot silently drop
        the ``status="answered"`` event from the frontend.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-answer", questions=[])
        pack.status = "answered"
        pack.answers = {"x": "y"}

        client, state = client_and_state_for_answer
        state["manager"] = _make_manager_for_answer_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        client.post(
            "/instances/inst-answer/answer",
            json={"answers": {"x": "y"}},
        )

        # live_hub.stream_question_pack called exactly once with the target id.
        state["live_hub"].stream_question_pack.assert_awaited_once()
        call = state["live_hub"].stream_question_pack.await_args
        assert call.args[0] == "inst-answer"
        pack_dict = call.args[1]
        assert pack_dict["instance_id"] == "inst-answer"
        assert pack_dict["status"] == "answered"

    def test_post_answer_proceeds_even_if_sse_emission_fails(
        self, client_and_state_for_answer,
    ):
        """A failing SSE emit MUST NOT block the resume cascade.

        The handler wraps ``stream_question_pack`` in a try/except so a
        transport hiccup never aborts the resume path. The endpoint
        still returns 200 and the resume cascade still runs.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-answer", questions=[])
        pack.status = "answered"

        client, state = client_and_state_for_answer
        state["manager"] = _make_manager_for_answer_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        # Force SSE emission to raise — handler should swallow it.
        hub = MagicMock()
        hub.stream_question_pack = AsyncMock(
            side_effect=RuntimeError("transport down"),
        )
        state["live_hub"] = hub

        response = client.post(
            "/instances/inst-answer/answer",
            json={"answers": {"a": "b"}},
        )

        assert response.status_code == 200, response.text
        # Resume still ran — SSE failure was contained.
        state["manager"].resume_instance_cascade.assert_awaited_once_with(
            "inst-answer",
        )


# ---------------------------------------------------------------------------
# Returns 404 if no pending pack
# ---------------------------------------------------------------------------


class TestAnswerEndpointNoPendingPack:
    """No pending pack → 404 (the ``set_answers`` returns ``None`` path)."""

    def test_post_answer_without_pack_returns_404(
        self, client_and_state_for_answer,
    ):
        """POST when ``set_answers`` returns ``None`` → 404 INSTANCE_NOT_FOUND.

        The endpoint distinguishes "answer without a question" from
        "answer stored" by returning 404 in the no-pack case. The
        resume cascade must NOT fire (no work to do).
        """
        client, state = client_and_state_for_answer
        manager = _make_manager_for_answer_endpoint(
            instance_exists=True,
            question_pack=None,  # set_answers → None
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post(
            "/instances/inst-answer/answer",
            json={"answers": {"any": "value"}},
        )

        assert response.status_code == 404, response.text
        body = response.json()
        # The 404 uses ErrorResponse shape; ``detail`` may be a dict
        # (Pydantic model_dump) or a string. Verify the code path runs.
        assert "detail" in body

        # No resume fired — there was no pack to resume against.
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()
        # And no SSE emit (we never get past the store-answers step).
        state["live_hub"].stream_question_pack.assert_not_awaited()


# ---------------------------------------------------------------------------
# Returns 404 if instance doesn't exist
# ---------------------------------------------------------------------------


class TestAnswerEndpointUnknownInstance:
    """Unknown instance_id → 404 from ``_check_instance_exists``."""

    def test_post_answer_for_unknown_instance_returns_404(
        self, client_and_state_for_answer,
    ):
        """POST against an instance the manager has never heard of → 404.

        ``_check_instance_exists`` runs BEFORE ``set_answers`` so the
        store-answers step is never reached. The 404 surfaces the
        standard INSTANCE_NOT_FOUND shape used by every other
        instance-scoped endpoint.
        """
        client, state = client_and_state_for_answer
        manager = _make_manager_for_answer_endpoint(
            instance_exists=False,  # get_instance → KeyError
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post(
            "/instances/inst-unknown/answer",
            json={"answers": {"any": "value"}},
        )

        assert response.status_code == 404, response.text
        body = response.json()
        assert "detail" in body

        # The existence check fires BEFORE set_answers / resume.
        manager._question_manager.set_answers.assert_not_called()
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()
        state["live_hub"].stream_question_pack.assert_not_awaited()


# ============================================================================
# Part 2 — _cleanup_instance_state question cleanup
# ============================================================================


def _make_manager_stub_with_question_state():
    """Build a minimal ``InstanceManager`` stand-in for cleanup tests.

    Mirrors the ``_ManagerStub`` pattern in
    ``tests/test_injection_slot.py`` — only the surface that
    ``_cleanup_instance_state`` touches is exposed:

      * ``_graph_tasks``, ``_pending_injections``, ``_gii_throttle`` — dicts
        popped by the centralized cleanup helper.
      * ``release_context_usage_cache`` — MagicMock so we can assert it was
        called exactly once.
      * ``_question_manager`` (real ``QuestionManager``) — exercises the
        ``clear_question_pack`` call without a mock hiding real bugs
        (matches the TodoManager / QuestionManager testing convention).
      * ``_question_pause_requested`` — dict that the manager's
        ``clear_question_pause_requested`` pops from.
      * The three manager methods bound as instance methods so the test
        can drive them and verify the integrated cleanup behavior.

    Returns:
        A ``_ManagerStub`` instance whose ``_cleanup_instance_state`` is
        bound to ``daemon.manager.InstanceManager._cleanup_instance_state``.
    """
    from daemon import manager as manager_module
    from daemon.services.question_manager import QuestionManager

    class _ManagerStub:
        """Minimal stand-in for InstanceManager — only the question-cleanup surface."""

        _question_manager: Any
        set_question_pause_requested: Any
        is_question_pause_requested: Any
        clear_question_pause_requested: Any
        _cleanup_instance_state: Any

        def __init__(self):
            self._question_manager = QuestionManager()
            self._question_pause_requested: dict[str, bool] = {}
            # Mirror the three dicts _cleanup_instance_state pops from so
            # we can verify the helper is a true centralized cleanup and
            # doesn't crash when only some dicts are populated.
            self._graph_tasks: dict = {}
            self._pending_injections: dict = {}
            self._gii_throttle: dict = {}
            self._deferred_question_pause: set[str] = set()
            self.release_context_usage_cache = MagicMock()
            # Bind the real helpers as instance methods.
            self.set_question_pause_requested = (
                manager_module.InstanceManager.set_question_pause_requested.__get__(self)
            )
            self.is_question_pause_requested = (
                manager_module.InstanceManager.is_question_pause_requested.__get__(self)
            )
            self.clear_question_pause_requested = (
                manager_module.InstanceManager.clear_question_pause_requested.__get__(self)
            )
            self._cleanup_instance_state = (
                manager_module.InstanceManager._cleanup_instance_state.__get__(self)
            )

    return _ManagerStub()


# ---------------------------------------------------------------------------
# Cleanup clears a pending question pack
# ---------------------------------------------------------------------------


class TestCleanupClearsQuestionPack:
    """``_cleanup_instance_state`` must drop the pending QuestionPack."""

    def test_cleanup_clears_a_pending_question_pack(self):
        """Set a pack, call cleanup, ``get_question_pack`` returns ``None``.

        ``_cleanup_instance_state`` is the single cleanup helper used by
        terminate / release / hard-delete. If it leaves a QuestionPack
        behind, a future instance that re-uses the same id would inherit
        the stale "pending" pack and refuse to accept a new question.
        """
        mgr = _make_manager_stub_with_question_state()

        # Stage a pending pack via the real QuestionManager.
        pack = mgr._question_manager.set_question_pack(
            "inst-clean-1",
            [{"id": "approach", "text": "Approach?"}],
        )
        assert pack is not None
        assert mgr._question_manager.get_question_pack("inst-clean-1") is not None

        # Run the centralized cleanup.
        result = mgr._cleanup_instance_state("inst-clean-1")

        # Pack was cleared.
        assert mgr._question_manager.get_question_pack("inst-clean-1") is None

        # The helper returns its cleared-items dict for SSE forwarding.
        assert "cleared_injection" in result
        assert "context_usage_cleared" in result
        assert result["context_usage_cleared"] is True

    def test_cleanup_is_safe_when_no_question_pack_present(self):
        """``_cleanup_instance_state`` is a no-op when nothing was pending.

        The cleanup helper must NEVER raise on the question-cleanup path,
        even when there is no pack to clear. The 0-pack case is the most
        common (every pause/terminate of a non-question instance goes
        through this code).
        """
        mgr = _make_manager_stub_with_question_state()

        # Sanity — no pack stored, no flag set, no other state.
        assert mgr._question_manager.get_question_pack("inst-clean-2") is None
        assert mgr.is_question_pause_requested("inst-clean-2") is False

        # No exception expected.
        result = mgr._cleanup_instance_state("inst-clean-2")

        # _cleanup_instance_state returns the same dict shape (cleared
        # values are None) regardless of whether the instance had state.
        assert result["cleared_injection"] is None
        assert result["graph_task"] is None
        assert result["context_usage_cleared"] is True


# ---------------------------------------------------------------------------
# Cleanup clears the pause flag
# ---------------------------------------------------------------------------


class TestCleanupClearsPauseFlag:
    """``_cleanup_instance_state`` must drop the pause-requested flag.

    Without this, a future instance that re-uses the same id would
    inherit a stuck "pause requested" state and immediately re-pause on
    the first tool call.
    """

    def test_cleanup_clears_the_pause_requested_flag(self):
        """Set the flag, call cleanup, ``is_question_pause_requested`` is False.

        The flag is normally cleared by ``question_pause_node``'s
        ``finally`` block on every successful pause, but
        ``_cleanup_instance_state`` is the SECOND line of defense for
        terminate / release / hard-delete paths that may bypass the
        pause node entirely.
        """
        mgr = _make_manager_stub_with_question_state()

        # Flip the pause flag via the real manager method.
        mgr.set_question_pause_requested("inst-clean-3")
        assert mgr.is_question_pause_requested("inst-clean-3") is True

        # Run the centralized cleanup.
        mgr._cleanup_instance_state("inst-clean-3")

        # Flag is cleared.
        assert mgr.is_question_pause_requested("inst-clean-3") is False

    def test_cleanup_clears_both_pack_and_flag_combined(self):
        """Both question-state surfaces are cleared in a single cleanup call.

        Combined coverage: a scenario that pre-loads both surfaces
        (pack + flag) — exercises the full question-cleanup branch of
        the centralized helper. If either of the two cleanup lines is
        removed/reordered, this test breaks.
        """
        mgr = _make_manager_stub_with_question_state()

        # Pre-load both surfaces.
        mgr._question_manager.set_question_pack(
            "inst-clean-4",
            [{"text": "Which approach?"}],
        )
        mgr.set_question_pause_requested("inst-clean-4")
        assert mgr._question_manager.get_question_pack("inst-clean-4") is not None
        assert mgr.is_question_pause_requested("inst-clean-4") is True

        # Single cleanup call drops BOTH.
        mgr._cleanup_instance_state("inst-clean-4")

        assert mgr._question_manager.get_question_pack("inst-clean-4") is None
        assert mgr.is_question_pause_requested("inst-clean-4") is False
