"""Unit tests for the PAUSED auto-resume fallback path.

Bug: when a user sends a message to a PAUSED instance that was paused
BEFORE its initial Task was claimed (task stayed PENDING, never reached
RUNNING), ``resume_processing_job`` returns ``None`` because:

  * The pause cascade (``_pause_cascade_db_sync``) only suspends
    tasks that were RUNNING at pause time. A PENDING task is not in
    the ``status = 'running'`` filter, so it never gets a
    ``resume_target_turn_id`` handle.
  * When the user message arrives and the cascade flips the
    instance to RUNNING, ``resume_processing_job`` consults
    ``find_paused_or_cancellable_turn`` which only looks at
    PAUSED/RUNNING/CANCELLED tasks — the PENDING task is not in
    that set, so no paused turn is found.
  * The selector routes to ``invalid_or_missing_handle`` and
    returns ``None`` — the §9.4 answer-gate fallback that used
    to fabricate a Task has been removed, deliberately.

Pre-fix the messages router trusted the ``None`` return and
returned ``auto_resumed: true`` WITHOUT delivering the user's
message — silent data loss (P1).

The fix lives in ``daemon/routers/messages.py``: when
``resume_processing_job`` returns ``None`` for the TARGET
instance, the router falls through to ``enqueue_message``
to deliver the user's message via the normal message queue.
For non-target resumed instances (silent cascade children) the
``None`` outcome remains correct — they need no fallback.

These tests exercise that router-level fallback seam end-to-end
through the real FastAPI ``TestClient`` mounted on a bare
``messages`` router (no DB, no MCP pool). The manager surface
is mocked so we can control ``resume_processing_job``'s return
value and verify ``enqueue_message`` is (or is not) called.

Test scenarios:
  1. ``test_paused_target_resume_none_falls_through_to_enqueue`` —
     target paused, ``resume_processing_job`` returns ``None``,
     ``enqueue_message`` is called with the user message,
     the response carries the real ``message_id``.
  2. ``test_paused_target_fallback_forwards_images`` — images
     are forwarded through the fallback path so vision messages
     are not lost either.
  3. ``test_paused_non_target_resume_none_does_not_enqueue`` —
     non-target resumed instances (silent cascade children) get
     the ``no_active_job`` result WITHOUT fallback enqueue.
  4. ``test_paused_target_fallback_enqueue_failure_surfaces_error``
     — when ``enqueue_message`` itself fails, the error
     surfaces in ``resume_results`` and the response still
     returns 200 (the cascade has already flipped the instance
     to RUNNING; we cannot un-resume).
  5. ``test_paused_target_resume_success_skips_fallback`` —
     regression guard: when ``resume_processing_job`` returns a
     normal result, the fallback path is NOT triggered.

Run with::

    pytest tests/unit/test_paused_auto_resume_fallback.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_paused_manager(
    *,
    instance_id: str = "inst-paused",
    resumed_ids: list[str] | None = None,
    resume_processing_return: dict | None = None,
    enqueue_return: dict | None = None,
    enqueue_side_effect: BaseException | None = None,
    with_vision: bool = False,
):
    """Build a mock ``InstanceManager`` shaped like the PAUSED branch.

    Args:
        instance_id: The instance ID surfaced via ``get_instance_info``.
        resumed_ids: The list returned by ``resume_instance_cascade``.
            Default ``[instance_id]`` (single-instance tree, no children).
        resume_processing_return: Return value of ``resume_processing_job``.
            Pass ``None`` to trigger the new fallback path.
        enqueue_return: Return value of ``enqueue_message`` (the
            fallback path). Defaults to a realistic AsyncMessageResult.
        enqueue_side_effect: If set, ``enqueue_message`` raises this
            exception (used to verify the error-surfacing branch).
        with_vision: If ``True``, configure ``model_vision`` so the
            router's image-validation gate accepts the request.

    Returns:
        A MagicMock with the messages-router surface fully wired.
    """
    if resumed_ids is None:
        resumed_ids = [instance_id]

    manager = MagicMock()
    manager.is_write_paused = False
    manager.config = MagicMock()
    # Router gates image requests on ``manager.config.llm.model_vision``;
    # tests that send images opt in via ``with_vision=True``.
    manager.config.llm.model_vision = (
        "openai/gpt-4o" if with_vision else None
    )
    manager.get_instance_info = MagicMock(
        return_value={"status": "paused", "instance_id": instance_id}
    )
    # Command dispatcher — the route calls
    # ``await manager.command_dispatcher.dispatch(...)`` to handle
    # slash commands BEFORE the PAUSED branch. Stub the dispatcher
    # so the MagicMock default (a non-awaitable) does not blow up.
    manager.command_dispatcher = MagicMock()
    manager.command_dispatcher.dispatch = AsyncMock(
        return_value=MagicMock(kind="passthrough", sanitized_text=None, ack=None)
    )

    # resume_instance_cascade returns the resumed/skipped dict.
    manager.resume_instance_cascade = AsyncMock(return_value={
        "target_id": instance_id,
        "resumed_ids": resumed_ids,
        "skipped_ids": [],
    })

    # resume_processing_job — caller controls whether None or a dict.
    # C1 facade-forwarding fix (council NEEDS-FIXES, 2026-09-19):
    # the facade accepts ``image_refs=`` as a keyword-only kwarg.
    # The AsyncMock below accepts ANY kwargs — a positional kwarg
    # drop would be masked by AsyncMock. We add a signature-shape
    # assertion in test_resume_facade_signature_accepts_image_refs_keyword_only
    # below to close the mask class.
    if resume_processing_return is None:
        manager.resume_processing_job = AsyncMock(return_value=None)
    else:
        manager.resume_processing_job = AsyncMock(
            return_value=resume_processing_return
        )

    # enqueue_message — the fallback path.
    if enqueue_side_effect is not None:
        manager.enqueue_message = AsyncMock(side_effect=enqueue_side_effect)
    else:
        if enqueue_return is None:
            enqueue_return = MagicMock(
                message_id="fallback-msg-uuid",
                job_id="fallback-job-uuid",
                queued=False,
            )
        manager.enqueue_message = AsyncMock(return_value=enqueue_return)

    return manager


def _make_live_hub() -> MagicMock:
    """Mock LiveEventHub — PAUSED path does not emit SSE injection events."""
    hub = MagicMock()
    hub.stream_message = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# Fixture: FastAPI TestClient mounted only on the messages router
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_state():
    """Yield (TestClient, state_dict) — mirrors ``tests/test_injection_api.py``.

    The state dict holds the manager and live_hub; per-test middleware
    injects them into ``app.state`` so each request sees the manager
    the test wants. This isolates the routing logic from the rest of
    the daemon (no DB, no MCP pool, no LLM).
    """
    from daemon.routers.messages import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None, "live_hub": None, "tmp_image_store": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        # Inject tmp_image_store so the router's image_refs conversion
        # hook (``getattr(request.app.state, "tmp_image_store", None)``)
        # resolves on ref-send tests; ``None`` keeps non-ref tests on
        # the no-hook path (mirrors the ref-path router test harness).
        request.app.state.tmp_image_store = state["tmp_image_store"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


# ---------------------------------------------------------------------------
# Tests — PAUSED target with resume_processing_job returning None
# ---------------------------------------------------------------------------


class TestPausedAutoResumeFallback:
    """Verify the fallback enqueue path when ``resume_processing_job`` returns ``None``."""

    def test_paused_target_resume_none_falls_through_to_enqueue(
        self, client_and_state
    ):
        """Target PAUSED, ``resume_processing_job`` returns ``None`` → fallback enqueue.

        The user's message must NOT be silently dropped. The router
        must call ``enqueue_message`` with the original content
        and surface the real ``message_id`` in the response.
        """
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return=None,
            enqueue_return=MagicMock(
                message_id="msg-fallback-1234",
                job_id="job-fallback-1234",
                queued=False,
            ),
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "hello after pause"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_resumed"] is True
        assert body["content"] == "hello after pause"
        # The fix: a real message_id is returned (no longer None).
        assert body["message_id"] == "msg-fallback-1234"

        # Verify the fallback enqueue was called with the right args.
        manager.enqueue_message.assert_awaited_once()
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "inst-paused"
        assert call_kwargs["message"] == "hello after pause"
        assert call_kwargs["source"] == "api_resume_fallback"
        assert call_kwargs["images"] is None

        # Verify resume_info reflects the fallback route.
        resume_info = body["resume_info"]
        assert resume_info["target_id"] == "inst-paused"
        target_result = resume_info["resume_results"]["inst-paused"]
        assert target_result["status"] == "queued"
        assert target_result["message_id"] == "msg-fallback-1234"
        assert target_result["route"] == "api_resume_fallback"

    def test_paused_target_fallback_forwards_images(self, client_and_state):
        """Fallback path forwards images so vision messages survive the resume."""
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return=None,
            with_vision=True,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        images = ["data:image/png;base64,iVBORw0KGgo="]
        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "what is this?", "images": images},
        )

        assert resp.status_code == 200, resp.text
        manager.enqueue_message.assert_awaited_once()
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["images"] == images

    def test_paused_non_target_resume_none_does_not_enqueue(self, client_and_state):
        """Non-target resumed instances (silent cascade children) MUST NOT enqueue.

        Regression guard for the fix: only the target instance gets the
        fallback enqueue. Silent cascade children returning ``None`` is
        the correct outcome — they don't need a fresh message.
        """
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-parent",
            resumed_ids=["inst-parent", "inst-child-1", "inst-child-2"],
            resume_processing_return=None,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-parent/messages",
            json={"content": "go parent"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Exactly ONE enqueue (the target). Children are silent cascade.
        assert manager.enqueue_message.await_count == 1
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "inst-parent"

        # Children get the no_active_job marker — no fallback.
        children_results = {
            k: v for k, v in body["resume_info"]["resume_results"].items()
            if k != "inst-parent"
        }
        assert children_results == {
            "inst-child-1": {"status": "no_active_job"},
            "inst-child-2": {"status": "no_active_job"},
        }

    def test_paused_target_fallback_enqueue_failure_surfaces_error(
        self, client_and_state
    ):
        """When fallback enqueue ALSO fails, the error surfaces in ``resume_results``.

        The HTTP response is still 200 (the cascade has already flipped
        the instance to RUNNING; we cannot un-resume), but the client
        sees an explicit error in ``resume_results`` so the frontend
        can surface it. ``message_id`` falls back to ``None`` since no
        message was actually queued.
        """
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return=None,
            enqueue_side_effect=RuntimeError("queue is down"),
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "this will fail"},
        )

        # 200 — cascade already flipped status to RUNNING.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_resumed"] is True
        # No message_id surfaced since the fallback enqueue failed.
        assert body["message_id"] is None

        target_result = body["resume_info"]["resume_results"]["inst-paused"]
        assert target_result["status"] == "error"
        assert "queue is down" in target_result["error"]
        assert target_result["route"] == "api_resume_fallback_failed"

        manager.enqueue_message.assert_awaited_once()

    def test_paused_target_resume_success_skips_fallback(self, client_and_state):
        """Regression guard: when ``resume_processing_job`` returns a normal result, fallback is NOT triggered.

        The fallback is ONLY for ``None`` returns. A successful resume
        (e.g. ``{status: "resumed", instance_id: ...}``) must take the
        existing path — no extra ``enqueue_message`` call.
        """
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return={
                "status": "resumed",
                "instance_id": "inst-paused",
            },
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "go"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Existing behavior: message_id is None when resume succeeded.
        assert body["message_id"] is None

        target_result = body["resume_info"]["resume_results"]["inst-paused"]
        assert target_result["status"] == "resumed"
        assert "route" not in target_result

        manager.enqueue_message.assert_not_called()


# ---------------------------------------------------------------------------
# C1 facade-acceptance mask-pin (council NEEDS-FIXES, 2026-09-19)
# ---------------------------------------------------------------------------


class TestResumeFacadeKwargAcceptance:
    """C1 facade-forwarding fix mask-pin: assert the real
    ``InstanceManager.resume_processing_job`` signature accepts
    ``image_refs=`` as a keyword-only kwarg.

    The AsyncMock above at line ~:127 masks a kwarg-drop bug
    (AsyncMock accepts any kwargs). The pin here closes the mask
    class — a future refactor that drops ``image_refs`` from the
    facade signature trips this test.
    """

    def test_resume_facade_signature_accepts_image_refs_keyword_only(self):
        """The real ``InstanceManager.resume_processing_job``
        signature carries ``image_refs`` as a KEYWORD-ONLY kwarg
        (post-C1 fix)."""
        import inspect

        from daemon.manager import InstanceManager

        sig = inspect.signature(InstanceManager.resume_processing_job)
        image_refs_param = sig.parameters.get("image_refs")
        assert image_refs_param is not None, (
            "resume_processing_job is missing the image_refs kwarg — "
            "C1 facade-forwarding fix REGRESSED"
        )
        # Keyword-only discipline: a positional caller would mask
        # the kwarg-drop bug class.
        assert (
            image_refs_param.kind == inspect.Parameter.KEYWORD_ONLY
        ), (
            f"image_refs must be keyword-only on resume_processing_job "
            f"(C1 facade discipline); got kind={image_refs_param.kind!r}"
        )

    def test_resume_router_forwards_images_kwarg(self, client_and_state):
        """The router calls ``resume_processing_job`` with
        ``images=message.images`` on the PAUSED-branch resume call
        (legacy data-URI channel — the request carries a data-URI
        ``images`` payload, so THAT is the kwarg this test pins)."""
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return=None,
            with_vision=True,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        refs = ["/api/tmp_images/" + "a" * 32]
        # Note: the manager mock's image_refs handling depends on
        # with_vision=True so the legacy vision gate passes (image_refs
        # path is gated on model_vision in the router). We use
        # ``images=`` here to keep the test focused on the
        # resume_processing_job kwarg-passing, not the
        # image_refs/vision gate.
        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "look", "images": ["data:image/png;base64,abc"]},
        )

        assert resp.status_code == 200, resp.text
        # The router passed images= (legacy data-URI channel). The
        # AsyncMock on resume_processing_job captured the call —
        # assert the kwarg was forwarded.
        manager.resume_processing_job.assert_awaited()
        call = manager.resume_processing_job.call_args
        # Pin the kwarg exists in the call (the AsyncMock-mask
        # class: if the facade signature dropped images=, this
        # call would still succeed via the mock's permissive
        # signature; the actual facade would TypeError).
        assert "images" in call.kwargs

    def test_resume_router_forwards_image_refs_kwarg(self, client_and_state):
        """PAUSED-branch resume call forwards ``image_refs`` — the
        router's real wiring (``daemon/routers/messages.py`` PAUSED
        branch) passes ``image_refs=message.image_refs`` for the
        target instance (phase-2 clipboard channel). The request goes
        through the REAL ``pre_dispatch_image_hook`` conversion seam
        (canonical refs normalize to themselves), and the captured
        ``resume_processing_job`` call must carry the refs."""
        client, state = client_and_state
        manager = _make_paused_manager(
            instance_id="inst-paused",
            resume_processing_return={
                "status": "ok",
                "message_id": "msg-resumed-1",
            },
            with_vision=True,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()
        # The conversion hook requires an in-process tmp-image store
        # (else the router 503s before reaching the PAUSED branch).
        store = MagicMock()
        store.get_image_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake")
        state["tmp_image_store"] = store

        refs = ["/api/tmp_images/" + "a" * 32]
        resp = client.post(
            "/instances/inst-paused/messages",
            json={"content": "look", "image_refs": refs},
        )

        assert resp.status_code == 200, resp.text
        manager.resume_processing_job.assert_awaited()
        call = manager.resume_processing_job.call_args
        # The REAL wiring: target instance keeps the request's
        # (hook-normalized) refs — non-target cascade children get
        # None.
        assert call.kwargs["image_refs"] == refs
