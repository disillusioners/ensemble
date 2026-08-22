# Lesson: e2e "never-claimed" signature ≠ code regression (2026-08-20)

**RESOLUTION 2026-08-21 (fix verified):** the root-cause bug this lesson documented — `_process_next_job`
`list_projects(limit=100)` scan starvation — is now FIXED on `fix/job-processor-admission-starvation`
(@ cc35959a) and verified CLOSED on the original failing context (ensemble_dev, 338 projects,
system-default rank #189, engine-log-verified): Release Gate 3/3 PASS, 5 `found PENDING job`
admissions, 42 Processing events, 50 spawns. Residual GUARD lines dropped 243 → 5, and the guard
parenthetical is a STATIC guard-list string (not a queue-admission attribution) — grep for
`[GUARD]` alone over-counts; grep the window AND check admission/processing counters before
calling starvation. See RESULTS/2026-08-21-job-processor-admission-starvation.md.

**Context:** Release Gate e2e (`test/packs/e2e_workflows_ensure_test.sh`) on live daemon :8079 during
feature/job-tools-cross-project-access verification. 3 of 4 tests failed identically, twice.

**Signature:** leader message task created but never claimed by worker pool — 0 `task_processor`
PROCESSING events, 0 LLM calls, 0 tool calls in leader lifetime; test times out at 60s
(`WAIT_CHILD timed out`); terminal report shows the test's own cancel cleaning the task.

**Key discriminators (use these before suspecting the diff):**
1. **0 tool/LLM calls in the failing instance** → whatever the diff changed never executed. Tool-layer
   changes cannot fail a run that never reaches a tool. Causation excluded on mechanism.
2. **A sibling test in the same pack that DOES drive the changed path passes** → daemon healthy,
   worker pool functional; the failure is admission/pickup-scoped, not behavioral.
3. **ensure.md queue-cleanup prerequisite was honored** (0 pending jobs at every checkpoint) → the
   documented false-failure cause was eliminated; remaining suspect is daemon long-uptime state.

**Procedure that worked:** honor the Release-Gate prerequisites (queue cleanup, one-by-one runs,
SSL unset) on a single re-dispatch; if the signature repeats deterministically, classify as
environment anomaly, do NOT quarantine (no base-evidence), and flag fresh-daemon re-run as follow-up.

**UPDATE 3 2026-08-21 (FINAL — bisect overturned, branch exonerated):** static analysis found the
confound; reproduction confirmed it. Base `39f76dc7` on DB `ensemble_dev` (338 projects) → F/F/F/P
identical to branch. Determinant = DB, not code: worktrees silently ran `ensemble_prod` (21
projects, system-default rank #1) because worktrees carry no `.env` (dev.sh sources the main
repo's `.env` → POSTGRES_DB=ensemble_dev). The 2026-08-20 bisect's "single-variable" claim was
false — code-state and DB-context were perfectly aliased. Real pre-existing bug:
`job_processor.py` ~:649 `list_projects(limit=100)` ordered `updated_at DESC`; system-default
`71931ae0` at rank #189 in ensemble_dev → jobs invisible to worker pool past the tests' 60s wait.
GUARD lines were secondary noise (decisive run had F/F/F/P with 0 GUARD lines).
The complete corrected attribution ladder for e2e failures:
  1. Zero-tool-call check → hypothesis only.
  2. Fresh-daemon re-run → rules out stale state; NEVER proves code causation.
  3. Base-commit controlled run → decisive ONLY if every context factor is pinned —
     especially the DATABASE (name via engine log/POSTGRES_DB, NOT /api/health.current_database,
     which reports TYPE not name), project count, and system-default rank.
  4. Worktrees do NOT inherit .env — any env-dependent behavior silently diverges between
     main repo and worktree runs. Source the intended env explicitly in every worktree run.
Key lesson: a confound that perfectly aliases with the variable under test is invisible to the
experiment — the worktree-vs-main distinction (prod DB vs dev DB) rode exactly along the
base-vs-branch distinction. When two context dimensions covary across every measurement, no
amount of repetition separates them; only a factorial design (base×dev, base×prod, tip×dev,
tip×prod) can. Cost of the factorial completion here: one extra run (~8 min).

**Rule of thumb:** on e2e FAIL, grep the leader's lifetime in daemon logs for PROCESSING/tool-call
counts FIRST. Zero-call failures are infrastructure/admission signals, not regression signals.

**Related:** RESULTS/2026-08-20-job-tools-cross-project-access.md §Anomaly.
