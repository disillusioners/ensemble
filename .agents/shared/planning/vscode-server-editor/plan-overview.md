# Plan Overview: VS Code Server Editor Integration

> **Revision 2** — Incorporated security review (6 critical blockers: C1-C6) and actionable warnings (W1-W5, S1-S6). Added Phase 0 spike. Updated effort estimate.
>
> **Revision 3** — Fixed 4 regressions (R1: `meta_value`, R2: `set_metadata` no session, R3: WorkspaceGuard instead of custom validator, R4: `--auth none`), 2 partial fixes (P1: streaming body cap, P2: Origin port + hop-by-hop), 3 new findings (N1/N2/N3).

## Objective
Allow users to choose between the built-in CodeMirror editor and a VS Code Server (`code-server`) web UI via Settings → Editor. When VS Code is selected, the backend spawns a `code-server` process (bound to `127.0.0.1` with `--auth none` — proxy is sole access path) and proxies `/vscode/*` (HTTP + WebSocket) to it; the frontend renders the VS Code web UI inside an iframe embedded in the existing workspace overlay.

## Scope Assessment
**LARGE** — spans backend (process manager, reverse proxy with WebSocket tunneling, settings API, lifecycle wiring, security hardening), frontend (settings UI, iframe component, editor switching, project sync), and DevOps (code-server binary lifecycle, port allocation, crash recovery). Involves new WebSocket proxying capability (none exists today), a new long-lived process supervisor, and a new sub-application mount — all novel to the codebase.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Backend**: FastAPI 0.115.6 + uvicorn (port 8079), SSE-based (no existing WebSocket routes)
- **Frontend**: Angular 21.2.5, CodeMirror 6 workspace editor
- **DB**: PostgreSQL primary (dual SQLite/PostgreSQL support required for migrations)
- **Python**: 3.13+ (enables `asyncio.TaskGroup`)

## Architecture Decision: `code-server` vs VS Code Tunnel

**Decision: Use `code-server` (Coder's open-source VS Code in browser).**

| Criterion | `code-server` | VS Code Tunnel (built-in) |
|---|---|---|
| Self-contained HTTP server | ✅ Yes — standalone binary | ❌ Relies on Microsoft tunnel service |
| Embeddable in iframe | ✅ With proxy CSP rewrite (proxy is sole access path) | ⚠️ Tunnel URLs are on `vscode.dev`/`*.dev tunnels.dev` |
| WebSocket proxy control | ✅ Full — we proxy everything | ❌ Microsoft relays WS |
| Offline / air-gapped | ✅ Works fully offline | ❌ Requires internet |
| Dependency surface | Binary + our process manager | Microsoft account, tunnel daemon |
| Port allocation | ✅ We control via `--port` | ❌ Random tunnel port |
| Multi-project workspaces | ✅ One process, `--folder-uri` per project | ⚠️ Complex |

**Rationale**: `code-server` gives full control over lifecycle, port, auth, and proxying — essential for embedding in an iframe behind our reverse proxy. VS Code Tunnel introduces external dependencies and uncontrolled WebSocket endpoints that can't be proxied through our FastAPI app.

**Port strategy**: Single shared `code-server` instance (one OS-assigned port, bound to `127.0.0.1`), with project switching via `?folder=` URL parameter (postMessage as enhancement). One-per-project was rejected as too resource-heavy (each code-server instance uses ~200-400MB RAM).

**Auto-start**: Lazy start on first request (when editor pref = "vscode" and user opens workspace). No auto-start on daemon boot — avoids wasting resources when feature is unused.

**Security model**: code-server always binds `127.0.0.1` with `--auth none` (R4: proxy is sole access path — `127.0.0.1` binding + proxy controls all access). The proxy sets a controlled CSP and adds an iframe `sandbox` attribute. See D11 (Security Boundary) in `decisions.md`.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 0 | WebSocket Proxy Spike | De-risk WS proxy against real code-server — validate binary frames, TaskGroup lifecycle, subprotocols | None | — (root, throwaway) | 6-8h |
| 1 | Backend Process Manager | `VSCodeServerManager` service — spawn (127.0.0.1 + `--auth none`) / track / health-check / stop | None | — (root) | 8h |
| 2 | Reverse Proxy (HTTP + WebSocket) | `/vscode/*` proxy with binary-frame WS, TaskGroup lifecycle, body cap, controlled CSP | Phase 1 | tight | 12h |
| 3 | Settings API & Lifecycle Wiring | Editor preference endpoint + path validation + lifespan integration + owns proxy mount | Phase 1, 2 | loose | 6h |
| 4 | Frontend Settings UI & Editor Preference | Settings page editor section + SettingsService methods + header menu entry | Phase 3 | loose | 4h |
| 5 | VS Code iframe Component & Editor Switching | `VsCodeViewerComponent` (signal inputs, postMessage with correct origin) + workspace editor-mode toggle | Phase 4 | tight | 6-8h |

**Total estimated time**: ~42-44 hours (5-6 developer-days)

> Previous estimate was 28h. Increased due to: (1) security hardening (C1-C2), (2) WebSocket complexity (C4-C5, binary frames + TaskGroup), (3) Phase 0 spike, (4) path validation endpoint (C2).

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|---|---|---|
| 0 → 2 | **de-risks** | Spike validates WS approach before full implementation; Phase 2 can proceed with confidence |
| 1 → 2 | **tight** | Proxy needs the manager's port + readiness state to forward requests |
| 1 → 3 | **tight** | Settings API calls manager.start()/stop(); lifespan wires manager + owns proxy mount |
| 2 → 3 | **loose** | Phase 2 delivers factory + router only; Phase 3 owns the mount exclusively (W3 fix) |
| 3 → 4 | **loose** | Frontend only needs the REST API contract; can mock during dev |
| 4 → 5 | **tight** | Editor switching depends on settings service state; iframe needs active proxy URL |

### Parallelism Opportunities
- **Phase 0 and Phase 1** can run in parallel — spike doesn't need the full manager (can mock port), and the manager doesn't need the proxy.
- **Phases 4 & 5 frontend** can partially overlap: Phase 4 (settings UI) and Phase 5 (iframe component) touch different components, but Phase 5's editor-switching reads settings from Phase 4's service. Recommend sequential with overlap in final integration.
- **Phase 3** spans both backend (API + wiring) and is the integration seam. Must complete before frontend phases can do end-to-end testing.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Unauthenticated RCE via exposed code-server** (C1) | **🔴 critical** | code-server always binds `127.0.0.1` (`--bind-addr 127.0.0.1:0`), `--auth none` (R4: proxy is sole access path), `vscode.allow_remote: false` config default, proxy controls all access |
| **Path injection — entire filesystem exposed** (C2) | **🔴 critical** | Server-side path validation on project creation + dedicated `/api/projects/{id}/vscode-folder` endpoint returning pre-validated path; reject `..`, symlinks outside root |
| WebSocket binary frames crash proxy (C4) | **🔴 critical** | Dispatch on `isinstance(msg, bytes)` → `send_bytes()`, else `send_text()`. Validated in Phase 0 spike |
| WebSocket connection leak on disconnect (C5) | **high** | Use `asyncio.TaskGroup()` (3.11+, project uses 3.13+) — cancels all children on first exception |
| WebSocket proxying through FastAPI/uvicorn unproven | **high** | Phase 0 spike validates against real code-server BEFORE committing to full implementation |
| `code-server` binary not installed on user's machine | **med** | Health check returns clear error; frontend shows install instructions + download link; config field for custom binary path |
| code-server iframe CSP blocks embedding (W1) | **high** | Proxy sets OWN controlled CSP (not strip-all) + iframe `sandbox` attribute. See W1 in `decisions.md` |
| Process orphaned after daemon crash (in-memory registry only) | **med** | PID file on disk; on startup, check PID file and kill stale process. Documented limitation: `killpg` may not reach all detached children (S3) |
| Port conflict between code-server allocation and other services | **low** | Use OS-assigned port (`--port 0`) then read actual port from stdout; exclude daemon port 8079/8088 |
| Angular effect dependency-tracking bugs (workspace stuck bug history) | **med** | Read all dependent signals unconditionally before any if-branch in `effect()`; follow established `tabWorkspaceEffect` pattern |
| Body DoS — large upload loaded into memory (W5) | **med** | 50MB hard cap at proxy entry; stream with byte counter; reject with 413 |
| CORS `allow_origins=["*"]` + `allow_credentials=True` (W6) | **low** | Pre-existing issue, NOT introduced by this feature. Deferred |
| WS compression CPU usage (S2) | **low** | Monitor; consider `compression=None` on upstream leg if hot |
| Per-project user-data-dir disk cost (S5) | **low** | Documented tradeoff; shared dir for MVP |

## Deferred Items (noted, not blocking)

| ID | Item | Action |
|----|------|--------|
| W6 | CORS `allow_origins=["*"]` + `allow_credentials=True` | Pre-existing; track for future hardening |
| S2 | WS compression CPU usage | Monitor; add `compression=None` if profiling shows it's hot |
| S3 | Residual orphan risk from `killpg` | Document in code comments; detached children may survive |
| S5 | Per-project user-data-dir tradeoff | Document disk cost vs isolation; shared dir for MVP |

## Success Criteria
- [ ] User can set editor preference to "VS Code" in Settings → Editor → Apply
- [ ] Selecting VS Code spawns `code-server` process bound to `127.0.0.1` with `--auth none`
- [ ] `/vscode/*` HTTP requests proxy correctly to code-server (with body size cap)
- [ ] WebSocket connections proxy correctly — including **binary frames** (terminal, file content)
- [ ] WebSocket connections clean up on browser disconnect (no leaks — TaskGroup)
- [ ] VS Code web UI renders inside the workspace overlay iframe (controlled CSP + sandbox)
- [ ] Project switching opens the correct folder in VS Code (pre-validated path via dedicated endpoint)
- [ ] Deselecting VS Code (→ Built-in) stops the code-server process and restores CodeMirror
- [ ] Daemon restart recovers gracefully (stale process killed, preference respected)
- [ ] **Path injection impossible** — `main_directory` with `..` or outside allowed root rejected
- [ ] **No unauthenticated access** — proxy rejects requests without valid auth
- [ ] All new endpoints use `WorkspaceGuard.resolve_strict()` where filesystem is touched
- [ ] New DB columns/migrations support both SQLite and PostgreSQL

## Tracking
- Created: 2026-07-24
- Last Updated: 2026-07-24 (Rev 3 — regression fixes)
- Status: draft
