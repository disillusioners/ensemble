# Test Report: Instance List Sorting Fix (New Instance at Top)

**Date**: 2026-05-25  
**Branch**: `feature/new-instance-top`  
**Commit**: `9f28afd` — "fix: sort instances newest-first so new instances appear at top of list"

---

## Summary

| Category | Result | Details |
|----------|--------|---------|
| Unit Tests | ✅ PASS | 680/680 passed, 0 failures |
| Sort Order Coverage | ✅ COVERED | Existing test validates behavior |
| Browser Automation | ✅ PASS | New instance confirmed at TOP |
| ensure.md | ✅ PASS | dev.sh stable 45+ minutes |

### Overall Status: ✅ READY

---

## The Fix

**File**: `frontend/src/app/services/instance.service.ts` line 133

```typescript
// BEFORE (appended at bottom):
return [...result, ...localById.values()];

// AFTER (prepended at top):
return [...localById.values(), ...result];
```

**Explanation**: `localById` contains instances in local state that aren't in the paginated API response (newer than API data). By spreading them first, newly created instances appear at index 0 (top of list), matching user expectation.

---

## Unit Test Results

- **Total**: 680 tests
- **Passed**: 680
- **Failed**: 0

### Existing Test Coverage

The sort order behavior is **already covered** by an existing test at `instance.service.spec.ts:647-662`:

```typescript
it('should prepend local-only instances at the top (newest-first order)', () => {
  const local: InstanceInfo[] = [
    createMockInstance({ instance_id: 'new-instance', status: 'running' }),
  ];
  const api: InstanceInfo[] = [
    createMockInstance({ instance_id: 'older-instance', status: 'completed' }),
  ];

  const result = service.mergeInstances(local, api);

  expect(result).toHaveLength(2);
  expect(result[0].instance_id).toBe('new-instance');
  expect(result[1].instance_id).toBe('older-instance');
});
```

**No additional test needed** — coverage is complete.

---

## Browser Automation Test Results

### Test Methodology
1. Started backend (`./dev.sh` on port 8079) and frontend (`npm start` on port 4199)
2. Used Playwright to navigate to http://localhost:4199
3. Created a new instance via API
4. Verified the new instance appears at position 0 (top) in the instance list sidebar

### Evidence

**Instance List Order (15 total):**
```
  1. [660bfa7d-787...] Approver (Just now)  ← NEW instance at TOP ✅
  2. [e7c839af-bf5...] Approver (Just now)
  3. [cac49012-dd4...] Approver (2m ago)
  ...
```

**Screenshots:**
- `/tmp/instance-sort-test-01-direct-nav.png` — Direct navigation to new instance
- `/tmp/instance-sort-test-02-instance-list.png` — Instance list showing new instance at top
- `/tmp/instance-sort-test-03-final.png` — Final state confirmation

### Verdict: ✅ PASS — New instance appears at TOP of list

---

## ensure.md Validation

- **dev.sh**: Stable for 45+ minutes (uptime: 2726 seconds)
- **Health check**: `{"status": "healthy", "version": "0.3.3"}`
- **Port 8088**: Untouched ✅

---

## Cleanup

- ✅ Port 8079 (backend): Freed
- ✅ Port 4199 (frontend): Freed
- ✅ Port 8088: Untouched (as required)

---

## Quick Fixes Applied: None needed

---

## Documentation Updated

- [x] RESULTS/2026-05-25-instance-sort-fix.md — This report
- [x] PACKS.md — No changes needed (frontend_unit_test pack already exists)
- [ ] rules/ensure.md — No changes (user-maintained)
- [ ] MOCK_TESTS.md — No changes
