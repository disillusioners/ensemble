# Lesson: enforce=True breaks Mock-work_id regression tests; batch-attribution ≠ context-attribution

Date: 2026-09-02 · Gate: Constitution Phase 0 + Fix A full regression gate (`feature/job-task-constitution-p0a` @ `b07a91f7`, base `940e88b7`) · Report: `RESULTS/2026-09-01-constitution-p0a-full-gate.md`

## 1. Fix A's WARN→raise escalation invalidates Mock-based WARN-pinning tests

`_assert_linkage_contract` (daemon/services/messaging_types.py) was WARN-only at base; Fix A adds `enforce=True` at 4 job-driven sites (Observer trigger, JobProcessor main dispatch, crash-recovery re-spawn, orphan-resume re-spawn). Any pre-existing test that drives those sites with a **mocked** `enqueue_message` whose return is a `MagicMock` now deterministically hits `LinkageContractError` — because `result.job_id` (a Mock) != the real `job_id`.

Two production-adjacent tests broke exactly this way (deterministic 3/3 at HEAD, pass at base):
- `tests/job_queue/test_f1_mint_processor_tripfire.py::test_processor_crash_recovery_respawn_warns_on_linkage_violation`
- `tests/job_queue/test_job_processor_admission_starvation.py::…::test_admits_job_for_system_default_when_over_100_other_projects_exist` (secondary effect: recovery finalizes the JobItem → `admission_state='done'` instead of `'active'`)

**Rule of thumb:** when a tripwire escalates from log-WARN to raise, every test that (a) exercises an enforced site and (b) mocks the dispatch result is a latent deterministic failure. Migration checklist for any future escalation: grep test suites for mocked `enqueue_message` returns feeding JobProcessor/Observer paths BEFORE shipping the enforcement; update mocks to return real `work_id=job_id` or assert the new raise.

## 2. Base attribution must be context-matched, not just node-matched

Running ONLY the failing node IDs at base (the cheap protocol) misclassified 18 integration tests as "caused": they passed at base in the isolated batch but FAIL at base in the full `tests/integration` partition context (conftest langgraph-mock injection / TestClient state — order-sensitive on both commits).

**Protocol refinement (adopted):** any HEAD failure that PASSES at base in batch gets a second, context-matched check — re-run the full partition (same paths, same flags) at base in the scratch worktree. Only "fail at HEAD in-context + pass at base in-context" is caused-in-context. Additionally run the 3× solo determinism budget at HEAD for anything pass-at-base; solo-vs-context divergence at BASE itself (e.g. `test_agent_bootstrap_and_hello`: fails solo ×3 at base, passes only via companion-test mock carryover) reveals pre-existing broken test infra masquerading as green.

## 3. Scratch-worktree attribution hygiene (what worked)

- `git worktree add <tmp> <base>` + `uv sync` in the worktree → own `.venv`. MANDATORY isolation proof before any run: `.venv/bin/python -c "import daemon; print(daemon.__file__)"` must resolve INSIDE the worktree — the main repo's venv likely carries an editable install pointing at the main worktree (would silently test HEAD code "at base" and invalidate everything).
- Failure-list build: `grep -hE "^(FAILED|ERROR) tests/" /tmp/full-p*.log` — the ` tests/` (single space) filter drops daemon-logger `ERROR    daemon.…` lines that are not pytest results. In `-q` mode some FAILED lines truncate (`- d...`); recover full IDs manually.
- `git worktree remove --force` + verify `git worktree list` + main HEAD unchanged afterwards.

## 4. Misc

- pytest default addopts deselect `-m integration` — a "full suite" that must include integration needs `--override-ini="addopts="` plus an explicit `-m "not postgres"` (otherwise 270 PG-server tests collect/fail).
- `timeout 300 … | tail` pipelines mask pytest's exit code (tail's 0) — classify from the summary line/log, not `$?`, or use `|| EXIT_CODE=$?`.
- Baseline established this gate: 16,058 collected / 15,512 passed / 225 failed / 39 errors / 251 skipped (8 partitions, tests/e2e + tests/postgres excluded). 264 F+E fully attributed: 261 pre-existing, 2 caused, 1 borderline, 0 unexplained.
