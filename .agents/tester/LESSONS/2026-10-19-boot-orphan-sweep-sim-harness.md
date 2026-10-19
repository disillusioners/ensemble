# LESSONS — boot-orphan-sweep restart-sim harness gotchas (2026-10-19)

## 1. astream stub MUST perform the faithful checkpoint side-effect write

Stubbing `CompiledStateGraph.astream` to "record + yield empty" is NOT enough for boot-generation tests: the orphan-sweep's natural-immunity/idempotency logic keys on the checkpoint last-write timestamp. If the stub never writes a checkpoint, the post-resume instance still looks orphan-shaped and the idempotency assertion silently INVERTS (or passes for the wrong reason). The stub must also perform the side effect the real graph performs — a new checkpoint write through the real `AsyncPostgresSaver` (uuid6 id, ts=now) — mirroring how the real turn advances the checkpoint axis. (Worker 78198b0c, scenario c1.)

## 2. The boot-resume lane mints NO task row — don't expect one as dispatch proof

`_schedule_explicit_handle_resume` → `_resume_processing_background` → `_process_message_with_tracking(is_retry=True)` drives the graph directly; Task rows belong to the worker-pool claim path. For boot-sweep dispatch proof use: `[RESUME] route_outcome=…` log line + stubbed astream invocation + checkpoint ts advance + JobItem `active→done` + instance terminal transition. Measuring `task_rows` after a rescue returns `[]` — correct by design, not a defect. (Actual route_outcome string for this sweep: `boot_continue_orphan_sweep`.)

## 3. `candidates` in the orphan-sweep summary is a PRE-barrier count

The SQL selector (`find_orphaned_live_turns`) surfaces current-generation turns as candidates; the boot-epoch generation barrier is enforced downstream at the resume layer. Observable consequences: scenario (b) (checkpoint >= boot_epoch) reports `candidates=1 scheduled=0 skipped_resume_refused=1`, and the natural-immunity skip lands in the `skipped_resume_refused` 3-way catch-all (known warning W-A) — disambiguate ONLY via the per-instance `SKIPPED instance=…: checkpoint last-write … >= boot_epoch …` line. Consumers of the summary line must not read `candidates` as "orphans found".

## 4. Worktree concurrency: attribute artifacts by mtime window, never by directory

Two test lanes ran simultaneously in the boot-orphan-sweep worktree (corpus + concurrency pack). Correct attribution: capture a pre-run file baseline, then `find -newermt` over the lane's exact wall-clock window; a directory mtime bump is NOT write evidence (a concurrent lane's `.pytest_cache` dir mtime bumped while its files stayed old). PYTHONDONTWRITEBYTECODE=1 holds for imports but NOT for pytest's assertion-rewrite hook — `-p no:cacheprovider` plus post-run window scan is the reliable combo.

## 5. Prior commission lessons re-confirmed (no new content)

POSIX `. ./.env` fence + Python re-derive + assert before ANY DB touch (ambient `10.44.0.2/ensemble_prod` poison present in every lane this session); `CAST(:p AS jsonb)` not `:p::jsonb`; `PIPESTATUS` is bash-only — under dash it exits 2 AFTER the inner block already reported success (don't rerun to "fix" a wrapper artifact).
