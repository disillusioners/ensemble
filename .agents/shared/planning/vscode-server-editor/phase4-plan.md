# Phase 4: Frontend Settings UI & Editor Preference

## Objective
Add an "Editor" section to the Settings page where users choose between "Built-in" and "VS Code" editors. Extend `SettingsService` with editor preference methods. This phase delivers the user-facing preference control.

> **Rev 2 changes**: `VSCodeStatus` interface updated to include `allow_remote` field (from C1 config). No other structural changes — this phase was already sound.

## Coupling
- **Depends on**: Phase 3 (Settings API endpoints must exist — but can mock during frontend dev)
- **Coupling type**: **loose** — frontend only needs the REST API contract; can develop against mocked responses
- **Shared files with other phases**: `frontend/src/app/pages/settings/settings.component.ts` (modify), `frontend/src/app/services/settings.service.ts` (modify)
- **Shared APIs/interfaces**: Consumes `GET/PUT /api/settings/editor`, `GET /api/settings/editor/status`
- **Why this coupling**: Phase 5's editor switching reads the preference set by this phase's UI.

## Context
- **Previous phase delivered**: Backend `GET/PUT /api/settings/editor` + `GET /api/settings/editor/status` endpoints.
- **Settings page**: `frontend/src/app/pages/settings/settings.component.ts` (206 lines) — currently single "Language Preference" section. Uses `SearchableSelectComponent` + conditional custom input.
- **SettingsService**: `frontend/src/app/services/settings.service.ts` (27 lines) — minimal, RxJS only (`getLanguagePreference()`, `setLanguagePreference()`).
- **Settings header menu**: Owned by root `App` component (`app.ts:23`). `settingsMenuItems` signal with `{label, route, icon}` entries. Pattern: `@for (item of settingsMenuItems(); track item.route)`.
- **ConfigSchemaFormComponent**: Ready-made dynamic form (supports text, number, boolean, select). Could render VS Code config fields but NOT needed for MVP — editor choice is a simple radio/select.

## Technical Approach

### Settings Page Layout
Add an "Editor" section below the existing "Language Preference" section:

```html
<!-- settings.component.html -->
<section class="settings-section">
  <h2>Editor</h2>
  <p class="settings-description">Choose your preferred code editor</p>
  
  <app-searchable-select
    [options]="editorOptions()"
    [value]="selectedEditor()"
    (valueChange)="onEditorChange($event)"
    placeholder="Select editor"
  />
  
  @if (selectedEditor() === 'vscode') {
    <div class="vscode-status">
      @if (vscodeStatus()?.status === 'running') {
        <span class="status-badge running">● Running (port {{ vscodeStatus()?.port }})</span>
      } @else if (vscodeStatus()?.status === 'starting') {
        <span class="status-badge starting">◌ Starting...</span>
      } @else if (vscodeStatus()?.status === 'crashed') {
        <span class="status-badge crashed">✕ Crashed — 
          <button (click)="retryStart()">Retry</button>
        </span>
      } @else {
        <span class="status-badge stopped">○ Stopped — press Apply to start</span>
      }
    </div>
  }
  
  <button mat-raised-button color="primary" (click)="saveEditor()" [disabled]="!editorDirty()">
    Apply
  </button>
</section>
```

### SettingsService Extensions
Follow existing RxJS pattern (no signals — keep consistent with current service):

```typescript
// settings.service.ts additions

getEditorPreference(): Observable<{editor: string, vscode: VSCodeStatus}> {
  return this.http.get<{editor: string, vscode: VSCodeStatus}>('/api/settings/editor');
}

setEditorPreference(editor: string): Observable<{editor: string, vscode: VSCodeStatus}> {
  return this.http.put<{editor: string, vscode: VSCodeStatus}>('/api/settings/editor', { editor });
}

getEditorStatus(): Observable<VSCodeStatus> {
  return this.http.get<VSCodeStatus>('/api/settings/editor/status');
}
```

### VSCodeStatus Interface (Rev 2 — includes C1 field)

```typescript
// frontend/src/app/models/index.ts

export interface VSCodeStatus {
  status: 'stopped' | 'starting' | 'running' | 'crashed' | 'stopping';
  port?: number | null;
  pid?: number | null;
  allow_remote?: boolean;  // Rev 2: C1 — shows if remote access is enabled
}
```

### Settings Component State
Add signals to `settings.component.ts`:

```typescript
// New signals
editorOptions = signal<string[]>(['builtin', 'vscode']);
selectedEditor = signal<string>('builtin');
savedEditor = signal<string>('builtin');  // last saved value
editorDirty = computed(() => this.selectedEditor() !== this.savedEditor());
vscodeStatus = signal<VSCodeStatus | null>(null);
statusPollTimer: ReturnType<typeof setInterval> | null = null;

// Lifecycle
ngOnInit() {
  this.loadEditorPreference();
}

ngOnDestroy() {
  this.stopStatusPolling();
}
```

### Apply Flow
```
User selects "VS Code" → editorDirty() = true
User clicks "Apply" → setEditorPreference('vscode')
  → On success: start status polling, update savedEditor
  → On error (503): show install instructions banner
```

### Status Polling
When editor = "vscode" and status is "starting", poll `GET /api/settings/editor/status` every 2 seconds until "running" or "crashed". Stop polling when leaving settings page or switching to "builtin".

### Header Menu Entry
No new menu entry needed — the existing "Settings" entry (`app.ts:54-57`, route `/settings`) already navigates to the settings page which now includes the Editor section.

**Alternative considered**: Add a separate `/settings/editor` route. Rejected — keeping everything on one settings page is simpler and matches the language preference pattern.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define `VSCodeStatus` interface | `{ status, port?, pid?, allow_remote? }` — Rev 2: includes `allow_remote` | `frontend/src/app/models/index.ts` |
| 2 | Extend `SettingsService` | Add `getEditorPreference()`, `setEditorPreference()`, `getEditorStatus()` methods | `frontend/src/app/services/settings.service.ts` |
| 3 | Add editor section to SettingsComponent template | Radio/select for editor choice + status badge + Apply button | `frontend/src/app/pages/settings/settings.component.ts` |
| 4 | Add editor state signals to SettingsComponent | `selectedEditor`, `savedEditor`, `vscodeStatus`, `editorDirty` computed | `frontend/src/app/pages/settings/settings.component.ts` |
| 5 | Implement `loadEditorPreference()` | Call `getEditorPreference()` on init, populate signals | `frontend/src/app/pages/settings/settings.component.ts` |
| 6 | Implement `saveEditor()` / `onEditorChange()` | Call `setEditorPreference()`, handle success/error, start status polling | `frontend/src/app/pages/settings/settings.component.ts` |
| 7 | Implement status polling | `setInterval` poll when status="starting"; stop on running/crashed/page-exit | `frontend/src/app/pages/settings/settings.component.ts` |
| 8 | Implement error handling | 503 → show install instructions; network error → retry prompt | `frontend/src/app/pages/settings/settings.component.ts` |
| 9 | Add SCSS styles | Status badges (running=green, starting=amber, crashed=red, stopped=gray), editor section layout | `frontend/src/app/pages/settings/settings.component.scss` |
| 10 | Write unit tests | Test signal state transitions, service calls (mocked HTTP), status polling lifecycle | `frontend/src/app/pages/settings/settings.component.spec.ts` |

## Key Files
- `frontend/src/app/services/settings.service.ts` — **MODIFY**: Add 3 methods (~25 lines)
- `frontend/src/app/pages/settings/settings.component.ts` — **MODIFY**: Add editor section (~120 lines)
- `frontend/src/app/pages/settings/settings.component.scss` — **MODIFY**: Status badge styles (~40 lines)
- `frontend/src/app/models/index.ts` — **MODIFY**: Add `VSCodeStatus` interface (~6 lines, includes `allow_remote`)
- `frontend/src/app/pages/settings/settings.component.spec.ts` — **MODIFY**: Add editor tests

## Constraints
- **Follow existing RxJS pattern** in SettingsService — no signals in the service layer (consistency with `getLanguagePreference`)
- **Use signals in component** for reactive UI state (consistent with Angular 21 + existing settings signals)
- **Cleanup status polling** in `ngOnDestroy()` — no orphaned intervals
- **SearchableSelectComponent** reuse for editor dropdown (same component as language pref)
- **Error UI must be informative** — show code-server install command on 503, not a generic error
- **localStorage caching** — cache editor preference like language pref (`settings-editor-preference` key) for instant UI load

## Deliverables
- [ ] Settings page shows "Editor" section with Built-in / VS Code options
- [ ] Selecting an editor and clicking Apply calls the API and updates status
- [ ] VS Code status badge shows running/starting/crashed/stopped states
- [ ] Status polling works during startup, stops on terminal states
- [ ] Error handling shows install instructions on 503
- [ ] Editor preference cached in localStorage for fast page load
- [ ] `VSCodeStatus` interface includes `allow_remote` field
- [ ] Unit tests for component state + service calls

## Testing Strategy

### Unit Tests (Phase 4)
- **Initial load**: `getEditorPreference()` called, `selectedEditor` populated from response
- **Editor change**: Selecting "vscode" sets `editorDirty() = true`
- **Apply success**: `setEditorPreference('vscode')` called, status polling starts, `savedEditor` updated
- **Apply error (503)**: Install instructions shown, `savedEditor` unchanged
- **Status polling**: Starts on "starting" status, stops on "running"/"crashed"
- **Cleanup**: `ngOnDestroy` clears polling interval
- **VSCodeStatus**: Verify `allow_remote` field parsed from response

### Test command
```bash
cd frontend && npx jest --test-path-pattern=settings
```

### Manual Verification
1. Navigate to Settings page
2. Verify "Editor" section appears below "Language Preference"
3. Select "VS Code", click Apply
4. Verify status badge shows "Starting" → "Running" (requires code-server installed)
5. Select "Built-in", click Apply
6. Verify status shows "Stopped"
7. If code-server NOT installed: verify install instructions appear
