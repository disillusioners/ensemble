# Plan Overview: Hard-Reboot Recovery — "Ensemble Continues After a Hard Machine/VM Power-Loss Reboot"

Date: 2026-10-19
Author: planner[v2] via plan-creation worker (Mode 1 — greenfield)
Branch: `feature/hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Worktree: `/home/nea/ensemble-src-wt-hard-reboot-recovery` (clean except untracked planning dir)
Evidence base: `.agents/shared/planning/hard-reboot-recovery/technical-analysis.md` (311 lines, verified; LEAD DRIFT corrections recorded there at §Context Summary)
Prior art (design authority for the existing boot pass): `.agents/shared/planning/auto-continue-running-after-restart/` (landed; reference for phase/task/test/AC/rollback structure and kill-switch shape)

---

## 1. Objective

After a hard machine/VM power-loss reboot — where the kernel SIGKILLs every process and the host loses power mid-flight — the ensemble daemon comes back into a state indistinguishable from a clean startup except that it transparently continues every `RUNNING` instance from its last LangGraph checkpoint, with `PAUSED` instances still parked, terminal instances still terminal, and the bus/queue/checkpoint secrets already wired.

**Testable completion sentence:** a power-loss-struck host that restarts brings the daemon back within ~14s (systemd `RestartSec=10` + boot), completes engine-open within ≤30s of PG readiness (env-knob `ENSEMBLE_INIT_RETRY=1` default OFF), rehydrates the existing AutoContinue + DependencyBus + StaleTaskRecovery passes without code change, and drains any stuck `message_queue` `processing` rows via the new `recover_stuck_messages_on_startup` service — proven by the Phase-3 demo E2E.

---

## 2. Scope

### In Scope (mapped to gap doc §4)

- **One OPS template edit** at `scripts/systemd/ensemble-daemon.service:84-87` — add `After=postgresql.service` + `Wants=postgresql.service` (G6.A, 5 LOC).
- **One new Python helper** `_init_manager_with_pg_retry` at `daemon/api.py:569` neighborhood — bounded retry (3×10s, fail loud), env-knob opt-in, ≥−1 homonym with the existing manager.initialize call (G6.B, ≤30 LOC Python).
- **One new boot pass** `daemon/services/message_queue_boot_recovery.py` — calls the existing selector `find_stuck_messages` (`daemon/repositories/message_queue/repository.py:385-423`), idempotent on reboot-loop, env-knob opt-in (G2.A, ≤40 LOC Python).
- **Three-layer never-wedge envelope** — verbatim port from `auto_continue_boot_pass.py:230-494` lifecycle pattern at `daemon/api.py:2350` neighborhood, kill-switch `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON, `=0` disables (mirror of `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` at `auto_continue_boot_pass.py:145-156`).
- **One NEW runbook** `docs/runbooks/hard-reboot-recovery.md` — per-state RTO SLA table (Q-2 resolution), stuck-message queue duplicate-fire window note (G3.B/Q-3), KMS_LANE-1 drain advisory (G7.C/Q-4), PAUSED reconciliation note (G5), no-checkpoint orphan triage (G4), and the hard-reboot drill (Q-5 — kill -9 + reboot, mirror `tests/test_busy_daemon_path.sh`).
- **Tests** — unit + integration per repo conventions; pack SPECs registered to the tester (PACKS edit DEFERRED to implementation lane per prior-art precedent).
- **Phase-3 demo E2E** — REAL environment, NON-live, kill -9 the daemon + reboot the host, observe the daemon come back and the existing AutoContinue + bus + STR + new MQ recover fire.

### Out of Scope (per gap doc §7 non-goals, honored)

- **KMS_LANE-1 deprecation/removal** (NG-1) — `secrets` P2 LANDED at v0.23.0 STAGING-ONLY awaiting user nonce ceremony; open as follow-up commission post-promote. LANE-2 covers day-1 BYOK chain today.
- **`WatchdogSec` wiring** (NG-2, P4 deferred by design) — no `sd_notify` wired; not a hard-reboot gap.
- **G3 scheduler catch-up re-fire suppression** (NG-3) — parked until a duplicate fire is reported.
- **In-flight tool-call at-least-once semantics** (NG-4) — LangGraph invariant; the user wants "continues" not "every micro-step replays".
- **G1 JobRecovery alive-row double-gap deletion** (NG-5) — AutoContinue covers the same row; safe to defer.
- **G5 Task↔JobItem PAUSED reconciliation** (NG-6) — pause-time concern, orthogonal.
- **`RequiresMountsFor=` templating** (NG-7) — install-dir data path is host-dependent; OPS drop-in is the right shape, not a template edit.
- **Checkpoint retry-budget for hard-reboot cases** — STR's `boot+10min` clamp is the existing backstop.
- **PACKS.md edit** — tester-owned; this plan registers pack SPECS only.

---

## 3. Current-State Inventory (CONDENSED — link to gap doc for full table)

Full 38-row inventory: `technical-analysis.md` §2. Headline counts:

| Class | Rows | Note |
|-------|-----:|------|
| **Reuse as-is** | 30 | systemd unit Restart/MALLOC/timeout/MQ/STR/AutoContinue/bus/RDRS/checkpoint_adapter/discard_on_startup/ExecutionGate/JobLockManager/SnapshotRepository |
| **Partial / audit-only** | 2 | DB recovery (live after PG WAL replay); env sourcing (spliced at adopt-time, idempotent) |
| **Broken → fixed in this plan** | 1 (G2) | `find_stuck_messages` dead code — get a production caller |
| **Broken → parked** | 3 (G1, G3, G7) | JobRecovery alive-row; scheduler RAM marker; KMS_LANE-1 drain |
| **Gap → fixed in this plan** | 1 (G6) | PG ordering in unit template + boot retry hook |
| **GAP → runbook only** | 3 (G4, G5, G8) | No-checkpoint orphans; PAUSED reconciliation; RTO vs STR `boot+10min` |

**Headline: ~70% of hard-reboot continuity already in place (gap doc §1 Executive Verdict).** This plan DOES NOT rebuild supervision (systemd already supervises per `adopt-unit.sh`); it adds two narrow plug-ins (PG-ordering unit edit + boot retry hook + dead-code selector caller) on top of the existing AutoContinue + bus + STR + checkpoint_adapter spine.

---

## 4. Mechanism Per Gap (G1–G8, adopted from §6 recommendations)

| Gap | Severity | Recommendation | Why | Surface |
|-----|----------|----------------|-----|---------|
| **G6** Unit PG ordering + boot retry hook | CRITICAL | **G6.B** — OPS edit + bounded retry hook | G6.A alone races PG readiness on WAL-replay hosts; G6.C (`Requires=`) regresses the "daemon comes up first to debug PG" operator shape; G6.B = wait-and-retry, the user's Q-1 answer | 5 LOC template + ≤30 LOC Python + ≤50 LOC test |
| **G1** JobRecovery alive-row | HIGH | **G1.A** — DEFER (NG-5) | AutoContinue covers the same row at the task layer; deletion is fragile (operators rely on log line) | −8 LOC parked |
| **G3** Scheduler one-shot RAM marker | HIGH | **G3.B** — DEFER (NG-3) | Duplicate-fire window is single-emit; user has not reported; open later if reported | 0 LOC now; ≤45 LOC + ≤20 test later |
| **G7** KMS_LANE-1 in-memory drain | HIGH | **G7.C** — PARK (NG-1) | LANE-2 covers day-1 BYOK; Secrets P2 LANDED at v0.23.0 STAGING-ONLY; deprecation WARNING cycle = post-promote follow-up | 0 LOC; ≤10 LOC runbook |
| **G2** `find_stuck_messages` dead code | MEDIUM | **G2.A** — new boot-time caller | Selector is correct & idempotent; just lacks a production caller; ride the same lifespan envelope pattern | ≤40 LOC service + ≤20 LOC test |
| **G4** No-checkpoint orphans | MEDIUM | **Runbook only** | STR's `boot+10min` clamp handles recovery; operators need a triage playbook | 0 LOC code; runbook entry |
| **G5** Task↔JobItem PAUSED reconciliation | MEDIUM | **Runbook only** | Pause-time concern; AutoContinue + STR both already exclude PAUSED instances | 0 LOC code; runbook entry |
| **G8** RTO vs STR `boot+10min` clamp | LOW | **Runbook only** — per-state SLA documented | Per-state RTO is a UX/document concern, not a code gap | 0 LOC code; ≤30 LOC runbook table |

Adjudication rule for all seven: adopt the §6 recommendation as the working decision, do NOT re-litigate. See `decisions.md` for explicit ratification + Q-1..Q-5 resolutions.

---

## 5. Reusing-What Mapping (from §5 — anchors for new code)

| Existing mechanism | Location | Reuse as what | New-code touch |
|---|---|---|---|
| `Restart=on-failure` + `RestartSec=10` | `scripts/systemd/ensemble-daemon.service:94-95` | Hard-reboot revival (no change) | none |
| AutoContinue lifespan envelope | `daemon/api.py:2361-2368` + `auto_continue_boot_pass.py:230-494` | Three-layer try/except + CAS pattern for ANY new boot-time reconciler | pattern, not primitive |
| `task.auto_continued_at` CAS | `daemon/repositories/task/repository.py:1066-1191` | Durable serializer / three-layer never-wedge for new passes | pattern, not primitive |
| `find_stuck_messages` selector | `daemon/repositories/message_queue/repository.py:385-423` | Dead-code reuse: just add a caller; the selector itself is correct | caller only |
| `_schedule_explicit_handle_resume` | `daemon/manager.py:10915-11443` (called at `:11775`) | Continue primitive for any "resume a frozen turn" pass | pattern |
| `DependencyBus.start()` re-arm | `daemon/services/dependency_bus.py:1499-1560` | Bus survives restart (DB-durable C1 dedup) | none |
| `StaleTaskRecovery.recover_on_startup` | `daemon/services/stale_task_recovery.py:752-799` + boot_epoch clamp `:764-767` | Age-gated (boot+10min) steady-state backstop | none |
| `discard_on_startup` | `daemon/manager.py:843` + `:1010-1059` + `daemon/api.py:660-676` | Env-poison class already closed | none |
| `boot_epoch` capture | `daemon/services/boot_epoch.py:91-133` + capture at `daemon/api.py:580-582` | Temporal anchor for any boot pass | none |
| `RDRS` lane framework | `daemon/services/report_delivery_recovery.py:719-769` | Pattern for "defer + re-run + stuck-wake" | pattern |
| `MaintenanceApiService` boot-CAS pattern | `daemon/services/maintenance_boot_sweep.py:39-60` (Pattern A) | "Idempotent UPDATE, zero rows on second call" | pattern |
| `_pending_doomed_ids` deferred DELETE | `daemon/api.py:660-665` | Crash-safe deferred cleanup | pattern |
| Unit-template `__ENV_LINES__` token | `scripts/upgrade/adopt-unit.sh:84-108` | Re-splice on every adopt; OPS edit propagates | template edit |
| `MALLOC_ARENA_MAX=2` directive | `scripts/systemd/ensemble-daemon.service:106` | Resource-cost-control O1 (no change) | none |

**Top-3 reuse anchors for the developer:**

1. **AutoContinue lifespan envelope at `daemon/api.py:2361-2368`** — the existing pass is the template for any new boot-time reconciler (kill-switch, CAS, three-layer never-wedge).
2. **`scripts/systemd/ensemble-daemon.service` template at `scripts/systemd/ensemble-daemon.service:84-108`** — OPS-only edit for G6; `adopt-unit.sh` re-splices per install.
3. **`find_stuck_messages` selector at `daemon/repositories/message_queue/repository.py:385-423`** — already-tested dead code; just need a production caller (one new service, ≤40 LOC).

---

## 6. Estimated New-Code Surface (Per Phase — ≤315 LOC ceiling held)

| Phase | What | Python LOC | Test LOC | Ops/Runbook LOC |
|-------|------|-----------:|---------:|----------------:|
| **0** | Worktree + import-resolution gate + env-fence audit | 0 | ≤10 (fixture) | 0 |
| **1** | **G6** — unit template + PG retry helper + boot hook wiring | ≤30 | ≤50 | 5 (template) |
| **2** | **G2** — `message_queue_boot_recovery.py` service + lifespan envelope + kill-switch | ≤40 | ≤20 | 0 |
| **3** | Runbook: per-state RTO SLA + drill procedure + LANE-1 advisory + PAUSED note + scheduler duplicate-fire note | 0 | ≤10 (drill smoke) | ≤80 (new runbook) |
| **4** | Demo E2E — kill -9 the daemon + reboot the host, observe AutoContinue + bus + STR + G2 + G6 retry all fire | 0 | ≤80 (e2e shell + pytest) | 0 |
| **TOTAL NEW SURFACE (in-scope)** | — | **≤70** | **≤160** | **≤85** |
| **PARKED (post-promote follow-up commissions)** | G1 deletion −8 / G3 ≤45+≤20 / G7 deprecate-cycle ≤40+≤80+≤100 | ≤77 (later) | ≤100 (later) | ≤10 (later) |

**Ceiling check:** tech-analysis §9 quotes ~305 LOC across in-scope recommendations; we expand slightly to ≤315 by adding the Phase-0 worktree gate (≤10 LOC fixture) and the Phase-4 e2e harness (≤80 LOC). Both are within the `try to REUSE; great code = less code` directive and ride existing conventions. The parked follow-ups are NOT counted against the in-scope ceiling — they ship in their own commissions after this one lands.

**Per-file touch list (complete, lean):**

| File | Nature of change |
|---|---|
| `scripts/systemd/ensemble-daemon.service:84-87` | +3 line edit (G6.A: add `After=postgresql.service` + `Wants=postgresql.service`) |
| `daemon/api.py:569` neighborhood | +≤30 LOC helper `_init_manager_with_pg_retry` (G6.B retry hook) + ≤20 LOC lifespan envelope wiring + INFO log |
| `daemon/services/message_queue_boot_recovery.py` | NEW — `recover_stuck_messages_on_startup()` service (G2.A) |
| `daemon/api.py:2350` neighborhood | +≤20 LOC lifespan envelope wiring for the MQ pass (Phase 2) + INFO log |
| `tests/unit/test_pg_init_retry.py` | NEW — env-knob ON, mock PG connect failure then success; env-knob OFF unchanged |
| `tests/unit/services/test_message_queue_boot_recovery.py` | NEW — idempotency + selector wiring + boot_epoch clamp |
| `tests/test_hard_reboot_smoke.sh` (or `tests/test_hard_reboot_e2e.py`) | NEW — Phase 4 kill-9 + reboot demo |
| `tests/conftest_worktree.py` (or ext.) | NEW (mirror prior-art Δ7) — fails LOUDLY if `import daemon` resolves outside the worktree |
| `docs/runbooks/hard-reboot-recovery.md` | NEW — per-state RTO table + drill + LANE-1 advisory + scheduler note + PAUSED note (≤80 LOC) |
| `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | NEW (mirror prior-art Δ7) — worktree path + branch receipt |

No changes to: AutoContinue, DependencyBus, StaleTaskRecovery, discard_on_startup, boot_epoch, checkpoint_adapter, scheduler, KMS_LANE-1, KMS_LANE-2, the existing `find_stuck_messages` selector body, or any test_pack that already covers them.

---

## 7. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| **R-01** | PG ordering race on custom install paths (PG NOT systemd-managed) | Low | High | G6.B retry hook + runbook env-knob; document the drop-in shape in runbook |
| **R-02** | `_init_manager_with_pg_retry` retry collides with discard_on_startup CAPTURE_PHASE — retries while CAPTURE is in flight could double-capture | Low | Medium | helper runs BEFORE manager.initialize() returns; CAPTURE is the FIRST thing inside initialize; ensure retry surround is the OUTERMOST try; G6.B review checklist pins this |
| **R-03** | `recover_stuck_messages_on_startup` collides with in-flight MessageQueue retry loops (a row is mid-write when boot kicks the selector) | Low | Medium | selector already uses `with self.engine.begin()` atomic transition; runbook note warns operators to NOT manually edit MQ rows during a reboot; selector's `process_timeout = MESSAGE_TIMEOUT_SECONDS=3600` per `:385-423` prevents premature reaping |
| **R-04** | G6 retry hook regresses dev experience (PG down on dev → 30s hang) | Low | Low | env-knob `ENSEMBLE_INIT_RETRY` default OFF preserves day-1 behaviour on dev; documented in runbook |
| **R-05** | Systemd `After=postgresql.service` rejects on a non-systemd-PG install (custom PG location) | Low | Medium | adopt-unit.sh already handles per-install tweaks; runbook drop-in example (one stanza) covers this |
| **R-06** | v0.23.0 Secrets promote flips the G7 parking into a hot commission mid-flight | Very Low | Medium | plan worker queries the staged version before opening the G7 follow-up (see decisions.md D11) |

(Condensed from gap doc §8 risks R-01..R-06; this plan's in-scope surface only.)

---

## 8. Phased Rollout

Each phase is independently shippable; subsequent phases can be reordered or skipped at the dispatcher's call.

| Phase | Name | Objective | Gaps closed | New LOC | Verification gate |
|-------|------|-----------|------------|--------:|-------------------|
| **0** | **Worktree + import-resolution gate + env-fence audit** | Implementation + E2E lanes run in `/home/nea/ensemble-src-wt-hard-reboot-recovery` with `import daemon` resolving INSIDE the worktree; env-fence confirms `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079` (per shared-meta-kv claim) | none (precondition) | ≤10 | `python -c "import daemon; print(daemon.__file__)"` resolves inside WT; `tests/conftest_worktree.py` fixture green; `git status --short` shows ONLY the planning dir |
| **1** | **G6 — PG unit ordering + boot retry hook + lifespan wiring** | The unit template orders daemon after PG; boot survives a brief PG-startup window via bounded retry helper; lifespan envelope-try wires the helper at the verified placement | G6 | 35 LOC (≤30 Python + ≤5 template + ≈0 wiring) | unit test `test_pg_init_retry.py` green; full `tests/unit -q` green; manual: bring PG up AFTER daemon unit start, observe daemon retries; kill -9 + restart UNIT (NOT host) to verify boot without host power-loss simulation |
| **2** | **G2 — `message_queue_boot_recovery` boot pass + lifespan envelope + kill-switch** | The dead-code `find_stuck_messages` selector gets a production caller; idempotent on reboot-loop; env-knob `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON | G2 | ≤60 LOC (≤40 Python + ≤20 test + ≈0 wiring) | unit test green; integration: stuck MQ row + boot → row reset to `ready`; second boot → no-op; kill-switch `=0` skips |
| **3** | **Runbook + ops surface (G4, G5, G7, G8 documentation)** | `docs/runbooks/hard-reboot-recovery.md` ships with: per-state RTO table (Q-2), hard-reboot drill procedure (Q-5), KMS_LANE-1 advisory (Q-4 / G7.C), G3 scheduler duplicate-fire window note (Q-3), G5 PAUSED reconciliation note, G4 no-checkpoint triage | G4, G5, G7, G8 (all runbook-only) | ≤90 LOC (≤80 new runbook + ≤10 drill smoke) | runbook review; drill smoke passes (kill -9 the daemon, verify bus + STR + new MQ pass all fire per runbook checklist) |
| **4** | **Demo E2E on real environment** | User-mandated REAL-environment proof: NON-live host, kill -9 the daemon + reboot the host, observe the daemon come back, observe AutoContinue + bus + STR + G2 MQ recovery + G6 retry all fire; capture evidence bundle | (integration test for Phases 1-3) | ≤80 LOC e2e harness | demo PASS; evidence bundle archived to `.agents/tester/RESULTS/2026-10-19-hard-reboot-recovery-e2e.md` |
| **TOTAL** | — | — | G2, G6 closed in scope; G1/G3/G7 deferred (post-promote follow-up commissions); G4/G5/G8 runbook-only | **≤315 LOC** | four-gate green: unit + integration + runbook review + demo E2E |

---

## 9. Assumptions & Open Questions Resolved

| Assumption / Question | Source | Resolution |
|------------------------|--------|------------|
| Auto-resume-everything vs park-and-report | user goal verbatim *"if our ensemble can continue after a hard reboot"* + prior-art design authority | **Auto-resume** (decided in `decisions.md` D1) |
| Q-1: strict PG vs wait-and-retry | gap doc §8 | wait-and-retry (G6.B) — see decisions.md D2 |
| Q-2: per-state RTO SLA | gap doc §8 | documented per-state in runbook (Phase 3) — see decisions.md D3 |
| Q-3: G3 scheduler catch-up semantic | gap doc §8 | parked (G3.B), documented in runbook — see decisions.md D4 |
| Q-4: G7 KMS_LANE-1 deprecation | gap doc §8 | runbook advisory only (G7.C), follow-up commission post-promote — see decisions.md D5 |
| Q-5: hard-reboot drill | gap doc §8 | YES, runbook entry + Phase 3 smoke — see decisions.md D6 |
| G6 mechanism choice | gap doc §6 | G6.B (OPS + retry hook) — see decisions.md D7 |
| Phase boundaries | per gap-doc severity + criticality ranking + dep coupling | Phase 1=G6 critical; Phase 2=G2 dead-code caller; Phase 3=runbook; Phase 4=demo E2E — see decisions.md D8 |

---

## 10. Lockstep Rules (mirrored from prior art)

- **Kill-switch:** `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON, `=0` disables (Phase 2). `ENSEMBLE_INIT_RETRY` default OFF, opt-in (Phase 1).
- **Worktree gate:** `pytest` fixture fails LOUDLY if `import daemon` resolves outside `/home/nea/ensemble-src-wt-hard-reboot-recovery` — copied from prior-art Δ7 / D20.
- **Three-layer never-wedge envelope:** verbatim port from `auto_continue_boot_pass.py:230-494` — at the unit-of-work level (per candidate), at the pass level, and at the lifespan level.
- **No new column / no migration:** G2 reads via the existing selector; G6 retry is opt-in via env knap (no schema change). Honours the **less code** user directive.
- **Phase 0 first, last, and always:** Worktree-claim file is the receipt gate before any code commit.

---

## References

- Evidence base: `.agents/shared/planning/hard-reboot-recovery/technical-analysis.md` (311 lines, verified; LEAD DRIFT corrections recorded at §Context Summary)
- Prior art (design authority + structure mirror): `.agents/shared/planning/auto-continue-running-after-restart/` (`plan-overview.md` 149 lines, `phase1-plan.md` 43 lines, `decisions.md` 322 lines)
- AutoContinue boot pass: `daemon/services/auto_continue_boot_pass.py:230-494` + `daemon/api.py:2361-2368`
- CAS durable serializer: `daemon/repositories/task/repository.py:1066-1191`
- Unit template: `scripts/systemd/ensemble-daemon.service:84-108`
- MQ dead-code selector: `daemon/repositories/message_queue/repository.py:385-423`
- PG retry hook placement: `daemon/api.py:569` (engine open inside manager.initialize)
- Lifespan envelope pattern: `daemon/api.py:2302-2396` (UpgradeJournalSweepService envelope-try)
