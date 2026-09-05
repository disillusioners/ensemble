# Phase 6: Fast-Follow Backstop (Durable Recovery + Pre-Flip Hardening)

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (test-layer reconciliation pass; reviewer CHANGED_REQUESTED)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase1-plan.md`](./phase1-plan.md), [`phase2-plan.md`](./phase2-plan.md), [`phase3-plan.md`](./phase3-plan.md), [`phase4-plan.md`](./phase4-plan.md), [`phase5-plan.md`](./phase5-plan.md), [`architecture-recommendation.md`](./architecture-recommendation.md)

---

## Objective

Ship the **durable `manager.enqueue_message` recovery backstop** (candidate C per `architecture-recommendation.md` §1, §2, §3, §5 Phase-6 row) **POST-SOAK**. The backstop covers the OS-2 no-leader-turn cascade class (parent-cascade completer at `_update_parent_on_child_complete` / `error_reporting.py:319` — the bug class the MVP's in-graph nudge cannot see, per the Honest Coverage Boundary in `architecture-recommendation.md` §2). It also re-homes the **observer-vs-revive race** (`job_feedback_observer.py:3703-3758` re-stamp race — see `architecture-recommendation.md` §6 correction #3b) which exists independently of this feature but is most acutely triggered by the durable recovery path. O5–O9 are listed as **pre-flip hardening work items** (runbook entries — implementation deferred; see §6.7).

This phase is RELOCATED out of the MVP and ships ONLY after the adjudicated dry→enforce soak data demonstrates the in-graph nudge is stable enough to be paired with a backstop. See §6.8 for the soak/promotion criteria.

Entry criterion: Phase 5 (MVP test matrix) is green in production; `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce` has been the default for ≥ the soak duration defined in §6.8; the dry-log false-positive rate is below the threshold; the escalation-rate threshold is below the bound.

Exit criterion: The durable backstop is shipped; O5–O9 pre-flip checklist items are completed (runbook entries written; toggles verified; lint clean); soak→enforce promotion is a single-env flip per `docs/setup.md` (extending Phase 4 task 4.6); quarantine-free.

---

## Why This Phase Exists (and Why It Is NOT in the MVP)

The MVP deny path (Phase 5 test 5.5) is the in-graph checkpoint-durable `HumanMessage` nudge — same execution, no `manager.enqueue_message`, no revive. This is race-free by construction: a denied turn never reaches observer Step 2 / child_reports / revive. The C5 ruling (`architecture-recommendation.md` §3) splits delivery by context — in-graph for prevention/continuation, durable for out-of-graph recovery. The MVP deny path is the prevention path; the durable path is the recovery path.

The durable path is RELOCATED here because:

1. **The MVP does not trigger it.** The MVP's deny path does not call `manager.enqueue_message`. There is no AC to satisfy in the MVP. Building it before soak would be premature.
2. **It re-introduces the race classes the MVP exists to eliminate.** Adding `manager.enqueue_message` to the MVP deny path reopens the observer-vs-revive race (`job_feedback_observer.py:3703-3758` Step 2 re-stamp vs `instance_messaging.py:1867-1909` revive — see `architecture-recommendation.md` §6 correction #3b). The race moves here, out of phases 1–5, where it can be designed against the actual surface.
3. **The OS-2 no-leader-turn cascade class is the only thing it covers.** The MVP's in-graph nudge CANNOT see a leader who completes WITHOUT a leader turn (last child completes → cascade stamps the parent via `_update_parent_on_child_complete` at `child_reports.py:952` / inline twin `:3325` / `error_reporting.py:319`). The C backstop sweep is the only mechanism that can cover it (`architecture-recommendation.md` §2 Honest Coverage Boundary).

---

## Coupling

- **Tight with:** Phase 3 (counter feeds the sweep; bound-enforcement logic is shared); Phase 4 (mode + kill-switch feed the sweep; dry-mode observability decisions apply to the sweep's own log lines).
- **Loose with:** Phase 5 (the MVP test matrix verifies the in-graph path; this phase adds the durable-path tests without duplicating them).
- **Independent of:** Phases 1–2 (tool, scanner, gate wiring — these are MVP-only).

---

## Tasks

### 6.1 — Recovery injector service (durable path)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_recovery.py` (new) |
| **Description** | New service module. Exposes one public function: `inject_recovery(instance_id: str, denied_count: int) -> None`. Internal contract: (a) call `manager.enqueue_message(instance_id=instance_id, source="attestation_recovery", priority=0, content=<server-authored constant text from NFR-6>)` — **facade call, NOT direct InstanceMessagingService.enqueue_message** (facade-forwarding duty per §6.5); (b) the message MUST render as HUMAN-authored (D6: source `"attestation_recovery"` does NOT start with `internal_*:`, so the `instance_messaging.py:1685-1704` default `else → HUMAN` stamp applies — see D6 in `architecture-recommendation.md` §1); (c) `priority=0` so the recovery message preempts normal user traffic (priority `1` is the default user-priority value per `daemon/services/instance_messaging.py:2178`; preemption requires the lower `priority=0` value); (d) content is the same server-authored constant as the in-graph nudge (single source of truth — no text drift). The function is **idempotent within a denial epoch** — re-entry on the same `denied_count` is a no-op (per O4: per-denial-epoch idempotent upsert or documented inflation; documented inflation chosen for MVP simplicity — see §6.2 test). |
| **Decision tags** | [D6], [C5 ruling (out-of-graph leg)] |
| **Test notes** | Unit test 6.2 verifies the contract. Integration test 6.3 verifies the durable wake. |

### 6.2 — Unit test: recovery injector contract

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_recovery.py` (new) |
| **Description** | AC-4.5 (durable path; the renumbering fix in this revision — see requirements.md:300-305 and Phase 5 task 5.3 / 5.18 — the MVP nudge ACs are AC-4.1 + AC-4.2; AC-4.5 is the deferred durable-path AC). Cases: (a) `inject_recovery` calls `manager.enqueue_message` exactly once with kwargs `{instance_id, source="attestation_recovery", priority=0, content=<server-authored constant>}`; (b) `source` value does NOT start with `internal_*:` (avoids wrong `msg_type` per `instance_messaging.py:1685-1704`); (c) priority is `0` (preemption over default user priority `1` per `daemon/services/instance_messaging.py:2178`); (d) entry point is `manager.enqueue_message` (facade), NOT the service directly — the test asserts the call site via `inspect.getsource` substring match on the recovery module (per `daemon/manager.py:6530-6626` facade-forwarding discipline); (e) idempotency within a denial epoch — calling `inject_recovery` twice with the same `denied_count` results in **two** `manager.enqueue_message` calls (documented inflation per O4; the sweep tick uses `denied_count` as the epoch key and accepts the inflation as a known artifact — the alternative (idempotent upsert) requires a per-instance epoch counter that the MVP does not have); (f) crash recovery — the `manager.enqueue_message` path writes `MessageQueue` + `Task` in a single transaction (`daemon/services/instance_messaging.py:1960-2073`); a crash between counter-increment (Phase 3 task 3.3) and `inject_recovery` is safe — the next gate evaluation sees the counter and fires recovery again (idempotent at the gate level, not the injector level). |
| **Decision tags** | [D6], [C-7 facade-forwarding] |
| **Test notes** | AC-4.5 verified. NFR-6 (server-authored constant) verified. |

### 6.3 — Integration test: durable wake — DB read-back (MessageQueue + Task row assertions)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_recovery_durable_wake.py` (new) |
| **Description** | Verify the durable path end-to-end: gate denies → `inject_recovery` → `MessageQueue` row created + `Task` row created in a single transaction → leader instance resumes (via the existing `enqueue_message` revive-or-wake path — `daemon/services/instance_messaging.py:1867-1909`) → leader processes the HumanMessage → calls `attest_completion` → gate allows → terminal. DB read-back assertions: (a) exactly ONE `MessageQueue` row created with `source="attestation_recovery"` and `priority=0`; (b) exactly ONE `Task` row created pointing at the message; (c) the `Task.work_id` matches the leader instance's `work_id` (work_id propagation discipline per C-3); (d) after leader attests and allows, the recovery message is in `state.values["messages"]` as a `HumanMessage` (verifies the wake fired); (e) `attestation_denied_count` reset to 0 on the allow path (per O2 + Phase 5 test 5.13). |
| **Decision tags** | [D6], [C-3 work_id propagation] |
| **Test notes** | AC-4.5 verified. AC-E2E-1 (durable-path variant — distinct from the in-graph-nudge E2E in Phase 5 task 5.5). |

### 6.4 — Integration test: revive re-arm (gate + ledger after terminal revival)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_revive_re_arm.py` (new) |
| **Description** | Spec the post-recovery revival flow: after the durable recovery wakes the leader and the leader attests + allows, the gate and ledger are in a clean state for the next mission. Assertions: (a) after allow, `attestation_denied_count=0` and `completion_gate_escalated=False` (per O2); (b) if the leader instance is then terminated and re-dispatched, the next mission's first deny sees `attestation_denied_count=0` (not pre-burdened — same as Phase 5 test 5.14 but on the durable-path variant); (c) the re-arm race with observer Step 2 (`job_feedback_observer.py:3703-3758`) — the durable recovery may fire on an instance that the observer is concurrently finalizing. The test asserts: if the leader is already terminal (`status=COMPLETED`), the recovery message is enqueued (it will trigger the existing `send_message`-revive path on a `COMPLETED` instance per `daemon/services/instance_messaging.py:1867-1909`) AND the observer's Step 2 re-stamp race is handled by the existing pause-cancellederror-fix-2026-07-12 precedent (the recovery message lands in the queue; the leader is revived; the observer does not stomp the running leader because the WHERE clause on the ORM update includes a `status != terminal` check — see §6.6 for the pre-existing race mitigation). |
| **Decision tags** | [O2], [D9] (finalize ordering) |
| **Test notes** | This test re-homes the **observer-vs-revive race** (`job_feedback_observer.py:3703-3758` Step 2 vs `instance_messaging.py:1867-1909` revive) — the race that exists independently of this feature but is most acutely triggered by the durable path. The race is mitigated per §6.6; this test verifies the mitigation under the recovery-specific scenario. |

### 6.5 — Integration test: JAFP no-JobItem for recovery + facade-forwarding discipline

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_jafp.py` (new); `tests/integration/test_attestation_facade.py` (new — only if a new kwarg was added to `enqueue_message`) |
| **Description** | Two related tests. **JAFP no-JobItem (AC-4.5 JAFP clause)**: after `inject_recovery` fires, query the `JobItem` table — there must be NO row for the recovery message. Internal paths use `enqueue_message` only; public entry points (Phase 4 work) use JobItems. Hard rule per C-3. **Facade-forwarding (C-7)**: if any new kwarg was added to `InstanceMessagingService.enqueue_message` (e.g., `attestation_recovery_priority=0` for preemption over the default user priority `1`, `attestation_origin="attestation_recovery"`), `manager.py:6530-6626` MUST forward it AND a real-dispatch integration test asserting the intended exception type must be added. Precedent at `tests/unit/test_manager_enqueue_message_work_id_required.py` + `tests/integration/test_job_driven_enqueue_work_id_facade.py`. **Skip the facade-forwarding test if no new kwarg was added.** The design intent is to thread D6's `source="attestation_recovery"` via the existing `source` kwarg (no new kwarg needed); if the implementation team opts for a new kwarg instead, this test is mandatory. |
| **Decision tags** | [C-3], [C-7], [D6] (if source value requires a new kwarg) |
| **Test notes** | AC-4.5 JAFP clause verified unconditionally. Facade-forwarding verified conditionally. |

### 6.6 — Pre-existing observer-vs-revive race mitigation (deferred handoff)

| Aspect | Detail |
|---|---|
| **Files touched** | Separate ticket (not this branch) — tracked here as a pointer per `architecture-recommendation.md` §7 watch-list item 8 ("Pre-existing observer-vs-revive race ... file separately; do not fix incidentally in this branch") |
| **Description** | The race between `job_feedback_observer.py:3703-3758` Step 2 (bare ORM terminal write, no WHERE guard) and `instance_messaging.py:1867-1909` (revive path) is a pre-existing bug class independent of this feature. **The durable recovery path in §6.4 ACUTELY TRIGGERS this race** (because the recovery message can wake a leader that the observer is concurrently finalizing). The Phase 6 implementation MUST (a) add a `status != terminal` WHERE guard to the Step 2 ORM update (the canonical fix), OR (b) document a different mitigation chosen by the dev team; either way, the fix is NOT in this branch — it is a separate ticket that this phase's tests (§6.4) document the dependency against. The race-mitigation ticket is filed at the start of Phase 6 and tracked as a Phase 6 entry criterion. |
| **Decision tags** | [architecture-recommendation.md §7 watch-list item 8] |
| **Test notes** | Phase 6 §6.4 test asserts the recovery-specific scenario behaves correctly; the broader race-mitigation tests live in the separate ticket. |

### 6.7 — Pre-flip hardening checklist (O5–O9)

O5–O9 are noted as fast-follow/pre-flip work items per the canonical design. **No implementation is specified here — these are runbook entries and pre-flight checks only.** The implementation work for O5–O9 is deferred to a separate ticket (or a follow-on phase) and is out-of-scope for this plan.

| O# | Item | Disposition |
|---|---|---|
| **O5** | **WC-wake-style kill-switch wiring parity** — confirm `attestation_recovery_enabled` (or equivalent flag) follows the same env-resolved, restart-read, one-time boot-log shape as `ENSEMBLE_WC_WAKE_ENQUEUE` (default OFF, operator flips ON after soak). | Runbook entry in `docs/setup.md` extending Phase 4 task 4.6. **No code in this phase.** |
| **O6** | **Sweep cadence tuning** — default 5 minutes is the spec; the operator's first 2 weeks of production data inform the final cadence. Document the cadence-observation template in the runbook. | Runbook entry. **No code in this phase.** |
| **O7** | **LLM-scripting fake-chat-model seam hardening** — Phase 5 task 5.0 ships the minimal seam (early-ship infrastructure; renamed from 5.17 per the early-task reordering); richer features (streaming, function-calling retries, vision) are deferred. Document the seam's known limits in the runbook. | Runbook entry. **No code in this phase.** |
| **O8** | **`aget_state` thread-id-only config** — the `messages_scanned > 0` log schema field requires `aget_state` to be invoked with thread-id-only config (no node config — per the compaction seam `_compaction_persist_seam.py:139` recipe). Confirm this is the shape shipped in MVP. The locking test for this contract is the unit-level O8 assertion at [`phase2-plan.md` task 2.3](./phase2-plan.md) line 55 and [`phase4-plan.md` task 4.5](./phase4-plan.md) line 104 (asserts the gate's config shape carries no `checkpoint_ns` key — the in-node pattern must NOT thread checkpoint_ns into the scanner config). | Pre-flip checklist reference: see those assertions for the unit-level guard. **No new code in this phase.** |
| **O9** | **Pre-flip lint and observability sweep** — before flipping `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce` as the project default (currently `dry`), confirm: (i) all Phase 5 + 6 tests are green in CI; (ii) the dry-log volume is bounded (no runaway log spam from a buggy scanner); (iii) the escalation-rate threshold defined in §6.8 is below the bound; (iv) the operator runbook (`docs/setup.md`) names all three envs and the flip procedure. | Pre-flip checklist (manual review). **No new code.** |

### 6.8 — Soak and promotion criteria

| Aspect | Detail |
|---|---|
| **Files touched** | `docs/setup.md` (runbook entry extending Phase 4 task 4.6) |
| **Description** | Define the dry→enforce→backstop promotion criteria. **Soak duration**: ≥ 14 calendar days of `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce` in production (mirrors `architecture-recommendation.md` §1 D2 "≤2-week soak" guidance and the WC-wake posture precedent). **Dry-log false-positive rate threshold**: < 0.5% of leader turn-ends satisfy the R2-deny predicate in dry mode (i.e. `dry_log_deny_predicate_total / dry_log_total < 0.005`, over the soak window — replaces the previous fuzzy counter name per CR-4). **Escalation-rate threshold**: < 0.05% of leader missions reach `gate_terminal_after_bound` in enforce mode (over the soak window). **Backstop shipping criteria**: only after the escalation-rate is sustained below the bound for the soak window AND the dry-log volume is bounded (no runaway). The flip procedure is a single-env change `ENSEMBLE_LEADER_ATTESTATION_MODE=dry → enforce → attestation_recovery_enabled=true` (in that order; the backstop ships separately). The Phase 4 runbook already names the first two flips; Phase 6 adds the third. |
| **Decision tags** | [D2], [architecture-recommendation.md §1 D2], [WC-wake posture] |
| **Test notes** | Manual review. No automated test. The criteria are operator-judgment thresholds and live in the runbook. |

---

## Mode-Pinning Convention (applies to every test in this file)

Every test in this file declares a mode (for the gate's behavior) AND a `attestation_recovery_enabled` flag (for the backstop's behavior):

| `ENSEMBLE_LEADER_ATTESTATION_MODE` | `attestation_recovery_enabled` | Test behavior |
|---|---|---|
| `off` | (any) | Gate bypassed; backstop not exercised |
| `dry` | `False` (default at MVP) | Gate evaluated, log only; backstop not exercised |
| `enforce` | `False` (default at MVP) | Gate evaluated; in-graph nudge (Phase 5 test 5.5) or durable recovery (this phase's tests) depending on whether `attestation_recovery_enabled` is True |
| `enforce` | `True` (Phase 6 default) | Gate evaluated; durable recovery path is the deny-side action |

The Phase 6 tests run with `mode="enforce"` AND `attestation_recovery_enabled=True`. Tests in this file MUST NOT call `inject_recovery` directly under `mode="off"` or `mode="dry"` — the gate's decision tree excludes the backstop under those modes by design.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Double-delivery class** — implementation accidentally calls BOTH in-graph nudge AND `manager.enqueue_message` on a single deny | High | Medium (the architectural fork is recent; the temptation to "be safe" and enqueue too is real) | Test 6.3's DB read-back asserts **exactly one** `MessageQueue` row per deny. The Phase 5 test 5.5 explicitly asserts `manager.enqueue_message.assert_not_called()` — the two tests together form the lock. Self-grep: zero `enqueue-on-deny` strings in this file. |
| 2 | **Race with observer Step 2 re-stamp** — durable recovery wakes a leader that the observer is concurrently finalizing | High (mission mis-finalizes or revives a finalized mission) | Medium | §6.6 — the pre-existing race is filed as a separate ticket; Phase 6 entry criterion gates on that ticket's resolution. The Phase 6 §6.4 test verifies the recovery-specific scenario after the race is mitigated. |
| 3 | **Source-prefix collision** — `source="attestation_recovery"` starts with a substring that some downstream `msg_type` mapper treats specially | Medium (msg_type becomes COMPLETION_REPORT / ERROR_REPORT / AGENT instead of HUMAN) | Low (D6 well-defined; `"attestation_recovery"` does not collide with any `internal_*:` prefix) | Test 6.2(b) asserts the source does NOT start with `internal_*:`. |
| 4 | **Documented inflation of `inject_recovery`** — re-entry on the same `attestation_denied_count` creates duplicate messages (per O4) | Low (operational noise; leader sees the same nudge twice in one deny epoch) | High (the sweep tick will fire repeatedly if the leader doesn't attest) | Test 6.2(e) documents the inflation; the runbook notes that the inflation is bounded by the bound (`attestation_denied_count` reaches `bound` and the gate returns `terminal_after_bound` rather than `deny`, stopping further recovery messages). |
| 5 | **Soak criteria are operator-judgment** — escalation-rate threshold may not generalize across projects | Medium (operator flips enforce too early or too late) | Medium | §6.8 names the thresholds with default values; the runbook explicitly states "operator-judgment" and the WC-wake posture precedent (default OFF, operator flips ON after ≤2wk soak). |
| 6 | **Facade-forwarding test skipped when a new kwarg IS added** — false sense of compliance | Low (facade breaks silently on the new kwarg; the AsyncMock-blind gap re-opens) | Low (the design intent is to thread D6's source via the existing `source` kwarg) | Test 6.5's facade-forwarding case is conditional but explicit: if a new kwarg was added, the test is mandatory. Code-review checklist requires the dev team to declare at PR-time whether a new kwarg was added. |

---

## Rollback Story

This phase is reversible per file:

1. **Recovery injector rollback** — delete `daemon/services/attestation_recovery.py` + `tests/unit/test_attestation_recovery.py` + `tests/integration/test_attestation_recovery_durable_wake.py`. Set `attestation_recovery_enabled=False` at the env level. The MVP (Phase 5) is unaffected.
2. **Sweep rollback** — disable the sweep lane via the per-lane kill-switch (modeled on `ReportDeliveryRecoveryService`'s per-lane kill-switch). No code change; just a config flip.
3. **Runbook rollback** — revert the Phase 6 additions in `docs/setup.md`. Operators revert to the Phase 4 procedure (dry→enforce flip only).
4. **O5–O9 pre-flip items** — these are runbook entries, not code; revert the runbook entries. The checklist items are deferred, not implemented, so there is no code to roll back.

**Restart-read:** the durable backstop is env-gated (`attestation_recovery_enabled`); flipping it ON/OFF requires a daemon restart (mirrors `ENSEMBLE_WC_WAKE_ENQUEUE`).

---

## Exit Criterion

This phase is done when:

- [ ] `daemon/services/attestation_recovery.py` ships with the contract per §6.1
- [ ] Test 6.2 (unit) passes
- [ ] Test 6.3 (integration, durable wake + DB read-back) passes
- [ ] Test 6.4 (integration, revive re-arm + race mitigation per §6.6) passes
- [ ] Test 6.5 (JAFP no-JobItem) passes; facade-forwarding test passes IF a new kwarg was added
- [ ] §6.6 pre-existing race ticket is filed (or already resolved); the race-mitigation is in place before §6.4 goes green
- [ ] §6.7 O5–O9 runbook entries are written in `docs/setup.md`; the pre-flip checklist is reviewed and signed off
- [ ] §6.8 soak/promotion criteria are documented in `docs/setup.md`; the escalation-rate and dry-log-false-positive thresholds are named with default values
- [ ] No new pre-existing failures introduced (quarantine-free)
- [ ] Self-grep clean: zero hits for `"kill_switch_on"`, `"enqueue-on-deny"`, `"5-path cleanup"` (and case variants) across this file and `phase5-plan.md`
- [ ] The flip procedure is a single-env change per `docs/setup.md`

The phase is the final pre-production gate; the backstop ships as a separate release from the MVP.

---

## Why the In-Graph Nudge Stays (Phase 5) and the Durable Backstop Complements (Phase 6)

A closing note for the reviewer and the operator. The MVP and the backstop are NOT redundant — they cover orthogonal failure modes:

- **In-graph nudge (Phase 5):** leader DOES take a turn, hallucinates END → prevent-and-continue inside the same execution. The leader is RUNNING throughout.
- **Durable backstop (Phase 6):** leader does NOT take a turn (parent-cascade completer at `_update_parent_on_child_complete` / `error_reporting.py:319` — OS-2) → durable wake, possible revive, leader processes the recovery message. The leader may be COMPLETED + revived.
- **Both (Phase 5 + Phase 6 combined):** defense-in-depth. The in-graph nudge handles the common case; the durable backstop handles the rare parent-cascade case. They never both fire on the same deny (test 6.3's exactly-one-row assertion enforces this).

The MVP ships without the backstop — the common case is covered. The backstop ships post-soak — the rare case is covered. Together they provide the full OS-1, OS-2, MVP coverage posture named in `plan-overview.md` §"Out of Scope (Deferred)" — OS-1 (origin-stamping defect fix) remains out of scope (P2.2 plans a `USER_ORIGIN_SOURCES` whitelist separately — note that `5ef35262a` already landed the whitelist per `architecture-recommendation.md` §6 correction #1, so this is moot); OS-2 (parent-cascade class) is the backstop's target.

## Future Candidates (NOT committed)

Items in this list are recorded here ONLY so a future phase can pick them up cleanly. None are in scope for this plan set; none have committed owners; none have scheduled work. If/when scope is opened, they graduate into proper Phase 6 tasks with full spec.

- **Broadening gate scope to non-leader parents** (planner / developer / reviewer / tidier / approver / architect / tester / etc.) — CANDIDATE, not committed. MVP scope is leader-only per FR-11 / D3 (graph-build-time `agent_id == "leader"` check). A future broadening would need its own D-set, its own incident data, and its own scope-handling for `attestation_nudge` — none of which is in this plan. Cross-reference: `requirements.md` FR-11 + §G1.
