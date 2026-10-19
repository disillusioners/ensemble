# Plan Overview: Hard-Reboot Recovery — "Ensemble Continues After a Hard Machine/VM Power-Loss Reboot"

Date: 2026-10-19
Author: planner[v2] via plan-creation worker (Mode 1 — greenfield)
Branch: `feature/hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Worktree: `/home/nea/ensemble-src-wt-hard-reboot-recovery` (clean except untracked planning dir)
Evidence base: `.agents/shared/planning/hard-reboot-recovery/technical-analysis.md` (311 lines, verified; LEAD DRIFT corrections recorded there at §Context Summary)
Prior art (design authority for the existing boot pass): `.agents/shared/planning/auto-continue-running-after-restart/` (landed; reference for phase/task/test/AC/rollback structure and kill-switch shape)

---

## 1. Objective

After a hard machine/VM power-loss reboot — where the kernel SIGKILLs every process and the host loses power mid-flight — the ensemble daemon comes back into a state indistinguishable from a clean startup except that it transparently continues every `RUNNING` instance from its last LangGraph checkpoint, with `PAUSED` instances still parked, terminal instances still terminal, and the bus/queue/checkpoint secrets already wired. The PG-wait burden rides the existing `_boot_db_preflight` + launcher exit-75 lane (no new retry helper, no new env-knob); `Wants=postgresql.service` is deliberately dropped (RAM-cost hazard on remote-PG hosts); and the task-less RUNNING-instance class is named D13 with a MANDATORY tracked follow-up commission (both 2026-10-19 RCAs — leader turn `897011bb` and rcc mission `6b1630e7` — are this class; a third occurrence is plausible before the follow-up lands).

**Testable completion sentence:** a power-loss-struck host that restarts brings the daemon back within ~14s (systemd `RestartSec=10` + boot), runs through the existing `_boot_db_preflight` exit-75 lane (budget-exempt, infinite) until PG accepts connections (now bounded by `connect_timeout=BOOT_DB_TIMEOUT_S=10` — `daemon/repositories/factory.py:232-284` previously had only `pool_pre_ping`, so a firewall-DROP was hanging 75–130s per attempt; one-line addition), rehydrates the existing AutoContinue + DependencyBus + StaleTaskRecovery passes without code change, and clears any stuck `message_queue` `processing` rows via the new `recover_stuck_messages_on_startup` service — proven by the Phase-4 demo E2E.

---

## 2. Scope

### In Scope (mapped to gap doc §4, post-architect-fold A1)

- **One OPS template edit** at `scripts/systemd/ensemble-daemon.service:84-87` — add `After=postgresql.service` only (NO `Wants=`, NO `Requires=`); plus 2 template comment lines stating the host-dependent intent and pointing to the runbook drop-in (G6.A, ≤8 LOC including the 2 template comments).
- **No new Python helper** — the planned `_init_manager_with_pg_retry` is REMOVED (architect focus 2 amendment: constructor fails first at `api.py:562-568`, so the retry never fires; preflight-reuse + `connect_timeout` is the corrected lane).
- **One LOC edit** in `daemon/repositories/factory.py:232-284` — add `connect_timeout=BOOT_DB_TIMEOUT_S=10` to `create_postgres_engine` (so firewall-DROP doesn't hang 75–130s per attempt; the existing preflight at `__main__.py:143-247` is the canonical PG-wait lane).
- **Reuse `_boot_db_preflight` + launcher exit-75 track** at `daemon/__main__.py:143-247` (both entry paths: `run_app.py:70` frozen binary; `__main__.py:467` `python -m daemon`); `BOOT_DB_TIMEOUT_S=10` per `constants.py:70`; launcher maps 75 → 5–60s capped backoff, **budget-exempt, infinite** per `launcher.sh:1340-1346,1576-1590` — the canonical "wait for a transient dependency" lane.
- **One new boot pass** `daemon/services/message_queue_boot_recovery.py` — guarded `UPDATE message_queue SET status='ready', processing_started_at=NULL WHERE message_id=:id AND status='processing'` per row, with boot-epoch-relative age predicate `status='processing' AND (last_activity_at IS NULL OR last_activity_at < :boot_epoch)`, skip rows whose `instances.status IN ('terminated','completed','error','failed')` (revive hazard — mirroring `task/repository.py:1150-1156`), skip rows with a RUNNING/PAUSED task (AutoContinue owns them); idempotent on reboot-loop, env-knob opt-in (G2.A amended, ≤40 LOC Python + ≤30 LOC test).
- **Three-layer never-wedge envelope** — verbatim port from `auto_continue_boot_pass.py:230-494` lifecycle pattern at `daemon/api.py:582` neighborhood (post-`capture_boot_epoch` + pre-`setup_worker_pool` — NOT beside AutoContinue at `:2350`; see A4 placement amendment), kill-switch `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON, `=0` disables (mirror of `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` at `auto_continue_boot_pass.py:145-156`).
- **One NEW runbook** `docs/runbooks/hard-reboot-recovery.md` — per-state RTO SLA table (Q-2 resolution), stuck-message queue duplicate-fire window note (G3.B/Q-3), KMS_LANE-1 drain advisory (G7.C/Q-4), PAUSED reconciliation note (G5), no-checkpoint orphan triage (G4), the hard-reboot drill (Q-5), AND the ready-to-ship drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) — the only delivery mechanism that reaches existing installs (adopt-unit.sh:537 refuses template clobber; the unit-template edit alone is illusory on the live fleet), AND the D13 task-less ghost triage query, AND the bus-start check, AND the pause-durability window, AND the NG-4 double-side-effect window.
- **Tests** — unit + integration per repo conventions; pack SPECs registered to the tester (PACKS edit DEFERRED to implementation lane per prior-art precedent).
- **Phase-4 demo E2E** — REAL environment, NON-live, kill -9 the daemon + reboot the host, observe the daemon come back and the existing AutoContinue + bus + STR + new MQ recover fire; amended per A3 to assert `get_stats()["processing_count"] == 0` post-boot + the busy-guard clears (NOT "the message was re-delivered" — delivery is task-driven only, see `repository.py:138-150`); AND a fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) because the NULL-`last_activity_at` fixture alone would mask §3.3 of the architect's review.

### Out of Scope (per gap doc §7 + architect's D13, honored)

- **D13 — Task-less RUNNING-instance class** (NG-8, architect-named) — `find_auto_continue_candidates` roots on `task` (`task/repository.py:1128-1131`); a RUNNING instance with no task row is invisible to AutoContinue, STR, all 6 RDRS lanes, JobRecovery, and the bus sweeps. `reconcile_turn_mirror` *detects* the class at DEBUG (`task/repository.py:2255-2281`) but is not a boot sweep. **MANDATORY tracked follow-up commission** (instance-rooted selector + checkpoint probe per candidate + resume-vs-park-vs-terminate decision ≈ 45–60 LOC, exceeds this plan's ceiling and risks false-resuming design-legal orphans — worse than a frozen instance). Both 2026-10-19 RCAs (leader turn `897011bb`; rcc mission `6b1630e7` task-less ghost governor) are this class. Runbook triage query shipped: `SELECT ... FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.id AND t.status IN ('pending','running','paused'))`. D13 scope **INCLUDES** mission job-watcher boot behavior — same "invisible to sweeps" class.
- **KMS_LANE-1 deprecation/removal** (NG-1) — `secrets` P2 LANDED at v0.23.0 STAGING-ONLY awaiting user nonce ceremony; open as follow-up commission post-promote. LANE-2 covers day-1 BYOK chain today.
- **`WatchdogSec` wiring** (NG-2, P4 deferred by design) — no `sd_notify` wired; not a hard-reboot gap.
- **G3 scheduler catch-up re-fire suppression** (NG-3) — parked until a duplicate fire is reported. Strengthens the PARK decision: the durable one-shot disable (per architect's A10 amplification) means a duplicate-fire hazard on hard reboot is bounded to a single re-emit; the destructive-task class is small; the per-`run_at` row is the future fix, ≤45 LOC + ≤20 test if reported.
- **In-flight tool-call at-least-once semantics** (NG-4) — LangGraph invariant; the user wants "continues" not "every micro-step replays". MCP stdio-children kill (orphaned children of the MCP pool on hard reboot) is the related NG-4 sub-class: documented in the runbook, not fixed in code.
- **G1 JobRecovery alive-row double-gap deletion** (NG-5) — AutoContinue covers the same row; safe to defer.
- **G5 Task↔JobItem PAUSED reconciliation** (NG-6) — pause-time concern, orthogonal.
- **`RequiresMountsFor=` templating** (NG-7) — install-dir data path is host-dependent; the runbook drop-in (D15) is the right shape, not a template edit.
- **`_init_manager_with_pg_retry`** (architect-amended) — REMOVED entirely. The canonical PG-wait lane (`_boot_db_preflight` + exit-75) is the correct reuse; the constructor-first DB I/O at `api.py:562-568` would have made the planned retry dead code.
- **`ENSEMBLE_INIT_RETRY` env-knob** (architect-amended) — ELIMINATED entirely. Per D16: single kill-switch adopted; fewer knobs = the mandate. The preflight is the better-shaped lane; no new env-knob.
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
| **Gap → fixed in this plan** | 1 (G6) | PG ordering via `After=` in unit template + existing `_boot_db_preflight`/exit-75 lane + `connect_timeout` |
| **GAP → runbook only** | 3 (G4, G5, G8) | No-checkpoint orphans; PAUSED reconciliation; RTO vs STR `boot+10min` |

**Headline: ~70% of hard-reboot continuity already in place (gap doc §1 Executive Verdict).** This plan DOES NOT rebuild supervision (systemd already supervises per `adopt-unit.sh`); it adds two narrow plug-ins (PG-ordering unit edit + `connect_timeout` + a new MQ recovery service) on top of the existing AutoContinue + bus + STR + checkpoint_adapter spine.

**File-lock-durability and tmp_images prose (A11):** the file-lock primitive (used by the daemon's compound-request handling and the atomic-write path) is restart-DURABLE by construction — locks are released on process death by the OS; no in-RAM state needs rehydration. The `tmp_images` substrate (charter chart images, OD plugin renders, the agent `image_save` transport) is on-disk + sidecar; the daemon's in-RAM ID→path map is rebuilt on the first store query post-boot (no WAL replay needed). Both subsystems survive hard reboot with zero continuity work — listed here for completeness because the Architect's A11 explicitly called them out as canonical row families that the plan should acknowledge even if no code touches them.

---

## 4. Mechanism Per Gap (G1–G8, adopted from §6 recommendations)

| Gap | Severity | Recommendation | Why | Surface |
|-----|----------|----------------|-----|---------|
| **G6** Unit PG ordering via `After=` (preflight/exit-75 reuse + `connect_timeout`; no in-lifespan helper) | CRITICAL | **G6 post-fold (architect A1):** `After=postgresql.service` ONLY in unit template (no `Wants=` per R-A5, no `Requires=` per D2) + existing `_boot_db_preflight` at `__main__.py:143-247` (both entry paths: `run_app.py:70` + `__main__.py:467`) + launcher exit-75 budget-exempt infinite backoff (`launcher.sh:1340-1346,1576-1590`) + `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `create_postgres_engine` (`daemon/repositories/factory.py:232-284`) | G6.A alone would race PG readiness on WAL-replay hosts; G6.C (`Requires=`) regresses the "daemon up first to debug PG" affordance; the architect amendment chose the **existing preflight lane** over a new in-lifespan helper (D2/D9/D16) because the constructor fails first (engine open comes after construction — any in-lifespan helper would be one call too late) and the exit-3 exhaustion path was a regression. No new env-knob. | ~5 LOC template (3 edit + 2 comments) + 1 LOC Python (`connect_timeout`) + ≤50 LOC test |
| **G1** JobRecovery alive-row | HIGH | **G1.A** — DEFER (NG-5) | AutoContinue covers the same row at the task layer; deletion is fragile (operators rely on log line) | −8 LOC parked |
| **G3** Scheduler one-shot RAM marker | HIGH | **G3.B** — DEFER (NG-3) | Duplicate-fire window is single-emit; user has not reported; open later if reported | 0 LOC now; ≤45 LOC + ≤20 test later |
| **G7** KMS_LANE-1 in-memory drain | HIGH | **G7.C** — PARK (NG-1) | LANE-2 covers day-1 BYOK; Secrets P2 LANDED at v0.23.0 STAGING-ONLY; deprecation WARNING cycle = post-promote follow-up | 0 LOC; ≤10 LOC runbook |
| **G2** `find_stuck_messages` dead code | MEDIUM | **G2.A** — new boot-time caller (amended per A3/A9) | Selector is correct; needs a production caller + **skip-guard IN-list** per A9 (`instances.status IN ('terminated','completed','error','failed')` mirroring `task/repository.py:1150-1156`; revive hazard) + **D17 discard-on-startup conditioning** (the `processing_count == 0` + busy-guard-clears assertion holds only on installs with `QUEUE_DISCARD_ON_STARTUP=true`); lifespan envelope at `api.py:582` (A4); idempotent on reboot-loop; boot-epoch-relative age predicate | ≤40 LOC service + **60–100 test (SOFT per A7)** |
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
| `_schedule_explicit_handle_resume` | `daemon/manager.py:11963` | Continue primitive for any "resume a frozen turn" pass | pattern |
| `DependencyBus.start()` re-arm | `daemon/services/dependency_bus.py:1499-1560` | Bus survives restart (DB-durable C1 dedup) | none |
| `StaleTaskRecovery.recover_on_startup` | `daemon/services/stale_task_recovery.py:752-799` + boot_epoch clamp `:764-767` | Age-gated (boot+10min) steady-state backstop | none |
| `discard_on_startup` | `daemon/manager.py:843` + `:1010-1059` + `daemon/api.py:660-676` | Env-poison class already closed | none |
| `boot_epoch` capture | `daemon/services/boot_epoch.py:91-133` + capture at `daemon/api.py:580-582` | Temporal anchor for any boot pass | none |
| `RDRS` lane framework | `daemon/services/report_delivery_recovery.py:719-769` | Pattern for "defer + re-run + stuck-wake" | pattern |
| `MaintenanceApiService` boot-CAS pattern | `daemon/services/maintenance_boot_sweep.py:39-60` (Pattern A) | "Idempotent UPDATE, zero rows on second call" | pattern |
| `_pending_doomed_ids` deferred DELETE | `daemon/api.py:660-665` | Crash-safe deferred cleanup | pattern |
| Unit-template `__ENV_LINES__` token | `scripts/upgrade/adopt-unit.sh:84-108` | Re-splice on every adopt; OPS edit propagates | template edit |
| `MALLOC_ARENA_MAX=2` directive | `scripts/systemd/ensemble-daemon.service:106` | Resource-cost-control O1 (no change) | none |
| **Question-pack boot rehydration** (NEW row, A11) | Reuse anchor TBD by implementation lane — the question-pack subsystem's DB-durable state survives restart (C1-style marker in DB, not RAM); on hard reboot the question-pack state rehydrates cleanly, contributing to the ~70% reuse thesis. Reuse: verify the rehydration runs at the right boot phase (after `capture_boot_epoch`, before user-facing passes) and document the recovery window in the runbook. | Inventory row lives canonically at `technical-analysis.md` §5; this row is the plan-docs mirror per A11. | none |
| **MCP stdio-children kill (NG-4 sub-class)** (NEW row, A11) | On hard reboot, the daemon's MCP stdio children (the per-server subprocesses spawned by the MCP pool) are orphaned. Per architect A11: documented in the runbook as a known NG-4 sub-class — the pool is rebuilt per process, so the orphans are cleaned up by the OS at parent death; the gap is the brief window between orphan and OS-reap. NOT fixed in code (it would re-introduce a parent-tracking primitive that doesn't exist today). | none — runbook disclosure only |

**Top-3 reuse anchors for the developer:**

1. **AutoContinue lifespan envelope at `daemon/api.py:2361-2368`** — the existing pass is the template for any new boot-time reconciler (kill-switch, CAS, three-layer never-wedge).
2. **`scripts/systemd/ensemble-daemon.service` template at `scripts/systemd/ensemble-daemon.service:84-108`** — OPS-only edit for G6; `adopt-unit.sh` re-splices per install.
3. **`find_stuck_messages` selector at `daemon/repositories/message_queue/repository.py:385-423`** — already-tested dead code; just need a production caller (one new service, ≤40 LOC).

---

## 6. Estimated New-Code Surface (Per Phase — ≤315 LOC ceiling held)

| Phase | What | Python LOC | Test LOC | Ops/Runbook LOC |
|-------|------|-----------:|---------:|----------------:|
| **0** | Worktree + import-resolution gate + env-fence audit + **worktree-gate failure-injection check (A5/R-07)** | 0 | ≤10 (fixture) | 0 |
| **1** | **G6** — unit template `After=postgresql.service` ONLY (drop `Wants=`) + `connect_timeout` in `create_postgres_engine` + 2 template comments | **≈4** (1 LOC `connect_timeout` + 2 LOC template comments) | **≤15** (preflight reuse tests, `connect_timeout` boundary, no-`Wants=` regression) | **8** (5 unit template + 3 runbook drop-in note) |
| **2** | **G2** — `message_queue_boot_recovery.py` service with **guarded `processing→ready` reset (NOT select-only reuse) + boot-epoch-relative age predicate + skip terminal-instance rows + skip RUNNING/PAUSED-task rows** + lifespan envelope at `api.py:582` (NOT `:2350`; A4) + kill-switch | **≤40** | **≤30** (incl. fresh-dead fixture + `processing_count == 0` post-boot assertion + busy-guard-clears assertion per A3) | 0 |
| **3** | Runbook: per-state RTO SLA + drill procedure + LANE-1 advisory + PAUSED note + scheduler duplicate-fire note + **drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) + D13 triage query + bus-start check + pause-durability window + NG-4 double-side-effect window + discard-on-startup interplay** | 0 | ≤10 (drill smoke) | **≤105** (new runbook) |
| **4** | Demo E2E — kill -9 the daemon + reboot the host, observe AutoContinue + bus + STR + G2 MQ recovery + G6 retry all fire; **amended (A3)**: fresh-dead fixture + `get_stats()["processing_count"] == 0` assertion + busy-guard-clears assertion | 0 | **≤120** (SOFT ceiling per A7; incl. fresh-dead fixture harness) | 0 |
| **TOTAL NEW SURFACE (in-scope, post-fold)** | — | **≈44** (net −26 vs. pre-fold plan) | **≤155** | **≤113** |
| **PARKED (post-promote follow-up commissions)** | G1 deletion −8 / G3 ≤45+≤20 / G7 deprecate-cycle ≤40+≤80+≤100 / **D13 task-less ghost ≈45–60** | ≤192 (later) | ≤120 (later) | ≤10 (later) |

**Ceiling check:** architect-amended §7 totals `≈44 / ≤155 / ≤113` (per architecture-recommendation.md §7). This plan's pre-fold table was `≤70 / ≤160 / ≤85`; the post-fold Python ceiling is **tighter** by −26 LOC. Total surface ≤312 (down from ≤315). All recommendations still ride existing plug points (no new plug-point needed).

**Per-file touch list (complete, lean):**

| File | Nature of change |
|---|---|
| `scripts/systemd/ensemble-daemon.service:84-87` | +3 line edit (G6.A: add `After=postgresql.service` ONLY; drop `Wants=` per R-A5; 2 comment lines) |
| `daemon/repositories/factory.py:232-284` | +1 LOC: add `connect_timeout=BOOT_DB_TIMEOUT_S=10` to `create_postgres_engine` |
| `daemon/services/message_queue_boot_recovery.py` | NEW — `recover_stuck_messages_on_startup()` service (G2.A amended: guarded reset + boot-epoch predicate + skip guards) |
| `daemon/api.py:582` neighborhood | +≤30 LOC lifespan envelope wiring (placement between `capture_boot_epoch` and `setup_worker_pool` per A4) + INFO log |
| `tests/unit/test_message_queue_boot_recovery.py` | NEW — fresh-dead fixture + `processing_count == 0` post-boot + busy-guard-clears assertion + skip-guard test (terminal + RUNNING/PAUSED task) + boot_epoch-relative age predicate test |
| `tests/unit/test_connect_timeout.py` | NEW — `connect_timeout=BOOT_DB_TIMEOUT_S=10` set; preflight reuse (mocked) shows it called once, not three times; firewall-DROP mock hangs ≤10s, not 75–130s |
| `tests/test_hard_reboot_smoke.sh` (or `tests/test_hard_reboot_e2e.py`) | NEW — Phase 4 kill-9 + reboot demo (≤120 LOC SOFT ceiling per A7) |
| `tests/conftest_worktree.py` (or ext.) | NEW (mirror prior-art Δ7) — fails LOUDLY if `import daemon` resolves outside the worktree |
| `docs/runbooks/hard-reboot-recovery.md` | NEW — per-state RTO table + drill + LANE-1 advisory + scheduler note + PAUSED note + **drop-in example** (D15) + **D13 triage query** + bus-start check + pause-durability window + NG-4 double-side-effect window (≤105 LOC) |
| `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | NEW (mirror prior-art Δ7) — worktree path + branch receipt |

No changes to: AutoContinue, DependencyBus, StaleTaskRecovery, discard_on_startup, boot_epoch, checkpoint_adapter, scheduler, KMS_LANE-1, KMS_LANE-2, the existing `find_stuck_messages` selector body, the `__main__.py:143-247` preflight, the launcher exit-75 lane, `daemon/manager.py` body, or any test_pack that already covers them. **REMOVED (architect A1):** `_init_manager_with_pg_retry`, `Wants=postgresql.service` line, `ENSEMBLE_INIT_RETRY` env-knob, `tests/unit/test_pg_init_retry.py`.

---

## 7. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| **R-01** (reframed per A6) | PG ordering race on hosts where PG takes longer to start than the daemon's RestartSec — daemon enters a noisy **10–300s systemd-revive cycle** (per `launcher.sh:1340-1346,1576-1590`; `StartLimitBurst`/`IntervalSec` absent → systemd defaults 5/10s never trip at ≥10s pacing; the cycle is sustained and benign *if* the exit lane is right) — **NOT "daemon permanently down"** as previously framed. The amended mechanism (`_boot_db_preflight` + exit-75 + `connect_timeout=10s`) is budget-exempt, infinite, and is the canonical "wait for a transient dependency" lane; a 5-min WAL replay resolves with the daemon starting cleanly the moment PG accepts connections. | Low | Medium | `_boot_db_preflight` reuse at `__main__.py:143-247`; `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `daemon/repositories/factory.py:232-284`; exit-75 budget-exempt infinite retry; runbook drop-in for non-systemd PG |
| **R-02** (resolved by A1 fold) | (REMOVED — the `_init_manager_with_pg_retry` retry collision with discard CAPTURE_PHASE is moot because the helper itself was deleted; preflight reuse is the correct lane.) | — | — | (RESOLVED) |
| **R-03** | `recover_stuck_messages_on_startup` collides with in-flight MessageQueue retry loops (a row is mid-write when boot kicks the reset) | Low | Medium | The amended G2 reset is a guarded per-row UPDATE (`WHERE message_id=:id AND status='processing'`) using the `dequeue()`-style CAS shape, NOT a select-only reuse; `with self.engine.begin()` atomic transition; runbook note warns operators to NOT manually edit MQ rows during a reboot |
| **R-04** (resolved by A1 fold) | (REMOVED — the `ENSEMBLE_INIT_RETRY` dev-experience regression is moot because the env-knob was eliminated; preflight is the existing, better-shaped lane.) | — | — | (RESOLVED) |
| **R-05** | Systemd `After=postgresql.service` rejects on a non-systemd-PG install (custom PG location, remote/tunnel PG) | Low | Medium | adopt-unit.sh:537 refuses template clobber; Phase-3 runbook ships the ready-to-ship drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) as the delivery mechanism for existing installs; on remote-PG hosts the real dependency is `network-online.target` (already declared) |
| **R-06** | v0.23.0 Secrets promote flips the G7 parking into a hot commission mid-flight | Very Low | Medium | plan worker queries the staged version before opening the G7 follow-up (see decisions.md D11) |
| **R-07** (NEW per A5) | Phase-0 worktree-gate fixture (`tests/conftest_worktree.py`) fails silently or is bypassed in CI → tests run against the wrong daemon import, false-green results | Low | High | Fixture MUST fail LOUDLY (exit 1) if `import daemon` resolves outside `/home/nea/ensemble-src-wt-hard-reboot-recovery`; **failure-injection check in Phase 0 task 0.3**: temporarily point the fixture at a fake path, run `pytest tests/unit -q`, observe LOUD refusal, revert. (Pinned by AC5 in phase1-plan.md.) |
| **R-08** (architect A6/R-A1 reframing) | (RESOLVED — see R-01 reframing. The pre-A1 "daemon permanently down" risk was the wrong read: it was actually a noisy systemd-revive cycle, exit-75-preferred for budget-exemption + clean backoff + no notify-spam.) | — | — | (RESOLVED) |

(Condensed from gap doc §8 risks R-01..R-06 + architect's R-A1..R-A9; this plan's in-scope surface only. Architect R-A2/R-A3/R-A4/R-A6/R-A7/R-A8/R-A9 addressed via the amended mechanism choices in §1, §2, §6, and the runbook (§3).)

---

## 8. Phased Rollout

Each phase is independently shippable; subsequent phases can be reordered or skipped at the dispatcher's call.

| Phase | Name | Objective | Gaps closed | New LOC (post-fold) | Verification gate |
|-------|------|-----------|------------|--------:|-------------------|
| **0** | **Worktree + import-resolution gate + env-fence audit + worktree-gate failure-injection (A5/R-07)** | Implementation + E2E lanes run in `/home/nea/ensemble-src-wt-hard-reboot-recovery` with `import daemon` resolving INSIDE the worktree; env-fence confirms `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`; **failure-injection check** (AC5) confirms the fixture REFUSES to run when pointed at a fake path | none (precondition) | 0 | `python -c "import daemon; print(daemon.__file__)"` resolves inside WT; `tests/conftest_worktree.py` fixture green; **fixture fails LOUDLY when pointed at a fake path** (R-07 / A5); `git status --short` shows ONLY the planning dir |
| **1** | **G6 — `After=postgresql.service` ONLY + `connect_timeout=BOOT_DB_TIMEOUT_S=10` + preflight reuse (no new env-knob)** | The unit template orders the daemon after PG (with `After=` only, NOT `Wants=` per R-A5, NOT `Requires=` per D2); the existing `_boot_db_preflight` + launcher exit-75 lane is the canonical PG-wait; `connect_timeout` removes the firewall-DROP hang | G6 | **≈4 py / ≤15 test / 8 ops** | unit test `test_connect_timeout.py` green; `daemon/repositories/factory.py` carries `connect_timeout=BOOT_DB_TIMEOUT_S=10`; `scripts/systemd/ensemble-daemon.service` has `After=postgresql.service` only (grep-verified, NO `Wants=`, NO `Requires=`); full `tests/unit -q` green |
| **2** | **G2 — `message_queue_boot_recovery` boot pass + lifespan envelope at `api.py:582` (A4) + kill-switch + guarded reset + skip guards (A3, A9)** | The dead-code `find_stuck_messages` selector gets a NEW production caller that ADDS a guarded per-row UPDATE (`WHERE message_id=:id AND status='processing'`); boot-epoch-relative age predicate (`status='processing' AND (last_activity_at IS NULL OR last_activity_at < :boot_epoch)`); **skip rows whose `instances.status IN ('terminated','completed','error','failed')` (revive hazard — mirroring `task/repository.py:1150-1156`; A9)** + skip rows with a RUNNING/PAUSED task (AutoContinue owns them); idempotent on reboot-loop; placement at `api.py:582` (between `capture_boot_epoch` and `setup_worker_pool`), NOT beside AutoContinue; env-knob `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON | G2 | **≤40 py / 60–100 test (SOFT per A7) / 0 ops** | unit test green; integration: stuck `processing` row + boot → row reset to `ready`; second boot → no-op; kill-switch `=0` skips; `processing_count == 0` post-boot (A3 acceptance, demo install with the `true` override per D17); busy-guard clears (A3 acceptance, demo install with the `true` override per D17); MQ-reset-vs-STR-disjointness test (A4 — STR does NOT re-process the same row post-reset). **A3 reframing (shipped default `QUEUE_DISCARD_ON_STARTUP=false` per `config.yaml:306`):** the wedge is TRANSFORMED to visible-pending (busy-guard at `daemon/tools/instance.py:4037` trips on `pending_count>0 or processing_count>0` — pending too) — NOT cleared; the `processing_count == 0` + busy-guard-clears assertion holds only on installs with the `true` override (live `.env`; demo install). The shipped-default installs transform the wedge to visible-pending; the runbook carries the discard-on-startup interplay disclosure. |
| **3** | **Runbook + ops surface (G4, G5, G6, G8 documentation + D13 triage + drop-in example + bus-start + pause-durability + NG-4 + discard-on-startup interplay)** | `docs/runbooks/hard-reboot-recovery.md` ships with: per-state RTO table (Q-2), hard-reboot drill procedure (Q-5), KMS_LANE-1 advisory (Q-4 / G7.C), G3 scheduler duplicate-fire window note (Q-3), G5 PAUSED reconciliation note, G4 no-checkpoint triage, **the ready-to-ship drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15 — the delivery mechanism for existing installs)**, the **D13 task-less ghost triage query**, the bus-start check, the pause-durability window, the NG-4 double-side-effect window, the discard-on-startup interplay | G4, G5, G7, G8 + D13 + D15 | **≤105 runbook / ≤10 drill smoke** | runbook review; drill smoke passes (kill -9 the daemon, verify bus + STR + new MQ pass all fire per runbook checklist); the drop-in example file is copy-paste-ready (D15 verification) |
| **4** | **Demo E2E on real environment (SOFT ceiling ≤120 test LOC per A7)** | User-mandated REAL-environment proof: NON-live host, kill -9 the daemon + reboot the host, observe the daemon come back, observe AutoContinue + bus + STR + G2 MQ recovery + G6 retry all fire; **amended (A3)**: fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) + `get_stats()["processing_count"] == 0` assertion + busy-guard-clears assertion; the NULL-branch fixture alone would mask the fresh-dead class | (integration test for Phases 1-3) | **≤120 test (SOFT) / 0 ops** | demo PASS; evidence bundle archived to `.agents/tester/RESULTS/2026-10-19-hard-reboot-recovery-e2e.md` |
| **TOTAL** | — | — | G2, G6 closed in scope; G1/G3/G7/D13 deferred (post-promote follow-up commissions); G4/G5/G8 runbook-only | **≈44 py / ≤155 test / ≤113 ops** | four-gate green: unit + integration + runbook review + demo E2E |

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
| G6 mechanism choice | gap doc §6 | G6 post-fold (`After=` + preflight/exit-75 + `connect_timeout`) — see decisions.md D2/D7/D9/D16 |
| Phase boundaries | per gap-doc severity + criticality ranking + dep coupling | Phase 1=G6 critical; Phase 2=G2 dead-code caller; Phase 3=runbook; Phase 4=demo E2E — see decisions.md D8 |

---

## 10. Lockstep Rules (mirrored from prior art, post-fold A1)

- **Single kill-switch:** `ENSEMBLE_HARD_REBOOT_BOOT_PASS=1` default ON, `=0` disables (Phase 2). The architect-folded A1 ELIMINATED `ENSEMBLE_INIT_RETRY` entirely (per D16; fewer knobs = the mandate; the preflight is the existing, better-shaped lane).
- **Worktree gate:** `pytest` fixture fails LOUDLY if `import daemon` resolves outside `/home/nea/ensemble-src-wt-hard-reboot-recovery` — copied from prior-art Δ7 / D20; **failure-injection check (A5/R-07)** verifies the fixture refuses to run when pointed at a fake path.
- **Three-layer never-wedge envelope:** verbatim port from `auto_continue_boot_pass.py:230-494` — at the unit-of-work level (per candidate), at the pass level, and at the lifespan level.
- **No new column / no migration:** G2 ADDS a guarded UPDATE to the existing `message_queue` table (no new column); G6.A is the unit-template edit + a 1-LOC `connect_timeout` in `create_postgres_engine` (no schema change). Honours the **less code** user directive.
- **Phase 0 first, last, and always:** Worktree-claim file is the receipt gate before any code commit; failure-injection check (A5) is the second gate.

---

## References

- Evidence base: `.agents/shared/planning/hard-reboot-recovery/technical-analysis.md` (311 lines, verified; LEAD DRIFT corrections recorded at §Context Summary; §9 carries a one-line reconciliation pointer to the post-fold LOC tables below)
- Architect amendment: `.agents/shared/planning/hard-reboot-recovery/architecture-recommendation.md` (237 lines, focus areas 1-6, ratified 2026-10-19 10:20; the A1-A11 fold is anchored here)
- Prior art (design authority + structure mirror): `.agents/shared/planning/auto-continue-running-after-restart/` (`plan-overview.md` 149 lines, `phase1-plan.md` 43 lines, `decisions.md` 322 lines)
- AutoContinue boot pass: `daemon/services/auto_continue_boot_pass.py:230-494` + wiring site `daemon/api.py:2361-2368` (the docstring at `:238` cites the old `:1522` line — the wiring is at `:2361-2368`, anchored in this plan as the canonical cite; A8)
- CAS durable serializer: `daemon/repositories/task/repository.py:1066-1191`
- Unit template: `scripts/systemd/ensemble-daemon.service:84-108`
- MQ dead-code selector: `daemon/repositories/message_queue/repository.py:385-423`; `dequeue()` CAS shape at `:302-313` (architect A3: the "atomic transition" the prior plan attributed to the selector is actually the `dequeue()` shape — the G2 reset mirrors this)
- PG preflight + exit-75 lane: `daemon/__main__.py:143-247` + `run_app.py:70` (frozen) + `__main__.py:467` (`python -m daemon`) + launcher `launcher.sh:1340-1346,1576-1590` + `constants.py:70` (`BOOT_DB_TIMEOUT_S=10`)
- `connect_timeout` target: `daemon/repositories/factory.py:232-284` (`create_postgres_engine` currently sets only `pool_pre_ping`)
- MQ boot pass placement: `daemon/api.py:582` neighborhood (between `capture_boot_epoch` and `setup_worker_pool`, per A4 — NOT `:2350` beside AutoContinue)
- Lifespan envelope pattern: `daemon/api.py:2302-2396` (UpgradeJournalSweepService envelope-try) + AutoContinue wiring `:2361-2368`
