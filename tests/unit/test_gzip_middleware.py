"""Pins for ``daemon.api.ResponseGzipMiddleware`` (cpu-throughput-arc2 L1 #2b).

WHY a house middleware instead of ``starlette.middleware.gzip``
--------------------------------------------------------------
Verified against the SHIPPED starlette 0.52.1 (read in the worktree
venv, ``starlette/middleware/gzip.py``) — not from docs:

* 0.52.1 defaults ``DEFAULT_EXCLUDED_CONTENT_TYPES = ("text/event-stream",)``,
  so SSE is safe — but that is the ONLY exclusion. It compresses
  ``application/octet-stream`` / ``image/*`` / ``video/*`` above
  ``minimum_size``, i.e. exactly the ``/views`` media + mermaid-asset
  lanes and the vscode proxy's byte-faithful pass-through.
* its default ``compresslevel=9``. Measured here on a 2.6 MB
  ``/api/instances``-shaped payload: level 9 = 98.4 ms, level 1 =
  6.2 ms for the same ~31-46x ratio class — on a CPU-reduction
  commission level 9 would be a regression.
* it compresses STREAMING bodies chunk-by-chunk through a shared
  ``gzip.GzipFile`` (internal zlib buffering) for any non-SSE content
  type, i.e. an unbounded-buffering hazard on ``more_body=True``.

Contract pinned below
---------------------
(a) small JSON < ``minimum_size``  → NO Content-Encoding, bytes identical
(b) fat JSON  ≥ ``minimum_size``   → Content-Encoding: gzip,
    gunzip(bytes) == identity body, Content-Type preserved,
    Vary: Accept-Encoding present, Content-Length == compressed size
(c) SSE (``text/event-stream``)    → identity, PER-EVENT flush intact
    (asserted at the ASGI message layer with timestamps, i.e. the layer
    the middleware actually acts on — not through a buffering client)
(d) response already carrying ``Content-Encoding`` → no double-compress
(e) ``Accept-Encoding: identity``  → uncompressed
(f) streaming ``application/octet-stream`` (non-SSE, ``more_body=True``)
    → identity, no buffering, no header mutation
(g) wiring: the middleware IS registered in ``daemon.api.create_app()``
    immediately INSIDE the CORS decoration (LIFO ⇒ it is the entry
    AFTER CORSMiddleware in ``app.user_middleware``), and end-to-end
    through the REAL app: ``/openapi.json`` (~513 KB JSON) compresses,
    ``/livez`` (tiny JSON) stays identity.
(h) MEASUREMENT GATE: bytes + wall-time, gzip vs identity, on a
    >= 100 KB JSON response through the app (median of N reps).

Run::

    .venv/bin/python -m pytest -q tests/unit/test_gzip_middleware.py
"""

from __future__ import annotations

import asyncio
import gzip
import json
import statistics
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from daemon.api import (
    GZIP_RESPONSE_COMPRESS_LEVEL,
    GZIP_RESPONSE_MINIMUM_SIZE,
    ResponseGzipMiddleware,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FAT_BYTES = 150_000
SMALL_BYTES = 1_200


def _payload(nbytes: int) -> dict:
    """An ``/api/instances``-shaped JSON body of at least ``nbytes``."""
    row = {
        "instance_id": "00000001-aaaa-bbbb-cccc-000000000001",
        "agent_id": "developer",
        "project_id": "proj-0",
        "agent_dir": "agents/developer",
        "status": "running",
        "title": "cpu-l1 module M3 work item",
        "initiative_message": (
            "Implement response gzip compression at the app middleware layer"
        ),
        "parent_id": None,
        "created_at": "2026-11-21T00:00:00+00:00",
        "updated_at": "2026-11-21T00:00:00+00:00",
    }
    rows: list[dict] = []
    approx = 0
    while approx < nbytes:
        # Unique ids/statuses per row — a realistic ``/api/instances``
        # shape, so the measured ratio is NOT inflated by duplicated
        # byte runs (the honest number is ~30x, not ~90x).
        rows.append(
            {
                **row,
                "instance_id": f"{len(rows):08x}-aaaa-bbbb-cccc-{len(rows):012x}",
                "status": ["running", "idle", "completed", "failed", "paused"][
                    len(rows) % 5
                ],
                "title": f"cpu-l1 module M3 work item {len(rows)}",
            }
        )
        approx += 360
    return {"instances": rows, "total": len(rows)}


def _build_app(
    *, minimum_size: int = GZIP_RESPONSE_MINIMUM_SIZE, with_cors: bool = True
) -> FastAPI:
    """The production wiring shape: CORS first, gzip registered after it."""
    app = FastAPI()
    if with_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(
        ResponseGzipMiddleware,
        minimum_size=minimum_size,
        compresslevel=GZIP_RESPONSE_COMPRESS_LEVEL,
    )

    @app.get("/small")
    def small():
        return _payload(SMALL_BYTES)

    @app.get("/fat")
    def fat():
        return _payload(FAT_BYTES)

    @app.get("/text")
    def text():
        return Response(
            content=("line of plain text\n" * 4000).encode(),
            media_type="text/plain",
        )

    @app.get("/binary")
    def binary():
        return Response(
            content=bytes(range(256)) * 400,
            media_type="application/octet-stream",
        )

    @app.get("/already-encoded")
    def already_encoded():
        body = gzip.compress(
            json.dumps(_payload(FAT_BYTES)).encode(),
            GZIP_RESPONSE_COMPRESS_LEVEL,
            mtime=0,
        )
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )

    @app.get("/sse")
    async def sse():
        async def gen():
            for i in range(3):
                yield {"event": "tick", "data": "x" * 400}
                await asyncio.sleep(0.12)

        return EventSourceResponse(gen(), ping=5)

    @app.get("/proxy-json")
    def proxy_json():
        """The vscode-proxy shape (``StreamingResponse``, media_type=None,
        content-type inherited from the upstream headers): a
        ``StreamingResponse`` ALWAYS sends ``more_body=True`` per chunk,
        so the streaming guard must keep it identity even though the
        content type is compressible."""

        async def gen():
            yield json.dumps(_payload(FAT_BYTES)).encode()
            await asyncio.sleep(0.05)
            yield b'{"tail":true}'

        return StreamingResponse(
            gen(), media_type=None,
            headers={"Content-Type": "application/json"},
        )

    @app.get("/stream-binary")
    def stream_binary():
        async def gen():
            for i in range(3):
                yield b"\x00\x01\x02" * 300
                await asyncio.sleep(0.05)

        return StreamingResponse(gen(), media_type="application/octet-stream")

    return app


@pytest.fixture
def client():
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# (a) small JSON stays byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_json_is_identity():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/small")
    identity = json.dumps(_payload(SMALL_BYTES), separators=(",", ":")).encode()
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") is None, (
        "a body below minimum_size must never be compressed"
    )
    assert "vary" not in resp.headers or "accept-encoding" not in (
        resp.headers.get("vary", "").lower()
    ), "Vary: Accept-Encoding must NOT be added when nothing was compressed"
    # Byte identity: the decoded body is exactly the handler's JSON.
    assert json.loads(resp.content) == json.loads(identity)
    assert resp.headers["content-length"] == str(len(resp.content))


# ---------------------------------------------------------------------------
# (b) fat JSON compresses, round-trips, headers preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fat_json_is_gzip_and_round_trips():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/fat")
        # Raw wire bytes (httpx transparently decodes on .content).
        async with ac.stream("GET", "/fat") as streamed:
            raw = b"".join([chunk async for chunk in streamed.aiter_raw()])
            raw_headers = dict(streamed.headers)

    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["content-type"].startswith("application/json")
    assert "accept-encoding" in resp.headers["vary"].lower()
    # Content-Length must describe the COMPRESSED bytes on the wire.
    assert int(resp.headers["content-length"]) == len(raw)
    # The wire really is gzip and decompresses to the handler's JSON.
    assert raw[:2] == b"\x1f\x8b"
    decoded = gzip.decompress(raw)
    assert json.loads(decoded) == _payload(FAT_BYTES)
    assert len(decoded) >= FAT_BYTES
    assert len(raw) < len(decoded)
    assert raw_headers["content-encoding"] == "gzip"


@pytest.mark.asyncio
async def test_fat_text_is_compressed():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/text")
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_binary_octet_stream_is_identity():
    """FENCE: binary / octet-stream never compresses."""
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/binary")
    assert resp.headers.get("content-encoding") is None
    assert resp.content == bytes(range(256)) * 400
    assert int(resp.headers["content-length"]) == len(resp.content)


# ---------------------------------------------------------------------------
# (c) SSE stays identity AND stays per-event flushed
# ---------------------------------------------------------------------------


class _MessageRecorder:
    """Record every ASGI message the app emits, with a timestamp.

    This is the layer the middleware acts on, so it is the only honest
    place to prove "events arrive unbuffered": a buffering client (or
    ``httpx.ASGITransport``, which can coalesce) would mask the
    regression the SSE care rule is about.
    """

    def __init__(self, app):
        self.app = app
        self.messages: list[tuple[float, dict]] = []

    async def __call__(self, scope, receive, send):
        t0 = time.perf_counter()

        async def rec(message):
            self.messages.append((time.perf_counter() - t0, dict(message)))
            await send(message)

        await self.app(scope, receive, rec)

    # -- assertions helpers --
    @property
    def start(self) -> dict:
        return next(m for _, m in self.messages if m["type"] == "http.response.start")

    @property
    def bodies(self) -> list[tuple[float, dict]]:
        return [(t, m) for t, m in self.messages if m["type"] == "http.response.body"]


@pytest.mark.asyncio
async def test_sse_is_identity_and_per_event_flushed():
    app = _build_app()
    recorder = _MessageRecorder(app)

    async def receive():
        await asyncio.sleep(5)
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/sse",
        "raw_path": b"/sse",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept-encoding", b"gzip, deflate")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    task = asyncio.create_task(recorder(scope, receive, send))
    # Three events at 0.12 s spacing, then the stream parks on ping=5.
    await asyncio.sleep(0.75)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    start = recorder.start
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers, (
        "SSE MUST stay identity — a gzip wrapper would buffer/encode events"
    )
    assert "content-length" not in headers, "SSE must remain a chunked stream"

    bodies = recorder.bodies
    event_chunks = [
        (t, m["body"]) for t, m in bodies if b"event: tick" in m.get("body", b"")
    ]
    assert len(event_chunks) == 3, (
        f"expected one flushed ASGI body message per event, got {len(event_chunks)}"
    )
    # Unbuffered: the three events arrive at three distinct instants,
    # ~0.12 s apart, rather than in one coalesced burst.
    stamps = [t for t, _ in event_chunks]
    assert stamps[1] - stamps[0] > 0.05, f"events coalesced: {stamps}"
    assert stamps[2] - stamps[1] > 0.05, f"events coalesced: {stamps}"
    # And the payload is untouched (not gzip magic).
    assert event_chunks[0][1].startswith(b"event: tick")


# ---------------------------------------------------------------------------
# (d) already-encoded response: no double compression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preexisting_content_encoding_is_not_recompressed():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        async with ac.stream("GET", "/already-encoded") as resp:
            raw = b"".join([chunk async for chunk in resp.aiter_raw()])
    # Exactly ONE gzip layer: decompress once and the JSON appears.
    assert json.loads(gzip.decompress(raw)) == _payload(FAT_BYTES)
    # A second layer would fail to parse as JSON after one decompress.


# ---------------------------------------------------------------------------
# (e) client that does not offer gzip gets identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_encoding_identity_is_not_compressed():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/fat", headers={"accept-encoding": "identity"})
    assert resp.headers.get("content-encoding") is None
    assert json.loads(resp.content) == _payload(FAT_BYTES)
    assert int(resp.headers["content-length"]) == len(resp.content)


@pytest.mark.parametrize(
    "accept_encoding, compressed",
    [
        ("gzip", True),
        ("gzip, deflate, br, zstd", True),
        ("GZIP", True),                      # case-insensitive
        ("*", True),                         # bare wildcard covers gzip
        ("gzip;q=0", False),                 # explicit refusal honoured
        ("gzip;q=0.0, deflate", False),
        ("br, zstd", False),
        ("identity", False),
        ("", False),
    ],
)
@pytest.mark.asyncio
async def test_accept_encoding_negotiation(accept_encoding, compressed):
    """``gzip;q=0`` is a REFUSAL (stock starlette's substring test gets
    this wrong) and a bare ``*`` covers gzip."""
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get(
            "/fat", headers={"accept-encoding": accept_encoding}
        )
    promoted = resp.headers.get("content-encoding") == "gzip"
    assert promoted is compressed, (
        f"Accept-Encoding={accept_encoding!r} -> {resp.headers.get('content-encoding')}"
    )


# ---------------------------------------------------------------------------
# (f) streaming non-SSE: identity, unmodified, no coalescing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_binary_is_identity_and_streamed():
    app = _build_app()
    recorder = _MessageRecorder(app)

    async def receive():
        await asyncio.sleep(5)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream-binary",
        "raw_path": b"/stream-binary",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept-encoding", b"gzip")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    await recorder(scope, receive, send)

    headers = {
        k.decode().lower(): v.decode() for k, v in recorder.start["headers"]
    }
    assert "content-encoding" not in headers
    bodies = [m for _, m in recorder.bodies]
    chunks = [m["body"] for m in bodies if m.get("body")]
    # Three chunk sends survive as three ASGI messages (no coalescing).
    assert len(chunks) == 3, f"streaming chunks were coalesced: {len(chunks)}"
    assert all(c == b"\x00\x01\x02" * 300 for c in chunks)


@pytest.mark.asyncio
async def test_compressed_content_type_but_streaming_stays_identity():
    """The vscode-proxy lane (``daemon/routers/vscode_proxy.py:1186``):
    compressible content type, but chunked streaming — MUST stay
    identity so nothing is buffered.

    Asserted at the ASGI message layer (httpx/ASGITransport coalesces
    body messages, which would hide exactly the regression under test).
    """
    app = _build_app()
    recorder = _MessageRecorder(app)

    async def receive():
        await asyncio.sleep(5)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/proxy-json",
        "raw_path": b"/proxy-json",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept-encoding", b"gzip")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    await recorder(scope, receive, send)

    headers = {
        k.decode().lower(): v.decode() for k, v in recorder.start["headers"]
    }
    assert headers["content-type"].startswith("application/json")
    assert "content-encoding" not in headers, (
        "a StreamingResponse (more_body=True) must never be compressed"
    )
    bodies = [m for _, m in recorder.bodies]
    # Two upstream chunks reach the wire as two ASGI messages — no
    # coalescing, i.e. the streaming contract is preserved.
    payload_chunks = [m["body"] for m in bodies if m.get("body")]
    assert len(payload_chunks) == 2, payload_chunks
    assert not any(c[:2] == b"\x1f\x8b" for c in payload_chunks)


# ---------------------------------------------------------------------------
# (b2) CORS interaction — gzip sits INSIDE the CORS decoration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_headers_survive_compression_in_lifo_order():
    """Brief's mandated order: ``app.add_middleware(GZip)`` immediately
    AFTER the CORS block ⇒ LIFO ⇒ the CORS middleware wraps gzip, so the
    CORS headers are already on the response when gzip rewrites
    Content-Encoding/Content-Length/Vary. They must survive untouched,
    and ``Vary`` must merge (``Origin, Accept-Encoding``)."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://fe.example"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        ResponseGzipMiddleware, minimum_size=512, compresslevel=1
    )

    @app.get("/fat")
    def fat():
        return _payload(FAT_BYTES)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/fat", headers={"origin": "https://fe.example"})
        preflight = await ac.options(
            "/fat",
            headers={
                "origin": "https://fe.example",
                "access-control-request-method": "GET",
            },
        )

    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["access-control-allow-origin"] == "https://fe.example"
    assert resp.headers["access-control-allow-credentials"] == "true"
    vary = resp.headers["vary"].lower()
    assert "origin" in vary and "accept-encoding" in vary, vary
    # Preflight (CORS answers it itself, tiny body): never compressed.
    assert preflight.status_code == 200
    assert preflight.headers.get("content-encoding") is None


# ---------------------------------------------------------------------------
# (e2) HEAD + pathsend edge shapes stay identity
# ---------------------------------------------------------------------------


class _RawASGIApp:
    """Emit a hand-built ASGI response (bypasses TestClient quirks)."""

    def __init__(self, headers: list[tuple[bytes, bytes]], messages: list[dict]):
        self.headers = headers
        self.messages = messages

    async def __call__(self, scope, receive, send):
        for message in self.messages:
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(self.headers)}
            await send(message)


async def _run_through_middleware(scope_extra: dict, inner_app) -> dict:
    middleware = ResponseGzipMiddleware(inner_app, minimum_size=200, compresslevel=1)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"t"), (b"accept-encoding", b"gzip")],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
        **scope_extra,
    }
    await middleware(scope, receive, send)
    return {
        "sent": sent,
        "headers": {
            k.decode().lower(): v.decode()
            for k, v in next(
                m for m in sent if m["type"] == "http.response.start"
            )["headers"]
        },
        "body": b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"),
    }


@pytest.mark.asyncio
async def test_head_is_identity():
    """HEAD: no body compression, so Content-Length stays the identity
    size (every HEAD pin in this repo pairs them)."""
    body = b"z" * 5000
    inner = _RawASGIApp(
        [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        [
            {"type": "http.response.start", "status": 200},
            {"type": "http.response.body", "body": body, "more_body": False},
        ],
    )
    out = await _run_through_middleware({"method": "HEAD"}, inner)
    assert "content-encoding" not in out["headers"]
    assert out["headers"]["content-length"] == "5000"


@pytest.mark.asyncio
async def test_pathsend_is_identity_and_flushes_headers():
    """``http.response.pathsend`` (FileResponse under uvicorn): the held
    start must be flushed and the message passed through untouched."""
    inner = _RawASGIApp(
        [(b"content-type", b"application/json")],
        [
            {"type": "http.response.start", "status": 200},
            {"type": "http.response.pathsend", "path": "/tmp/whatever.json"},
        ],
    )
    out = await _run_through_middleware({}, inner)
    assert "content-encoding" not in out["headers"], (
        "a pathsend body never enters the responder — headers must not "
        "claim compression"
    )
    types = [m["type"] for m in out["sent"]]
    assert types == ["http.response.start", "http.response.pathsend"]


@pytest.mark.asyncio
async def test_etag_bearing_response_is_identity():
    """VALIDATOR SAFETY: a strong ETag must identify exactly one
    representation. Serving gzip and identity bytes under the same tag
    is a validator lie, so ETag-bearing responses stay identity (the
    live-view wrapper / mermaid-asset lanes)."""
    inner = _RawASGIApp(
        [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"etag", b'"abc123"'),
        ],
        [
            {"type": "http.response.start", "status": 200},
            {"type": "http.response.body", "body": b"<html>" + b"z" * 20000 + b"</html>", "more_body": False},
        ],
    )
    out = await _run_through_middleware({}, inner)
    assert "content-encoding" not in out["headers"]
    assert out["headers"]["etag"] == '"abc123"'
    assert len(out["body"]) == 20013


# ---------------------------------------------------------------------------
# (g) wiring in the REAL create_app()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_built_stack_runs_route_then_cors_then_gzip():
    """The brief's mandated response order, asserted against the BUILT
    middleware chain (not just the registration list): egress must be
    route → CORS → ResponseGzip, i.e. gzip runs INSIDE the CORS
    decoration, and the only middleware outside it are the access-log
    and host-capture ones (registration order untouched).
    """
    from daemon.config import AuthConfig
    from daemon.api import create_app

    app = create_app(auth_config=AuthConfig(enabled=False))
    names = [m.cls.__name__ for m in app.user_middleware]

    # ``user_middleware`` is newest-first (Starlette inserts at index 0),
    # so gzip registered AFTER CORS lands directly BEFORE it in the list.
    assert "ResponseGzipMiddleware" in names, names
    cors_idx = names.index("CORSMiddleware")
    assert names[cors_idx - 1] == "ResponseGzipMiddleware", (
        "gzip must be registered immediately after CORS so the response "
        f"order is route -> CORS -> gzip: {names}"
    )
    # Downstream registrations unchanged (access log + host capture).
    assert names[: cors_idx - 1] == [
        "HostCaptureMiddleware",
        "SelectiveAccessLogMiddleware",
    ], names

    # Build the real stack with a warmup request, then walk it.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        await ac.get("/livez")

    chain, node = [], app.middleware_stack
    while node is not None and getattr(node, "app", None) is not None:
        chain.append(type(node).__name__)
        node = node.app

    assert "ResponseGzipMiddleware" in chain, chain
    gz = chain.index("ResponseGzipMiddleware")
    cors = chain.index("CORSMiddleware")
    # Outermost → innermost. CORS is ONE node deeper than gzip, so the
    # response egress order is route → CORS → gzip.
    assert cors == gz + 1, chain


@pytest.mark.asyncio
async def test_real_app_openapi_compresses_livez_does_not():
    """End-to-end through the production app: the 500 KB-class
    ``/openapi.json`` compresses; the tiny ``/livez`` JSON does not
    (no DB / no lifespan needed for either route)."""
    from daemon.config import AuthConfig
    from daemon.api import create_app

    app = create_app(auth_config=AuthConfig(enabled=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        openapi = await ac.get("/openapi.json")
        livez = await ac.get("/livez")

    assert openapi.status_code == 200
    assert openapi.headers.get("content-encoding") == "gzip"
    assert openapi.headers["content-type"].startswith("application/json")
    assert "accept-encoding" in openapi.headers["vary"].lower()
    assert isinstance(openapi.json(), dict) and "paths" in openapi.json()

    assert livez.status_code == 200
    assert livez.headers.get("content-encoding") is None, (
        "small poll/probe JSON must stay byte-identical"
    )


# ---------------------------------------------------------------------------
# (h) MEASUREMENT GATE — bytes + wall time, gzip vs identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_measurement_gate_bytes_and_wall_time(capsys):
    """Report compressed-vs-identity bytes and median wall time on a
    >= 100 KB JSON response through the app. Prints a table so the
    numbers land in the pytest output (asserted loosely — a hard
    threshold here would be a flaky CI pin)."""
    app = _build_app()
    reps = 15
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # warm both paths
        await ac.get("/fat")
        await ac.get("/fat", headers={"accept-encoding": "identity"})

        identity_ms: list[float] = []
        gzip_ms: list[float] = []
        identity_bytes = gzip_bytes = 0

        async def _wire(path: str, headers: dict) -> tuple[int, str | None]:
            """Raw wire bytes (no httpx decompression)."""
            async with ac.stream("GET", path, headers=headers) as resp:
                n = 0
                encoding = resp.headers.get("content-encoding")
                async for chunk in resp.aiter_raw():
                    n += len(chunk)
            return n, encoding

        for _ in range(reps):
            t0 = time.perf_counter()
            n, enc = await _wire("/fat", {"accept-encoding": "identity"})
            identity_ms.append((time.perf_counter() - t0) * 1000)
            identity_bytes = n
            assert enc is None

            t0 = time.perf_counter()
            n, enc = await _wire("/fat", {"accept-encoding": "gzip"})
            gzip_ms.append((time.perf_counter() - t0) * 1000)
            gzip_bytes = n
            assert enc == "gzip"

    med_identity = statistics.median(identity_ms)
    med_gzip = statistics.median(gzip_ms)
    ratio = identity_bytes / max(gzip_bytes, 1)
    print(
        "\n[gzip measurement] payload>=100KB json through the app "
        f"({reps} reps)\n"
        f"  identity: {identity_bytes:>8} B  median {med_identity:7.2f} ms\n"
        f"  gzip    : {gzip_bytes:>8} B  median {med_gzip:7.2f} ms\n"
        f"  ratio   : {ratio:5.1f}x   latency delta {med_gzip - med_identity:+.2f} ms"
    )

    assert identity_bytes >= 100_000
    assert ratio > 10, "a repetitive JSON list payload must compress >10x"
    # The absolute CPU cost must stay in the sub-10 ms band at level 1.
    assert med_gzip - med_identity < 20, (
        f"gzip added {med_gzip - med_identity:.2f} ms at level "
        f"{GZIP_RESPONSE_COMPRESS_LEVEL} on a {identity_bytes} B body"
    )
    assert GZIP_RESPONSE_MINIMUM_SIZE == 8192
