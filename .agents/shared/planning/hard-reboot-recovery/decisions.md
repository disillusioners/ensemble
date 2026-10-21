# Decisions: Hard-Reboot Recovery — "Ensemble Continues After a Hard Machine/VM Power-Loss Reboot"

Date: 2026-10-19
Author: planner[v2] via plan-creation worker
Branch: `feature/hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Evidence base: `technical-analysis.md` (311 lines, verified; LEAD DRIFT corrections recorded at §Context Summary)
Prior art: `.agents/shared/planning/auto-continue-running-after-restart/` (`decisions.md` 322 lines, structure mirror)

Every decision below adopts the gap-doc §6 recommendation as the plan's working decision, with rationale + the rejected alternative. Where the gap doc defers an open question (Q-1..Q-5 in §8), this file resolves it. Sources: `technical-analysis.md` §§1, 2, 4, 6, 7, 8; the prior-art design authority; and the user's verbatim directive in the task brief.

---

## D1. Auto-resume on hard reboot — NOT park-and-report

**Decision:** The daemon resumes `RUNNING` instances on hard reboot, transparent to user and downstream. `PAUSED` instances stay parked (excluded by AutoContinue's existing instance-status filter at `auto_continue_boot_pass.py:354-356`); terminal instances never touched (excluded by the same filter plus `task_repository.find_auto_continue_candidates` at `repository.py:1066-1191`); `WAITING_CHILDREN` parents remain owned by `DependencyBus.start()` (`dependency_bus.py:1499-1560`) — bus is restart-durable. A reboot storm can never double-continue or wedge the boot (three-layer never-wedge envelope verbatim from `auto_continue_boot_pass.py:230-494`).

**Rationale:**
- User-verbatim: *"if our ensemble can continue after a hard reboot of machine/vm, that will be a very nice feature"* — the "continues" verb is unambiguous (continue ≠ pause; continue ≠ report).
- Prior-art design authority: `auto-continue-running-after-restart` already shipped this exact behavior for the daemon-only crash case. Extending it to the host-power-loss case reuses EVERY mechanism that pass already proved (C1 dedup, CAS at `repository.py:1066-1147`, instance-status exclusion set, kill-switch, three-layer never-wedge).
- Evidence-base §1 confirms ~70% of hard-reboot continuity is already in place — auto-resume is the natural extension, not a redesign.
- The fundamental mechanism (LangGraph `astream(None)` against `thread_id == instance_id` at `manager.py:3162` + `instance_messaging.py:4267-4269`) is a restart-durable primitive — it does NOT care whether the restart came from `kill -TERM` or kernel SIGKILL.
- Park-and-report would invent a new visibility surface (a "post-reboot task panel") with zero precedent, zero demand, and full cost of a new messaging path.

**Rejected alternative:** park-and-report — every `RUNNING` instance becomes a notification "your last turn was interrupted by a reboot, click to resume." User-verbatim forbids this ("no manual action"); structural cost = a new notifier surface + a new resume UX + tests for "user wants to resume half-interrupted state" = ≥3× the surface, zero benefit.

---

## D2. G6 mechanism = architect-folded: `After=postgresql.service` ONLY + preflight-reuse + `connect_timeout=10s` (NOT the original G6.B retry helper)

**Decision:** G6 architect-folded. Edit `scripts/systemd/ensemble-daemon.service:84-87` to add `After=postgresql.service` ONLY (NO `Wants=`, NO `Requires=`); add `connect_timeout=BOOT_DB_TIMEOUT_S=10` to `create_postgres_engine` at `daemon/repositories/factory.py:232-284`; reuse the existing `_boot_db_preflight` + launcher exit-75 lane at `daemon/__main__.py:143-247` (entry paths: `run_app.py:70` frozen binary + `__main__.py:467` `python -m daemon`); launcher maps 75 → budget-exempt infinite backoff per `launcher.sh:1340-1346,1576-1590`; `BOOT_DB_TIMEOUT_S=10` already declared at `daemon/constants.py:70`. NO new env-knob. Phase 3 ships the ready-to-ship drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) as the delivery mechanism for existing installs (because `adopt-unit.sh:537` refuses template clobber).

**Rationale:**
- **The original G6.B retry helper would have been dead code.** With PG down, `InstanceManager.__init__` raises first (`create_postgres_engine` → `create_all` → migrations → `_ensure_postgres_columns` → discard CAPTURE — `manager.py:657,751,755-757,773,973-1166`), an UNWRAPPED call. The planned `_init_manager_with_pg_retry` at the engine-open step is one call too late; constructor fails first; the planned loop is dead code in the canonical PG-down boot (architects A + B independently verified, C confirmed).
- **The exhaustion path was a regression.** Lifespan failure → uvicorn `sys.exit(STARTUP_FAILURE=3)` (`uvicorn/main.py:43,614`) → launcher `classify_exit(3)` = **crash lane**: backoff 10→20→40→80→160→300s under burst budget >5/600s → abort exit 1 after ~5 cycles (~5–8 min). A transient PG outage (WAL replay) would leave the daemon **permanently down** under the original plan. The preflight + exit-75 lane is **budget-exempt, infinite** — the canonical "wait for a transient dependency" lane; a 5-min WAL replay resolves with the daemon starting cleanly the moment PG accepts connections; no crash-budget burn; systemd never sees a failure.
- **Drop `Wants=postgresql.service` (architect R-A5).** On remote-PG hosts, local `postgresql.service` is a Debian meta-unit (`Type=oneshot`, `ExecStart=/bin/true`, `RemainAfterExit`) — zero readiness value; the real dependency is `network-online.target`, already declared. On mixed hosts (local PG installed but unused), `Wants=` would start the unused local PG at every boot (wasted RAM on a memory-constrained box — this host was OOM-killed on 2026-10-17). `After=` alone is correct + harmless on local-PG hosts, inert on remote-PG hosts.
- **Drop `Requires=`.** Preserves the operator's "daemon up first to debug PG" affordance (G6.C is too strict).
- **`connect_timeout=BOOT_DB_TIMEOUT_S=10` is the only code change in Phase 1.** The current `create_postgres_engine` at `daemon/repositories/factory.py:232-284` sets only `pool_pre_ping`; a firewall-DROP hangs 75–130s per attempt, making any retry budget fiction. `connect_timeout=10s` makes the preflight's `SELECT 1` time out at 10s, which is the right resolution for both the budget-exempt preflight AND any future in-lifespan retry (none currently).
- **No new env-knob.** Per D16: single kill-switch adopted; fewer knobs = the mandate; the preflight is the existing, better-shaped lane.
- **Net Python LOC: −26** (architect amendment A1). The original plan was ≤30 LOC; the folded plan is ≈4 LOC (1 `connect_timeout` + 2 template comments + 1 template edit). Honours the user directive *"great code = less code"* better than the original.

**Rejected alternatives:**
- **G6.B original** (the planned retry helper + `Wants=` + `ENSEMBLE_INIT_RETRY`) — DEAD CODE at the constructor seam + exit-3 regression + env-knob proliferation. Architecturally incorrect at every level.
- **G6.A alone** (`After=` only, no `connect_timeout`) — firewall-DROP would still hang 75–130s per attempt; the preflight's `SELECT 1` would time out at the default 0 (infinite) and the daemon would wedge. Rejected.
- **G6.C (`Requires=postgresql.service`)** — wrong semantics for a multi-purpose host; regresses the "daemon up first to debug PG" operator affordance. Stricter than needed.
- **Fresh-factory construction+init retry (~12 LOC, exit-75 mapped on exhaustion)** — only if preflight coverage is judged insufficient on some install shape; the architects verified both entry paths reach the preflight, so this is not recommended.

---

## D3. G1 = DEFER (NG-5) — auto-continue already covers the same row

**Decision:** DEFER the alive-row branch deletion (`job_recovery_service.py:682-689` literal log line *"leaving as PROCESSING"* + `stats["alive"] += 1`). Add a follow-up commission item, parked post-promote.

**Rationale:**
- AutoContinue's `find_auto_continue_candidates` (`repository.py:1066-1191`) operates on the same RUNNING row at the task layer; the alive-row branch in `JobRecoveryService` operates on the instance layer with the same projection. Removing the JobRecovery branch saves −8 LOC but risks a future triage regression if an operator relies on the log line for "instances alive but PROCESSING task".
- The G1.A (-8 LOC) recommendation in gap doc §6 is informative: the branch is structurally redundant. But the cost of a future mis-triage outweighs the −8 LOC gain.
- Deferral respects the user's directive: *"great code = less code"* — but −8 LOC after the bigger G6 + G2 surface is rounding error, not signal.

**Rejected alternatives:**
- **G1.A delete the branch now** — same rationale cost, smaller net benefit; deferring lets a future commission decide.
- **G1.B route alive-row through `_schedule_explicit_handle_resume`** — the gap-doc notes the claim-guard (`repository.py:2230-2294`) already prevents concurrent claims, so this is safe in principle; in practice, AutoContinue already routes the same row through that primitive, so adding a second caller creates a second code path for the same recovery semantic. Reject.

---

## D4. G3 = PARK (G3.B / NG-3) — duplicate-fire window acceptable until reported

**Decision:** PARK. Accept the single-emit duplicate-fire window on hard reboot. Document the window in the runbook. Open a follow-up commission if the operator reports a duplicate.

**Rationale:**
- The window is single-emit: a scheduled one-shot task fires at most once extra on hard reboot (`scheduler.py:564-566` + `:681-683` checks the lost-RAM `is_one_time_executed` flag and re-fires when it's gone).
- User has not reported any duplicate. The destructive-task class (the only class where a duplicate matters) is small and operator-visible.
- G3.A fix (`schedule_executions` row on success) is correct but requires a status-enum migration (`schedule_executions` table currently only carries `skipped` rows per `:738+`); the migration cost is not justified by the current evidence.
- The runbook note (Q-3 resolution) tells operators how to detect a duplicate (`schedule_duplicate_emit_total` counter is a future telemetry addition) and how to recover (manual reissue via `safety.task.cancel`).
- **A10 strengthening (architect):** the durable one-shot disable (i.e., the future `schedule_executions` row on success — if/when the G3.A fix ships) bounds the duplicate-fire hazard to a single re-emit on hard reboot. This STRENGTHENS the PARK decision: the future state is well-bounded, and the current parked state is not "fire twice" but "fire once extra" — a small, observable, operator-recoverable window. No code change today; the strengthening argument is about the future G3.A fix's correctness when it does land.

**Rejected alternative:** G3.A status-enum migration now — adds ≤10 LOC emit-side + ≤15 LOC consult-side + ≤20 LOC repo method + a SQLite migration file + telemetry counter wiring. The cost > benefit at zero-observation count.

---

## D5. G7 = PARK (G7.C / NG-1) — KMS_LANE-1 deprecation is a post-promote follow-up

**Decision:** PARK. Add a runbook advisory ("if you used LANE-1 KMS handles, re-attach them after a hard reboot OR migrate to LANE-2 / Secrets before next reboot"). Open the G7.A deprecation cycle commission post-promote of `v0.23.0` (Secrets P2 LANDED, STAGING-ONLY per the staged_release_v0230_composition in project metadata).

**Rationale:**
- LANE-2 env-ref (`kms_resolver.py:125-144`) is the production-shape for BYOK today; reads `os.environ[<VAR>]` at spawn time and is restart-DURABLE.
- Secrets (P2 LANDED at v0.23.0 STAGING-ONLY) is the canonical replacement for LANE-1 (Fernet-encrypted DB storage, restart-DURABLE by construction).
- Per the user's self-provisioning directive (`REUSE, don't generate`) — LANE-1 deprecation is already on the post-promote roadmap; opening it now would race the v0.23.0 nonce ceremony.
- The G7.B "auto-rehydrate LANE-1 from Secrets at boot" option is technically clean (≤40 LOC rehydrate hook + ≤30 LOC test) but locks the LANE-1 surface for another release cycle; if we're going to deprecate LANE-1, we should NOT add a restart-rehydrate path first.
- Add runbook pointer NOW so operators know the work-around; deprecate-and-remove in the post-promote commission.

**Rejected alternatives:**
- **G7.B auto-rehydrate from Secrets** — adds a hook that we'd then delete in the same release as the LANE-1 removal. Less code wins.
- **G7.A deprecation-and-removal now** — races v0.23.0 promote; forbidden by the staged-state per project metadata.

---

## D6. G4 / G5 / G8 = RUNBOOK ONLY — no code path

**Decision:** RUNBOOK ONLY. No code, no migration, no opt-in env-knob. The runbook ships in Phase 3 with the entries enumerated below.

**Rationale:**
- **G4 (no-checkpoint orphans):** an instance row exists but no checkpoint thread. Operational triage only. STR's `boot+10min` clamp handles recovery at the task layer; the runbook tells operators how to identify (`SELECT id, status, … FROM instances WHERE id NOT IN (SELECT thread_id FROM checkpoints)`) and what to do (instance_status='terminal' if grace expires; otherwise, leave, let STR handle).
- **G5 (Task↔JobItem PAUSED reconciliation):** Pause-time concern; orthogonal to hard-reboot. AutoContinue + STR both already exclude PAUSED instances via the existing instance-status filter (`auto_continue_boot_pass.py:354-356`). Runbook notes how an operator with paused jobs during a hard reboot should verify they STAY paused after boot (one SELECT query).
- **G8 (RTO vs STR `boot+10min` clamp):** UX/document concern only. Per-state RTO table documents: RUNNING → ~14s (systemd `RestartSec=10` + boot pass); IDLE → next user/child (worker pool claim); WAITING_CHILDREN → bus-driven (next wake claim); PAUSED → never auto (by design). STR age-gated backstop at boot+10min is the upper-bound safety net, NOT the target RTO.

**Rejected alternative:** ship a "health-check polling" pass to surface orphans — invented surface, no precedent, no value.

---

## D7. Lifespan envelope = the canonical template for the Phase 2 pass ONLY (NOT Phase 1)

**Decision:** ONLY Phase 2 (MQ boot recovery) uses the `daemon/api.py` lifespan envelope pattern — wired at `api.py:582` neighborhood (between `capture_boot_epoch` and `setup_worker_pool`, per A4; NOT beside AutoContinue at `:2350`). Three-layer try/except (per-candidate, per-pass, lifespan-level), kill-switch env-knob opt-in, INFO log on success, WARNING log on per-candidate failure, never-wedge at the lifespan level. **Phase 1 has NO in-process lifespan wiring at all** — it is an OPS template edit (`After=postgresql.service` only) + a +1 LOC `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `create_postgres_engine` (`daemon/repositories/factory.py:232-284`); the canonical PG-wait lane is the existing `_boot_db_preflight` + launcher exit-75 (budget-exempt, infinite), which runs on the `__main__.py` entry path — upstream of the lifespan envelope entirely.

**Rationale:**
- The envelope pattern is the proven defensive shape (gap-doc §5 row #1 reuse anchor; prior-art D2/D3 used this verbatim).
- Ensures both new passes participate in the kill-switch discipline (kill one off, the other keeps running).
- Reuses the operator-mental-model of the existing pass: log lines look the same, failure modes are the same, runbook entry reads are the same.

**Rejected alternative:** roll a new envelope — every new envelope is a new failure mode; violate the uniformity rule.

---

## D8. Phase boundaries

**Decision:**
- **Phase 0** = Worktree + import-resolution gate + env-fence audit (precondition; off the critical path of features).
- **Phase 1** = G6 (unit template `After=` edit + `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `daemon/repositories/factory.py:232-284`; preflight/exit-75 lane already exists — no lifespan wiring). **CRITICAL + OPS-CHEAP** — the gap-doc §4 ranks G6 critical because without it the boot can crash against PG readiness and undermine every other survival path. Phase 1 unblocks Phases 2-4 in the safest way.
- **Phase 2** = G2 (dead-code selector caller). MEDIUM + small surface + reuses a verified selector. Phase 1 proves the lifespan envelope shape; Phase 2 reuses the shape and proves it's durable.
- **Phase 3** = Runbook (G4, G5, G6, G8 documentation + Q-2 RTO table + Q-5 drill). ZERO code path; ships as soon as Phases 1-2 land, in parallel with Phase 4 prep.
- **Phase 4** = Demo E2E on real environment. INTEGRATION test for Phases 1-3. User-mandated per the prior-art AC8 precedent.

**Rationale:**
- Phase 1's tight scope = one gap (G6) + one hook + lifespan wiring = traceable PR; matches the gap-doc severity ranking.
- Phase 2 = the smallest remaining code-only gap. The G2 dead-code is "free" reuse: existing selector + a new caller + a new lifespan wiring. Two phases of in-scope code is the minimum consistent with the gap-doc recommendations.
- Phase 3 (runbook) before Phase 4 (e2e) = operators have the playbook BEFORE they need to drill it.
- Phase 4 last = it's a real-environment proof that depends on Phases 1-3 being live.

**Rejected alternatives:**
- **Single mega-phase** — violates the per-phase reviewability discipline that the prior art's r3 fold re-established; harder to rollback; harder to attribute the failure if Phase 4 e2e fails.
- **Reorder Phase 3 BEFORE Phase 2** — runbook documents code paths that don't exist yet; rewrites twice.

---

## D9. Kill-switch model = SINGLE (per D16); `ENSEMBLE_INIT_RETRY` ELIMINATED entirely

**Decision:** Single kill-switch. `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` (Phase 2) — default ON, `=0` disables. Mirrors the `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` shape (`auto_continue_boot_pass.py:145-156`) — same convention, separate concern (the MQ recovery pass is the only Phase-2 concern; the PG-wait concern is upstream and needs no kill-switch because the preflight is the canonical, fail-closed-by-construction lane). `ENSEMBLE_INIT_RETRY` (Phase 1) is **ELIMINATED entirely** per the architect-folded A1 + D16 ruling: fewer knobs = the mandate; the preflight is the existing, better-shaped lane; the env-knob would have been an opt-in flag for a path that doesn't exist.

**Rationale:**
- Kill-switches with sane defaults are the operator's escape hatch. Naming convention: `<FEATURE>_..._ON_RESTART` mirrors the existing precedent.
- **One switch, one concern.** A separate `ENSEMBLE_INIT_RETRY` would have been a switch for a non-existent path (the helper was deleted). Collapsing to one switch keeps the operator's mental model: "is the boot pass on or off?"
- The preflight (Phase 1, no switch) is upstream of the daemon's main process; the operator's "off" for the boot pass is at the per-pass level (`ENSEMBLE_HARD_REBOOT_BOOT_PASS`); the preflight cannot be turned off without breaking the canonical entry path.

**Rejected alternatives:**
- **Two switches (`ENSEMBLE_HARD_REBOOT_BOOT_PASS` + `ENSEMBLE_INIT_RETRY`)** — second switch was for a dead-code path; eliminating it is the architect's A1 + D16 ruling. Collides two concerns into one mental model.
- **No kill-switch at all** — operator has no escape hatch if the MQ recovery pass misbehaves on a custom install shape. Reject.

---

## D10. Non-goals honored verbatim from gap doc §7

**Decision:** All seven non-goals (NG-1..NG-7 from `technical-analysis.md` §7) are honored as-is. Each is parked as a follow-up commission or a documented runbook note; no plan-level scope creep.

| Non-goal | Parking location |
|---|---|
| **NG-1** KMS_LANE-1 deprecation/removal | Follow-up commission post-promote of `v0.23.0` Secrets |
| **NG-2** `WatchdogSec` wiring | (no action — P4 deferred by design) |
| **NG-3** Schedule catch-up semantics | Follow-up commission if duplicate fire reported |
| **NG-4** In-flight tool-call at-least-once | (no action — LangGraph invariant) |
| **NG-5** G1 JobRecovery alive-row deletion | Follow-up commission post-Phase-3 |
| **NG-6** G5 Task↔JobItem PAUSED reconciliation | Runbook note (Phase 3) |
| **NG-7** `RequiresMountsFor=` templating | Runbook drop-in example (Phase 3) |

**Rationale:** The gap-doc non-goals table is the dispatcher's prior decision record; honor it verbatim unless we have evidence to overturn. None of the seven is being overturned by this plan.

---

## D11. Plan worker's pre-flight for G7 follow-up commission

**Decision (pre-flight, not in scope here):** When the post-promote G7 follow-up commission opens, the plan worker MUST verify the current staged version of the Secrets feature via `git tag -l 'v0.23*'` + `git rev-parse latest` BEFORE drafting the deprecation cycle. If v0.23.0 has been promoted (no longer STAGING-ONLY), the deprecation cycle can ship one release after the promote. If still STAGING-ONLY, the deprecation cycle is DEFERRED until promote.

**Rationale:** Project metadata's `staged_release_v0230_composition` is the source of truth; do not race the nonce ceremony.

**Rejected alternative:** open the G7 commission now unconditionally — risks a deprecation cycle tracking a STAGING-ONLY feature.

---

## D12. Worktree convention

**Decision:** ALL implementation + E2E lanes run in `/home/nea/ensemble-src-wt-hard-reboot-recovery` (sibling of main checkout). The main checkout at `/home/nea/ensemble-src` is LEFT UNTOUCHED per its current dirty state. The worktree-claim KV row in shared-meta-kv (`wt.claim.hard-reboot-recovery`) is the receipt; the `worktree-claim.txt` file in the planning dir is the developer-readable receipt.

**Rationale:**
- Mirrors prior-art Phase 0 + Δ7 / D20 worktree-gate discipline.
- `wt.claim.hard-reboot-recovery` is already populated (per shared-meta-kv snapshot in current context) — branch `feature/hard-reboot-recovery`, base `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`, env fence verified, venv `none (lazy rule)`.
- The `_ensure_postgres_columns`-style "if no fenced .env → refuse" guard is already wired in `dev.sh:85-91`.

**Rejected alternative:** work in main checkout — direct violation of the no-main-checkout-edits convention; would also collide with main's dirty state.

---

## D13. Task-less RUNNING-instance class = EXPLICIT non-goal with MANDATORY tracked follow-up commission (architect A2)

**Decision:** D13 is a **non-goal of this plan** with a **MANDATORY tracked follow-up commission** (not a "nice-to-have", not "park and forget"). The task-less RUNNING-instance class is invisible to every existing boot sweep:

- `find_auto_continue_candidates` roots on `task` (`task/repository.py:1128-1131`) — a RUNNING instance with no task row cannot appear.
- STR (StaleTaskRecovery) roots on Task; all 6 RDRS lanes root on `report_injections`; bus sweeps root on `dependency_watchers`; JobRecovery roots on `job_queue_items` (and its alive-row branch explicitly does nothing — the G1 debt); `reconcile_turn_mirror` *detects* the class (`drift_flags["instance_running_without_inflight_task"]`, `task/repository.py:2255-2281`) but logs at DEBUG and is not a boot sweep.
- **Both 2026-10-19 RCAs are this class:** leader turn `897011bb` (no boot reader for "non-terminal instance + healthy checkpoint + no PROCESSING task"); rcc mission `6b1630e7` task-less ghost governor (kernel OOM-kill landed in council-spawn Txn1→Txn2 gap → task-less ghost → boot watcher-cancellation → design-legal orphan → idle root).

**Scope of the follow-up commission (~45–60 LOC, exceeds this plan's ≈44-LOC ceiling):** instance-rooted selector + checkpoint probe per candidate + resume-vs-park-vs-terminate decision. The commission MUST land before a third occurrence causes a user-visible incident. Risks: false-resuming design-legal orphans (worse than a frozen instance) — the commission must include a per-candidate safety check (e.g., checkpoint has superstep before pause-cancel) before any auto-resume.

**D13 follow-up scope INCLUDES mission job-watcher boot behavior (recorded ruling 5 / D18):** the same "invisible to sweeps" class applies to mission jobs whose watcher fires during a hard reboot; a mission job-watcher that was about to enqueue a wake message but the daemon dies in the gap is structurally identical to a task-less RUNNING instance from the boot sweep perspective. The follow-up commission's selector design must include a mission-job-watcher boot pass that handles this class.

**Why not in this plan:**
- 45–60 LOC + the per-candidate safety check is a real implementation lane, not a planning artifact.
- The risk of false-resume (design-legal orphan) is higher than the risk of the current behavior (frozen instance).
- The runbook ships a triage query so the operator can identify instances in this state: `SELECT ... FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.instance_id AND t.status IN ('pending','running','paused'))`.

**Why MANDATORY tracked (not "parked"):** the user directive *"if our ensemble can continue after a hard reboot"* is not satisfied for this class today; a third occurrence is plausible before a follow-up commission lands; the existing RCAs are 24h old (both 2026-10-19). The follow-up commission is a tracked obligation, not a "someday maybe".

**Rejected alternatives:**
- **Fix it in this plan** — exceeds the ≈44-LOC ceiling; risks false-resume; would require a 2-week investigation to design the per-candidate safety check. Not the right scope for this plan.
- **Document only, no commission** — violates the user's intent ("continues after reboot" applies to this class too) and ignores the 24h-old RCA pattern.
- **Force-cancel on boot for task-less instances** — destroys design-legal orphans (e.g., a paused-then-killed instance the operator is debugging); worse than the current behavior.

---

## D14. FOLD ratification meta-decision (recorded ruling 1)

**Decision:** The architect's amendments A1–A11 are **FOLDED in-place** (one canonical home per artifact; no SUPERSEDED stamps; no history rewrite). The plan docs and the gap doc's §5 reuse inventory are the canonical homes; the §9 LOC tables in the plan docs supersede the gap doc's §9 by reference (the gap doc gets a one-line §9 reconciliation pointer).

**Rationale:**
- FOLD (per the dispatcher's explicit instruction) is the right pattern: every artifact has one canonical home, the next reader sees the current truth without history traps.
- SUPERSEDED stamps leave a cold-read trap (a future maintainer sees "SUPERSEDED" and has to look up what replaced it; the look-up may be wrong if the replacement itself was amended later).
- The architect's review is the authoritative source for the amendments; the planner's role is to fold them in, not to relitigate them.

**Rejected alternative:** **SUPERSEDED stamps + a history section in each file** — explicitly forbidden by the dispatcher's fold instruction; cold-read trap; would have required a parallel history table in each file, doubling the maintenance surface.

---

## D15. Phase-3 runbook: SHIP the ready drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (recorded ruling 2)

**Decision:** The Phase-3 runbook SHIPS a copy-paste-ready drop-in example file (a single stanza with `After=postgresql.service`) at the path `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf`. The drop-in is the **delivery mechanism for existing installs** because `adopt-unit.sh:537` refuses template clobber; without it, the unit-template edit is illusory on the live fleet.

**Rationale:**
- `adopt-unit.sh:537` is a guard against clobbering a unit file the operator has customized; it refuses to overwrite a unit that already exists. The new-install path (a fresh `ensemble-daemon.service` template) propagates the `After=postgresql.service` edit; the existing-install path requires a drop-in (systemd's standard mechanism for unit-level overrides).
- The drop-in pattern is the canonical systemd idiom: `/etc/systemd/system/<unit>.d/<n>-<purpose>.conf` is the override file; `systemctl daemon-reload` + `systemctl restart <unit>` activates it.
- The drop-in is a SINGLE stanza — it cannot be a multi-purpose config file; the runbook ships the file content as a fenced code block so the operator can `cat > /etc/systemd/system/ensemble-main.service.d/10-pg-order.conf <<EOF ... EOF` it.
- The drop-in is the answer to Q-1 (wait-and-retry) on hosts where the unit template edit doesn't reach (the canonical install shape).

**Rejected alternatives:**
- **No drop-in** — G6.A becomes illusory on existing installs; the operator has to either re-install (heavy) or hand-edit the unit (error-prone).
- **Multiple drop-in paths** — confusion; one canonical drop-in is the right shape.

---

## D16. Single kill-switch model; `ENSEMBLE_INIT_RETRY` ELIMINATED entirely (recorded ruling 3)

**Decision:** Adopt the single kill-switch model. `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` (Phase 2) is the only new kill-switch. `ENSEMBLE_INIT_RETRY` (which would have been a sister switch for the original G6.B retry) is **ELIMINATED entirely** because the helper it would have switched is also eliminated (architect A1). The mandate is *"fewer knobs = better"*; the preflight is the existing, better-shaped lane and does not need a switch (it is upstream of the daemon's main process; the operator's "off" is at the entry-path level, not the per-pass level).

**Rationale:**
- The original two-switch model (per D9 pre-fold) was designed for two distinct features (PG retry + MQ recovery). After the fold, the PG retry feature is gone; the second switch has no feature to switch. Collapsing to one switch keeps the operator's mental model clean.
- The preflight cannot be turned off without breaking the canonical entry path; a switch for it would be misleading.
- Mirrors the `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` precedent — one switch per recovery pass.

**Rejected alternatives:**
- **Two switches** — second switch is for a non-existent path; the D16 ruling eliminates it.
- **No switch** — operator has no escape hatch if the MQ recovery pass misbehaves on a custom install shape.

---

## D17. `QUEUE_DISCARD_ON_STARTUP` default revisit = PARKED, tied to the G2 follow-up commission (recorded ruling 4)

**Decision:** PARKED. The shipped `config.yaml:318` default for `QUEUE_DISCARD_ON_STARTUP` is `false`; the live `.env` overrides to `true`. The default revisit (whether to flip the shipped default to `true`) is parked; a **G2 follow-up commission** (a separate, small commission from D13) owns the decision. The G2 follow-up commission's scope is: re-examine the G2 reset action under the discard-on-startup interplay (architect A3), and decide whether the shipped default should flip to `true` based on Phase 4 demo data.

**Rationale:**
- The shipped `false` default is the right dev-friendly value; the live `true` override is the right production value. Flipping the shipped default is a behavior change for every install; it must be tied to a real production-data signal (e.g., "X% of live boots had stuck `processing` rows that the override cleaned up").
- The Phase 4 demo E2E (kill -9 + reboot the host) is the first data point; if the demo shows the override catching real stuck rows, that becomes the production signal. Until the demo runs, the default is left as-is.
- A3 amends the G2 acceptance to "assert `get_stats()["processing_count"] == 0` post-boot and the busy-guard clears" — this assertion runs on the DEMO install (which has the `true` override); it does NOT change the shipped default.

**Addendum (2026-10-21 — leader ruling Q1, after D19):** D17 **STAYS PARKED**. Its "G2 hygiene value" rationale is **VOID**: the pass does **NOT** clear the busy-guard on shipped-default installs (D19 — `get_stats()` counts `ready` rows in `pending_count`, so the wedge is *transformed into visible-pending*, not cleared). The default flip is nevertheless **not** the answer — silent backlog deletion at boot is worse than a visible-pending row. Instead of flipping the default, the **runbook now carries an explicit operator triage** for a visible-pending row (`docs/runbooks/hard-reboot-recovery.md` §6.2, added with this ruling). The "A3 amends the G2 acceptance" bullet above is **superseded** by D19's split acceptance: P4-1..P4-6 are mandatory on EVERY install; P4-7 (busy-guard clears) holds only on a `QUEUE_DISCARD_ON_STARTUP=true` demo install. The G2 follow-up commission remains the owner of the default, with a corrected premise (no hygiene value to justify it).

**Rejected alternatives:**
- **Flip the shipped default now** — behavior change for every install; no production-data signal yet; reversible only via config override; the G2 follow-up commission will be the right place if the data supports it.
- **Document the divergence only, no commission** — the divergence is real and worth a follow-up; the runbook notes it but does not own the decision.

---

## D18. D13 follow-up scope INCLUDES mission job-watcher boot behavior (recorded ruling 5)

**Decision:** The D13 follow-up commission (task-less RUNNING-instance class, MANDATORY tracked per D13) explicitly INCLUDES the **mission job-watcher boot behavior** in its scope. The mission job-watcher is a daemon-side component that fires when a mission job's conditions are met; on hard reboot, a watcher that was about to enqueue a wake message but the daemon dies in the gap is structurally identical to a task-less RUNNING instance from the boot sweep perspective (the bus's `_recover_fired_unsent` sees `enqueued_at IS NULL` and re-fires; but if the watcher itself was a process, not a row, the watcher's state is gone).

**Rationale:**
- The rcc mission `6b1630e7` RCA revealed that a mission job-watcher boot gap can produce a task-less ghost governor that is invisible to all 11 sweeps. The watcher DIDN'T re-arm because the boot watcher-cancellation path doesn't include mission-watchers.
- The D13 follow-up's "instance-rooted selector + checkpoint probe" must extend to the mission-job-watcher surface: a check that all mission-watchers re-armed on boot, and a re-arm action for any that didn't.
- A separate D13-sibling commission would be premature; the same selector design pattern works for both surfaces.

**Rejected alternative:** Scope D13 narrowly to instance rows only; open a separate D19 commission for mission-watchers — doubles the review surface; the design pattern is the same.

---

## D19. G2's lane-6-enablement claim is WITHDRAWN — the claim is narrowed to queue-accounting hygiene; the skip-guard stays (INV-1)

**Decision (recorded ruling 6, 2026-10-21 — architect ruling on reviewer-council finding C4, `006396b0`):**

1. **The claim "the MQ boot reset enables RDRS lane-6 healing" is WITHDRAWN as factually unreachable.** The reset's skip-guard (a row whose owning task is `running`/`paused` is skipped) excludes exactly the rows lane 6 heals (lane 6 requires `mq.status='ready'` AND `tk.status='running'`). The composition is unreachable in **every** boot ordering: the reset runs at `daemon/api.py:747`, ahead of StaleTaskRecovery (`api.py:776` → `pool_orchestrator.py:313`), the RDRS boot sweep (`pool_orchestrator.py:467/499`), and AutoContinue (`api.py:2605`); no lane terminalizes an orphan task before it runs; and STR's own boot pass is a no-op at boot under the boot-epoch amnesty clamp (`task/repository.py:3913-3914`).
2. **The skip-guard is NOT narrowed — it is load-bearing (INV-1).** *Queue ownership:* a `message_queue` row whose `message_id` is owned by a `task` row in status `running`/`paused` is owned by the TASK lifecycle, not by the queue, and the boot pass must never make it claimable. Narrowing the guard would re-arm such a row while AutoContinue is resuming its turn (`api.py:2605`), and the row would then be claimed a second time → **double delivery** of the same message. Regular instance messages have no dedup; report-family rows are guarded only for `PENDING`/`DEFERRED` obligations. Narrowing the guard also exceeds the plan's ≈44-Python-LOC ceiling (it needs a checkpoint-existence probe for the instance) and would drag in the no-checkpoint-orphan class (G4) that this plan defers to runbook-only.
3. **The delivered benefit is narrowed to what the pass actually does:** stranded rows leave the `processing` bucket and become visible as *queued* (`processing_count == 0` post-boot); the narrow class whose owning task is non-running and non-terminal becomes claimable again. **The busy-guard is NOT cleared on shipped-default installs** — `get_stats()` counts `ready` in `pending_count` (`message_queue/repository.py:1477-1488`) and the guard is a disjunction (`daemon/tools/instance.py:4200`), so the wedge is **transformed into visible-pending**, not cleared. It clears only on claim-and-complete or on a `QUEUE_DISCARD_ON_STARTUP=true` install (deferred wipe).
4. **The §9-3 implementation-time check is replaced** with the mutual-exclusivity (documentation) test specified in `.agents/shared/planning/hard-reboot-recovery/c4-lane6-ruling.md` §3 — it pins that a report-family wake row whose task is `running` is not flipped and does not enter lane 6's predicate before or after the pass.
5. **Scope-truth correction is surfaced, not buried.** The commission's scope statement to the user asserted lane-6 healing; that assertion is not deliverable as written. It is escalated with the leader for the user report (C4 ruling §7).
6. **Lane 6 is unaffected.** Its own production producer (the claim-window shape — wake task claimed while its row is still `ready`, preserved by the F-1 epoch-belt) is independent of the MQ reset (`report_injection/repository.py:1980-2012`). Narrowing this claim disables nothing that works today.

**Rationale:**
- The contradiction is **inherited**: `plan-overview.md:28` mandates the guard and `architecture-recommendation.md:116` endorsed a benefit that cannot coexist with it. The implementer followed the more specific instruction literally, which was the correct call under contradiction — **the code is SAFE; the CLAIM is what fails.** This ruling fixes the claim, not the code.
- The skip-guard and the boot-epoch predicate answer different questions (ownership vs age). The guard is the only discriminator between "a task lifecycle owns this row" and "nothing will ever move this row". At reset time no task is live in the current boot (the worker pool starts at `api.py:776`, after the reset at `:747`), so a `running` task row is always a pre-boot orphan whose fate belongs to AutoContinue (resume) or STR (reap at ~boot+10min).
- The leader's decision rule applies twice over: narrowing the guard **alters at-least-once delivery semantics** (the rule's second trigger) and would exceed 10 LOC. Both branches resolve to *narrow the claim + escalate*.
- The honest wording already exists in the plan (`plan-overview.md:176`, the "A3 reframing"); this ruling propagates it to the architecture recommendation, the phase rows, and the runbook, and pins it with a non-tautological M5.

**Rejected alternatives:**
- **Narrow the skip-guard to make the composition reachable** — breaks INV-1, creates a double-delivery window in the hard-reboot-resume case, needs a checkpoint probe (≫10 LOC), and imports the G4 no-checkpoint-orphan class. Rejected on safety, scope, and the leader's decision rule.
- **Make G2 lane-aware (join RDRS) to feed lane 6 directly** — violates the G2.A "no RDRS join" amendment (`architecture-recommendation.md:92, :96`), inherits the loop-thread hazard the RDRS wiring already carries, and is a seventh lane in a framework whose value-add does not apply.
- **Delete the pass** — it still delivers real value (the processing bucket empties; the stranded row becomes visible; a narrow class becomes claimable again) at zero risk. Rejected.
- **Fix the claim only in the architecture recommendation** — the same false claim appears in `plan-overview.md:32/:124/:178` and (in weakened form) in the Phase-4 acceptance. Rejected as incomplete.

**D19-FU1 (tracked follow-up candidate, NOT part of this commission):** a *task-aware MQ ownership reconciliation* pass — for rows whose owning task is dead/terminal, terminalize the row through the existing `reconcile_turn_mirror` primitive (row → `failed`) instead of re-queueing it to `ready`, and terminalize task-less stranded rows rather than re-queuing them. This converts "invisible wedge" into "honest terminal state" without ever creating a second owner, and it is the only path that would clear the busy-guard for the stranded classes on shipped-default installs. It is a new subsystem (task↔message↔instance correlation + a terminal-state vocabulary decision) and is out of scope here.

---

## Source-of-Decision Honest Audit

| Decision | Adopted from | Modified beyond gap doc? |
|----------|--------------|--------------------------|
| D1 (auto-resume) | gap doc §1 + §4 (implicit from RUNNING-row continuation); user directive | YES — gap doc did not explicitly contrast auto-resume vs park-and-report; resolved by inferring from user directive + prior-art design authority |
| D2 (G6.A amended + preflight-reuse + `connect_timeout`) | architect-recommendation §2, §4, §7 (A1 amendment) | YES — D2 was G6.B pre-fold; architect's seam-defect finding (constructor fails first at `api.py:562-568`) made the retry dead code; preflight + `connect_timeout` is the corrected lane |
| D3 (G1 defer) | gap doc §6 (G1.A); §7 NG-5 | NO |
| D4 (G3 park) | gap doc §6 (G3.B); §7 NG-3 | NO (architect A10 STRENGTHENS the PARK: durable one-shot disable bounds duplicate-fire to single re-emit) |
| D5 (G7 park) | gap doc §6 (G7.C → G7.A path); §7 NG-1 | NO |
| D6 (G4/5/8 runbook) | gap doc §6 (G4/5/8 runbook-only) | NO |
| D7 (envelope) | gap doc §5 + prior-art D2/D3 | NO (consistency with prior art; envelope still rides `daemon/api.py` neighborhood — placement amended to `:582` for G2 per A4) |
| D8 (phase boundaries) | gap doc §4 ranking + §6 recommended order | YES — gap doc did not enumerate phases; this plan partitions per (severity, surface, downstream dependency); architect A6 reaffirms P1-first + tightens P1 to ≈4 LOC |
| D9 (single kill-switch; `ENSEMBLE_INIT_RETRY` ELIMINATED per D16) | architect-recommendation §4 (A1 amendment) + recorded ruling 3 (D16) | YES — original was two switches; architect's A1 fold removed the G6.B helper, leaving the second switch with no feature to switch; collapsed to single `ENSEMBLE_HARD_REBOOT_BOOT_PASS` |
| D10 (non-goals honored) | gap doc §7 verbatim | NO (extended by D13 — task-less ghost class is the new NG-8) |
| D11 (G7 pre-flight) | gap doc §8 R-05 + project metadata | YES — gap doc mentions R-05 but did not prescribe the pre-flight; this plan resolves it |
| D12 (worktree) | prior-art Δ7 / D20 | NO (mirrored convention; A5 failure-injection check added on top) |
| D13 (task-less ghost class = NG-8 with MANDATORY tracked follow-up) | architect-recommendation §5.iv (GAP-NAMED) + recorded ruling 5 (D18) + both 2026-10-19 RCAs | YES — gap doc §7 had NG-1..NG-7; this is NG-8, architect-named; explicit non-goal of this plan with MANDATORY tracked follow-up commission (~45–60 LOC exceeds the ≈44 ceiling) |
| D14 (FOLD ratification meta-decision) | dispatcher's explicit FOLD instruction | YES — meta-decision: one canonical home per artifact; no SUPERSEDED stamps; the gap doc §9 gets a one-line pointer, not a rewrite |
| D15 (Phase-3 runbook drop-in example) | architect-recommendation §4 + recorded ruling 2 | YES — gap doc did not enumerate a delivery mechanism for existing installs; the drop-in is the canonical systemd idiom and the only path that reaches the live fleet (adopt-unit refuses clobber) |
| D16 (single kill-switch; `ENSEMBLE_INIT_RETRY` ELIMINATED) | architect-recommendation §4 (A1) + recorded ruling 3 | YES — mandated by the fold; fewer knobs = better; the preflight is the existing, better-shaped lane |
| D17 (`QUEUE_DISCARD_ON_STARTUP` default revisit = PARKED) | recorded ruling 4 + architect A3 (acceptance re-specified for discard semantics) | YES — gap doc did not address the shipped-default vs live-override divergence; parked until G2 follow-up commission + Phase 4 demo data point |
| D18 (D13 follow-up scope INCLUDES mission job-watcher boot behavior) | recorded ruling 5 + rcc mission `6b1630e7` RCA | YES — rcc RCA revealed the same "invisible to sweeps" class for mission-watchers; same selector design pattern works for both; D19 split rejected |
| D19 (G2's lane-6-enablement claim WITHDRAWN; the claim is narrowed to queue-accounting hygiene; the skip-guard stays) | architect C4 ruling (recorded ruling 6, reviewer-council finding C4 `006396b0`) | YES — the inherited contradiction between the ratified guard and the endorsed benefit is resolved by NARROWING THE CLAIM, not the code: the code is safe, the claim was false. The "D19 split" rejected under D18 was a different, never-created entry (the mission-job-watcher split) |

**Summary:** 6 of 18 decisions (D1, D2, D7, D8, D9, D11, D13, D14, D15, D16, D17, D18) add resolution beyond what the gap doc explicitly states — each in response to an unresolved question in §8 (Q-1..Q-5), the architect's amendments A1-A11, or the user's verbatim directive. The remaining 6 (D3, D4, D5, D6, D10, D12) adopt gap-doc §6/§7 recommendations verbatim. No gap-doc recommendation is overturned — the fold ADDS, never SUBTRACTS.