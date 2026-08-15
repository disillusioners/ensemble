# Child Error Resilience — Functional Verification Campaign (2026-08-15)

Commit `2fca56ae` on `feature/child-error-resilience` (parent `94350082` on `latest`).
Outcome: **PASS** — 10/10 nodes green, 0 production bugs, 0 quick fixes needed.

## What the change does
1. `RECOVERY_GUIDANCE_HINT` (error_reporting.py:41) appended in `_send_error_report` (:739) — guides parent: revive-once → spawn-new → stop-and-report.
2. `MalformedLLMResponseError` (llm_error_classifier.py:77) raised by `ThinkingChatOpenAI._create_chat_result` guard (graph.py:1826) for non-dict/non-model responses; member of `TRANSIENT_EXCEPTIONS` (:125) → RETRYABLE; handler at classifier:580; compaction catch tuple graph.py:3045.

## Key lessons

### 1. Mock the SDK seam, not `client.create`
Bare-str provider responses flow through `client.with_raw_response.create(**payload).parse()` → `_create_chat_result(response)`. Patching `client.create` directly does NOT reproduce the incident — MagicMock auto-creates `.model_dump`, so the guard never fires. The mock must land on `raw_response.parse.return_value = <bare str>`. Encoded in `test/packs/child_error_incident_repro_unit_test.py::make_incident_llm`.

### 2. Incident-chain verification beats re-asserting unit mocks
The deliverable was FUNCTIONAL verification "beyond the unit tests". The chain pack drives real production modules end-to-end: poisoned provider → real `ThinkingChatOpenAI.invoke()` → guard → real `classify_llm_errors` → real tenacity `Retrying` (asserts provider re-hit exactly 3× then exhaust) → real `ErrorReportingService._send_error_report` (asserts 1 enqueue to PARENT + `[RECOVERY GUIDANCE]` tail + metadata linkage). Staged probing (guard / retry-exhaust / report separately) before writing the full script caught the seam issue (lesson 1) cheaply.

### 3. Convergence-path analysis can replace per-path testing when there is a single choke point
Recon proved all 4 `_send_error_report` callers (manager.py:3723 stale-task bridge, manager.py:5682 wrapper, worker_pool.py:619, message_processing_errors.py:297) route through the single service method where the hint is appended. Verifying hint presence at the service level (C-phase) + the jq error-reporting suite (24/24) covers every path without 4 separate harnesses.

### 4. Baseline archaeology is mandatory before comparing
Three baseline surprises this run, all benign:
- **concurrency pack**: PACKS.md Location column lists 7 files but the script runs 13 → canonical figures are 91P/74S (13-file) vs 66P/19S (7-file). Reconciled note exists at PACKS.md:162; use the 13-file figure when running the script as-is.
- **reasoning fallback**: file grew 7→29 tests since the May baseline (21→43 total) — pass, not regression.
- **new dev tests**: 26 `def test_` functions but 34 collected cases — 2 parametrized functions × 5 params. `grep -c "def test_"` undercounts parametrized suites.

### 5. cascade_integration pack is vacuous (pre-existing)
All 5 tests skip-marked since Phase 5 CorrelationManager removal (`bf9e5890`). Exit-0 PASS is vacuous — 0 executed. Do not cite this pack as coverage for anything. PACKS.md row annotated. Un-skipping requires rewriting against the dependency-bus architecture (out of quick-fix scope).

### 6. Task-brief line numbers drift — verify against source
Brief said hint @:733 (actual :739), TRANSIENT member @:119 (actual :125), handler @graph.py:580 (actually classifier.py:580 — no such handler in graph.py), compaction @:3044 (actual :3045). Substance held; locations corrected in RESULTS. Always re-verify claimed line numbers via grep before citing them.

### 7. ensure.md gate scoping — verified, not assumed
The e2e Release Gate critical note fires on job/task/queue file changes. Verified via `git diff latest...HEAD --name-only | grep -E "claim_pending_task|turn_transitions|reconcile_turn_mirror|job_processor|job_locks|task/repository|job_state_machine|dependency_bus|work_status|instance_messaging|job_queue"` → zero matches → gate N/A. Still ran the cheapest e2e subset (happy_path, 47.3s PASS) because graph.py is core infra — precedent: LoopRepairer 2026-08-14.

## Numbers
| Pack | Result |
|---|---|
| child_error_resilience_unit_test (NEW dev tests) | 34/34 PASS |
| child_error_incident_repro_unit_test (NEW chain) | PASS all steps A2/A5/A3+A4/B1/B2/C1-C4 |
| llm_error_classifier | 74/74 baseline-exact |
| graph_retry | 18/18 baseline-exact |
| compaction | 206/206 baseline-exact |
| jq error reporting | 24/24 |
| concurrency_atomic (ensure.md Critical) | 91P/74S/0F 13-file canonical |
| reasoning_content regression | 43/43 (drift 21→43 documented) |
| e2e happy_path subset | 1/1 PASS 47.30s |
| cascade_integration | vacuous (5 skipped, pre-existing) |

Full report: `RESULTS/2026-08-15-child-error-resilience-functional-test.md`
