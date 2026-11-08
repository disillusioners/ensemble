# source-gc — Plan Overview

**Board:** wi-ed230eb4 · **Branch:** `feature/source-gc` @ 6b571771d (== origin/latest at plan time)
**Commission:** Remove unused schedules AND their sources from `source_configs` by extending the existing cleanup family (one GC subsystem, consistent knobs/logging) — never a parallel new subsystem.
**Plan status:** v3 — POST-PLAN-COUNCIL FOLD. Architect gate PASSED **SOUND-WITH-AMENDMENTS** (AM-1..AM-11 folded); plan council P3 returned **APPROVE-WITH-ADVISORIES 0C/5W/5A — no re-council required; D2 uniform rule RATIFIED WITH CONDITIONS**. All W1-W5 + advisories A-E + the three D2 conditions are folded (fold maps: phase4-plan §11, both rounds). `architecture-recommendation.md` remains the untouched architect gate record. Ready for the FRESH APPROVER on this v3 set.
**Plan-time note:** all anchors worktree-verified @ 6b571771d — including every architect-introduced anchor (re-verified during this fold).

---

## 1. Problem

Live `source_configs` carries **103 rows**: 100 inert scheduler rows (ALL `enabled=false`: 93 one-shot `stopped`, 5 one-shot `cancelled`, 2 cron `stopped` — incl. `jev-free-reprobe` stopped 2026-10-20 and `self-alarm:nest-patrol-recurring` stopped 2026-10-23) + 3 chat rows (discord/telegram/slack, ALL `enabled=true` — **owned by a parallel regression lane, job 9e94f9e3, NEVER touched**). The rows are boot-skipped (registry.py:312-314) and cost nothing at runtime, but they bloat `/api/sources` + the FE Sources page. 110 `schedule_executions` rows all reference inert sources; 103/110 are phantom-`queued` (ledger unreliable). 100 `instance_mappings` pin the 100 scheduler sources 1:1.

Existing `SourceCleanup` (daemon/sources/cleanup.py) handles only `processed_messages` (24h) + inactive `instance_mappings` (30d) — **never `source_configs`**. No knobs, no kill-switch, hardcoded 6h interval, 60s-delayed first tick, logs only when it deletes.

## 2. Decision summary — FINAL rulings (full rationale in decisions.md; architect adjudications + 3 leader rulings folded)

| # | FINAL | One-line |
|---|----------|----------|
| **D1** | **(b) additive archive migration — CONFIRMED, minus `credentials` (AM-4), plain-INSERT fail-loud PK policy (AM-3), race-safe txn (AM-1)** | `schedule_executions_archive` + `source_configs_archive` (NO FK, TEXT ids, TEXT-only columns), copy-then-delete same-txn; honors ADR-012 history doctrine + removal directive; **+1 migration file**, prefix `20261107_*`; archive retention UNBOUNDED with trip-point follow-up (~50k rows / ~50 MB / first slow query). FK relaxation (c) rejected: ALTER on hot table. Pure purge (a) rejected: contradicts cancel-preserves-history doctrine verbatim. |
| **D2** | **Uniform rule — CONFIRMED (SP-3) and RATIFIED WITH CONDITIONS by plan council P3**; disclosure via AM-5 tool strings; AM-8 rationale corrected | ANY scheduler row `enabled=false AND status IN (stopped,cancelled)` past retention goes, shape-agnostic; distinct `paused` status REJECTED for v1 (would force a sealed-registry :1463 re-pin — routed). **Ratification conditions (binding):** AM-5 strings land VERBATIM at scheduling.py :18/:236/:247; ceremony states PLAINLY that post-reap recovery is MANUAL FROM THE ARCHIVE (no restore surface ships in v1); operator-lever hint included (raise retention; "default 14d" softening — advisory B). `cleanup_inactive_mappings` is age-only incl. orphans (repository.py:689-715) — disjointness is operational, fixture teeth via W5 chat-mapping rows. |
| **D3** | **Steady-state code + SP-1 promote recipe ADOPTED (LEADER RULING 1)** | No coded backfill, no manual SQL. **Recipe:** at promote set `SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS=7` (floor 7 — resume-after-reap is a bare `ValueError`, scheduling_service.py:989-991), optional one-boot `DRY_RUN=true` observe pass, bulk of ~100 rows collected within ~4h (batch cap 25 ⇒ ~4 ticks), then restore 14. Commission sentence is **recipe-satisfied, not default-satisfied** (AM-6 annotation). |
| **D4** | **New api.py-lifespan loop sweep WITH kill-switch — CONFIRMED**; wiring seams fixed per architect §3 (W1 re-point applied) | `SourcesGcService` (TmpImageCleanupService shape, first-tick-immediate, never starves); `SERVICES_SOURCES_GC_ENABLED` default True (terminalizing-write class — mission-strand-heal counter-precedent); insert after **api.py:1866**, shutdown mirror at the **block boundary after :3976** (W1 re-point — the architect's :3966 sat mid-block), accessor **manager.py:1693**; AM-10: `to_thread` on BOTH repo calls (scheduling_service :882/:1114 precedent). `SourceCleanup` untouched (routed). |
| **D5** | **Destructive-on-promote (`dry_run` default False) — LEADER RULING 2; observability per architect §5 FINAL; ERROR rows EXCLUDED (LEADER RULING 3 / SP-5)** | Fence = dev-proof + kill-switch + repo predicate + AM-1; one-boot live observe rides the SP-1 recipe optionally. Final defaults `ENABLED=true / DRY_RUN=false / RETENTION=14`. Dry-run ALWAYS-logs candidates+reasons; destructive logs IFF work/skips/remainder + one-time first-destructive receipt; no-op ticks SILENT; durable trace = archive tables (no new audit substrate, no metric in v1 — both routed). ERROR exclusion mechanism-evidenced (registry.py:869-871 + routers/sources.py:408-415 sequence) with `observed_error_rows` counter. |
| **D6** | **Repo-level bulk method — CONFIRMED, hardened by AM-1/AM-2/AM-9/AM-11**; REST untouched | `gc_find_candidates(*, retention_days, now, limit)` ORDER BY `updated_at` ASC (AM-2); `gc_archive_and_delete(*, source_id, now, dry_run)` ONE txn: **locked re-SELECT (`with_for_update`, instance/repository.py:1988-1992 idiom) + all-four-leg re-assert incl. fresh age** (AM-1 CRITICAL — a mid-sweep resume must never silently delete a just-re-armed schedule) → plain-INSERT archives (AM-3) → child-first deletes → commit; not-found/skipped never raise; batch cap 25/tick; predicate hardcoded `source_type='scheduler'` at repo level (chat fence structural); REST refusal untouched, no new routes. |

## 3. Safety fences (non-negotiable, from the leader)

1. **Chat fence** — only `source_type='scheduler'` candidates. The trap (telegram+slack are `enabled=true, status='stopped'`) is defeated by BOTH legs: `source_type='scheduler'` (hardcoded, no parameter) AND `enabled=false`. Enforced at repo level.
2. **enabled=true fence** — predicate requires `enabled=False`. No path deletes an enabled row.
3. **Age/retention fence** — `updated_at` (ISO TEXT, frozen at terminal transition — no further repo writes reach terminal rows; active schedules re-stamp every fire) parsed in Python; `age > retention` required; **unparseable/missing `updated_at` → SKIP + count** (fail-MISS, never delete on unprovable age). Config floor `ge=1` days + repo-side re-check.
4. **Terminal-status fence (defense-in-depth)** — `status IN ('stopped','cancelled')` also required, so a mid-transition row (`enabled=false` but status still `running`/`starting`/`error`) is never collected. Live data: all 100 inert rows satisfy both legs.
5. **Dry-run-first proving** — destructive path shipped only after the tester proves non-destructiveness on a live-sized dev fixture (§6).
6. **AM-1 race fence** — the delete txn never acts on stale candidacy: in-txn `with_for_update` row lock + re-assert of ALL FOUR predicate legs (incl. age re-parsed from fresh `updated_at`) + rowcount-0 raced-skip. A mid-sweep resume can never be silently deleted.

## 3b. Promote ceremony recipe (SP-1, LEADER RULING 1 — rides the ceremony disclosure notes; W4 wording fixed per plan council)

> At promote, operator: set `SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS=7` (floor 7 — do NOT go lower; resume-after-reap is a bare `ValueError("Schedule not found")`, scheduling_service.py:989-991) and optionally `SERVICES_SOURCES_GC_DRY_RUN=true`. **Boot 1** (the promote restart itself): the first-tick dry-run emits the operator's receipt — **COUNTERS + SAMPLED ROWS** (`candidates=<N> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred` + ≤5 per-row lines per tick, ≤25 candidates per tick — NOT "every candidate": W4(a)); flip `DRY_RUN` off. **Boot 2** (+1 restart): collects all rows terminal ≥7d (median age ~10d ⇒ most of the ~100 rows; batch cap 25/tick ⇒ ~4 ticks / ~4h). **Boot 3** (+1 more restart): restore `RETENTION=14`. **W4(b): the recipe costs the promote restart + 2 further restarts** — BOTH knobs (`DRY_RUN` and `RETENTION`) are restart-required; the collect window at 7d must complete BEFORE the restore, so the two flips cannot be merged. **W4(c) completion signal:** watch the work-log for **`remainder=0 deferred`** — that line means every ≥7d-terminal row is collected and RETENTION can be restored. Emperor-facing trade, stated plainly: do nothing → clutter clears by ~2026-11-21; run the recipe → clutter clears within hours of promote at the cost of two documented env steps at an already-scheduled ceremony. NO coded backfill, NO manual SQL — this recipe is the sanctioned resolution of the "first real GC pass collects the ~102" commission sentence (recipe-satisfied, not default-satisfied).

## 4. Gate chain / phase table

| Phase | Gate | Entry | Exit |
|-------|------|-------|------|
| P1 Plan (this set) | — | commission + forensics + code audit | plan set (overview + decisions + phase4) on branch; D1-D6 resolved; anchors verified — **DONE** |
| P2 Architect | architect review | plan set @ branch tip | **PASSED — SOUND-WITH-AMENDMENTS** (1 critical AM-1 + AM-2..AM-11); all folds landed in v2 |
| P3 Reviewer council | governor council | **FOLDED v2 set** | **PASSED — APPROVE-WITH-ADVISORIES 0C/5W/5A; D2 RATIFIED WITH CONDITIONS; no re-council.** All W1-W5 + A-E folded → v3 set |
| P3b Fresh approver | fresh approver | **v3 re-folded set (this)** | APPROVED (cap 3 iterations → leader disposition per house rule) |
| P4 Implementation | — | approved plan | migration + models + repo methods (AM-1 race-safe + advisory-A DELETE pin) + service (AM-10 to_thread, W3 latch, E docstrings) + config knobs + api.py wiring (:1866 / :3976-boundary seams) + scheduling.py AM-5 verbatim strings + unit tests (**P1-P21**); scoped corpus green |
| P5 Code council + tidier | governor council | implementation PR-ready | 0 critical folded; tidier PASS (or disclosed/routed) |
| P6 Tester | functional + staged | code-closed tip | functional gate PASS (dry-run non-destructiveness + destructive correctness + fence/mutation legs incl. AM-1 race leg + boot-no-change on live-sized dev fixture); staged verification PASS |
| P7 Giter stage | release staging | tester GO | W9 slot derived AT STAGE TIME (plan pre-claims NO version); tag+push per house convention; **promote = user 3-factor ceremony** — SP-1 recipe rides the ceremony disclosure notes |

## 5. Seal-trip scope statement

**Expected: EMPTY.** `SEALED_SHA_BASELINES` (tests/test_chart_image_delivery_e2e.py:1425-1578, 35 rows) pins `daemon/sources/base.py`, `daemon/sources/registry.py` (row :1463), `daemon/sources/dispatcher.py`, 3 chat adapters, and `daemon/constants.py`. The design touches **none of them**:

- new `daemon/services/sources_gc_service.py` (unsealed dir)
- `daemon/config.py` (not sealed), `daemon/api.py` (not sealed), `daemon/tools/scheduling.py` (AM-5 strings — NOT in the pinned set, verified against the pin census)
- `daemon/repositories/source/repository.py` + `models.py` (**ZERO pins** on repositories/source/*)
- new migration file

**`registry.py` is NOT edited** (boot filter untouched — deleting stopped/cancelled rows changes no boot behavior; the only observable delta is the cosmetic "Scheduled auto-start for N adapters" count at registry.py:339-342, which never counted them). No re-pin owed. If implementation drift into registry.py, the standing rule fires: same-commit re-pin of row :1463 + 35-count discipline. Guarded by the boot-no-behavior-change pin (§6) + a file-level `git diff --stat daemon/sources/registry.py` assertion in the tester pack. **Architect-confirmed:** `SEALED_SHA_BASELINES` (tests/test_chart_image_delivery_e2e.py:1425, 35 rows) pins zero rows on any touched path (phase4-plan §1 NOT-TOUCHED list).

## 6. Test strategy (summary — full pin/mutation table in phase4-plan.md §7)

- **Fixture:** live-sized dev fixture — 100 scheduler rows (ages 0.5h→30d spread around the 14d boundary, incl. 10 rows <24h; shapes mixed one-shot/cron; statuses 93 stopped + 5 cancelled + 2 cron-stopped) + 3 chat rows (incl. the `enabled=true, status='stopped'` trap) + 110 executions + 100 mappings 1:1. Tester must assert the counts it built.
- **Fence pins (each with a mutation leg that flips RED):** chat fence, enabled=true fence, age/retention fence (boundary ±1s), terminal-status fence, repo-level predicate (direct repo calls with hostile args), batch cap, dry-run non-destructiveness (all table counts identical pre/post dry-run), archive 1:1 integrity, child-first FK order (real PG leg), boot-no-behavior-change (`registry.start_all` over post-GC fixture), knob fail-fast (`ge=1` ValidationError), kill-switch no-op.
- **Architect-added pins (v2):** **P16** resume-mid-sweep race (AM-1) — **W2 re-scope (plan council): unit mutation covers the RE-ASSERT half ONLY** (`with_for_update` no-ops on SQLite where the unit pack runs — a lock-removal mutation can never fire there); the LOCK half is a real-PG two-connection interleave leg (GC holds lock; concurrent resume blocks → 404 shape, scheduling_service.py:1119-1121) or an explicit inspection-verified disclosure — both branches recorded; **P17** oldest-first ordering (AM-2); **P18** archive-PK collision (AM-3) **+ W3 latch (second tick same id = silent count)**; **P19** two-write crash-window shape `(stopped, enabled=true)` excluded (AM-9) **+ advisory D inverse parked-row shape asserted excluded**; **P20** `SQLModel.metadata` registration (AM-11). Full pin × mutation table: phase4-plan §7.
- **Plan-council additions (v3):** **advisory A** — the final DELETE pins the locked `updated_at` (`AND updated_at = :locked_value`), closing the SQLite micro-window and giving P16's re-assert mutation a SECOND teeth source; **P21/W5** — the fixture plants ~3 chat-source `instance_mappings` (incl. one >30d) and asserts them INTACT post-GC (the AM-8-corrected fact makes the assertion vacuous without them); mutation = table-wide mappings DELETE → RED.
- **Dry-run proof before destructive ship:** dev daemon run with `DRY_RUN=true` over the live-sized fixture → candidate report + zero row deltas; then destructive run on the same fixture → expected counts deleted, archive rows present, chat rows intact.
- **Boot smoke:** env-poison rules apply (per-variable `env -u` scrubs — never globs; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; `ENSEMBLE_SELF_ENV=sandbox`; binary COPIED to scratch).

## 7. FE-effect statement (verified in-worktree)

- `frontend/src/app/services/api.service.ts:477-479` — `listSources()` = plain `GET ${API_BASE}/sources`.
- `frontend/src/app/components/source-list/source-list.component.ts:46-61` — `loadSources()` sets `this.sources.set(response.sources)` directly; **no client-side filter, no cache**; fresh GET per component init.
- Server side: `daemon/routers/sources.py:126-133` — `list_sources` returns ALL `source_configs`, no filter.
→ Row deletion shrinks the list naturally. **NO list-filter hack, NO FE change ships.** Confirmed.

## 8. Migration census implications (D1)

- Worktree census @ 6b571771d: **122 files**, latest prefix `20261105_102821_create_memoir_tables.sql`; next free prefix family `20261107_*`. **Lane-authored delta: exactly +1 file** → 123.
- Additive only: `CREATE TABLE IF NOT EXISTS` ×2 + `CREATE INDEX IF NOT EXISTS`; **every string column TEXT** (never VARCHAR); **NO FK to any existing table** (archive `schedule_id`/`source_id` are plain TEXT — house archive/audit precedent: memoir's only FK is intra-table self-ref; notify_audit/MaintenanceRun column-shape precedent).
- Standing rule (quoted from the memoir migration template, 20261105_102821 header): *"SQLite (this file): runs via the .sql migration runner on every startup (idempotent CREATE TABLE IF NOT EXISTS)… PostgreSQL: the schema comes from `SQLModel.metadata.create_all` with the models… as the canonical source… The PG-side runner is a NO-OP for this file."* create_all runs FIRST, runner immediately after; bare CREATE would collide → IF NOT EXISTS mandatory.
- DOWN section: children-first, never executes on rollback (`rollback_migration()` has zero production callers; runner SQLite-only).
- **Delta vs live:** live binary census is 121 (v0.28.16) or 122 (post-v0.29.0 promote, ceremony order unknown at plan time). Our authored delta is exactly +1 either way; giter states the full census at stage time. **Non-attribution note (SP-6.7):** the pre-existing fresh-SQLite blocker `20260714_000001` (ALTER…DROP CONSTRAINT) is UNRELATED to this lane — the staged gate must not misattribute it.

## 9. Rollback-safety reasoning (house standing rule)

`rollback_safe=true source=explicit`: additive-only (CREATE TABLE IF NOT EXISTS + indexes, zero ALTER, zero DROP of existing objects, no FK to existing tables); the old binary references no archive tables → **inert post-rollback** (auto-rollback may fire safely; consequence = FIX-LOSS, not incompatibility). Behavior side: `SERVICES_SOURCES_GC_ENABLED=false` reproduces pre-GC behavior exactly (service no-op), and dry-run ticks write nothing — the destructive surface is knob-gated behind a default-on-but-dry-run-provable path.

## 10. Observability contract (architect §5 FINAL — answers "3-of-4 GC services leave zero trace")

1. **Start INFO once** (api.py wiring): `[SourcesGC] started: enabled=<b> dry_run=<b> interval=<N>s retention=<N>d batch_cap=<N>`; when disabled: `[SourcesGC] disabled (SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run` (no task scheduled).
2. **Dry-run ticks ALWAYS log**: `[SourcesGC] dry-run: candidates=<N> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred` + ≤5 per-row lines `(id, name, one-shot|cron, status, age_d)`.
3. **Destructive ticks log IFF work or skips or remainder**: `[SourcesGC] collected=<N> archived_configs=<N> archived_executions=<M> mappings=<X> processed=<Y> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred duration_s=<S> sample=[ids≤5]`.
4. **One-time marker**: `[SourcesGC] first destructive sweep performed at <iso> (retention=<N>d, collected=<N>)` — the operator's post-promote receipt.
5. **No-op destructive tick: SILENT** (tmp-image discipline, tmp_image_cleanup_service.py:303-317 — an hourly count=0 heartbeat is 24 noise lines/day adding nothing).
6. **Per-row failure**: WARNING `(source_id, exception-type)` + continue (per-source txn isolates; AM-3's IntegrityError archive-collision catch surfaces here as skip).
7. **Durable trace = the archive tables** (`archived_at`-stamped, queryable). No new audit substrate: maintenance_runs rejected (single-flight lifecycle models user-triggered runs — an autonomous hourly sweep would mint ~8.7k rows/yr needing their own GC); central metric `sources_gc_deleted_total` = routed follow-up if the metrics lane wants it.
- Boot-no-behavior observable: registry's "Scheduled auto-start for N adapters" count is unaffected (stopped rows never counted).

## 10b. Open questions — CLOSED by the architect gate (§10 of architecture-recommendation.md)

- OQ-1 archive breadth: **both tables** (confirmed) **minus credentials column** (AM-4).
- OQ-2 ERROR rows: **exclude** (SP-5, mechanism-evidenced) + `observed_error_rows` counter.
- OQ-3 SourceCleanup: untouched (confirmed; routed follow-up).
- OQ-4 chat-mappings interplay: disjoint — **operationally** (AM-8 rationale correction: `cleanup_inactive_mappings` sweeps ALL mappings age-only incl. orphans; chat mappings survive because chat sources are never collected).
- OQ-5 first-destructive marker: **yes** (AM-7 item 4), with candidate count added.
- Leader-facing §8 decisions of the architect doc are CLOSED by the three leader rulings (SP-1 recipe ADOPTED; D5-final destructive-on-promote; ERROR exclusion) — recorded in decisions.md.

## 11. W9 note

The version slot is derived by the giter at stage time from the live census (tags × releases × bump). **This plan does NOT pre-claim a version number.**

## 12. Anchor table (worktree-verified @ 6b571771d — planner set + architect-introduced anchors re-verified during the fold)

| Anchor | File:line | Verified content |
|---|---|---|
| SourceCleanup shape | daemon/sources/cleanup.py:12-22, :46, :51-52, :74-76 | ctor `interval_hours=6`; `sleep(60)` first tick; logs only on delete; `run_once()` zero production callers |
| SourceCleanup wiring | daemon/manager.py:15966-15969, :15990-15992 | `start_sources` construct+start; teardown in `stop_sources` |
| updated_at re-stamp | daemon/repositories/source/repository.py:140 | ISO TEXT, unconditional on any `update_source_config` write (incl. enabled-only) |
| cancel_source_config | daemon/repositories/source/repository.py:465-511 | atomic (enabled=false, cancelled); docstring: cancel NEVER routes through delete (history doctrine) |
| delete_source_config | daemon/repositories/source/repository.py:513-545 | sole purge path; child-first ONE-txn mappings→processed→executions→config; repo-UNGUARDED |
| FK: instance_mappings | daemon/repositories/source/models.py:113 | `source_id` FK → source_configs, NOT NULL, NO ACTION |
| FK: schedule_executions | daemon/repositories/source/models.py:168 | `schedule_id` FK, NOT NULL, NO ACTION |
| processed_messages NO FK | daemon/repositories/source/models.py:142-150 | plain composite PK, no FK |
| SourceStatus enum | daemon/repositories/source/models.py:20-30 | STOPPED/STARTING/RUNNING/ERROR/CANCELLED — no 'fired' |
| updated_at column | daemon/repositories/source/models.py:82 | ISO TEXT |
| list_source_configs | daemon/repositories/source/repository.py:420 | no-args → ALL rows |
| boot reader + filter | daemon/sources/registry.py:291, :295-297, :300-302, :312-314, :339-342 | ALL rows load; disabled/non-autostart/stopped+cancelled skips; cosmetic count |
| one-shot consumed → terminal | daemon/sources/registry.py:518-532 | on_complete → (stopped, enabled=false) |
| user-pause → same shape | daemon/services/scheduling_service.py:1101-1102, :1130 | pause sets (enabled=false, 'stopped'); explicit status write |
| REST refusal | daemon/routers/sources.py:45-73 (helper), :442-450 (DELETE guard + ADR-012 comment) | scheduler rows refused 400 SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED |
| GET /api/sources | daemon/routers/sources.py:126-133 | returns all rows, no filter |
| ServicesConfig | daemon/config.py:1907, env_prefix :1910 | `SERVICES_` prefix, flat Fields, ge=1 fail-fast |
| tmp-image knobs + amendment-#15 doctrine | daemon/config.py:2813-2864 | no-kill-switch HARD POLICY rationale (regenerable data) |
| strand-heal kill-switch counter-precedent | daemon/config.py:2720-2790 | terminalizing-write lane ships ENABLED knob + age_bound + batch_cap=25 |
| tmp-image exemplar wiring | daemon/api.py:1847-1865 | construct→start()→app.state→start INFO w/ knobs; first tick IMMEDIATE (:1843-1845 comment) |
| shutdown mirror | daemon/api.py:3961-3975 | getattr-guarded stop + WARNING |
| tmp-image service shape | daemon/services/tmp_image_cleanup_service.py:84, :246, :303-311 | `_REAP_SAMPLE_IDS_MAX=5`; `sweep_once`; no-op ticks silent |
| scheduler config-keys reader | daemon/sources/adapters/scheduler.py:501-570 (:511, :513, :546, :563) | `run_at`=one-shot, `schedule`=cron; writer emits only these two |
| memoir migration template | daemon/migrations/versions/20261105_102821_create_memoir_tables.sql:1-73 | dual-driver notes, TEXT-only columns, IF NOT EXISTS mandatory, create_all-first standing rule |
| MaintenanceRun audit precedent | daemon/repositories/maintenance_runs/models.py:50-81 | single-flight partial unique index (considered, not chosen — see D5) |
| FE list fetch | frontend/src/app/services/api.service.ts:477-479; frontend/src/app/components/source-list/source-list.component.ts:46-61 | plain GET; direct set; no filter/cache |
| migration census | daemon/migrations/versions/ (ls) | 122 files, latest 20261105_102821 |
| **AM-1 resume-after-reap ValueError** | daemon/services/scheduling_service.py:989-991 | `_resolve_schedule_ref` → `raise ValueError(f"Schedule not found: …")` — the 7d-floor rationale |
| **AM-1 writer census** | daemon/services/scheduling_service.py:1104→:1114-1121 (resume), :1108-1110 (rebuild re-stamp) | resume rewrites enabled=true; rebuild re-stamps a still-paused row |
| **AM-1 row-lock idiom** | daemon/repositories/instance/repository.py:1988-1992 (use), :1949-1954 (SQLite no-op render) | `Session.get(..., with_for_update=True)` house idiom |
| **AM-1 lock-holder releases** | daemon/services/scheduling_service.py:1119-1120 | concurrent resume blocks on the locked row then honestly 404s |
| **AM-3 REST-create reachability** | daemon/routers/sources.py:135-165 (:153 `"scheduler"` in supported_types) | generic create accepts scheduler + caller-supplied id → archive-PK collision reachable |
| **AM-4 credentials facts** | daemon/routers/sources.py:257 (encrypt-before-insert) | chat credentials Fernet at rest; scheduler rows always NULL |
| **AM-5 disclosure seam** | daemon/tools/scheduling.py:18 (docstring), :236, :247 (`paused` field descriptions) | pause is RESUMABLE today — strings gain the reap disclosure |
| **AM-5 REST-no-pause** | daemon/models/schedule.py:114-128 | `ScheduleUpdate` has NO `paused` field — REST cannot pause |
| **AM-5 rejected alternative** | registry.py boot filter (sealed :1463) | a distinct `paused` status would force a re-pin — routed |
| **SP-5 error writer** | daemon/sources/registry.py:869-871 | `update_source_status(..., ERROR)` on adapter crash — the only scheduler-config error writer |
| **SP-5 operator-disable PUT** | daemon/routers/sources.py:408-415 | generic PUT writes enabled without status |
| **SP-5 dispatch failures** | daemon/sources/adapters/scheduler.py:998-1004 (execution row), :806-812 (consumption gate) | failures land on EXECUTION rows; failed/re-armed one-shots never consumed |
| **AM-8 mappings sweep** | daemon/repositories/source/repository.py:689-715 (:699-702) | `cleanup_inactive_mappings` = age-only over ALL mappings incl. orphans |
| **AM-9 two-write windows** | daemon/sources/registry.py:525-526; daemon/tools/self_alarm.py:1108-1109 | crash between the two writes leaves `(stopped, enabled=true)` — excluded by enabled fence |
| **AM-11 registration chain** | daemon/manager.py:57 → daemon/repositories/__init__.py:16-17 → source/models.py module exec; create_all manager.py:999; accessor :1693 | zero manager.py change; unit assertion makes it durable |
| **SP-6 runner posture** | daemon/migrations/runner.py:742 (`rollback_migration`, zero production callers), :816+ (SQLite-only / PG structural NO-OP) | DOWN never executes; `20260714_000001` blocker unrelated |
