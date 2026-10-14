# 2026-08-31 — Mid-Review Branch Switch Invalidates Test-Execution Dispatches

**Context:** Light review of `feature/slash-command-autocomplete` (39f5d257 + 4f729f43). Single-worktree repo shared with an active user.

**What happened:**
- Dispatch-time `git status` confirmed the PR branch at HEAD → I told the test-execution worker "branch already checked out."
- Externally, the user switched the worktree to `feature/compact-on-completed` mid-review.
- Static-review worker (ran early window): valid — targeted suites ran green on the correct tree.
- Execution worker (ran late window): correctly detected drift via `git rev-parse` before running, refused to `git checkout` (read-only), reported blocker. Its early cache-warm run returned green numbers for spec files that were absent by its second (`--no-cache`) run.

**Lessons for future dispatches:**
1. "Branch checked out" is a *point-in-time* fact, not a dispatch invariant. Any test-execution prompt must instruct: **record `git rev-parse --abbrev-ref HEAD` + `git rev-parse --short HEAD` immediately before EVERY test invocation** and treat drift as a hard blocker (report; never checkout/stash).
2. Distrust warm-cache Jest results not bracketed by a rev-parse check — a `--no-cache` re-run is cheap insurance on fast suites (this one: 7–9s full).
3. When a worker reports an environment blocker it did not cause, do NOT burn the escape-valve re-dispatch — the replacement hits the same blocker. Aggregate with an explicit Gaps section instead.
4. Corroboration hierarchy when execution is blocked: live run by another worker in the valid window > cache-warm results > dev claim. State which tier each verified item rests on.

**Handling that worked:** execution worker's refusal to switch branches + blocker report is the correct behavior — keep instructing it.
