# ensure-validation lessons — 2026-09-11 (wc-wake-resilience core gate)

1. **Release-Gate E2E items are structurally unusable under a no-boot constraint.** ensure.md's Release Gate prerequisites require a live daemon (`./dev.sh`, port 8079); any validation run under "no daemon boot" safety constraints must declare these items NOT-validated via a contradiction notice, never attempt them. The durable fix is the file's own suggestion (:34): convert E2E scenarios to daemon-mocked mock-test packs; keep live-daemon variants as an operator-run section.

2. **Pack `-q` output hides node names — ran-vs-skipped needs a scoped verdict run.** The concurrency pack prints only counts (98P/74S). A `--collect-only -k "<family>"` probe proves nodes exist in scope but cannot distinguish pass from setup-skip. To positively confirm a named sub-family executed, run a scoped `-k` verdict over the specific files with `-rs` — cheap (0.43s for 12 nodes) and gives skip reasons verbatim.

3. **Documented skip-with-pointer is a legitimate coverage adjudication.** The 2 h15 thread-identity skips (legacy CorrelationManager API removed in D13 `89333a47`) cite the removing commit and name equivalent post-migration coverage (`tests/job_queue/test_job_feedback_observer.py`). Verification recipe: (a) read `-rs` skip text, (b) `git merge-base --is-ancestor <commit> HEAD` for ancestry, (c) compare counts to the canonical baseline — 98P/74S unchanged = pre-existing, branch-caused = 0.

4. **Canonical-baseline equality is itself pre-existing evidence.** When a pack result exactly matches its recorded canonical baseline (98P/0F/74S), every skip/failure inside it is baseline-attributed without further per-node A/B work — use this as a fast attribution shortcut before reaching for worktree A/B.

5. **Worktree claim awareness (shared KV).** This worktree carries an active `wt.claim.wc-wake-resilience` (owner: giter). Evidence was written as untracked files under `.agents/tester/RESULTS|LESSONS` only — no commit — to avoid colliding with the claimant's flow; leader decides commit timing.
