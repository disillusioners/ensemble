# Key Technical Decisions: Workspace Viewer

## Summary

Each decision includes the question, options considered, chosen answer, and rationale.

---

## D1: WorkdirGuard Extraction Strategy

**Question**: Extract `_resolve_within_workdir` to a shared module/service, or keep it in `filesystem.py` and import directly?

| Option | Description |
|--------|-------------|
| A. Full service class | New `WorkspaceGuard` class in `daemon/services/workspace_guard.py` with config-driven allowlists, size limits, ignore patterns |
| B. Module-level import | Move functions to a new module, import from both `filesystem.py` and `workspace.py` |
| C. Keep in filesystem.py | Import private functions directly from `daemon.tools.filesystem` |

### Decision: **A — Full `WorkspaceGuard` class**

**Rationale**:
- The HTTP layer needs additional context that the agent tools don't (size limits, ignore patterns, depth limits). A class encapsulates these per-instance.
- Agent tools and HTTP endpoints share the **core** path resolution + boundary check, but the HTTP layer adds config like `MAX_FILE_SIZE_BYTES` and `IGNORE_PATTERNS`.
- `WorkspaceGuard` takes a `workdir` in its constructor — this is the natural unit of scoping. Each request creates one instance for the project's workdir.
- The existing `filesystem.py` functions become thin wrappers: `_resolve_within_workdir(path, workdir)` instantiates a temporary `WorkspaceGuard(workdir)` and calls `.resolve(path)`.

**Migration path**:
1. Create `WorkspaceGuard` class with all existing logic moved in
2. Update `filesystem.py` to import and delegate
3. All existing agent tool tests run unchanged (regression validation)
4. New `WorkspaceRouter` creates its own `WorkspaceGuard` per request

---

## D2: File Tree Depth Limit and Ignore Patterns

**Question**: What depth limit for the file tree, and which directories to ignore?

### Decision:

**Depth limit**: 5 levels (configurable via `WorkspaceGuard.DEFAULT_TREE_DEPTH`, max 10 via query param)

**Ignore patterns** (hardcoded in `WorkspaceGuard.IGNORE_PATTERNS`):
```
.git, node_modules, __pycache__, .venv, venv,
dist, build, .next, .pytest_cache, .mypy_cache,
.tox, egg-info, .eggs
```

**Rationale**:
- 5 levels covers the vast majority of project structures without overwhelming the tree API response
- The ignore list matches common build artifacts, dependency directories, and cache directories
- `node_modules` alone can contain 10k+ files — excluding it is essential for performance
- Lazy directory expansion (Phase 2 Task 7) means the client only fetches one level at a time on expand — the depth limit is per-request, not total tree depth
- Users can override depth via `?depth=N` query parameter (capped at 10)

**Future consideration**: Add `.gitignore` parsing for project-specific ignore patterns. Out of scope for v1.

---

## D3: Diff Rendering — CodeMirror MergeView vs Custom Diff

**Question**: Use CodeMirror 6's `@codemirror/merge` MergeView for diff rendering, or build a custom diff component?

| Option | Pros | Cons |
|--------|------|------|
| A. `@codemirror/merge` MergeView | Official, purpose-built, side-by-side, syntax-highlighted, maintained | Adds ~100KB to bundle |
| B. Custom unified diff renderer | Full control, lighter weight | Must implement diff algorithm, syntax highlighting, line alignment |

### Decision: **A — `@codemirror/merge` MergeView**

**Rationale**:
- MergeView is the official CodeMirror 6 diff extension — it handles word-level diffs, line alignment, and syntax highlighting out of the box
- It's purpose-built for exactly this use case (comparing two versions of a file)
- The ~100KB bundle addition is well within the 10MB client budget
- A custom diff renderer would need to solve: diff algorithm (Myers), line mapping, syntax highlighting, scroll sync — all already solved by MergeView
- Both panes are read-only (Path B), so we don't need MergeView's editing features — just the visual diff
- The backend returns `head_content` and `working_content` separately; MergeView takes two docs directly

**Configuration**: Side-by-side layout (left = HEAD, right = working tree). Both panes `editable.of(false)`.

---

## D4: SSE File Change Detection — Watchdog vs Polling

**Question**: Use `watchdog` library for filesystem notifications, or poll the filesystem?

| Option | Pros | Cons |
|--------|------|------|
| A. `watchdog` (inotify/FSEvents) | Real-time, low CPU, event-driven | New dependency, platform-specific backends |
| B. Polling (every 5s) | No dependency, simple, works everywhere | CPU usage on large dirs, delayed detection |
| C. Both (watchdog with polling fallback) | Best of both worlds | Slightly more code |

### Decision: **C — Watchdog primary, polling fallback**

**Rationale**:
- `watchdog` provides near-instant file change notifications via OS-native APIs (inotify on Linux, FSEvents on macOS)
- The daemon may run in environments where watchdog isn't installed (Docker, minimal containers) — polling is the universal fallback
- The `FileChangeMonitor` class tries `from watchdog.observers import Observer`; if ImportError, it falls back to a 5-second polling loop
- Both modes feed the same `_emit()` method, so SSE consumers don't know or care which mode is active
- Watchdog is an **optional** dependency — it's not in `requirements.txt` by default. If installed, it's used; if not, polling works

**Debounce**: Regardless of detection mechanism, events are debounced with a 2-second minimum interval per path. This prevents flooding during bulk agent operations (e.g., when an agent writes 20 files in rapid succession).

**Thread Safety** (Blocking Fix 1):
- `watchdog.Observer` runs on its own thread. `asyncio.Queue` is NOT thread-safe.
- `_emit()` captures `self._loop = asyncio.get_running_loop()` (in `add_subscriber`, inside the async context) and uses `self._loop.call_soon_threadsafe(self._safe_put, queue, event_data)` to schedule the `put_nowait` on the event loop.
- Same pattern as `daemon/services/dispatch_event_bus.py:67` and `daemon/services/completion_registry.py:133`.

**Singleton Lifecycle** (Blocking Fix 3 + W2):
- `watchdog.Observer` is single-shot — once `stop()` is called, its internal thread terminates and the instance cannot be restarted.
- `_stop()` evicts the monitor from `_instances` when no subscribers remain.
- `get_or_create()` checks `_started` before returning a cached instance; if the instance was stopped, it creates a fresh one with a new Observer.

---

## D5: CodeMirror Integration — ngx-codemirror vs Custom Directive

**Question**: Use the `ngx-codemirror` Angular wrapper, or write a custom directive?

| Option | Pros | Cons |
|--------|------|------|
| A. `ngx-codemirror` | Angular-idiomatic, config-driven | Extra abstraction, less CM6 support, another dependency |
| B. Custom directive | Direct EditorView access, full control, no wrapper | Slightly more boilerplate |

### Decision: **B — Custom directive**

**Rationale**:
- CodeMirror 6 is designed to be framework-agnostic — `EditorView` attaches to any DOM element
- A thin directive (`CodemirrorDirective`) creates the `EditorView` in `ngOnInit`, updates content via `view.dispatch()`, and destroys in `ngOnDestroy()` — ~60 lines of code
- `ngx-codemirror` is primarily designed for CM5 and has incomplete CM6 support
- Direct `EditorView` access means we can use CM6 extensions (merge-view, language loading) without fighting a wrapper's abstraction
- The directive gives us full control over read-only mode, theme, line numbers, and language extensions

**Implementation**: See Phase 2 Task 4 for the full directive code.

---

## D6: API Endpoint Structure — Flat vs Nested

**Question**: URL structure for workspace endpoints — flat (`/api/workspace/tree?project_id=X`) or nested (`/api/workspace/{project_id}/tree`)?

### Decision: **Nested — `/api/workspace/{project_id}/tree`**

**Rationale**:
- Matches existing project-scoped patterns in the codebase (e.g., `/api/instances/{id}/messages`, `/api/projects/{id}/pause-queue`)
- Project ID in the path makes it clear which workspace is being accessed
- RESTful: the project is a resource, the workspace is a sub-resource
- Enables future per-project workspace configuration without query param pollution
- Consistent with frontend routing: `/projects/:projectId/workspace`

---

## D7: File Content Pagination Strategy

**Question**: How to handle large files that exceed the display limit?

### Decision:

- **Hard size limit**: 1MB (`WorkspaceGuard.MAX_FILE_SIZE_BYTES`). Files > 1MB return HTTP 413 with file metadata.
- **Line pagination**: For files under 1MB but > 2000 lines, paginate with `offset` and `limit` query params (same as `read_file` agent tool).
- **Binary detection**: Files that fail UTF-8 decode return `binary: true` with no content — the UI shows a "Binary file" placeholder.
- **Frontend pagination**: The code viewer shows a "Load more" button at the bottom when `truncated: true`.

**Rationale**:
- 1MB is generous for source code (the largest source files are typically < 500KB)
- Line pagination matches the existing `read_file` agent tool pattern (offset/limit, 1-indexed)
- Binary detection prevents corrupted displays and confusing errors
- The hard limit protects the server from memory exhaustion (a 100MB minified JS file would crash the response)

---

## D8: Workdir Resolution — How Does the Router Find the Project's Directory?

**Question**: How does the WorkspaceRouter resolve a `project_id` to a filesystem path?

### Decision:

The router uses the existing `SQLModelProjectRepository.get_by_id(project_id)` to fetch the project, then reads `project.main_directory`.

```python
def _get_workdir(project_id: str) -> str:
    if _project_repo is None:
        raise HTTPException(status_code=503, ...)
    project = _project_repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, ...)
    if not project.main_directory:
        raise HTTPException(status_code=400, detail={"error": "Project has no main_directory configured"})
    return project.main_directory
```

**Rationale**:
- The project model already has `main_directory: str | None` (see `daemon/repositories/project/models.py:210`)
- This is the canonical source of truth for project workdir
- If `main_directory` is None (project created without a directory), return 400 — the workspace viewer can't function without a directory
- The frontend route includes `projectId` in the URL, so this resolution happens on every request

**Edge case**: Projects with `related_directories` — for v1, only `main_directory` is browsable. Multi-directory browsing is a future enhancement.

---

## D9: Language Detection Strategy

**Question**: How to detect the programming language for syntax highlighting?

### Decision:

Simple extension-based mapping in both backend and frontend:

**Backend** (`daemon/routers/workspace.py`):
```python
_LANGUAGE_MAP = {
    ".py": "python", ".ts": "typescript", ".js": "javascript",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".sql": "sql", ".sh": "shell",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".tsx": "tsx", ".jsx": "jsx",
}
```

The backend returns `language` in the `FileContentResponse`. The frontend's `CodemirrorDirective` uses it to select the CodeMirror language extension.

**Rationale**:
- Extension-based detection is fast, deterministic, and correct for 99% of cases
- No heuristic content analysis needed (avoids edge cases and false positives)
- Unknown extensions get `language: null` — CodeMirror renders as plain text (still with line numbers and dark theme)
- The language list covers the most common languages in the project (Python, TypeScript, JavaScript, HTML, CSS, JSON, YAML, Markdown, SQL)

---

## D10: Git Operations — Library vs Subprocess

**Question**: Use a Python git library (GitPython, pygit2) or raw subprocess?

### Decision: **Subprocess with `asyncio.to_thread()`**

**Rationale**:
- `subprocess.run(["git", "diff", "HEAD", "--", path])` is simple, universal, and has no dependencies
- GitPython adds a significant dependency (~2MB) and has known memory issues with large repos
- pygit2 requires libgit2 native bindings — unnecessary complexity
- Git is universally available on any system running agents-ensemble
- The subprocess approach is consistent with how the giter agent already invokes git (via bash tool)
- `asyncio.to_thread()` wraps the blocking subprocess call, following the existing async pattern in routers
- Security: path is pre-validated by `WorkspaceGuard` and passed as a relative path (never absolute), and subprocess uses argument list (never `shell=True`)
