# NET-NEW RED CLASSIFICATION — dbpool-idle-txn functional gate G3.5 (2026-10-29)

Comm source: /home/nea/ensemble-src-wt-tdbp-gate/.agents/tester/RESULTS/raw/2026-10-29-dbpool-wholetree-head/comm_vs_base.txt
(== /tmp/tdbp-wthead-120/comm_vs_base.txt, diff-verified IDENTICAL)
HEAD leg = /tmp/tdbp-wthead-120 (78 chunks, timeout=120, fence-matched to base leg).

Worktrees (pre-run verification):
- HEAD: /home/nea/ensemble-src-wt-tdbp-mut1 @ c89e3470c64756f6df989a2868ac9d68b484980e, porcelain = only `?? .agents/tester/RESULTS/raw/`
- BASE: /home/nea/ensemble-src-wt-tdbp-base-r1 @ 19fc11c8b52239813fd91327ee0c1961c5b6fc87, porcelain clean

Fence used for EVERY run (matches gate driver2.sh verbatim):
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" ENSEMBLE_SELF_ENV=dev /home/nea/ensemble-src/.venv/bin/python -m pytest "<id>" -q --tb=short --timeout=120

Scratch PG: NOT CREATED — zero of the 19 ids required PG (all failures/passers resolved at unit level; no PG_TEST_* exported; nothing to tear down).

## ROOT CAUSE (shared by all 11 net-new reds) — verbatim minimal evidence

AttributeError: 'StaticPool' object has no attribute 'checkedout'
  daemon/services/readiness.py:1512  in _refuse_pool_admission_at_capacity
      if pool.checkedout() >= MANAGER_POOL_CONFIGURED_CAPACITY:
  called from daemon/services/readiness.py:1841 (_probe, the queue-probe path)
  caught-and-warned at daemon/services/readiness.py:1314 ("Readiness probe failed: ...")
Secondary same-family form: "'FakeEngine' object has no attribute 'url'" at readiness.py:1555/:1849
  (engine.url.get_backend_name() on test fake engines).
Mechanism: the lane's new pool-admission pre-check guards getattr(engine,"pool",None) but calls
pool.checkedout() unconditionally — SQLAlchemy StaticPool (sqlite test engines) and test FakeEngine
objects lack .checkedout()/.url. All 11 reds' tracebacks terminate in readiness.py lane code.

Minimal repro (deterministic, <1s):
  cd /home/nea/ensemble-src-wt-tdbp-mut1 && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
  -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" ENSEMBLE_SELF_ENV=dev \
  /home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/test_health_probes.py::test_amnesty_epoch_boundary_is_inclusive" -q --tb=short --timeout=120

## CLASSIFICATION TABLE (15 HEAD-only)

| # | id | failure class (from isolated tb / chunk logs) | HEAD iso ×3 | BASE iso ×3 | lane-touched correlation | verdict |
|---|----|----|----|----|----|----|
| 1 | tests/test_health_probes.py::test_amnesty_epoch_boundary_is_inclusive | AttributeError StaticPool.checkedout @ readiness.py:1512 (assert-crash) | F/F/F | P/P/P | imports LANE-TOUCHED daemon/services/readiness.py | b NET-NEW |
| 2 | tests/test_health_probes.py::test_amnesty_mixed_set_max_uses_only_post_boot_beats | same AttributeError seam | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 3 | tests/test_health_probes.py::test_amnesty_pre_boot_beat_is_invisible_to_freshness | same AttributeError seam | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 4 | tests/test_health_probes.py::test_composite_carries_inflight_advisory_without_affecting_status | probe-warning @1314 → database=False → AssertionError | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 5 | tests/test_health_probes.py::test_inflight_turns_counts_fresh_post_boot_beats_only | same family | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 6 | tests/test_health_probes.py::test_inflight_turns_zero_on_all_frozen_and_none_without_threshold | same family | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 7 | tests/test_health_probes.py::test_periodic_refresh_loop_applies_drill_knob_between_ticks | 'FakeEngine' has no 'url' @ readiness.py:1555/:1849 → database=False → wait_for timeout AssertionError | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 8 | tests/test_health_probes.py::test_periodic_refresh_loop_populates_state_and_stops | same FakeEngine.url family → ready False | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 9 | tests/test_health_probes.py::test_signal_preservation_post_boot_stale_still_degrades | same family | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 10 | tests/e2e/.../test_promote_commits_with_busy_daemon | make_queue_probe(engine) → readiness.py:1841→:1512 AttributeError (direct, assert-crash) | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 11 | tests/e2e/.../test_stale_while_continuously_up_still_degrades | probe fail @1314 (StaticPool.checkedout) → composite shape changed → AssertionError queue_freshness | F/F/F | P/P/P | readiness.py lane-touched | b NET-NEW |
| 12 | tests/message_queue_redesign/...::test_complete_concurrent_double_call_only_one_succeeds | chunk-only; gate leg --tb=no (no tb); id NOT red in in-chunk re-run — the SAME file's sibling concurrency test flipped into the fail slot instead (roulette) | P/P/P | n/a (green at base chunked) | none (file+subject not lane-touched) | a ORDER/LOAD |
| 13 | tests/perf/...::test_public_path_p99_under_threshold | perf p99 threshold; load-sensitive by construction; chunk --tb=no | P/P/P | n/a | none | a ORDER/LOAD (in-chunk repro SKIPPED: owning chunk_010 = 719s > 5-min bound) |
| 14 | tests/test_worker_notification.py::...::test_multi_worker_notification | chunk-only; REPRODUCES in-chunk at HEAD (chunk_027 re-run: 14/14 identical fail set incl. this id) yet 3/3 green isolated | P/P/P | n/a | none | a ORDER/LOAD (reproduces only under chunked/co-resident execution) |
| 15 | tests/unit/tools/test_upgrade_journal.py::...::test_real_spawn_env_and_process_group_independence | chunk-only one-off; in-chunk re-run of chunk_076: 0 failed (did NOT reproduce; 1-for-2 chunk runs) + 3/3 green isolated | P/P/P | n/a | none | a ORDER/LOAD |

## BASE-ONLY 4

| # | id | BASE iso ×3 | HEAD iso ×3 | verdict |
|---|----|----|----|----|
| 16 | tests/integration/test_lcancheck_family_separation.py::...::test_s3_whole_tree_census | P/P/P | P/P/P | flaky-at-base (base-leg chunk red was order/load; NOT lane-fixed) |
| 17 | tests/services/test_skill_evolution_service.py::...::test_ab_resolution_threshold_met | P/P/P | P/P/P | flaky-at-base |
| 18 | tests/unit/test_ari_user_ruling_wake_semantics.py::...::test_a_daily_alarm_is_also_reuse_instance | P/P/P | P/P/P | flaky-at-base |
| 19 | tests/unit/test_scheduler_deferred_mechanism_probe.py::...::test_p2_pending_task_is_blocked_then_claimed_after_free | F/F/F (assertion: captured claim SQL lacks per-instance RUNNING-task gate / 'status_running_guard' — positional ? params captured instead) | F/F/F (IDENTICAL assertion) | NOT lane-fixed, NOT an improvement: the comm's "green at HEAD" was a co-residency-induced PASS in the head chunk; isolated it is red at BOTH revs with the identical assertion. Asserted code daemon/repositories/task/repository.py is NOT lane-touched ⇒ pre-existing context-dependent test, red-by-default isolated on both sides. Disclosed as order/context-dependent; the BASE-only comm entry does not survive isolation on either side. |

## AGGREGATES
- NET-NEW REGRESSIONS (b) = 11 — ids 1–11, ONE shared root cause (readiness.py StaticPool/FakeEngine pool-admission seam), gate-blocking
- ORDER/LOAD-ARTIFACTS (a) = 4 — ids 12–15 (12: roulette-flip proven; 14: deterministic in-chunk reproduction; 15: one-off, not reproduced; 13: skipped in-chunk by cost rule)
- FLAKY (c, mixed isolated) = 0
- ENVIRONMENT (d) = 0 (no connection-refused/daemon-absent class among the 19)
- LANE-FIXED (base-only) = 0 — 3 of 4 base-only ids are flaky-at-base (green isolated at base); the 4th (id 19) is red-by-default at BOTH revs

## IN-CHUNK RE-RUN EVIDENCE (optional step, only <5-min chunks)
- chunk_008 @ HEAD re-run: rc=1, 14 failed/386 passed in 96.68s; fail-set diff vs gate leg = exactly 1 swap:
  TestCompleteAtomic::test_complete_concurrent_double_call_only_one_succeeds (gate) ↔ TestUpdateActivityAtomic::test_update_activity_concurrent_does_not_clobber_terminal_status (re-run)
- chunk_027 @ HEAD re-run: rc=1, 14 failed/374 passed/12 skipped in 103.50s; fail-set IDENTICAL to gate leg (14/14) incl. test_multi_worker_notification
- chunk_076 @ HEAD re-run: rc=0, 399 passed/1 skipped in 36.89s (0 failed; gate leg had 1: real_spawn_env) — one-off
- chunk_010 @ HEAD: NOT re-run (719s > 5-min budget per method)

## RULES APPLIED
- (b): 3/3 RED isolated at HEAD + 3/3 GREEN isolated at BASE (ids 1–11 all satisfied this deterministically, 0.3–8.1s each).
- (a): 3/3 GREEN isolated at HEAD (ids 12–15); disclosed with in-chunk evidence where cheap.
- base-only: isolated ×3 per side; lane-fixed requires red-at-base + green-at-head isolated — none qualified.

## TEARDOWN + FINAL HYGIENE
- Scratch PG: never created (no id needed PG) — nothing to tear down. No PG_TEST_* exported at any point.
- install-audit.jsonl side effect: did NOT appear in either worktree (both porcelain clean of it).
- Final porcelain: mut1 = `?? .agents/tester/RESULTS/raw/` only; base-r1 = CLEAN (empty).
- No fixes, skips, quarantines, or edits performed. Zero commits. Zero live contact (fence on every command; per-variable env -u).
