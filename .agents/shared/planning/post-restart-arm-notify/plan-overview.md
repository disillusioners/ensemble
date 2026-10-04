# Post-Restart Arm-Notify — Plan Overview (synthesized entry point)

- **Date:** 2026-10-03 · **Author:** architect (controller) — feature analysis; per-phase plans authored by planner worker; r4 fold applied by plan-creation worker
- **Status:** Ready for Review (r4 fold applied — 2 critical + 3 warnings + 4 suggestions folded; see `## Review round r4` below)
- **Base:** branch `feature/post-restart-arm-notify` (the planning-authoritative tip, `2ddb9683`)
- **Parent initiative:** `.agents/shared/planning/self-restart-upgrade-phase2/` (Phase 2 = arm-restart-upgrade surface; this feature is its **zero-user-action outcome-reporting** complement — see `supersession-record.md`)
- **Reuse / no-parallel-machinery constraint (AC6):** the wake is the **existing `enqueue_message` primitive** (manager.py:7935-7996 → instance_messaging.py:2108); no parallel messaging subsystem. The durable record reuses the existing `releases/state.json` atomic surface (single-writer, tmp+fsync+os.replace) — see `architecture-recommendation.md` §FA1.

---

## 0. Artifact Map (read in this order)

This directory holds **10 files**. Read them in the order below;
each is a single-author artifact with a clear purpose. There is
**no** `technical-analysis.md` / `research-findings.md` by design —
`architecture-recommendation.md` IS the technical analysis (the
"FA1-FA6 + 12 invariants" structure replaces the
research-findings doc the Phase 2 plan used).

| # | File | One-line purpose | Size |
|---|------|------------------|------|
| 1 | `plan-overview.md` (THIS FILE) | Synthesized entry point — the developer reads this first. Goal, ACs, non-goals, phase table, hard constraints, open questions, ADR range | ~210 lines |
| 2 | `architecture-recommendation.md` | The technical analysis — REWRITTEN by the validation round (c77c5ff1): FA1–FA6 verification verdicts + consolidated plan deltas (:136-156). Cite this for "what" + "why"; D-FA1.x for sub-decisions | 178 lines |
| 3 | `decisions.md` | The ADR-style extraction (ADR-039–ADR-044). Six ADRs in Context → Options → Decision → Consequence format. Cite this for "the ruling" | 389 lines |
| 4 | `test-strategy.md` | The AC → test-case map (T1.1–T6.3, D1–D6) + per-pack conventions + the regression matrix. Cite this for "how we verify" | 563 lines |
| 5 | `risk-register.md` | The risk inventory (R-1..R-20) + the Live-Outright-Refusal Invariant cardinal rule + risk → test-case matrix. Cite this for "what could go wrong" | 284 lines |
| 6 | `supersession-record.md` | The D-FA1.2 supersession record (pull-model → push-wake-on-boot). Cite this for "what changed from Phase 2" | 129 lines |
| 7 | `phase1-plan.md` | Per-phase plan: arm-side record + journal write (FA1 + FA2; ADR-039 + ADR-044) | 377 lines |
| 8 | `phase2-plan.md` | Per-phase plan: boot sweep + wake delivery (FA3 + FA4; ADR-040 + ADR-041 + ADR-042 + ADR-044) | 370 lines |
| 9 | `phase3-plan.md` | Per-phase plan: terminal-state gating + edge cases + structural AC6 tests (FA4 + FA5; ADR-042 + ADR-043 + ADR-044) | 353 lines |
| 10 | `phase4-plan.md` | Per-phase plan: banner text updates + runbook + drill + release notes (FA6; all six ADRs in the docs surface) | 362 lines |

**Total: ~3,830 lines of planning. The plan-overview (this file)
is the only required read; the rest is on-demand per the matrix
in §4 below.**

---

## 0b. Architecture Validation Round (c77c5ff1) — deltas applied

The architect (controller) validated this plan against actual code
at post-`dac38fd8` HEAD and committed a REWRITTEN
`architecture-recommendation.md` (178 lines, authoritative,
READ-ONLY). **Verdict: the architecture is VALIDATED on all six
focus areas** — `pending_wakes` on `releases/state.json` (ADR-039,
with atomicity reword), boot + 90s-tick sweep delivery (ADR-040),
terminal-class-event gating (ADR-042, with one CRITICAL predicate
fix), re-stamp routing for AC3 (ADR-041 — proven end-to-end, no
dispatch-logic change), bounded supersession, and the env
kill-switch (ADR-044, with the abandon-on-switch-off gap closed).
All six ADR numbers (ADR-039–ADR-044) are UNCHANGED.

Three MUST findings forced plan deltas (consolidated list at
`architecture-recommendation.md:136-156`; all 3 MUST + 7 SHOULD +
4 NICE deltas are now folded into the sibling artifacts):

1. **Wake predicate defect (MUST):** `_TERMINAL_EVENTS`
   (`upgrade_journal.py:983`) has NO `"restart"` member and the
   only existing terminal reader is PROMOTE-only (`:1016`) — as
   planned, the wake would NEVER fire for intentional restarts.
   Fixed: sibling `WAKE_TERMINAL_EVENTS` + wake-owned reader
   (Phase 2 T13); mutation guard T4.8 (Phase 3).
2. **Kill-switch × persisted records (MUST):** abandon-on-switch-off
   semantics (`reason=kill_switch_off` + one history event,
   one-time pass) — Phase 2 T14 / T5.16; re-enable-no-stale pin
   T5.17 (Phase 3).
3. **Arm-time atomicity reword (MUST):** the original claim that
   the arm+wake write is self-atomic was misleading — atomicity
   is BY THE CALLER-ACQUIRED journal lock (`journal_write` has no
   internal lock); `arm_pending_wake` pinned INSIDE the
   lock-holding `try` (Phase 1 T12 placement + Phase 3 T6.4
   structural pin).

The SHOULD deltas (#4 CAS `wait_s≈30`, #5 delivered-mark retry
semantics, #6 Site 1 test T3.5, #7 T3.3 reword, #8 defensive
pre-enqueue stamp note, #9 burst-abort risk R-21, #10 sweep
hardening) and NICE items (#11–#14, recorded in `decisions.md`
"Deferred (NICE) items") are likewise applied. This revision did
NOT modify `architecture-recommendation.md`.

---

## 0c. Review round r4 (2026-10-03, fold applied)

The reviewer REJECTED the suite @ `2ddb9683` for implementation
readiness with **2 critical + 3 warnings + 4 suggestions**. The
architecture is **VALIDATED** (`architecture-recommendation.md` is
READ-ONLY and remains the source of truth); this fold is a
bounded revision, NOT a redesign. Every finding is mapped to a
specific edit, and the verified wins are NOT disturbed (the
**VERIFIED WINS** list below was preserved verbatim across the
fold):

* `manager.enqueue_message` boot-reachable semantics
  (re-stamp is defensive; natural stamp at `manager.py:8112` is
  binding — ADR-041 addendum stands)
* **AC3** riding the DB-persistent
  `instance_metadata.original_source` (T6 in-memory re-stamp
  stays defensive/redundant)
* `WAKE_TERMINAL_EVENTS` 7-member predicate
  (architecture delta #1, MUST — sits alongside the 6-member
  `_TERMINAL_EVENTS`, never mutates it)
* `restart.sh:250-262` `pending_op` clearing (test T4.5
  asserts the wake record survives)
* Caller-lock rewording (architecture delta #3 — atomicity is
  by the caller-acquired journal lock, not `journal_write`
  self-locking)
* Live-env refusal → no record (live-outright-refusal
  pre-empts the journal write; D-FA5.5 stands)

**Findings → edits mapping:**

| Finding | Severity | Artifact(s) edited | Task / Test / Risk ID(s) |
|---|---|---|---|
| **C1** — wake-reader built on fictional `{"name", "run_id"}` journal shape | 🔴 | `phase1-plan.md` (T7 + T10 fixtures), `phase2-plan.md` (T13 + T13.1) | T7, T13, new T13.1 (promote-lane fire test) |
| **C2** — `UpgradeJournalSweepService` has no `manager` seam; T6/T10 are unreachable | 🔴 | `phase2-plan.md` (T6, T10), new T18 | T6, T10, T18 (manager-wiring task — api.py:1489-1496) |
| **W1** — AC5 front-door ari fallback has tests but no implementation task | 🟡 | `phase2-plan.md` (new T19) | T5.1, T5.2 + new T19 (ari lookup + body variant + `arm_notify_no_instance` journal branch) |
| **W2** — risk-register misses 4 probed failure modes | 🟡 | `risk-register.md`, `decisions.md` (ADR-040 amendment), `phase2-plan.md` (T16 wording) | R-22, R-23, R-24, R-25; ADR-040 amendment pins the **deferred delivered-mark** choice |
| **W3** — no test-pack registration | 🟡 | `test-strategy.md` (case→pack table + 5 pack filenames), `phase1-4-plan.md` (per-phase verification → pack invocations), `phase3-plan.md` (new T13), `phase4-plan.md` (new T11) | T1.7, T3.6, T4.9, T5.18, T6.5 — five pack files: `test/packs/post_restart_arm_notify_{journal,sweep,routing,edge_cases,structural}_unit_test.sh` |
| **S1** — derive `WAKE_TERMINAL_EVENTS` from `_TERMINAL_OUTCOME_EVENTS` to avoid duplication | 🟢 | `phase2-plan.md` (T13 wording) | T13 (derive by alias; NEVER mutate base) |
| **S2** — explicit entry for executor-never-ran silent abandon | 🟢 | `risk-register.md` | R-21 (existing burst-abort risk; executor-never-ran is the same abandon+grace path with a separate trigger) — wording extended to enumerate the trigger |
| **S3** — ADR-042 wall-clock monotonicity caveat | 🟢 | `decisions.md` (ADR-042 amendment) | ADR-042 addendum: ts-scope compares journal wall-clock ISO strings; clock skew bounds scope accuracy (accepted) |
| **S4** — confirm phase-4 in-scope explicitly | 🟢 | `plan-overview.md` (§4 Phase-4 sanction note) | Phase 4 is the operator-facing surface (banner + runbook + drill + release notes); explicitly sanctioned |

**PINNED decisions (stated verbatim for downstream consumers):**

1. **Ts-scope reader rationale (ADR-042 amendment, fold C1):**
   "The wake reader is `wake_terminal_event_after` over the
   SIBLING constant `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS +
   ('restart',)`, scoped by `armed_at` TS, with no `run_id`
   matching — because the journal's `history` entries are flat
   `{ts, event, detail}` records (real shape at
   `upgrade_journal.py:326` and `lib.sh:663,666`) with NO
   `run_id` field. Promote-lane terminal events carry NO
   `run_id` at all (`promote.sh:366`; `rollback.sh:203,209,211`);
   only `restart.sh:252,262` embeds a `run_id=…` substring
   inside the `detail` PROSE, used as an OPTIONAL tie-breaker
   on the RESTART lane only. Choosing a structured `run_id`
   schema would cross the lib.sh/launcher boundary (out of
   scope per the reviewer). **Accepted cross-run ts-scope edge:**
   an `armed_at`-scoped event-class match can attribute a
   sibling concurrent run's terminal event to the wrong wake
   when two arms fire within the same second — bounded by the
   1-second `journal_history_append` timestamp resolution, and
   the user-visible consequence is one extra `upgrade_status`
   call (idempotent; not a wrong answer)."
2. **Delivered-mark semantics choice (ADR-040 amendment, fold
   W2):** "The wake's `mark_wake_delivered` is called AFTER
   `enqueue_message` returns a `message_id` (synchronous write
   path), NOT deferred to first dispatch attempt. The one-shot
   loss window is **ACCEPTED** — if a chat adapter is disabled
   or fails at wake time, the `MessageQueue` row exists but
   dispatch is dropped (`dispatcher.py:158-165` silent-drop for
   `system:*`, or adapter-level error), and the user's recovery
   is the unchanged pull-model `upgrade_status` query
   (D-FA1.2 fall-back, `supersession-record.md` §4). The
   alternative — defer the delivered-mark to first dispatch
   attempt — would require holding the wake record in
   `delivering` for the full chat-adapter timeout window
   (typically 30s) plus a retry round, which is structurally
   racy with the 90s tick AND requires a new dispatch-success
   callback surface (parallel-machinery violation, AC6). The
   pull-model recovery path is the documented recovery; the
   drill's D5 (kill-switch) and the runbook's Recovery Flow
   section enumerate the operator path."

**§FA anchors and ADR numbering 039–044 are STABLE** across the
fold. ADR amendments are **addendum blocks inside the existing
ADR** (not new top-level ADR numbers).

**Commit follow-up:** the implementation-lane commit lands on
the same branch after this planning commit. The implementation
commit MUST add a verification step that runs each registered
pack and asserts GREEN — the per-phase acceptance criteria in
each `phaseN-plan.md` cite the pack by path, not by bare
`pytest` invocation.

---

## 1. Goal

**When `system_upgrade` / `system_restart` arms a run and the daemon goes down and comes back, the SYSTEM must automatically wake the arming instance so it checks the run outcome and reports back to the user in the SAME chat/channel where the confirmation happened. Zero user action.**

The wake is the **push** analog of the existing D-FA1.2 pull model.
Both are in force: the pull model for interactive `upgrade_status`
queries (unchanged), the push model for the post-restart
outcome-report (this feature, replacing D-FA1.2 for the wake
concern only). See `supersession-record.md` for the explicit
ruling and the unchanged-affected ADRs.

### Hard requirement (inherited from Phase 2, unchanged)

No LLM in the critical recovery path. The wake is a deterministic
journal→enqueue_message action; the agent's response is the LLM step
which is **NOT** on the recovery path — it is the user-facing
delivery, with the same async/best-effort delivery semantics as
every other wake in the system (e.g. the `WC watchdog` wake documented
at manager.py:7984).

### Acceptance criteria (all mandatory, verbatim from the task spec)

| AC | Summary | Section | ADR | Delivered in |
|---|---|---|---|---|
| **AC1** | Arm-time durable record (run_id, arming instance_id + agent_id, originating source routing context, pending state; written transactionally with the arm) | `architecture-recommendation.md` §FA1, §FA2 | ADR-039 | **Phase 1** |
| **AC2** | Boot delivery (detect pending records at startup, enqueue self-describing wake to the recorded instance) | §FA3 | ADR-040 | **Phase 2** |
| **AC3** | ROUTING (wake turn's outcome report flows back to the confirming chat — non-negotiable) | §FA3.3 | ADR-041 | **Phase 2** (tests T3.1–T3.4) + **Phase 3** (T3.5 Site 1 + structural) |
| **AC4** | Terminal-state gating (no wake while pipeline could still roll back, OR race harmless by design) | §FA4 | ADR-042 | **Phase 2** (`WAKE_TERMINAL_EVENTS` predicate, T13) + **Phase 3** (edge cases + T4.8 mutation guard) |
| **AC5** | Edge cases (missing instance → front-door ari fallback or surfaced notice; multiple records → coalesce sensibly; idempotent delivery crash-safely — unrelated later restart must not re-deliver; boot must never wedge on delivery failure; live-env refused restarts → structurally no record; kill-switch off → abandon, re-enable → no stale flood) | §FA5 | ADR-043 + ADR-044 | **Phase 2** (core + T5.1–T5.11 + T5.16) + **Phase 3** (T5.13–T5.15 + T5.17) |
| **AC6** | Reuse existing machinery (no parallel messaging subsystem) | §FA3.1, §FA6 | (cross-cutting; ADR-039/040/043/044 all assert) | **Phase 3** (structural tests T6.1–T6.4) |
| **AC7** | Tests following `tests/unit/` + `tests/job_queue/` conventions | `test-strategy.md` | (cross-cutting) | **Phase 1** (T1.* + T4.1–T4.6 + T5.12) + **Phase 2** (T2.* + T3.1–T3.4 + T5.1–T5.11 + T5.16) + **Phase 3** (T3.5 + T4.8 + T5.13–T5.15 + T5.17 + T6.1–T6.4) + **Phase 4** (T4.7 banner regression + D1–D6 drill) |

---

## 2. Non-Goals (explicitly OUT of scope)

| Non-goal | Reason |
|---|---|
| **Watcher-notifier for daemon-stays-down** | Already in force as ADR-025(b) (watchdog-watcher). This feature is the daemon-RESTARTED analog; the two complement, do not overlap. The P2.3 T8 hard-dependency for the failure-path story is now downgraded — the wake is the *primary* restart path; the watcher is the *backstop* for stays-down. |
| **Replacing the pull model for interactive `upgrade_status` queries** | `upgrade_status` / `release_info` stay as the authoritative interactive read. The push-wake is **additive**: zero-user-action, runs once, and does not preclude the user from also asking interactively. |
| **Live-env wake** | The live outright-refusal at `upgrade_tools.py:2051-2058` means a live `system_restart` NEVER produces a record (refused before any journal write). For `system_upgrade` on live, the 3-factor gate remains; the wake record is written only on a successfully-confirmed verified arm. In either case, the wake is gated by the same env-self-match / live-outright-refusal — the live-wake path remains USER-GATED (hard constraint of Phase 2). |
| **A new SQLModel table for the wake record** | The journal's `releases/state.json` is the existing durable single-writer surface (ADR-004); we extend it with a new keyed record (`pending_wakes: dict[str, PendingWake]`). A new table would add a second atomicity surface (two files / table+journal — explicitly rejected by D-FA1.1 in the Phase-2 plan). The wake is read-rarely, written at arm time, swept at boot — the journal's read-cost is acceptable (the file is a few KB). **ADR-039 confirms journal-section (no new table); therefore no new `.sql` file in `daemon/migrations/`.** |
| **A new HTTP endpoint** | The boot sweep rides the existing `UpgradeJournalSweepService` seam (api.py:1477-1523). The wake action calls the existing `manager.enqueue_message` (no new route). The status surface is the existing `MessageQueue` admin read. |
| **Cross-project / cross-daemon wake forwarding** | The wake is local to the same daemon's restart cycle. If the project migrates to a different daemon install (e.g. failover), the journal travels with the install dir — the wake is still delivered, but the agent identity may be different. The "arming instance" is identified by `instance_id` (string, the same row UUID); revive semantics in `enqueue_message` (instance_messaging.py:1954-1976) auto-flip terminal→RUNNING. |
| **Wake for `arm → cancel / arm → re-arm` cycles** | Each `system_upgrade` / `system_restart` arm mints a fresh `run_id`; the previous wake record is cleared on terminal-state observation (D-FA5.1). The user sees ONE wake per run, not per arm attempt. |
| **Modifying the arm-side refusal vocabulary** | The arm-side refusal tokens (`pipeline-busy`, `journal-unavailable`, `live-restart-refused`, etc.) are unchanged. The wake record is written **only** on a successful arm — refused arms produce no record. |
| **Prompt-level instruction for ari / jober** (R-7 follow-up) | The wake body is a self-describing pointer; the LLM is expected to read it. Prompt-level instruction is a follow-up patch to the prompt-maintenance initiative, **NOT** this feature's code. Flagged in `risk-register.md` R-7. |

---

## 3. Verified Foundation (cite, do not re-derive)

All facts below were verified by 3 explorer passes on this branch.
The full inventory is in `architecture-recommendation.md` §0; the
headline items:

* **The arm contract** — `system_restart` (`upgrade_tools.py:2030-2268`,
  arm at :2156-2263) and `system_upgrade` (`upgrade_tools.py:2313-2900`,
  3-factor gate at :2494-2660, arm at :2707-2872) both write a
  `PendingOp` (`upgrade_journal.py:708-742`) into
  `releases/state.json` BEFORE returning to the caller. The arm-side
  `armed_by_instance` field already carries the arming instance id —
  the wake record reuses this.
* **The durable surface** — `<install_dir>/releases/state.json` is
  the single-writer atomic surface (tmp+fsync+os.replace at
  `upgrade_journal.py:281-294`). `ensure_extensions`
  (`upgrade_journal.py:332-352`) is the additive extension point —
  every existing `pending_op` / `pending_restart` / `pending_actions`
  / `history` extension rides this helper. The wake record rides the
  same helper. **No** second file (rejected by D-FA1.1).
* **The `pending_op` lifecycle** — `restart.sh:250-262` clears
  `pending_op` + `pending_restart` + `in_flight` on completion and
  journals a `restart` terminal event. **Critical consequence:** a
  wake detector that looks only at `pending_op` would MISS
  completed restarts whose `pending_op` is already cleared. The wake
  record must be cleared on **delivery**, not on completion — it
  outlives the `pending_op` clear.
* **The wake primitive** — `manager.enqueue_message(instance_id, message, source, priority, metadata, ...)`
  (`manager.py:7935-7996` → `instance_messaging.py:2108`) is THE
  internal wake primitive. It creates `MessageQueue` + `Task` rows
  in ONE transaction, notifies the worker pool. Revive semantics
  auto-flip terminal→RUNNING (instance_messaging.py:1954-1976);
  PAUSED is exempt (held until resume at :2154-2161, :1925-1934).
* **The source / routing context** — `manager._user_origin_windows[instance_id]`
  (`manager.py:4239-4245`) and
  `manager._user_origin_last_stamp[instance_id]` (`manager.py:4230-4238`)
  are the in-memory per-instance stamps. They are wiped at boot
  (RAM-only). The wake record must PERSIST the source at arm time so
  the boot-time wake can re-stamp the window for the wake turn (the
  3-factor gate reads this window — re-stamping is what makes a
  post-wake `upgrade_status` call succeed without user re-confirmation).
* **The boot seam** — `daemon/api.py:1477-1523` constructs and starts
  `UpgradeJournalSweepService` with a guaranteed pre-yield boot
  reconcile. The wake-detection sweep rides this same service.
  Cadence: 90s tick + boot pass (the boot pass is the load-bearing
  path for our feature).
* **Live refusal** — `upgrade_tools.py:2051-2058`: live `system_restart`
  returns BEFORE any journal write. The wake surface therefore
  structurally cannot originate from a live restart path.
* **`reconcile_pending_op` is PROMOTE-kind only** (`upgrade_journal.py:1002-1067`).
  The wake sweep is **RESTART-kind aware** and is the only consumer
  that knows the wake record exists — it does not depend on the
  reconcile for the wake path.

---

## 4. Phasing (four sequential phases, shippable each)

The feature is split into **four sequential phases**, each
independently shippable (the tree stays green at every phase
boundary). The phases build on each other; later phases
**cannot** start until the earlier phase is GREEN.

| Phase | Name | Objective | Tasks | Key files touched | Status |
|-------|------|-----------|-------|-------------------|--------|
| **1** | Arm-Side Record | Add the durable `pending_wakes` JSON key to `releases/state.json`; mint `PendingWake` dataclass + lifecycle helpers (`arm_pending_wake`, `mark_wake_delivering`, `mark_wake_delivered`, `mark_wake_abandoned`, `list_pending_wakes` + the parameterized terminal walker); wire the arm-side capture helpers into both arm paths INSIDE the lock-holding `try` (delta #3). Atomic with the existing arm write under the caller-acquired journal lock. | 12 | `daemon/tools/upgrade_journal.py` (six new helpers + new dataclass); `daemon/tools/upgrade_tools.py` (three new capture helpers + two `arm_pending_wake` calls in existing `try` blocks); `tests/unit/tools/test_post_restart_arm_notify_journal.py` (NEW) | pending |
| **2** | Boot Sweep + Wake Delivery | Extend `UpgradeJournalSweepService` with `sweep_wake_records`; wire it into the existing boot pass and the 90s tick. Wake = `manager.enqueue_message` with re-stamped source. Terminal gating via `WAKE_TERMINAL_EVENTS` + wake-owned reader (delta #1). Kill-switch abandon-on-switch-off (delta #2). CAS `wait_s≈30` + delivered-mark retry semantics + sweep hardening (deltas #4/#5/#10). Idempotency via `status` lifecycle. Best-effort, never wedges. | 17 | `daemon/services/upgrade_journal_sweep.py` (new `WakeSweepResult` + new `sweep_wake_records` + helpers); `daemon/api.py` (one new wrapped call in boot pass); `tests/unit/services/test_post_restart_arm_notify_sweep.py` (NEW); `tests/job_queue/test_post_restart_arm_notify_routing.py` (NEW) | pending |
| **3** | Terminal-State Gating + Edge Cases + Structural AC6 | Close long-downtime double-arm coalesce (T5.13), paused-instance defer (T5.14), terminal-instance revival (T5.15), kill-switch re-enable-no-stale (T5.17). Add the load-bearing structural tests (T6.1–T6.4 — T6.4 = delta #3 lock-position pin) + the mutation guard (T4.8, delta #1) + the Site 1 dispatch test (T3.5, delta #6). | 12 | `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` (NEW); `tests/unit/test_post_restart_arm_notify_no_parallel.py` (NEW); zero source changes | pending |
| **4** | Banner Updates + Runbook + Drill + Release Notes | Update the obsolete banner text in `upgrade_tools.py`; author the operator runbook; author the bash drill; append the release-notes line. | 10 | `daemon/tools/upgrade_tools.py` (string literal swap in two arm-return branches); `docs/runbooks/post-restart-arm-notify.md` (NEW); `test/drills/post_restart_arm_notify_drill.sh` (NEW); `RELEASE_NOTES.md` (APPEND); `tests/unit/tools/test_post_restart_arm_notify_banner.py` (NEW) | pending |

**Phase 4 is SANCTIONED IN-SCOPE (r4 fold S4 clarification).**
Phase 4 closes the operator-facing surface: banner text update
in `upgrade_tools.py` (the obsolete "ask me to run
`upgrade_status`" instruction, replaced by the auto-wake
prose), the runbook at
`docs/runbooks/post-restart-arm-notify.md`, the bash drill at
`test/drills/post_restart_arm_notify_drill.sh` (six scenarios
D1–D6), the release-notes line, AND the regression test
(T4.7). Phase 4 is the **feature-complete** commit; the
release cut is a follow-up PR.

**Phase ordering rationale:** Phase 1 establishes the durable
record (the surface Phase 2 consumes); Phase 2 establishes the
delivery (the surface Phase 3 exercises); Phase 3 closes the
edge cases (the surface Phase 4 documents); Phase 4 closes the
operator-facing surface (banner, runbook, drill, release notes).
A phase boundary is GREEN when its per-phase verification (§Per-Phase
Verification in each phase plan) is met AND the existing test
packs are non-regressed.

**Phase coupling:**

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| **Phase 1** | — | tight (Phase 2 consumes Phase 1 helpers: `list_pending_wakes`, `mark_wake_*`, `latest_matching_event` + defines `WAKE_TERMINAL_EVENTS` on top, `PendingWake`) | tight (Phase 3 tests ride the Phase 1 surface) | loose (Phase 4 docs reference Phase 1 by ADR) |
| **Phase 2** | tight | — | tight (Phase 3 long-downtime + paused + revival tests ride the Phase 2 sweep) | loose (Phase 4 drill exercises the Phase 2 wake branch) |
| **Phase 3** | tight | tight | — | loose (Phase 4 unskips the Phase 3 drill smoke) |
| **Phase 4** | loose | loose | loose | — |

**Kill-switch at every phase:** the
`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` env (default ON) is the
operator's opt-out lever; it short-circuits BOTH the arm-side
write (Phase 1) AND the sweep's wake branch (Phase 2). Phase 3
has no source changes; Phase 4 inherits the kill-switch.

**Migration design cross-reference:** ADR-039 is journal-section
(no new table). The migrations dir (`daemon/migrations/`) gains
**no** new `.sql` file from this feature. Any new module the
plans propose uses `from __future__ import annotations` for
Python 3.13 import safety.

---

## 5. Hard Constraints (inherited from Phase 2, verbatim)

> NEVER touch the live/production ensemble environment — it is the
> running environment of Ari and all live agents (~/agents-ensemble,
> port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live
> pids must remain untouched). ALL work/testing/drills in dev and
> demo only. If any plan step would require touching live, mark it
> as USER-GATED and design it as an explicit user-confirmed action.
> Sandbox instances (own port + throwaway PG) are fine.

This constraint is restated in `architecture-recommendation.md` and
`decisions.md`. The wake record is written only for non-live arms
(`system_restart` outright-refused on live; `system_upgrade` on live
only on verified-arm path, which inherits the live-gate F2 surface).
A live-arming wake is structurally impossible by construction.

---

## 6. Open Questions (to resolve at review)

| Q | Question | Where addressed | Status |
|---|----------|-----------------|--------|
| Q1 | When the arming instance is **missing** at boot (e.g. the project was deleted between arm and restart), do we front-door to ari (the canonical fall-back) or surface a notice-only? | `architecture-recommendation.md` §FA5.1; ADR-043 | **DECIDED** — ari fall-back if available, otherwise journaled `arm_notify_no_instance` notice (Phase 2 T5.1 + T5.2 cover both branches) |
| Q2 | When multiple wake records exist for the same instance (e.g. a long downtime with two arm-restart cycles), do we deliver them as ONE wake (coalesce) or as separate wakes? | `architecture-recommendation.md` §FA5.2; ADR-043 | **DECIDED** — coalesce by `arming_instance_id` at delivery time, bounded by `PENDING_WAKE_COALESCE_MAX = 16` (Phase 2 T5.3–T5.6 + Phase 3 T5.13) |
| Q3 | When the wake is delivered but the agent's response is interrupted by another restart mid-turn, do we re-wake on the next boot or accept the loss? | `architecture-recommendation.md` §FA5.3; ADR-043 | **DECIDED** — accept the loss, user inquiry is the recovery (same as D-FA1.2). The one-shot guarantee is structural (status field) |
| Q4 | Should the wake message body embed the full upgrade_status prose, or should the wake be a self-describing pointer and let the agent call `upgrade_status` itself? | `architecture-recommendation.md` §FA3.2; ADR-041 | **DECIDED** — self-describing pointer; the LLM does the work; no LLM in the critical path |

All four open questions from the original plan-overview are
**DECIDED** in `architecture-recommendation.md` + `decisions.md`.
No open questions remain at the time of this synthesis.

---

## 7. ADR Numbering (the renumber record)

The new ADRs in this directory are **ADR-039** through
**ADR-044** (six ADRs, one per focus area). The original draft
used ADR-033–ADR-038; those numbers were **renumbered** to avoid
collisions with the upgrade-subsystem ADRs already in force:

| New range | Renumbered from | Subject | Collided with (pre-existing) |
|---|---|---|---|
| **ADR-039** | ADR-033 | Wake record schema + write atomicity | ADR-033 (halt-semantics posture, cited from `scripts/upgrade/restart.sh:231`) |
| **ADR-040** | ADR-034 | Boot sweep + wake delivery primitive | ADR-034 (splice/journal-append discipline, cited from `scripts/upgrade/ledger_check.py:6,394` + `lib.sh:1464,3085` + `stop-ensemble.sh:213`) |
| **ADR-041** | ADR-035 | Routing preservation (re-stamp user-origin window) | ADR-035 (`stage.sh` `rollback_safe` rider, `self-restart-upgrade-phase2/decisions.md:215`) |
| **ADR-042** | ADR-036 | Terminal-state gating & abandonment policy | ADR-036 (nonce 60min, `self-restart-upgrade-phase2/decisions.md:247`) |
| **ADR-043** | ADR-037 | Edge cases & coalescing | (no collision; the original ADR-037 number was unused) |
| **ADR-044** | ADR-038 | Kill-switch & boot-never-wedge | (no collision; the original ADR-038 number was unused) |

The renumber note is also recorded at the top of `decisions.md`
(matching the format of the existing in-force ADRs).
**Verification (2026-10-03):** zero `ADR-03[3-8]` references
remain in this directory; 45 `ADR-04[0-4]` cross-references
are present across the 6 files (header occurrences + body
occurrences).

**Post-merge invariant:** the renumber note in `decisions.md` is
a planning-only artifact — the live code, the existing ADRs,
and the project-wide ADR registry are unchanged. The note is
a discoverability aid for a future reader; it does not affect
any other artifact in the project.

---

## 8. Architectural Review Flags

These are the items a reviewer should pay particular attention
to; the corresponding risks are in `risk-register.md`.

1. **R-3 (medium severity)** — re-stamp of the user-origin window
   could enable a forged live arm. Mitigation: four-layer defense
   (recorded source already passed F2; in-memory state only;
   source-format validation; TTL bound). **The reviewer should
   verify the four-layer defense is in the Phase 2 implementation.**
2. **R-8 (high severity, low likelihood)** — the
   live-outright-refusal could be bypassed by a future refactor.
   Mitigation: structural coupling (wake in same `journal_write`
   envelope as `pending_op`); kill-switch is NO-OP for live;
   T5.12 is a release-blocker regression pin. **The reviewer
   should verify the wake write is INSIDE the try block that
   follows the live-outright-refusal return, not before it.**
3. **R-7 (medium severity)** — the LLM may not call
   `upgrade_status` on the wake's self-describing pointer.
   Mitigation: explicit body; prompt-level instruction is a
   follow-up (out of scope for this feature). **The reviewer
   should flag the prompt-maintenance follow-up at the user
   sync.**
4. **R-9 (medium severity)** — the `pending_wakes` dict could
   grow unbounded. Mitigation: structural removal on
   delivered/abandoned; `abandon_after` clock bounds
   un-deliverable records; the `+24h GC` follow-up is out of
   scope. **The reviewer should verify the structural removal
   test (T1.5) is GREEN in Phase 1.**
5. **The ADR-039 renumber** — the new ADRs are in the
   ADR-039–ADR-044 range, NOT the original ADR-033–ADR-038 draft.
   The reviewer should verify the renumber note is at the top
   of `decisions.md` and that no `ADR-03[3-8]` reference is
   minted by any file in this directory (the Phase 1 +
   Phase 2 + Phase 3 + Phase 4 plans all use the new range).
6. **R-21 (medium severity) — launcher burst-abort gap
   (architecture delta #9):** the launcher's plain exit-1 path
   (`launcher.sh:902-939`) journals NO terminal event, so the
   wake abandons at grace (600s) and the user receives NO
   notification of the burst-abort itself. The stays-down story
   remains watchdog ADR-025(b) — complementary, not superseded.
   **The reviewer should confirm the acknowledgment (not a fix)
   is the intended scope for this feature.**
7. **Known limitation (architecture delta #13, NICE — flagged,
   out of scope):** `_progressive_sent_sources` is keyed by
   source_id (channel id, e.g. `"discord"`), not full source —
   multi-user concurrent arms on ONE instance could
   cross-suppress each other's progressive chunks (single-user
   arming verified safe); telegram/slack ride the identical
   dispatcher path but were not exercised by the validation
   analysis. Recorded in `decisions.md` Deferred-NICE #13.
8. **R-10 CLOSED** — the boot-ordering risk is resolved by the
   architect's post-`dac38fd8` verification (discard wipe runs
   before sweep construction; workers ready via
   `setup_worker_pool`). Kept in the register (marked CLOSED)
   so the T2.1 pin's rationale stays discoverable.

---

## 9. Sign-off Path (the order of operations)

1. **Reviewer checks** the §8 architectural-review flags.
2. **Reviewer verifies** the renumber is consistent (grep
   `ADR-04[0-4]` in this directory → all hits; grep
   `ADR-03[3-8]` → zero hits in this directory).
3. **Developer implements Phase 1** (arm-side record + tests).
   Phase 1 is mergeable when its per-phase verification is GREEN
   AND the existing test packs are non-regressed.
4. **Developer implements Phase 2** (boot sweep + wake delivery
   + tests). Phase 2 is mergeable when its per-phase verification
   is GREEN AND Phase 1 + the existing packs are non-regressed.
5. **Developer implements Phase 3** (edge cases + structural
   tests). Phase 3 is mergeable when its per-phase verification
   is GREEN AND Phase 1 + Phase 2 + the existing packs are
   non-regressed.
6. **Developer implements Phase 4** (banner + runbook + drill
   + release notes). Phase 4 is the feature-complete commit.
7. **Release cut** (a follow-up PR that bumps the version and
   pushes the tag) is the deploy-side concern; this feature is
   the dev-side completion.

**Hard constraint reminder:** every phase acceptance step on
demo is preceded by a live-pid checkpoint. The drill (Phase 4)
runs against a sandbox install dir; the live install is never
touched. A live-arming wake is structurally impossible by
construction (the live-outright-refusal at
`upgrade_tools.py:2051-2058` pre-empts the journal write).
