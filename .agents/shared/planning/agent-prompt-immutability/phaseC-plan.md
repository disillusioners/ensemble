# Phase C: Minimal Frontend Review/Approve UI (GATED)

Date: 2026-10-13
Author: planner[v2] via plan-creation worker
Status: **GATED — execution conditional on gate conditions below**
Research refs: `.agents/shared/planning/agent-prompt-immutability/research-findings-db-api.md` §5 (frontend naturalness), `…-inner-soul.md`, `…-prompt-loading.md`
Upstream: `.agents/shared/planning/agent-prompt-immutability/phaseB-plan.md` (REST API + state machine + selection pointer)
Prior art: `frontend/src/app/services/skill-bank.service.ts:38-75` (service pattern), `frontend/src/app/services/` house style, `frontend/src/app/pages/skill-bank/` (page pattern).

---

## ⚠️ GATE CONDITIONS (decide BEFORE opening this phase)

This phase is explicitly **conditional**. Do NOT begin execution until all three gate conditions below have been re-evaluated and the dispatcher (planner / Ari) has issued an explicit "proceed with Phase C" signal. If the gate fails, **Phase B's REST API is the complete usable approval path** — operators can list / get / diff / approve / reject / set-selection via `curl` / HTTP clients. No frontend surface is required to ship the immutability regime.

### Gate Condition 1 — Phase B REST API is MERGED (not just drafted)

- The 12 endpoints listed in Phase B §"Phase B's contract on Phase C" must be live in `daemon/routers/prompts.py`, registered at `daemon/api.py:3575-3620`, and reachable on the operator's host.
- Reason: a frontend that points at a stubbed or in-flight API ships a broken surface. The REST contract is the Phase C contract; the frontend consumes it.
- Verification (S14/O-11 — the two postures are DISTINCT checks, never conflated):
  - **Auth-ON smoke (run in the Gate 1b environment)**: `curl -s http://localhost:8079/api/prompts/versions` with a valid session returns 200 `{"items": [], "total": 0}`.
  - **Auth-OFF check**: on an auth-OFF deployment the SAME call returns **503 `AUTH_NOT_CONFIGURED`** (D3 fail-closed) — never expect the 200 shape from the auth-OFF posture.

### Gate Condition 2 — Phase B test suite is GREEN on both engines

- All T1, T3, T5, T12, T13, T14, T15 acceptance criteria from Phase B pass on SQLite AND PostgreSQL.
- The 28-agent no-regression CI gate (Phase B T13) is GREEN.
- Reason: a frontend layered over an unstable backend doubles the surface area to debug. Stable backend is a prerequisite.

### Gate Condition 1b — auth-ON smoke environment (S14/O-11)

- The UI is untestable without an OIDC session: a dev environment with `auth.enabled=True` + a local operator account must exist before Phase C starts. This is the environment Gate 1's auth-ON curl smoke runs against.

### Gate Condition 2b — frontend toolchain check (S14/O-11)

- `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` passes — the LOCAL devDependency binary ONLY. **NEVER bare `npx tsc` outside `frontend/`** (2026-10-03 critical note: the npm package `tsc` ≠ TypeScript; bare `npx` outside a package dir fetchs + executes a registry package).

### Gate Condition 3 — Remaining overnight budget + user decision

- Per the dispatch (F7): "minimal frontend = phase 2 GATED on naturalness + remaining overnight budget".
- If the overnight commission clock has expired (the 2026-10-06 overnight authorization was user-bounded per the project's metadata), the gate fails by default and Phase C is **deferred to the next user-touch** — operators use the REST API until then.
- If budget remains AND the dispatcher has re-affirmed "proceed with Phase C", the gate passes.

---

## Objective

Build the **minimal review surface** that lets an authenticated human operator (browser lane, `auth_method == "session"`) inspect pending prompt-version drafts, see what would change (diff vs effective), and approve / reject / set-selection WITHOUT having to reach for `curl`. The surface is **deliberately minimal** — list + detail + diff + actions — and slots in beside the existing Skills / Skill Bank pages using the project's established `@Injectable` service + signals + Angular Material pattern.

Outcome is testable as: a logged-in operator opens the page, sees all pending drafts grouped by `(agent, artifact)`, opens one, sees the diff vs the current effective content, clicks `Approve`, the row transitions to `approved`, an audit event is stamped with their subject, and the next `load_and_cache_prompt` for that agent picks up the new content.

---

## Scope

### In Scope (only if gates pass)

1. **`PromptVersionService`** (`frontend/src/app/services/prompt-version.service.ts`) — `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, `API_BASE = '/api/prompts'`, signals for `items / loading / error`. Mirrors `skill-bank.service.ts:38-75`. Methods: `list(filter)`, `get(id)`, `diff(id, against)`, `approve(id)`, `reject(id)`, `setSelection({agent, versionTag, artifact, versionId})`, `clearSelection({agent, versionTag, artifact})`, `submit(versionId)` (machine-lane), `retract(versionId)` (machine-lane), `audit(filter)`.
2. **Route registration** — `frontend/src/app/app.routes.ts` (or the equivalent routing module) gains `/prompts` and `/prompts/:id` lazy-loaded routes. Per the Frontend Architecture blueprint: "Routes lazy-load per project" — Phase C follows the same pattern, defaulting to the active project (no per-project scoping for prompt versions in this iteration; `decisions.md` may add it).
3. **Page: Prompt Versions List** (`frontend/src/app/pages/prompts/prompts-list/`) — table grouped by `(agent_id, version_tag, artifact)`, state filter (`pending_approval` default), pagination (`limit/offset` per `skill-bank.service.ts:63-75` strip-empty-filters), row-click → detail page.
4. **Page: Prompt Version Detail** (`frontend/src/app/pages/prompts/prompts-detail/`) — content preview (monospace text block), diff section (server-rendered `unified_diff`, displayed with simple line-numbering; no semantic-diff library), action buttons (`Approve` / `Reject` / `Set as selected` / `Clear selection`), audit-event timeline (last 50 events for this `(agent_id, version_tag, artifact)`).
5. **Menu wiring** — add a "Prompt Versions" entry to the operator navigation (header menu / sidebar per the existing layout), gated by the `prompts:read` scope (render the entry; let the per-endpoint scope check return 403 if the operator lacks `prompts:approve`).
6. **Auth-aware UX** — when the operator lacks `prompts:approve`, render the action buttons disabled with a tooltip ("`prompts:approve` scope required"); when `auth_method != "session"`, the buttons are hidden entirely (machine lane cannot approve; per D2). When auth is OFF, the page shows a banner with the AUTH_NOT_CONFIGURED guidance and disables all actions.
7. **State-machine UI feedback** — buttons reflect row state (`pending_approval` → show Approve/Reject; `approved` → show "Selected" indicator + Clear-selection if currently selected; `rejected` → show "Retract" / "Re-draft" buttons only if submitted by the current operator's instance; `draft` → "Submit" button visible to machine lane only).
8. **Selection pointer display** — list page shows a star / pin icon on the currently-selected version per `(agent, version_tag, artifact)`; detail page has a "Set as selected" toggle (visible to operators with `prompts:approve`).
9. **Empty / loading / error states** — mirror the skill-bank page: skeleton loader, empty-state message ("No pending versions"), error snackbar on mutation failures.

### Out of Scope

- **Inline editing** — operators do not draft new versions in the UI. Drafts come from agent submissions via `inner_soul` / `agent_mother`. The UI is review-only.
- **Bulk approve / reject** — one action per row. Bulk operations are REST-call ergonomics, not in scope.
- **Comments / threaded discussion on drafts** — no annotation layer.
- **Real-time push notifications** — polling on the list page is sufficient (matches `notification.service.ts`'s SSE pattern but is NOT added in this phase). Operators can refresh manually.
- **Cross-agent bulk operations** — no "approve all pending for this agent" affordance.
- **Per-project scoping** — the prompt-version surface is global (no per-project filter in this iteration; `decisions.md` may add it).
- **Audit-log search / advanced filters** — the audit timeline shows the last 50 events for the row's key; full-text audit search is a separate surface.
- **Notification when a draft I submitted is approved/rejected** — out of scope; operator's instance discovers via its own polling on `audit` endpoint.
- **Dark mode toggle / theme tweaks** — UI uses the existing Material theme; no new theme tokens.
- **Mobile-specific layout** — desktop-first; responsive only insofar as Material's defaults are.

---

## Binding Forks (inherited from dispatch)

- **F7** (approval path split) — daemon-side approval endpoints are mandatory core (Phase B); minimal frontend is gated Phase 2 (this phase). This gate is the F7 implementation.
- **F4** (approver = USER ONLY) — UI gates `Approve` / `Reject` buttons on `auth_method == "session"`.
- **F1 / F9** — UI consumes the REST surface; does NOT change seeding / keying behavior.

---

## Phases Coupling

| | Phase A | Phase B | Phase C (this) |
|---|---|---|---|
| Phase A | — | (n/a) | (n/a) |
| Phase B | (n/a) | — | supplies REST contract + audit + selection pointer |
| Phase C | (n/a) | consumes REST | — |

**Phase B's contract on us** (binding): endpoints listed in Phase B §"Phase B's contract on Phase C". If Phase B ships a renamed field, the service layer in Phase C is the only adaptation point — pages consume the service, not the HTTP layer.

---

## Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|------------------------|
| D14 | **No new dependencies** — service + pages built with `@angular/common/http`, `@angular/material`, existing routing. `unified_diff` is server-rendered text; the UI displays it in a `<pre>` block with simple CSS line-numbering. | Per the user's dependency-self-install philosophy (project metadata 2026-10-02): don't pull in `diff` / `jsdiff` / `monaco-editor` for a 100-line surface. Server-side `difflib` (Phase B T5) is sufficient; zero new npm packages. | Pull in a diff library (dependency creep); client-side diff (LLM cost when content is large). |
| D15 | **Lazy-loaded routes** — `/prompts` and `/prompts/:id` register via the existing `loadComponent` / `loadChildren` pattern in `app.routes.ts`. Per the Frontend Architecture blueprint's "Routes lazy-load per project" guidance. | Matches the existing page pattern (skill-bank, blueprint, snapshot pages all lazy-load); keeps initial bundle unchanged. | Eager-load (bundle size); per-project sub-routes (over-engineered for a review surface). |
| D16 | **Scope-aware UI** — operators with `prompts:read` see the list + detail; operators with `prompts:read + prompts:approve` see Approve/Reject buttons; operators without `prompts:read` see the page but get 403 from the backend (UI mirrors the 403 with a friendly message). | D2 + D3 require human-lane-only approve + auth-OFF fail-closed; the UI must reflect both without leaking scope information. | Always render all buttons (privilege escalation visible to operator); hide the menu entry entirely (operator doesn't know the feature exists — bad UX for `prompts:read`-only operators). |
| D17 | **Polling cadence** — list page polls every 10s when visible; detail page polls every 5s when open. No SSE channel. | Simplicity; matches `pollingScope` pattern in `chat.component.ts:521-528`. Real-time push (SSE) is a separate commission's worth of work. | WebSocket push (over-engineering); no polling (operators refresh manually — UX regression vs skill-bank page). |
| D18 | **Diff display** — server-side `unified_diff` rendered as plain `<pre>` with no syntax highlighting. Operators who need richer display can copy the diff to clipboard. | Zero-dep decision (D14); matches the skill-bank detail page's plain-text-block display. | Render with `monaco-editor` (new dep); render with `ngx-diff` (new dep); custom highlight (over-engineered). |
| D19 | **State-machine button matrix** mirrors Phase B T5 endpoints: list of `(row_state, button_set)` pairs documented as a TypeScript const in the service. UI never guesses button availability — it derives from the row's state + the operator's scope. | Single source of truth (the service) for the state→button matrix; pages consume the derived signal. | Hard-code buttons per page (drift); render all buttons (privilege escalation visible). |
| D20 | **Selection toggle behavior** — clicking "Set as selected" calls `POST /api/prompts/selections`; if the operator clears the selection (the only approved version was this one), the UI prompts "Clear selection? Resolution will fall back to the latest approved version, then to the file template." before issuing `DELETE /api/prompts/selections/...`. | Selection is a UX-visible state change (which version runs); clearing has user-visible effects. | Silent clear (operator accidentally degrades the agent's resolution); blocked clear (operator can't undo their own action). |
| D21 | **Audit timeline** — 50 most-recent events for the row's `(agent_id, version_tag, artifact)` key; sorted newest-first; columns `actor_subject`, `actor_auth_method`, `event_type`, `created_at`. | Mirrors the drift-events audit-row style (`drift_events` 20261007_000001); bounded result set keeps the page snappy. | Full audit history (potentially large); paginated audit with infinite scroll (over-engineered). |

---

## Tasks

| # | Task | Depends On | Touchpoints (files) | Risks | Acceptance |
|---|------|------------|---------------------|-------|------------|
| T17 | **Create `PromptVersionService`** (`frontend/src/app/services/prompt-version.service.ts`) — `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, `API_BASE = '/api/prompts'`, signals for `items / loading / error`. Methods per scope §1. Mirrors `skill-bank.service.ts:38-75`. State-machine button matrix const per D19. | Gate Conditions 1 + 2 + 3 | new file | R15 | Service imports without runtime error; `list()` calls `/versions` and unwraps `{items, total}` per `skill-bank.service.ts:53-75`; `approve(id)` returns the updated row. |
| T18 | **Create list page** (`frontend/src/app/pages/prompts/prompts-list/prompts-list.component.ts` + `.html` + `.scss`) — table grouped by `(agent_id, version_tag, artifact)`, state filter (default `pending_approval`), pagination (limit 50, offset 0). Row click navigates to detail. Empty / loading / error states per skill-bank pattern. | T17 | new files | R16 | Page renders empty state when no drafts; renders rows when drafts exist; state filter updates the URL query param; clicking a row navigates to `/prompts/:id`. |
| T19 | **Create detail page** (`frontend/src/app/pages/prompts/prompts-detail/prompts-detail.component.ts` + `.html` + `.scss`) — content preview, diff section, action buttons per D19, audit timeline per D21. | T17, T18 | new files | R16, R17 | Approve button calls service + transitions row to `approved`; selection toggle calls `setSelection` / `clearSelection`; rejection requires a `reason` text input (audit-event payload per Phase B: `prompt_audit_events.payload` JSON with `event_type=REJECTED` and a `reason` field — the earlier "D22" reference was dangling, decisions end at D21); audit timeline renders last 50 events newest-first. |
| T20 | **Register routes** — `frontend/src/app/app.routes.ts` (or the project's routing module) gains lazy-loaded routes: `{ path: 'prompts', loadComponent: () => import('./pages/prompts/prompts-list/prompts-list.component').then(m => m.PromptsListComponent) }` and `{ path: 'prompts/:id', loadComponent: ... }`. Per the Frontend Architecture blueprint's "Routes lazy-load per project" precedent. | T18, T19 | `frontend/src/app/app.routes.ts` | none | Routes register without conflict; navigating to `/prompts` lazy-loads the chunk; the SPA catch-all in `daemon/api.py` still serves the shell. |
| T21 | **Menu wiring** — add "Prompt Versions" entry to the operator navigation (header / sidebar per the existing layout). Gate by `prompts:read` scope via the existing scope-display helper. When auth is OFF, the entry still renders but the page shows the AUTH_NOT_CONFIGURED banner (D3 + D16). | T20 | `frontend/src/app/app.html` + nav helper | R18 | Entry visible to operators with `prompts:read`; hidden from operators without (or visible-but-disabled with a tooltip — see D16); entry absent on /plan detail routes per the Frontend Architecture overlay-hide rule. |
| T22 | **Auth-aware UX** — operators with `prompts:approve` see Approve/Reject enabled; operators without see disabled + tooltip ("`prompts:approve` scope required"); operators on non-session lanes (e.g., the chat lane if it ever surfaces) see buttons hidden. Auth-OFF: banner with doc link; all actions disabled. | T19 | `frontend/src/app/pages/prompts/prompts-detail/prompts-detail.component.ts` | R19 | Buttons correctly reflect operator scope; auth-OFF banner renders; tooltips match scope names. |
| T23 | **Polling** — list page polls every 10s when visible; detail page polls every 5s when open. Uses Angular `interval()` + cleanup-on-destroy per the existing pattern in `chat.component.ts:521-528`. | T18, T19 | `frontend/src/app/pages/prompts/prompts-list/`, `frontend/src/app/pages/prompts/prompts-detail/` | R20 | Polling fires when page is visible; stops on hide / navigate-away; no memory leaks; no duplicate concurrent calls. |
| T24 | **Component tests** — Angular Jest tests for the service (mock HttpClient), list page (renders empty + populated states, navigates on row click), detail page (renders action buttons per state + scope, calls service correctly). E2E test via Playwright: operator logs in, opens `/prompts`, approves a pending row, asserts the row state changes and the audit event appears. | T17, T18, T19, T22 | `frontend/src/app/services/prompt-version.service.spec.ts`, `frontend/src/app/pages/prompts/prompts-list/prompts-list.component.spec.ts`, `frontend/src/app/pages/prompts/prompts-detail/prompts-detail.component.spec.ts`, `frontend/e2e/prompts.spec.ts` | R21 | All Jest tests pass; Playwright e2e PASSES against the dev daemon with Phase B API live. |
| T25 | **Operator runbook** — short doc at `.agents/shared/planning/agent-prompt-immutability/operator-runbook.md` (or a wiki entry) explaining: how to log in (existing operator auth flow), how to find pending drafts, how to approve / reject, what `setSelection` does (resolution effect), where to read the audit trail. Cross-link from `daemon/routers/prompts.py` docstring. | T19 | new `.agents/shared/planning/agent-prompt-immutability/operator-runbook.md` (or wiki) | none | Doc exists; cross-link works; morning review ratifies. |

---

## Shared files with other phases (read-only evidence trail)

- `frontend/src/app/services/skill-bank.service.ts:38-75` — service template (T17, D14)
- `frontend/src/app/app.routes.ts` — route registration pattern (T20)
- `frontend/src/app/app.html` — menu wiring pattern (T21)
- `frontend/src/app/app.scss:180-214` — z-index ladder; the prompts page lives at default z (no overlay)
- `frontend/src/app/services/notification.service.ts` — SSE pattern (NOT consumed in this phase; deferred)
- `frontend/src/app/pages/chat/chat.component.ts:521-528` — polling validator pattern (T23)
- `frontend/src/app/components/project-tab-bar/` — menu wiring helper (T21, if relevant)
- `daemon/routers/prompts.py` (Phase B T5) — REST consumer (T17 + T18 + T19)

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R15 | **Service API drift** — Phase B's REST surface changes mid-implementation. | Medium | Medium | Service is the only HTTP consumer; pages don't reach below it. Phase B's REST contract is the binding contract; Phase C absorbs changes via the service layer. |
| R16 | **Routing conflicts** — `/prompts` may conflict with an existing route. | Low | Low | Verify `frontend/src/app/app.routes.ts` for collisions before registration; the SPA catch-all in `daemon/api.py` is downstream and unaffected. |
| R17 | **State machine UI bugs** — buttons rendered for the wrong state (e.g., Approve on a `rejected` row). | Medium | Medium | D19's button matrix is the single source of truth; component tests cover every state × scope combination. |
| R18 | **Menu wiring mis-gating** — operators without `prompts:read` see the entry and get 403 from the backend; UX is jarring. | Low | Low | Render entry to operators with `prompts:read` only; hide otherwise (D16). |
| R19 | **Auth-OFF UX confusion** — operators on auth-OFF deployments don't know why the page is non-functional. | Medium | Medium | Banner with doc link (T22); morning review may opt to hide the entry entirely in auth-OFF deployments if the confusion is real. |
| R20 | **Polling storms** — multiple operators with the page open cause load. | Low | Low | 10s / 5s cadence is reasonable for a review surface; backend Phase B T1 acceptance covers state-machine concurrency; load test in dev. |
| R21 | **E2E test brittleness** — Playwright tests against the operator login + page are sensitive to auth-flow refactors. | Medium | Low | E2E test uses stable selectors (data-testid attributes added in T19); CI gate excludes the e2e from the fast lane (matches existing skill-bank e2e precedent). |

---

## Success Criteria

- **C15** — Operator logs in, opens `/prompts`, sees a list of pending drafts grouped by `(agent, artifact)`, opens one, sees content + diff + audit timeline, clicks `Approve`, the row transitions to `approved`, an audit event is stamped with their subject, and the next `load_and_cache_prompt` for that agent picks up the new content (verified by spawning that agent and observing the prompt).
- **C16** — Operators without `prompts:approve` see the page but the Approve / Reject buttons are disabled with a tooltip; operators on non-session lanes do not see the buttons at all; operators on auth-OFF deployments see a banner with guidance and no actions.
- **C17** — All Angular Jest tests pass; Playwright e2e passes against the dev daemon with Phase B API live.
- **C18** — Zero new npm dependencies added to `frontend/package.json`.
- **C19** — The list page polls every 10s; the detail page polls every 5s; both clean up on hide / navigate-away.
- **C20** — The operator runbook (T25) is committed and discoverable from the page's doc-link.
- **C21** — Phase C ships behind all three gate conditions; if any gate fails, Phase C is **not built** and Phase B's REST is the complete usable surface.

---

## Assumptions & Open Questions

- **OQ9** — **Per-project filter on prompt versions** — Phase C is global (no per-project scoping). If `decisions.md` decides prompts are per-project, Phase C grows a project-filter dropdown. Defer to follow-up.
- **OQ10** — **Real-time SSE channel for new drafts** — out of scope for Phase C; operators see updates via polling. Future commission if user demand.
- **OQ11** — **Inline edit / re-draft** — Phase C is review-only. If agents need a way to revise a rejected draft before resubmitting, that's the existing `inner_soul` lane; the UI surfaces the new version's row.
- **OQ12** — **Notification bell integration** — `notification.service.ts` already has an SSE channel; Phase C does NOT add a "new draft submitted" notification. If morning review requests it, add as T26 in a follow-up.
- **OQ13** — **Audit retention policy** — out of scope; the timeline shows the last 50 events for the row's key.

---

## If the Gate Fails (Fallback Plan)

If Gate Condition 1, 2, or 3 fails:

1. **Do NOT start Phase C.** The dispatcher (planner / Ari) reports the gate failure to the user in the morning briefing.
2. **Operators use Phase B's REST API** — `curl` / Postman / HTTP client is the complete usable surface. Document the equivalent curl flow in the operator runbook (T25) anyway, since the runbook is useful regardless of whether the UI ships.
3. **Re-evaluate Phase C at next user touch.** If the user wants the UI, open Phase C as a new commission with explicit budget; the gates re-evaluate from scratch (Phase B may have evolved).

This is the design intent — Phase B is the complete functional surface, Phase C is the operator-experience upgrade. The regime is fully usable without Phase C; Phase C is convenience.