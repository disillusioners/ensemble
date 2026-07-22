# Plan Overview: Workspace Viewer (Read-Only with Git Diff)

## Objective

Build a browser-based, read-only file browser integrated into the agents-ensemble platform. Users can navigate a project's file tree, view file contents with syntax highlighting, and see inline git diffs against HEAD — all without leaving the ensemble UI. This delivers the core "agent-native" value proposition: seeing what agents changed, in context.

## Scope Assessment

**LARGE** — Three logical modules spanning backend + frontend + integration:

- **Backend**: New `WorkspaceRouter` with 4 endpoints (tree, file, diff, SSE events), a reusable `WorkspaceGuard` security layer extracted from existing `filesystem.py`, and a git-diff service. No database schema changes.
- **Frontend**: New Angular module with 3 new components (file tree, code viewer, diff viewer), CodeMirror 6 integration, a workspace service, and routing. ~8-12 new files.
- **Integration**: SSE file-change event wiring, navigation UX, project-scoped workspace selection, and end-to-end testing.

This is a coherent feature with clear module boundaries. Each phase is self-contained and independently testable.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Brainstorm Doc**: `docs/plans/workspace-viewer-plan.md` (Path B recommended)
- **Backend**: FastAPI 0.115.6, Python ≥3.13, port 8079, 22 routers
- **Frontend**: Angular 21.2.5, @angular/material 21.2.5, port 4199
- **Security**: `_resolve_within_workdir()` in `daemon/tools/filesystem.py` (path-traversal protection)
- **SSE Standard**: `sse-starlette` `EventSourceResponse`, per-connection `asyncio.Queue` pattern
- **No auth layer**: All endpoints are daemon-wide (no users table)

## Architecture

```
Frontend (Angular 21 — port 4199)
  ┌─────────────────────────────────────────────────┐
  │ NEW: Workspace Viewer Module                     │
  │  • FileTreeComponent (recursive tree, lazy dirs) │
  │  • CodeViewerComponent (CodeMirror 6 read-only)  │
  │  • DiffViewerComponent (CodeMirror 6 merge-view) │
  │  • WorkspaceService (HTTP + SSE consumption)     │
  │  Route: /projects/:projectId/workspace            │
  └───────────────────────┬─────────────────────────┘
                          │ HTTP (REST) + SSE
                          ▼
Backend (FastAPI — port 8079)
  ┌─────────────────────────────────────────────────┐
  │ NEW: WorkspaceRouter (/api/workspace/*)          │
  │  • GET /tree   → file tree listing               │
  │  • GET /file   → file content read               │
  │  • GET /diff   → git diff HEAD:{path}            │
  │  • GET /events → SSE file-change stream          │
  │                                                  │
  │ NEW: WorkspaceGuard (extracted from filesystem)  │
  │  • Path resolution + boundary check (shared)     │
  │                                                  │
  │ NEW: GitDiffService                              │
  │  • subprocess git show/diff with timeout         │
  └─────────────────────────────────────────────────┘
  [Agent tools unchanged — existing filesystem.py delegates to WorkspaceGuard]
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend API | WorkspaceRouter with 4 endpoints, WorkspaceGuard extraction, GitDiffService | None | — (root) | 6-8h |
| 2 | Frontend Viewer | CodeMirror 6 integration, file tree, code viewer, diff viewer components | Phase 1 (API contract) | loose (REST contract only) | 8-10h |
| 3 | Integration & Polish | SSE wiring, navigation UX, project-scoping, e2e testing | Phase 1 + 2 | tight (both phases complete) | 4-6h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **loose** | Phase 2 depends only on the REST API contract (request/response shapes), not Phase 1's implementation. Can pipeline: Phase 2 can start against mock data once API schema is agreed. |
| 2 → 3 | **tight** | Phase 3 wires the frontend components to the backend SSE stream and adds navigation. Requires both Phase 1 endpoints and Phase 2 components to be complete. |
| 1 → 3 | **tight** | Phase 3's SSE integration needs the actual SSE endpoint from Phase 1. |

**Scheduling Strategy**:
- Phase 1 starts immediately.
- Phase 2 can start in parallel once the API contract is documented (after Phase 1 Task 2 — the **schema freeze gate**), using mock data.
- Phase 3 **must wait strictly** for both Phase 1 and Phase 2 to be fully complete (W9). No pipelining of Phase 3 with Phase 2 — the SSE wiring, navigation, and e2e tests require the frontend components to be functional against the real backend.

## Security Model

| Boundary | Mechanism |
|----------|-----------|
| Path traversal | `WorkspaceGuard.resolve()` — reuses `_resolve_within_workdir()` logic, resolves symlinks, checks `relative_to(workdir)` |
| File size | Hard limit: 1MB for file content read. Return 413 if exceeded. Configurable via constant. |
| Binary files | Detect via null-byte heuristic or `UnicodeDecodeError`. Return metadata-only response for binaries. |
| Git injection | GitDiffService uses `subprocess.run()` with argument list (never shell=True). Path is pre-validated by WorkspaceGuard. |
| Directory depth | File tree limited to configurable depth (default: 5 levels). Prevents infinite recursion. |
| Ignore patterns | `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.next` excluded from tree listing |
| SSE rate limiting | Debounce file-change events (min 2s between emissions per path). Prevents flooding during bulk operations. |

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| CodeMirror 6 Angular integration complexity | high | medium | Use raw `EditorView` in a directive, not `ngx-codemirror` wrapper. CM6 is framework-agnostic. |
| Large file / binary file handling | medium | high | Size limit (1MB) + binary detection. Return metadata for binaries. Pagination for large text. |
| SSE file-change events flooding during bulk agent ops | medium | high | Debounce: coalesce rapid changes, min 2s interval per path. Backpressure via queue maxsize. |
| Git not available / not a git repo | low | medium | Graceful degradation: return 404 with `{"error": "not_a_git_repo"}`. UI shows "No git history". |
| `watchdog` dependency adding to daemon footprint | low | low | Use polling fallback (5s interval) if watchdog unavailable. Make watchdog optional. |
| WorkdirGuard extraction breaking existing tools | high | low | Extract as thin wrapper. Existing `filesystem.py` functions import from new module. Full regression test. |
| Performance on large repos (10k+ files) | medium | medium | Lazy-load tree directories on expand. Server-side pagination on tree endpoint. Cache tree structure. |

## Success Criteria

- [ ] `GET /api/workspace/{project_id}/tree` returns a file tree respecting ignore patterns and depth limits
- [ ] `GET /api/workspace/{project_id}/file?path=...` returns file content with line numbers, rejects path traversal
- [ ] `GET /api/workspace/{project_id}/diff?path=...` returns git diff against HEAD (or 404 if no git)
- [ ] `GET /api/workspace/{project_id}/events` streams SSE file-change events with debounce
- [ ] Frontend file tree renders with lazy directory expansion
- [ ] Code viewer shows file content with syntax highlighting (CodeMirror 6)
- [ ] Diff viewer shows inline git diff (CodeMirror 6 merge-view)
- [ ] All path traversal attempts return 403
- [ ] Binary files and oversized files are handled gracefully
- [ ] Backend tests pass (pytest, PostgreSQL mode)
- [ ] Frontend tests pass (Jest)
- [ ] E2E test: navigate tree → view file → view diff → receive SSE update

## Tracking

- **Created**: 2026-07-22
- **Last Updated**: 2026-07-22 (Revision 1 — 4 blocking fixes + 8 critical issues + 6 warnings applied)
- **Status**: draft (revised)
