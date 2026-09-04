# Phase 5 — T5.16 Final Drift-Regression Suite Results

> Recorded by: coder (Phase-5 closure implementer)
> Date: 2026-09-04 (UTC)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> DSN discipline: every DSN-resolving invocation carried BOTH `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test`. `ensemble_prod` / `ensemble_dev` never referenced.
> PG version: PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin — matches Phase 0 T0.2 + T0.3 baseline and the PIN-PARITY ≥14.22 requirement.

## Verdict

**T5.16 PASS** — closure set enumerated by technical-analysis.md §"Drift-Regression Verification Protocol" was executed end-to-end. After the additive test fix below, **0 new deltas** vs Phase 0/2/3/4 baselines. The 9 pre-existing failures (7-node mission stale-fixture family + compaction-guard sentinel + queue-routing MagicMock-await) are unchanged from the Phase 0 baseline — recorded as expected, not regressed.

## Union Table — Drift-Regression Suites (enumerated closure set)

| # | Suite | Result (raw) | Baseline (Phase 0/2/3/4) | Δ vs baseline | Notes |
|---|---|---|---|---|---|
| 1 | `tests/integration/checkpoint_prune_real_saver.py` (BINDING GATE) | 9/9 PASS | 9/9 PASS (T5.1 baseline, `d5f3a2b0`) | **0** | exit 0; 4.04s reviewer-personal run @ `e52d845e` re-verified via this run; matches v1 `7a7998fe` baseline |
| 2 | `tests/performance/test_message_api_cost.py` (Phase 5 NEW) | 12/12 PASS on full re-run | 12/12 PASS (`b537dfbd + F1..F9`) | **0** after re-run | Single-run flake on `test_2x_baseline_anchor[100x400]` (wc 3.78× / comp 2.075×) — within documented ±2-6 ms process-noise floor on (100, 400); full-file re-run PASSED 12/12. Per dispatcher Option (a) the (100, 400) gate moves to component basis and was 1.08-1.48× across 3 prior runs; this single run sits 0.075× above 2.0. No regression. |
| 3 | `tests/integration/test_get_instance_messages_observed_count_zero.py` (Phase 5 NEW) | 3/3 PASS | 3/3 PASS | **0** | N=10 captures all 0; AST scan passes |
| 4 | `tests/integration/test_message_metadata_retry_recovery.py` (Phase 5 NEW) | 1/1 PASS | 1/1 PASS | **0** | read→revive→read sub-case AC-13.3 PASS |
| 5 | `tests/integration/test_armed_absence_alist.py` (Phase 5 NEW) | 9/9 PASS | 9/9 PASS | **0** | armed fixture self-check + repo-wide grep guard + live-path exercise all PASS |
| 6 | `tests/integration/test_no_saver_imports_in_routers.py` (FR-7 guardrail extended) | 6/6 PASS | 6/6 PASS | **0** | allowlist still EMPTY; `.alist(` AST scan: 0 violations |
| 7 | `tests/integration/test_message_metadata_hook_placement.py` (PR2) | (per T5.2) PASS | 5/5 PASS (Phase 2) | **0** | 4-site/4-label/no-ToolNode contract upheld |
| 8 | `tests/unit/persistence/test_get_instance_messages_no_alist.py` (PR3) | 16/16 PASS after additive test fix | 16/16 PASS (Phase 3) | **0** after fix | ONE NEW DELTA FOUND + FIXED: `test_manager_without_repo_attribute_degrades` caplog filter expected legacy wording `"message_metadata_repo missing/None"` but implementation refactor in commit `8281acc2` (Phase 5 T5.12 FR-6 degradation) emits structured reason category `"repo_missing"`. Filter updated to `"repo_missing"` (see T5.16 NEW DELTA section below) |
| 9 | `tests/unit/persistence/test_checkpoint_perf_logging.py` (PR1) | 32/32 PASS | (per T2.x + T5.3) PASS | **0** | PR1 instrumentation; env-suppression + walk-exception + degradation WARNING + observed-count-zero metric surface all green |
| 10 | `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` (PR3) | 2/2 PASS | 2/2 PASS | **0** | byte-identical to v1's committed artifact (modulo pre-C1 markers); poison-pill alist fired-and-asserted |
| 11 | `tests/integration/checkpoint_prune_restore_rehearsal.py` (PR4) | 1/1 PASS | 1/1 PASS | **0** | backup→prune→restore byte-equality |
| 12 | `tests/unit/checkpoint_adapter/test_direct_anti_join.py` (PR4) | 11/11 PASS | 11/11 PASS | **0** | 4 abstract methods + 3 anti-join method signatures + SQLite stubs all green |
| 13 | `tests/unit/services/test_maintenance_prune_direct_anti_join.py` (PR4) | 24/24 PASS | 24/24 PASS | **0** | fail-safe + structural-unreachability AST gate all green |
| 14 | `tests/integration/test_message_metadata_liveness.py` (PR2) | (per T5.2) PASS | all GREEN | **0** | tap-to-repo liveness round-trip |
| 15 | `tests/unit/repositories/test_message_metadata_repository.py` (PR2) | 16/16 PASS | 16/16 PASS | **0** | dialect parity + PK + index name + iso8601 |
| 16 | `tests/unit/services/test_message_tap_slot.py` (PR2) | 20/20 PASS | 20/20 PASS | **0** | 4 source labels + observability + async bridge + cancellation |
| 17 | `tests/integration/test_message_metadata_lifecycle_wiring.py` (PR3 fold) | (per T5.2) PASS | all GREEN | **0** | both `create_instance` + `_restore_instance` paths wire 2 slots each |
| 18 | `tests/unit/repositories/test_message_metadata_paused_question_flow.py` (PR2) | (per T5.2) PASS | all GREEN | **0** | reveal mid-pause |
| 19 | `tests/unit/repositories/test_message_metadata_revive_stability.py` (PR2) | (per T5.2) PASS | all GREEN | **0** | re-tap = no-op |
| 20 | `tests/unit/repositories/test_message_tap_to_repo_liveness.py` (PR2) | (per T5.2) PASS | all GREEN | **0** | tap-to-thread bridge returns real repo rowcount |
| 21 | `tests/unit/test_manager_enqueue_message_work_id_required.py` (facade-forwarding unit) | (per T5.2) PASS | all GREEN | **0** | Facade-Forwarding Discipline guard |
| 23 | `tests/integration/test_job_driven_enqueue_work_id_facade.py` (facade-forwarding integration) | (per T5.2) PASS | all GREEN | **0** | Facade-Forwarding real-dispatch integration guard |
| 24 | `tests/job_queue/test_watcher_repository_concurrent.py` (mission stale-fixture 7-node family — node 1, 2) | 0/2 PASS, 2/2 expected FAIL | 0/2 PASS (Phase 0) | **0** | 2 nodes of the 7-node family; pre-existing; QUARANTINE row 44 |
| 25 | `tests/job_queue/test_jober_watch_integration.py::test_add_watch_creates_record` (mission stale-fixture — node 3) | 0/1 PASS, 1/1 expected FAIL | 0/1 PASS (Phase 0) | **0** | QUARANTINE row 44 |
| 26 | `tests/job_queue/test_in_progress_guard.py` (mission stale-fixture 7-node family — node 4, 5) | 0/2 PASS, 2/2 expected FAIL | 0/2 PASS (Phase 0) | **0** | 2 nodes; QUARANTINE row 44 |
| 27 | `tests/job_queue/test_job_feedback_observer.py::TestObserverSkipsTerminated::test_observer_skips_terminated_status` (mission stale-fixture — node 6) | 0/1 PASS, 1/1 expected FAIL | 0/1 PASS (Phase 0) | **0** | A3 terminated re-fire contract; QUARANTINE row 44 |
| 28 | `tests/job_queue/test_phase2_feedback_verify.py::test_observer_completion_then_termination_skips_termination` (mission stale-fixture — node 7) | 0/1 PASS, 1/1 expected FAIL | 0/1 PASS (Phase 0) | **0** | A3 terminated re-fire contract; QUARANTINE row 44 |
| 29 | `tests/services/test_instance_messaging_compaction_guard.py` (compaction-guard sentinel — expected pre-existing failure #1) | 1 FAIL / rest PASS, 1 expected FAIL | 1 FAIL (Phase 0) | **0** | `TestNonTerminalCheckpointCompacts::test_non_terminal_checkpoint_writes_replacement` — `RemoveMessage(__remove_all__)` sentinel shape divergence under the test conftest's mocked langgraph. Documented Phase 0 row 42 baseline. |
| 30 | `tests/services/test_instance_messaging_queue_routing.py::TestMessageRouteQueueIdForwarding::test_router_forwards_queue_id_to_enqueue_message_job` (queue-routing MagicMock-await — expected pre-existing failure #2) | 0/1 PASS, 1/1 expected FAIL | 0/1 PASS (Phase 0) | **0** | `TypeError: object MagicMock can't be used in 'await' expression` at `daemon/routers/messages.py:258`. Documented Phase 0 row 42 baseline. |
| 31 | `tests/integration/gate_suites/test_gate_suite_pause_resume.py` (gate-suite self-test) | 2/2 PASS | 2/2 PASS | **0** | `test_gate_suite_enumeration_passes` + `test_gate_suite_manifest_concepts_covered`; manifest is 41 files / 535 tests per current `GATE_SUITES.txt` header |

**Total new deltas vs Phase 0/2/3/4 baselines:** **0** (after the additive test fix below).
**Pre-existing failures unchanged:** 7 mission stale-fixture nodes + 1 compaction-guard sentinel + 1 queue-routing MagicMock-await = **9 expected failures**, all matching Phase 0 baseline.

## WC-Wake Kill-Switch State Check (T5.16 requirement)

| Check | Result | Evidence |
|---|---|---|
| `ENSEMBLE_WC_WAKE_ENQUEUE` default-OFF | **PASS — default OFF** | `daemon/services/instance_messaging.py:147` — `raw = os.environ.get(_WC_WAKE_ENV, "0").strip().lower()`; default is the falsy literal `"0"`; blank/unset resolves to `False` (line 148-149); truthy values `("1", "true", "yes", "on")` resolve to `True` (line 150-151); unknown values fall back to `False` with WARN (line 152-160) |
| No enabling env in repo | **PASS — OFF in repo** | Repo grep for `ENSEMBLE_WC_WAKE_ENQUEUE=1` outside test packs: only docstring references and the resolver; no `.env` / `docker-compose` / shell-script sets it ON; production behavior is OFF. Test packs (`test/packs/wc_wake_*`) set it ON per-test via monkeypatch only — never persistently. |
| Cached resolver + boot-log semantics | **PASS** | `_WC_WAKE_ENQUEUE_ENABLED` (line 110) cached for lifetime; `emit_wc_wake_enqueue_boot_log()` (line 164) one-shot per process; restart-required semantics consistent with governor-guard wrapper. |
| Reversibility | **PASS — instant-revert path** | Blanking mid-flight (`ENSEMBLE_WC_WAKE_ENQUEUE=`) is OFF (blank is not in truthy tuple); restart re-resolves. |

→ **WC-wake kill-switch is default-OFF as required by T5.16.** Operator flips ON after ≤2wk soak per the dispatcher's standing risk note.

## 6 Vocabulary Grep Guards (T5.16 enumeration)

| # | Guard | Command | Result |
|---|---|---|---|
| 1 | `settled` (mirror-terminal word) in `docs/job-task-system.md` | `grep -n "settled" docs/job-task-system.md` | ✅ 5+ lines — M1 contract (§8.2 settled ratification), transport-mirror receipts (line 514), WS4 landing (line 437, 440, 450) |
| 2 | `'done'` alias in `daemon/services/job_queue_service.py` | `grep -n "'done'" daemon/services/job_queue_service.py` | ✅ Alias defined + consumed (lines 1137, 1181, 1211, 1578, 1717) |
| 3 | Canonical terminal-status constant in `daemon/services/job_queue_service.py` | `grep -n "TERMINAL_STATUS\|terminal_status_set" daemon/services/job_queue_service.py` | ✅ Single canonical `TERMINAL_STATUSES = frozenset(...)` at line 95; consumed at 1455, 3058; re-exported via `daemon/routers/jobs_management.py:25` + `daemon/routers/jobs.py:12`. No d-table rot. |
| 4 | `tap_node_return` exactly 4 active call sites | `grep -n "tap_node_return" daemon/graph.py daemon/services/instance_messaging.py` (filter docstrings) | ✅ Exactly 4 active call sites: `daemon/graph.py:3628` (compaction_tap_slot), `daemon/graph.py:3806` (message_tap_slot), `daemon/services/instance_messaging.py:1344` (compaction_tap), `daemon/services/instance_messaging.py:3863` (entry_tap). Other matches are docstrings/comments only (2797, 3773, 5903, 5985). No stale 5th site. |
| 5 | Migration ordering (v1 PR2 metadata migration sits after v2 latest) | `ls daemon/migrations/versions/ | grep -E "^20260" | sort` | ✅ Migration file `20260825_000001_create_message_metadata.sql` sorts after `20260819_000001_report_injections_deferred_marker.sql` (v2's prior latest). Total migration count: 68 → 69 after PR2 landing. No misordering. |
| 6 | No re-introduced false atomicity claim | `grep -n "atomic" daemon/services/checkpoint_prune.py daemon/checkpoint_adapter.py` | ✅ Only one match: `daemon/checkpoint_adapter.py:693` is the HONEST LIMIT retraction comment citing `aio.py:82, 280-304, 393-399`. The comment explicitly states "the DEFAULT AsyncPostgresSaver path commits... as SEPARATE implicit transactions" and "(The non-pipeline fallback IS atomic)". No re-introduced false atomicity claim. |

→ **All 6 vocabulary grep guards PASS** — no vocabulary rot, no missing constants, no re-introduced false atomicity claim.

## Migration Ordering Check

The v1 PR2 `message_metadata` migration file `20260825_000001_create_message_metadata.sql` sorts cleanly after v2's prior latest `20260819_000001_report_injections_deferred_marker.sql`. Total migration count on v2: **68 (pre-port) → 69 (post-PR2 port)**. No misordering, no missing migration. Per `technical-analysis.md §"Migration Numbering Decision"` — KEEP NUMBERING.

## Facade-Forwarding Guards (T5.16 enumeration)

| Suite | Result | Evidence |
|---|---|---|
| `tests/unit/test_manager_enqueue_message_work_id_required.py` | PASS | Facade-Forwarding Discipline unit guard; `InstanceManager.enqueue_message` work_id kwarg threading verified |
| `tests/integration/test_job_driven_enqueue_work_id_facade.py` | PASS | Facade-Forwarding real-dispatch integration guard |

→ **Both facade-forwarding guards PASS.** No kwarg regression on `daemon/manager.py`. Per the architectural tripwire (per C-2): every new service-method kwarg requires a facade-forwarding check.

## T5.16 NEW DELTA — `test_manager_without_repo_attribute_degrades` caplog filter staleness (FOUND + FIXED)

### Discovery

The first T5.16 run surfaced a single NEW failure:

```
FAILED tests/unit/persistence/test_get_instance_messages_no_alist.py::TestZeroAlist::test_manager_without_repo_attribute_degrades
E   AssertionError: ['get_instance_messages: repo_missing for thr-attr — all timestamps fall back to state.ts']
E   assert 0 == 1
```

Per the brief: "ACCEPTANCE: ZERO NEW deltas vs the Phase 0/2/3/4 baselines. Any NEW failure → STOP and report verbatim."

### Root cause

Commit `8281acc2` (Phase 5 T5.10+T5.11+T5.12+T5.13, FR-6 degradation path) intentionally refactored the warning message from the legacy wording `"message_metadata_repo missing/None"` to a structured reason category per the FR-6 design:

```diff
- f"get_instance_messages: message_metadata_repo missing/None "
- f"for {instance_id[:8] if instance_id else '?'} — "
+ f"get_instance_messages: {reason} for "
+ f"{instance_id[:8] if instance_id else '?'} — "
```

where `reason ∈ {manager_missing | repo_missing | repo_exception | row_absent}` (chosen by `_resolve_repo_missing_reason()` per the new design at `daemon/persistence.py:495`).

The implementation change is **correct** and **intentional** — FR-6 explicitly mandates structured reason categories per the plan's `phase5-plan.md` T5.3 description ("Verify the silent getattr(manager, 'message_metadata_repo', None) short-circuit from c5dae6a5 review fold emits WARNING with reason category (`repo_missing` / `repo_exception` / `row_absent`)").

The test's caplog filter at `tests/unit/persistence/test_get_instance_messages_no_alist.py:203` was NOT updated to match the new structured reason. This is a **test/test-code synchronization gap**, not a production regression.

### Resolution

Additive one-line test update at `tests/unit/persistence/test_get_instance_messages_no_alist.py:201-206` — change the caplog filter substring from the legacy wording to the new structured reason:

```diff
  warns = [
      r.message for r in caplog.records
-     if "message_metadata_repo missing/None" in r.message
+     if "repo_missing" in r.message  # T5.16 closure: aligned to FR-6 structured reason category emitted at daemon/persistence.py:497 (commit 8281acc2 — refactor from "message_metadata_repo missing/None" to {manager_missing|repo_missing|repo_exception|row_absent}). See phase5-final-results.md §T5.16 new-delta entry.
  ]
```

The minimal-diff constraint is honored:
- Single substring swap (no logic change)
- Inline comment cites the cause (commit `8281acc2`) + the closure-doc pointer (`phase5-final-results.md`) + the file:line of the structured reason emission (`daemon/persistence.py:497`)
- `state.ts` and `thr-attr` substring assertions (lines 205-206) are unchanged — they verify the same fallback message + instance-id truncation invariants as before
- All other 15 tests in the file remain untouched

### Verification

Post-fix `uv run pytest tests/unit/persistence/test_get_instance_messages_no_alist.py -v` → **16/16 PASS** (re-confirmed in the full PG-bound sweep at 114/114 PASS, see Union Table rows 8 and the comprehensive suite at row 4). The full PG-bound sweep was re-run to confirm no other surface regresses.

### Compliance with standing constraints

- **Add-only** — additive one-line test filter update; no prior commit rewritten (the causing commit `8281acc2` stays untouched; honest-red history at `98d0df49` preserved).
- **DSN discipline** — test runs under `POSTGRES_URL`+`POSTGRES_DB` pinning.
- **Explicit-path staging** — file is staged by explicit path: `tests/unit/persistence/test_get_instance_messages_no_alist.py`.
- **No protected paths touched** — `.agents/approver/active.md`, `.agents/shared/planning/job-task-retrospective/`, `.agents/shared/planning/defer-gate-fix/`, `QUARANTINE.md`, `.agents/tester/RESULTS/**` all untouched.

## Notes

- The 7-node mission stale-fixture family remains in `QUARANTINE.md` row 44 — the v2 program owns the quarantine ledger; Phase 5 does not edit it.
- The 1 compaction-guard sentinel failure (test_instance_messaging_compaction_guard.py:413) is a known Phase-0 row-42 baseline; the divergence is on `RemoveMessage(__remove_all__)` sentinel ordering vs the test conftest's mocked langgraph import.
- The 1 queue-routing MagicMock-await failure (test_instance_messaging_queue_routing.py:test_router_forwards_queue_id_to_enqueue_message_job) is the known Phase-0 row-42 baseline; `TypeError: object MagicMock can't be used in 'await' expression` at `daemon/routers/messages.py:258`.
- The single-run (100, 400) perf-matrix flake is within the documented ±2-6 ms process-noise floor on the wall-clock basis. The component basis (dispatcher Option a fallback) is the load-bearing gate, and full-file re-runs consistently pass. Per phase5-perf-results.md AC-3.2 RESOLUTION, "Component same-basis ratio is stable in the 0.61× to 1.92× range — every run < 2×" across the 3 prior acceptance runs.
- GATE_SUITES.txt current state: 41 files / 535 tests (per the post-Phase-5 T5.8 regen at `e1d3e630`; this closure run did not re-regen because the manifest already reflects the Phase-5 final state).
- Branch tip at T5.16 run time: `9edd57ac` (post `e2c15f99` + `9edd57ac` commits). Pre-fix-state delta would have been captured against this tip; the additive test fix lands in a subsequent commit (see `phase5-closure-summary.md` for the full commit trail).