# Mission FINAL Gate — Stale-Fixture Families from Deliberate Vocabulary/Dispatch Changes

Date: 2026-09-03 | Branch: `feature/mission-class` (program HEAD `3f9fca81`, gate HEAD `1f95a9a9`) | Base: `e676ddea`
Gate: RESULTS/2026-09-03-mission-program-final-gate.md

## The pattern (recurring, now 3 manifestations)

**A program-wide rename/dispatch change ships with updated feature tests but leaves ADJACENT fixtures asserting the old contract.** The fixtures fail at HEAD, pass at base — they look like regressions in partition diffs until origin-traced to the deliberate commit.

This gate's three origin commits and their stale-fixture fallout:

| Origin commit | Deliberate change | Stale fixtures left behind |
|---|---|---|
| `05618c55` (M3 settled wire rename) | `settled` inserted into `watcher_models.ALL_TERMINAL_STATES` at **index 1** | 3 fixtures asserting the old default list `["completed","failed","cancelled","dead_letter"]` verbatim (watcher_repository_concurrent ×2, jober_watch_integration ×1) |
| `144012c4` (N8 per-kind dispatch at notify sites) | `getattr(self._job_queue_service, "_work_resolver", None)` + `per_kind_status_for(...)` at observer notify sites (:1335/:1897); **unconditional pre-loop read at :1863** | 2 fixtures whose mocks never configure `per_kind_status_for` (in_progress_guard ×2 → MagicMock-vs-literal assert); 1 probe `__new__`-constructing the observer without `_job_queue_service` (ri_off ×2 → AttributeError) |
| `ac37331e` (A3 watcher re-fire hole) | TERMINATED branch now fires `_fire_watcher_notify_for_terminal` (needs `get_job_by_instance` to compute candidate set/token); post-notify skip STILL applies | 2 fixtures asserting `get_job_by_instance` NOT called on terminated (job_feedback_observer ×1, phase2_feedback_verify ×1) |
| M3 vocabulary (B12/B13 `4a99547d` + per-kind dispatch) | mirror JobItems render terminal `settled` | 1 E2E VJM assertion `status in ("completed","processing")` (test_e2e_workflows.py:1391) |

**Key discriminator (use this before calling a regression):** a stale fixture fails with a *vocabulary/mock-shape* signature (token mismatch, MagicMock repr, AttributeError-on-attr-the-fixture-never-set) against a *deterministic deliberate commit* — and the replacement contract is pinned GREEN by the program's own new pin suites. In this gate: N8 hot-path pins 2/2, N1 5/5, N3 4/4, m3 dispatch 10/10, vocab runtime probe 9/9 — all green while the 8 stale fixtures were red.

## Detection protocol that worked

1. Partition packs with **0F baselines are tripwires** — job_queue partition went 0F→7F and that was the loudest signal of the gate.
2. Paired worktree A/B (base `e676ddea` vs HEAD) per NODE, not per file: 7/7 base-PASS proved branch-caused.
3. `git log -S "<symbol>" --oneline base..HEAD -- <file>` + pasted hunks pinned each failure to ONE deliberate commit.
4. The "(d) determination" for behavior-looking changes: read the CURRENT code path (does the skip still apply after the notify?) — `sync_mock.assert_called_once()` passing inside the same failing test proved the finalize path intact.

## Fixes applied this gate (test-code only, both quick-fix)

- `a0e4c59b` — ri_off probe: `SimpleNamespace(_work_resolver=None)` stub for `_job_queue_service` (8 lines). Lesson inside the lesson: the first `edit_file` import-add silently no-op'd despite SUCCESS — always grep-verify after edit.
- `1f95a9a9` — e2e VJM assertion: add `"settled"` to the expected tuple (3 lines). Live proof: T1 retry leader completed naturally in 67s; only the stale assert failed.

## Open follow-up: fixture migration for the 7 quarantined nodes (QUARANTINE.md row "Mission-program FINAL-gate stale-fixture family")

Exact edits (~7 across 3 files, all TEST code):
- watcher_repository_concurrent ×2 + jober_watch_integration ×1: expected list gains `"settled"` at index 1 (match `05618c55`'s insertion order).
- in_progress_guard ×2: configure `_work_resolver.per_kind_status_for = MagicMock(return_value="completed")` (or return the per-kind default).
- job_feedback_observer ×1 + phase2_feedback_verify ×1: replace `assert_not_called` with `assert_called_once` for `get_job_by_instance` inside the terminated re-fire + keep asserting finalize/skip-after-notify (the REAL new contract: notify fires, finalize does not).

## Second lesson: coarse family buckets hide signature shifts

M2's ledger counted `test_memory_integration.py ×10` inside "sqlite-migration cascade 29". Per-node A/B this gate showed the true signature is `inner_soul.py:1389 _load_growth_rules` MagicMock TypeError — identical at BOTH revs (pre-existing stands) but a different root than the bucket implied. **Record per-test signatures in family rows, or re-verify per-node when composition shifts** (this gate: sqlite 29→18 + memory 0→11 forced the investigation).

## Third lesson: the E2E flake family (row 31) now has a second surface

T1 first-run failed with `DependencyBus is not initialized for instance=… (Phase 5)` → leader stuck running until cleanup DELETE. Same leader-stuck-on-child-completion mode as quarantined T2 (row 31), different fault line (bus init vs WAIT_COMPLETE). Retry passed the wait window (completed in 67s) — non-deterministic infra flake, pre-dating the branch. Watch: third manifestation → promote to family row.
