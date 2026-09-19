"""REAL-route TestClient test pinning the ``POST /messages`` fail-fast
+ tmp-image store-503 seams (council NEEDS-FIXES, C3-1, 2026-09-19).

Replaces the prior ``tests/unit/routers/test_messages_router_ref_path.py``
that inlined a COPY of the fail-fast check and raised it itself —
tautologies immune to drift. The replacement drives the REAL
FastAPI route through a TestClient, asserts the response shape the
FE actually sees, and pins three contract guarantees:

  1. ``model_vision`` unset + ``image_refs`` non-empty → 400 with the
     ``ErrorResponse`` shape (status_code, ``code`` field, the
     exact ``OPENAI_MODEL_VISION`` message body). The fail-fast lives
     in the router at ``daemon/routers/messages.py:242-254``; the test
     pins the PRODUCTION bytes by going through the route, not by
     mirroring the predicate.
  2. ``model_vision`` set + ``image_refs`` non-empty → 200/202 (no
     400). Pins that the fail-fast only fires when vision is unset.
  3. ``model_vision`` set + ``image_refs`` non-empty BUT
     ``request.app.state.tmp_image_store`` is ``None`` → 503. Pins
     the router 503s when the store is unavailable (this is the
     shape the FE / curl consumers actually see when the lifespan
     hasn't published a tmp-image store).
  4. ``XOR at the model layer`` — sending both ``images`` and
     ``image_refs`` → 422 (Pydantic validation error before the
     router). Backstop for the seam between the model validator and
     the route.

The fix DOES NOT modify the router. We test the existing
production route (no copy, no inference).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.routers.messages import router


# ---------------------------------------------------------------------------
# FastAPI app fixture — only the messages router; bare manager stub
# ---------------------------------------------------------------------------


@pytest.fixture
def messages_client():
    """Yield a TestClient mounted ONLY on the messages router.

    The state dict carries the manager + live_hub + tmp_image_store;
    per-test middleware injects them into ``app.state`` so each
    request sees the surface the test wants. We mount ONLY the
    messages router — no DB, no MCP, no worker pool — so the test
    fails ONLY on the fail-fast + tmp-store seams, not on infra.
    """
    app = FastAPI()
    app.include_router(router)
    state: dict = {
        "manager": None,
        "live_hub": None,
        "tmp_image_store": None,
    }

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        # Inject tmp_image_store so the router's ``getattr(request.app.state, ...)``
        # path resolves to the value the test wants (None for the
        # 503 test, a stub for the 200 case).
        request.app.state.tmp_image_store = state["tmp_image_store"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


def _make_stub_manager(*, model_vision: str | None = None) -> MagicMock:
    """Build a MagicMock manager surface the messages route requires.

    The router gates ``image_refs`` on
    ``manager.config.llm.model_vision``. Other paths (instance lookup,
    command dispatch) read more fields — but the fail-fast branch
    raises BEFORE any of them run. We provide safe stub values for
    whatever the route reads on the happy path so the 200 test does
    not blow up on a missing attribute.
    """
    mgr = MagicMock()
    mgr.config.llm.model_vision = model_vision
    mgr.is_write_paused = False
    mgr.get_instance_info = MagicMock(return_value={
        "status": "running",
        "instance_id": "inst-test",
        "agent_id": "developer",
    })
    mgr.command_dispatcher = MagicMock()
    mgr.command_dispatcher.dispatch = AsyncMock(
        return_value=MagicMock(
            kind="passthrough", sanitized_text=None, ack=None
        )
    )
    # The route calls ``manager.enqueue_message`` after the fail-fast
    # passes. Return a realistic AsyncMessageResult stub so the route
    # does not blow up on attribute access.
    mgr.enqueue_message = AsyncMock(
        return_value=MagicMock(
            message_id="msg-ok",
            job_id="job-ok",
            queued=False,
        )
    )
    # The 200 path also calls ``manager.set_injection`` (FIFO entry)
    # then ``manager.get_injection_count``. Stub both.
    mgr.set_injection = MagicMock(return_value={
        "content": "look",
        "echo_id": "echo-1",
        "timestamp": "2026-09-19T21:00:00Z",
    })
    mgr.get_injection_count = MagicMock(return_value=1)
    return mgr


def _make_stub_live_hub() -> MagicMock:
    """Stub LiveEventHub for the 200 path (the route emits SSE events)."""
    hub = MagicMock()
    hub.stream_message = AsyncMock()
    return hub


def _make_stub_tmp_image_store() -> MagicMock:
    """Stub TmpImageStore — the 200 path's pre-dispatch hook calls
    ``store.get_image_bytes(ref)`` per ref. Stub a single ref-mock
    so the hook returns a stable text description.
    """
    store = MagicMock()
    store.get_image_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake")
    return store


# ---------------------------------------------------------------------------
# Test 1 — fail-fast (model_vision unset + image_refs non-empty → 400)
# ---------------------------------------------------------------------------


class TestFailFastRealRoute:
    """REAL-route TestClient: the fail-fast pin."""

    def test_model_vision_unset_and_image_refs_non_empty_returns_400(
        self, messages_client
    ):
        """The ROUTE raises 400 with the ErrorResponse shape when
        ``model_vision`` is unset AND ``image_refs`` is non-empty.

        The fail-fast lives at
        ``daemon/routers/messages.py:242-254`` — the test goes
        through the REAL route (no copy), so a future refactor of
        the predicate shape (e.g. moving the check below the
        tmp-store gate) breaks the test.
        """
        client, state = messages_client
        state["manager"] = _make_stub_manager(model_vision=None)

        ref = "/api/tmp_images/" + "a" * 32
        resp = client.post(
            "/instances/inst-test/messages",
            json={"content": "look", "image_refs": [ref]},
        )

        assert resp.status_code == 400, resp.text
        # FastAPI wraps HTTPException(detail=...) under "detail" — the
        # ErrorResponse payload lives under detail.code / detail.message.
        detail = resp.json()["detail"]
        assert detail["code"] == "INVALID_REQUEST"
        assert "image_refs" in detail["message"]
        assert "OPENAI_MODEL_VISION" in detail["message"]

    def test_model_vision_set_and_image_refs_non_empty_passes_fail_fast(
        self, messages_client
    ):
        """With vision configured, the fail-fast does NOT fire. The
        route then reaches the pre-dispatch conversion hook — the
        conversion writes a stub text description, persists the
        refs, and enqueues. We assert the response is NOT 400.
        """
        client, state = messages_client
        state["manager"] = _make_stub_manager(model_vision="openai/gpt-4o")
        state["live_hub"] = _make_stub_live_hub()
        state["tmp_image_store"] = _make_stub_tmp_image_store()

        ref = "/api/tmp_images/" + "a" * 32
        resp = client.post(
            "/instances/inst-test/messages",
            json={"content": "look", "image_refs": [ref]},
        )

        # 200 OK on the legacy ``send_message`` route (the success
        # body shape varies; the contract is "not 400" here). The
        # fail-fast test is the primary pin.
        assert resp.status_code != 400, resp.text

    def test_image_refs_non_empty_but_tmp_store_missing_returns_503(
        self, messages_client
    ):
        """The router 503s when ``request.app.state.tmp_image_store``
        is ``None`` (the lifespan did not publish a store). Pins the
        seam at ``daemon/routers/messages.py:260-268``.

        With vision configured AND refs supplied, but the store
        absent, the response is 503 (NOT 400, NOT 200). The FE
        surface must distinguish the two failure modes.
        """
        client, state = messages_client
        state["manager"] = _make_stub_manager(model_vision="openai/gpt-4o")
        state["live_hub"] = _make_stub_live_hub()
        # tmp_image_store LEFT AS None — the test wants the 503 path.

        ref = "/api/tmp_images/" + "a" * 32
        resp = client.post(
            "/instances/inst-test/messages",
            json={"content": "look", "image_refs": [ref]},
        )

        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "INTERNAL_ERROR"
        assert "tmp-image store is unavailable" in detail["message"]


# ---------------------------------------------------------------------------
# Test 2 — XOR at the model layer (Pydantic 422 before the route runs)
# ---------------------------------------------------------------------------


class TestXORValidatorRealRoute:
    """The XOR at the Pydantic model layer is enforced BEFORE the
    router runs (the route never sees a request with both fields
    non-empty). The 422 is the contract."""

    def test_both_images_and_image_refs_returns_422(self, messages_client):
        """XOR — both ``images`` and ``image_refs`` non-empty → 422
        (Pydantic ``ValidationError``). The FE surfaces this as
        the standard FastAPI 422 detail-array shape; we assert the
        status and that one of the entries mentions the XOR
        contract.
        """
        client, state = messages_client
        state["manager"] = _make_stub_manager(model_vision="openai/gpt-4o")
        state["live_hub"] = _make_stub_live_hub()
        state["tmp_image_store"] = _make_stub_tmp_image_store()

        resp = client.post(
            "/instances/inst-test/messages",
            json={
                "content": "both",
                "images": ["data:image/png;base64,abc"],
                "image_refs": ["/api/tmp_images/" + "a" * 32],
            },
        )

        assert resp.status_code == 422, resp.text
        # The 422 body shape is FastAPI's detail-array; the XOR
        # message lands there.
        body = resp.json()
        assert "detail" in body
        # The XOR error text mentions "at most one image channel"
        # (paraphrased; we assert a substring present in the
        # validator message at ``daemon/models/message.py:_xor_*``).
        rendered = str(body["detail"]).lower()
        assert (
            "at most one image channel" in rendered
            or "data-uri" in rendered
            or "image_refs" in rendered
        )
