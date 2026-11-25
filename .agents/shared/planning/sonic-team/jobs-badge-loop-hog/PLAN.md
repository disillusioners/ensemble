# PLAN — jobs-badge-loop-hog (Option E: cheap badge polling + FE visibility gates)

Commission (sonic-leader, 2026-11-20): make /api/jobs badge polling CHEAP (backend cost), not rare;
FE visibility gates for ungated pollers. Base 213ea6c1e == origin/latest (verified).

## BINDING RULINGS (recorded verbatim in module briefs)
- EMPEROR (Discord 2026-11-19): 8s badge cadence STAYS; fix is BACKEND COST; hidden tabs stop
  polling; visible tabs keep full cadence — NO visible-tab interval changes.
- D-68 LEADER RULING (wi-3f5bea95, from git log -S 'jobs_active_badge'): the no-limit badge shape
  (`GET /api/jobs?status=queued,active`) carries NON-REGRESSION-only gating; "no server-side
  default cap" (Q1-ii invariant D-8) — NEVER add a server-side default limit to the no-limit path.
  Bounding must come from explicit client limit (leg 2) or batching (leg 1), never a default cap.

## VERIFIED ANCHORS (at base 213ea6c1e — all CONFIRMED)
- work_resolver.list_work def :1174; _query_jobs NO-LIMIT fetch (~:2340-2397, select(JobItem)
  order_by created_at desc, NO .limit()); slice happens at :1506-1509 AFTER unbounded batch
  lookups (_batch_instances/_batch_mission_fields/_task_repo.get_timing_by_work_ids over ALL
  matching rows) + per-row _parse_iso_datetime over ALL rows.
- Patch-up (D-29/D-69 page-completeness) :1538-1580: draws ensured ids from the FULL jobs list —
  a SQL LIMIT without margin+fallback would silently drop ensured rows (RESPONSE REGRESSION).
- jobs_crud.list_jobs :730: enrichment call :917-922 (limit/ensure_job_ids forwarded — v0.27.14
  shape intact); per-QUEUED-job `await service._get_queue_position` N+1 in the row loop (~:937-943);
  per-dead_letter-row `dlq_service.get_dlq_by_job_id` (SYNC, on-loop) ~:946-953.
- job_queue_service: list_work bridged via asyncio.to_thread :1841 (house bridge precedent);
  list_jobs bridged :2995+; _get_queue_position :4420 (bridged internally, but called per-row);
  UNBRIDGED unbounded watch reads: get_all_active_watches() at :556 (reconcile_terminal_watches),
  :711 (_reconcile_terminal_watches_legacy), :844 (reconcile_held_watches_for_instance).
- watcher_repository.get_all_active_watches :471 = select(JobWatcher) — NO active-state WHERE
  (full-table read; callers filter in Python).
- FE badge poller: job-queue-indicator.component.ts const POLL_INTERVAL_MS=8000 :130,
  setInterval :577, 5-leg forkJoin :676+ w/ per-leg catchError, NO visibility gate.
- FE ungated always-on pollers (6, from FE-POLL-SURVEY §3 census): badge (#2), home (#1),
  metrics resources-live (#6), instance.service (#10), mcps-gateway (#13), command-state (#9).
  Gated exemplars to copy: jobs page (#3 shouldTick), nest-reports (#7 supersede token), room (#12).
- CPU lane (row2, RUNNING since ~03:42Z, coders spawned ~04:01Z) surface: daemon/compaction.py,
  daemon/tools/** + NEW daemon/tools/_schema_registry.py, daemon/graph.py, daemon/loader.py.
  VERIFIED DISJOINT from this lane (routers/services/repositories/FE). No R-3 piece needed:
  E is standalone per off-loop-audit HANDOFF §46 item 6 ("independent commission ... jobs surface").

## MODULE SPLIT (4 coders, ONE parallel wave; disjoint files)
- M1a jbh-m1a — resolver cost bound: SQL LIMIT pushdown in _query_jobs when limit EXPLICIT
  (margin = limit + len(ensure_job_ids) + tie pad; guaranteed fallback re-query for ensured ids
  missing from the bounded page — page-completeness preserved ALWAYS); no-limit path UNTOUCHED
  (Q1-ii). Batch lookups follow the bounded set. Tests: engine-bound row-budget + per-leg SELECT
  budget + page-completeness + order-compat pins. FILES: daemon/services/work_resolver.py,
  tests/unit/services/test_work_resolver_badge_pushdown.py (NEW).
- M1b jbh-m1b — router/service batching + watch bridges: batch queue positions (ONE pending-list
  fetch → compute all positions; zero per-row queries; identical 1-based semantics); batch DLQ
  enrichment (new get_dlq_by_job_ids IN query); bridge the ×3 get_all_active_watches() reads via
  asyncio.to_thread; add active-state WHERE to the watch read (semantics-verified, callers-filter
  parity) as the "bound". FILES: daemon/routers/jobs_crud.py, daemon/services/job_queue_service.py,
  daemon/services/dead_letter_service.py, daemon/repositories/job_queue/watcher_repository.py,
  tests/unit/routers/test_jobs_list_badge_batch.py (NEW).
- M2 jbh-m2 — FE visibility gates on the 6 ungated always-on pollers (house pattern from #3/#7/#12:
  visibilitychange listener, skip tick when hidden, immediate refocus refresh debounced ~2s,
  supersede token on async writes, listener teardown). INTERVALS UNTOUCHED. FILES: the 6 source
  files + their existing specs. NO daemon/, NO *bindings.pins* files, NO interval literal changes.
- M3 jbh-m3 — gates + measurement: NEW FE pin spec poll-visibility.bindings.pins.spec.ts (source
  pins: gate present on all 6 + intervals unchanged); NEW measurement harness
  .agents/tester/harness/jobs-badge-cost/ (adapt eventloop-phase0 shape: seed fixture DB with
  large terminal job population + queued jobs, replay 5-leg badge composite ×8 rounds, capture
  per-leg latency + statement/row census + loop-lag read (lag−1.0s floor)); run BEFORE on base,
  record MEASUREMENT-BASE.md. AFTER run happens at integrate on the merged tree.

## MERGE ORDER
M1a (slot 1) → M1b (slot 2) → M2 (slot 3) → M3 (slot 4). All merge --no-ff into
sonic/jobs-badge-loop-hog at the task worktree. CPU-lane merge expected to land on origin/latest
first; re-check origin/latest at integrate; merge on top if it moved (FINALIZE discipline).

## COMPLEXITY VERDICT: MEDIUM (assessed §2.5 after explore+plan; not raised, not overridden)
Drivers: 4 disjoint single-turn modules; zero migrations; additive-only; wire contract preserved
by byte-compat/page-completeness pins; Q1-ii constraint fenced; in-repo test/harness precedents
for every gate; disjointness vs the in-flight CPU lane VERIFIED.

## MEASUREMENT CONTRACT (Emperor): 8s cadence survives; per-tick loop occupancy → noise; the
3.7–4× felt inflation GONE — measured before/after under browser-open load simulation with the
loop-lag instrument read as lag−1.0s; the causal split (watch-read vs enrichment-count vs
pool-queueing) isolated. Numbers, not round-trip counts alone.

## STAGE (end): derive version slot LIVE at stage time per W9 (v0.31.13 staged; CPU lane likely
v0.31.14; the two lanes MUST NOT claim the same slot). STAGE ONLY — no promote. FE build via
scripts/upgrade/_build_frontend.sh.
