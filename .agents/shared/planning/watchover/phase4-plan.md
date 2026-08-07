# Phase 4: Frontend Integration

## Objective

Add the Watchover button to the chat UI (LEFT of the Think button), wire the
toggle signal + localStorage persistence + onToggle handler, call the `POST
/instances/{id}/watchover` endpoint on toggle, and surface SSE events for denial
and termination feedback so the operator sees watchover activity in real time.

## Files to Modify

| # | Path | What Changes |
|---|------|--------------|
| M4.1 | `frontend/src/app/pages/chat/chat.html:49-85` | Add a new "Watchover" button LEFT of the Think button in the `.toggle-buttons` container. Follow the exact toggle pattern: Angular signal binding (`[class.active]`), `(click)` handler, title tooltip. **Reuse callout:** mirrors Think button (`chat.html:64`). |
| M4.2 | `frontend/src/app/pages/chat/chat.ts` (component) | Add `showWatchover = signal(false)` (or the project's signal convention). Add `onToggleWatchover()` handler: toggles signal, persists to `localStorage`, calls the watchover API service. Add `watchoverRequirement` signal/prompt for the requirement text (if not set, use a default or prompt the user). **Reuse callout:** mirrors `onToggleThinking()` / `showThinking` pattern. |
| M4.3 | `frontend/src/app/pages/chat/chat.ts` (component) | Handle SSE events for watchover: denial events (show a toast/badge), termination events (show instance-terminated notification). Subscribe in the existing SSE handler. |
| M4.4 | `frontend/src/app/pages/chat/chat.scss` (or `.css`) | Add `.watchover-btn` styling: distinct color when active (e.g. red or purple to signal "watching"), matching the Think/Tools/System button sizing. |
| M4.5 | `frontend/src/app/services/` (API service) | Add `setWatchover(instanceId, enabled, requirement)` method to the existing instance/chat API service. Calls `POST /instances/${instanceId}/watchover` with body `{enabled, requirement}`. |

## Files to Create

| # | Path | Purpose |
|---|------|---------|
| C4.1 | (none — all changes are in existing files following established patterns) | — |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T4.1 | Add the Watchover button to `chat.html` in the `.toggle-buttons` div, positioned LEFT of the Think button (`chat.html:64`). Use a distinct icon/emoji (e.g. 👁️ Watchover). Bind `[class.active]` to the `showWatchover()` signal, `(click)` to `onToggleWatchover()`. **Reuses the Think/Tools/System toggle pattern (`chat.html:64-85`).** | Phase 3 (T3.7 endpoint exists) | Button renders in the correct position; clicking it calls `onToggleWatchover()`. |
| T4.2 | Implement `onToggleWatchover()` in `chat.ts`: (1) toggle `showWatchover` signal; (2) persist to `localStorage` key `watchover-{instanceId}`; (3) if turning ON and no requirement is set, use a default requirement or open a simple prompt/dialog for the user to enter one; (4) call `apiService.setWatchover(instanceId, enabled, requirement)`. Handle loading state + error toast on API failure. **Reuses `onToggleThinking()` / `showThinking` + localStorage pattern.** | T4.1 | Toggle persists across reloads; API call fires on toggle; requirement is captured (default or user-entered). |
| T4.3 | Add `.watchover-btn` CSS in `chat.scss`: active state uses a warning color (red/purple) to signal active watching; inactive state matches the other toggle buttons. Ensure responsive layout is not broken by the additional button. | T4.1 | Button styles render correctly; active state is visually distinct; no layout breakage at various viewport widths. |
| T4.4 | Add SSE event handling for watchover in the existing SSE subscription (`chat.ts`): on `watchover_denial` event → show a toast/badge "Tool call blocked: {reason}" (amber/red); on `watchover_terminate` event → show "Instance terminated: 3 denials reached" notification + disable the chat input. **Reuses existing `SseService` subscription pattern.** | Phase 3 (SSE events emitted from backend — coordinate with Phase 3/5) | Denial events show real-time feedback; termination event shows a clear notification and disables further interaction. |
| T4.5 | Add `setWatchover(instanceId, enabled, requirement)` to the frontend API service. `POST /instances/${instanceId}/watchover` with body `{enabled, requirement}`. Return the API response; throw on HTTP error. | Phase 3 (T3.7 endpoint) | API service method calls the correct endpoint; unit/integration test verifies the request shape. |
| T4.6 | Restore watchover state on component init: read `localStorage` key on load; if watchover was active, verify with a backend GET (or the instance metadata) and set the signal accordingly. Sync the button's active state with the actual backend state. | T4.2 | On page reload, if watchover was active, the button shows active and the state matches the backend. |
| T4.7 | **Add watchover fields to Instance API schema + frontend model (TD-12).** Add `watchover_enabled: bool`, `watchover_context: str \| None`, `watchover_denial_count: int` to the Instance response schema (`daemon/routers/instances.py` InstanceInfo / `schemas.py`). Add corresponding fields to the frontend Instance model (`frontend/src/app/models/index.ts`). The FE must not rely on localStorage alone — it reads `watchover_enabled` from the API response to restore toggle state reliably. | T4.6 | API response includes watchover fields; frontend model updated; FE restores toggle from API response, not localStorage-only. |

## Coupling

- **Tight with: Phase 3** — consumes the `POST /watchover` endpoint (T3.7) and the SSE events.
- **Independent of: Phases 1, 2** — the frontend does not interact with the graph directly.

## Reuse Callouts

| Pattern | Source | Reused For |
|---------|--------|------------|
| Think/Tools/System toggle buttons | `chat.html:64-85` | Watchover button: same HTML structure, signal binding, click handler |
| Angular signal + localStorage | `chat.ts` (`showThinking`, `onToggleThinking`) | `showWatchover`, `onToggleWatchover` |
| `SseService` per-instance EventSource | `chat.ts` SSE subscription | Watchover denial/termination events |
| Instance API service | existing instance service methods | `setWatchover()` API call |

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P4-R1 | The requirement input UX is awkward — a simple toggle doesn't capture a text requirement. | Medium | T4.2: when turning ON, open a small dialog/prompt for the requirement. Default to a sensible requirement ("Block destructive operations") if the user skips. |
| P4-R2 | SSE event names for watchover are not yet defined — Phase 3/5 must emit them. | Low | T4.4: define the event names (`watchover_denial`, `watchover_terminate`) in Phase 3/5 and coordinate. Use the existing SSE event naming convention. |
| P4-R3 | Adding a 4th toggle button overflows the header on narrow viewports. | Low | T4.3: test at common breakpoints; consider icon-only display on narrow screens (matching the workspace toggle button at `chat.html:51`). |
| P4-R4 | Watchover state desyncs between frontend and backend (e.g. instance terminated by 3-strikes but button still shows active). | Medium | T4.6: sync on init from backend state. T4.4: on `watchover_terminate` SSE event, set `showWatchover` to false and disable the button. |

## Exit Criterion

- Watchover button appears LEFT of Think in the chat header.
- Clicking it toggles the API call (activate/deactivate).
- Requirement is captured (default or user-entered) on activation.
- Active state is visually distinct (warning color).
- State persists across reloads and syncs with the backend.
- SSE denial/termination events show real-time feedback.
- No layout breakage on standard viewport widths.
