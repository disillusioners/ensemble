# Architecture Decisions: VS Code Server Editor Integration

> **Revision 2** — Updated D5 (TaskGroup), D6 (controlled CSP). Added D11-D17 covering security (C1/C2), WebSocket binary/subprotocol/body-cap (C4/W2/W5), mount ownership (W3), and frontend fixes (C3/S6).

## D1: `code-server` vs VS Code Tunnel

**Decision**: Use `code-server` (Coder's open-source VS Code in browser).

**Rationale**:
- Self-contained HTTP server we fully control
- Works offline/air-gapped (no Microsoft tunnel dependency)
- Embeddable in iframe behind our reverse proxy (VS Code Tunnel URLs are external)
- Full control over WebSocket proxying, port allocation, and auth
- Single binary dependency, no external accounts

**Rejected**: VS Code built-in tunnel/server feature — requires internet, Microsoft account, uncontrollable WebSocket endpoints, and tunnel URLs that can't be proxied through our FastAPI app.

---

## D2: Port Strategy — Single Shared Instance

**Decision**: One shared `code-server` instance (one OS-assigned port, bound to `127.0.0.1`) for all projects. Project switching via `?folder=` URL parameter (postMessage as enhancement).

**Rationale**:
- Each code-server instance uses ~200-400MB RAM — per-project would be wasteful
- OS-assigned port (`--bind-addr 127.0.0.1:0`) avoids port-range management and conflicts
- Actual port parsed from code-server stdout ("HTTP server listening on...")

**Rejected**: One code-server per project — too resource-heavy, complex lifecycle for multiple processes, and the user typically works on one project at a time in the workspace overlay.

---

## D3: Auto-start Strategy — Lazy Start

**Decision**: Code-server starts lazily on first request when editor preference = "vscode". No auto-start on daemon boot.

**Rationale**:
- Avoids wasting resources when feature is unused
- `PUT /api/settings/editor` with value "vscode" triggers `manager.ensure_running()`
- First `/vscode/*` request also triggers lazy start if manager is stopped but pref = "vscode"
- Daemon restart respects stored preference but does NOT auto-start (waits for user action)

---

## D4: Reverse Proxy Architecture — Sub-application Mount

**Decision**: Mount a FastAPI sub-application at `/vscode` using `app.mount()`, before the catch-all SPA route. Update the catch-all guard to exclude the `vscode` prefix.

**Rationale**:
- Mirrors the existing MCP KB mount pattern (`api.py:1382-1387`)
- Clean separation: all `/vscode/*` routing handled by the proxy sub-app
- Does not interfere with existing API routes (`/api/*`)
- **S1**: Catch-all guard update is required — Starlette mount prefix matching does NOT match `/vscodefoo` to the `/vscode` mount

**W3 Ownership**: Phase 2 delivers the factory + router only. Phase 3 owns the `app.mount()` exclusively (since the manager is constructed in the lifespan there).

**Implementation**:
```python
# In lifespan (Phase 3), after manager init:
vscode_app = create_vscode_proxy_app(vscode_manager)
app.mount("/vscode", vscode_app)

# Catch-all guard update (api.py:1405):
# S1: 'vscode' prefix required — prevents /vscodefoo from hitting SPA fallback
if path.startswith('api') or path.startswith('ws') or path.startswith('vscode'):
    return JSONResponse(status_code=404, content={"error": "Not found"})
```

---

## D5: WebSocket Proxy — `websockets` Library + TaskGroup + Binary Frames

**Decision**: Use the `websockets` Python library with `asyncio.TaskGroup()` for lifecycle management and `isinstance(msg, bytes)` dispatch for binary frames.

**Rationale**:
- `httpx` does NOT support WebSocket protocol — it's HTTP-only
- `websockets` is the standard async WebSocket client library for Python
- **C5 (TaskGroup)**: `asyncio.gather(a, b)` does NOT cancel siblings on failure → connection leaks. `TaskGroup()` (Python 3.11+, project uses 3.13+) cancels ALL children on first exception.
- **C4 (binary frames)**: VS Code uses msgpack-RPC binary frames for terminal, file content, extension host. `send_text(data)` raises `TypeError` on bytes. Must dispatch on `isinstance(msg, bytes)` → `send_bytes()`, else `send_text()`.
- **W2 (subprotocols)**: Forward `Sec-WebSocket-Protocol` from browser to upstream — some code-server features silently fail without correct subprotocol.

**Implementation pattern** (Rev 2):
```python
@router.websocket("/{path:path}")
async def proxy_ws(websocket: WebSocket, path: str):
    # W2: Capture subprotocol
    raw_proto = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [s.strip() for s in raw_proto.split(",") if s.strip()]
    await websocket.accept(subprotocol=subprotocols[0] if subprotocols else None)
    
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/{path}",
            subprotocols=subprotocols or None,  # W2
            # R4: No additional_headers needed — code-server runs --auth none
        ) as upstream:
            # C5: TaskGroup cancels ALL children on first exception
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_browser_to_upstream(websocket, upstream))
                tg.create_task(_upstream_to_browser(upstream, websocket))
    except* (WebSocketDisconnect, websockets.ConnectionClosed):
        pass  # Expected on disconnect

# C4: Binary frame dispatch — both directions
async def _browser_to_upstream(ws, upstream):
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect": break
        if "bytes" in msg: await upstream.send(msg["bytes"])    # binary
        elif "text" in msg: await upstream.send(msg["text"])    # text

async def _upstream_to_browser(upstream, ws):
    while True:
        msg = await upstream.recv()
        if isinstance(msg, bytes): await ws.send_bytes(msg)     # binary
        else: await ws.send_text(msg)                            # text
```

---

## D6: iframe Embedding — Controlled CSP Replacement + Sandbox

**Decision** (Rev 2): The proxy **replaces** (not strips) code-server's CSP with our own controlled policy. The frontend iframe adds a `sandbox` attribute.

**Rationale**:
- code-server sets CSP/X-Frame-Options that block iframe embedding
- **W1**: Stripping ALL CSP is insecure — it disables protections for the entire proxy. Instead, set our own controlled policy that allows what VS Code needs.
- Defense-in-depth: proxy CSP + iframe sandbox attribute together

**Proxy CSP replacement**:
```python
VSCODE_PROXY_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)
# Replace in response headers — NOT strip
```

**Frontend iframe sandbox** (Phase 5):
```html
<!-- W1: sandbox — omit allow-top-navigation -->
<iframe
  sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
  allow="clipboard-read; clipboard-write; fullscreen"
/>
```

---

## D7: Editor Preference Storage — Metadata KV

**Decision**: Store editor preference in `project_metadata_records` table against `SYSTEM_DEFAULT_PROJECT_ID`, following the exact language preference pattern.

**Rationale**:
- No new DB table or migration needed
- Follows established global-preference pattern
- `set_metadata()` / `get_metadata_record()` already support this
- Key: `EDITOR_METADATA_KEY = "editor_preference"`, values: `"builtin"` | `"vscode"`

**R1/R2 Implementation Note**: 
- **R1**: The model attribute is `record.meta_value`, NOT `record.metadata_value` (confirmed in `models.py:185`).
- **R2**: `set_metadata()` signature is `set_metadata(project_id, key, value)` with NO session param — it opens its own `Session` internally. Mirror `settings.py:58-59` — do NOT wrap in `Session(repo.engine)`.

**Rejected**: Dedicated settings table — overkill for a single preference; the metadata KV pattern is proven and simple.

---

## D8: Frontend Editor Switching — @switch Directive

**Decision**: Use Angular's `@switch` control flow in the workspace template to render either `app-code-viewer` or `app-vscode-viewer` based on `editorMode` signal.

**Rationale**:
- Clean separation — only one editor rendered at a time
- CodeMirror state preserved in `editStateMap` when not rendered (no data loss)
- Follows existing `viewMode` signal pattern (code/diff toggle)
- No complex teardown needed — `@switch` removes the unused component from DOM

---

## D9: Project Switching in VS Code — URL Parameter First

**Decision**: Use `?folder=<validated-path>` URL parameter as primary method for opening project folders in the VS Code iframe. postMessage as secondary enhancement.

**Rationale**:
- URL parameter is universally supported by code-server
- postMessage support varies by code-server version and may not be documented
- URL parameter causes iframe reload (~2-3s) but is 100% reliable
- postMessage allows folder switching without reload (smoother UX) — add as enhancement once basic flow works
- **C2**: The folder path comes from the pre-validated `/api/projects/{id}/vscode-folder` endpoint, never from raw `main_directory`

---

## D10: Crash Recovery — PID File

**Decision**: Write a PID file (`data/vscode-server.pid`) on process start. On manager init, check for stale PID and clean up.

**Rationale**:
- Process registry is in-memory only (same as `proc_tools.py`)
- If daemon crashes, the code-server process becomes orphaned
- PID file allows detection and cleanup on next daemon start
- **S3 limitation**: `killpg` may not reach all detached child processes (language servers, extension hosts that call `setsid()`). This is an accepted, documented limitation.

---

## D11: Security Boundary — Localhost Binding (C1, W4, R4)

**Decision** (Rev 3): code-server is ALWAYS launched with `--bind-addr 127.0.0.1:0` + `--auth none`. The `127.0.0.1` binding + proxy-as-sole-access-path is the security boundary, NOT code-server's auth.

**R4 rationale for `--auth none`**:
- code-server is bound to `127.0.0.1` (not reachable externally)
- Our reverse proxy is the SOLE access path — it controls all access
- Cookie/token management with code-server v4.x is problematic: `--auth password` issues a `key` session cookie after `POST /login`, NOT a `password=` cookie. Injecting `Cookie: password={token}` caused 401 on every proxied request.
- `--auth none` eliminates cookie/token management complexity entirely
- Phase 0 spike already uses `--auth none`

**Config**:
```yaml
vscode:
  allow_remote: false  # C1: default to localhost-only
```

**Rev 2 (superseded)**: Previously used `--auth password` + `secrets.token_hex(16)` generated token + proxy cookie injection. This was rejected in Rev 3 due to the code-server v4.x `key` session cookie mismatch (R4).

---

## D12: Path Injection Prevention (C2, R3, N1)

**Decision** (Rev 3): Never pass `main_directory` directly to code-server. Use the existing `WorkspaceGuard.resolve_strict()` for all path validation — NOT a custom validator.

**R3 rationale**: The previous custom `_validate_vscode_folder()` rejected `..`, root `/`, symlinks, and non-existent dirs — but **accepted `/etc`, `/root`, `/var/log`** because it had NO containment check. This was a security regression. `WorkspaceGuard.resolve_strict()` already handles `..`, symlinks, AND containment within the allowed root.

**N1 rationale**: Zero references to the existing `WorkspaceGuard` in all 8 plan files. The plan reinvented a weaker validator. Use the battle-tested existing code.

**Implementation**:
```python
from daemon.services.workspace_guard import WorkspaceGuard

# In endpoint:
guard = WorkspaceGuard(project.main_directory)
resolved, error = guard.resolve_strict(project.main_directory)  # enforces containment
if error:
    raise HTTPException(403, f"Path outside allowed root: {error}")
```

**Rev 2 (superseded)**: Previously used a custom `_validate_vscode_folder()` function with `os.path.realpath()` + manual checks. This was replaced in Rev 3 because it lacked containment enforcement (R3).

---

## D13: WebSocket Binary Frame Handling (C4)

**Decision**: The WebSocket proxy dispatches on `isinstance(msg, bytes)` to route binary frames to `send_bytes()` and text frames to `send_text()`, symmetrically in both directions.

**Rationale**:
- VS Code uses msgpack-RPC **binary** frames for terminal, file content, extension host
- `send_text(data)` raises `TypeError`/`UnicodeDecodeError` when called with bytes
- The dispatch must be applied in both the browser→upstream and upstream→browser directions

---

## D14: WebSocket Connection Lifecycle — TaskGroup (C5)

**Decision**: Use `asyncio.TaskGroup()` (Python 3.11+) for the bidirectional WebSocket pipe instead of `asyncio.gather()`.

**Rationale**:
- `asyncio.gather(a, b)` does NOT cancel sibling tasks when one raises — the other keeps running, leaking the upstream connection indefinitely
- `asyncio.TaskGroup()` cancels ALL children on first exception — when the browser disconnects, both pipe tasks are cancelled and the upstream connection closes cleanly
- Project uses Python 3.13+, so TaskGroup is available
- `except*` syntax handles the expected exception groups from disconnect

---

## D15: Body DoS Prevention — Streaming (W5, P1)

**Decision** (Rev 3): The proxy enforces a 50MB hard cap on request bodies via **streaming byte counter**, NOT `request.body()` which materializes the entire body first.

**P1 rationale**: 
- `await request.body()` loads the ENTIRE upload into memory BEFORE the size check fires
- A 10GB upload allocates 10GB before 413 returns
- The fix streams the body in chunks, checking size incrementally — rejects >50MB without allocating the full body

**Implementation**:
```python
async def _read_capped_body(request, max_bytes=50_000_000):
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            return None  # caller returns 413
        chunks.append(chunk)
    return b"".join(chunks)
```

---

## D16: postMessage Target Origin (C3)

**Decision**: The frontend `postMessage` call uses `window.location.origin` as the targetOrigin, NOT a relative path.

**Rationale**:
- HTML spec requires `targetOrigin` to be an absolute URL
- `'/vscode/'` is a relative path — browsers silently drop the message
- `window.location.origin` is correct because the iframe loads `/vscode/` which is same-origin (proxied by our FastAPI)

---

## D17: Frontend Signal Inputs (S6)

**Decision**: `VsCodeViewerComponent` uses Angular `input()` signal functions for `projectId` and `workdir`, NOT `@Input()` decorators.

**Rationale**:
- `@Input()` is decorator-based and NOT tracked by `computed()` or `effect()` — the `iframeUrl` computed would miss workdir changes
- `input<string>()` returns a signal that IS tracked — changes propagate reactively
- Debounce rapid changes (300ms) to prevent iframe thrashing on rapid project switches
- Null-guard for system default project (no real directory)

---

## Summary Decision Matrix

| ID | Decision | Choice | Impact |
|----|----------|--------|--------|
| D1 | Code editor server | `code-server` binary | Self-contained, offline, iframe-embeddable |
| D2 | Port strategy | Single shared, OS-assigned, `127.0.0.1` | Low resource, simple lifecycle |
| D3 | Start strategy | Lazy on first request | No wasted resources |
| D4 | Proxy architecture | Sub-app mount at `/vscode` + catch-all guard (S1) | Clean routing, mirrors MCP pattern |
| D5 | WebSocket proxy | `websockets>=13.0` + **TaskGroup** (C5) + **binary dispatch** (C4) + **subprotocols** (W2) | Correct lifecycle + binary support |
| D6 | iframe CSP | **Controlled CSP replacement** (W1) + sandbox attr | Secure embedding, not strip-all |
| D7 | Preference storage | Metadata KV (existing table) + **R1: `meta_value`** + **R2: no session** | No migration needed |
| D8 | Frontend switching | Angular `@switch` directive | Clean, state-preserving |
| D9 | Project switching | `?folder=` URL param with **validated path** (C2) | Reliable, universal support |
| D10 | Crash recovery | PID file | Best-effort orphan cleanup |
| D11 | Security: binding | **`127.0.0.1` + `--auth none`** (R4: proxy is sole access path) | No unauthenticated RCE |
| D12 | Security: path injection | **`WorkspaceGuard.resolve_strict()`** (R3/N1) — not custom validator | No filesystem traversal, containment enforced |
| D13 | WS: binary frames | **`isinstance` dispatch** (C4) → `send_bytes`/`send_text` | No crash on binary data |
| D14 | WS: lifecycle | **TaskGroup** (C5) — not gather | No connection leaks |
| D15 | HTTP: body cap | **Streaming `_read_capped_body()`** (P1) → 413 | No memory exhaustion (real, not decorative) |
| D16 | FE: postMessage origin | **`window.location.origin`** (C3) — absolute URL | Messages not silently dropped |
| D17 | FE: signal inputs | **`input()` signals** (S6) — not `@Input`; **clear timer in ngOnDestroy** (N3) | Reactive workdir tracking, no timer leak |

## Review Item Cross-Reference

| Review ID | Type | Decision/Phase | Status |
|-----------|------|----------------|--------|
| C1 | Critical — auth boundary | D11, Phase 1, Phase 2 | ✅ Fixed (R3: `--auth none` justified by localhost binding) |
| C2 | Critical — path injection | D12, Phase 3, Phase 5 | ✅ Fixed (R3/N1: WorkspaceGuard.resolve_strict) |
| C3 | Critical — postMessage origin | D16, Phase 5 | ✅ Fixed |
| C4 | Critical — binary frames | D13, Phase 2 (Phase 0 validates) | ✅ Fixed |
| C5 | Critical — TaskGroup lifecycle | D14, Phase 2 (Phase 0 validates) | ✅ Fixed |
| C6 | Critical — repo signature | D7, Phase 3 | ✅ Fixed (R1: `meta_value`, R2: no session param) |
| W1 | Warning — CSP stripping | D6, Phase 2 (proxy) + Phase 5 (sandbox) | ✅ Fixed |
| W2 | Warning — subprotocols | D5, Phase 2 (Phase 0 validates) | ✅ Fixed |
| W3 | Warning — mount ownership | D4, Phase 2 (factory only) + Phase 3 (mount) | ✅ Fixed |
| W4 | Warning — code-server binding | D11, Phase 1 | ✅ Fixed |
| W5 | Warning — body DoS | D15, Phase 2 | ✅ Fixed (P1: streaming byte counter, not request.body()) |
| S1 | Suggestion — catch-all guard | D4, Phase 3 | ✅ Fixed |
| S4 | Suggestion — Origin/Host forwarding | Phase 2 | ✅ Fixed (P2: port included + complete hop-by-hop filter) |
| S6 | Suggestion — signal input | D17, Phase 5 | ✅ Fixed |
| R1 | Regression — `metadata_value` | D7, Phase 3 | ✅ Fixed (`meta_value`) |
| R2 | Regression — `set_metadata` session | D7, Phase 3 | ✅ Fixed (no session param) |
| R3 | Regression — path containment | D12, Phase 3 | ✅ Fixed (WorkspaceGuard.resolve_strict) |
| R4 | Regression — wrong auth cookie | D11, Phase 1, Phase 2 | ✅ Fixed (--auth none) |
| P1 | Partial — decorative body cap | D15, Phase 2 | ✅ Fixed (streaming) |
| P2 | Partial — Origin port + hop-by-hop | Phase 2 | ✅ Fixed |
| N1 | New — WorkspaceGuard ignored | D12, Phase 3 | ✅ Fixed (R3) |
| N2 | New — websockets version pin | Phase 0, Phase 2 | ✅ Fixed (`>=13.0`) |
| N3 | New — debounce timer leak | D17, Phase 5 | ✅ Fixed (ngOnDestroy clears timer) |
| W6 | Warning — CORS wildcard | — | ⏳ Deferred (pre-existing) |
| S2 | Suggestion — WS compression | Phase 5 notes | ⏳ Deferred (monitor) |
| S3 | Suggestion — orphan risk | D10 | ⏳ Deferred (documented) |
| S5 | Suggestion — user-data-dir | — | ⏳ Deferred (documented) |
