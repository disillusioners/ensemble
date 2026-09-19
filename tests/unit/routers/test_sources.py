"""Unit tests for the registration-time chat-source_id validator.

``POST /sources`` (``daemon/routers/sources.py::create_source``) accepts a
free-form ``source_id`` (pattern ``^[a-zA-Z0-9_-]+$``). The adapter mint
site (``daemon/sources/registry.py:857``) formats
``f"{source_id}:{external_user_id}"`` — the registered ``source_id``
BECOMES the minted row's source prefix.

For interactive-chat adapter types (telegram / slack / discord) a
non-type-matching ``source_id`` therefore silently mints rows that MISS
the chat lane (``CHAT_SOURCE_PREFIXES``) and ride the default worker
lane with zero runtime signal — the operator vector the HTTP /jobs
gate cannot reach (chat-source-worker-lane, D10.1 architect amendment
A7.2).

Coverage here:
    1. ``source_type="telegram"`` + ``source_id="tg-prod"`` → 422 +
       ``JobValidationError`` envelope (same shape as the /api/jobs
       forged-source gates).
    2. ``source_type="discord"`` + ``source_id="telegram"`` → 422
       (cross-type misconfig mints into the WRONG lane).
    3. ``source_type="telegram"`` + ``source_id="telegram"`` → 201
       (compliant registration persists).
    4. Case-variant source_id REJECTED: ``"Telegram"`` / ``"TELEGRAM"``
       against each chat type → 422 with the same envelope (the
       validator compares EXACTLY; mint site and lane predicate
       remain case-sensitive by design, unchanged).
    5. Non-chat types unaffected (``webhook`` + custom id → 201 — the
       validator is a deliberate narrow scope, NOT a general
       source_id naming policy).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _stub_source_config(source_id: str, source_type: str) -> SimpleNamespace:
    """Minimal source-config stand-in shaped for ``_source_to_info``."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=f"{source_id} adapter",
        config={},
        enabled=True,
        autostart=True,
        status="stopped",
        error_message=None,
        created_at=now_iso,
        updated_at=now_iso,
        credentials=None,
    )


@pytest.fixture
def sources_test_app():
    """FastAPI app wired with the sources router + stubbed manager.

    The validator fires BEFORE the persistence call, so the stubs only
    need to satisfy the router's happy-path collaborators (the 422
    cases never reach them — asserted via ``create_source_config``
    NOT called).
    """
    app = FastAPI()
    from daemon.routers.sources import router as sources_router

    app.include_router(sources_router)

    manager = SimpleNamespace(
        is_write_paused=False,
        _source_repository=SimpleNamespace(
            get_source_config=lambda source_id: None,  # no existing source
            create_source_config=lambda **kwargs: _stub_source_config(
                kwargs["source_id"], kwargs["source_type"]
            ),
        ),
        source_registry=SimpleNamespace(start_adapter=None),  # replaced below
    )

    async def _start_adapter(_source_id: str) -> None:  # auto-start stub
        return None

    manager.source_registry.start_adapter = _start_adapter
    app.state.manager = manager
    app.state.credential_manager = SimpleNamespace(encrypt=lambda c: "{}")
    yield app


@pytest.fixture
def sources_client(sources_test_app):
    with TestClient(sources_test_app) as client:
        yield client


class TestChatSourceRegistrationValidator:
    """The create_source chat-source_id validator (D10.1 A7.2)."""

    def test_create_source_rejects_mismatched_telegram_id(
        self, sources_client
    ):
        """``source_type='telegram'`` + ``source_id='tg-prod'`` → 422.

        ``tg-prod`` would mint ``tg-prod:<user>`` rows — NOT
        chat-prefixed — silently riding the default lane with zero
        runtime signal. The envelope is the SAME ``JobValidationError``
        shape the /api/jobs forged-source gates use."""
        resp = sources_client.post(
            "/sources",
            json={
                "source_id": "tg-prod",
                "source_type": "telegram",
                "name": "Telegram Production",
            },
        )

        assert resp.status_code == 422, resp.text
        body = resp.json()
        # Exact envelope shape — identical to the /api/jobs gates.
        assert body["detail"]["error"] == "Validation Error"
        assert isinstance(body["detail"]["details"], list)
        assert any(
            d.get("field") == "source_id" for d in body["detail"]["details"]
        )

    def test_create_source_rejects_cross_type_discord_id(
        self, sources_client
    ):
        """``source_type='discord'`` + ``source_id='telegram'`` → 422.

        Cross-type misconfig: the discord adapter would mint
        ``telegram:<user>`` rows — landing in the WRONG lane (the
        telegram chat lane) with no ``discord:`` row ever existing."""
        resp = sources_client.post(
            "/sources",
            json={
                "source_id": "telegram",
                "source_type": "discord",
                "name": "Discord (misconfigured id)",
            },
        )

        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["error"] == "Validation Error"
        assert any(
            d.get("field") == "source_id" for d in body["detail"]["details"]
        )

    def test_create_source_rejects_before_persistence(
        self, sources_test_app
    ):
        """Validation precedes persistence — a rejected body NEVER
        reaches ``create_source_config`` (no partial registration)."""
        called = {"create": 0}

        def _create(**kwargs):
            called["create"] += 1
            return _stub_source_config(kwargs["source_id"], kwargs["source_type"])

        sources_test_app.state.manager._source_repository.create_source_config = _create

        with TestClient(sources_test_app) as client:
            resp = client.post(
                "/sources",
                json={
                    "source_id": "tg-prod",
                    "source_type": "telegram",
                    "name": "Telegram Production",
                },
            )

        assert resp.status_code == 422, resp.text
        assert called["create"] == 0

    def test_create_source_accepts_matching_telegram_id(
        self, sources_client
    ):
        """``source_type='telegram'`` + ``source_id='telegram'`` → 201.

        Compliant registration: the minted prefix ``telegram:<user>``
        lands in ``CHAT_SOURCE_PREFIXES`` so the lane predicate routes
        the adapter's rows to the chat worker pool."""
        resp = sources_client.post(
            "/sources",
            json={
                "source_id": "telegram",
                "source_type": "telegram",
                "name": "Telegram",
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source_id"] == "telegram"
        assert body["source_type"] == "telegram"

    def test_create_source_rejects_case_variant_source_id(self, sources_client):
        """``source_id`` MUST exactly equal the canonical lowercase
        type id (``"Telegram"``, ``"TELEGRAM"``, or other case
        variants rejected). The validator compares
        ``source_id != source_type.value`` directly — case-SENSITIVE.

        Pin rationale (chat-source-worker-lane, D10.1 A7.2
        follow-up): the case-blind comparison let mixed-case
        operator input pass validation, then the mint site
        (``daemon/sources/registry.py:857``) wrote the RAW id
        verbatim — producing ``Telegram:alice`` rows whose prefix is
        NEVER matched by the case-sensitive lane predicate
        (``LIKE 'telegram:%'`` over ``CHAT_SOURCE_PREFIXES``). The
        row then rode the DEFAULT worker lane with ZERO runtime
        signal — the exact silent-default-lane misconfig the
        validator exists to catch. Tightening the validator to
        case-SENSITIVE forces operators to lowercase at registration
        so the canonical prefix reaches the mint and the lane
        predicate matches.
        """
        case_variants_per_type = [
            ("telegram", "Telegram"),
            ("telegram", "TELEGRAM"),
            ("slack", "Slack"),
            ("slack", "SLACK"),
            ("discord", "Discord"),
            ("discord", "DISCORD"),
        ]
        for source_type, source_id in case_variants_per_type:
            resp = sources_client.post(
                "/sources",
                json={
                    "source_id": source_id,
                    "source_type": source_type,
                    "name": f"{source_id} (case variant)",
                },
            )
            assert resp.status_code == 422, (
                f"case variant ({source_type!r}, {source_id!r}) "
                f"returned {resp.status_code} — expected 422 (exact-case "
                f"match required; mint site and lane predicate are "
                f"case-SENSITIVE by design)"
            )
            body = resp.json()
            assert body["detail"]["error"] == "Validation Error", (
                f"case variant ({source_type!r}, {source_id!r}) envelope "
                f"missing: {body}"
            )
            assert any(
                d.get("field") == "source_id" for d in body["detail"]["details"]
            ), f"case variant ({source_type!r}, {source_id!r}) details missing field=source_id: {body}"

    def test_create_source_non_chat_type_unaffected(self, sources_client):
        """Non-chat types keep the free-form ``source_id`` contract —
        the validator is a NARROW scope decision (A7.2), not a general
        naming policy: ``webhook`` + ``gh-hook`` → 201."""
        resp = sources_client.post(
            "/sources",
            json={
                "source_id": "gh-hook",
                "source_type": "webhook",
                "name": "GitHub Webhook",
            },
        )

        assert resp.status_code == 201, resp.text
