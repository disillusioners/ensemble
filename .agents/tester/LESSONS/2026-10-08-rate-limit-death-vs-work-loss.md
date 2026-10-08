# Rate-limit worker death ≠ work loss — verify committed state before re-dispatching authoring tasks

**Date:** 2026-10-08 · **Gate:** tool-pairing full-history heal (RESULTS/2026-10-08-tool-pairing-heal-gate.md)

## What happened

During the gate's authoring wave, the shared LLM proxy saturated (`openai: Rate limit reached`) and killed workers mid-flight. Two test-author workers (3dc47f7b, then its replacement 1d8fe9e6) reported as `error` and were later `not found` on revive — I recorded the G2/G4/G5 authoring node as "work never landed."

**That was wrong.** The replacement (1d8fe9e6) had fully authored and pathspec-committed its work (`6455b66c2`, 4 files / 287 lines) ~2 minutes before dying — its final LLM turn failed, but the git commit had already landed. The pins-author worker dispatched later (3603c71d) found the files already committed and correctly built only the missing artifacts (pack script + PACKS.md row) instead of re-authoring.

## Root cause

An `execution_error` / `error` status marks the instance's **LLM loop** as dead — it says nothing about whether the instance's **tool side-effects** (file writes, git commits) completed before the fatal call. A worker that dies between "did the work" and "reported the work" leaves committed artifacts with no report.

## Rule going forward

Before re-dispatching any authoring/write task after a worker death (especially "environmental" deaths like rate limits):

1. Check disk state: `ls <expected-output-dir>/` + `git status --porcelain`
2. Check git state: `git log --oneline -5` + `git log --all -- <expected paths>` — match the dead worker's task-spec commit message
3. If the work exists and is committed: dispatch a *verification* task (validate API-correctness + build missing peripherals), not a re-authoring task

## Related operational lessons from the same storm

- **Concurrency kill zone:** 3-4 simultaneous worker spawns reliably triggered the proxy rate limit; **serialized single dispatches** ran 9/9 clean afterward. Cap parallel waves at 2, serialize when deaths appear.
- **Recovery ladder discipline held:** original → revive (once, per instance) → one replacement → stop + report. The leader's re-authorization then closed the remaining gaps cleanly (tph44 44/44 first attempt once the environment was healthy).
- **A/B fairness recipe that worked:** scratch detached worktree at base + byte-identical pack-script copy (md5-verified) + venv fence check + node-for-node failing-set diff. The 16/16 pre-existing verdict for jq_full was produced this way in one pass.
