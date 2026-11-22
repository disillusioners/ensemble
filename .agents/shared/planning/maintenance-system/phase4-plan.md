# Phase 4 — Report + Receipt + Manual Trigger + FE Surface (Central Maintenance System)

**Objective:** Define the file-based run report/receipt/INDEX convention, the single canonical manual-trigger path, and the FE "report" surface wired into the gear menu — so every run (scheduled or manual) leaves a durable, versioned, human-readable artifact and is reachable from the UI.

**Status:** PLANNED

---

## Scope

### In scope
- D-B report path convention + `run_id` format; report + receipt + INDEX.md formats.
- Receipt schema: run id, trigger type scheduled|manual, timestamps, per-lane results + statuses, three-class recommendations, bytes AND inodes reclaimed where applicable.
- D-C justification (files only, no DB — stated here, not re-litigated).
- D-D manual trigger: canonical one-shot-at-now-to-maintenancer path + receipt + fallback; exact invocation documented.
- D-E FE wiring: gear-menu entry, lazy `/report` route, report view component, read-only BE list route, spec updates for ordering pins.
- Report authorship via worker child with absolute paths (D-A).

### Out of scope
- Trigger chain mechanics (Phase 1, sibling) — cross-ref `phase1-plan.md (sibling)`; P4 documents the invocation + receipt, not the one-shot internals.
- Lane result generation (Phase 3) — P4 consumes those blocks.
- Orchestrator skill registration (Phase 2) — `run-report-writer` skill is registered there.
- Live DB prompt adoption / FE dist rebuild-and-promote (operator step, post-promote).
- Any code execution, daemon run, DB connection, or git mutation — plan only.

---

## Preconditions & dependencies

- **Upstream:** Phase 3 produces the five per-lane result blocks; Phase 2 registers `run-report-writer` and the `worker` child write path; Phase 1 (sibling) supplies the one-shot trigger + `schedule_executions` audit.
- **Downstream:** Phase 5 (sibling) owns test pins and staging.
- **Parallelizable-with:** Phase 3 — agree on the receipt's per-lane block fields (Phase 3's common schema). FE work (D-E) is independent of report schema and can proceed in parallel.
- **Base:** worktree `/home/nea/ensemble-src-wt-maintsys`, branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. NEVER operate from `/home/nea/ensemble-src`.

---

## Design decisions consumed

- **D-A** — seat non-mutating; report + receipt + INDEX authoring routes through a `worker` child with ABSOLUTE paths. Fallback (scoped write capability on report-writer child only) named with tradeoffs, not silently picked.
- **D-B** — path convention `.agents/maintenancer/RESULTS/run-<run_id>.md` + `receipt-<run_id>.md` + `INDEX.md`; run_id = `ckpt-YYYYMMDD_HHMMSSffffff-<hex8>` (precedent `daemon/services/maintenance_run_identity.py:6,30-47`, migration_id family); `.md` never `.log` (gitignored).
- **D-C** — files only; do NOT write `maintenance_runs` (old tier's ledger). Justification stated below.
- **D-D** — canonical manual trigger = scheduler ONE-SHOT at `run_at=now` targeting maintenancer (same dispatch path as the weekly chain; receipt `trigger_type=manual` derived from a requester/origin marker on the manual row; Ari invokes via `task_schedule` tool surface). Fallback: direct `send_message` to a maintenancer instance.
- **D-E** — gc-fe LANDED (merge 7f928cbc5, `frontend/src/app/pages/chat/chat.html:100-118` room browser) but header nav untouched (`app.html:11-39` hardcoded) → collision premise VOID → WIRE IT, GEAR MENU ONLY (app.ts:577-578 rule), lazy `/report` route + `report.component.ts`, read-only BE list route (live_views has NO list endpoints — `daemon/services/live_views.py:36-37` GET/HEAD only), do NOT host under Maintenance shell (`maintenanceAvailabilityGuard` hides it unless checkpoint-cleanup ready — `app.routes.ts:214-219`). FE deferred-part NOTHING.
- **D-H** — run_id IS single-sourced from `new_maintenance_run_id()` (do NOT switch authorities). The receipt writer is a worker child in an agent turn (cannot call daemon Python as a function), so it shells out to the pure helper via `python3 -c` (PRIMARY); a bash format-spec mint is the documented FALLBACK. Import-purity pinned in P5; agent-side mechanics in Task 4.1.

---

## Tasks

### Task 4.1 — Report path convention + run_id (D-B)
- **Description:** Establish `.agents/maintenancer/RESULTS/` as the durable report dir (precedent: `.agents/tester/RESULTS/` dated-file house pattern). Three artifacts per run: `run-<run_id>.md` (human-readable report + recommendations), `receipt-<run_id>.md` (machine-readable receipt), `INDEX.md` (chronological ledger). `run_id = ckpt-YYYYMMDD_HHMMSSffffff-<hex8>` — format is `ckpt-` + UTC microsecond timestamp + `secrets.token_hex(4)` suffix (`daemon/services/maintenance_run_identity.py:6,34-37,46-47`). **D-H:** run_id IS single-sourced from `new_maintenance_run_id()` — do NOT switch authorities. Because the receipt writer is a worker child in an agent turn (it cannot invoke daemon Python as a function), use these agent-side mint mechanics:
  - **PRIMARY mint path:** the child shells out to the pure helper — `cd <repo-checkout> && python3 -c "from daemon.services.maintenance_run_identity import new_maintenance_run_id; print(new_maintenance_run_id())"`. Single-sourcing is preserved (format changes propagate from the one module); the helper is pure (stdlib only — timestamp + `secrets.token_hex(4)`); no daemon runtime is needed, just the package importable from the checkout. **Task note:** implementer verifies the module has NO import-time config/DB side effects and pins that in P5 (import-purity check).
  - **FALLBACK (documented, same task):** if the import is unavailable in the executing context, bash format-spec mint `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)` matching the canonical regex — documented as drift-risk-bounded (regex-pinned by the P5 run_id shape pin already listed in the Verification plan).
  - **INDEX.md dedup by run_id covers both paths** — already in Task 4.4; keep unchanged.
  Use `.md` only — `.log` is gitignored.
- **Touch points:** convention documented in `run-report-writer` skill body (Phase 2 artifact) and here; no new code file in-repo for the path itself.
- **Verification:** path uses `.md`; run_id matches the helper's regex; INDEX is chronological by run_id prefix (lexicographic = chronological, per helper docstring :36-37).
- **Agent hint:** `doc-writer` / `worker` (report writer child).

### Task 4.2 — Report body format (`run-<run_id>.md`)
- **Description:** Human-readable: header (run_id, trigger type, start/end timestamps, overall status), one section per lane with its status (ok/degraded/failed) + summary + metrics + evidence paths, a Recommendations section grouped into three classes (auto-fix-now / needs-Emperor-word / route-to-lane), and a `### Gaps` section for `[incomplete]` lanes (fan-in valve precedent, workflow.md:42). Report is ALWAYS written even on total lane failure (R4) — in that case all lanes show `failed` and the report still exists.
- **Touch points:** `run-report-writer` skill body (Phase 2); report template documented here.
- **Verification:** report contains all five lane sections + three recommendation classes + gaps; total-failure case still produces a report.
- **Agent hint:** `worker` (report writer child, absolute paths).

### Task 4.3 — Receipt schema (`receipt-<run_id>.md`)
- **Description:** Machine-readable fields, one per line or a fenced YAML block:
  ```
  run_id: <ckpt-...>
  trigger_type: scheduled | manual
  started_at: <ISO-8601 UTC>
  ended_at: <ISO-8601 UTC>
  overall_status: ok | degraded | failed
  lanes:
    - lane: lane-disk-sweep
      status: ok | degraded | failed
      summary: <one line>
      metrics: { bytes_reclaimed: N, inodes_reclaimed: N, ... }   # disk lane; others lane-specific
      evidence_paths: [ <absolute paths> ]
    - ... (one per lane; Phase 3 common result block)
  recommendations:
    auto_fix_now: [ ... ]
    needs_emperor_word: [ ... ]
    route_to_lane: [ ... ]
  ```
  `bytes_reclaimed` AND `inodes_reclaimed` are required for the disk lane ("where applicable"); other lanes carry their own metrics keys. `trigger_type` is `scheduled` for the weekly chain, `manual` for the one-shot manual path (D-D).
- **Touch points:** `run-report-writer` skill body; schema documented here.
- **Verification:** receipt parses; all five lanes present with statuses; disk lane has both bytes+inodes; three recommendation classes present; trigger_type in the closed set.
- **Agent hint:** `worker` (report writer child).

### Task 4.4 — INDEX.md format
- **Description:** Chronological table, newest first or last (state the choice), columns: `run_id | date | trigger_type | overall_status | report_path | receipt_path`. Append-only; one row per run; ordered by run_id prefix (lexicographic = chronological). Idempotence: re-running a run_id must not duplicate its INDEX row (dedup by run_id).
- **Touch points:** `run-report-writer` skill body; format documented here.
- **Verification:** INDEX rows sorted; each run_id appears once; paths are absolute or repo-relative under `.agents/maintenancer/RESULTS/`.
- **Agent hint:** `worker` (report writer child).

### Task 4.5 — D-C justification (no DB) — state, do not re-litigate
- **Description:** Document in this plan: files only, do NOT write `maintenance_runs`. Reasons: (1) its `kind` vocabulary is `auto|manual_dry_run|manual_execute` (`daemon/repositories/maintenance_runs/models.py:11,105-107`) — semantically mismatches our `trigger_type` `scheduled|manual`; (2) writing agent-turn rows into daemon bookkeeping tables conflicts with the seat's non-mutating convention (D-A); (3) tier conflation risk is explicit (old tier is separate + LIVE); (4) trigger-side audit already exists in `schedule_executions`. No new DB tables/migrations are justified for this system.
- **Touch points:** this plan (justification record); no code.
- **Verification:** no migration file created for this system; no `maintenance_runs` write path in the design.
- **Agent hint:** `doc-writer` (record only).

### Task 4.6 — D-D manual trigger: canonical path + invocation + receipt
- **Description:** Canonical path = scheduler ONE-SHOT at `run_at=now` targeting `maintenancer`, same dispatch as the weekly chain. Ari invokes via the `task_schedule` tool surface (`daemon/tools/scheduling.py:165-189`): `task_schedule(label=..., message=..., when=<now ISO 8601>, recurrence="once", agent_id="maintenancer")`. Capability VERIFIED in-repo: `TaskScheduleInput` supports `recurrence="once"` with ISO-8601 `when` (`:93-94`), `agent_id` targeting (`:98,175`), and past/near-now one-shots are handled — the adapter fires them immediately within the lateness cap or skips, and the operator reads truth from executions history (`daemon/services/scheduling_service.py:491-493`). Receipt `trigger_type=manual` derived from a requester/origin marker on the manual schedule row (the row carries the requester; the report writer reads that marker). Fallback: direct `send_message` to a maintenancer instance (documented, but NOT canonical). Trigger mechanics detail lives in `phase1-plan.md (sibling)` — cross-ref only.
- **Touch points:** this plan (exact invocation + receipt); `phase1-plan.md (sibling)` cross-ref for mechanics; Ari's invocation documented as a runbook note (no Ari prompt edit — out of lane).
- **Verification:** invocation uses recurrence=once + agent_id=maintenancer; receipt trigger_type=manual; fallback documented; no contradiction with scheduling.py (verified above).
- **Agent hint:** `doc-writer` (runbook note); trigger mechanics are sibling Phase 1.

### Task 4.7 — D-E FE wiring: gear-menu entry + lazy route + view + BE list route + specs
- **Description:**
  1. **Gear-menu entry:** add a static `settingsMenuItems` entry in `frontend/src/app/app.ts` (array at `:566-597`), placed AFTER `Metrics` (`:596`) and BEFORE the conditional Database/Maintenance `settingsMenuItems.update(...)` appends (`:838-854`), so those appends stay at the menu tail. GEAR MENU ONLY — no top-level header link (header nav is hardcoded in `app.html:11-39`; do not touch it). Follow the idempotence guard pattern (`app.ts:841` `.some(i => i.route === ...)`) if using a conditional-append; a static entry is simpler and matches MCP Gateway/Metrics.
  2. **Lazy `/report` route:** add `{ path: 'report', loadComponent: () => import(...).then(m => m.ReportComponent), title: '...' }` in `frontend/src/app/app.routes.ts` BEFORE the catch-all `**` (`:225`). Do NOT host under the Maintenance shell (`maintenance/checkpoint-cleanup` at `:214-219` uses `maintenanceAvailabilityGuard` which hides it unless checkpoint-cleanup ready).
  3. **Report view component:** new `frontend/src/app/pages/report/report.component.ts` (+ html/scss/spec) — read-only list of runs from the BE list route + render selected report; mirror the MCP Gateway read-only page pattern (`frontend/src/app/pages/mcps-gateway/`).
  4. **BE read-only list route:** add a GET-only route that lists report artifacts (run_id/date/status/paths) from `.agents/maintenancer/RESULTS/INDEX.md` or a dir scan — mirror the tool-usage/metrics read-only door posture (`daemon/routers/tool_usage.py:39-50` GET-only, auth scopes). live_views has NO list endpoint (`daemon/services/live_views.py:36-37` GET/HEAD only) so a dedicated read route is required.
  5. **Spec updates (ordering pins):** update `frontend/src/app/pages/mcps-gateway/mcps-gateway.pins.spec.ts` and `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts` (both pin the `MCP Gateway → Metrics → (conditional appends)` order at metrics-gear-menu.spec.ts:47-59) and `frontend/src/app/app.component.spec.ts` (gear-menu/snapshots pins at :2092-2121) to include the new Report entry in the ordering assertions. Add a report page pin spec mirroring the gateway pins (lazy route + gear entry + no header link + read-only service).
  - **FE ordering note:** the new entry goes AFTER Metrics and BEFORE the conditional appends; every existing ordering pin that asserts `... < dbUpdateIdx` must be extended to assert `reportIdx < dbUpdateIdx` and `metricsIdx < reportIdx`.
- **Touch points:** `frontend/src/app/app.ts`, `frontend/src/app/app.routes.ts`, `frontend/src/app/pages/report/` (new), a new daemon router module for the list route + its registration, `mcps-gateway.pins.spec.ts`, `metrics-gear-menu.spec.ts`, `app.component.spec.ts`, new report pin spec.
- **Verification:** `/report` reachable via gear menu only; no header link; lazy route before wildcard; BE route GET-only; all ordering pins green with Report in the chain; FE deferred-part NOTHING (fully wired).
- **Agent hint:** `coder` (FE + BE), `tester` (P5 pins).

---

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Report not written on total lane failure | High | Report-writer dispatch unconditional (Phase 2); fan-in valve marks `[incomplete]`; report-ALWAYS pin in P5. |
| Seat writes report directly (breaks non-mutating convention) | High | D-A: worker child with absolute paths; deny list unchanged; scoped-write fallback named with tradeoffs, not silently picked. |
| `.log` used → gitignored → report vanishes | Medium | `.md` only (D-B); pin the extension. |
| run_id re-derived incorrectly / collisions | Low | PRIMARY: shell out to the pure `new_maintenance_run_id()` via `python3 -c` (single-sourced; implementer verifies no import-time config/DB side effects, pinned in P5); FALLBACK: bash format-spec mint `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)`, drift-risk-bounded by the P5 run_id shape regex pin; hex8 suffix; dedup INDEX by run_id (covers both paths). |
| FE entry breaks ordering pins | Medium | Place AFTER Metrics BEFORE conditional appends; update all three pin specs; add report pin. |
| FE entry under Maintenance shell hidden by guard | Medium | Standalone `/report` route, not under `maintenance/checkpoint-cleanup`. |
| BE list route accidentally write-capable | High | GET-only, mirror tool-usage door scopes; no list endpoint reuses live_views (it has none). |
| Manual trigger capability assumed | Medium | VERIFIED against scheduling.py:93-98,175 + scheduling_service.py:491-493 — no contradiction; cross-ref Phase 1 for mechanics. |

---

## Acceptance criteria

- Report + receipt + INDEX written per run via worker child with absolute paths; `.md` only; run_id matches `ckpt-YYYYMMDD_HHMMSSffffff-<hex8>`.
- Receipt has run id, trigger type scheduled|manual, start/end timestamps, five per-lane results + statuses, three recommendation classes, bytes AND inodes for disk lane.
- INDEX chronological, dedup by run_id.
- D-C justification recorded; no DB tables/migrations; no `maintenance_runs` writes.
- D-D canonical manual trigger documented with exact `task_schedule` invocation + receipt trigger_type=manual + fallback; cross-ref Phase 1.
- D-E: gear-menu Report entry (after Metrics, before appends, no header link), lazy `/report` route before wildcard, report view component, GET-only BE list route, all ordering pins updated; nothing deferred.
- Report ALWAYS written incl. total-failure case.

---

## Verification plan (pins this phase needs; P5 sibling owns the suite)

- Report-ALWAYS-on-total-failure pin (named in Phase 2 and here) — assert report/receipt/INDEX exist even when all lanes fail.
- Receipt schema pin — closed trigger_type set, five lanes, disk bytes+inodes, three recommendation classes.
- run_id shape pin — regex `ckpt-\d{8}_\d{6}\d{6}-[0-9a-f]{8}` (mirror any existing maintenance_run_identity test).
- D-C pin — no migration file for this system; no `maintenance_runs` write path.
- FE pins — update `mcps-gateway.pins.spec.ts`, `metrics-gear-menu.spec.ts`, `app.component.spec.ts`; new report pin spec (lazy route + gear entry + no header link + GET-only service).
- BE list route pin — GET-only, no write verb, auth scopes.

---

## Open questions

- **BE list route registration:** a new daemon router module must be registered in the app (where routers are wired). Confirm the canonical registration site during implementation; this plan names the requirement but does not pin the exact `create_app` line (not yet verified in-repo).
- **Manual origin marker:** the exact field on the manual schedule row that carries requester/origin (for `trigger_type=manual` derivation) is a Phase 1 (sibling) detail; P4 assumes it is readable by the report writer. Cross-ref `phase1-plan.md (sibling)`; no blocker to planning.
- **No contradiction found** with the one-shot-at-now-to-agent capability — verified in-repo (scheduling.py + scheduling_service.py). Nothing to escalate.
