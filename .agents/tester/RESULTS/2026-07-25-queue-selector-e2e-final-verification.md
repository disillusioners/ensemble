# Test Report: Queue Selector UI — FINAL Browser E2E Verification
Date: 2026-07-25T08:46:56Z
Run/Reference ID: 1784968639656
Project: E2E-QueueSelector-1784966240935 (ID 9c022ae3-5bb8-43a4-9132-4c4a4d3ae971)
Verification Type: Browser E2E (real Chromium via Playwright)
Worker: `15d5bc12` (e2e-queue-selector-verify, skill `e2e-test`)

---

## Summary
- **RESULT: ✅ PASS** — all observable queue-selector behaviors work correctly.
- Known regression bug ("default selection = system_background_queue") is **FIXED** in current build (commits `fd876cfb` + `2567af9e`).
- No new bugs found. No network errors. No blocking console errors.
- Services: frontend (`:4199`) ✅, backend (`:8079`) ✅ both reachable.
- No code modified — verification-only (no production/test fixes applied).
- Runtime: ~3 min total (well within 5-min cap).

---

## Service Availability
| Service | Reachable | Verification |
|---------|-----------|-------------|
| Frontend (`http://localhost:4199`) | ✅ Y | `GET /` → HTTP 200, Angular index (624 bytes), Vite dev server live |
| Backend (`http://localhost:8079`) | ✅ Y | `GET /api/health` → 200 `{"status":"healthy","version":"0.9.8"}}`; `GET /api/projects/{id}/queues` → 200 with 5 queues |

> Correct queues endpoint is `/api/projects/{project_id}/queues` (NOT `/api/queues`, which 404s).

---

## Per-Scenario Results

### Scenario A — Queue selector renders (happy path): ✅ PASS
- `label.queue-selector select` renders when opening a fresh idle instance.
- Option count: **5** (system_background_queue, system_defer_queue, system_fifo_queue, system_kb_fifo_queue, system_parallel_queue).
- All expected queues present.
- Screenshot: `e2e-shots/queue-selector/final-A-rendered.png`

### Scenario B — Default selection (fd876cfb regression check): ✅ PASS
- `select.value` = `b4dceb7f-b543-468d-87a6-e5d0182f0101`
- `selectedIndex` = 4
- Angular `selectedQueueId()` signal = `b4dceb7f-...` (correct)
- **Actual default name = `system_parallel_queue`** ✅ (correct — NOT system_background_queue)
- Screenshot: `e2e-shots/queue-selector/final-B-default-selection.png`

> **Resolved finding:** The regression spec's KNOWN-FAIL note for Test 1 is STALE. The fix is actually a two-part fix: `fd876cfb` fixed the HTML binding (`[selected]` per option); `2567af9e` "fix(message-input): default to system_parallel_queue by name, not id" fixed the latent logic bug (line 126 now compares `q.queue_name === 'system_parallel_queue'` instead of UUID-vs-name). Fix is complete.

### Scenario C — Selection persistence across reload (CRITICAL): ✅ PASS
- Selected system_fifo_queue → `select.value` = `06089124-3499-4fdf-aeca-e4dc3d62c815`
- `localStorage["ensemble-queue-select-9c022ae3-..."]` = `06089124-...` (UUID saved ✅)
- **After reload:** `select.value` = `06089124-...`, selected option text = `system_fifo_queue` ✅ (persisted)
- Screenshots: `final-C1-after-select-fifo.png`, `final-C2-after-reload.png`

### Scenario D — Submit/dispatch with selected queue: ✅ PASS
- Selected queue: fifo (`06089124-...`)
- Status BEFORE send: `idle`
- Typed message, clicked send → button enabled, click succeeded
- Status AFTER send (after ~5s): `running` ✅ — instance transitioned out of idle successfully
- Trial instance cleaned up (deleted) in finally block
- Screenshots: `final-D1-message-typed.png`, `final-D2-after-send.png`

### Scenario E — Console + network errors: observed (non-blocking)
**Console errors (3, non-blocking):**
1. `[SSE] Connection error` / `[SSE] EventSource connection error` (×2) — EventSource reconnect noise; appears during instance state transitions (idle→running) when the SSE stream refreshes.
2. `NG0100: ExpressionChangedAfterItHasBeenCheckedError` in `InstanceListComponent` — previous value `'38m ago'`, current `'39m ago'`. Change-detection refresh quirk tied to the "x minutes ago" time-ago formatter ticking across cycles. **Unrelated to queue selector.**

**Network errors (HTTP ≥400): NONE** — no 4xx/5xx responses to the API observed.

---

## Screenshots Saved
All at project-root `/e2e-shots/queue-selector/` (NOTE: spec's `__dirname` resolves `../../e2e-shots` ABOVE the frontend root, so they land at project root, not `frontend/e2e-shots/`):

| File | Size | Dimensions |
|------|------|-----------|
| `final-A-rendered.png` | 100 KB | 1280×720 |
| `final-B-default-selection.png` | 100 KB | 1280×720 |
| `final-C1-after-select-fifo.png` | 100 KB | 1280×720 |
| `final-C2-after-reload.png` | 100 KB | 1280×720 |
| `final-D1-message-typed.png` 103 KB | 1280×720 |
| `final-D2-after-send.png` | 135 KB | 1280×720 |

(Pre-existing test1/test2/test3 screenshots from the older regression spec run are also present there.)

---

## Notable Findings / Follow-ups (non-blocking)

1. **⚠️ The existing regression spec (`frontend/e2e/queue-selector-regression.spec.ts`) is STALE and reports FALSE failures.** Tests 1 & 2 fail with `waitForSelector` timeout, but NOT because of a queue-selection bug. They fail because the spec hardcodes `INSTANCE_ID = eb79d594-648f-400e-8df8-30893de1ef80` (the target instance), which is now in `waiting_children` status (not idle). The queue selector is conditionally rendered only when `@if (isIdle() && queues().length > 0)` (`message-input.html:124`), so on a running instance the selector is permanently hidden → timeout. Additionally, the spec's KNOWN-FAIL note on Test 1 is obsolete (bug was fixed by `2567af9e`).
   - **Recommended follow-up (not done — out of verification scope):** update the spec to spawn a fresh IDLE instance for Tests 1 & 2 (as Test 3 already does), and remove the stale KNOWN-FAIL marker.
   - Recorded via `experience()` for future sessions.

2. **agent-browser skill NOT available** in this project's skill store (`skill_search` returned only `e2e-test` at 0.9 match). Used Playwright direct fallback (option b) — Playwright ^1.60.0 + Chromium were already installed.

3. **No source code was modified** — verified via `git status` (only screenshot artifacts + the pre-existing untracked regression spec). A temporary verification spec (`_queue-verify-final.spec.ts`) was written, run against a fresh idle instance, then **deleted** — no source artifacts left behind.

4. **Unrelated surface issue (non-blocking):** `NG0100 ExpressionChangedAfterAfterItHasBeenCheckedError` in `InstanceListComponent` (the "x minutes ago" time-ago formatter ticking across change-detection cycles). Independent of queue selector; flagged for separate investigation.

---

## Console Errors Observed
- `[SSE] Connection error` / `[SSE] EventSource connection error` (×2) — SSE reconnect noise during state transition (non-blocking)
- `NG0100: ExpressionChangedAfterItHasBeenCheckedError` in `InstanceListComponent` (time-ago formatter, unrelated to queue selector)

## Network Errors Observed
- **NONE** (no 4xx/5xx responses to the API)

---

## Documentation Updated
- [x] RESULTS/2026-07-25-queue-selector-e2e-final-verification.md — this report
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no changes (no mock tests involved; real services used)
- [x] LESSONS/2026-07-25-queue-selector-e2e-stale-regression-spec.md — recorded stale-spec finding + screenshot path resolution quirk
- [ ] PACKS.md — no new packs (verification-only; existing spec was used)

## Code Changes Summary
- NONE. Verification-only run. No production or test code modified.
- Worker wrote a temporary `_queue-verify-final.spec.ts`, ran it, then deleted it.
- KB updated via `experience()` (confirmed bug fix + stale spec note).

---

### Overall Status
- Queue Selector UI E2E: ✅ **PASS**
- Happy path: ✅
- Default selection (regression fix confirmed): ✅
- Selection persistence (critical): ✅
- Submit/dispatch: ✅
- Console/network errors: only non-blocking SSE reconnect noise + unrelated NG0100
- **Testing Complete: ✅ READY**
