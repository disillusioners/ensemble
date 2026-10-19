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

## D2. G6 mechanism = G6.B (OPS template edit + boot retry hook + env-knob opt-in)

**Decision:** G6.B. Edit `scripts/systemd/ensemble-daemon.service:84-87` to add `After=postgresql.service` + `Wants=postgresql.service` (3-line addition); add bounded retry at `daemon/api.py:569` `manager.initialize()` neighborhood (3 attempts × 10s backoff, fail loud), env-knob `ENSEMBLE_INIT_RETRY=1` default OFF for dev; wire into lifespan envelope at the verified cluster.

**Rationale:**
- G6.A alone (`After=` only) races PG readiness when PG WAL-replay takes non-zero seconds (very common on hosts with large WAL or slow disks — the canonical install shape per `adopt-unit.sh`).
- G6.C (`Requires=postgresql.service`) regresses the operational shape: if PG is misconfigured, the daemon never starts, and the operator loses the "daemon up first to debug PG" affordance.
- G6.B = wait-and-retry = resolution of Q-1 (wait-and-retry, NOT strict-pg-prereq). The retry preserves day-1 dev experience (PG typically up before daemon) while remaining correct on the rare-but-real PG-not-yet-ready case.
- Env-knob default OFF preserves day-1 dev behaviour (the most common case in CI/dev); only hosts with PG-also-systemd-managed should opt in. Documented in runbook.

**Rejected alternatives:**
- **G6.A alone** — same unit edit, no retry helper. Saves ≤30 LOC Python but leaves a fragile race window; gap-doc §6 explicitly recommends against this on a wait-and-retry semantic.
- **G6.C (`Requires=postgresql.service`)** — wrong semantics for a multi-purpose host (dev installs often have a local PG up before unit start; demo installs don't always manage PG via systemd). Stricter than needed.

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

## D7. AutoContinue lifespan envelope = the canonical template for both new passes

**Decision:** Both Phase 1 (PG retry hook) and Phase 2 (MQ boot recovery) wire into the SAME `daemon/api.py:2361-2368` envelope pattern that AutoContinue uses — three-layer try/except (per-candidate, per-pass, lifespan-level), kill-switch env-knob opt-in, INFO log on success, WARNING log on per-candidate failure, never-wedge at the lifespan level.

**Rationale:**
- The envelope pattern is the proven defensive shape (gap-doc §5 row #1 reuse anchor; prior-art D2/D3 used this verbatim).
- Ensures both new passes participate in the kill-switch discipline (kill one off, the other keeps running).
- Reuses the operator-mental-model of the existing pass: log lines look the same, failure modes are the same, runbook entry reads are the same.

**Rejected alternative:** roll a new envelope — every new envelope is a new failure mode; violate the uniformity rule.

---

## D8. Phase boundaries

**Decision:**
- **Phase 0** = Worktree + import-resolution gate + env-fence audit (precondition; off the critical path of features).
- **Phase 1** = G6 (unit template edit + boot retry hook + lifespan wiring). **CRITICAL + OPS-CHEAP** — the gap-doc §4 ranks G6 critical because without it the boot can crash against PG readiness and undermine every other survival path. Phase 1 unblocks Phases 2-4 in the safest way.
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

## D9. Kill-switch names + default state (lane discipline)

**Decision:**
- `ENSEMBLE_INIT_RETRY=1` (Phase 1) — default OFF, opt-in per host. Off preserves day-1 dev behaviour.
- `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` (Phase 2) — default ON. Set `=0` to disable the MQ recovery pass. Mirrors `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` (`auto_continue_boot_pass.py:145-156`) — same shape, separate concern.

**Rationale:**
- Kill-switches with sane defaults are the operator's escape hatch. Naming convention: `<FEATURE>_..._ON_RESTART` mirrors the existing precedent.
- Two separate switches (PG retry / MQ recovery) lets an operator diagnose which pass is misbehaving.

**Rejected alternative:** single mega-switch — collides two failure modes, makes incident-response slower.

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

## Source-of-Decision Honest Audit

| Decision | Adopted from | Modified beyond gap doc? |
|----------|--------------|--------------------------|
| D1 (auto-resume) | gap doc §1 + §4 (implicit from RUNNING-row continuation); user directive | YES — gap doc did not explicitly contrast auto-resume vs park-and-report; resolved by inferring from user directive + prior-art design authority |
| D2 (G6.B) | gap doc §6 verbatim | NO |
| D3 (G1 defer) | gap doc §6 (G1.A); §7 NG-5 | NO |
| D4 (G3 park) | gap doc §6 (G3.B); §7 NG-3 | NO |
| D5 (G7 park) | gap doc §6 (G7.C → G7.A path); §7 NG-1 | NO |
| D6 (G4/5/8 runbook) | gap doc §6 (G4/5/8 runbook-only) | NO |
| D7 (envelope) | gap doc §5 + prior-art D2/D3 | NO (consistency with prior art) |
| D8 (phase boundaries) | gap doc §4 ranking + §6 recommended order | YES — gap doc did not enumerate phases; this plan partitions per (severity, surface, downstream dependency) |
| D9 (kill-switches) | prior-art `auto_continue_boot_pass.py:145-156` + kill-switch pattern at `auto_continue_boot_pass.py:145-156` | NO (mirrored convention) |
| D10 (non-goals honored) | gap doc §7 verbatim | NO |
| D11 (G7 pre-flight) | gap doc §8 R-05 + project metadata | YES — gap doc mentions R-05 but did not prescribe the pre-flight; this plan resolves it |
| D12 (worktree) | prior-art Δ7 / D20 | NO (mirrored convention) |

**Summary:** 9 of 12 decisions adopt gap-doc §6 recommendations verbatim; 3 decisions (D1, D8, D11) add resolution beyond what the gap doc explicitly states — each in response to an unresolved question in §8 (Q-1..Q-5) or to the user's verbatim directive. No gap-doc recommendation is overturned.
