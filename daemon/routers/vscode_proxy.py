"""Reverse proxy application for an unauthenticated local code-server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocketState

from ..services.vscode_server_manager import VSCodeServerManager

MAX_BODY_BYTES = 50 * 1024 * 1024

# W1: Controlled CSP policy — NOT strip-all. Replaces code-server's restrictive CSP
# with our own that allows what VS Code needs for iframe embedding.
VSCODE_PROXY_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _proxy_headers(headers: Mapping[str, str], port: int) -> dict[str, str]:
    """Filter request hop-by-hop headers and set the local upstream authority."""
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() not in {"host", "origin"}
    }
    result["Host"] = f"127.0.0.1:{port}"
    result["Origin"] = f"http://127.0.0.1:{port}"
    return result


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Filter response hop-by-hop headers and replace framing-related policies."""
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"content-security-policy", "x-content-security-policy", "x-frame-options"}
    }
    result["Content-Security-Policy"] = VSCODE_PROXY_CSP
    result["X-Content-Security-Policy"] = VSCODE_PROXY_CSP
    result["X-Frame-Options"] = "SAMEORIGIN"
    return result


def create_vscode_proxy_app(manager: VSCodeServerManager) -> FastAPI:
    """Create an unmounted HTTP and WebSocket proxy for code-server.

    Args:
        manager: VS Code server lifecycle manager.

    Returns:
        An independent FastAPI sub-application.
    """
    app = FastAPI(title="VS Code proxy")

    def readiness() -> JSONResponse | None:
        if not manager.is_running():
            return JSONResponse(
                {"detail": "VS Code server is not ready"},
                status_code=503,
                headers={"Retry-After": "1"},
            )
        return None

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def proxy_http(request: Request, path: str):
        """Proxy an HTTP request with a bounded streaming request body."""
        unavailable = readiness()
        if unavailable is not None:
            return unavailable
        port = manager.get_port()
        if port is None:
            return JSONResponse(
                {"detail": "VS Code server is not ready"},
                status_code=503,
                headers={"Retry-After": "1"},
            )

        chunks: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
            chunks.append(chunk)

        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
        upstream: httpx.Response | None = None
        try:
            target = "/" + path
            if request.url.query:
                target += f"?{request.url.query}"
            upstream = await client.send(
                client.build_request(
                    request.method,
                    target,
                    headers=_proxy_headers(request.headers, port),
                    content=b"".join(chunks),
                ),
                stream=True,
            )

            async def content() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            return StreamingResponse(
                content(),
                status_code=upstream.status_code,
                headers=_response_headers(upstream.headers),
                media_type=None,
            )
        except Exception:
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()
            raise

    @app.websocket("/{path:path}")
    async def proxy_websocket(websocket: WebSocket, path: str) -> None:
        """Bridge text and binary WebSocket messages in both directions."""
        unavailable = readiness()
        if unavailable is not None:
            await websocket.close(code=1013, reason="VS Code server is not ready")
            return
        port = manager.get_port()
        if port is None:
            await websocket.close(code=1013, reason="VS Code server is not ready")
            return

        import websockets
        from websockets.typing import Subprotocol

        offered = [
            value.strip()
            for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if value.strip()
        ]
        selected = offered[0] if offered else None
        await websocket.accept(subprotocol=selected)
        upstream: Any = None
        try:
            upstream = await websockets.connect(
                f"ws://127.0.0.1:{port}/{path}",
                subprotocols=cast(list[Subprotocol] | None, offered or None),
                close_timeout=2,
                ping_interval=20,
                ping_timeout=20,
                additional_headers={"Host": f"127.0.0.1:{port}"},
            )

            async def browser_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_browser() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            async with asyncio.TaskGroup() as group:
                group.create_task(browser_to_upstream())
                group.create_task(upstream_to_browser())
        except* (WebSocketDisconnect, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if upstream is not None:
                await upstream.close()
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()

    return app
