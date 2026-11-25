# Tester Gate — self-service-promote (TIER-2 RE-SCOPE leg) — VERDICT: RED (4 NET-NEW)

**Lane:** `fix/self-service-promote` @ `ce9b45525b2a434bcf04859c289d1b6cc2103376`
**BASE leg:** `2a0e69b37a72789ce42f25099faf5cb5d7451f77` · **HEAD leg:** `ce9b45525…`
**Gate date:** 2026-11-20 · **Tier:** 2 (core seam `daemon/manager.py`) · **Budget:** 60 min hard
**This is the sanctioned RE-SCOPE** of the prior worker that breached its budget without reporting.

## VERDICT: 🔴 RED — NET-NEW = 4 (NOT EMPTY). NOT merge-ready as-is.

### Re-scope decision (disclosed reduction)
Measured serial probe: 1,244 nodes / 282.4 s = **264 nodes/min** ⇒ full-tree dual-side ≈ **299 min** (≫ 60) ⇒ RE-SCOPE to the mechanically-derived DECLARED SET (every `tests/` file importing the daemon package).
Declared set = **1,437 files / 32,333 nodes** (base ∩ head file lists; 82 % of the 39,469-node full tree). Acceleration: `xdist -n 12 --dist worksteal` within each leg (recipe §2.4).

### Coverage
Both legs executed **32,333 / 32,333 nodes** (7 shards each, identical command shape + identical node set, BASE leg first, never concurrent). Accounting closure **OK** on both legs (`sum(P+F+E+S+X) == collected`, xfailed included).

### sweep_digest_verdict.sh — exit code 1 (NET-NEW NOT EMPTY, non-vacuous)
```
FORM=grepfull: BASE 370 / HEAD 372 ids · base-only 9 · head-only 11 · both 361
  DIGEST base: ab031fc074996009e6317ba98e5d0bd6eb4f56618cbf9d21ab0be60e9e0f8cad
  DIGEST head: 2eafe460d87beae4bbe6547388fca3523ce05ffaebc4e93d9f5d4ea39fc1640f
FORM=bareid  : same counts · same digests
  VERDICT [FORM=grepfull]: NET-NEW = 11 (NOT EMPTY — the sets differ)
  VERDICT [FORM=bareid]  : NET-NEW = 11 (NOT EMPTY — the sets differ)
```
(The 11 sweep-level head-only ids are an INPUT to adjudication, not a verdict — the P3 composition artifact.)

### 3× isolated adjudication (diff nodes only, no -n, 3 runs per side)
**4 NET-NEW (head fails 3/3, base passes 3/3):**
1. `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_decision_lane_manager_drift_is_confined_to_decision_hunks`
2. `…::test_the_group_chat_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`
3. `…::test_the_memoir_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`
4. `tests/unit/tools/test_agent_restart.py::TestT8Extras::test_restart_kind_argv_lock_adoption_and_unit_prefix`

**7 NOT REPRODUCED IN ISOLATION (both clean)** — parallel-shard composition artifacts, not regressions:
`test_critical_notes_embed_pipeline::test_lazy_mint_window_mints_through_real_builder`, `test_f1_killswitch_tz_matrix::test_recovery_33p_pass_count_identical_both_states`, `test_api.py::test_get_instance_success`, `test_worker_notification.py::test_multi_worker_notification`, `test_bash_ram_guard.py::TestKillSwitch::test_switch_off_is_byte_identical_to_on`, `test_bash_ram_guard.py::TestStagedGrowerStrictUnderReport::test_the_under_report_bound_is_strictly_positive_on_a_staged_grower`, `test_defer_blocked_api.py::TestSeverityShapes::test_naive_timestamps_normalized_to_utc`.

**Base-only 9 ("FIXED at head")**: base-side 3× = 2 order/load-sensitive (base run1/run3) + 7 clean ⇒ base-side flakes, none net-new. Head-side 3× NOT re-run (HEAD worktree already removed by teardown) — disclosed limitation; irrelevant to the NET-NEW verdict.

### Root cause of the 3 P11 pins (HARD EVIDENCE)
Guard span = `ea57bdb46c370e3bc9a40b4ca87ca5f5a2a62624..HEAD` (`rev-list --first-parent --merges -1 HEAD`).
The lane's 3 commits `4cb69120b` / `2c2df283f` / `068cad67a` add **92 lines to `daemon/manager.py`** in that span, and **0** of the added lines carry a sanctioned marker (`decision` / `memoir` / `group chat`) ⇒ the 3 pins red BY DESIGN (the guard's intent: any unsanctioned manager.py drift reds).
**Fix (lane-side, test-side not at fault):** add a sanctioned marker to the lane's `daemon/manager.py` hunks, or obtain a scoped source-gc sanction for this lane (the `c81dd0dea` precedent).

### `test_agent_restart.py::TestT8Extras::test_restart_kind_argv_lock_adoption_and_unit_prefix`
The lane changed the restart unit-name derivation in `daemon/manager.py` (`_uj.UNIT_NAME_PREFIX_RESTART` / `kind == "restart"` branch + the new `identity_stamper` persistence). The T8 pin on the restart-kind argv lock / unit prefix no longer holds at HEAD.

### Quarantine check (all 4 NET-NEW ids: NOT in `.agents/tester/QUARANTINE.md`; their test FILES are byte-identical base↔head — the causality is via the lane's `daemon/manager.py`, which the guard and the T8 pin both read).

### Coverage disclosures
- The lane's **99 head-only nodes** (3 NEW test files + new nodes in 4 modified files) are absent at BASE ⇒ excluded from the identical node set; they were executed separately on HEAD (`tests/unit/test_watchdog_hold_event_kinds.py`, `test_watchdog_probe_contract.py`, `tests/unit/services/test_executor_reaper_classifier.py`, `tests/test_bind_retry.py`, `tests/unit/tools/test_upgrade_tools.py`, `…preflight_daemon_side.py`, `…serving_green_closer.py`).
- The lane's **shell/pack surfaces** (`scripts/upgrade/*.sh`, `tests/packs/*.sh`, `tests/test_*.sh`, `tests/helpers/preflight_mutation_apply.py`) are **not pytest nodes** ⇒ NOT covered by this leg (they run via the registered pack lanes).
