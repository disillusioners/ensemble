# E2E Console-Hygiene vs Plane iframe CSP (dev port 4199) — 2026-08-19

## Symptom
Every frontend e2e test with a zero-console-errors tail FAILS when PLANE_BASE_URL is enabled:
2-3× `[error] Framing 'https://plane.ensem.dev/' violates ... "frame-ancestors 'self' https://*.ensem.dev https://*.mtri.app http://localhost:8079 http://localhost:9797"`.
The Plane iframe is ALWAYS-MOUNTED (app.html) → emits on every page load/navigation.

## Root cause
Remote CSP allowlist includes :8079 and :9797 but NOT :4199 (ng serve dev port the e2e suite
attaches to via `reuseExistingServer`). Environmental — zero relation to repo code under test.

## Impact pattern (observed 2026-08-19)
- instances_state_e2e_regression: R6 hygiene tail FAIL → `mode: 'serial'` cascade → R2/R4/R5/terminate
  "did not run". Pack formally FAIL while ALL functional asserts pass (verified per-test via --grep).
- reload_tabs_e2e (new): same — R-TAB-1 functional PASS, suite bail, R-TAB-2..5 needed --grep evidence runs.
- Contrast: closure cycle 2026-08-18 recorded PASS 5/5 → the CSP allowlist/plane-enablement changed
  in the environment BETWEEN cycles (unproven which; likely PLANE_BASE_URL newly set or remote CSP edited).

## Rules going forward
1. When frontend e2e FAILS on console-error arrays: diff the array contents FIRST. CSP
   frame-ancestors / plane.ensem.dev entries = environmental; classify, don't debug the app.
2. Serial-mode specs + one environmental hygiene failure = total evidence blackout for successors.
   Evidence recovery WITHOUT modification: `--grep` per-test runs for self-seeding tests
   (verified with --list first); chain-dependent tests may be unobtainable — say so explicitly.
3. Permanent fixes (owner's call, all one-liners):
   (a) backend: add `http://localhost:4199` to plane frame-ancestors allowlist (root fix),
   (b) test-code: extend filterConsoleErrors with the CSP signature,
   (c) run e2e with PLANE_BASE_URL unset in dev.
4. Baseline note: earlier report said "2× CSP errors"; actual count is 3× per fresh-context+reload
   (2 navigations + 1 reload) — count varies by navigation count; match on signature, not count.

Refs: RESULTS/2026-08-19-reload-tabs-regression-verify.md; LESSONS/2026-08-19-playwright-parallel-pack-interference.md (companion: serialization rule).
