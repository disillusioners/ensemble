# E2E Test Report: Instance Lifecycle Verification
Date: 2026-05-24
Session: opencode ses_1a603f55cffeo7YmBmD5REmuOo

## Summary
- Tests Run: 3 | Passed: 2 | Failed: 1
- Overall: ❌ NOT READY

## Test 1: Simple Agent — Verify instance reaches `completed` state
**Verdict: ✅ PASS**

| Detail | Value |
|--------|-------|
| Instance ID | f778a111-e39c-4717-a352-8edd9ea5b89a |
| Agent | coder |
| Final Status | completed |
| Response | "Hello world" |
| Orphan Detection | NONE |

The simple coder instance correctly reached `completed` state after processing a message. No false orphan detection.

---

## Test 2: Agent-to-Agent — Verify leader reaches `completed` after child finishes
**Verdict: ❌ FAIL**

| Detail | Value |
|--------|-------|
| Leader Instance ID | b5654b65-005f-457b-a09a-b305d99103b5 |
| Leader Status | **paused** (STUCK — expected `completed`) |
| Coder Instance ID | ee0d3d64-b74b-4ebf-a70b-56d67fbbfd39 |
| Coder Status | **paused** (expected `completed` or `terminated`) |
| Orphan Detection | NONE (stuck in waiting_children first, then paused) |

### Root Cause Analysis
1. Leader spawns coder instance via `spawn_instance` tool
2. Leader sends message to coder via `send_message` tool
3. Coder receives and responds: "Hey! 👋 I'm here and ready..."
4. **BUG**: After responding, the child coder does NOT transition to `completed` or `terminated`
5. Leader remains in `waiting_children` → eventually gets `paused`
6. `child_instance_ids` field is `None` on both leader and coder — **parent-child relationship not properly tracked**

### Pre-existing Evidence of Same Bug
These instances were already stuck before the test:
- `5d50681a` (leader) — stuck in `waiting_children` since 12:07
- `1a18ce2d` (leader) — stuck in `waiting_children` since 11:02
- `96c659cd` (coder) — stuck in `waiting_children` since 12:04

All have `child_instance_ids: None` and `parent_instance_id: None`.

---

## Test 3: Quick Stress — 3 messages in succession
**Verdict: ⚠️ PARTIAL PASS**

| Detail | Value |
|--------|-------|
| Leader Instance ID | 501aa43f-a2a2-4428-8fe4-efff36f8f44f |
| Leader Status | completed ✅ |
| Messages Sent | 3 |
| Messages Delivered | **2 of 3** (Message 2 dropped) |
| Orphan Detection | NONE |

### Message Flow Analysis
| # | Question | Response | Status |
|---|----------|----------|--------|
| Original prompt | (project context + task instructions) | "4." | Answered original context, not a stress message |
| Message 1 | "What is 1+1?" | "2." | ✅ Delivered & answered |
| Message 2 | "What is 2+2?" | **DROPPED** | ❌ Never appeared in conversation |
| Message 3 | "What is 3+3?" | "6." | ✅ Delivered & answered |

**Additional Issue**: Message 2 ("What is 2+2?") was lost during rapid succession delivery. The concurrency gate (DB-level) may be dropping or merging messages.

---

## Bugs Identified

### 🔴 Bug 1: Child Instance Lifecycle Never Completes (CRITICAL)
- **Area**: Instance state transitions after child agent responds
- **Symptom**: Child instances respond but stay `running` → `paused`, never reach `completed`/`terminated`
- **Impact**: Parent leaders stuck in `waiting_children` forever
- **Evidence**: Test 2 leader + coder stuck, plus 3 pre-existing stuck instances
- **Related**: `child_instance_ids` and `parent_instance_id` are both `None` — relationship tracking broken

### 🟡 Bug 2: Message Loss Under Concurrent Send (HIGH)
- **Area**: Message handling / job queue concurrency
- **Symptom**: 1 of 3 rapid-fire messages was dropped (Message 2)
- **Impact**: Messages can be silently lost under concurrent send
- **Evidence**: Test 3 — Message 2 never appeared in conversation history

### 🟡 Bug 3: Stop API Not Working Properly (HIGH)
- **Area**: Instance stop endpoint
- **Symptom**: `POST /api/instances/{id}/stop` returns `{"paused": true}` instead of actually stopping
- **Impact**: Cannot terminate stuck instances
- **Evidence**: Test 2 instances remain after stop attempts

---

## ensure.md Validation
- **dev.sh running**: ✅ Server healthy, uptime >10min during testing

---

## Action Needed
- [ ] **Fix Bug 1**: Investigate child instance completion flow — why child instances don't transition to `completed` after responding
- [ ] **Fix Bug 2**: Investigate message loss during concurrent sends — check DB-level concurrency gate logic
- [ ] **Fix Bug 3**: Investigate stop endpoint — returning `paused` instead of actually stopping
- [ ] **Investigate parent-child tracking**: `child_instance_ids` and `parent_instance_id` are both `None` across all tested instances
