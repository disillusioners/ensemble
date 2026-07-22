"""Tests for the GET /api/instances/{instance_id}/question endpoint.

Covers the question tool's fallback query endpoint — the Phase 2 GET route
in ``daemon/routers/instances.py::get_pending_question`` that lets the
frontend reconcile a pending question pack after it missed the
``question_pack`` SSE event (mid-stream reconnect, dropped events during a
long-lived connection, fresh tab before SSE connect lands, etc.).

The tests use ``fastapi.testclient.TestClient`` against a minimal app that
only mounts the ``instances`` router (no DB, no MCP pool, no full daemon
manager) — mirroring the pattern established in
``tests/test_injection_api.py`` for the analogous injection endpoint.

The manager surface is mocked so the router is exercised in isolation. The
real :class:`QuestionManager` is used in two tests so the serialization
contract (``pack_to_dict``) is asserted end-to-end; the other two tests
fake the question manager entirely with ``MagicMock`` to keep the surface
narrow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    instance_id: str = "inst-abc",
    question_manager: object | None = None,
    instance_exists: bool = True,
) -> MagicMock:
    """Build a mock InstanceManager covering what ``get_pending_question`` reads.

    The endpoint reads three attributes off the manager:

    * ``get_instance(instance_id)`` (async) — 404 gate.
    * ``_question_manager.get_question_pack(instance_id)`` — the pack.

    Plus the Phase 3 ``is_write_paused`` property.

    Args:
        instance_id: The instance ID used in the success payloads.
        question_manager: Object to attach as ``manager._question_manager``.
            ``None`` means "attach a real ``QuestionManager``" so the two
            happy-path tests can drive real ``pack_to_dict`` behavior.
        instance_exists: When ``True`` the manager returns a placeholder
            from ``get_instance``; when ``False`` it raises ``KeyError``
            to trigger the 404 path.
    """
    manager = MagicMock()
    manager.is_write_paused = False

    if instance_exists:
        async def _get_instance(iid):
            return MagicMock(instance_id=iid)
        manager.get_instance = _get_instance
    else:
        async def _raise_keyerror(iid):
            raise KeyError(iid)
        manager.get_instance = _raise_keyerror

    if question_manager is None:
        # Default to the real QuestionManager — happy-path tests populate
        # it via ``set_question_pack``; null-path tests leave it empty.
        from daemon.services.question_manager import QuestionManager
        manager._question_manager = QuestionManager()
    else:
        manager._question_manager = question_manager

    return manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_state():
    """Yield (TestClient, state_dict) so tests can inject a manager.

    Mounts only the ``instances`` router on a bare FastAPI app, then uses
    middleware to inject ``app.state.manager`` per request — same
    pattern as ``tests/test_injection_api.py``.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        return await call_next(request)

    test_client = TestClient(app)
    yield test_client, state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_question_returns_200_with_pending_pack(client_and_state):
    """Pending pack exists → 200 with the serialized pack payload.

    Drives the real :class:`QuestionManager` so the contract asserted is
    ``pack_to_dict(pack)`` — not a hand-rolled mock. Verifies the
    question id / text round-trip and that ``status="pending"`` is what
    triggers the serialization branch.
    """
    client, state = client_and_state
    manager = _make_manager()
    state["manager"] = manager

    # Seed a pending pack via the real QuestionManager.
    pack = manager._question_manager.set_question_pack(
        "inst-abc",
        [
            {"id": "q-1", "text": "Pick a color"},
            {
                "id": "q-2",
                "text": "Pick a number",
                "options": ["1", "2", "3"],
                "allow_custom": False,
                "required": False,
            },
        ],
    )
    assert pack is not None
    assert pack.status == "pending"

    resp = client.get("/instances/inst-abc/question")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instance_id"] == "inst-abc"
    assert body["question_pack"] is not None

    qp = body["question_pack"]
    assert qp["status"] == "pending"
    assert qp["instance_id"] == "inst-abc"
    assert "created_at" in qp

    questions = qp["questions"]
    assert len(questions) == 2
    assert questions[0]["id"] == "q-1"
    assert questions[0]["text"] == "Pick a color"
    assert questions[0]["options"] == []
    assert questions[0]["allow_custom"] is True
    assert questions[0]["required"] is True
    assert questions[0]["answer"] is None

    assert questions[1]["id"] == "q-2"
    assert questions[1]["text"] == "Pick a number"
    assert questions[1]["options"] == ["1", "2", "3"]
    assert questions[1]["allow_custom"] is False
    assert questions[1]["required"] is False

    assert qp["answers"] == {}


def test_get_question_returns_200_with_null_when_no_pack(client_and_state):
    """No pack for the instance → 200 with ``question_pack=null``.

    The endpoint is a fallback query, not a lookup — the absence of a
    pending pack is a valid steady state (IDLE / cleared instances) and
    must return ``null`` rather than 404. Only an UNKNOWN instance
    triggers 404 (see next test).
    """
    client, state = client_and_state
    state["manager"] = _make_manager()  # fresh QuestionManager, empty packs

    resp = client.get("/instances/inst-abc/question")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"instance_id": "inst-abc", "question_pack": None}


def test_get_question_returns_404_for_unknown_instance(client_and_state):
    """Unknown instance → 404 via ``_check_instance_exists``.

    The 404 check fires BEFORE the question manager lookup; the manager
    must NOT be touched on this path.
    """
    client, state = client_and_state
    state["manager"] = _make_manager(instance_exists=False)

    resp = client.get("/instances/inst-missing/question")

    assert resp.status_code == 404, resp.text
    body = resp.json()
    # ``_check_instance_exists`` raises HTTPException(detail=ErrorResponse(...).model_dump())
    assert "detail" in body
    detail = body["detail"]
    assert detail.get("code") == "INSTANCE_NOT_FOUND"
    assert "inst-missing" in detail.get("message", "")


def test_get_question_returns_200_with_null_when_pack_answered(client_and_state):
    """Pack with ``status="answered"`` → 200 with ``question_pack=null``.

    Mirrors the production invariant from the endpoint's docstring:
    once the pack has transitioned past ``pending`` the frontend should
    hide the question UI, and ``null`` is the wire signal for that.
    Verifies it by driving the real QuestionManager through
    ``set_question_pack`` → ``set_answers``.
    """
    client, state = client_and_state
    manager = _make_manager()
    state["manager"] = manager

    # Create a pending pack, then transition it to "answered".
    created = manager._question_manager.set_question_pack(
        "inst-abc",
        [{"id": "q-1", "text": "Pick a color"}],
    )
    assert created is not None
    answered = manager._question_manager.set_answers(
        "inst-abc", {"q-1": "blue"},
    )
    assert answered is not None
    assert answered.status == "answered"

    resp = client.get("/instances/inst-abc/question")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"instance_id": "inst-abc", "question_pack": None}
