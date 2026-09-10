# Pre-Merge Verification — Answer-Gate Resume Lifecycle Fix

**Date:** 2026-09-10
**Branch:** `feature/fix-answer-gate-resume` @ `c6348df06024c8f696583dad6da90ea84358d2ff` (6 commits over base `e58bbbbf` / v0.12.6)
**Change set:** 3 prod files (`daemon/routers/instances.py` +182, `daemon/manager.py` +47, `daemon/services/task_processor.py` +91) + 1 new test file (`tests/unit/test_answer_gate_resume_chain.py`, 1133 lines). Diffstat verified: 4 files, +1436/−17.
**Verdict:** ✅ **PASS FOR MERGE** — all scoped suites green on SQLite AND PostgreSQL; fail-on-base mechanically proven at both target defect sites; zero branch-caused failures; zero PG-dialect findings; endpoint behaviors 3/3; all known pre-existing failures adjudicated (one node-identity correction, §4).

Workers: 11 instances (2 infra, 1 authoring, 7 execution, 1 architecture-fix), verify-only — **0 repo writes, 0 commits, 0 pushes**; drift-pinned per pack; dual-layer timeout everywhere; `uv run python -m pytest` exclusively; `ensemble_prod` never mutated; all disposable resources force-dropped and verified.

---

## 1. Per-Suite × Per-Engine Results

| Suite | SQLite | PostgreSQL | Notes |
|---|---|---|---|
| `tests/unit/test_answer_gate_resume_chain.py` (10 nodes) | **10/10 PASS** (0.61s) | **13/13 PASS** (1.71s) — 10 chain + dialect canary + 2 isolation canaries | PG via engine-shadow (star-import + engine override, per LESSONS 2026-09-10 §1 recipe) |
| **Focused set (PINNED: 14 files / 160 tests)** | **160 collected → 155 P / 5 F / 0 E** (3.45s pytest) | n/a (skip-path subset below) | All 5 F inside `TestPausedAutoResumeFallback` — known pre-existing, identical `TypeError: object MagicMock can't be used in 'await' expression` signature; **0 new failures** |
| Skip-path pause_root shadow (`test_pause_resume_root.py` → PG) | — | **21/21 PASS** (7.06s) — 18 real + 3 canaries | Exercises the guarded `complete_task` prior_status CAS the Defect-2 carve-out bypasses |
| Skip-path deferred_recovery shadow (`test_resume_router_deferred_recovery.py` → PG) | — | **28/28 PASS** (3.18s) — 25 real + 3 canaries | Rowcount-guarded recovery SQL; stable 3× re-runs |
| `concurrency_atomic_unit_test` (ensure.md Core) | **98P / 0F / 74S** (9s) | — | Exact recorded baseline |
| **PG-side totals** | — | **62/62** (53 real + 9 canaries) | Zero dialect findings |

**Count reconciliation (task item 1):** dev reported 144, reviewer measured 131 — different module sets. This gate's authoritative focused set = **14 files / 160 tests**:
`tests/unit/test_answer_gate_resume_chain.py`, `test_cascade_pause_resume.py` (18), `test_child_resume.py` (8), `test_explicit_handle_resume_report_guard.py` (10), `test_pause_resume_root.py` (18), `test_paused_auto_resume_fallback.py` (5), `test_question_deferred_pause_callback.py` (6), `test_question_deferred_pause_edge_cases.py` (5), `test_resume_child_notification.py` (12), `test_resume_message_append.py` (6), `test_resume_router_deferred_recovery.py` (25), `test_resume_waiting_children.py` (7), `test_turn_handle_transitions.py` (21), `tests/test_resume_gate.py` (9).

## 2. Fail-on-Base Verification (task item 2) — ✅ PROVEN, assertion-class

Run in detached base worktree `/private/tmp/answergate-base-e58bbbbf` @ `e58bbbbffff05145e67b2a391b7056a265f2fbfb` (chain file copy-in, removed by EXIT trap, base left clean):

| Node | Class | Evidence |
|---|---|---|
| `TestResumeEndpointGateSupersession::test_plain_message_via_resume_while_gated_does_not_consume_handle` (Defect-1 full-chain repro) | **assertion-failure** | `AssertionError: Expected 'resume_processing_job' to not have been called. Called 1 times. Calls: [call('inst-gate-steal', message='actually I just want to ask something else', ...)]` — the gate-steal reproduces verbatim at base (plain message consumed the gate) |
| `TestScheduleExplicitHandleResumeRealSeamPreservesAwaitingAnswerHandle::test_real_seam_preserves_handle_and_cancels_paused_external` (Defect-2 seam carve-out, real `_schedule_explicit_handle_resume` seam) | **assertion-failure** | `AssertionError: assert 'cancelled' == 'paused'` (chain:707) — at base the real seam CANCELS the awaiting-answer handle instead of preserving it |

Both failures are genuine assertion failures at the exact defect sites (not import/collection artifacts) — the strongest fail-on-base signal. Logs: `/tmp/answergate-gate/logs/pack5_node_{a,b}.log`.

## 3. PG-Side Execution (task item 3) — ✅ zero dialect findings

Mechanism: engine-shadow modules (auto-staged untracked under `tests/postgres/`, removed by EXIT traps) — star-import the original test classes, override the SQLite `engine` fixture with a PG engine bound to per-test dedicated schemas via **libpq connect-options `search_path`** (LESSONS §1 — connection-scoped `SET` is forbidden), `create_all` per test, `DROP SCHEMA ... CASCADE` reset via AUTOCOMMIT admin connection after `engine.dispose()`. Non-vacuity + isolation proven by `TestDialectCanary` + shared-seed-key isolation canaries in every shadow.

Explicitly watched, all clean: guarded `complete_task` prior_status CAS (repository.py:2054), `DELETE … RETURNING` claim family (watcher_repository.py:203-296), rowcount-guarded UPDATE/DELETE, timestamp/uuid comparisons. **No PG-specific bug surfaced** — prod truth parity confirmed for the SQL-sensitive skip-path claim-first logic.

## 4. Pre-Existing Failure Adjudication (task item 4) — ✅ identical base ≡ branch, with node-identity correction

`tests/e2e/test_answer_dismiss_flow.py` (3 nodes, in-process) run at base AND branch, `--override-ini="timeout=120"`:

| node | base | branch |
|---|---|---|
| `test_answer_handle_resolves_while_paused_before_cascade` | PASSED | PASSED |
| `test_full_answer_lifecycle_pause_answer_resume_complete` | **FAILED** | **FAILED** |
| `test_suspension_reason_awaiting_answer_persisted_correctly` | PASSED | PASSED |

⚠️ **Node-identity correction for the known-pre-existing ledger:** the task/council-named node `test_suspension_reason_awaiting_answer_persisted_correctly` **PASSES on both sides**. The actually-failing node is `test_full_answer_lifecycle_pause_answer_resume_complete` — identical class and text on both sides: `AssertionError: Terminal Task MUST have JobItem admission_state='done'; got 'active'.` at `tests/e2e/test_answer_dismiss_flow.py:599` (JobItem mirror seam). Adjudication conclusion unchanged: failure exists at base, identical on branch → **pre-existing, not branch-caused, not branch-masked**. Recommend updating the standing known-pre-existing list to name `test_full_answer_lifecycle_pause_answer_resume_complete`.

Other known pre-existing, status: `TestPausedAutoResumeFallback` ×5 reproduced in focused set (identical signature — MagicMock drift class) → pre-existing confirmed; `test_phase4_manager_decomposition` ×1, `test_pause_during_report_resume_turn_handle` ×2 (QUARANTINED, base-evidenced), `TestAccessMemoryArchive` ×5 (quarantined), `TestPromptComposition` ×2 — all outside this gate's scoped suites; none encountered, none changed.

## 5. Endpoint-Behavior Check (task item 5) — ✅ 3/3 PASS (TestClient-on-disposable-PG mode)

**Mode justification:** real-uvicorn boot was infeasible in the time-box because `QuestionManager._packs` (`daemon/services/question_manager.py:211`) is an in-memory per-process singleton — seeding a pending gate in a separate live process requires a real LLM-driven `question` tool turn (multi-minute). Mode 2 used instead: the SAME FastAPI app/router via `httpx.AsyncClient + ASGITransport`, real disposable PG (`ensemble_test_answergate_boot_c6348df0`, OWNER ensemble, force-dropped after), real ASGI dispatch; manager seam middleware-injected per the chain-test pattern. Real-`enqueue_message` PG-row coverage cross-referenced to chain unit pins (chain file :435-442, :1037-1042).

| Behavior | Verdict | Key evidence |
|---|---|---|
| (a) resume + plain message with pending gate | **PASS** | 200; `gate_superseded=true`; `resume_results[id].status="enqueued_as_fresh_message"`, `.route="api_gate_supersede"`, `message_id` present (message-id invariant); `enqueue_message` called once with the user's plain text, `source="api_gate_supersede"`; cascade invoked AFTER enqueue (ordering pinned); SSE `status="superseded"`; pack cleared + pause flag dropped |
| (b) answer with no handle | **PASS** | 200; `status="answer_fallback_enqueued"` (distinct from `"answered"`); `.route="api_answer_fallback"`; message = formatted Q↔A echo; NOT lost (enqueue surface verified; warning log proves fallback branch, not 200-mask) |
| (c) enqueue failure | **PASS** (both endpoints) | `/resume` → **500**, raw-string detail `"superseded question gate but failed to enqueue message: …"`, cascade count 0, instance still `paused`; `/answer` → **500**, structured `ErrorResponse{INTERNAL_ERROR}`, cascade 0, `paused` |

Observation (not a defect): **asymmetric 500 envelopes** — `/resume` supersession failure returns a raw string detail; `/answer` fallback failure returns the structured `ErrorResponse.model_dump()`. The chain file pins only the structured shape; the live check confirmed both verbatim.

## 6. 500-Retry Wrinkle (task item 6 — RECORDED, documentation only)

After a supersession-500 on `/resume`, the in-memory question pack is already cleared, so a client RETRY of the same request no longer takes the gate-supersede branch — it routes via `resume_processing_job` and consumes the resume handle as the answer. **Content is still delivered (no loss), but the retry semantic differs from the first attempt.** Attached to backlog #3 per task instruction — documented here, no fix attempted in this gate.

## 7. ensure.md Scoped Validation

| Requirement | Status | Evidence |
|---|---|---|
| Critical #1 — no regressions in changed packs | ✅ PASS | all scoped packs green (modulo §4-adjudicated pre-existing) |
| Critical #2 — deadlock/concurrency integrity | ✅ PASS | `concurrency_atomic_unit_test` 98P/0F/74S in 9s — exact baseline |
| Critical #3 — no sync DB calls on event loop | ✅ PASS | same pack (thread-identity tests) |
| Critical #4 — dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | dev.sh:102 (flag), :99-101 (comment) |
| Important #1 — async-await callers | N/A-scope | named functions (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`) untouched by this diff |
| Important #2 — original deadlock scenario | ✅ PASS | covered by concurrency pack |
| Release Gate | NOT TRIGGERED | scoped bugfix (3 prod files, single subsystem); not big/critical/architecture |

No ensure.md contradictions. No `pytest -x` anywhere.

## 8. Verifier-Infra Anomalies & Lessons (0 code-under-test impact)

1. **First skippath-PG run:** pytest-timeout killed pytest mid-teardown — `--override-ini="addopts="` does NOT neutralize pytest-timeout (it reads the `timeout` ini option). Fixed with `--timeout=0`.
2. **Second run timeout was a HANG, not throughput** (correcting the interim diagnosis): `NullPool` × `asyncio.to_thread(<session-bound-method>)` cross-thread deadlock in the deferred_recovery shadow — verified by minimal repro; fixed by `StaticPool` in that shadow (28/28 in 3.18s, stable ×3). Original combined pack preserved at `/tmp/answergate-gate/packs/answer_gate_skippath_pg_test.sh` for the record.
3. **Star-import shadow gap:** `import *` silently drops underscore fixtures — `_wire_bus_mock` needed an explicit import in the pause_root shadow (the single "1 error" in early runs).
4. **PG shadow pack sizing:** split at ≤ ~30 tests/pack (49-test combined pack could not be trusted under one 280s budget with per-test schema cycles).
5. **Protocol deviation (no data risk):** skippath run-1 cleanup verification issued a read-only catalog SELECT against `ensemble_prod` (`pg_tables` count). Instructions now prohibit ANY `ensemble_prod` connection, including read-only; all subsequent runs verified by observing no connection. Full lessons: `LESSONS/2026-09-10-pg-shadow-star-import-staticpool-pytest-timeout.md`.

## 9. Safety & Cleanup

- Disposable DBs (all `CREATE DATABASE … OWNER ensemble`, all `DROP … WITH (FORCE)` + verified 0 rows): `ensemble_test_answergate_{boot,chain,skippath,skippr,skipdr,smoke}_c6348df0`; leaked diagnostic schema `answergate_skippath_test` also dropped (`pg_namespace` like-filter → 0).
- No shadow-file residue (`tests/postgres/` clean), base worktree clean, branch worktree only the pre-existing untracked `docs/llm-stream-stall-hardening.md`; drift-pins held `c6348df0…` / `e58bbbbf…` throughout.
- Ports 8079 (running old-code daemon — never contacted for testing) and **8088 (ensemble self-system — never touched)**; no orphan processes; port 18790 never left listening.
- `ensemble_prod`: never mutated, never used as a test target (see §8.5 for the single read-only catalog query deviation).

## 10. Worker Inventory

| Instance | Role |
|---|---|
| da8c7153 | infra discovery + endpoint-behavior live-boot (e2e-test) |
| 297615a9 | base worktree setup @ e58bbbbf |
| d8b9b9ff | pack authoring (6 packs + PG shadow harness + smoke) |
| e2f7a1c9 | chain SQLite execution |
| 956f18cd | focused set SQLite execution |
| fb941706 | chain PG shadow execution |
| affeb8bd | skippath PG execution ×2 (fail → repair re-run) |
| c7ca6c47 | fail-on-base execution |
| 4da8d6d9 | e2e dismiss adjudication |
| 13bbe432 | concurrency pack execution |
| e24ce1b6 | Test Architecture Fix (pack split + StaticPool + underscore-import) |

Ad-hoc packs (ephemeral, `/tmp/answergate-gate/packs/`): `answer_gate_chain_sqlite_test.sh`, `answer_gate_focused_sqlite_test.sh`, `answer_gate_chain_pg_test.sh`, `answer_gate_skippath_pg_test.sh` (superseded), `answer_gate_skippath_pause_root_pg_test.sh`, `answer_gate_skippath_deferred_recovery_pg_test.sh`, `answer_gate_fail_on_base_test.sh`, `answer_gate_e2e_dismiss_adjudication_test.sh`, + in-repo `test/packs/concurrency_atomic_unit_test.sh`.

---

### Overall Status
- Chain suite: ✅ PASS (SQLite 10/10; PG 13/13)
- Focused set: ✅ PASS (160 pinned; 5 failures all known pre-existing)
- PG-side CAS suites: ✅ PASS (21/21 + 28/28; zero dialect findings)
- Fail-on-base: ✅ PROVEN (assertion-class at both defect sites)
- Pre-existing adjudication: ✅ identical base ≡ branch (+ node-identity correction)
- Endpoint behaviors: ✅ 3/3 (TestClient-on-PG mode, justified)
- ensure.md Core: ✅ 4/4
- **PASS FOR MERGE** — with §4 ledger correction and §6 wrinkle documented for backlog #3.
