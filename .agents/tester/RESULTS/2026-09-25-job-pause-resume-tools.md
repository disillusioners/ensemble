# Test Report: job_pause/job_resume agent tools — regression + verification (commits 1c774667 → delta 5369c9bf)

Date: 2026-09-25
Worktree: `/home/nea/ensemble-worktrees/job-pause-resume-tools` — branch `feature/job-pause-resume-tools`, base `75d7e2d1cb6c1e9263bc0500c13f176acd8c5894`
Original run HEAD: `1c77466740440f6ce7ab4dd72553e45201cc2679` (3 commits ahead of base: 5b194ef7, 64a88b9b, 1c774667)
Delta run HEAD: `5369c9bf1965888fe51cda1bc749f4f2cf0648f0` (4th commit: kind-refusal fail-closed + guard-order pin + parametrized terminal-status + STUCK_AWAITING_ANSWER constant) — see §Delta (COMPLETE, final verdict at end of file).
Commission: report-only regression + verification. Read-only on code — no fixes, no commits by tester.

Worker instances (original run): jpr-discover `47ea85cd` · jpr-basesetup `58fc5164` · jpr-b1-feature `c24d29f1` · jpr-b2-jobtools `7f09ec00` · jpr-b3-sibling `29b5acf7` · jpr-b4-conc `609ee34a` · jpr-b5-boot `22c54355` · jpr-x2-jobtools-base `85c03d72` · jpr-x3-sibling-base `fbb36714`

Safety: `POSTGRES_*` / `PG*` / `DATABASE_URL` scrubbed (`env -u` full list) in every worker shell + the boot-probe env (verified "SCRUB-CLEAN" inside the boot shell); import-path guard verified in EVERY worker (`daemon.__file__` inside the target worktree — feature worktree, or `/tmp/ens-base-75d7e2d1` for base legs); live daemon (ensemble_prod, port 9797, pid 1220496) verified untouched; all runs `timeout 300`-wrapped; zero PG connections (pure-mock unit selections); both worktrees left at their HEADs with zero tester-introduced changes; feature worktree clean except a concurrent tidier commission's `.agents/tidier/notes.md` edit (not ours, left untouched).

## VERDICT (original run @ 1c774667): 🟠 REGRESSION-FOUND — 1 branch-caused failure

`tests/test_job_queue_tools.py::TestJobQueueToolRegistration::test_create_job_tools_returns_expected_count` (line 41) PASSES on base (X2: 134/134) and FAILS on branch → NEW on branch, not pre-existing-RED. Mechanism: branch adds `job_pause`/`job_resume` to the job-tools factory (`daemon/tools/job_queue.py`) without bumping the expected-count assertion in this pre-existing test (feature diff `75d7e2d1..1c774667` does NOT touch `tests/test_job_queue_tools.py`). Test-expectation miss, not product breakage; quick-fix eligible (<20 lines, single test file, obvious root cause) — NOT applied per read-only mandate. Developer owes the count bump (and should check for any other count/registry pins).

## Summary (original run)

- Scoped feature suite: 27/27 PASS (2.16s) — matches developer claim exactly
- Adjacent job-tools (test_job_queue_tools.py + test_queue_ref_tools.py): **1 FAIL** — 133P/1F/0S of 134 (8.36s); formal report recovered via worker revival
- Sibling instance-tools (test_pause_resume_instance_tools.py): 32/32 PASS
- Concurrency pack (ensure.md Core #2/#3): 98P/74S/0F — byte-identical to 2026-09-23/24 baseline
- Boot probe: FAIL, **branch-neutral** (pre-existing sqlite-migration defect, see §5)

## 1. Scoped suites

| Suite | Expected | Actual | Verdict | Worker |
|---|---|---|---|---|
| tests/unit/tools/test_job_pause_resume_tools.py (NEW file) | 27P | 27P/0F/0S (2.16s) | ✅ | c24d29f1 |
| tests/test_job_queue_tools.py + tests/test_queue_ref_tools.py | 134P (per base) | 133P/1F/0S (8.36s) | 🔴 | 7f09ec00 |
| tests/unit/tools/test_pause_resume_instance_tools.py | 32P | 32P/0F/0S (7.82s) | ✅ | 29b5acf7 |
| test/packs/concurrency_atomic_unit_test.sh (registered) | 98P/74S baseline | 98P/74S/0F (64.13s) | ✅ | 609ee34a |
| Scrubbed dev.sh boot probe | 30s must-not-crash | FAIL — branch-neutral (§5) | ⚪ | 22c54355 |

## 2. A/B regression table (base 75d7e2d1 vs branch 1c774667)

Methodology mirrors 2026-09-25-parallel-default-queue-verification.md: pristine scratch base worktree `/tmp/ens-base-75d7e2d1` (detached @ 75d7e2d1, own venv, `_editable_impl_ensemble.pth` → base path, `daemon.__version__` 0.14.1), identical selection both sides, node-id-exact comparison.

| Selection | Base (75d7e2d1) | Branch (1c774667) | Outcome |
|---|---|---|---|
| tests/test_job_queue_tools.py + tests/test_queue_ref_tools.py | 134P/0F/0S (8.82s) | 1F: `TestJobQueueToolRegistration::test_create_job_tools_returns_expected_count` | 🔴 **DIVERGENT — NEW branch failure** |
| tests/unit/tools/test_pause_resume_instance_tools.py | 32P/0F/0S (8.93s) | 32P/0F/0S (7.82s) | ✅ IDENTICAL |
| tests/unit/tools/test_job_pause_resume_tools.py | n/a (file does not exist on base) | 27P/0F/0S | branch-only (new surface) |

**Known pre-existing RED fencing** (per commission contract): 3× `TestSite1InlineMirrorFinalize` + `enqueue_shared` idle→running drift live in `tests/job_queue/` (e.g. test_event_driven_completion.py) — NOT part of this selection (scoped to tool-layer surfaces; lane rule §4). 5× quarantined `TestAccessMemoryArchive` (QUARANTINE.md) live in `tests/unit/tools/test_archive_lifecycle.py` — also NOT in this selection. Therefore **zero** failures in our selection are attributable to the known-RED set; the single observed failure is fully classified as branch-caused.

## 3. Registration-count failure — classification detail

- Exact id: `tests/test_job_queue_tools.py::TestJobQueueToolRegistration::test_create_job_tools_returns_expected_count` (tests/test_job_queue_tools.py:41)
- NEW on branch: yes — proven by A/B (base leg X2 passed it as part of 134/134, identical selection, same machine, minutes apart)
- Verbatim assertion (recovered via jpr-b2 revival, captured at 1c774667; log `/tmp/job_tools_pack_20260925_074709.log`):
  ```
  tests/test_job_queue_tools.py:41: in test_create_job_tools_returns_expected_count
      assert len(tools) == 22
  E   assert 24 == 22
  E    +  where 24 = len([StructuredTool(name='job_create', ...job_delete ...)])
  ```
  Counts: 134 collected (81 + 53) → 133P / 1F / 0S, 8.36s. `tests/test_queue_ref_tools.py` contributed zero failures (53/53 green).
- Root cause confirmed: feature adds job_pause + job_resume to `create_job_tools` → live count 24; the pre-existing assertion pins the pre-feature count 22 and was never bumped (file untouched by the feature diff).
- Suggested fix (developer's, not ours): one-line bump `assert len(tools) == 24` — quick-fix eligible, NOT applied per read-only mandate.

## 4. e2e execution-lane judgment: NOT TRIGGERED

Judged by EXECUTION-LANE intersection, not file diff (per project convention). Diff `75d7e2d1..1c774667` = `daemon/tools/job_queue.py` (M), `daemon/tools/_tool_registry.py` (M), `agents/leader/meta.json` (M), `tests/unit/tools/test_job_pause_resume_tools.py` (A) — verified by git diff --name-status (jpr-discover). Execution lane = `daemon/services/task_processor.py` claim/dispatch (task_processor.py:267, claim_pending_task :464) and message_job_handler — **zero intersection** (lane files absent from diff). The change adds agent-invoked tool wrappers over EXISTING service APIs; no claim/dispatch/retry/dead-letter code touched.
ensure.md grep-verified before judging (citation discipline): `.agents/tester/rules/ensure.md` in the worktree = **53 lines**, pack-mapped Core/Release-Gate structure — the "4-line dev.sh boot-probe ONLY" characterization in the commission preamble is STALE (predates current structure). Scoped Core results: #1 changed-packs (see failures — FAIL via §2 divergence); #2/#3 concurrency pack PASS (§1); #4 `dev.sh --timeout-graceful-shutdown 10` static grep FOUND (lines 99/102) — PASS. Release Gate: not triggered (small scoped change; no lane intersection).
Boot probe (belt-and-braces, sibling-commission precedent — NOT an ensure.md mandate): FAIL, branch-neutral — see §5.

## 5. Boot probe — branch-neutral FAIL attribution

Scrubbed dev.sh boot in the feature worktree: uvicorn + reloader started, config loaded (sqlite, isolated /tmp data), service-init lines emitted (ServiceTool/ContextMessages/CriticalNotes/ResponseValidation/SymptomRepair, TmpImages ready), then lifespan died at InstanceManager init → migrations:
`MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error [SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]` → `Application startup failed`.
Attribution: traceback frames confined to `daemon/api.py:385`, `daemon/manager.py:530`, `daemon/migrations/runner.py:747/608`; ZERO frames/log mentions of `_tool_registry.py`, `meta.json`, `job_pause`/`job_resume`. The failing migration SQL lives in `daemon/migrations/` — untouched by the branch — and this sqlite-boot death is the documented open pre-existing defect (project critical note: "sqlite boot dies at migration 20260714_000001 (PG-only DDL) — separate commission"). Teardown clean: cwd-verified kills only, port 8079 freed, 9797 untouched. Registration-surface boot sanity is instead evidenced by: the suite's `TestRegistration` class (factory surface + KNOWN_TOOL_NAMES, 27/27 incl.) + boot reaching service init past all tool-module imports. Note for the record: the 2026-09-24 sibling commission's boot probe PASSED on adjacent lineage — conditions differed; our attribution rests on frame-level evidence above.

## 6. Anomalies / notes

1. jpr-b2-jobtools final report lost in transit (interim only) — RESOLVED via revival: full report delivered from captured data (verbatim `assert 24 == 22`, counts 133P/1F/0S @ 1c774667; no re-run performed, per the moved-HEAD warning).
2. Feature worktree NOT byte-clean: `.agents/tidier/notes.md` modified by a CONCURRENT tidier commission working this same branch (mtime 07:47, pre-dates our boot probe; content = tidier iteration-001 record @ 1c774667). Left untouched. HEAD unaffected. Delta workers must verify HEAD == 5369c9bf before running.
3. `uv` not on PATH on this host — all workers used `/home/nea/.local/bin/uv` (absolute).
4. PACKS.md active-commission header still references the 2026-09-24 agent-pause-resume gate; this branch's ad-hoc packs are documented here (registered-pack usage: concurrency_atomic_unit_test only). Gate author may want to catalogue this commission.
5. Stale `/tmp/mfq-gate*` worktrees (246b7325) confirmed unused; base leg used fresh `/tmp/ens-base-75d7e2d1`.

## Delta verification @ 5369c9bf (4th commit: kind != "job" fail-closed refusal ×2 tests, guard-order pin, parametrized terminal-status, STUCK_AWAITING_ANSWER constant) — COMPLETE

Original A/B was against 1c774667, NOT the delta HEAD → branch-side legs re-run on 5369c9bf (D1–D3, all STEP-0-verified HEAD `5369c9bf1965888fe51cda1bc749f4f2cf0648f0`, import-isolation proven, env-scrubbed, `timeout 300`-wrapped). Base legs NOT re-run — X2/X3 (this session, `/tmp/ens-base-75d7e2d1` @ 75d7e2d1) remain valid: base sha unchanged, selection identical, results deterministic (prior evidence); the polish commit's new tests do not exist on base, so no base-side counterpart exists.

| Leg | Selection | Result | Worker |
|---|---|---|---|
| D1 | tests/unit/tools/test_job_pause_resume_tools.py | **36P / 0F / 0S** (2.77s) — commission expectation met exactly. Delta +9 vs 27: (a) 2 NEW kind-refusal tests `TestJobPause::test_pause_refuses_non_job_kind` + `TestJobResume::test_resume_refuses_non_job_kind`; (b) 1 NEW guard-order pin `TestJobResume::test_resume_write_paused_gate_fires_before_question_pack_guard`; (c) 2 terminal tests parametrized ×4 (`[completed]/[failed]/[cancelled]/[dead_letter]`): `test_pause_terminal_job_refused` + `test_resume_terminal_job_passes_through_fe_identically`. Math: 27 + 3 new + 6 parametrization-expansion = 36 ✓. Per-class: TestJobPause=11, TestJobResume=12, TestJobPauseAccessControl=4, TestJobResumeAccessControl=3, TestJobStateUntouched=2, TestRegistration=4 | 84c26296 |
| D2 | tests/test_job_queue_tools.py + tests/test_queue_ref_tools.py | **133P / 1F / 0S** (8.50s) — SAME single failure PERSISTS at 5369c9bf: `tests/test_job_queue_tools.py::TestJobQueueToolRegistration::test_create_job_tools_returns_expected_count` — `assert len(tools) == 22` → `assert 24 == 22` (line 41; comment line 40 pins "…= 22"). 5369c9bf did not touch this test file (polish commit confined to daemon/tools/job_queue.py + feature test file). Verbatim evidence re-captured at delta HEAD | 7c4712f1 |
| D3 | tests/unit/tools/test_pause_resume_instance_tools.py | **32P / 0F / 0S** (7.81s) — identical to both prior legs (base 8.93s, branch-1c774667 7.82s); polish commit did not perturb the sibling surface | f398a92c |

## FINAL MERGE VERDICT: 🟠 REGRESSION-FOUND (persists at 5369c9bf)

- **Exact failure**: `tests/test_job_queue_tools.py::TestJobQueueToolRegistration::test_create_job_tools_returns_expected_count` — `assert len(tools) == 22` fails with actual 24 (`tests/test_job_queue_tools.py:41`; count-arithmetic comment at :40).
- **Classification**: NEW on branch (base 75d7e2d1 passes 134/134 on the identical selection); test-expectation miss — the feature added job_pause + job_resume (2 tools) to `create_job_tools`; the pre-existing count pin was never bumped; `tests/test_job_queue_tools.py` is untouched by the branch diff (verified both at 1c774667 and via the 5369c9bf commit contents).
- **Developer fix owed** (NOT applied — read-only mandate): bump comment+assertion to 24, i.e. line 40 `# 16 original + job_continue + job_messages + job_tree + job_progress + job_inject + job_answer + job_pause + job_resume = 24` and line 41 `assert len(tools) == 24`. One line + comment; quick-fix eligible.
- **Everything else green at both HEADs**: feature suite 27/27 @ 1c774667 and 36/36 @ 5369c9bf (incl. the new kind-refusal fail-closed tests and the guard-order pin — both pass); sibling 32/32 on all three legs (base, 1c774667, 5369c9bf); concurrency pack baseline-identical 98P/74S/0F; boot probe FAIL is the documented pre-existing sqlite-migration defect, branch-neutral (§5).
- **Merge disposition (leader's call)**: the sole failure is a stale test pin, not product breakage — all behavioral surfaces green. Recommend the developer applies the one-line bump before merge (trivial); with that applied, this branch is merge-clean from the testing side.

## Documentation trail

- RESULTS: this file (original + delta sections complete).
- PACKS.md: concurrency_atomic_unit_test Last Run updated → 2026-09-25 (98P/74S/0F @ 1c774667).
- No code changes, no commits by tester (report-only commission). RESULTS/PACKS.md edits live in the MAIN checkout (/home/nea/ensemble-src, itself at 75d7e2d1) — commit of these docs is left to the leader per read-only mandate on repo state.
