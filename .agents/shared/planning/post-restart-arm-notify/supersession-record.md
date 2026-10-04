# Supersession Record — D-FA1.2 (Pull-Model-Only) Replaced by Push-Wake-on-Boot

> **STATUS: BINDING SUPERSESSION** — the ratified pull-model design in
> `self-restart-upgrade-phase2/architecture-recommendation.md` §D-FA1.2 is
> **REPLACED** by the push-wake-on-boot design in this directory.
> Per **user directive 2026-10-03**, this supersession is explicit and
> unconditional for the **post-restart / post-upgrade wake** concern
> (zero-user-action outcome reporting). All other FA1 decisions (D-FA1.1
> pending-op record, D-FA1.3 daemonized executor, D-FA1.4 post-turn
> trigger, D-FA1.5 in-flight semantics) remain **UNCHANGED**.

---

## 1. What was ratified (the document being superseded)

**File:** `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md`
**Section:** D-FA1.2 — "Polling, reporting, and the daemon-never-returns path" ✅ DECIDED (unanimous)
**Verbatim ruling (lines 60–63):**

> - **Who polls:** Ari, on its NEXT turn, via `upgrade_status(run_id)` / `release_info`. **Pull model only** — no reliance on `ReportDeliveryRecoveryService` (verified periodic-only: 300s interval, 10-min age bound, NO boot sweep — `report_delivery_recovery.py:136,275`).
> - **What Ari reports post-restart:** journal terminal entry (`committed` / `rolled_back` + reason + quarantine / `refused` / `halted-for-human`) + `/livez` version verify + `/readyz` composite + `rollbacks_24h`/cooldown state. Prompt-level instruction (P2.2 T3): during daemon-down polling errors, Ari relays "daemon restarting" — never "tool broken".
> - **When the daemon does NOT come back:** the launcher owns recovery (exit-75 tempfail loop → burst-abort latch at 5 crashes/600s; exit-78 refuse-no-loop). The **only component alive when the daemon is down is the watchdog-watcher** → the daemon-down notifier is the **ADR-025(b) watchdog-watcher extension**: watch set gains journal `halt` / burst markers (`.launcher-state`, `releases/state.json`) so stay-down, cap-halt, and sweep-rollback are notified without the daemon. Anything pointed at live = USER-GATED (ladder U6). P2.3 wires it; the architecture depends on it, so P2.3 T8 is now a **hard dependency for the failure-path story**, not optional polish.

## 2. Why this is superseded

The D-FA1.2 pull model is **correct for the steady-state** (Ari drives the
polling; `upgrade_status` is the answer) and is **preserved as the
fallback / user-inquiry path**. It is **inadequate for the
zero-user-action requirement** of the post-restart-arm-notify feature.

The user's request, verbatim: "When system_upgrade / system_restart arms
a run and the daemon goes down and comes back, the SYSTEM must
automatically wake the arming instance so it checks the run outcome
(upgrade_status semantics) and reports back to the user in the SAME
chat/channel where the confirmation happened. Zero user action."

The D-FA1.2 pull model cannot satisfy "zero user action" — by
construction, it requires the user to next-message Ari after the daemon
comes back. That works for the **happy path** (the user is present and
happens to message Ari). It does not work when:

1. The user armed a run via a non-Ari channel (a direct command-line
   operator run) and is now AFK.
2. The user armed a run in a long-running session, switched windows, and
   the daemon restart lands while they are away.
3. The user armed a run expecting "I will be notified when it lands"
   (the contract the arming tool's prose implies) and the pull model
   silently defers until the user re-engages.

The push-wake-on-boot model **complements** the pull model rather than
replacing it: the pull model is preserved for `upgrade_status` /
`release_info` interactive queries; the push model is added for the
"daemon-restart-then-wake" path.

## 3. What is now in force (this directory's design)

| Concern | D-FA1.2 ruling (superseded for wake) | New design (this directory) |
|---|---|---|
| **Who initiates the report after restart** | Ari, on its NEXT user-driven turn (pull) | The boot pipeline of the new daemon (push) — wakes the arming instance via `enqueue_message`, the agent's first turn after the wake drives the report. |
| **What reaches the user** | Nothing — until Ari's next turn | A `[SYSTEM CONTEXT]`-tagged wake message routed to the arming instance, carrying `run_id` + outcome already resolved from the journal. The agent relays via `upgrade_status(run_id)` and the report goes to the **recorded originating source**. |
| **Channel/chat of the report** | Whatever channel the user next-messages on (often Ari's default, NOT the channel where the arm happened) | The recorded `source` from the arm-time stamp — e.g. `discord:user123` — set as the `source` on the enqueued wake so the agent's response routes to that channel. |
| **Idempotency / one-shot** | Implicit — `upgrade_status` is read-only | Explicit — wake record has `status` lifecycle (pending→delivering→delivered) and is cleared on delivery. Unrelated later restarts see no record. |
| **Failure path** | Watchdog-watcher (ADR-025(b), still in force) | Same — the watchdog-watcher is the daemon-DOWN notifier. The push-wake-on-boot covers the daemon-RESTARTED case. The two are complementary, not overlapping. |

## 4. What is **NOT** superseded (D-FA1.2 fall-back)

The pull-model surface remains in force for **all non-wake paths**:

* `upgrade_status(run_id)` continues to be the authoritative
  interactive read of journal state.
* `release_info(section=...)` continues to be the conversational
  visibility tool.
* The prompt-level P2.2 T3 instruction ("during daemon-down polling
  errors, Ari relays 'daemon restarting' — never 'tool broken'")
  remains in force and is **unaffected**.
* The watchdog-watcher ADR-025(b) for the "daemon does NOT come
  back" case is **unchanged** and is the complementary failure
  surface — see §5.

## 5. Interaction with other Phase-2 ADRs (UNCHANGED, noted for review)

| ADR | Status under this supersession |
|---|---|
| **ADR-025 (watchdog-watcher)** | **UNCHANGED** — the daemon-DOWN notifier; the wake-on-boot covers the daemon-RESTARTED case. Together they form the complete "down / back / down-forever" notification surface. |
| **D-FA1.1 (pending_op record)** | **UNCHANGED** for the *pipeline* concern. We **add** a new durable record `pending_wakes` alongside `pending_op` (same atomic surface, different key, NOT cleared by `clear_pending_op` or by `restart.sh`'s post-completion clearing). See `architecture-recommendation.md` §FA1. |
| **D-FA1.3 (daemonized executor)** | **UNCHANGED** — restart.sh / promote.sh still own the pipeline execution. The wake is a *post-pipeline* concern. |
| **D-FA1.4 (post-turn trigger)** | **UNCHANGED** — the post-graph `drain_pending_system_execution` still arms the executor at exact turn-end. The wake is the BOOT-time analog for the case when the turn-end path could not run (daemon died between tool-return and post-graph callback). |
| **D-FA1.5 (in-flight semantics)** | **UNCHANGED** — the in-flight freeze-at-checkpoint model is preserved; the wake is delivered only when the journal terminal event is observed. |
| **D-FA2.4 (env-self-match, live outright-refusal)** | **UNCHANGED** — the live outright-refusal for `system_restart` (upgrade_tools.py:2051-2058) means a live restart NEVER produces a record. The wake surface therefore can NEVER originate from a live-arming path; live-wake remains out of scope (USER-GATED per hard constraint). |
| **ADR-017 (env-target permission model)** | **UNCHANGED** — the arm record is written only when the env-target path is taken (non-live for `system_restart` outright; non-live OR verified live for `system_upgrade`). The wake inherits the same env constraint. |

## 6. Effect on P2.3 wiring (the previously-deferred watchdog dependency)

D-FA1.2 made **P2.3 T8 a hard dependency for the failure-path story**.
This supersession **reduces** the load on P2.3 T8: the "daemon
restarted, arming instance wakes" path no longer needs the
watchdog-watcher at all. P2.3 T8 remains in force for the
**daemon-stays-down** case (the only failure path now), and is no
longer load-bearing for the typical restart cycle.

## 7. Review / ratification record

* **Date:** 2026-10-03
* **Author:** architect (controller) — feature analysis for
  post-restart-arm-notify
* **Directive source:** user directive 2026-10-03, captured in
  project metadata as the feature task spec.
* **Superseded artifact:** `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md` §D-FA1.2 only.
* **Decision bundle:** this directory (`post-restart-arm-notify/`)
  is the single canonical home for the new design — see
  `architecture-recommendation.md` for the full FA1–FA6 analysis,
  `decisions.md` for the ADR-style records, and
  `test-strategy.md` for AC7 compliance.

## 8. Edits to the superseded file (NOT applied by this author)

The superseded document is **historical record**. Per the project's
"do not edit historical prose — it documents the decision context"
convention (see the file's own supersession-note banner at line 3 for
an analogous case), **no edits are applied to the D-FA1.2 ruling
above**. The supersession is **recorded in this file** and is
**discoverable by a future reader of the superseded document** via
the sibling file listing under `.agents/shared/planning/`.

A future editor who wishes to add a "SUPERSEDED" banner inline to
the D-FA1.2 section (analogous to the existing line-3 banner) may
do so on a follow-up patch, with a one-line cross-reference to this
file. Such an inline edit is **not required** for the supersession
to be in force.
