# Phase 3: Integration & Polish — SSE Wiring, Navigation, E2E Testing

## Objective

Wire the frontend components to the backend SSE file-change stream, add navigation/UX polish (project-aware workspace access, file tree refresh on SSE events, loading states, error handling), and validate the complete feature with end-to-end tests.

## Coupling

- **Depends on**: Phase 1 (SSE endpoint) + Phase 2 (frontend components)
- **Coupling type**: **tight** — Phase 3 wires real backend endpoints to real frontend components. Both phases must be complete.
- **Shared files with other phases**: Modifies `WorkspaceService` (Phase 2) to add SSE consumption; modifies `WorkspaceComponent` (Phase 2) to add navigation entry points.
- **Shared APIs/interfaces**: Consumes `GET /api/workspace/{project_id}/events` (SSE)
- **Why this coupling**: The SSE integration is the final glue. It requires the actual endpoint (Phase 1) and the actual components (Phase 2) to exist and function.

> **⚠️ W9 Scheduling Note**: Phase 3 MUST run strictly sequential after BOTH
> Phase 1 and Phase 2 are complete. Do not attempt to pipeline Phase 3 with
> Phase 2 — the SSE wiring, navigation entry points, and e2e tests all
> require the frontend components from Phase 2 to be functional against the
> real Phase 1 backend.

## Context

- **Phase 1 delivered**: WorkspaceRouter with tree/file/diff/events endpoints, WorkspaceGuard, GitDiffService, FileChangeMonitor
- **Phase 2 delivered**: FileTreeComponent, CodeViewerComponent, DiffViewerComponent, WorkspaceComponent, WorkspaceService with HTTP calls
- **Phase 3 adds**: SSE integration, navigation entry points, refresh-on-change, loading/error UX, e2e tests

### Existing Navigation Patterns

The frontend has a toolbar with navigation. The project list page shows projects. The chat page is project-scoped at `/projects/:projectId/instances/:instanceId`.

For workspace access, we need entry points:
1. A "Workspace" button/link in the project list or project detail
2. A toolbar link in the global navigation
3. A contextual link from the chat page (when an agent is working on files)

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add SSE file-change consumption to WorkspaceService | Subscribe to `/api/workspace/{projectId}/events`, auto-refresh file tree + current file content on change events | `frontend/src/app/services/workspace.service.ts` (modify) |
| 2 | Add workspace navigation entry points | Add "Workspace" link to project tab bar, chat page toolbar, and global nav | `frontend/src/app/components/project-tab-bar/`, `frontend/src/app/pages/chat/chat.html` (modify) |
| 3 | Add loading + error states to workspace UI | Loading spinners for tree/file/diff, error banners, retry buttons | `frontend/src/app/pages/workspace/workspace.component.ts` (modify) |
| 4 | Add file tree refresh on SSE events | When `file_changed` event arrives, refresh the affected tree node and re-fetch current file if changed | `frontend/src/app/components/file-tree/file-tree.component.ts` (modify) |
| 5 | Add keyboard shortcuts | `Cmd/Ctrl+P` for quick file search, `Esc` to deselect | `frontend/src/app/pages/workspace/workspace.component.ts` (modify) |
| 6 | Write E2E tests | Playwright tests for full flow: navigate → tree → file → diff → SSE update | `frontend/e2e/workspace-viewer.spec.ts` (new) |
| 7 | Write integration tests | Backend integration test for SSE event flow (file modify → event emitted → received) | `tests/integration/test_workspace_sse.py` (new) |
| 8 | Documentation | Update API docs, add workspace viewer to user docs | `docs/` (modify) |

---

## Task Details

### Task 1: SSE File-Change Consumption

**Modify**: `frontend/src/app/services/workspace.service.ts`

Add SSE subscription logic:

```typescript
import { Injectable, inject, signal, NgZone, OnDestroy, effect } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class WorkspaceService implements OnDestroy {
  // ... existing fields from Phase 2 ...

  private eventSource: EventSource | null = null;
  readonly sseConnected = signal(false);

  // Blocking Fix 4: Keep fileChanged as a signal (consistent with codebase
  // patterns). Phase 3 Task 4 uses `effect()` to react — NOT `.pipe()`.
  // Signals don't have `.pipe()`; they're not Observables.
  readonly fileChanged = signal<{ path: string; type: string } | null>(null);

  private ngZone = inject(NgZone);

  /** Subscribe to SSE file-change events for a project */
  connectSSE(projectId: string): void {
    this.disconnectSSE();
    this._currentProjectId = projectId;  // W14: track for SSE refresh
    this.eventSource = new EventSource(
      `${this.API_BASE}/${encodeURIComponent(projectId)}/events`
    );

    // Issue 9: EventSource callbacks run OUTSIDE Angular's zone. All signal
    // mutations MUST be wrapped in ngZone.run() so change detection fires.
    // Same pattern as sse.service.ts:218-228.
    this.eventSource.addEventListener('connected', () => {
      this.ngZone.run(() => {
        this.sseConnected.set(true);
      });
    });

    this.eventSource.addEventListener('file_changed', (event) => {
      this.ngZone.run(() => {
        const data = JSON.parse((event as MessageEvent).data);
        this.fileChanged.set({ path: data.path, type: data.change_type });
        this.handleFileChange(data.path);
      });
    });

    this.eventSource.addEventListener('keepalive', () => {
      // No-op — just keeps the connection alive
    });

    this.eventSource.onerror = () => {
      this.ngZone.run(() => {
        this.sseConnected.set(false);
        // EventSource auto-reconnects; just update indicator
      });
    };
  }

  disconnectSSE(): void {
    this.eventSource?.close();
    this.eventSource = null;
    this.sseConnected.set(false);
  }

  /** Auto-refresh on file change */
  private handleFileChange(changedPath: string): void {
    const currentPath = this.selectedPath();
    // Refresh current file if it changed
    if (currentPath && changedPath === currentPath) {
      const projectId = this._currentProjectId;
      if (projectId) {
        this.getFileContent(projectId, currentPath).subscribe();
      }
    }
    // Note: Tree refresh handled by FileTreeComponent via effect() on fileChanged
  }

  // _currentProjectId is already declared in Phase 2's WorkspaceService

  ngOnDestroy(): void {
    this.disconnectSSE();
  }
}
```

**Key Decision**: Use native `EventSource` API (not a library). The frontend already uses `EventSource` in `sse.service.ts` for instance events — same pattern.

### Task 2: Navigation Entry Points

**Option A — Project Tab Bar** (recommended):

The existing `project-tab-bar` component shows tabs for project sections. Add a "Workspace" tab.

**Modify**: `frontend/src/app/components/project-tab-bar/project-tab-bar.component.ts` (or .html)
```html
<!-- Add workspace tab -->
<a mat-tab-link [routerLink]="['/projects', projectId, 'workspace']"
   routerLinkActive #rla="routerLinkActive" [active]="rla.isActive">
  <mat-icon>folder_open</mat-icon> Workspace
</a mat-tab-link>
```

**Option B — Chat Page Toolbar Link**:

Add a button in the chat page toolbar that opens the workspace in a new tab:

**Modify**: `frontend/src/app/pages/chat/chat.html`
```html
<!-- Add to toolbar -->
<a mat-icon-button [routerLink]="['/projects', projectId, 'workspace']"
   matTooltip="Open Workspace Viewer">
  <mat-icon>folder_open</mat-icon>
</a>
```

**Both options should be implemented** — the tab bar for persistent navigation, the chat toolbar for contextual access when an agent is working.

### Task 3: Loading + Error States

**Modify**: `frontend/src/app/pages/workspace/workspace.component.ts`

```typescript
// Add to template:
<div class="workspace-container">
  <!-- SSE connection indicator -->
  <div class="sse-status" [class.connected]="workspace.sseConnected()">
    <span class="dot"></span>
    {{ workspace.sseConnected() ? 'Live' : 'Disconnected' }}
  </div>

  <!-- Error banner -->
  @if (workspace.error(); as err) {
    <div class="error-banner">
      <mat-icon>error</mat-icon>
      <span>{{ err }}</span>
      <button mat-icon-button (click)="workspace.clearError()">
        <mat-icon>close</mat-icon>
      </button>
    </div>
  }

  <!-- Loading overlay -->
  @if (workspace.loading()) {
    <div class="loading-overlay">
      <mat-spinner diameter="32"></mat-spinner>
    </div>
  }

  <!-- ... existing sidenav content ... -->
</div>
```

### Task 4: File Tree Refresh on SSE

**Modify**: `frontend/src/app/components/file-tree/file-tree.component.ts`

```typescript
import { Component, Input, Output, EventEmitter, inject, effect, OnDestroy } from '@angular/core';
import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeNode } from '../../models/workspace.model';

export class FileTreeComponent implements OnDestroy {
  // ... existing fields ...

  // Blocking Fix 4: Use effect() to react to fileChanged signal — NOT .pipe().
  // Signals don't have .pipe() (they're not Observables). effect() is the
  // Angular-native way to react to signal changes. It auto-tracks the
  // fileChanged signal as a dependency.
  private fileChangeEffect = effect(() => {
    const change = this.workspace.fileChanged();
    if (change) {
      this.refreshAffectedNode(change.path);
    }
  });

  private refreshAffectedNode(changedPath: string): void {
    // Find the parent directory of the changed file in the tree
    // Re-fetch that directory's children
    // Update the dataSource without full tree reload
    const parentPath = changedPath.split('/').slice(0, -1).join('/') || '.';
    this.workspace.expandDirectory(this.projectId, {
      name: '', path: parentPath, type: 'directory', size: null, children: null
    }).subscribe(children => {
      this.updateNodeChildren(parentPath, children.tree);
    });
  }

  ngOnDestroy(): void {
    // effect() is auto-cleaned up when the component is destroyed if created
    // in an injection context. But if created manually, call .destroy().
    // Since we created it as a field initializer (injection context), Angular
    // handles cleanup. No explicit destroy needed.
  }
}
```

### Task 5: Keyboard Shortcuts (Optional Enhancement)

Add `Cmd/Ctrl+P` for a quick file search dialog:

```typescript
// In WorkspaceComponent:
@HostListener('window:keydown', ['$event'])
handleKeyboard(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key === 'p') {
    event.preventDefault();
    this.openQuickSearch();
  }
}
```

The quick search can use a simple filter input over the loaded tree nodes. This is a nice-to-have for v1.

### Task 6: E2E Tests (Playwright)

**New file**: `frontend/e2e/workspace-viewer.spec.ts`

```typescript
import { test, expect } from '@playwright/test';

test.describe('Workspace Viewer', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to workspace viewer for test project
    await page.goto('/projects/test-project-id/workspace');
  });

  test('displays file tree on load', async ({ page }) => {
    await expect(page.locator('app-file-tree')).toBeVisible();
    await expect(page.locator('.tree-header')).toContainText('Files');
  });

  test('expands directory on click', async ({ page }) => {
    // Click first directory
    const firstDir = page.locator('mat-tree-node .dirname').first();
    await firstDir.click();
    // Wait for child nodes to appear
    await expect(page.locator('mat-tree-node').count()).resolves.toBeGreaterThan(1);
  });

  test('shows file content when file clicked', async ({ page }) => {
    // Expand a directory and click a file
    await page.locator('mat-tree-node .dirname').first().click();
    await page.locator('mat-tree-node .filename').first().click();
    // Verify code viewer appears
    await expect(page.locator('app-code-viewer')).toBeVisible();
    await expect(page.locator('.code-content')).toBeVisible();
  });

  test('switches to diff view', async ({ page }) => {
    // Select a file first
    await page.locator('mat-tree-node .filename').first().click();
    // Click Diff tab
    await page.locator('mat-button-toggle[value="diff"]').click();
    // Verify diff viewer appears
    await expect(page.locator('app-diff-viewer')).toBeVisible();
  });

  test('shows SSE connection status', async ({ page }) => {
    await expect(page.locator('.sse-status')).toBeVisible();
  });
});
```

### Task 7: Backend SSE Integration Test

**New file**: `tests/integration/test_workspace_sse.py`

```python
"""Integration test for workspace SSE file-change events.

Issue 11: This test MUST actually consume the SSE stream and assert
receiving a ``file_changed`` event — not just verify the REST endpoint.
"""
import asyncio
import os
import tempfile
import json

import pytest
import httpx
from sqlmodel import SQLModel, create_engine
from daemon.api import app
from daemon.routers.workspace import set_project_repository
from daemon.repositories import SQLModelProjectRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sse_file_change_event_flow():
    """Test that file modifications trigger SSE file_changed events.

    Full flow:
    1. Connect to SSE stream → receive 'connected' event
    2. Modify a file on disk
    3. Receive 'file_changed' event with the correct path
    4. Verify file content endpoint reflects the change
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create initial test file
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('hello')\n")

        # Set up project with workdir
        engine = create_engine(f"sqlite:///{tmpdir}/test.db")
        SQLModel.metadata.create_all(engine)
        repo = SQLModelProjectRepository(engine)
        project = repo.create_project(name="test-ws", main_directory=tmpdir)
        set_project_repository(repo)

        app.state.manager = None
        app.state.start_time = 0.0

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Verify file content endpoint works first
            resp = await client.get(
                f"/api/workspace/{project.project_id}/file",
                params={"path": "test.py"},
            )
            assert resp.status_code == 200
            assert "hello" in resp.json()["content"]

            # Connect to SSE stream and consume events
            received_events: list[dict] = []

            async with client.stream(
                "GET", f"/api/workspace/{project.project_id}/events"
            ) as response:
                # Read 'connected' event
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event_type == "connected":
                        assert json.loads(line.split(":", 1)[1].strip())["status"] == "connected"
                        break

                # Give the monitor time to start watching
                await asyncio.sleep(1.0)

                # Modify a file to trigger change event
                with open(test_file, "a") as f:
                    f.write("# modified\n")

                # Consume events until we get file_changed or timeout
                event_type = None
                got_file_changed = False
                try:
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:") and event_type == "file_changed":
                            data = json.loads(line.split(":", 1)[1].strip())
                            assert "path" in data
                            assert "test.py" in data["path"]
                            got_file_changed = True
                            break
                        elif line.startswith("data:") and event_type == "keepalive":
                            # Keepalive — continue waiting
                            continue
                except httpx.ReadTimeout:
                    pass  # Expected after consuming available events

                assert got_file_changed, (
                    "Did not receive file_changed event after modifying test.py. "
                    "Check that FileChangeMonitor (watchdog or polling) is working."
                )

            # Verify file content endpoint reflects the change
            resp = await client.get(
                f"/api/workspace/{project.project_id}/file",
                params={"path": "test.py"},
            )
            assert resp.status_code == 200
            assert "modified" in resp.json()["content"]
```

### Task 8: Documentation

**Modify/Create**:
- `docs/api/workspace.md` — API endpoint reference
- Update any user-facing docs with workspace viewer feature description
- Add workspace viewer screenshots/usage guide

---

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `frontend/src/app/services/workspace.service.ts` | **MODIFY** | Add SSE consumption + auto-refresh |
| `frontend/src/app/components/project-tab-bar/` | **MODIFY** | Add workspace tab link |
| `frontend/src/app/pages/chat/chat.html` | **MODIFY** | Add workspace link in chat toolbar |
| `frontend/src/app/pages/workspace/workspace.component.ts` | **MODIFY** | Add loading/error/SSE status UI |
| `frontend/src/app/components/file-tree/file-tree.component.ts` | **MODIFY** | Auto-refresh on SSE events |
| `frontend/e2e/workspace-viewer.spec.ts` | **CREATE** | Playwright E2E tests |
| `tests/integration/test_workspace_sse.py` | **CREATE** | Backend SSE integration test |
| `docs/api/workspace.md` | **CREATE** | API documentation |

## Constraints

- SSE connection MUST auto-reconnect (native `EventSource` behavior — no custom retry needed)
- SSE disconnection indicator MUST be visible but non-intrusive
- File tree refresh MUST be partial (re-fetch only affected directory, not full tree)
- Loading states MUST use Material spinners (consistency)
- Error states MUST be recoverable (retry button)
- E2E tests MUST run against the real dev server (not mocks)
- Navigation links MUST preserve project context (`/projects/:projectId/workspace`)

## Deliverables

- [ ] `WorkspaceService` subscribes to SSE events and auto-refreshes current file
- [ ] Project tab bar includes "Workspace" link
- [ ] Chat page toolbar includes workspace link
- [ ] Loading states show spinner during API calls
- [ ] Error states show banner with retry
- [ ] SSE connection status indicator visible
- [ ] File tree refreshes on `file_changed` events
- [ ] Playwright E2E tests pass
- [ ] Backend SSE integration test passes
- [ ] API documentation written
