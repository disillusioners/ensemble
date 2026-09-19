# Merge Gate: chat-source-worker-lane — VERDICT: **PASS** (after re-verification @ 6f624c2c; initial verdict FAIL @ d016aeef overturned by test-only fix commit)

**Date:** 2026-09-19
**Branch:** `feature/chat-source-worker-lane` @ `d016aeef` (tree FROZEN, verified)
**Base (merge-base with `latest`):** `c8855e8a` (v0.13.4 bump)
**Method:** 16 workers (1 discovery, 13 census packs, PG harness, headline 3×, non-inheritance 2×, mock audit, boot/statics, collection count, P-12 sub-slices, base A/B adjudication). All runs `uv run python -m pytest` only, dual-layer timeouts, READ-ONLY (0 fixes, 0 production changes, 0 commits on the frozen tree).

---

## Verdict

**❌ FAIL — DO NOT MERGE YET.** 16 branch-introduced red node-ids (base-green → HEAD-red, proven by detached-worktree A/B at `c8855e8a`). Everything the USER asked the feature to DO is verified green (headline, non-inheritance, fail-open, PG claim seam, boot observability, mock quality); what fails is two stale-contract test classes the branch did not re-contract, plus one census-methodology note. Per the frozen-tree rule: NOT fixed here; developer gets the list.

### Branch-introduced reds (16) — the gate blockers

**Class A — `_notify_all_pools` stale-holder rot (6 nodes).** The branch's Phase-2 wake fan-out (`manager._notify_all_pools`, manager.py:6805-6838) replaced direct wake calls in reconcile/create paths; six test holder stubs were never widened. Base: 14/14 PASS across both files. HEAD: 6 FAIL with `AttributeError: '<Holder>' object has no attribute '_notify_all_pools'`. Same rot class as the branch's own `42ed8f7f` fix (which fenced only worker_notification + message_queue_redesign — these two files sat outside the fence).
- `tests/unit/test_reconciler_wedge_fix.py::TestSubshapeCCarrierRevivalSync::test_revives_carrier_when_alive_parent_no_live_carrier`
- `tests/unit/test_reconciler_wedge_fix.py::TestSubshapeCCarrierRevivalAsync::test_async_revives_carrier_when_alive_parent_no_live_carrier`
- `tests/unit/test_task_only_create_notify_work.py::TestTaskOnlyCreateNotifyWorkSync::test_notify_work_called_after_commit`
- `tests/unit/test_task_only_create_notify_work.py::TestTaskOnlyCreateNotifyWorkAsync::test_notify_work_called_after_commit_async`
- `tests/unit/test_task_only_create_notify_work.py::TestMessageOnlyRecreateNotifyWorkSync::test_notify_work_called_after_commit_sync`
- `tests/unit/test_task_only_create_notify_work.py::TestMessageOnlyRecreateNotifyWorkAsync::test_notify_work_called_after_commit_async`
Fix shape (developer): add `_notify_all_pools` to the SyncHolder/AsyncHolder/B5/W3 stubs (mirroring the production fan-out contract), same as 42ed8f7f did for claim_task mocks.

**Class B — source-API contract reds in `tests/test_api.py` (10 nodes).** Base `test_api.py` = 45P/2F (exactly the 2 known await-rot reds, byte-identical to the 2026-09-18 source-config gate). HEAD = +10 new: the branch's registration-time `source_id` validator (Phase 1, `1def6776`/`697cbcfb`) rejects the legacy fixtures' source payloads → 422 on create, then cascading 404s on get/update/delete/list/start/stop/webhook.
- `test_create_source_success` (422 vs 201), `test_create_source_duplicate` (422 vs 409), `test_get_source_success`, `test_update_source_success`, `test_delete_source_success`, `test_delete_source_cascades_to_mappings`, `test_list_mappings_empty`, `test_start_source_no_registry`, `test_stop_source_no_registry`, `test_webhook_registry_not_available` (404 vs 200)
Fix shape (developer): re-contract the 10 fixtures' source payloads to validator-conforming ids (validator is intended behavior per plan D10.1), or assert the new 422 contract explicitly where the fixture intends a rejection.

### Non-blocking re-classifications (documented, NOT gate blockers)
- `tests/migration/test_jsonb_migration.py` ×3 — census P-8 showed "worker crashed" under xdist; **green solo at BOTH base and HEAD** (adjudicator re-verified at both trees). Classification: xdist-worker-crash artifact via the pre-existing fresh-SQLite migration trap; not branch code. Pack-hygiene follow-up.
- 3 "flake suspects" (`atomic_dequeue::test_dequeue_with_instance_filter_under_concurrency`, `atomic_status_transitions::test_retry_concurrent_double_call_only_one_increments`, `skill_evolution_service::test_ab_resolution_force_resolve`) — **3/3 PASS solo at HEAD**; their census reds were load-induced (see Lessons). QUARANTINE atomic_dequeue row remains valid as a load-context flake (same profile as documented).
- P-12's 15 reds — classified by documented-family signature match (httpx env-class ×9, admission-mirror `active≠done` ×5 incl. quarantined `complete_cancel_route_through_transitions` ×4, live-daemon collection ×1, documented misc ×5); pack red count DROPPED vs 2026-09-03 baseline (24F→15F). Per-node base A/B not re-run for P-12 (verdict already determined by Class A/B above; families match QUARANTINE/baseline).

---

## 1. Census (full regression sweep, frozen tree)

| Partition | Scope | Result | Runtime | Reds → adjudication |
|---|---|---|---|---|
| P-1 regression_unit_tools | tests/unit/tools/ | 2691P/0F/6S | 36.7s | 0 |
| P-2 regression_unit_services | tests/unit/services/ | 1808P/8F | 20.1s | 8 → ALL pre-existing (proxy_phase1 ×7 waived-list-confirmed + b1 env-defect) |
| P-3 regression_unit_smaller_subdirs_routers | unit/{graph,job_queue,job_state,rag,repositories,routers} | 782P/0F | 20.1s | 0 |
| P-4 regression_unit_loose_a_d | tests/unit/test_[a-d]* | 1705P/10F/21E/2S | 25.9s | 31 → all pre-existing (coder_agent, coder_dev_migration ×5, api_size, devops ×3, builtin_mcp ×17E, context7 ×4E) |
| P-5 regression_unit_loose_e_l | tests/unit/test_[e-l]* | 1427P/22F | 77.0s | 22 → all pre-existing (find_near ×13, gaia ×3, llm coding2 ×2, job_processor ×4); hide_kb ×5 HEALED |
| P-6 regression_unit_loose_m_r | tests/unit/test_[m-r]* | 2094P/12F/40S | 81.7s | 12 → 10 pre-existing + **2 BRANCH-INTRODUCED** (`_notify_all_pools`) |
| P-7 regression_unit_loose_s_z | tests/unit/test_[s-z]* | 1494P/9F/11S/2E | 59.9s | 11 → 7 pre-existing + webfetch ×2E pre-existing + **4 BRANCH-INTRODUCED** (`_notify_all_pools`) |
| P-8 regression_top_level_a_h | tests/test_[a-h]* + api/manager/property/migration/static/performance/lint | 1048P/32F/54S | 209.7s | 32 → 20 pre-existing + jsonb ×3 (xdist artifact) + **10 BRANCH-INTRODUCED** (test_api.py source surface) |
| P-9 regression_top_level_i_q | tests/test_[i-q]* + services/repositories | 2419P/59F/73S | 97.5s | 59 → ALL pre-existing (injection_api ×25, memory_integration ×10, SQLite trap ×18+1, charter-row ×4, misc) |
| P-10 regression_top_level_r_z_misc | tests/test_[r-z]* + message_queue_redesign/tools | 2275P/15F/34S/5xf | 83.0s | 15 → 14 pre-existing/trap + 1 load-flake (atomic_status, 3/3 solo PASS); **worker_notification pair GREEN** (rot-fix 42ed8f7f holds); message_queue_redesign re-contract d016aeef holds |
| P-11 regression_job_queue | tests/job_queue/ | 1723P/7F/38S | 36.9s | 7 → node-for-node identical to documented 2026-09-10 FAIL-by-quarantine set |
| P-12 (sub-slices A+B) | integration/opencode/e2e | 1204P/15F/7E+1 coll/4S | 283.4s + 17.3s | pack BREACHES 300s solo (timed out 2×; executed as 2 ad-hoc slices mirroring pack flags) — see Lessons |
| concurrency_atomic (ensure.md Core #2/#3) | canonical 13-file suite | 98P/0F/74S | 19.5s | 0 — exact count-parity with baseline |
| chat_source integration family (ad-hoc) | 10 new files, 49 tests | 49P/0F | 32.8s | 0 — the ONLY census execution of the feature suite (P-12 addopts deselect them) |
| PG claim-lane (ad-hoc, disposable PG14 :15440) | test_chat_source_claim_lane_pg.py | 9P/0F | 1.2s | 0 |

**Census totals:** ~20,826 passed / 189 failed / 30 node-errors + 1 collection file / 139 skipped / 5 xfailed across ~21,200 executed. **Branch-introduced: 16. Pre-existing/env/artifact: 173 + P-12's 15 family-matched.**

**Coverage note (census blind spot closed):** top-level `tests/` was covered by P-8/P-9/P-10 including the two earlier rot classes (`worker_notification*`, `message_queue_redesign` mocks) — both GREEN at HEAD.

## 2. PG harness — PASS 9/9
Disposable PG 14.22 (Homebrew) on 127.0.0.1:15440, role/db created per conftest contract, `PG_TEST_{HOST,PORT,DB,USER,PASSWORD}` exported with invocation-time proof; injected prod `POSTGRES_*` scrubbed (the known silent-fallback trap was live and neutralized). Teardown verified (port freed). Green: strict two-way predicate ×4, all-three-prefixes routing, uppercase-`Telegram:` NOT chat, fail-open flag=false → default claims chat row, constants/helper pins.

## 3. Headline scenario — PASS 3/3 (deterministic)
`tests/integration/test_chat_source_saturation_isolation.py` (2 tests) × 3 sequential runs, quiet machine: 3/3 PASS (~4.0s each). Internal bound is **≤3.0s from enqueue** (line 209 — tighter than the plan's 3.5s; meets the user's ≤3s exactly). **A2.2 notify-not-poll proven:** two-read DELTA of `workers_woken_by_timeout` over [enqueued_at, claimed_at] asserted `== 0` (lines 176-223) and passing; no pre-test zeroing (autouse fixture touches only the B1 module flag, never `_stats`). The chat row was delivered by the notify fan-out, not the 3s poll fallback. (Tests are assertion-based; no numeric latency prints on the green path — the bound is the asserted invariant.)

## 4. Non-inheritance e2e — PASS 2/2 (deterministic, <1s each)
`test_chat_source_non_inheritance_e2e.py::test_chat_worker_then_child_routes_to_default_lane`: parent row `telegram:alice:1` → claimed by `chat-worker-*` (assert line 265); child enqueued with server-mirrored stamp `source="agent:stub_caller"` (mirrors `job_queue.py:712-714`) → claimed by `worker-*` DEFAULT lane (assert line 291); distinct worker_ids (line 299). No lane inheritance. Requirement #2 proven at the e2e seam.

## 5. Mock-quality audit (TrueAuto) — CLEAN, no blockers
- Real contracts extracted: `TaskProcessor.claim_task(self, worker_id, lane: Literal["default","chat"]="default")` (task_processor.py:1268-1292) → `TaskRepository.claim_pending_task(worker_id, lane="default")` (repository.py:1570-1689, ValueError on unknown lane at :1685); `WorkerPool(worker_id_prefix="worker-", lane="default")` (worker_pool.py:1207-1228), worker id = `f"{prefix}{i}"` (:1403), `Worker.run` threads `lane=self._lane` (:302-304); chat pool constructed `worker_id_prefix="chat-worker-", lane="chat"` (manager.py:6768-6785).
- All 8 claim-mock sites re-contract the lane-aware signature (worker_notification ×3 classes, edge_cases ×2, message_queue_redesign ×2).
- **Tautology-free lane proof:** `test_worker_pool_prefix.py::test_chat_pool_workers_claim_with_lane_chat` (non-default lane through real `Worker.run` — regression would flip the assertion) + `test_claim_task_forwards_lane_to_repository` (`assert_called_once_with(..., lane="chat")` at the processor→repo seam).
- Caveats (non-blocking): default-pool rot-fix assertions are value-tautological in isolation (default==default) — compensated by the chat-lane tests + integration lineage; `message_queue_redesign/conftest.py:111` records no lane value (hardening follow-up).
- Edge cases pinned: unknown lane → `ValueError` (production :1685 + `pytest.raises` pin); case-variant `Telegram:` → default-lane routing pinned at **5 seams** (helper, validator, SQLite repo, PG repo, parametrized) with the operator follow-up documented inline at `sources.py:158-166` (NAMED OPERATOR FOLLOW-UP block).

## 6. Boot observability — PASS
Production line `daemon/manager.py:6800-6803`: `ChatSourceWorkerPool started: workers={CHAT_WORKER_POOL_SIZE}, prefixes={','.join(CHAT_SOURCE_PREFIXES)}` → renders `workers=2, prefixes=telegram:,slack:,discord:`. Dedicated test file 4/4 PASS via **in-process caplog capture** (docstring forbids filesystem log parsing; assertions at lines 78/98/120/147).

## 7. worker_notification collection count — settled: **37**
`test_worker_notification.py` = 14 + `test_worker_notification_edge_cases.py` = 23 → pair total **37** (exact arithmetic + combined collect proof). **64 matches no current selection** (single-file max 23; pair 37) — stale/wider-scope reference from an earlier ledger. `42ed8f7f` was mock-only (155+/22− across the two files, zero test-count delta). Both files PASS live in census P-10.

## 8. ensure.md status (Release-Gate scope: cross-module worker-pool change ⇒ full census warranted)
- Core #1 (no regressions in changed packs): **FAIL** — 16 branch-introduced reds (above).
- Core #2/#3 (deadlock/concurrency; no sync DB on loop): **PASS** — concurrency_atomic 98P/0F, count-parity exact.
- Core #4 (dev.sh `--timeout-graceful-shutdown 10`): **PASS** (dev.sh:102).
- Important #1 (await callers of converted async fns): **PASS** — all 8 call sites awaited.
- Important #2 (parent→child→complete no blocking): **PASS** (concurrency pack + headline/non-inheritance e2e).
- Release Gate E2E live-LLM scenarios (daemon + real LLM): NOT RUN — outside the task's test plan; requires `./dev.sh` daemon + real API spend. Surface for a separate release pass if desired.

## 9. Scope decision
Full census warranted and run: 18 production files touched (manager, worker_pool, task_processor, task repository, api, routers, 8 services), worker-pool architecture change. No scope reduction applied.

## 10. Operator/developer action list
1. 🔴 **Fix 16 branch-introduced reds** (Class A: 6 `_notify_all_pools` holder stubs; Class B: 10 `test_api.py` source fixtures vs new validator) — then re-run partitions P-6, P-7, P-8 (or just the three files) + this gate's adjudication seam.
2. 🟠 **P-12 pack maintenance:** `regression_integration_opencode_e2e_test.sh` now breaches the 300s cap solo (2× timeout, ~93% at cap). Split/re-register post-merge (test-code architecture, tester-owned follow-up).
3. 🟠 **Mock hardening follow-up:** record `claim_lanes` in `tests/message_queue_redesign/conftest.py:111` so ~12 downstream files pin the lane value.
4. 🟢 Disclose: 3 untracked foreign artifacts in `test/packs/` (`fe_edit_agent_e2e_test.sh`, `fe_edit_agent_e2e_test/`, `fe_unit_full_test.sh`) — not in branch diff; left untouched per shared-worktree discipline.
5. 🟢 Orphan `__pycache__/test_chat_source_phase3_smoke*.pyc` (removed xfail file, commit 03300365) — cosmetic.
6. 🟢 64-vs-37 discrepancy closed (37 is truth); QUARANTINE atomic_dequeue row unchanged (load-context flake profile re-confirmed).

## Worker instances
discovery e5551cf7 · P-1 59ec194d · P-2 50926a6f · P-3 41b7f3cc · P-4 6d451231 · P-5 0a7647bd · P-6 406151d8 · conc 71ddadec · PG e3ca2623 · P-7 217cdef1 · P-8 64bb313d · P-9 78e87a37 · P-10 6136a974 · P-11 26d94282 · P-12 87d24c98 (+slices 807c0be8) · chatint 58e9f89c · audit eae54d9a · boot 7d2bf4cb · count fde259ed · headline 39c0ac03 · noninherit 8b2f2cfd · adjud 4c48cc58

**Zero code changes, zero commits — frozen tree honored throughout (final `git status` delta: pre-existing `.agents/` artifacts only).**

---

## ADDENDUM (2026-09-19, later) — Re-verification @ `6f624c2c` → **FINAL VERDICT: ✅ PASS — CLEARED FOR MERGE**

The 16 branch-introduced reds were fixed in test-only commit `6f624c2c` ("close stale-contract rot classes repo-wide — _notify_all_pools holders (A) + validator-conform source fixtures (B)", exactly 3 test files, +91/−25, zero `daemon/` changes, verified by `git show --stat`; sits directly atop `d016aeef`). Targeted re-verification (3 workers, read-only, `uv run python -m pytest` only): **Class A** — `tests/unit/test_task_only_create_notify_work.py` (4/4) + `tests/unit/test_reconciler_wedge_fix.py` (10/10) = **14/14 green in 0.63s**, zero `_notify_all_pools` AttributeErrors; **Class B** — `tests/test_api.py` full file = **45P/2F in 1.23s**, exactly the base-documented pre-existing await-rot pair (`test_send_message_success` + `test_global_exception_handler`, both `TypeError: object Mock/MagicMock can't be used in 'await' expression` at `daemon/routers/messages.py:249` — byte-identical failure mode to base `c8855e8a`; no new shape), all 10 source-API reds green; **collateral spot-runs** — `test_pause_resume_terminate_tree_fix_p1.py` 37/37, `test_resume_router_deferred_recovery.py` 25/25, `tests/unit/routers/test_sources.py` 6/6 (validator coverage intact: 3 reject-path + 2 accept-path + 1 non-chat-type). All census findings from the initial gate stand unchanged (nothing else re-run — the fix touched only the 3 adjudicated files). One cosmetic anomaly flagged for the record: the `6f624c2c` commit body claims `test_sources.py 8/8 PASSED` but the file collects exactly 6 (6/6 green) — a count-claim overstatement in the commit message only, NOT a functional issue (COUNT-CLAIM DECOMPOSITION class); recommend correcting the local-only commit message before merge if convenient. The initial gate's non-blocking follow-ups (P-12 pack split/re-registration post-merge, mq_redesign conftest `claim_lanes` hardening, foreign `test/packs/fe_*` artifacts disclosure) remain open and carry into the merge.
