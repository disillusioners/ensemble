# Fix B — FULL Regression Gate — `feature/job-task-fix-b` @ `db070275`

Date: 2026-09-02 (UTC) · Base: `4ea6fa37` (latest) · Range: `4ea6fa37..db070275` (8 commits)
Dispatched: 19 worker instances (1 wave-0 env/discovery, 6 acceptance, 8 full-suite partitions, 1 base-worktree [2 phases], 1 mock-audit, 1 fix-b-verification, 1 ensure.md). Repo READ-ONLY throughout — zero commits by gate; base scratch worktree created + removed cleanly (`git worktree list` = main only). Evidence: `/tmp/fixb-gate/` (p1–p8.log, acc-*.log, base-*.log, solo/, fixb-*.log, ensure-concurrency.log).
Note: several worker self-report titles carried mismatched instance names (system labeling); adjudication is by instance-id + content, which matched every dispatch 1:1.

## FINAL VERDICT: ✅ **PASS (merge-ready)** — 0 branch-caused regressions; 6/6 acceptance sets EXACT; full-suite census 241 pre-existing / 0 caused / 18 context-flakes, all base-evidenced.

---

## 1. Acceptance sets (6/6 EXACT — independent re-runs)

| Set | Expected | Actual | Result |
|---|---|---|---|
| `bash test/packs/constitution_drift_test.sh` | 24P, 23 writers, delta 0, SKIP notice | **24 passed in 6.13s**, exit 0; SKIP notice verbatim `RESULT: SKIP (set EXPECTED_BRANCH to enforce branch guard)` | ✅ |
| `tests/unit/job_queue/test_fix_b_inline_mirror_transition.py` | 13 | **13 passed in 1.27s**, exit 0 | ✅ |
| `tests/integration/test_fix_b_inline_mirror_transition_incident_b.py` | 3 | **3 passed in 1.21s**, exit 0, 0 skips/deselects (`--override-ini="addopts=" -m "not postgres"`) | ✅ |
| `tests/job_queue/test_orphan_active_job_recovery.py` | 33+2 | **33 passed in 0.99s**, exit 0, **zero skips both addopts modes** | ✅ (see count note) |
| `tests/unit/job_queue/test_fix_b_legacy_zombie_reap.py` | 28 | **28 passed in 1.32s**, exit 0 (verbose per-test on file) | ✅ |
| `tests/unit/job_queue/test_fix_b_terminal_message_mirror_backstop.py` | 13 | **13 passed in 0.96s**, exit 0 (verbose per-test on file) | ✅ |

**"33+2" count note (resolved):** the file collects/runs **33** at HEAD — 31 pre-existing tests + **2 branch-added PASSING tests** (`TestFixBPatternFMessageSkipForMirrorSliceRetired::test_message_job_is_skipped_with_observable_detail`, `::test_message_skip_does_not_block_task_processing`). Base version of the file runs **31 passed** (branch diff `+244/−1`); zero skip markers anywhere. The "+2" = the two new Fix-B tests, actively passing — not skipped tests.

**Constitution pack census note:** the pack stdout carries no literal census/delta lines (pytest captures them). 23 writers + delta 0 verified via read-only module introspection (`KNOWN_ADMISSION_STATE_WRITERS = 23 writers`, `only_in_source=0, only_in_static=0, equal=True` for writers/creators/mints) and independently by the 10 passing bidirectional drift assertions. 🟢 pack-hygiene follow-up: echo census to stdout.

**Cutover margin confirmations (from reap verbose run):** `test_post_cutover_not_reaped` PASSED, `test_reaps_legacy_message_zombie_with_absent_instance` PASSED, `test_no_candidates_returns_empty` PASSED, `test_only_forward_rows_returns_empty` PASSED, `test_cutover_constant_value` PASSED, plus 5-state live-instance protection sweep [idle/paused/queued/running/waiting_children] and terminal-status match loops — all PASSED. (Class names use `TestReapLegacyMirrorZombies<Aspect>` prefixes; method names match exactly.)

**Backstop state coverage (from verbose run):** widened scan covered for **queued/active/paused in BOTH directions** (`..._reconciles_terminal_task_in_every_pre_terminal_state[state]` + `..._keeps_live_task_in_every_pre_terminal_state[state]`, ×3 each), stale-mirror follow, idempotent rerun, service soft-failure detail, task-type mirror protection.

## 2. FULL-suite baseline at HEAD (p0a protocol, 8 partitions)

Scope: all pytest-collectable tests EXCEPT `tests/e2e` (Release-Gate territory), `tests/postgres` (live-PG provisioning; see §4d), shell packs, FE. P1–P7 repo-default addopts; P8 `--override-ini="addopts=" -m "not postgres"`. All under `timeout 300`; all drift-guarded before/after (HEAD unchanged: `feature/job-task-fix-b` @ `db070275`).

| Partition | Scope | Collected | Passed | Failed | Errors | Skipped | Runtime |
|---|---|---:|---:|---:|---:|---:|---:|
| P1 | tests/unit subdirs except tools (9 dirs, enumerated) | 1,564 | 1,556 | 8 | 0 | 0 | 40.9s |
| P2 | unit/tools + unit/test_[a-k]* | 2,539 | 2,492 | 24 | 21 | 2 | 56.1s |
| P3 | unit/test_[l-m]* | 1,472 | 1,468 | 3 | 0 | 1 | 176.9s |
| P4 | unit/test_[n-z]* | 2,128 | 2,017 | 59 | 2 | 50 | 38.1s |
| P5 | {job_queue,services,message_queue_redesign,migration} | 2,749 | 2,681 | 2 | 0 | 66 | 72.3s |
| P6 | test_[a-j]* + {tools,api,manager,lint,performance,property,static} | 1,769 | 1,675 | 46 | 0 | 48 | 63.3s |
| P7 | test_[k-z]* + {opencode,repositories} | 3,479 | 3,336 | 43 | 0 | 79 (+5xf) | 110.3s |
| P8 | tests/integration (override) | 419 | 353 | 35 | 16 | 1 | 43.0s |
| **TOTAL** | (30 postgres-marked deselected: P7×16 + P8×14) | **16,119** | **15,578** | **220** | **39** | **247** (+5xf) | ~10.5 min summed |

Δ vs p0a baseline 16,058: +61 ≈ **59 Fix-B test additions** (54 unit + 3 incident-B integration + 2 orphan) + 2 incidental. Coverage sentinel (P6): no unassigned collectable top-level entries (helpers/, manual/, mocks/, regression/, *.sh, fixtures — non-collectable).

## 3. Per-failure attribution — every one of the 259 F+E classified

Method: scratch worktree @ `4ea6fa37` (own `uv sync` venv; isolation proven via `daemon.__file__` under worktree; removed cleanly after). All 259 unique HEAD F+E nodes batch-run at base per partition (P8 mode-matched); every pass-at-base candidate solo-budgeted 3× at base AND 3× at HEAD (main repo, read-only).

| Verdict | Count | Detail |
|---|---:|---|
| **PRE-EXISTING at base** | **241** | fail/error at base in batch (P1–P7: 208/208; P8: 33) |
| **🔴 CAUSED** | **0** | none met the bar (HEAD solo 3/3 fail ∧ base solo 3/3 pass ∧ base batch pass) |
| 🟠 Context-flake (pre-existing, order-sensitive) | **17** | 13 vscode routing/security + 3 multi_turn_resume + 1 workspace_sse — PASS solo 3/3 at BOTH commits; fail only in full-partition context (QUARANTINE.md row 2026-09-01, re-verified) |
| 🟠 Isolation-inverse (pre-existing-broken) | **1** | `test_agent_bootstrap_and_hello` — fails solo 3/3 at BOTH commits, passes batched at base (QUARANTINE.md row 2026-09-01, re-verified) |
| Unexplained / other base outcome | **0** | full reconciliation; no base-side skips/xfails among the 259 |

**Formerly-stale p0a blockers now green (leader expectation met):**
- `test_job_processor_admission_starvation.py::…::test_admits_job_for_system_default_when_over_100_other_projects_exist` → **1 passed** (solo, HEAD).
- Old blocker-#1 ID `test_processor_crash_recovery_respawn_warns_on_linkage_violation` **no longer exists** (renamed in the p0a flip-commit `bdfa57d1`); its two live successors `test_processor_crash_recovery_respawn_raises_on_linkage_omission` + `test_assert_linkage_contract_warns_on_mismatch_when_not_enforced` both **green at HEAD**. 🟢 External docs citing the old ID should be corrected.

**Known pre-existing pair still red (documented on latest, in the 241):** `test_non_terminal_checkpoint_writes_replacement` (compaction `__remove_all__` prepend) and `test_router_forwards_queue_id_to_enqueue_message_job` (MagicMock-unawaitable dispatch) — both fail deterministically at base `4ea6fa37` (2 failed in 0.78s batch). Matches the "Pre-Existing Failure Hygiene" blueprint note; NOT branch-caused.

**P8 shift p0a 36F/16E → HEAD 35F/16E — exact arithmetic:** base full-dir re-run at `4ea6fa37` reproduces 36F/16E (partition-equivalence proven). Node diff: **−2 = the two incident-B Fix-B differentials** (failing at base by design, green at HEAD = Fix B working); **+1 = the bootstrap flake**. 36−2+1=35, 16E→16E.

**P5 shift p0a 6F → HEAD 2F:** the 2 residual failures are the documented pre-existing pair; the other 4 p0a-era P5 failures do not reproduce at HEAD (improvement side; base-side non-reproduction consistent).

## 4. Fix-B-specific verification

### 4a. Incident-B end-to-end differential — ✅ REAL
Copied-test worktree runs at base: `test_message_job_done_at_t0_while_instance_waiting_children` and `test_double_on_success_call_is_idempotent` **FAIL at base** with the exact original failure mode (`AssertionError: Inline mirror transition must finalize the JobItem at T0 … assert 'active' == 'done'`); green at HEAD. `test_task_job_unchanged_by_on_success` passes at base **by design** (negative/scope test). Inline file at base: 13/13 fail (`AttributeError: 'JobRepository' object has no attribute 'finalize_mirror_job_at_completion'`); backstop 13/13 fail (missing `reconcile_terminal_message_mirrors`); reap collection-errors (missing `LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO` import). All four differentials genuine.

### 4b. Backstop×reap×f2 ordering — ✅ structurally enforced; 🟠 test gap flagged
- Methods: reap = `JobRepository.reap_legacy_mirror_zombies` (repository.py:2166, reason mint :2395); backstop = `JobRepository.reconcile_terminal_message_mirrors` (:1930). In `reconcile_drift_states`: reap invoked :885-886, backstop :919 — **same cycle, sequential awaits, deterministic reap-first order**.
- **Candidate overlap IS possible** (backstop has NO cutover guard — intentional, comment: "F-1 covers both legacy rows and rows created during/after cutover"; task-terminal is in both accept-sets). A pre-cutover ACTIVE message row with terminal Task + dead instance matches BOTH.
- **Exactly-one-terminal-write** is arbitrated by the twin guarded UPDATEs (reap `WHERE admission_state=='active'` → done/`orphan_retired`; backstop `WHERE admission_state IN (queued,active,paused)` → done/`completed`; loser `rowcount==0` → silent no-op, logged). Reap-first order yields `orphan_retired` for the overlap class — matching the intended reason mapping.
- **Race mechanism test-covered for the inline writer only** (`TestFixBMirrorTransitionConcurrentRaceSafety::test_double_fire_is_one_transition`, two threads, file-backed SQLite, exactly-one-winner + final-row re-read). **No cross-sweep reap×backstop double-fire test exists** (no file references both sweeps). 🟠 Non-blocking follow-up: add a sweep-pair race test mirroring the inline one.
- f2 note: pattern (f2) retained but age-floored (`_F2_COMPLETED_AGE_FLOOR_SECONDS=60`); the f-sweep blanket message-skip is retired to an observable-detail skip (covered by the 2 new orphan-file tests).

### 4c. 'orphan_retired'→'cancelled' canonicalization — ✅ 4/4 paths
- Primary: `daemon/services/work_status.py:118` map entry, consumed by `_derive_legacy_status` (single source).
- Fallbacks all delegate: `jobs_management.py:472`, `jobs_crud.py:150`, `dlq.py:503` (grep-quoted; F16 comments at each site).
- Behavioral coverage: `test_f16_legacy_status.py` **38 passed**; `orphan_retired` key exercised at helper level (`[done-orphan_retired-cancelled]` param). 🟢 Disclosure: router-fallback tests cover other keys behaviorally; the `orphan_retired` key at the 3 fallbacks is **static-only** (delegation pinned by import-check tests); `jobs_management` fallback has no behavioral test in the file. Follow-up: add `orphan_retired` params.

### 4d. Cutover lex-compare boundary — ✅ CONFIRMED
`LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO = "2026-09-02T00:00:00+00:00"` (repository.py:81, pinned str). Candidate predicate `.where(JobItem.created_at < cutover_iso)` (:2261) — strict `<`, lexicographic compare on ISO **str** column (`models.py:212`). Row created **exactly AT cutover is RETAINED** (strict `<` excludes equality). Margin tests solo-confirmed deterministic (0.86s / 0.71s). 🟢 Minor: no `_AT_CUTOVER ==` equality case seeded (semantics unambiguous from operator).

### 4e. PG-side — analytical disclosure (live-PG attempt cleanly blocked)
Live PG up on localhost:5432 (socket+TCP, auth OK), but `tests/postgres/test_orphan_reaper_pg.py` fixture fails at DDL: `psycopg.errors.InvalidSchemaName` in `ensemble_test` (schema/db not provisioned for connecting role; conftest creates tables, not the database). Stopped per read-only mandate — environment-side provisioning required. **New repo methods with ZERO PG tests:** `reap_legacy_mirror_zombies`, `reconcile_terminal_message_mirrors` (guarded UPDATE + lex-compare cutover are SQLite-proven only). Reviewer precedent (analytical + §8.1 note) already accepted; this attempt was due diligence.

## 5. Mock-quality audit (TrueAuto rule) — ✅ PASS

- **Silent-no-op probe: ALL mechanisms CAUGHT.** Production writers gate return records on `rowcount` AND re-read post-commit — a no-op leaks as None/empty/stale values. Independent fresh-session read-backs: reap ×4 tripwires (`len(reaped)==1`, reason snapshot, `_read_job` re-read, idempotent-second-run-empty); backstop per-state ×3 (docstring pins the exact historical rowcount==0 bug class); inline both no-op shapes (None-return + stale-re-read) incl. end-to-end `post_job.admission_state == DONE` via `_read_job`; f-sweep skip both directions.
- **Zero** `terminal_reason` assertions sourced from mock kwargs; **zero** source-grep assertions in the five files.
- No mock replaces a repository UPDATE path on any asserted happy path (single repo-replacement mock is the deliberate subject of a soft-failure containment test). Incident-B file: real `ProcessMessageProcessor` + real production T0 callback + real `JobRepository` on the production access path (`task_processor.py:922-933`); mocks confined to LLM pipeline + facade shell.
- 🟢 Non-blocking: incident-B module docstring claims file-backed `tmp_path` but fixture is in-memory StaticPool (:99-103) — doc drift (race coverage deliberately delegated to file-1's file-backed QueuePool test); file-4 `test_terminal_task_statuses_all_follow` is stamp-blind (no-op-safe via len); file-3 orphaned docstring at :821.

## 6. ensure.md Core — ✅ 4/4

| Req | Evidence | Status |
|---|---|---|
| #1 No regressions in changed packs | 6/6 acceptance sets PASS (§1) | ✅ |
| #2 Deadlock/concurrency integrity | `concurrency_atomic_unit_test` **98P/74S/0F in 7.24s** — baseline-exact, zero drift | ✅ |
| #3 No sync DB calls on event loop | same pack (thread-identity tests) | ✅ |
| #4 dev.sh graceful flag | `--timeout-graceful-shutdown 10` at dev.sh:102 (grep-quoted) | ✅ |

Release Gate (e2e) not run — Fix B range has no e2e-scope change and p0a protocol excludes tests/e2e from the full-suite baseline (documented territory split).

## 7. Gaps / known limitations

1. `tests/postgres/` excluded (live-PG provisioning blocked at schema creation; §4e) — new repo methods PG-untested by design of the branch's test layout.
2. `tests/e2e/` excluded per p0a protocol (Release-Gate territory).
3. 🟠 No cross-sweep reap×backstop ordering/double-fire test (§4b) — structural enforcement verified; recommended follow-up.
4. 🟢 17-node P8 context-flake family + bootstrap isolation-inverse node: pre-existing (QUARANTINE.md rows 2026-09-01, re-verified this gate); polluter pair-bisection follow-up carried forward.
5. 🟢 Cosmetic: incident-B docstring drift; file-3 orphaned docstring; file-4 stamp-blind assert; stale p0a blocker-#1 node ID in external docs; constitution pack census not in stdout.

## 8. Documentation updated

- PACKS.md: Fix B gate entry (this run).
- QUARANTINE.md: re-verification stamps on rows 37 (context-ordered family) + 38 (bootstrap) — no new rows (all families pre-existing).
- LESSONS/2026-09-02-fixb-gate-notes.md.
- This file.

## 9. Verdict

- Acceptance **6/6 EXACT** · full suite **15,578 passed / 259 F+E, 100% adjudicated: 0 caused** · differentials real (base-proven) · mock-audit PASS · ensure.md Core 4/4 · census 23 writers / delta 0 · repo READ-ONLY, worktree clean.
- **FINAL: ✅ PASS — merge-ready @ `db070275`.** Non-blocking follow-ups in §7 (one 🟠 ordering-test gap; rest 🟢).
