# Instances Tab State Persistence + Detail Component Caching — Feature Test

Date: 2026-08-18
Branch: `feature/instances-state-cache` (UNCOMMITTED working tree, base c80e0232)
Feature files: 16 changed (+2491/−67) — larger than the 7-file brief; extras include agent-switcher F6 gate, instance-delete-dialog W1 clear, todo-list W6 gate, instances list W1 clear.
Worker instances: recon a606cef6, jest 3d98cdb1, build ad3e35b0, ensure-static fa65c5e6, build-baseline c9825dc2, script-author 579380eb, e2e executor fbe83719 (all packs).

## Verdicts

| Area | Verdict | Evidence |
|------|---------|----------|
| Unit suite (full Jest) | ✅ **PASS** | 2037/2037, 57 suites, 9.6s (baseline 1935 → +102 new tests, +3 suites; zero failures) |
| Production build | ❌ **FAIL (NEW)** | Bundle 6.09 MB vs 6 MB `maximumError`, over by 87.66 kB. Base build at c80e0232: **PASS at 5.82 MB** — feature adds ~270 kB, crossing the gate |
| E2E core flows (feature spec) | ❌ **FAIL — 2 real bugs** | 3/4 PASS; draft + scroll NOT preserved (same DOM node survives; SSE/net=0 chat-scoped, zero console errors, localStorage PASS) |
| E2E regression flows (R2/R4/R5/R6/terminate) | ✅ **PASS (behavioral)** | R6 ✓ R2 ✓ R4 ✓ R5 ✓; terminate fully proven at runtime (dialog → delete → cache cleared → nav fallback `/instances`). Exit-red only on severed-SSE console noise (classified expected) |
| Playwright existing-suite sweep (12 specs) | ⚠️ **MIXED** | 1 feature-caused regression candidate + 5 failure classes triaged (see below); 34+ passed, 21 failed, 31 blocked-by-serial-abort; Group B hit internal 270s cap |
| ensure.md (in-scope) | ✅ **PASS** | Core #4 static: dev.sh:102 `--timeout-graceful-shutdown 10` exact. Backend packs out of blast radius (zero daemon files) |

## Bugs Found (route to leader — NOT fixed, per brief)

### BUG 1 🔴 Draft lost on Plan↔Instances round-trip (feature gap vs stated intent)
- **Repro:** open instance detail → type draft in message input → Plan → Instances. Draft comes back EMPTY.
- **Expected:** `e2e-draft-PERSIST`; **Actual:** `""`.
- **Locus:** `chat.html:175` — `<app-message-input>` nested inside `@if (currentInstance() && !instanceNotFound())`. The hide→show visibility effect transiently drops `currentInstance()` → destroys the input component → its `message()` signal dies with it. Outer `.chat-container` (marker-proven same node) survives.
- **Intent gap:** comments at `chat.component.ts:87` / `~514` claim "scroll position, input drafts… is preserved".
- **Evidence:** `frontend/test-results/instances-state-cache-core-a3902-ored-draft-scroll-preserved-chromium/error-context.md`

### BUG 2 🔴 Scroll position lost on Plan↔Instances round-trip
- Same root cause as BUG 1: `.messages-scroll` (inside `app-chat-interface`, guarded at chat.html:150) destroyed/recreated. `scrollTop` set to 100 → comes back **0**.

### BUG 3 🟠 Production build budget regression
- Base passes at 5.82 MB; feature tree fails at 6.09 MB (+270 kB over the 6 MB gate). Likely mechanism: ChatComponent moved from lazy route chunk to eager root-mount import in `app.ts`.
- **Suggested fixes (developer's call):** lazy-load the chat subtree / keep deep-link route chunked, or raise `maximumError` deliberately with docs.

### BUG 4 🟡 Duplicate `app-project-tab-bar` in DOM (sweep finding; needs product decision)
- `instances-project-tabs` spec strict-mode fail: `app-project-tab-bar` resolves to **2 elements** — `app-instances` has one, and the now-always-mounted `app-chat` carries its own. Either scope the selector (test) or hide the chat's copy (app). Visibility/a11y duplication risk on non-instance routes.

### Observation (classified expected, not a bug): severed-SSE console noise on terminate
- Terminating an instance fires 2 `[SSE] Connection error` logs (sse.service.ts:495/508, onerror → designed handleClose). Logged-and-handled. Optional app polish: disconnect() before delete.

## Sweep failure classes (existing 12-spec suite)
1. **FEATURE-CAUSED:** duplicate tab-bar (BUG 4).
2. **TEST-INFRA (pre-existing):** `queue-selector-regression` posts old `{agent_id:'leader'}` contract → 400 at spawn (sibling `queue-selector-states` 6/6 PASS with current API); `project-tabs` expects old `href="/instances/{id}"` (now `/projects/{pid}/instances/{id}`).
3. **FIXTURE-GAP (pre-existing):** workspace specs (tab-workspace-sync ×3, workspace-state-preserve, workspace-file-tabs, workspace-toolbar-compact) — synthetic projects lack on-disk workspace files → tree `files=[]`. Needs on-disk fixture seeding; broader than one spec.
4. **NEEDS-TRIAGE:** `auto-scroll-to-bottom` 0/3 (`.messages-scroll` never visible; may interact with chat-interface guard or stale fixture route); `send-pause-button` T4 (instance TERMINATED mid-test + pre-existing NG0100 noise).
5. **Serial-abort blocked evidence** (not independent failures): 8+18 did-not-runs.
- Group B TIMEOUT at internal 270s (43/46 queued) — dual-layer cap worked; remaining 3 tests unmeasured.

## Scope Decision
> Full-suite requested for the feature gate; change is frontend-only (16 files, zero daemon files) → scoped to frontend packs (Jest full, build, 2 feature e2e packs, 12-spec Playwright sweep) + ensure.md in-scope Core #4 static. Backend daemon packs (concurrency, e2e workflows, ~2400 pytest) not warranted. Jest full-suite WAS run (cross-component regression net) — warranted for an app-shell/routing/mount change.

## ensure.md Validation Results (in-scope)
- ✅ Core Critical #4: dev.sh graceful-shutdown flag — PASS (grep evidence dev.sh:99/102)
- ✅ Core Critical #1 (scoped): "no regressions in changed packs" — FAIL surfaces below
- Out of scope: concurrency_atomic_unit_test, sync-DB, await-callers (backend packs; zero daemon files in change set)

## Packs & commits (test artifacts, this session)
- `frontend_jest_regression` — PASS 2037/2037
- `frontend_prod_build` — FAIL (new)
- `instances_state_e2e_core` — FAIL (BUG 1+2; SSE criterion PASS)
- `instances_state_e2e_regression` — behavioral PASS (exit-red = classified noise)
- `frontend_playwright_sweep_a` — FAIL (1 feature-caused)
- `frontend_playwright_sweep_b` — TIMEOUT at 270s internal (43/46)
- Commits: `13393065` (SSE scoping+domcontentloaded), `7a13b985` (reorder evidence harvest), `42d37bfd` (networkidle→domcontentloaded ×8), `c648f230` (Escape dismissal + R2 selector), `b957a838` (R2 instance-id selector), `0a36a32c` (console filter + sidebar wait), `8c868be1` (vscode-folder filter), `581c5a13` (terminate dialog confirm), `21794ba2` (sweep wrappers A/B)

## Environment notes
- BE dev daemon :8079 PID 96878 — NEVER killed, healthy throughout. FE via Playwright webServer (4199, warm reuse). Port 8088 untouched.
- E2E fixture leftovers (~10 synthetic projects) in dev DB — known-acceptable dev side effect; cleanup API cannot delete (401-class). Real dev instances untouched; terminate test used fixture-created instances only.

## Overall
- Unit: PASS · Build: FAIL (new) · E2E core: FAIL (2 real bugs) · E2E regression flows: PASS · Sweep: MIXED (1 feature-caused, rest pre-existing/fixture) · ensure.md in-scope: PASS
- **Testing Complete: ❌ NOT READY** — BUG 1/2 (draft+scroll preservation, the feature's core promise) and BUG 3 (build budget) block; BUG 4 needs a product call.
