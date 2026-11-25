# TESTER GATE — wake-path-carrier-fix (board wi-8f60e6f1)

**Date:** 2026-11-19 (UTC) · **Worktree:** `/home/nea/ensemble-src-wt-wake-path-carrier-fix` · **Branch:** `feature/wake-path-carrier-fix` @ `00e6fd9362745d87c4eac92fb4baf3c1e8beaa01` · **Base:** `eca98397d`
**Verdict: PASS** — full required matrix executed; zero net-new reds; zero hard-exclusion hits; PG list executed on real PostgreSQL (NOT BLOCKED-ON-PG); zero edits / zero commits / zero live contact.

**Workers:** gate-preflight-infra `5dc7dd50` · gate-main-pack `e180ff68` · gate-core-a `1c3c0b34` · gate-core-b `aa6cb383` · gate-core-c `51c185d3` · gate-fences `aebe9357` · gate-pg `2dc03d7b`

---

## 1. Environment safety (pre-flight, worker 5dc7dd50)

| Check | Result |
|---|---|
| `.env` mode 600 | ✅ 600 |
| `POSTGRES_DB=ensemble_dev` (0× ensemble_prod) | ✅ |
| `ENSEMBLE_SELF_ENV=dev` | ✅ |
| non-assignment lines | ✅ 0 |
| `POSTGRES_PASSWORD=` lines | ✅ exactly 1 |
| `daemon.__file__` resolution | ✅ worktree `daemon/` (both python3 3.12.3 and .venv 3.14.7) |
| pytest | ✅ `.venv/bin/pytest` 9.0.2 ONLY (system python has NO pytest) |

**Fence amendments proven mid-gate (see LESSONS 2026-11-19-gate-env-fence):** ambient shell carries `ENSEMBLE_SELF_ENV=live`, `POSTGRES_DB` unset-default-prod, and **7 `ENSEMBLE_AUTH_*` vars incl. `ENSEMBLE_AUTH_ENABLED=true`**. The ISSUER/DISCOVERY_URL-only scrub leaves enabled-without-issuer → `AuthConfig` raises in any `Config()`-constructing suite. Canonical amended fence (proven green at 3 sites): scrub ONE-BY-ONE `-u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_SELF_ENV -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` then `set -a; source .env; set +a`. Mechanism settled: `config.yaml:130` ships `auth.enabled: false` — the trigger is AMBIENT env, not the file.

## 2. Required test matrix — results

### 2.1 Primary scoped pack
`test/packs/wedge_fix_suites_unit_test.sh` → **PASS** — `110 passed, 106 warnings in 14.02s`, EXIT 0. Dual-layer timeout confirmed (script-internal 120s + outer 300s). Meets ≥110 expectation exactly.
⚠️ PACKS.md integrity note: this pack is **NOT registered in `.agents/tester/PACKS.md`** (direct grep: zero `wedge` hits). Run proceeded under gate authority (leader-named pack). Registration owed post-gate.

### 2.2 Individual core suites
| Suite (file) | Verbatim | Verdict |
|---|---|---|
| `tests/unit/services/test_waiting_children_watchdog.py` | `51 passed, 5 warnings in 3.99s` | ✅ PASS (51+) |
| `tests/unit/test_reconciler_wedge_fix.py` | `42 passed, 20 warnings in 8.26s` | ✅ PASS (42+) |
| `tests/unit/services/test_a5_wedge_notify_work.py` | `9 passed, 1 warning in 0.99s` | ✅ PASS (9+) |
| `tests/test_dependency_bus.py` | `90 passed, 1 failed` → flake | ✅ adjudicated PRE-EXISTING |
| `tests/test_report_lane_phase2.py` | `30 passed` (3.97s) | ✅ PASS |
| `tests/job_queue/test_watcher_loss_terminal_strand.py` | `26 passed` (8.43s) | ✅ PASS |
| `tests/job_queue/test_watcher_loss_terminal_strand_gap.py` | `13 passed` (5.34s) | ✅ PASS |
| `tests/unit/test_nudge_completion_watcher_d2.py` (gate's "nudge_d2") | `19 passed, 1 warning in 2.46s` | ✅ PASS |
| instance_ops ×5 (behaviour/delegation/dismiss/prompt_layer/registration) | `58+39+19+3+27 passed` | ✅ PASS (146/146) |
| `tests/unit/services/test_child_reports.py` | `50 passed, 3 warnings in 10.08s` (amended fence) | ✅ PASS (see 2.2.1) |
| `tests/unit/test_error_report_recovery_hint.py` | `14 passed, 1 warning in 0.70s` | ✅ PASS |
| mission_strand_healer — no same-named unit file; covered by `test_reconciler_wedge_fix.py` (2+4 refs) + `tests/job_queue/test_running_zombie_sweep.py` `76 passed, 1 failed` | ✅ adjudicated (see 2.2.2) |
| message_processing_pipeline — no same-named file; `tests/unit/test_turn_end_injection_drain.py` | `31 passed, 1 warning in 0.91s` | ✅ PASS + census 19 refs/8 files |
| settings_service — no same-named file; census **66 refs across 14 unit files** (s1_wiring 13, checkpoint_pool 12, pg_engine_utc 10, …) | ✅ covered (distributed) |

**2.2.1 child_reports adjudication:** 17 deterministic reds (`AuthConfig: auth.enabled=true but missing issuer`) proven **fence artifacts, not lane**: (a) byte-identical FAILED node lists at base `eca98397d` (git-archive extraction, `daemon.__file__` asserted in extraction); (b) lane diff does not touch the test file; (c) amended fence → **50/0 green**. Matches POK `services-unit-pre-existing-reds-2026-11-11` ("child reports" entry).

**2.2.2 running_zombie_sweep adjudication:** 1 red (`TestRunningZombieReArmWiring::test_pre_stamp_hold_schedules_a_heal`, heal awaited 0×) — identical node at base under same fence → **base-attributable, net-new = 0**.

**B3/T5 named-test verification (watchdog, verbose):**
- B3 rewritten test: `TestWedgeRearmAfterCooldown::test_episode_end_on_condition_false_and_recurrence_notifies` — **PASSED** [98%] (verbatim).
- T5 (mapped via plan-overview.md:590 — no literal "T5" token in file): carried by `TestWedgeRearmAfterCooldown` (file :1597-1798), BOTH tests PASSED, asserting observable row state (r2): `_wedge_rows() == 1` (tick1, within-episode incl. WC→RUNNING→WC flip), episode-END on row-check-false + no extra notice, `_wedge_rows() == 2` on recurrence (B2 defect pin), `wedge_notices_enqueued == 1`, episode survives leaving WC. Supporting: `test_second_tick_does_not_renotify_same_episode`, `test_b_notice_suppresses_wedge_for_same_episode`, `test_wedge_eligible_again_after_new_b_episode` — all PASSED.
- Coverage-shape disclosure 🟡: the S4 test (`test_episode_survives_leave_wc_and_ends_on_the_row_check`) asserts survival but NOT the row-check-drop leg self-contained — that leg is asserted by the B3 test (:1745/1749/1758). Pair satisfies the T5 contract.

**dependency_bus flake adjudication:** `TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` — `InvalidRequestError: Could not refresh instance` at `repository.py:144` (base `:138`). Budget: HEAD 3F/7P (10 runs), **BASE 1F/10P (11 runs)** → flaky at base ⇒ **PRE-EXISTING**. Disproof of the first hypothesis: `no such table: task` orphan-sweep warning appears in 5/5 BASE + 5/5 HEAD diagnostic runs INCLUDING passes (fail-open background noise, both trees) — NOT the cause; real cause is an intermittent SQLite concurrency race in the test's own fixture. **Quarantine recommended (post-gate — read-only gate made no QUARANTINE.md edit).**

### 2.3 Regression fences (r3/r4)
| Fence | Files | Result | Verdict |
|---|---|---|---|
| r3 room/group_chat | 26 | **442 passed, 0 failed, 0 skipped** (4 chunks, 356.68s total; each chunk < 300s cap) | ✅ CLEAN |
| r4 report-injection/RDRS | 3 | **61 passed, 0 failed** (8.70s) | ✅ CLEAN |
| baseline/guideline/seal/registry | 12 | 313 passed + 3 reds = fence artifacts → `test_b_kill_switch_registry.py` **15/15 green** under amended fence | ✅ CLEAN |

`docs/guidelines/guideline-wedge-notice-kill-switch.md` exists (5678 B) and **trips NO registry/seal/guideline test** (`test_guidelines_registration.py` green).

### 2.4 MANDATORY PostgreSQL execution list — **EXECUTED (PG-REACHABLE)**
**Reachability proof:** `pg_isready 127.0.0.1:5432 → accepting connections` (rc 0); psycopg3 connect → `current_database() = ensemble_dev`, PG 16.15, user `ensemble_dev_app`. The implementer's "no PG on this box" claim is **disproven**.

**Execution-target correction (safety-grade, commission defect D2):** `tests/postgres/conftest.py:141-147` **hard-forbids** `ensemble_dev`/`ensemble_prod` (session `pg_engine` runs `drop_all` at setup AND teardown — would have dropped ensemble_dev's 111 tables twice). Suites + probes ran on the conftest-sanctioned throwaway **`ensemble_test`**; `ensemble_dev` verified untouched (111 tables before/after, 0 probe rows). Least-privilege role `ensemble_dev_app` for all reported numbers (first pass as default role `ensemble` was re-run and discarded — disclosed).

**Suites** (with `--override-ini="addopts=" -m postgres` — the repo addopts otherwise DESELECT the postgres marker: vacuous 6-deselected/EXIT 5 trap, PACKS.md:1249,1459):
| Suite | Verbatim | Verdict |
|---|---|---|
| `tests/postgres/test_dependency_bus_pg.py` | `6 passed, 1 warning in 14.77s` | ✅ PASS |
| `tests/postgres/test_report_lane_phase2_pg.py` | `14 passed, 1 warning in 15.53s` | ✅ PASS |
| `tests/postgres/test_06f500af_bug_class_eliminated_pg.py` | `1 failed, 2 passed, 1 warning in 6.44s` | ✅ adjudicated PRE-EXISTING/QUARANTINED |

The single red `test_orphan_watcher_cancelled_on_startup_sweep` (`PENDING != CANCELLED`, deterministic 3×+2 passes) is **QUARANTINE-registered on both QUARANTINE.md copies** (worktree :50/:181); lane's only edit to the file is `make_fu`'s child_id default. Root cause (report-only, D3): structurally un-passable since the W-C fix — sweep grace `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS = 30` (`dependency_bus.py:208`) protects a microseconds-old watcher; docstring/assert text stale ("not implemented yet" is false). QUARANTINE.md attribution is itself inaccurate (D4: claims Phase-1 unimplemented; it IS implemented at `:2314`, grace-gated).

**Dialect probes** (12 scratch rows `gate_probe_wpcx_%`, cleanup proven count=0):
- **(i) `mark_delivered_watch_id`** (`repository.py:612-689`): claim#1 **True** / claim#2 (fresh session, second engine) **False**; 2-thread concurrent race ×5 rounds → exactly one winner every round; marker value == watch_id. Mechanism: guarded compare-and-set `UPDATE … WHERE metadata->>'k' IS NULL RETURNING` (+ PG row lock + READ-COMMITTED re-eval) — NOT `SELECT … FOR UPDATE`. SQL captured on PG verbatim.
- **(ii) `fetch_pending_for_targets`** (`repository.py:209-243`): one call `[t1,t2]` → 4 rows `{t1:2, t2:2, t3:0}`; non-listed target excluded ✅; FIRED row excluded ✅; set-equality ✅. IN-batch verified on PG.
- **(iii) `fetch_pending_for_child_instance`** (`repository.py:349-401`): SQL is state-only (`WHERE state='PENDING'`, child filter applied in Python). **Residual 8 evidence (verbatim):** `Seq Scan on dependency_watchers (cost=0.00..12.25 rows=1 width=420)`, Filter `(state)::text = 'PENDING'`; ANALYZE: actual `rows=11 loops=1`, Rows Removed by Filter: 1, Execution 0.046 ms — planner estimate 11× low, seq-scan documented as by-design (rare events, never hot path).
- **(iv) watch()/child_id:** static census ✅ **NO-BARE-WATCH-CALLERS** — arming API `DependencyBus.watch` (`daemon/services/dependency_bus.py:677`); exactly 2 production call sites (`daemon/tools/instance_ops.py:731`, `daemon/tools/instance.py:1436`), both stamp `metadata["child_id"]`; refusal guard at `:731` (WARNING + return, no row minted); `FollowUp` constructed at exactly those 2 sites; no direct `DependencyWatcherRepository.insert()` callers; no dynamic dispatch. PG behavioral coverage of the **refusal guard = GAP** (🟠 finding F-2): `child_id` in the 3 gated PG files is fixture scaffolding only; watch-named PG nodes otherwise green (dependency_bus_pg basic-emit PASS; phase2 recovery trio PASS; sweep-pair 1 PASS + 1 quarantined).

### 2.5 Diff & hard-exclusion audit (gate item 5)
`git diff --stat eca98397d..00e6fd936`: **43 files changed, 4480 insertions(+), 310 deletions(-)** — 10 production files + 1 guideline doc + 32 test files. Verbatim stat captured by pre-flight (full listing in worker report `5dc7dd50`).
- `.sql` migration files: **0** ✅
- Hard-exclusion edits (`daemon/services/skill_*.py`, `daemon/tools/skill_*.py`, `daemon/services/job_processor.py`, `daemon/config.py`, `daemon/manager.py`, `daemon/graph.py`): **0** ✅

### 2.6 Pre-existing reds cross-check (gate item 6)
Every red encountered was dispositioned with evidence, none net-new:
| Red | Disposition |
|---|---|
| child_reports 17 nodes | fence artifact (AuthConfig) → 50/0 green amended fence; byte-identical at base |
| b_kill_switch_registry 3 nodes | fence artifact → 15/15 green amended fence |
| running_zombie_sweep 1 node | base-attributable (identical node at base) |
| dependency_bus 1 flake | flaky at base (1F/10P) — PRE-EXISTING |
| PG orphan-sweep node | QUARANTINE-registered pre-existing |
**Net-new reds: 0.**

## 3. Findings (new defects / advisories — report only; zero fixes applied per read-only gate)
- **F-1 🟠 latent test-debt (lane-adjacent):** bus-API swap `count_pending_for_target_sync` → `count_fulfillable_pending_for_target_sync` at TWO `mission_strand_healer.py` sites (~L560, ~L1242) refreshed mocks at only ONE test location (`test_running_zombie_sweep.py:1004,1010`). Census: **21 test files** mock the old API only; the second site is fail-closed (`except → _rz_skip`) ⇒ silent skip-not-heal if a stub lacks the new attr. `tests/job_queue/conftest.py:481` package default covers tests/job_queue/ only. Latent — no active red today.
- **F-2 🟠 PG coverage gap:** no PG node behaviorally pins the S1 watch-arming **refusal guard** (probe iv PG half) — only the static census exists.
- **F-3 🟡 coverage-shape:** S4 test's row-check-drop leg not self-contained (B3 test carries it; pair satisfies T5).
- **F-4 🟡 stale test prose:** `test_06f500af…::test_orphan_watcher_cancelled_on_startup_sweep` docstring + assertion text stale; QUARANTINE.md attribution inaccurate (grace-gated, not unimplemented).
- **F-5 🟡 quarantine owed (post-gate):** `test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` (pre-existing flake, base-reproduced). No QUARANTINE.md edit made in this read-only gate.
- **F-6 🟢 housekeeping:** `wedge_fix_suites_unit_test.sh` unregistered in PACKS.md; mission_strand_healer unit coverage thinnest of the five modules (9 refs / 3 files); transient `.env` ENOENT anomaly filed (9822b90d, minor, env-config).
- **Commission-side corrections (dispatcher, acknowledged):** PG suites need `--override-ini="addopts=" -m postgres` (vacuous-deselect); "target ensemble_dev" was unsafe (conftest forbids it — `ensemble_test` is the sanctioned throwaway); pre-verify must run post-source.

## 4. Integrity
- **Zero edits** to tracked files; final `git status --porcelain` byte-identical to pre-gate baseline (2 pre-existing modified agent-state files + 4 untracked lane entries). `install-audit.jsonl` never modified.
- **Zero commits/branches/tags/stashes. Zero live contact:** no `ensemble_prod` connection, no daemon/systemctl/process signals, port 8088 untouched (not listening at check time), live PIDs 2581963/2581974 continuous.
- PG scratch rows (12) deleted with proof (count=0); `ensemble_test` restored to 0 tables; `ensemble_dev` 111 tables before/after.

## 5. Recommendation to the lane owner (post-gate, outside this gate's mandate)
1. Land F-5 quarantine row + F-4 doc corrections (test-side, small).
2. Register `wedge_fix_suites_unit_test` in PACKS.md.
3. Decide F-1 (refresh 21 mock sites vs repo-wide fixture) and F-2 (add a PG refusal-guard pin) — both small test-side commissions.
4. Adopt the amended fence (LESSONS/2026-11-19-gate-env-fence-ensemble-auth-enabled.md) for all future gate runners on this host.
