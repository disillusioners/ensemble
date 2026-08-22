# Test Report: Reasoning-Echo Allowlist→Denylist Flip

Date: 2026-08-22T04:00Z
Branch: `feature/reasoning-echo-denylist` @ `28ea76a9` (inversion + test flips) + `018800b8` (CHANGELOG + deprecation helper + docstring) — test-infra commits atop: `ae8d0c83` (targeted pack), `1f8149bd` (mock verification script)
Instance IDs: recon `871f55b0`, sanity `99a4b980`, mock `478b748d`, pack-regression `597a6c2b`, pack-targeted `53ec23b5`, pack-concurrency `374ec5e4`

## Summary
- Total: 6 dispatches | Passed: 6 | Failed: 0 | Errors: 0
- Unit Tests: 94 (43 regression trio + 51 targeted 4-file) — all PASS
- Real-behavior Mock Tests: 6/6 scenarios PASS (0.15s)
- ensure.md: Core 4/4 Critical PASS (+2/2 in-scope Important) — Release Gate NOT triggered
- Quick Fixes Applied: 0 (clean first-run passes everywhere)
- Quarantined: 5 tests skipped elsewhere (TestAccessMemoryArchive — pre-existing, unrelated, not in any run scope)

### Scope Decision
> Full suite NOT warranted — change is a single-subsystem payload-gate flip: `daemon/graph.py` echo gate + `daemon/config.py` env parsing + `daemon/__main__.py` wiring + 4 test files. No job/task/queue touch → project e2e convention (ensure.md Release Gate) NOT triggered. Ran: reasoning regression pack + new targeted pack + real-behavior mock + concurrency_atomic (ensure.md gate precedent from 2026-08-18 reasoning-echo arc) + static sweeps. Skipped: full non-integration suite, e2e packs, frontend.

## 1. Regression Pack — PASS
`test/packs/reasoning_content_regression_unit_test.sh` → **43/43 in 0.68s** (exit 0, baseline-exact vs PACKS.md 2026-08-15 record). Inner `timeout 120s`, outer `timeout 300`. Quarantined TestAccessMemoryArchive did not appear — scope confirmed clean.

## 2. Targeted 4-File Suite — PASS (dev claim confirmed)
NEW pack `test/packs/reasoning_echo_targeted_unit_test.sh` (commit `ae8d0c83`, created by recon worker, registered in PACKS.md:115) → **51 passed / 0 failed / 0 skipped in 0.73s**. Breakdown: roundtrip 8 + fallback 29 + edge_cases 6 + config 8 = 51. Developer-reported "51 passed" independently confirmed.

## 3. Real-Behavior Mock Verification — PASS 6/6
Script `tests/mocks/reasoning_echo_denylist_mock.py` (commit `1f8149bd`), runtime 0.15s, dual-layer (`signal.alarm(180)` + `timeout 200`), no ports/daemon/network; REAL `ThinkingChatOpenAI` never stubbed; env→LLMConfig→ClassVar wiring replicates `daemon/__main__.py:30-32`; assertions on actual `_get_request_payload` output.

| # | Scenario | Evidence | Verdict |
|---|----------|----------|---------|
| S1 | Default (no env): gpt-4o echoes | payload includes `"S1-thinking"` | ✅ PASS |
| S2 | `DISABLED_MODELS=gpt-4o`: gpt-4o blocked, deepseek-chat spared | gpt-4o: False / deepseek-chat: True | ✅ PASS |
| S3 | Case-insensitive `GPT-4O` disables gpt-4o | payload False | ✅ PASS |
| S4 | Empty-string env → `[]` (no `[""]` poison) | gpt-4o still echoes True | ✅ PASS |
| S5 | Old key `MODELS=deepseek`: warning fires once, behavior unchanged | gpt-4o True + exactly 1 warning (logger dedup) | ✅ PASS |
| S6 | Presence gate: WITH reasoning echoes (non-tool-call turn); WITHOUT never echoes | True / False | ✅ PASS |

## 4. Sibling Drift Sweep — 0 OLD-DIRECTION findings
Full `tests/` + `test/packs/` sweep excluding the 4 known-updated files: zero references to old env key or old ClassVar in source; all semantic hits judged LEGITIMATE (capture-path, direction-neutral, or new-direction infra — full hit table in sanity report). The 2026-08-18 drift lesson's re-audit HELD — independently confirmed. Only artifact: stale `tests/__pycache__/conftest.cpython-313.pyc` bytecode (regenerates; no repo-source drift).

## 5. Import Sanity — PASS
- `import daemon.api` → success (exit 0)
- `daemon/__main__.py` → py_compile OK + live import success (`main()` guarded; `warn_deprecated_reasoning_echo_env()` at `__main__.py:40` inside `main()`)
- `ThinkingChatOpenAI` reasoning attrs → `['_should_echo_reasoning', 'reasoning_echo_disabled_models']` — new ClassVar present, old absent, no AttributeError
- `LLMConfig` → `reasoning_echo_disabled_models` field + `mode="before"` CSV/JSON validator, env `OPENAI_REASONING_ECHO_DISABLED_MODELS`

## ensure.md Validation Results (scoped)
- **Critical 4/4 PASS**
  - ✅ R1 No regressions in changed packs: regression 43/43 + targeted 51/51 (+ concurrency adjacent baseline-exact)
  - ✅ R2 Deadlock/concurrency integrity: `concurrency_atomic_unit_test` 91P/74S/0F in 7.52s — baseline-exact
  - ✅ R3 No sync DB calls on event loop: same pack thread-identity tests PASS
  - ✅ R4 dev.sh `--timeout-graceful-shutdown 10`: present (dev.sh:102)
- **Important 2/2 (in-scope) PASS** — R6 deadlock scenario (concurrency pack); R5 async-caller grep out of scope (change converts no async functions)
- **Nice-to-have**: old-key path is dead-by-design with deprecation warning (intentional, CHANGELOG'd) — not dead code
- **Release Gate: NOT RUN** — correctly out of scope (no job/task/queue touch; not a cross-module/architecture change)

### ensure.md Improvement Notices
None — no contradictions encountered this arc.

## Observations (non-blocking, informational)
1. 🟢 `warn_deprecated_reasoning_echo_env` has zero pytest-suite coverage; the mock's S5 covers it end-to-end. If dev wants suite coverage, one test in `test_llm_reasoning_echo_config.py` suffices.
2. 🟢 Deprecation dedup is per-process: flag consumed even by an env-absent first call, so a later env-set call stays silent within the same process. Benign at real startup (env fixed before first call) — worth knowing if startup order ever changes.
3. 🟢 Stale `tests/__pycache__` retains old-key bytecode → false grep positives until regenerated.

## Code Changes Summary (this testing session)
- `test/packs/reasoning_echo_targeted_unit_test.sh` — NEW targeted pack (39 lines) — commit `ae8d0c83`
- `tests/mocks/reasoning_echo_denylist_mock.py` — NEW real-behavior mock verification (446 lines) — commit `1f8149bd`
- `.agents/tester/` docs: PACKS.md (arc entry + 3 row updates), MOCK_TESTS.md (spec + last-run), RESULTS/ + LESSONS/ (this arc) — committed by docs worker

## Documentation Updated
- [x] PACKS.md — arc summary + targeted pack registered + regression/concurrency rows + ensure.md table
- [x] MOCK_TESTS.md — new mock spec (ACTIVE) + Last Run
- [x] RESULTS/2026-08-22-reasoning-echo-denylist.md — this report
- [x] LESSONS/2026-08-22-reasoning-echo-denylist-verification.md — drift re-audit closure
- [ ] rules/ensure.md — no changes (user-maintained)

---

### Overall Status
- Unit Tests: ✅ PASS (43/43 + 51/51)
- Real-Behavior Mock: ✅ PASS (6/6)
- Sibling Drift: ✅ CLEAN (0 findings)
- Import Sanity: ✅ PASS
- ensure.md Core: ✅ 4/4 Critical
- **Testing Complete: ✅ READY — verdict SHIP for feature/reasoning-echo-denylist (28ea76a9 + 018800b8)**
