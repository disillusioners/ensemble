# Baseline attribution via detached temp worktrees (verification gates)

Date: 2026-10-05 — question-watch-fanout gate (18 workers, 43 reds attributed, 0 new-at-delta).

## Pattern

When a verification gate must classify sweep failures as PRE-EXISTING vs NEW-AT-DELTA, do NOT reason from commit archaeology alone — run the failing set at the pristine base SHA:

1. `git worktree add /tmp/<gate>/base-<sha> <base_sha> --detach` from the feature worktree (shares the repo object store; cheap).
2. `uv sync` in the temp worktree (~4-8s warm cache; budget 240s cold). NOTE: `uv` may live at `~/.local/bin/uv` — export PATH first.
3. Run the EXACT failing tests (single `-k "t1 or t2 or ..."` invocation over the affected files) with `timeout 300`, `-p no:cacheprovider` (avoids .pytest_cache races with parallel workers), `--tb=line -rf` for compact per-test IDs.
4. Machine-check the failure-set equality: sort both failing-ID lists, `comm -3 base delta` → 0 diff lines = provable parity. Stronger than eyeballing.
5. `git worktree remove --force` AFTER output is captured. Give each parallel baseline worker its OWN /tmp path (we used -sweep/-b2/-b3) to avoid collisions.

## Why it worked

- Leg A (11 reds): two "obvious delta candidates" (tool-count 24≠22, `settled` watch_events drift) turned out PRE-EXISTING — commit-archaeology guesses would have mis-blocked the gate. Only the base run settles it.
- Leg C (28 reds): one flagged "candidate regression" was actually in the documented pre-existing set; base run + comm-diff closed it in 22s of test time.
- Cost: ~4 min per leg including uv sync — cheaper than adjudication arguments.

## Related traps observed this gate

- Worktree venv trap ABSENT here: the editable `.pth` resolves INSIDE the worktree (prep leg verified) — but always have a prep worker confirm this before trusting imports in a new worktree.
- `echo ${PIPESTATUS[0]}` under `/bin/sh` gives Bad substitution / bogus exit codes — capture exit codes via a separate `echo $?` line or run the pipeline under bash explicitly.
- `complete_task` takes the int PK, not `work_id` (a work_id silently no-ops and leaves the row RUNNING, blocking claims via the per-instance concurrency gate) — harness-wiring trap when building in-process scenarios.
- `next_retry_at` binds require the repo's offset-bearing `%Y-%m-%dT%H:%M:%S.%f%z` TEXT format when injecting fake clocks.
