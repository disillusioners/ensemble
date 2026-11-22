# Phase 3 — Five Lane Implementations (Central Maintenance System)

**Objective:** Define the exact weekly procedure for each of the five maintenance lanes (disk cleanup, POK sweep, skill-system check, abnormality check, metrics watch) as a measure → act → report loop with per-lane safety fences, a per-lane result block feeding Phase 4's receipt, and route-to-lane recommendations for out-of-lane findings.

**Status:** PLANNED

---

## Scope

### In scope
- Lane 1 DISK: fence recipe + hbk class rules + protected set + worktree-report-only rule; measure bytes AND inodes.
- Lane 2 POK: consume committed censuses as baselines, recompile fresh live listing, delta reconcile, new cluster adjudication under the guardrail.
- Lane 3 SKILL-SYSTEM: consolidated weekly check (usage growth, orphan residue, A/B stalls, bank requirement_json, skill-file/embedding GC).
- Lane 4 ABNORMALITY: anomaly_reports/clusters watch + old-tier `maintenance_runs` health watch (read-only).
- Lane 5 METRICS: `/api/tool-usage/summary` digest with path convention decision.
- Per-lane result-block schema (feeds P4 receipt); three-class recommendation routing; idempotency/limits.

### Out of scope
- The trigger chain (Phase 1, sibling) and the orchestrator dispatch mechanics (Phase 2).
- Report/receipt/INDEX authoring, run_id, manual trigger, FE (Phase 4).
- Applying any route-to-lane recommendation — lanes REPORT, they do not fix out-of-lane items.
- Writing into any daemon bookkeeping table (D-C); all lanes are read-only against daemon DB tables except Lane 1's filesystem sweep (fenced, via worker child).
- Old-tier `MaintenanceService` internals — separate and LIVE; Lane 4 only WATCHES its health (ruling).

---

## Preconditions & dependencies

- **Upstream:** Phase 2 registers the six lane skills and the `instance` category; each lane here is the body behind one registered skill. Lane skill names are fixed by Phase 2: `lane-disk-sweep`, `lane-pok-sweep`, `lane-skill-check`, `lane-abnormality-check`, `lane-metrics-watch`.
- **Downstream:** Phase 4 consumes each lane's result block into the receipt. Phase 5 (sibling) owns test pins.
- **Parallelizable-with:** Phase 4 (receipt schema) — agree on the result-block fields below. Lanes are mutually independent (different substrates); each is dispatched as its own worker child with its own skill (R4).
- **Base:** worktree `/home/nea/ensemble-src-wt-maintsys`, branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. NEVER operate from `/home/nea/ensemble-src`.
- **Seat capabilities:** Lane 4/2/3/5 read via the seat's existing `ens-db` SELECT-only + `pok` + `nest-report` + `metrics-read` categories; Lane 1's filesystem deletions run in a `worker` child (bash) under the fence recipe — NOT from the seat.

---

## Design decisions consumed

- **R4** — isolated execution boundary per lane; per-lane error capture; report ALWAYS written with per-lane status.
- **R9** — deletion safety: salvaged baseline fence recipe governs ALL disk-lane deletions; never operate from canonical; mktemp-fenced dry-runs; never delete files held open by live processes; explicit-class pathspecs; measure before/after bytes AND inodes.
- **D-A** — seat non-mutating; filesystem sweep and report writing go through worker children with absolute paths.
- **D-C** — files only, no DB writes into `maintenance_runs` (old tier's ledger).
- Old tier is SEPARATE and LIVE (`daemon/services/maintenance.py` `MaintenanceService`, `JOB_IDLE_POLICIES`, `test_maintenance_idle_starvation.py`); Lane 4 may WATCH it, never conflate.

---

## Binding requirements (architect addendum §7 — BR-11..BR-14)

Each BR below is a labeled, grep-verifiable block carrying its operative text. The addendum stays as the verdict record.

- **BR-11** Event-driven lane deadline — a lane is `failed-timeout` when a parent wake carries `source=system:watchdog` (`daemon/services/waiting_children_watchdog.py:627`, ≤3600s threshold+cadence) or `system=long-tool-nudge` (`daemon/services/long_tool_nudge.py:1471-1475`) naming that child; apply the valve once, else `[incomplete]`, then proceed. Deadline covers the initial dispatch AND its single re-dispatch. Report-writer fires on lane resolution OR global deadline (same wake mechanism bounds the writer wait ≈2h). NO polling. Loop-breaker (threshold 3, `daemon/config.py:6648`) and bash 1800s cap (`daemon/tools/bash.py:1283`) already bound the two likeliest wedge shapes.
- **BR-12** Lane-status mapping table (P2 T2.4): child `ERROR` ⇒ `failed`; silent-past-deadline ⇒ `degraded` + `[incomplete]`; `COMPLETED` ⇒ `ok|degraded` per its result block.
- **BR-13** Report-writer ladder (pre-decided): L0 dispatch, write order run→receipt→INDEX last · L1 crash/wedge ⇒ ONE fresh minimal-stub writer (no skill load) · L2 ⇒ ONE bare worker child, no load_skill, skeleton all-failed + best-effort receipt/INDEX · L3 accepted documented reportless terminal, surfaced by BR-15. The seat never writes (PD-6 intact). DP-1 ruling row (verbatim, lane leader 2026-11-22, ACCEPTED): "BR-13 L3 accepts a surfaced, documented reportless week when both writer rungs fail. Rationale: failure-isolation contract = a lane failure never kills the report; double independent writer failure is beyond contract; BR-15 ensures the residual is surfaced (detector + orphan listing), never silent."
- **BR-14** run_id single authority = the report-writer child. Carry a `run_token` in the dispatch task; re-dispatches reuse it (INDEX dedup survives re-mints). The venv interpreter and bash fallback are named in P2/P4 (BR-14).

---

## Common weekly procedure (all lanes)

Every lane follows **measure → act → report** and returns this **per-lane result block** (consumed by P4's receipt):

```
lane: <lane-disk-sweep | lane-pok-sweep | lane-skill-check | lane-abnormality-check | lane-metrics-watch>
status: ok | degraded | failed
measured_at: <ISO-8601 UTC>
summary: <one-line outcome>
metrics: { <lane-specific key>: <value> }        # bytes/inodes for disk; counts for others
actions_taken: [ <idempotent action ids or "none"> ]
recommendations:
  auto_fix_now: [ ... ]        # class 1 — safe, bounded, in-lane
  needs_emperor_word: [ ... ]  # class 2 — needs user decision
  route_to_lane: [ ... ]       # class 3 — another lane/owner owns the fix
evidence_paths: [ <absolute file paths> ]        # NEVER inline artifacts (4000-char cap)
```

- **status semantics:** `ok` = ran clean; `degraded` = partial (some sub-check failed but result produced); `failed` = lane could not produce a result. A failing lane NEVER blocks the report (R4).
- **Three-class routing:** every recommendation lands in exactly one class; class-2 items are surfaced, never acted on; class-3 items name the owning lane/team.
- **Idempotency:** each lane's actions are safe to re-run; a re-run within the same week must not double-count (dedup by run_id / measured_at where applicable).
- **BR-11/BR-12/BR-13/BR-14 consequences (cross-ref P2):** lanes resolve under the BR-11 event-driven deadline (a lane is `failed-timeout` on a `system:watchdog`/`system:long-tool-nudge` wake naming that child; apply the valve once, else `[incomplete]`, then proceed — initial dispatch AND single re-dispatch covered); per-lane result blocks (this section's schema) are consumed by the BR-13 writer ladder; the `run_token` threads the dispatch so re-dispatches reuse identity (INDEX dedup survives re-mints).

---

## Lane 1 — DISK CLEANUP

### Substrate (verified)
- Baseline: `.agents/shared/planning/maintenance-system/disk-lane-baseline-2026-11-18.md` — round-2 freed ~60 GB bytes + ~2.32M inodes; `/tmp` was ~86.6 GB / 3.20M entries (~51% of all used inodes) (lines 7-8).
- Consumer-class table (lines 10-24): lane-scratch-trees, full-repo base-extract clones, mktemp-trees, shakedown-fleet-*, pytest-of-nea (newest-3+current kept), _MEI* PyInstaller dirs, skillfix* (KEPT — paused incident), node-compile-cache, cn-review2-BSntpd (KEPT — live lane cwd), protected set (NEVER: 9router* user-owned, v0*sidecars-preserved, systemd/snap-private, .X11-unix).
- Fence recipe (lines 41-45): (1) recency `find <p> -newermt <now-48h> -print -quit` → delete only zero-hit; (2) open-by-process `/proc/*/fd` + cwd/exe/root readlink sweep immediately BEFORE each rm batch; (3) evidence-pointer grep of `/tmp/<name>` across `data/evidence/` + `.agents/` + `docs/` → protect-list; (4) worktrees: HEAD ancestor-of origin/latest AND porcelain-clean AND zero /proc refs AND not in a live commission; dirt = sidecar-rescue or keep+flag.
- hbk classes (measured 2026-11-22, NOT in the 2026-11-18 table — add as a class-family): `ensemble-hbk-err.*` ~1,746 EMPTY · `ensemble-hbk-verify.*` ~4,721 (297 POPULATED with diagnostics = RETENTION, never blind-delete) · `ensemble-hbk-velapsed.*` ~4,427 EMPTY · legacy `ensemble-hbk-verify.<pid>` — ≈10,894 files total. Root cause = 3 mktemp calls at `scripts/upgrade/lib.sh` SOURCE time (err:5939/verify:5995/velapsed:6032, shipped v0.31.18; readers use `-s` so empty-by-design). Sibling fix lane stops minting; our lane sweeps accumulated + recurring.

### Weekly procedure
1. **Measure:** `df` bytes + `df -i` inodes BEFORE; enumerate `/tmp` consumer classes; count hbk classes (empty vs populated).
2. **Act (fenced, worker child only):** build protect-list (fence 3), recency-scan candidates (fence 1), open-by-process sweep immediately before each rm batch (fence 2), delete only explicit-class pathspecs that pass all fences. Never operate from canonical. mktemp-fenced dry-run first. hbk: delete EMPTY members only; KEEP all populated `ensemble-hbk-verify.*` diagnostics. Worktrees: REPORT drift only, never delete (fence 4).
3. **Report:** bytes AND inodes reclaimed (before/after), candidates kept + why, protected hits, worktree drift list; result block per common schema.

### Safety fences applied
- R9 all four fences + never-operate-from-canonical + mktemp dry-run + explicit-class pathspecs + measure bytes AND inodes.
- Protected set NEVER touched; skillfix* and cn-review2-BSntpd KEPT.
- No idle gating: the lane runs regardless of busyness; file-level open-handle protection is safety, not gating.

### Out-of-lane items (route_to_lane class)
- The 5 ranked structural fixes in baseline lines 26-31 (full-repo extraction per gate/verify run; pytest-of-nea keep-3 bypass; shakedown-fleet-*/skill-capture-sink-session-* no TTL; journal .bak rotation; worktree census drift) → route-to-lane, NOT swept by us.

### Containment note (OQ3 leader ruling, 2026-11-22)
> "Lane-1 sweep is designed for the PRE-fix contract (empty ensemble-hbk-* files exist and are swept); NO gating dependency on the sibling hbk fix (c4b322b03, not yet on origin/latest) landing. Containment re-checked at rebase/tag time per BR-20." (Authority: lane leader, 2026-11-22.)

### Idempotency/limits
- 48h recency gate; newest-3+current pytest roots kept; bounded batch deletes; re-run within same week re-measures but never deletes protected/kept classes.

---

## Lane 2 — POK COMPACT / OPTIMIZE / SWEEP

### Substrate (verified)
- `.agents/shared/planning/pok-sweep/` (10 files): `census-active-2026-11-18.json/.md`, `dual-key-census-2026-11-18.md` + `dual-key-census-gen.py`, `cluster-proposals-2026-11-18.md`, `family-cap-consolidation-2026-11-18.md` + `_laneB_generate.py`, `live-active-2026-11-18-post-phase1.json`, `phase1-completion-scan-2026-11-18.md`, `phase4-recensus-2026-11-18.md`.
- Guardrail (cluster-proposals-2026-11-18.md:12-14): archive ONLY if (a) durable fact covered by another ACTIVE row, or (b) dated snapshot whose content lives in a routed home — NEVER on age/access_count. 240-char body cap; durable-fact rubric at line 12.
- Do-not-re-execute ledger (family-cap-consolidation-2026-11-18.md:9,27-29): snapshot-l3 4→3 merge already executed; prompt-assent/git cap-blocked; multi-token family gating LANDED (`daemon/repositories/pok/models.py:132` pattern `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$`); supersede is count-preserving + `pok_family_full`-exempt.
- Constraints: `POK_FAMILY_MAX=3` (`daemon/services/pok_service.py:117`), `POK_CURATED_CAP_PER_VIEW=2` (:115), `POK_BLOCK_MAX_ENTRIES=8` (:122).
- Open follow-ups wi-f1367551 (POK sweep) + wi-34d44806 (doctrine v2) bound scope.

### Weekly procedure
1. **Measure:** fresh live listing via `pok_query` (pattern `live-active-*.json`); per-row sha verify; active count; family prefix census.
2. **Act (read-only in-lane):** delta reconciliation vs committed baseline (phase-4 §2a-2b style); adjudicate new clusters under the guardrail; produce merge/consolidation PROPOSALS only — this lane does NOT write POK (curator authority is PM; merges are proposals). Re-use generator scripts as METHOD (`dual-key-census-gen.py`, `_laneB_generate.py`), do not hand-type.
3. **Report:** new active rows since baseline, sha mismatches, candidate clusters with COVERAGE verdict, cap-blocked families, result block.

### Safety fences applied
- No POK writes from this lane (read-only seat via `pok` category); proposals route to PM/curator.
- Guardrail: never archive on age/access_count; 240-char cap; durable-fact coverage required.
- Respect do-not-re-execute ledger — do not re-run the executed snapshot-l3 merge.

### Out-of-lane items (route_to_lane)
- Approved merge proposals → PM/curator (class 3). Doctrine v2 (wi-34d44806) → planner lane.

### Idempotency/limits
- Baseline comparison is by captured sha/snapshot, not live drift; re-runs reconcile against the same committed census; no writes = naturally idempotent.

---

## Lane 3 — SKILL-SYSTEM EVOLUTION CHECK

### Substrate (verified)
- 3 old-tier jobs UN_GATED (`daemon/services/maintenance.py:173-181`): `skill_metric_scan`, `skill_orphan_sweep`, `skill_ab_inactivity_sweep`; plus `project_skill_gc` (:183).
- Skill tables: migrations 20260710-20261031 family. Services: `daemon/services/skill_evolution_service.py` + A/B CAS; `daemon/repositories/skill/skill_bank_repository.py`.
- A daily-skill-system-watch schedule exists (live-DB state, definition NOT in repo) — reference pattern only; our lane is the WEEKLY consolidated check.

### Weekly procedure
1. **Measure** (read-only via `ens-db` SELECT): usage-record growth since last week; orphan-sweep residue (usage records that escaped finalization); A/B stalls (pairs stuck in a state); bank `requirement_json` presence; skill-file/embedding GC candidates.
2. **Act:** none in-lane — consolidate findings; classify each as healthy / watch / act-needed.
3. **Report:** counts + deltas, stalled A/B pairs, orphan residue, result block.

### Safety fences applied
- Read-only; no skill-keeper/evolution write triggered by this lane; no daemon bookkeeping mutation (D-C).
- Do NOT conflate with the daily watch (that is a separate live schedule).

### Out-of-lane items (route_to_lane)
- Stalled A/B pairs / orphan residue needing repair → skill-keeper / old-tier repair lane (class 3). Bank requirement gaps → owning skill author.

### Idempotency/limits
- Window-based (since last measured_at); bounded SELECTs; no writes.

---

## Lane 4 — ABNORMALITY / CRITICAL CHECKS

### Substrate (verified)
- `anomaly_reports` + `anomaly_clusters` tables (`daemon/migrations/versions/20261105_020500_create_anomaly_reports.sql`, `020501_create_anomaly_clusters.sql`). Cluster row columns verified at `020501...sql:78-89`: `cluster_key_hash`, `tree_root_instance_id`, `category`, `first_report_at`, `last_report_at`, `report_count`, `max_severity`, `board_item_id`.
- Timestamps are FIXED-WIDTH ISO-8601 TEXT (`:35-40`) → lexicographic windows with Python-computed cutoffs; NEVER SQL `now() - interval`.
- `anomaly_retention` job deletes aged rows (`daemon/services/maintenance.py:193`, UN_GATED) — retention-aware.
- Seat already holds `nest-report` + `nest-report-read` (meta.json:23-24).
- `maintenance_runs` history as signal: interrupted row's `completed_at` = daemon death timestamp; boots <60s leave no row; skip-streak WARNINGs = starvation telemetry. `kind` vocabulary `auto|manual_dry_run|manual_execute` (`daemon/repositories/maintenance_runs/models.py:11,105-107`).

### Weekly procedure
1. **Measure** (read-only): anomaly clusters in the weekly window by severity (`max_severity`), repeat incidents (`report_count`), unresolved (`board_item_id` null); old-tier `maintenance_runs` health — interrupted rows, skip-streak WARNINGs, boots with no row.
2. **Act:** none in-lane — classify anomalies; flag starvation telemetry (the old tier's idle-only starvation class — 500 skips, 0 executions — is a regression PIN for P5 sibling, not re-litigated here).
3. **Report:** cluster counts by severity, top repeat incidents, old-tier health signals, result block.

### Safety fences applied
- Read-only; agents CANNOT mutate daemon bookkeeping tables (`maintenance_runs`) — SELECT-only via `ens-db` seat (D-C).
- Retention-aware: aged rows already deleted by `anomaly_retention`; do not report them as active.
- TEXT ISO timestamps → Python cutoffs, lexicographic comparison only.

### Out-of-lane items (route_to_lane)
- Critical anomalies needing user decision → needs_emperor_word (class 2). Old-tier starvation/health defects → old-tier owner (class 3). Board items for clusters → PM/board lane.

### Idempotency/limits
- Window-based on `last_report_at`; bounded SELECTs; no writes.

---

## Lane 5 — METRICS WATCH

### Substrate (verified)
- `GET /api/tool-usage/summary` (`daemon/routers/tool_usage.py:115-141`): per-tool aggregate; `days` clamp 1-90 (`:136`); scopes `maintenance:read|metrics:read` (`:41-42`); 1 MB serialized-response guard (`:49-50`); nested per-agent list capped top-50 (module docstring :17-18).
- Seat holds `metrics-read` (meta.json:22).

### Weekly procedure
1. **Measure:** pull `/api/tool-usage/summary` for the weekly window (days bounded ≤90); note top tools, growth vs last week, any anomaly spikes.
2. **Act:** none in-lane — produce a metrics digest.
3. **Report:** digest + deltas, result block.

### Safety fences applied
- Read-only GET; respect days clamp and 1 MB guard; no write verb.

### Digest output path (OPEN ITEM — decided here, flagged)
- The digest path convention is unverified in-repo. Precedent `.agents/shared/planning/<lane>/digests/` leaf is DEV-LOCAL / gitignored — do NOT put durable weekly reports there. **Decision:** the durable weekly report is the Phase 4 file-based report (`run-<run_id>.md`); any Lane 5 digest is written into that report's `metrics` section / an evidence path under the report artifact, NOT a gitignored digests leaf. If a standalone digest file is needed, use an absolute path under the report artifact dir and record it in the receipt's `evidence_paths`.

### Out-of-lane items (route_to_lane)
- Metric regressions → owning tool/feature lane (class 3).

### Idempotency/limits
- days ≤90; bounded response; no writes.

---

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Disk sweep deletes a live/evidence-referenced file | High | All four R9 fences + protected set + open-by-process sweep immediately before rm; mktemp dry-run; worker child only. |
| hbk populated diagnostics blind-deleted | High | Delete EMPTY members only; KEEP all populated `ensemble-hbk-verify.*`; explicit-class pathspecs. |
| POK lane writes despite read-only intent | High | No POK writes in-lane; proposals only; curator authority stays with PM. |
| TEXT timestamp window done wrong | Medium | Python-computed cutoffs, lexicographic comparison (substrate :35-40). |
| Lane failure blocks the report | High | Per-lane status capture; report ALWAYS written (R4); fan-in valve marks `[incomplete]`. |
| Digest written into gitignored dir | Low | Durable report is the P4 file; digest folded into report artifact. |

---

## Acceptance criteria

- Lane 1: measures bytes AND inodes before/after; all four fences encoded; hbk empty-vs-populated rule; protected set + skillfix*/cn-review2 KEPT; worktrees report-only.
- Lanes 2-5: each has substrate file:line, measure→act→report procedure, safety fences, result block matching the common schema, three-class recommendations, idempotency/limits.
- No lane writes daemon bookkeeping tables; no lane applies a route-to-lane fix.
- Every lane returns ok|degraded|failed; a failing lane never kills the report.

---

## Verification plan (pins this phase needs; P5 sibling owns the suite)

- Lane 1: P5 pin for the fence recipe (recency / open-by-process / evidence-pointer / worktree-report-only) and hbk empty-vs-populated rule; regression pin for the old-tier idle-only starvation class (500 skips, 0 executions) — sibling-owned, named here.
- Lane 2: guardrail pin — no archive on age/access_count; do-not-re-execute ledger respected.
- Lane 4: TEXT-ISO-window pin (Python cutoffs, no SQL now()-interval); read-only `maintenance_runs` access.
- Lane 5: days-clamp + no-write-verb pin on the digest.
- No new test files authored in this phase — name the pins for P5.

---

## Open questions

- **OQ3 lane-1 containment (resolved):** leader ruling (2026-11-22) — "Lane-1 sweep is designed for the PRE-fix contract (empty ensemble-hbk-* files exist and are swept); NO gating dependency on the sibling hbk fix (c4b322b03, not yet on origin/latest) landing. Containment re-checked at rebase/tag time per BR-20." Folded into Lane 1 (Containment note). No blocker to planning.
- **Lane 5 digest path:** resolved in-lane (fold into P4 report artifact) but flagged because the in-repo convention is unverified; if the implementing lane finds an established durable convention, use it and update this section.
- **Lane 2 merge execution:** this lane only proposes; confirm PM/curator remains the writer for POK merges (consistent with D-C's no-agent-writes-to-daemon-state principle). No blocker to planning.
