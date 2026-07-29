"""Reverse proxy application for an unauthenticated local code-server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocketState

from ..services.vscode_server_manager import VSCodeServerManager
from ..services.workspace_guard import WorkspaceGuard

logger = logging.getLogger(__name__)

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


def _validate_folder_param(
    query_string: str,
    project_repo,
) -> str:
    """Validate the ``?folder=`` query parameter against known project directories.

    C1: Prevents arbitrary filesystem access via ``?folder=/etc`` etc. by
    confining the folder to a known project's main_directory using
    :meth:`WorkspaceGuard.resolve_strict`.

    - If no ``folder`` param is present, returns the query string unchanged.
    - If ``folder`` is present but ``project_repo`` is ``None``, drops the
      ``folder`` param entirely (fail-closed: no validation possible →
      don't forward user-supplied paths).
    - If ``folder`` is present and matches a known project workdir, returns
      the query string with the resolved (canonicalized) folder value.
    - If ``folder`` is present but matches no known project, raises
      :class:`HTTPException` with status 403.
    """
    if not query_string:
        return query_string

    params = parse_qs(query_string, keep_blank_values=True)
    folder = params.get("folder", [None])[0]
    if not folder:
        # No folder param, pass through unchanged
        return query_string

    if project_repo is None:
        # Fail-closed: can't validate — drop the folder param entirely
        logger.warning(
            "C1: dropping ?folder= because project_repo is unavailable"
        )
        params.pop("folder", None)
        return urlencode(params, doseq=True)

    # Validate the folder against any project's main_directory
    try:
        projects = project_repo.list_projects()
    except Exception as exc:
        logger.warning("C1: folder validation DB read failed: %s", exc)
        params.pop("folder", None)
        return urlencode(params, doseq=True)

    for project in projects:
        main_directory = getattr(project, "main_directory", None)
        if not main_directory:
            continue
        try:
            guard = WorkspaceGuard(main_directory)
        except (ValueError, OSError) as exc:
            # main_directory doesn't exist or is invalid — skip
            logger.debug(
                "WorkspaceGuard init skipped for %s: %s", main_directory, exc
            )
            continue
        resolved, error = guard.resolve_strict(folder)
        if error is None and resolved is not None:
            # Folder is within this project's workdir — valid
            params["folder"] = [str(resolved)]
            return urlencode(params, doseq=True)

    # Folder doesn't match any project — reject
    raise HTTPException(
        status_code=403,
        detail={
            "error": "Invalid folder parameter",
            "detail": "The folder path is not within any known project directory",
        },
    )


async def upstream_to_browser(websocket: WebSocket, upstream: Any) -> None:
    """Forward messages from ``upstream`` (code-server) to ``websocket`` (browser).

    Defense-in-depth against a race: when the browser disconnects while
    code-server is still streaming messages, sending on a closed WS raises
    ``RuntimeError: Unexpected ASGI message 'websocket.send', after sending
    'websocket.close'``. Without a guard this propagates out of the
    TaskGroup and the proxy crashes with an unhandled exception.

    Three layers of defense:

    1. **Pre-send state check** (Fix 3): best-effort — if the browser WS
       is no longer in ``CONNECTED`` state, stop before issuing a write.
    2. **try/except around send** (Fix 1): catches the ``RuntimeError``
       that Starlette raises when the WS is already closed, breaking
       the loop cleanly.
    3. The enclosing ``except*`` (Fix 2) also catches ``RuntimeError`` as
       a final backstop in case either (1) or (2) misses.
    """
    async for message in upstream:
        # (Fix 3) Skip writes if the browser has already disconnected.
        # WebSocketState is imported at module top — if starlette ever
        # stops exporting it, we still fall through to layer 2 below.
        if websocket.client_state != WebSocketState.CONNECTED:
            break
        try:
            # (Fix 1) Wrap the actual write so a stale WS doesn't crash
            # the TaskGroup. We catch RuntimeError specifically and
            # break out — propagating up would crash the proxy.
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)
        except RuntimeError:
            # Browser WS closed mid-stream; stop forwarding.
            break


def create_vscode_proxy_app(
    manager: VSCodeServerManager,
    project_repo=None,
) -> FastAPI:
    """Create an unmounted HTTP and WebSocket proxy for code-server.

    Args:
        manager: VS Code server lifecycle manager.
        project_repo: Optional project repository used to validate the
            ``?folder=`` query parameter (C1 security fix). When ``None``,
            the folder param is dropped (fail-closed).

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
                # C1: Validate ?folder= before forwarding to code-server
                validated_query = _validate_folder_param(
                    request.url.query, project_repo
                )
                if validated_query:
                    target += f"?{validated_query}"
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

            async with asyncio.TaskGroup() as group:
                group.create_task(browser_to_upstream())
                group.create_task(upstream_to_browser(websocket, upstream))
        # (Fix 2) Add RuntimeError so the outer handler swallows the
        # ASGI-after-close crash even if Fixes 1/3 miss an edge case.
        except* (WebSocketDisconnect, ConnectionError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            if upstream is not None:
                await upstream.close()
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()

    return app
