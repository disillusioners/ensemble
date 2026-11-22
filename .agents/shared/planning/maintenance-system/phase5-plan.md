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
- Live DB prompt adoption (operator step, flagged not executed), any live promote/ceremony, any daemon run against prod, any DB write to live-shaped paths, and any git mutation of PROTECTED refs or surfaces: no pushes, no merges, no tags, no release-dir changes, no worktree mutations. **In-branch, pathspec-limited commits of THIS phase's own deliverables (new test files, pin extensions, planning artifacts) are ALLOWED and expected** — the P1–P4 convention; the leader merge needs a clean worktree.

---

## Preconditions & dependencies

- **Upstream:** P1–P4 artifacts landed in the worktree branch `feature/central-maintenance-system`, base `origin/latest @ 1b9797ab7`. P5 consumes their acceptance criteria; it does not redefine them.
- **Downstream:** leader ceremony (handoff only); tester gate for the manual-trigger receipt (downstream of staging).
- **Parallelizable-with:** nothing — P5 is the terminal verification/staging gate; it runs after sibling phases are coherent.
- **Base rule:** all work under `/home/nea/ensemble-src-wt-maintsys`. NEVER from `/home/nea/ensemble-src` (detached canonical).
- **Upstream drift:** `origin/latest` moved to `86e196576` (engine-arc-p1 merge, 160 files, zero frontend changes), and on to `8894f86a` at iteration-001 time — `tests/unit/test_scheduler_deferred_delivery_pin.py` SURVIVES both (the earlier "deletion" note was false — one line changed, file present); its pin guards exactly the surface T1.3 rewrites ⇒ unconditional keep-green. Our base is pushes behind ⇒ rebase onto the staged `source_commit` at tag time (R7/BR-20); note the scheduler-pin keep-green interaction (SURVIVES upstream — see the corrected bullet above).

---

## Design decisions consumed

- **R2 (no idle gating):** the old daemon-internal tier's idle-only starvation (500 skips / 0 executions) is regression-PINNED here — the single most important test in this phase. Two-legged, mutation-proven style.
- **R7 (release):** nominal `v0.31.27`; RE-CHECK at tag time; if the sibling hbk lane staged beyond `v0.31.26` first, take the NEXT free slot and rebase onto the staged `source_commit`. NO live promote, NO ceremony — staging only, handoff note.
- **R8 (research against origin/latest):** this worktree is off `origin/latest`; canonical has zero hbk code.
- **D-D (manual trigger):** receipt `trigger_type="manual"` via the **message-body token round-trip** (manual invocation message embeds `trigger: manual`; orchestrator parses → writer envelope; spec owner P4 T4.6) — row provenance (creator id + fresh row) survives as a marked AUDIT SUB-CHECK only; P4 owns the receipt/invoice shape — P5 verifies the token round-trip, not the shape.
- **D-F / D-G:** knobs are env-first (P1 T1.2); re-arm seam is adapter-side at the one-shot CONSUMPTION branch (P1 T1.3, BR-4) — P5 tests independence, config completeness and no-idle on exactly those seams.
- **Architect validation addendum (binding, folded here):** `architecture-recommendation.md` §7 — **BR-1** (guard re-mint predicate), **BR-2** (at-least-once semantics), **BR-3** (archive-aware recreate), **BR-4** (re-arm write contract), **BR-5** (tz honesty), **BR-6** (canonical no-idle predicate), **BR-7** (census/pin hygiene), **BR-11** (event-driven deadline pin), **BR-14** (run_id authority/interpreter/run_token pin), **BR-15** (reportless-week + orphan-listing pin), **BR-20** (staging re-check discipline). Their operative text is reproduced at each landing site — T5.1 (BR-6), T5.2 (BR-5), T5.3 (BR-4, BR-2), T5.4 (BR-1, BR-3), T5.5 (BR-11, BR-14, BR-15), T5.7 (BR-20), the Test-substrate section (BR-7), the acceptance map (BR-8 row fix), and AC1–AC7. This phase doc is now the single source of truth for those requirements; the addendum remains the verdict record.

---

## Tasks

### Task 5.1 — R2 no-idle-starvation two-legged pin (MUTATION TARGET)
- **Description:** Implement the R2 pin in the style of `tests/unit/test_maintenance_idle_starvation.py` (MUTATION TARGET at `:194-208`, busy fixture `:78-85`, structural policy pin `:112-132`). **(i) BEHAVIORAL:** build a busy fixture (non-empty request registry, mirror `:83-84`) and a due one-shot `maintenancer` chain row; assert `manager.enqueue_message_job` (`daemon/manager.py:12046`) fired with `idempotency_key = f"scheduler:{source_id}:{run_at.isoformat()}"` (derivation `daemon/sources/adapters/scheduler.py:2728-2731`); assert NO idle/busy consult anywhere on the dispatch path (`_emit_scheduled_message :2834-2936` has no idle-gate concept); assert the only deferral — busy-CALLER for `reuse_instance` (`:2897-2912`) — is NOT reached for `new_instance` (one-shots force new_instance `:351-358`). **(ii) STRUCTURAL:** **AST** pin (docstrings/comments EXCLUDED — the no-idle attestation text is EMBEDDED in the new files' docstrings, so a raw grep false-REDs; real-AST precedent `tests/unit/test_defer_busy_witness_caller_pin.py:128-140` — `ast.parse` + `ast.walk`; do NOT copy text-scan precedents, BR-6 forbids grep) asserting no reference resolving to `JOB_IDLE_POLICIES` / `IdlePolicy` / `_is_idle` over EXACTLY the FOUR files below. Both legs must go RED on ONE mutation (idle-gate insertion in the chain branch).
- **BR-6** — no-idle pin (operative text):
  > Structural leg = AST (docstrings/comments excluded) over exactly `daemon/sources/adapters/scheduler.py`, `daemon/services/maintenance_schedule_knobs.py`, `daemon/repositories/source/maintenance_seed.py`, `daemon/manager.py`; assert no reference resolving to `JOB_IDLE_POLICIES`/`IdlePolicy`/`_is_idle`. Behavioral leg per T5.1. One mutation (idle-gate insertion in the chain branch) must RED both legs. ONE canonical predicate string — P1 T1.5 is the single source; AC5 and P5 T5.1 cite it VERBATIM (predicate-ledger convention; the three paraphrases were consolidated 2026-11-22 — one canonical string since).

  The canonical predicate string (cited VERBATIM from P1 T1.5 / P1 AC5 — the single source of truth):

  ```
  no reference resolving to JOB_IDLE_POLICIES, IdlePolicy, or _is_idle in daemon/sources/adapters/scheduler.py, daemon/services/maintenance_schedule_knobs.py, daemon/repositories/source/maintenance_seed.py, or daemon/manager.py
  ```

  - **AST file set (exactly four, no more):** `daemon/sources/adapters/scheduler.py` (whole — green today), `daemon/services/maintenance_schedule_knobs.py` (new), `daemon/repositories/source/maintenance_seed.py` (new), `daemon/manager.py` (whole — green today; whole-file so a gate added at the hook site cannot escape). A grep over "the chain path" is FALSE ASSURANCE and is not acceptable here.
- **Touch points:** new `tests/unit/test_maintenance_no_idle_trigger.py` (both legs live here; do NOT extend `test_maintenance_idle_starvation.py` — that file is the old-tier substrate and stays untouched except per BR-7).
- **Verification:** ONE mutation (insert an idle-gate in the chain branch) ⇒ leg (i) AND leg (ii) both RED; green at final HEAD.
- **Agent hint:** `tester`.

### Task 5.2 — Random-picker unit tests
- **Description:** Unit-test `daemon/services/maintenance_schedule_knobs.py` (P1 T1.2): uniformity smoke — **bucket-count with a fixed tolerance** (or a deterministic seed), NOT a loose "roughly even" assertion (flake-prone; advisory A-drift); `[start,end)` inclusivity (start reachable, end excluded; 01:00–05:00 ⇒ 240 candidate minutes); weekday-boundary (next weekday D chosen; today-past-window ⇒ +7d re-anchor); ICT tz conversion (fixed UTC+7, no DST; naive local → ISO UTC via `daemon/util/tz.py:353-384` `anchor_local_to_utc`); invalid-window fail-closed (start ≥ end ⇒ no arm + loud warning). Assert no croniter call on the chain path.
- **BR-5** — DST-zone leg (operative text):
  > The picker MUST consume `anchor_local_to_utc`'s warning (loud, never silent) and fail-closed if the anchored instant leaves `[start,end)`; P5 T5.2 adds a DST-zone leg (America/New_York gap day + fold day; deterministic instant inside the window).
  - **Leg detail:** with `ENSEMBLE_MAINT_TIMEZONE=America/New_York`, exercise a DST **gap** day (spring-forward) and a DST **fold** day (fall-back): the anchored instant must be deterministic, must fall INSIDE `[start,end)`, and the non-empty `anchor_local_to_utc` warning must be surfaced (asserted, never swallowed). Add a fail-closed leg: a gap-shift that lands outside the window ⇒ NO arm + loud warning.
- **Touch points:** new `tests/unit/test_maintenance_schedule_knobs.py`.
- **Verification:** all legs green (incl. the DST-zone gap/fold legs); no DB/daemon dependency (pure helper).
- **Agent hint:** `tester`.

### Task 5.3 — Re-arm independence + chain persistence tests
- **Description:** Prove P1 T1.3: a run that fails still mints the next `run_at`; the new `run_at` yields a different idempotency key (`:2728-2731`); the row is NOT disabled by the one-shot completion callback (`daemon/sources/registry.py:608-620` chain-aware skip). Prove the rewritten `run_at` PERSISTS across adapter reconstruction — if the light `source_repo.update_source_config` write is insufficient, this test goes RED and P1 T1.3 must upgrade to the full `update_schedule` rebuild (`scheduling_service.py:1005`, `:574-606`). Distinguish weekly re-arm from the same-episode 5s `rearmed` outcome (`scheduler.py:245`).
- **BR-4** — config-completeness leg (operative text):
  > Persistence assertion in P5 T5.3.
  - **Leg detail:** after the re-arm write, the **COMPLETE config survives** — `agent`, `message`, `timezone`, `recurrence` (plus `instance_mode`, `project_id`, and the `chain` block) are all still present and unchanged apart from `run_at`. This is a direct regression pin on `update_source_config` FULL-REPLACING the config column (`daemon/repositories/source/repository.py:178-201` — *"always written as a full replace … there is no partial key-level merge at this layer"*): a partial write would silently strip `agent`/`message` and the chain would die on the next boot.
- **BR-2** — at-least-once leg (operative text):
  > Test leg: enqueue with expired key ⇒ 2nd JobItem (P5 T5.3). Do NOT widen the substrate key TTL (global blast radius; widening would make the >24h boot re-dispatch collapse into the consumed JobItem = a missed week — worse).
  - **Leg detail:** enqueue with an EXPIRED idempotency key (past the 24h TTL, `daemon/services/job_queue_service.py:276`) ⇒ a **SECOND JobItem** is created — two runs are DOCUMENTED, not widened away. The test asserts the second JobItem exists AND that the substrate TTL was NOT changed by the implementation.
- **Touch points:** new `tests/unit/test_maintenance_chain_rearm.py`.
- **Verification:** failed-run case still advances the chain; persistence + config-completeness check green; expired-key leg green.
- **Agent hint:** `tester`.

### Task 5.4 — Chain-break guard tests
- **Description:** Prove P1 T1.4 with the EXACT BR-1 predicate and the EXACT BR-3 archive-aware semantics.
- **BR-1** — guard re-mint predicate legs (operative text):
  > Test legs: boot with cap unset ⇒ `run_at` untouched; cap exceeded ⇒ re-minted (P5 T5.4).
  - **Leg (a) cap unset:** boot the guard with `SchedulingConfig.one_shot_max_lateness_seconds` unset (default `None` = unlimited, `daemon/config.py:3367-3373`) against a row whose `run_at` is in the past ⇒ the guard LEAVES `run_at` UNTOUCHED (the adapter's past-due auto-fire, `scheduler.py:924-956`, must deliver the late run). Asserting a re-mint here is FORBIDDEN — it would pre-empt the auto-fire and silently skip the missed week.
  - **Leg (b) cap exceeded:** boot the guard with a cap configured and `now - run_at > cap` ⇒ the guard RE-MINTS the next `run_at` (mirroring the adapter's own cap comparison at `scheduler.py:926-937`).
- **BR-3** — archive-aware recreate legs (operative text):
  > Test legs: cancelled-archived not resurrected; stopped+chain revived (P5 T5.4).
  - **Leg (c) cancelled-archived NOT resurrected:** an operator-cancelled chain (`enabled=False, status='cancelled'` — written atomically at `daemon/repositories/source/repository.py:604-607`) that source-GC has ARCHIVED (archive tables `daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql:80-93`, source_id PK at `:81`; archive model `daemon/repositories/source/models.py:221-251`; GC predicate `daemon/repositories/source/repository.py:728-772`; service `daemon/services/sources_gc_service.py`; wiring `daemon/api.py:2192-2205`) leaves the live row ABSENT ⇒ the guard MUST NOT recreate it. **This leg REPLACES the earlier "post-source-GC row recreated" leg, which wrongly enshrined resurrection of a cancelled row as a PASSING outcome** — a correct BR-3 implementation would fail that leg.
  - **Leg (d) stopped+chain revived:** an archived row whose `status='stopped'` and whose archived config snapshot CARRIES a `chain` block ⇒ the guard revives it (same keying as the re-arm seam — one concept, one marker).
  - **Leg (e) no archive row ⇒ create:** a missing row with no archive record ⇒ created, exactly as the seed lifecycle mirror prescribes (deterministic uuid5, `tests/unit/repositories/test_tool_usage_seed_lifecycle.py:136-145`).
- **Also still proven (unchanged):** a present cancelled/paused row is RESPECTED (`daemon/tools/scheduling.py:128` `TaskScheduleUpdateInput.paused`; seeder NO-OP-in-any-state `seed.py:164-166`) — the guard must not re-enable it; and the archive re-archive path never collides the archive PK (`repository.py:83-84` `archive_id_collision`).
- **Touch points:** new `tests/unit/test_maintenance_chain_guard.py`.
- **Verification:** legs (a)–(e) green; the guard never fights operator intent and never resurrects a cancelled chain.
- **Agent hint:** `tester`.

### Task 5.5 — P2/P3/P4 acceptance verification map
- **Description:** Map every sibling acceptance criterion to a concrete check (see Verification Pass below). Reuse/extend existing pins where they exist: `tests/unit/test_maintenancer_skill_versions_consistent.py`, `test_maintenancer_team_members_within_team.py`, `test_maintenancer_report_scrutiny.py`, `test_maintenancer_fan_in_valve.py` (P2 named these); add the NEW P2 pin for report-ALWAYS-on-total-failure. Verify P3 per-lane result-block schema and three-class routing; verify P4 receipt/receipt-trigger provenance and FE surface (with the AOT gate reminder from the FE blueprint — `ng build --configuration development` after template-touching commits, since `tsc --noEmit`/jest TestBed do not catch NG8002/NG0303). The three legs below carry their own BR labels and are part of this task's pin set.
- **BR-11** — event-driven lane deadline pin (operative text):
  > Event-driven lane deadline — a lane is `failed-timeout` when a parent wake carries `source=system:watchdog` or `source=system:long-tool-nudge` naming that child; apply the valve once, else `[incomplete]`, then proceed. Deadline covers the initial dispatch AND its single re-dispatch. Report-writer fires on lane resolution OR global deadline (same wake mechanism bounds the writer wait ≈2h). NO polling.
  - **Leg detail:** assert the deadline covers the INITIAL dispatch AND its single re-dispatch (a lane wedged on either one is `failed-timeout`); assert the `failed-timeout` determination is driven by a parent wake whose `source` is `system:watchdog` (`daemon/services/waiting_children_watchdog.py:179` `WATCHDOG_SOURCE`, notice at `:286`) or `system:long-tool-nudge` (`daemon/services/long_tool_nudge.py:347` `LONG_TOOL_NUDGE_SOURCE`, notice at `:1151`) naming THAT child; assert the valve is applied exactly ONCE (then `[incomplete]`); assert **NO polling anywhere** (no sleep/poll loop in the orchestrator path — the house rule "I never poll").
- **BR-14** — run_id authority + interpreter + run_token pin (operative text):
  > run_id single authority = the report-writer child. Strike the seat-mint step in P2 T2.4 (the seat has no bash/filesystem). Name the daemon venv interpreter (`/home/nea/ensemble-src/.venv/bin/python` — bare `python3` fails on this host). Bash fallback `ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)` is a BINDING rung in the skill body. Carry a `run_token` in the dispatch task; re-dispatches reuse it (INDEX dedup survives re-mints).
  - **Leg detail (P5 side, replaces the import-purity check):** the run_id mint test MUST invoke the **named daemon venv interpreter** `/home/nea/ensemble-src/.venv/bin/python` — bare `python3` FAILS on this host (`ModuleNotFoundError: sqlalchemy`) and a test that runs `python3` would false-GREEN/RED for the wrong reason; assert the **bash fallback binding** (`ckpt-$(date -u +%Y%m%d_%H%M%S%6N)-$(openssl rand -hex 4)`) is present as a BINDING rung in the `run-report-writer` skill body; assert the **run_token reuse** — a re-dispatch reuses the SAME identity, and the INDEX dedup survives re-mints (a re-minted run_id would defeat it).
- **BR-15** — orchestrator-death backstop pin (operative text):
  > Orchestrator-death backstop (surface-only): the boot guard additionally checks "most recent fire produced a `run-*.md`?" ⇒ report_anomaly/board notice (never author, never self-heal, never re-fire). Lane 4 lists non-terminal instances with a maintenancer parent (existing `instance_read`).
  - **Leg detail (P5 side):** assert the boot guard's reportless-week DETECTOR — "did the most recent fire produce a `run-*.md`?" — fires a `report_anomaly` / board notice when the answer is NO, and that it NEVER authors an artifact, NEVER self-heals, and NEVER re-fires the schedule. Assert the lane-4 ORPHAN LISTING check: non-terminal instances whose parent is a `maintenancer` are listed (via the existing `instance_read` surface — no new grant).
- **Touch points:** new `tests/unit/test_maintenance_acceptance_map.py` + extensions to the THREE OTHER maintenancer pin files (the versions-consistency count-pin edit is solely owned by P2 T2.7 — P5 keeps it green, never edits it); a verification report under `.agents/shared/planning/maintenance-system/` (artifact, plan-only).
- **Verification:** each AC in the map has a named test/pin and a green result at final HEAD; the BR-11 / BR-14 / BR-15 legs above are green.
- **Agent hint:** `tester`.

### Task 5.6 — Manual-trigger receipt provenance check
- **Description:** Per D-D, verify the canonical manual path: Ari (holds `scheduling` grant `agents/ari/meta.json:34`) creates a `task_schedule` ONE-SHOT at `run_at=now`, `agent_id="maintenancer"`, message embedding `trigger: manual`; assert the **token round-trip** (token in the dispatch message → orchestrator parse → `trigger_type` in the writer envelope → receipt header `trigger_type="manual"`; the writer never reads the DB — spec owner P4 T4.6, do not redefine). **AUDIT SUB-CHECK (secondary, explicitly marked — NOT the runtime path):** the row's provenance (creator_instance_id = Ari, freshly-minted row) and per-fire audit (`schedule_executions` rows + `schedule_fire_notify` SYSTEM notice — `daemon/services/schedule_fire_notify.py:181`, identity gate `:92-103`) corroborate the transport. Document the two documented fallbacks and their constraints: `POST /api/schedules/{id}/trigger` (`daemon/routers/schedules.py:476-567`, requires RUNNING adapter, 409 on cancelled `:519-526`, execution recorded by callback `:551-552`) and direct `send_message` (weakest provenance).
- **Touch points:** new `tests/unit/test_maintenance_manual_trigger_provenance.py`; staging note section.
- **Verification:** token round-trip green (message token → envelope → receipt header); audit sub-check green; fallback constraints documented.
- **Agent hint:** `tester`.

### Task 5.7 — Staging plan (R7, staging-only) + handoff note
- **Description:** Produce a staging artifact for the branch: nominal version `v0.31.27`; RE-CHECK tags at tag time (verified top tag `v0.31.26`; `v0.31.14` is a known hole); if the sibling hbk lane staged beyond `v0.31.26` first, take the NEXT free slot and rebase onto the staged `source_commit`. Discipline: rebase onto staged `source_commit` (R7), note the `86e196576` scheduler-pin keep-green interaction (the deferred-delivery pin SURVIVES upstream — corrected 2026-11-22). NO live promote, NO ceremony from this lane — emit a handoff note to leader with the exact promote command left to the ceremony owner. Flag live-DB prompt adoption as a post-promote operator step (R3/P2). Record the manual-trigger receipt proof from Task 5.6 in the staging note for the downstream tester gate.
- **BR-20** — staging re-check discipline (operative text; content already carried above, the token makes the per-BR map grep-complete):
  > At tag time (binding, per R7): re-check the free slot (`v0.31.27` free at addendum time; engine-lane release-B may take it ⇒ next free slot + rebase onto the staged `source_commit`); drift-merge/rebase onto `origin/latest` (was `86e196576` at addendum time, `8894f86a` at iteration-001 — daemon-only; `test_scheduler_deferred_delivery_pin.py` SURVIVES both, earlier "deletes" claim corrected 2026-11-22 ⇒ obligation = unconditional keep-green, reconcile the BR-7 list at that moment); FE stacking proven clean (zero FE delta base→HEAD and base→upstream).
- **Touch points:** staging note under `.agents/shared/planning/maintenance-system/` (artifact); no release-dir/tag mutation by this lane.
- **Verification:** handoff note complete (version, source_commit, rebase basis, no-promote statement, operator-step flag).
- **Agent hint:** `devops` / `leader` handoff.

---

## Verification pass — P2/P3/P4 acceptance → check map

| Source AC (sibling) | Concrete check | Owning task |
|---|---|---|
| P2 (BR-8): `maintenancer/meta.json` allow contains the FOUR LITERAL tool names `spawn_instance`, `send_message`, `subtree_messages`, `subtree_status` (NOT the whole `"instance"` category), and `deny` is byte-unchanged; JSON valid | JSON parse + exact-literal set assertion on `agents/maintenancer/meta.json`; deny byte-equality vs `{git_commit, edit_file, write_file}`; resolved-surface assertion via `resolve_tool_filter` (`daemon/tools/instance.py:455`) EXCLUDES `terminate_instance`, `pause_instance`, `resume_instance`, `set_instance_tunable`, `convene_council*`, `spawn_councilor`; effective spawnable set is 4 (kb-writer implied by `knowledge` via `TOOL_REQUIRED_AGENTS`) | 5.5 |
| P2: 6 lane skills + 6 matching `skill-set.yaml` rows, version-consistency green | `test_maintenancer_skill_versions_consistent.py` (set equality + version match, extended to 6) | 5.5 |
| P2: rule/workflow encode 5-lane + report-writer dispatch, per-lane status, report-ALWAYS incl. total failure, seat non-mutating, no idle gating, load_skill spawn contract, path-passing | Workflow/rule string pins + NEW report-ALWAYS-on-total-failure pin | 5.5 |
| P2: post-promote DB prompt adoption flagged as operator step | Staging note contains the flag (R3) | 5.7 |
| P2 (BR-9): Cardinal-tier run-containment rule in `rule.md` + `workflow.md` mirror — touch ONLY children of THIS run; never message/pause/resume/terminate instances not spawned this run; wedged-child kill via audited `instance_terminate` only; never pass `join_room`/`member_name` at spawn | Rule/workflow string pins for the containment clauses + the seat-grant cross-check (BR-10) | 5.5 |
| P2 (BR-12): lane-status mapping table incl. never-dispatched ⇒ `failed` + `[incomplete]` (P2 T2.4 authoritative) | Workflow string pin on the mapping table | 5.5 |
| P4: receipt `trigger_type` TRANSPORTED (message token → orchestrator parse → writer envelope; writer never reads DB) + INDEX chronological/dedup/append-via-`write_file(append=True)` (BR-16b) | Token round-trip check in T5.6 + INDEX mechanics pin | 5.5 / 5.6 |
| P2 (BR-11): lane deadline is event-driven — `failed-timeout` on a parent wake carrying `source=system:watchdog` or `source=system:long-tool-nudge` naming the child; deadline covers the initial dispatch AND its single re-dispatch; NO polling | BR-11 leg in T5.5 | 5.5 |
| P4 (BR-14): run_id minted ONLY by the report-writer child; named daemon venv interpreter; bash fallback binding; `run_token` reuse survives re-mints | BR-14 leg in T5.5 | 5.5 |
| P1/P3 (BR-15): reportless-week detector on the boot guard (P1 T1.4 landing) + lane-4 orphan listing (P3 Lane 4 landing) | BR-15 leg in T5.5 | 5.5 |
| P3: each lane returns the PD-4a canonical result block (decisions.md PD-4/PD-4a — incl. measured_at + actions_taken) | Schema validation test against PD-4a (the frozen canonical block — NOT a per-phase copy) | 5.5 |
| P1 AC1 (row shape: name/agent/recurrence/instance_mode/chain block; idempotent seed) + AC6 (failure-mode table completeness + BR-2 at-least-once semantics stated; TTL not widened) | Seed lifecycle test (T5.4 leg) + doc-presence check (failure-mode rows carry named mitigation + P5 anchor) | 5.4 / 5.5 |
| P3: disk-lane fence recipe (baseline, protected set, open-by-process, bytes+inodes) | Fence-recipe presence pin vs `disk-lane-baseline-2026-11-18.md` | 5.5 |
| P3: old tier SEPARATE and LIVE — Lane 4 only WATCHES, never writes `maintenance_runs` | No-write pin on daemon bookkeeping tables | 5.5 |
| P4: report + receipt + INDEX authored with run_id convention; receipt `trigger_type` shape | Receipt schema check (cross-ref `phase4-plan.md (sibling)`) | 5.5 / 5.6 |
| P3 lane-2 (POK): skill-body prohibition strings present — `pok_upsert`/`pok_curate` ABSENT from `lane-pok-sweep.md`, `pok_query` permitted | Prohibition-string pin over the skill body (enforcement exists and is mapped — phase3 lane 2 fence) | 5.5 |
| P4: manual trigger via one-shot-at-now → maintenancer; `trigger_type="manual"` via the token round-trip (row provenance = marked audit sub-check) | Task 5.6 token round-trip test (+ audit sub-check) | 5.6 |
| P4: FE surface attached per FE conventions | FE check + `ng build --configuration development` AOT gate reminder | 5.5 |
| P2 (BR-13): report-writer ladder pre-decided L0→L3 (L0 dispatch, write order run→receipt→INDEX last; L1 one minimal-stub writer; L2 one bare worker child skeleton all-failed; L3 accepted surfaced reportless terminal per DP-1) | Ladder-encoding string pins in `rule.md`/`workflow.md`/`run-report-writer` skill + L3-residual surfaced via BR-15 detector | 5.5 |
| P4 (BR-16): artifact root = live-install daemon project root `.agents/maintenancer/RESULTS/`, created by P1's seeder at first boot; never a worktree | Fresh-boot dir-exists check; root identical across seeder, writer skill, FE list route, live_views registration; no-worktree-path pin | 5.5 |
| P4 (BR-16b): INDEX appends via `write_file(append=True)` only (in-process lock + tail read-back), never `bash >>`; rows carry run_id | Skill-body string pin (no `bash >>`/`tee -a` against INDEX) + append-mechanism check | 5.5 |
| P4 (BR-17): BE list route — fixed root at startup, run_id regex `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$` filename-only, realpath containment, GET(+HEAD)-only, `maintenance:read` scope, registered in `daemon/api.py` beside `tool_usage_router` | Route spec pins: verb pin (`.not.toMatch(/\.(post|put|delete|patch)\(/)` posture), scope assertion, regex+containment unit checks, registration-site assertion | 5.5 |
| P4 (BR-18): live_views root `maintenance-reports` — daemon-injected lifespan (`inject_maintenance_results_live_view_root` mirroring `inject_reports_live_view_root`, `daemon/api.py:434/:2291`), relative root resolved at startup, `.md` allowlist `run-*.md`/`receipt-*.md`/`INDEX.md`, view-URL response shape | Root-registration unit check (name, mechanism, allowlist) + response-shape assertion (view URLs, not repo-relative paths) | 5.5 |
| P4 (BR-19): gear-menu static Report after Metrics; BOTH conditional appends named (Database `app.ts:797` no-guard; Maintenance `:842` route-keyed); `metricsIdx < reportIdx < dbUpdateIdx`; three spec files extended + new report pin spec | `mcps-gateway.pins.spec.ts:60-84`, `metrics-gear-menu.spec.ts:47-59`, `app.component.spec.ts:2092-2114` extended; new report pin spec green | 5.5 |
| P1 (BR-1..BR-6): no-idle (BR-6), re-arm independence + config completeness (BR-4), at-least-once expired-key leg (BR-2), guard re-mint predicate (BR-1) + archive-aware recreate (BR-3), picker + DST-zone (BR-5), seed lifecycle | Tasks 5.1–5.4 | 5.1–5.4 |

---

## Test substrate & fencing rules (MANDATORY)

- **Mock-manager REST fixture:** `tests/test_scheduler_api.py:22-129` — temp SQLite `source_configs` / `instance_mappings` / `schedule_executions` (`:49-87`), Mock repo (`:93-103`), `scheduling_service` defaulted to None-returning AsyncMocks (`:114-117`). FENCE every new REST test like this — temp DBs only.
- **E2E mirrors (integration-marked, deselected by default):** `tests/integration/test_scheduled_tasks_e2e.py` — `pytestmark` `:56`, `test_one_shot_schedule_creates_job_item :163`, `test_cancelled_schedule_never_dispatches :257`, `test_one_shot_beyond_cap_no_dispatch_with_reason :418`.
- **Seed lifecycle mirror:** `tests/unit/repositories/test_tool_usage_seed_lifecycle.py:136-145`.
- **Source-GC substrate (corrected anchors):** GC predicate `daemon/repositories/source/repository.py:728-772`; service `daemon/services/sources_gc_service.py`; wiring `daemon/api.py:2192-2205`; defaults `daemon/config.py:3047-3110`; archive table `daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql:80-93` (span includes the `source_id` PK at `:81` — BR-3-load-bearing); archive model `daemon/repositories/source/models.py:221-251`; archive-PK collision `daemon/repositories/source/repository.py:83-84`. *(CORRECTED — the earlier "GC lanes `daemon/manager.py:6870,6897,6962`" anchors were WRONG; `manager.py` carries no GC lane.)* The idle-policy DEFAULT lives at `daemon/services/maintenance.py:352` (CORRECTED from `:132`).
- **BR-7 census/pin hygiene:** if the optional UN_GATED backstop job lands, update `tests/unit/test_maintenance_idle_starvation.py:113` (set-equality census `set(JOB_IDLE_POLICIES) == REGISTERED_JOBS`) AND `:172` (un_gated count `== 9`). The existing scheduler pins `tests/test_scheduler_adapter.py` (PATH CORRECTED 2026-11-22 — the tracked file is at tests/, not tests/unit/), `tests/unit/test_scheduler_deferred_mechanism_probe.py` — and `tests/unit/test_scheduler_deferred_delivery_pin.py` (SURVIVES upstream — present at `86e196576` and at tip `8894f86a`; the earlier "deletes" claim was FALSE; unconditional keep-green) — MUST stay GREEN after T1.3 makes the consumption branch chain-aware. Exposure note: the BR-6 AST set already pins `daemon/manager.py` whole-file (a gate added at the boot-hook site cannot escape) — the keep-green list and the AST set together cover the full exposure surface.
- **run_id mint interpreter:** the mint test MUST use the named daemon venv interpreter `/home/nea/ensemble-src/.venv/bin/python` — bare `python3` FAILS on this host (`ModuleNotFoundError: sqlalchemy`), so a `python3`-based test measures the wrong thing.
- **No live-shaped HOME paths:** never resolve install dirs from `$HOME` (incident history: two live-install deletions 2026-10-12); use `mktemp` FAKE_HOME + escape guard.
- **Dev env fence for any daemon-booting test:** `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079` (per the worktree claim), mode 600. Scrub per-variable (`env -u POSTGRES_DB -u POSTGRES_USER ...`) — `env -u POSTGRES_*` is a silent NO-OP (bash does not glob env names).
- **Boot-sweep guard:** any frozen-binary boot smoke from a daemon-child shell MUST pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly (`env -u` on it strips the ambient guard and the sweep KILLS live processes).
- **TypeScript:** only via `frontend/node_modules/.bin/tsc`; never bare `npx tsc` from repo root (remote fetch incident 2026-10-03).
- **Git discipline:** status/log/diff --stat plus in-branch pathspec-limited commits of this phase's OWN deliverables only; no pushes/merges/tags/release-dir/worktree mutations (protected refs are the leader's).

---

## Risks & mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R-P5-1 | R2 pin written as bookkeeping-only (not behavioral) ⇒ idle gate reintroduced undetected | high | Behavioral leg FIRST (mirror `:204-208`), structural leg second; both mutation-proven |
| R-P5-2 | Test resolves live-shaped HOME / prod DB | high | Temp DBs + FAKE_HOME fence + per-var env scrub + dev fence |
| R-P5-3 | Re-arm persistence test passes on light write but fails at boot | high | Explicit boot-reconstruction assertion; upgrade P1 T1.3 to rebuild if RED |
| R-P5-4 | A scheduler pin our change depends on breaks at rebase (upstream churn on exactly the T1.3-touched surface) | medium | Rebase onto staged `source_commit`; keep-green list asserted at final HEAD. *(CORRECTED 2026-11-22: `86e196576` does NOT delete `test_scheduler_deferred_delivery_pin.py` — the file survives; the obligation is unconditional keep-green, which is stricter.)* |
| R-P5-5 | Version slot collision with sibling hbk lane | medium | RE-CHECK tags at tag time; take next free slot; rebase onto staged source_commit |
| R-P5-6 | FE surface check passes `tsc`/jest but fails AOT | medium | `ng build --configuration development` gate after template-touching commits |
| R-P5-7 | Manual-trigger receipt loses the token (writer derives from the DB/row instead of the envelope) | low | T5.6 asserts the token round-trip end-to-end (writer never reads DB); row provenance kept as marked audit sub-check; P4 owns shape |
| R-P5-8 | Structural leg written as a grep ⇒ false assurance (the no-idle attestation text lives in the new files' docstrings, so a raw grep false-REDs) or false-GREENs | high | BR-6: AST over EXACTLY the four named files, docstrings/comments excluded (real-AST precedent `test_defer_busy_witness_caller_pin.py:128-140` — `ast.parse`+`ast.walk`; never a text scan) |
| R-P5-9 | BR-3 test legs still expect a cancelled row to be RESURRECTED ⇒ a correct implementation fails the plan's own gate | high | T5.4 leg (c) REPLACES the old "post-GC row recreated" leg; cancelled-archived is asserted NOT resurrected |
| R-P5-10 | Guard re-mints a past `run_at` unconditionally ⇒ the missed week is silently skipped | high | BR-1 exact predicate; T5.4 legs (cap unset ⇒ untouched; cap exceeded ⇒ re-minted) |
| R-P5-11 | Optional UN_GATED backstop lands and breaks the old-tier census | medium | BR-7: update `test_maintenance_idle_starvation.py:113` and `:172` in the SAME change |
| R-P5-12 | run_id mint test runs bare `python3` ⇒ false result on this host | medium | Named interpreter `/home/nea/ensemble-src/.venv/bin/python` (BR-14 leg) |

---

## Acceptance criteria

- AC1: R2 two-legged pin exists in `tests/unit/test_maintenance_no_idle_trigger.py` — BEHAVIORAL + AST STRUCTURAL over EXACTLY the four BR-6 files; ONE mutation REDS BOTH legs; GREEN at final HEAD.
- AC2: Picker (incl. BR-5 DST-zone legs + bucket-count uniformity), re-arm independence (+ BR-4 config completeness), BR-2 expired-key leg, guard (BR-1 legs + BR-3 legs), seed lifecycle tests all green with fencing rules honored. **Existing scheduler pins stay GREEN after T1.3** — `tests/test_scheduler_adapter.py` (path corrected 2026-11-22), `tests/unit/test_scheduler_deferred_mechanism_probe.py`, and `tests/unit/test_scheduler_deferred_delivery_pin.py` (SURVIVES upstream — no deletion; unconditional keep-green); if the optional UN_GATED backstop lands, `tests/unit/test_maintenance_idle_starvation.py:113` (set-equality) and `:172` (un_gated count) are updated in the SAME change (BR-7).
- AC3: Every P2/P3/P4 AC maps to a named, green check; the four maintenancer pins stay green + new report-ALWAYS pin added; the BR-8 row is asserted on the FOUR LITERAL tool names (`spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`) with deny byte-unchanged — NOT the `"instance"` category form.
- AC4: Manual-trigger token round-trip test green (message `trigger: manual` → orchestrator envelope → receipt `trigger_type="manual"`; audit sub-check green); fallback constraints documented.
- AC5: Staging artifact produced at the re-checked next-free version, rebased onto staged `source_commit`, with explicit NO-promote/NO-ceremony handoff note and operator-step flag (BR-20).
- AC6: No test touches live-shaped paths or prod DB; all boot-smoke fences pinned.
- AC7: BR-11 (event-driven deadline covering the initial dispatch AND its single re-dispatch, no polling), BR-14 (named venv interpreter + bash fallback binding + run_token reuse) and BR-15 (reportless-week detector + lane-4 orphan listing) legs are green.

---

## Verification plan (this phase IS the verification)

- Run the full new unit pack under the dev fence; confirm zero net-new deterministic failures.
- Confirm BOTH R2 legs RED under ONE injected idle-gate mutation (AST structural leg over the four files + behavioral leg) and GREEN at final HEAD.
- Confirm the acceptance map has a green check per row — including the BR-8 four-literal-grant row, BR-11, BR-14 and BR-15.
- Confirm the BR-7 keep-green list (`tests/test_scheduler_adapter.py`, `tests/unit/test_scheduler_deferred_mechanism_probe.py`, `tests/unit/test_scheduler_deferred_delivery_pin.py` — all three SURVIVE upstream; keep-green is unconditional) is green after T1.3; confirm `test_maintenance_idle_starvation.py:113`/`:172` are updated iff the optional backstop landed.
- Confirm the BR-1/BR-2/BR-3/BR-4/BR-5 legs are green with the operative predicates quoted from the addendum.
- Confirm staging note completeness (version, source_commit, rebase basis, no-promote, operator step, receipt proof) — BR-20.
- Downstream: tester gate consumes the staging note for the manual-trigger receipt.

---

## Open questions

- **Version slot:** nominal `v0.31.27` — leader must confirm at tag time against the sibling hbk lane's staged version (R7, BR-20). No blocker to planning.
- **Re-arm persistence:** if Task 5.3 goes RED on the light write, P1 T1.3 must be upgraded to the full `update_schedule` rebuild — a cross-phase change needing leader awareness (flagged in P1 open questions too). The COMPLETE-config read-modify-write (BR-4) is mandatory either way.
- **Daily backstop job:** if P1 includes the optional UN_GATED `MaintenanceService` job, add its `UN_GATED` assertion to Task 5.4 AND update `test_maintenance_idle_starvation.py:113`/`:172` in the same change (BR-7); confirm with P1 implementer.
- **Live version re-verification:** release state not derivable in-repo; the planning-time figures (caller said v0.31.25, adjacent history suggested v0.31.24) are a DATED SNAPSHOT — re-derive at promote time (ceremony owner's call, out of this lane; BR-20).
- **DP-1 (leader veto window, addendum §10):** BR-13 L3 — the accepted, documented, surfaced REPORTLESS week when both writer rungs fail. Not a P5 blocker; the BR-15 detector is what makes it visible.
