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

---

## 2026-09-06 — Waiting-children false-deny incident fix (option-a, additive third R2 input)

**Status:** APPLIED 2026-09-06 on `feature/leader-completion-attestation`.

### Context

Incident 809e2a59 (2026-09-02 self-diagnosis): a leader escalated to
`terminal_after_bound` while a transitive grandchild was still alive.
Root cause: the watcher lifecycle is per-TURN
(`child_still_running_defer` at `daemon/services/child_reports.py:3646-3695`,
the 02fb2e01 un-wedging fix) DELIBERATELY fires the parent's watcher
when the child defers to `waiting_children` — so the parent's
`pending_children` drops to 0 while the deeper child mission continues.
`count_pending_for_target` only counts direct PENDING watchers
(`daemon/repositories/dependency_bus/repository.py:429-482`), so the
gate saw `pending_children=0`. Deferral emits no report / no task row,
so `get_queued_or_expected_wakeups` also returned 0 (deferral excludes
claimable rows). The gate correctly per-spec decided DENY → escalate
to `terminal_after_bound` (instance `809e2a59` escalated at 09:30:19
while the child `a135fb55` lived until ≥09:59). TOCTOU excluded; zero
gate-error rows.

### Decision (option-a)

**Add an additive THIRD R2 input — `live_descendants`** — counted by a
new manager facade `InstanceManager.count_live_descendants(instance_id)`
that BFS-walks the permanent `instances.parent_id` lineage (root
EXCLUDED) and counts descendants whose status is NOT IN {COMPLETED,
TERMINATED, ERROR, FAILED}. The R2 deny predicate becomes a three-input
conjunction: `not attested AND pending_children == 0 AND
queued_or_expected_wakeups == 0 AND live_descendants == 0`. Same
decision value — `ALLOWED_LEGITIMATE_PENDING_WAKEUP` — NO new enum
member (decision enum stays 5-valued).

### Why option-a (and not option-b / option-c)

- **Option-b** (touch the watcher lifecycle) was REJECTED — the
  per-turn semantics are DELIBERATE (the 02fb2e01 un-wedging fix). A
  change there re-introduces the un-wedging class of bug.
- **Option-c** (gate-internal sweep over instances) is structurally
  identical to option-a but lacks the canonical facade shape; option-a
  is the additive, minimum-surface-area fix that mirrors the existing
  two R2 facades (decision tree stays a pure function over inputs).
- **Option-a reads INSTANCE tree, NOT dependency_bus.** This is the
  key design choice: the watcher lifecycle emits PENDING rows for
  direct children only and DELETES them on deferral fire — the bus
  is structurally silent about transitive live descendants. Only the
  permanent `instances.parent_id` lineage carries the durable signal
  (and it survives completion / error / terminate / revive).

### Performance

- BFS bounded at `InstanceManager.LIVE_DESCENDANTS_BFS_CAP = 500`
  (the gate sits on the routing hot path with a 20ms P95 budget; the
  current gate P95 is ~0.016ms — keep that order of magnitude). The
  cap is well above any realistic leader subtree; admin-tool-only
  territory past it. The unit-test pins both the cap value and the
  cap behavior.
- The BFS uses `SQLModelInstanceRepository.get_tree_ids_permanent`
  precedent (which already caps at `_MAX_TRAVERSAL_DEPTH = 256` and
  WARN-logs on cap-hit); we apply our own additional slice cap on the
  descendant set (the visited set minus the root).

### Schema change

`CANONICAL_LOG_SCHEMA_FIELDS` grows from 15 → 16 fields — `live_descendants`
appended (positioned after `queued_or_expected_wakeups` in the field
order). The canonical log format string emits the new field, and the
DB-seam fail-open path reports `live_descendants=-1` alongside the
existing two `-1` sentinels. The scanner-fail-open path also reports
`live_descendants=-1` (UNKNOWN on that path; never the meaningful 0).

### DO NOT TOUCH

- Watcher lifecycle (`child_still_running_defer` per-turn semantics).
- `dependency_bus` / `count_pending_for_target`.
- The existing two R2 inputs.
- Nudge text, deny/bound/escalation semantics, mode handling.
- The 5-value canonical decision enum (no new member).

### Tests (acceptance suite — all required, all green at ship)

(a) repro of exact 809e2a59 incident class — child defers to
`waiting_children` (watcher FIRED), grandchild RUNNING → gate returns
`allowed_legitimate_pending_wakeup`, NO nudge, NO counter write;
(b) transitive descendant at depth 2+ counts;
(c) all descendants terminal, no watchers / wakeups → DENY still fires
(regression guard: the ORIGINAL protection must survive);
(d) ERROR / FAILED descendants do NOT count as live (terminal set);
(e) facade unit tests incl. the BFS cap (pin cap value + cap behavior);
(f) log row carries `live_descendants` (drift pin);
(g) P95 timing sanity (gate stays inside the 20ms NFR-1 budget).

### Files

- `daemon/manager.py:8594-8683` (new facade `count_live_descendants` +
  `LIVE_DESCENDANTS_BFS_CAP = 500`).
- `daemon/services/attestation_gate.py:24-30` (R2 docstring); `:284`
  (GateDecision field); `:297, :345-346` (decide arg + docstring);
  `:394-401` (R2 allow predicate); `:467` (CANONICAL_LOG_SCHEMA_FIELDS
  entry); `:528, :556` (evaluate docstring); `:630, :679, :689,
  :708, :719, :733, :750, :762` (evaluate implementation); `:802`
  (scanner-fail-open path).
- `docs/setup.md:545, :548, :576` (schema documentation — 16 fields,
  third-input predicate, deny-log diagnostic surface).
- `requirements.md` — append-only FR-3 / FR-10 third-input note.
- `tests/support/conftest.py:117-180` (attestation_manager_factory
  extended with `live_descendants` kwarg, mirroring pending_children /
  queued_wakeups).
- `tests/unit/test_attestation_gate.py` (decide() matrix gains
  live_descendants arm; canonical schema field assertion grows to 16).
- `tests/integration/test_attestation_live_descendants.py` (NEW —
  acceptance suite (a)-(g)).

---

## 2026-09-06 — Conditional attestation (delegation-gated) + system-context nudge header (Phase 6 fastfollow, user decision)

**Status:** APPLIED 2026-09-06 on `feature/leader-completion-attestation`.
**Trigger:** User decision after observing the unconditional `attest_completion` requirement produced unnecessary friction on quick-turn commands (the user's quoted examples: quick follow-up questions, chart requests). The unconditional-MUST gate forces a leader who produced a simple non-delegating answer to round-trip through `attest_completion` for no correctness reason — a "the leader is hallucinating completion" failure mode that the gate is built to prevent does NOT apply when no children were dispatched.

### Context

The original Phase 1 contract instructed the leader LLM to call `attest_completion` BEFORE declaring done in plain text, on every turn (unconditional). This catches the leader-hallucinating-completion failure mode BUT also fires on missions where the leader did not delegate to any child — plain text answers to follow-up questions, chart renderings (the `generate_chart` tool call alone), clarification responses. Each such turn would (a) require the toolcall and (b) on every would-be deny, the gate's in-graph nudge got injected — adding noise without protecting anything.

The user-spec'd change: the gate is CONDITIONAL on delegation. A `send_message` tool call since the last real user message flips the conditional requirement ON. Without `send_message`, the gate allows END without demanding `attest_completion`. The user's quote: "no delegation, no hallucination risk."

### Decision (option-a, additive — same as the prior third-input pattern)

The conditional-attestation scanner walks the AIMessage tail at-or-after the last REAL user message and detects any `send_message` tool call. The denial predicate is gated on `delegation_since_last_user`:
- `True` ⇒ existing deny / nudge / bound / escalation logic UNCHANGED.
- `False` ⇒ ALLOWED without demanding the toolcall (counter untouched, no nudge, no metric tick).
- DEGENERATE FALLBACK: if no real user message exists in the conversation, the conservative path falls back to walking the WHOLE message list — `delegation_since_last_user` becomes `True` IFF any `send_message` tool call appears anywhere in history. Defense against silent regression if the real-user anchor drifts.

A 17th canonical log schema field `attestation_required: bool` (POSITIONED AFTER `live_descendants` in the canonical tuple, grouped with the conditional/R2 input family) carries the verdict into the operator log; supplementary diagnostic fields `last_real_user_found`, `last_real_user_index`, `first_delegation_after_last_user_index`, `delegation_tool_call_total`, `delegation_since_last_user` ride the format-string log alongside the canonical tuple. Same Pattern C additive shape as the prior `live_descendants` amendment.

The R2 decision tree grows by ONE branch in `decide()` — `enforce + not attestation_required → ALLOWED` (counter unchanged, no nudge). Inserted as branch (3), BEFORE the existing attested-allow branch (now (4)); the rest of the tree is a re-numbering only.

### Self-reference trap (the must-design-out failure mode)

**Critical design trap:** the deny-path nudge is injected as a `HumanMessage`. If the `is_real_user_message` predicate allowed it through, the deny → nudge → next-turn-end sequence would reset the delegation window on every deny — the gate would relax as soon as a deny fired, and the feature would self-defeat.

The exclusion predicate (defense in depth — every metadata surface the codebase already carries) reads:

1. `not isinstance(HumanMessage)` — type gate (AIMessage / SystemMessage / ToolMessage / RemoveMessage excluded).
2. `additional_kwargs["attestation_nudge"] == True` — the gate's own deny injection (THE critical exclusion).
3. `additional_kwargs["injected_message"] == True` OR `additional_kwargs["is_synthetic"] == True` — the `_make_context_message` context-block factory marker AND the synthetic-system marker.
4. `additional_kwargs["source"]` starting with `"internal_report:"` (child-report convention from `_frame_injected_report`) OR `"internal_agent:"` (agent-to-agent dispatch convention from `instance_messaging.py`).
5. `content` starting with `"[SYSTEM CONTEXT:"` — content sentinel (canonical CONTEXT_PREFIX from `context_messages.py:_make_context_message`). Belt-and-suspenders if a future kwargs shim ever drops a flag.

### Nudge amendment (system-context header + self-sufficiency)

The `ATTESTATION_NUDGE_TEXT` constant is now led by a single non-blank header line:

```
[SYSTEM CONTEXT: Completion Check Nudge]
```

followed by a blank line and the body. The header is a system-origin marker so the LLM recognizes the message as system-authored at parse time (despite the underlying message role remaining user-authored — the header is the marker). The body has been expanded to:
- restate the CONDITIONAL semantics ("This gate is CONDITIONAL on delegation: it fires ONLY when a child was dispatched (a `send_message` tool call happened) since the last real user message. Plain questions, chart requests, and other non-delegating turns do NOT trigger this gate. When you have dispatched a child this mission, the work is not complete until you attest.");
- keep the two-step teaching ("FIRST deliver your full detailed final report as its own message; THEN call `attest_completion` ALONE as a subsequent step — never bundle the report into the attestation tool-call message");
- keep the substance ("the work is not yet finished — check current progress (tasks/children status) and continue") and the MUST-for-delegated-missions reminder.

The prompt contract is now SLIM:
- `agents/leader/rule.md` — replaced the unconditional MUST-call block with a CONDITIONAL "When this mission DID delegate (any `send_message` tool call since the last user message)" block. The unconditional `Before declaring yourself done, you MUST call the attest_completion tool` sentence is RETRACTED.
- `agents/leader/workflow.md` — the one-line pointer names the tool + the rule canonical home + the conditional semantics; no verbatim restatement.
- `daemon/tools/attestation.py` — module docstring and `CATEGORY_DOC` softened from "the leader LLM MUST call" to "the leader LLM calls" + a CONDITIONAL-semantics paragraph pointing at `daemon/services/attestation_scanner.py` (`is_real_user_message` + `scan_delegation_after_last_user`).
- The marker kwargs on the nudge `HumanMessage` are UNCHANGED (`additional_kwargs["attestation_nudge"] = True`); the in-graph seam stays the same.

The unconditional-MUST contract fragments are GONE from `rule.md`; the prompt-contract test pins both the new conditional fragments AND the absence of the retracted unconditional fragments.

### Convention exception (appendix — `[SYSTEM CONTEXT: ...]` on a deny-path HumanMessage)

Per the "App Architecture blueprint", `[SYSTEM NOTE: ...]` frames are data-only. The user explicitly asked for a `[SYSTEM CONTEXT: Completion Check Nudge]` header on the deny-path HumanMessage so the LLM recognizes it as system-origin at parse time. This is an APPEND-ONLY exception: the existing `[SYSTEM CONTEXT: …]` injection convention (the `CONTEXT_PREFIX` from `daemon/services/context_messages.py`) is reserved for `_make_context_message` content blocks. The deny-nudge header REUSES the `[SYSTEM CONTEXT: ` prefix deliberately so the predicate's content-sentinel exclusion (exclusion ladder step 5) catches it symmetrically — a single exclusion class for "HumanMessages whose body starts with `[SYSTEM CONTEXT:`". No new prefix is introduced; no existing prefix convention is broken.

### Performance

The conditional scanner walks the AIMessage tail at-or-after the last real user message. In the worst case (long conversation with no real-user anchor, fallback to whole list) the walk is O(len(messages)) AIMessage inspections — bounded by the proactive-compaction summary boundary. The cost is amortized into the existing gate-decision budget; no new hot-path I/O. P95 timing unchanged.

### DO NOT TOUCH

- The decision enum value set (5-valued — no new member; `ALLOWED` is reused with the schema's `attestation_required=False` flag).
- The marker kwargs on the deny-nudge injection (`attestation_nudge=True`, `attestation_nudge_denied_count=<n>`) — back-compat.
- The R0 inputs (pending_children, queued_or_expected_wakeups, live_descendants) — unchanged.
- The `attest_completion` tool semantics — unchanged (idempotent no-op).
- The 5-value canonical decision enum.

### Tests (acceptance suite — all required, all green at ship)

(a) delegation mission without attest → DENY + nudge (unchanged protection) — pinned in `tests/unit/test_attestation_nudge_inject.py::test_deny_injects_checkpoint_plain_dict_and_routes_to_agent` with a `send_message` AIMessage in the mission state;
(b) quick-question mission (real user msg, NO send_message, plain answer, no attest) → ALLOWED, `attestation_required=False` — pinned in `tests/unit/test_attestation_nudge_inject.py::test_quick_question_mission_allows_without_attest` AND in `tests/unit/test_attestation_conditional_gate_outcomes.py::test_quick_question_returns_allowed_with_attestation_required_false` + full-graph `test_quick_question_full_graph_terminates_without_nudge_or_attest`;
(c) chart mission (`generate_chart` toolcall, no send_message) → ALLOWED — pinned in `tests/unit/test_attestation_conditional_gate_outcomes.py::test_chart_request_returns_allowed_with_attestation_required_false` + full-graph `test_chart_mission_full_graph_terminates_without_nudge_or_attest`;
(d) delegation + attest → ALLOWED, counter reset — pinned in `tests/unit/test_attestation_conditional_gate_outcomes.py::test_delegation_attested_returns_allowed_with_reset`;
(e) THE SELF-REFERENCE TRAP — deny-nudge fires, then next turn-end STILL requires attestation (nudge did NOT reset the window) — pinned in `tests/unit/test_attestation_conditional_scanner.py::TestSelfReferenceTrapDenyNudgeDoesNotResetWindow` (two tests) AND `tests/unit/test_attestation_conditional_gate_outcomes.py::TestSelfReferenceTrapEvaluateLevel::test_after_deny_nudge_gate_still_requires_attestation`;
(f) child-report HumanMessage does not reset the window — pinned in `tests/unit/test_attestation_conditional_scanner.py::TestRealUserMessageExclusionClassMatrix::test_child_report_is_excluded` AND `tests/integration/test_attestation_live_descendants.py` (the original live_descendants tests retain their child-mission shape and continue to pass with the conditional gate ON);
(g) no-real-user-message fallback — pinned in `tests/unit/test_attestation_conditional_scanner.py::TestScanDelegationAfterLastUser::test_no_real_user_message_falls_back_to_whole_list` + `TestConditionalGateEvaluateOutcomes::test_no_real_user_message_with_stale_delegation_flips_to_required`;
(h) nudge header present + marker unchanged — pinned in `tests/unit/test_attestation_nudge_inject.py::test_nudge_header_is_first_nonblank_line_and_marker_unchanged`;
(i) scanner unit matrix for the exclusion classes (real / nudge / [SYSTEM CONTEXT] / child_report / internal_agent / synthetic / AIMessage / SystemMessage / content-sentinel-only / combined-marker-and-source) — pinned in `tests/unit/test_attestation_conditional_scanner.py::TestRealUserMessageExclusionClassMatrix` (9 cases).

### Files

- `daemon/services/attestation_scanner.py` (new) — `is_real_user_message` predicate, `find_last_real_user_index`, `DelegationScanResult`, `scan_delegation_after_last_user`, `DEFAULT_DELEGATION_TOOL_NAME = "send_message"`. Pure-function module; no I/O.
- `daemon/services/attestation_gate.py` — `decide()` grows `attestation_required` keyword-only param (branch (3) — `enforce + not attestation_required → ALLOWED`); `CANONICAL_LOG_SCHEMA_FIELDS` grows 16→17 fields (`attestation_required` appended after `live_descendants`); `GateDecision` grows 5 supplementary diagnostic fields (`delegation_since_last_user`, `last_real_user_found`, `last_real_user_index`, `first_delegation_after_last_user_index`, `delegation_tool_call_total`); `evaluate()` calls `scan_delegation_after_last_user` and threads the verdict through `decide()` + the format-string log emission.
- `daemon/graph.py` — `ATTESTATION_NUDGE_TEXT` gains the `[SYSTEM CONTEXT: Completion Check Nudge]` header line + the conditional-semantics body + the self-sufficient two-step contract. Marker kwargs on the deny-path injection UNCHANGED.
- `daemon/tools/attestation.py` — module docstring + `CATEGORY_DOC` + tool docstring + `_full_doc_` softened to conditional language.
- `agents/leader/rule.md` — replaced the unconditional `### 📜 Completion Attestation (LCA feature, Phase 1)` block with `### 📜 Completion Attestation (LCA feature — conditional, 2026-09-06)` (the conditional MUST-for-delegated-missions contract).
- `agents/leader/workflow.md` — the one-line pointer refreshed to identify the conditional semantics.
- `requirements.md` — append-only amendment section (see immediately below).
- `tests/unit/test_attestation_prompt_contract.py` — `CONTRACT_FRAGMENTS` rewritten for conditional semantics; `UNCONDITIONAL_MUST_CONTRACT_FRAGMENTS` (absent-pins the retracted unconditional contract); `CONTRACT_HEADING_ANCHOR` retargeted; new `test_unconditional_must_contract_removed` parameter; new `test_pointer_names_conditional_semantics` on workflow.md.
- `tests/unit/test_attestation_nudge_inject.py` — `test_deny_injects_checkpoint_plain_dict_and_routes_to_agent` injects `send_message` AIMessage (delegated-mission matrix); NEW `test_quick_question_mission_allows_without_attest` (core case b); NEW `test_nudge_header_is_first_nonblank_line_and_marker_unchanged` (case h).
- `tests/unit/test_attestation_conditional_scanner.py` (NEW — 26 tests, exclusion-class matrix + delegation walk + self-reference trap + DelegationScanResult shape pin).
- `tests/unit/test_attestation_conditional_gate_outcomes.py` (NEW — 15 tests, evaluate()/graph-node matrix for cases (a)/(b)/(c)/(d)/(e)/(g) + canonical schema 17-field pin + full-graph delegated-still-denies pin).
- All integration tests that previously hardcoded `NUDGE_TEXT = (...)` rewritten to `from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT` — the single-source-of-truth fix eliminates per-file prose drift.
- All integration tests that previously inferred a non-delegated baseline updated to anchor the mission as DELEGATED (a `send_message` AIMessage as the scripted model's first response) — the legacy deny / wakeup-allow / bound-escalation / counter-reset semantics continue to exercise against the conditional-gate-ON branch.

---

## Phase 6 fastfollow (2026-09-07) — Inline-LLM completion-report judge

### Context (append-only)

The Phase 6 fastfollow adds a per-deny-attempt inline-LLM judge that asks "are the leader's last messages a REAL completion report?" before injecting the in-graph nudge. The judge is a pure inline chat completion (no instance spawn, no message persistence). It is invoked ONLY on the would-be-deny path: after the gate has resolved to `Decision.DENIED` and BEFORE the counter increments / the nudge is injected. The judge-yes verdict flips the would-be-deny to `Decision.ALLOWED` (no nudge, no counter increment); the judge-no verdict (or any error / timeout / unparsable JSON path) falls through to the existing deny+nudge path unchanged.

The motivation: a leader LLM can satisfy the conditional-attestation branch (delegated mission, no `attest_completion` in window) yet still have authored a genuine, detailed completion report — outcomes, evidence, follow-ups. The gate's windowed scanner only looks for the `attest_completion` tool call. A short prose recap delivered as the final AIMessage is the false-positive pattern (the leader DID finish, but the tool call never happened because the LLM didn't think it was required when the work was already complete). The judge disambiguates: a conservative LLM yes-or-no on "is this a real completion report?" before the nudge.

### RESOLVED-2026-09-07 — Judge service (D13)

**Resolution:** Ship an inline-LLM judge service at `daemon/services/attestation_report_judge.py` that:

* Takes the SAME scanned message window the gate already uses (last `ENSEMBLE_LEADER_ATTESTATION_WINDOW` AIMessages, content truncated to a sane cap);
* Calls the LLM with a STRICT system prompt that judges whether the final messages constitute a genuine, detailed completion report (outcomes, evidence, follow-ups — NOT a short summary, NOT mid-work status text; conservative);
* Demands STRICT JSON output `{"is_complete_report": <bool>, "reason": "<one-sentence rationale>"}`;
* Parses STRICTLY — unparsable = NOT a report (conservative fall-through);
* Returns a `JudgeResult` dataclass carrying `is_complete_report`, `verdict` (`"yes" | "no" | "error" | "timeout" | "unparsable"`), `reason`, `model`, `latency_ms`, `error_class`;
* NEVER raises — every error path (timeout, exception, unparsable JSON) resolves to `is_complete_report=False` so the deny+nudge fall-through is always available.

**Async entry point:** `judge_completion_report_async(messages, *, config, window, timeout_s)` — used by the gate node (the gate's `attestation_gate_node` is async).

**Sync entry point:** `judge_completion_report_sync(messages, *, config, window, timeout_s)` — used by sync callers (worker threads, scripts). Uses `asyncio.run`. Cannot be called from inside a running event loop — gate node uses the async entry point.

**Bounds:**
* `JUDGE_TIMEOUT_S = 10.0` (wall-clock cap; belt-and-braces `asyncio.wait_for` wraps the facade-wrapped call; the HA facade's `wall_clock_cap_s` is the primary defense);
* `JUDGE_MAX_INPUT_CHARS = 12,000` (per-message budget = `MAX / count` so the total stays below cap);
* `JUDGE_MAX_OUTPUT_CHARS = 400` (defensive ceiling on LLM reply size; oversized payloads are truncated then re-parsed conservatively).

**Decision-owner:** architect (RESOLVED 2026-09-07).

### RESOLVED-2026-09-07 — Kill-switch (D14)

**Resolution:** Pattern C sibling resolver at `daemon/services/attestation_judge_resolver.py` mirroring the existing `attestation_resolver.py` shape:

* `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (default ON; `=0` / `=false` / `=no` / `=off` disables; restart-read);
* `is_llm_judge_enabled()` cached-global resolver (one-shot boot log via the parent resolver's `emit_attestation_boot_log`);
* `reset_llm_judge_resolver_for_tests()` test-only cache reset (also wired into `reset_attestation_resolver_for_tests` so a single call clears both caches).

The gate config dict gains a `llm_judge_enabled: bool` field (default `True`) via `attestation_gate.build_gate_config(...)`. The gate node reads both sources: the env resolver AND the gate-config field. Either being `False` skips the judge.

OFF = pre-feature byte-identical: judge never invoked, deny+nudge path runs as before. The gate emits NO judge log lines when OFF.

**Decision-owner:** architect (RESOLVED 2026-09-07).

### RESOLVED-2026-09-07 — Model resolution (D15)

**Resolution:** Honor the existing `daemon/services/keyword_extraction.py` resolution semantics — `config.llm.model_keywords` when non-empty, else `config.llm.model`. The judge module exposes `resolve_judge_model(config) -> str` which is a single-line mirror of the keyword-extraction pattern:

```python
model_keywords = (config.llm.model_keywords or "").strip()
return model_keywords or config.llm.model
```

The `set_title_model_fallback` validator at `daemon/config.py:424-431` already collapses empty `model_keywords` to `model` at config-load time, so the runtime branch above is defense-in-depth. Both paths yield identical behavior. The boot log surfaces the resolved model name (`llm_judge_model=<name>` when ON; `<disabled>` when OFF). The judge logs `llm_judge_model=<name>` on every call so operators can grep the resolved model per instance.

**Flag (per the dispatcher's instruction):** The dispatcher's pre-flight note ("how does `daemon/config.py` resolve `OPENAI_MODEL_KEYWORDS` today? It may be a keyword-based model SELECTOR, not a plain model name") was INVESTIGATED. The actual semantics are: **`OPENAI_MODEL_KEYWORDS` is a plain model name string**, NOT a keyword-based selector. The LLMConfig field at `daemon/config.py:130-137` declares `model_keywords: str | None` (default `None`); `OPENAI_MODEL_KEYWORDS` is consumed via the `config.yaml` interpolation at `config.yaml:22`. The `set_title_model_fallback` model_validator at `daemon/config.py:424-431` collapses empty `model_keywords` to `model`. There is no keyword-to-model mapping logic in `daemon/config.py`. The operator docs at `config.yaml:22` and `.env.example:42` describe the field as "Optional. Set to 'quick' to mirror the explorer agent's llm_model" — i.e., a literal model name, not a selector. The judge's implementation matches this straightforward reading.

**Decision-owner:** architect (RESOLVED 2026-09-07).

### RESOLVED-2026-09-07 — Observability (D16)

**Resolution:** Diagnostic extras OUTSIDE the canonical 17-field tuple (same pattern as the supplementary conditional-attestation fields `last_real_user_found`, `last_real_user_index`, `first_delegation_after_last_user_index`, `delegation_tool_call_total`, `delegation_since_last_user`). One-shot structured log line per judge call:

```
event=leader_completion_gate_judge instance_id=%s verdict=%s llm_judge_verdict=%s llm_judge_model=%s llm_judge_latency_ms=%s llm_judge_reason=%s llm_judge_error_class=%s
```

Fields:
* `verdict` — `yes` / `no` / `error` / `timeout` / `unparsable`
* `llm_judge_verdict` — duplicate of `verdict` for grep convenience
* `llm_judge_model` — the resolved quick model (or main model fallback)
* `llm_judge_latency_ms` — wall-clock latency of the judge call
* `llm_judge_reason` — the LLM's one-sentence rationale (empty on error paths)
* `llm_judge_error_class` — exception class name on the error path; `<none>` otherwise

A separate `event=leader_completion_gate_judge_error` log line carries `error_class` for wrapper-layer bugs (e.g. config-load failure). The canonical 17-field tuple remains unchanged — existing test pins coherent.

**Decision-owner:** architect (RESOLVED 2026-09-07).

### RESOLVED-2026-09-07 — Nudge mermaid (D17)

**Resolution:** The mermaid embedded in `ATTESTATION_NUDGE_TEXT` gains ONE new decision node between `"AttestRecent -- No"` and `"Nudged"`:

```
AttestRecent -- No --> ReportJudge{"Did the gate's report judge confirm a real completion report?"}
ReportJudge -- Yes --> FinishGate
ReportJudge -- No --> Nudged["You are being nudged: work not finished"]
```

The canonical byte-pin test `test_attestation_nudge_text_canonical_byte_pin` (and the `EXPECTED_NUDGE_TEXT_CANONICAL` constant) is updated in the same commit. All other `ATTESTATION_NUDGE_TEXT` import sites (the lane artifact at `.agents/tester/RESULTS/2026-09-06-lca-independent-e2e-testfile.py`, the scenarios at `.agents/tester/RESULTS/2026-09-07-lca-cond-e2e-scenarios.py`, the integration tests under `tests/integration/test_attestation_*.py`) import the constant via `from daemon.graph import ATTESTATION_NUDGE_TEXT` — single source of truth, no per-file byte drift.

**Decision-owner:** architect (RESOLVED 2026-09-07).

### Files (Phase 6 fastfollow judge)

- `daemon/services/attestation_judge_resolver.py` (new) — Pattern C kill-switch resolver.
- `daemon/services/attestation_report_judge.py` (new) — the judge service (`JudgeResult`, `resolve_judge_model`, `_slice_judge_window`, `_format_window_for_judge`, `_parse_judge_response`, `_invoke_judge_llm`, `judge_completion_report_async`, `judge_completion_report_sync`).
- `daemon/services/attestation_resolver.py` — `emit_attestation_boot_log` extended with `llm_judge_enabled` + `llm_judge_model` fields; `reset_attestation_resolver_for_tests` also clears the judge cache.
- `daemon/services/attestation_gate.py` — `build_gate_config(...)` grows `llm_judge_enabled: bool = True` kwarg; `GATE_CONFIG_KEYS` extended.
- `daemon/graph.py` — `create_attestation_gate_node` runs the judge BEFORE the ledger write on the would-be-deny path (judge-yes → return END; judge-no / error / timeout / unparsable → fall through to existing deny+nudge). `ATTESTATION_NUDGE_TEXT` mermaid gains the `ReportJudge` decision node.
- `tests/unit/test_attestation_report_judge.py` (new) — 34 tests: pure-function surface (`resolve_judge_model`, `_slice_judge_window`, `_format_window_for_judge`, `_parse_judge_response`) + async judge entry points (`_invoke_judge_llm` patched) + the W4 `JudgeResult` frozen/dataclass construction pin.
- `tests/unit/test_attestation_judge_wiring.py` (new) — 21 tests: gate-level scenarios (a)–(i) per the spec + the W3 `llm_judge_verdict` duplicate-field drop (test assertion was tightened) + the W5/S6 monkeypatch-to-real-env conversion + the S5 judge-yes-at-bound-1 counter-no-escalation regression pin.
- `tests/unit/test_attestation_judge_resolver.py` (new) — parametrize the W5 `_parse_llm_judge_enabled` truth table (falsy / truthy / unset / cached-global / reset helper); 17 tests.
- `tests/unit/test_attestation_nudge_inject.py` — `EXPECTED_NUDGE_TEXT_CANONICAL` updated for the new mermaid; the byte-pin test continues to enforce the full literal.
- `docs/setup.md` — new "Inline-LLM completion-report judge" section appended (judge behavior, env flag, model resolution, log fields).

### DO NOT TOUCH (Phase 6 fastfollow)

- The 5-value canonical decision enum (`ALLOWED` is reused with no new value added).
- The counter-reset semantics (R1 — attested-allow only; the judge-yes path does NOT reset because it is distinct from attested-allow).
- The marker kwargs on the deny-nudge injection (`attestation_nudge=True`, `attestation_nudge_denied_count=<n>`).
- The canonical 17-field `leader_completion_gate` log schema tuple.
- The conditional-attestation scanner / scanner's `delegation_since_last_user` flag.
- The `_make_context_message` factory and the `[SYSTEM CONTEXT: ...]` prefix convention.

### RESOLVED-2026-09-07 — Judge wall-clock cap env-tunable (D18)

**Context (operator tuning decision, 2026-09-07):** the prior hardcoded `JUDGE_TIMEOUT_S = 10.0` (wall-clock cap on the inline-LLM judge) was timeslicing genuine-report quick-model calls. The tester live-LLM probe captured at `.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe*` (evidence commits `b42f7237..2a43904c` on branch `feature/leader-completion-attestation`) showed real quick-model latencies of successes 2.6s–13.6s with **4/8 calls >15s** — the 10.0s cap was firing on a substantial fraction of genuine-report calls and silently flipping the gate to the conservative deny+nudge path, defeating the feature's purpose. The feature was working as designed (the judge timed out; the gate fell through to deny+nudge) — but the timeout was wrong.

**Resolution:** Pattern C sibling resolver at `daemon/services/attestation_judge_timeout_resolver.py` mirroring the existing `attestation_judge_resolver.py` (boolean kill-switch) and `attestation_resolver.py` (main tri-state mode) shapes:

* `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` (default `25.0` seconds; minimum clamp `5.0` seconds — values below clamp to `5.0` with a one-shot WARN; restart-read);
* `get_judge_timeout_s()` cached-global resolver (one-shot boot log via the parent resolver's `emit_attestation_boot_log`, which now also carries the `llm_judge_timeout_s=<resolved>` field);
* `reset_judge_timeout_resolver_for_tests()` test-only cache + one-shot WARN flag reset helper (sibling to `reset_llm_judge_resolver_for_tests`, both also called from the umbrella `reset_attestation_resolver_for_tests` in `daemon/services/attestation_resolver.py`);
* the module constant `DEFAULT_JUDGE_TIMEOUT_S = 25.0` (was hardcoded `10.0`) and the documented bound `JUDGE_TIMEOUT_S = DEFAULT_JUDGE_TIMEOUT_S` in `daemon/services/attestation_report_judge.py` (kept as the canonical DOCUMENTED default reference; runtime value flows from the resolver).

**Failure / clamp policy (fail-OPEN, one-shot WARN, restart-read):**

| Env value | Resolved | One-shot WARN? |
|-----------|----------|----------------|
| unset / blank | `25.0` (default) | no |
| `=30` / `=15.5` | parsed float | no |
| `=abc` / `=1.5x` | `25.0` (default) | yes (invalid → default) |
| `=0` / `=-1` | `25.0` (default) | yes (invalid → default) |
| `=3` / `=4.9` | `5.0` (clamp) | yes (below clamp → floor) |

**Trade-off:** a longer worst-case turn-end wait on the rare deny path (the judge runs only on the WOULD-BE-DENY branch, so the cost is bounded by the per-instance `deny_bound=3` escalation + the existing 3-deny escalation guard) vs fewer false nudges of genuine reports. The `25.0s` default keeps the wait bounded while letting the quick-model tail latency ride. Operators with a known-fast quick-model can tighten via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=15` for a faster worst-case bound; operators with a slow quick-model can loosen to `=40`. Restart required to flip (Pattern C — no live flip).

**Wiring (replaces the prior hardcoded references — all hardcoded `JUDGE_TIMEOUT_S=10.0` usage removed):**

* The `asyncio.wait_for` cap in `_invoke_judge_llm` now reads the resolved value (`timeout_s` is resolved lazily inside `judge_completion_report_async` from the resolver; passed through to `_invoke_judge_llm`).
* The W1 coupling (`request_timeout = min(resolved_timeout, config.llm.request_timeout or resolved_timeout)`) is computed inside `_invoke_judge_llm` from the resolver (was `min(JUDGE_TIMEOUT_S, config.llm.request_timeout or JUDGE_TIMEOUT_S)`).
* The failover `wall_clock_cap_s=timeout_s` argument picks up the resolved value via the same `timeout_s` parameter (no separate reference).
* The async + sync public entry points (`judge_completion_report_async`, `judge_completion_report_sync`) use `timeout_s: float | None = None` and resolve inside; explicit numeric `timeout_s` (test fixtures, hot-loop callers) passes through unchanged.

**Files (this resolution):**

- `daemon/services/attestation_judge_timeout_resolver.py` (new) — Pattern C timeout resolver (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`, `_parse_judge_timeout_s`, `get_judge_timeout_s`, `reset_judge_timeout_resolver_for_tests`; default `25.0`, min clamp `5.0`).
- `daemon/services/attestation_report_judge.py` — `JUDGE_TIMEOUT_S` constant bumped to `DEFAULT_JUDGE_TIMEOUT_S` (25.0; documented default reference only); the `_invoke_judge_llm` W1 coupling + `asyncio.wait_for` cap + failover `wall_clock_cap_s` now all read from the resolver; the public async + sync entry points use `timeout_s: float | None = None` and resolve inside.
- `daemon/services/attestation_resolver.py` — `emit_attestation_boot_log` extended with `llm_judge_timeout_s=<resolved>` field + an additional env readout; `reset_attestation_resolver_for_tests` also clears the new timeout cache + one-shot WARN flags.
- `docs/setup.md` — the inline-LLM completion-report judge section updated (bounds line shows `JUDGE_TIMEOUT_S=25.0s` env-tunable; boot-log example updated to include `llm_judge_timeout_s=25.0`; new "Judge wall-clock cap" subsection documents the env name, default, min clamp, fail-OPEN policy table, and the 2026-09-07 tuning rationale).
- `tests/unit/test_attestation_judge_resolver.py` — extended with the timeout truth table: 29 new tests (valid parses / below-clamp clamp / invalid fail-OPEN / unset returns default / None defensive / one-shot WARN emission discipline for invalid + below-clamp + zero + valid + unset / cached-global restart-read / reset helper re-resolves / reset clears one-shot WARN flags / sibling-resolver-caches-are-independent). File total: 46 tests (was 17).
- `tests/unit/test_attestation_judge_wiring.py` — 5 new wiring tests asserting the resolved timeout FLOWS to BOTH seams (`asyncio.wait_for` cap + W1 `request_timeout` coupling): the resolved-default case, the env=17 case, the below-clamp-clamp case, the explicit-kwarg-overrides-resolver case, and the W1-coupling-uses-min case (config-side request_timeout below resolved → request_timeout is the config value). File total: 26 tests (was 21).
- `tests/unit/test_attestation_report_judge.py` — `test_constants_pinned` updated: `assert JUDGE_TIMEOUT_S == 25.0` (was `10.0`).

**Test-count truth-table discipline (grep-verified 2026-09-07):** the three touched unit files ship **106 tests total** (`pytest --collect-only -q tests/unit/test_attestation_judge_resolver.py tests/unit/test_attestation_judge_wiring.py tests/unit/test_attestation_report_judge.py` → `106 tests collected`): `test_attestation_report_judge.py` 34, `test_attestation_judge_resolver.py` 46, `test_attestation_judge_wiring.py` 26. The full attestation matrix (40 files across `tests/unit/`, `tests/integration/`, `tests/migration/`, `tests/unit/tools/`) collects the baseline + these new tests; see the Coder final report for the exact run numbers (any drift here is a doc-truth violation — the matrix run is ground truth, not the numbers above).

**Decision-owner:** architect (RESOLVED 2026-09-07) + operator tuning decision (RESOLVED 2026-09-07).
---

**2026-09-08 user decision:** Completion Attestation prompt-contract sections removed from `agents/leader/rule.md` + `agents/leader/workflow.md`. The deny-time nudge is the sole teaching source (header + conditional semantics + two-step pattern + embedded mermaid); the LLM judge releases genuine reports. Rationale: a standing prompt section is redundant. Accepted cost: possibly one extra nudge cycle on delegated missions whose report the judge cannot confirm.