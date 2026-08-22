# Reasoning-Echo Denylist Flip: Drift Re-Audit Closure & Verification Pattern

Date: 2026-08-22
Arc: `feature/reasoning-echo-denylist` @ `28ea76a9`+`018800b8` — see `RESULTS/2026-08-22-reasoning-echo-denylist.md`

## Context

The 2026-08-18 lesson (`2026-08-18-reasoning-echo-test-contract-drift.md`) established that
reasoning-echo spec shifts orphan sibling test fixtures — drift is **symmetric**: a spec
tightening AND a spec loosening each orphan fixtures encoding the prior direction. The
2026-08-22 allowlist→denylist inversion was the third shift of this contract in one week
(gate in → gate reverted out → allowlist → denylist). Dev+review claimed a re-audit;
this arc independently verified it.

## Findings

1. **Re-audit HELD: 0 old-direction findings.** Full-tree sweep (tests/ + test/packs/,
   excluding the 4 dev-updated files) found zero fixtures asserting allowlist semantics
   or referencing the old env key / ClassVar. Dev's re-audit was complete.
2. **The only "old direction" artifacts were stale bytecode** —
   `tests/__pycache__/conftest.cpython-313.pyc` compiled pre-flip still matches
   old-key greps while current source does not. Lesson: when grepping for contract
   drift, exclude `__pycache__` or clear it first; a "hit" in `.pyc` is not repo drift.
3. **Coverage gap closed by mock, not suite**: `warn_deprecated_reasoning_echo_env`
   (daemon/config.py:1007) has zero pytest-suite coverage; the real-behavior mock's S5
   exercises it end-to-end (warning fires exactly once; behavior unchanged). One
   deprecation test in `test_llm_reasoning_echo_config.py` would close it at suite
   level if desired.
4. **Deprecation dedup nuance**: the once-per-process warning flag is consumed even
   when the first call happens with the env var absent — a subsequent env-set call in
   the same process stays silent. Benign at real startup (env fixed before first
   call at `daemon/__main__.py:40`) but a trap for anyone testing the helper with
   repeated in-process calls under different env states (must reload/patch the module
   flag between scenarios — the mock does exactly this).

## Verification pattern that worked (reuse for future contract flips)

Four independent angles, all green this arc:
1. Registered regression pack (baseline-exact count comparison catches silent
   test-count drift, e.g. the 2026-08-15 21→43 growth documentation).
2. Ad-hoc targeted pack for the full touched-file set (independent confirmation of
   dev-reported totals — 51/51).
3. Real-behavior mock against the REAL class at the payload seam — env→LLMConfig→
   ClassVar wiring replicating startup; per-scenario env save/restore; the class
   under test never stubbed (mock-test skill's "mock tests are the truth").
4. Static drift sweep with explicit judgment per hit (LEGITIMATE vs OLD-DIRECTION),
   excluding known-updated files and `__pycache__`.
