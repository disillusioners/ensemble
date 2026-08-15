# Test Report: Child Error Resilience — Functional Verification
Date: 2026-08-15
Branch/Commit: `feature/child-error-resilience` @ `2fca56ae` (parent `94350082` on `latest`)
Change under test: 5 files, +727/−1 — error-report recovery hint (`daemon/services/error_reporting.py`), retryable malformed-response guard (`daemon/graph.py` + `daemon/llm_error_classifier.py`), 2 new unit test files.
Reviewer status pre-test: APPROVED (deep-review, 0 blockers).
Tester dispatches: 12 (1 recon, 1 script-creation, 9 pack runs, 1 commit). All 12 reported; 0 re-dispatches.

## Summary
- **Verdict: PASS — no functional failures found.**
- Packs: 9 executed (7 green, 1 vacuous-skip documented, 1 e2e subset green) + recon evidence
- Functional incident chain: VERIFIED end-to-end through real production modules
- Quick Fixes Applied: 0 (none needed — no failures anywhere)
- Quarantined: 0 new (1 pre-existing entry untouched)
- Test-infra commit: `de1538fb` (4 new pack files, +502)

## Scope Decision
> Full suite conditionally requested ("if time permits", developer deferred to tester). Change touches 5 files / 3 modules (graph, classifier, error-reporting), no architecture change, and **zero job/task/queue files in the diff** (verified via `git diff latest...HEAD --name-only` grepped for claim_pending_task / turn_transitions / reconcile_turn_mirror / job_processor / job_locks / task/repository / job_state_machine / dependency_bus / work_status / instance_messaging / job_queue — 0 matches). Full suite (262 packs) NOT warranted. Ran 9 targeted packs + cheapest e2e subset. Skipped: ~250 unrelated packs.

## 1. Scenario Verification — incident chain (the core deliverable)
Exercised via `test/packs/child_error_incident_repro_unit_test.py` — REAL production modules, not unit-test mocks. All steps [ok]:
- **A2** — poisoned provider (bare str returned through `client.with_raw_response.create().parse()` — the exact SDK seam from the incident) → real `ThinkingChatOpenAI.invoke()` → guard fires → `MalformedLLMResponseError("expected dict or object with model_dump(), got str")`, raw body carried on `.response`.
- **A5** — `MalformedLLMResponseError in TRANSIENT_EXCEPTIONS` → retryable by declaration.
- **A3+A4** — real `classify_llm_errors` re-raises the same instance (retryable handler, no wrapping); real `tenacity.Retrying` with `make_llm_retry_strategy(transient_max=3)` re-hits the provider **exactly 3×** (asserted on the mock client call count) then exhausts with the error unchanged; 3 `[LLM] Malformed response (retryable):` log lines observed mid-loop.
- **B1/B2** — generic `AttributeError` NOT in TRANSIENT_EXCEPTIONS; the exact incident signature `AttributeError("'str' object has no attribute 'model_dump'")` classified NON-retryable by the real predicate → the retry net was not widened.
- **C1** — real `ErrorReportingService._send_error_report`: exactly **1** message enqueued, to the **PARENT**.
- **C2** — original exhausted-error string preserved in the report.
- **C3** — `[RECOVERY GUIDANCE]` / `RECOVERY_GUIDANCE_HINT` present as the appended tail.
- **C4** — `type=error_report` metadata with `child_instance_id` linkage.
- **Hint presence through convergence paths**: recon mapped all 4 callers (manager.py:3723 stale-task bridge, manager.py:5682 manager wrapper, worker_pool.py:619 `_notify_parent_of_failure`, message_processing_errors.py:297) — every path routes through the single `_send_error_report` (error_reporting.py:399) with the hint appended once at :739. Verified at service level (C-phase) + jq error-reporting suite (24/24). Manager-wrapper/stale-task-bridge/worker_pool harnesses per-path were therefore redundant (single choke point) — noted as covered-by-construction.

## 2. Test-Suite Hygiene
| Pack | Baseline | Result | Runtime |
|------|----------|--------|---------|
| child_error_resilience_unit_test (2 NEW dev files) | 34 claimed | ✅ PASS 34/34 | 0.92s |
| llm_error_classifier_unit_test | 74 | ✅ PASS 74/74 exact | 0.49s |
| graph_retry_unit_test | 18 | ✅ PASS 18/18 exact | 0.63s |
| compaction_unit_test | 206 | ✅ PASS 206/206 exact | 1.12s |
| jq error reporting (tests/test_jq_error_reporting.py) | PASS-only doc | ✅ PASS 24/24 | 0.75s |
| concurrency_atomic_unit_test (ensure.md Critical) | 91P/74S 13-file canonical | ✅ PASS 91P/74S/0F | 6.98s |
| reasoning_content_regression_unit_test | 21 (stale) | ✅ PASS 43/43 (drift: fallback file grew 7→29 since May) | 0.68s |
| phase3_cascade_integration | 8 (stale) | ⚠️ VACUOUS 0 executed / 5 skipped (pre-existing CM-removal skips, predates 2fca56ae) | <1s |
| e2e happy_path (cheapest subset) | ~51s | ✅ PASS 1/1 | 47.30s |

- **Count reconciliation (34 vs 26):** both correct — 26 `def test_` functions, 2 of them parametrized ×5 params → 34 collected cases. Task brief's 34 matches pytest collection.
- **Full suite: NOT run** — not warranted (scope decision above); the battery above is the scoped evidence.

## ensure.md Validation Results
- **Critical 3/3 in-scope PASS:**
  - ✅ No regressions in changed packs — all executed packs PASS (cascade vacuous-skip is pre-existing, not this change)
  - ✅ Deadlock/concurrency integrity — concurrency_atomic 91P/74S/0F (13-file canonical)
  - ✅ No sync DB calls on event loop — same pack (thread-identity tests green)
  - ✅ dev.sh `--timeout-graceful-shutdown 10` — static grep PASS (dev.sh:102)
- **Release Gate: N/A** — the critical-note rule fires only when job/task/queue files change; this diff touches none (verified from git diff, not assumed). Cheapest e2e subset still run for graph.py infra due diligence: happy_path PASS 47.30s on the healthy pre-existing daemon (healthz 200, queue 0 pending before AND after).
- No contradictions between ensure.md and my rules this run.

## 3. Reviewer-Flagged Edge Cases
- **Cancelled-type reports:** hint is appended to ALL reports incl. `error_type="cancelled"` — no crash/format issue (jq suite 24/24; append is tail string-concat). Reviewer-endorsed accepted behavior, unchanged by this run.
- **Exception message includes offending type:** PASS — "got str" asserted (A2).
- **Generic AttributeError stays NON-retryable:** PASS — B1/B2, including the exact incident signature.

## 4. Regression
- Import smoke: `daemon.graph` + `daemon.services.error_reporting` + `daemon.llm_error_classifier` → `IMPORT_OK` (exit 0).
- TERMINAL_STATUSES / `_derive_legacy_status` / work_status.py / instance_messaging.py: diff-verified EMPTY — zero gate/revive/status-derivation changes.
- Daemon-level: happy_path E2E PASS on daemon serving with the modified graph code.

## Gaps
- None blocking. Two informational items:
  1. `tests/test_cascade_integration.py` no longer executes (pre-existing Phase 5 CM-removal skips) — PACKS.md row annotated VACUOUS/stale-baseline; needs a dependency-bus rewrite someday (not this change's debt).
  2. Reviewer-endorsed deferrals remain untested by design (streaming-path guard dormant, dedup-skip tests, cancelled-type hint suppression) — consistent with the approved review.

## Task-Notes Line-Number Drift (informational)
Verified claims but corrected locations: hint append error_reporting.py:**739** (brief: ~733); TRANSIENT member :**125** (brief: 119); `except MalformedLLMResponseError` handler is at **llm_error_classifier.py:580** — there is NO such handler in graph.py (brief mislabeled the file); compaction catch graph.py:**3045** (brief: 3044).

## Documentation Updated
- [x] RESULTS/2026-08-15-child-error-resilience-functional-test.md (this file)
- [x] PACKS.md — summary line, 3 new pack rows (child_error_resilience, child_error_incident_repro, reasoning_content_regression), concurrency 13-file canonical note, classifier/graph-retry/jq/cascade last-run refresh, cascade VACUOUS annotation
- [x] LESSONS/2026-08-15-child-error-resilience-campaign.md (7 lessons: SDK mock seam, chain-verification pattern, choke-point coverage, baseline archaeology ×3, vacuous pack, line drift)
- [ ] rules/ensure.md — untouched (user-owned)

## Code Changes Summary
- Production code: NONE (deliverable was report-only; no failures to fix)
- Test infra committed: `de1538fb` — test/packs/child_error_resilience_unit_test.sh, child_error_incident_repro_unit_test.sh + .py, reasoning_content_regression_unit_test.sh (+502 lines)

## Overall Status
- Unit Tests: ✅ PASS (scoped battery, baseline-exact everywhere executable)
- Functional/Mock Tests: ✅ PASS (incident chain end-to-end, all steps)
- ensure.md: ✅ PASS (Core Critical 3/3 in-scope; Release Gate N/A per scoping rule)
- **Testing Complete: ✅ READY — SHIP**
