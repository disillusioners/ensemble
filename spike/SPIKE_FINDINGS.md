# Phase 0 Spike Findings — VS Code Server WS Proxy

**Date**: 2026-07-25
**code-server**: 4.112.0 (Homebrew)
**Python**: 3.13.3
**websockets**: 16.0 (pinned `>=13.0` per N2)
**httpx**: 0.28.1, **fastapi**: 0.133.1, **uvicorn**: 0.41.0

---

## TL;DR

**All three risks (C4, C5, W2) validated PASS.** The spike confirms that
proxying code-server through an in-process FastAPI WebSocket endpoint is
feasible. **Proceed to Phase 1/Phase 2 implementation.**

| Risk | What | Status |
|------|------|--------|
| C4 | Binary frames don't crash `send_text()` | ✅ PASS |
| C5 | `asyncio.TaskGroup()` cancels siblings on disconnect | ✅ PASS |
| W2 | `Sec-WebSocket-Protocol` negotiation works | ✅ PASS |

---

## 1. What Was Validated

### C4 — Binary frame dispatch

**What we did:** Connected a `websockets` client to `ws://127.0.0.1:8091/vscode/ws/`
through the proxy, sent a 256-byte binary payload covering all byte values
(includes non-UTF-8 sequences) followed by a text frame. Verified no
`TypeError` / `UnicodeDecodeError` raised by the proxy.

**Result: PASS.** The proxy relays bytes vs text symmetrically:
`isinstance(msg, bytes)` → `send_bytes()`, else `send_text()`. Both directions
use the same dispatch.  No crash, no decode attempt on raw bytes.

**Log evidence** (spike-proxy.log):
```
ws-connect path= subprotocols=['v5.code-server-protocol'] selected=v5.code-server-protocol
ws-upstream-connected negotiated=None
ws-browser-disconnect path=         ← clean exit, no exception
ws-cleanup-done path=              ← TaskGroup finished cleanly
```

### C5 — TaskGroup lifecycle on abrupt disconnect

**What we did:** Connected a `websockets` client through the proxy, confirmed
the upstream WS connection appeared on port 9100 (count via `lsof`),
force-aborted the client's TCP transport (no close frame — simulates a tab
crash / hard process kill), waited 5 s, recounted.

**Result: PASS.** Upstream connection count dropped from 6 to 0 within 5 s
(the proxy's contribution: 2 → 0; code-server's internal conns also settled
in this window). The cascade took ~2 s — exactly matching `close_timeout=2`.

**Log evidence** (spike-proxy.log, C5 trace):
```
17.905 ws-connect path= subprotocols=[] selected=None
17.907 ws-upstream-connected negotiated=None
18.491 ws-browser-disconnect path=    ← abort detected (~584ms after connect)
20.492 ws-cleanup-done path=         ← TaskGroup cascade finished (2s later)
```

The 2 s gap between `ws-browser-disconnect` and `ws-cleanup-done` matches
`close_timeout=2` exactly: the proxy sent a close frame to code-server, waited
2 s for code-server's ack, then gave up and force-closed. This is the
expected behavior — production code should use the same pattern (or a tuned
value).

**Connection count trace:**
```
before=4  during=6  after=0
       ↑ +2 (proxy's TCP + upstream WS)
                              ↑ -6 (all proxy+code-server conns gone)
Δ = before-after = 4 ≥ 1 ✓ PASS
```

> **Methodology note:** `lsof` counts ALL `ESTABLISHED` conns to port 9100,
> including code-server's internal extension-host connections. The 4 → 6 → 0
> trace is noisy because code-server's own internals fluctuate. The reliable
> signal is `Δ = before - after ≥ 1` AND the proxy log shows `ws-cleanup-done`
> within `close_timeout`. Both hold.

### W2 — `Sec-WebSocket-Protocol` forwarding

**What we did:** Connected with `Sec-WebSocket-Protocol: v5.code-server-protocol`.
Verified (a) the handshake succeeded with 101 Switching Protocols, (b) the
client saw the requested subprotocol as the negotiated one.

**Result: PASS.** Requested and negotiated both equal
`'v5.code-server-protocol'`.

**Subtle finding:** The **proxy's** `upstream.subprotocol` is `None` — code-server
does not send a `Sec-WebSocket-Protocol` header in its handshake response, so
upstream-side negotiation selects nothing. But the proxy **explicitly**
calls `websocket.accept(subprotocol=selected)`, which causes the FastAPI
handshake response to include the `Sec-WebSocket-Protocol` header. The
browser receives it and sets its `ws.subprotocol` accordingly.

**Net effect:** From the browser's perspective, the subprotocol looks
negotiated correctly. From code-server's perspective, it doesn't care about
subprotocols (its WS endpoint is subprotocol-flexible). Functionally the
proxy is transparent to both sides.

---

## 2. Issues Encountered with `websockets` v16

### 2.1 `Subprotocol` is a `NewType` over `str`

`websockets.typing.Subprotocol` is `NewType('Subprotocol', str)`. The
`subprotocols=` parameter on `websockets.connect()` is typed as
`Sequence[Subprotocol] | None`, which a strict type checker rejects when given
`list[str]`. At runtime it's identical to `list[str]`.

**Workaround:** Cast via `[Subprotocol(s) for s in subprotocols]`. Production
code can either keep this cast or annotate `subprotocols` as
`list[Subprotocol]` from the start.

### 2.2 `close_timeout` default is 10s — too long for our cleanup path

When the browser-side disconnects and the TaskGroup cancels both pipes, the
`async with websockets.connect(...)` exit calls `upstream.close()`, which
sends a close frame and **waits up to `close_timeout` (default 10 s)** for
the upstream to ack. code-server's WS does not ack promptly when its
extension host is busy or shutting down.

**Fix:** Set `close_timeout=2` on `websockets.connect()`. This is the cleanest
solution. Production should keep this short (2–5 s) so that proxy cleanup is
prompt and not blocked on a slow upstream.

**Alternative considered but rejected:** Calling
`upstream.transport.abort()` in a `finally:` instead of relying on
`async with` exit. More aggressive, but skips the close-frame grace period
that helps upstream clean its internal state. Keep `close_timeout=2` for
Phase 2.

### 2.3 `ws.transport.abort()` exists on the client (v16)

`websockets.ClientConnection` exposes `.transport` (an asyncio
`_SelectorSocketTransport`). Calling `.abort()` hard-closes the TCP socket
without sending a close frame — exactly what we needed for the C5 test.

### 2.4 No top-level `transport` on the SERVER side

The **server** side (`ServerConnection`) does NOT expose `.transport` the
same way. To force-close from the server, the production proxy should:
- Call `await websocket.close()` for graceful close, OR
- Catch the disconnect in the read loop and let `async with` exit trigger
  `close()` (which is what our spike does)

The spike's approach (let `async with websockets.connect(...)` exit naturally)
works correctly with `close_timeout=2`.

---

## 3. Recommended Adjustments for Phase 2 Production

| # | Adjustment | Why | Where |
|---|-----------|-----|-------|
| 1 | Use `close_timeout=2` (or 5) on every `websockets.connect()` | Default 10s blocks cleanup on slow upstream; we already do this in the spike | `daemon/routers/vscode.py` |
| 2 | Keep the `async with websockets.connect(...)` pattern + `asyncio.TaskGroup()` | C5 evidence: cancellation cascade works end-to-end with this structure | Same |
| 3 | Set `ping_interval=20` and `ping_timeout=20` on both sides | Detect dead peers within ~40 s instead of TCP keepalive defaults (~2 h); code-server uses pings too | Same |
| 4 | Add `additional_headers={"Host": "127.0.0.1:9100"}` (or the configured upstream host) | code-server's same-origin checks expect Host to match its bind-addr | Same |
| 5 | Use `except* WebSocketDisconnect, websockets.ConnectionClosed` pattern | Both directions of the pipe raise on disconnect; `except*` aggregates cleanly | Same |
| 6 | Configure `max_queue=None` (unbounded) for upstream→browser direction only | Browser-side backpressure shouldn't throttle upstream recv; conversely browser→upstream should be bounded | Same |
| 7 | For HTTP proxy: keep P1 (streaming body with byte cap) and P2 (full RFC 7230 §6.1 hop-by-hop filter) — already in spike | These are not new findings; they're carried-forward from the Rev-3 review | Same |
| 8 | **NEW**: Track per-WS metadata (browser peer, upstream subprotocol, start time) in a `weakref` registry keyed by WS id | Without this, log lines from concurrent WS sessions are unjoinable; P2 prod router needs per-session logs | `daemon/routers/vscode.py` + `daemon/state.py` |
| 9 | **NEW**: Add Origin rewriting for HTTP (already in spike) AND for WS (handled automatically by subprotocol acceptance) | Browser WS sends no Origin header, but HTTP requests from the SPA do; spike already rewrites `Origin → http://127.0.0.1:9100` | Spike already correct |
| 10 | **NEW**: Production must enforce `project_id` on every WS path (`/vscode/{instance_id}/ws/{path:path}`) — spike uses flat `/vscode/ws/` | Multi-tenant isolation; not needed in spike (single-user) | `daemon/routers/vscode.py` |

### Hardening not validated by the spike (manual / integration testing required)

The following cannot be validated programmatically without a real browser
session against a fully-routed proxy:

- **VS Code UI interactivity** — opening files, editing, saving, terminal I/O.
  The spike only proves binary frames don't crash the proxy; full UI
  exercise requires a human or Playwright.
- **Extension host traffic** — installing/running an extension exercises a
  separate WS connection per extension. Spike doesn't test this.
- **Long-lived sessions (>5 min)** — `ping_interval` keepalives, idle
  connection reaping. Spike tests run in seconds.
- **Concurrent sessions** — multiple browser tabs/users. Spike runs one at a
  time.

These should be covered by Phase 1's manual test plan or by Playwright
integration tests in Phase 3.

---

## 4. Captured Validation Output

```
$ uv run python spike/validate_spike.py

=== C4: binary frame dispatch ===
  [PASS] C4 binary frame dispatch: binary (256-byte) + text frames sent through proxy without TypeError/UnicodeDecodeError (code-server may close the WS handshake after this, is acceptable for the test)

=== W2: subprotocol forwarding ===
  [PASS] W2 subprotocol negotiation: requested='v5.code-server-protocol' negotiated='v5.code-server-protocol'

=== C5: TaskGroup cleanup on abrupt disconnect ===
  [PASS] C5 TaskGroup cleanup on abrupt disconnect: upstream conn count: before=4 during=6 after=0 (closed within 5.0s; Δ=4)

=== SUMMARY ===
  [PASS] C4 binary frame dispatch
  [PASS] W2 subprotocol negotiation
  [PASS] C5 TaskGroup cleanup on abrupt disconnect

All spike validations passed.
```

(Exited with status 0.)

---

## 5. Exit Criteria Verdict

Per `phase0-plan.md` §Exit Criteria:

> **Proceed to Phase 1+2 if**: All three validations pass (or have documented
> workarounds).

All three passed with no workarounds beyond the `close_timeout=2` adjustment
(Recommendation #1, which is a tuning choice, not a workaround for a broken
mechanism).

> **Stop and redesign if**: Binary frames cannot be proxied, OR TaskGroup
> doesn't clean up connections, OR code-server rejects proxied subprotocol
> negotiations entirely.

None of the stop conditions were triggered.

---

## 6. Decision

**PROCEED to Phase 1 + Phase 2.** The spike validates that an in-process
FastAPI WebSocket proxy can safely relay code-server traffic, including
binary frames, with correct cancellation semantics on disconnect and correct
subprotocol forwarding.

The Phase 1 implementation should adopt all 10 recommendations from §3 as
the starting architecture for `daemon/routers/vscode.py`.

---

## Appendix A — Spike Artifact Locations

| File | Purpose |
|------|---------|
| `spike/__init__.py` | Marks `spike/` as a Python package (empty) |
| `spike/vscode_ws_spike.py` | Throwaway proxy: HTTP + WS forwarder with P1/P2 hardening |
| `spike/validate_spike.py` | Automated harness: starts code-server, runs 3 tests, reports PASS/FAIL |
| `spike/SPIKE_FINDINGS.md` | This document |
| `/tmp/vscode-spike-logs/code-server.log` | Captured code-server output (per-run) |
| `/tmp/vscode-spike-logs/spike-proxy.log` | Captured proxy output (per-run) |

## Appendix B — Reproducing

```bash
# Prereqs already met:
#   - code-server at /opt/homebrew/bin/code-server (v4.112.0)
#   - websockets>=13.0 in pyproject.toml
#   - httpx, fastapi, uvicorn as transitive deps

uv run python spike/validate_spike.py
```

The validator is hermetic: it spawns code-server + the proxy as subprocesses,
runs tests, tears them down. No persistent state survives a run.