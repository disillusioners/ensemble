# Independent Tester Validation — langgraph-checkpoint-perf-v2

> Date: 2026-09-04 (UTC) · Tester phase (pre-user-review regression + validation net)
> Branch: `feature/langgraph-checkpoint-perf-v2` · Base: `2f80d45b` (fork point) · HEAD at sweep: `1cbad96d`
> Commits landed this phase (tester): `1f16f651` (revive test), `fdf13d0c` (sweep-found regression fixes), ledger/results commit (this file + QUARANTINE.md)
> DSN discipline: every DSN-resolving command pinned BOTH `POSTGRES_URL` and `POSTGRES_DB` to disposables (`ensemble_cpv2_test*` family). `ensemble_prod`/`ensemble_dev` never written. NOTE: session env ships `POSTGRES_DB=ensemble_prod` — pins are mandatory, not optional.

## Verdict: **SHIP-WITH-NOTES**

All four deliverables complete. 0 unexplained failures. 2 port-caused test regressions found by the full-suite net, root-caused with base worktree evidence, fixed test-side on-branch, 2× green. E2E proves the original pathology gone. One operational note for the user (dev.sh pin-clobber) and one production-adjacent census-data edit to be aware of in `fdf13d0c`.

---

## Deliverable 1 — Full-suite regression (0-delta vs 249 budget)

### Method
Phase0 scope replicated exactly (15,537 tests / 613 files / 613-file universe from `--collect-only` at HEAD), split into 7 contiguous partitions run in parallel, each on its OWN disposable PG (`ensemble_cpv2_test_p1..p7` — eliminates cross-partition DB interference vs the single-process baseline), each dual-layer timeout (280s script-internal + 300s command-level). Same 16 `--deselect` args as phase0. `-o addopts= --tb=no -q -rfE -p no:cacheprovider`. Artifacts: `/tmp/cpv2_sweep/` (manifests, run scripts, out_p*.txt).

### Results

| Partition | Files | Failed | Errors | Passed | Skipped | Desel | Runtime |
|---|---|---|---|---|---|---|---|
| P1 (api/job_queue/manager) | 85 | 17 | 0 | 1689 | 39 | 1 | 51.4s |
| P2 (mqr/migration/perf/property/repo/services/static/test_a–e) | 90 | 12 | 0 | 2155 | 61 | 3 | 244.0s |
| P3 (test_f–s) | 88 | 68 | 12 | 2359 | 73 | 0 | 70.6s |
| P4 (test_s–w/tools/unit-1) | 87 | 19 | 0 | 2298 | 20 | 0 | 80.1s |
| P5 (unit-2) | 88 | 15 | 21 | 1918 | 2 | 1 | 38.9s |
| P6 (unit-3) | 87 | 13 | 0 | 2267 | 18 | 0 | 181.4s |
| P7 (unit-4) | 88 | 54 | 2 | 2357 | 33 | 5 | 56.1s |
| **Union** | **613** | **198** | **35** | **15,043** | **246** | **10** | — |

Phase0 budget: 214F + 35E = 249. Tester union: 198F + 35E = **233**.

### Reconciliation (every delta attributed)

**Errors: 35 ≡ 35, exact.** settings_api ×12 + builtin_mcp ×17 + blueprint-fixture family ×6 (context7 ×4 + webfetch ×2) — identical node sets and signatures.

**Failures: 198 vs 214 = −16, fully explained:**
- −11: row-43 context-flake family passed under partition context (test_main_entry ×3, test_task_reconciliation ×6, test_context_api_latency ×2 — documented "PASS in isolation" at phase0; partitions are closer to isolation).
- −5 node-level shifts: test_job_feedback_observer ×1 (deselect arg now matches at HEAD — class segment `TestObserverSkipsTerminated` exists), test_in_progress_guard ×2 (phase0's extra family members context-dependent), test_skill_evolution_service ×1 (threshold-met node context), test_watchover_edge_cases ×1 (context).
- Large file-level context subset: test_manager.py ×38 + test_persistence.py ×15 (row-18 SQLite-migration-cascade family, order-dependent) PASSED under partition context while their siblings (spawn_limit ×9, progressive_dispatch ×18, memory_integration ×10, migration_api ×1) failed identically to phase0 — solo runs confirm both files pass at HEAD (13P+30S / 23P). Family root documented pre-existing.

**Outside-budget failures at HEAD: 9 → ALL attributed → ALL fixed (see Quick Fixes):**
1. `test_governor_recursion_acceptance_walk` ×8 — base 16/16 PASS → HEAD 8 FAIL. Root: port added `self._manager.message_metadata_repo` consumption at `instance_lifecycle.py:1669` (spawn-time tap wiring); test's `WalkerManager` stub lacked the attribute. **Port-caused, test-side.**
2. `test_constitution_drift::test_known_mint_sites_is_subset_of_source` ×1 — base PASS → HEAD FAIL. Root: 6 stale `uuid.uuid4` mint registrations (instance_messaging.py:702/1596/1600/1604/1608/2241) in `KNOWN_MINT_SITES` — port rewrote those regions; census gate caught the drift (working as designed). **Port-caused, census-data-side.**

**Solo-clean partition-noise (no action):** rag/test_config ×1, frozen_tool_name_discovery ×1, tool_config_validation_boot ×1 (all PASS solo at HEAD), atomic_status_transitions ×2 (3/3 full-file runs clean — partition CPU-contention class, row-43 sibling).

### Quick fixes landed (commit `fdf13d0c`, 2 files, +1/−6)
- `tests/test_governor_recursion_acceptance_walk.py` +1 line: `mgr.message_metadata_repo = None` (optional C2 tap path tolerates None; governor assertions unchanged). 16/16 ×2 green.
- `daemon/job_state/constitution.py` −6 lines: removed exactly the 6 stale `KNOWN_MINT_SITES` entries (static census data only; no behavior change; `instance_messaging.py` untouched). 10/10 ×2 green.
- Post-fix outside-budget count: **0**.

## Deliverable 2 — QUARANTINE.md dispositions (ledger committed separately)

- **NEW row** settings_api ×12: quarantine-with-attribution (base-evidenced ×2: phase0 + tester worktree A/B 2f80d45b≡1cbad96d). Fixture builds own DSN from `PG_TEST_*` → `ensemble_test`; catalog nuance recorded (`public` exists today yet signature identical at both commits — intra-DB cause not fully root-caused; categorically not port-caused).
- **NEW row** builtin_mcp ×17: quarantine-with-attribution (mock_config lacks `slash_commands`; phase0 base-attributed; re-confirmed ×17E at HEAD).
- **NEW row** cold_resume_ttl ×2 (v1 PR1 transfer, D2 deferred): quarantine-with-attribution — pre-existing at base (worktree A/B 2026-09-04), `assert 'pending' == 'cancelled'`, c171a289 semantic-shift family (rows 20/22/23). question_deferred ×1 PASSES at HEAD → no row (documented). pause_race_w7: already covered by M2 family row — no duplicate.
- **Rows 34–37 (_ManagerStub family): DE-QUARANTINED** — stub synced upstream (test_injection_slot.py:79, message-display-latency batch); tester evidence 3× solo clean (25/25 + 3/3) @ 1cbad96d.
- Governor ×8 / constitution ×1: NOT quarantined — branch-caused, fixed on-branch (`fdf13d0c`).

## Deliverable 3 — E2E original-symptom verification (ALL PASS)

Boot: disposable `ensemble_cpv2_test` (drop/recreate) — daemon booted via direct uvicorn (see Note A), readiness 9s, DB landing proven by `pg_stat_activity` (7 conns to cpv2_test, 0 to others; the misleading persistence.py checkpointer log line is the known F-DR1-2 print issue).

| Check | Result | Evidence |
|---|---|---|
| Message history correct | PASS | 3 turns ("ok" ×3), GET `/api/instances/{id}/messages` HTTP 200, 8 messages complete+ordered (synthetic system + system-context + 3×user + 3×assistant) |
| Timestamps side-table-real | PASS | all created_at non-null, UTC wall-clock matching turns, monotonic; message_metadata rows byte-equal to API output |
| Response shape | PASS | list envelope; message keys: message_id/type/role/content/thinking/thinking_extracted/tool_calls/images/created_at/instance_id/is_synthetic |
| **Deep-history anti-scaling** | **PASS** | depth-100: 12.2/9.2/8.3 ms (108 msgs, 80KB); depth-1000 (+999 history checkpoints, 1008 msgs, 345KB): 37.8/25.5/23.2 ms — flat within serialization cost, vs pre-fix **42s @ 206MB** |
| alist never fires | PASS | 13× `[/Messages]` log lines, every one `alist_count=0`; zero exceptions |
| Side table populated | PASS | 7 rows from real turns (populate writes correctly bypass it — raw-saver path), real timestamps, 0 NULLs |
| state.ts fallback | PASS | deleted one metadata row → re-GET HTTP 200, all 1008 messages intact, victim falls back to state-derived timestamp (10:45:40 vs side-table 10:43:10) — graceful, no crash |
| Blob-prune DRY-RUN | PASS | real factory checkpointer, default args: `{'dry_run': True, 'total_deleted': 0, ...}`; checkpoint_blobs 3012 → 3012 unchanged; DESTRUCTIVE never set |
| FE smoke | PASS | `npm start` port 4199 → HTTP 200 in ~6s, Angular shell served; clean teardown |

Teardown verified (own PID only, ports 8079/4199 freed, zero residual DB connections). Artifacts: `/tmp/cpv2_e2e/`.

## Deliverable 4 — Residual #4: send_message-revive test (LANDED)

`tests/integration/test_message_metadata_send_message_revive.py` (637 lines) @ commit **`1f16f651`** (parent `1cbad96d`; explicit-path staging; production untouched):
- `test_send_message_revives_completed_instance_and_read_stays_aget_only`: real AsyncPostgresSaver thread via `graph.ainvoke` → side-table via production `upsert_batch` → pre-revive snapshot → REAL `InstanceMessagingService.enqueue_message` (revive branch `instance_messaging.py:1816-1845`) → asserts COMPLETED→RUNNING in DB, exactly one revive emit, `AsyncMessageResult.status=="queued"` with `job_id==Task.work_id` linkage, Task(PENDING)+MessageQueue(READY), post-revive read byte-identical, alist_count==0 both reads + armed-absence gate. 2/2 passed (2.46s); sibling guard `test_message_metadata_retry_recovery.py` still green (1.67s).

## Notes for the user (non-blocking)

- **Note A (operational):** `./dev.sh` CANNOT carry `POSTGRES_*` DSN pins — dev.sh:58-63 `set -a; source .env` and `.env:57` hardcodes `POSTGRES_DB=ensemble_dev`, clobbering exported pins (split-brain: URL survives, DB part does not). Tester booted the identical uvicorn command directly with both pins (daemon core never loads `.env`). Recommend either fixing dev.sh pin-through or documenting direct-uvicorn for pinned drills. DB-landing must be gated on `pg_stat_activity`, not the persistence.py log line (known F-DR1-2 print).
- **Note B (review awareness):** `fdf13d0c` touches `daemon/job_state/constitution.py` (production-adjacent) — authorized static-census-data-only edit (6 stale entries removed per the gate's own remedy text); no behavior change; flagged here for review visibility.
- **Note C (base-fixture debt, pre-existing):** settings_api ×12 (fixture DSN ignores pins → ensemble_test 3F000) and builtin_mcp ×17 (mock_config lacks slash_commands) remain red on the branch as documented pre-existing failures — both have queued ledger rows with fix directions; neither is port-caused.

## Workers used

discovery `5e8aacde` · sweep p1–p7 `fcb3c34f/b42483b6/453aa96f/11b1459c/6ad7dd45/4e14dafa/97cb7ba7` · adjudicate `d61e76ac` · revive `436d8fb3` (integration-test skill) · e2e `2172a981` · quickfix `3d7fc0af` (quick-fix skill) · commit worker (this ledger)
