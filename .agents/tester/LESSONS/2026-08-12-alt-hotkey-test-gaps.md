# LESSON: Alt+` Hotkey Feature — Test Gap Patterns (2026-08-12)

## Feature
Global Alt+` hotkey toggles editor visibility. State lifted from ChatComponent to WorkspaceOverlayService (root singleton). `<app-workspace>` moved to app.html. Global `@HostListener` in app.ts.

Branch: `feature/editor-toggle-hotkey` @ `d09160af`

## Outcome
- ✅ 1,931/1,931 Jest tests pass (no regressions)
- ✅ All 9 edge cases handled correctly in production code
- ⚠️ 4 of 9 edge cases have test gaps (code correct, test missing)

## Key Pattern: Service-Level Tests ≠ Integration Test
The `WorkspaceOverlayService` has excellent unit test coverage (13 tests covering all toggle/hide/show permutations). However, the **service consumer** (app.ts `@HostListener`) has **zero tests** because no `app.spec.ts` exists. Service tests verify the *service logic*, not the *wiring* between the keyboard event and the service call.

**Lesson:** When state is lifted to a root service, always test the **consuming component's integration** with the service, not just the service in isolation.

## Test Gaps Found

### 1. Root component with @HostListener has no spec file
- `app.ts:119-128` has `@HostListener('document:keydown')` for Alt+`
- No `app.spec.ts` exists at all
- Need: synthetic `KeyboardEvent` dispatch + assert `toggle()` called

### 2. SSE [visible] lifecycle untested in workspace.component.spec.ts
- `workspace.component.ts:410-421` has `ngOnChanges` → connect/disconnect SSE
- The spec's host component never binds `[visible]`
- Need: host variant with `[visible]` input, test connect/disconnect on flip

### 3. No duplicate-overlay assertion
- Single `<app-workspace>` in `app.html` (confirmed removed from chat.html)
- No test guards against future re-introduction of duplicates
- Need: App-level smoke test

## Dead Code Found (cosmetic)
1. `app.ts:123` — `=== 'all'` check is unreachable (`activeProjectId()` returns `null`, not `'all'`)
2. `chat.component.ts:1031-1033` — `onWorkspaceHide()` is dead code (template binding moved to app.html)
