# Phase 0: WebSocket Proxy Spike (De-risk)

## Objective
Build a minimal throwaway WebSocket proxy against a **real** `code-server` instance to validate the three highest-risk technical unknowns (C4: binary frames, C5: TaskGroup lifecycle, W2: subprotocols) before committing to full Phase 2 implementation. This phase produces no production code — only a validation script and findings report.

## Coupling
- **Depends on**: None (root, throwaway)
- **Coupling type**: — (standalone spike)
- **Shared files with other phases**: None — spike code is throwaway, lives in a scratch directory
- **Why this coupling**: Phase 2 (full proxy) depends on the spike's findings. If the spike reveals the approach is infeasible, Phase 2 design must change before any production code is written.

## Context
- This is the very first work item. No prior phase deliverables exist.
- WebSocket proxying is **completely novel** to this codebase — all existing real-time communication uses SSE.
- VS Code's web UI uses msgpack-RPC **binary** WebSocket frames for terminal, file content, and extension host communication.
- `code-server` needs to be installed locally for this spike (`brew install code-server` or `curl -fsSL https://code-server.dev/install.sh | sh`).

## Technical Approach

### What the Spike Validates

| Risk ID | What | How Validated |
|---------|------|---------------|
| **C4** | Binary frames don't crash `send_text()` | Open a terminal in VS Code (heavy binary traffic) via the proxy; verify no `TypeError`/`UnicodeDecodeError` |
| **C5** | `asyncio.TaskGroup()` cancels sibling task on disconnect | Close browser tab mid-terminal-session; verify upstream WS connection closes within ~2s (no leak) |
| **W2** | `Sec-WebSocket-Protocol` negotiation works | Check code-server accepts proxied subprotocol headers; verify VS Code connects (some features may silently fail without correct subprotocol) |

### Spike Architecture (throwaway)

```
spike/vscode_ws_spike.py
  ├── Start real code-server manually: code-server --port 9100 --auth none --bind-addr 127.0.0.1
  ├── FastAPI app with:
  │     ├── HTTP proxy: httpx streaming GET/POST
  │     └── WebSocket proxy endpoint: /vscode/{path:path}
  │           ├── Accept browser WS
  │           ├── Connect to upstream via websockets.connect()
  │           ├── asyncio.TaskGroup for bidirectional pipe
  │           │     ├── browser→upstream: dispatch bytes/text
  │           │     └── upstream→browser: dispatch bytes/text
  │           └── On disconnect: TaskGroup cancels all → cleanup
  └── Run: uvicorn spike.vscode_ws_spike:app --port 8079
```

### Minimal WS Proxy Code (spike reference)

```python
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
import httpx

app = FastAPI()
UPSTREAM = "http://127.0.0.1:9100"
WS_UPSTREAM = "ws://127.0.0.1:9100"


@app.api_route("/vscode/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_http(request: Request, path: str):
    async with httpx.AsyncClient() as client:
        upstream_resp = await client.send(
            client.build_request(
                request.method, f"{UPSTREAM}/{path}",
                headers=dict(request.headers),
                content=await request.body(),
            ),
            stream=True,
        )
        return StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            headers=dict(upstream_resp.headers),
        )


@app.websocket("/vscode/ws/{path:path}")
async def proxy_ws(websocket: WebSocket, path: str):
    # Capture subprotocol from browser request (W2)
    subprotocols = websocket.headers.get("sec-websocket-protocol", "").split(",")

    await websocket.accept()
    upstream_uri = f"{WS_UPSTREAM}/{path}"

    try:
        async with websockets.connect(
            upstream_uri,
            subprotocols=[s.strip() for s in subprotocols if s.strip()] or None,
        ) as upstream:
            # C5: TaskGroup cancels ALL children on first exception
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_browser_to_upstream(websocket, upstream))
                tg.create_task(_upstream_to_browser(upstream, websocket))
    except* (WebSocketDisconnect, websockets.ConnectionClosed):
        pass  # Expected on disconnect


async def _browser_to_upstream(ws: WebSocket, upstream):
    """C4: Dispatch bytes vs text."""
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            break
        if "bytes" in msg:
            await upstream.send(msg["bytes"])
        elif "text" in msg:
            await upstream.send(msg["text"])


async def _upstream_to_browser(upstream, ws: WebSocket):
    """C4: Dispatch bytes vs text symmetrically."""
    while True:
        msg = await upstream.recv()
        if isinstance(msg, bytes):
            await ws.send_bytes(msg)
        else:
            await ws.send_text(msg)
```

### Validation Procedure

Run these steps manually with a real code-server:

1. **Setup**:
   ```bash
   # Terminal 1: Start real code-server
   code-server --port 9100 --auth none --bind-addr 127.0.0.1 ~/test-project

   # Terminal 2: Start spike proxy
   cd /path/to/agents-ensemble
   uvicorn spike.vscode_ws_spike:app --port 8079
   ```

2. **Test C4 (binary frames)**:
   - Open `http://localhost:8079/vscode/` in browser
   - Open the integrated terminal (`` Ctrl+` ``)
   - Type commands, run `ls -la`, `cat` a binary file
   - ✅ **Pass**: No `TypeError`/`UnicodeDecodeError` in spike proxy logs
   - ❌ **Fail**: If binary dispatch doesn't work, investigate `websockets` library message format

3. **Test C5 (TaskGroup lifecycle)**:
   - With a terminal session active, close the browser tab
   - Check upstream: `lsof -i :9100 | grep ESTABLISHED`
   - ✅ **Pass**: Upstream WS connection closes within ~2s
   - ❌ **Fail**: If connection persists, TaskGroup exception handling needs adjustment

4. **Test W2 (subprotocols)**:
   - Check browser DevTools → Network → WS connection → Request Headers
   - Verify `Sec-WebSocket-Protocol` forwarded to upstream
   - ✅ **Pass**: VS Code connects without subprotocol errors; extension features work
   - ❌ **Fail**: Some features silently fail → need to investigate code-server's expected subprotocols

5. **Test overall interactivity**:
   - Open a file, edit it, save (tests HTTP proxy + WS sync)
   - Install the Python extension (tests extension host WS)
   - Open a terminal and run a command (tests binary WS frames)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Install code-server locally | `brew install code-server` (macOS) or install script | N/A (system) |
| 2 | Create spike directory + script | Minimal FastAPI app with HTTP + WS proxy | `spike/vscode_ws_spike.py` (throwaway) |
| 3 | Verify `websockets` dependency | `pip show websockets`; add to pyproject if missing | `pyproject.toml` |
| 4 | Run C4 validation (binary frames) | Open terminal in VS Code via proxy; check for crashes | Manual |
| 5 | Run C5 validation (TaskGroup lifecycle) | Disconnect mid-session; check upstream cleanup | Manual |
| 6 | Run W2 validation (subprotocols) | Inspect WS headers; verify feature parity | Manual |
| 7 | Write findings report | Document what worked, what didn't, recommended adjustments for Phase 2 | `spike/SPIKE_FINDINGS.md` (throwaway) |

## Key Files
- `spike/vscode_ws_spike.py` — **NEW (throwaway)**: Spike proxy script
- `spike/SPIKE_FINDINGS.md` — **NEW (throwaway)**: Validation results
- `pyproject.toml` — **MODIFY**: Add `websockets>=13.0` (N2: `additional_headers=` param introduced in v13.0; this carries forward to production)

## Constraints
- **Spike code is throwaway** — do NOT integrate into `daemon/` or production routers
- **Use real code-server** — mocks don't validate binary frames or subprotocol negotiation
- **Time-box to 8 hours** — if the spike reveals fundamental issues, escalate before continuing
- **If C4/C5 fail**: The proxy approach may need redesign (e.g., use a dedicated WS proxy like `wsproxy` or nginx instead of in-process FastAPI). Document alternatives in findings.

## Deliverables
- [ ] code-server installed and running locally
- [ ] Spike proxy script that forwards HTTP + WebSocket to code-server
- [ ] C4 validated: binary frames work through proxy (terminal opens, binary data flows)
- [ ] C5 validated: TaskGroup cancels sibling task on disconnect (no connection leak)
- [ ] W2 validated: subprotocols forwarded correctly (VS Code features work)
- [ ] Findings report documenting any adjustments needed for Phase 2
- [ ] `websockets` dependency added to `pyproject.toml`

## Exit Criteria
**Proceed to Phase 1+2 if**: All three validations pass (or have documented workarounds).
**Stop and redesign if**: Binary frames cannot be proxied, OR TaskGroup doesn't clean up connections, OR code-server rejects proxied subprotocol negotiations entirely.

## Testing Strategy

This phase is entirely manual validation against real code-server. No automated tests — the goal is empirical validation of unknowns.

### Test command
```bash
# Start code-server
code-server --port 9100 --auth none --bind-addr 127.0.0.1 ~/test-project

# Start spike
uvicorn spike.vscode_ws_spike:app --port 8079

# Open in browser
open http://localhost:8079/vscode/
```
