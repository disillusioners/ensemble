# Plan: Lightweight Agent-Native Code Workspace

> **Status:** Brainstorm / deferred — parked while we fix a critical bug. Revisit later.
> **Created:** 2026-07-19
> **Source:** Architectural review of user proposal (Go file server + CodeMirror 6 frontend) against the existing agents-ensemble stack. Grounded by Wanderer file-level investigation.
> **No implementation yet — this is a discussion artifact, not an approved plan.**

---

## 1. Proposal Summary (Original Idea)

A lightweight, agent-native code and directory viewer to replace heavy IDE servers (VS Code Server). Goals:
- Backend under 50MB RAM, client under 10MB.
- No AST indexing (language intelligence offloaded to the client).
- File tree, inline diff against git HEAD, and an agent bridge for applying patches.
- Proposed stack: **Go** backend on :4000 + **vanilla HTML/JS** frontend + **CodeMirror 6**.

Three progressive milestones proposed:
1. Lazy-loading tree nodes (enterprise-scale repos).
2. WebSocket bidirectional sync (agent writes stream to browser).
3. Agent inline UI decorations (Accept/Reject change blocks between lines).

---

## 2. Grounded Context (Current State of agents-ensemble)

Investigation findings (Wanderer, 2026-07-19):

### Frontend
- **Angular 21.2.5** + Material 21.2.5 + ng-zorro-antd. Signal-based, standalone components.
- Port 4199 (dev), proxies to backend 8079.
- **No code editor, no file browser, no diff viewer** — pure agent orchestration UI (chat, jobs, instances, skills).
- Mermaid 11.4.0 already integrated.

### Backend
- **FastAPI 0.115.6** (Python ≥3.13) + LangGraph, port 8079.
- **22 routers** — all agent-orchestration (instances, jobs, skills, messages, projects, queues, sources, etc.).
- **NO file-read, file-list, git, diff, or patch HTTP endpoints.** Files are only accessed internally by agents via tools.
- Git is invoked by the giter agent via `bash` — no git API service exists.

### File Tools
- Location: `daemon/tools/filesystem.py` (622 lines), registered as the `"filesystem"` category.
- Tools: `read_file`, `write_file`, `list_directory`, `glob_files`, `grep_files`, `edit_file`.
- **`write_file` is NOT atomic** — plain `open(..., "w")`, no temp+rename, no backup.
- Path-traversal protection: `_resolve_within_workdir()` via `Path.resolve(strict=True)` + `relative_to(workdir)` boundary check. Temp dirs whitelisted. Fails closed.
- **These are agent-internal tools, not HTTP endpoints.**

### Technology Stack
- **Zero Go code** in the project (only in `.inspiration-projects/openclaw/` vendored reference repo).
- **No WebSocket** in app code (only Slack adapter). **SSE** is the standard push model (sse-starlette) across instances, jobs, messages, notifications.
- Deployment: native Python daemon + Angular SPA. Test-only docker-compose (Postgres 16). PyInstaller spec exists.
- DB: SQLite (default) + PostgreSQL (primary dev/test since v0.5.2, dual-driver via repository pattern).

---

## 3. Architectural Assessment

### What's Good in the Proposal
1. **CodeMirror 6 over Monaco** — Correct. Monaco drags multi-MB Web Workers; CodeMirror 6 is modular, has a merge-view extension.
2. **Git baseline diffing** — The killer "agent-native" feature. Users see what the agent changed vs. committed state.
3. **"Under 50MB RAM" constraint** — Good forcing function; AST-indexing ban is pragmatic.
4. **Agent-native vision** — See changes in-context is the differentiator vs. a plain editor.
5. **Lazy loading + WebSocket sync as future milestones** — Well-prioritized.

### Critical Concerns
1. **Technology stack mismatch** — Proposal adds a 5th net-new layer (Go backend, vanilla JS frontend, new :4000 process, WebSocket transport, CodeMirror editor) on top of a project that is Angular + FastAPI + SSE.
2. **Reinventing existing capabilities** — The proposed file HTTP API duplicates `read_file`/`list_directory`/`write_file` which already exist as agent tools with battle-tested path-traversal protection.
3. **Competing write path** — A new `/api/agent/apply-patch` would parallel the existing agent→filesystem tool path, creating two write models that need reconciliation.
4. **Go unjustified** — The "under 50MB" goal doesn't require Go. File I/O is disk-bound, not CPU-bound. FastAPI async file serving is well within budget. Go would be pure greenfield with no team expertise signal.

---

## 4. Recommendations

### Recommendation A: Native Integration (Not Go)

| Aspect | Verdict | Rationale |
|--------|---------|-----------|
| CodeMirror 6 | ✅ Keep | Lightweight, merge-view extension, Angular-compatible |
| Git baseline diffing | ✅ Keep | Core value prop |
| Agent-native vision | ✅ Keep | Compelling differentiator |
| Go backend | ❌ Cut | Adds net-new language, unjustified for I/O-bound work |
| Vanilla JS frontend | ❌ Cut | Fragments the UI into two stacks |
| WebSocket transport | ❌ Cut | SSE is the project standard, fits use case |
| Apply-patch endpoint | ⏸️ Defer | Solve write-path ownership first |
| HTTP file API | ✅ Keep | As FastAPI router reusing `WorkdirGuard` |
| CodeMirror in Angular | ✅ Keep | Framework-agnostic, no conflict |

### Recommendation B: Scope Decision (Path A/B/C)

| Path | Scope | Pro | Con |
|------|-------|-----|-----|
| **A. Orchestration-only (stay focused)** | None | Sharp product identity, low maintenance | Users need separate editor to see agent changes |
| **B. Orchestration + read-only workspace viewer** | Read + diff | Best balance — see changes in-context, no write-path complexity | Meaningful frontend work |
| **C. Full code workspace (read + write + diff + sync)** | Full | Complete "agent-native IDE" story | Largest scope, changes product identity |

**Recommended: Path B (read-only viewer with diff)** — delivers the most compelling feature without the write-path complexity. Writes can be added later if demand exists.

### Recommendation C: Transport

Use **SSE** (project standard) for file-change notifications, not WebSocket. WebSocket is only justified for true collaborative editing (multiple cursors, conflict resolution) — out of scope for v1.

### Target Architecture

```
Frontend (Angular 21)
  [Chat] [Jobs] [Skills] ... [NEW: Workspace Viewer]
                                    │
                        [CodeMirror 6 + merge-view]
                                    │ SSE (file-change events)
                                    ▼
Backend (FastAPI :8079)
  ┌─────────────────────────────────────────────────────┐
  │ NEW: WorkspaceRouter (/api/workspace/*)             │
  │ • GET  /tree      → list_directory logic            │
  │ • GET  /file      → read_file logic                 │
  │ • GET  /diff      → git show HEAD:{path} (NEW)      │
  │ • GET  /events    → SSE: file-change notifications  │
  │ • (Write endpoints deferred — Path B)               │
  │ Uses: WorkdirGuard (promoted from filesystem.py)    │
  └─────────────────────────────────────────────────────┘
  [Agent tools unchanged — giter/developer write directly]
```

---

## 5. Prerequisites (Do Regardless of Decision)

These two refactors are independently valuable — do them whether or not the workspace feature proceeds:

1. **Promote `WorkdirGuard` service** — Extract `_resolve_within_workdir` from `daemon/tools/filesystem.py` into a reusable service that both agent tools and any future HTTP router share. Single security boundary.
2. **Atomic write capability** — Implement temp+`os.replace` in `write_file`. Current plain `open("w")` is a latent risk for agent-driven writes. Benefits the whole project.

---

## 6. Open Questions (To Resolve When We Return)

1. **Path A, B, or C?** Strategic decision on product identity (orchestration-only vs. workspace viewer vs. full IDE).
2. **Go vs. native FastAPI?** Architecture decision — the recommendation is to drop Go, but confirm with the user.
3. **Write-path ownership.** If Path C: do browser edits go direct-to-disk, or are they mediated through the agent system (creating a job)?
4. **CodeMirror integration approach.** `ngx-codemirror` wrapper vs. custom Angular directive wrapping `EditorView`.
5. **WorkdirGuard extraction scope.** Just promote the function, or build a full service class with config-driven allowlists?
6. **Atomic write strategy.** temp+rename only, or temp+rename+backup (`.bak`) with rollback?

---

## 7. Next Steps When We Return

1. Resolve Open Questions (§6) — especially Path A/B/C and Go/no-Go.
2. If proceeding: spawn Planner for formal implementation plan (Phase 1: read-only viewer + diff).
3. Complete prerequisites (§5) as standalone improvements first.
4. Phase 1 implementation per formal plan.
5. Revisit write capability (Path C) based on Phase 1 adoption/feedback.
