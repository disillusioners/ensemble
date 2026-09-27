"""Embedding-Gzip Wire Exemption mock test (strict-server red→green repro).

Drives the REAL ``SkillEmbeddingService.embed_text`` in-process through
the REAL ``openai.OpenAI`` SDK against a strict HTTP server bound to
127.0.0.1:<port>. The server reproduces OpenAI's direct /embeddings
behavior: any request with ``Content-Encoding: gzip`` OR a body that
fails ``json.loads`` gets HTTP 400 with the canonical
"We could not parse the JSON body of your request" error envelope;
otherwise the server returns an embeddings-shaped JSON response.

The MOCK_TESTS.md spec for this scenario:

* GREEN (default, ``--expect green``): with ``OPENAI_REQUEST_GZIP=true``
  and ``EMBEDDING_BASE_URL`` → local strict server, ``embed_text`` must
  send PLAIN JSON (no ``Content-Encoding: gzip`` header, no gzip magic
  bytes in the body) and receive 200 + a vector back. Chat-path check
  via ``resolve_gzip_client(True)`` must still stamp
  ``Content-Encoding: gzip`` on the wire (gzip transport is preserved
  for chat, untouched by the fix).
* RED (scratch at base ``139ba352``, ``--expect red``): same driver
  against base code (which resolves ``gzip_embed_client`` from
  ``request_gzip`` and passes it to ``_do_embed_call``) must show the
  server observing gzip body → 400 → failure surfaces client-side.
  Exit 0 in RED iff the expected failure-shape was reproduced
  (``expected-behavior-observed`` semantics — the bug has been
  faithfully re-proven).

Service-construction facts (verified at HEAD ``ce644d4a`` and at
base ``139ba352`` — IDENTICAL constructor signature on both):

* Class: ``daemon.services.skill_embedding_service.SkillEmbeddingService``
* ``__init__(self, config, embedding_repo, llm_config)``
  - ``config``: ``SkillEvolutionConfig``-shaped. The service reads
    ``config.embedding_model`` (str), ``config.embedding_base_url``
    (str | None), ``config.embedding_api_key`` (str | None). The
    three-call-site access via ``getattr`` allows a MagicMock
    substitute (see ``_make_service_config`` below).
  - ``embedding_repo``: ``SkillEmbeddingRepository``-shaped. NOT used
    on the embed_text wire path; a MagicMock is sufficient.
  - ``llm_config``: ``dict`` with at least ``base_url``, ``api_key``,
    ``model``; ``request_gzip`` is the operator-knob read by
    ``resolve_gzip_client(bool(self.llm_config.get("request_gzip")))``
    in ``generate_trigger_queries`` ONLY — the embed path at HEAD
    ignores it (``http_client=None`` is passed unconditionally); at
    base, it routes a gzip httpx client into ``_do_embed_call``.

Endpoint-resolution facts (verified against ``_resolve_embedding_*``
helpers in skill_embedding_service.py:687-699):

* ``base_url`` precedence: ``config.embedding_base_url`` (if set) else
  ``llm_config["base_url"]`` else None.
* ``api_key`` precedence: ``config.embedding_api_key`` (if set) else
  ``llm_config["api_key"]`` else None (the openai SDK is happy with
  an empty string when ``base_url`` is set — the local strict server
  accepts any key value).
* ``model``: ``config.embedding_model``.

``resolve_gzip_client(True)`` (verified at HEAD and base in
``daemon/services/llm_gzip.py:372``) returns the module-level
``httpx.Client`` singleton (an ``httpx.Client`` instance whose
``transport`` wraps the gzip-compressing ``GzipRequestTransport``).
The driver calls ``client.post(url, json=payload)`` on it directly —
the gzip wrapping happens in the transport BEFORE the bytes go on
the wire. No wrapper around the client.

Construction discipline (preserves the wire proof):

* ``embedding_repo`` = ``MagicMock()`` (NOT a real repo; no DB
  required for the embed_text call path).
* ``config`` = ``MagicMock(spec=[...])`` with ``embedding_model``,
  ``embedding_base_url``, ``embedding_api_key`` set explicitly — the
  service accesses those via ``getattr(self.config, ...)``.
* ``llm_config`` = plain dict with ``base_url`` pointing at the
  local server, ``api_key`` arbitrary string, ``model`` arbitrary,
  ``request_gzip=True``.
* NO monkeypatching of the httpx client or openai.OpenAI SDK — the
  wire goes through the REAL ``openai.OpenAI(http_client=...)``
  construction site, so the only way to break the gzip exemption is
  for the SERVICE CODE under test to pass a non-None ``http_client``.

Script invocation:

    uv run python tests/mocks/embed_gzip_wire_mock.py --expect green --port 18771
    uv run python tests/mocks/embed_gzip_wire_mock.py --expect red   --port 18771
    uv run python tests/mocks/embed_gzip_wire_mock.py --repo-root PATH

Dual-layer timeout: ``signal.alarm(120)`` self + ``timeout 240``
pack-internal + ``timeout 300`` outer (declared by the pack wrapper
``test/packs/embed_gzip_wire_mock_test.sh``). Port hygiene: refuse to
bind if the port is already in use (NEVER kill processes on 8088 or
any other port — a refused bind is reported as a setup error).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gzip
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Path bootstrap — same idiom as the sibling mocks (pinned_cleanup, denylist).
# ---------------------------------------------------------------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))


# Probe text for the embed call. Sized to comfortably cross the
# gzip-shrink threshold (~150+ bytes — gzip overhead on tiny bodies
# can EXCEED savings; see daemon/services/llm_gzip.py:216-217, the
# transport no-ops when len(compressed) >= len(original)). Real-world
# embed payloads (skill descriptions, trigger phrases, multi-sentence
# text) are well above the threshold; the driver must mirror that to
# faithfully reproduce the original bug at base. Deterministic sentence
# repeated to a fixed count so green + red transcripts compare
# byte-for-byte. Final raw JSON body ~= 2.1 KB; final gzipped body
# ~ 750 B — well within transport's compression zone.
_PROBE_SENTENCE = (
    "Skill evolution queries test the embedding pipeline end-to-end "
    "with realistic input payloads to faithfully reproduce transport-"
    "level behaviors. "
)
PROBE_TEXT = _PROBE_SENTENCE * 15  # ~ 2100 chars raw, well above threshold


def _default_repo_root() -> str:
    """Default repo root — derived from this script's location.

    ``tests/mocks/embed_gzip_wire_mock.py`` → ``<root>/tests/mocks/``.
    """
    return os.path.abspath(os.path.join(THIS_DIR, "..", ".."))


# IMPORTANT: parse args BEFORE heavy imports so `--help` works without
# the daemon import cost (the daemon package pulls in many heavy deps).

# ---------------------------------------------------------------------------
# Self-timeout (script-level guard, inner layer)
# ---------------------------------------------------------------------------
HARD_TIMEOUT_SECONDS = 120


def _timeout_handler(_signum: int, _frame: Any) -> None:
    print("RESULT: TIMEOUT (script exceeded 120s hard cap)", flush=True)
    sys.exit(124)


signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(HARD_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Port-hygiene helpers
# ---------------------------------------------------------------------------


def _is_port_free(host: str, port: int) -> bool:
    """Return True if ``host:port`` can be bound right now.

    Tries a connect attempt to a freshly-bound probe socket; if the
    socket closes cleanly, the port was free.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


# ---------------------------------------------------------------------------
# Strict embeddings server
# ---------------------------------------------------------------------------


class _StrictEmbeddingsHandler(BaseHTTPRequestHandler):
    """Strict HTTP handler simulating OpenAI's direct /embeddings endpoint.

    Per request:

    * Log: ``REQ <n> <method> <path> Content-Encoding=<val|absent>
            body_head=<first 16 bytes hex> json_parse=<ok|fail|gzip>
            -> <status>``
    * Chat-probe path exemption (paths containing ``/chat/``): the
      driver's chat-path probe is sent through ``resolve_gzip_client(True)``
      so it carries ``Content-Encoding: gzip`` — but the probe's purpose
      is purely to observe the gzip header on the wire, NOT to validate
      a chat response. The chat-probe path always returns
      ``200 {"ok": true}`` regardless of Content-Encoding (the strict
      embeddings rule is NOT applied to chat paths).
    * Strict rule (all other paths):
      - If ``Content-Encoding: gzip`` is present OR the raw body fails
        ``json.loads`` → 400 with the canonical OpenAI
        invalid-request-error envelope.
      - Else → 200 with an embeddings-shaped JSON payload
        (``object":"list", "data":[{"object":"embedding", "index":0,
        "embedding":[...]}]``).

    The handler captures each request in ``_StrictEmbeddingsHandler.server._captures``
    so the driver can introspect the wire-level observations
    (Content-Encoding header, raw body bytes, parse verdict, status
    sent). Accepts ANY path (chat paths get the probe response;
    everything else gets the strict embeddings treatment).
    """

    # Reasonable socket timeouts so the daemon doesn't hang forever if
    # the client disappears mid-request.
    timeout = 5.0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default per-request access log; the driver
        # emits its own structured transcript lines.
        return

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        n = self.server._counter  # type: ignore[attr-defined]
        self.server._counter = n + 1  # type: ignore[attr-defined]
        method = "POST"
        path = self.path
        content_encoding = self.headers.get("Content-Encoding")
        ce_str = content_encoding if content_encoding is not None else "absent"

        # Read the body (the strict server never inspects the
        # ``Content-Encoding`` header to alter its read — it reads the
        # raw bytes the wire delivered, then applies the gzip-detection
        # rule based on those bytes).
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError:
            content_length = 0
        try:
            body = self.rfile.read(content_length) if content_length > 0 else b""
        except Exception:  # noqa: BLE001
            body = b""

        body_head_hex = body[:16].hex()

        # Hoist gzip_magic_present so it is bound on BOTH branches
        # (chat-path branch skips the strict-rule assignment that would
        # otherwise bind it). Referenced by the capture-dict below
        # regardless of which branch runs — without this hoist the
        # chat-path branch raises UnboundLocalError on the reference.
        gzip_magic_present = body[:2] == b"\x1f\x8b"

        # Path classification: chat-probe paths are exempt from the
        # strict rule — the probe's purpose is to observe the gzip
        # header on the wire, NOT to validate a chat response. The
        # probe always returns 200 {"ok": true} so the chat-path
        # gzip client completes cleanly.
        is_chat_path = "/chat/" in path

        if is_chat_path:
            json_parse = "ok"  # probe is a successful round-trip
            status, response_body = self._build_chat_probe_200()
        else:
            # Strict embeddings rule: gzip on the wire OR non-JSON body → 400.
            gzip_header_present = (
                content_encoding is not None
                and "gzip" in content_encoding.lower()
            )
            if gzip_header_present or gzip_magic_present:
                json_parse = "gzip"
                status, response_body = self._build_400()
            else:
                try:
                    json.loads(body.decode("utf-8"))
                    json_parse = "ok"
                except (ValueError, UnicodeDecodeError):
                    json_parse = "fail"
                    status, response_body = self._build_400()
                else:
                    status, response_body = self._build_200()

        # Record the observation BEFORE writing the response so a
        # transient write error can't drop the capture.
        self.server._captures.append(  # type: ignore[attr-defined]
            {
                "n": n,
                "method": method,
                "path": path,
                "content_encoding_header": content_encoding,
                "content_length_header": content_length_raw,
                "body_bytes": body,
                "body_head_hex": body_head_hex,
                "gzip_magic": gzip_magic_present,
                "json_parse": json_parse,
                "status": status,
            }
        )

        # Server transcript line — required by the MOCK_TESTS.md spec.
        print(
            f"REQ {n} {method} {path} Content-Encoding={ce_str} "
            f"body_head={body_head_hex} json_parse={json_parse} -> {status}",
            flush=True,
        )

        # Write the response.
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        except Exception:  # noqa: BLE001
            # Client may have disconnected mid-flight; nothing we can do.
            pass

    def do_GET(self) -> None:  # noqa: N802 — http.server convention
        # Never used by the driver, but handle for completeness so
        # any health-check probe doesn't 501.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    @staticmethod
    def _build_400() -> tuple[int, bytes]:
        """Build the canonical OpenAI invalid-request-error envelope."""
        payload = {
            "error": {
                "message": (
                    "We could not parse the JSON body of your request. "
                    "(Simulated strict OpenAI /embeddings behavior)"
                ),
                "type": "invalid_request_error",
                "param": None,
                "code": None,
            }
        }
        return 400, json.dumps(payload).encode("utf-8")

    @staticmethod
    def _build_200() -> tuple[int, bytes]:
        """Build the embeddings-shaped 200 response.

        The vector is intentionally small (8 floats) — the driver
        only asserts non-empty + non-trivial.
        """
        payload = {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "index": 0,
                    "embedding": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
                }
            ],
            "model": "repro-mock-model",
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }
        return 200, json.dumps(payload).encode("utf-8")

    @staticmethod
    def _build_chat_probe_200() -> tuple[int, bytes]:
        """Build the chat-path-probe 200 ``{"ok": true}`` response.

        The chat probe exists ONLY to observe the wire-level
        ``Content-Encoding: gzip`` header stamped by
        ``resolve_gzip_client(True)`` — the response payload is
        irrelevant. A minimal ``{"ok": true}`` envelope is enough.
        """
        return 200, b'{"ok":true}'


class _StrictEmbeddingsServer:
    """ThreadingHTTPServer bound to 127.0.0.1:<port>.

    Holds the request captures list; ``self.base_url`` is the
    canonical ``http://127.0.0.1:<port>/v1`` string the driver uses
    to configure the embedding service.
    """

    def __init__(self, host: str, port: int) -> None:
        if not _is_port_free(host, port):
            raise RuntimeError(
                f"port {port} is already in use on {host} — refuse-to-bind "
                "(never kill anything on 8088 or any other port; investigate "
                "the existing listener and free the port or pick another)"
            )
        self._server = ThreadingHTTPServer((host, port), _StrictEmbeddingsHandler)
        # Per-instance state lives on the http.server itself.
        self._server._counter = 0  # type: ignore[attr-defined]
        self._server._captures = []  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"strict-emb-server-{port}",
            daemon=True,
        )
        self.port = port
        self.host = host

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    @property
    def captures(self) -> list[dict]:
        return self._server._captures  # type: ignore[attr-defined]

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        # Daemon thread, so no need to join — just yield to let it exit.
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Service construction (preserves the wire proof)
# ---------------------------------------------------------------------------


_EMBED_MODEL = "text-embedding-3-small"


def _make_service_config(embedding_base_url: str, embedding_api_key: str) -> MagicMock:
    """MagicMock quacking like ``SkillEvolutionConfig``.

    The service accesses ``config.embedding_model``,
    ``config.embedding_base_url``, ``config.embedding_api_key`` via
    ``getattr(self.config, ...)``. Setting the three explicitly on a
    MagicMock is sufficient and avoids importing the real
    ``SkillEvolutionConfig`` (which is a pydantic-settings class that
    reads env vars at instantiation — heavy and off-path for a
    wire-only test).
    """
    cfg = MagicMock(
        spec=["embedding_model", "embedding_base_url", "embedding_api_key"]
    )
    cfg.embedding_model = _EMBED_MODEL
    cfg.embedding_base_url = embedding_base_url
    cfg.embedding_api_key = embedding_api_key
    return cfg


def _make_embedding_repo() -> MagicMock:
    """MagicMock quacking like ``SkillEmbeddingRepository``.

    The embed_text call path does NOT touch the repo (it only
    returns a vector); a MagicMock is safe.
    """
    return MagicMock()


def _make_service(
    *, base_url: str, api_key: str, request_gzip: bool
) -> Any:
    """Build a SkillEmbeddingService pointing at the local strict server.

    ``request_gzip=True`` mirrors the production repro environment
    (OPENAI_REQUEST_GZIP=true). The constructor signature is
    IDENTICAL at HEAD and base — one driver serves both branches.
    """
    from daemon.services.skill_embedding_service import SkillEmbeddingService

    cfg = _make_service_config(embedding_base_url=base_url, embedding_api_key=api_key)
    repo = _make_embedding_repo()
    llm_config = {
        "base_url": base_url,
        "api_key": api_key,
        "model": "gpt-4o-mini",
        "request_gzip": request_gzip,
    }
    return SkillEmbeddingService(config=cfg, embedding_repo=repo, llm_config=llm_config)


# ---------------------------------------------------------------------------
# Chat-path probe (gzip transport separation check)
# ---------------------------------------------------------------------------


def _issue_chat_path_probe(base_url: str) -> dict:
    """Issue ONE POST through ``resolve_gzip_client(True)`` and return
    the server-side observation.

    The driver asserts the server saw ``Content-Encoding: gzip`` on
    this probe — proving the chat-path gzip transport is still
    active and untouched by the embedding-gzip exemption fix.
    """
    import httpx

    from daemon.services.llm_gzip import resolve_gzip_client

    client = resolve_gzip_client(True)
    if client is None:
        raise RuntimeError(
            "resolve_gzip_client(True) returned None — expected an "
            "httpx.Client singleton when enabled=True"
        )
    # Hit the chat path on the strict server (any path is accepted).
    # Payload MUST be sized above the gzip-shrink threshold —
    # ``GzipRequestTransport._compress_request_body`` (llm_gzip.py:216-217)
    # short-circuits when ``len(compressed) >= len(original)`` (the
    # tiny-payload "gzip overhead exceeds savings" edge case). A 60-byte
    # probe ("hi") would NOT trigger compression and the wire would
    # carry plain JSON, falsely failing the "chat-path stays gzipped"
    # assertion. Reuse PROBE_TEXT (2160 chars) so the chat body is
    # well above threshold and the gzip transport actually fires.
    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": PROBE_TEXT}],
    }
    try:
        resp = client.post(f"{base_url}/chat/completions", json=payload, timeout=10.0)
        chat_status = resp.status_code
        try:
            resp.read()
        except Exception:  # noqa: BLE001
            pass
    finally:
        # The httpx.Client is the module-level singleton — DO NOT close
        # it (would break any later consumer in the same process).
        # Just drop the local reference; the daemon owns the lifecycle.
        pass
    return {"chat_status": chat_status}


# ---------------------------------------------------------------------------
# Driver scenarios
# ---------------------------------------------------------------------------


def _drive_green(server: _StrictEmbeddingsServer) -> int:
    """GREEN: the fix at HEAD — embed_text must NOT gzip the wire body."""
    print("\n=== GREEN SCENARIO (HEAD, fix expected) ===", flush=True)

    service = _make_service(
        base_url=server.base_url, api_key="sk-repro-local", request_gzip=True
    )

    # Run embed_text — the REAL SkillEmbeddingService through the REAL
    # openai.OpenAI SDK on the REAL httpx default client (because
    # the fix passes http_client=None at the construction site).
    try:
        vector = asyncio.run(service.embed_text(PROBE_TEXT))
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: embed_text raised an exception: {type(e).__name__}: {e}")
        return 1
    if not isinstance(vector, list) or not vector:
        print(f"FAIL: embed_text returned a non-vector: {vector!r}")
        return 1

    # Inspect the server's wire-level captures.
    emb_captures = [
        c for c in server.captures if "embedding" in c["path"].lower()
        or c["path"].lower().endswith("/v1")
        # The path may be just "/v1/embeddings" or "/v1/..." — the openai
        # SDK appends "/embeddings" to the base_url. Accept either shape.
    ]
    # The actual path will be exactly "/v1/embeddings" because the
    # driver sets EMBEDDING_BASE_URL=http://127.0.0.1:<port>/v1.
    emb_captures = [
        c for c in server.captures if c["path"].endswith("/embeddings")
    ]
    if not emb_captures:
        print(
            "FAIL: server saw NO /embeddings requests; captures="
            f"{[(c['method'], c['path']) for c in server.captures]}"
        )
        return 1
    cap = emb_captures[-1]  # most recent

    print(
        f"GREEN embed observation: path={cap['path']} "
        f"Content-Encoding={cap['content_encoding_header']!r} "
        f"body_head={cap['body_head_hex']} json_parse={cap['json_parse']} "
        f"-> {cap['status']}",
        flush=True,
    )

    # GREEN assertions:
    ok = True
    if cap["content_encoding_header"] is not None:
        print(
            "FAIL: GREEN — embeddings request carried "
            f"Content-Encoding={cap['content_encoding_header']!r} "
            "(expected absent)"
        )
        ok = False
    if cap["gzip_magic"]:
        print("FAIL: GREEN — embeddings request body started with gzip magic bytes")
        ok = False
    if cap["json_parse"] != "ok":
        print(f"FAIL: GREEN — embeddings body did not parse as JSON: {cap['json_parse']}")
        ok = False
    if cap["status"] != 200:
        print(f"FAIL: GREEN — embeddings response status={cap['status']} (expected 200)")
        ok = False

    # Chat-path probe — gzip transport must STILL be active for chat.
    chat_obs = _issue_chat_path_probe(server.base_url)
    chat_caps = [c for c in server.captures if "chat" in c["path"].lower()]
    print(
        f"GREEN chat observation: chat_captures={len(chat_caps)}, "
        f"chat_status={chat_obs['chat_status']}",
        flush=True,
    )
    if not chat_caps:
        print("FAIL: GREEN chat-path — server saw NO /chat/completions request")
        ok = False
    else:
        chat_cap = chat_caps[-1]
        if chat_cap["content_encoding_header"] is None:
            print(
                "FAIL: GREEN chat-path — server saw NO Content-Encoding on "
                "the chat request (expected gzip transport to stamp it)"
            )
            ok = False
        else:
            print(
                "GREEN chat-path OK — server saw "
                f"Content-Encoding={chat_cap['content_encoding_header']!r} "
                "(gzip transport preserved)"
            )

    if ok:
        print("\nGREEN RESULT: PASS — embed path is ungzipped, chat path stays gzipped")
        return 0
    print("\nGREEN RESULT: FAIL")
    return 1


def _drive_red(server: _StrictEmbeddingsServer) -> int:
    """RED: base v0.15.2 (no fix) — embed_text WILL gzip the wire body.

    Exit 0 iff the 400-symptom was faithfully reproduced (the bug
    was re-captured). The driver is an HONEST observer of the wire.
    """
    print("\n=== RED SCENARIO (BASE, bug expected to reproduce) ===", flush=True)

    service = _make_service(
        base_url=server.base_url, api_key="sk-repro-local", request_gzip=True
    )

    exc_type: str | None = None
    exc_msg: str | None = None
    try:
        vector = asyncio.run(service.embed_text(PROBE_TEXT))
        returned = vector
    except Exception as e:  # noqa: BLE001
        exc_type = type(e).__name__
        exc_msg = str(e)
        returned = None

    emb_captures = [
        c for c in server.captures if c["path"].endswith("/embeddings")
    ]
    if not emb_captures:
        print(
            "RED FAIL: server saw NO /embeddings requests; captures="
            f"{[(c['method'], c['path']) for c in server.captures]}"
        )
        return 1
    cap = emb_captures[-1]

    print(
        f"RED embed observation: path={cap['path']} "
        f"Content-Encoding={cap['content_encoding_header']!r} "
        f"body_head={cap['body_head_hex']} json_parse={cap['json_parse']} "
        f"-> {cap['status']} client_returned={type(returned).__name__} "
        f"client_exc={exc_type}",
        flush=True,
    )

    # RED expected observations:
    # * server saw Content-Encoding: gzip (or gzip magic bytes)
    # * server returned 400
    # * client-side: the embed_text call did NOT silently return a
    #   vector — it raised an exception (RuntimeError "Embedding API
    #   call failed: ...") OR returned an empty/malformed vector.
    observed_gzip = (
        cap["content_encoding_header"] is not None
        and "gzip" in cap["content_encoding_header"].lower()
    ) or cap["gzip_magic"]
    observed_400 = cap["status"] == 400
    client_side_failure = (
        exc_type is not None
        or returned is None
        or (isinstance(returned, list) and len(returned) == 0)
    )

    print(
        f"RED assertions: observed_gzip={observed_gzip} "
        f"observed_400={observed_400} client_side_failure={client_side_failure} "
        f"(client_exc={exc_type}: {exc_msg})",
        flush=True,
    )

    if observed_gzip and observed_400 and client_side_failure:
        print(
            "\nRED RESULT: PASS — bug faithfully reproduced "
            "(gzip on wire → strict server 400 → client-side failure surfaced)"
        )
        return 0
    print(
        "\nRED RESULT: FAIL — expected red-side symptom did not reproduce "
        "(observed_gzip/observed_400/client_side_failure must all be True)"
    )
    return 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Embedding-Gzip Wire Exemption mock test (strict-server red→green repro)"
    )
    p.add_argument(
        "--expect",
        choices=("green", "red"),
        default="green",
        help="GREEN (default) = HEAD fix expected to ungzip embed path; "
        "RED = base bug expected to reproduce (gzip → 400 → client failure)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=18771,
        help="Strict server port (default 18771 — mock range 10000-19999)",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Strict server bind host (default 127.0.0.1)",
    )
    p.add_argument(
        "--repo-root",
        default=_default_repo_root(),
        help="Path to repo root whose 'daemon' package is importable "
        "(default: parent of this script's tests/mocks/ dir)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _classify_client_failure(exc: BaseException | None) -> str:
    """Classify a client-side failure for transcript clarity."""
    if exc is None:
        return "none"
    name = type(exc).__name__
    msg = str(exc)
    return f"{name}: {msg[:200]}"


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    # Ensure repo root is on sys.path BEFORE importing daemon modules.
    repo_root = os.path.abspath(args.repo_root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    os.chdir(repo_root)

    # The driver controls its own env (per MOCK_TESTS.md). The embedding
    # service reads ``llm_config`` directly (not env vars) — we set
    # these as documentation/reference but they are NOT required for
    # the wire proof (the driver passes base_url/api_key/request_gzip
    # through the ``llm_config`` dict into the service constructor).
    os.environ.setdefault("OPENAI_API_KEY", "sk-repro-local")
    os.environ.setdefault("OPENAI_REQUEST_GZIP", "true")
    os.environ.setdefault(
        "EMBEDDING_BASE_URL", f"http://{args.host}:{args.port}/v1"
    )

    print(
        f"=== Embedding-Gzip Wire Mock ===\n"
        f"  expect    : {args.expect}\n"
        f"  host:port : {args.host}:{args.port}\n"
        f"  repo_root : {repo_root}\n",
        flush=True,
    )

    # Boot the strict server. Port-hygiene first: refuse-and-report if
    # the port is already in use (NEVER kill processes on 8088 or any
    # other port — that's the operator's call).
    try:
        server = _StrictEmbeddingsServer(args.host, args.port)
    except RuntimeError as e:
        print(f"RESULT: FAIL (setup) — {e}", flush=True)
        return 1

    server.start()
    print(
        f"  strict server : bound at {server.base_url} "
        f"(pid={os.getpid()}, thread={server._thread.name})",
        flush=True,
    )

    started = time.monotonic()
    rc: int = 1
    try:
        if args.expect == "green":
            rc = _drive_green(server)
        else:
            rc = _drive_red(server)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("RESULT: FAIL (uncaught exception)", flush=True)
        rc = 1
    finally:
        elapsed = time.monotonic() - started
        # Tear down the server thread + socket — daemon thread, so no
        # join needed, but a brief sleep lets any in-flight request
        # finish its response write before we close the socket.
        try:
            server.close()
        finally:
            print(f"\nActual runtime: {elapsed:.2f} s", flush=True)
            signal.alarm(0)  # cancel the self-timeout

    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
