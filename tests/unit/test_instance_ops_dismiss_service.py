"""Tests for the extracted dismiss-question service.

Covers ``daemon/services/instance_ops_service.py`` — the body extracted
from ``POST /api/instances/{instance_id}/question/dismiss``
(``daemon/routers/instances.py::dismiss_question``) — and the router's
delegation to it.

The extraction exists so the ``instance_ops`` tool category can dismiss a
pending question pack WITHOUT importing a router (a tools→routers
dependency inversion). The router keeps its exact decorator, path,
signature and HTTP contract; only the body moved.

Test cases:

    1. ROUTE-TABLE READBACK PIN (DV-2 / critical note ``ed2dfe67``) —
       ``POST /instances/{instance_id}/question/dismiss`` AND
       ``POST /instances/{instance_id}/answer`` are still bound to their
       own handlers with the same paths/methods.
    2. SSE-BEFORE-CASCADE ORDERING (F3) — the ``question_pack`` SSE emit
       fires BEFORE the resume cascade.
    3. The typed errors re-map onto the route's HTTP status codes
       (503 / 404 / 404 / 409) with the SAME ``detail`` envelopes.
    4. The recorded-set release runs on EVERY exit path (the
       ``finally``), including the cascade-failure path.
    5. The service's public API shape is exactly what the tool imports
       (a plain ``live_hub`` parameter, typed errors carrying
       ``.message``).
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

INSTANCE_ID = "inst-dismiss"


# ============================================================================
# Fixtures / helpers
# ============================================================================


def _make_pack(*, status: str = "pending"):
    """Build a QuestionPack in the requested status."""
    from daemon.services.question_manager import QuestionPack

    pack = QuestionPack(instance_id=INSTANCE_ID, questions=[])
    if status != "pending":
        pack.status = status
    return pack


def _make_manager(
    *,
    instance_exists: bool = True,
    question_pack: Any = None,
    is_write_paused: bool = False,
    order: list[str] | None = None,
) -> MagicMock:
    """Build a mock ``InstanceManager`` covering the dismiss service surface.

    The service touches:

      * ``is_write_paused`` — write-paused gate (503 path).
      * ``get_instance`` (async) — instance-existence check (404 path).
      * ``_question_manager.get_question_pack`` (sync) — read the pack.
      * ``_question_manager.clear_question_pack`` (sync) — drop the pack.
      * ``clear_question_pause_requested`` (sync) — drop the pause flag.
      * ``_deferred_question_pause`` (set) — discard the deferred marker.
      * ``resume_processing_job`` (async) — resolve the dismissal handle.
      * ``resume_instance_cascade`` (async) — cascade-resume the tree.

    ``order`` (optional) collects the call order — the F3 ordering pin
    reads it.
    """
    calls = order if order is not None else []

    manager = MagicMock()
    manager.is_write_paused = is_write_paused

    if instance_exists:

        async def _ok(iid: str):
            return MagicMock(instance_id=iid)

        manager.get_instance = _ok
    else:

        async def _missing(iid: str):
            raise KeyError(iid)

        manager.get_instance = _missing

    manager._question_manager = MagicMock()
    manager._question_manager.get_question_pack = MagicMock(
        return_value=question_pack
    )
    manager._question_manager.clear_question_pack = MagicMock(return_value=None)

    manager.clear_question_pause_requested = MagicMock(return_value=None)
    manager._deferred_question_pause = set()

    async def _resume_processing_job(instance_id, message=None, silent=False):
        calls.append(f"resume_processing_job:{instance_id}")
        return {"status": "resumed", "instance_id": instance_id}

    manager.resume_processing_job = AsyncMock(
        side_effect=_resume_processing_job
    )

    async def _resume_instance_cascade(instance_id, **kwargs):
        calls.append(f"resume_instance_cascade:{instance_id}")
        return {
            "target_id": instance_id,
            "resumed_ids": [instance_id],
            "skipped_ids": [],
        }

    manager.resume_instance_cascade = AsyncMock(
        side_effect=_resume_instance_cascade
    )

    return manager


def _make_live_hub(*, order: list[str] | None = None) -> MagicMock:
    """Build a mock live-event-hub whose ``stream_question_pack`` records order."""
    calls = order if order is not None else []

    hub = MagicMock()

    async def _stream_question_pack(instance_id, payload):
        calls.append(f"sse:{instance_id}:{payload.get('status')}")

    hub.stream_question_pack = AsyncMock(side_effect=_stream_question_pack)
    return hub


def _idx(order: list[str], prefix: str) -> int:
    """Index of the first recorded call whose entry starts with ``prefix``."""
    for i, entry in enumerate(order):
        if entry.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} not recorded in {order}")


@pytest.fixture
def client_and_state():
    """Yield ``(TestClient, state_dict)`` for the dismiss route.

    Mirrors the lightweight pattern in ``tests/test_question_dismiss.py``
    — mount only the instances router on a bare FastAPI app, then
    middleware-inject the manager + live_hub into ``app.state``.
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


@pytest.fixture
def recorded_releases(monkeypatch):
    """Capture the recorded-set release calls (the ``finally`` block).

    The service imports ``release_question_paused_set`` lazily from
    ``daemon.services.question_pause_set`` inside the ``finally``, so
    patching the module attribute intercepts the call.
    """
    from daemon.services import question_pause_set

    calls: list[dict] = []

    async def _fake_release(manager, asker_id, pack_id, *, reason="answer"):
        calls.append({"asker_id": asker_id, "pack_id": pack_id, "reason": reason})
        return {}

    monkeypatch.setattr(
        question_pause_set, "release_question_paused_set", _fake_release
    )
    return calls


# ============================================================================
# (a) ROUTE-TABLE READBACK PIN — DV-2 / critical note ed2dfe67
# ============================================================================


class TestRouteTableReadback:
    """The route table must be unchanged by the extraction.

    Inserting a helper BETWEEN a FastAPI decorator and its handler
    silently re-binds the route — the module imports, the boot smoke
    stays green, and the endpoint 422s (critical note ``ed2dfe67``).
    The body-move itself cannot do that, but the pin is the cheap
    precaution that keeps it that way.
    """

    def test_dismiss_route_still_bound_to_its_handler(self):
        """``POST /instances/{id}/question/dismiss`` → ``dismiss_question``."""
        from daemon.routers.instances import dismiss_question, router

        matches = [
            r
            for r in router.routes
            if r.path == "/instances/{instance_id}/question/dismiss"
        ]
        assert len(matches) == 1, [r.path for r in router.routes]
        route = matches[0]
        assert "POST" in route.methods, route.methods
        assert route.endpoint is dismiss_question

    def test_answer_route_still_bound_to_its_handler(self):
        """``POST /instances/{id}/answer`` → ``answer_questions`` (untouched)."""
        from daemon.routers.instances import answer_questions, router

        matches = [
            r for r in router.routes if r.path == "/instances/{instance_id}/answer"
        ]
        assert len(matches) == 1
        route = matches[0]
        assert "POST" in route.methods, route.methods
        assert route.endpoint is answer_questions

    def test_mounted_app_route_table_carries_both_paths(self):
        """The APP's route table (post-``include_router``) carries both."""
        from daemon.routers.instances import answer_questions, dismiss_question, router

        app = FastAPI()
        app.include_router(router)

        api_routes = [r for r in app.routes if hasattr(r, "endpoint")]
        by_path = {r.path: r for r in api_routes}

        dismiss_route = by_path.get("/instances/{instance_id}/question/dismiss")
        assert dismiss_route is not None, sorted(by_path)
        assert "POST" in dismiss_route.methods, dismiss_route.methods
        assert dismiss_route.endpoint is dismiss_question

        answer_route = by_path.get("/instances/{instance_id}/answer")
        assert answer_route is not None, sorted(by_path)
        assert "POST" in answer_route.methods, answer_route.methods
        assert answer_route.endpoint is answer_questions

    def test_dismiss_route_declares_no_helper_between_decorator_and_handler(self):
        """The decorator is IMMEDIATELY followed by the handler def.

        A helper (or a blank-line/comment gap carrying a def) inserted
        between ``@router.post(...)`` and ``async def dismiss_question``
        re-binds the route. The source-level pin makes the regression
        impossible to miss in review, not just in production.
        """
        import inspect as _inspect

        from daemon.routers import instances as instances_router

        source = _inspect.getsource(instances_router)
        lines = source.splitlines()
        decorator_idx = next(
            i
            for i, line in enumerate(lines)
            if line.startswith('@router.post("/{instance_id}/question/dismiss"')
        )
        # The very next non-empty line must be the handler signature.
        next_line = next(
            line for line in lines[decorator_idx + 1:] if line.strip()
        )
        assert next_line.startswith("async def dismiss_question("), next_line


# ============================================================================
# (b) SSE-BEFORE-CASCADE ORDERING — the F3 contract
# ============================================================================


class TestSseBeforeCascadeOrdering:
    """The ``question_pack`` SSE emit fires BEFORE the resume cascade."""

    async def test_service_emits_sse_before_the_cascade(self):
        """Service level: ``sse`` precedes ``resume_instance_cascade``."""
        from daemon.services.instance_ops_service import dismiss_question_pack

        order: list[str] = []
        manager = _make_manager(question_pack=_make_pack(), order=order)
        live_hub = _make_live_hub(order=order)

        result = await dismiss_question_pack(
            manager, INSTANCE_ID, live_hub=live_hub
        )

        assert _idx(order, "sse") < _idx(order, "resume_instance_cascade"), order
        # The handle is still resolved BEFORE the cascade (the dismissal
        # message needs Task.status == 'paused' to route).
        assert (
            _idx(order, "resume_processing_job")
            < _idx(order, "resume_instance_cascade")
        ), order
        # The emitted payload carries status="dismissed".
        assert f"sse:{INSTANCE_ID}:dismissed" in order, order
        assert result["status"] == "dismissed"

    def test_route_emits_sse_before_the_cascade(self, client_and_state):
        """Route level: the delegation did not reorder the emit."""
        client, state = client_and_state
        order: list[str] = []
        state["manager"] = _make_manager(question_pack=_make_pack(), order=order)
        state["live_hub"] = _make_live_hub(order=order)

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 200, response.text
        assert _idx(order, "sse") < _idx(order, "resume_instance_cascade"), order

    async def test_failed_sse_emit_does_not_stop_the_cascade(self):
        """A failing SSE emit is best-effort — the cascade still runs."""
        from daemon.services.instance_ops_service import dismiss_question_pack

        order: list[str] = []
        manager = _make_manager(question_pack=_make_pack(), order=order)
        live_hub = MagicMock()
        live_hub.stream_question_pack = AsyncMock(
            side_effect=RuntimeError("hub down")
        )

        result = await dismiss_question_pack(
            manager, INSTANCE_ID, live_hub=live_hub
        )

        assert "resume_instance_cascade:inst-dismiss" in order, order
        assert result["status"] == "dismissed"


# ============================================================================
# (c) TYPED ERROR RE-MAPPING — one test per typed error
# ============================================================================


class TestTypedErrorRemapping:
    """Each typed service error re-maps onto the route's HTTP envelope."""

    def test_write_paused_maps_to_503(self, client_and_state):
        """``DismissWritePaused`` → 503 with the plain-string detail."""
        from daemon.services.instance_ops_service import DismissWritePaused

        client, state = client_and_state
        state["manager"] = _make_manager(
            question_pack=_make_pack(), is_write_paused=True
        )
        state["live_hub"] = _make_live_hub()

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 503, response.text
        # The 503 detail is the bare string (NOT an ErrorResponse envelope) —
        # same as the pre-extraction route.
        assert response.json()["detail"] == (
            "Writes are paused for database migration"
        )

    def test_unknown_instance_maps_to_404(self, client_and_state):
        """``DismissInstanceNotFound`` → 404 + INSTANCE_NOT_FOUND envelope."""
        from daemon.services.instance_ops_service import DismissInstanceNotFound

        client, state = client_and_state
        state["manager"] = _make_manager(instance_exists=False)
        state["live_hub"] = _make_live_hub()

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 404, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "INSTANCE_NOT_FOUND"
        assert detail["message"] == f"Instance not found: {INSTANCE_ID}"

    def test_no_pending_question_maps_to_404(self, client_and_state):
        """``DismissNoPendingQuestion`` → 404 + INSTANCE_NOT_FOUND envelope."""
        from daemon.services.instance_ops_service import DismissNoPendingQuestion

        client, state = client_and_state
        state["manager"] = _make_manager(question_pack=None)
        state["live_hub"] = _make_live_hub()

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 404, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "INSTANCE_NOT_FOUND"
        assert detail["message"] == (
            f"No question pack for instance {INSTANCE_ID!r}. "
            "The user can only dismiss a question that was actually asked."
        )

    def test_pack_not_pending_maps_to_409(self, client_and_state):
        """``DismissPackNotPending`` → 409 + INVALID_REQUEST envelope."""
        from daemon.services.instance_ops_service import DismissPackNotPending

        client, state = client_and_state
        state["manager"] = _make_manager(
            question_pack=_make_pack(status="answered")
        )
        state["live_hub"] = _make_live_hub()

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "INVALID_REQUEST"
        assert detail["message"] == (
            f"Question pack for instance {INSTANCE_ID!r} is no longer "
            "pending (status: answered). Only pending packs can be "
            "dismissed."
        )

    def test_cascade_failure_maps_to_500(self, client_and_state):
        """``DismissCascadeFailed`` → 500 + INTERNAL_ERROR envelope."""
        client, state = client_and_state
        manager = _make_manager(question_pack=_make_pack())
        manager.resume_instance_cascade = AsyncMock(
            side_effect=RuntimeError("cascade boom")
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        response = client.post(f"/instances/{INSTANCE_ID}/question/dismiss")

        assert response.status_code == 500, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "INTERNAL_ERROR"
        assert "cascade boom" in detail["message"]

    async def test_service_raises_the_typed_errors(self):
        """The service raises the typed errors (not HTTPException)."""
        from daemon.services.instance_ops_service import (
            DismissInstanceNotFound,
            DismissNoPendingQuestion,
            DismissPackNotPending,
            DismissWritePaused,
            dismiss_question_pack,
        )

        with pytest.raises(DismissWritePaused) as paused:
            await dismiss_question_pack(
                _make_manager(is_write_paused=True), INSTANCE_ID
            )
        assert paused.value.message == "Writes are paused for database migration"

        with pytest.raises(DismissInstanceNotFound) as missing:
            await dismiss_question_pack(
                _make_manager(instance_exists=False), INSTANCE_ID
            )
        assert missing.value.message == f"Instance not found: {INSTANCE_ID}"

        with pytest.raises(DismissNoPendingQuestion) as no_pack:
            await dismiss_question_pack(
                _make_manager(question_pack=None), INSTANCE_ID
            )
        assert "No question pack for instance" in no_pack.value.message

        with pytest.raises(DismissPackNotPending) as not_pending:
            await dismiss_question_pack(
                _make_manager(question_pack=_make_pack(status="answered")),
                INSTANCE_ID,
            )
        assert "no longer pending (status: answered)" in not_pending.value.message


# ============================================================================
# The recorded-set release — the ``finally`` block
# ============================================================================


class TestRecordedSetRelease:
    """The recorded side-effect set is released on EVERY exit path."""

    async def test_release_runs_on_the_happy_path(
        self, recorded_releases,
    ):
        """A successful dismiss releases the recorded set."""
        from daemon.services.instance_ops_service import dismiss_question_pack

        manager = _make_manager(question_pack=_make_pack())
        await dismiss_question_pack(manager, INSTANCE_ID, live_hub=None)

        assert len(recorded_releases) == 1, recorded_releases
        assert recorded_releases[0]["asker_id"] == INSTANCE_ID
        assert recorded_releases[0]["reason"] == "dismiss"

    async def test_release_runs_when_the_cascade_raises(
        self, recorded_releases,
    ):
        """A raising cascade must NOT skip the release (the strand bug)."""
        from daemon.services.instance_ops_service import (
            DismissCascadeFailed,
            dismiss_question_pack,
        )

        manager = _make_manager(question_pack=_make_pack())
        manager.resume_instance_cascade = AsyncMock(
            side_effect=RuntimeError("cascade boom")
        )

        with pytest.raises(DismissCascadeFailed):
            await dismiss_question_pack(manager, INSTANCE_ID, live_hub=None)

        assert len(recorded_releases) == 1, recorded_releases
        assert recorded_releases[0]["reason"] == "dismiss"


# ============================================================================
# The service's public API shape (what the tool imports)
# ============================================================================


class TestServiceApiShape:
    """The service exposes exactly the API the tool module imports."""

    def test_public_names_exist(self):
        from daemon.services import instance_ops_service as svc

        for name in (
            "InstanceOpsServiceError",
            "DismissWritePaused",
            "DismissInstanceNotFound",
            "DismissNoPendingQuestion",
            "DismissPackNotPending",
            "dismiss_question_pack",
        ):
            assert hasattr(svc, name), name

    def test_dismiss_question_pack_signature(self):
        """``(manager, instance_id, live_hub=None)`` — live_hub is PLAIN."""
        from daemon.services.instance_ops_service import dismiss_question_pack

        sig = inspect.signature(dismiss_question_pack)
        assert list(sig.parameters) == ["manager", "instance_id", "live_hub"]
        assert sig.parameters["live_hub"].default is None

    def test_typed_errors_carry_a_message_attribute(self):
        """Each error carries the human text in ``.message``."""
        from daemon.services.instance_ops_service import (
            DismissInstanceNotFound,
            DismissNoPendingQuestion,
            DismissPackNotPending,
            DismissWritePaused,
            InstanceOpsServiceError,
        )

        for cls in (
            DismissWritePaused,
            DismissInstanceNotFound,
            DismissNoPendingQuestion,
            DismissPackNotPending,
        ):
            err = cls("some human text")
            assert issubclass(cls, InstanceOpsServiceError)
            assert err.message == "some human text"
            # ``str(exc)`` is what the tool's refusal envelope carries.
            assert str(err) == "some human text"

    async def test_service_works_without_a_live_hub(self):
        """``live_hub=None`` (the tool shape) is a supported call."""
        from daemon.services.instance_ops_service import dismiss_question_pack

        manager = _make_manager(question_pack=_make_pack())
        result = await dismiss_question_pack(manager, INSTANCE_ID)

        assert result["status"] == "dismissed"
        assert result["instance_id"] == INSTANCE_ID
        assert result["resume_info"]["resumed"] is True
        assert result["resume_info"]["resumed_ids"] == [INSTANCE_ID]
        # The state surfaces were unwound.
        manager._question_manager.clear_question_pack.assert_called_once_with(
            INSTANCE_ID
        )
        manager.clear_question_pause_requested.assert_called_once_with(
            INSTANCE_ID
        )
