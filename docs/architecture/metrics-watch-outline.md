# The metrics WATCH/ALERT outline — design-only

**Status: v1 is PULL-ONLY (ruling R3).** The `metrics-read` category ships three READ
tools and **no** alert machinery. This document is the **outline for a FUTURE lane's design
doc** — it is deliberately a set of *questions with a recommended shape*, not a plan.

**What this document is NOT.** It plans no tool, no category, no schedule row, no trigger
vocabulary, no delivery path and no configuration knob. It is a cited starting point for a
future lane, folded from the design's §C
(`.agents/shared/planning/metrics-agent-tools/technical-analysis.md` §C, `:1493-1556`).

**The one correction this outline carries (AmM-2iii).** The design's §C.2 absence/staleness
row claims *"Gap: none"* — that is **WRONG**. The `last_sample_age_s > 3 × interval` trigger is
**silently inert for the whole post-restart window** while the ring refills (post-restart
`last_sample_age_s` returns `0.0` — `daemon/services/metrics_query/provider.py:397-404`),
which is exactly the wedged-sampler incident family the trigger claims to cover. The future
lane **must pair the trigger with a ring-state signal** (the shipped tools carry
`read_status.trend_empty_since_restart` for exactly this reason). This is a DESIGN-ONLY
correction — no machinery ships with it.

---

## 1. What the future lane would decide (the outline's spine)

### 1.1 Trigger semantics

| Trigger class | Example | Semantics | Difficulty |
|---|---|---|---|
| **Threshold** | `ensemble_loop_lag_seconds{stat=p95} > 1.0` for ≥ 60 s | the value crosses a line and stays across it | simplest; but the frozen contract has **no threshold vocabulary** — the tool would evaluate it against published state |
| **Delta** | `delta(ensemble_job_queue_depth) > +50` in a 5-minute window | the value CHANGED materially | needs the ring's points; a delta over a downsampled window is approximate |
| **Absence / staleness** | `last_sample_age_s > 3 × effective_interval_s`, or `availability.state == "dark"` for ≥ 3 windows | the signal STOPPED arriving | the most operationally valuable class (a wedged sampler is the incident class) — and the cheapest to evaluate (the contract already exposes `stale` and `availability`) |

**Recommended shape: start with absence/staleness**, because (a) the contract already freezes the
exact inputs (`last_sample_age_s`, `stale`, `availability.state/reason`, `restart_epoch`), (b) it
needs no new vocabulary, and (c) it covers the incident family this project keeps hitting (a
wedged sampler that leaves a frozen line). Threshold and delta come later.

### 1.2 Fit with the existing trigger/scheduling machinery

The house's reusable pieces: the scheduler source adapter (cron-style schedules with a durable
execution ledger), the self-alarm arm (`daemon/services/self_alarm_service.py`,
`MIN_HORIZON_SECONDS = 1800`), and the mid-flight QA fan-out. The future lane must answer:
*does the metrics watch arm a SCHEDULE (poll the metrics substrate on a cadence) or does it ride
the existing event/notification machinery?* The first is simpler and matches the scheduler's
existing durability; the second is cheaper but needs a new publish-triggered seam.

**Recommendation: a schedule-driven poller** — it reuses the durable execution ledger, the
deferred/coalesced delivery path, and the at-most-once invariant for free.

### 1.3 Alert delivery surfaces

See §3 below — exactly ONE primitive is reusable without widening.

### 1.4 Idempotence / de-duplication

A metrics watch must not re-fire on every poll while the condition holds. The scheduler's
deferred/coalesced machinery already provides an at-most-once *delivery* invariant, but the
future lane must also decide the **firing** invariant (fire on transition only? on a rising
edge? with a cooldown?). The house's existing chat bounds vocabulary is N=1/turn (≥ 60 s) and
M=6/rolling-hour per destination (`daemon/tools/notify_user.py`).

### 1.5 Evaluation substrate

A metrics watch MUST evaluate against **published registry state** (the same read-path-purity
rule the door obeys: no DB read, no metric lock, no switch read). It must NOT query the
database for metrics — that is the door's binding clause
(`docs/architecture/metrics-query-contract.md`, the read-path clause).

### 1.6 Scope estimate (an estimate for the future lane, NOT this lane)

| Phase | Content | Rough size |
|---|---|---|
| P0 | Evidence + contract freeze for the trigger semantics (vocabulary, firing invariant, de-dup) | 1 plan round, ~1–2 days |
| P1 | The watch model + the armed-watch substrate (a schedule row + a trigger spec) | ~3–5 days |
| P2 | The evaluator (published-state only) + the firing invariant | ~3–5 days |
| P3 | Delivery wiring (the ONE reusable primitive) | ~2–4 days |
| P4 | The agent-facing arm/disarm tools (a SECOND privileged category, or a sibling of `metrics-read`) | ~3–5 days + a full pin ceremony |
| P5 | Gates (plan council → code council → functional → staged) | ~1 week |

**Total ≈ 3–4 engineer-weeks** — a lane comparable in size to the `metrics-read` lane, NOT a
follow-on. This is the concrete reason R3 (pull-only) is the right posture for v1.

---

## 2. The three trigger classes, mapped onto what the frozen contract already gives

| Trigger class | Frozen input it would read | Gap |
|---|---|---|
| Threshold | `snapshot()` values + the family's `labels` | a **threshold vocabulary does not exist** — the future lane must define one, and it must live OUTSIDE the frozen contract (the contract is frozen) |
| Delta | `history()` points | the delta is computed by the consumer; the future lane reuses the same computation but must decide whether a **downsampled** window's delta is acceptable (the tools surface a `downsampled` flag for exactly this reason) |
| Absence / staleness | `last_sample_age_s`, `stale`, `availability.state/reason`, `restart_epoch` | **none** — every input is already frozen and published. **BUT** the `3 × interval` trigger is silently inert post-restart (see the correction at the top of this document) — the future lane must pair it with a ring-state signal |

---

## 3. The delivery primitives (the researched census)

| Surface | Category | Owner | Mutating? | Reusable for a metrics watch? |
|---|---|---|---|---|
| **scheduler deferred / coalesced** | (no category — a transport) | daemon | writes Task/MQ/JobItem rows | **YES — FULLY GENERIC.** It is exactly *"the target is busy → deliver later → coalesce the repeats"*, it is agent-agnostic, and it is durable + at-most-once. **The ONLY immediately reusable generic delivery primitive.** Its bounds come from pydantic-settings knobs (**NOT DDL**) ⇒ the migration delta stays EMPTY. |
| `self_alarm` | `self-alarm` (privileged) | **ari** | arms a schedule row | **Mechanism generic; target HARDCODED to ari.** A metrics watch for `maintenancer` / `devops` / `wanderer` cannot reuse it as-is. Re-gating the category + the body gate away from `ari` is a *user-visible authority change* — it needs its own lane, its own pin ceremony, and a deliberate decision about whether `ari` keeps exclusivity. |
| `notify_user` | `notify-user` (privileged) | **ari** | writes to the user's chat | **Mechanism generic; category + body gate ari-EXACTLY.** Same scope-widening cost as `self_alarm` — plus it writes to the *user's* channel, the single most sensitive surface in the daemon. |
| `board_*` / `board_link` | `board`, `board-link` (privileged) | **ari** | writes board rows / mints a passcode | **Pattern generic** (a durable, global, shared evidence surface) but the namespace is **ari-owned and global**; opening it to other agents is an explicit user decision, not a default. |
| `watchover` | (no category — a service) | `watcher` | activates a watcher on an instance | **NO — IT IS NOT A DELIVERY CHANNEL.** It is an LLM evaluator that intercepts every tool call of a watched instance *before execution* and returns Allow/Deny. It has no queue, no delivery, no recipient. Its only generic value is the **pause-first-then-quiesce choreography** — which a watch may need IF it ever mutates daemon state while an instance is running. |

**The design implication, verbatim from the research:** *"the only **immediately** reusable
delivery primitive is the **scheduler deferred/coalesced delivery** path, because it is
agent-agnostic and already durable + at-most-once. The ari-owned surfaces (`self_alarm`,
`notify_user`, `board_*`) would each need a per-agent re-gating lane."*

---

## 4. The future lane's open questions (NOT this lane's plan items)

1. **May metrics-triggered alerts reach non-ari agents?** This is the **explicit open question**
   and it directly determines the scope-split: if alerts may only ever reach `ari`, the future
   lane can reuse the ari-owned surfaces (with an ari-only recipient) and the scope model stays
   simple; if alerts may reach `maintenancer` / `devops` / `wanderer`, the future lane must
   build a **non-ari delivery path** (the scheduler's deferred/coalesced path into a target
   agent's session) and **must** re-gate `self_alarm` / `notify_user` or build a sibling
   surface. **This question also bears back on the `metrics-read` lane's scope decision**: the
   READ surface is granted to three non-ari agents while the future ALERT surface may end up
   ari-only. That asymmetry is fine, and the shipped docs state it explicitly (the
   `CATEGORY_DOC` and the three `tools_note.md` sections).
2. **What is the notification budget?** The chat surfaces already carry N=1/turn (≥ 60 s) and
   M=6/rolling-hour per destination. A metrics watch needs its own equivalent, or it inherits
   the chat surface's (which is shared with `ari`'s other notifications).
3. **Who may ARM a watch?** An agent-readable trigger is a durable write; arming is not a read.
   If arming is agent-facing it needs an ACT category (a `metrics-watch` sibling of
   `instance_ops`) with its own audit ledger — a **write** surface, unlike the read-only
   `metrics-read` category.
4. **Does a watch survive a restart?** The ring is in-RAM and lost on restart;
   `restart_epoch` makes the loss visible. A watch whose evaluation window is entirely in-RAM
   has a cold-start blind spot — the future lane must decide whether the arming state is durable
   and whether the first evaluation after a restart is suppressed.

---

## Provenance

* Folded from `.agents/shared/planning/metrics-agent-tools/technical-analysis.md` §C
  (`:1493-1556`) — the design is the binding input; this document ORGANISES it, it does not
  redesign it.
* The one correction (the post-restart inertness of the `3 × interval` trigger) comes from the
  architect fold **AmM-2iii** (recorded in `decisions.md` Part 5).
* Authored during the `metrics-agent-tools` lane's close-out (P-7) as a **close-out artefact**,
  not a planning deliverable — the future lane still owns every open decision in §4.
* `docs/` is repo-only: this document is **not** part of the release payload.
