# RESULTS — sonic test gate: sweep-truth-gate-hygiene (merged tree)

- **Commission**: sweep-truth-gate-hygiene · **Branch**: feature/sweep-truth-gate-hygiene @ 29f368431 (tag v0.30.11) · **Base**: bf4bfde6e
- **Worktree**: /home/nea/ensemble-src-wt-sweep-truth-gate-hygiene (clean throughout; all mutations in throwaway detached worktrees, removed after)
- **Date**: 2026-11-14 · **Tester**: sonic-tester · **Verdict**: **TEST: PASS**

## Targeted suites (developer's exact commands, re-run independently)

Worktree venv (no bare python), per-variable `POSTGRES_*` scrub + explicit `ENSEMBLE_SELF_ENV=dev PORT=8079` (ambient `ENSEMBLE_SELF_ENV=live` present on this lane — pinned):

- `tests/job_queue/test_stgh_sweep_truth_pins.py + tests/job_queue/test_a3_eligible_pending_sweep.py + tests/unit/test_reconcile_empty_cycle_info_log.py + tests/test_run_tests_scrubbed_selfenv_gate.py + tests/unit/test_log_hygiene.py -q` → **71 passed** (matches handoff "expect 71 passed")
- `bash -n scripts/run_tests_scrubbed.sh` → OK
- `ENSEMBLE_SELF_ENV=live bash scripts/run_tests_scrubbed.sh --version` → **rc=78** + `ENV-SCRUB FAILURE: ambient ENSEMBLE_SELF_ENV='live'` + remediation line + no pytest banner
- `env -u ENSEMBLE_SELF_ENV ...` → rc=0 (pytest 9.0.2) · `ENSEMBLE_SELF_ENV=dev ...` → rc=0
- P10 pack `tests/packs/plane_shell_scrub_content.sh` → **10/10 PASS** (5 repo assertions + 5 mutation legs RED as expected)

## Mutation proof (counter-mutation convention; every mutation reddened)

Throwaway detached worktree @ 29f368431 + symlinked venv; pins file re-run per leg.

| Pin | Mutation applied | Reddened? |
|---|---|---|
| P2 signal (notify_work) | A1: `worker_pool.notify_work` returns `None` (production pre-change shape) | ✅ `test_p2_notify_work_returns_true` (1F/6P) |
| P2 count (notify_all) | A2: `pulsed += 1` → `pass` (notify_all returns 0) | ✅ `test_p2_notify_all_one_ok_one_raising` (1F/6P) |
| P1 False-with-cause | A3: `notified = bool(notify_result)` → `notified = True` (the chronic lie) | ✅ `test_p1_failed_dispatch_logs_false_with_cause` (1F/6P) |
| P1 True-on-real-dispatch | A4: `notified` → `False` (dispatch claimed never) | ✅ `test_p1_dispatched_true_on_real_signal` (1F/6P) |
| P4 W-D off-loop | A5: `asyncio.to_thread(...)` reverted to sync call on loop | ✅ `test_p4_wd_filter_off_loop` |
| M2 leak-gate | B1: `case "${ENSEMBLE_SELF_ENV:-}"...esac` block deleted | ✅ 6/6 refusal legs RED (rc=0 instead of 78); 4/4 clean legs stay GREEN (sanctioned runs unaffected — correct semantics) |
| P3 bounded scan | C1: `if limit is not None: stmt = stmt.limit(limit)` dropped | ✅ `test_p3_repo_limit_oldest_n` |
| P3 oldest-first | C2: `.asc()` → `.desc()` | ✅ `test_p3_repo_limit_oldest_n` |
| M3 openai pin | E1: `logging.getLogger("openai").setLevel(_OPENAI_LEVEL)` removed | ✅ `test_default_pin_is_info` + `test_pin_survives_reinstall` (2F/2P; override-to-DEBUG legs survive — expected, the load-bearing default pin is proven) |

**Correction to the handoff disclosure**: the claim "reverting notify_work()->None collapses P1's True case" is inaccurate at unit level — P1's dispatched-True leg SURVIVES A1 (its fallback pool is a `MagicMock`, decoupled from the production return; `bool(MagicMock)` is truthy under old and new code). Coverage is NOT vacuous — A4 counter-mutation reddens P1's True case directly — but the surface-sharing mechanism claimed is wrong. Recorded here and in the verdict.

**Reconciler unboundedness**: static inspection — all 3 `job_recovery_service.py` call sites (:1433, :1731, :3718) call `list_pending_tasks_older_than` with `min_pending_age_seconds` only, NO `limit` kwarg. `_SWEEP_PENDING_LIMIT` = 2×(5+2) = 14 (constants pinned by `test_constants_present`). Repo-level default-unbounded pinned by P3 leg 2 (and PG CHECK 2). No call-site-level pin protects the reconciler's unboundedness (disclosed coverage gap, low risk — keyword-only default `None`).

## Broader regression shard (blast-radius-intersecting, sized to wall)

66 files = grep-derived seam set (worker_pool/pool_orchestrator/eligible_pending_sweep/safe_notify_all_pools/list_pending_tasks_older_than/notify_work/logging_setup in tests/{unit,services,repositories}) + job_queue themed subset (sweep|recover|reconcil|notify|wedge|defer|a2_|a3_|vgap|midflight|seam_invariants|autopromote|watchdog). 1696 collected, `pytest -n 8`, junitxml + -rf, wall **176 s** (log `/tmp/stgh_shard_212311.log`).

**1684 passed / 12 failed / 3 deselected.** All 12 failures A/B-attributed at base bf4bfde6e (throwaway detached worktree, same commands): **12 failed / 0 passed at base, identical signature classes** — all PRE-EXISTING, zero commission-caused:

- `test_running_zombie_sweep` ×1 — heal_running_zombie awaited 0× (stub-drift; QUARANTINE job_queue family class)
- `test_instance_messaging_queue_routing` ×2 — "expected await not found" @ messages.py:331 (QUARANTINE 2026-10-19 messages.py await-seam family, line drift 326→331)
- `test_child_report_next_turn_execution` ×3 — `_process_message_with_tracking() got unexpected kwarg 'dispatch_chain'` (stub-arity drift)
- `test_instance_read_shakedown_defects` ×4 — `_RecordingRepo` lacks `list_child_ids_for_parents` (stub-drift @ instance_lifecycle.py:5121)
- `test_scheduler_deferred_mechanism_probe` ×1 — claim-SQL gate probe (base-identical)
- `test_checkpoint_pool_config` ×1 — `assert 15 == 10` constants-mirror drift (base-identical; constants.py untouched by commission)

Per-failing-file `git diff bf4bfde6e..29f368431 -- <file>`: **all 6 EMPTY**. QUARANTINE pre-check performed (families cross-referenced above).

## PG-dialect gap — CLOSED

Developer-flagged unexercised path exercised: disposable PG16 cluster (native `initdb -A trust -U stgh_probe`, TCP 127.0.0.1:5544, scratch DB `stgh_scratch`, DROP teardown, ambient POSTGRES_* scrubbed per-variable, psycopg3 `postgresql+psycopg://`). Script `/tmp/stgh_pg_exercise.py` (removed after), worktree venv:

1. `list_pending_tasks_older_than(60, limit=2)` → exactly 2 oldest, asc — **PASS**
2. default unbounded → 5 eligible, heartbeat-carrier + young excluded (reconciler shape) — **PASS**
3. `EligiblePendingSweepService.sweep_once()` on PG → eligible=5, notified=True, log `dispatched=True` — **PASS**
4. W-D filter off-loop on PG (thread-id assertion) — **PASS**

ALL 4 PG CHECKS PASS. Cluster torn down (DB dropped, stopped, dir removed).

## Pack registration

`plane_shell_scrub_content` (P10, sonic) registered in MAIN repo `.agents/tester/PACKS.md` **line 1233** AND worktree copy (identical rows) — gate-verifiable. Registration-drift cross-check clean.

## Residuals (disclosed, non-blocking)

- No live-daemon boot smoke (truthful `dispatched=True` verified at unit level only) — post-promote check per handoff.
- `_SWEEP_PENDING_LIMIT` uses constants defaults; runtime settings overrides not reflected (in-source documented).
- Adjacent scrub-gate gaps (ENSEMBLE_UPGRADE_LIVE, MCPS_GATEWAY_*) out of scope per commission.
- Reconciler-unbounded call-site pin does not exist (repo-level + static verified).
