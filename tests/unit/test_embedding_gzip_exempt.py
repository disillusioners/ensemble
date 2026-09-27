"""Regression tests: the embedding path is EXEMPT from outbound
request-body gzip compression.

Bugfix 2026-09-26 (branch ``fix/embedding-gzip-exemption``). Root
cause (proven in production — 31 embedding failures): with
``OPENAI_REQUEST_GZIP=true``, ``SkillEmbeddingService.embed_text``
resolved the gzip-enabled httpx client via
``daemon.services.llm_gzip.resolve_gzip_client`` and passed it as
``http_client=`` into ``_do_embed_call`` → ``openai.OpenAI(...)``.
Embedding calls target ``EMBEDDING_BASE_URL`` — the DIRECT
``https://api.openai.com/v1`` endpoint — whose ``/embeddings`` route
REJECTS gzip-compressed request bodies with HTTP 400 ("We could not
parse the JSON body of your request"). Chat calls survived because
they hit the llm proxy, which tolerates gzip.

The fix removes the gzip client resolution at the single shared
construction site (:meth:`SkillEmbeddingService.embed_text`), so
every consumer — skill-search re-rank, trigger minting,
critical-notes embedding, blueprint embedding — is covered in one
place:

* the embeddings wire body is NEVER gzip-compressed;
* the embeddings request NEVER carries ``Content-Encoding: gzip``;
* chat-path gzip behavior is byte-identical to pre-fix (pinned by
  the anti-cheat tests below — the chat path of the SAME service
  still resolves AND attaches the gzip transport under the flag).

Coverage layers here (mirroring ``tests/unit/test_llm_request_gzip.py``):

* WIRE-LEVEL — a real in-process socket server captures the exact
  bytes the SDK serialized onto the TCP socket; assertions are on
  server-RECEIVED bytes, not in-memory request objects.
* SEAM-LEVEL — ``openai.OpenAI`` constructor-kwargs inspection at
  the production construction site, plus a recording patch on
  ``resolve_gzip_client`` that intercepts even a reintroduced
  in-function ``from .llm_gzip import resolve_gzip_client``.

No external network traffic — all HTTP lands on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daemon.services.skill_embedding_service import SkillEmbeddingService

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_PROMPT = "embed this user message for gzip-exemption wire proof"


# ═══════════════════════════════════════════════════════════════════
# In-process socket server (harness shape mirrors
# ``tests/unit/test_llm_request_gzip.py::_LocalServer`` — dual-path
# replies so both the embeddings route and the chat-completions route
# can be driven through the SAME server).
# ═══════════════════════════════════════════════════════════════════


def _embeddings_response_json(model: str) -> dict:
    """Minimal valid ``/embeddings`` response body (SDK-parseable)."""
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "model": model,
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }


def _chat_completion_response_json(content: str) -> dict:
    """Minimal valid non-streaming chat-completion response body."""
    return {
        "id": "chatcmpl-gzip-exempt-test",
        "object": "chat.completion",
        "created": 1735689600,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


class _DualPathServer:
    """In-process HTTP server capturing raw request bytes per path.

    Replies ``/embeddings`` with a valid embedding JSON and
    ``/chat/completions`` with a valid chat-completion JSON, so the
    real OpenAI SDK parses both. Captures (body bytes, headers) —
    the bytes that crossed the TCP socket.
    """

    def __init__(self) -> None:
        self.captured: list[dict] = []
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._server = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"embed-gzip-exempt-server-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def _make_handler(self):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server convention
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                outer.captured.append(
                    {
                        "path": self.path,
                        "body": body,
                        "content_encoding_header": self.headers.get(
                            "Content-Encoding"
                        ),
                        "content_length_header": self.headers.get(
                            "Content-Length"
                        ),
                    }
                )
                if self.path.endswith("/chat/completions"):
                    resp = json.dumps(
                        _chat_completion_response_json('["q1","q2","q3"]')
                    ).encode("utf-8")
                else:  # /embeddings (default)
                    resp = json.dumps(
                        _embeddings_response_json(_EMBED_MODEL)
                    ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, *args, **kwargs):
                pass  # silence test output

        return _Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def captures(self) -> list[dict]:
        return self.captured

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)


# ═══════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_gzip_singletons():
    """Reset the llm_gzip client singletons around every test.

    The chat anti-cheat wire test builds the REAL gzip singleton via
    ``resolve_gzip_client(True)``; resetting before/after keeps the
    module-global cache isolated across tests (same discipline as the
    autouse fixture in ``test_llm_request_gzip.py``).
    """
    from daemon.services.llm_gzip import reset_cached_clients

    reset_cached_clients()
    try:
        yield
    finally:
        reset_cached_clients()


def _make_service(llm_config: dict[str, Any]) -> SkillEmbeddingService:
    """Build a SkillEmbeddingService pointed at an explicit base_url."""
    config = MagicMock()
    config.embedding_model = _EMBED_MODEL
    # Dedicated embedding overrides absent → llm_config fallbacks.
    config.embedding_base_url = None
    config.embedding_api_key = None
    return SkillEmbeddingService(
        config=config,
        embedding_repo=MagicMock(),
        llm_config=llm_config,
    )


def _make_service_against(*, request_gzip: Any) -> tuple[
    SkillEmbeddingService, _DualPathServer
]:
    """Build a service whose embedding+chat base_url is a fresh server."""
    server = _DualPathServer()
    service = _make_service(
        {
            "model": "test-model",
            "base_url": server.base_url,
            "api_key": "test-key",
            "request_gzip": request_gzip,
        }
    )
    return service, server


# ═══════════════════════════════════════════════════════════════════
# Wire-level: the embeddings request body is NEVER gzipped
# ═══════════════════════════════════════════════════════════════════


class TestEmbedWireExemption:
    """Server-received bytes prove the embed wire body is plain JSON."""

    def test_embed_wire_body_never_gzipped_flag_on(self):
        """request_gzip=True: ``embed_text`` ships an UNCOMPRESSED body
        with NO ``Content-Encoding`` header to the wire.

        Pre-fix this exact configuration gzipped the body and stamped
        ``Content-Encoding: gzip`` — the production 400 repro. If
        ``json.loads`` succeeds directly on the captured bytes, the
        body was not gzipped (gzip-compressed bytes are invalid JSON).
        """
        service, server = _make_service_against(request_gzip=True)
        try:
            vector = asyncio.run(service.embed_text(_EMBED_PROMPT))

            assert vector == [0.1, 0.2, 0.3, 0.4]
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["path"].endswith("/embeddings")
            assert wire["content_encoding_header"] is None, (
                "embeddings request must NEVER carry Content-Encoding: "
                "gzip — the direct OpenAI /embeddings endpoint rejects "
                f"gzip bodies with HTTP 400; observed header: "
                f"{wire['content_encoding_header']!r}"
            )
            # Direct JSON parse — would raise on gzipped bytes.
            payload = json.loads(wire["body"])
            assert payload["input"] == _EMBED_PROMPT
            assert payload["model"] == _EMBED_MODEL
        finally:
            server.close()

    def test_embed_wire_body_never_gzipped_flag_off(self):
        """request_gzip=False: same invariant (unchanged legacy path)."""
        service, server = _make_service_against(request_gzip=False)
        try:
            vector = asyncio.run(service.embed_text(_EMBED_PROMPT))
            assert vector == [0.1, 0.2, 0.3, 0.4]
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["content_encoding_header"] is None
            payload = json.loads(wire["body"])
            assert payload["input"] == _EMBED_PROMPT
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════
# Seam-level: the shared construction site never resolves/attaches
# a gzip client
# ═══════════════════════════════════════════════════════════════════


class TestEmbedConstructionSiteExemption:
    """``embed_text`` must not attach (or even resolve) a gzip client."""

    def test_embed_construction_site_passes_no_http_client_flag_on(self):
        """request_gzip=True: ``openai.OpenAI`` is constructed WITHOUT
        an ``http_client`` override — the SDK therefore builds its
        built-in default httpx client (no gzip transport possible).
        """
        service = _make_service(
            {
                "model": "test-model",
                "base_url": "http://test.local/v1",
                "api_key": "test-key",
                "request_gzip": True,
            }
        )
        item = MagicMock()
        item.embedding = [0.1, 0.2, 0.3, 0.4]
        fake_response = MagicMock()
        fake_response.data = [item]

        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(return_value=fake_response)
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.skill_embedding_service.openai.OpenAI",
            mock_openai_cls,
        ):
            vector = asyncio.run(service.embed_text(_EMBED_PROMPT))

        assert vector == [0.1, 0.2, 0.3, 0.4]
        ctor_kwargs = mock_openai_cls.call_args.kwargs
        assert "http_client" not in ctor_kwargs, (
            "embed path must construct openai.OpenAI WITHOUT an "
            f"http_client override under request_gzip=True; observed "
            f"kwargs keys: {sorted(ctor_kwargs)}"
        )
        assert ctor_kwargs["api_key"] == "test-key"

    def test_embed_never_resolves_gzip_client_flag_on(self):
        """request_gzip=True: ``resolve_gzip_client`` is never called on
        the embed path. The recorder patches the llm_gzip module
        attribute, so a reintroduced in-function
        ``from .llm_gzip import resolve_gzip_client`` is still
        intercepted (the old call site imported lazily).
        """
        service = _make_service(
            {
                "model": "test-model",
                "base_url": "http://test.local/v1",
                "api_key": "test-key",
                "request_gzip": True,
            }
        )
        calls: list[bool] = []

        def recorder(enabled: bool):
            calls.append(bool(enabled))
            return MagicMock(name="gzip-client-stub")

        item = MagicMock()
        item.embedding = [0.1, 0.2, 0.3, 0.4]
        fake_response = MagicMock()
        fake_response.data = [item]
        mock_openai_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.embeddings.create = MagicMock(return_value=fake_response)
        mock_openai_cls.return_value = mock_client

        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder
        ), patch(
            "daemon.services.skill_embedding_service.openai.OpenAI",
            mock_openai_cls,
        ):
            asyncio.run(service.embed_text(_EMBED_PROMPT))

        assert calls == [], (
            "embed_text must NOT resolve a gzip client — the embedding "
            "path is exempt from request-body gzip regardless of "
            f"request_gzip; observed resolve_gzip_client calls={calls}"
        )


# ═══════════════════════════════════════════════════════════════════
# ANTI-CHEAT: the fix must NOT neuter gzip globally — the CHAT path
# of the SAME service still resolves AND attaches the gzip transport
# under the same flag.
# ═══════════════════════════════════════════════════════════════════


class TestChatPathGzipStillActive:
    """Chat gzip attachment survives the embed-path exemption."""

    def test_chat_wire_body_still_gzipped_flag_on(self):
        """Wire proof: ``generate_trigger_queries`` (chat path) under
        request_gzip=True ships a GZIPPED body stamped
        ``Content-Encoding: gzip`` — server-received bytes.
        """
        service, server = _make_service_against(request_gzip=True)
        try:
            queries = asyncio.run(service.generate_trigger_queries(MagicMock()))

            assert queries == ["q1", "q2", "q3"]
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["path"].endswith("/chat/completions")
            assert wire["content_encoding_header"] == "gzip", (
                "chat path must STILL gzip request bodies under "
                "request_gzip=True (the embed exemption must not neuter "
                "gzip globally); observed header: "
                f"{wire['content_encoding_header']!r}"
            )
            decoded = gzip.decompress(wire["body"])
            payload = json.loads(decoded)
            assert payload["messages"]  # gunzip → valid chat request body
        finally:
            server.close()

    def test_chat_path_still_resolves_gzip_client_flag_on(self):
        """Seam proof: ``generate_trigger_queries`` still invokes
        ``resolve_gzip_client(True)`` (same flag value the embed path
        now ignores).
        """
        service = _make_service(
            {
                "model": "test-model",
                "base_url": "http://test.local/v1",
                "api_key": "test-key",
                "request_gzip": True,
            }
        )
        calls: list[bool] = []

        def recorder(enabled: bool):
            calls.append(bool(enabled))
            return MagicMock(name="gzip-client-stub")

        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder
        ), patch(
            "daemon.services.skill_embedding_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_embedding_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [
                MagicMock(message=MagicMock(content='["q1","q2","q3"]'))
            ]
            mock_failover.return_value = fake
            asyncio.run(service.generate_trigger_queries(MagicMock()))

        assert len(calls) >= 1, (
            "chat path (generate_trigger_queries) must still resolve "
            "the gzip client under request_gzip=True"
        )
        assert all(c is True for c in calls), (
            f"chat path must invoke resolve_gzip_client(True); "
            f"observed calls={calls}"
        )
