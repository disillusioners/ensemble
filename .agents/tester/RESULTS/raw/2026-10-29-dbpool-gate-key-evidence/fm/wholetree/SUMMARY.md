# FM4 — WHOLE-TREE LEG at the MERGED TREE (6947cc2b8), union-judged vs KNOWN-RED UNION with 3×-isolation at 69d767b2a

**Date**: 2026-10-29 · **Leg**: FM4 whole-tree, merged tree
**Tree A (merged)**: `6947cc2b862e24481e0ce14ac82b5f0744aca3a5` (tester/dbpool-functional-gate tip; merge `2655d7d67` + docs `6947cc2b8`)
**Tree B (release / origin/latest)**: `69d767b2aa6c86981a8a34966f99ed432ca4fcd4`
**Method**: method-identical to the base leg (/tmp/tdbp-wtbase/driver.sh) and rehead attempt-2 (driver2.sh): house `--ignore` ×2 at census; per-var `env -u POSTGRES_×5 + DAEMON_PORT`; `ENSEMBLE_SELF_ENV=dev`; `HOME=$(mktemp -d)`; certified interpreter `/home/nea/ensemble-src/.venv/bin/python`; `-q --tb=no -rfE --timeout=120`; `timeout 1500` per chunk; ~400-id chunks via mapfile+quoted arrays; strictly sequential; ONE pytest at a time.

## 1. Census (STOP gate) — PASS
`31150/32259 tests collected (1109 deselected) in 82.27s` — **EXACT match** to the expectation recorded at merge time (expected-collected.md merged-tree reconciliation). Id list byte-faithful (`grep -E "^tests/.*::"` on collect output — re-verified to reproduce the base leg's `ids.base.txt` byte-exactly before use).
- `collect.fmwt.txt` sha256 `f4901292f67a6c4eb72d1baeaadf96a0b22fb5a9a63e4bac2b5dc0420bfaed36`
- Census command: `pytest --collect-only -q --ignore=tests/packs/g7_unique_index_smoke_test.py --ignore=tests/e2e/test_context_injection_hybrid.py` (house recipe; same as base/rehead).

## 2. Execution
- 78 chunks × ~400 ids (mapfile + quoted array), sequential. **Zero DEAD / zero splits / zero re-runs / zero UNRESOLVED.**
- Driver wall: 2026-10-29T14:51:02Z → 16:49:46Z = **7,124 s = 1h58m44s** (sum of chunk walls 6,929 s; chunk_000 outlier 962 s — one slow test cluster, session summary present, counted as evidence).
- **Totals: 30,505 passed / 316 failed / 27 errors / 296 skipped / 6 xfailed / 0 xpassed**
- **Sum = 31,150 = corpus size ✓ (delta 0)**

## 3. FAILED+ERROR set
- Unique ids: **343** (316 FAILED + 27 ERROR); extraction unparsed = 0 (byte-compatible longest-prefix extractor vs chunk id lists).
- `failed.fmwt.txt` (bare sorted dedup ids) sha256 **`2e97872fe44372b3e543fe76068c8816728c9b1942b9ba98bac2e8eb70cae76d`**

## 4. THE UNION JUDGMENT — **LANE-CAUSED = 0**
Known-red union = 346 base ids (`wtbase/failed.base.txt`) ∪ 15 attempt-2 HEAD-only ledger ids ∪ 2 rehead-leg extra ids (`test_update_activity_concurrent_does_not_clobber_terminal_status`, `test_per_parent_lock_serializes_db_insert`) = **363 ids** (`known_red_union.txt`).
- Shared with union: 341 · **HEAD-only unknown: 2** · base-only-gone: 22.

| unknown id | A corpus (6947cc2b8) | A isolated | B isolated (69d767b2a) | verdict |
|---|---|---|---|---|
| `tests/message_queue_redesign/test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_with_instance_filter_under_concurrency` | RED (1 corpus occurrence, chunk_008) | 9/10 PASS, 1 FAIL — `sqlite3.OperationalError: cannot commit transaction - SQL statements in progress` in a worker thread (PytestUnhandledThreadExceptionWarning) | **25/35 PASS, 10 FAIL** — identical SQLite thread-race signatures (`cannot commit transaction - SQL statements in progress` / `cannot start a transaction within a transaction`) | **NOT LANE-CAUSED — flaky BOTH sides** (disclosed sibling-roulette class: same file family as `test_atomic_status_transitions`, added by the same `d589d360b` concurrency-remediation commit; test file + `daemon/repositories/message_queue/*` both UNTOUCHED by the lane delta — `git log 69d767b2a..6947cc2b8 -- <paths>` empty; whole-file 22/22 green at BOTH trees). Recommend adding to the disclosed flake ledger for future legs. |
| `tests/test_resource_monitor_platform_fallback.py::TestSelfThrottleLatch::test_the_throttle_latch_is_released_when_the_ema_recovers` | RED (chunk_022) | 0/3 PASS (deterministic red) | **0/3 PASS (deterministic red)** | **INHERITED — the release's own** → staged-verification watch list. Verbatim: `assert sampler.counters.self_throttled is True` → `AssertionError: assert False is True` with log `no daemon snapshot (platform read failed: proc-root-unreadable, read_ms=500.0)`; counters show `ticks=0, ticks_skipped=6, missed_samples=6, self_throttled=False`. Attribution: file ABSENT at base 19fc11c8b, added by `53547b915` (resource-monitor dark-tier restore), present at 69d767b2a; lane delta touches it ZERO (`git log 69d767b2a..6947cc2b8 -- <file>` empty). Deterministic red at the release itself in this environment. |

Attribution decomposition for release-delta (new) files: both unknown ids' files pre-date the lane (not in `69d767b2a..6947cc2b8`); `test_resource_monitor_platform_fallback.py` IS a release-delta file vs base (one of the +175); `test_atomic_dequeue.py` predates base. Of the release's +175 new tests, exactly **one** red (the throttle-latch id) → INHERITED at B; the other 174 passed.

## 5. The 11 regression ids — ALL GREEN (double-cited)
Corpus: none of the 11 appear in `failed.fmwt.txt`. Bounded probe (same fence, `-v`): rc=0, `11 passed, 76 warnings in 5.85s` with per-id PASSED lines (regression11.green.log): `test_promote_commits_with_busy_daemon`, `test_stale_while_continuously_up_still_degrades`, and all 9 `tests/test_health_probes.py` ids (`test_amnesty_epoch_boundary_is_inclusive`, `test_amnesty_mixed_set_max_uses_only_post_boot_beats`, `test_amnesty_pre_boot_beat_is_invisible_to_freshness`, `test_composite_carries_inflight_advisory_without_affecting_status`, `test_inflight_turns_counts_fresh_post_boot_beats_only`, `test_inflight_turns_zero_on_all_frozen_and_none_without_threshold`, `test_periodic_refresh_loop_applies_drill_knob_between_ticks`, `test_periodic_refresh_loop_populates_state_and_stops`, `test_signal_preservation_post_boot_stale_still_degrades`).

## 6. Base-only-gone (22; in the 346/base-union, GREEN at the merged tree) — list only, no action
- 11 = the lane's fixed regression ids (§5).
- 9 = disclosed flake/order-load ledger ids, green this run: `test_complete_concurrent_double_call_only_one_succeeds`, `test_update_activity_concurrent_does_not_clobber_terminal_status`, `test_public_path_p99_under_threshold`, `test_multi_worker_notification`, `test_real_spawn_env_and_process_group_independence`, `test_per_parent_lock_serializes_db_insert`, `s3_whole_tree_census`, `test_ab_resolution_threshold_met`, `test_a_daily_alarm_is_also_reuse_instance`.
- 2 = base reds fixed by the RELEASE delta (red at base AND rehead, green at merged): `tests/test_phase8_docs_gate.py::test_changelog_has_unreleased_section_naming_commission`, `tests/test_resource_monitor_storage.py::TestFlusherBatching::test_window_writes_raw_minute_hour` (the WS-A resource-monitor-flusher-fix).
- (4th disclosed base-only id `test_p2_pending_task_is_blocked_then_claimed_after_free` stayed red this run — shared, disclosed roulette class.)

## 7. Excluded specials (excluded_specials.fmwt.txt) — as expected
- `tests/packs/g7_unique_index_smoke_test.py` → INTERNALERROR `sys.exit(0)` at import, "no tests ran", **pytest_exit=3** (the pack's own runner prints `RESULT: PASS / Tests run: 3 | Passed: 3`).
- `tests/e2e/test_context_injection_hybrid.py` → collection ERROR `requests.exceptions.ConnectionError` (:8079 refused), **pytest_exit=2**.

## 8. Re-runs / kills
NONE — no chunk died (zero splits); nothing re-run beyond the mandated isolation matrices.

## 9. Side effects & fences
- `install-audit.jsonl` (fmwt): corpus appended **+31 rows** (374→377 lines… file restored to tracked 346-line state) — preserved at `install-audit.fmwt.preserved.jsonl` (+ `install-audit.fmwt.appended31.jsonl`, `install-audit.side-effect.diff`), worktree file restored via `git checkout --`; fmwt porcelain CLEAN after.
- fmbase (worktree B): porcelain CLEAN throughout; install-audit untouched (346 lines).
- NOTHING committed, NOTHING pushed; gate worktree and all other worktrees untouched; ports 9797/8088 never used; no processes killed. Worktrees A/B remain in place (detached @ their HEADs).

## VERDICT
**FM4 WHOLE-TREE LEG PASSES: net-new LANE-CAUSED reds = 0.** Census exact; 31,150/31,150 accounted; 343 unique FAILED+ERROR, all either in the known-red union (341) or classified NOT-LANE-CAUSED (1 both-sides flake of the disclosed family) / INHERITED (1 deterministic release red → staged-verification watch list). The 11 regression ids green (cited).
