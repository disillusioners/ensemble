# Test Report: LLM Provider HA Auto-Fallback (`OPENAI_BASE_URL_BACKUP`)

Date: 2026-08-15
Branch: `feature/llm-provider-fallback` — feature commits `d2c218a2` (feat) + `ef35ff4a` (fix); test infra commits `67b4bb19`, `45f9a4a6` (this session)
Instance IDs: 9f1fb5c7 (recon), d6646adb (pack create), 4477174f, a50ab7f9, 97cde039, 1747caa5, fe7be7ea (regression packs), d6fef368, 6a4aaa73, 09158447, 2f841eb3 (feature packs)

## Summary
- **9 packs run, 9 resolved. Total 192 feature/adversarial/regression tests + 5 regression packs — 0 NEW failures anywhere.**
- Feature suites: 192/192 PASS (64 failover + 74 classifier + 18 graph-retry + 36 adversarial NEW)
- Regression: config-override 31/31 · concurrency 91P/74S/0F (baseline match) · loop-detector 28/28 · loop-repairer 29/29 · core 710P + 41F (41/41 = documented pre-existing baseline, 1:1 match)
- ensure.md Core: 4/4 Critical PASS, 2/2 Important PASS, 1/1 Nice-to-have PASS
- Quick Fixes Applied: 0 (none needed)
- Quarantined: 1 (pre-existing, unchanged — `TestManagerGetInstanceAsync::test_manager_get_instance_delegates_to_lifecycle_service`, SQLite migration `20260714_000001` class)
- **Verdict: SHIP**

## Scope Decision
> Full suite not warranted. Scope = cumulative diff `de3d3582..ef35ff4a`: 19 files, +2645/−67, centered on LLM invocation path (`daemon/llm_error_classifier.py` +364, `daemon/graph.py` +180, `daemon/config.py` +69) with small IndexError-handling edits in manager/instance_lifecycle/child_reports/keyword_extraction/title_generation. Pure LLM-path change — does NOT touch job/task/queue system (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks), so the ensure.md mandatory full e2e gate does NOT trigger (confirmed by diff file list). Ran: 4 feature packs + 5 regression packs (config, graph-adjacent middleware, manager-async discipline, broad core sweep). Skipped: full suite (~40+ min across 250+ packs), e2e packs, PG packs, frontend (no frontend changes).

## Test Focus Results (task's 7 focus areas)

### 1. Re-run feature suites — ✅ PASS
| Pack | Tests | Result | Runtime |
|---|---|---|---|
| `llm_failover_unit_test` | 64 | ✅ 64/64 | 11s |
| `llm_error_classifier_unit_test` | 74 | ✅ 74/74 | 0.62s |
| `graph_retry_unit_test` | 18 | ✅ 18/18 | 0.82s |

Independent verification (fresh workers, pack discipline, dual-layer timeout) confirms dev claim. Note: dev said graph-retry "18" was "11" — collect-only showed 18 (7 pre-existing graph-retry tests predate the feature); all green either way.

### 2. Zero-behavior-change with backup unset — ✅ VERIFIED (independently, not just suite-trusted)
36 NEW adversarial tests (commit `67b4bb19`) incl. dedicated `TestZeroBehaviorChange*` battery:
- Retry budgets remain 10 transient / 3 timeout on primary (asserted via config values AND exact attempt counts in mocked failure scenarios)
- No failover state engages under failure storms — URL never swaps (1000-attempt IndexError sweep + failure-storm URL assertion)
- IndexError stays **non-retryable** in the pre-HA path (controller absent/unconfigured)
- Adversarial worker confirmed rule from code: `IndexError` retried ONLY when `failover_controller is not None and failover_controller.is_configured`

### 3. Failover works end-to-end — ✅ VERIFIED via MockTransport
- Primary returns 500s → requests verified landing on backup URL (transport call-URL inspection)
- `[LLM-HA]` WARNING logged on swap (caplog capture)
- Backup-down fall-through: both legs exhaust → original `TransientAPIError` reraised (operators see the failure, no silent swallow)
- No `[LLM-HA]` warning when no backup configured
- Daemon spin-up path NOT exercised (env lacks runnable daemon per task allowance — "MockTransport-based verification acceptable"); this is the one focus area verified at client level, not daemon level

### 4. Budget split arithmetic (W2) — ✅ VERIFIED
6 boundary tests: primary slice < / == / > budget × {transient, timeout}. `effective_cap = min(primary_cap, full_budget)` — operator budget stays a CEILING. Custom `retry_config` (e.g. `transient_max=2`) still allows failover (W2 regression fixed). Constants: `PRIMARY_TRANSIENT_MAX=3`, `PRIMARY_TIMEOUT_MAX=2`.

### 5. Config validation — ✅ VERIFIED
11 tests: unset → None; empty → None; whitespace-only (5 parametrized) → None; non-string garbage (6 parametrized, incl. YAML `true` → bool) → legible targeted error, no silent corruption; YAML empty-substitution edge. `LLMConfig.base_url_backup` validator `_coerce_base_url_backup_empty_to_none`.

### 6. Regression sweep — ✅ 0 NEW failures
| Pack | Result | Baseline comparison |
|---|---|---|
| `core_unit_test` (19 files) | 710P / 41F / 0E | 41/41 match documented baseline (38× SQLite migration `20260714_000001` cascade in test_manager.py + 2× test_agents_api isolation + 1× migration_api) — **1:1, zero new** |
| `concurrency_atomic_unit_test` | 91P / 74S / 0F | Identical to 2026-08-14 baseline |
| `llm_config_override_unit_test` | 31/31 | Matches prior 31/31 |
| `loop_detector_unit_test` | 28/28 | Matches 2026-07-17 |
| `loop_repairer_unit_test` | 29/29 | — |

Dev's "43f/6e baseline" claim: not found in RESULTS/ (canonical documented baseline for this pack class is 41f). The observed 41f/0e matched the documented baseline exactly — no discrepancies introduced.

### 7. Web/frontend — ✅ N/A confirmed
Diff contains zero frontend files. No browser automation run (correctly scoped out).

## ensure.md Validation Results (Core, blast-radius scoped)
- **Critical 4/4**:
  - ✅ No regressions in changed packs — all 9 packs PASS/baseline-clean
  - ✅ Deadlock/concurrency integrity — `concurrency_atomic_unit_test` PASS (91P/74S/0F)
  - ✅ No sync DB calls on event loop — covered by concurrency pack (thread-identity tests skipped per baked-in skipmarks, unchanged from baseline)
  - ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` — static check PASS (dev.sh:102)
- **Important 2/2**: await-correctness (no violations surfaced in any pack) · original deadlock scenario covered by concurrency pack
- **Nice-to-have 1/1**: no dead code — `clean_llm_config` strip + `reset_to_primary()` exercised by tests (no orphaned code paths found)
- Release Gate: NOT triggered (change is LLM-path scoped, not job/task/queue architecture)

## Quick Fixes Applied
None — zero failures requiring fixes; no production bugs found by 36 adversarial tests.

## Action Needed
- [ ] none blocking. Optional (non-blocking) observations:
  - Production side-LLM calls (title_generation, keyword_extraction, child_reports) use bare `except Exception` around `asyncio.to_thread(llm.invoke, ...)` — no failover coverage there by design (documented behavior: graceful fallback values). If HA matters for those paths later, wrap with `classify_llm_errors` + controller like the main path.
  - `pytest-timeout` plugin absent from `.venv` → `--override-ini="timeout=120"` is inert; shell `timeout 110s` remains the real inner guard (dual-layer intact via bash layer). Installing the plugin would restore 3-layer protection.

## Documentation Updated
- [x] PACKS.md — 4 packs registered + statuses set to ✅ PASS with dates/runtimes; summary counts 252→256 packs (Unit 197→201); feature-run summary line added
- [x] RESULTS/2026-08-15-llm-provider-failover-feature-test.md — this report
- [x] LESSONS/2026-08-15-unregistered-feature-suites.md — PACKS.md integrity gap + fix
- [ ] MOCK_TESTS.md — no changes (no mock-service tests added; MockTransport is in-process, no ports)
- [ ] QUARANTINE.md — no changes (1 pre-existing entry, unchanged)

## Code Changes Summary (this session, all test-infra; committed)
- `test/packs/llm_failover_unit_test.sh` — NEW pack (commit `67b4bb19`)
- `test/packs/llm_error_classifier_unit_test.sh` — NEW pack (commit `67b4bb19`)
- `test/packs/graph_retry_unit_test.sh` — NEW pack (commit `67b4bb19`)
- `tests/unit/test_llm_failover_adversarial.py` — NEW 36 adversarial tests (commit `67b4bb19`)
- `test/packs/llm_failover_adversarial_unit_test.sh` — NEW pack (commit `45f9a4a6`)
- `.agents/tester/PACKS.md` — 4 rows + statuses + summary (committed with `67b4bb19`; status/date updates this session)
- Production code: **ZERO changes** by tester session

## Overall Status
- Feature suites: ✅ PASS (192/192)
- Regression: ✅ 0 NEW failures (41f = pre-existing baseline, 1:1)
- ensure.md Core: ✅ 4/4 Critical
- Adversarial verification: ✅ all 7 focus areas verified, 0 production bugs
- **Testing Complete: ✅ READY — Verdict: SHIP**
