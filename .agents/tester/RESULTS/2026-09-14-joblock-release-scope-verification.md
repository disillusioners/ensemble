# Test Report: W1 job-scoped lock release (commits `be349e6b` + `e9fe08c5`)

Date: 2026-09-14/15
Branch: `feature/fix-joblock-release-scope` @ `e9fe08c5` (base `b5100215` = joblock-leak merge incl. adopted tester files)
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-question-resume-stuck`
Worker instances: K1 `24e03588` · K2 `84db9240` · K3 prime `e00bcd1e` · K4 `f9e6b9e9` · K5 `45bd29d3` · K6 PG `00c8da36` · K7a-d `c8cc2198`/`0f945be6`/`234718eb`/`c5df8c54` · K9 `225dcfcf` · K8a-d `147922d0`/`b889d378`/`8b19593a`/`69a473d6` · adjudication `06207263` · cleanup `632ce4d3`
Mode: verification-only — zero commits by tester workers; 1 disclosed untracked test addition; 1 foreign uncommitted doc-only edit found at cleanup (disclosed §8).

## VERDICT: ✅ SHIP WITH CONDITIONS — production fix fully verified (8/8 base proofs, zero regressions, PG green); merge gated on re-contracting 2 stale-contract test files the dev missed (test-only changes, fix shapes documented §6)

### Summary
- **Prime directive: 8/8 pins independently CONFIRMED-FAIL-AT-BASE** (`b5100215`) with the exact over-release shapes — sibling/successor/orphan `assert 0 == 1`; observer `assert 2 == 1` / `assert 2 == 0`. Collection check FIRST passed cleanly (19 collected; R1 lesson satisfied — no rescue variant needed; all 11 daemon imports exist at base).
- **Acceptance bar met at branch:** over-release dead at all 4 sites (8 pins + 5 re-contracted, both-direction assertions via shared helper — no vacuous pass) AND leak stays dead (prior 26/26 + 6/6 green; job_queue family 94/94 in 4.10s).
- **Census branch↔base: byte-identical everywhere except two single-node flips, both adjudicated** — 1 branch-introduced STALE-CONTRACT test (`test_pause_resume_root`, passes at base), 1 known-environmental (`test_filesystem_workdir` under /tmp; prior-mission same-commit proof). Net delta = exactly +8 green new pins.
- **PG: 28/28 green** (25 existing incl. adopted trio; +3 new disclosed W1-PG sibling-survives tests under PROD-shape triggers). No over-release on PG at any repository site.
- **Gates:** constitution drift 24/24 PASS; zero new env flags/knobs (config.py untouched).
- **🔴 The 2 merge conditions:** `tests/test_observer_failed_at_stamp.py` + `tests/unit/test_pause_resume_root.py` each contain ONE test asserting the OLD instance-wide/None-releases doctrine → both fail at branch, passed at base → branch-introduced STALE-CONTRACT TEST ROT (production correct per W1; adjudicated with code quotes). The dev re-contracted the sibling `test_observer_finalize_no_job.py` but missed these two files.

### Scope Decision
One fix family (2 commits, 5 files, +1025/−61: repository.py ×3 sites, job_feedback_observer.py site 4, 2 new + 1 modified test file) in the established worktree. Packs: base proofs (detached worktree, collection-first), job_queue family, observer family (cross-tree 9-file set), PG disposable cluster, 4×2 census slices, gates. NOT run: tests/e2e (no release), observer site-4 on PG (harness weight — residual R1).

---

### 1. Prime directive — base proofs (K3)
- Setup: `/tmp/ens-base-b5100215` detached @ `b5100215`; 3 test files copied (2 new + 1 branch version overwriting base's tracked file); no production code copied.
- **Collection: PASSED cleanly** (19 tests; 5+3+11) — the R1 module-import hazard does NOT apply (static import audit: 11/11 modules exist at base; dynamically verified).
- **8/8 pins CONFIRMED-FAIL-AT-BASE**, exact expected shapes:
  - `test_sibling_lock_survives_inline_writer_finalize` / `..._backstop_reconcile` — `assert 0 == 1` (over-release deleted sibling)
  - `test_successor_lock_survives_late_inline_writer_finalize` / `..._late_backstop_reconcile` — `assert 0 == 1`
  - `test_orphan_reap_releases_only_orphan_lock` — `assert 0 == 1`
  - `test_sibling_lock_survives_observer_finalize` / `test_successor_lock_survives_observer_finalize` — `assert 2 == 1` (locks_released=2)
  - `test_none_job_id_releases_nothing` — `assert 2 == 0` (None-path over-release)
- Re-contracted `test_observer_finalize_no_job.py` at base: **5F (re-contracted) + 6P (untouched)** — categorization exact; the 6 integrity-log tests green at base confirm the fix is surgical.
- Observer 1F (`test_observer_skips_terminated_status`, tests/job_queue/test_job_feedback_observer.py:304): **fails at base with byte-identical error** → pre-existing rot, dev claim CONFIRMED.

### 2. Implementation + coverage (K2)
4 sites verified before/after: repository.py:2322 (inline writer WHERE job_id), :2581 (backstop), :4164 (F5 DELETE WHERE job_id); observer.py:4003 (gated `if job_id is not None`, `released = 0` default). Coverage map a–h FULL: sibling×3 sites, successor×3, F5-own-only, None-releases-nothing (pin + 5 re-contracts), BOTH-direction assertions (`_assert_own_lock_released_and_coinstance_lock_survives` — own released AND sibling survives simultaneously; observer pins assert `locks_released == 1` + both counts). Prior-26 suite safety verified statically (single-job fixtures; no instance-scope reliance). Combined-40 arithmetic exact: 26+6+5+3. Flag census: zero new env reads, config.py untouched. Diff-stat: exactly 5 files, no leftovers. Old-vs-new contract of the 5 re-contracted tests documented (e.g., `locks_released == 2 → 0`, `_count_locks 0 → 2`).

### 3. Branch suites (K4) + observer family (K5)
- `tests/unit/job_queue` 94/94 (4.10s): 26+6 prior green (leak stays dead), 5+3 new pins green, Fix-B trio 54 green.
- Observer family (analysis-verified 9-file cross-tree set; 111 collected / 29 module-skipped): **2F / 80P / 29S in 2.08s.**
  - 1F = the pre-existing `test_observer_skips_terminated_status` (base-identical, §1).
  - **2F (unexpected) = `tests/test_observer_failed_at_stamp.py::TestFailedAtStamp::test_failed_path_stamps_failed_at_and_row_is_retryable`** → adjudicated (§6).
  - Dev's "76P/14S/1F=91" claim **not reproducible** from any derivable set (actuals 111/80/29/2) — report-quality discrepancy.
  - `test_observer_finalize_no_job.py` 11/11 green at branch.

### 4. Census branch ↔ base (K7a-d vs K8a-d)
| Slice | Branch | Base | Verdict |
|---|---|---|---|
| A services | 8F/1671P/40s | 8F/1671P/41s | IDENTICAL |
| B subdirs | 5F/3366P/57s | 5F/3358P/57s | identical failures; +8 = new pins |
| C root½1 | 30F+21E/2963P | 31F+21E/2962P | 30 identical + 1 known-ENVIRONMENTAL (filesystem_workdir; /tmp symlink mechanism proven prior mission same-commit) |
| D root½2 | 15F+2E/3094P | 14F+2E/3095P | 14 identical + 1 = test_pause_resume_root → adjudicated §6 |
| **Total** | **58F+23E/11,094P** | **58F+23E/11,086P** | net +8 passes = exactly the new pins; both flips adjudicated |

All other failures map to the documented pre-existing backlog (phase1 ×7, archive ×5 quarantined, messages.py:249 await-rot ×7, find_near ×13, builtin_mcp/context7/webfetch ×23, agent-drift, release-tag pin, api-size [2400 lines both sides — number drift from intervening commits, red both sides], etc.). Base-side hygiene: tracked overwrite restored via checkout; untracked copies quarantined to /tmp/k3-w1-artifacts (preserved).

### 5. PG dialect (K6)
Disposable PG14 @15432 (initdb -A trust, role ensemble SUPERUSER; torn down; port verified free). Existing 25/25 (trio 2+3+3=8 — dev claim reconciles; orphan_reaper actual 2 — task-note "10" does NOT reconcile; phase2 15). New disclosed `tests/postgres/test_joblock_release_scope_w1_pg.py` (418 ln, PROD-shape trigger DDL): **3/3 green** — sibling survives inline-writer finalize, backstop reconcile (real TaskRepository terminal-Task path), F5-own-only. Combined 6-file 28/28, zero regression. Authoring gotchas documented (FK parents; UNIQUE (project_id, queue_id, lock_slot) → distinct slots). **Observer site-4 on PG = residual** (needs full EventBus+service harness; covered by SQLite suite).

### 6. The 2 merge conditions — adjudicated stale-contract test rot (branch-introduced, production CORRECT)
1. **`tests/test_observer_failed_at_stamp.py::...test_failed_path_stamps_failed_at_and_row_is_retryable`** — PASS at base (isolated base worktree) + FAIL at branch. Root cause: seeds a JobLock with an INDEPENDENT random job_id ≠ the driven finalize's job_id, then asserts instance-wide count==0 (old doctrine). Under W1 the different-job lock correctly SURVIVES. Branch diff for this file = EMPTY (never re-contracted). Fix shape (3 edits): `_seed_lock` accept+propagate `job_id`; pass `job_id=job.job_id` at :231; assertion then holds; optional docstring upgrade. Adjudicated by dedicated worker with code quotes; cleanup confirmed.
2. **`tests/unit/test_pause_resume_root.py::TestPauseResumeRoot::test_pause_then_resume_then_finalize_reaches_completed`** — PASS at base (K8d attribution check) + FAIL at branch. Assertion text asserts the old doctrine VERBATIM ("Step 3 (lock release) must run even with job_id=None; the seeded JobLock must be deleted"). Same class: re-contract like the sibling (seed the lock under the driven job's job_id, or assert survival + sweep backstop).

Both are TEST-ONLY changes; production behavior is prescribed-correct per W1 (sweep ≤90s / F5 reclaim any genuinely-orphaned locks). Until re-contracted, the branch tree carries 2 new red tests → gate the merge on them.

### 7. Gates
Constitution drift (`EXPECTED_BRANCH=feature/fix-joblock-release-scope`): **RESULT: PASS, 24/24** (census anchors hold; W1 release sites don't write admission_state). Flag census: NO-NEW-ENV-READS; config.py diff empty. ensure.md Core: scoped packs PASS (94/94 family); concurrency gate not re-run (lock-release paths unchanged in concurrency surface vs prior mission's green run at b5100215-lineage; note as minor).

### 8. Disclosures
- **Disclosed test addition (UNCOMMITTED, kept):** `tests/postgres/test_joblock_release_scope_w1_pg.py` (418 ln, 3 tests) — leader/dev to adopt or discard.
- **Foreign uncommitted edit found at cleanup (NOT from tester workers):** ` M daemon/services/job_feedback_observer.py` +20/−12, comment/docstring-only (3 hunks ~:218/:740/:884), rewording the job_id=None path docs to match the COMMITTED W1 contract — reads as a post-commit doc-truth touch-up left uncommitted by a concurrent session. Doc-only → zero effect on all verification results (ran against HEAD e9fe08c5). **Leader decision: commit or discard.**
- Preserved artifacts: `/tmp/k3-w1-artifacts/` (2 quarantined base-copy files).

### 9. Residuals / risks
- **R0 (🟠 merge gate):** the 2 stale-contract tests (§6) — must re-contract before merge; fix shapes documented.
- R1 (🟢): observer site-4 on PG unexercised (harness weight; SQLite-covered).
- R2 (🟢): dev's observer-family count claim (76/14/1) unreproducible — report quality only.
- R3 (🟢): task-note "10 orphan-reaper PG" vs actual 2 — claim-number drift, actuals enumerated.
- R4 (🟢 pre-existing): the 58-family backlog unchanged; environmental filesystem_workdir test-bug (location-dependent).
- R5 (🟢): foreign doc-only edit pending owner decision (§8).

### 10. Worktree end-state
`/tmp/ens-base-b5100215` removed (worktree list clean of tmp entries); feature worktree @ `e9fe08c5`: tracked diff = the one foreign doc-only edit; staged empty; untracked = the 1 disclosed PG test file; ports 15432/8088 verified free; artifacts preserved.

### Documentation Updated
- [x] RESULTS/2026-09-14-joblock-release-scope-verification.md — this report
- [ ] PACKS.md/QUARANTINE.md — no changes (no new flaky candidates; no new pack scripts)

### Overall Status
- Base proofs (prime): ✅ 8/8 CONFIRMED · Acceptance (new+prior): ✅ 94/94 · Observer family: ✅ with 2 adjudicated findings · Census: ✅ byte-identical (2 flips adjudicated) · PG: ✅ 28/28 · Gates: ✅
- **Testing Complete: ✅ PRODUCTION VERIFIED — merge conditional on re-contracting the 2 stale tests (§6) and owner decisions on the 2 disclosures (§8)**
