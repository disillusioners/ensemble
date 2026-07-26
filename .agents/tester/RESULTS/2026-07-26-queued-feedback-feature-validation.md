# Queued Message Feedback Feature Validation (commits 0ecc91f7+dbe382dd)
**Date:** 2026-07-26
**Worker Instance:** ff3cd6a3-2747-4e0f-844a-92a37b982ce6 (queued-feedback-feature-validation)
**Skill:** `e2e-test`
**Branch:** `feature/queue-dispatch-option-b` @ `0ecc91f7` + `dbe382dd`
**Trigger:** User request — validate the Queued Message Feedback feature + e2e regression
**Environment:** DEV only (backend `:8079` + frontend `:4199`)

---

## Summary

| Metric | Value |
|--------|-------|
| Part A — E2E Release Gate (regression) | ✅ **PASS (4/4)** |
| Part B — Queued feedback scenario (API + UI) | ❌ **FAIL** — `queued` field false-positive when slot available |
| Part C — Edge cases | **2/3 PASS** (Edge 3 confirms the bug) |
| Overall status | ❌ **NOT READY** — backend `queued` field bug |
| Quick fixes | none (architecture-level, not quick-fix eligible) |
| Files modified | none |

---

## Scope Decision

> Commits `0ecc91f7`+`dbe382dd` add the queued message feedback feature (backend `queued` field + frontend UI indicator). Full Release Gate e2e regression + the targeted feature scenario + edge cases are all warranted. No scope reduction.

---

# Part A: E2E Release Gate Regression — ✅ 4/4 PASS

| Test | Result | Runtime | Exit Code |
|------|--------|---------|-----------|
| `test_parent_child_workflow_happy_path` | ✅ PASS | 50s | 0 |
| `test_pause_after_spawn_then_resume` | ✅ PASS | 50s | 0 |
| `test_terminate_after_spawn_then_revive` | ✅ PASS | 81s | 0 |
| `test_three_level_cascade_reports` | ✅ PASS | 132s | 0 |

**No regressions.** All prerequisites verified (daemon health, SSL cleanup, queue cleanup, one-by-one execution).

---

# Part B: Queued Feedback Scenario — ❌ FAIL (API contract)

## What Works ✅
- **Frontend UI indicator wiring is correct** (verified via code inspection — browser automation tool not available):
  - `queuedMessage` signal holds `{ content: string }`
  - `queuedSnippet` computed: `content.length > 50 ? content.slice(0,50) + '...' : content` (truncation correct)
  - Indicator template: `⏳ Queued: "{{ queuedSnippet() }}" — waiting for slot` (shows actual message text)
  - Sets signal when `response.queued === true`
  - Clears on: assistant-message effect, `status_change`→running/processing, instance switch — all guarded by `instance_id` match
- **Genuinely-queued case is correct** (Message 2, slot full): API `queued: true` ✅, and the indicator would show correctly
- **Steady-state queue dispatch works** (verified): A processes first, B gets the slot when A finishes

## What Fails ❌ — The `queued` Field Snapshot Race

**The bug:** the `queued` field is correct ONLY when a slot is genuinely unavailable. When a slot IS available, it returns a deterministic **false positive** (`true`).

### API Response Verification

| Message | Expected `queued` | Actual `queued` | Correct? |
|---------|-------------------|-----------------|----------|
| Message 1 (instance A, slot AVAILABLE) | `false` | **`true`** | ❌ |
| Message 2 (instance B, slot FULL) | `true` | `true` | ✅ |

### Root Cause (definitively confirmed via code + polling)

In `daemon/routers/messages.py` `send_message`, the snapshot reads `JobItem.admission_state` via `JobQueueService.get_job` **immediately** after `enqueue_message_job` returns. But:

1. `JobQueueService.enqueue` (`daemon/services/job_queue_service.py:787-808`) creates the JobItem with `admission_state='queued'` (the DB default — migration in `daemon/manager.py:3283`: `DEFAULT 'queued'`)
2. It then fires `dispatch_bus.notify_new_job()` — a **fire-and-forget asyncio Event signal**, not a synchronous admission
3. The actual `queued → active` admission happens **asynchronously** in the `JobProcessor`'s claim loop (a separate async task)
4. The immediate `get_job` read happens within ~100-200ms, **before** admission completes

### Proof via polling (parallel queue, slot guaranteed available)

```
API response queued=True (snapshot at t=0)
poll @100ms: admission_state=queued | status=pending   ← still pre-admission
poll @200ms: admission_state=active | status=processing ← admission completed!
poll @300ms+: active (stays active)
```

### True-queued case confirmed correct (all slots held)

```
API response queued=True
poll @1s–5s: admission_state=queued | status=pending   ← STAYS queued (correct!)
```

### The field's truth table

| Actual slot state | API `queued` field | Correct? |
|-------------------|-------------------|----------|
| Genuinely unavailable (slot full) | `true` | ✅ |
| Available (admitted within ~200ms) | `true` | ❌ false positive |

### User impact
The UI would almost always show the "queued" indicator for ~the first LLM turn of any message, because the snapshot races the admission. The indicator clears on the next SSE event (thinking/status_change→running), so the user impact is a **brief spurious indicator** — but it is incorrect behavior.

### NOT quick-fix eligible
This is architecture-level. The fix would require either (a) making `enqueue` perform synchronous admission, or (b) a short `await asyncio.sleep`/yield before the `get_job` read (fragile), or (c) a different signal. See `LESSONS/2026-07-26-queued-field-snapshot-race.md` for the recommended fix.

---

# Part C: Edge Cases — 2/3 PASS

### Edge Case 1: Long message (> 50 chars) → truncated with "..."
- **Result:** ✅ **PASS** (truncation logic verified in frontend code)
- **Evidence:** `queuedSnippet` computed: `content.length > 50 ? content.slice(0,50) + '...' : content`. For a 210-char input, expected indicator: `⏳ Queued: "This is a very long message that definitely exceed..." — waiting for slot`

### Edge Case 2: Instance switch while message is queued → indicator clears
- **Result:** ✅ **PASS** (code-logic verification)
- **Evidence:** `handleInstanceIdChange` (`chat.component.ts:352-360`) explicitly sets `this.queuedMessage.set(null)` on every instance route change. All SSE-driven clears are guarded by `statusChange.instance_id !== currentInstance.instance_id` checks.

### Edge Case 3: Parallel queue (concurrency=5) → should NOT show queued indicator
- **Result:** ❌ **FAIL** (confirms the bug)
- **Evidence:** Sent 2 messages to 2 IDLE instances on `system_parallel_queue` (concurrency=5, both slots available). Expected: both `queued: false`. **Actual: both `queued: true`.** Queue state after: `active_jobs=2, pending_jobs=0` — confirming both were dispatched immediately. Same snapshot race as Part B.

---

## ensure.md Validation Status — Release Gate (Critical): 4/4 PASS ✅

- [x] E2E: Normal parent→child workflow completes (happy path)
- [x] E2E: Pause after spawn, then resume works correctly
- [x] E2E: Terminate after spawn, then revive documented
- [x] E2E: 3-level cascade (leader→tester→staggered workers)

### Feature-specific validation
- [x] **Genuinely-queued case** (slot full): API `queued: true` ✅, frontend indicator wiring ✅
- [ ] **Available-slot case** (slot available): API `queued: false` ❌ — false positive (`true`) due to snapshot race
- [x] Long message truncation: ✅ (frontend logic)
- [x] Instance switch clears indicator: ✅ (frontend logic)
- [ ] Parallel queue no queued indicator: ❌ — same snapshot race

---

## Overall Status

- **E2E Release Gate:** ✅ **PASS (4/4)** — no regressions
- **Queued Feedback Feature:** ❌ **FAIL** — the backend `queued` field has a deterministic snapshot race (false-positive when a slot is available)
- **Frontend indicator wiring:** ✅ **CORRECT** — truncation, instance-switch clear, instance_id-guarded SSE clears all verified; the frontend consumes a buggy signal
- **Quick Fixes Applied:** none (architecture-level, not quick-fix eligible)
- **Action Needed:**
  - [ ] Fix the `queued` field snapshot race in `daemon/routers/messages.py` `send_message` — the snapshot reads `admission_state` before the async `queued → active` admission completes (see `LESSONS/2026-07-26-queued-field-snapshot-race.md` for the recommended fix)
  - [ ] Re-run Parts B and C after the fix
- **Testing Complete:** ❌ **NOT READY** — the `queued` field is correct only for the genuinely-queued case; it returns `true` for available slots too (a false positive that would cause a brief spurious UI indicator on every message)
