# HANDOFF — wc-watchdog-fp (sonic lane) — v0.31.33

Commission (2026-11-22T17:05Z, sonic standard path): cure the waiting-children WATCHDOG FALSE-POSITIVE class — "no_live_carrier / zero non-terminal children" wedge notices on HEALTHY parked roots (anomaly a370b036; four false notices on ari root 77ddcb9f-ba89-4466-af9c-2c4e6f25d6cc, 2026-11-21→22). Base 8a1045c02 (= origin/latest = v0.31.32 staged tip). Staged release v0.31.33, NO live promote; v0.31.32 tag untouched.

Integration branch: sonic/wc-watchdog-fp-int. HANDOFF_PATH = .agents/shared/planning/sonic-team/wc-watchdog-fp/HANDOFF.md.

## Files touched across modules

- **Module 1 mod-wake-source-gate @ 93da27b29 (merged 7b624281c, slot 1):**
  - daemon/services/waiting_children_watchdog.py (tri-state `_has_legitimate_wake_source` + 5 leg helpers, wired into the wedge pass after S4 in-scope, before carrier)
  - daemon/repositories/task/repository.py (`list_live_delivery_tasks_for_instance`)
  - daemon/repositories/report_injection/repository.py (`has_open_obligation_for_parent`)
  - daemon/api.py (watchdog construction wires `watcher_repository=watcher_repo`)
- **Module 2 mod-fp-pin-pack @ 3231bdfca (merged 72a55b1f8 after bounce-1, slot 2):**
  - tests/unit/services/test_waiting_children_watchdog_wake_sources.py (NEW, 17 behavioral nodes)
  - .agents/tester/PACKS.md (`## WC-WATCHDOG-WAKE-SOURCE-GATE — Unit Pack Registration`)
- **Integration & Release Vehicle:**
  - CHANGELOG.md, daemon/__init__.py, pyproject.toml, tests/test_api.py, uv.lock (scripts/bump_version.py patch → v0.31.33, commit acddb37d7)
  - .agents/shared/planning/sonic-team/wc-watchdog-fp/HANDOFF.md (this file)

## What changed

The WC watchdog's wedge pass now recognizes ALL legitimate wake sources before flagging, curing both recorded false-positive shapes: (a) event-watch roots (armed watch_mission/job-event rows, no PROCESS_REPORT carrier, direct children terminal) and (b) parked-cascade roots (direct children terminal, grandchildren non-terminal — the direct-vs-descendants census disagreement). The gate is tri-state per house R1 discipline: True (any of 5 legs) suppresses the notice AND clears the wedge episode with one INFO log naming the holding leg; None (a wired read raised) suppresses WITHOUT clearing (fail-closed on unknown); False proceeds to the unchanged carrier gate. Legs: (1) any live PENDING/RUNNING delivery task of any kind; (2) armed job-watch whose watched receipt can still fire (terminal receipt = stranded watcher = NOT a wake source — the watcher-loss true-wedge family stays detectable); (3) open PENDING/DEFERRED report-injection obligations (RDRS armed-post-restart callbacks); (4) resumable descendants via canonical `evaluate_mission_live(skip_root=True, bus_pending_count=fulfillable only)`; (5) unsettled mission_commissions over the permanent tree (RDRS lane-7 semantics; watchdog defers to lane-7 TTL). Unwired leg reads False (all pre-existing unwired fixtures unchanged — zero existing wedge-test expectation changes, byte-identical results proven by M1). Detection power preserved: true-wedge legs still fire; proven by mutation pins both directions. The §9.7 v0.26.4-era "watchdog false positives + heartbeat-on-turn-progress" commission: wake-source recognition NEVER landed before — this release SUBSUMES it; "heartbeat-on-turn-progress" remains a named open follow-up (out of scope; the guard's member-alive companion leg covers mid-turn staleness via task rows).

## What is UNTESTED

- Live-PostgreSQL E2E (all module + integration runs on in-memory SQLite; no PG-specific SQL added but PG not exercised — M1 disclosure carried forward).
- api.py lifespan boot with real watcher-repo wiring (needs PG + agents; construction-site change is import-smoked and constructor-arg-typed only).
- Leg-2's task-backed `get_by_work_id` fallback and the mirror-row dual-terminal branch are covered only by M1's throwaway 11/11 self-check, not by committed pins (M2's committed pack pins the armed-watch legs via the primary seam).
- Live daemon observation of the four recorded notices' non-recurrence (staged release; Emperor ceremony + live soak would supply it).

## Known risks

- Leg 1 widening: ANY live PENDING/RUNNING task row of any kind now suppresses the wedge notice — a stale never-claimed PENDING row can keep the wedge quiet indefinitely (brief-specified; EligiblePendingSweepService remains the true lost-wake authority).
- Mixed watcher sets: a parent with BOTH fulfillable and unfulfillable bus watchers is now suppressed (the fulfillable one will wake it) — the S4 subject arm stops notifying for mixed sets (brief-specified).
- Leg-2 receipt-terminality rule is mirrored locally from work_resolver._job_to_record (JobItem has no `status` column since Phase 5) — can drift if the resolver changes.
- The incident's exact watch (task-kind JobItem, admission done/terminal_reason=completed) reads STRANDED under leg 2 — that shape is cured via leg 5 (mission_commissions=2 outstanding on root 77ddcb9f) / leg 3 / leg 1, pinned generically.
- Per-tick cost: ~4-6 extra DB reads + 2 tree walks per in-scope WC parent per hourly tick (small set; acceptable).
- Pre-existing RED, NOT introduced and proven identical at base (anomaly fb9916a0, filed by M1): tests/unit/services/test_vgap_episode_hygiene.py::TestWedgeEpisodeEndPurges::test_wedge_entry_discarded_on_parent_left_wc (pins a removed `departed_for_wedge` purge; TypeError 'float' object is not iterable).
- Merge/integration seams: none — both merges were conflict-free (disjoint file sets); the one integration finding (M2 leg-3 mock targeted `count_outstanding_for_subtree` while the implementation probes `has_open_obligation_for_parent`) was bounced to M2, fixed at 3231bdfca, and re-verified 17/17 on the merged tree.

## How to verify

On the MERGED tree (integration worktree or a checkout of v0.31.33):
1. `.venv/bin/python -m pytest tests/unit/services/test_waiting_children_watchdog_wake_sources.py -q` → 17 passed (both FP shapes suppressed; true-wedge legs fire; preservation pins).
2. `.venv/bin/python -m pytest tests/unit/services/test_waiting_children_watchdog.py tests/unit/services/test_b3_watchdog_escalation.py tests/unit/services/test_w_b_watchdog_liveness_gate.py -q` → 81 passed (zero existing-expectation changes).
3. `.venv/bin/python -m pytest tests/job_queue/test_mission_live_guard.py tests/job_queue/test_mission_terminal_commission_pins.py tests/job_queue/test_watcher_loss_terminal_strand.py tests/job_queue/test_watcher_loss_terminal_strand_gap.py tests/job_queue/test_pattern_f_mission_live_gate.py -q` → 78 passed (adjacent authority surfaces).
4. Mutation both directions (throwaway plugin, e.g. pytest_configure patching `WaitingChildrenWatchdog._has_legitimate_wake_source` to always-True): → exactly 9 failed (detection power); patching `_leg_armed_job_watch` to False → 2 failed (leg non-vacuity; M2's matrix additionally proves leg1 1F / leg3 1F / leg4 3F / leg5 1F).
5. `.venv/bin/python -m pytest tests/test_api.py -k test_health_check -q` → version 0.31.33.
Pre-fix base evidence (BEFORE): the same pack at 8a1045c02 → 8 failed / 9 passed — the 8 RED are exactly the suppression pins (both false-positive shapes + tri-state legs fire the notice = the defect reproduced; reference: the four notices on root 77ddcb9f).

## Bash-raises used

none (all lane commands ≤600s; longest single command was a 235s pytest batch)

## Complexity self-assessment

medium — one detector file + 3 repo/wiring surfaces + one behavioral pin pack; no schema migrations, no new threads, no new config knobs; the weight was polarity discipline (tri-state fail-closed, skip_root, fulfillable-only bus count, stranded-watcher exclusion) and mutation-verified detection power. No handover override — verdict medium throughout, autonomous through merge + stage per commission.
