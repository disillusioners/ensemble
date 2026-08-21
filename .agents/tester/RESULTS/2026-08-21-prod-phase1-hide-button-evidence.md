# PROD Phase 1 Evidence: hide-button symptom at instance-detail URL (READ-ONLY)

Date: 2026-08-21T12:17Z | Target: http://localhost:9797/projects/83da04de-a410-4fb5-9e92-251a99d28a52/instances/cba392f7-49c8-403c-852d-f7c260ae4606
Worker: 41792d37 (pack-4 e2e worker, strict read-only protocol, zero clicks/writes confirmed)
Artifacts: frontend/test-results/prod-phase1-01-fresh-load.png, prod-phase1-02-after-reload.png, /tmp/prod-phase1-raw.json (810 lines)

## Symptom reproduction — CONFIRMED verbatim on PROD v0.10.5
Both fresh load AND reload (symptom moment), identical state:

| Observation | Fresh load | After reload |
|---|---|---|
| `.overlay-hide-btn` present | 1 | 1 |
| Affordance | visibility_off / "Hide overlay" (title=null, display=block) | same |
| `app-chat` | display:flex, offsetParent≠null | same |
| Messages | 50 `.message-row` / 50 `.message-content`, no spinner, no empty-state | same |
| Chat header | instance=cba392f7… / agent=Leader | same |
| First message | `[SYSTEM CONTEXT: Related Project]…` (this very conversation) | same |
| `app-workspace` | display:none, inner container EMPTY (no toolbar/file-tree/editor pane) | same |
| Plane overlay | display:none | same |
| URL | unchanged, HTTP 200, no redirect | same |

## Factual decomposition (no fix proposals)
- The button's presence + HIDE affordance on v0.10.5 coincides with the CHAT overlay being VISIBLE (instance detail rendered with content). The EDITOR (workspace) did NOT load: mounted shell display:none, empty interior, and NO workspace/overlay key exists in localStorage — workspace state is in-memory singleton only (WorkspaceOverlayService), so after reload workspaceProjectId is necessarily null (not recoverable).
- Read: on v0.10.5, at this URL, the button tracks the chat-visible state, not the editor. Whether that is the defect vs. pre-fix intended behavior is for the git audit (fixes ed6aea34 + 0d15433b are on latest; prod is tag v0.10.5, build main-5QK2S7LY.js).

## localStorage (complete, read-only)
- `ensemble-project-tabs`: openTabs [all, 83da04de…], activeTabId=83da04de… (215 chars)
- `ensemble-instances-view-state`: {"activeInstanceId":"cba392f7…","activeProjectId":"83da04de…"} (116 chars)
- `ensemble-show-thinking/show-toolcalls/show-system-prompt`: "false" ×3
- NO workspace/overlay/chat-persistence keys.

## Console errors (verbatim, by phase)
- Initial: 1 sandbox warning (iframe allow-scripts+allow-same-origin); plane.ensem.dev React #418 hydration error + 401 on /api/users/me/; 49× pageerror React #418 + 1× #423 (all from Plane iframe MessagePort cascade).
- Reload: +2 SSE errors `[SSE] Connection error` / `[SSE] EventSource connection error` (chunk-UPIWME3K.js) before the same iframe cascade.
- ZERO errors from Angular app chunks (:9797) — none from overlay/chat/hide-btn code path.

## Build version evidence
- Header `.health-status .version` = **v0.10.5**; document.title="Frontend"; no meta/window version globals; Angular devtools API disabled (prod).
- main bundle: `main-5QK2S7LY.js`; styles `styles-X6HLAMWD.css`; 31+ lazy chunks from :9797.
- SPA shell confirmed (app-root populated, app-container), HTTP 200 both phases, no router redirect.

## Leftovers (for Phase 2 / cleanup decision)
- Untracked observation scripts: frontend/scripts/prod-phase1-observe.cjs, -probe-msg.cjs, -probe-ws.cjs (not committed; candidates for deletion or reuse in Phase 2 dev repro).
- /tmp/prod-phase1-raw.json (ephemeral).

Phase 2 (dev repro + fix verification) awaits the git audit outcome — not started per brief.
