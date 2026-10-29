# G3.5 dual-leg re-proof — HEAD re-verification (whole default-collected corpus)

**Date**: 2026-10-29 · **Leg**: HEAD side, merged gate tree
**Tree**: merge `3d835d8ddae258fb6769b08be8c056dd8a72b7d4` (= `148a7b30d` + lane fix `441a1657d` "F4-A non-QueuePool shape guards")
**Worktree**: /home/nea/ensemble-src-wt-tdbp-rehead (detached @ 3d835d8dd, porcelain clean at start)
**Method**: identical to authoritative attempt-2 (fence: per-var `env -u` POSTGRES_×5 + DAEMON_PORT, `HOME=$(mktemp -d)`, `ENSEMBLE_SELF_ENV=dev`, certified interpreter /home/nea/ensemble-src/.venv/bin/python, `-q --tb=no -rfE --timeout=120`, `timeout 1500` per chunk, house tally-regex completion detection)

## Census
`31084/32158 tests collected (1074 deselected)` — EXACT match to expectation (fix touched no test files).

## Execution
- 78 chunks × ~400 ids (mapfile + quoted array), sequential, zero DIED / zero splits / zero re-runs
- Driver wall: 2026-10-29T12:02:38Z → 13:58:34Z = **1h55m56s** (sum of chunk walls 6,954 s)
- **Totals: 30,436 passed / 319 failed / 27 errors / 296 skipped / 6 xfailed / 0 xpassed / 0 deselected**
- **Core sum = 31,084 = corpus size ✓**
- Arithmetic vs attempt-2 (30,425/330/27/296/6): exactly **+11 passes / −11 failures** — the fix's fingerprint

## FAILED+ERROR set
- Raw short-summary lines: 346 (`failed.rehead.txt`, sha256 `b606dd7059557bafe750fa7546860082d5954da202acafd3a72e1d312502d874`)
- Sorted-dedup raw: 346 (`failed.rehead.sorted.txt`, sha256 `543c8ce8f74d3fd1c5b3602fafee3b2e89b17c1a6197f5fea7a04759579a1f10`)
- Bare normalized ids (prefix + " - reason" stripped, validated ⊆ collected corpus 346/346): `rehead.ids`, sha256 `d54c365b4cf83c8b973dc032e1fe2e9c03f64e2bf318cafda8547d7c276d529c`
- collect.rehead.txt sha256 `d34d11fc4c6b3ad4d0fb4d096c9c2d15348db0ead3e8d0d393ed771a552e2c39`

## Byte-comm vs standing base (failed.base.txt, 346 bare ids)
- Shared: 344
- **HEAD-only (2)** — both classified class-a order/load artifacts (see legs):
  1. `tests/message_queue_redesign/test_atomic_status_transitions.py::TestUpdateActivityAtomic::test_update_activity_concurrent_does_not_clobber_terminal_status` — the disclosed sibling-roulette family firing on a different sibling; isolated legs: rehead 3/3 PASS, base 2/3 PASS + 1 FAIL → flaky BOTH sides, no HEAD-vs-BASE divergence → NOT a regression
  2. `tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` — isolated legs: rehead 3/3 PASS, base 3/3 PASS → green isolated everywhere, corpus-load-only flake → NOT a regression
- **BASE-only (2)** — both members of the previously-disclosed base-only 4:
  1. `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met`
  2. `tests/unit/test_ari_user_ruling_wake_semantics.py::TestTheRealScheduleRow::test_a_daily_alarm_is_also_reuse_instance`
  (the other 2 disclosed base-only ids — `test_lcancheck_family_separation.py::…s3_whole_tree_census`, `test_scheduler_deferred_mechanism_probe.py::…p2_pending_task_is_blocked_then_claimed_after_free` — stayed red at rehead too → shared this run; roulette both directions, disclosed class)
- The 11 attempt-2 net-new ids: **NONE in the failed set — all GREEN** (see fixed11.green.log: 11 passed in 5.50s, rc=0, per-id PASSED lines)
- The 4 disclosed order/load ids: all green this run

## Excluded specials (excluded_specials.rehead.txt)
- `tests/packs/g7_unique_index_smoke_test.py`: rc=3, INTERNALERROR sys.exit(0), "no tests ran in 0.52s" — as expected
- `tests/e2e/test_context_injection_hybrid.py`: rc=2, collection ERROR requests.exceptions.ConnectionError (:8079 refused) — as expected

## Verdict
**G3.5 HEAD re-proof PASSES.** The 11 net-new failures of attempt-2 are gone (green, cited); comm residue is fully classified class-a (2 HEAD-only order/load flakes — one the already-disclosed atomic sibling-roulette family, one base-green-isolated dependency-bus flake; 2 base-only disclosed); no true net-new regression on the merged tree.

## Side effects
- `install-audit.jsonl`: corpus run appended **+29 rows** (374 lines) — preserved at install-audit.jsonl.preserved + diff, worktree file restored via `git checkout --`; porcelain back to clean (only untracked artifacts under this dir + collect.rehead.txt).
- Base worktree /home/nea/ensemble-src-wt-tdbp-base-r1: read-only respected (HEAD 19fc1…c8b unchanged, porcelain 0 before AND after legs).
- Nothing committed, nothing pushed; gate worktree untouched; ports 9797/8088 never used; no processes killed.
