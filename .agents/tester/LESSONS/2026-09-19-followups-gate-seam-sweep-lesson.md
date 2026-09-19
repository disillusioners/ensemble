# 2026-09-19 — chat-lane-followups gate: wake-contract sweeps must grep by SEAM not by filename (2nd occurrence)

**Arc:** follow-up gate, `feature/chat-lane-followups` @ b702065f (base 3a6373ca). Verdict FAIL — 5 branch-introduced reds; full report: `RESULTS/2026-09-19-chat-lane-followups-merge-gate.md`.

## 1. The same rot class struck TWICE on this arc — sweep by seam, not filename

- Gate 1 (chat-source-worker-lane): Phase-2 `_notify_all_pools` fan-out → 6 holder stubs un-swept (`test_task_only_create_notify_work.py`, `test_reconciler_wedge_fix.py`). Fixed in 6f624c2c.
- Gate 2 (chat-lane-followups): Phase-B `safe_notify_all_pools` consolidation → 5 pins un-swept (`test_a2_autopromote_notify.py` ×2, `test_a3_eligible_pending_sweep.py` ×2, `test_a4_f14_orphan_detection.py` ×1 — 2 behavioral single-pool mocks + 3 STATIC literal pins on `worker_pool.notify_work()`).
- The branch's own sweep commit (`feaf76ba` "update tests that pinned pre-Phase-B source shape") re-pointed 6 files but MISSED these 3 job_queue files. Why: the sweep found tests touching the changed files, but constitution-style pins (`assert 'literal' in contents`) live in UNRELATED files that grep for the literal. **Rule: when replacing a canonical seam literal (wake calls, chokepoint callers, mirror sets), grep the LITERAL across ALL of tests/ (`grep -rn "worker_pool.notify_work()" tests/`), not just files importing the touched modules.** Static constitution pins are the most likely un-swept class — they reference production TEXT, not symbols.
- Counter-evidence pattern that correctly diagnosed test-rot vs regression: production enqueue→wake e2e + A2.2 notify-path assertions + 71 re-pointed tests all green while single-pool mock pins red = mock-contract supersession, not a broken wake path.

## 2. Census mechanics that worked (repeat-worthy)

- **Sibling pack scope-mirror under the 300s invariant:** the registered pack (350s/360s own guards) exceeded the gate cap; executing its exact scope as 2 alphabetical file-glob slices (a-l / m-z, `--ignore-glob` preserved, PG_TEST_PORT pinned to a dead port so the 14 PG-marked entrants skip) delivered the full census (943P/32F/5E) under 2×300s with zero shared-PG exposure. Quoting the glob is WRONG — pytest treats `[]` as parametrization; leave it unquoted for shell expansion.
- **First-time-in-pack selections need base A/B by default:** of the 254 newly-selected integration tests, 12 reds + 1 hang + 1 flake appeared — ALL reproduced at base. A "never ran before" red is not a branch red until base says so.
- **Chokepoint caller-list verbatim compare** (base vs HEAD failure TEXT) is the cleanest pre-existence proof for static-analysis tests — byte-identical lists at 3a6373ca closed the suspicion in one shot.
- Census workers must redirect pack output to a file and grep — `| tail -N` truncates `--tb=short` traceback blocks and loses signatures (P-9 worker had node-ids only; QUARANTINE family mapping still resolved it, but signature capture is the robust path).

## 3. Environment notes

- Main checkout drifted mid-gate (foreign session: 1042de8c → cd0adb52). Census validity survived because every worker pinned `git rev-parse HEAD` at run time and the adjudicator used a detached worktree at the frozen SHA. **Rule: frozen-tree gates must (a) have each worker echo HEAD, and (b) keep an at-SHA worktree fallback for the adjudicator.**
- `test_terminal_reset_and_fresh_episode_rearm_next_mission` hangs 60s at BOTH trees (asyncio select never completes under `_run_with_classification`) — env-related pre-existing hang, not branch-caused; candidate for a skip-marker or quarantine review.
- W19 atomic flake (`test_update_activity_concurrent_does_not_clobber_terminal_status`): 1/3 fail at HEAD vs 0/3 at base solo — same class (SQLite commit-contention under thread load), higher rate; watch, don't block.
