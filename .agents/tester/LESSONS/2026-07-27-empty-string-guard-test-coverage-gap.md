# Quick Fix: Empty/whitespace-string guard tests for VS Code failed-to-start detail
Date: 2026-07-27
Commit: `4aca4f6c` (quick-fix by worker retest-frontend)
Follow-up to: `f5a37ba8` ("fix: handle additional VS Code init error cases")
Pack: vscode_frontend_unit_test (settings.component.spec.ts)

## Problem
Commit `f5a37ba8` added a W2 empty-string guard in `saveEditor()`:
```ts
message = detail?.detail?.trim() || 'VS Code server failed to start. Check server logs for details.';
```
But the existing test ("omits explanation") sent `detail.detail` as `undefined`, which short-circuits via optional chaining (`?.`) *before* `.trim()` is ever reached. **All tests would pass even if `.trim()` were deleted** — a classic "passes for the wrong reason" gap.

## Root Cause
The "omits explanation" test exercised the `undefined` path, not the empty-string path the guard was written for. The `.trim()` and `||` logic had zero test coverage.

## Fix Applied
Added 2 explicit tests to `settings.component.spec.ts`:
1. `detail: ''` (empty string) — guards against `||` being mutated to `??`
2. `detail: '   \t  '` (whitespace) — guards the `.trim()` call itself

## Verification
**Mutation-verified:** Removed `.trim()` from the test double → whitespace test FAILED (received `"   \t  "` instead of the fallback message), proving the test genuinely guards the `.trim()` call. Restored `.trim()`, full pack re-run: 92/92 PASS.

## Lesson
When production logic contains defensive guards (`.trim()`, `||` vs `??`, optional chaining), ensure tests exercise the *specific condition the guard protects against* — not just the `undefined`/falsy case. Optional chaining (`?.`) short-circuits on `undefined`, so it cannot validate `.trim()` behavior. For mirror/test-double tests, **mutation verification** (remove the production line, confirm test fails) is the only way to prove the test guards real behavior.
