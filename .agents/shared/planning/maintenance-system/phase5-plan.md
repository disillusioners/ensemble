# Phase 5 — Tests, Verification & Staging (Central Maintenance System)

**Objective:** Own the full test matrix (including the R2 no-idle-starvation pin and the random-picker/re-arm/guard pins), map every P2/P3/P4 acceptance criterion to a check, and produce a staging-only release artifact per R7 — NO live promote, NO ceremony.

**Status:** PLANNED

---

## Scope

### In scope
- Test matrix: R2 two-legged pin (behavioral + structural), random-picker unit tests, re-arm independence, chain-break guard, seed lifecycle, no-idle structural guard, P2/P3/P4 acceptance verification, manual-trigger receipt provenance check.
- Fencing rules for every test that touches daemon code or boots a daemon (temp DBs, dev env fence, boot-sweep guard, no live-shaped HOME paths).
- Verification pass: cross-reference P2/P3/P4 acceptance criteria by sibling filename and map each to a concrete check.
- STAGING plan per R7: nominal `v0.31.27`, re-check at tag time, sibling-staging discipline, rebase onto staged `source_commit`, handoff note to leader. NO promote/ceremony.

### Out of scope
- Implementation of the trigger chain (P1), orchestrator upgrade (P2), lane bodies (P3), report/receipt/FE (P4) — cross-ref `phase1-plan.md (sibling)`, `phase2-plan.md (sibling)`, `phase3-plan.md (sibling)`, `phase4-plan.md (sibling)`.
- Live DB prompt adoption (operator step, flagged not executed), any live promote/ceremony, any daemon run against prod, any DB write to live-shaped paths, any git mutation beyond read-only status/log/diff --stat.

---

## Preconditions & dependencies

- **Upstream:** P1–P4 artifacts landed in the worktree branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. P5 consumes their acceptance criteria; it does not redefine them.
- **Downstream:** leader ceremony (handoff only); tester gate for the manual-trigger receipt (downstream of staging).
- **Parallelizable-with:** nothing — P5 is the terminal verification/staging gate; it runs after sibling phases are coherent.
- **Base rule:** all work under `/home/nea/ensemble-src-wt-maintsys`. NEVER from `/home/nea/ensemble-src` (detached canonical).
- **Upstream drift:** `origin/latest` moved to `86e196576` (engine-arc-p1 merge, 160 files, zero frontend changes) — includes deletion of `tests/unit/test_scheduler_deferred_delivery_pin.py`. Our base is 1 push behind ⇒ rebase onto the staged `source_commit` at tag time (R7); note the scheduler-test deletion interaction.

---

## Design decisions consumed

- **R2 (no idle gating):** the old daemon-internal tier's idle-only starvation (500 skips / 0 executions) is regression-PINNED here — the single most important test in this phase. Two-legged, mutation-proven style.
- **R7 (release):** nominal `v0.31.27`; RE-CHECK at tag time; if the sibling hbk lane staged beyond `v0.31.26` first, take the NEXT free slot and rebase onto the staged `source_commit`. NO live promote, NO ceremony — staging only, handoff note.
- **R8 (research against origin/latest):** this worktree is off `origin/latest`; canonical has zero hbk code.
- **D-D (manual trigger):** receipt `trigger_type="manual"` derived from row provenance (creator id + freshly-minted row); P4 owns the receipt/invoice shape — P5 verifies provenance, not the shape.
- **D-F / D-G:** knobs are env-first (P1 T1.2); re-arm seam is adapter-side post-dispatch (P1 T1.3) — P5 tests independence and no-idle on exactly those seams.

---

## Tasks

### Task 5.1 — R2 no-idle-starvation two-legged pin (MUTATION TARGET)
- **Description:** Implement the R2 pin in the style of `tests/unit/test_maintenance_idle_starvation.py` (MUTATION TARGET at `:194-208`, busy fixture `:78-85`, structural policy pin `:112-132`). **(i) BEHAVIORAL:** build a busy fixture (non-empty request registry, mirror `:83-84`) and a due one-shot `maintenancer` chain row; assert `manager.enqueue_message_job` (`daemon/manager.py:12046`) fired with `idempotency_key = f"scheduler:{source_id}:{run_at.isoformat()}"` (derivation `daemon/sources/adapters/scheduler.py:2728-2731`); assert NO idle/busy consult anywhere on the dispatch path (`_emit_scheduled_message :2834-2936` has no idle-gate concept); assert the only deferral — busy-CALLER for `reuse_instance` (`:2897-2912`) — is NOT reached for `new_instance` (one-shots force new_instance `:351-358`). **(ii) STRUCTURAL:** grep/AST pin that the chain trigger + dispatch + re-arm path never references `JOB_IDLE_POLICIES` / `_is_idle` (mirror the policy-table-derived classification pin style `:112-132`). Both legs must go RED on reintroducing an idle gate.
- **Touch points:** new `tests/unit/test_maintenance_no_idle_trigger.py` (or extend `tests/unit/test_maintenance_idle_starvation.py`).
- **Verification:** flip a hypothetical idle gate ⇒ leg (i) and leg (ii) both RED; green at final HEAD.
- **Agent hint:** `tester`.

### Task 5.2 — Random-picker unit tests
- **Description:** Unit-test `daemon/services/maintenance_schedule_knobs.py` (P1 T1.2): uniformity smoke (many draws over the window, all minutes reachable, roughly even), `[start,end)` inclusivity (start reachable, end excluded; 01:00–05:00 ⇒ 240 candidate minutes), weekday-boundary (next weekday D chosen; today-past-window ⇒ +7d re-anchor), ICT tz conversion (fixed UTC+7, no DST; naive local → ISO UTC via `daemon/util/tz.py:353-384` `anchor_local_to_utc`), invalid-window fail-closed (start ≥ end ⇒ no arm + loud warning). Assert no croniter call on the chain path.
- **Touch points:** new `tests/unit/test_maintenance_schedule_knobs.py`.
- **Verification:** all legs green; no DB/daemon dependency (pure helper).
- **Agent hint:** `tester`.

### Task 5.3 — Re-arm independence + chain persistence tests
- **Description:** Prove P1 T1.3: a run that fails still mints the next `run_at`; the new `run_at` yields a different idempotency key (`:2728-2731`); the row is NOT disabled by the one-shot completion callback (`daemon/sources/registry.py:608-620` chain-aware skip). Prove the rewritten `run_at` PERSISTS across adapter reconstruction — if the light `source_repo.update_source_config` write is insufficient, this test goes RED and P1 T1.3 must upgrade to the full `update_schedule` rebuild (`scheduling_service.py:1005`, `:574-606`). Distinguish weekly re-arm from the same-episode 5s `rearmed` outcome (`scheduler.py:245`).
- **Touch points:** new `tests/unit/test_maintenance_chain_rearm.py`.
- **Verification:** failed-run case still advances the chain; persistence check green.
- **Agent hint:** `tester`.

### Task 5.4 — Chain-break guard tests
- **Description:** Prove P1 T1.4: (a) missing row RECREATED (deterministic uuid5, mirror `tests/unit/repositories/test_tool_usage_seed_lifecycle.py:136-145`); (b) post-source-GC row recreated — the GC archive tables (`daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql:9,35-37`, GC lanes `daemon/manager.py:6870,6897,6962`) remove the live row; (c) cancelled/paused row RESPECTED (`daemon/tools/scheduling.py:128` `TaskScheduleUpdateInput.paused`; seeder NO-OP-in-any-state `seed.py:164-166`) — guard must not re-enable; (d) past `run_at` auto-fires at boot (default unlimited lateness `scheduler.py:924-956`), and under a set lateness cap (`SchedulingConfig.one_shot_max_lateness_seconds`, `:926-937`) a SKIPPED fire (`:938-955`) causes the guard to re-mint. Optional daily backstop job, if present, asserted `UN_GATED`.
- **Touch points:** new `tests/unit/test_maintenance_chain_guard.py`.
- **Verification:** all four legs green; guard never fights operator intent.
- **Agent hint:** `tester`.

### Task 5.5 — P2/P3/P4 acceptance verification map
- **Description:** Map every sibling acceptance criterion to a concrete check (see Verification Pass below). Reuse/extend existing pins where they exist: `tests/unit/test_maintenancer_skill_versions_consistent.py`, `test_maintenancer_team_members_within_team.py`, `test_maintenancer_report_scrutiny.py`, `test_maintenancer_fan_in_valve.py` (P2 named these); add the NEW P2 pin for report-ALWAYS-on-total-failure. Verify P3 per-lane result-block schema and three-class routing; verify P4 receipt/receipt-trigger provenance and FE surface (with the AOT gate reminder from the FE blueprint — `ng build --configuration development` after template-touching commits, since `tsc --noEmit`/jest TestBed do not catch NG8002/NG0303).
- **Touch points:** new `tests/unit/test_maintenance_acceptance_map.py` + extensions to the four maintenancer pin files; a verification report under `.agents/shared/planning/maintenance-system/` (artifact, plan-only).
- **Verification:** each AC in the map has a named test/pin and a green result at final HEAD.
- **Agent hint:** `tester`.

### Task 5.6 — Manual-trigger receipt provenance check
- **Description:** Per D-D, verify the canonical manual path: Ari (holds `scheduling` grant `agents/ari/meta.json:34`) creates a `task_schedule` ONE-SHOT at `run_at=now`, `agent_id="maintenancer"`; assert the row's provenance (creator_instance_id = Ari, freshly-minted row) and the per-fire audit (`schedule_executions` rows + `schedule_fire_notify` SYSTEM notice — `daemon/services/schedule_fire_notify.py:181`, identity gate `:92-103`). Assert receipt `trigger_type="manual"` is DERIVED from row provenance (P4 owns the shape — cross-ref `phase4-plan.md (sibling)`; do not redefine it). Document the two documented fallbacks and their constraints: `POST /api/schedules/{id}/trigger` (`daemon/routers/schedules.py:476-567`, requires RUNNING adapter, 409 on cancelled `:519-526`, execution recorded by callback `:551-552`) and direct `send_message` (weakest provenance).
- **Touch points:** new `tests/unit/test_maintenance_manual_trigger_provenance.py`; staging note section.
- **Verification:** provenance-derived `trigger_type` green; fallback constraints documented.
- **Agent hint:** `tester`.

### Task 5.7 — Staging plan (R7, staging-only) + handoff note
- **Description:** Produce a staging artifact for the branch: nominal version `v0.31.27`; RE-CHECK tags at tag time (verified top tag `v0.31.26`; `v0.31.14` is a known hole); if the sibling hbk lane staged beyond `v0.31.26` first, take the NEXT free slot and rebase onto the staged `source_commit`. Discipline: rebase onto staged `source_commit` (R7), note the `86e196576` scheduler-test deletion interaction. NO live promote, NO ceremony from this lane — emit a handoff note to leader with the exact promote command left to the ceremony owner. Flag live-DB prompt adoption as a post-promote operator step (R3/P2). Record the manual-trigger receipt proof from Task 5.6 in the staging note for the downstream tester gate.
- **Touch points:** staging note under `.agents/shared/planning/maintenance-system/` (artifact); no release-dir/tag mutation by this lane.
- **Verification:** handoff note complete (version, source_commit, rebase basis, no-promote statement, operator-step flag).
- **Agent hint:** `devops` / `leader` handoff.

---

## Verification pass — P2/P3/P4 acceptance → check map

| Source AC (sibling) | Concrete check | Owning task |
|---|---|---|
| P2: `maintenancer/meta.json` has `instance`/`subtree_messages`/`subtree_status` in allow, `deny` unchanged, JSON valid | JSON parse + set assertions on `agents/maintenancer/meta.json` | 5.5 |
| P2: 6 lane skills + 6 matching `skill-set.yaml` rows, version-consistency green | `test_maintenancer_skill_versions_consistent.py` (set equality + version match, extended to 6) | 5.5 |
| P2: rule/workflow encode 5-lane + report-writer dispatch, per-lane status, report-ALWAYS incl. total failure, seat non-mutating, no idle gating, load_skill spawn contract, path-passing | Workflow/rule string pins + NEW report-ALWAYS-on-total-failure pin | 5.5 |
| P2: post-promote DB prompt adoption flagged as operator step | Staging note contains the flag (R3) | 5.7 |
| P3: each lane returns the per-lane result block (status ok/degraded/failed, metrics, 3-class recommendations, evidence paths) | Schema validation test against P3's block definition (`phase3-plan.md (sibling)` §Common weekly procedure) | 5.5 |
| P3: disk-lane fence recipe (baseline, protected set, open-by-process, bytes+inodes) | Fence-recipe presence pin vs `disk-lane-baseline-2026-11-18.md` | 5.5 |
| P3: old tier SEPARATE and LIVE — Lane 4 only WATCHES, never writes `maintenance_runs` | No-write pin on daemon bookkeeping tables | 5.5 |
| P4: report + receipt + INDEX authored with run_id convention; receipt `trigger_type` shape | Receipt schema check (cross-ref `phase4-plan.md (sibling)`) | 5.5 / 5.6 |
| P4: manual trigger via one-shot-at-now → maintenancer; provenance-derived `trigger_type="manual"` | Task 5.6 provenance test | 5.6 |
| P4: FE surface attached per FE conventions | FE check + `ng build --configuration development` AOT gate reminder | 5.5 |
| P1: no-idle, re-arm independence, guard, picker, seed lifecycle | Tasks 5.1–5.4 | 5.1–5.4 |

---

## Test substrate & fencing rules (MANDATORY)

- **Mock-manager REST fixture:** `tests/test_scheduler_api.py:22-129` — temp SQLite `source_configs` / `instance_mappings` / `schedule_executions` (`:49-87`), Mock repo (`:93-103`), `scheduling_service` defaulted to None-returning AsyncMocks (`:114-117`). FENCE every new REST test like this — temp DBs only.
- **E2E mirrors (integration-marked, deselected by default):** `tests/integration/test_scheduled_tasks_e2e.py` — `pytestmark` `:56`, `test_one_shot_schedule_creates_job_item :163`, `test_cancelled_schedule_never_dispatches :257`, `test_one_shot_beyond_cap_no_dispatch_with_reason :418`.
- **Seed lifecycle mirror:** `tests/unit/repositories/test_tool_usage_seed_lifecycle.py:136-145`.
- **No live-shaped HOME paths:** never resolve install dirs from `$HOME` (incident history: two live-install deletions 2026-10-12); use `mktemp` FAKE_HOME + escape guard.
- **Dev env fence for any daemon-booting test:** `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079` (per the worktree claim), mode 600. Scrub per-variable (`env -u POSTGRES_DB -u POSTGRES_USER ...`) — `env -u POSTGRES_*` is a silent NO-OP (bash does not glob env names).
- **Boot-sweep guard:** any frozen-binary boot smoke from a daemon-child shell MUST pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly (`env -u` on it strips the ambient guard and the sweep KILLS live processes).
- **TypeScript:** only via `frontend/node_modules/.bin/tsc`; never bare `npx tsc` from repo root (remote fetch incident 2026-10-03).
- **Read-only git:** status/log/diff --stat only; no git mutations by this lane.

---

## Risks & mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R-P5-1 | R2 pin written as bookkeeping-only (not behavioral) ⇒ idle gate reintroduced undetected | high | Behavioral leg FIRST (mirror `:204-208`), structural leg second; both mutation-proven |
| R-P5-2 | Test resolves live-shaped HOME / prod DB | high | Temp DBs + FAKE_HOME fence + per-var env scrub + dev fence |
| R-P5-3 | Re-arm persistence test passes on light write but fails at boot | high | Explicit boot-reconstruction assertion; upgrade P1 T1.3 to rebuild if RED |
| R-P5-4 | `origin/latest 86e196576` deleted a scheduler test our pin depends on | medium | Rebase onto staged `source_commit`; grep deleted-test references before final HEAD |
| R-P5-5 | Version slot collision with sibling hbk lane | medium | RE-CHECK tags at tag time; take next free slot; rebase onto staged source_commit |
| R-P5-6 | FE surface check passes `tsc`/jest but fails AOT | medium | `ng build --configuration development` gate after template-touching commits |
| R-P5-7 | Manual-trigger receipt derived from wrong provenance | low | Assert creator id + freshly-minted row; P4 owns shape, P5 verifies derivation |

---

## Acceptance criteria

- AC1: R2 two-legged pin exists, both legs mutation-proven RED on reintroduced idle gate, GREEN at final HEAD.
- AC2: Picker, re-arm independence (+ persistence), guard (4 legs), seed lifecycle tests all green with fencing rules honored.
- AC3: Every P2/P3/P4 AC maps to a named, green check; the four maintenancer pins stay green + new report-ALWAYS pin added.
- AC4: Manual-trigger provenance test green; receipt `trigger_type="manual"` derivation verified; fallback constraints documented.
- AC5: Staging artifact produced at the re-checked next-free version, rebased onto staged `source_commit`, with explicit NO-promote/NO-ceremony handoff note and operator-step flag.
- AC6: No test touches live-shaped paths or prod DB; all boot-smoke fences pinned.

---

## Verification plan (this phase IS the verification)

- Run the full new unit pack under the dev fence; confirm zero net-new deterministic failures.
- Confirm both R2 legs RED under an injected idle gate (mutation proof) and GREEN at final HEAD.
- Confirm the acceptance map has a green check per row.
- Confirm staging note completeness (version, source_commit, rebase basis, no-promote, operator step, receipt proof).
- Downstream: tester gate consumes the staging note for the manual-trigger receipt.

---

## Open questions

- **Version slot:** nominal `v0.31.27` — leader must confirm at tag time against the sibling hbk lane's staged version (R7). No blocker to planning.
- **Re-arm persistence:** if Task 5.3 goes RED on the light write, P1 T1.3 must be upgraded to the full `update_schedule` rebuild — a cross-phase change needing leader awareness (flagged in P1 open questions too).
- **Daily backstop job:** if P1 includes the optional UN_GATED `MaintenanceService` job, add its `UN_GATED` assertion to Task 5.4; confirm with P1 implementer.
- **Live version re-verification:** release state not derivable in-repo (caller says v0.31.25, adjacent history suggests v0.31.24); plan says RE-VERIFY at promote — ceremony owner's call, out of this lane.
