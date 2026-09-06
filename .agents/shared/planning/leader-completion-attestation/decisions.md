# Decisions: Leader Completion Attestation

Date: 2026-09-05 (initial), reconciled 2026-09-05 (post-leader ruling pass)
Author: planner[v2] via technical-analysis worker
Target branch: `feature/leader-completion-attestation`
Companion docs: [`technical-analysis.md`](./technical-analysis.md) (SUPERSEDED IN PART), [`architecture-recommendation.md`](./architecture-recommendation.md) (architect adjudication, authoritative), [`requirements.md`](./requirements.md) (post-reconciliation SPEC)

---

## Status Legend

- **CLOSED-by-user** — hard constraint fixed by the user; cannot be reopened by the architect.
- **CLOSED-by-leader** — resolution fixed by the leader (architect + reviewer chain) during the post-author review pass; cannot be reopened by the planner.
- **CLOSED-by-architect** — resolution fixed by the architect (council adjudication); cannot be reopened by the planner without explicit re-referral.
- **RESOLVED** — architect ruling applied; downstream spec layer reflects the resolution; references the architect's decision mechanism.
- **DEFERRED-to-phase6** — relocated to `phase6-fastfollow-plan.md` (C backstop, post-soak); not in MVP.
- **OPEN** — decision pending; architect or user must choose; cannot be closed by the planner. No "default if unresolved" rows — OPEN means OPEN, full stop.

---

## CLOSED-by-user Constraints (Reference)

These are the hard constraints the user fixed. Recorded here so the architect does not propose variants of them.

### C1 — Loop safety: bounded per-instance retry + terminal fallback

**Constraint:** The recovery path MUST have a bounded per-instance retry count with a terminal fallback (let the instance complete + flag/escalate). The precedent is the `loop-breaker` (`max_repairs` cap + counter auto-reset at `daemon/graph.py:1840-1847, :1836-1837`; `_loop_breaker_state` reset hooks at `daemon/manager.py:3734, :3798, :8548`). For THIS feature, `attestation_denied_count` is a row-scoped instance column (per D5 architect ruling), so the in-memory-dict cleanup precedent does not apply; the equivalent is `attestation_denied_count` reset-on-allow and reset-on-terminal-after-bound (C-11 in `requirements.md`).

**Implication:** No unbounded recovery loops. The terminal fallback must be observable (the user must be able to detect a recovery-capped instance).

**Decision-owner:** user (CLOSED).

### C2 — Kill-switch via env, restart-read resolver pattern

**Constraint:** The kill-switch MUST be env-resolved, read at restart. The DEFAULT is **`dry`** at ship (D2, RESOLVED closed-by-architect: tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce`). Three patterns are available:
- **Pattern A** — `pydantic validation_alias` + explicit `load_config` resolver, env > legacy alias > yaml > default, typo-safe (`daemon/config.py:805-844, :2155-2215`).
- **Pattern B** — dual-read cfg AND env (`daemon/config.py:463-506`).
- **Pattern C** — module env resolver + cached global + one-time boot log (WC-wake variant at `daemon/services/instance_messaging.py:114-191`).

**Implication:** No dynamic reload; restart required to flip. Pattern C is the WC-wake precedent and produces a one-time boot log; recommended for the new kill-switches (gate-disable + per-lane-disable for sweep backstop).

**Decision-owner:** user (CLOSED — pattern shape, default value closed per D2: tri-state MODE, default dry).

### C3 — Window N configurable (env/config), not hardcoded

**Constraint:** N (the scanner's lookback window in messages) MUST be configurable via env or config. Default value = N=3 (D4, RESOLVED closed-by-leader: `ENSEMBLE_LEADER_ATTESTATION_WINDOW` Pattern C resolver, restart-read).

**Decision-owner:** user (CLOSED — configurability, default value closed per D4: N=3).

### C4 — Leader-scoped tool via meta.json tools.allow opt-in + fail-closed authz

**Constraint:** The attestation tool MUST be opt-in via `meta.json` `tools.allow` (`agents/leader/meta.json:14-15`) + fail-closed authz (`daemon/tools/_auth.py`). The `PRIVILEGED_TOOL_CATEGORIES` consideration (`daemon/tools/_tool_registry.py:101-103`, currently listing only `system_upgrade`) is closed as **NOT privileged** per D7 ruling (sub-question of D7, RESOLVED closed-by-leader).

**Implication:** The tool cannot be enabled globally; agents that have not opted in will not see it. The 10-step tool-registration checklist (`daemon/tools/upgrade_tools.py:110-143`) is mandatory; missing `CATEGORY_MODULES` entry = SILENTLY INVISIBLE.

**Decision-owner:** user (CLOSED — opt-in shape, sub-question on PRIVILEGED_TOOL_CATEGORIES also closed per D7: NOT privileged).

### C5 — Recovery via durable manager.enqueue_message

**Constraint:** Recovery MUST use `manager.enqueue_message` (facade at `daemon/manager.py:6530-6626` → service at `daemon/services/instance_messaging.py:1960-2073`) which writes `MessageQueue` + `Task` in a single transaction. NEVER RAM `set_injection`. Per JAFP, internal paths use `enqueue_message` only, no `JobItem`.

**Implication:** Recovery is durable, survives restart. The `work_id` field is the stable cross-system UUID4 handle; the facade-forwarding discipline applies if a new kwarg is added (grep `manager.py` + real-dispatch integration test).

**Decision-owner:** user (CLOSED).

### C6 — Recovery origin renders as user-authored

**Constraint:** The recovery message MUST render as user-authored when ingested by the leader. Two paths exist:
- **Default else-branch** in `_prepare_enqueued_message` stamps `MessageType.HUMAN.value` (`daemon/services/instance_messaging.py:1685-1704`, drifted from old `:1310-1319`).
- **`source="api"`** arms the user-origin window (`daemon/manager.py:3159-3197`), which has side effects (SSE notification, message-id assignment, audit log).

The known deferred defect: the else-branch stamps HUMAN for internal callers (cascade_resume, internal_invoke_and_wait); anti-forgery rests on caller discipline. P2.2 plans to add a `USER_ORIGIN_SOURCES` whitelist.

The recovery MUST NOT use the `[SYSTEM NOTE: ...]` data-frame convention (`daemon/graph.py:216-224`) — leaders hallucinate from system-framed reports.

**Implication:** Source value selection has side-effect implications. The exact `source` value to pass to `enqueue_message` is DEFERRED-to-phase6 (D6, moot in MVP per R1: MVP deny path is in-graph only; the durable-enqueue source mapping is the C backstop, post-soak).

**Decision-owner:** user (CLOSED — must render as user-authored), DEFERRED-to-phase6 source value + side-effect analysis (D6).

### C7 — Must-not-break (non-negotiable)

The feature MUST NOT break:
- Normal attested completion (no gate when attestation present).
- Mission finalize (observer `_finalize_job` Step 2 at `daemon/services/job_feedback_observer.py:3703-3758`).
- Revive semantics (terminal→RUNNING at `daemon/services/instance_messaging.py:1867-1909`; PAUSED exempt).
- WC-wake lanes (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF; flip is operator decision).
- Report-injection claim machine (atomic PENDING→INJECTED at `daemon/graph.py:414-490`, `:3622-3658`).
- Existing recovery sweeps (`ReportDeliveryRecoveryService` 5-lane; `WaitingChildrenWatchdog` hourly nudge-only).

**Decision-owner:** user (CLOSED).

### C8 — Test strategy

**Constraint:** Unit tests (scanner, gate decision, recovery injection, loop-guard bounds, kill-switch) + integration tests (full hallucination→recovery→continue). Facade-forwarding duty applies if any new kwarg is added to `enqueue_message` (grep `manager.py` + real-dispatch integration test, precedent at `tests/unit/test_manager_enqueue_message_work_id_required.py`).

**Decision-owner:** user (CLOSED).

---

## CLOSED-by-leader Rulings (Post-Reconciliation Reference)

These are the rulings fixed by the leader (architect + reviewer chain) during the post-author review pass on 2026-09-05. They are recorded here so the spec layer (`requirements.md`) and the architecture recommendation reflect a single coherent design. They CANNOT be reopened by the planner; reopening requires explicit re-referral to the architect.

### R1 — Deny path semantics (nudge-MVP; C5 interpretation fork)

**Ruling:** The deny path of the completion gate MUST be the **in-graph checkpoint-durable `HumanMessage` nudge** mirrored on the `language_check` reminder precedent (`daemon/graph.py:2666-2685`). The leader is RUNNING throughout; the nudge is appended to the existing `state['messages']`; the graph routes back to the `agent` node. There is **no** `manager.enqueue_message` call on deny and **no** instance revival.

**Rationale:** A literal reading of C5 ("every deny MUST enqueue via `manager.enqueue_message`") would force the B deny path to END-then-enqueue-revive — reintroducing the observer/revive race that B exists to eliminate, and degrading B to a detector-only role. Doing both (in-graph nudge AND enqueue) on one deny double-delivers — the enqueued task fires after the eventual attested END and spuriously revives a COMPLETED instance. The leader splits delivery by context:

1. **In-graph deny nudge (shipped, B):** checkpoint-durable in-state `HumanMessage` — pre-END continuation, no RAM-only state (LangGraph checkpoints at node boundaries), instance is RUNNING throughout. Satisfies C5's intent (durable delivery, no RAM-only).
2. **Out-of-graph recovery (C fast-follow, phase6):** durable `manager.enqueue_message` with `source="attestation_recovery"` per C5's letter — cross-restart, revive-capable, the only correct tool for post-completion recovery (the OS-2 / parent-cascade no-leader-turn class that B cannot see by construction).

**Consequence:** the durable-enqueue recovery injector (`attestation_recovery.py`), the D6 source mapping, and the facade-forwarding/JAFP test duties all **move to phase6**. The MVP deny path is purely in-graph. This is a CLOSED-constraint interpretation, not a modification — flagged for user veto in `architecture-recommendation.md` §8.

**Decision-owner:** leader (CLOSED).

### R2 — Gate deny input (pending-wakeup input)

**Ruling:** The gate's deny input MUST be `(attestation_present == false) AND (pending_children == 0) AND (queued_or_expected_wakeups == 0)`. The deny fires ONLY when all three hold simultaneously. If ANY of the three is non-zero, the gate ALLOWS the would-be END without attestation.

**Rationale:** In the original bug class, the children are all TERMINAL (hallucinated "in progress") when the leader turn-end arrives — so `pending_children == 0` is naturally satisfied at the gate's evaluation point. But a healthy delegation turn-end has `pending_children > 0` (children are ACTIVE in WAITING_CHILDREN) OR `queued_or_expected_wakeups > 0` (a fresh wakeup is en route); without the R2 input the gate would nudge-flood those leader instances on every legitimate delegation turn-end. Including `pending_children` and `queued_or_expected_wakeups` in the gate's input kills nudge-flood at the policy level.

**Consequence:** the dry-log schema MUST carry `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, and `messages_scanned` (per `requirements.md` NFR-8 / NFR-16 / FR-10) for W5 measurability and for adjudicating the promote-to-`enforce` decision. The Phase-1 test contract documents a `attest_seen_outside_window=true` rate as the signal that the R2 input is firing correctly during dry-log adjudication.

**Decision-owner:** leader (CLOSED).

### Auxiliary rulings applied (O1–O9, not a separate CLOSED block)

The leader's review pass also ratified the following architecture-recommendation rulings; these are documented in `requirements.md` (Resolved Decisions) and `architecture-recommendation.md` §4 + §5:

- **O1** — Boot assert `N ≤ min_recent_window`: WARN-only (per FR-7 / AC-7.8); gate continues running; violation is operator-visible.
- **O2** — Reset-on-allow + reset-on-terminal_after_bound + documented reset triggers. The earlier planner-stage in-memory-dict cleanup precedent (loop-breaker counter cleanup) is DROPPED — row-scoped DB columns need no per-instance in-memory cleanup hooks. Actual `_loop_breaker_state.pop` sites are `daemon/manager.py:3734, :3798, :8548` (and apply to the loop-breaker, not to the gate's `attestation_denied_count`).
- **O4** — Pause-mid-gate double-increment: idempotent per-denial-epoch upsert OR documented inflation (implementation-defined within FR-13/AC-6.6 constraints).
- **O5–O9** — fast-follow / pre-flip notes handled in `phase6-fastfollow-plan.md` (a different worker's deliverable).
- **Fail-open (C3 → C7)** — any exception in scanner/gate ⇒ allow completion + structured error log; the bootstrap exception set (W4 precedent `graph.py:2663-2688`) deliberately does NOT cover SQLAlchemy `OperationalError` raised by the `attestation_denied_count` ledger DB seam.
- **Mode config** — single tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce`, default `dry`. The single-state-mode env shape is NOT supported on any legacy key.

**Decision-owner:** leader (CLOSED).

---

## OPEN Decision Records

Each record below carries an explicit status. Resolved decisions are PRESERVED here as historical evidence (the architect's ruling is in `architecture-recommendation.md`; the spec layer is in `requirements.md`). OPEN decisions are unresolved — meaning OPEN, full stop; there is no "default if unresolved" mechanism (per the post-reconciliation ruling).

---

### D1 — Gate placement: A / B / C / D / E or hybrid

- **Status:** RESOLVED (CLOSED-by-architect, 2026-09-05 — see `architecture-recommendation.md` §1 D1)
- **Decision-owner:** architect
- **Dependencies:** none
- **Related:** D9 (mission finalize ordering); D10 (tool-call visibility edge cases)

#### Question

Which candidate (or hybrid) is the primary gate placement for the attestation check?

#### Options (per `technical-analysis.md`)

1. **A — Synchronous pre-commit gate at child_reports.** Atomic UPDATE extended with attestation predicate (`daemon/services/child_reports.py:1983`, three sites at `:2545, :2737, :2895`). Smallest blast radius; defense-in-depth UPDATE precedent.
2. **B — In-graph `end_candidate` interception** (language_check pattern). Wrapper returns `END` only when attestation present; recovery routes back to `agent` (`daemon/graph.py:2707-2734`, wiring `:6463`). Zero race with observer Step 2; pure function over `state['messages']`.
3. **C — Async post-completion watchdog sweep.** New lane modeled on `ReportDeliveryRecoveryService` 5-lane (`daemon/services/report_delivery_recovery.py:207`) or `WaitingChildrenWatchdog` (`:312`). Defense-in-depth for B's misses.
4. **D — Tool-as-trigger (inverted control).** Tool call drives/schedules completion via state flag. Subtle interaction with `should_continue` END routing; per-revive re-attest UX cost.
5. **E — Observer-path gate at `_finalize_job` Step 2.** Extend Step 2 (`daemon/services/job_feedback_observer.py:3703-3758`) with `gate_deferred` (`:259-277`, re-arm `:1698`). Smallest blast radius; defer-starvation footgun.
6. **Hybrid: B primary + C backstop.** B catches the would-be END at the source; C is defense-in-depth for cases where B's wrapper failed (e.g., instance spawned before B was deployed). Mirrors WC-wake posture (default OFF for legacy + sweep for stragglers).

#### Trade-offs (summary; see `technical-analysis.md` for full)

| Criterion | A | B | C | D | E | B+C |
|-----------|---|---|---|---|---|-----|
| Race with observer Step 2 | moderate | none | high | none | n/a | none + backstop |
| Race with revived RUNNING | low | none | high | low | low | none + backstop |
| Latency on hot path | small | small | zero | small | small | small + zero |
| Blast radius | small | medium | large | medium | small | medium |
| Testability | high | very high | integration-heavy | medium | high | very high |
| Compaction safety | at-risk | at-risk | at-risk | safe | at-risk | at-risk |
| Defer-starvation | none | none | low | none | HIGH | none + low |
| Pattern fit (precedent) | moderate | best | high | none | moderate | best + high |

#### Resolution

**D1 = B (in-graph pre-END interception) per the architect's adjudication** (`architecture-recommendation.md` §1 + §2 trade-off matrix, D1 verdict `B`, weighted score `3.90-4.00`). The gate is composed into `create_should_continue` as a wrapper-on-the-routing-function mirroring the `language_check` precedent; wired under its **own flag** in BOTH branches of `create_should_continue(language_check_enabled)` (`:2707` has two paths — `language_check=on` AND `language_check=off`; piggybacking on language_check wiring silently disables the gate whenever `language_check_enabled=False`, the common case). Scope = leader-only, enforced at graph-build time via an `agent_id == 'leader'` check; non-leader graphs are untouched.

The C backstop (D6 + durable-enqueue recovery injector) is **DEFERRED-to-phase6** (`phase6-fastfollow-plan.md`) per the leader's R1 ruling (C5 interpretation fork). It addresses the OS-2 / parent-cascade no-leader-turn completion class that B cannot see by construction. The MVP ships B alone; the C backstop lands only after the in-graph gate's dry-soak data is adjudicated. Candidates A, D, E are disqualified per the architect's trade-off matrix.

#### Impacted Components

- B: `daemon/graph.py:2707-2734` (wrapper); `:6463` (wiring); new `attestation_gate` node co-located with `language_check`; its own flag threaded in both branches; graph-build-time `agent_id` check.
- C (phase6 backstop): `daemon/services/attestation_recovery.py`; `daemon/manager.py:6093-6250` (facade wiring); per-lane kill-switch `daemon/config.py:1107-1185`.

---

### D2 — Mode env (kill-switch replacement)

- **Status:** RESOLVED (CLOSED-by-architect, 2026-09-05 — see `architecture-recommendation.md` §1 D2)
- **Decision-owner:** architect
- **Dependencies:** D1 (gate placement determines mode surface)
- **Related:** D8 (dry-run mode)

#### Question

What is the default ship value of the gate's mode env? What is the flip/soak plan?

#### Resolution

**D2 = tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce`, default `dry` at ship** per the architect's adjudication. The tri-state strictly dominates a single-bool / two-env pair design — dry strictly dominates plain OFF (telemetry before commitment) and plain ON (the "bad gate blocks all leader completions" outage class is bounded to log volume); the tri-state avoids the inconsistent-state class that a two-env pair design would create. Promotion to `enforce` is operator-driven after a ≤2-week soak on adjudicated dry-log false-positive rate (mirrors the WC-wake posture for `ENSEMBLE_WC_WAKE_ENQUEUE` per `daemon/services/instance_messaging.py:114-191`).

The single-state-mode env shape (under any prior canonical name) is **NOT a supported surface** post-reconciliation (C-5 in `requirements.md`, AC-7.9 — resolver raises `ResolverError` if set).

**2026-09-06: operator override — default mode enforce (user decision); dry-at-ship rationale superseded; fail-open + off-kill-switch remain the safety valves.**

#### Impacted Components

- Config resolver: `daemon/services/attestation_resolver.py` (Pattern C, module env resolver + cached global + one-time boot log).
- Boot log: `leader_completion_gate: mode=<value> window=<N> bound=<N> gate_locations=[...] N_le_min_recent_window=PASS|WARN`.
- Operator runbook: `docs/setup.md` is updated with the three envs and the dry→enforce flip checklist.

---

### D3 — Gate scope: leaders only, all parents, or all instances

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — confirms architect ruling per `architecture-recommendation.md` §1 D3: leader-only, enforced at graph-build time via `agent_id == "leader"` check; non-leader graphs are untouched)
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1
- **Related:** none

#### Question

Which instances does the gate apply to?

#### Options

1. **Leader-only.** Minimal: only instances with `agent_id == "leader"`. Other agents can finalize freely. Matches the user's scenario exactly (the leader is the parent that hallucinates from child reports).
2. **All parent instances with children.** Generalizes: any instance that has spawned children must attest before completion. Protects the same defect class in any parent agent.
3. **All instances.** Universal: every instance must attest before completing. Strongest safety; highest friction (every agent's flow gets the gate).

#### Trade-offs

| Criterion | Leader-only | All-parents | All-instances |
|-----------|-------------|-------------|---------------|
| Defect coverage | leader scenario only | all parent scenarios | all scenarios |
| Friction on other agents | zero | low | high |
| Scope creep risk | low | medium | high |
| Config surface | 1 env var (or hardcoded) | per-agent-id match | global flag |
| Pattern fit (language_check) | bespoke | scope extension | matches language_check (global) |

The user requested "leaders" specifically; leader-only is the minimal interpretation. All-parents generalizes to the same defect class (any agent that spawns children may hallucinate). All-instances may over-apply (agents that don't spawn children never need the gate).

#### Impacted Components

- `agents/leader/meta.json:14-15` (tools.allow opt-in) — for tool-driven candidates (D7).
- `daemon/graph.py:2707-2734` — wrapper scope: per-instance flag vs global flag.
- `daemon/services/child_reports.py` — pre-commit gate scope: per-instance check vs global check.
- `daemon/config.py` — scope resolution.

---

### D4 — Window N default value + config surface

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — confirms architect ruling per `architecture-recommendation.md` §1 D4: N=3 default, configurable via `ENSEMBLE_LEADER_ATTESTATION_WINDOW` env with Pattern C resolver (restart-read, cached global, one-time boot log))
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1
- **Related:** D10 (compaction interaction)

#### Question

What is the default value of N (number of trailing messages scanned for the attestation tool call)? What env name + Pattern (A/B/C) for the kill-switch resolver?

#### Options (default N)

1. **N=3** — matches user's "default 3, configurable". Smallest scanner window; one in-flight tool call + the post-tool AIMessage.
2. **N=5** — slightly larger; covers "attest + say done + one extra turn".
3. **N=10** — generous; survives one round of agent-thought / Ghost-promise routing.
4. **Compaction-aware window** — scan until N messages OR the most recent summary message, whichever comes first.

#### Options (config surface)

- **Env name:** `ENSEMBLE_LEADER_ATTESTATION_WINDOW` (Pattern A) or `ENSEMBLE_LEADER_ATTESTATION_WINDOW_N` (Pattern B/C).
- **Pattern A** (`daemon/config.py:805-844`): pydantic `validation_alias` + explicit `load_config` resolver, env > legacy alias > yaml > default, typo-safe. Recommended for new envs.
- **Pattern B** (`daemon/config.py:463-506`): dual-read cfg AND env. Simpler; no resolver.
- **Pattern C** (`daemon/services/instance_messaging.py:114-191`): module env resolver + cached global + one-time boot log. WC-wake precedent; recommended for restart-read.

#### Trade-offs

| Default | Pros | Cons |
|---------|------|------|
| N=3 | user-requested; minimal scan; fast | tight; may miss attestation if one extra turn intervenes |
| N=5 | safer; covers one extra turn | larger scan |
| N=10 | most generous | overkill; misses would be a real bug |
| Compaction-aware | survives compaction summary | more complex scanner logic |

| Pattern | Pros | Cons |
|---------|------|------|
| A | typo-safe; canonical precedence | more code |
| B | simpler | no typo-safety |
| C | WC-wake precedent; one-time boot log | cached global requires care |

#### Impacted Components

- Config: `daemon/config.py` (new entry in pattern-specific location).
- Boot log: if Pattern C, new resolver function + boot log call.

---

### D5 — Retry bound default + counter storage + ledger semantics + terminal fallback

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — confirms architect ruling per `architecture-recommendation.md` §1 D5; sub-item close on reset semantics per leader ruling below — overrides the prior vague "instance-revival transitions" wording)
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1, D3
- **Related:** D8 (dry-run mode for observability)

#### Question

What is the default max recovery attempts per instance? Where is the attempt counter stored? What is the terminal fallback behavior when the cap is hit? What are the exact reset triggers?

#### Resolution

Per the architect's adjudication (`architecture-recommendation.md` §1 D5 + §4 + §5 phasing adjustments + C-11 in `requirements.md`), CLOSED-by-leader for reset semantics:

| Sub-decision | Resolution | Notes |
|---|---|---|
| **Retry bound default** | **3** (env `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`) | Mirrors loop-breaker precedent (`daemon/graph.py:1840-1847`); aggressive but safe. Configurable via env (Pattern C resolver). |
| **Counter storage** | **Row-scoped DB columns on the instance row**: `attestation_denied_count` (int) + `completion_gate_escalated` (bool). PG+SQLite-safe migration (fresh-SQLite boot trap is a live hazard — `LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md`). | The 5-path / in-memory-dict precedent does NOT apply to row-scoped DB columns. |
| **Reset semantics (O2) — LEADER RULING (VERBATIM)** | **`attestation_denied_count` has PER-MISSION (per-work-episode) semantics. It accumulates within a mission — in-graph deny-nudges NEVER reset it (this is the loop protection). It resets on exactly four triggers: (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation. It does NOT reset on pause/resume or checkpoint reload.** | Leader ruling 2026-09-05 (closed-by-leader). Supersedes the prior "instance-revival transitions" wording. The four triggers must be reproduced verbatim at every carrier: phase3-plan.md entry criteria + task 3.3 reset enumeration; phase5-plan.md task 5.7 trigger reconciliation; plan-overview.md D5 row + risk row 8; the `attestation_denied_count` reset method `reset_attestation_denied_count(instance_id)` is invoked at trigger #1 and trigger #2 only; triggers #3 and #4 fire at instance-state transitions (instance creation sets the column to 0 by column default; revive-from-COMPLETED-with-fresh-episode is invoked from the `send_message`-revive path per `daemon/services/instance_messaging.py:1867-1909`). |
| **Pause-mid-gate double-increment (O4)** | Idempotent per-denial-epoch upsert OR documented inflation is implementation-defined within FR-13/AC-6.6 constraints (i.e., the gate MUST NOT silently inflate `attestation_denied_count` on `OperationalError`). | See C-7 / NFR-15 / AC-6.6 / AC-13.2. |
| **Terminal fallback** | Complete (allow END) + structured `gate_terminal_after_bound` event + persistent `completion_gate_escalated=true` flag. Crit-note addition is **OPTIONAL** (per the optional observability hardening question in `architecture-recommendation.md` §8 — default NO). | Mission-marker and SSE-alert variants discarded; flag + structured event suffice at MVP. |

The earlier _loop_breaker_state.pop() in-memory-dict cleanup precedent is **DROPPED** for this feature — actual loop-breaker reset sites are `daemon/manager.py:3734, :3798, :8548` (3 sites, not 5), and the in-memory-dict cleanup precedent does NOT apply to row-scoped DB columns anyway.

#### Drift disclosure (per leader ruling)

The previous D5 enumeration named "instance-revive-from-TERMINATED" as a trigger. Per the leader ruling above, revive-from-TERMINATED is REMOVED as a named trigger (it is not in the leader's four). The new enumeration replaces it with "instance creation" as trigger #4. All carrier locations have been reconciled.

#### Impacted Components

- Counter columns: instance row (`daemon/repositories/instance/`); migration at `daemon/migrations/<ts>_attestation_ledger.py` (PG+SQLite-safe); default value of `attestation_denied_count` column is 0 (fires trigger #4 at instance creation).
- Terminal fallback: instance row column (`completion_gate_escalated`); gate decision log schema (`leader_completion_gate` event with `decision=terminal_after_bound`).
- Reset hooks: gate decision function (`attestation_gate`) — attested-allow write (trigger #1) and `terminal_after_bound` write (trigger #2) are in the same tx as the `attestation_denied_count = 0` UPDATE. Triggers #3 and #4 fire at instance-state transitions (NOT inside the gate node).

---

### D6 — Recovery message source value + durable-enqueue recovery injector

- **Status:** DEFERRED-to-phase6 (per R1 ruling)
- **Decision-owner:** architect (ruling); phase6 worker (implementation)
- **Dependencies:** D1 (now resolved as B)
- **Related:** R1 (closed-by-leader); OS-2

#### Question

What `source` value does the durable-enqueue recovery injector pass to `manager.enqueue_message`? What side effects does each choice have?

#### Resolution

**D6 is RELOCATED to `phase6-fastfollow-plan.md`** per R1's C5 interpretation fork. The MVP deny path is in-graph only (per R1); the durable-enqueue path is the C backstop for OS-2 (parent-cascade no-leader-turn), not the MVP deny path. D6's "recovery source value" question is moot in the MVP scope.

The MVP per R1 is:
- **Deny path**: in-state checkpoint-durable `HumanMessage` nudge (`additional_kwargs={'attestation_nudge': True}` — mirror of `language_check` reminder precedent at `daemon/graph.py:2666-2685`) — NO `manager.enqueue_message`, NO instance revival.
- **Origin rendering**: N/A; the nudge is in-state, no origin-stamp is involved (the deferred origin-stamping defect — else-branch stamps `MessageType.HUMAN` for internal callers — is not encountered on this path).

Phase6's durable-enqueue injector will carry:
- `source="attestation_recovery"` value per architect adjudication (D6 opt-2, NOT `"api"` per the disputed user-origin-window side-effects noted in `architecture-recommendation.md` §6 plan-correction #2).
- Phase6 brings the facade-forwarding / JAFP no-JobItem tests and D6 source mapping, both as their own work item.

#### Wording contract (preserved from MVP planning — applies to the nudge text; will also apply to the phase6 durable-enqueue text if it differs)

The nudge text (MVP) — and any future phase6 durable-enqueue text — MUST:
- Read as user-authored prose (NOT `[SYSTEM NOTE: ...]` — `daemon/graph.py:216-224` precedent).
- Use present-tense imperative (`"The work is not yet finished — check current progress and continue."`).
- Be short (one or two sentences) to minimize context footprint.
- NOT name the attestation tool (avoid revealing the gate).
- Be idempotent: if the nudge fires multiple times (compaction, etc.), the wording remains coherent.

User-provided draft: `"The work is not yet finished — check current progress and continue."` — carried verbatim into MVP (FR-4, AC-4.4).

#### Impacted Components

- MVP: in-graph nudge (`daemon/graph.py:6463` wiring) — no D6 surface in MVP.
- Phase6: `daemon/services/attestation_recovery.py` (C backstop); `daemon/services/instance_messaging.py:1685-1704` (source→HUMAN stamp); `daemon/manager.py:3159-3197` (user-origin window — NOT to be used); facade `daemon/manager.py:6530-6626`; tests `tests/integration/test_attestation_facade.py`, `tests/integration/test_attestation_recovery_injector.py`.

---

### D7 — Attestation tool semantics

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — confirms architect ruling per `architecture-recommendation.md` §1 D7: `attest_completion`, no-arg, idempotent (any call in window counts), short confirmation ToolMessage return, NOT privileged)
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1, D3
- **Related:** none

#### Question

What are the tool's arguments (if any), idempotency model, return shape, and exact name?

#### Options (arguments)

1. **No-arg.** Simplest. Tool call alone = attestation. Matches `tool_calls[i].name` scanner.
2. **Structured args** — `summary: str, mission_id: str | None`. Allows the leader to encode a human-readable completion summary + optional mission identifier for cross-checking.
3. **Args + validation.** Tool rejects malformed args (e.g., empty summary).

#### Options (idempotency)

1. **Idempotent: any call counts.** Multiple attestation calls are fine; scanner matches the most recent.
2. **Single-shot: subsequent calls warn.** Encourages the leader to attest once.
3. **Per-mission: tool tracks (instance_id, mission_id) pairs.** Most flexible; needs DB.

#### Options (return shape)

1. **Confirmation frame** (`ToolMessage` content: "Completion attested. You may now finalize.").
2. **Silent** (no return content; just signals the gate).
3. **Structured result** (JSON: `{"attested": true, "mission_id": "..."}`).

#### Options (name)

Candidates:
- `attest_completion` — verb-first; clear.
- `complete_mission` — domain-aligned.
- `mark_done` — colloquial.
- `finalize` — short; but conflicts with internal "finalize" terminology.

#### Trade-offs

| Arguments | Pros | Cons |
|-----------|------|------|
| No-arg | minimal; scanner-friendly | no summary text |
| Structured | richer; mission-id cross-check | more code; need validation |

| Idempotency | Pros | Cons |
|-------------|------|------|
| Idempotent | robust | may mask bugs |
| Single-shot | cleaner intent | brittle (race) |
| Per-mission | most flexible | DB dependency |

| Return | Pros | Cons |
|--------|------|------|
| Confirmation | leader sees the result | extra context |
| Silent | minimal context | no feedback |
| Structured | parseable | extra context |

#### Impacted Components

- Tool registration: `daemon/tools/_tool_registry.py:106`; `CATEGORY_MODULES`; `DYNAMIC_TOOL_NAMES` (`:23-78`); `KNOWN_TOOL_NAMES` regen.
- `agents/leader/meta.json:14-15` — `tools.allow` entry.
- `daemon/tools/_auth.py` — fail-closed authz.
- `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:101-103`) — open sub-question: should attestation be privileged (visible only to leader agents)?

---

### D8 — Dry-run / observability mode when kill-switched OFF

- **Status:** RESOLVED (CLOSED-by-architect, 2026-09-05 — see `architecture-recommendation.md` §1 D8)
- **Decision-owner:** architect
- **Dependencies:** D2 (now RESOLVED — tri-state mode; the dry mode is the dry-run)
- **Related:** C-12 in `requirements.md`; NFR-16; AC-E2E-6

#### Question

Should there be a dry-run mode that logs would-have-recovered events without enqueueing recovery? What does the log line contain?

#### Resolution

**D8 = the tri-state `dry` mode IS the dry-run.** Per the architect's adjudication:

- `mode=off`: legacy behavior (no gate evaluation).
- `mode=dry`: gate evaluates every would-be END, emits structured `leader_completion_gate` decision-log entries with scanner diagnostics (`dry_log_deny_predicate_total`-computable values per Phase 4 task 4.5 canonical schema — i.e. the R2 inputs `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`, `scanned_window_size`; the canonical metric name per CR-4 is `dry_log_deny_predicate_total`), but allows all END (zero side effects).
- `mode=enforce`: gate denies per FR-3.

There is no separate pre-Phase-2 dry-run activity. The instrumented dry-mode observability is in the gate from Phase-1 onward. Dry lines carry scanner diagnostics so the dry→enforce promotion decision is adjudicated on data (per `requirements.md` NFR-16: adjudicated dry-log false-positive rate is the gate to promotion), not conjecture.

#### Impacted Components

- `daemon/services/attestation_gate.py` — gate function emits decision-log entries in `dry` mode regardless of allow/deny choice (same tx as the gate evaluation).
- `daemon/services/attestation_resolver.py` — Pattern C resolver reads `ENSEMBLE_LEADER_ATTESTATION_MODE` (tri-state) and caches the value.
- `docs/setup.md` (operator runbook) — dry-log schema, dry→enforce flip checklist, and the W5 promotion criteria.

---

### D9 — Mission finalize ordering: does recovery need to land before observer Step 2 commits?

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — moot for D1=B per `architecture-recommendation.md` §1 D9: recovery lands before finalize by construction because denied turn never ENDs, so observer Step 2 never fires on it)
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1
- **Related:** none

#### Question

For candidates that gate at finalize time (E) or near it (A), does the recovery message need to land BEFORE observer Step 2 commits the terminal status?

#### Options

1. **Yes — recovery must land first.** The gate holds finalize until recovery has been delivered (e.g., wait for the recovery Task to be claimed). Coordination via `work_id` linkage.
2. **No — recovery can race finalize.** The gate just blocks finalize; recovery fires asynchronously after finalize (Candidate C territory).
3. **Mixed: B (pre-END) primary + E (gate at finalize) backstop.** B blocks END entirely; E catches the case where B's wrapper failed. Recovery lands asynchronously; no pre-commit blocking.

#### Trade-offs

| Option | Pros | Cons |
|--------|------|------|
| Yes — block finalize | mission state consistent with recovery | latency; coordination |
| No — race | simpler | mission may be COMPLETED for a window before recovery fires |
| Mixed | belt-and-suspenders | two surfaces |

#### Impacted Components

- `daemon/services/job_feedback_observer.py:3083` (finalize).
- `daemon/services/child_reports.py:1983` (atomic UPDATE).
- Coordination: `work_id` linkage; `task_id` propagation.

---

### D10 — Tool-call visibility edge cases

- **Status:** RESOLVED (CLOSED-by-leader, 2026-09-05 — confirms architect ruling per `architecture-recommendation.md` §1 D10: (a) ANY-in-last-3-AIMessages scanner window semantics, (b) scan current post-compaction state — safe at default config with N≤min_recent_window coupling enforced, (c) report-injection immunity by construction)
- **Decision-owner:** leader (CLOSED, confirming architect)
- **Dependencies:** D1, D4 (window N)
- **Related:** none

#### Question

How does the gate handle: (a) attestation call followed by more turns (window semantics); (b) compaction folding the attestation message into a summary; (c) report-injection interleaving in the window?

#### Options (a — window semantics)

1. **Match the LAST N messages regardless of content.** Simple; may match an attestation that was already followed by another turn.
2. **Match the most recent AIMessage's tool_calls only.** Stricter; ignores prior turns.
3. **Match if ANY of the last N messages has the attestation call.** Most forgiving.

#### Options (b — compaction folding)

1. **Scan pre-compaction state via `aget_state`.** Reliable; latency cost.
2. **Compaction preserves tool_call shape.** Best long-term; requires compaction service change.
3. **Scan summary text for tool_call hint.** Brittle (LLM paraphrasing).

#### Options (c — report-injection interleaving)

1. **Tool-call presence is sufficient.** Report injection doesn't change tool_calls; safe.
2. **Require the attestation call to be the LAST tool_call.** Stricter; may reject valid cases.
3. **Ignore report-injected messages in the scan.** Most precise; needs marker recognition.

#### Trade-offs

| Option | Pros | Cons |
|--------|------|------|
| (a1) Match last N | simple | may over-accept |
| (a2) Last AIMessage only | strict | may reject valid flow |
| (a3) ANY in last N | forgiving | may over-accept |

| (b1) aget_state | reliable | latency |
| (b2) Preserve shape | long-term correct | compaction service change |
| (b3) Scan summary text | no code change to compaction | brittle |

| (c1) Tool-call sufficient | simple | may not catch edge cases |
| (c2) Last tool_call | strict | brittle |
| (c3) Skip injected | precise | needs marker |

#### Impacted Components

- Scanner function (new): pure function over `state['messages']`.
- `daemon/compaction.py` — preserve tool_call shape (if option b2).
- `daemon/graph.py:414-490` — report-injection marker (if option c3).

---

## Dependency Graph

```
RESOLVED upstream (post-reconciliation):
  D1 → RESOLVED (B in-graph)
  D2 → RESOLVED (tri-state MODE, default dry)
  D3 → RESOLVED (CLOSED-by-leader, leader-only scope)
  D4 → RESOLVED (CLOSED-by-leader, N=3 default + Pattern C resolver)
  D5 → RESOLVED (row-scoped columns; reset-on-allow + reset-on-terminal_after_bound; O2)
  D6 → DEFERRED-to-phase6 (per R1; C backstop, post-soak)
  D7 → RESOLVED (CLOSED-by-leader, attest_completion / no-arg / idempotent / NOT privileged)
  D8 → RESOLVED (tri-state dry IS the dry-run)
  D9 → RESOLVED (CLOSED-by-leader, moot per D1=B — recovery lands before finalize by construction)
  D10 → RESOLVED (CLOSED-by-leader, ANY-in-last-3-AIMessages scanner + post-compaction scan + report-injection immunity by construction)
  R1, R2 → CLOSED-by-leader (architect adjudication)
```

**Decision-order reality (post-reconciliation):** D1, D2, D3, D4, D5, D6, D7, D8, D9, D10 are ALL CLOSED-or-DEFERRED and the SPEC layer (`requirements.md`) reflects them. D3, D4, D7, D9, D10 are CLOSED-by-leader per R-2; D6 is DEFERRED-to-phase6 per R1. There is no implicit fallback for any open decision — all ten decisions are now closed or explicitly deferred.

---

## CLOSED-by-user Summary (Reference Index)

- C1 — Loop safety: bounded retry + terminal fallback. Precedent: `loop-breaker` (`daemon/graph.py:1840-1847`, `:1836-1847`; `_loop_breaker_state.pop` reset hooks at `daemon/manager.py:3734, :3798, :8548` — **NOT** applicable to row-scoped DB columns; per D5 reset-on-allow + reset-on-terminal_after_bound is the equivalent).
- C2 — Mode-env resolver via env, restart-read. (Patterns at `daemon/config.py:805-844`, `:463-506`, `daemon/services/instance_messaging.py:114-191`)
- C3 — Window N configurable, not hardcoded; **now softened** by R1: deny path is in-graph nudge, not durable enqueue recovery. C3 as originally worded is preserved (configurability) but the "durable path" interpretation is RELOCATED to phase6 (D6) per R1's C5 interpretation fork.
- C4 — Leader-scoped tool via `meta.json` `tools.allow` + fail-closed authz. (`agents/leader/meta.json:14-15`, `daemon/tools/_auth.py`)
- C5 — **RECONCILED via R1**: Recovery delivery splits by context — durable-delivery intent (C5's reason) is satisfied by LangGraph checkpointing for the in-graph nudge (B's deny path); the C5 letter (durable `manager.enqueue_message`) applies to the C backstop (phase6). See `architecture-recommendation.md` §3.
- C6 — Recovery origin renders as user-authored — **MOOT per R1**: the MVP nudge is in-state, no origin-stamp is involved. C6 will re-engage for the phase6 durable-enqueue backstop (D6).
- C7 — Must-not-break (non-negotiable list). The OperationalError carve-out is documented as NFR-15 / AC-13.2 / C-7 in `requirements.md`.
- C8 — Test strategy (unit + integration + facade-forwarding). The durable-enqueue facade-forwarding / JAFP tests now live in phase6; MVP carries the in-graph nudge tests.

---

## CLOSED-by-leader Summary (Reference Index)

- **R1** — Deny path semantics: in-graph checkpoint-durable `HumanMessage` nudge, no `manager.enqueue_message`, no revive on deny. Durable-enqueue recovery injector RELOCATED to `phase6-fastfollow-plan.md` (C backstop, post-soak).
- **R2** — Gate deny input requires pending-wakeup input: `pending_children == 0` AND `queued_or_expected_wakeups == 0` AND `attestation_present == false`. Legitimate delegation turn-ends allowed un-attested.
- **O1** — Boot assert `N ≤ min_recent_window`: WARN-only (per FR-7 / AC-7.8).
- **O2** — Reset semantics (leader ruling 1, SUPERSEDES prior "every allow" wording): `attestation_denied_count` reset on **attested allow only** (`allowed_legitimate_pending_wakeup` MUST NOT reset — that non-reset IS the loop protection) + reset on `terminal_after_bound` finalization + reset on revive-from-COMPLETED via a NEW top-level user/mission message + reset on instance creation. The planner-stage in-memory-dict cleanup precedent is DROPPED (row-scoped DB columns need no per-instance in-memory cleanup hooks).
- **O4** — Pause-mid-gate double-increment: idempotent per-denial-epoch upsert or documented inflation, implementation-defined within FR-13/AC-6.6.
- **O5–O9** — fast-follow / pre-flip notes handled in `phase6-fastfollow-plan.md`.

**Decision-owner:** leader (CLOSED). Reopening requires explicit re-referral to the architect.

---

## References

- [`architecture-recommendation.md`](./architecture-recommendation.md) — architect adjudication (authoritative for resolved decisions)
- [`technical-analysis.md`](./technical-analysis.md) — companion trade-off document (SUPERSEDED IN PART by `architecture-recommendation.md`; historical evidence only)
- [`requirements.md`](./requirements.md) — post-reconciliation SPEC layer
- `daemon/graph.py:2462-2533` (should_continue) — END routing
- `daemon/graph.py:2707-2734` (create_should_continue wrapper) — language_check precedent
- `daemon/graph.py:6463` — wiring of the wrapper
- `daemon/graph.py:2666-2685` — `language_check` reminder precedent (in-state `HumanMessage` injection)
- `daemon/services/child_reports.py:1983` (`_process_child_completion_db_sync`)
- `daemon/services/job_feedback_observer.py:3083` (`_finalize_job_db_sync`); Step 2 `:3703-3758`; gate_deferred `:259-277`; re-arm `:1698`
- `daemon/services/instance_messaging.py:1685-1704` (source→HUMAN stamp; moot for MVP per R1); `:1867-1909` (revive); `:1960-2073` (`enqueue_message`)
- `daemon/manager.py:6530-6626` (facade); `:3159-3197` (user-origin window; not used per R1 in MVP)
- `daemon/tools/_tool_registry.py:101-103` (`PRIVILEGED_TOOL_CATEGORIES`); `:106` (`@register_tool_category`); `:23-78` (`DYNAMIC_TOOL_NAMES`)
- `daemon/tools/upgrade_tools.py:110-143` (10-step checklist)
- `daemon/services/report_delivery_recovery.py:207` (5-lane); `daemon/services/waiting_children_watchdog.py:312` (hourly)
- `daemon/config.py:805-844`, `:2155-2215` (Pattern A); `:463-506` (Pattern B); `:1107-1185` (per-lane kill-switches)
- `daemon/services/instance_messaging.py:114-191` (Pattern C, WC-wake)
- `agents/leader/meta.json:14-15` (tools.allow)
- `daemon/graph.py:414-490` (report-injection claim machine)
- `daemon/graph.py:1836-1847` (loop-breaker cap); `_loop_breaker_state.pop` reset hooks at `daemon/manager.py:3734, :3798, :8548` (in-memory precedent only — does NOT apply to row-scoped DB columns; per D5 reset-on-allow)
- `daemon/graph.py:216-224` (`[SYSTEM NOTE: ...]` data-frame convention — MUST NOT be used for recovery)
- `daemon/compaction.py` (compaction folding behavior — to verify pre-implementation)
