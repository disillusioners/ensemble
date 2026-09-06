# Verification Gate — Terminal-Report Wake Fix (ee66f0eb)

Date: 2026-09-06 | Branch: `feature/fix-terminal-report-wake` | Fix commit: `ee66f0eb` (parent `77ce4ae8`)
Gate tip after verification commits: `9b0dab41` (ee66f0eb..9b0dab41 = test/packs/* + ONE amended test file — see §5)
Incident closed against: reviewer child 7807e521 terminal 00:53:48 → parent d77727cf report delivery 01:07:37 (**14m49s FIFO starvation**, 16 partition tasks on WORKER_POOL_SIZE=4).

## VERDICT: ✅ CLOSED — symptom eliminated at the claim-order level, proven by test, no delivery regression. ONE contract amendment (W5 pin) requires leader ratification.

## 1. Fix under test
`daemon/repositories/task/repository.py` — `claim_pending_task` gains a two-tier ranking applied AFTER all eligibility filters (defer/background/queue-awareness/per-instance/pause/cross-system all precede it):
```sql
ORDER BY CASE WHEN task_type = :report_wake_task_type THEN 0 ELSE 1 END,
  created_at ASC LIMIT 1
```
(`:report_wake_task_type` = `TaskType.PROCESS_REPORT.value`, bound at repository.py:1637; ranking at :1608-1611.)
`daemon/services/child_reports.py` — replaces the misleading "bus callback owns completion" log with truthful bus-is-pure-state-machine documentation; parent terminal transition rides natural PROCESS_REPORT delivery.

## 2. Pre-fix failure proof (strongest evidence — cat-b)
Worktree pinned at parent `77ce4ae8` (daemon.__file__ real-path verified inside worktree; macOS /private/tmp resolution handled):
```
FAILED tests/integration/test_report_wake_priority_claim.py::TestReportWakeLane::test_report_task_claims_ahead_of_older_pending_under_saturation
tests/integration/test_report_wake_priority_claim.py:209
E   AssertionError: the PROCESS_REPORT task must claim FIRST despite being the youngest PENDING task — its claim IS the parent wake (7807e521: report starved 14m49s under strict FIFO)
E   assert 5 == 13      # older process_message id=5 won via strict FIFO; report id=13 starved
```
4/5 pass at parent; ONLY the priority-lane assertion fails — the test genuinely encodes the incident mode. At HEAD: 5/5 PASS. Pack: `test/packs/terminal_report_wake_prefix_worktree_test.sh` (commit `bbd1532b`).

## 3. Pack results (all on feature/fix-terminal-report-wake; HEAD gate: ee66f0eb ancestor + only test-infra diffs above it)

| # | Pack | Result | Counts | Runtime |
|---|------|--------|--------|---------|
| 1 | terminal_report_wake_unit_test (NEW) | ✅ PASS | 4/4 | 0.36s |
| 2 | terminal_report_wake_integration_test (NEW) | ✅ PASS | 9/9 (cat-b 5/5 + exactly-once 4/4) | 0.85s |
| 3 | terminal_report_wake_pg_smoke_integration_test (NEW) | ✅ PASS | repository-level verifier on real PG | 2s |
| 4 | has_instance_busy_pins_unit_test (NEW) | ✅ PASS | 16/16 (ground truth 16 — request cited ~21) | 0.39s |
| 5 | claim_guard_locks_unit_test | ✅ PASS | 178/178 | 2.12s |
| 6 | child_reports_unit_test | ✅ PASS | 48/48 | 1.29s |
| 7 | completion_regression_test | ✅ PASS | 96P/37S/1-des (quarantined dependency_bus node correctly deselected) | 1.66s |
| 8 | concurrency_atomic_unit_test (ensure.md Critical) | ✅ PASS | 98P/74S | 7.05s |
| 9 | child_parent_lifecycle_regression_test | ✅ PASS | 220P/19S | 10.15s |
| 10 | wc_wake_d1_w5_pairing_unit_test | ✅ PASS after adjudicated amendment | 57/57 (pre-amendment 56P/1F — see §5) | 0.44s |
| 11 | terminal_report_wake_prefix_worktree_test (NEW) | ✅ PASS (= cat-b FAILS at parent, proof) | parent: 4P/1F(assertion) | 6s test / 1s setup |

**Head totals: 726 passed / 130 skipped / 1 deselected / 0 failed.** Skips are pre-existing by design (feature-flag/quarantine/infra-gated). Zero regressions in delivery, exactly-once, dependency_bus, finalize, wake/resume, work_resolver.

## 4. PG live-path coverage
- Dev verified SQLite only; gate added real-PostgreSQL execution. The 2 new integration files are hard-wired file-backed SQLite (own `engine` fixture, no postgres marker — `-m postgres` cannot select them), so PG validation ran as a repository-level verifier: disposable DB `ensemble_test_wake_ee66f0eb` (create→seed→verify→drop, trap-guarded; HARD GUARDS abort on any `ensemble_prod` mention in DB name or resolved URL).
- Results on real PG: claim#1 = process_report (id=5) ahead of older FIFO backlog ✅; claim#2 = oldest process_message (id=1, 30min) ✅ — CASE-ranking SQL is PG-dialect-correct.
- `ensemble_prod` verified untouched (pg_database read + before/after DB-list snapshot).
- Verifier needed 8 test-infra-only script fixes (commits 4964c105..0e456df7): psycopg3 dialect URL, `task` (singular) table, `Session.exec(params=)` kwarg, `worker_id` required arg, tz-aware datetimes, `-c TimeZone=UTC` (server is UTC+7), separate report instance (per-instance concurrency gate), trap-after-RESULT exit. See LESSONS/2026-09-06-pg-smoke-verifier-dialect-traps.md.

## 5. ⚠️ Contract amendment REQUIRING LEADER RATIFICATION
`tests/unit/services/test_w5_claim_order_wc_wake.py::TestW5TwoTurnClaimOrder::test_user_msg_first_created_claimed_first_report_second_turn` FAILED at HEAD (56/57): the W5 pin asserted symmetric created_at-driven claim order ("not type-biased"); ee66f0eb deliberately makes order type-biased (PROCESS_REPORT outranks FIFO) — that IS the fix. Adjudicated intended-new-behavior; amended minimally (commit `9b0dab41`, 12+/7−, one file): both claims still asserted with identities, order flipped to report-first, comment cites the superseding contract + canonical tests. Mirror variant and all other W5/D1/pairing tests untouched; re-run 57/57 PASS.
**Ratification needed because this changes a pinned WC-wake invariant:** user messages now claim AFTER a pending child-report task across tiers. Delivery remains exactly-once (4/4 barrier-race tests); report volume is bounded by child completions; the 7807e521 incident is precisely the case this trade-off resolves.

## 6. Edge-case matrix (investigation + /tmp scratch, repo untouched)
| Edge | Verdict | Evidence |
|---|---|---|
| Multi PROCESS_REPORT FIFO (tier-0 tiebreak) | 2-report case COVERED in-repo (:252-276); 3+/interleaved VERIFIED-OK by scratch (R1..R4 before M1..M3, oldest-first per tier); identical created_at = implementation-defined rowid order (SQLite) — coverage note, not a defect |
| Pause-gate interaction | COVERED in-repo `test_pause_gate_still_dominates_the_wake_lane` (:278-313) + scratch replication — filters precede ranking; priority re-orders survivors, never promotes ineligible |
| Concurrent claim | COVERED in-repo — `threading.Barrier(2)` racing claimers, exactly-one-winner + both-ran proof (:232-259) |
Report-only observations: identical-timestamp tiebreak unpinned (option: `id ASC` tiebreak later); TERMINATED-parent coverage implicit-only; 2-thread vs 4-worker incident shape is a monotonic generalization.

## 7. ensure.md (Core, blast-radius scoped)
- ✅ Critical: no regressions in changed packs (all 10 HEAD packs PASS)
- ✅ Critical: concurrency/deadlock integrity — concurrency_atomic PASS (incl. thread-identity sync-DB checks)
- ✅ Critical: dev.sh `--timeout-graceful-shutdown 10` FOUND (line 102, static check)
- ℹ️ Important async-await grep items (_get_system_prompt_tokens etc.): OUT OF SCOPE — fix touches no such functions
- ℹ️ Nice-to-have dead-code: child_reports −4 lines are the replaced log text (no dead code)
- Release Gate NOT warranted (scoped 2-source-file fix); no contradictions with ensure.md methods found

## 8. Scope decision
Full suite NOT run — warranted scope: 11 packs across the claim/wake/delivery/completion/concurrency surface + pre-fix worktree proof + PG verifier + edge investigation. 726 green tests cover the entire blast radius of a 2-file production change.

## 9. Commits made by this gate (all test-infra/test-file only)
`7c3ab1ec` (4 pack scripts) · `bbd1532b` (pre-fix worktree pack) · `4964c105`+`419283c0`+`b934a6a8`+`2385144a`+`b7870b66`+`c5e3259c`+`79f19c08`+`0e456df7` (pg-smoke verifier fixes) · `9b0dab41` (W5 pin amendment — RATIFY). Zero source modifications. Production `ensemble_prod` never written.

## 10. Workers (15 dispatches, 13 instances)
infra 231f97fc · prefix f2b74e4e · claim-guard cc8fe302 · child-reports 19844bb8 · completion 81fbb8fa · wc-wake 93652330 (×3: run→blocked-drift-adjudication→amendment) · concurrency 9ee519d5 · lifecycle cb2b3c3e · unit-new bc2ffd9c · int-new 24ea8e26 · pg-smoke 31ca31cc · busy-pins 89e97a20 · edge d5ccd7ad

## Notes / follow-ups (non-blocking)
- PACKS.md wc_wake row was stale (54→57; 3 tests added by 33d65e5a BELOW the pin) — refreshed in this gate block.
- has_instance_busy ground truth is 16 tests, not the ~21 cited in the request.
- The blocked wc-wake dispatch (exact-pin drift vs my own gate commits) was adjudicated in-flight; relaxed gate (ancestor + test/packs-only diff) applied to all subsequent runs.
