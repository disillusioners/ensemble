# source-gc — Architecture Recommendation (Architect Gate, P2)

**Board:** wi-ed230eb4 · **Branch:** `feature/source-gc` @ 6b571771d · **Date:** 2026-11-07
**Gate:** Architect review of the planner set (plan-overview.md / decisions.md / phase4-plan.md)
**Evidence base:** 3 dispatched analysts (data-flow-design, resilience-design, trade-off-analysis), all anchors independently re-verified in-worktree @ 6b571771d; 2 architect-side verifications (REST create path, repo id handling). All three workers confirmed skill injection (`Skill loaded: [...]`) — no skill-bank misses.

## VERDICT: **SOUND-WITH-AMENDMENTS**

D1–D6 are structurally sound and anchor-faithful — every load-bearing planner anchor re-verified against code. **One critical amendment (AM-1: the delete txn as specified is race-unsafe)** plus ten further folds (AM-2..AM-11), none structural. One Emperor-facing decision is carried to the leader (SP-1 clutter-relief recipe — §2.1 / §8), everything else is decided here.

---

## 1. The six leader stress points — firm adjudications

### SP-1 — D3 backfill vs steady-state (**FIRM: steady-state code + sanctioned promote-time operator recipe**)

5-axis (trade-off worker, weighted — Maintainability 25% dominant):

| Approach | Cplx | Scal | Maint | Risk | Cost | Weighted | Rec |
|---|---|---|---|---|---|---|---|
| A: steady-state 14d + operator lever | 5 | 5 | 5 | 5 | 5 | **5.00** | ✔ |
| B1: backfill + persisted marker | 2 | 3 | 2 | 3 | 2 | 2.40 | ✘ |
| B2: backfill, markerless (empty-archive marker) | 3 | 4 | 3 | 3 | 3 | 3.20 | ✘ |

**The finding that forces a resolution (🔴 expectation defect):** the commission sentence *"the existing ~102 stale rows get collected by the first real GC pass after promote"* is **not** what shipped-default D3 delivers — default 14d collects ~16 of 100 at first tick (median live age ~10d), full clearance ~2026-11-21. The directive's promise fails by ~2 weeks unless closed here.

**Resolution (recommended, recorded as the promote recipe — env-only, no code, no marker):**
1. At promote (operator, optional-but-recommended): set `.env` `SERVICES_SOURCES_GC_TERMINAL_RETENTION_DAYS=7` and (optionally) `SERVICES_SOURCES_GC_DRY_RUN=true`.
2. Boot 1 (the promote restart itself): the dry-run tick logs **every** candidate with per-row reasons on the REAL live data — the operator's receipt; flip `DRY_RUN` off.
3. Boot 2 collects all rows terminal ≥7d (median age ~10d ⇒ most of the 100 rows, spread over ~4 ticks / ~4h at batch cap 25).
4. Restore `RETENTION=14` (boot 3).

**Emperor-facing trade, stated plainly:** do nothing → the clutter clears by ~2026-11-21; run the recipe → the clutter clears within hours of promote at the cost of one documented env step at an already-scheduled ceremony.

**Why 7d floor, not the planner's documented `retention=1` lever:** resume-after-reap is a bare `ValueError("Schedule not found")` (scheduling_service.py:988-991) — a reaped 3-day-old paused schedule is a broken promise. 7d honors the pause/resume window while still collecting ~most of the backlog; resume before collection writes `enabled=true`, which the fence then excludes forever. 0h is additionally unreachable (`ge=1` + repo re-check).

**Planner-set folds (AM-6):** correct the D3 lever documentation to the ≥7d recipe; record the recipe as the sanctioned clutter-relief path; annotate the commission sentence so the gates read the promise as recipe-satisfied, not default-satisfied. A coded backfill remains rejected: B2's empty-archive marker is an invisible coupling a future archive-retention lane would trip, and B1 leaves permanent dead state.

### SP-2 — D5 first-production-pass posture (**FIRM: ship destructive-on-promote; dry-run default False; one-boot live observe rides the SP-1 recipe**)

Planner's default-False is right (resilience-concurred): (1) a dry-run-default ships a GC that silently collects nothing until an untracked future flip — failing the directive; (2) promote restarts the daemon anyway; (3) the archive makes every deletion recoverable-but-manual; (4) the batch cap bounds any pass to 25/tick (100 rows = 4 gentle ticks). Dev-fixture proof (P6/P8) + kill-switch + repo-enforced predicate + AM-1 re-assert are **sufficient to ship destructive**.

The one-boot live dry-run observe is **not required by the fences** but is **recommended via the SP-1 recipe** (+1 restart total): the recipe's pass-1 is high-volume (~70–85 rows) and the observe tick proves the predicate against live's real 103 rows — which were never dry-run-seen — while producing the operator's receipt. Final posture: defaults `ENABLED=true / DRY_RUN=false / RETENTION=14`; kill-switch = panic lever; `RETENTION=very-large` = soft-disable.

### SP-3 — D2 uniform rule + interplay (**CONFIRMED; disclosure seam specified; one rationale corrected**)

- **Chat fence — structural.** Predicate hardcoded `source_type='scheduler'` at the repo layer, no widenable parameter exists on the planned signatures; the trap rows (`enabled=true, status='stopped'`) fail the enabled leg independently. Fixture pin stands.
- **AM-8 rationale correction:** the planner's claim that `SourceCleanup` "only sees mappings of existing sources" is imprecise — `cleanup_inactive_mappings` (repository.py:689-715) is an **age-only sweep over ALL mappings including orphans** (:699-702). Disjointness still holds *operationally* (GC's mapping deletes are same-txn with the source delete; chat mappings survive because chat sources are never collected). Fix the sentence; keep the fixture pin.
- **Uniform rule confirmed.** Pause (scheduling_service.py:1101-1102, :1130) and consumed one-shot (registry.py:524-527) land in identical `(status='stopped', enabled=false)`; only config keys differ. No split rule.
- **Disclosure seam (AM-5):** pause is **tool-only** — the REST `ScheduleUpdate` model has no `paused` field (daemon/models/schedule.py:114-128), so REST cannot pause and needs no doc change. Amend the three agent-facing strings in `daemon/tools/scheduling.py` (:18 module docstring "pause is RESUMABLE"; :236 and :247 `paused` field descriptions) to disclose: *"pause (resumable; a schedule left paused >14d is garbage-collected — history archived, config removed; resume before then is untouched)"*. `tool_help` inherits. **Reject** a distinct `paused` status for v1 — it would edit the `SourceStatus` enum + write gate + the **sealed** registry.py boot filter (forcing a :1463 re-pin) + adapter resume + FE — disproportionate; routed follow-up (§9).
- **Disclosed gap:** `instance_mappings` rows are deleted **without** archive (D1 covers configs + executions). Mitigated — `schedule_executions.instance_id` is archived, so the schedule→instance linkage survives in the archive.

### SP-4 — Archive breadth + retention (**FIRM: planner shape minus the credentials column; unbounded v1 + trip-point follow-up**)

Breadth 5-axis: planner shape **4.20** ✔ / scalars-only 3.90 ✘ (turns the audit trail into a tombstone list; the config snapshot is the *only* reconstruction input since no API restore path exists — update-of-missing raises) / full-row JSONB 3.10 ✘ (violates the TEXT-only dual-driver standing rule).

**AM-4 — drop the `credentials` column from `source_configs_archive`.** Evidence: the scheduler adapter has **zero** credential references — scheduler rows carry `credentials=None` (verified); chat credentials that do exist are Fernet ciphertext at rest anyway (routers/sources.py:257 encrypt-before-insert) and can never enter this GC's reachable set. The column would be always-NULL while duplicating a secret-surface the scrub families don't know (resilience's 🟡 dissolves entirely). Zero forensic loss. This resolves the planner's council flag (phase4 §2:60) — no masking needed, no verbatim debate: the column simply does not exist.

Retention 5-axis: unbounded + routed follow-up with written trip-point **4.80** ✔ vs unbounded-no-note 4.60 vs bounded-now 2.60 (a GC-of-GC deletes the evidence D5 just promised — the no-trace complaint returns one level up). **Trip-point: ~50k archive rows / ~50 MB / first slow forensic query** — years away at observed cadence (low single-digit MB/year steady-state). Write the trip-point into the routed follow-up (§9).

### SP-5 — ERROR-status rows (**FIRM: EXCLUDE — planner's OQ-2 recommendation confirmed, now with mechanism evidence**)

A `(enabled=false, status='error')` config row requires the exotic sequence: `start_adapter` failure (registry.py:871, the only scheduler-config error writer) **then** an operator disable via the generic REST PUT (routers/sources.py:411 writes enabled without status). Dispatch failures land on the **execution** row (scheduler.py:998-1005/:1406-1449), and the consumption gate (:806-830) never consumes failed/rearmed one-shots — those stay `enabled=true` and are excluded by the **enabled fence, correctly** (5s re-arm cadence is live behavior, not inert). Live census: zero error rows. Excluding preserves misconfigured-but-wanted intent; the state is transient (next user touch rewrites status). **AM-7 adds `observed_error_rows` to the sweep report** so phase4 §9.3's revisit trigger has data instead of folklore.

### SP-6 — Migration + rollback posture (**CONFIRMED — standing rule satisfied on every leg**)

1. Additive-only: `CREATE TABLE IF NOT EXISTS` ×2 + `CREATE INDEX IF NOT EXISTS`, zero ALTER/DROP of existing objects, no FK to any existing table. 2. Old binary references no archive tables (new symbols only in new files) → **inert post-rollback**. 3. DOWN never executes: `rollback_migration()` (runner.py:742) has zero production callers; runner is SQLite-only / PG structural NO-OP (:816/:837). 4. PG schema arrives via `create_all` — the registration chain verified: manager.py:57 → repositories/__init__.py:16-17 → module execution of source/models.py registers every SQLModel class before create_all at manager.py:999 → **zero manager.py change confirmed**. 5. `rollback_safe=true source=explicit`, consequence FIX-LOSS. 6. Prefix `20261107_*` unique against the verified 122-file census. 7. The pre-existing fresh-SQLite blocker `20260714_000001` (ALTER … DROP CONSTRAINT) is **UNRELATED** — the staged gate must not misattribute it.

---

## 2. Amendments (folds required before/at P4)

| # | Sev | Amendment | Source |
|---|---|---|---|
| **AM-1** | 🔴 | **In-txn predicate re-assert + row lock in `gc_archive_and_delete`.** The planner's txn archives+deletes on the strength of the earlier `gc_find_candidates` SELECT — a user resume landing in between silently deletes a just-re-armed schedule (writer census: resume scheduling_service.py:1104→:1114-1121; the rebuild path :1108-1110 even re-stamps a still-paused row, defeating the age fence because the delete never re-reads `updated_at`). Required shape, inside the single txn: (1) `Session.get(SourceConfig, source_id, with_for_update=True)` — the exact house idiom (instance/repository.py:1990; no-op render on SQLite per :1949-1954); (2) re-assert **all four legs from the locked row** — source_type, `enabled=false`, terminal status, **age re-parsed from fresh `updated_at`**; any leg fails → `skipped+counted`, never raise; (3) hold through DELETE so a concurrent PG resume blocks then honestly 404s (:1119-1120); belt-and-braces final `DELETE ... WHERE enabled=false AND status IN (...)` with rowcount-0 → raced-skip. Not-found → skipped (mirror delete_source_config's `{"deleted": False, "error": "Not found"}`, repository.py:517-521), never raise. Do NOT reuse the asyncio `_get_source_lock` — it misses registry/self_alarm/REST writers. Mutation pin: resume-mid-sweep leg. | resilience §1 |
| **AM-2** | 🟡 | `gc_find_candidates` orders `updated_at ASC` (oldest-first) so batch-cap remainder is deterministic and newest rows defer. | dataflow A1 |
| **AM-3** | 🟡 | **Archive PK policy: plain INSERT, fail-loud — NO `ON CONFLICT`.** Architect verification: the collision is *reachable*, not structurally impossible — the generic REST create path accepts `source_type="scheduler"` with a caller-supplied `source_id` (routers/sources.py:135-165; `create_source_config` honors caller ids), so a deliberately re-created id can collide with an archived one. But `ON CONFLICT DO NOTHING` is the wrong fix: it would silently keep the **stale** archive row and drop the fresh snapshot — audit corruption worse than a wedged row. Correct: plain INSERT; `IntegrityError` → per-row defensive catch → **WARNING naming the source_id** ("archive id collision — stale archive row for a re-created source") → skip+count; sweep continues (per-source txn bounds the blast). Operator resolution documented (delete the stale archive row or use a new id). Pin: pre-plant an archive row with a candidate's id → WARNING + row survives + counted in errors. | dataflow A2 ⊕ resilience §4c, adjudicated |
| **AM-4** | 🟡 | Drop `credentials` from `source_configs_archive` (SP-4). Migration spec (phase4 §2) and model edited accordingly. | trade-off ⊕ resilience |
| **AM-5** | 🟡 | The 3-string pause-disclosure in `tools/scheduling.py` (:18, :236, :247) — SP-3. | dataflow §3c |
| **AM-6** | 🟡 | SP-1 folds into the planner set: lever recipe corrected to ≥7d floor; promote recipe recorded; commission sentence annotated as recipe-satisfied. | trade-off ⊕ architect |
| **AM-7** | 🟡 | Observability contract final shape (§5): `remainder=<R> deferred` on work-logs and dry-run logs; `observed_error_rows` counter; one-time first-destructive marker; no-op destructive ticks stay SILENT. | dataflow §4 ⊕ resilience §2 |
| **AM-8** | 🟢 | Correct the SourceCleanup disjointness rationale sentence (age-only sweep incl. orphans); keep the fixture pin. | dataflow A3 |
| **AM-9** | 🟢 | Named mutation pin for the two-write consumed transition (registry.py:524-528; self_alarm.py:1109-1110): crash window leaves `(stopped, enabled=true)` — excluded by the enabled fence; pin it as a P2/P5 extension leg. Pre-existing non-atomicity itself routed as debt (§9). | resilience 🟡 |
| **AM-10** | 🟡 | `asyncio.to_thread` on **both** repo calls — `gc_find_candidates` AND every `gc_archive_and_delete`. Correct precedent is scheduling_service itself (:882, :1114), NOT tmp-image (inline FS walks, no DB); sync SQL on the loop is the exact class v0.27.14 cured. phase4 §4 step 3 must wrap each per-candidate call. | resilience §4e |
| **AM-11** | 🟢 | Make the import-chain claim durable: a unit assertion that `ScheduleExecutionArchive`/`SourceConfigArchive` are present in `SQLModel.metadata` after importing `daemon.repositories` (the testable form of "implementation verifies the chain"). | resilience §4g |

---

## 3. Wiring-seam inventory (developer-grade, all verified @ 6b571771d)

| Seam | File:line | Content |
|---|---|---|
| api.py start block | daemon/api.py — insert immediately after **:1866** (tmp-image INFO end; live-view block begins :1867) | `from daemon.services.sources_gc_service import SourcesGcService`; construct from `config.services.*` (exemplar construct :1850-1857, `.start()` :1858, `app.state.tmp_image_cleanup` :1859, start INFO w/ knobs :1860-1866); first-tick-immediate family shape (comment :1844-1845) |
| api.py shutdown mirror | daemon/api.py — insert after **:3966** (beside tmp-image, after orphan-watcher; block :3961-3976) | getattr-guarded `app.state.sources_gc` → `stop()` → WARNING on error → state clear |
| Repo accessor | daemon/manager.py:**1693** | `self._source_repository = create_source_repository(engine=self._engine, create_tables=False)` — same accessor routers/sources.py uses (:62, :130, :463…) |
| create_all chain | daemon/manager.py:**999** (create_all); :57 → daemon/repositories/__init__.py:**16-17** → source/models.py module execution registers ALL SQLModel classes (proof: `ScheduleExecution` is absent from the name list yet registered) | Zero manager.py change; AM-11 makes it a unit assertion |
| ServicesConfig knobs | daemon/config.py:**1907** (class), **:1910** (`env_prefix="SERVICES_"`) | `sources_gc_enabled=True` · `sources_gc_dry_run=False` · `sources_gc_interval_seconds=3600 (ge=1)` · `sources_gc_terminal_retention_days=14 (ge=1; repo re-checks ≥1)` · `sources_gc_batch_cap=25 (ge=1)` — flat Field + fail-fast pattern verified pervasive (:2167…:2773); precedents: strand-heal trio :2736-2790 (kill-switch class), tmp-image :2813-2830 (no-switch class) |
| Service file | daemon/services/sources_gc_service.py (NEW) | ctor `(source_repo, *, interval_seconds, retention_days, batch_cap, dry_run, enabled)`; `start()/stop()/sweep_once()`; first-tick-immediate; module-level `_SAMPLE_IDS_MAX = 5` (tmp-image precedent :84); `[SourcesGC]` f-string prefix; defensive except → WARNING + continue |
| Repo methods | daemon/repositories/source/repository.py (beside delete_source_config :513-545) | `gc_find_candidates(*, retention_days, now, limit) -> tuple[list[GcCandidate], int]` (SQL prefilter type/enabled/status; Python age via `fromisoformat`; unparseable → skip+count; ORDER BY updated_at ASC per AM-2). `gc_archive_and_delete(*, source_id, now, dry_run) -> GcRowResult` — ONE txn: locked re-SELECT + four-leg re-assert (AM-1) → archive INSERTs (config + executions, plain per AM-3) → DELETE instance_mappings (:522-524 order) → processed_messages (:526-528; parity no-op today — scheduler sources produce zero rows; keep for delete_source_config parity) → schedule_executions (:530-532) → source_config (:534) → commit (:535). Dataclasses: `GcCandidate(source_id, name, status, updated_at, age_days, config_json)` — shape derived at SERVICE layer from config keys (`run_at`/`schedule`, scheduler.py:511/:513); `GcRowResult(source_id, deleted, dry_run, archived_config, archived_executions, deleted_mappings, deleted_processed, deleted_executions, skipped_reason, error)` |
| Archive models | daemon/repositories/source/models.py (ADD 2 classes) | `ScheduleExecutionArchive`, `SourceConfigArchive` — `__table_args__` index names byte-match the .sql (3-site lockstep); bools as SQLModel `bool` (INTEGER affinity); NO `credentials` column (AM-4) |
| Migration | daemon/migrations/versions/`20261107_HHMMSS_create_source_gc_archive_tables.sql` (NEW; 122→123, unique prefix) | phase4 §2 spec minus `credentials`; memoir-template dual-driver header; IF NOT EXISTS everywhere; TEXT-only; no FK; DOWN children-first (never executes) |
| **NOT touched** | daemon/sources/registry.py (sealed :1463) · daemon/sources/cleanup.py · daemon/manager.py · daemon/routers/sources.py (REST refusal :442-450 intact) · daemon/constants.py (sealed) · daemon/services/scheduling_service.py · frontend/** | Seal-trip scope EMPTY — `SEALED_SHA_BASELINES` (tests/test_chart_image_delivery_e2e.py:1425, 35 rows) pins zero rows on any touched path; module-level constants in the service file only |

## 4. Predicate (single source of truth — repo layer, unchanged from planner + AM-1/AM-2)

```
source_type == 'scheduler'            # hardcoded, no parameter (chat fence, structural)
AND enabled == False                  # enabled=true fence
AND status IN ('stopped','cancelled') # terminal-only ('error' excluded — SP-5)
AND age(now - fromisoformat(updated_at)) > retention_days
                                      # unparseable/missing → SKIP + count (fail-MISS)
-- SELECT legs prefilter in SQL; age in Python (103-row scale);
-- ALL FOUR legs re-asserted inside the delete txn under with_for_update (AM-1)
```

## 5. Observability contract (final — answers "3-of-4 GC services leave zero trace")

1. **Start INFO once** (api.py wiring): `[SourcesGC] started: enabled=<b> dry_run=<b> interval=<N>s retention=<N>d batch_cap=<N>`; when disabled: `[SourcesGC] disabled (SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run` (no task scheduled).
2. **Dry-run ticks ALWAYS log**: `[SourcesGC] dry-run: candidates=<N> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred` + ≤5 per-row lines `(id, name, one-shot|cron, status, age_d)`.
3. **Destructive ticks log IFF work or skips or remainder**: `[SourcesGC] collected=<N> archived_configs=<N> archived_executions=<M> mappings=<X> processed=<Y> skipped_unparseable=<K> observed_error_rows=<E> remainder=<R> deferred duration_s=<S> sample=[ids≤5]`.
4. **One-time marker**: `[SourcesGC] first destructive sweep performed at <iso> (retention=<N>d, collected=<N>)` — the operator's post-promote receipt.
5. **No-op destructive tick: SILENT** (tmp-image discipline, tmp_image_cleanup_service.py:303-317; an hourly count=0 heartbeat is 24 noise lines/day adding nothing).
6. **Per-row failure**: WARNING `(source_id, exception-type)` + continue (per-source txn isolates).
7. **Durable trace = the archive tables** (`archived_at`-stamped, queryable). No new audit substrate: maintenance_runs rejected (single-flight lifecycle models user-triggered runs — maintenance_runs/models.py:46-81; an autonomous hourly sweep would mint ~8.7k rows/yr needing their own GC); central metric family (`sources_gc_deleted_total`) = routed follow-up if the metrics lane wants it.

## 6. Approach comparison (condensed — full tables in the trade-off report, preserved verbatim in lane history)

| Contest | Winner | Score | Dominant axis | Loser(s) |
|---|---|---|---|---|
| SP-1 clutter relief | A steady-state + operator recipe | 5.00 | Maintainability | B1 marker backfill 2.40 · B2 markerless 3.20 |
| SP-4 archive breadth | A planner shape (− credentials) | 4.20 | Maintainability (Risk close 2nd) | scalars-only 3.90 · JSONB blob 3.10 |
| SP-4 archive retention | C unbounded + trip-point follow-up | 4.80 | Maintainability (Risk tie-break) | unbounded-no-note 4.60 · bounded-now 2.60 |

Interaction (recorded): batch cap decouples pass-1 volume from violence (100 rows = 4 ticks/~4h); breadth must land **before** any lever is exercised (config snapshot is the only reconstruction input); retention is the guard on the guard — bounding it recreates the no-trace complaint one level up.

## 7. Risks (merged, deduped — highest severity + most specific variant kept)

- 🔴 **AM-1 race** — delete txn without in-txn re-assert + lock silently destroys a just-resumed schedule (or a just-edited paused one) — REQUIRED fold before P4.
- 🔴 **Commission-sentence vs D3** — expectation defect at the Emperor level unless SP-1 recipe (or sentence correction) is adopted at the gates.
- 🟡 **AM-3 archive PK collision is reachable** (REST create accepts scheduler + explicit id) — plain-INSERT fail-loud policy + pin; never ON CONFLICT (stale-snapshot absorption corrupts the audit).
- 🟡 **Two-write consumed transition** (registry.py:524-528; self_alarm.py:1109-1110) — crash window `(stopped, enabled=true)` excluded by the enabled fence; AM-9 pin; non-atomicity itself routed as pre-existing debt.
- 🟡 **Resume-after-reap UX** — bare `ValueError("Schedule not found")`; disclosure strings (AM-5) + routed docs note.
- 🟢 instance_mappings deleted without archive (executions carry instance_id — linkage survives); ERROR exclusion + observed_error_rows counter; SourceCleanup rationale fix; processed_messages parity no-op.

## 8. Decisions pending (leader/Emperor — everything else decided)

1. **SP-1 recipe adoption (Emperor-facing):** run the promote-time retention=7 (+optional one-boot dry-run observe) recipe → clutter cleared within hours of promote; or do nothing → cleared by ~2026-11-21. Architect recommendation: **run the recipe**. Both are safe; this is a UX-timeline call the Emperor owns.
2. **D2 standing-policy ratification** (paused-recurring reaped at 14d, disclosed via AM-5) — planner already flagged for plan council; architect concurs with the uniform rule.

## 9. Routed follow-ups (recorded, out of scope)

1. Distinct `paused` status (enum + sealed-registry edit + backfill + FE) — only if the 14d reap ever generates user friction.
2. Archive GC trip-point: revisit at **~50k rows / ~50 MB / first slow forensic query**.
3. Metric counter `sources_gc_deleted_total` (central metrics lane) + archive read surface (read tool / maintenance console).
4. `SourceCleanup` refactor into the knob'd family (pre-existing).
5. Non-atomic two-write consumed transition (registry/self_alarm) — own debt note.
6. Resume-after-reap copy: docs/FE note ("recreate from archive") beyond the AM-5 tool strings.

## 10. Planner-set open questions — closed

- OQ-1 archive breadth: **both tables** (confirmed) **minus credentials column** (AM-4).
- OQ-2 ERROR rows: **exclude** (SP-5, mechanism-evidenced) + `observed_error_rows` counter.
- OQ-3 SourceCleanup: untouched (confirmed).
- OQ-4 chat-mappings interplay: disjoint (operationally — rationale corrected per AM-8).
- OQ-5 first-destructive marker: **yes** (AM-7 item 4), with candidate count added.

---

*Architect: 3-worker fan-out (data-flow-design / resilience-design / trade-off-analysis), all skill injections confirmed; aggregation and adjudication by the architect. Conflicting worker claims (archive PK policy) adjudicated by direct code verification (routers/sources.py:135-165; repository.py create_source_config).*
