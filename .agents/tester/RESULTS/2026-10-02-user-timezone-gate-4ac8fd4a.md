# Independent Test Gate — user-timezone-setting @ 4ac8fd4a

- **Branch / worktree:** `feature/user-timezone-setting` @ `4ac8fd4a` (base `e73ae6ff`), `/home/nea/ensemble-worktrees/user-timezone-setting`
- **Date:** 2026-10-02 (gate window 11:11–11:55 UTC)
- **Tester instance:** tester (this gate); 12 worker instances dispatched (all single-pack scope)
- **VERDICT: 🟢 GREEN — ready to merge.** All 5 commissioned gates PASS by independent execution; mock fidelity verified; e2e smoke attempted, feasible, and PASS; zero new regressions. Follow-ups listed in §7 (all non-blocking).

## Scope Decision

Full commissioned gate set executed (5 gates + pre-existing-red confirmation + ensure.md Core). No expansion beyond the commission; no product code modified by any worker. Worktree untouched by tester except authorized test-only commit `b80ed598` (e2e scaffolding, see §4c) and this docs commit.

## Per-gate results

| Gate | Pack / Suite | Command (essentials) | Result |
|---|---|---|---|
| G1 settings persistence | `tests/test_settings_api.py` | `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" -m postgres tests/test_settings_api.py -v` | ✅ **32/32 PASS** (0 deselected — canonical invocation confirmed effective), 24.25 s |
| G2a injection unit | `test_current_time_injection.py` + `test_user_timezone_utils.py` | `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" <2 files> -v` | ✅ **30/30 PASS** (14+16), 0.43 s |
| G2b mock fidelity | static, read-only | cross-analysis of test mocks vs real consumer | ✅ **FIDELITY: MATCH** (see §2) |
| G3 scheduling chain unit | `test_tz_resolver.py` + `test_scheduling_service.py` + `test_scheduling_tools.py` | `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" <3 files> -v` | ✅ **66/66 PASS** (16+36+14), 1.52 s |
| G4a FE Jest | `settings.component.spec.ts` + `settings.service.spec.ts` | `cd frontend && timeout 300 node_modules/.bin/jest <2 specs> --ci --runInBand` | ✅ **113/113 PASS** (102+11, exact match vs expected), 15.2 s |
| G4b e2e smoke | Playwright, live dev lane | see §4c | ✅ **FEASIBLE + PASS 3/3** |
| G5a sibling acceptance | `tests/packs/scheduled_tasks_acceptance.sh` | `timeout 300 bash tests/packs/scheduled_tasks_acceptance.sh` | ✅ **166/166 PASS** (94 adapter + 66 REST + 6 e2e; 0 deselected), 23.3 s |
| G5b regression re-run | 18-file scheduling + message-metadata set | one invocation, `-m "integration or not integration"` | ⚠️ **341/343** — 2 reds **proven PRE-EXISTING at base** (§5) |
| Pre-existing reds | `test_stop_instance_subtree.py` + `test_work_router.py` | one invocation | ✅ exactly the 4 known reds, signatures match, 27/31 others green, 0 new (§5.1) |
| ensure.md Core #2/#3/#4 | `test/packs/concurrency_atomic_unit_test.sh` + grep | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` (+ dev.sh grep) | ✅ **98P/0F/74S baseline-exact**; grep PASS (`dev.sh:102`) |

All runs: dual-layer timeout (outer `timeout 300` + suite-internal bound), worktree venv import-verified (`daemon.__file__` resolves inside the worktree — editable-install trap checked every worker), dev PG `localhost:5432` (conftest `PG_TEST_*` defaults; password verified by probe).

## §1 G1 — scenario coverage (all 6 commissioned scenarios)

set→get (`test_put_then_get_returns_value`, `test_returns_stored_value_with_current_offset`, helper-variant tests) · null/empty clear → nulls (`test_set_get_clear_get_round_trip`, `test_empty_string_clears_the_setting`, `test_returns_nulls_when_unset`) · invalid IANA → 422 (`Not/ARealZone`, `Mars/Olympus_Mons`, 100×'A') · path-traversal → 422 (`../etc/passwd` + `/etc/passwd`) · Factory-accepted deliberate pin (200, stored as-is) · whitespace-only clears (`"   "` → 200 + nulls). No gaps.

## §2 G2b — mock fidelity (commission sanity check)

- Real consumer `instance_lifecycle.py:1173` (inside `_apply_post_cache_appends`, reached by spawn `:1982` + restore `:4448`): `get_user_timezone_preference(project_repository)` — 1 positional arg; return `str | None`.
- Real definition `daemon/services/user_timezone_utils.py:31`: free function; `None` sentinel covers repo-None / no-row / empty / invalid-at-read / DB-error; never raises.
- Test mock patches `lifecycle_mod.get_user_timezone_preference` — exactly the module-attribute path the `from … import` binding (`:78`) resolves at the call site. SET and UNSET both MATCH on name/arity/return/kwarg-forwarding.
- **Byte-level unset pin: CONFIRMED.** `LEGACY_SECTION` full-string `==` in 4 appender tests (`test_current_time_injection.py:54/59/64/68`) + set-shape canonical full-string (`:88`, `test_bangkok_exact_bytes`) + set-minus-two-lines ≡ unset structural pin (`:107-111`). Wiring-level unset test is substring-only by design (variable timestamps through `_apply_post_cache_appends`); appender layer carries the byte-level invariant.
- Not pins-testing-fiction: two independent workers (execution + static) reached identical conclusions.

## §3 G3 — priority matrix coverage

(a1) explicit > user-setting (5 tests, incl. explicit-invalid-still-wins) ✅ · (a2) user-setting > env default ✅ · (a3) env default when setting absent ✅ · (a4) host-local rung: exercised via neutralized-host→UTC fall-through in this pack; **positive host-local pin lives in `tests/test_scheduler_adapter.py::TestTzResolution` — ran GREEN in G5a/G5b** ✅ · (a5) UTC-with-warning terminal (4 tests, stdlib-UTC-not-ZoneInfo invariant pinned) ✅ · (b) explicit → zero metadata reads (`assert_not_called` ×2) ✅ · (c) invalid stored value → silent fall-through (no raise; warning only when surfaced via `for_tool` path) ✅ · (d) user-setting used → no warning (`tz_warning == ""` ×5) ✅.

## §4c G4b — e2e smoke (attempted, feasible, PASS)

- Dev lane: backend via `dev.sh` (8079) with explicit `ENSEMBLE_SELF_ENV=dev` + `POSTGRES_*=localhost/ensemble_dev` (verified-working creds); FE `ng serve --port 4199` (proxy.conf → 8079). **Ambient env carried `POSTGRES_HOST=10.44.0.2` (live DB) — explicit exports overrode it; env-poison guardrail held.**
- S1 picker renders 418 offset-bearing zones incl. `Asia/Bangkok (UTC+07:00)`, `Auto / not set` default ✅ · S2 select Bangkok → auto-PUT 200 → reload persists (`GET {"timezone":"Asia/Bangkok","utc_offset":"+07:00"}`) ✅ · S3 select Auto → PUT null → reload cleared (GET nulls) ✅. Playwright 3/3 in 15.6 s.
- Evidence: `frontend/e2e-results/timezone-smoke/01..03*.png` (1280×720) + API bodies above.
- Teardown verified: 8079/4199 freed; all 8 booted PIDs reaped; protected lanes untouched and alive post-run (9797 live pid 2890792, 7979 demo pid 2185455, 7456 OpenDesign pid 2965860).
- Feature nuance vs brief: picker is a Material autocomplete (`app-searchable-select`), auto-saves on selection (Save button only in the no-`Intl.supportedValuesOf` fallback branch) — semantically satisfies "select → saves".
- Scaffolding committed `b80ed598` (spec only, 174 lines, zero product/config edits) — keep-or-drop is the commissioner's call; useful as future regression scaffolding.

## §5 Pre-existing reds

### §5.1 The 4 known reds — CONFIRMED, only reds in their files, unrelated

Exactly 4 failures, signatures match, 27/31 others green, 5.64 s:
- `test_stop_instance_subtree.py` case7/7bis/8 — `TypeError: _mock() got an unexpected keyword argument 'originator_instance_id'` @ `instance_lifecycle.py:3550` (QUARANTINE #97 family, base-attributed).
- `test_work_router.py::TestSerialization::test_response_field_shape` — 21-key exact-set pin vs actual extra `completion_gate_escalated` key (not in QUARANTINE).
- Diff-intersection (discovery, hunk-level): DISJOINT — the tz diff's 4 hunks in `instance_lifecycle.py` touch only imports + `append_current_time` signature + `_apply_post_cache_appends`; no `pause_instance_cascade`, no `WorkRecord`/work-router surface (`schemas.py` diff = 2 new standalone pydantic models).

### §5.2 Two NEWLY-DISCOVERED pre-existing reds (proven at base — NOT regressions)

`tests/integration/test_message_metadata_deletion_paths_prune.py`:
- `TestOrphanSweepPruneHappyPath::test_orphan_sweep_drops_side_table_rows` (:497) and `TestOrphanSweepPruneNeverRaise::test_prune_failure_does_not_abort_orphan_sweep` — `adelete_thread(...) await not found`; log: `maintenance.py:972 Orphaned threads cleanup failed: not enough values to unpack (expected 3, got 2)`.
- Evidence chain: 3/3 deterministic at HEAD in isolation → **byte-identical 2F/3P at base `e73ae6ff`** (temp worktree `/tmp/tz-base-e73ae6ff`, fresh `uv sync` venv, import-verified; removed after) → `git log e73ae6ff..HEAD` (6 commits, all tz/settings/frontend) has **zero** diffs on `maintenance.py`.
- Root cause: test-stub rot — stubs `instance_repo.list = MagicMock(return_value=([], 0))` (2-tuple, born 2026-09-04 `714f58ff`) vs 3-tuple contract `(instances, total, truncated)` since `02918951` (2026-09-09); ValueError swallowed by the sweep's broad except → zero deletes. Aggravator: `daemon/repositories/instance/repository.py:783` type-hint still says 2-tuple while `:1131` returns 3-tuple.
- Action: QUARANTINE row added (deterministic, base-attributed). Fix (stubs → `([], 0, False)`; optional annotation repair) = small test-side commission — NOT this gate's scope.

### §5.3 Dev "336 passed" reconciliation

Collect at HEAD = 343 (292 scheduling + 51 message-metadata). 341 passed + 2 §5.2 reds. The +7 vs the dev's 336 is consistent with tz test growth after the dev's snapshot (e.g. `test_scheduling_service.py` 23→36); no recorded prior status exists for `deletion_paths_prune` in the sibling RESULTS doc, so the dev's set/snapshot likely differed — no contradiction with the base proof.

## §6 ensure.md (Core, blast-radius scoped)

- Core #1 (no regressions in changed packs): ✅ via the gate packs above.
- Core #2/#3 (concurrency/atomic + no sync DB on loop): ✅ `concurrency_atomic_unit_test` **98P/0F/74S baseline-exact** (69.29 s; all 74 skips = pre-existing Phase-5/D13 in-test architectural skips, zero QUARANTINE overlap; thread-identity tests green). Structural note: the new tz read at `instance_lifecycle.py:1173` mirrors the pre-existing language read (`:1176`) exactly — same table/session/error-shape; no new event-loop surface.
- Core #4 (dev.sh graceful-shutdown grep): ✅ `dev.sh:102` `--timeout-graceful-shutdown 10`.
- Release Gate: not triggered (feature-branch validation, not release/architecture).
- Improvement notices: none — no requirement contradicts pack/timeout/scoping rules.

## §7 Follow-ups (non-blocking, for the commissioner)

1. 🔴 none blocking.
2. 🟠 Test-debt: fix the 2 quarantined `deletion_paths_prune` stubs (→ `([], 0, False)`) + repair stale `repository.py:783` 2-tuple annotation (trap for next test author).
3. 🟠 `test_response_field_shape` remains an un-quarantined known red (extra `completion_gate_escalated` key) — pin or quarantine in a maintenance pass.
4. 🟢 E2e scaffolding `b80ed598`: keep (recommended, reusable smoke) or drop before merge.
5. 🟢 Ops hazard observed: ambient shell env in this session carried `POSTGRES_HOST=10.44.0.2` (live DB) and a stale `POSTGRES_PASSWORD` — dev-lane boots MUST keep explicit localhost overrides (env-poison family note; guardrail worked here).

## Worker instances

discovery `aaa575df` · g1 `39956f81` · g2a `6121152a` · g2b `461684a3` · g3 `cbb7c33b` · g4a `84487401` · g5a `c2255880` · g5b `502a9230` · reds `f938d705` · g4b `023d4fd8` · ensure `5a9d68d2` · classify `76a6d6db`
