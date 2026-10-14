# 2026-08-30 — WC-Wake Phase 1 Code Review (Deep-Review Council)

**Verdict: APPROVED** — 0 🔴, 3 🟡 (pre-flip conditions, not merge blockers), 6 🟢.
Range: `1f8f8ed4..c0ae6378` (15 commits, 33 files, +4385/−965) on `feature/wc-wake-report-integrity`.
Mode: Deep-Review (4 trigger categories). Council: governor `016408dd-7705-42ec-b4f9-74bd24b374b5`, councilors = worker×(agentic, coding), skill `code-review`, 2/2 completed.

## Result
- F1–F7 all PASS (F1 PASS with one plan-declared error-text drift; F6 PASS with 1 hygiene defect).
- Job_queue pack: **1569P/38S/0F exact baseline — reproduced independently by both councilors** (serial, `-p no:cacheprovider`). All other requested suites green; pre-existing exclusions honored.

## 🟡 Pre-flip conditions (land BEFORE D2.5-FLIP soak flip, not before merge)
1. **Kill-switch test cache pollution** — flag-ON tests leak module-global `_WC_WAKE_ENQUEUE_ENABLED` (no teardown; `tests/test_injection_api.py:208-222` + flag tests in `tests/unit/tools/test_job_visibility_tools.py`). Found independently by both councilors. Fix: autouse reset fixture.
2. **Resolver `""`-truthy** — `daemon/services/instance_messaging.py:135` treats blanked env (`ENSEMBLE_WC_WAKE_ENQUEUE=`) as ON → defeats instant-OFF revert in the classic incident reflex. Fix: remove `""` from truthy tuple. (D1 disagreement: agentic 🟡 vs coding 🟢; governor sided 🟡.)
3. **Phantom config key** — `docs/setup.md:368` documents `messaging.wc_wake_enqueue_enabled` not found in `daemon/` (resolver env-only; contrast real key `limits.governor_recursion_guard_enabled` at config.py:486). Config.yaml flip = silent no-op → soak clock never starts. PENDING dev confirmation (D2 genuine factual divergence).

## Awareness flags
- `watchover_*` ×80 and `job_queue_proxy_phase1` ×8 failures attributed by coding councilor as base/outside-diff — not on prior exclusion list; verify before next release.
- F1 OFF-state drift ledger: `daemon/tools/job_queue.py:1929-1941` error TEXT drifts from legacy (behavior identical; plan §6-T2 declared it) — record in revert-runbook byte-compat ledger.

## Strongest PASS evidence (from council)
`instance_messaging.py:332-490` (D1 heal list-local, no aupdate_state, graph_input=None skip, R1 id parity); `:608-614` (D2 ordering, leftovers strictly last-before-user); `manager.py:2502-2548` (`requeue_injections` FIFO prepend-order-preserving); `messages.py:383-387` + `tools/instance.py:893-899` + `tools/job_queue.py:1923-1938` (OFF ≡ legacy by construction); `tests/integration/test_wc_wake_pure_hang.py` (real-engine 3-surface wake acceptance).
