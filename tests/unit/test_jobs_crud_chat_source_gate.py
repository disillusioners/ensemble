"""HTTP-level forged-source gate test for the chat-prefix extension.

``POST /api/jobs`` rejects user-supplied chat-channel source prefixes
(``telegram:`` / ``slack:`` / ``discord:`` — ``CHAT_SOURCE_PREFIXES``)
with 422 + the ``JobValidationError`` envelope, via a check PARALLEL to
the existing reserved-source gate (chat-source-worker-lane, D10.1).

Coverage here:
    * Each chat prefix → 422 + envelope, service NEVER called
      (the new ``is_chat_source`` gate).
    * ``agent:foo`` → 422 from the EXISTING ``is_reserved_source``
      gate — proves the gates COMPOSE without regression (Pin 3's
      HTTP-level companion: neither gate disturbs the other).
    * A non-chat user source still passes the boundary (webhook →
      reaches the service — sentinel 500) — the gate is a narrow
      addition, not a general lockdown.

The stubbing pattern mirrors ``tests/unit/routers/test_source_reservation.py``
(sentinel-raising ``enqueue`` stub; the router's ``except Exception``
surfaces it as 500, proving the boundary passed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_SENTINEL_EXCEPTION = RuntimeError(
    "SENTINEL: chat-source gate boundary passed — test expects to "
    "catch this before downstream processing."
)


def _stub_enqueue_service():
    svc = MagicMock()
    svc.enqueue = AsyncMock(side_effect=_SENTINEL_EXCEPTION)
    return svc


@pytest.fixture
def chat_gate_test_app():
    app = FastAPI()
    from daemon.routers.jobs_crud import router as crud_router

    app.include_router(crud_router)
    app.state.manager = MagicMock(is_write_paused=False)
    yield app

    from daemon.routers.jobs_crud import get_job_queue_service

    get_job_queue_service.set_service(None)


@pytest.fixture
def chat_gate_client(chat_gate_test_app):
    with TestClient(chat_gate_test_app) as client:
        yield client


class TestChatSourceGateRejectsChatPrefixes:
    """Chat prefixes are rejected at the HTTP boundary (D10.1)."""

    @pytest.mark.parametrize(
        "source_value",
        ["telegram:fake", "slack:fake", "discord:fake"],
    )
    def test_chat_prefix_rejected_with_422_envelope(
        self, chat_gate_client, source_value
    ):
        """Each chat prefix → 422 + JobValidationError envelope; the
        service is NEVER called (validation precedes persistence)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = chat_gate_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": source_value,
            },
        )

        assert resp.status_code == 422, (source_value, resp.text)
        body = resp.json()
        assert body["detail"]["error"] == "Validation Error"
        assert any(
            d.get("field") == "source" for d in body["detail"]["details"]
        )
        stub.enqueue.assert_not_called()


class TestChatGateComposesWithReservedGate:
    """The chat gate is PARALLEL to the reserved gate — both fire."""

    def test_reserved_prefix_still_rejected(self, chat_gate_client):
        """``agent:foo`` → 422 from the EXISTING ``is_reserved_source``
        gate — the chat extension does not weaken the original gate
        (gates compose)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = chat_gate_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "agent:foo",
            },
        )

        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["error"] == "Validation Error"
        stub.enqueue.assert_not_called()

    def test_non_chat_user_source_still_reaches_service(
        self, chat_gate_client
    ):
        """A user source in NEITHER family passes the boundary and
        reaches the service (sentinel → 500) — the widening is a
        narrow chat-subset addition (Implementer Note (c))."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = chat_gate_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "webhook:gh-hook",
            },
        )

        assert resp.status_code == 500, resp.text  # sentinel -> 500
        stub.enqueue.assert_called_once()
        assert stub.enqueue.call_args.kwargs["source"] == "webhook:gh-hook"
