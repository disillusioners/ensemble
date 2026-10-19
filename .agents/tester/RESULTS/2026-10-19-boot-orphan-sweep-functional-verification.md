# Functional Verification — boot-time orphan sweep fix (boot-orphan-sweep)

- **Date**: 2026-10-19
- **Verdict**: 🟢 **PASS-WITH-NOTES — every commissioned check green; notes are expectation-deltas and harness fidelity limits, none blocking**
- **Worktree**: `/home/nea/ensemble-src-wt-boot-orphan-sweep` (branch `feature/boot-orphan-sweep`)
- **HEAD**: `12f298ba238b78bdec9af177cae98d1176d6ee63` | **Parent base**: `333b2c225` (3 commits, 4 files, migration delta EMPTY, rollback-safe additive-only)
- **Workers**: 4 dispatches, 3 instances (`4465e0a6` corpus+adjacent · `78198b0c` restart-sim · `e1b4be54` concurrency). 0 re-dispatches, 0 timeouts, no `TESTER_CANT_OPTIMIZE_TEST_PACK`.
- **Headline**: touched-area corpus **110/110 PASS** (claimed 32/23/14/26=95 **CONFIRMED exactly**) + orphan/restart-adjacent **67/67 PASS** + concurrency pack **99P/0F/74S baseline-exact** + restart-sim **9/9 scenario checks PASS** on real PG `ensemble_dev`.

---

## Gate 1 — touched-area corpus (worker `4465e0a6`, skill test-pack-execution) — ✅ PASS

One `timeout 300 pytest <file> -q --tb=short -p no:cacheprovider` per file, worktree cwd, fence `127.0.0.1:5432/ensemble_dev` asserted (ambient `10.44.0.2/ensemble_prod` poison present and overridden).

| file | P | F | E | S | runtime |
|---|---|---|---|---|---|
| tests/unit/services/test_boot_orphan_sweep.py | 32 | 0 | 0 | 0 | 47.2s |
| tests/unit/services/test_auto_continue_boot_pass.py | 23 | 0 | 0 | 0 | 5.1s |
| tests/unit/services/test_auto_continue_interleaving.py | 14 | 0 | 0 | 0 | 12.5s |
| tests/unit/repositories/test_auto_continue_candidates.py | 26 | 0 | 0 | 0 | 17.4s |
| tests/unit/services/test_auto_continue_terminalizer.py *(surplus)* | 7 | 0 | 0 | 0 | 5.8s |
| tests/unit/repositories/test_task_auto_continued_lifecycle.py *(surplus)* | 8 | 0 | 0 | 0 | 9.7s |

**Claimed distribution CONFIRMED exactly: 32/23/14/26 = 95.** Surplus (outside the claim, same corpus family): terminalizer 7 + lifecycle 8 = +15 → full corpus 110/110. `dev.sh` static check PRESENT (`--timeout-graceful-shutdown 10` at `dev.sh:142`). Final `git status --porcelain`: empty.

### Gate 1b — orphan/restart-family adjacency (same worker, follow-up pack) — ✅ PASS

Commission §1 "job-recovery-adjacent if cheap"; the sanctioned discovery pattern returned only already-run files, so the three most on-point orphan/restart suites were run bounded: `test_queued_orphan_reconciler.py` 1P · `tests/job_queue/test_orphan_active_job_recovery.py` 33P · `tests/unit/services/test_post_restart_arm_notify_sweep.py` 33P → **67/67, ~26s wall**. Combined corpus+adjacent picture: **177 tests / 0 failures / 9 files**.

## Gate 2 — REAL restart-simulation (worker `78198b0c`, skill integration-test) — ✅ PASS 9/9

Harness: 2 throwaway packs in `/tmp/orphan-sim.<rand>/` (removed after), outer `timeout 280` + inner `signal.alarm(240)`; wall 27.7s + 35.4s. Seam driven exactly as documented: `set_boot_epoch(<fresh naive-UTC now>)` → `await sweep_orphaned_live_turns(manager, <same epoch>)`. All rows namespaced `orphansim-`; pre-flight `0` rows; post-run fresh-process scan over every public table with instance_id/thread_id: **LEFTOVER ROWS: NONE**; `instances` status histogram pre=post (`idle 21 / completed 12 / terminated 1`); `job_queue_items` 1142 unchanged; scratch dir removed; no stray processes.

| Scenario | Verdict | Evidence |
|---|---|---|
| (a) orphan rescue | ✅ PASS | `candidates=1 scheduled=1`; astream fired exactly 1× for `orphansim-a-65ad1eaf`; ckpt 15:43:37→15:53:51 (≥ epoch 15:53:37); JobItem `active→done`; instance `running→completed` |
| (b) current-gen safety | ✅ PASS | `candidates=1 scheduled=0 skipped_resume_refused=1`; ZERO astream calls; per-instance line: `SKIPPED instance=orphansim-b: checkpoint last-write (…15:53:52.541846) >= boot_epoch (…15:52:52.546435) — natural immunity / pre-existing resume` |
| (c1) idempotency | ✅ PASS | 2nd sweep same epoch: `scheduled=0`, astream total still 1, resume-schedule lines still 1; `candidates=0` (post-rescue shape no longer matches) |
| (c2) multi-restart | ✅ PASS | new later epoch 15:54:40 + new orphan → `candidates=1 scheduled=1`, astream 1× for `orphansim-c2-8db27f74`, instance→completed, job→done |
| (d) latest-task-COMPLETED | ✅ PASS | not swept; astream 0; task row intact; instance stays running |
| (d) latest-task-CANCELLED | ✅ PASS | not swept; astream 0 |
| (d) latest-task-FAILED | ✅ PASS | **IS swept** (crash rescue): `scheduled=1`, astream 1× |
| (d) idle status | ✅ PASS | not swept; stays idle |
| (d) WAITING_CHILDREN | ✅ PASS | not swept; stays waiting_children |
| (e) observability | ✅ PASS | summary line emitted EVERY run incl. `candidates=0` (4 lines sim1, 2 sim2) |

Verbatim summary line (representative, scenario a):
```
2026-10-19 15:53:38,033 INFO daemon.services.auto_continue_boot_pass: [BOOT_CONTINUE] orphan-sweep summary: candidates=1 scheduled=1 skipped_no_checkpoint=0 skipped_resume_refused=0 skipped_kill_switch=0 skipped_no_boot_epoch=0 errors=0 duration_s=0.034 boot_epoch=2026-10-19T15:53:37.970400
```
Resume-route evidence: `[RESUME] route_outcome=boot_continue_orphan_sweep` for (a)/(c2)/(d-failed) only. Stub dispatch log proves real dispatch fired (thread_id + ts) and stayed silent for (b).

**Real vs stubbed (disclosure)** — REAL: `InstanceManager.__init__` (real PG engine), `EnsembleConfig.load_or_create`, `get_checkpointer` (real AsyncPostgresSaver + asyncpg pool), `TaskRepository.find_orphaned_live_turns` (real selector), `sweep_orphaned_live_turns`, `_schedule_explicit_handle_resume` + `_resume_processing_background` (real resume primitive), real graph build, real `InstanceMessagingService._process_message_with_tracking` turn, `JobQueueService`/`JobFeedbackObserver`/`DependencyBus`/`JobLockManager`. STUBBED: (1) `CompiledStateGraph.astream` class-level — records invocations AND performs the faithful side effect (new checkpoint write via real AsyncPostgresSaver) then yields empty; (2) `PoolOrchestrator.setup` partial (qpk Gate-2 precedent) — real TaskRepository wiring, skips worker-pool threads/StaleTaskRecovery to protect shared dev DB; (3) `config.mcp_pool.enabled=False` (MCP warm-up pool only; DB bootstrap still real). No HTTP boot; :9797/:8088 never bound.

## Gate 3 — concurrency pack (worker `e1b4be54`) — ✅ PASS

`timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` @ worktree HEAD: **99P/0F/74S in 82.23s — baseline-exact, zero deviation**. Venv-gate proved `daemon` resolved inside the worktree (right branch under test).

## ensure.md scope (blast-radius: isolated fix verification)

- **Core criticals 4/4 PASS**: #1 scoped packs all PASS (corpus+adjacent+sim) · #2/#3 `concurrency_atomic_unit_test` PASS · #4 dev.sh grep PRESENT (`:142`).
- **Release Gate NOT warranted** (no architecture/release change — verification of an isolated boot-pass fix). No contradictions, no improvement notices. Quarantine: no quarantined tests in scope.

---

## NOTES to the caller (all non-blocking; read before merge)

1. **`route_outcome` actual value is `boot_continue_orphan_sweep`**, not the literal `boot_continue` paraphrased in the commission — behavioral proof (exactly 1 schedule → real resume chain) is unaffected; the 32-test unit corpus pins the real value.
2. **Scenario (a) "task-row creation" expectation NOT met — by design, not defect.** Measured `task_rows=[]` after (a)/(c2); the sweep's premise is a task-less orphan and the resume lane (`_resume_processing_background` → `_process_message_with_tracking(is_retry=True)`) drives the graph directly without minting a Task (Task axis = worker-pool claim path). Dispatch proof rests on: real `_schedule_explicit_handle_resume` chain + astream invocation + checkpoint advance + JobItem `active→done` + instance `running→completed` (design-doc D18 success-branch terminalizer).
3. **Generation barrier enforced at the resume layer, not the SQL selector.** Scenario (b) shows `candidates=1 scheduled=0 skipped_resume_refused=1` — current-gen turns ARE selected as candidates, then refused downstream with an attributable per-instance line. `candidates` is a pre-barrier count; consumers of the summary line should not read it as "orphans found".
4. **Known warnings observed/not observed**: W-A (`skipped_resume_refused` 3-way catch-all) confirmed LIVE — scenario (b)'s natural-immunity skip landed in that counter, disambiguated only by the per-instance log line. W-B (MAX(Task.id) latest-axis auxiliary contamination) NOT exercised — every (d) instance had exactly one task row; the masked-FAILED-latest risk remains open pending the queued rider patch. W-C covered from both sides ((a) wiped-row case swept; (d-completed)/(d-cancelled) row-present B1 exclusions held).
5. **Harness fidelity limits**: no real model turn (astream stub; empty-stream completion), seeded agent = `watcher` (registry-lightest, 0 tools — same sweep/resume logic, tool-light graph), boot call site `daemon/api.py:2371` itself not executed (in-process seam per commission §2; boot ordering pinned structurally elsewhere).

## Cleanup confirmations

Worktree `git status --porcelain`: **empty** (workers 1/2/3 all verified; artifact attribution mtime-windowed against the concurrent lanes). Dev DB: zero `orphansim-%` rows post-run (fresh-process whole-table scan), status histograms unchanged. Scratch dirs removed. Live install `/home/nea/agents-ensemble`, `ensemble_prod`, daemon `:9797` (PID 131667), port `:8088`: untouched throughout.
