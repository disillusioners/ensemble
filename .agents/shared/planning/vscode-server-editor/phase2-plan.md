# Phase 2: Reverse Proxy (HTTP + WebSocket)

## Objective
Build a reverse proxy that forwards `/vscode/*` requests — including WebSocket upgrade connections — from the FastAPI app to the spawned `code-server` process. This phase delivers the **proxy router and factory only** — the actual `app.mount()` happens in Phase 3 (W3 fix). This is the most technically challenging phase because WebSocket proxying is novel to this codebase and must handle **binary frames** (C4) with proper **task lifecycle** (C5).

> **Rev 2 changes**: C4 (binary frame dispatch), C5 (TaskGroup), W1 (controlled CSP instead of strip-all), W2 (subprotocol forwarding), W3 (mount ownership → Phase 3), W5 (body cap), S1 (catch-all guard), S4 (Origin/Host forwarding). Estimate raised from 8h to 12h.
>
> **Rev 3 changes (R4, P1, P2)**: R4 (removed auth cookie injection — code-server `--auth none`), P1 (streaming body cap via `_read_capped_body()` — not `request.body()`), P2 (Origin header includes port + complete hop-by-hop filter per RFC 7230 §6.1), N2 (pin `websockets>=13.0`).

## Coupling
- **Depends on**: Phase 1 (VSCodeServerManager — needs port + readiness + auth token)
- **Coupling type**: **tight** — proxy reads `manager.get_port()` and `manager.is_running()` on every request
- **Shared files with other phases**: `daemon/routers/vscode_proxy.py` (new — router + factory only)
- **Shared APIs/interfaces**: `create_vscode_proxy_app(manager)` factory → returns FastAPI sub-app
- **Why this coupling**: The proxy cannot function without knowing the code-server port and readiness state from Phase 1's manager.
- **W3 note**: Phase 2 does NOT touch `api.py`. The mount (`app.mount("/vscode", ...)`) is owned by Phase 3 exclusively, since the manager is constructed in the lifespan there.

## Context
- **Previous phase delivered**: `VSCodeServerManager` with `start()`, `stop()`, `is_running()`, `get_port()`, `get_status()`.
- **Phase 0 spike**: Validated that binary frames, TaskGroup lifecycle, and subprotocols work against real code-server.
- **Current proxy state**: NONE. No reverse proxy, no `httpx` proxying, no WebSocket routes exist anywhere in the codebase.
- **Available tools**: `httpx.AsyncClient` (in deps), `starlette` (via FastAPI), `sse-starlette` (explicit dep), `websockets>=13.0` (added in Phase 0/1).
- **Python**: 3.13+ — `asyncio.TaskGroup` available.
- **R4**: code-server runs with `--auth none` (bound to `127.0.0.1`, proxy is sole access path). No cookie/token injection needed.

## Technical Approach

### The Core Challenge: WebSocket Proxying

VS Code's web UI relies heavily on WebSocket connections for:
- Terminal sessions (**binary** msgpack-RPC frames)
- File watcher notifications
- Extension host communication (**binary** frames)
- Debug adapter protocol

A plain HTTP proxy is insufficient. We need bidirectional WebSocket tunneling with **binary frame support** (C4) and **proper task cancellation** (C5).

#### HTTP Proxy (with streaming body cap — P1/W5)

```python
VSCODE_MAX_BODY_BYTES = 50 * 1024 * 1024  # 50MB hard cap (W5)

async def _read_capped_body(request: Request, max_bytes: int = VSCODE_MAX_BODY_BYTES):
    """P1: Stream body with byte counter — NOT request.body() which materializes all at once."""
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            return None  # caller returns 413
        chunks.append(chunk)
    return b"".join(chunks)

async def proxy_http(request: Request, path: str):
    port = manager.get_port()
    upstream = f"http://127.0.0.1:{port}/{path}"
    
    # P1/W5: Stream body with byte counter — reject >50MB BEFORE full materialization
    body = await _read_capped_body(request)
    if body is None:
        return JSONResponse(status_code=413, content={"error": "Body too large"})
    
    # S4: Forward Origin/Host headers to upstream
    upstream_headers = _build_upstream_headers(request.headers, port)
    
    async with httpx.AsyncClient() as client:
        upstream_req = client.build_request(
            request.method, upstream,
            headers=upstream_headers,
            params=request.query_params,
            content=body,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
        
        return StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            headers=_filter_response_headers(upstream_resp.headers),
        )
```

#### WebSocket Proxy (binary frames + TaskGroup — C4, C5)

```python
@router.websocket("/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    port = manager.get_port()
    upstream_uri = f"ws://127.0.0.1:{port}/{path}"
    
    # W2: Capture subprotocol from browser request
    raw_proto = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [s.strip() for s in raw_proto.split(",") if s.strip()]
    
    await websocket.accept(subprotocol=subprotocols[0] if subprotocols else None)
    
    # R4: No auth headers needed — code-server runs with --auth none (127.0.0.1 only)
    try:
        async with websockets.connect(
            upstream_uri,
            subprotocols=subprotocols or None,  # W2
        ) as upstream:
            # C5: TaskGroup cancels ALL children on first exception
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_browser_to_upstream(websocket, upstream))
                tg.create_task(_upstream_to_browser(upstream, websocket))
    except* (WebSocketDisconnect, websockets.ConnectionClosed):
        pass  # Expected on disconnect — TaskGroup handled cleanup
```

#### Binary Frame Dispatch (C4) — Both Directions

```python
async def _browser_to_upstream(ws: WebSocket, upstream):
    """Pipe browser → code-server. C4: dispatch bytes vs text."""
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            break
        if "bytes" in msg and msg["bytes"] is not None:
            await upstream.send(msg["bytes"])       # binary frame
        elif "text" in msg and msg["text"] is not None:
            await upstream.send(msg["text"])         # text frame

async def _upstream_to_browser(upstream, ws: WebSocket):
    """Pipe code-server → browser. C4: dispatch bytes vs text symmetrically."""
    while True:
        msg = await upstream.recv()
        if isinstance(msg, bytes):
            await ws.send_bytes(msg)                 # binary frame
        else:
            await ws.send_text(msg)                   # text frame
```

### Header Handling (W1, S4)

#### Response Headers: Controlled CSP (W1)

**NOT strip-all.** Replace code-server's CSP with our own controlled policy:

```python
# W1: Our own controlled CSP — NOT strip-all
VSCODE_PROXY_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)

REPLACED_RESPONSE_HEADERS = {
    "content-security-policy": VSCODE_PROXY_CSP,
    "x-frame-options": "SAMEORIGIN",  # Allow same-origin iframe
    "x-content-security-policy": VSCODE_PROXY_CSP,
}

def _filter_response_headers(headers) -> dict:
    """Replace CSP/X-Frame-Options with controlled values (W1)."""
    result = {}
    for k, v in headers.items():
        lower = k.lower()
        if lower in REPLACED_RESPONSE_HEADERS:
            result[k] = REPLACED_RESPONSE_HEADERS[lower]
        else:
            result[k] = v
    # Ensure CSP is set even if upstream didn't send one
    if not any(k.lower() == "content-security-policy" for k in result):
        result["Content-Security-Policy"] = VSCODE_PROXY_CSP
    return result
```

The frontend iframe must also set `sandbox="allow-scripts allow-same-origin allow-forms allow-popups"` (omit `allow-top-navigation`). See Phase 5.

#### Request Headers: Origin/Host Forwarding (S4, P2)

```python
# P2: Complete hop-by-hop header filter per RFC 7230 §6.1
_HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "upgrade", "te",
    "trailer", "transfer-encoding",
    "proxy-authenticate", "proxy-authorization",
})

def _build_upstream_headers(request_headers, port: int) -> dict:
    """Forward headers to code-server upstream (S4). P2: complete hop-by-hop filter + correct Origin."""
    result = {}
    for k, v in request_headers.items():
        lower = k.lower()
        # P2: Skip ALL hop-by-hop headers per RFC 7230 §6.1
        if lower in _HOP_BY_HOP_HEADERS or lower == "host":
            continue
        result[k] = v
    
    # S4/P2: Forward origin with port so code-server doesn't reject
    result["Origin"] = f"http://127.0.0.1:{port}"
    
    return result
```

> **R4 note**: No auth cookie injection needed — code-server runs with `--auth none` (bound to `127.0.0.1`, proxy is sole access path).

### Readiness Gate
Every proxy request checks `manager.is_running()`:
- If not running and editor pref = "vscode" → trigger `manager.ensure_running()` (lazy start), return 503 with Retry-After
- If not running and binary not installed → return 503 with error JSON
- **R4**: No auth token injection needed — code-server runs with `--auth none` (localhost-only binding + proxy is sole access path).

### Factory Function (W3 — router + factory only, NO mount)

Phase 2 delivers ONLY the factory and router. Phase 3 owns the mount.

```python
# daemon/routers/vscode_proxy.py

def create_vscode_proxy_app(manager: VSCodeServerManager) -> FastAPI:
    """Create a FastAPI sub-app for proxying to code-server.
    
    Phase 2 delivers this factory only.
    Phase 3 calls this from lifespan and mounts the result at /vscode.
    """
    app = FastAPI(title="VS Code Proxy")
    
    # HTTP catch-all route
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    async def proxy_http_handler(request: Request, path: str):
        if not manager.is_running():
            ...
        return await _proxy_http(manager, request, path)
    
    # WebSocket route
    @app.websocket("/{path:path}")
    async def proxy_ws_handler(websocket: WebSocket, path: str):
        ...
    
    return app
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Verify `websockets>=13.0` dependency | Added in Phase 0/1; confirm in `pyproject.toml` (N2: `additional_headers=` needs v13+) | `pyproject.toml` |
| 2 | Create proxy router module | `APIRouter`/sub-app with HTTP catch-all + WebSocket endpoint | `daemon/routers/vscode_proxy.py` |
| 3 | Implement HTTP proxy handler | `httpx.AsyncClient` streaming; **P1/W5: streaming body cap via `_read_capped_body()`**; forward query params, filtered headers | `daemon/routers/vscode_proxy.py` |
| 4 | Implement WebSocket proxy with TaskGroup | **C5: `asyncio.TaskGroup()`** for bidirectional pipe; `except*` for graceful disconnect handling | `daemon/routers/vscode_proxy.py` |
| 5 | Implement binary frame dispatch | **C4: `isinstance(msg, bytes)` → `send_bytes()`, else `send_text()`** — both directions | `daemon/routers/vscode_proxy.py` |
| 6 | Implement subprotocol forwarding | **W2: Capture `Sec-WebSocket-Protocol`** from browser, pass to upstream `websockets.connect(subprotocols=...)` | `daemon/routers/vscode_proxy.py` |
| 7 | Implement controlled CSP replacement | **W1: Replace** (not strip) CSP/X-Frame-Options with own controlled policy; set iframe-compatible values | `daemon/routers/vscode_proxy.py` |
| 8 | Implement header forwarding | **S4: Forward Origin with port**; **P2: complete hop-by-hop filter** per RFC 7230 §6.1; **R4: no auth cookie** (code-server `--auth none`) | `daemon/routers/vscode_proxy.py` |
| 9 | Implement readiness gate | Check `manager.is_running()` before forwarding; return 503 + Retry-After if not ready | `daemon/routers/vscode_proxy.py` |
| 10 | Create `create_vscode_proxy_app()` factory | Returns FastAPI sub-app; **W3: router + factory ONLY, no mount** | `daemon/routers/vscode_proxy.py` |
| 11 | Write integration tests | Mock code-server (HTTP+WS, binary+text), verify proxy forwards all correctly; test TaskGroup cleanup; **test streaming body cap (P1)**; **test hop-by-hop filter (P2)** | `tests/integration/test_vscode_proxy.py` |

## Key Files
- `daemon/routers/vscode_proxy.py` — **NEW**: Proxy router + WebSocket handler (~400 lines)
- `pyproject.toml` — **MODIFY**: Ensure `websockets>=13.0` (added in Phase 0/1)
- `tests/integration/test_vscode_proxy.py` — **NEW**: Integration tests
- `daemon/api.py` — **NOT MODIFIED in Phase 2** (W3: mount owned by Phase 3)

## Constraints
- **W3: Do NOT mount in this phase** — Phase 2 delivers factory + router only. Phase 3 owns the mount (since manager is constructed in lifespan there).
- **C4: Binary frames MUST be dispatched correctly** — `isinstance(msg, bytes)` check before `send_bytes()`/`send_text()`. Apply symmetrically both directions.
- **C5: MUST use `asyncio.TaskGroup()`** — NOT `asyncio.gather()`. TaskGroup cancels all children on first exception, preventing connection leaks.
- **W1: Do NOT strip-all CSP** — Replace with controlled policy. Stripping entirely is insecure.
- **W2: MUST forward `Sec-WebSocket-Protocol`** — some code-server features silently fail without correct subprotocol.
- **P1/W5: MUST stream body with byte counter** — `await request.body()` materializes the ENTIRE body into memory before the size check. A 10GB upload allocates 10GB before 413 fires. Use `_read_capped_body()` with `request.stream()` instead.
- **S4/P2: MUST forward Origin with port** — `f"http://127.0.0.1:{port}"`, not just `"http://127.0.0.1"`. Some code-server versions reject unknown origins.
- **P2: Complete hop-by-hop filter** — per RFC 7230 §6.1: `connection`, `keep-alive`, `upgrade`, `te`, `trailer`, `transfer-encoding`, `proxy-authenticate`, `proxy-authorization`.
- **R4: No auth cookie injection** — code-server runs with `--auth none` (bound to `127.0.0.1`, proxy is sole access path). Do NOT inject `Cookie: password=...` or `Cookie: key=...`.
- **No `httpx` for WebSocket** — `httpx` does NOT support WebSocket; use `websockets` library.
- **Proxy must not buffer large responses** — use streaming (`aiter_raw()`) for code-server assets.

## Deliverables
- [ ] HTTP proxy forwarding all methods with streaming + **streaming body cap via `_read_capped_body()`** (P1/W5)
- [ ] WebSocket proxy with **binary frame dispatch** (C4) + **TaskGroup lifecycle** (C5)
- [ ] **Subprotocol forwarding** (W2)
- [ ] **Controlled CSP replacement** (W1) — not strip-all
- [ ] **Origin header with port** + **complete hop-by-hop filter** (S4/P2)
- [ ] **No auth cookie injection** (R4 — code-server `--auth none`)
- [ ] Readiness gate with lazy-start support
- [ ] `create_vscode_proxy_app()` factory (router + factory only, **no mount** — W3)
- [ ] Integration tests with mock code-server (binary + text frames, TaskGroup cleanup, streaming body cap, hop-by-hop filter)

## Testing Strategy

### Integration Tests (Phase 2)
Create a mock "code-server" using Python `websockets` server that:
1. Serves a simple HTML page at `/` with `X-Frame-Options: DENY` header
2. Has a WebSocket endpoint that echoes messages — **both text AND binary**

**Test cases**:
- HTTP GET proxied correctly (status, body, content-type)
- HTTP POST body forwarded correctly
- **P1/W5: Body >50MB rejected with 413** — verify via streaming (not after full materialization)
- **W1: Response CSP replaced** with controlled policy (not stripped, not original)
- **W1: X-Frame-Options replaced** with `SAMEORIGIN`
- **C4: WebSocket binary frames** proxied correctly (send bytes, receive bytes)
- **C4: WebSocket text frames** proxied correctly (send text, receive text)
- **C4: Mixed text+binary** session works
- **C5: WebSocket disconnect** → TaskGroup cancels sibling → upstream closes within 2s
- **W2: Subprotocol** forwarded to upstream correctly
- **S4/P2: Origin header** present in upstream request **with port** (e.g., `http://127.0.0.1:9100`)
- **P2: Hop-by-hop headers stripped** — verify `connection`, `keep-alive`, `upgrade`, `te`, `trailer`, `transfer-encoding`, `proxy-authenticate`, `proxy-authorization` NOT forwarded
- 503 returned when manager not running
- Large response streamed without buffering issues

### Test command
```bash
pytest tests/integration/test_vscode_proxy.py -v
```

### Manual Verification
After automated tests (and after Phase 3 mounts the proxy), manually verify with real `code-server`:
1. Install `code-server` (`brew install code-server` or `curl -fsSL https://code-server.dev/install.sh | sh`)
2. Set editor pref to "vscode" (Phase 3 API)
3. Open `http://localhost:8079/vscode/` in browser
4. Verify VS Code UI renders with **controlled CSP** (check DevTools console for no CSP errors)
5. Open a file, verify it loads
6. **Open a terminal** (`` Ctrl+` ``) — this tests binary WS frames (C4)
7. Run a command in terminal — verify no proxy crash
8. **Close the browser tab** — verify upstream connection closes (C5, check `lsof`)
9. Install the Python extension — tests extension host WS
