# Phase 4: Tests, packs, gates (AC7)

## Objective

The full unit/integration matrix is packed per repo conventions, pack SPECs realized as executable pack wrappers (PACKS.md itself is TESTER-OWNED — its edit is DEFERRED to the implementation/tester lane; this plan registers the SPECS in test-strategy.md), and the execution-lane intersection is discharged: because resume = re-dispatch, the lane intersection is LIKELY NON-EMPTY → FULL-DIR job-queue gate + cascade e2e per the 2026-10-03 precedent, plus the lane-site drift rule observed for every citation.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4.1 | Complete the unit/integration matrix per test-strategy.md §Matrix: fill any gap between P1/P2/P3 tests and the matrix (config kill-switch cases, boot-epoch None fallback, `ContinueResult` counter accounting, candidate ordering determinism, PG-vs-SQLite predicate parity where testable in-memory) | P1-P3 | Matrix table fully green; no TODO-left tests; coverage of every AC at unit level except the AC8 real-environment proof |
| 4.2 | Create `test/packs/auto_continue_boot_pass_unit_test.sh` — transparent wrapper mirroring `test/packs/post_restart_arm_notify_sweep_unit_test.sh` verbatim structure (cd to repo root, `PACK_NAME`, watchdog `timeout 110s` Layer-2, `.venv/bin/pytest tests/unit/services/test_auto_continue_boot_pass.py tests/unit/repositories/test_auto_continue_candidates.py --tb=short -q`, exit-code propagation, `RESULT: PASS/FAIL/TIMEOUT` protocol). Scope line names the tested surface | P2, P1 | Pack runs green locally: `bash test/packs/auto_continue_boot_pass_unit_test.sh` → `RESULT: PASS`; <110 s |
| 4.3 | Create `test/packs/auto_continue_interleaving_unit_test.sh` — same wrapper pattern over `tests/unit/services/test_auto_continue_interleaving.py` | P3 | Pack green; <110 s |
| 4.4 | **DO NOT edit `.agents/tester/PACKS.md`** (tester-owned). Instead verify test-strategy.md §Pack SPECS matches the implemented packs 1:1 (name, path, tested file, scope summary, trigger, expected evidence). Record in the implementation hand-off note: "PACKS.md registration pending — tester lane to add rows for `auto_continue_boot_pass_unit_test` + `auto_continue_interleaving_unit_test` using the SPECs in test-strategy.md" | 4.2, 4.3 | test-strategy.md §Pack SPECs === reality; hand-off note present in the PR/commit description |
| 4.5 | Full-dir gate (2026-10-03 precedent — "gate ran 30/30 pack, never full job_queue dir → undetected"): run the ENTIRE `tests/unit/job_queue/` + `tests/unit/services/` + `tests/unit/repositories/` directories, not just targeted packs. Because resume = re-dispatch, the execution-lane intersection is likely non-empty (lane sites: `task_processor.py:267`, `message_processing_pipeline.py` (replaced deleted `message_job_handler.py`), `claim_pending_task` `task/repository.py:1733+`/guard `:2230-2294` — GREP-VERIFY each at use time; anchors drift) | 4.1 | Full-dir runs green (or pre-existing reds triaged + documented as not-ours with the A/B evidence format used in the 2026-09-22 post-merge gate report) |
| 4.6 | Cascade e2e gate (execution-lane intersection): run the existing cascade end-toe-to-toe pack (the parent/child completion-report cascade — pick the current canonical cascade e2e pack from `test/packs/` by grepping `child_parent_lifecycle` / `completion_regression` at execution time) against the feature branch. Purpose: prove the feature introduces no regression in the child→parent report cascade it deliberately does NOT touch | 4.5 | Cascade e2e green; pack name + result recorded in the implementation evidence file |

## Coupling

- **Tight with P1-P3** — packs wrap their test files; a renamed test file breaks the pack (wrapper asserts on exact pytest paths).
- **Loose with P5** — the demo E2E is manual/procedural (P5), not pack-gated; but P5 requires P4's full-dir + cascade gates green FIRST.
- **Tester-lane handoff** — PACKS.md rows (deferred), post-merge full-dir acceptance gates, and any flake triage follow `.agents/tester/` conventions.

## Verification Steps

1. `bash test/packs/auto_continue_boot_pass_unit_test.sh && bash test/packs/auto_continue_interleaving_unit_test.sh` → both `RESULT: PASS`.
2. Full-dir: `.venv/bin/pytest tests/unit/job_queue tests/unit/services tests/unit/repositories -q` → green (or triaged).
3. Cascade e2e pack green (grep-verified canonical name at execution time).
4. Confirm zero `PACKS.md` diff in the branch (`git diff --stat .agents/tester/PACKS.md` → empty).
5. Structural hygiene: every new `.py` file starts with `from __future__ import annotations` (tests included — repo trap #1); run `.venv/bin/python -W error::DeprecationWarning -c "import daemon.services.auto_continue_boot_pass"` as a smoke import.

## AC Mapping

- **AC7** — this phase IS AC7: unit/integration per repo conventions, pack registration (SPECs now, PACKS.md row deferred to tester lane), full-dir + cascade gates per the 2026-10-03 precedent, drift rule observed.

## Rollback Note

Packs and tests delete cleanly; no production coupling. The full-dir/cascade gate RESULTS should be preserved as evidence files under `.agents/tester/RESULTS/` by the implementation lane (naming per existing convention `YYYY-MM-DD-<subject>.md`) — those are evidence, not code, and roll back by deletion.

## Exit Criterion

Both packs green, full-dir green (or triaged with A/B evidence), cascade e2e green, PACKS.md untouched, hand-off note written. Feature is test-gated and ready for the demo E2E (P5).
