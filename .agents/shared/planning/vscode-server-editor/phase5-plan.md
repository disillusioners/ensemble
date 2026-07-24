# Phase 5: VS Code iframe Component & Editor Switching

## Objective
Build the `VsCodeViewerComponent` that wraps the code-server web UI in an iframe (with correct **`postMessage` origin** (C3) and **`sandbox` attribute** (W1)), integrate it into the workspace overlay with editor-mode switching (CodeMirror ↔ VS Code) using **signal inputs** (S6), and implement project switching via the **path-validated** folder endpoint (C2).

> **Rev 2 changes**: C3 (postMessage targetOrigin must be absolute URL), W1 (iframe sandbox attribute), S6 (workdir as signal input, not @Input), C2 (use validated folder endpoint instead of raw main_directory).
>
> **Rev 3 changes (N3)**: Clear debounce timer (`_reloadTimer`) in `ngOnDestroy()` to prevent fire-after-destroy leak.

## Coupling
- **Depends on**: Phase 4 (editor preference from SettingsService), Phase 2 (proxy `/vscode/*` must work), Phase 3 (C2: `/api/projects/{id}/vscode-folder` endpoint)
- **Coupling type**: **tight** with Phase 4 (reads editor preference signal), **tight** with Phase 2 (iframe loads `/vscode/` URL)
- **Shared files with other phases**: `frontend/src/app/pages/workspace/workspace.component.ts` (modify template), `frontend/src/app/services/workspace.service.ts` (add editor mode)
- **Shared APIs/interfaces**: Consumes editor preference from SettingsService, loads `/vscode/` iframe, calls `GET /api/projects/{id}/vscode-folder` (C2)
- **Why this coupling**: Editor switching is triggered by the preference set in Phase 4; the iframe needs the proxy from Phase 2; the folder path comes from Phase 3's validated endpoint.

## Context
- **Previous phases delivered**: 
  - Phase 4: Editor preference in SettingsService (`getEditorPreference()`)
  - Phase 2: `/vscode/*` proxy serving code-server web UI with controlled CSP
  - Phase 3: `GET /api/projects/{id}/vscode-folder` endpoint (C2 — pre-validated path)
- **Workspace overlay**: `WorkspaceComponent` (`workspace.component.ts`) is rendered as absolute overlay inside `.chat-area` (z-index:50). Show/hide via `showWorkspace` signal in `ChatComponent`.
- **Editor rendering**: Currently `workspace.component.ts:166` hardcodes `<app-code-viewer>`. The `viewMode: signal<'code'|'diff'>` toggles code vs diff — but there's NO editor-mode concept yet.
- **Project switching**: `TabStateService.activeProjectId()` → `ChatComponent.tabWorkspaceEffect` → `WorkspaceComponent.projectId` setter → `WorkspaceService.saveCurrentState/restoreState`.
- **Angular effect hazard**: `tabWorkspaceEffect` had a dependency-tracking bug (workspace stuck on one project). ANY new `effect()` must read all dependent signals unconditionally before any if-branch.

## Technical Approach

### Editor Mode Signal
Add to `WorkspaceService` (central state store, already manages `viewMode`):

```typescript
// workspace.service.ts
editorMode = signal<'builtin' | 'vscode'>('builtin');

setEditorMode(mode: 'builtin' | 'vscode'): void {
  this.editorMode.set(mode);
}
```

Load preference on service init:
```typescript
constructor() {
  // Load editor preference from settings API
  this.settingsService.getEditorPreference().subscribe({
    next: (resp) => this.editorMode.set(resp.editor === 'vscode' ? 'vscode' : 'builtin'),
    error: () => this.editorMode.set('builtin'),
  });
}
```

### VsCodeViewerComponent (Rev 2 — signal inputs, correct postMessage, sandbox)

```typescript
import { Component, signal, computed, input, effect, viewChild, ElementRef } from '@angular/core';

@Component({
  selector: 'app-vscode-viewer',
  standalone: true,
  template: `
    <div class="vscode-container">
      @if (loading()) {
        <div class="vscode-loading">
          <mat-spinner diameter="40"></mat-spinner>
          <p>Starting VS Code Server...</p>
        </div>
      }
      @if (error()) {
        <div class="vscode-error">
          <p>VS Code Server is not running.</p>
          <button mat-raised-button (click)="goToSettings()">Configure in Settings</button>
        </div>
      }
      <iframe
        #iframe
        [src]="iframeUrl()"
        class="vscode-iframe"
        [class.loaded]="!loading() && !error()"
        (load)="onIframeLoad()"
        <!-- W1: sandbox attribute — omit allow-top-navigation -->
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        allow="clipboard-read; clipboard-write; fullscreen"
      ></iframe>
    </div>
  `,
})
export class VsCodeViewerComponent implements OnDestroy {
  // S6: Signal inputs (not @Input) so computed signals track changes
  projectId = input<string>('');
  workdir = input<string>('');  // from validated endpoint (C2)
  
  loading = signal(true);
  error = signal(false);
  private iframeRef = viewChild<ElementRef<HTMLIFrameElement>>('iframe');
  
  // C2: Use validated folder from /api/projects/{id}/vscode-folder
  // The workdir input comes pre-validated from the backend endpoint
  iframeUrl = computed(() => {
    const base = '/vscode/';
    const dir = this.workdir();
    if (!dir) return base;
    return `${base}?folder=${encodeURIComponent(dir)}`;
  });
  
  constructor() {
    // S6: effect watches workdir signal input — no ngOnChanges needed
    effect(() => {
      const dir = this.workdir();
      if (dir) {
        this.openFolder(dir);
      }
    });
  }
  
  onIframeLoad(): void {
    this.loading.set(false);
    // Send the initial folder via postMessage (C3: correct origin)
    this.openFolder(this.workdir());
  }
  
  private openFolder(path: string): void {
    const iframe = this.iframeRef()?.nativeElement;
    if (!iframe || !iframe.contentWindow) return;
    
    // C3: targetOrigin MUST be absolute URL, not relative path
    // HTML spec requires absolute origin — relative paths are silently dropped
    iframe.contentWindow.postMessage(
      {
        type: 'openFolder',
        path: path,
      },
      window.location.origin  // C3: absolute origin (same-origin via proxy)
    );
  }
  
  goToSettings(): void {
    // Navigate to settings page
    this.router.navigate(['/settings']);
  }
  
  ngOnDestroy(): void {
    // N3: Clear debounce timer to prevent fire-after-destroy
    if (this._reloadTimer) clearTimeout(this._reloadTimer);
  }
}
```

### C3 Fix: postMessage targetOrigin

**Problem**: `targetOrigin='/vscode/'` is a relative path. HTML spec requires an **absolute URL**. Browsers silently drop the message.

**Fix**: Use `window.location.origin`:
```typescript
// WRONG (C3 bug — message silently dropped):
iframe.contentWindow.postMessage(payload, '/vscode/');

// CORRECT (C3 fix — absolute origin):
iframe.contentWindow.postMessage(payload, window.location.origin);
```

Since the iframe loads `/vscode/` which is same-origin (proxied by our FastAPI), `window.location.origin` is the correct target.

### W1 Fix: iframe sandbox Attribute

The proxy sets a controlled CSP (Phase 2, W1). The iframe also needs a `sandbox` attribute for defense-in-depth:

```html
<!-- W1: sandbox — omit allow-top-navigation to prevent iframe from navigating parent -->
<iframe
  sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
  allow="clipboard-read; clipboard-write; fullscreen"
/>
```

- `allow-scripts`: VS Code needs JavaScript
- `allow-same-origin`: VS Code needs same-origin access for WebSocket/API
- `allow-forms`: VS Code has search inputs
- `allow-popups`: VS Code may open new windows
- **NOT `allow-top-navigation`**: Prevents the iframe from hijacking the parent page

### S6 Fix: Signal Inputs Instead of @Input

**Problem**: `workdir` was `@Input` (decorator-based), so `iframeUrl` computed signal wouldn't track changes to it. Angular 21 supports `input()` signal functions.

**Fix**: Use `input<string>()` instead of `@Input()`. Then `effect()` watches the signal:
```typescript
// S6: Signal input — reactive, tracked by computed/effect
workdir = input<string>('');

// No ngOnChanges needed — effect reacts automatically
constructor() {
  effect(() => {
    const dir = this.workdir();
    if (dir) this.openFolder(dir);
  });
}
```

**Debounce reloads**: If workdir changes rapidly (e.g., user spamming project tabs), debounce the `openFolder` call. **N3: Clear the timer in `ngOnDestroy()` to prevent fire-after-destroy.**
```typescript
private _reloadTimer: any = null;

constructor() {
  effect(() => {
    const dir = this.workdir();
    if (this._reloadTimer) clearTimeout(this._reloadTimer);
    this._reloadTimer = setTimeout(() => {
      if (dir) this.openFolder(dir);
    }, 300);  // S6: debounce 300ms
  });
}

ngOnDestroy(): void {
  // N3: Clear debounce timer to prevent fire-after-destroy
  if (this._reloadTimer) clearTimeout(this._reloadTimer);
}
```

**Null-guard for system default project**: If `projectId` is `SYSTEM_DEFAULT_PROJECT_ID`, there's no real workdir:
```typescript
iframeUrl = computed(() => {
  const dir = this.workdir();
  const pid = this.projectId();
  // S6: null-guard for system default project (no real directory)
  if (!dir || pid === 'system-default' || pid === '') return '/vscode/';
  return `${base}?folder=${encodeURIComponent(dir)}`;
});
```

### Workspace Template Switching

Modify `workspace.component.ts` template (~line 166):

**Before:**
```html
<app-code-viewer [projectId]="projectId"></app-code-viewer>
```

**After:**
```html
@switch (editorMode()) {
  @case ('builtin') {
    <app-code-viewer [projectId]="projectId"></app-code-viewer>
  }
  @case ('vscode') {
    <!-- S6: pass workdir as signal input binding -->
    <app-vscode-viewer [projectId]="projectId" [workdir]="validatedWorkdir()"></app-vscode-viewer>
  }
}
```

### C2: Workdir Resolution via Validated Endpoint

**NOT** `project.main_directory` directly. Use the Phase 3 validated endpoint:

```typescript
// In WorkspaceComponent
validatedWorkdir = signal<string>('');

loadValidatedWorkdir(projectId: string): void {
  // C2: Use pre-validated path from dedicated endpoint
  this.http.get<{folder: string, encoded: string}>(
    `/api/projects/${projectId}/vscode-folder`
  ).subscribe({
    next: (resp) => this.validatedWorkdir.set(resp.folder),
    error: () => this.validatedWorkdir.set(''),  // fallback to no folder
  });
}
```

Call this when `projectId` changes (in the `loadProject` flow).

### Project Switching Flow (C2 + S6)

When the user switches project tabs:
1. `ChatComponent.tabWorkspaceEffect` updates `workspaceProjectId`
2. `WorkspaceComponent.projectId` setter triggers `loadProject()`
3. `loadProject()` calls `loadValidatedWorkdir(projectId)` — **C2: fetches pre-validated path**
4. `validatedWorkdir` signal updates → `VsCodeViewerComponent.workdir` signal input updates
5. `effect()` in VsCodeViewerComponent fires → calls `openFolder()` with correct path
6. `openFolder()` sends postMessage with **`window.location.origin`** (C3 fix)

**Fallback**: If `postMessage` doesn't work with code-server, the `?folder=` URL parameter in `iframeUrl` computed handles it (causes iframe reload, but always works).

### Angular Effect Safety
When adding editor-mode reactivity, follow the proven pattern from `tabWorkspaceEffect`:

```typescript
// In ChatComponent or WorkspaceComponent
effect(() => {
  // Read ALL dependent signals unconditionally BEFORE any if-branch
  const showWorkspace = this.showWorkspace();
  const projectId = this.activeProjectId();
  const editorMode = this.workspaceService.editorMode();  // NEW dependency
  
  if (!showWorkspace || projectId === 'All' || !projectId) {
    this.workspaceProjectId.set(null);
    return;
  }
  // ... rest of logic
});
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `VsCodeViewerComponent` with **signal inputs** (S6) | `input<string>()` for projectId + workdir; iframe wrapper; loading/error states | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 2 | Implement **C3: correct postMessage origin** | Use `window.location.origin` (absolute URL), NOT `/vscode/` (relative) | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 3 | Add **W1: iframe sandbox attribute** | `sandbox="allow-scripts allow-same-origin allow-forms allow-popups"` (omit `allow-top-navigation`) | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 4 | Implement **S6: signal input + effect** for workdir changes | `input()` signal + `effect()` with debounce; null-guard for system default project | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 5 | Implement **C2: folder opening via validated path** | `?folder=` URL param using validated workdir from `/api/projects/{id}/vscode-folder` | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 6 | Add `editorMode` signal to WorkspaceService | `'builtin' \| 'vscode'` signal, load from settings API on init | `frontend/src/app/services/workspace.service.ts` |
| 7 | Modify workspace template for editor switching | `@switch` on `editorMode()` rendering code-viewer vs vscode-viewer | `frontend/src/app/pages/workspace/workspace.component.ts` |
| 8 | Implement **C2: validated workdir resolution** | Fetch from `/api/projects/{id}/vscode-folder`; pass to VsCodeViewerComponent | `frontend/src/app/pages/workspace/workspace.component.ts` |
| 9 | Implement project switch sync | When `projectId` changes → fetch validated workdir → update signal input | `frontend/src/app/pages/workspace/workspace.component.ts` |
| 10 | Handle VS Code server not running | If proxy returns 503, show "Configure in Settings" button | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 11 | Add iframe loading/error states | Spinner during load, error fallback if iframe fails | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` |
| 12 | Add SCSS styles | Full-height iframe, loading overlay, error state, responsive container | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.scss` |
| 13 | Write unit tests | Test signal inputs, postMessage with **correct origin** (C3), URL computation, loading/error states, debounce (S6) | `frontend/src/app/components/vscode-viewer/vscode-viewer.component.spec.ts` |
| 14 | Update WorkspaceComponent tests | Verify editor switching renders correct component; validated workdir fetched (C2) | `frontend/src/app/pages/workspace/workspace.component.spec.ts` |
| 15 | E2E test | Full flow: settings → select VS Code → workspace shows iframe → switch project → folder updates | Manual / Playwright |

## Key Files
- `frontend/src/app/components/vscode-viewer/vscode-viewer.component.ts` — **NEW**: Iframe wrapper (~200 lines)
- `frontend/src/app/components/vscode-viewer/vscode-viewer.component.scss` — **NEW**: Styles (~35 lines)
- `frontend/src/app/services/workspace.service.ts` — **MODIFY**: Add `editorMode` signal (~15 lines)
- `frontend/src/app/pages/workspace/workspace.component.ts` — **MODIFY**: Template switch + validated workdir resolution (~30 lines)
- `frontend/src/app/components/vscode-viewer/vscode-viewer.component.spec.ts` — **NEW**: Unit tests
- `frontend/src/app/pages/workspace/workspace.component.spec.ts` — **MODIFY**: Editor switching tests

## Constraints
- **C3: postMessage targetOrigin MUST be `window.location.origin`** — NOT a relative path like `/vscode/`. HTML spec requires absolute URL; relative paths are silently dropped by browsers.
- **W1: iframe MUST have `sandbox` attribute** — `allow-scripts allow-same-origin allow-forms allow-popups`. Omit `allow-top-navigation` to prevent parent hijack.
- **S6: Use signal inputs (`input()`), NOT `@Input()`** — so `computed` and `effect` track workdir changes. Debounce rapid changes (300ms). Null-guard for system default project.
- **C2: Never use `project.main_directory` directly** — always fetch the pre-validated path from `GET /api/projects/{id}/vscode-folder`.
- **Angular effect dependency-tracking**: Read ALL signals unconditionally before any `if`/`@if` branch — this codebase has a history of stuck-workspace bugs from conditional signal reads
- **Same-origin proxy**: The iframe loads `/vscode/` which is same-origin (proxied by our FastAPI) — no cross-origin issues. `postMessage` target origin = `window.location.origin`
- **Loading state**: Show spinner until iframe `load` event fires; VS Code is slow to start (~3-5s)
- **Fallback graceful degradation**: If code-server not installed/running, show actionable error (link to Settings), not a blank iframe
- **Project switch**: Prefer `?folder=` URL param over postMessage for reliability (postMessage support varies by code-server version)
- **No CodeMirror teardown needed**: When switching to VS Code mode, CodeMirror is simply not rendered (`@switch` removes it from DOM); its state is preserved by `editStateMap` for when user switches back

## Deliverables
- [ ] `VsCodeViewerComponent` with **signal inputs** (S6), iframe, loading/error states
- [ ] **C3: postMessage uses `window.location.origin`** (absolute URL)
- [ ] **W1: iframe has `sandbox` attribute** (no `allow-top-navigation`)
- [ ] **S6: workdir is signal input** with debounce + null-guard
- [ ] **C2: Folder path from validated endpoint** (not raw `main_directory`)
- [ ] `editorMode` signal in WorkspaceService synced with settings
- [ ] Workspace template switches between CodeMirror and VS Code iframe
- [ ] Project switching opens correct folder in VS Code (pre-validated path)
- [ ] Error handling when code-server not running (actionable UI)
- [ ] Loading spinner during iframe load
- [ ] SCSS styles for full-height iframe container
- [ ] Unit tests for component behavior (including C3 origin check)
- [ ] Workspace component tests updated for editor switching

## Testing Strategy

### Unit Tests (Phase 5)
- **VsCodeViewerComponent**:
  - `projectId` / `workdir` signal inputs trigger folder open
  - **C3**: `postMessage` called with `window.location.origin` as targetOrigin (verify mock)
  - **C3**: `postMessage` NOT called with `/vscode/` (relative path)
  - `iframeUrl` computed correctly with/without workdir
  - **S6**: workdir signal input triggers effect (not ngOnChanges)
  - **S6**: debounce delays `openFolder` by 300ms
  - **S6**: null-guard returns base URL when projectId is system default
  - **N3**: `ngOnDestroy` clears `_reloadTimer` (no fire-after-destroy)
  - **W1**: iframe element has `sandbox` attribute with correct tokens
  - `onIframeLoad()` sets `loading = false`
  - Error state when iframe fails to load
- **WorkspaceService**:
  - `editorMode` initialized from settings API response
  - `setEditorMode()` updates signal
- **WorkspaceComponent**:
  - `@switch` renders `app-code-viewer` when mode='builtin'
  - `@switch` renders `app-vscode-viewer` when mode='vscode'
  - **C2**: `loadValidatedWorkdir()` calls `/api/projects/{id}/vscode-folder`
  - `validatedWorkdir` signal passed to VsCodeViewerComponent

### Test command
```bash
cd frontend && npx jest --test-path-pattern="vscode-viewer|workspace"
```

### E2E / Manual Verification
Full end-to-end flow (requires all phases complete + code-server installed):
1. Go to Settings → Editor → select "VS Code" → Apply
2. Verify status badge shows "Running"
3. Open a project → click workspace icon
4. Verify VS Code UI renders in the overlay (not CodeMirror)
5. **Check DevTools console**: verify no CSP errors, no "Failed to execute postMessage" errors (C3)
6. Open a file in VS Code → verify it loads
7. **Open a terminal** (`` Ctrl+` ``) → verify binary WS frames work (C4 proxy)
8. Switch to another project tab
9. Verify VS Code opens the new project folder (from validated endpoint — C2)
10. Go back to Settings → select "Built-in" → Apply
11. Open workspace → verify CodeMirror editor is back
12. Switch between editors rapidly → verify no stuck states (effect safety)

## Integration Notes

### postMessage Protocol (best-effort enhancement)
If code-server supports receiving postMessage commands:
```typescript
// C3: targetOrigin is absolute URL
iframe.contentWindow.postMessage({
  type: 'openFolder',
  path: '/validated/path/to/project',  // C2: pre-validated path
}, window.location.origin);
```

**Verify support**: Check code-server documentation/version. If unsupported, the `?folder=` URL parameter is the reliable fallback (causes iframe reload but always works).

### Performance Considerations
- **Iframe persistence**: Once loaded, keep the iframe alive across project switches (use postMessage/URL param, not destroy/recreate). Destroying the iframe reloads VS Code (~3-5s each time).
- **Memory**: VS Code iframe is heavy (~200-400MB in browser). Only render when editor mode = vscode AND workspace is shown.
- **CodeMirror state preservation**: When switching to VS Code mode, CodeMirror's `editStateMap` state persists in memory. Switching back restores it without data loss.
- **S2 (deferred)**: Monitor WS compression CPU usage. If profiling shows it's hot, add `compression=None` on the upstream `websockets.connect()` leg.
