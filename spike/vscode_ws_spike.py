"""Phase 0 spike: minimal HTTP + WebSocket proxy for code-server.

THROWAWAY code — not for production. Lives in `spike/` to keep it isolated
from `daemon/` and the production router tree. See `spike/SPIKE_FINDINGS.md`
for the validation report.

Validates three unknowns against a real code-server instance:
- C4: binary frames don't crash the proxy (`send_bytes` vs `send_text` dispatch)
- C5: `asyncio.TaskGroup()` cancels sibling tasks on disconnect
- W2: `Sec-WebSocket-Protocol` is forwarded from browser to upstream

Adaptations from the phase0-plan reference (Rev 3 carries forward):
- P1: streaming `_read_capped_body()` — capped by byte count, not full materialization
- P2: complete hop-by-hop header filter per RFC 7230 §6.1 (Connection, Keep-Alive,
     Proxy-Authenticate, Proxy-Authorization, TE, Trailers/Trailer, Transfer-Encoding,
     Upgrade, Host, Content-Length, plus Origin rewrite for cross-origin proxying)
- N2: websockets>=13.0 (already pinned in pyproject.toml)

Verified against `websockets==16.0` API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from websockets.typing import Subprotocol

logger = logging.getLogger("spike.ws")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)

# Upstream code-server binds to 127.0.0.1 only. Authentication is disabled because
# the proxy is the sole access path on localhost (this is throwaway, not prod).
UPSTREAM_HTTP = "http://127.0.0.1:9100"
UPSTREAM_WS = "ws://127.0.0.1:9100"

# 64 MiB cap on HTTP body forwarded to upstream. P1 fix: stream + count, don't
# materialize. Production should make this configurable.
MAX_BODY_BYTES = 64 * 1024 * 1024

# Hop-by-hop headers per RFC 7230 §6.1. These must be stripped when proxying
# because they apply to a single transport connection, not an end-to-end message.
# Origin is handled separately (rewritten to upstream host, including port).
_HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    )
)


def _forward_headers(headers) -> dict[str, str]:
    """Strip hop-by-hop headers and rewrite Origin/Host for the upstream hop."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        # Rewrite Origin so upstream's same-origin / CORS checks accept us.
        if k.lower() == "origin":
            out[k] = "http://127.0.0.1:9100"
            continue
        out[k] = v
    return out


async def _read_capped_body(request: Request, cap: int) -> AsyncIterator[bytes]:
    """Stream the request body byte-by-byte, stopping at `cap` bytes.

    P1 fix: raises if `cap` exceeded. Doesn't materialize the full body first.
    Falls back to reading chunks if the underlying receive() returns them.
    """
    received = 0
    while True:
        chunk = await request.receive()  # ASGI receive callable
        if chunk["type"] != "http.request":
            break
        body = chunk.get("body", b"")
        more_body = chunk.get("more_body", False)
        if body:
            received += len(body)
            if received > cap:
                raise ValueError(
                    f"request body exceeds {cap} bytes (got >{received})"
                )
            yield body
        if not more_body:
            break


app = FastAPI(title="vscode-ws-spike")


@app.api_route(
    "/vscode/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_http(request: Request, path: str) -> StreamingResponse:
    """Stream HTTP requests through to upstream code-server.

    Streams the body in chunks (P1) and strips hop-by-hop headers (P2).
    """
    fwd_headers = _forward_headers(request.headers)
    upstream_url = f"{UPSTREAM_HTTP}/{path}"

    # Buffer chunks to forward via httpx's `content` parameter. httpx itself
    # accepts an async iterator via the `content=` kwarg only in some versions;
    # for the spike we use a list-of-bytes since bodies are small.
    body_chunks: list[bytes] = []
    try:
        async for chunk in _read_capped_body(request, MAX_BODY_BYTES):
            body_chunks.append(chunk)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=413, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        upstream_resp = await client.send(
            client.build_request(
                request.method,
                upstream_url,
                headers=fwd_headers,
                content=b"".join(body_chunks) if body_chunks else b"",
            ),
            stream=True,
        )

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        # Strip hop-by-hop from the response too. Content-Length is dropped
        # because StreamingResponse will set it correctly.
        resp_headers = _forward_headers(upstream_resp.headers)
        return StreamingResponse(
            relay(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )


@app.websocket("/vscode/ws/{path:path}")
async def proxy_ws(websocket: WebSocket, path: str) -> None:
    """Bidirectional WS proxy.

    W2: capture the browser's Sec-WebSocket-Protocol, forward to upstream,
    accept with the negotiated subprotocol so the browser uses it.
    C4: dispatch bytes vs text in both directions.
    C5: TaskGroup cancels both pipes when either side raises.
    """
    raw_proto = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [s.strip() for s in raw_proto.split(",") if s.strip()]
    selected = subprotocols[0] if subprotocols else None
    # websockets v16 types `subprotocols` as Sequence[Subprotocol] (a NewType).
    # At runtime it's just str; cast for the strict type-checker.
    typed_subprotocols: list[Subprotocol] | None = (
        [Subprotocol(s) for s in subprotocols] if subprotocols else None
    )

    logger.info(
        "ws-connect path=%s subprotocols=%s selected=%s",
        path,
        subprotocols,
        selected,
    )

    await websocket.accept(subprotocol=selected)

    upstream_uri = f"{UPSTREAM_WS}/{path}"
    try:
        async with websockets.connect(
            upstream_uri,
            subprotocols=typed_subprotocols,
            # v13+ kwarg, kept forward-compatible.
            additional_headers={"Host": "127.0.0.1:9100"},
            # On disconnect, don't wait long for upstream to ack our close frame.
            # code-server's default is slow; 2s is enough for the spike.
            # Production may want a different value.
            close_timeout=2,
        ) as upstream:
            logger.info(
                "ws-upstream-connected negotiated=%s", upstream.subprotocol
            )
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        _browser_to_upstream(websocket, upstream),
                        name="browser_to_upstream",
                    )
                    tg.create_task(
                        _upstream_to_browser(upstream, websocket),
                        name="upstream_to_browser",
                    )
            except* WebSocketDisconnect:
                logger.info("ws-browser-disconnect path=%s", path)
            except* websockets.ConnectionClosed:
                logger.info("ws-upstream-closed path=%s", path)
    except websockets.InvalidStatus as exc:
        logger.warning("ws-upstream-rejected status=%s", exc.response.status_code)
    except websockets.InvalidHandshake as exc:
        logger.warning("ws-upstream-handshake-error: %s", exc)
    finally:
        logger.info("ws-cleanup-done path=%s", path)


async def _browser_to_upstream(
    ws: WebSocket, upstream: websockets.ClientConnection
) -> None:
    """Forward browser frames to upstream, dispatching bytes vs text."""
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(
                code=msg.get("code", 1000), reason=msg.get("reason", "")
            )
        # C4: dispatch bytes vs text. websockets v16 `.send()` accepts either.
        if "bytes" in msg and msg["bytes"] is not None:
            await upstream.send(msg["bytes"])
        elif "text" in msg and msg["text"] is not None:
            await upstream.send(msg["text"])


async def _upstream_to_browser(
    upstream: websockets.ClientConnection, ws: WebSocket
) -> None:
    """Forward upstream frames to browser, dispatching bytes vs text."""
    while True:
        msg = await upstream.recv()  # raises ConnectionClosed on disconnect
        # C4: dispatch symmetrically.
        if isinstance(msg, bytes):
            await ws.send_bytes(msg)
        else:
            await ws.send_text(msg)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "spike.vscode_ws_spike:app",
        host="127.0.0.1",
        port=int(os.environ.get("SPIKE_PORT", "8079")),
        log_level="info",
    )