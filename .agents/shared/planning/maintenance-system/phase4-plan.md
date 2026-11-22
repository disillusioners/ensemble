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
- **D-D** — canonical manual trigger = scheduler ONE-SHOT at `run_at=now` targeting maintenancer (same dispatch path as the weekly chain; receipt `trigger_type` TRANSPORTED via the message-body trigger token — see Task 4.6: `trigger: scheduled` embedded in the chain message literal (P1 T1.1), `trigger: manual` embedded in the documented manual invocation's message; the orchestrator parses the token and passes `trigger_type` to the writer child; no new DB field; Ari invokes via `task_schedule` tool surface). Fallback: direct `send_message` to a maintenancer instance.
- **D-E** — gc-fe LANDED (merge 7f928cbc5, `frontend/src/app/pages/chat/chat.html:100-118` room browser) but header nav untouched (`app.html:11-39` hardcoded) → collision premise VOID → WIRE IT, GEAR MENU ONLY (app.ts:577-578 rule), lazy `/report` route + `report.component.ts`, read-only BE list route (live_views has NO list endpoints — `daemon/services/live_views.py:36-37` GET/HEAD only), do NOT host under Maintenance shell (`maintenanceAvailabilityGuard` hides it unless checkpoint-cleanup ready — `app.routes.ts:214-219`). FE deferred-part NOTHING.
- **D-H** — run_id IS single-sourced from `new_maintenance_run_id()` (do NOT switch authorities). The receipt writer is a worker child in an agent turn (cannot call daemon Python as a function), so it shells out to the pure helper via the DAEMON VENV interpreter `/home/nea/ensemble-src/.venv/bin/python -c` (PRIMARY — bare `python3` FAILS on this host with ModuleNotFoundError sqlalchemy, per BR-14); a bash format-spec mint is the documented FALLBACK. Interpreter + run_token pinned in P5; agent-side mechanics in Task 4.1.

---

## Binding requirements (architect addendum §7 — BR-14, BR-16, BR-16b, BR-17, BR-18, BR-19)

Each BR below is a labeled, grep-verifiable block carrying its operative text. The addendum stays as the verdict record.

- **BR-14** run_id single authority = the report-writer child. Strike the seat-mint step (the seat has no bash/filesystem). Name the daemon venv interpreter (`/home/nea/ensemble-src/.venv/bin/python` — bare `python3` fails on this host: ModuleNotFoundError sqlalchemy). Bash fallback `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)` is a BINDING rung in the skill body. Carry a `run_token` in the dispatch task; re-dispatches reuse it (INDEX dedup survives re-mints).
- **BR-16** Artifact root pinned + created: the LIVE-INSTALL daemon project root `.agents/maintenancer/RESULTS/` (never a worktree — worktrees are report-only per R8/R9; absolute `write_file` bypasses the boundary check, so the root must be stated ONCE in the `run-report-writer` skill); dir created by the seeder at first boot; the FE list route and the live_views root read THE SAME root.
- **BR-16b** INDEX appends via `write_file(append=True)` (in-process lock + tail read-back, `daemon/tools/filesystem.py:1570-1590`), never `bash >>`; rows carry the run_id.
- **BR-17** BE list-route safety contract: fixed root resolved at daemon startup; run_id regex `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$` used only as a filename component + `os.path.realpath` containment (defense-in-depth, mirroring `daemon/services/live_views.py:30-35`); GET(+HEAD)-only with the verb pinned by a spec (`.not.toMatch(/\.(post|put|delete|patch)\(/)` posture); scope `maintenance:read` (`daemon/auth/scopes.py:76`); registration in `daemon/api.py` beside `tool_usage_router`.
- **BR-18** Content access = live_views root registration for `.agents/maintenancer/RESULTS/` (reuses traversal guard + `.md` renderer; no new path-serving surface); list response = view URLs (`run_id, trigger_type, started_at, ended_at, overall_status, report_view_url, receipt_view_url`), not repo-relative paths. (Architect rulings §9.3/§9.4.) **Registration pinned (all four elements):** (1) **Mechanism** = daemon-injected lifespan pattern — mirror `inject_reports_live_view_root(config, data_dir)` (`daemon/api.py:434`, called at `:2291` during lifespan) with a new `inject_maintenance_results_live_view_root`; NOT a static `config.yaml` absolute path. (2) **Root name** = `maintenance-reports` (named root, sibling of `designer-artifact`/`planning`/`tmp-images`). (3) **Form** = the RELATIVE root `.agents/maintenancer/RESULTS/` resolved at daemon startup against the live-install project root (reconciles BR-16's relative root with live_views' startup-resolved paths; host-portable — no absolute path in config). (4) **Allowlist** = `.md` renderer only, restricted to `run-*.md`, `receipt-*.md`, `INDEX.md` filename patterns.
- **BR-19** Anchor corrections in P4 FE tasks: TWO conditional appends (Database `frontend/src/app/app.ts:797` — no idempotence guard; Maintenance `:842` — route-keyed); `dbUpdateIdx` resolves to the FIRST (Database); static Report sits after Metrics (`:596`) so `metricsIdx < reportIdx < dbUpdateIdx` holds; extend the three verified spec files (`mcps-gateway.pins.spec.ts:60-84`, `metrics-gear-menu.spec.ts:47-59`, `app.component.spec.ts:2092-2114`).

---

## Tasks

### Task 4.1 — Report path convention + run_id (D-B)
- **Description:** Establish `.agents/maintenancer/RESULTS/` as the durable report dir (precedent: `.agents/tester/RESULTS/` dated-file house pattern). Three artifacts per run: `run-<run_id>.md` (human-readable report + recommendations), `receipt-<run_id>.md` (machine-readable receipt), `INDEX.md` (chronological ledger). `run_id = ckpt-YYYYMMDD_HHMMSSffffff-<hex8>` — format is `ckpt-` + UTC microsecond timestamp + `secrets.token_hex(4)` suffix (`daemon/services/maintenance_run_identity.py:6,34-37,46-47`). **BR-14 (D-H aligned):** run_id single authority = the report-writer child — strike any seat-mint step (the seat has no bash/filesystem). The writer carries a `run_token` in its dispatch task; re-dispatches reuse it (INDEX dedup survives re-mints). Mint mechanics (agent-side):
  - **PRIMARY mint path:** the child shells out to the pure helper via the daemon venv interpreter (bare `python3` FAILS on this host — ModuleNotFoundError sqlalchemy): `cd <repo-checkout> && /home/nea/ensemble-src/.venv/bin/python -c "from daemon.services.maintenance_run_identity import new_maintenance_run_id; print(new_maintenance_run_id())"`. Single-sourcing preserved (format changes propagate from one module); the helper module is pure stdlib, but the IMPORT CHAIN is not (KMS filter installs at import) — the venv works but costs ~6.5s. **Task note:** the import-purity check was REPLACED by the BR-14 leg in P5 T5.5 (venv-interpreter + bash-fallback + run_token reuse — see phase5-plan.md "replaces the import-purity check"); this doc cites that replacing pin and asserts nothing about import-time side effects.
  - **FALLBACK (BINDING rung in the skill body):** if the import is unavailable in the executing context, bash format-spec mint `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)` matching the canonical regex — drift-risk-bounded (regex-pinned by the P5 run_id shape pin already listed in the Verification plan). Rows carry the run_id.
  - **INDEX.md dedup by run_id covers both paths (and re-dispatches via run_token)** — already in Task 4.4; keep unchanged.
  Use `.md` only — `.log` is gitignored.
- **Touch points:** **SPEC-ONLY — the implementation surface is P2's `run-report-writer.md` skill body (owned by P2 T2.5); P4 edits NO file.** The path convention is documented here as spec.
- **Verification:** path uses `.md`; run_id matches the helper's regex; INDEX is chronological by run_id prefix (lexicographic = chronological, per helper docstring :36-37).
- **Agent hint:** `doc-writer` / `worker` (report writer child).

### Task 4.2 — Report body format (`run-<run_id>.md`)
- **Description:** Human-readable: header (run_id, trigger type, start/end timestamps, overall status), one section per lane with its status (ok/degraded/failed) + summary + metrics + evidence paths, a Recommendations section grouped into three classes (auto-fix-now / needs-Emperor-word / route-to-lane), and a `### Gaps` section for `[incomplete]` lanes (fan-in valve precedent, workflow.md:42). Report is ALWAYS written even on total lane failure (R4) — in that case all lanes show `failed` and the report still exists.
- **Touch points:** **SPEC-ONLY — implementation surface is P2's `run-report-writer.md` (owned by T2.5); P4 edits NO file.** Report template documented here as spec.
- **Verification:** report contains all five lane sections + three recommendation classes + gaps; total-failure case still produces a report.
- **Agent hint:** `worker` (report writer child, absolute paths).

### Task 4.3 — Receipt schema (`receipt-<run_id>.md`)
- **Description:** Machine-readable fields, one per line or a fenced YAML block. **The `lanes:` entries embed the PD-4a CANONICAL RESULT BLOCK verbatim (frozen in `decisions.md` PD-4/PD-4a — full field set: `lane`, `status`, `measured_at`, `summary`, `metrics`, `actions_taken`, `recommendations` {three classes}, `evidence_paths`; cited by reference here, never re-embedded divergently — dropping `measured_at` or `actions_taken` from any lane entry is a schema violation).** Receipt header fields:
  ```
  run_id: <ckpt-...>
  trigger_type: scheduled | manual
  started_at: <ISO-8601 UTC>
  ended_at: <ISO-8601 UTC>
  overall_status: ok | degraded | failed
  lanes:
    - <PD-4a canonical block, one per lane (all five present)>
  recommendations:
    auto_fix_now: [ ... ]
    needs_emperor_word: [ ... ]
    route_to_lane: [ ... ]
  ```
  `bytes_reclaimed` AND `inodes_reclaimed` are required inside the disk lane's `metrics` ("where applicable"); other lanes carry their own metrics keys. `trigger_type` is `scheduled` for the weekly chain, `manual` for the one-shot manual path (D-D).
- **Touch points:** **SPEC-ONLY — implementation surface is P2's `run-report-writer.md` (owned by T2.5); P4 edits NO file.** Receipt schema documented here as spec (PD-4a reference).
- **Verification:** receipt parses; all five lanes present with statuses; disk lane has both bytes+inodes; three recommendation classes present; trigger_type in the closed set.
- **Agent hint:** `worker` (report writer child).

### Task 4.4 — INDEX.md format (BR-16b)
- **Description:** Chronological table, newest first or last (state the choice), columns: `run_id | date | trigger_type | overall_status | report_path | receipt_path`. Append-only; one row per run; ordered by run_id prefix (lexicographic = chronological). **BR-16b:** INDEX appends via `write_file(append=True)` (in-process lock + tail read-back, `daemon/tools/filesystem.py:1570-1590`) — NEVER `bash >>`. Rows carry the run_id. Idempotence: re-running a run_id must not duplicate its INDEX row (dedup by run_id; covers re-dispatches via the run_token).
- **Touch points:** **SPEC-ONLY — implementation surface is P2's `run-report-writer.md` (owned by T2.5); P4 edits NO file.** INDEX format documented here as spec.
- **Verification:** INDEX rows sorted; each run_id appears once; paths are absolute or repo-relative under `.agents/maintenancer/RESULTS/`; no `bash >>` in the writer skill; append uses the locked helper.
- **Agent hint:** `worker` (report writer child).

### Task 4.5 — D-C justification (no DB) — state, do not re-litigate
- **Description:** Document in this plan: files only, do NOT write `maintenance_runs`. Reasons: (1) its `kind` vocabulary is `auto|manual_dry_run|manual_execute` (`daemon/repositories/maintenance_runs/models.py:11,105-107`) — semantically mismatches our `trigger_type` `scheduled|manual`; (2) writing agent-turn rows into daemon bookkeeping tables conflicts with the seat's non-mutating convention (D-A); (3) tier conflation risk is explicit (old tier is separate + LIVE); (4) trigger-side audit already exists in `schedule_executions`. No new DB tables/migrations are justified for this system.
- **Touch points:** this plan (justification record); no code.
- **Verification:** no migration file created for this system; no `maintenance_runs` write path in the design.
- **Agent hint:** `doc-writer` (record only).

### Task 4.6 — D-D manual trigger: canonical path + invocation + receipt (trigger_type derivation owned here)
- **Description:** Canonical path = scheduler ONE-SHOT at `run_at=now` targeting `maintenancer`, same dispatch as the weekly chain. Ari invokes via the `task_schedule` tool surface (`daemon/tools/scheduling.py:165-189`): `task_schedule(label=..., message=..., when=<now ISO 8601>, recurrence="once", agent_id="maintenancer")`. Capability VERIFIED in-repo: `TaskScheduleInput` supports `recurrence="once"` with ISO-8601 `when` (`:93-94`), `agent_id` targeting (`:98,175`), and past/near-now one-shots are handled — the adapter fires them immediately within the lateness cap or skips, and the operator reads truth from executions history (`daemon/services/scheduling_service.py:491-493`). **trigger_type — ONE owner (leader ruling, iteration-002 #8): P4 T4.6 owns the transport spec end-to-end; P1 T1.1 embeds the scheduled token and cross-references here.** TRANSPORT = **message-body trigger token** (zero daemon code, no DB field, and correct even though the writer child has NO DB surface): (1) the weekly chain row's `message` literal (authored by P1 T1.1) embeds `trigger: scheduled` on its own line; (2) the canonical manual invocation's `message` argument embeds `trigger: manual` (documented in the exact invocation below — Ari includes it); (3) the ORCHESTRATOR parses the `trigger:` token from its dispatch message at run start and passes `trigger_type` through to the `run-report-writer` child in the task envelope (alongside `run_token`); (4) the writer embeds exactly what it receives — it never reads the DB. The row-config derivation (`config.chain` present ⇒ scheduled; absent + `creator_instance_id` present ⇒ manual) is retained as the DOCUMENTED rationale for why each message template carries its token — it is not the runtime path. **REST-fallback caveat (documented limitation):** REST-created rows carry NO `creator_instance_id` (`scheduling_service.py:818-824`) and REST callers may omit the token — REST-origin manual runs are outside the canonical path (documented, unsupported). Exact manual invocation: `task_schedule(label="central-maintenance-manual-<ts>", message="trigger: manual\nExecute one manual Central Maintenance run now: dispatch the five lane children + report writer per the orchestrator workflow; parse the trigger token and pass trigger_type to the writer child.", when=<now ISO 8601>, recurrence="once", agent_id="maintenancer")`. Fallback: direct `send_message` to a maintenancer instance with the same token line (documented, but NOT canonical). Trigger mechanics detail lives in `phase1-plan.md (sibling)` — cross-ref only.
- **Touch points:** this plan (exact invocation + receipt + derivation spec); `phase1-plan.md (sibling)` cross-ref for mechanics; Ari's invocation documented as a runbook note (no Ari prompt edit — out of lane).
- **Verification:** invocation uses recurrence=once + agent_id=maintenancer AND the `trigger: manual` token line in the message; receipt trigger_type equals the token the orchestrator parsed and passed through the writer envelope; fallback documented; no contradiction with scheduling.py (verified above).
- **Agent hint:** `doc-writer` (runbook note); trigger mechanics are sibling Phase 1.

### Task 4.7 — D-E FE wiring: gear-menu entry + lazy route + view + BE list route + specs
- **Description:**
  1. **Gear-menu entry (BR-19):** add a static `settingsMenuItems` entry in `frontend/src/app/app.ts` (array at `:566-597`), placed AFTER `Metrics` (`:596`) and BEFORE the conditional appends — TWO of them: Database (`app.ts:797`, NO idempotence guard) and Maintenance (`:842`, route-keyed guard). `dbUpdateIdx` in every pin spec resolves to the FIRST (Database, `:797`). GEAR MENU ONLY — no top-level header link (header nav is hardcoded in `app.html:11-39`; do not touch it). Follow the idempotence guard pattern (`app.ts:841` `.some(i => i.route === ...)`) if using a conditional-append; a static entry is simpler and matches MCP Gateway/Metrics.
  2. **Lazy `/report` route:** add `{ path: 'report', loadComponent: () => import(...).then(m => m.ReportComponent), title: '...' }` in `frontend/src/app/app.routes.ts` BEFORE the catch-all `**` (`:225`). Do NOT host under the Maintenance shell (`maintenance/checkpoint-cleanup` at `:214-219` uses `maintenanceAvailabilityGuard` which hides it unless checkpoint-cleanup ready).
  3. **Report view component (BR-18):** new `frontend/src/app/pages/report/report.component.ts` (+ html/scss/spec) — read-only list of runs from the BE list route + render selected report; mirror the MCP Gateway read-only page pattern (`frontend/src/app/pages/mcps-gateway/`). **Content access = register `.agents/maintenancer/RESULTS/` as a live_views root** (reuses the traversal guard + `.md` renderer; no new path-serving surface) — **registration per BR-18's four pinned elements: daemon-injected lifespan (`inject_maintenance_results_live_view_root` mirroring `inject_reports_live_view_root`, `daemon/api.py:434/:2291`), root name `maintenance-reports`, relative root resolved at startup, `.md` allowlist `run-*.md`/`receipt-*.md`/`INDEX.md`**. **Response shape = view URLs** (`{run_id, trigger_type, started_at, ended_at, overall_status, report_view_url, receipt_view_url}`), NOT repo-relative paths. (Architect rulings §9.3/§9.4.)
  4. **BE read-only list route (BR-17):** add a GET-only route that lists report artifacts — mirror the tool-usage/metrics read-only door posture (`daemon/routers/tool_usage.py:39-50` GET-only, auth scopes). live_views has NO list endpoint (`daemon/services/live_views.py:36-37` GET/HEAD only) so a dedicated read route is required. Safety contract: fixed root resolved at daemon startup; `run_id` param validated `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$` and used ONLY as a filename component under the fixed root + `os.path.realpath` containment (defense-in-depth, mirroring `live_views.py:30-35`); GET(+HEAD)-only with the verb pinned by a spec (`.not.toMatch(/\.(post|put|delete|patch)\(/)` posture); scope `maintenance:read` (`daemon/auth/scopes.py:76`); registration in `daemon/api.py` beside `tool_usage_router`.
  5. **Spec updates (ordering pins, BR-19):** update `frontend/src/app/pages/mcps-gateway/mcps-gateway.pins.spec.ts` (`:60-84`) and `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts` (`:47-59`) (both pin the `MCP Gateway → Metrics → (conditional appends)` order) and `frontend/src/app/app.component.spec.ts` (`:2092-2114`) to include the new Report entry in the ordering assertions. Add a report page pin spec mirroring the gateway pins (lazy route + gear entry + no header link + read-only service). **TWO conditional appends exist** (Database `app.ts:797` — NO idempotence guard; Maintenance `:842` — route-keyed guard); `dbUpdateIdx` in every pin spec resolves to the FIRST (Database); static Report sits after Metrics (`:596`) so `metricsIdx < reportIdx < dbUpdateIdx` holds. Task 4.7.5 must name BOTH appends.
  - **FE ordering note:** the new entry goes AFTER Metrics and BEFORE the conditional appends; every existing ordering pin that asserts `... < dbUpdateIdx` must be extended to assert `reportIdx < dbUpdateIdx` and `metricsIdx < reportIdx`.
- **Touch points:** `frontend/src/app/app.ts`, `frontend/src/app/app.routes.ts`, `frontend/src/app/pages/report/` (new), a new daemon router module for the list route + its registration, `mcps-gateway.pins.spec.ts`, `metrics-gear-menu.spec.ts`, `app.component.spec.ts`, new report pin spec.
- **Verification:** `/report` reachable via gear menu only; no header link; lazy route before wildcard; BE route GET-only; all ordering pins green with Report in the chain; FE deferred-part NOTHING (fully wired).
- **Agent hint:** `coder` (FE + BE), `tester` (P5 pins).

### Task 4.8 — Seeder creates RESULTS dir at first boot (BR-16)
- **Description:** BR-16 pins the artifact root as the LIVE-INSTALL daemon project root `.agents/maintenancer/RESULTS/` (never a worktree — worktrees are report-only per R8/R9; absolute `write_file` bypasses the boundary check, so the root must be stated ONCE in the `run-report-writer` skill). The dir does not exist in this worktree (no `.agents/maintenancer/`); its presence at any given install root is ENVIRONMENT-DEPENDENT and not asserted here (corrected 2026-11-22 — the earlier "live install has only shared/" claim was environment-specific; the seeder's mkdir is idempotent either way). Add a seeder step that creates `.agents/maintenancer/RESULTS/` at first boot. The FE list route (Task 4.7.4) and the live_views root (BR-18) read THE SAME root.
- **Touch points:** **NO file edits by P4.** `run-report-writer` skill body is **P2-OWNED (T2.5 — the sole owner package-wide; T2.5's spec explicitly writes reports/receipts/INDEX to this BR-16 root)**; FE list route (reads the same root); live_views root registration (`daemon/api.py` lifespan — BR-18). **P4 DECLARES the root contract only** (this task + Task 4.7 consumers); it edits no file. Root resolution note: the seeder (P1), the BE route (BR-17), and the live_views registration (BR-18) must resolve the SAME live-install project root via the daemon's existing root-resolution idiom (implementer picks the `skill_roster.py:97` `_repo_root`-style helper or the `api.py:434` data_dir-based resolution — the basis must be identical across all three; P5 pins the equivalence).
- **Verification:** dir exists after a fresh boot; root is identical across seeder, writer skill, FE list route, and live_views registration; no worktree path is ever used as the root.
- **Agent hint:** `coder` / `devops`.

---

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Report not written on total lane failure | High | Report-writer dispatch unconditional (Phase 2); fan-in valve marks `[incomplete]`; report-ALWAYS pin in P5. |
| Seat writes report directly (breaks non-mutating convention) | High | D-A: worker child with absolute paths; deny list unchanged; scoped-write fallback named with tradeoffs, not silently picked. |
| `.log` used → gitignored → report vanishes | Medium | `.md` only (D-B); pin the extension. |
| run_id re-derived incorrectly / collisions | Low | PRIMARY: shell out to the pure `new_maintenance_run_id()` via the daemon venv interpreter `/home/nea/ensemble-src/.venv/bin/python -c` (single-sourced; bare `python3` fails on this host — BR-14, pinned in P5); FALLBACK: bash format-spec mint `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)`, drift-risk-bounded by the P5 run_id shape regex pin; hex8 suffix; dedup INDEX by run_id (covers both paths). |
| FE entry breaks ordering pins | Medium | Place AFTER Metrics BEFORE conditional appends; update all three pin specs; add report pin. |
| FE entry under Maintenance shell hidden by guard | Medium | Standalone `/report` route, not under `maintenance/checkpoint-cleanup`. |
| BE list route accidentally write-capable | High | GET-only, mirror tool-usage door scopes; no list endpoint reuses live_views (it has none). |
| Manual trigger capability assumed | Medium | VERIFIED against scheduling.py:93-98,175 + scheduling_service.py:491-493 — no contradiction; cross-ref Phase 1 for mechanics. |

---

## Acceptance criteria

- Report + receipt + INDEX written per run via worker child with absolute paths; `.md` only; run_id matches `ckpt-YYYYMMDD_HHMMSSffffff-<hex8>` (minted by the writer via venv PRIMARY or bash FALLBACK; run_token reuses identity across re-dispatches).
- Receipt has run id, trigger type scheduled|manual (TRANSPORTED via the message-body trigger token per Task 4.6 — the writer embeds what the orchestrator parsed and passed; no DB read), start/end timestamps, five per-lane results + statuses (PD-4a blocks incl. measured_at + actions_taken), three recommendation classes, bytes AND inodes for disk lane.
- INDEX chronological, dedup by run_id; appends via `write_file(append=True)` (BR-16b), never `bash >>`; rows carry run_id.
- D-C justification recorded; no DB tables/migrations; no `maintenance_runs` writes.
- Artifact root pinned (BR-16): live-install daemon project root `.agents/maintenancer/RESULTS/`, created by seeder at first boot; FE list route + live_views root read the SAME root; never a worktree.
- BE list route (BR-17): fixed root + run_id regex + realpath containment + GET(+HEAD)-only + `maintenance:read` scope + registration in `daemon/api.py` beside `tool_usage_router`.
- Content access (BR-18): `.agents/maintenancer/RESULTS/` registered as a live_views root — daemon-injected lifespan mechanism (`inject_maintenance_results_live_view_root` mirroring `inject_reports_live_view_root`, `daemon/api.py:434/:2291`), root name `maintenance-reports`, relative root resolved at startup, `.md` allowlist (`run-*.md`, `receipt-*.md`, `INDEX.md`); list response = view URLs (`run_id, trigger_type, started_at, ended_at, overall_status, report_view_url, receipt_view_url`).
- D-D canonical manual trigger documented with exact `task_schedule` invocation + receipt trigger_type derivation + fallback; cross-ref Phase 1.
- D-E: gear-menu Report entry (after Metrics, before BOTH conditional appends, no header link), lazy `/report` route before wildcard, report view component, GET-only BE list route, all ordering pins updated (three verified spec files + report pin); nothing deferred. `dbUpdateIdx` resolves to the FIRST (Database) append.
- Report ALWAYS written incl. total-failure case (BR-13 ladder L0→L1→L2→L3).

---

## Verification plan (pins this phase needs; P5 sibling owns the suite)

- Report-ALWAYS-on-total-failure pin (named in Phase 2 and here) — assert report/receipt/INDEX exist even when all lanes fail (BR-13 ladder L0→L1→L2→L3).
- Receipt schema pin — closed trigger_type set, five lanes, disk bytes+inodes, three recommendation classes; trigger_type equals the message-body token transported through the writer envelope (per Task 4.6).
- run_id shape pin — regex `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$` (BR-17); mint via venv PRIMARY or bash FALLBACK; the replacing pin is the BR-14 leg in P5 T5.5 (import-purity check superseded — all three sites agree: this line, Task 4.1's task note, and phase5 "replaces the import-purity check").
- D-C pin — no migration file for this system; no `maintenance_runs` write path.
- Artifact root pin (BR-16) — seeder creates `.agents/maintenancer/RESULTS/` at first boot; FE list route + live_views root read the SAME root; never a worktree.
- BE list route pin (BR-17) — GET-only, no write verb, auth scopes, run_id regex + realpath containment, registration in `daemon/api.py` beside `tool_usage_router`.
- Content access pin (BR-18) — `.agents/maintenancer/RESULTS/` registered as a live_views root; list response = view URLs (`run_id, trigger_type, started_at, ended_at, overall_status, report_view_url, receipt_view_url`).
- FE pins (BR-19) — update `mcps-gateway.pins.spec.ts` (`:60-84`), `metrics-gear-menu.spec.ts` (`:47-59`), `app.component.spec.ts` (`:2092-2114`); new report pin spec (lazy route + gear entry + no header link + GET-only service); `dbUpdateIdx` resolves to the FIRST (Database) append; `metricsIdx < reportIdx < dbUpdateIdx`.
- INDEX append pin (BR-16b) — `write_file(append=True)`, never `bash >>`.

---

## Open questions

- **BE list route registration (resolved by BR-17):** registration pinned in `daemon/api.py` beside `tool_usage_router`; fixed root resolved at daemon startup. No open item remains.
- **Manual trigger_type transport (resolved, leader ruling iteration-002 #8):** message-body trigger token — chain literal embeds `trigger: scheduled` (P1 T1.1), manual invocation embeds `trigger: manual` (T4.6 exact invocation), orchestrator parses and passes through the writer envelope; row-config derivation retained as documented rationale only. P1 cross-references this spec without redefining it. No open item remains.
- **Manual origin marker (resolved):** no separate marker field — the token IS the marker (no DB field, consistent with derivation-only).
- **No contradiction found** with the one-shot-at-now-to-agent capability — verified in-repo (scheduling.py + scheduling_service.py). Nothing to escalate.
