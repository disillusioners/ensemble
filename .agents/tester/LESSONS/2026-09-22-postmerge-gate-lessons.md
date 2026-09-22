# Post-Merge Gate Lessons — latest @ ca4ab125 (2026-09-22)

## 1. F1 env-guard lesson: the scrub must wrap the TEST invocation, not just the daemon boot
The Intent5 emission-surface test (`tests/e2e/test_result_summary_emission.py:393`) carries an F1 env guard that REFUSES when the *test process* resolves a prod-like `POSTGRES_DB`. A worker booted `dev_with_mock.sh` with the full 7-var scrub but ran pytest WITHOUT the scrub → the test process inherited ambient `POSTGRES_DB=ensemble_prod` → guard refused (correctly, before any query). Correct shape: the `env -u …` prefix must wrap every invocation that can read DB coordinates — daemon boots AND pytest processes. The guard firing is the system working; treat `REFUSED (F1 env guard)` as a scrub-placement bug in the harness invocation, never bypass it.

## 2. A/B worktree legs resolve attribution when baseline family labels drift
Post-merge jq-full showed 14F vs a documented 11F baseline. Two failure groups looked "new" under the old family labels. What settled both in ~3 min each: detached `/tmp` worktrees at BOTH parents (`b024bb09` base + `56248e9c` feature tip), per-worktree `uv sync`, import-verify from INSIDE the worktree (cd first — sys.path[0] CWD trap resolves main-repo daemon otherwise), run only the suspect nodes.
- Fails at base + passes at feature tip → **pre-existing on latest** (the 3 event-driven MagicMock-JSON failures: b024bb09's restored content arm; the branch gate never saw them because its base was v0.13.9).
- Fails at BOTH parents → **pre-existing, lineage-independent** (in_progress_guard pair, enqueue_shared title-bridge).
- Passes solo everywhere → **load artifact** (multi_worker_notification; 1F/9P, 6/6 solo).
Also: family labels rot across gates ("proxy_phase1 instance-derived-status ×7" decomposed into instance-derived-status ×4 + default-watch-events ×3 on the newer tree). Keep NODE-level baseline lists, not just family labels, in future commissions.

## 3. A green feature gate does not green the merged base — and a green release gate does not green the full dir
The v0.13.10 gate validated the 30/30 acceptance pack but never ran the full `tests/job_queue/` dir → 3 regressions sat undetected on latest for hours until this post-merge full-dir run. Post-merge/post-release gates on the execution lane should include the lane's full dir at least once per merge, not only the scoped acceptance pack.

## 4. Operational notes
- Merged-tip daemons self-ID `0.13.10` (no version bump on the merge commit) — informational, not a boot failure.
- Dev boot under scrub resolves dev PG (`ensemble_dev` @ localhost), so the SQLite `20260714_000001` adjudication path was not exercised; that pre-existing blocker remains untested-by-this-gate (fine — boot PASSED on the real dev path).
- The 5-var ambient `POSTGRES_*` set was confirmed live in worker shells on this box (three independent workers observed it). The 7-var scrub stays mandatory on every invocation.
- Census drift (`terminal_write_census`) now flags `manager.py:5047 _on_stale_task_permanent_failure` as an unlisted terminal-write site — merge-added code inside an already-red test; census refresh stays open test-debt.

Artifacts: RESULTS/2026-09-22-post-merge-acceptance-gates-ca4ab125.md (full evidence); logs at /tmp/adjacent_run.log, /tmp/job_queue_full_regression_postmerge_ca4ab125.log, /tmp/g3_boot_probe.log, /tmp/g2b_dev_with_mock.log (scratch, not permanent).
