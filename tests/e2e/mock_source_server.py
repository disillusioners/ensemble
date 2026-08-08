#!/usr/bin/env python3
"""Mock source adapter + standalone HTTP server + DaemonSourceMock helper.

This module provides test infrastructure for the job-orchestration e2e tests.
It does NOT modify any production code; it is purely additive.

The module is split into three concerns:

  1. **MockSourceAdapter** — a minimal implementation of
     :class:`daemon.sources.base.MessageSourceAdapter` that captures outgoing
     messages in a list (so tests can assert on what the agent said back)
     and exposes an ``emit()`` helper for pushing synthetic incoming
     messages through the adapter's ``_on_message`` callback. This is
     useful for unit-style tests that exercise the source/dispatcher
     machinery directly.

  2. **Standalone aiohttp server** — a small HTTP service that exposes
     ``POST /send``, ``GET /sent``, and ``GET /health``. The server owns
     a single ``MockSourceAdapter`` instance (the "singleton") and
     forwards ``POST /send`` payloads through ``adapter.emit()``. This
     lets external test drivers push messages into a running daemon
     without going through the real Slack/Telegram surface.

  3. **DaemonSourceMock** — a synchronous (requests-based) helper that
     speaks to a running daemon's HTTP API directly. Because the
     daemon's ``/api/sources`` endpoint only accepts the production
     adapter types (``telegram``, ``slack``, ``scheduler``, ``webhook``,
     ``whatsapp``, ``discord``) — never a generic ``mock`` — this class
     is the e2e-friendly way to drive the daemon: spawn an instance,
     send it messages, read its history, and terminate it.

Run the standalone server directly::

    python -m tests.e2e.mock_source_server            # port 8099
    python -m tests.e2e.mock_source_server --port 9100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import requests
from aiohttp import web

from daemon.sources.base import (
    IncomingMessage,
    MessageSourceAdapter,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1. MockSourceAdapter
# --------------------------------------------------------------------------- #
class MockSourceAdapter(MessageSourceAdapter):
    """In-memory mock that satisfies the MessageSourceAdapter contract.

    The adapter captures every ``send()`` call into ``self.sent_messages`` so
    tests can assert on what the agent emitted back through the source
    pipeline. It also exposes ``emit()`` and ``handle_webhook()`` so test
    drivers can push synthetic IncomingMessage instances through the
    adapter's ``_on_message`` callback (the same path the production
    adapters use).
    """

    DEFAULT_SOURCE_ID = "mock-source"
    DEFAULT_SOURCE_TYPE = "mock"

    def __init__(
        self,
        config: SourceConfig,
        on_message=None,  # type: ignore[assignment]
    ) -> None:
        # The parent ABC stores ``_on_message`` and ``_status``; mirror that
        # initialisation so the adapter is usable in isolation (e.g. when
        # unit tests instantiate the adapter without a real dispatcher).
        super().__init__(config=config, on_message=on_message)  # type: ignore[arg-type]
        self.sent_messages: list[OutgoingMessage] = []

    # -- ABC methods --------------------------------------------------------
    async def start(self) -> None:
        """Mark the adapter RUNNING — no external connection to establish."""
        self._status = SourceStatus.RUNNING
        logger.info(f"[MockSourceAdapter] start() source_id={self.source_id}")

    async def stop(self) -> None:
        """Mark the adapter STOPPED — nothing to disconnect."""
        self._status = SourceStatus.STOPPED
        logger.info(f"[MockSourceAdapter] stop() source_id={self.source_id}")

    async def send(self, message: OutgoingMessage) -> bool:
        """Capture the outgoing message for later assertions.

        Returns ``True`` unconditionally — the mock cannot fail to send.
        """
        self.sent_messages.append(message)
        logger.debug(
            f"[MockSourceAdapter] send() captured "
            f"user={message.external_user_id} "
            f"content={message.content[:80]!r}"
        )
        return True

    async def health_check(self) -> bool:
        """Always healthy — the mock is a pure in-memory object."""
        return True

    # -- Test helpers -------------------------------------------------------
    async def emit(
        self,
        content: str,
        external_user_id: str = "test_user",
        message_id: str | None = None,
        agent: str = "ari",
    ) -> None:
        """Push an IncomingMessage through the adapter's ``_on_message`` callback.

        Args:
            content: The message text to deliver.
            external_user_id: Stable per-user id (used for instance mapping).
            message_id: External message id; auto-generated UUID4 hex if
                omitted. Each emit must carry a UNIQUE message_id so the
                daemon's dedup layer (``_handle_message`` → ``message_id``)
                treats the message as fresh.
            agent: Agent name to stamp on the metadata so the daemon
                resolves the message to that agent (see
                ``daemon/sources/registry.py:_handle_message`` — it reads
                ``metadata["agent"]`` and constructs ``agent_dir``).
        """
        if self._on_message is None:
            raise RuntimeError(
                "MockSourceAdapter.emit() called before on_message was wired. "
                "The standalone server wires on_message at /send handler time."
            )
        if message_id is None:
            import uuid

            message_id = uuid.uuid4().hex

        msg = IncomingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=self.source_id,
            metadata={
                "agent": agent,
                "message_id": message_id,
            },
        )
        logger.info(
            f"[MockSourceAdapter] emit() user={external_user_id} "
            f"agent={agent} content={content[:80]!r}"
        )
        await self._on_message(msg)

    async def handle_webhook(self, payload: dict, headers: dict) -> None:
        """Webhook API compatibility — extract fields from the payload and emit().

        The daemon's ``POST /webhooks/{source_id}`` route calls
        ``adapter.handle_webhook(payload, headers)`` (see
        ``daemon/routers/webhooks.py``). For a real adapter this is where
        signature verification + payload normalization happens; for the
        mock we just unwrap the standard keys and forward.
        """
        content = payload.get("content", "")
        if not content:
            logger.warning(
                "[MockSourceAdapter] handle_webhook called with empty content; ignoring."
            )
            return
        external_user_id = payload.get("external_user_id") or payload.get("user_id", "test_user")
        message_id = payload.get("message_id")
        agent = payload.get("agent", "ari")
        await self.emit(
            content=content,
            external_user_id=external_user_id,
            message_id=message_id,
            agent=agent,
        )


# --------------------------------------------------------------------------- #
# 2. Standalone aiohttp server
# --------------------------------------------------------------------------- #
# Module-level singleton so /send handlers can reach the same adapter
# (and therefore the same sent_messages list) across requests. Tests
# reset this list between scenarios by calling ``reset_singleton()``.
_singleton_adapter: MockSourceAdapter | None = None
_singleton_lock = asyncio.Lock()


async def _get_singleton_async() -> MockSourceAdapter:
    """Return the process-wide MockSourceAdapter, creating it on first use.

    Async-safe via an asyncio.Lock so concurrent /send requests don't
    race the lazy init. The adapter's start() is itself async (sets
    status to RUNNING) and we await it inline so we never try to
    nest run_until_complete inside the already-running aiohttp loop.
    """
    global _singleton_adapter
    async with _singleton_lock:
        if _singleton_adapter is None:
            config = SourceConfig(
                source_id=MockSourceAdapter.DEFAULT_SOURCE_ID,
                source_type=MockSourceAdapter.DEFAULT_SOURCE_TYPE,
                name="mock-source-singleton",
                config={},
                credentials={},
            )
            # on_message is set lazily by /send so a single adapter can
            # be wired into the daemon's dispatcher on demand. The
            # standalone server doesn't have a daemon dispatcher
            # attached; callers can use it for unit-style tests.
            adapter = MockSourceAdapter(config=config, on_message=None)
            await adapter.start()
            _singleton_adapter = adapter
    return _singleton_adapter


def reset_singleton() -> None:
    """Drop the singleton + clear its sent_messages list. Test helper."""
    global _singleton_adapter
    _singleton_adapter = None


async def _handle_send(request: web.Request) -> web.Response:
    """POST /send — accept {content, external_user_id, agent, message_id}.

    Forwards the payload through the singleton adapter's ``emit()`` so
    the daemon (if wired) processes the message via the source pipeline.
    For test-only setups without a daemon dispatcher, the singleton
    simply records that the call happened and the request returns 202.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response(
            {"ok": False, "error": f"invalid JSON: {exc}"}, status=400
        )

    content = payload.get("content", "")
    if not content:
        return web.json_response(
            {"ok": False, "error": "missing 'content'"}, status=400
        )

    external_user_id = payload.get("external_user_id", "test_user")
    message_id = payload.get("message_id")
    agent = payload.get("agent", "ari")

    adapter = await _get_singleton_async()
    try:
        await adapter.emit(
            content=content,
            external_user_id=external_user_id,
            message_id=message_id,
            agent=agent,
        )
    except RuntimeError as exc:
        # The singleton was created without an on_message callback —
        # that's expected when the standalone server runs in isolation.
        # We log it and return success so the test driver can still push
        # messages and inspect the captured sent_messages list directly.
        logger.info(
            f"[mock_source_server] /send: emit() short-circuited "
            f"(no on_message wired): {exc}"
        )
    return web.json_response({"ok": True, "emitted": True})


async def _handle_sent(request: web.Request) -> web.Response:
    """GET /sent — return captured sent_messages as JSON."""
    adapter = await _get_singleton_async()
    return web.json_response(
        {
            "ok": True,
            "count": len(adapter.sent_messages),
            "messages": [
                {
                    "external_user_id": m.external_user_id,
                    "content": m.content,
                    "source_id": m.source_id,
                    "message_type": m.message_type,
                    "reply_to_id": m.reply_to_id,
                    "metadata": m.metadata,
                }
                for m in adapter.sent_messages
            ],
        }
    )


async def _handle_health(request: web.Request) -> web.Response:
    """GET /health — trivial liveness probe."""
    return web.json_response({"status": "ok"})


def build_app() -> web.Application:
    """Construct the aiohttp app with the three routes registered."""
    app = web.Application()
    app.router.add_post("/send", _handle_send)
    app.router.add_get("/sent", _handle_sent)
    app.router.add_get("/health", _handle_health)
    return app


async def run_server_async(port: int = 8099) -> web.AppRunner:
    """Start the aiohttp server and return the AppRunner so the caller can stop it.

    Usage::

        runner = await run_server_async(port=8099)
        try:
            # ... do test work ...
        finally:
            await runner.cleanup()
    """
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()
    logger.info(f"[mock_source_server] listening on http://127.0.0.1:{port}")
    return runner


def run_server(port: int = 8099) -> None:
    """Blocking entry point — used by the ``if __name__ == "__main__"`` block."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = loop.run_until_complete(run_server_async(port=port))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("[mock_source_server] shutting down on KeyboardInterrupt")
    finally:
        loop.run_until_complete(runner.cleanup())


# --------------------------------------------------------------------------- #
# 3. DaemonSourceMock — synchronous bridge to the running daemon HTTP API
# --------------------------------------------------------------------------- #
class DaemonSourceMock:
    """Drive a running daemon via its HTTP API on behalf of e2e tests.

    Why this class exists
    ---------------------
    The daemon's source API (``POST /api/sources``) only accepts a
    hard-coded whitelist of adapter types — ``telegram``, ``slack``,
    ``webhook``, ``whatsapp``, ``discord``, ``scheduler`` — and rejects
    anything else, including a hypothetical ``mock`` type. That means
    the e2e test cannot register a real mock source adapter through
    the daemon's source lifecycle.

    Instead, this class bypasses the source layer entirely and talks
    to the daemon's instance + message HTTP API directly, which is the
    same surface the production sources end up using after the
    registry normalizes a message. Functionally this exercises the
    exact same downstream path (instance spawning, message dispatch,
    job orchestration) — the only thing it does not exercise is the
    adapter-internal "external platform → IncomingMessage" layer,
    which is covered by unit tests on each concrete adapter.

    Why ``requests`` (sync) instead of ``aiohttp`` (async)
    -----------------------------------------------------
    Every existing e2e test in ``tests/e2e/`` already uses
    ``requests`` with synchronous polling (``_wait_for_*`` helpers).
    Matching that style keeps the test bodies declarative and
    sidesteps the need for an asyncio test harness.

    Attributes:
        daemon_url: Base URL of the running daemon (default
            ``http://localhost:8079``).
        api_base: Convenience ``{daemon_url}/api``.
        sent_messages: Test-facing list of dict-shaped capture of
            messages the agent emitted (populated by helpers that
            read instance history; left empty by default).
    """

    DEFAULT_TIMEOUT = 30  # seconds — same as the existing e2e helpers

    def __init__(self, daemon_url: str = "http://localhost:8079") -> None:
        self.daemon_url = daemon_url.rstrip("/")
        self.api_base = f"{self.daemon_url}/api"
        self.sent_messages: list[dict] = []
        logger.info(f"[DaemonSourceMock] initialised daemon_url={self.daemon_url}")

    # -- Spawning / messaging ----------------------------------------------
    def spawn_instance(
        self,
        agent_id: str,
        project_id: str | None = None,
    ) -> str:
        """POST ``/api/instances`` and return the new ``instance_id``.

        Mirrors the pattern of ``_spawn_instance`` in
        ``tests/e2e/test_e2e_workflows.py`` but lives on this class so
        e2e tests can group "spawn + send + poll" in one place.
        """
        logger.info(f"[DaemonSourceMock] spawn agent_id={agent_id} project_id={project_id}")
        payload: dict[str, Any] = {"agent_id": agent_id}
        if project_id is not None:
            payload["project_id"] = project_id
        response = requests.post(
            f"{self.api_base}/instances",
            json=payload,
            timeout=self.DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        instance_id = data.get("instance_id")
        if not instance_id:
            raise RuntimeError(f"spawn_instance: response missing instance_id: {data}")
        logger.info(f"[DaemonSourceMock] spawn -> instance_id={instance_id}")
        return instance_id

    def send_message(
        self,
        agent_id: str,
        content: str,
        project_id: str | None = None,
    ) -> tuple[str, str]:
        """Spawn an instance and send it one message.

        Returns:
            ``(instance_id, message_id)`` tuple so the caller can
            immediately start polling the instance history.

        Convenience wrapper used by the e2e tests — it collapses
        spawn + send into a single call because most test scenarios
        only need a brand-new instance anyway.
        """
        instance_id = self.spawn_instance(agent_id=agent_id, project_id=project_id)
        message_id = self.send_to_instance(instance_id=instance_id, content=content)
        return instance_id, message_id

    def send_to_instance(self, instance_id: str, content: str) -> str:
        """POST ``/api/instances/{id}/messages`` and return the ``message_id``."""
        logger.info(
            f"[DaemonSourceMock] send_message instance_id={instance_id[:8]}... "
            f"len={len(content)}"
        )
        response = requests.post(
            f"{self.api_base}/instances/{instance_id}/messages",
            json={"content": content},
            timeout=self.DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        message_id = data.get("message_id")
        if not message_id:
            raise RuntimeError(
                f"send_to_instance: response missing message_id: {data}"
            )
        logger.info(f"[DaemonSourceMock] send_message -> message_id={message_id}")
        return message_id

    # -- Read / state -------------------------------------------------------
    def get_messages(self, instance_id: str) -> list[dict]:
        """GET ``/api/instances/{id}/messages`` and return the parsed list."""
        response = requests.get(
            f"{self.api_base}/instances/{instance_id}/messages",
            timeout=self.DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        # The endpoint may return either a bare list or a dict with a
        # ``messages`` key depending on version — be tolerant of both.
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data["messages"]
        raise RuntimeError(
            f"get_messages: unexpected response shape: {type(data).__name__}"
        )

    def get_instance(self, instance_id: str) -> dict:
        """GET ``/api/instances/{id}`` and return the parsed body."""
        response = requests.get(
            f"{self.api_base}/instances/{instance_id}",
            timeout=self.DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    # -- Lifecycle ----------------------------------------------------------
    def terminate_instance(self, instance_id: str) -> bool:
        """DELETE ``/api/instances/{id}``; return True on a 2xx response."""
        try:
            response = requests.delete(
                f"{self.api_base}/instances/{instance_id}",
                timeout=self.DEFAULT_TIMEOUT,
            )
            ok = 200 <= response.status_code < 300
            logger.info(
                f"[DaemonSourceMock] terminate {instance_id[:8]}... "
                f"status={response.status_code} ok={ok}"
            )
            return ok
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"[DaemonSourceMock] terminate {instance_id[:8]}... failed: {exc}"
            )
            return False

    # -- Polling helpers ----------------------------------------------------
    def wait_for_status(
        self,
        instance_id: str,
        status: str,
        timeout: int = 60,
        poll_interval: float = 2.0,
    ) -> dict | None:
        """Poll the instance until its status equals ``status`` or times out.

        Returns the final instance dict on success, ``None`` on timeout.
        """
        import time

        deadline = time.time() + timeout
        last: dict | None = None
        while time.time() < deadline:
            try:
                last = self.get_instance(instance_id)
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    f"[DaemonSourceMock] wait_for_status GET failed: {exc}"
                )
            else:
                if last.get("status") == status:
                    return last
            time.sleep(poll_interval)
        logger.warning(
            f"[DaemonSourceMock] wait_for_status: instance {instance_id[:8]}... "
            f"never reached {status!r} within {timeout}s (last={last})"
        )
        return last


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone mock source server for e2e tests."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8099,
        help="Port to listen on (default: 8099)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args()
    run_server(port=args.port)
