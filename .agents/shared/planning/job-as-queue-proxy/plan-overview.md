# Plan Overview: Job as Queue Proxy — Collapse Execution State onto Instance (Iteration 2)

## Objective
Collapse all execution lifecycle state (status/timing/result/error) from `JobItem` onto its existing authority — the **Instance** + **DependencyBus** — making `JobItem` a pure "queue proxy" that carries only admission concerns (priority, idempotency, queue membership, locks, retry policy).

## Scope Assessment
**LARGE** — Multi-feature storage-collapse refactor spanning ~20 backend files + frontend. 7 phases, estimated 3-5 days. The read landing zone (`WorkResolverService`/`WorkRecord` from D14) already exists; this plan extends it to the only read path, then cuts over writers, then drops columns.

## Context
- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Branch:** `feature/job-as-queue-proxy`
- **Base commit:** `077483f1`
- **Builds on:** D11/D13 message decouple (done), D14 Virtual Job Management Surface (done), `docs/architecture/completion-authority.md`
- **Review reports:** `.agents/shared/working/job-as-queue-proxy-review/review-report.md` (iteration 1 review)

## Iteration 2 Changes (from Reviewer's 3 blocking + 8 warnings)

| ID | Issue | Fix |
|----|-------|-----|
| **C1** | `_finalize_terminal` inventory incomplete — 10+ bare `instance.status` write paths | §6.1 expanded to COMPLETE inventory of ALL instance.status write sites (13 sites across 5 files); each classified as "route through `_finalize_terminal`" or "handle differently" |
| **C2** | Defer-idle-gate breaks FIFO priority with `WHERE admission_state='active'` | Changed to `WHERE admission_state IN ('queued', 'active')` — preserves existing `PENDING+PROCESSING` semantics |
| **C3** | Stale-lock sweep race-deletes in-flight locks | Changed `_ACTIVE_JOB_IDS_SUBQUERY` to `WHERE admission_state IN ('queued', 'active')` — protects both states |
| **W1** | Trigger test surface impact unspecified | §9.1 added: PostgreSQL tests need `SET CONSTRAINTS ALL IMMEDIATE` or the trigger fires at commit; SQLite tests unaffected |
| **W2** | Trigger function body unspecified | §8.7.1 added: full SQL for both trigger functions + install statements |
| **W3** | `_try_start_job` fate unspecified | §6.3 added: repurposed as the canonical `start_job` lock-acquire path |
| **W4** | Crash-recovery mid-finalize window | §8.8 added: crash between Step 1 and Step 3 = `active` job with terminal instance, recovered by `JobRecoveryService` on next startup |
| **W5-W8** | ~8 drifted line references | ALL line references re-verified against codebase; correction log in §13.2 |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 0 | Audit & invariants doc | Confirm admission-state vocabulary; write invariants doc | None | — | 2h |
| 1 | Read authority cutover | Route all job execution-state reads through Instance/WorkRecord | None | independent | 4-6h |
| 2 | Introduce `admission_state` | Add column + dual-write + install constraint triggers | Phase 0 | tight (schema) | 4-6h |
| 3 | Cut over gating/count queries | All admission queries filter on `admission_state` | Phase 2 | tight (depends on column) | 4-6h |
| 4 | Flip writers to instance-authoritative | Single `_finalize_terminal` boundary; stop writing `status` | Phase 2, 3 | tight (query cutover done) | 6-8h |
| 5 | Drop redundant columns | Remove `status`/timing/result columns + old indexes | Phase 4 (≥2 weeks clean in prod) | tight (no writers left) | 3-4h |
| 6 | Frontend | Jobs page renders from `Work` exclusively | Phase 1 (loose) | loose | 3-4h |
| 7 | Cleanup | Remove flag, legacy branches, dead code | Phase 5, 6 | loose | 2-3h |

### Coupling Assessment

| Phase pair | Coupling | Rationale |
|------------|----------|-----------|
| 0 → 1 | independent | Phase 0 is docs only; Phase 1 is code |
| 1 ↔ 2 | independent | Different concerns (reads vs schema); can overlap |
| 2 → 3 | **tight** | Phase 3 queries the `admission_state` column Phase 2 creates |
| 3 → 4 | **tight** | Phase 4 stops writing `status`; Phase 3 ensures all reads use `admission_state` first |
| 4 → 5 | **tight** | Phase 5 drops `status`; Phase 4 must be the last writer (plus 2-week observation) |
| 1 → 6 | loose | Frontend needs the read API ready (Phase 1), not Phase 2-5 |
| 5, 6 → 7 | loose | Cleanup needs columns dropped + frontend migrated |

### Parallelization Opportunities
- **Phase 0 + Phase 1** can run in parallel (docs vs code).
- **Phase 6 (frontend)** can start after Phase 1 and run in parallel with Phases 2-5.
- **Phases 2→3→4→5** must be strictly sequential.

## Critical Constraints (from project critical notes)

| Constraint | How Addressed |
|-----------|---------------|
| 🔴 PostgreSQL is PRIMARY dev/test DB | Dual-path migrations: SQLite `.sql` + `_ensure_postgres_columns()` ALTERs |
| 🔴 `_ensure_postgres_columns()` for ALL new columns | Phase 2 adds `admission_state` here; Phase 5 adds drop helper |
| 🔴 No SQLite-only syntax in migrations | All SQL uses portable `TEXT`, `IF NOT EXISTS`, `IF EXISTS` |
| 🟡 PostgreSQL default from v0.5.2+; dual-driver support | Both SQLite and PostgreSQL maintained throughout |

## Key Design Decisions

1. **4-value `AdmissionState`** (`queued`/`active`/`done`/`dead`) replaces 7-value `JobStatus`. Terminal classification (`completed` vs `failed` vs `cancelled`) moves to read side (Instance.status via WorkRecord join).
2. **Single `_finalize_terminal(instance_id, decision)` boundary** with required `Decision` enum (`NO_RETRY`/`RETRY`/`DEAD_LETTER`) — structural guarantee that retry is never silently skipped. NOTE: today `maybe_retry` lives in `complete_job`/`complete_job_sync` (`job_queue_service.py:1579`/`:1657`), NOT in the finalization path; Phase 4 consolidates it into the new boundary. The `_finalize_terminal` boundary governs **job admission state writes**, NOT all instance.status writes (see §6.1 for the complete instance.status inventory).
3. **`active ⇔ lock-held` invariant** enforced via PostgreSQL deferred CONSTRAINT TRIGGERs (first-ever trigger usage in codebase), with SQLite bidirectional CI sweep fallback.
4. **No `JobResponseV2`** — D14's byte-identical canonicalization neutralizes semantic-drift risk.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Pause correctness after dropping job-side pause write | **high** | §8.1 integration test; pause stays `active` with lock held; instance status checked at every gate |
| `maybe_retry` consolidation changes behavior | **medium** | Phase 4 structural guarantee via required `Decision` enum; consolidate from `complete_job` paths |
| Query semantic changes — **FIXED**: `count_active_jobs*` preserves `queued+active` semantics (C2) | low | §3.3 explicitly specifies `IN ('queued', 'active')` for count queries |
| Stale-lock sweep race — **FIXED**: `_ACTIVE_JOB_IDS_SUBQUERY` uses `IN ('queued', 'active')` (C3) | low | §3.4 explicitly specifies both states protected |
| Column drop is irreversible | **high** | Phase 5 `MANUAL: TRUE` migration; ≥2-week observation window; DLQ snapshots preserve data |
| `CONSTRAINT TRIGGER` novelty (no codebase precedent) | **low** | Well-documented; idempotent install; SQLite fallback sweep; trigger SQL in §8.7.1 |
| Crash mid-finalize window | **medium** | §8.8: recovery via `JobRecoveryService` on next startup; atomic transaction prevents partial writes |

## Success Criteria
- [ ] `JobItem` has no execution-state columns (`status`, `started_at`, `completed_at`, `result_summary`, `error_message`, `cancelled_at`, `failed_at`)
- [ ] `JobStatus` enum deleted; replaced by 4-value `AdmissionState`
- [ ] `_finalize_job_db_sync` Step 1 writes only `admission_state`; mapping gone
- [ ] Pause cascade writes only instance; no job-status pause write
- [ ] Status-drift warning, shim, dead SSE param deleted
- [ ] All job execution-state reads resolve through Instance/WorkRecord
- [ ] `count_active_jobs*`, `list_pending*`, defer-gate, recovery queries filter on `admission_state` with `IN ('queued', 'active')` semantics (C2 fix)
- [ ] `_ACTIVE_JOB_IDS_SUBQUERY` uses `admission_state IN ('queued', 'active')` (C3 fix)
- [ ] `active ⇔ lock-held` invariant enforced (PG trigger + SQLite sweep)
- [ ] Frontend renders from `Work` exclusively
- [ ] Every terminal write routes through `_finalize_terminal(instance_id, decision)` (grep-verifiable)
- [ ] All existing tests green after reseed; new admission-state tests green
- [ ] PostgreSQL constraint trigger tests use `SET CONSTRAINTS ALL IMMEDIATE` for deterministic firing

## Tracking
- Created: 2026-06-27
- Last Updated: 2026-06-27 (iteration 2 — 3 blocking + 8 warnings fixed)
- Status: **draft** — corrected per review iteration 2
- Review reports: `.agents/shared/working/job-as-queue-proxy-review/review-report.md`
