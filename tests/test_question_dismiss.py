"""Tests for the dismiss-question endpoint.

Covers ``POST /api/instances/{instance_id}/question/dismiss``
(``daemon/routers/instances.py:dismiss_question``).

The endpoint lets the user drop a pending question pack without
answering it and resume the paused instance cascade. It mirrors the
answer endpoint end-to-end but unwinds the question state instead of
storing answers. We mount only the ``instances`` router on a bare
FastAPI app and inject the ``InstanceManager`` via ``app.state.manager``
— mirroring the lightweight pattern in ``tests/test_injection_api.py``
and ``tests/test_question_untested_paths.py``.

Test cases:

    1. Happy path: dismiss clears pack + flag + deferred marker, emits
       SSE with ``status="dismissed"``, runs cascade, returns 200.
    2. 404 when no question pack exists for the instance.
    3. 404 when the instance itself is unknown.
    4. SSE failure does NOT block the resume cascade.
    5. SSE payload carries the original question snapshot + status field.
    6. Target instance receives the dismissal HumanMessage; children get
       silent resume.
    7. Write-paused daemon returns 503.
    8. State surfaces are actually cleared after the call (pack gone,
       pause flag False, deferred marker removed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ============================================================================
# Test fixtures — mirror tests/test_question_untested_paths.py helpers
# ============================================================================


def _make_manager_for_dismiss_endpoint(
    *,
    instance_exists: bool = True,
    question_pack: Any = None,
    get_instance_raises: bool = False,
    is_write_paused: bool = False,
) -> MagicMock:
    """Build a mock ``InstanceManager`` covering the dismiss endpoint's surface.

    The endpoint touches these manager attributes / methods:

      * ``is_write_paused`` — write-paused gate (503 path).
      * ``get_instance`` (async) — instance-existence check via
        :func:`_check_instance_exists`.
      * ``_question_manager.get_question_pack`` (sync) — read pack.
      * ``_question_manager.clear_question_pack`` (sync) — drop pack.
      * ``clear_question_pause_requested`` (sync) — drop pause flag.
      * ``_deferred_question_pause`` (set) — discard deferred marker.
      * ``resume_instance_cascade`` (async) — fan-out target + children.
      * ``resume_processing_job`` (async) — actual graph re-entry per id.
      * ``stream_question_pack`` on the live_hub (async) — best-effort SSE.
    """
    manager = MagicMock()

    # Write-paused gate — every happy-path test wants this False.
    manager.is_write_paused = is_write_paused

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

    # Question manager — pack state surface.
    manager._question_manager = MagicMock()
    manager._question_manager.get_question_pack = MagicMock(return_value=question_pack)
    manager._question_manager.clear_question_pack = MagicMock(return_value=None)

    # Pause-flag surface.
    manager.clear_question_pause_requested = MagicMock(return_value=None)

    # Deferred-pause marker surface (C2 fix — the post-graph callback
    # in instance_messaging pops this set; if we leave a marker behind
    # the resume will race with a phantom pause cascade).
    manager._deferred_question_pause = set()

    # resume_instance_cascade — fan-out the resume across the tree.
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "target_id": "inst-dismiss",
            "resumed_ids": ["inst-dismiss"],
            "skipped_ids": [],
        }
    )

    # resume_processing_job — re-enters the LangGraph graph loop.
    manager.resume_processing_job = AsyncMock(
        return_value={"status": "resumed", "instance_id": "inst-dismiss"},
    )

    return manager


def _make_live_hub() -> MagicMock:
    """Build a mock live-event-hub whose ``stream_question_pack`` is an AsyncMock."""
    hub = MagicMock()
    hub.stream_question_pack = AsyncMock(return_value=None)
    return hub


@pytest.fixture
def client_and_state_for_dismiss():
    """Yield ``(TestClient, state_dict)`` for the dismiss endpoint.

    Mirrors the lightweight pattern used by ``tests/test_injection_api.py``
    and ``tests/test_question_untested_paths.py`` — mount only the
    instances router on a bare FastAPI app, then middleware-inject the
    manager + live_hub into ``app.state`` per request.
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


# ============================================================================
# Happy path — dismiss returns 200 + clears state + runs cascade
# ============================================================================


class TestDismissEndpointHappyPath:
    """Happy path: pending pack exists, POST dismisses, returns 200."""

    def test_post_dismiss_returns_200_with_dismissed_status(
        self, client_and_state_for_dismiss,
    ):
        """POST with a pending pack returns 200 + status="dismissed".

        The response body must carry ``status="dismissed"`` and a
        ``resume_info`` block describing the cascade fan-out.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-dismiss",
            questions=[],
        )

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "dismissed"
        assert body["instance_id"] == "inst-dismiss"
        # Resume info mirrors the answer endpoint's shape.
        assert body["resume_info"]["resumed"] is True
        assert body["resume_info"]["target_id"] == "inst-dismiss"
        assert body["resume_info"]["resumed_ids"] == ["inst-dismiss"]
        assert body["resume_info"]["skipped_ids"] == []

    def test_post_dismiss_clears_state_surfaces(
        self, client_and_state_for_dismiss,
    ):
        """Dismiss must clear pack, pause flag, AND deferred-pause marker.

        Without all three, the post-graph callback in instance_messaging
        could fire ``pause_instance_cascade`` for this instance and race
        the resume we just kicked off, producing the exact torn
        PAUSED→RUNNING state we just unwound (C2 fix).
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-dismiss", questions=[])

        client, state = client_and_state_for_dismiss
        manager = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        # Stage a deferred-pause marker to confirm it gets discarded.
        manager._deferred_question_pause.add("inst-dismiss")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-dismiss/question/dismiss")

        # Pack was read, then cleared.
        manager._question_manager.get_question_pack.assert_called_once_with(
            "inst-dismiss",
        )
        manager._question_manager.clear_question_pack.assert_called_once_with(
            "inst-dismiss",
        )
        # Pause flag was dropped.
        manager.clear_question_pause_requested.assert_called_once_with(
            "inst-dismiss",
        )
        # Deferred-pause marker was discarded.
        assert "inst-dismiss" not in manager._deferred_question_pause

    def test_post_dismiss_runs_resume_cascade(
        self, client_and_state_for_dismiss,
    ):
        """Dismiss must run the resume cascade exactly once.

        Cascade + per-instance ``resume_processing_job`` follow the
        same fan-out pattern as the answer endpoint: target gets the
        HumanMessage, children get silent resume.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-dismiss", questions=[])

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-dismiss/question/dismiss")

        state["manager"].resume_instance_cascade.assert_awaited_once_with(
            "inst-dismiss",
        )
        state["manager"].resume_processing_job.assert_awaited_once()


# ============================================================================
# SSE emission
# ============================================================================


class TestDismissEndpointSSE:
    """SSE emission: status="dismissed", carries question snapshot."""

    def test_post_dismiss_emits_question_pack_sse_event(
        self, client_and_state_for_dismiss,
    ):
        """Endpoint must emit ``stream_question_pack`` with status="dismissed".

        Order matters for the F3 timing invariant — SSE is emitted
        BEFORE the resume cascade so a slow resume cannot silently drop
        the event from the frontend.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-dismiss",
            questions=[],
        )
        pack.answers = {}  # answers default to {} on a fresh pack

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-dismiss/question/dismiss")

        state["live_hub"].stream_question_pack.assert_awaited_once()
        call = state["live_hub"].stream_question_pack.await_args
        assert call.args[0] == "inst-dismiss"
        pack_dict = call.args[1]
        # Status flipped to "dismissed" — the frontend reads this to hide
        # the question UI without treating it as an answer.
        assert pack_dict["status"] == "dismissed"
        assert pack_dict["instance_id"] == "inst-dismiss"
        # Frozen schema preserved — created_at, questions, answers all
        # carried through verbatim from the original pack.
        assert "created_at" in pack_dict
        assert "questions" in pack_dict
        assert "answers" in pack_dict

    def test_post_dismiss_carries_question_text_in_sse_payload(
        self, client_and_state_for_dismiss,
    ):
        """SSE payload must include the original question text.

        The frontend may want to display "You dismissed: <question>"
        or simply confirm the right question was dismissed. The
        pack_to_dict schema guarantees the questions array survives
        the status flip.
        """
        from daemon.services.question_manager import QuestionPack, Question

        pack = QuestionPack(
            instance_id="inst-dismiss",
            questions=[
                Question(id="q1", text="Which approach?", options=["A", "B"]),
            ],
        )

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-dismiss/question/dismiss")

        pack_dict = state["live_hub"].stream_question_pack.await_args.args[1]
        assert pack_dict["questions"][0]["text"] == "Which approach?"
        assert pack_dict["questions"][0]["options"] == ["A", "B"]
        assert pack_dict["status"] == "dismissed"

    def test_post_dismiss_proceeds_even_if_sse_emission_fails(
        self, client_and_state_for_dismiss,
    ):
        """A failing SSE emit MUST NOT block the resume cascade.

        The handler wraps ``stream_question_pack`` in a try/except so a
        transport hiccup never aborts the resume path. The endpoint
        still returns 200 and the resume cascade still runs.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-dismiss", questions=[])

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        # Force SSE emission to raise — handler should swallow it.
        hub = MagicMock()
        hub.stream_question_pack = AsyncMock(
            side_effect=RuntimeError("transport down"),
        )
        state["live_hub"] = hub

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 200, response.text
        # Resume still ran — SSE failure was contained.
        state["manager"].resume_instance_cascade.assert_awaited_once_with(
            "inst-dismiss",
        )

    def test_post_dismiss_skips_sse_when_no_live_hub(
        self, client_and_state_for_dismiss,
    ):
        """Missing ``live_hub`` must NOT raise — SSE is best-effort.

        Mirrors the answer endpoint's ``getattr(..., None)`` guard. The
        dismiss path may run in test contexts where the live-event-hub
        is not wired up; the endpoint should still complete the resume
        cascade cleanly.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-dismiss", questions=[])

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = None  # explicitly absent

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 200, response.text
        state["manager"].resume_instance_cascade.assert_awaited_once()


# ============================================================================
# Resume message content
# ============================================================================


class TestDismissEndpointResumeMessage:
    """Target receives the dismissal HumanMessage; children get silent resume."""

    def test_target_instance_receives_dismissal_message(
        self, client_and_state_for_dismiss,
    ):
        """``resume_processing_job`` on the target gets the dismissal text.

        The dismissal message tells the agent the user chose not to
        answer — the agent must receive this as a HumanMessage so its
        next turn can acknowledge the user's intent instead of
        re-asking the same question.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-dismiss", questions=[])

        client, state = client_and_state_for_dismiss
        state["manager"] = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-dismiss/question/dismiss")

        call = state["manager"].resume_processing_job.await_args
        assert call.args[0] == "inst-dismiss"
        assert "dismiss" in call.kwargs["message"].lower()
        assert call.kwargs.get("silent") is False

    def test_child_instances_resume_silently(
        self, client_and_state_for_dismiss,
    ):
        """Children resume with ``message="resume"`` and ``silent=True``.

        Children were paused as a side effect of the parent's question;
        they don't need a new message — the same fan-out pattern as
        the answer endpoint.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(instance_id="inst-parent", questions=[])
        manager = MagicMock()
        manager.is_write_paused = False

        async def _ok(iid: str):
            return MagicMock(instance_id=iid)
        manager.get_instance = _ok

        manager._question_manager = MagicMock()
        manager._question_manager.get_question_pack = MagicMock(return_value=pack)
        manager._question_manager.clear_question_pack = MagicMock()
        manager.clear_question_pause_requested = MagicMock()
        manager._deferred_question_pause = set()

        # Cascade resumes a parent + child tree.
        manager.resume_instance_cascade = AsyncMock(
            return_value={
                "target_id": "inst-parent",
                "resumed_ids": ["inst-parent", "inst-child"],
                "skipped_ids": [],
            }
        )
        manager.resume_processing_job = AsyncMock(
            return_value={"status": "resumed"},
        )

        from daemon.routers.instances import router
        app = FastAPI()
        app.include_router(router)
        state = {"manager": manager, "live_hub": _make_live_hub()}

        @app.middleware("http")
        async def _inject_state(request, call_next):
            request.app.state.manager = state["manager"]
            request.app.state.live_hub = state["live_hub"]
            return await call_next(request)

        client = TestClient(app)
        client.post("/instances/inst-parent/question/dismiss")

        # resume_processing_job called twice — once per resumed id.
        assert manager.resume_processing_job.await_count == 2

        # Collect all (instance_id, message, silent) tuples the endpoint passed.
        calls = manager.resume_processing_job.await_args_list
        per_id_kwargs = {
            c.args[0]: (c.kwargs["message"], c.kwargs.get("silent"))
            for c in calls
        }

        # Target gets the dismissal HumanMessage, child gets silent resume.
        assert "inst-parent" in per_id_kwargs
        target_msg, target_silent = per_id_kwargs["inst-parent"]
        assert "dismiss" in target_msg.lower()
        assert target_silent is False

        assert "inst-child" in per_id_kwargs
        child_msg, child_silent = per_id_kwargs["inst-child"]
        assert child_msg == "resume"
        assert child_silent is True


# ============================================================================
# 404 paths
# ============================================================================


class TestDismissEndpointNoPack:
    """No question pack → 404."""

    def test_post_dismiss_without_pack_returns_404(
        self, client_and_state_for_dismiss,
    ):
        """POST when ``get_question_pack`` returns ``None`` → 404.

        The endpoint distinguishes "dismiss without a question" from
        "dismiss stored" by returning 404 in the no-pack case. The
        resume cascade must NOT fire (no work to do).
        """
        client, state = client_and_state_for_dismiss
        manager = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=None,  # get_question_pack → None
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 404, response.text
        body = response.json()
        assert "detail" in body

        # No state was cleared — there was nothing to clear.
        manager._question_manager.clear_question_pack.assert_not_called()
        manager.clear_question_pause_requested.assert_not_called()
        # No resume fired — there was no pack to resume against.
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()
        # And no SSE emit.
        state["live_hub"].stream_question_pack.assert_not_awaited()


class TestDismissEndpointNonPending:
    """Non-pending pack (already answered/dismissed) → 409 Conflict."""

    def test_post_dismiss_returns_409_when_pack_status_is_answered(
        self, client_and_state_for_dismiss,
    ):
        """POST when ``pack.status != "pending"`` → 409 Conflict.

        The 409 guard prevents dismissing an already-answered pack,
        which would emit a misleading ``status="dismissed"`` SSE event
        and could race the original answer/cascade path. The endpoint
        must NOT clear state, NOT emit SSE, and NOT run the resume
        cascade in this branch.
        """
        from daemon.services.question_manager import QuestionPack

        # Build a pack in the "answered" terminal state — the user
        # already consumed the question, dismiss would be a no-op at
        # best and a misleading SSE emit at worst.
        pack = QuestionPack(instance_id="inst-dismiss", questions=[])
        pack.status = "answered"

        client, state = client_and_state_for_dismiss
        manager = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=pack,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 409, response.text
        body = response.json()
        assert "detail" in body
        # The 409 message must surface the actual pack status so the
        # frontend can distinguish "answered" from "dismissed".
        assert "answered" in body["detail"]["message"]

        # 409 short-circuits BEFORE the cleanup + cascade path —
        # nothing gets cleared, nothing gets resumed, no SSE emit.
        manager._question_manager.clear_question_pack.assert_not_called()
        manager.clear_question_pause_requested.assert_not_called()
        manager._deferred_question_pause.add("inst-dismiss")  # pre-stage
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()
        state["live_hub"].stream_question_pack.assert_not_awaited()


class TestDismissEndpointUnknownInstance:
    """Unknown instance_id → 404 from ``_check_instance_exists``."""

    def test_post_dismiss_for_unknown_instance_returns_404(
        self, client_and_state_for_dismiss,
    ):
        """POST against an instance the manager has never heard of → 404.

        ``_check_instance_exists`` runs BEFORE ``get_question_pack`` so
        the entire question-cleanup / resume path is never reached.
        """
        client, state = client_and_state_for_dismiss
        manager = _make_manager_for_dismiss_endpoint(
            instance_exists=False,  # get_instance → KeyError
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post("/instances/inst-unknown/question/dismiss")

        assert response.status_code == 404, response.text
        body = response.json()
        assert "detail" in body

        # The existence check fires BEFORE get_question_pack / clear / resume.
        manager._question_manager.get_question_pack.assert_not_called()
        manager._question_manager.clear_question_pack.assert_not_called()
        manager.clear_question_pause_requested.assert_not_called()
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()
        state["live_hub"].stream_question_pack.assert_not_awaited()


# ============================================================================
# Write-paused gate
# ============================================================================


class TestDismissEndpointWritePaused:
    """Write-paused daemon → 503 (migration guard)."""

    def test_post_dismiss_returns_503_when_write_paused(
        self, client_and_state_for_dismiss,
    ):
        """POST during a write-paused daemon → 503.

        The write-paused gate runs FIRST — before the existence check —
        to keep the migration contract uniform across all mutating
        endpoints.
        """
        client, state = client_and_state_for_dismiss
        manager = _make_manager_for_dismiss_endpoint(
            instance_exists=True,
            question_pack=None,  # would 404 if we got past the gate
            is_write_paused=True,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post("/instances/inst-dismiss/question/dismiss")

        assert response.status_code == 503, response.text

        # No work done — write-paused is a hard gate.
        manager._question_manager.get_question_pack.assert_not_called()
        manager._question_manager.clear_question_pack.assert_not_called()
        manager.clear_question_pause_requested.assert_not_called()
        manager.resume_instance_cascade.assert_not_awaited()
        state["live_hub"].stream_question_pack.assert_not_awaited()