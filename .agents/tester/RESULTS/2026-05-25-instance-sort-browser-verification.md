# Test Report: New Instance Sort Order — Browser Automation Verification

**Date**: 2026-05-25  
**Sessions**: ens/unit-test-task, ens/browser-test-task, ens/ensure-md-task

## Summary
- **Unit Tests**: ✅ PASS (690/690)
- **Type Check**: ✅ PASS
- **Browser Automation**: ✅ PASS (all 3 criteria met)
- **ensure.md**: ✅ PASS (dev.sh stable 21+ minutes)
- **Quick Fixes Applied**: 0

---

## Frontend Unit Tests

| Metric | Result |
|--------|--------|
| Type Check (tsc --noEmit) | ✅ PASS |
| Total Test Suites | 20 |
| Passed Suites | 18 |
| Failed Suites | 2 (e2e Playwright - pre-existing, unrelated) |
| Total Tests | 690 |
| Tests Passed | 690 |
| Tests Failed | 0 |

**Note**: 2 e2e Playwright test suites failed due to pre-existing Playwright module resolution error (`TypeError: Class extends value undefined is not a constructor or null`). Not related to the sort order fix.

---

## Browser Automation Test

| Criterion | Status | Details |
|-----------|--------|---------|
| First spawn appears at TOP | ✅ PASS | Coder instance appeared at TOP with "Just now" |
| Second spawn pushes first to second | ✅ PASS | Newest at TOP, previous moved to second |
| Recent time display ("Just now") | ✅ PASS | Both new instances showed "Just now" |

### Instance Order Verification
```
BEFORE:
  1. 👑 Leader c6b7df56... 20m ago  ← TOP

AFTER SPAWN 1:
  1. 💻 Coder af4615a6... Just now   ← NEW TOP ✅
  2. 👑 Leader c6b7df56... 21m ago

AFTER SPAWN 2:
  1. 💻 Coder 3d736fb2... Just now   ← NEW TOP ✅
  2. 💻 Coder af4615a6... Just now   ← PUSHED DOWN ✅
  3. 👑 Leader c6b7df56... 21m ago
```

### Screenshots
- Baseline: `~/.agent-browser/tmp/screenshots/screenshot-1779705007505.png`
- After 1st spawn: `~/.agent-browser/tmp/screenshots/screenshot-1779705046429.png`
- After 2nd spawn: `~/.agent-browser/tmp/screenshots/screenshot-1779705068528.png`

---

## ensure.md Validation

| Requirement | Status | Evidence |
|-------------|--------|----------|
| dev.sh runs stable 30s+ | ✅ PASS | Backend healthy, uptime 1262s (~21 min) |

---

## Overall Status
- **Unit Tests**: ✅ PASS
- **Browser Automation**: ✅ PASS
- **ensure.md**: ✅ PASS
- **Testing Complete**: ✅ READY
