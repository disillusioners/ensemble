# source-gc FUNCTIONAL GATE (P6) — G1–G8

- **Commission:** source-gc (board wi-ed230eb4) — unused scheduler schedules + their source rows GC-removed.
- **Lane:** `feature/source-gc` @ HEAD `7b6fa2cdf` (worktree `/home/nea/ensemble-src-wt-source-gc`, fenced dev `.env` → `ensemble_dev`).
- **TRUE base for regression diffs:** `b21bbb87f` (v0.29.2 bump = `origin/latest`; merge-base verified). **Base correction:** the dispatch's `f0f3e1724` carries a wrong parenthetical ("our lane's code commits sit above it") — `daemon/services/sources_gc_service.py` is created at `a6b181ead` which sits BELOW the sync-merge, so `f0f3e1724` already contains the impl and would have HIDDEN impl-caused regressions. Both the G1(a) base leg and the G6 whole-tree base leg ran at `b21bbb87f`.
- **Gate date:** 2026-11-08 (legs 02:27–04:4x UTC). **Tester instances:** A2 `bc457a0f` (integration-test), B `88ec1fda` (test-pack-execution), C `dbc64097` (test-pack-execution), D2 `449d6ac0` (test-pack-execution). Original A `2e72db27` + D `1368ed4b` wedged at 2026-11-07 ~15:35–15:37Z (anomaly `b7423ea5`) — terminated, one re-dispatch each per house ladder.
- **Evidence:** `/tmp/sgc-g135-re/` (A2) · `/tmp/sgc-g4/` (B) · `/tmp/sgc-g78/` (C) · `/tmp/sgc-g6/` (D2, 23 MB).

## VERDICT: **NO-GO** — one deterministic lane-caused defect (G6); everything else PASS.

G1/G2/G3/G5/G7/G8 ALL PASS · G4 mutation matrix clean with 2 pin-coverage findings (routed, non-blocking) · **G6 FAIL: 5 deterministic net-new reds, single root cause = the lane's own migration file is broken on SQLite.** 1-line fix shape below; scoped Tier-1 re-gate (the 5 migration ids) runs the moment the lane lands the fix — the remaining 63 diff ids are adjudicated clean.

---

## Gate table

| Leg | Verdict | Evidence |
|---|---|---|
| **G1(a)** BASE no-GC @ `b21bbb87f` | **PASS** | `git cat-file -e` rc=128 (module absent); base-tree import probe: `gc_find_candidates`/`gc_archive_and_delete`/`SourceConfigArchive` all absent; fixture byte-identical before==after |
| **G1(b)** HEAD exact-set @ `7b6fa2cdf` | **PASS** (14 sub-asserts) | 97/97 deleted == SQL-recomputed eligible set; 9 survivors exact (3 young + active + error + unparseable + 3 chat); archives 1:1 (97 cfg + 110 exe), `config_json` verbatim incl. `run_at`/`schedule`; **NO `credentials` column** (AM-4), 0 credential values; children gone (mappings/execs/processed 0); counters truthful (`processed=10/10`, `skipped_unparseable=1/1`, `observed_error_rows=1/1`, `remainder=0`) |
| **G2** dry-run non-destructive | **PASS** | 97 candidates → ZERO row/child/archive deltas; counts == would-collect; 1 summary + 5 row lines; knob flip on SAME state collected exactly 97 |
| **G3(a)** chat fence | **PASS** | 3 ticks: 3/3 chat sources + mappings intact incl. both traps (`enabled=true,status=stopped`) |
| **G3(b)** enabled fence | **PASS** | active row never a candidate |
| **G3(c)** age boundary | **PASS** | predicate strictly `>`: age==retention KEPT (repo layer, injected `now`); −60s kept; +60s collected; wall-clock service leg consistent (disclosed) |
| **G3(d)** kill-switch | **PASS** | disabled line exactly once, `_task is None`, zero writes |
| **G3(e)** batch cap | **PASS** | 30 eligible → 25 collected oldest-first (`updated_at ASC`), `remainder=5`; tick 2 collects the 5 deferred |
| **G5** real daemon + FE | **PASS** (24 sub-legs) | verbatim start INFO; first-tick-immediate; §8.3 work line + ONE-TIME §8.4 marker; REST `{sources,total}` shrink **105→80→55→30→8** over 5 ticks; chat sources still listed; `/api/schedules` pages; pagination honored; SIGTERM by exact PID, port freed, no orphans; live `:9797` untouched (uptime-continuous, state.json promote fields untouched) |
| **G4** mutation matrix (unit half) | **PASS with findings** | 23 legs: 20 discriminating RED→GREEN, 1 disclosed skip (bare `with_for_update` — SQLite no-op, PG half owns it), **2 false-confidence findings F1/F2 (pin-coverage gaps, routed)**; census 43/43 verified before AND after |
| **G7** PG harness + seal | **PASS** | unit 43/43 (33 repo + 10 service); PG pack 5/5 ×3 green runs (P16 interleave + W1-listener discriminator, P10 FK order, W6 listener/blocking); seal tripwire 35 pins 0 stale + `PROMPT-BASELINE: OK — 181`; static `git diff --stat b21bbb87f..7b6fa2cdf -- daemon/sources/ daemon/constants.py` EMPTY; throwaway DB `ensemble_sgc_g7` created→dropped (catalog 0); SERIAL proven |
| **G8** A10 paused-deferred ceremony | **PASS** ×2 | REAL `gc_archive_and_delete` on planted shape (config defer keys `_defer_gen/_defer_message_id/_defer_count/_defer_since` per scheduler.py:1147-1151 + READY `message_queue` row): queued delivery **still resolves by message_id** via real `SQLModelMessageQueueRepository.get` (no orphan, no exception); defer bookkeeping lands in `source_configs_archive.config`; children cleaned. Fully reachable in-harness — no compensation owed |
| **G6** Tier-2 whole-tree | **FAIL** | base `b21bbb87f` 34,817 collected (33,997P/455F/25E/334S) 22m21s vs head `7b6fa2cdf` 34,860 collected (34,011P/481F/25E/337S) 27m08s; digests `9a8b0d5f…`/`3d4bc7e6…` (GREP-FULL canonical); 68 diff ids adjudicated 3× isolated: **5 GENUINE net-new (deterministic)**, 43 composition artifacts, 16 pre-existing both-sides, 3 flaky, 1 base-only opposite-direction |

## G6 root cause (the 5 net-new reds — ALL one defect)

Failing ids (deterministic: head 3/3 FAIL, base 3/3 PASS):
1. `tests/migration/test_board_runner_apply_rollback.py::TestFullMigrationDirReality::test_the_board_migration_applies_in_the_full_dir_once_the_known_blockers_are_pre_marked` (:534)
2. `tests/migration/test_migration_dup_prefix_guard.py::test_fresh_sqlite_boot_applies_both_20261018_migrations_in_registry_order` (:161)
3. `tests/migration/test_migration_legacy_ledger_shadow.py::test_documented_remediation_adds_secrets_audit_context`
4. `tests/migration/test_migration_legacy_ledger_shadow.py::test_pre_marked_20261018_000001_shadows_secrets_audit_context_migration`
5. `tests/test_prompt_version_resolution_core.py::TestMigrationParity::test_baseline_table_re_apply_idempotent`

Traceback class: `sqlite3.OperationalError: near "chat": syntax error` → `MigrationError: Migration 20261107_122648 failed` (`daemon/migrations/runner.py:723-724`, exec at `:677`, from `run_pending_migrations` `:870`).

**Defect:** `daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql:92`
```sql
);   -- AM-4: NO credentials column (scheduler rows are always NULL; chat rows unreachable)
```
The runner strips full-line comments but leaves **trailing** comments, then splits statements on `;` — the `;` inside the trailing comment shatters the statement. The file's own header (RUNTIME-SPLITTER NOTE) explicitly promises "This file uses no semicolon in any comment" — the lane violated its own documented invariant.

**Blast radius:** every fresh SQLite boot (runner is SQLite-only). PG is unaffected (schema via `create_all`) — which is exactly why the PG legs (G7), the dev-PG fixture legs (G1–G3) and the dev daemon boot (G5) never saw it; only the whole-tree SQLite corpus exercises the full migration dir. Verified the lane worktree HEAD **is** `7b6fa2cdf` — the verdict binds the lane tip as it stands.

**Fix shape (1 line, lane owner's call — NOT applied by the gate; read-only lane discipline + migration-ceremony authorship):** remove the `;` from that trailing comment, or promote the comment to a full-line comment above the statement. Suggested companion (fix commit): a migration-corpus pin asserting no `;` occurs inside any comment in any `daemon/migrations/versions/*.sql` (the invariant the header already promises — see LESSONS/2026-11-08-source-gc-migration-comment-semicolon.md).

**Re-gate recipe (after the fix lands):** scoped Tier-1 re-run of exactly the 5 ids above (both legs not needed — the other 63 diff ids are adjudicated clean; the fix is comment-only so the corpus shape is unchanged) + one full-dir fresh-SQLite boot proof (id 2's shape).

## G4 mutation matrices

**Unit half (B, scratch worktree @ `7b6fa2cdf`, porcelain-empty + removed):** 23 legs — P16(i) re-assert leg drop → RED (`TestP2EnabledFence`+`TestP19`); P16(ii) `updated_at` WHERE-pin → RED (`TestRacedSkip`); P17 ORDER BY drop → RED; P18(i) ON CONFLICT DO NOTHING → RED (collision-skip contract pin; the `test_no_on_conflict_absorption` pin does NOT discriminate DO-NOTHING — a stale row survives DO-NOTHING by definition, disclosed); P18(ii) latch removal → RED (warned2=1); P19 enabled/status leg drops → RED both; P20 index rename → RED; P21 mappings table-wide → RED; W2 chat-fence predicate → RED; W3 raced-as-success → RED; W4 dry-run guard → RED; W8/P11 span re-point → RED; P15 no-op-silence + marker-drop → RED; P3 boundary `<=`→`<` → RED; P4 unparseable→0.0 → RED; P8 dry-run fields → RED; P9 exec-archive skip → RED; P12 `ge=1` → RED; P13 kill-switch → RED; P14 sleep-first → RED; P1/P6 source_type leg → RED. P14-structural: `daemon/sources/` + `daemon/constants.py` diff EMPTY.

**PG half (C, scratch worktree, throwaway PG):** P16 lock-drop (`with_for_update` removed from the locked re-SELECT) → RED via `test_p16_positive_control_uncontended_deletes_cleanly` (W1-listener discriminator — the two-connection interleave node plants its own FOR UPDATE, so the listener node is the teeth source, per fix-cycle e17b4668a design); W6 order revert (remove the early parent lock) → RED via `test_w6_delete_source_config_locks_parent_first`; P10 parent-first delete → RED via real `ForeignKeyViolation` (`instance_mappings_source_id_fkey`). All restored byte-identically (`git diff --quiet` rc=0) → GREEN 5/5.

### False-confidence findings (pin-coverage gaps — ROUTED, non-blocking)
- **F2 (material):** NO pin covers the AM-10 off-loop contract — `asyncio.to_thread` replaced with direct-sync passthrough at all 5 call sites leaves the full service pack 10/10 GREEN (zero `to_thread`/`threading`/`get_ident` references in either pack). The v0.27.14-class regression this fence exists to prevent is unpinned. Suggested shape: thread-identity pin (repo mock records `threading.get_ident()`, assert ≠ loop thread).
- **F1 (leg-scoped):** P15 "marker-every-tick" mutation survives `test_first_destructive_marker_once` (tick 2 collects nothing, so the latch is unobservable); the pin's other two legs discriminate. Suggested: make tick 2 collect a candidate.

## Observability contract (§8) — verified live, verbatim
```
[SourcesGC] started: enabled=True dry_run=False interval=20s retention=7d batch_cap=25
[SourcesGC] dry-run: candidates=97 skipped_unparseable=0 observed_error_rows=1 remainder=0 deferred stuck=0
[SourcesGC] collected=97 archived_configs=97 archived_executions=110 mappings=97 processed=10 skipped_unparseable=1 observed_error_rows=1 remainder=0 deferred stuck=0
[SourcesGC] first destructive sweep performed at 2026-11-08T02:53:32.841525+00:00 (retention=7d, collected=97)
[SourcesGC] disabled (SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run
[SourcesGC] collected=25 ... remainder=72 deferred   (then 47/22/0 over successive ticks)
[SourcesGC] stopped
```

## Flake ledgers
- A2: **empty** (all reds were deterministic harness bugs, fixed in-harness and re-proven; never lane code).
- B: **empty** (every RED/GREEN deterministic first-run).
- C: **empty** (PG pack ×4, G8 ×2 all green).
- D2: 3 flakes (3× isolation: `tests/test_manager_shutdown.py::…durable_sweep_sibling_alive_skip_and_dead_writer_swept` h1/3 b1/3 — house follow-up R4; `tests/tools/test_auto_kill_integration.py::…sibling_alive_row_skipped_by_terminal_lane` h2/3 b1/3; `tests/services/test_skill_evolution_service.py::…ab_resolution_threshold_met` h1/3 b0/3) + 1 unstable-shape (`tests/test_api.py::test_send_message_success` — different error texts BOTH legs, passes isolated). 1 base-only deterministic (`tests/perf/test_auth_middleware_latency.py::test_public_path_p99_under_threshold` base 3/3 fail, head 0/3 — opposite direction, non-blocking, disclosed).

## Disclosures
1. **Wedge incident:** original A + D workers froze simultaneously in their first turn (2026-11-07 ~15:35–15:37Z; anomaly `b7423ea5`) — both terminated, one re-dispatch each (A2/D2). The wedged A had in fact leaked real state before freezing (71 fixture rows + 387 archive rows + a detached dev daemon pid 88053); A2 verified ownership via `/proc` + lane KV, purged prefix-scoped, fenced SIGTERMs. Zero work lost.
2. **Pre-existing REST fragility (NOT this lane; byte-identical at base):** `GET /api/sources` 500s if ANY row has non-ISO `updated_at` (`daemon/utils.py::parse_utc_datetime` unguarded). Composes poorly with the lane's fail-MISS skip (unparseable rows also break the listing). ROUTED: router/sources lane follow-up.
3. **Pre-existing `SourceCleanup` 30d age-only sweep** reaps >30d chat mappings independent of the GC (G5B chat-mapping survival 2/3 with log attribution `Cleaned up 1 inactive instance mappings older than 30d`); GC innocence separately asserted (P21). Unchanged behavior — disclosed, no action.
4. **Ambient env poison recurred in worker shells** (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`) — caught + neutralized per-variable in C and D2 (scrub self-tests 4/4, 25-var identity). Zero prod contact anywhere in the gate.
5. **G6 pre-exclusion deviation:** `tests/performance/test_message_api_cost.py` ran (registry rule not applied by the recipe's canonical collection command) — contributed 5 base-only sweep reds, adjudicated pass-in-isolation; compensating pin `regression_performance`. e2e live-daemon cells failed digest-identically both legs (pre-existing; pin `e2e_workflows_ensure_test`). `tests/packs` g7 trap excluded via `--ignore` (pin: registered pack scripts).
6. G5 point-in-time REST "before" was unobservable at 20s tick cadence — compensated with the conservation identity (`live + archived == 105` at every instant) + full observed shrink sequence + Phase A `REST total == DB total == 105`.
7. Fixture chat rows must use REAL `SourceType` members (literal `'chat'` 500s in `_source_to_info` — fixture detail).
8. CPython 3.14.7 venvs used by C/D2 (house py_note: certified 3.13 reader cannot read 3.14 artifacts — relevant to the later staged-verification leg).
9. `install-audit.jsonl` KMS-audit side effects in D2's worktrees preserved to `/tmp/sgc-g6/` and both worktrees restored to porcelain-0 before teardown; all scratch worktrees removed; dev DB left zero-residual (`source_configs=0 … archives=0`; archive TABLES remain — daemon `create_all` owns them).
10. Foreign POK-lane pytest overlapped D2's start — completed before D2's first leg; untouched.

## Action Needed (lane owner)
- [ ] **BLOCKING:** fix the trailing-comment `;` at `20261107_122648_create_source_gc_archive_tables.sql:92` (remove the semicolon or promote to full-line comment); ideally + the no-semicolon-in-migration-comments corpus pin. Then ping the tester for the scoped Tier-1 re-gate (5 ids + one fresh-SQLite full-dir boot) — everything else is green and adjudicated.
- [ ] ROUTED: AM-10 thread-identity pin (F2) + P15 marker-latch leg strengthening (F1) — pin owner.
- [ ] ROUTED: `parse_utc_datetime` unguarded 500 in `GET /api/sources` — router lane (pre-existing).
- [ ] NOTED: pre-existing `SourceCleanup` 30d mapping sweep behavior (AM-8) — unchanged, no action.

## Scope Decision
Gate scoped to the lane's surface per blast radius (16-file lane diff, `daemon/api.py` touched → Tier-2 whole-tree for G6; all other legs scoped to the lane packs/fixtures). Full-suite beyond the house corpus was not warranted and not run; the default corpus IS the house whole-tree corpus (34.8k nodes/leg). Base correction (`b21bbb87f`, not `f0f3e1724`) applied to G1(a) and G6 — disclosed at the top of this artifact.
