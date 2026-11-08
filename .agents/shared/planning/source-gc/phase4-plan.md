# source-gc — Phase 4 Implementation Plan (POST-ARCHITECT FOLD, v2)

Entry: approver-APPROVED plan (architect gate PASSED — SOUND-WITH-AMENDMENTS, all folds landed; see `architecture-recommendation.md` §3/§4/§5, folded here as binding).
Exit: implementation complete, scoped corpus green, ready for code council + tidier (P5).

**Fold status:** AM-1..AM-11 all landed (map at bottom). The architect's §3 wiring-seam inventory is the developer's BINDING inventory (folded verbatim in §1); its §5 observability contract is final (§8).

---

## 1. Implementation surface inventory (architect §3 folded verbatim — binding)

| Seam | File:line | Content |
|---|---|---|
| api.py start block | daemon/api.py — insert immediately after **:1866** (tmp-image INFO end; live-view block begins :1867) | `from daemon.services.sources_gc_service import SourcesGcService`; construct from `config.services.*` (exemplar construct :1850-1857, `.start()` :1858, `app.state.tmp_image_cleanup` :1859, start INFO w/ knobs :1860-1866); first-tick-immediate family shape (comment :1843-1845) |
| api.py shutdown mirror | daemon/api.py — insert at the BLOCK BOUNDARY: after **:3976** (`app.state.tmp_image_cleanup = None`), before the PlaneSyncWatchdog comment at **:3978** (:3977 is the blank separator). **W1 re-point (plan council): the earlier :3966 anchor sat INSIDE the TmpImageCleanupService shutdown block — literal compliance would splice mid-block.** **Implementer note:** line numbers drift; grep ONCE at implementation time (`grep -n "app.state.tmp_image_cleanup = None" daemon/api.py`) and pin the verified number in the lane notes before inserting. Content: getattr-guarded `app.state.sources_gc` → `stop()` → WARNING on error → state clear |
| Repo accessor | daemon/manager.py:**1693** | `self._source_repository = create_source_repository(engine=self._engine, create_tables=False)` — same accessor routers/sources.py uses (:62, :130, :463…) |
| create_all chain | daemon/manager.py:**999** (create_all); :57 → daemon/repositories/__init__.py:**16-17** → source/models.py module execution registers ALL SQLModel classes (proof: `ScheduleExecution` is absent from the name list yet registered) | Zero manager.py change; AM-11 makes it a unit assertion |
| ServicesConfig knobs | daemon/config.py:**1907** (class), **:1910** (`env_prefix="SERVICES_"`) | `sources_gc_enabled=True` · `sources_gc_dry_run=False` · `sources_gc_interval_seconds=3600 (ge=1)` · `sources_gc_terminal_retention_days=14 (ge=1; repo re-checks ≥1)` · `sources_gc_batch_cap=25 (ge=1)` — flat Field + fail-fast pattern verified pervasive (:2167…:2773); precedents: strand-heal trio :2736-2790 (kill-switch class), tmp-image :2813-2830 (no-switch class) |
| Service file | daemon/services/sources_gc_service.py (NEW) | ctor `(source_repo, *, interval_seconds, retention_days, batch_cap, dry_run, enabled)`; `start()/stop()/sweep_once()`; first-tick-immediate; module-level `_REAP_SAMPLE_IDS_MAX = 5` (house name per tmp-image precedent :84 — **advisory E name correction**); `[SourcesGC]` f-string prefix; defensive except → WARNING + continue; **W3 latch:** module-level `_ARCHIVE_COLLISION_WARNED: set[str]` (per-source-id first-warning latch — see §2 AM-3); service docstring states **advisory E's fact: deletion needs NO adapter stop/unregister — the boot filter already skips terminal rows (registry.py:312-314), so a GC'd source can never have a live adapter in the registry** |
| Repo methods | daemon/repositories/source/repository.py (beside delete_source_config :513-545) | `gc_find_candidates(*, retention_days, now, limit) -> tuple[list[GcCandidate], int]` (SQL prefilter type/enabled/status; Python age via `fromisoformat`; unparseable → skip+count; **ORDER BY updated_at ASC per AM-2**). `gc_archive_and_delete(*, source_id, now, dry_run) -> GcRowResult` — ONE txn: **locked re-SELECT (`Session.get(..., with_for_update=True)` house idiom, instance/repository.py:1988-1992; SQLite renders no lock per :1949-1954) + four-leg re-assert from the locked row (AM-1) — source_type, `enabled=false`, terminal status, age re-parsed from fresh `updated_at`** → archive INSERTs (config + executions, **plain per AM-3**; **W3:** collision → `IntegrityError` caught → first warning per source_id via the latch, then count-silent → skip) → DELETE instance_mappings (:522-524 order) → processed_messages (:526-528; parity no-op today — scheduler sources produce zero rows; keep for delete_source_config parity) → schedule_executions (:530-532) → source_config (**advisory A: `AND updated_at = :locked_value` pinned into the WHERE** — rowcount-0 ⇒ raced-skip, closes the SQLite micro-window) → commit (:535). Not-found → skipped (mirror delete_source_config `{"deleted": False, "error": "Not found"}`, repository.py:517-519), never raise; any re-assert leg fails → skipped+counted. Dataclasses: `GcCandidate(source_id, name, status, updated_at, age_days, config_json)` — **advisory C: `config_json` is `json.dumps()` of the live config dict and is the SOLE reconstruction input for a restored schedule** (no restore API exists; update-of-missing raises); shape derived at SERVICE layer from config keys (`run_at`/`schedule`, scheduler.py:511/:513); `GcRowResult(source_id, deleted, dry_run, archived_config, archived_executions, deleted_mappings, deleted_processed, deleted_executions, skipped_reason, error)` |
| Archive models | daemon/repositories/source/models.py (ADD 2 classes) | `ScheduleExecutionArchive`, `SourceConfigArchive` — `__table_args__` index names byte-match the .sql (3-site lockstep); bools as SQLModel `bool` (INTEGER affinity); **NO `credentials` column (AM-4)** |
| Migration | daemon/migrations/versions/`20261107_HHMMSS_create_source_gc_archive_tables.sql` (NEW; 122→123, unique prefix) | §2 spec minus `credentials` (AM-4); memoir-template dual-driver header; IF NOT EXISTS everywhere; TEXT-only; no FK; DOWN children-first (never executes) |
| Pause-disclosure strings (AM-5) | daemon/tools/scheduling.py **:18** (module docstring), **:236** + **:247** (`paused` field descriptions) | Land **VERBATIM (D2 ratification condition 1)**: *"pause (resumable; a schedule left paused past the default 14d retention is garbage-collected — history archived, config removed; resume before then is untouched. To protect a paused schedule, raise SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS; recovery after a reap is manual, from the archive.)"* — `tool_help` inherits. Carries advisory B (escape-hatch hint, "default 14d" softening) + D2 condition 2 (manual-from-archive truth). REST needs NO change: `ScheduleUpdate` has no `paused` field (daemon/models/schedule.py:114-128) — pause is tool-only |
| **NOT touched** | daemon/sources/registry.py (sealed :1463) · daemon/sources/cleanup.py · daemon/manager.py · daemon/routers/sources.py (REST refusal :442-450 intact) · daemon/constants.py (sealed) · daemon/services/scheduling_service.py · frontend/** | **Seal-trip scope EMPTY** — `SEALED_SHA_BASELINES` (tests/test_chart_image_delivery_e2e.py:1425, 35 rows) pins zero rows on any touched path; module-level constants in the service file only |

Test files (planner-invented, unchanged in kind): `tests/unit/repositories/test_source_gc_repository.py`, `tests/unit/services/test_sources_gc_service.py`.

## 2. Archive schema (migration spec — AM-4 applied)

```sql
-- Migration: create source-GC archive tables (schedule_executions_archive,
-- source_configs_archive) — source-gc lane (board wi-ed230eb4).
-- Additive-only: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- NO FK to any existing table (archive ids are plain TEXT — memoir
-- precedent: house archive tables keep no FK to the table they record).
-- Dual-driver notes per house standing rule (memoir template header):
--   SQLite: this file runs idempotently on every startup (runner).
--   PostgreSQL: SQLModel.metadata.create_all with the models in
--   daemon/repositories/source/models.py as canonical source; the
--   PG-side runner is a NO-OP for this file. create_all runs FIRST,
--   runner immediately after — IF NOT EXISTS mandatory.
--   EVERY STRING COLUMN IS TEXT — never VARCHAR.
CREATE TABLE IF NOT EXISTS schedule_executions_archive (
    execution_id  TEXT PRIMARY KEY,
    schedule_id   TEXT NOT NULL,
    triggered_at  TEXT,
    instance_id   TEXT,          -- survives the unmapped instance_mappings delete (architect §3 disclosure)
    status        TEXT,
    error_message TEXT,
    completed_at  TEXT,
    archived_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_executions_archive_schedule_id
    ON schedule_executions_archive (schedule_id);
CREATE INDEX IF NOT EXISTS idx_schedule_executions_archive_archived_at
    ON schedule_executions_archive (archived_at);

CREATE TABLE IF NOT EXISTS source_configs_archive (
    source_id     TEXT PRIMARY KEY,
    source_type   TEXT,
    name          TEXT,
    config        TEXT,          -- JSON-serialized snapshot (the only reconstruction input — SP-4)
    enabled       INTEGER,       -- boolean per driver
    autostart     INTEGER,
    status        TEXT,
    error_message TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    archived_at   TEXT NOT NULL
);                               -- AM-4: NO credentials column (scheduler rows are always NULL; chat rows unreachable)
CREATE INDEX IF NOT EXISTS idx_source_configs_archive_archived_at
    ON source_configs_archive (archived_at);
-- DOWN (never executes on rollback): children first
-- DROP INDEX IF EXISTS idx_source_configs_archive_archived_at;
-- DROP TABLE IF EXISTS source_configs_archive;
-- DROP INDEX IF EXISTS idx_schedule_executions_archive_archived_at;
-- DROP INDEX IF EXISTS idx_schedule_executions_archive_schedule_id;
-- DROP TABLE IF EXISTS schedule_executions_archive;
```

**AM-3 (archive PK policy):** plain INSERT, fail-loud — NEVER `ON CONFLICT`. Collision is REACHABLE (REST create accepts `source_type="scheduler"` + caller-supplied id, routers/sources.py:135-165; `create_source_config` honors caller ids). `IntegrityError` → per-row defensive catch → WARNING naming the source_id → skip+count, sweep continues. **W3 latch (plan council, binding):** collisions are PERMANENT (an archived terminal row is never re-collectable), so un-latched the sweep re-warns hourly ≈ 24 noise lines/day forever — the repo method keeps a module-level per-source-id latch (`_ARCHIVE_COLLISION_WARNED: set[str]`): FIRST collision per id → WARNING + counted; same id on later ticks → counted SILENTLY. Latch resets on daemon restart (acceptable: bounded by collided ids only). **Operator resolution (W3-extended):** re-create with a FRESH id, or purge the stale archive row. **Advisory C:** the model/method docstrings document that `config_json` = `json.dumps()` of the live config dict and is the SOLE reconstruction input for a restored schedule (no restore API exists — update-of-missing raises).

## 3. Config knobs — unchanged from v1 (see §1 seam row for the binding defaults/constraints); env names `SERVICES_SOURCES_GC_*` (note: leader recipe line uses `SERVICES_SOURCES_TERMINAL_RETENTION_DAYS` — implementation ships `SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS` per the architect inventory; the recipe text in decisions.md D3-final governs ceremony notes and must name the SHIPPED env var).

## 4. Service shape — v2 deltas

- `sweep_once()`:
  1. `candidates, skipped_unparseable = await to_thread(repo.gc_find_candidates, retention_days=..., now=..., limit=batch_cap)` (AM-10: to_thread)
  2. dry_run → ALWAYS log per architect §5.2 (counts + skip counters + `observed_error_rows` + remainder + ≤5 per-row reason lines) — NO writes.
  3. destructive → for each candidate: `await to_thread(repo.gc_archive_and_delete, ...)` (**AM-10: to_thread on EVERY call** — sync SQL on the loop is the exact class v0.27.14 cured); aggregate per `GcRowResult`; log IFF work/skips/remainder per §5.3; one-time first-destructive receipt marker (§5.4).
  4. per-row defensive except → WARNING `(source_id, exception-type)` + continue (per-source txn isolates; AM-3's IntegrityError catch lives inside the repo method's result, surfaced as skip).
- Return a sweep report dict for tests (incl. `remainder`, `observed_error_rows`, `skipped_unparseable`).
- `observed_error_rows`: cheap `COUNT(*)` of scheduler rows with `status='error'` (SP-5 — data for the phase-9.3-planner revisit trigger; folded into §1 knob/predicate reads).

## 5. Predicate (single source of truth — repo layer; architect §4 folded verbatim)

```
source_type == 'scheduler'            # hardcoded, no parameter (chat fence, structural)
AND enabled == False                  # enabled=true fence
AND status IN ('stopped','cancelled') # terminal-only ('error' excluded — SP-5)
AND age(now - fromisoformat(updated_at)) > retention_days
                                      # unparseable/missing → SKIP + count (fail-MISS)
-- SELECT legs prefilter in SQL; age in Python (103-row scale);
-- ALL FOUR legs re-asserted inside the delete txn under with_for_update (AM-1)
```

## 6. Wiring detail — architect §3 seams are binding (api.py after :1866 / shutdown at the W1-re-pointed boundary after :3976 / accessor manager.py:1693); service ctor takes `enabled` (False → `[SourcesGC] disabled (SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run`, no task scheduled).

## 7. Test strategy — v3: 15 planner pins + 5 architect pins (AM-1/2/3/9/11, P16-P20 with the W2 re-scope on P16) + 1 plan-council pin (**P21/W5**)

Fixture builder unchanged (live-sized: 100 scheduler rows around the 14d boundary incl. <24h rows, 3 chat rows incl. the `enabled=true, status='stopped'` trap, 110 executions, 100 mappings 1:1) **+ W5 (plan council, binding): plant ~3 `instance_mappings` rows belonging to the CHAT sources (mixed ages, at least one older than the 30d sweep threshold — SourceCleanup's `cleanup_inactive_mappings` is age-only over ALL mappings incl. orphans, repository.py:689-715, so the chat-mappings-intact assertion has real teeth only if such rows exist)**; fixture asserts its own counts before any sweep.

**New pins (each mutation-pinned per house doctrine):**

| Pin | Asserts | Mutation leg |
|---|---|---|
| **P16 (AM-1, CRITICAL)** | **W2 re-scope (plan council, binding):** UNIT mutation leg covers the **RE-ASSERT half ONLY** — `with_for_update` is a NO-OP on SQLite (instance/repository.py:1949-1954) where this pack runs, so a "remove the lock → RED" leg can NEVER fire there. Unit asserts: `gc_find_candidates` returns a row; the row is then resumed in a separate session (enabled=true re-write); `gc_archive_and_delete` on it → skipped+counted, row intact, nothing archived (re-assert + the advisory-A `updated_at` pin both fire here — **A gives this mutation a second teeth source**: mutating the re-assert OR the WHERE-pin flips it RED); and the belt-and-braces conditional DELETE with rowcount-0 → raced-skip. **LOCK half moves to the real-PG tester leg:** genuine two-connection interleave (connection A inside `gc_archive_and_delete` mid-txn holding the row lock; connection B runs the resume write → blocks; A commits; B's resume then 404s per scheduling_service.py:1119-1121 shape). **Recorded branch:** if the interleave proves impractical in the harness, the lock half ships as an EXPLICIT inspection-verified disclosure in the tester artifact (never silently dropped). Positive leg: un-resumed row deletes cleanly under the same lock | mutate the re-assert (drop a leg) OR the updated_at WHERE-pin → unit RED; the bare lock-removal mutation is NOT a valid unit leg (SQLite no-op) — lock teeth are PG-leg/disclosure only |
| **P17 (AM-2)** | 30 candidates with ages mixed → selected order `updated_at ASC`; cap 25 keeps the 5 NEWEST deferred | drop the ORDER BY → RED |
| **P18 (AM-3)** | pre-plant an archive row with a candidate's id → sweep → WARNING naming the id, fresh snapshot NOT absorbed, candidate row skipped+counted in errors, sweep continues; **W3:** second tick on the same id → counted SILENTLY (no log line), latch observable | wrap insert in ON CONFLICT DO NOTHING → stale-snapshot absorption → RED; remove the latch → second-tick duplicate WARNING → RED |
| **P19 (AM-9)** | two-write crash window: row shaped `(status='stopped', enabled=true)` (mid-transition of registry.py:525-526 / self_alarm.py:1108-1109) → excluded by the enabled fence, never collected; **advisory D inverse window:** row parked `(status='running', enabled=false)` post-archive-write → excluded (status leg), disclosed as permanently-uncollectable debt | drop enabled leg → RED; drop status leg → the parked row gets collected → RED |
| **P20 (AM-11)** | after `import daemon.repositories`: both archive models present in `SQLModel.metadata`; index names byte-match the .sql (3-site lockstep) | rename model index / drop registration → RED |
| **P21 (W5, plan council)** | **chat mappings fixture rows are NON-VACUOUS:** plant ~3 `instance_mappings` rows belonging to the chat sources (mixed ages incl. one older than 30d — the age-only sweep class) → post-GC assert all 3 INTACT (GC never deletes non-collected-source mappings); destructive and dry-run modes both | narrow the mappings DELETE to a table-wide form (drop the `source_id ==` filter) → chat mappings deleted → RED |

Retained pins P1–P15 unchanged in substance (chat fence, enabled fence, retention boundary, unparseable-age skip, terminal-status fence, repo predicate structural, batch cap, **P8 dry-run non-destructiveness — extended to assert `remainder` + `observed_error_rows` appear in dry-run output**, archive 1:1 integrity, child-first FK order on real PG, boot-no-behavior-change incl. the `git diff --stat daemon/sources/ daemon/constants.py` empty assertion, knob fail-fast, kill-switch, first-tick-immediate, log discipline — **P15 extended: no-op destructive tick SILENT, work-iff-work incl. remainder, first-destructive marker exactly once**).

**Tester integration legs (dev fixture, P6 gate):** unchanged from v1 (dry-run daemon pass → zero deltas; destructive pass → expected counts; FE effect via `GET /api/sources` shrink; acceleration leg now uses the ≥7d floor recipe shape — `RETENTION=7`, never 1; boot smoke with per-variable env scrub / `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` / `ENSEMBLE_SELF_ENV=sandbox` / binary copied to scratch / throwaway PG DB dropped / state.json fence PRE==MID==POST).

## 8. Observability contract (architect §5 FINAL — folded verbatim)

1. **Start INFO once** (api.py wiring): `[SourcesGC] started: enabled=<b> dry_run=<b> interval=<N>s retention=<N>d batch_cap=<N>`; when disabled: `[SourcesGC] disabled (SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run` (no task scheduled).
2. **Dry-run ticks ALWAYS log**: `[SourcesGC] dry-run: candidates=<N> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred` + ≤5 per-row lines `(id, name, one-shot|cron, status, age_d)`.
3. **Destructive ticks log IFF work or skips or remainder**: `[SourcesGC] collected=<N> archived_configs=<N> archived_executions=<M> mappings=<X> processed=<Y> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred duration_s=<S> sample=[ids≤5]`.
4. **One-time marker**: `[SourcesGC] first destructive sweep performed at <iso> (retention=<N>d, collected=<N>)` — the operator's post-promote receipt.
5. **No-op destructive tick: SILENT** (tmp-image discipline, tmp_image_cleanup_service.py:303-317).
6. **Per-row failure**: WARNING `(source_id, exception-type)` + continue (per-source txn isolates).
7. **Durable trace = the archive tables** (`archived_at`-stamped, queryable). No new audit substrate: maintenance_runs rejected (single-flight lifecycle models user-triggered runs — an autonomous hourly sweep would mint ~8.7k rows/yr needing their own GC); central metric family (`sources_gc_deleted_total`) = routed follow-up.
8. **W4(c) — operator completion signal (explicit):** the §5.3 work-log emits `remainder=<R> deferred`; during the SP-1 promote recipe (RETENTION=7 collection window), the operator watches for **`remainder=0 deferred`** as THE completion signal — that line means every ≥7d-terminal row has been collected and RETENTION can be restored to 14 (no further restarts needed beyond the restore itself).

## 9. Rollback & migration deltas — unchanged (SP-6 confirmed all legs): +1 file `20261107_*`; `rollback_safe=true source=explicit`; old binary inert on rollback; FIX-LOSS consequence; **staged gate must NOT misattribute the pre-existing fresh-SQLite blocker `20260714_000001` (ALTER…DROP CONSTRAINT) to this lane — unrelated**.

## 10. Routed follow-ups (architect §9 + plan-council round, out of scope)

1. Distinct `paused` status (enum + sealed-registry edit + backfill + FE) — only if the 14d reap generates user friction.
2. Archive GC trip-point: revisit at **~50k rows / ~50 MB / first slow forensic query**.
3. Metric counter `sources_gc_deleted_total` (central metrics lane) + archive read surface (read tool / maintenance console) — **includes any future restore surface; D2 condition 2: v1 recovery from a reap is MANUAL FROM THE ARCHIVE, no restore tooling ships**.
4. `SourceCleanup` refactor into the knob'd family (pre-existing).
5. Non-atomic two-write consumed transition (registry.py:525-526 / self_alarm.py:1108-1109) — own debt note.
6. Resume-after-reap copy: docs/FE note ("recreate from archive") beyond the AM-5 tool strings.
7. AM-8 note: `cleanup_inactive_mappings` (repository.py:689-715) is an age-only sweep over ALL mappings incl. orphans — chat mappings survive GC operationally (chat sources never collected), **asserted with teeth via P21/W5 fixture rows**.
8. **Advisory D (plan council) — inverse crash-window debt, disclosed:** a row parked at `(status='running', enabled=false)` between the archive-write and the delete commit is SAFE (status leg excludes collection) but PERMANENTLY UNCOLLECTABLE — its `updated_at` froze at the park moment, no later writer re-stamps a terminal row, so age grows without end while the status leg keeps it out. Pre-existing two-write non-atomicity class (same family as item 5); no v1 action.

## 11. Fold confirmation map

**Round 1 — architect gate (AM-1..AM-11):**

| AM | Landed in |
|---|---|
| AM-1 (critical) | decisions.md D6-final; §1 repo-method row; §5 predicate note; §7 P16 |
| AM-2 | decisions.md D6-final; §1 ORDER BY ASC; §7 P17 |
| AM-3 | decisions.md D1-final; §2 spec note; §7 P18 |
| AM-4 | decisions.md D1-final; §2 spec (credentials absent); §1 model row |
| AM-5 | decisions.md D2-final; §1 disclosure-seam row |
| AM-6 | decisions.md D3-final (recipe + commission-sentence annotation) |
| AM-7 | decisions.md D5-final; §8 observability FINAL; §4/§7 counter wiring |
| AM-8 | decisions.md D2-final + §10.7 corrected rationale; fixture pin retained |
| AM-9 | decisions.md D6-final; §7 P19 |
| AM-10 | decisions.md D4-final; §4 to_thread on both calls |
| AM-11 | decisions.md D6-final; §7 P20; §1 create_all-chain row |

**Round 2 — plan council P3 (W1-W5, A-E, D2-conditions):**

| Item | Landed in |
|---|---|
| W1 anchor re-point | §1 shutdown-mirror row (after :3976 block boundary, before :3978 PlaneSyncWatchdog comment) + grep-once implementer note |
| W2 P16 re-scope | §7 P16 (unit mutation = re-assert half ONLY; lock half → real-PG two-connection interleave leg OR explicit inspection-verified disclosure — both branches recorded) |
| W3 collision latch | decisions D1 AM-3 note; §2 AM-3 handling + §1 repo-method row (`_ARCHIVE_COLLISION_WARNED` latch) + §7 P18 latch mutation; operator-resolution note extended |
| W4 recipe fixes | overview §3b ((a) receipt = counters + sampled rows; (b) +2 restarts; (c) `remainder=0 deferred` completion signal) + phase4 §8.8 explicit operator signal |
| W5 chat-mappings teeth | §7 fixture builder (+3 chat mappings incl. one >30d) + **P21** new pin (table-wide-DELETE mutation → RED) |
| A (binding hardening) | decisions D1 AM-1 note + D6 round-2 bullet; §1 repo-method row (`AND updated_at = :locked_value`); §7 P16 second-teeth mutation |
| B (escape hatch) | decisions D2 AM-5 disclosure text (raise-retention hint + "default 14d"); §1 AM-5 row |
| C (config_json docstring) | §1 dataclass note + §2 advisory-C note (json.dumps, sole reconstruction input) |
| D (inverse window debt) | decisions D6 round-2 bullet; §10.8 routed debt; §7 P19 asserts the parked row excluded |
| E (constant name + no-stop docstring) | §1 service-file row (`_REAP_SAMPLE_IDS_MAX`; deletion-needs-no-adapter-stop docstring fact, registry.py:312-314) |
| D2 ratification conditions | decisions D2 RATIFIED-WITH-CONDITIONS block (3 conditions verbatim) + §1 AM-5 row (verbatim strings incl. manual-from-archive truth); overview §2 D2 row marked RATIFIED |
