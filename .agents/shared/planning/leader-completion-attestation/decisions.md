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
---

**2026-09-11 incident decision (D-ENTRY, incident b08f40fe): idle-orphan descendants are NOT live — two-set `live_descendants` semantics.**

**Root cause (verified forensics):** leader `b08f40fe` completed 2026-09-11 14:50:10 UTC via gate branch (5) `allowed_legitimate_pending_wakeup` with inputs `pending_children=0, wakeups=0, live_descendants=4`. All four "live descendants" were IDLE-orphan grandchildren spawned by tester `c6f57749` but NEVER dispatched: zero `message_queue` rows, zero `message_metadata` rows, every dependency watcher already FIRED. The former `InstanceManager.count_live_descendants` counted `IDLE` (and `QUEUED`) unconditionally via terminal-set exclusion, so never-reporting orphans held branch (5) open — its rationale ("a child report will revive the leader") cannot apply to an orphan, and the leader completed without attestation.

**Decision — TWO-SET live semantics** (fix lands entirely in what feeds `live_descendants`; branch (5) semantics unchanged):

* UNCONDITIONAL-live (counted, no checks): `RUNNING`, `WAITING`, `WAITING_CHILDREN`, `PAUSED` — running-ish states where execution or an operator-held pause of it is real.
* CONDITIONAL-live (dormant; counted ONLY with work en route): `IDLE`, `QUEUED`. Work en route = (a) a not-yet-processed `message_queue` row targeting the descendant (`PENDING`/`READY`/`PROCESSING`/`RETRYING` — new `MessageQueueRepository.get_unprocessed_for_instances`, which widens `get_pending_for_instances` to include the retry-scheduled `PENDING` family), OR (b) a not-yet-settled `job_queue_items` row targeting it (`admission_state` in `ACTIVE_ADMISSION_STATES` = QUEUED | ACTIVE, via the existing `JobRepository.get_active_by_instance`; `JobQueueService._repository` is a `JobRepository`). The job lane is MANDATORY, not a fallback: an IDLE instance with a QUEUED job has no message row yet. DONE/DEAD are settled and never count.
* Terminal (excluded, unchanged): `COMPLETED`, `TERMINATED`, `ERROR`, `FAILED`.
* Unknown status values keep the historical default (not terminal ⇒ live) so a future enum member can never silently read as not-live.
* Preserved exactly: BFS cap (`LIVE_DESCENDANTS_BFS_CAP=500`), permanent `instances.parent_id` tree walk, root exclusion.
* Fail-open preserved: DB errors inside the NEW conditional sub-checks propagate to the gate's `except Exception` DB seam → fail-open ALLOWED with `live_descendants=-1` in the log row — the same surface every other R2 read gets; they never fail toward orphan=not-live. A merely-unwired lane (`_queue_repository` absent, `_job_queue_service` None pre-`set_job_queue_service`) contributes no signal — no wiring ⇒ no work could exist there.
* The work-en-route helper is MODULE-LEVEL (`daemon.manager._dormant_descendants_with_work_en_route`), deliberately not a method: test stubs that bind the facade via `MethodType` need no second attribute binding (a missing method on a partial stub failed open at the gate with `-1` — observed in this suite before the refactor).

**Incident regression:** `tests/integration/test_attestation_idle_orphan_incident.py` reconstructs the exact tree (8 terminal children + 4 never-dispatched IDLE-orphan grandchildren, delegation anchored, no attestation, mode=enforce): branch (5) MUST NOT fire → `decision=denied` + nudge + counter increments; after the orphans are terminated and `attest_completion` is in window → attested ALLOWED with counter reset; terminated-but-unattested still denies.

**Files (this decision):** `daemon/manager.py` (`count_live_descendants` two-set rewrite + module-level helper); `daemon/repositories/message_queue/repository.py` (`get_unprocessed_for_instances`); `tests/support/conftest.py` (`attestation_manager_factory` mirror replaced by delegation to the real facade — kills the drift-prone logic duplicate); `tests/integration/test_attestation_live_descendants.py` (pin test split into unconditional/conditional sets incl. the b08f40fe count-level orphan regression); `tests/integration/test_attestation_idle_orphan_incident.py` (new, gate level); `docs/setup.md` (runbook note).

---

## 2026-09-11 (incident decision) — Mid-work marker scan trigger on ALLOW paths (D-ENTRY)

**Trigger:** Incident b08f40fe (2026-09-11) — a leader instance completed silently through the gate's `ALLOWED_LEGITIMATE_PENDING_WAKEUP` branch because its final AIMessage read "Awaiting final four: C12a/b/c + blame-worker. Then I aggregate and write RESULTS. Ending turn." — mid-work phrasing that no existing gate predicate detects. The orphan-fix (D-ENTRY 2026-09-11 above) closed the `live_descendants` two-set semantics for the IDLE-orphan subtree but did not address the broader class where the leader's PROSE reads mid-work while the R2 inputs are clean.

**Decision — marker-path disambiguator on ALLOW paths:**

The marker scan is the **TRIGGER** (cheap substring match across the AIMessage tail against ~12-18 curated mid-work phrases). The existing inline-LLM judge at `daemon/services/attestation_report_judge.py` is the **VERDICT**. Markers fire → judge runs → verdict + R2 inputs drive the (a)/(b)/(c)/(d) routing:

| Path | Conditions | Action |
|------|------------|--------|
| **(a)** | markers + judge-not-complete + nothing pending | **CONVERT TO DENY** via the existing nudge machinery (counter/bound/escalation apply unchanged) |
| **(b)** | markers + judge-not-complete + real pending work | **ALLOW STANDS** + inject a checkpoint-durable Completion Check Note alongside END (NO counter, NO deny, NO re-route — the turn still ends so the wake-up can arrive) |
| **(c)** | markers + judge-confirmed-complete | **ALLOW normally** (log marker_hit=true + verdict; no action) |
| **(d)** | markers + judge-error/timeout/unparsable | (a)-behavior if nothing pending, (b)-behavior otherwise (log judge_error) |

**Hook scope (where the scan runs):**

* `Decision.ALLOWED` (attested_allow is SKIPPED — attested is an explicit contract) — INCLUDED
* `Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP` (real wakeup en route — the b08f40fe-class incident's natural decision) — INCLUDED
* `Decision.DENIED` — EXCLUDED (existing would-be-deny judge already handles this family)
* `Decision.TERMINAL_AFTER_BOUND` — EXCLUDED (escalation already fired)
* `Decision.DRY_LOG` — EXCLUDED (dry mode is passive observer; marker hits logged but no judge call)
* meta-condition bypass (mode=off / scope not applicable / attestation_enabled=False) — EXCLUDED (byte-equivalent OFF baseline)
* `attestation_required=False` (conditional OFF) — **INCLUDED** (the brief's "a quick answer ending '...Ending turn, will continue after your reply' must reach the judge" example depends on the conditional-OFF branch being scanned too; this is the spec's explicit requirement that the conditional gate is NOT a marker-skip bypass)

**Kill-switch coupling (DECIDED — do not relitigate):**

The marker path respects the EXISTING judge kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (default ON; `=0`/`=false`/`=no`/`=off` disables). With the judge disabled, marker hits are logged (so dry-log soak sees them) but NO judge call fires; the gate falls through to plain ALLOW in ALL marker cases. Rationale: marker-only signal is too weak to deny — without the LLM verdict the marker hit is ambiguous, and the cost of a false deny (forcing the leader to re-issue a turn) outweighs the cost of a false allow (the deny counter resets on attested allow). Operators who want to disable the marker-path entirely can also set `mode=off` (the meta-condition bypass).

**Marker catalog (curated 16-pattern list, 12-18 balance per the brief):**

`ending turn`, `ending my turn`, `awaiting`, `then i aggregate`, `then i compile`, `will write`, `will aggregate`, `not a completion report`, `interim`, `in progress`, `not yet complete`, `still pending`, `to be continued`, `will report back`, `standby`, `stand by`.

Excluded from the catalog (would false-positive on legitimate completions): `done`, `completed`, `finished`, `shipped`, `summary`, `results` alone. The judge verdict filters any remaining false positives (the marker is the trigger, the LLM is the verdict).

**Completion Check Note constant (canonical home `daemon/graph.py`; NFR-6 parity with `ATTESTATION_NUDGE_TEXT`):**

```
[SYSTEM CONTEXT: Completion Check Note]

The completion gate noticed mid-work phrasing on a turn where
real pending work is still outstanding (children, wakeups, or
live descendants remained). The gate allowed the turn to end so
the wake-up you expected can still arrive, but please confirm on
your next turn that the wake-up actually comes — if the pending
work was orphaned or already idle, clean it up or call
attest_completion once the work is truly done. Reminder: when
you do finish, FIRST deliver your full detailed final report as
its own message, THEN call attest_completion ALONE — never
bundle the report into the attestation tool-call message.
```

The hint rides a `HumanMessage` constructed via `_make_context_message(kind=CONTEXT_KIND_TASK_CONTEXT, ...)` (the construction-time `id` invariant is upheld; the existing `[SYSTEM CONTEXT: …]` prefix convention is reused — same exclusion class as `Completion Check Nudge` so the `is_real_user_message` predicate in `attestation_scanner.py` recognizes it as not-a-real-user-message).

**Log schema (additive, NOT in the canonical 17-field tuple):**

* `marker_hit` (bool) — emitted on every `event=leader_completion_gate` log row (False when no scan fired or no marker hit)
* `marker_terms` (capped list, comma-joined in log; `<none>` when empty) — distinct markers that fired, ordered by catalog order
* `marker_path` (`""` / `"a"` / `"b"` / `"c"` / `"d"` / `"<pending>"` transient) — the routing decision; the canonical row carries `<pending>` (synchronous scan, async judge hasn't run yet); the routing log line emits the final value
* `marker_judge_verdict` (`"yes"` / `"no"` / `"error"` / `"timeout"` / `"unparsable"` / `"<pending>"` / `"<none>"`) — informational
* `marker_judge_latency_ms` (int; `0` on skip) — informational
* `marker_judge_error_class` (string or `<none>`) — informational

Tuple-discipline: the additive fields ride alongside the canonical tuple in the SAME format-string log line (same shape as the supplementary conditional-attestation fields `last_real_user_found` / `last_real_user_index` / etc. — `attestation_gate.py:894-1008`). The marker-path judge ALSO emits a separate one-shot `event=leader_completion_gate_marker_judge` log line mirroring the existing would-be-deny judge's `event=leader_completion_gate_judge` shape so operators grep one set of keys for both paths.

**Test matrix (acceptance suite — all required, all green at ship):**

| # | Case | Pinned in |
|---|------|-----------|
| (a) | markers + judge-no + nothing pending → DENY + nudge + counter+1 | `tests/unit/test_attestation_marker_wiring.py::test_marker_a_deny_nudge_counter_increments` |
| (b) | markers + judge-no + real pending → ALLOW + hint, no deny, no counter | `tests/unit/test_attestation_marker_wiring.py::test_marker_b_hint_injection_no_deny_no_counter` |
| (c) | markers + judge-yes → ALLOW normally | `tests/unit/test_attestation_marker_wiring.py::test_marker_c_judge_yes_allows_normally` |
| (d1) | markers + judge-error + nothing pending → DENY | `tests/unit/test_attestation_marker_wiring.py::test_marker_d_error_with_nothing_pending_deny` |
| (d2) | markers + judge-timeout + real pending → ALLOW + hint | `tests/unit/test_attestation_marker_wiring.py::test_marker_d_timeout_with_real_pending_hint` |
| (d3) | markers + judge-unparsable + nothing pending → DENY | `tests/unit/test_attestation_marker_wiring.py::test_marker_d_unparsable_with_nothing_pending_deny` |
| (e1) | kill-switch OFF (env var) → markers logged, no judge, plain ALLOW | `tests/unit/test_attestation_marker_wiring.py::test_marker_kill_switch_env_off_no_judge_call` |
| (e2) | kill-switch OFF (gate-config flag) → markers logged, no judge, plain ALLOW | `tests/unit/test_attestation_marker_wiring.py::test_marker_kill_switch_config_off_no_judge_call` |
| (f) | no markers → no judge call, plain ALLOW (cost control) | `tests/unit/test_attestation_marker_wiring.py::test_no_markers_no_judge_call` |
| (g) | attested allow → scan skipped, no judge, no marker fields populated | `tests/unit/test_attestation_marker_wiring.py::test_attested_allow_skips_marker_scan` |
| (h) | log fields present on marker-(a) path | `tests/unit/test_attestation_marker_wiring.py::test_log_marker_fields_present_on_marker_a_path` |
| (i) | log fields present on no-marker path | `tests/unit/test_attestation_marker_wiring.py::test_log_marker_fields_present_on_no_marker_path` |
| (j) | verbatim incident phrase killed by path (a) | `tests/unit/test_attestation_marker_wiring.py::test_verbatim_incident_phrase_killed_by_path_a` |
| (k) | brief example "Ending turn, will continue after your reply" reaches the judge | `tests/unit/test_attestation_marker_wiring.py::test_quick_question_with_marker_reaches_judge` |

Marker-scanner unit matrix (38 tests in `tests/unit/test_attestation_marker_scanner.py`): catalog size pin (12-18); case-insensitivity; catalog-order marker_terms ordering; marker_terms list cap; verbatim incident phrase fires; every catalog entry fires standalone; benign completion phrases do NOT fire (`"done"`, `"completed"`, `"nothing pending, all shipped"`, `"I delivered the report"`, etc.); window semantics (only the last `window` AIMessages inspected, clamp to ≥1); non-AI messages invisible; list-of-blocks content flattened; empty content handled; empty message list returns no hit; backward walk semantics.

**Files (this decision):**

- `daemon/services/attestation_marker_scanner.py` (NEW) — pure-function scanner (`MID_WORK_MARKERS`, `MarkerScanResult`, `scan_for_mid_work_markers`); 16-pattern curated catalog.
- `daemon/services/attestation_gate.py` — `GateDecision` extended with `marker_hit` / `marker_terms` / `marker_path` / `marker_judge_verdict` / `marker_judge_latency_ms` / `marker_judge_error_class` / `marker_hint_message`; marker scan inserted in `evaluate()` for ALLOWED / ALLOWED_LEGITIMATE_PENDING_WAKEUP decisions with `attestation_present=False`; canonical log format string gains 6 additive fields.
- `daemon/graph.py` — `COMPLETION_CHECK_NOTE_TEXT` constant (canonical home, NFR-6 parity with `ATTESTATION_NUDGE_TEXT`); `create_attestation_gate_node` extended with the marker-path judge wiring (kill-switch respected; routing to (a)/(b)/(c)/(d); `_make_context_message` factory used for hint construction); existing would-be-deny judge is SKIPPED when `decision.marker_path in {"a", "d"}` (the marker-path judge IS the disambiguator); END branch consumes `decision.marker_hint_message`.
- `tests/unit/test_attestation_marker_scanner.py` (NEW) — 38 tests (catalog + case-insensitivity + verbatim phrase + benign negatives + window + non-AI invisible + content flattening + multi-marker + cap).
- `tests/unit/test_attestation_marker_wiring.py` (NEW) — 23 tests (acceptance matrix (a)/(b)/(c)/(d)/(e)/(f)/(g)/(h)/(i)/(j)/(k) + 2 wrapper-fault tests (F2 P0 close) + 7 review-pass tests (W1 dry-mode marker logging ×1, W2 Shape A supersede/isolation/compaction ×3, green #1 kill-switch OFF `<skipped>` stamp ×1, green #3 dead-import removal ×1, green #5 catalog pin RuntimeError ×1)).
- `requirements.md` — append-only FR amendment (see immediately below).
- `docs/setup.md` — append-only runbook note (mid-work marker scan section; judge kill-switch explanation).

**DO NOT TOUCH (this decision):**

- The 5-value canonical decision enum (`ALLOWED` / `DENIED` / `TERMINAL_AFTER_BOUND` / `DRY_LOG` / `ALLOWED_LEGITIMATE_PENDING_WAKEUP` is UNCHANGED).
- The `attest_completion` tool semantics — unchanged (idempotent no-op).
- The would-be-deny judge (`attestation_report_judge.py`) — unchanged, REUSED.
- The marker-path judge is the SAME judge as the would-be-deny judge; only the routing is new.
- The R2 inputs (pending_children, queued_or_expected_wakeups, live_descendants) — unchanged.
- The counter-reset semantics (R1) — unchanged.
- The `ATTESTATION_NUDGE_TEXT` constant — unchanged (canonical nudge home, NFR-6 parity).

---

## Open residual — F1 (2026-09-12) — Unbounded `context_kind` hint accumulation under three-bucket compaction

**AMENDMENT (2026-09-12, review W2 ordered fix — Shape A landed):**

Shape A has been implemented per the external reviewer's W2 ordered fix. This entry is now RESOLVED-FIXED, not backlog. The implementation lands in this same commit (review-pass commit on top of 06ddad57):

- `_make_completion_check_note_message(instance_id)` plumbs a stable id through the helper's reserved `instance_id` slot using the new `_stable_id_for("completion_check_note", instance_id=...)` row in the canonical id-format table at `daemon/services/context_messages.py`. The factory's `instance_id=None` fallback preserves the pre-F1 fresh-uuid4 behavior for degenerate / test-only call sites.
- Each subsequent (b) event on the same instance now SUPERSEDES the prior checkpoint entry in place via LangGraph's `add_messages` reducer. The resulting state carries EXACTLY ONE Completion Check Note block regardless of how many (b) events fired — the unbounded `context_kind=task_context` tail that previously drove `INJECTIONS_DOMINATE` skips under three-bucket compaction (merge 77ce4ae8) is closed.
- The three-bucket compaction seam (`daemon/compaction.py::_is_hoisted_injected` + `build_sentinel_replacement`) does NOT need a dedupe fix — its partition reads `current_messages` post-LangGraph-channel-upsert, where same-id messages have already been collapsed to one. The dedupe happens at the LangGraph layer (the canonical id is the one canonical upsert key).

Tests (added this pass):
- `tests/unit/test_attestation_marker_wiring.py::test_marker_b_hint_stable_id_collapses_on_supersede` — two `(b)` events on the same instance ⇒ ONE Completion Check Note block in resulting state (LangGraph `add_messages` upsert by id; counts assertion).
- `tests/unit/test_attestation_marker_wiring.py::test_marker_b_hint_stable_id_isolates_per_instance` — two `(b)` events on DIFFERENT instances ⇒ TWO blocks (the stable id is per-instance, not global).
- `tests/unit/test_attestation_marker_wiring.py::test_completion_check_note_compaction_seam_hoists_once` — compaction seam (`_partition_injected_for_compaction`) hoists exactly ONE Completion Check Note after the LangGraph upsert.

DO NOT TOUCH (this amendment):
- The `(b)-path` semantics (route, log row, hint content) — unchanged.
- The `context_messages._make_context_message` `id_=None` default — unchanged for any other caller.
- The id-format table row "completion_check_note" — append-only; any new kind MUST go through `_stable_id_for` and follow the canonical table convention.

---

**Original entry (preserved for audit history — Shape A was the recommended next step, see below):**

**Symptom (recorded during 2026-09-11 adversarial review pass):**

Every (b)-path "Completion Check Note" mint currently goes through `daemon.graph._make_completion_check_note_message(instance_id)` → `_make_context_message(kind=CONTEXT_KIND_TASK_CONTEXT, title=..., content=...)`. The factory lets `_make_context_message` mint a fresh `uuid4` per call (the `id_=None` default; see `daemon/services/context_messages.py:118-125`). Repeated (b) events on the same session therefore stamp a distinct `HumanMessage.id` each time, and each one survives compaction as a permanently-hoisted `context_kind=task_context` block under the three-bucket contract (per merge 77ce4ae8 / `daemon/compaction.py` threshold numerator counts ALL messages incl. injected `[SYSTEM CONTEXT]`). Net effect: a leader that mid-work-phrases across many turns accumulates an unbounded tail of Completion Check Note hints in its context, dominating the budget without ever being absorbed.

**Proposed fix shapes (not yet implemented — superseded by amendment above):**

- *Shape A — stable id per session:* `id_=_stable_id_for("completion_check_note", instance_id=effective_instance_id)` plumbed through the new helper's reserved `instance_id` slot. Adds a new kind to `_stable_id_for`'s canonical id-format table (decisions.md D3 — kv-ambient-awareness-fix; single source of truth — all callers route through this helper so the mint site stays grep-able and the formats stay append-only). Each subsequent (b) event on the same instance SUPERSEDES the prior checkpoint entry in place via LangGraph's `add_messages` reducer.
- *Shape B — drop `context_kind` for the bare-flag shape:* mint the hint with `injected_message=True` but NO `context_kind` field (the bare-flag UNANSWERED treatment per merge 77ce4ae8 — preserved verbatim and hoisted). Lower-priority — changes how FE / consumers filter these blocks (the `context_kind` enumeration becomes a fuzzy set vs. an exact match).

**Recommended next step (landed per amendment above):** Shape A — minimal diff, no consumer-key changes, single new `_stable_id_for` row, and the helper's already-reserved `instance_id` slot is the natural seam.

**Files (FIXED — implementation landed this pass):**

- `daemon/graph.py` — `_make_completion_check_note_message` (the helper already plumbs `instance_id` for this exact purpose; ONE line at the call site to `_make_context_message` to use `_stable_id_for("completion_check_note", instance_id=...)`).
- `daemon/services/context_messages.py` — append the `completion_check_note` row to `_stable_id_for`'s id-format table (the single source of truth for stable-id formats per D3 kv-ambient-awareness-fix).
- `tests/unit/test_attestation_marker_wiring.py` — added the supersede + per-instance isolation + compaction-seam hoists-once tests (Shape A contract end-to-end).

---

## 2026-09-12 (user request) — Word-count trigger (< 150 words) for the allow-path judge (D-ENTRY)

**Trigger:** User observation 2026-09-12 — mid-work ACKs are often SHORT with no marker words ("Understood, continuing." / "OK, waiting on the tester.") — brevity is an INDEPENDENT trigger signal that the marker-only catalog misses. Real completion reports are normally detailed (hundreds of words); the brevity class is suspicious on the gate's ALLOW path. The marker catalog catches phrasing ("ending turn", "awaiting"); the word-count trigger catches brevity — orthogonal signals that compose via ``OR`` on the gate's ALLOW path.

**Decision — word-count trigger as the SECOND half of the two-stage disambiguator:**

The length-trigger scanner is the second TRIGGER half (orthogonal to the marker substring scan). The existing inline-LLM judge (`daemon/services/attestation_report_judge.py`) remains the VERDICT. The two halves compose via ``OR`` on the gate's ALLOW paths (``marker_hit OR length_trigger`` → judge fires). The actual (a)/(b)/(c)/(d) routing is unchanged — same judge verdict, same R2 input-driven routing.

**Threshold: 150 words (module-level constant, NOT env-tunable by design).**

```python
SHORT_REPORT_WORD_THRESHOLD: int = 150
```

Rationale: real completion reports the leader would put through this gate are routinely detailed (hundreds of words); very-short prose on an ALLOW path is suspicious. Below 150 words ⇒ trigger fires (the brevity class). The threshold is a MODULE-LEVEL CONSTANT — deliberately NOT env-tunable. One knob fewer; revisit at soak. Operators wanting to disable this trigger set `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` (the existing judge kill-switch) and accept the marker-only signal as the trigger.

**Word count is whitespace-split on the flattened LAST AIMessage content (mirrors `_flatten_ai_content` for list-of-blocks content). Pure function; no I/O.**

**Trigger-source derivation (additive log field):**

* `"markers"` — only the marker substring scan fired.
* `"length"` — only the word-count threshold fired.
* `"markers+length"` — both halves fired (the COMBINED trigger class).
* `""` (empty) — neither fired; the cheap allow path with no judge call.

Both halves firing ⇒ judge is called ONCE (single call, single verdict); the additive `trigger_source` log field lets operators grep-distinguish the three trigger classes.

**Hook scope (where the scan runs):**

Identical to the marker scan (the same `result.decision in (ALLOWED, ALLOWED_LEGITIMATE_PENDING_WAKEUP, DRY_LOG) AND not result.attestation_present` gate). Attested allows still skip entirely. Cost control: NEITHER trigger fires ⇒ NO judge call (cheap allow path). Cost control is preserved end-to-end.

**Kill-switch coupling:**

The length trigger respects the existing judge kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (default ON). With the judge disabled, length triggers are logged but NO judge call fires; the gate falls through to plain ALLOW in ALL length cases. Rationale: length-only signal is too weak to deny — the same marker-path rationale (allow + log; without the LLM verdict, plain allow is the safer default). On the canonical log row, `marker_judge_verdict=<skipped>` stamps; the kill-switch OFF branch in `daemon/graph.py` emits the existing `event=leader_completion_gate_marker_judge_disabled` log row with `verdict=<skipped>` (operators grep the existing event — no new event name).

**Dry-mode behavior:**

Length trigger is logged side-effect-free exactly like markers: the canonical log row carries `length_trigger` / `final_word_count` / `trigger_source` fields. The dry-mode `allow unconditionally` posture is preserved end-to-end — no judge call, no hint, no deny, no counter. Operator forensics can grep the additive fields in `decision=dry_log` soak rows to see how often mid-work brevity would have triggered the length path.

**Log schema (additive, NOT in the canonical 17-field tuple):**

* `length_trigger` (bool) — True when the LAST AIMessage word count is < `SHORT_REPORT_WORD_THRESHOLD`. False on degenerate empty/no-AI tail (no AIMessage to measure).
* `final_word_count` (int, ≥0) — word count of the flattened LAST AIMessage content. `0` on degenerate empty/no-AI tail.
* `trigger_source` ∈ `{"markers" | "length" | "markers+length" | ""}` — which trigger half fired (both = combined; neither = empty `<none>` in log row).

Tuple-discipline: the three new fields ride alongside the canonical tuple in the SAME format-string log line (same shape as the supplementary conditional-attestation and marker fields). Format-string placeholder count grows 28 → 31 (drift pin in `test_length_log_placeholder_count_is_31`).

**DO NOT TOUCH (this decision):**

* The 5-value canonical decision enum (`ALLOWED` / `DENIED` / `TERMINAL_AFTER_BOUND` / `DRY_LOG` / `ALLOWED_LEGITIMATE_PENDING_WAKEUP` is UNCHANGED).
* The marker catalog (16 patterns; 12-18 balance).
* The marker-path judge (`attestation_report_judge.py`) — unchanged, REUSED.
* The would-be-deny judge — unchanged, REUSED.
* The R2 inputs (pending_children, queued_or_expected_wakeups, live_descendants) — unchanged.
* The counter-reset semantics — unchanged.
* The nudge/hint texts — unchanged.
* The bound/escalation semantics — unchanged.
* The idle-orphan two-set semantics — unchanged.

**Files (this decision):**

- `daemon/services/attestation_marker_scanner.py` — `SHORT_REPORT_WORD_THRESHOLD` constant + `LengthScanResult` NamedTuple + `count_words` helper + `scan_for_short_final_ai` scanner function.
- `daemon/services/attestation_gate.py` — `GateDecision` extended with `length_trigger` / `final_word_count` / `trigger_source` fields (additive, default values preserve existing constructor call sites); trigger-site in `evaluate()` extended to `marker_hit OR length_trigger` → judge fires; canonical log format string gains 3 additive fields (28 → 31 placeholders); `trigger_source` derivation logic.
- `daemon/graph.py` — gate-node marker-path judge wiring extended: `decision.marker_hit or decision.length_trigger` (the OR-composition); routing semantics unchanged.
- `tests/unit/test_attestation_marker_scanner.py` — 16 new tests (length-trigger matrix: threshold pin, helper basics, 149/150/151 boundaries, short fires, long doesn't fire, last-message-only counting, empty list, only-non-AI messages, empty content, list-of-blocks flattening, window clamp).
- `tests/unit/test_attestation_marker_wiring.py` — 9 new tests (short-no-marker judge fires, long-no-marker no-judge cost control, short+complete artifact judge-yes allows, short real-pending hint, short+markers combined trigger_source, markers-only on long-with-marker, dry-mode log-only, kill-switch OFF log-only, log-row placeholder count drift pin 28→31).
- `tests/unit/test_attestation_judge_wiring.py` — `_delegated_mission_without_attest` default final_text updated to a long completion report (>= 150 words, no marker phrases) so the legacy `judge never called` assertions stay green on the new length trigger (the cost-control invariant under the OR-composition).
- `tests/unit/test_attestation_nudge_inject.py` — `test_quick_question_mission_allows_without_attest` AIMessage updated to a long completion report (the cheap allow path is preserved end-to-end when NEITHER trigger fires).
- `tests/unit/test_attestation_conditional_gate_outcomes.py` — `test_quick_question_full_graph_terminates_without_nudge_or_attest` and `test_chart_mission_full_graph_terminates_without_nudge_or_attest` AIMessages updated to long completion reports (>= 150 words) so the cheap allow path stays green end-to-end.
- `tests/integration/test_attestation_marker_routing_lca.py` — `_no_marker_mission` updated to a long completion report (>= 150 words, no marker phrases) so the (h) scenario cost-control assertion stays green under the new length trigger.
- `requirements.md` — append-only requirement entry (see immediately below).
- `docs/setup.md` — append-only runbook note (length-trigger section; threshold, field names, interplay).

## 2026-09-12 (demo-testcase findings) — Judge fallback-config import-typo fix + send_message continuation clauses in nudge/hint (D-ENTRY)

**Grounding:** defect found via the `plan/mid-work-report-testcase` demonstration lane (@ eed44334); tester-verified against `2d06a7b3`, re-located unchanged at synced head `93025b79`.

**Decision (import typo — defect fix):** both judge-seam fallback config resolutions in `daemon/graph.py` used `from ..config import load_config` (two dots → `ImportError: attempted relative import beyond top-level package`): the marker-path judge seam and the would-be-deny judge seam. With a manager that genuinely lacks `.config`, the wrapper `except Exception` swallowed the ImportError and the judge SILENTLY degraded to the fail-safe route (d) (`event=leader_completion_gate_marker_judge_error ... decision=fail_safe_marker_d` / `event=leader_completion_gate_judge_error ... decision=fail_safe_deny`) — no raise, judge skipped, wrong route. Masked in production because `InstanceManager.__init__` always sets `manager.config` (the buggy else-branch is dead with a real manager), and masked in test embeddings because `MagicMock` auto-creates `.config`. Fix: one character each — `from .config import load_config` (one dot) at both seams.

**Decision (phrasing):** both gate texts now name `send_message` as the continuation mechanism (grounded in the same demo-testcase findings — leaders were not told HOW to continue):

* `ATTESTATION_NUDGE_TEXT` — the anchor sentence gains one parenthetical: "...check current progress (tasks/children status) and continue **(send_message to children/revive as needed)**."
* `COMPLETION_CHECK_NOTE_TEXT` — the confirm-sentence gains one parenthetical: "...confirm on your next turn that the wake-up actually comes **(check your children's status and continue or revive their work via send_message if needed)** — if the pending work was orphaned..."

This consciously amends the length-trigger decision's "The nudge/hint texts — unchanged" DO-NOT-TOUCH line for these two clauses only; no other byte of either constant changes. Verbatim pins re-pinned: the canonical byte pin `EXPECTED_NUDGE_TEXT_CANONICAL` (`tests/unit/test_attestation_nudge_inject.py`) is the ONLY verbatim-substring pin in the 14-file reference set — all other references import the constants (equality assertions auto-green) or pin structural prefixes (headers), which are unchanged. No char-count/length pins exist.

**Regression tests (fail-safe contract now observable):** added to `tests/integration/test_attestation_marker_routing_lca.py` (the file that owns marker-path routing scenarios and its `_make_node` harness): (1) `test_configless_manager_marker_judge_fallback_config_is_healthy` — config-less manager (`_ConfigLessManagerStub`, deliberately no `.config`; a MagicMock would auto-attr it and mask the bug again) → judge runs exactly once with the real `load_config()` product, routing is judge-driven (marker-path a), the ImportError fail-safe row is GONE; (2) `test_configless_manager_config_load_failure_still_failsafe_route_d` — a GENUINE config-load failure (monkeypatched `load_config` raising) must still (i) not raise, (ii) degrade to route (d), (iii) skip the judge, (iv) carry the route in the log row (`decision=fail_safe_marker_d`, `marker-path d`) — assert the route, not just the outcome; (3) `test_configless_manager_would_be_deny_judge_fallback_config_is_healthy` — same fallback-health pin at the SECOND seam: judge-yes must still be able to override a would-be-deny for a config-less manager. Fail-on-base proven: with the one-char fixes stashed, tests (1) and (3) FAIL (log shows `error_class=ImportError ... decision=fail_safe_deny`); test (2) passes pre-fix by design (the contract it pins holds either way).

**Files (this decision):** `daemon/graph.py` (two one-char import fixes; two clause additions to the constants); `tests/unit/test_attestation_nudge_inject.py` (canonical byte pin re-pinned); `tests/integration/test_attestation_marker_routing_lca.py` (three regression tests + `_ConfigLessManagerStub`); this file + `requirements.md` (this entry).

## 2026-09-12 (user request, council-predicted W2) — LCA busy-descendant trigger suppression (false-positive hint fix)

**Trigger:** User observation 2026-09-12 — Route-(b) Completion Check Note hint fires on healthy waits. The user's repro: a leader awaiting a RUNNING child writes a short mid-work ACK ("Awaiting the tester reply. Ending turn, will continue.") — markers (`ending turn`, `awaiting`) AND length (short, < 150 words) BOTH fire on the ALLOW path → judge fires → judge-no (mid-work phrasing) → route (b) → ALLOW + checkpoint-durable Completion Check Note injected. This is the LCA-council-predicted W2 false-positive class: the hint appears on essentially every awaiting turn-end where the leader is doing the HEALTHY thing. Pre-fix a leader with 3 RUNNING children would inject a Completion Check Note on EVERY turn-end during a long-running mission.

**Decision — busy-descendant trigger suppression (additive, lazy guard at the trigger site):**

The marker/length trigger is SUPPRESSED ENTIRELY when at least one descendant is in the unconditional-busy subset ``{RUNNING, WAITING, WAITING_CHILDREN}``. A busy descendant ⇒ the leader is awaiting a healthy child; the marker/length trigger on the LAST AIMessage of THAT turn-end is a false positive. The trigger is disarmed BEFORE the judge call, so no judge fires, no route-(b) hint is injected, and the leader is allowed silently. The marker/length signal STAYS RECORDED on the canonical log row for forensics (``marker_hit`` / ``length_trigger`` keep their computed values; ``trigger_source`` is force-cleared to ``""`` and a new ``trigger_suppressed_by`` field stamps the suppressor name).

**Why busy ≠ live (the two-set live semantics):**

* Live (existing third R2 input, 809e2a59 + b08f40fe): blocks the deny path because a wakeup is en route. Counts status in ``{RUNNING, WAITING, WAITING_CHILDREN, PAUSED}`` (unconditional-live) PLUS ``{IDLE, QUEUED}`` (conditional-live, only with an unprocessed message row OR an unsettled QUEUED/ACTIVE job). PAUSED is kept in the live set so a stuck-paused subtree still blocks the deny path.
* Busy (NEW fourth input, 2026-09-12): disarms the marker/length trigger on healthy waits. Strict subset of unconditional-live MINUS PAUSED — ``{RUNNING, WAITING, WAITING_CHILDREN}`` ONLY. PAUSED is NOT busy (suspect, not healthy — the gate keeps the marker/length trigger armed on PAUSED so a stuck child is caught). Conditional-live dormant ``IDLE``/``QUEUED`` ids are NOT busy either (no execution happening — work is merely en route; the trigger stays armed so en-route-only work is caught). The two counts are derived from the SAME BFS in ``InstanceManager._count_descendants_busy_and_live`` — single source of truth for the descendant scan; no double-walk on the gate hot path.

**Spec points (binding):**

1. **NEW INPUT — busy-descendants count.** New ``InstanceManager.count_busy_descendants(instance_id) -> int`` counts the unconditional-busy subset. Returns 0 when the root is not found OR no descendants exist OR every descendant is terminal/PAUSED/dormant. Sibling to ``count_live_descendants``; shares a private ``_count_descendants_busy_and_live`` helper that does ONE BFS pass per invocation. From the gate, both methods are called — two BFS passes total, each bounded by ``LIVE_DESCENDANTS_BFS_CAP=500``, current per-pass P95 ~0.016ms (total ~0.032ms stays well under the 20ms budget).
2. **GATE CONDITION.** At the trigger site ``daemon/services/attestation_gate.py:993-1037`` (trigger_source derivation ~:998-1009, consumed ~:1036): if ``busy_descendants > 0`` AND at least one trigger half fires ⇒ suppress the WHOLE trigger ENTIRELY: NO judge call, NO route-(b) hint, plain allow. The marker/length fields (``marker_hit``, ``marker_terms``, ``length_trigger``, ``final_word_count``) STAY RECORDED for observability. ``trigger_source`` is force-cleared to ``""`` (the cheap-allow signal); a NEW additive ``trigger_suppressed_by`` field stamps ``"busy_descendants"``. ``marker_path`` is force-cleared to ``""`` (no judge fires — no path to record).
3. **PROTECTIONS UNCHANGED.** Suspect-pending shapes KEEP triggering: PAUSED descendants (maybe stuck — trigger stays armed), en-route-only work (IDLE + pending message OR IDLE + unsettled job — maybe lost). The deny path + the two-set live semantics are UNTOUCHED (deny with RUNNING children is structurally impossible — ``live_descendants > 0`` blocks it). The b08f40fe idle-orphan class stays dead (idle orphans were never counted live and are still not counted busy). The route (a)/(b)/(c)/(d) routing for non-suppressed triggers is UNCHANGED.
4. **LOG — additive fields.** ``busy_descendants`` (int, ≥0) and ``trigger_suppressed_by`` (str, default ``""``; non-empty means suppressed). Format-string placeholder count grows 31 → 33 (drift pin in ``test_length_log_placeholder_count_is_33``). Tuple-discipline: the two new fields ride alongside the canonical 17-field tuple in the SAME format-string log line (same shape as the supplementary marker + length-trigger fields).
5. **GRAPH-NODE WIRING.** The gate-node marker-path judge-firing check (``daemon/graph.py:5161-5168``) gets a new condition: ``and not decision.trigger_suppressed_by``. When the gate has stamped a non-empty ``trigger_suppressed_by`` on the decision, the entire judge block short-circuits — NO judge call, NO route-(b) hint, NO counter, plain allow. The dry-mode early-out at the same check (DRY_LOG excluded from the judge block) is preserved; the suppression check ORs into the same boolean.
6. **KILL-SWITCH POLICY.** No new ENSEMBLE_* flags. The fix/flag policy (7d5285aa) is explicit: bugfixes/improvements are NOT user-togglable, ship always-on. The busy suppression is a behavior fix — operators wanting the OLD behavior (suppress busy=0) have no knob to flip; the only kill-switch surface is the existing judge kill-switch ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` which still controls whether the marker-path judge runs at all. Operators wanting to disable LCA suppression entirely would have to revert this commit — by design.
7. **FAIL-OPEN CONTRACT.** The DB seam in ``attestation_gate.py`` already handles DB errors on ``count_busy_descendants`` (mirrors the existing ``count_live_descendants`` fail-open). On a DB error reading the busy subset, the gate fails open to ALLOWED with ``busy_descendants=-1`` in the log row (same ``-1`` sentinel pattern as the existing R2 inputs — never a 0 default, since 0 is a meaningful R2 value).

**Decision — query shape (one BFS pass per call):**

The two public methods (``count_live_descendants`` and the new ``count_busy_descendants``) share a private helper ``InstanceManager._count_descendants_busy_and_live`` that does ONE BFS pass and returns ``(busy_count, live_count)``. Each public method is a thin wrapper that calls the helper and returns its respective count. From the gate, both methods are called — two BFS passes total (each bounded, each ~0.016ms P95). The "ONE query pass" constraint from the spec refers to the BFS being a single tree-walk per invocation (one ``get_tree_ids_permanent`` call + N ``repo.get`` reads + one batched work-en-route lookup). The dormant work-en-route sub-check runs only for conditional-live (dormant) ids; busy excludes dormant by construction, so busy work-en-route work is a no-op (dormant is never busy). Per-id reads inside the BFS are unchanged.

**Decision — graph-node short-circuit (the trigger-suppressed check):**

The marker-path judge wiring in ``daemon/graph.py:5161-5168`` adds ``and not decision.trigger_suppressed_by`` to the existing ``decision.marker_hit or decision.length_trigger`` check. When the gate's ``evaluate()`` has stamped ``trigger_suppressed_by="busy_descendants"`` on the decision, the entire judge block is skipped: NO ``judge_completion_report_async`` call, NO route-(b) hint injection, NO counter write, plain allow. The graph-node decision is byte-equivalent to the gate's plain-allow decision — the marker/length signal STAYS recorded on the canonical ``event=leader_completion_gate`` log row inside ``evaluate()`` for forensics. PAUSED keeps the trigger armed (``trigger_suppressed_by=""``) so a stuck child is caught by the existing marker-path judge.

**Decision — observability vs. behavior change:**

This is a behavior CHANGE on the user's repro path (healthy waits no longer inject hints). The marker/length fields stay recorded on the log row for forensics (operators can grep ``event=leader_completion_gate trigger_suppressed_by=busy_descendants`` to count suppressions; ``marker_hit=True`` + ``trigger_suppressed_by=busy_descendants`` confirms the trigger would have fired but for busy suppression). The dry-mode ``allow unconditionally`` posture is preserved end-to-end — dry-mode + busy suppression is log-only, no judge, no hint. Kill-switch coupling: the busy suppression is independent of the existing judge kill-switch ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` — the busy suppression disarms the trigger BEFORE the kill-switch check, so the kill-switch continues to apply on the non-suppressed paths exactly as before.

**DO NOT TOUCH (this decision):**

* The 5-value canonical decision enum — unchanged.
* The marker catalog (16 patterns; 12-18 balance) — unchanged.
* The marker-path judge (``attestation_report_judge.py``) — unchanged, REUSED on non-suppressed paths only.
* The would-be-deny judge — unchanged, REUSED.
* The R2 inputs (``pending_children``, ``queued_or_expected_wakeups``, ``live_descendants``) — unchanged.
* The deny path + the two-set live semantics — unchanged.
* The counter-reset semantics — unchanged.
* The nudge/hint texts — unchanged.
* The bound/escalation semantics — unchanged.
* The b08f40fe idle-orphan class — unchanged (idle orphans still NOT counted live, still NOT counted busy).
* No new ENSEMBLE_* env flags (fix/flag policy 7d5285aa).

**Files (this decision):**

- `daemon/manager.py` — new `count_busy_descendants` sibling method (shared private `_count_descendants_busy_and_live` helper does ONE BFS pass returning both counts); `count_live_descendants` refactored to a thin pass-through.
- `daemon/services/attestation_gate.py` — `GateDecision` extended with `busy_descendants` (int, default 0) and `trigger_suppressed_by` (str, default `""`); `evaluate()` calls `manager.count_busy_descendants(...)` in the same try block as `count_live_descendants`; trigger block clears `trigger_source=""` and stamps `trigger_suppressed_by="busy_descendants"` when `busy_descendants > 0 AND trigger_fires`; canonical log format string gains 2 additive placeholders (31 → 33); DB-seam fail-open reports `busy_descendants=-1`.
- `daemon/graph.py` — gate-node marker-path judge wiring adds `and not decision.trigger_suppressed_by` to the trigger-firing check; comment on the busy-suppression contract.
- `tests/unit/test_attestation_marker_wiring.py` — `_make_node` helper extended with `busy_descendants` kwarg; 11 new tests (AC-B1..AC-B10): RUNNING, WAITING, WAITING_CHILDREN, PAUSED, length-only, combined-trigger, deny-path-unchanged boundary, log-pin, dry-mode-busy; `test_length_log_placeholder_count_is_33` (renamed from `_is_31`, updated 31 → 33); all existing `MagicMock` manager stubs patched to return `count_busy_descendants=0` for the busy-suppression default.
- `tests/integration/test_attestation_marker_routing_lca.py` — `_ConfigLessManagerStub.count_busy_descendants` + `_count_descendants_busy_and_live` methods bound via MethodType (mirrors the existing `count_live_descendants` delegation pattern); 3 MagicMock manager stubs patched to return `count_busy_descendants=0`.
- `tests/integration/test_attestation_live_descendants.py` — `_StubManager.count_busy_descendants` + `_count_descendants_busy_and_live` bound via MethodType (mirrors the existing `count_live_descendants` binding); `TestFacadeBfsCap` cap-test stub updated; canonical log schema test + DB-seam fail-open test patched for the new input.
- `tests/integration/test_attestation_mid_work_report_testcase.py` — `test_scenario_a_children_out_allow_with_hint` rewritten to assert the NEW (suppressed) behavior (no judge, no hint, plain allow, suppression signal recorded); `test_scenario_a_live_judge` rewritten to assert the LIVE judge is NOT called on suppression. These tests previously pinned the BUGGY behavior (the false-positive hint on healthy waits) and now pin the FIXED behavior.
- `tests/integration/test_attestation_c2_both_branches.py`, `tests/integration/test_attestation_dry_mode.py`, `tests/integration/test_attestation_observability.py`, `tests/unit/test_attestation_dry_logging.py`, `tests/unit/test_attestation_gate.py` — `make_manager` / `_manager` helpers extended with `count_busy_descendants` returning 0 (the existing `count_live_descendants` extension pattern).
- `tests/support/conftest.py` — `GraphTestManager` extended with `count_busy_descendants` + `_count_descendants_busy_and_live` methods (mirrors the existing `count_live_descendants` delegation pattern).
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — append-only requirement entry (this decision).
- `docs/setup.md` — append-only runbook note (busy-suppression section; trigger-site, log fields, suppression vs. protection semantics).


---

## D-ENTRY (2026-09-16): LCA judge unparsable retry + forensic logging + deny-path symmetry guard

**Trigger:** Incident 98b59dd7 (2026-09-16, class D judge false-negative). A genuine 2328-char final report was flipped to DENY + nudge because the judge returned `verdict=unparsable` (model=`quick`, 6125ms, `reason=""`, `error_class=None`). The leader self-healed in 13 seconds, but the raw judge output was NOT logged — the empty `reason` field on the unparsable verdict made root-cause analysis unrecoverable. The class D surface (judge responded with a body but the body did not parse) is a known fail-safe shape that fires conservative deny+nudge by construction; this decision closes the forensic gap and adds one retry to recover the false-positive class.

**Decision — ONE retry on unparsable (NOT on timeout/error):**

When the model RESPONDED with a body AND `_parse_judge_response` returned `None`, the judge service retries ONCE with the same input + same config + same per-attempt `timeout_s` (a fresh call, no prompt mutation, no model swap, no backoff). The retry fires ONLY on `verdict="unparsable"` paths. On `verdict="timeout"` / `verdict="error"` paths, NO retry fires — existing fail-safe semantics are preserved exactly. The retry's own per-attempt timeout window is `timeout_s`; total worst-case wall-clock is `2 × timeout_s` (e.g., 50.0s with the default 25.0s cap).

**Decision — JudgeResult extended with two additive fields (backward compatible):**

* `attempt: int = 1` — which LLM call attempt produced this verdict. `1` for first-attempt outcomes (default); `2` for retry-after-unparsable outcomes. Always `1` for non-unparsable paths (success, error, timeout, no-AIMessages).
* `first_unparsable_excerpt: str | None = None` — on a 2-attempt outcome, carries the truncated + redacted raw response of attempt 1 for forensic logging. `None` when `attempt == 1` (no first-unparsable happened).

Both new fields are appended to the END of the `JudgeResult` field list with defaults so existing positional-construction call sites (which use the first six fields positionally) are NOT broken. The `test_judge_result_is_frozen_and_error_class_defaults_none` test pin was updated to include the new fields in the field-order list. New helpers in `daemon/services/attestation_report_judge.py`: `_redact_secrets` (conservative regex for bearer/api-key/token/secret-shaped strings; replaces with `[REDACTED]` sentinel), `_truncate_excerpt` (whitespace-collapse + cap at `JUDGE_EXCERPT_MAX_CHARS=400` with `[truncated]` tail marker), `_shape_unparsable_excerpt` (composition: redact → truncate).

**Decision — `reason` is no longer empty on unparsable rows:**

The brief is explicit: "The reason field must no longer be empty on unparsable rows." On the retry-exhaustion path (`attempt=2`, both attempts unparsable), `reason` carries `"judge_response_unparsable on both attempts: <excerpt-head>"` (excerpt-head capped at 120 chars). On retry-after-unparsable-then-timeout/error (`attempt=2`, attempt 2 was a transport failure), `reason` carries `"judge_response_unparsable (attempt 1); <timeout|error> on attempt 2"`. On retry-success (`attempt=2`, attempt 2 parsed), `reason` is the retry's LLM rationale (unchanged from single-attempt success). This is the incident 98b59dd7 root-cause closure — operators can now read the unparsable row's `reason` field directly without needing the raw response.

**Decision — log-row additions (`event=leader_completion_gate_judge` + `event=leader_completion_gate_marker_judge`):**

Both log rows gain two additive fields (incident 98b59dd7 forensic surface):

* `llm_judge_attempt=%s` — the `JudgeResult.attempt` value (1 or 2).
* `llm_judge_first_unparsable_excerpt=%s` — the `JudgeResult.first_unparsable_excerpt` value, or `<none>` when absent.

Both fields are positional in the format string and ride alongside the existing `llm_judge_model` / `llm_judge_latency_ms` / `llm_judge_reason` / `llm_judge_error_class` fields. The 33-placeholder pin in `test_length_log_placeholder_count_is_33` is NOT affected — that pin covers the gate's canonical `event=leader_completion_gate decision=%s` log row (unchanged by this fix); the two judge log rows are separate format strings with their own placeholder counts.

**Decision — deny-path symmetry guard (automatic via the retry):**

The brief's symmetry guard is AUTOMATIC through the retry semantics, not a separate gate:

* (i) Long-form final AIMessage (>150 words, `final_word_count >= SHORT_REPORT_WORD_THRESHOLD`) + unparsable on attempt 1 + unparsable on attempt 2 → conservative fail-safe. The judge service returns `is_complete_report=False, attempt=2`; the gate sees this AFTER retry exhaustion and the existing deny+nudge machinery fires. The retry gates the nudge on retry exhaustion, NOT on single-attempt unparsable. The `final_word_count` check uses the existing `SHORT_REPORT_WORD_THRESHOLD` constant from `daemon/services/attestation_marker_scanner.py` — no duplicate constant, no env-tunable knob. (Single source of truth.)

* (ii) Long-form final AIMessage (>150 words) + unparsable on attempt 1 + parse-success on attempt 2 → `is_complete_report=True, attempt=2` → gate flips to ALLOWED → NO nudge. The retry RECOVERS the false-positive class from incident 98b59dd7 — without the retry, a single-attempt unparsable would flip a genuine 2328-char final report into deny+nudge.

Both pins are covered by `test_deny_symmetry_long_form_unparsable_then_unparsable_nudge_after_retry` and `test_deny_symmetry_long_form_unparsable_then_parse_success_no_nudge` in `tests/unit/test_attestation_report_judge.py`. The symmetry guard is the emergent property of the retry; no separate gate logic was added.

**Decision — KB-trap pin test (brief §4):**

On `Decision.DENIED` rows, the marker/length scanner NEVER runs (the scan is gated on `result.decision in (ALLOWED, ALLOWED_LEGITIMATE_PENDING_WAKEUP, DRY_LOG) AND not attestation_present` — see `daemon/services/attestation_gate.py:1009-1017`). The `marker_hit` / `length_trigger` / `final_word_count` / `marker_path` fields on `GateDecision` therefore stay at their dataclass defaults (False / False / 0 / "") on a DENIED row. Operators / future readers MUST NOT interpret those fields as measurements of the final AIMessage content on a DENIED row — they are noise on the deny path. Pinned by `TestDeniedRowsHaveDefaultMarkerFields` (4 tests) in `tests/unit/test_attestation_gate.py`.

**Decision — kill-switch OFF = zero judge calls (including zero retries):**

The retry lives INSIDE `judge_completion_report_async`. The kill-switch (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`, `daemon/services/attestation_judge_resolver.py`) is checked at the graph layer in `daemon/graph.py:5483-5486` BEFORE the judge is called. When the kill-switch is OFF, the judge is never called → zero retries by construction. The existing pin `test_judge_not_called_when_env_kill_switch_off_real_resolver` (asserts `calls == []`) covers this; no new test was added because the architecture guarantees it.

**DO NOT TOUCH (this decision):**

* The marker catalog (16 patterns; 12-18 balance) — unchanged.
* `SHORT_REPORT_WORD_THRESHOLD` value (150) — unchanged. Used as the symmetry-guard long-form detector without duplication.
* Busy suppression (`busy_descendants` + `trigger_suppressed_by`) — unchanged.
* The 5-value canonical decision enum — unchanged.
* Routing semantics (a)/(b)/(c)/(d) — unchanged.
* Timeout default (25.0s) — unchanged.
* Nudge/hint texts — unchanged.
* Kill-switch behavior (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` default ON; =0 disables judge ENTIRELY, retries included) — unchanged.
* The 33-placeholder pin on the canonical `event=leader_completion_gate` log row — unchanged. The two judge log rows are separate format strings; this fix adds 2 placeholders to each (4 total across the two rows) but does NOT touch the 33 count.
* No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa). The retry is shipped always-on per the policy.

**Files (this decision):**

- `daemon/services/attestation_report_judge.py` — `JudgeResult` extended with `attempt: int = 1` and `first_unparsable_excerpt: str | None = None` (appended at the END with defaults for backward compat); `_redact_secrets` + `_truncate_excerpt` + `_shape_unparsable_excerpt` helpers added; `JUDGE_EXCERPT_MAX_CHARS: int = 400` constant; `_AttemptOutcome` NamedTuple added (carries `kind`/`latency_ms`/`raw_text`/`model`/`error_class` for a single LLM attempt); `judge_completion_report_async` refactored to extract `_attempt_once` inner helper + retry logic on unparsable; module docstring updated with retry semantics (when fires / when MUST NOT); non-empty `reason` on unparsable paths.
- `daemon/graph.py` — two log lines (`event=leader_completion_gate_judge` and `event=leader_completion_gate_marker_judge`) each gain 2 additive placeholders: `llm_judge_attempt=%s llm_judge_first_unparsable_excerpt=%s`. Comment on the 2026-09-16 retry-fix contract.
- `tests/unit/test_attestation_report_judge.py` — `test_constants_pinned` extended with `JUDGE_EXCERPT_MAX_CHARS == 400` pin; `test_judge_result_is_frozen_and_error_class_defaults_none` updated to include new fields in field-order list + default values (attempt=1, first_unparsable_excerpt=None); `test_judge_async_unparsable_response_is_conservative` updated to assert attempt=2 + non-empty reason + first_unparsable_excerpt. 18 new tests added: retry-success, retry-exhaust, NO-retry-on-timeout, NO-retry-on-error, NO-retry-on-yes, NO-retry-on-no, unparsable-then-timeout, unparsable-then-error, redact-bearer, redact-api-key-shapes, redact-secret-shapes, redact-preserves-short-tokens, truncate-caps, truncate-collapses-whitespace, truncate-empty, shape-end-to-end, deny-symmetry-(i), deny-symmetry-(ii).
- `tests/unit/test_attestation_gate.py` — new `TestDeniedRowsHaveDefaultMarkerFields` class with 4 tests pinning that on `Decision.DENIED`, `marker_hit` / `length_trigger` / `final_word_count` / `marker_path` stay at dataclass defaults (the scanner never runs on the deny path).
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — append-only requirement entry (this decision).
- `docs/setup.md` — append-only runbook note (judge-row field additions; worst-case 2× latency bound; redaction contract).

---

## D-ENTRY 2026-09-16 — Incident 6a0d60c9 fix cycle: marker-path bound bypass (FIX-1) + answer-gate blindness (FIX-2) + nudge id stability (FIX-3)

**Incident (verified):** leader `6a0d60c9` produced 123 gate evaluations / 115 deny+nudge injections over 27 min (~13s cycle). Every turn-end was a terse "ending turn, awaiting go/no-go" one-liner while ALL descendants were terminal-completed and the leader held an OPEN user-answer suspension (resumed via `route_outcome=answer_gate_existing_turn`, `suspension_reason=awaiting_answer`, `target_work_id=ad4743f1`). `decide()` returned early (`attestation_required=False`, FR-3 step 3) → the marker scan fired → judge verdict=no (correct — not a completion report) → the graph converted allow→deny at the marker-path sites WITHOUT consulting `deny_bound` → `attestation_denied_count` climbed 0→122, `completion_gate_escalated` stayed false, and ZERO `event=leader_completion_gate_terminal_after_bound` rows exist in the entire fleet log — the bound backstop was dead on this path everywhere.

**FIX-1 decision — shared deny_bound escalation predicate.** The bound check (`denied_count + 1 > bound`, formerly inline at `decide()` step (6)) is extracted into the shared helper `deny_bound_exceeded(denied_count, bound)` in `daemon/services/attestation_gate.py`. ALL THREE deny producers consult it: `decide()` step (6) (unchanged semantics, now via the helper), the graph marker-path (a) conversion (judge-no + nothing pending), and the graph marker-path (d) conversion (judge timeout/error/unparsable + nothing pending — both the post-call route and the wrapper-fault route). Past the bound the marker-path conversion produces the canonical TERMINAL outcome mirroring `decide()` step (6) EXACTLY — `Decision.TERMINAL_AFTER_BOUND`, `next_denied_count = 0`, `should_inject_nudge = False` — so the EXISTING terminal machinery (ledger `set_escalated_and_reset` = flag+counter in one atomic UPDATE, the `leader_completion_gate_terminal_after_bound` operator event, plain allow END) runs unchanged. NO new terminal behavior was invented. Marker-path (b) (real pending → ALLOW + hint) and (c) (judge-yes → ALLOW) are untouched — they do not deny.

**FIX-2 decision — user_answer_pending = FIFTH legitimate-pending input (leader design call, approved).** When the leader holds an OPEN awaiting-answer suspension handle at gate evaluation, the gate PLAIN ALLOWS before ANY trigger/judge work: no marker scan, no judge, no nudge, no hint, no counter movement. The awaiting answer is the whole turn's purpose; the pending party is the USER and the leader cannot progress alone.

**Detection mechanism chosen (DB-backed, not the in-memory question pack):** `InstanceManager.has_open_user_answer(instance_id)` → `TaskRepository.has_open_answer_handle_for_gate(instance_id)` — the SAME persisted handle the answer endpoint's resume selector consumes (`find_suspended_turn_for_answer`: `suspension_reason='awaiting_answer'` + `resume_target_turn_id IS NOT NULL` + `status='paused'`; the selector behind `route_outcome=answer_gate_existing_turn`). Clearing: `ResumeTurn` transitions `status='paused' → 'pending'` and nulls `suspension_reason`/`resume_target_turn_id` in ONE atomic guarded UPDATE at answer consumption — the detection self-clears, NO stale-allow window. Freshness guard (the false-positive half): the handle must be the instance's NEWEST `task` row (autoincrement `id` ordering — monotonic insertion order, wall-clock `created_at` can tie); a leaked pre-revive handle is expired the moment any later turn exists, so the plain-allow can NEVER become a permanent allow bypass (the new silent-completion hole this fix must not open). Ambiguity (>1 open handles = invariant violation) refuses the bypass (False) — the read-only gate predicate refuses where the resume path raises `ValueError`. Duck-typing guard at the gate seam: the input arms the bypass ONLY when the facade returns the literal `True` — a truthy-but-not-True value (every pre-existing MagicMock test embedding) reads as False so the deny path stays reachable. DB-error contract mirrors `count_busy_descendants`: the facade propagates; the gate's existing DB-seam fail-open converts to the -1-allow (the row reads `user_answer_pending=False` — the allow is the pre-existing whole-eval fail-open, NOT an answer-pending bypass).

**Arm placement:** the arm sits in `decide()` as step (3.b) — AFTER the conditional-off check (FR-3 step 3 keeps its historical plain-`allowed` enum when `attestation_required=False`) and BEFORE the attested check, so the plain-allow contract is ZERO counter movement: an attested-allow reset (trigger 1) does not run while an answer is pending. With the gate otherwise armed (delegated / not attested / nothing pending) the arm resolves to `allowed_legitimate_pending_wakeup`. Dry mode is untouched (DRY_LOG at step 2 trumps everything); meta-condition bypasses stay byte-identical. The dry-mode deny-predicate promotion metric excludes answer-pending evaluations (they would NOT have denied under the new enforce semantics — the adjudication signal stays honest).

**FIX-3 decision — stable nudge id.** The attestation nudge `HumanMessage` carries `id = _stable_id_for("attestation_nudge", instance_id=...)` = `attestation_nudge:{instance_id}` (new canonical row in the `_stable_id_for` id-format table, mirroring `completion_check_note:{instance_id}` F1 Shape A). ALL deny producers funnel through the SINGLE nudge construction site in `daemon/graph.py` — the plain `decide()` deny AND both marker-path (a)/(d) conversions — so all three mint the SAME id and supersede each other via LangGraph's `add_messages` reducer upsert (`merged[existing_idx] = m`, langgraph 1.0.9). Consecutive denies collapse to ONE nudge block carrying the LATEST deny's `attestation_nudge_denied_count` stamp. `additional_kwargs` (`attestation_nudge`, `injected_message`, `attestation_nudge_denied_count`) are unchanged. Degenerate no-instance fallback remains a fresh `uuid4` (never crash the deny path).

**Log-row addition (additive):** `user_answer_pending=%s` is the 18th field of the canonical `event=leader_completion_gate` row — `CANONICAL_LOG_SCHEMA_FIELDS` grows 17→18, the format string 33→34 placeholders (drift pins updated).

**DO NOT TOUCH (this decision):** marker catalog; `SHORT_REPORT_WORD_THRESHOLD` / length trigger; busy-suppression logic (`busy_descendants` / `trigger_suppressed_by`); judge service / retry semantics (merged separately, restart-pending); two-set live-descendant semantics; `ATTESTATION_NUDGE_TEXT` / `COMPLETION_CHECK_NOTE_TEXT` contents; the 5-value decision enum; routing semantics (a)/(b)/(c)/(d). No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa) — all three fixes ship always-on; `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` stays as the existing tuning-only knob, now enforced everywhere.

**Files (this decision):**
- `daemon/services/attestation_gate.py` — `deny_bound_exceeded` shared helper; `decide()` kwarg `user_answer_pending` + arm (3.b) + step (6) via the helper; `GateDecision.user_answer_pending` field; `evaluate()` fifth-input read (strict `is True` coercion) + marker-skip + dry-metric exclusion; 18-field canonical schema + 34-placeholder format string.
- `daemon/graph.py` — marker-path (a) and (d) conversions consult `deny_bound_exceeded` and produce the canonical terminal outcome past the bound; nudge id via `_stable_id_for("attestation_nudge", ...)`.
- `daemon/manager.py` — `has_open_user_answer` facade (propagating, mirrors `count_busy_descendants` contract).
- `daemon/repositories/task/repository.py` — `has_open_answer_handle_for_gate` (exactly-one invariant + newest-row freshness guard).
- `daemon/services/context_messages.py` — `attestation_nudge` kind in `_stable_id_for`.
- Tests: `tests/integration/test_attestation_marker_bound_enforcement_lca.py` (7), `tests/integration/test_attestation_user_answer_pending_lca.py` (7), `tests/unit/test_attestation_user_answer_pending_decide.py` (15), `tests/integration/test_attestation_nudge_supersede_lca.py` (3); drift pins updated (`test_attestation_conditional_gate_outcomes.py`, `test_attestation_live_descendants.py`, `test_attestation_marker_wiring.py`, `test_attestation_bound_escalation.py` — the last now pins the FIX-3 supersede channel shape).
- Docs: `docs/setup.md` (runbook note + 18-field schema enumeration); `requirements.md` (AC-6A0D-1..14).


## D-ENTRY 2026-09-16 — Child-terminal contradiction detection: the original hallucination bug caught at the source (user design)

**Origin:** The leader completion attestation (LCA) stack closes the *leader-side* hallucination bug (an LLM that emits a final assistant message without doing the actual work — gate denies the END, judge disambiguates, marker/length triggers + the busy suppression keep FPs tolerable). The complementary *child-side* bug remained open: a child instance that emits a final report promising future work ("Then I aggregate and write RESULTS. Ending turn.") and then transitions to terminal. The promised "next report" never arrives — the parent wakes up on the completion signal and trusts the report as if the work were done. This is the original hallucination bug class, caught at the SOURCE rather than at the parent's gate.

**D-CTD-1 decision — pure-function detector, zero LLM involvement.** The detection is a substring scan over `CHILD_TERMINAL_PROMISE_MARKERS` (17 entries, FP-tight range 10–18) at the child→parent terminal-report delivery seam (`daemon/services/child_reports.py::_process_child_completion_db_sync`, inserted RIGHT AFTER the existing `report_message` + `PROCESS_REPORT` task INSERTs and BEFORE the `report_injections` queue INSERT — same transaction, four-row crash-consistency). The catalog is a TIGHTER subset of the leader-path `MID_WORK_MARKERS` (17 vs 16) because there is NO LLM judge to disambiguate FPs — markers are BOTH the trigger AND the verdict. False-positive cost is borne by an explicitly advisory note text (see D-CTD-4). The decision to NOT route through the LCA judge service is a hard boundary: the judge is for leader-side prose; the child-side signal is a different bug class with a different seam and a different intervention (advisory note, not deny+nudge).

**D-CTD-2 decision — advisory note, never gate.** The detector attaches a `[SYSTEM CONTEXT: Child Report Check]` `HumanMessage` to the PARENT's queue as a SEPARATE `MessageQueue` row (NOT a modification of the child's report content; NOT a deny; NOT a delay; NOT a gate). The note carries the canonical stable id `child_report_check:{parent_id}:{child_id}` (new row in the `_stable_id_for` id-format table at `daemon/services/context_messages.py`, mirroring the `completion_check_note:{instance_id}` F1 Shape A precedent) so LangGraph's `add_messages` reducer SUPERSEDES repeats in place — no accumulation under three-bucket compaction. Marker kwargs: `additional_kwargs.child_report_check=True` + `child_report_check_terms=[...]` (the matched catalog substrings, surfaced as structured data so observability / compaction hooks can pin them without reparsing the prose body). The note is delivered via the standard `MessageQueue` drain (graph turn pickup), NOT via the `report_injection` hot path — the note is a system-framed side-channel, not a child-to-parent delivery.

**D-CTD-3 decision — scope guards.** The detector is ADVISORY ONLY and explicitly does NOT:
  * modify the child's report content (the note rides as a separate row)
  * block / delay the child's terminal transition
  * block / delay the report delivery to the parent
  * invoke the LCA judge service, the leader gate, or any other LCA machinery
  * carry per-agent state (fires for ANY agent's terminal report — agent-agnostic)
The detector IS invoked from the same dead-parent / paused-parent branches as the existing `PROCESS_REPORT` task creation — the note INSERT is suppressed on `marker_paused`, `db_paused`, or `db_dead_parent` to avoid feeding Lane-3/4's `find_pending_past_age` past the 10-min bound (a stranded note to a dead parent would re-claim forever and inflate `recovered` metric). The parent's existing deny-path judge / nudge machinery is UNTOUCHED — the detector rides alongside, not on top of.

**D-CTD-4 decision — note text is server-authored constant, framed as heuristic.** The note body is a single server-authored string (the LITERAL text below). It does NOT include the matched catalog substrings as embedded prompt injection (that would let a child LLM influence its own advisory); the substrings are surfaced as a STRUCTURED kwarg (`child_report_check_terms`) instead. The text explicitly tells the parent to VERIFY the actual work state and offers `send_message("continue your work")` as the revival mechanism. The framing is "likely premature completion … advisory / heuristic — marker scan is a substring match, not an LLM verdict" so the parent LLM knows the heuristic nature before reacting. Pin text (single source of truth — the spec's required shape, locked here):

> Child {child_instance_id} completed while its final report promises future work ("{matched_terms}") — likely premature completion. Its promised next report will never arrive. Verify the actual work state; if unfinished, revive it via send_message (e.g. "continue your work") or verify its subtree before relying on this report. (Advisory / heuristic — marker scan is a substring match, not an LLM verdict.)

**D-CTD-5 decision — near-FP adjudication.** "completed X, awaiting your merge decision" FIRES the detector (the catalog includes `awaiting` as an explicit spec seed). Excluding `awaiting` would lose detection on the most common promise-while-stopping phrasing the leader-side incident family produces; the FP cost is borne by an explicitly advisory note text (D-CTD-4), not by a hidden gate. Operators grep `event=leader_completion_gate_child_report_check_fired matched_terms=awaiting` in the structured log to compute FP rate from log rows. Adjudication explicitly pinned in `tests/unit/test_child_terminal_contradiction.py::test_c_near_fp_awaiting_fires_with_advisory_note`.

**D-CTD-6 decision — observability event, no new env flag.** The structured event name is `event=leader_completion_gate_child_report_check_fired` (the LCA namespace — `leader_completion_gate_*`). The event carries `parent_id={short} child_id={short} matched_terms={csv} note_message_id={uuid} stable_id={id} enqueued_at={iso}` and emits at the time the note INSERT succeeds (inside the worker-thread `db_sync` half; the outer commit at the end of `_process_child_completion_db_sync` persists the row alongside the rest of the trio). The detector ships always-on (no new `ENSEMBLE_*` env flag, per fix/flag policy 7d5285aa — bugfixes/improvements are not user-togglable). Operator override: kill the entire LCA subsystem via the existing `ENSEMBLE_LEADER_ATTESTATION_MODE` (the child-terminal contradiction detector is a separate subsystem, but operators wanting to disable BOTH can flip the LCA mode off; no NEW kill switch is introduced for this feature).

**DO NOT TOUCH (this decision):** leader gate semantics (the LCA judge, the marker/length trigger logic, the busy-suppression logic, the deny-bound escalation predicate — all untouched); `MID_WORK_MARKERS` (leader-path catalog; the child-terminal contradiction catalog is a SEPARATE constant); `_ChildCompletionDbResult` NamedTuple (no new field added; the structured log line is sufficient); the parent-side judge / nudge machinery (the contradiction detection rides alongside, never on top of); the `report_injections` INSERT (the note rides on the `message_queue` table, not the report-injection queue); LCA judge kill-switch (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`) — the child-terminal contradiction detector does NOT route through the judge, so this flag has no effect on the new feature (intentional separation).

**Files (this decision):**
- `daemon/services/attestation_marker_scanner.py` — `CHILD_TERMINAL_PROMISE_MARKERS` catalog (17 entries, FP-tight range 10–18), `ChildTerminalPromiseScanResult` NamedTuple, `scan_child_terminal_report_for_promises` pure function, `__all__` updated, sanity guard pinned.
- `daemon/services/context_messages.py` — `CONTEXT_KIND_CHILD_REPORT_CHECK = "child_report_check"` enum constant; new row in the canonical `_stable_id_for` id-format table: `child_report_check:{parent_id}:{child_id}` (raises on missing parts).
- `daemon/services/child_reports.py` — `_process_child_completion_db_sync` hook: scan after `PROCESS_REPORT` task INSERT, before `report_injections` queue INSERT; structured `event=leader_completion_gate_child_report_check_fired` log line; note INSERT suppressed on `marker_paused` / `db_paused` / `db_dead_parent`.
- Tests: `tests/unit/test_child_terminal_contradiction.py` (30 new) — pure-function scan matrix, stable-id format pin, hook surface (a)/(b)/(c)/(d)/(e)/(f)/(g), source-level pins (catalog lives in scanner, kind lives in context_messages, hook lives in child_reports, no new ENSEMBLE_* flag).
- Docs: `.agents/shared/planning/leader-completion-attestation/decisions.md` (this D-entry); `.agents/shared/planning/leader-completion-attestation/requirements.md` (CTD-1..CTD-7 AC list); `docs/setup.md` (runbook entry under "LCA child-terminal contradiction detection" section).

---

## D-RES1 — Stage 1 parallel-dry shadow (unified 3-source resolver), 2026-09-16

**Spec:** `resolver-unification.md` (this directory; untracked in the main checkout — read from the absolute path). **Spec sha256:** `a332d603c81a8d69524910207043a36b28ab04c46c44f0a7005d1ef0ce47f1ec` (recorded for provenance; the file is NOT copied into the repo). Branch: `feature/lca-resolver-stage1` @ `ab3b5dc1` (Stage-0 merge). Purely additive; NO routing change; old paths authoritative.

**What landed.** New module `daemon/services/attestation_resolver_activation.py` — the §4.2 pure activation predicate over typed A/B/C signal structs (lazy provider callables; Term 0 scope/mode → Term 1 delegation gate → C fail-open → core terms `c_quiet`/`b_fires`/`a_suspicion` with band precedence deny > marker > a_suspicion), the §4.3 no-judge would-be-outcome mapping, the §4.1 fused-bundle assembler (A ≤3000 + B ≤6000 + C ≤3000, total ≤12000, id-redacted, sha256+size witnesses), the tree-rows provider (lazy, best-effort, only on would-fire), and the ONE-per-evaluation structured event. Wiring: `attestation_gate.evaluate()` gains exactly two additive, exception-isolated seams — §(vi) at the canonical-path tail (before `return result`) and the fail-open row inside the C-read DB-error handler (before its return). The shadow never runs on the meta-bypass/off-mode early returns (they return before any log row — "runs exactly where the existing gate evaluation runs"). The `manager=None` degenerate-embedding branch and the outer scanner/decide crash catch-all do NOT emit shadow rows (scoping choice: no meaningful predicate inputs exist there).

**Decisions applied (user-locked 2026-09-16):**
* **Δ1–Δ4 approved** — bundle carries A child-report evidence (Δ1), C first-10 tree rows + `+N more` suffix + scalar counts (Δ3); Δ4 (hint-channel enrichment) and the fused judge itself are Stage-2 live effects (Stage 1 only logs the would-be `would_hint`).
* **Δ2 mirrored exactly** — Source A is NOT busy-suppressed (`a_suspicion` fires ALONE even when `busy_descendants>0`); Source B IS busy-muted (`b_fires := (marker_hit ∨ length_trigger) ∧ busy_descendants=0`). Pinned by `test_delta2_a_band_fires_alone_while_busy`.
* **DP-5 REJECTED** — judge error/timeout stays conservative path-(d) deny in the TARGET design. Stage 1 has no live effect (no judge at all); the rejected fail-safe-allow shape appears nowhere.
* **R1–R8 retirement: Stage 3 ONLY** — this cycle removes/alters NOTHING (busy-suppression block, delegation arm, marker trigger plumbing, judge call sites, log names all untouched).
* **No new env flags** (repo convention n) — zero new `os.environ`/`os.getenv` reads under `daemon/` (pinned by test).
* **Budget parity ABSOLUTE** — zero new LLM calls; the Stage-2 invocation seam (`STAGE2_JUDGE_SEAM`) exists structurally, defaults `None`, and is provably inert (zero-LLM sentinel test).

**Naming divergence (caller-pinned).** Spec §4.1 suggested `event=leader_activation` and module `attestation_activation.py`; shipped as **`event=leader_completion_resolver_eval`** and **`attestation_resolver_activation.py`** — the LCA `leader_completion_*` log namespace and the existing `attestation_resolver` module family. The module docstring records the same.

**Would-be-outcome mapping (§4.3 no-judge, pinned by tests + docs/setup.md).** fail-open/not-fired → `would_allow`; deny band → `would_deny_nudge`, `would_terminal` when the SHARED `deny_bound_exceeded` fires (imported from `attestation_gate`, never re-implemented); marker/A bands → `would_hint` when route-(b) pending (`pending ∨ wakeups ∨ live` — the graph.py `nothing_pending` composition), else `would_allow`. Note: marker/A bands imply ¬c_quiet by construction, so they always map to `would_hint` through the live predicate; the explicit else-arm exists for the pure function's completeness (Stage-2 band compositions). **Agreement** = exact class match after mapping the old `Decision` into the four-value space (`would_hint` can never agree at the `evaluate()` seam — the old path's hint rides the graph node's post-judge conversion; hint-band rows are divergence rows BY CONSTRUCTION — the Δ2/Δ4 soak counter).

**Spec-vs-landed deltas (Source A contract).** Spec §4.1 anticipated per-report `{advisory_present, contradiction_flag, phrase_promise_while_stopping, word_count_below_threshold, child_instance_id, report_excerpt}`. The LANDED Stage-0 producer (ab3b5dc1) is NARROWER: a promise-phrase substring scan (`CHILD_TERMINAL_PROMISE_MARKERS`, 17 entries in `attestation_marker_scanner.py`) whose only gate-side surface is the delivered `[SYSTEM CONTEXT: Child Report Check]` note (MessageQueue row → leader state), carrying `child_instance_id`, `child_report_check_terms`, matched terms quoted in the note body. There is NO contradiction flag, NO word-count signal, NO raw report excerpt (the note body is server-authored constant text). Consumed contract: `advisory_present ≡ phrase_match ≡` note-present; `contradiction_flag`/`word_count_below_threshold` stay on `SourceASignals` (default False) for forward compatibility; the note excerpt (not a child raw-report excerpt) is the A evidence text. Note detection is dual-surface (kwargs `context_kind=child_report_check` OR the canonical content prefix) because kwargs survival depends on the delivery drain branch; the prefix never changes.

**Spec-vs-landed delta (D10 claim).** Spec §2.2 states "when `attestation_required=False`, the marker scan never runs today (gate.py:1101)". The LANDED code (unchanged from f2611f07) runs the marker scan on the conditional-off branch too — gate.py:1100-1104 explicitly documents "the conditional_attestation_required=False branch is scanned". The R4/D10 MIRROR is nonetheless implemented as specified for the UNIFIED predicate: `¬attestation_required ⇒ A/B providers never invoked` (spy-testable short-circuit, `TestR4ShortCircuitInvariant`) — the target design's exclusion is stricter than today's marker-scan wiring, which is exactly the "markers would newly fire on non-delegation missions" hazard the mirror invariant exists to prevent (spec Appendix A, R4 flag).

**Scoping notes.** The shadow consumes the gate's ALREADY-MATERIALIZED C facade reads (zero new DB reads for the predicate); the tree-rows provider runs lazily and only on would-fire (≤50 point reads via the public `get_tree_ids_permanent` facade + instance repository). The `+N more` suffix counts provider-rows (provider-capped at 50; the subtree BFS is capped at `LIVE_DESCENDANTS_BFS_CAP` upstream) — beyond-cap descendants are approximated in the suffix, acceptable for shadow evidence. The §10.2 `busy>0 ⇒ ¬c_quiet` pin is enforced as a SOURCE pin over `InstanceManager._count_descendants_busy_and_live`'s inline status-set literals (busy ⊆ unconditional-live; PAUSED live-not-busy) — a future edit breaking the subset fails `TestSourceSetPins`.

**Files (this decision):** `daemon/services/attestation_resolver_activation.py` (new), `daemon/services/attestation_gate.py` (§(vi) + fail-open seam, additive only), `tests/unit/test_attestation_resolver_activation.py` (new, 63), `docs/setup.md` (soak-runbook section), this entry + requirements.md R-RES AC list.

---

## D-RES1 review-follow-up — F1/F2/F3 closure (2026-09-16)

Review of `bb17d7fe` (Stage-1 parallel-dry shadow) returned 3 findings; the 4 attack-surface checks (routing, old paths, env flags, LLM/judge, nudge/hint/note texts, bound/escalation) were CLEAN.

**F1 (MAJOR) — A-excerpt id-redaction leak + vacuous test.** `collect_source_a_signals` builds `note_excerpt=_excerpt(body)` from the Stage-0 producer note whose body opens `Child {uuid} completed …` (`child_reports.py:3140-3150`), so the raw child uuid is ALWAYS inside the excerpt. `_build_a_section` (`activation.py:658`) emitted `excerpt: {_clip(ev.note_excerpt, 400)}` WITHOUT `redact_ids` — only `child=`/`stable_id=` were redacted, violating the 98b59dd7 evidence boundary. The existing `test_a_child_ids_redacted` was vacuous: its fixture excerpt used `"Child … completed"` (ellipsis — the real producer ALWAYS embeds the uuid) and the asserted literal `11111111-…` appeared in NO input of that assembly. **Fix:** `activation.py:658` now wraps the excerpt with `redact_ids(_clip(ev.note_excerpt, 400))`; the test was rewritten to (a) build the A-note body in the REAL producer shape (`Child {fixed-uuid} completed …` with a real 36-char uuid), (b) assert a positive-control pre-redaction check that the uuid IS in the raw input fixture (so the test cannot silently go vacuous on a future fixture regression), and (c) assert the uuid IS absent + the `redacted-` placeholder IS present in the final bundle.

**F2 (MINOR) — total-cap hard clip overshot its own bound + branch untested.** The hard-clip branch at `activation.py:759-763` built `text[:BUNDLE_TOTAL_MAX-1] + "…\n[bundle truncated at total cap]\n"` = 11999 + 34 = 12033 chars (overshoot by `len(suffix)` because the suffix length was kept OUT of the clip budget). The branch never ran in tests because per-section caps (A≤3000 + B≤6000 + C≤3000 = 12000) kept the fixtures small. **Fix:** precompute `_truncation_suffix` and clip to `BUNDLE_TOTAL_MAX - len(suffix)` so `len(bundle.text) == BUNDLE_TOTAL_MAX` exactly (no overshoot). Added `test_total_cap_hard_clip_branch_respects_bound` which forces all three per-section clips to their caps (notes with 3000-char excerpts, 8000-char AIMessage, 10-row tree) — the concat hits 12033, the hard-clip branch fires, and the assertion `len(text) == BUNDLE_TOTAL_MAX` would have caught the prior overshoot.

**F3 (MINOR) — docs/setup.md field-name drift.** Rule 5 said `old_decision=denied` but the emitted field is `old_decision_value=denied` (renamed precisely to avoid substring collisions with sibling LCA log tokens). **Fix:** doc token corrected to `old_decision_value=`; added `TestResolverShadowRowShapeDocTruth.test_doc_names_canonical_old_decision_value_token` to the runbook-drift suite pinning both directions (the doc names the canonical token AND does NOT name the retired token).

**Attacks 1–4 (per review scope):** routing — untouched (gate seam additive only, §(vi) + fail-open). old paths — untouched (`attestation_gate.decide()` step (6) and the gate's existing branches byte-identical). env flags — zero new `ENSEMBLE_*` flags introduced. LLM/judge — zero new LLM calls (the `STAGE2_JUDGE_SEAM` stays `None`, the zero-LLM sentinel test still passes). nudge/hint/note texts — untouched (note body is server-authored constant, child_reports.py:3140-3150 verbatim). bound/escalation — `deny_bound_exceeded` is imported from `attestation_gate`, never re-implemented.

**Amended commit:** `bb17d7fe` amended locally on `feature/lca-resolver-stage1` (never pushed). Files in amended delta: `daemon/services/attestation_resolver_activation.py` (F1.1 + F2), `tests/unit/test_attestation_resolver_activation.py` (F1.2 + F2 test), `docs/setup.md` (F3), `tests/integration/test_attestation_runbook_drift.py` (F3 pin). No strays, no `.env`. `git status --porcelain` clean post-amend.

---

## D-RES2 — Stage 2 flip: fused judge authoritative, old sites dead-but-present (2026-09-16)

Branch `feature/lca-resolver-stage2` (isolated worktree, base 0ea60d91 = Stage-1 merge tip). Spec: `resolver-unification.md` §4.2/§4.3/§5 + user-locked Δ1–Δ4, DP-5 REJECTED. **The flip seam:** `daemon/graph.py` module constant `_LCA_STAGE2_RESOLVER_FLIP = True` (no runtime toggle — repo convention n) + the node's fused block (inserted immediately before the legacy marker-path block; graph.py:5243) + flip guards on the two legacy judge entries (`not _LCA_STAGE2_RESOLVER_FLIP and …` at the marker-path entry; `if judge_on and not _LCA_STAGE2_RESOLVER_FLIP:` on the would-be-deny judge). **Zero deletions** — the legacy blocks are dead-but-present; Stage 3 (R6/R7) deletes.

**What landed.**

* **ONE judge call site** — `daemon/services/attestation_report_judge.judge_fused_bundle_async(bundle_text, *, config)`: consumes the Stage-1 assembled bundle VERBATIM as the user payload, new `FUSED_JUDGE_SYSTEM_PROMPT` (names SOURCE A/B/C; conservative "when in doubt, not_complete"), verdict JSON `{"verdict": "complete"|"not_complete", "evidence_cited": [...], "advisory_note_text": str, "rationale": str}` (`_parse_fused_judge_response` mirrors the legacy parser's tolerance shape — fence-strip + single substring fallback; load-bearing field is `verdict`; evidence/advisory defensively capped 5×120/240). Retry-once-on-unparsable mirrored exactly (fires ONLY on responded-but-unparsable; never on timeout/error; `attempt=2` + `first_unparsable_excerpt`). Transport SHARED with the legacy judge via `_invoke_judge_llm` gaining an additive `system_prompt` kwarg (default = legacy prompt, byte-identical for the old callers) — model resolution, per-attempt `request_timeout` binding, Pattern C timeout resolver, and the HA facade can never drift between the two judges. Kill-switch resolution stays at the CALL SITE (the fused block resolves `is_llm_judge_enabled() and gate_config.get("llm_judge_enabled", True)`) exactly like the two legacy sites.
* **Row emission moved gate-thread → node.** `evaluate_shadow_activation` → renamed `evaluate_resolver_activation` returning `ResolverEvalSnapshot` (NO logging, NO LLM — compute only: predicate → would-be → agreement → conditional bundle); the snapshot attaches to the new additive `GateDecision.resolver` field at the §(vi) seam. The graph node's fused block emits the ONE `event=leader_completion_resolver_eval` row AFTER the judge decision via `emit_resolver_eval_row(snapshot, judge_invoked=…, judge_verdict=…, resolver_outcome=…)` — row SHAPE byte-compatible with Stage 1 plus the additive `judge_verdict=` / `resolver_outcome=` tail fields. `log_shadow_fail_open` unchanged (gate-thread; `judge_invoked` there is the derived absence-of-record default). Rationale: `judge_invoked` must be DERIVED from a real invocation record (the Stage-1 hazard pin) — impossible pre-judge in the gate thread.
* **judge_invoked derivation** (hazard pin closed): `judge_invoked = fused_result.invoked` when a `FusedJudgeResult` exists (the flag is True iff ≥1 LLM HTTP attempt was made; False only on the empty-bundle degenerate guard), else False. No literals anywhere (the fail-open row passes the same derived variable). Pinned by `TestJudgeInvokedDerivation`.
* **STAGE2_JUDGE_SEAM retired.** The Stage-1 module-global seam (`STAGE2_JUDGE_SEAM` + `_maybe_invoke_stage2_judge`) is deleted from `attestation_resolver_activation.py`: the fused block's `judge_fused_bundle_async` call IS the seam (the Stage-1 scaffolding existed "for Stage 2 to wire"; the wiring landed at the node where the async judge + config resolution live). Stage-1 `TestStage2SeamInert` re-contracted to `TestFusedSeamContract` (retirement pin + gate-thread zero-LLM sentinel re-targeted: gate `evaluate()` NEVER invokes any judge surface).
* **R7 invariant pins** (spec Appendix A R7 loud flags — named, failing-if-violated, in `tests/unit/test_attestation_resolver_stage2.py`): `TestR7PinJudgeErrorNeverAllows` (deny band error/timeout/unparsable×2 → deny+nudge bound-enforced, NEVER allow — DP-5; marker-band path-(d)-with-pending → hint), `TestR7PinKillSwitchPerBandMapping` (Q1: deny+nudge WITHOUT judge on the deny band; plain allow on marker/A bands; zero HTTP attempts spied), `TestR7PinBoundEscalationFromFusedPath` (at-bound TERMINAL + escalation ledger write + operator event; below-bound increments via the existing machinery; EXACTLY-3-nudges loop pin).
* **Budget guard sentinel** (§5 + task item 4): `TestBudgetGuardSentinel` — ≤1 LOGICAL invocation per evaluation across deny/marker/A-band/rescue configurations (entry-count wrapper around `judge_fused_bundle_async`), the old two-site worst case structurally impossible (single site — pinned by `TestOldSitesDeadButPresent.test_legacy_judge_entries_never_called`: `judge_completion_report_async` stays at ZERO calls across deny-band + marker-band evaluations while the fused seam serves both). **Documented reading (task-required):** the sentinel is pinned at the INVOCATION level; the retry-once-on-unparsable may issue 2 HTTP attempts WITHIN one logical invocation (the preserved 98b59dd7 contract) — `test_unparsable_retry_is_attempts_within_one_invocation` pins exactly that shape (entries==1 ∧ attempts==2). 0-LLM rows preserved (D10 meta-bypass + dry-mode sentinels).
* **Divergence logging** (task item 5 — judgment call, documented): the `agreement` flag is KEPT with its Stage-1 semantics (the resolver's no-judge would-be vs the still-computed gate `decide()` value — cheap, computed in the gate thread). Post-flip the old JUDGE paths are dead and no longer evaluated, so the flag's role shrinks to the kill-switch-off/dry reference; the soak watches `resolver_outcome` + `judge_verdict` directly (docs/setup.md Stage-2 soak section rewritten accordingly).
* **D4 hint citation**: `_make_completion_check_note_message` gained the additive `evidence_citation` kwarg (None → byte-identical pre-D4 note, pinned); `_fused_hint_citation` builds the suffix from `evidence_cited` + `advisory_note_text` when the verdict carries them. Applied on BOTH marker and A bands whenever the verdict cites evidence (Δ4 is band-agnostic; the task matrix explicitly names the A-band case). Nudge text / stamps / note text otherwise untouched.
* **Incident-class E2E** (task item 6 — named per shape, all in `test_attestation_resolver_stage2.py`): `test_incident_b08f40fe_unattested_quiet_judge_sees_child_report_deny_nudge` (the judge's payload carries SOURCE A evidence — Δ1 verified on the payload), `test_incident_98b59dd7_genuine_report_complete_allows_no_nudge`, `test_incident_6a0d60c9_awaiting_answer_plain_allow` (+ the bound-enforced sibling), `test_incident_original_child_lie_full_arc` (3-eval arc: A-band allow+D4-hint → quiet deny+nudge → attested allow with counter reset + Term-1 bypass, judge invoked exactly twice across the arc).

**Behavior deltas shipped (all within the user-locked set):**

1. Δ1/Δ3 live: the judge's input is the fused bundle (A child-report evidence + C tree rows + B signals) — verdicts may differ from the legacy window-judge on the same mission.
2. Δ2 live: A-band rows (child-report suspicion + non-quiet tree) invoke the judge (0→1); B stays busy-muted; A is NOT busy-suppressed.
3. Δ4 live: hints may carry the citation suffix (byte-identical otherwise).
4. **D10 meta-bypass now EXCLUDES non-delegated missions from the judge** — the single largest visible delta outside Δ1–Δ4, documented in D-RES1 as the "target design's exclusion is stricter than today's marker-scan wiring": the legacy marker wiring scanned the conditional-off (non-delegated) branch and could deny non-delegated quick-questions with mid-work phrasing (the 6a0d60c9 incident family); the unified predicate's Term-1 short-circuit (`¬attestation_required ⇒ A/B never evaluated`) exempts them entirely (plain allow, 0 LLM). The 6a0d60c9 unbounded-nudge class is closed STRUCTURALLY (nothing converts those rows to deny anymore); the bound machinery still guards the delegated deny band. The affected legacy tests were re-contracted to the unified doctrine (quick-question marker rows → no-judge plain allow; the deny/bound regressions ride delegated missions).
5. Deny band judge gating: the fused judge fires on a would-be-DENY decision only; an at-bound TERMINAL decision gets NO judge (today's budget row — pre-flip the would-be-deny judge also never saw TERMINAL decisions), and the marker/A-band deny-flip arm is structurally unreachable (¬c_quiet by construction) but present mirroring the legacy conversion (bound-enforced).

**Hazard-pin adjudications (task-mandated):**

* judge_invoked literal — closed (see above).
* Tree-rows provider BFS cost — NOT capped further this cycle: the provider runs lazily ONLY on fired rows and only at bundle-assembly time (≤50 point reads); on the live path the fused block consumes the ALREADY-ASSEMBLED bundle from the snapshot (zero additional reads). The doubling concern from Stage-1 review applies to would-fire rows only and is bounded by the existing caps; revisit only if the fired-row rate itself becomes a DB concern (documented; no change).
* `_eval_error` prefix ambiguity — handled consciously: dashboards must anchor on the TRAILING token (`resolver_eval ` vs `resolver_eval_error`); documented in docs/setup.md Stage-2 soak section AND in the `emit_resolver_eval_row` docstring; the same rule extends to the new `leader_completion_gate_fused_judge` / `_disabled` / `_error` family.

**Convention-n compliance:** zero new `os.environ`/`os.getenv` reads under `daemon/` (existing Stage-1 AST pin re-run green; the fused judge + flip constant add none). Kill-switch/timeout/mode resolvers untouched.

**Test re-contracting ledger (superseded-contract class):** `tests/unit/test_attestation_resolver_activation.py` (row emission → snapshot+emitter; seam retirement; 63 green), `tests/unit/test_attestation_marker_wiring.py` (delegated-mission doctrine + fused rows; D10 exemption pin `test_quick_question_with_marker_does_not_reach_judge`; 42 green), `tests/unit/test_attestation_judge_wiring.py` (verdict vocabulary complete/not_complete + fused event names; trailing-token grep guards; 26 green), `tests/integration/test_attestation_marker_routing_lca.py` (scenarios a–e + configless trio → fused seam; 16 green), `tests/integration/test_attestation_marker_bound_enforcement_lca.py` (delegated 6a0d60c9 loop shape; 7 green), `tests/integration/test_attestation_user_answer_pending_lca.py` (delegated stale-guards; 7 green), `tests/integration/test_attestation_nudge_supersede_lca.py` (delegated `_produce_nudge` — the FIX-3 stable-id/supersede contract itself unchanged; 3 green). New: `tests/unit/test_attestation_fused_judge.py` (21), `tests/unit/test_attestation_resolver_stage2.py` (29). LIVE-LLM incident tests (`test_attestation_mid_work_report_testcase.py` live variants, `test_attestation_idle_orphan_incident.py`, `test_attestation_incident_acceptance_lca.py`) run against the real endpoint when `OPENAI_API_KEY` is exported — unchanged in shape, real-judge latency (~1–2 min/file) requires the 300s per-test timeout.

**Files (this decision):** `daemon/graph.py` (flip constant + fused block + guards + D4 helpers), `daemon/services/attestation_report_judge.py` (fused judge + `system_prompt` seam + parser), `daemon/services/attestation_resolver_activation.py` (snapshot + emitter + seam retirement + rename), `daemon/services/attestation_gate.py` (`resolver` field + §(vi) re-wire), `docs/setup.md` (Stage-2 soak + revert runbook), `tests/unit/test_attestation_fused_judge.py` (new), `tests/unit/test_attestation_resolver_stage2.py` (new), the six re-contracted files above, this entry + requirements.md R-RES2.

---

## D-RES3 — Stage-2 flip blocker closure: F-A fused-scoped cap + F-B fused-block fail-open wrap + F-C gate_exception_seen stamps (2026-09-16)

Branch `feature/lca-resolver-stage2` (worktree `agents-ensemble-wt-lca-stage2`, base `f926de24` + tester evidence at `28edbdd8`; this commit stacks the F-A/B/C fixes on top, ship-into-merge-gate only). Three tester findings from `RESULTS/2026-09-16-lca-stage2-flip-verification.md` (and `LESSONS/2026-09-16-lca-stage2-failopen-seam-findings.md`) closed in one cycle. **The lesson:** the live-LLM probe + deterministic repro caught what static review could not — the F-A cap shared with the legacy window judge silently downgraded every compliant verbose verdict to unparsable ×2; the fused block sat outside the gate node's try/except (seam-iv); `gate_exception_seen` was unstamped on the new Stage-2 seam faults. The defensive discipline (separate caps per call site; fail-open wrap on every new code path; stamp the FR-13 marker on every catch) is now applied symmetrically.

**Lifecycle:** live-LLM probe `tests/probe/lca2_live_fused_judge_probe.py` (model `quick`, both payloads) returned `unparsable` even though the raw model output contained the correct verdict JSON — verifier-side cap+truncate-before-parse was discarding the verdict before the parser ever saw it. The deterministic credentials-free repro `tests/probe/lca2_judge_truncation_repro.py` (`20024fda`) confirmed the order (compact 122-char JSON → `complete`; compliant 997-char JSON → truncated at 400 → `unparsable`×2). The flip made the shared cap the authoritative truncation point on the AUTHORITATIVE completion path (the legacy window judge was dead-but-present but paid-for; the fused judge became the only real consumer of LLM verdicts post-flip) — the silent downgrade was structurally unreachable before the flip and structurally unavoidable after it.

### F-A — fused-scoped output cap (the blocker)

`daemon/services/attestation_report_judge.py` introduces a SECOND module constant `FUSED_JUDGE_MAX_OUTPUT_CHARS = 2048` (kept adjacent to the legacy `JUDGE_MAX_OUTPUT_CHARS = 400` for greppability; docstring explicitly states the legacy cap is dead-but-present and deleted in Stage 3). The two fused truncation sites (`attestation_report_judge.py:1340-1341` and `:1378-1379`, inside `judge_fused_bundle_async`) now reference the fused cap; the two legacy truncation sites (`:921-922` and `:963-964`, inside `judge_completion_report_async`) are UNTOUCHED.

Sizing rationale (preserved in the docstring): the fused prompt's compliant payload is `5 × 120 evidence + 240 advisory + 240 rationale = 1080 minimum`; 2048 covers it with comfortable headroom for verbose-but-correct model output. Truncation still applies at the fused cap (unbounded LLM output must never flow onward); the cap is just raised to fit the fused prompt's compliant shape. Conservative fail-safe preserved on case (c) (runaway >2048 payload → truncated at 2048 → unparsable ×2 → `is_complete=False`).

The CI-registered regression lives at `tests/unit/test_attestation_fused_judge_truncation.py` (new file, 7 tests). Matrix:
- **(a) 122-char compact verdict** → parses complete on attempt 1 (both pre-fix and post-fix).
- **(b) 997-char compliant verbose verdict** → parses complete on attempt 1 post-fix (was `unparsable×2` pre-fix; this is the LIVE-FAILURE case).
- **(c) 3072-char runaway verdict (3× the cap)** → truncation at 2048 → unparsable ×2 → `is_complete=False` (conservative fail-safe preserved; unbounded output never reaches the parser unmangled).

Three drift pins (same file) ensure the legacy/fused cap distinction never silently re-merges: `test_fused_cap_is_2048_distinct_from_legacy_400`, `test_legacy_sites_still_use_legacy_cap`, `test_fused_sites_use_fused_cap` (the third uses an `inspect.getsource` + bare-token regex to assert the fused function never references the un-prefixed legacy token).

The manual-only probe at `tests/probe/lca2_judge_truncation_repro.py` was updated in lockstep: the case-(b) assertion FLIPPED (now `verdict=complete` / `attempt=1` instead of `unparsable` / `attempt=2`); the docstring rewrites the hypothesis under test from "shared cap downgrades verbose verdict" to "fused-scoped cap accommodates the compliant payload"; both cases now print `FIX VERIFIED` on success.

### F-B — fused-block fail-open wrap (robustness, strongly-recommended)

`daemon/graph.py` wraps the ENTIRE fused block (the `if _LCA_STAGE2_RESOLVER_FLIP and resolver_snapshot is not None:` body, lines `~5243`–`~5567` after the F-A commit's re-indent) in a try/except modeled on the outer scanner/decide catch at `graph.py:5176`:

- **Loud error row** — `event=leader_completion_gate_error` with `gate_location=fused_block` (greppable, distinct from the node-level catch) + `error_class` + `decision=fail_open_allowed` + `gate_exception_seen=true`.
- **Stamp the FR-13 marker** via `_persist_gate_exception_marker(ledger, effective_instance_id)` — same helper the outer catch uses, same instance-row key (`attestation_gate_exception_seen`).
- **Conservative per-band outcome** — falls through to Phase-3 (no early return). The existing `decision.decision` from `decide()` drives the per-band recovery (DP-5 REJECTED preserved):
  - deny-band → Phase-3 deny+nudge machinery runs (counter increments + in-graph nudge injects); `attestation_route=agent`.
  - marker/A bands → Phase-3 ALLOWED_LEGITIMATE_PENDING_WAKEUP arm runs (no hint, no nudge, no counter write — marker-only signal too weak to deny on this seam); `attestation_route=None`.
- **Early returns inside the block** (rescue at `:5399`, route-(c) at `:5432`, allow+hint path-(d) at `:5504`) are NOT caught — only raised exceptions fall into the wrapper.

Existing seam-iv tests (`tests/integration/test_attestation_stage2_failopen.py::TestSeamIVOutcomeMappingRaises`) re-contracted from `pytest.raises(RuntimeError)` to assert the fail-open behavior: `result["attestation_route"]` per-band, `ledger.increment` per-band, loud row + `gate_location=fused_block`, `ledger.set_metadata(...)` call for the marker stamp. Class docstring rewritten from "document the crash finding" to "pinned fail-open contract post-F-B"; the per-band recovery shape is now the contract.

### F-C — `gate_exception_seen` stamps on Stage-2 seam faults

Two surfaces closed:

1. **Resolver-compute faults catch (`daemon/services/attestation_gate.py:1483-1518`)** — seam (i) `activation_predicate` raising and seam (ii) `evaluate_resolver_activation` / `assemble_fused_bundle` raising now stamp the FR-13 marker via the shared helper. Mechanism: `evaluate()` gained an optional `ledger: Any = None` kwarg (default keeps every pre-existing caller signature-compatible); `graph.py:attestation_gate_node` passes its `ledger` through at the call site. The catch stamps ONLY via the helper — it does NOT flip `decision.gate_exception_seen=True` (that field is the early-return trigger at `graph.py:5170`; flipping it would bypass Phase-3 deny+nudge, violating DP-5 on the resolver-fault deny band). Lazy-import of `_persist_gate_exception_marker` inside the catch avoids the graph.py ←→ attestation_gate.py import cycle.

2. **New F-B wrapper (graph.py ~5551-5567)** — already covered above; the same helper stamps the instance row on every fused-block raise.

Pin suite: `tests/integration/test_attestation_stage2_failopen.py::TestGateExceptionSeenStampFC` (new class, 3 tests) — `test_fc_seam_i_deny_band_stamps_marker_via_ledger`, `test_fc_seam_ii_deny_band_stamps_marker_via_ledger`, `test_fc_seam_iv_deny_band_stamps_marker_via_ledger`. Each asserts `ledger.set_metadata.assert_called_once_with(<id>, "attestation_gate_exception_seen", True)` AND that the conservative per-band outcome holds (deny-band → counter increments + nudge injects). Seam (iii) judge wrapper faults are EXPLICITLY out of scope for the F-C pin — they live inside the fused block's existing try/except at `graph.py:5294-5319` and stamp via the fused-judge-error row + decision-shape; extending the F-C contract to it would be a separate contract change.

### Hazard-pin adjudications (re-stated post-fix)

- **DP-5 conservative direction** — preserved on every seam. The F-B wrapper falls through, never promotes. The seam (i)/(ii) catch stamps the marker but does NOT flip `decision.gate_exception_seen=True` (would trigger the graph.py:5170 early-return). The fused judge (`verdict=unparsable` ×2) still maps to `is_complete=False` at the parser level (DP-5 reject any path to fail-safe-allow).
- **FR-13 literal compliance** — the marker write to the instance row is the canonical stamp (not the result-dict propagation); the outer catch's result-dict shape (`{"attestation_route": None, "gate_exception_seen": True}`) is unchanged because the outer catch is still the only path that triggers the early-return.
- **Convention-n compliance** — zero new `os.environ`/`os.getenv` reads under `daemon/`. The two caps are module constants (no env knob, no resolver) — the legacy cap was paid-for and dead-but-present; the fused cap is module-level. Convention-(i) drift-pin: the legacy judge code references `JUDGE_MAX_OUTPUT_CHARS` and never `FUSED_JUDGE_MAX_OUTPUT_CHARS` (TestFusedCapDriftPin); the fused judge references only `FUSED_JUDGE_MAX_OUTPUT_CHARS` (no bare `JUDGE_MAX_OUTPUT_CHARS` token).

### Files (this decision)

- `daemon/services/attestation_report_judge.py` — F-A constant + two fused-site swaps; legacy sites unchanged.
- `daemon/graph.py` — F-B try/except wrap around the fused block + F-C `_persist_gate_exception_marker` call on the F-B exception path; `evaluate()` call site passes `ledger=ledger`.
- `daemon/services/attestation_gate.py` — F-C `evaluate()` signature gains `ledger: Any = None`; seam (i)/(ii) catch stamps via the helper when `ledger is not None`.
- `tests/unit/test_attestation_fused_judge_truncation.py` (new) — F-A regression matrix (7 tests).
- `tests/integration/test_attestation_stage2_failopen.py` — seam (iv) re-contract + new `TestGateExceptionSeenStampFC` class (3 tests).
- `tests/probe/lca2_judge_truncation_repro.py` — flipped case-(b) expectation; docstring rewritten; still manual-only.
- `.agents/tester/RESULTS/2026-09-16-lca-stage2-flip-verification.md` — verifier's recipe (already shipped at `28edbdd8`).
- `docs/setup.md` — unaffected; Stage-2 soak runbook unchanged (the F-A/B/C fixes restore the post-flip behavior to its spec, the soak invariants are unchanged).
- This entry.

### Re-verify recipe (post-fix; per the tester's post-fix re-verification section)

- `tests/probe/lca2_judge_truncation_repro.py` → case (b) now parses `complete` (was `unparsable`×2 pre-fix). Both cases print `FIX VERIFIED`.
- Live-LLM probe `tests/probe/lca2_live_fused_judge_probe.py` → if creds allow, both payloads parse `complete` (the live-failure regression is closed). If creds unavailable, the credentials-free repro is the ground-truth substitute.
- `tests/integration/test_attestation_stage2_failopen.py` → 17/17 (was 14/14 pre-fix; +3 from `TestGateExceptionSeenStampFC`).
- `tests/integration/test_attestation_stage2_incident_abc.py` → expected 8/8 unchanged (the F-A cap change is transparent to the payload fixtures that fit).
- `tests/integration/test_attestation_stage2_budget_parity.py` → expected 20/20 unchanged (F-A/B/C do not change the budget guard — same ONE invocation per evaluation).
- `tests/unit/test_attestation_fused_judge_truncation.py` → 7/7 new (F-A regression).
- No full matrix rerun required (per the tester's scope decision).

### Convention sweep

- `docs/setup.md` Stage-2 soak section — unaffected (the F-A/B/C fixes restore spec behavior, no new invariants). `[CriticalNotes:state]`/`pinned_count` unchanged.
- `requirements.md` R-RES2 — unaffected (the FR-13 contract is now pinned at the implementation level via the new tests; no spec text change required).
- `.agents/tester/RESULTS/2026-09-16-lca-stage2-flip-verification.md` — the verifier's `Post-fix re-verification recipe` section already prescribes exactly the suite above; this commit is the implementation that satisfies it.

---

## D-RES4 — Stage-3 retirement EXECUTED (R1–R8 + ledger items a–e)

Date: 2026-09-17. Branch `feature/lca-resolver-stage3` (worktree `agents-ensemble-wt-lca-stage3`), base `f7588291b1` (`latest` at branch time). Authoritative spec: `resolver-unification.md` §7 + Appendix A (user-APPROVED item-by-item). Post-retirement net behavior = the Stage-2 flip behavior + the four approved deltas (Δ1–Δ4), and nothing else.

**USER OVERRIDE of the Stage-2 soak gate:** resolver-unification §8 gated Stage 3 on "≥1 soak period of Stage 2 in prod". The user directed Stage 3 to proceed IMMEDIATELY (2026-09-17). The revert seam is unchanged and does not depend on the soak having run: **redeploy the earlier build** (frozen-PyInstaller rebuild+restart discipline — pre-Stage-3 lineage restores the Stage-2 dead-but-present shape; pre-Stage-2 restores the legacy-authoritative shape). The in-place kill-switch matrix (judge kill-switch / mode dry / mode off) remains the cheapest incident brake BEFORE any redeploy.

### R1 — outside-window diagnostic retired
`attestation_seen_outside_window` (scanner helper, `daemon/services/attestation_scanner.py`) + the `attest_seen_outside_window` GateDecision field + the canonical-log key + the DB-error-row key DELETED. Diagnostic note (this entry): the field was LOG-ONLY since birth — no decision weight anywhere; the gate consumes only the in-window attestation scan. The behavior it made visible (a STALE pre-revive attestation aged out of the window does not satisfy the gate) remains pinned by `tests/unit/test_attestation_gate.py::test_stale_outside_window_attestation_still_denies`. `CANONICAL_LOG_SCHEMA_FIELDS` drops 18 → 17.

### R2 — gate-config meta-flag threading retired
`build_gate_config` loses `attestation_enabled` / `scope_applicable` (params + `GATE_CONFIG_KEYS` entries); the gate node stops threading `gate_config.get(...)` for them; `decide()` loses the three meta params + its step-1 meta branch + the unknown-mode branch. The C2/D3/off checks are enforced at GRAPH-BUILD time (`build_instance_graph` wires the gate node only for leaders with the master flag on; mode=off ⇒ not wired) and re-encoded as the predicate's Term 0, mirrored once at `evaluate()`'s early return (the byte-equivalent OFF baseline — no scanner, no log row on that branch).

### R3 — decide()-level DRY branch retired
`decide()` is now the PURE enforce tree (answer-pending → attested → R2 → bound → deny). The DRY_LOG mapping moved to `evaluate()`'s MODE LAYER: the enforce-tree decision is computed with full diagnostics, then mapped to `DRY_LOG` with the counter frozen at its input and the nudge disarmed. Same observable contract (dry rows: `decision=dry_log`, counter unchanged, zero side effects) — pinned by `TestDryModeAtCompositionLayer` (unit gate file) + the dry-logging/dry-mode suites.

### R4 — the decide()-level no-delegation arm retired (🚩 guard LANDED)
`decide()`'s `if not attestation_required` arm DELETED; the concept lives in ONE place — the predicate's Term 1 — mirrored by `evaluate()`'s composition bypass (plain ALLOW, counter untouched) BEFORE the enforce tree runs. **D10 mirror invariant (the loud flag):** `evaluate()`'s Source-B scan block now requires `result.attestation_required` — on a NON-delegated mission the marker/length scans are SKIPPED entirely (suspicion signals are not even evaluated), mirroring the predicate's Term-1 short-circuit of its A/B providers. Observable delta: log-row-only — `marker_hit`/`length_trigger`/`final_word_count` carry their False/0 defaults on non-delegated rows (the judge NEVER fired on those rows under the Stage-2 flip; outcomes unchanged). Pinned by `test_attestation_stage3_census.py::TestR4CensusDelegationArmDeleted` + the re-contracted quick-question tests.

### R5 — busy-suppression block retired into the predicate term
The inline `trigger_fires ∧ busy>0` bookkeeping (`trigger_source` force-clear + `trigger_suppressed_by` stamping) DELETED from `evaluate()`. The busy-mute semantics live in the predicate's `b_fires` term (`(marker_hit ∨ length_trigger) ∧ busy_descendants == 0`) — Source B busy-muted, Source A deliberately NOT (approved Δ2). `busy_descendants` (the raw count) stays a first-class field (predicate input + forensics). The `trigger_suppressed_by` log key retired; the suppression is observable via `busy_descendants>0` + zero judge rows + zero hints.

### R6 — trigger plumbing retired; scanners kept as activation-signal producers
The `(a)/(b)/(c)/(d)` route enum (`marker_path` + `MARKER_PATH_*` constants), the `marker_judge_verdict`/`marker_judge_latency_ms`/`marker_judge_error_class` stamp fields, and the `trigger_source` derivation DELETED from the gate. The scanners (`scan_for_mid_work_markers` 16-pattern catalog, `scan_for_short_final_ai` <150-word threshold) KEEP their homes and contracts — they feed the predicate's `b_fires` term and the canonical row's `marker_hit`/`marker_terms`/`length_trigger`/`final_word_count` fields. Canonical format-string placeholders: 34 → 27.

### R7 — BOTH legacy judge sites + the flip constant DELETED (🚩 guards LIVE)
`_LCA_STAGE2_RESOLVER_FLIP` (graph.py) DELETED — the fused block is guarded only by `resolver_snapshot is not None`: EXACTLY ONE completion path (predicate → conditional fused judge → outcomes). The legacy marker-path judge block (~438 LoC) and the legacy would-be-deny judge block (~166 LoC) deleted from `create_attestation_gate_node`; the legacy judge service surface (`judge_completion_report_async`/`_sync`, `JudgeResult`, `JUDGE_SYSTEM_PROMPT`, `_parse_judge_response`, `_slice_judge_window`, `_format_window_for_judge`, the legacy caps `JUDGE_MAX_INPUT_CHARS`/`JUDGE_MAX_OUTPUT_CHARS`/`JUDGE_MAX_WINDOW`/`JUDGE_DEFAULT_WINDOW`) deleted from `attestation_report_judge.py`. **Both R7 neutrality mappings hold on the single path** (pinned by the R7 invariant suite, re-contracted `TestLegacySitesDeleted`): (1) judge error/timeout/unparsable×2 → deny+nudge BOUND-ENFORCED (path-(d)-exact incl. the at-bound edge → terminal_after_bound) — NEVER fail-safe allow; (2) kill-switch OFF → deny band deny+nudge WITHOUT judge (Q1 parity), marker/A bands plain-ALLOW.

### R8 — legacy judge event family removed
The `*_marker_judge*` event names and the bare `leader_completion_gate_judge` row died with their call sites; the single family is `leader_completion_gate_fused_judge` / `_fused_judge_disabled` / `_fused_judge_error`. The `_disabled` row's context field changed from the retired `trigger_source=` to `marker_hit=%s length_trigger=%s` (the surviving B-signal fields). Dashboards grepping the legacy names must migrate (the soak-period alias window the plan allowed is closed by this retirement).

### Stage-3 ledger items (a)–(e)
- **(a) FULL B-redaction — SHIPPED.** `_build_b_section` now wraps every leader-prose excerpt with `redact_ids(..., 'leader')`; the f926de24 docstring scope-note workaround ("full B-redaction lands in Stage 3") is REPLACED by truthful docstrings on `redact_ids` + `assemble_fused_bundle`. All id-bearing bundle text (A structural fields, B prose, C tree rows) is redacted per the 98b59dd7 boundary. (Task-note correction: the bundle assembly lives in `attestation_resolver_activation.py`, not `attestation_report_judge.py` as the task text said.)
- **(b) F2 fail-open target PIN — NAMED.** `tests/integration/test_attestation_stage2_failopen.py::TestF2FailOpenTargetPin::test_f2_fail_open_target_mirrors_kill_switch_off` pins the aggregate: on resolver-compute faults (seam i), deny band → deny+nudge without judge; marker/A bands → plain allow — the kill-switch-off mirror.
- **(c) F-C marker-write seam — SHIPPED.** `child_reports.py`: the lazy `attestation_marker_scanner` import hoisted to module top (out of the per-completion hot path; ImportError now surfaces at boot, not mid-transaction); the marker-write catch splits `ImportError` (LOUD ERROR row, `deploy_bug=true`) from runtime errors (the existing advisory-lost WARNING row). Both non-blocking (SAVEPOINT guarantees unchanged).
- **(d) conservative rescue-judge-forfeit on fault rows — DOCUMENTED (this entry).** On ANY resolver-compute fault (seams i/ii) or fused-block fault (seam iv), the row FORFEITS its rescue-judge opportunity: a deny-band row that the fused judge would have rescued (verdict=complete → allow) instead takes the conservative deny+nudge, and — at the bound — the canonical terminal_after_bound. This is the deliberate DP-5-REJECTED direction: the fault path must never become a fail-safe-allow bypass, at the accepted cost that genuine completions caught in a fault window are nudged (bounded by deny_bound; the F2 re-fire tests prove the next clean evaluation re-fires the judge and can rescue).
- **(e) `noqa: BLE001` justification — VERIFIED.** Every BLE001 suppression in the fused region carries an explanatory note (kill-switch resolver fault / wrapper-layer fault / F-B fail-open seam iv); pinned by `test_attestation_stage3_census.py::TestR5R6CensusTriggerPlumbingDeleted::test_noqa_ble001_comments_justified_in_fused_region`.

### Census (deleted-code negative pins)
`tests/unit/test_attestation_stage3_census.py` (23 tests; 22 at the retirement commit + 1 added in the review round — the bare `leader_completion_gate_judge_error` suffix-variant pin, see §10 of the retirement report): flip constant, legacy judge entry points + helpers + events, R1 field, R2 config keys + decide signature, R3/R4 branches, R5/R6 fields + constants, scanner-catalog SURVIVAL pins, ledger-(c) seam pins — any resurrection is loud.

### Family test reconciliation (SQLite lane)
Baseline 940 collected / 64 files → post-retirement: unit 671 green (incl. +22 census, −~40 legacy-judge/legacy-field tests, re-contracted classes throughout), integration 214 green / 31 skipped / 1 pre-existing hang (see report), migration 18, probes 2. Arithmetic + per-file detail: `stage3-retirement-report.md`.

### Pre-existing issues found (NOT introduced here)
- `tests/integration/test_attestation_bound_escalation.py::test_bound_plus_one_escalates_once_without_fourth_nudge` HANGS (asyncio selector wedges past the pytest-timeout thread dump) at BASELINE (f7588291, zero edits) in this worktree — pre-existing; excluded from the family matrix and left for the tester lane.
- resolver-unification.md §2.2 D10 claim ("the marker scan never runs today, gate.py:1101") was WRONG about the pre-retirement gate (both f2611f07 and f7588291 scanned the conditional-off branch, log-only); the plan's D10 mirror is now literally true post-R4 (the scan is skipped). Recorded here so nobody "fixes" it back.

- Backlog: council doc-pass suggestions 5–8 (2026-09-17) deferred by council decision — not folded in the 7089e73b follow-ups.

## D-U1 — Section U (user intent) in the fused bundle + judge intent-fulfillment instruction (2026-09-18, incident 4dfded83)

**Incident.** Leader instance `4dfded83-5912-4588-8c60-ca4fdd0464c5`. The user asked (Q1, 2026-09-18 04:42:08 UTC, verbatim): "what is current service tool description that agents will see?" The leader's step-297 answer (3029 chars, 3-layer enumeration of service_* tool descriptions) directly and completely answered it — informally, with no report headers. The completion gate nonetheless fired a "Completion Check Nudge" at step 298 (04:45:46–58 UTC), forcing a redundant Mission Report (step 299) + `attest_completion` (04:46:00.951 UTC); denial epoch `7e8a6323-6166-5a51-bd47-592957a6948c` minted. Root cause (Phase A, verified read-only; prod = v0.13.3 ≡ a6442bff = tip, so NOT a stale-build artifact): `assemble_fused_bundle` carried ONLY sections A (child-report advisories ≤3000) / B (leader last-3-AIMessages ≤6000) / C (first-10 descendant tree rows ≤3000) — the user's request was NOWHERE in the bundle, and `FUSED_JUDGE_SYSTEM_PROMPT` never asked about intent-fulfillment, so the judge scored report-shape/tree-status, not whether the user's ask was answered.

**Fix (Phase B, branch `feature/lca-judge-user-intent`, base a6442bff).**
- **Section U** in `assemble_fused_bundle` (`daemon/services/attestation_resolver_activation.py`): renders the CONTENT of the last real user message under the header `=== SOURCE U: the user's original request for this mission ===`. The anchor REUSES the delegation scanner's already-computed `delegation_scan.last_real_user_index` (gate extracts the message CONTENT at the `evaluate_resolver_activation` call site — no re-walk, no new source scans, no new DB queries); `assemble_fused_bundle` re-checks the passed message with the scanner's canonical `is_real_user_message` predicate and fails CLOSED to omission (a nudge/injected message must never render as "the user's request"). Cap `BUNDLE_U_SECTION_MAX = 2000`; id-redacted with the same `redact_ids` (`slot_hint="user"`).
- **Fallback**: anchor absent (or non-user message) → U OMITTED entirely; `FusedBundle.user_message_included=False`, `u_chars=0`; the eval row logs `user_message_included=False` (NOTE: Python `%s` bool rendering — the convention of every other boolean field on the row, e.g. `fail_open=`; the spec's lowercase `false` example was adapted to family convention, pinned in tests).
- **Budget decision (pinned in tests)**: total cap raised 12000 → 14000 with U ADDITIVE — the raise is EXACTLY the U cap so the pre-existing A/B/C caps (3000/6000/3000) and their pins are untouched, and the legacy maxed A+B+C shape now never hard-clips (verified: the natural A+B+C maximum fits within the new 14000 total — pinned by the test pins — so the total clip becomes defensive for realistic shapes). QUALIFIER (review-fix, 2026-09-18 council): "defensive-only" overstated it — the clip is NOT a mathematical impossibility: an all-sections-EXACTLY-at-cap assembly totals 14034 > 14000 (the per-section clips bound A+B+C+U at exactly 14000 chars, but assembly adds the `[LCA FUSED EVIDENCE BUNDLE v1]` header + section separators, +34); the SAME pre-existing class as base (12033 > 12000 at the old 12000 cap — header + separators, +33). Not a regression; the final hard clip still enforces the ≤14000 wire invariant. Final hard-clip-with-truncation-marker discipline unchanged.
- **Judge prompt** (`FUSED_JUDGE_SYSTEM_PROMPT`): enumeration now FOUR sections (U first) and carries the intent-fulfillment instruction: a message that genuinely ANSWERS or FULFILLS the user's request IS a completion report regardless of formality/formatting/shape; a formal-looking report that does NOT address the request is NOT complete. Conservative default, strict single-line JSON contract, retry-once-on-unparsable, and `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048` all unchanged.
- **Judge budget UNCHANGED**: ≤1 logical invocation — U is input enrichment at the single existing call site (`judge_fused_bundle_async`); no new call sites (spy-pinned).
- **Witnesses**: `FusedBundle` gains `u_chars` + `user_message_included`; the `event=leader_completion_resolver_eval` row gains `bundle_u_chars=` + `user_message_included=` beside the existing bundle witnesses (key=value additive evolution — grep consumers are field-name-anchored).

**Pins**: `tests/unit/test_attestation_resolver_user_intent.py` (25 tests) — incident-shape intent-match (informal genuine answer + complete → rescue/allow, no nudge), intent-mismatch (formal report ignoring the ask + not_complete → deny+nudge), anchor-absent omission (unit + node-level incl. nudge-shaped HumanMessage exclusion), fail-closed non-user re-check, U cap+ellipsis, UUID redaction, sha256/size witness flips, A/B/C-untouched, legacy-shape-no-clip, maxed-assembly-within-total, eval-row fields, prompt identity + JSON-contract + output-cap pins.

## D-CTD-7 — Advisory note REMOVED (2026-09-18 user decision)

**User decision (2026-09-18, FINAL).** The LCA ``[SYSTEM CONTEXT: Child Report Check]`` advisory note is REMOVED entirely. Reasoning: it was Stage-0 UX legacy predating the fused judge; high-FP (awaiting/standby/in-progress phrases inside legitimate completions); demanded leader verification work; and its task-less mint caused the strand-wedge class (the wedge fix in commit 222eddba is the only thing that kept it from re-parking root instances at WAITING_CHILDREN forever — a future relighting of that wedge would re-open the bug). The LLM judge now subsumes the gate role.

**Scope.** Three Stage-0 sub-roles decomposed:

  (i) **Advisory note mint → REMOVED.** The ``daemon/services/child_reports.py::_process_child_completion_db_sync`` mint block is deleted: the SAVEPOINT-scope, the second ``MessageQueue`` row INSERT, the ``PROCESS_MESSAGE`` delivery ``Task`` mint, the ``notify_worker_pool`` wake flag (no longer needed — no Task to claim), the ``event=leader_completion_gate_child_report_check_fired`` / ``_failed`` log rows. The note-text constant and the note-specific marker kwargs (``child_report_check=True`` + ``child_report_check_terms=[...]``) are gone with the mint.

  (ii) **A-signal feeding the activation predicate / A-band trigger → KEEP UNTOUCHED.** The 17-pattern catalog (:data:`daemon.services.attestation_marker_scanner.CHILD_TERMINAL_PROMISE_MARKERS`) is preserved byte-identical — the user explicitly declined tightening (one extra quick judge call vs. broader real-child-lie coverage; the user picked coverage). The scanner function (:func:`scan_child_terminal_report_for_promises`) is kept as a public module surface so a future re-attachment of a different consumer is unblocked. The resolvers's :func:`collect_source_a_signals` and :func:`activation_predicate` paths are untouched.

  (iii) **Judge bundle A-section evidence content → KEEP UNCHANGED.** The A-section cap (``BUNDLE_A_SECTION_MAX = 3000``) and the ``_build_a_section`` renderer stay. With no notes minted, the resolver's Source A surface is dormant in production today (no notes to read) — but the wiring, the bundle assembly, and the activation predicate's ``a_suspicion`` term all stay as future re-attach points.

**Strand-fix commit 222eddba parts — preserved / discarded (per task brief).**

  * **Mint-with-delivery (SAVEPOINT + Task + wake) → DISCARDED.** The mint site is gone, so the literal Task mint is moot. The wake flag (``notify_worker_pool``) on ``_ChildCompletionDbResult`` is removed (it was note-only).
  * **Finalizer count-guard predicate hardening → KEPT.** :func:`daemon.repositories.message_queue.predicates.finalizer_counts_as_pending` and :data:`CHILD_REPORT_CHECK_SOURCE_PREFIX` are defense-in-depth for any future task-less row producer. Refinement 1 (``source.startswith(CHILD_REPORT_CHECK_SOURCE_PREFIX)`` exclusion) is now dead-but-harmless — no rows have that prefix. Refinement 2 (unknown task-less READY rows count conservatively with a WARNING) is the live hardening.

**Context-kind constant.** :data:`daemon.services.context_messages.CONTEXT_KIND_CHILD_REPORT_CHECK` is KEPT so the resolver-side ``_is_child_report_check_note`` detector can still recognize note-shaped messages that older agents may have preserved through compaction (the A-signal path stays untouched per (ii)). The corresponding ``_stable_id_for`` branch in :mod:`context_messages` is REMOVED (no remaining callers after the mint deletion).

**A-signal-vs-event mapping (the MANDATORY MAPPING QUESTION).** The structured log event ``event=leader_completion_gate_child_report_check_fired`` (and the ``_failed`` suffix variant) is SOLELY the note-mint-side log row — emitted at the time the mint INSERT (or its SAVEPOINT rollback) occurred. It is NOT the A-signal the resolver consumes; the resolver consumes NOTE MESSAGES (``HumanMessage`` instances with ``context_kind=child_report_check`` kwargs OR the ``[SYSTEM CONTEXT: Child Report Check]`` content prefix), which are produced by the mint site. With the mint site gone, both the event and the messages are gone. The note MESSAGE is the A-signal data path; the log event is note-only. Per task brief: the event is removed because it is note-only.

**Pins added (D-CTD-7 resurrection-loud).**

  * **Negative mint-resurrection pin**: ``tests/unit/test_child_terminal_contradiction.py::TestSourcePins::test_note_mint_site_is_gone_from_child_reports`` — production-code shapes (the MessageQueue constructor with the source prefix, the Task constructor for PROCESS_MESSAGE delivery, the SAVEPOINT ``session.begin_nested()``, the marker-kwargs assignments, the structured log events emitted at mint time) MUST NOT appear as production code in ``daemon/services/child_reports.py``.
  * **Positive A-signal-path preservation pin**: ``tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins`` — verifies the activation predicate still consumes ``collect_source_a_signals``, the ``a_suspicion`` term still ORs the four Source-A fields (advisory_present / contradiction_flag / phrase_match / word_count_below_threshold), the A-section bundle cap stays at 3000, and the 17-pattern catalog is byte-identical.
  * **Catalog byte-identity pin**: ``TestDCTD7ASignalPathPins::test_catalog_byte_identical_after_removal`` — the catalog size is pinned to 17 entries (catalog size at the user's pinned preservation).

**Note-specific tests removed.**

  * ``tests/unit/test_child_terminal_contradiction.py``: 26 tests removed (TestChildReportCheckStableId class — 6 tests; TestChildTerminalContradictionHook class — 14 tests; TestChildReportCheckMintWithDelivery class — 6 tests). Total file shrinks 40 → 14 tests.
  * ``tests/unit/test_attestation_stage3_census.py``: 2 tests removed (TestLedgerCMarkerWriteSeam::test_scanner_import_hoisted_out_of_hot_path and ::test_marker_write_catch_splits_import_error — both obsolete after the mint block is gone; replaced by a comment referencing D-CTD-7). Total file shrinks 23 → 21 tests.

**Operator-visible observability change.**

  * The ``event=leader_completion_gate_child_report_check_fired`` log row no longer fires (mint gone). Operators who ``grep`` for it will get zero hits; this is expected. The 17-pattern catalog is still the gate's catalog for the leader-side scanner (:data:`MID_WORK_MARKERS` is a separate catalog for the leader's allow path; ``CHILD_TERMINAL_PROMISE_MARKERS` is for the now-removed child-side detector).
  * No new env flag introduced (per fix/flag policy 7d5285aa). The detector shipped always-on with the catalog; the catalog stays always-on for the gate's scanner surface and any future re-attach point.

**Acceptance criteria changes** (see ``requirements.md`` — note-specific ACs marked SUPERSEDED 2026-09-18, pointer to this D-entry).

---

## D-CTD-8 — A-band RESTORED via evaluation-time transcript scan (2026-09-18, user standing decision Q2 + leader shape)

**Context.** D-CTD-7 (above) removed the Stage-0 mint-with-delivery note machinery and left the A-band **DORMANT** (the catalog + scanner + `_is_child_report_check_note` + `collect_source_a_signals` wiring were all preserved but unfed by an empty note stream). The user's standing decision (Q2, 2026-09-18): **A-band stays ACTIVE with the catalog UNCHANGED** — restore the signal WITHOUT resurrecting the note.

**Restoration shape (leader-decided, binding; APPROVED 2026-09-18).** The A-band now scans the leader's in-context child-report ``HumanMessage`` rows (the ``internal_report:<child_iid>`` stamp emitted by the report-injection drain at ``daemon/graph.py:6950-6967`` and the fallback ``_stamped_additional_kwargs`` path at ``daemon/services/instance_messaging.py:525``) for the 17-pattern catalog **at gate-evaluation time**, in the SAME window the A/B/C reads already use. The delivery-time machinery is gone FOREVER — no note, no SAVEPOINT, no ``PROCESS_MESSAGE`` Task mint, no notify, no mint-side log row.

**Catalog deliberately unchanged.** User ruling (Q2): the catalog is preserved BYTE-IDENTICAL (17 entries; the existing ``test_catalog_byte_identical_after_removal`` pin at ``tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins`` stays green — no entries added, none removed). The judge filters FPs at one quick call per gate-eval-fires-A; the user's call is **coverage over a tighter catalog**.

**Closure FP specimens (2026-09-18, runtime corroboration).** The still-live advisory fired on both removal-task children's completion reports — coder ``bd5f7b2d`` and reviewer ``9abfcb33`` — because each report quoted the 17-marker catalog VERBATIM as preservation evidence ("the catalog includes 'awaiting' and 'ending turn' … …"). Self-referential FP class: catalog preservation language triggers the catalog. The user's preservation-by-quote decision is exactly what made those reports FP-positive. Direct runtime corroboration of the removal rationale; cited here as the empirical anchor for "judge filters FPs at the cost of one quick call".

**Field mapping (old note-stamped → new scan-derived).** The 4-field Source-A OR shape (``advisory_present ∨ contradiction_flag ∨ phrase_match ∨ word_count_below_threshold`` → ``a_suspicion = bool(...)``) is preserved verbatim — D2 SEMANTICS STAY (A-band not busy-suppressed, activates alone). Each field is re-derived from the evaluation-time scan:

  +---------------------------+----------------------------------------------------+
  | Field (SourceASignals)    | New meaning (evaluation-time scan of transcript)   |
  +===========================+====================================================+
  | ``advisory_present``      | True iff at least one catalog-hit child-report     |
  |                           | message in the window (preserves old ≡ phrase      |
  |                           | semantic).                                         |
  +---------------------------+----------------------------------------------------+
  | ``phrase_match``          | True iff at least one catalog-hit child-report     |
  |                           | has a non-empty ``matched_terms`` set (primary    |
  |                           | trigger; old semantic preserved).                 |
  +---------------------------+----------------------------------------------------+
  | ``contradiction_flag``    | True iff any catalog-hit's ``matched_terms``       |
  |                           | include an explicit-contradiction marker          |
  |                           | (``still pending`` / ``not yet complete`` /        |
  |                           | ``interim``). Raises the strongest "claimed-      |
  |                           | done-but-isn't" sub-signal. Was always False in   |
  |                           | the deleted producer; now live (forward-          |
  |                           | compatible — predicate was already OR'ing it).   |
  +---------------------------+----------------------------------------------------+
  | ``word_count_below_       | True iff any catalog-hit child-report's raw word  |
  | threshold``               | count is < 150 (Source B's ``length_trigger``     |
  |                           | mirror). The 150-word threshold matches the        |
  |                           | Source B short-completion heuristic exactly.      |
  +---------------------------+----------------------------------------------------+

**Compaction question (the file:line-verified outcome).** The drained child-report ``HumanMessage`` rows carry the standard live stamp ``additional_kwargs={"injected_message": True, "source": f"internal_report:{child_iid}"}`` — bare-flag ``injected_message`` with NO ``context_kind`` (per ``daemon/services/compaction.py:130-148``). Behavior: protected UNTIL answered, then absorbable. At gate-evaluation time, the just-delivered report is FRESH (drain → LLM answer → gate eval, all in this turn, no compaction cycle between); older reports MAY be absorbed by compaction in subsequent turns — but those reports were captured by their own turn's gate-eval call at the time. The semantic contract is "turn-cycle-relative contradiction suspicion", not "lifetime ledger", and the A-band correctly catches new lies this turn. Verdict: NOT a blocker; bare-flag absorbable is the correct steady-state for a turn-relative signal.

**Legacy-note detector kept as defense-in-depth.** :func:`daemon.services.attestation_resolver_activation._is_child_report_check_note` (the prefix-fallback detector) is RETAINED — old notes may still ride in long-running checkpoints that survived the upgrade (they're ``context_kind=child_report_check`` permanently hoisted). The live A-signal source is the transcript scan; the note-path adds no new evidence rows in production. The legacy detector is the resurrection insurance, not the live producer.

**Do-NOT-touch list.** Judge service, ``MID_WORK_MARKERS`` / Source B, tree-status / Source C, bound / escalation, nudge / hint texts, U-section, count-guard predicates (``daemon/repositories/message_queue/predicates.py``). The change is STRICTLY: ``collect_source_a_signals`` + the two new helpers (``_is_child_report_message``, ``_build_evidence_from_report_message``) + three constants (``_INTERNAL_REPORT_SOURCE_PREFIX``, ``_CONTRADICTION_MARKERS``, ``_SHORT_REPORT_WORD_THRESHOLD``).

**Pins added (D-CTD-8 restoration-loud).**

  * **Functional pin** — ``tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins::test_functional_pin_internal_report_message_produces_a_signal``: an ``internal_report:``-stamped ``HumanMessage`` containing a catalog phrase MUST produce ``advisory_present=True`` through the real ``collect_source_a_signals`` code path. The literal-must-pass assertion re-anchors on the evaluation-time scan, replacing the deleted mint-time ``notify_worker_pool`` flag as the structural witness of A-band liveness.
  * **Live-path source-A collection tests** — ``TestSourceACollection``: 7 new tests (``test_internal_report_with_catalog_phrase_fires`` — the FP-positive canonical seed, ``test_internal_report_without_catalog_phrase_does_not_fire`` — clean completions do not raise the band, ``test_user_injected_note_does_not_match_internal_report_path`` — disambiguation from user-injected notes, ``test_a_system_message_with_internal_report_source_does_not_match`` — type guard, ``test_contradiction_flag_raised_on_explicit_marker``, ``test_word_count_below_threshold_raised_on_short_report``, ``test_legacy_note_and_live_report_paths_coexist`` — both paths bounded by ``A_EVIDENCE_NOTES_CAP``).
  * **Negative mint-resurrection pin stays green** — ``tests/unit/test_child_terminal_contradiction.py::TestSourcePins::test_note_mint_site_is_gone_from_child_reports``: the 10-needle production-code grep stays untouched. Verification: no production-code shape from the deleted mint block re-appears in ``daemon/services/child_reports.py``.
  * **No-new-env-flag pin stays green** — ``test_no_new_env_flag_added``: zero new ``os.environ``/``os.getenv`` reads referencing ``CHILD_REPORT_CHECK``. The A-band ships always-on per fix/flag policy 7d5285aa.

**Requirements.md changes.** A-band acceptance criteria SUPERSEDED 2026-09-18 with pointer to this D-entry; the supersession pattern matches the D-CTD-7 note-specific AC supersession (no silent deletion).

**Activation contract (no behavior drift outside the A-band).** ``activation_predicate`` is BYTE-IDENTICAL on the wire (same 4-field OR, same D2 semantics). ``_build_a_section`` is BYTE-IDENTICAL (same renderer, same cap ``BUNDLE_A_SECTION_MAX = 3000``). The fused bundle's A-section can now render scan-derived ``ChildReportCheckEvidence`` rows (with ``child_instance_id`` extracted from the ``source`` stamp's uuid substring) instead of note-body-parsed ids — visual surface unchanged.

**Out of scope (deferred).** Repairing the legacy note-path detector to prefer the stamped kwargs surface when both old notes and new internal_report messages exist on the same conversation (rare cross-upgrade scenario). The double-evidence-row case is bounded by ``A_EVIDENCE_NOTES_CAP = 5`` and produces one row + a stable_id dedup; the budget stays correct.

---

## D-ENTRY 2026-09-19 — Incident bc145c7e R1: fused judge retry-once-on-timeout (rescuer-path supersession)

**Trigger:** Incident bc145c7e (2026-09-19, R1 read-only-verified). A delegated investigation mission produced a complete report-shaped answer and obeyed the suppression rule (no ``attest`` call); the deny-band rescuer judge TIMED OUT at exactly 25.000s (attempt 1 of 1, ``verdict=timeout``, ``reason=""``, ``model=quick``) → conservative fail-safe deny → nudge → leader attested next turn → clean complete. No spec violation, but the suppression rule makes the judge load-bearing for EVERY delegated completion, and quick-model tail latency (documented 2.6–22s live, 25s cap) makes the class recurring. The class-D design DELIBERATELY excluded timeout from retry (timeout kept fail-safe semantics) — this decision SUPERSEDES that exclusion for the rescuer path.

**Decision — ``judge_fused_bundle_async`` retries ONCE on attempt-1 ``TimeoutError`` (mirrors the unparsable retry shape):**

The rescuer judge retries ONCE when attempt 1 raises :class:`asyncio.TimeoutError`. SAME per-attempt timeout window for the retry (``_resolver_get_judge_timeout_s()``, default 25.0s, env-tunable via ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``). Attempt accounting via :attr:`FusedJudgeResult.attempt` (1 → 2). The retry fits the existing budget sentinel exactly like the unparsable retry:

* ``entries == 1`` (ONE logical invocation per evaluation — the pre-flip budget pin still holds)
* ``len(spy.attempts) <= 2`` (the retry fires AT MOST ONCE — the existing pin's ``<=`` already covers timeout-then-success)

DISTINCT log discrimination (three tokens, one per state):

* ``event=fused_judge_first_attempt_timeout`` — first attempt timed out, retry pending. Format: ``timeout_s=%s latency_ms=%s will_retry=true``.
* ``event=fused_judge_timeout_retry`` — retry was fired on the timeout path. Format: ``timeout_s=%s attempt=%s``.
* ``event=fused_judge_timeout_post_retry`` — both attempts timed out (post-retry timeout path). Format: ``timeout_s=%s retry_latency_ms=%s``.

The canonical graph-node log row at ``daemon/graph.py:5341`` (``event=leader_completion_gate_fused_judge``) already discriminates via ``verdict`` + ``attempt`` + ``error_class`` — ``verdict=timeout attempt=2 error_class=TimeoutError`` is the post-retry timeout surface; ``verdict=complete|not_complete attempt=2`` is the recovered-retry surface. The three tokens above are forensic granularity inside the function for grep triage.

**Decision — HTTP/API errors keep NO retry (unchanged):**

The ``except Exception`` arm still returns conservative fail-safe ``verdict=error attempt=1`` immediately. Only :class:`asyncio.TimeoutError` triggers the retry. DP-5 posture is preserved: error/timeout/unparsable-after-retry NEVER fail-safe-allow.

**Decision — worst-case latency accounting (2 × timeout_s):**

With retry, worst-case wall-clock on the routing path before fail-safe deny is ``2 × JUDGE_TIMEOUT_S`` (default: 2 × 25.0s = 50.0s). Documented in:

* ``daemon/services/attestation_report_judge.py`` module docstring (Bounds section + the retry-once-on-unparsable comment block)
* ``daemon/services/attestation_report_judge.py::judge_fused_bundle_async`` ``timeout_s`` parameter docstring
* ``docs/setup.md`` runbook note (JUDGE_TIMEOUT_S section, retry semantics, worst-case)

**Decision — ``first_unparsable_excerpt`` is NOT stamped on a timeout-retry path:**

The :attr:`FusedJudgeResult.first_unparsable_excerpt` field is reserved for the forensic surface of an unparsable-on-attempt-1 row (incident 98b59dd7). A timeout leaves no body to redact/excerpt, so the field stays ``None`` on every timeout-retry path (recovered-retry, post-retry-timeout, timeout-then-error). This preserves the dataclass invariant that ``first_unparsable_excerpt is not None iff attempt == 2 AND the prior attempt was an unparsable row``.

**Rationale — why this supersedes the class-D no-timeout-retry decision:**

* The suppression rule (no ``attest`` call for non-deny-band rows; the judge is the ONLY way the deny band allows end-of-mission) makes judge reliability load-bearing for EVERY delegated completion.
* Quick-model tail latency is documented 2.6–22s live with a 25s cap — recurring class, not a one-shot.
* A successful retry RECOVERS the false-positive class (incident bc145c7e R1 itself: a complete report + clean rescue would have allowed end-of-mission without the nudge round-trip).
* Post-retry timeout still → conservative fail-safe deny → DP-5 posture unchanged.
* The retry fits the existing budget sentinel (``entries==1 && attempts<=2``) exactly — no expansion of the per-evaluation judge budget, only a re-distribution of the existing budget across attempt-1 and attempt-2 on the rescuer path.

**DO NOT TOUCH (this decision):** ``nudge/hint`` text constants (``ATTESTATION_NUDGE_TEXT``, marker hint message body); deny-band band-mapping (the rescuer's verdict-driven band mapping at ``daemon/graph.py``); marker/length triggers (``MID_WORK_MARKERS``, ``SHORT_REPORT_WORD_THRESHOLD``); Section U bundle rendering; bound/escalation (``deny_bound`` predicate, ``attestation_denied_count`` semantics); kill-switch coupling (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` disables the judge ENTIRELY — retries included, unchanged).

**Files (this decision):**

* ``daemon/services/attestation_report_judge.py`` — ``judge_fused_bundle_async`` retry-once-on-timeout branch (three new logger.info tokens); module docstring + ``timeout_s`` parameter docstring + retry-once-on-unparsable comment block updated for the new retry class.
* ``tests/unit/test_attestation_fused_judge.py`` — five new tests: ``test_timeout_retries_once_recovered``, ``test_timeout_post_retry_conservative_deny``, ``test_timeout_then_error_post_retry_conservative``, ``test_timeout_retry_log_discrimination``, ``test_timeout_post_retry_log_discrimination``. Existing ``test_timeout_no_retry_conservative`` retired (its assertion — ``timeout → 1 attempt, attempt=1`` — was the inverse of the new contract).
* ``docs/setup.md`` — runbook note under JUDGE_TIMEOUT_S (retry semantics, worst-case 2 × timeout_s).
* This file (``decisions.md``, this D-entry). ``requirements.md`` is NOT modified by this change (acceptance-criteria SUPERSEDED notes belong to a separate append-only ledger entry, out of scope here).

**Matrix verification (this decision, scoped to the attestation pack — NOT repo-wide):** Glob-enumerated ``tests/**/test_attestation*.py`` → 62 files collected (the cohort breakdown is authoritative per file — do NOT re-run to verify, just reconcile the ledger to the cohort table):

* 29 unit files / 671 tests PASS (``tests/unit/test_attestation_*.py`` + ``tests/unit/tools/test_attestation_*.py``).
* 15 self-contained integration files / 127 tests PASS, per-file enumeration (corpus replay, O1 boot assert, marker bound enforcement, marker routing, nudge supersede, runbook drift, observability, wakeups helper, dry mode, mode tri-state [non-enforce subset], user answer pending, stage2 failopen, stage2 killswitch, must-not-break, c2 both branches). Stage-3 zoo, compaction, config, performance are NOT in the self-contained cohort (either deselected under default addopts or live-LLM-only).
* 13 live-LLM integration files / 67 tests env-blocked (SSE hang on ``llm_stream_watchdog`` — requires live LLM provider infra, exactly like ``tests/probe/lca2_live_fused_judge_probe.py``).
* 1 migration file / 18 tests: 17 PASS + 1 known env-fail (PG-only ``DROP CONSTRAINT IF EXISTS`` trap; ``migration 20260714_000001``).
* 1 postgres file / 21 tests fully deselected under default addopts (PG-only marker).

Glob total: **62 files / 904 tests**.

## D-ENTRY 2026-09-20 — dual-autopsy B1: stale-A never-clear defect; 5-part fix

**Trigger (two production autopsies, 2026-09-19/20):** the fused judge's 4-source bundle (U=user ask, A=child-report advisories, B=leader prose, C=tree rows) carried STALE Source-A evidence that NEVER cleared. Incident **acbf5627**: 4/4 final advisories stale — giter phase-stop protocol language ×3 (superseded reports accumulating in the transcript scan) + operator-scope "still pending (rebuild+restart)"; they held `a_suspicion` into the final quiet-tree evals. Incident **fba90db8**: reviewer's "still pending on my ledger" advisory for work later APPROVED. Secondary: the B-section clipped acbf5627's final leader report at 1500/2372 chars, losing the evidence tail + activation note from the judge bundle.

**Decision — five-part fix (approved B1 spec; branch `feature/lca-stale-a-fix`, base 94fc6da1):**

1. **A-scan NEWEST-REPORT-ONLY per child** (`collect_source_a_signals`, Pass 2 rework): each child's LATEST `internal_report:` message (transcript order) is the ONLY one scanned; superseded reports from the same child drop wholesale. PINNED EDGE SEMANTICS (chosen + pinned in `TestNewestReportOnlyPerChild::test_earlier_only_contradiction_does_not_resurface_when_latest_clean`): an earlier report's contradiction that the LATEST report does not repeat does NOT resurface — delivery supersedes promise. The lie must live in the child's NEWEST report to surface; the surfacing half is the later-contradiction exception in (2).
2. **Cross-resolution A vs C** (B1 item 2): when the tree-rows provider is wired (real path via `evaluate_resolver_activation`, fetch-once cached + shared with the bundle's C-section; lazy — invoked only when advisory candidates exist; best-effort — a raise ⇒ no rows ⇒ suppression no-ops), advisories from a child whose tree status is `completed` (`_CROSS_RESOLVE_DELIVERED_STATUS`; ONLY that status — terminated/error/failed keep advisories; absent-from-rows keeps) are SUPPRESSED unless the later-contradiction exception holds: the child's newest report still carries a GENUINE (non-operator-scoped) hit — a completed child that lied at the end must still surface (the child-lie class pin; flagship regression `TestCrossResolveAgainstTreeRows::test_child_lie_completed_without_delivery_still_fires`). Legacy note entries participate: a completed child's note is suppressed unless the child's newest live report carries a genuine hit (no live report at all ⇒ conservative keep).
3. **Operator-action scoping** (B1 item 3): a catalog hit whose containing sentence (split on `[.!?\n;]`; colon deliberately does NOT split) also names an operator action (`_OPERATOR_ACTION_TOKENS` = rebuild / restart / re-deploy / redeploy — kept tight; bare "deploy"/"activation" excluded) is the OPERATOR's pending action, not undelivered child work: EXCLUDED from `contradiction_flag` and disqualified from the lie exception; the advisory stays visible (demoted, not excluded — the judge weighs it via (4)). Recorded on `ChildReportCheckEvidence.operator_scoped_terms` (additive frozen-dataclass field, default ()).
4. **Judge prompt A/C-subordination SOFTENED** (`FUSED_JUDGE_SYSTEM_PROMPT`, attestation_report_judge.py): the subordination line now carves out the two stale-advisory classes (later-delivered supersession + operator-action pending) while keeping subordination VERBATIM for GENUINE unresolved advisories — the false-rescue guard (council pin 1b343329, child-lie class) does NOT regress. Byte-tail discipline held: new sentences inserted BEFORE the "Be CONSERVATIVE" anchor; the strict-JSON contract tail byte-stable (`_EXPECTED_PROMPT_BYTE_TAIL` pin untouched, 501 chars). The old verbatim subordination pin (`test_u_fulfilled_a_advisory_c_live_judged_not_complete_honored`) was RE-CONTRACTED to the new softened contract (superceded-doctrine re-contract, atomic with this change).
5. **B-section per-message clip 1500 → 2500** (`_B_MESSAGE_CLIP`, `_build_b_section`; docstring updated): incident acbf5627's final report evidence tail + activation note survive into the judge bundle. The B-section external cap (≤6000) and the ≤14000 total are UNCHANGED — only the per-message truncation point moves. Pinned by `TestBSectionClip2500` (2500-inclusive boundary / 2501 clip / 1501-no-longer-clipped / section-cap-still-binding).

**HARD CONSTRAINTS HONORED:** zero edits to `daemon/graph.py` (verified: the sibling lane `feature/lca-attest-first-contract` owns the gate-hold/meta_bypass/nudge/rule.md seams); judge-call budget unchanged (no new LLM calls — the cross-resolution is a pure provider read shared with the existing bundle C-section fetch); predicate band structure unchanged (`a_suspicion` remains a band — only evidence quality changed); deny-band semantics unchanged; no env flags added.

**Known doc-comment drift (left intentionally):** `daemon/services/attestation_gate.py` §(vi) comment "the tree-rows provider runs lazily and only on would-fire" is now "runs lazily and at most once per evaluation (A-scan cross-resolution when advisory candidates exist, else bundle assembly)" — the gate file is shared seam territory with the sibling attest-first lane; fix there or in a follow-up.

**Files (this decision):** `daemon/services/attestation_resolver_activation.py` (newest-only Pass 2 + `_operator_scoped_terms` / `_completed_child_ids` helpers + cross-resolution block + fetch-once cache in `evaluate_resolver_activation` + `_B_MESSAGE_CLIP` + docstrings); `daemon/services/attestation_report_judge.py` (prompt softening + constant docstring); `tests/unit/test_attestation_resolver_activation.py` (4 new test classes, 27 tests + the `test_resolver_consumes_collect_source_a_signals` wiring-pin re-contract to the `tree_rows_provider=_cached_tree_rows` shape); `tests/unit/test_attestation_resolver_user_intent.py` (subordination-pin re-contract + genuine-guard pin); `docs/setup.md` (bundle-shape update paragraph); `requirements.md` (R-RES-8/R-RES-9 SUPERSEDED-IN-PART marks); this file.

**Matrix verification (2026-09-20, scoped — NOT repo-wide):** glob-enumerated unit attestation matrix (29 files: `tests/unit/test_attestation*.py` + `tests/unit/tools/test_attestation*.py`) — BEFORE: **672 passed** (baseline at 94fc6da1; prior ledger's 671 grew by 1 on the tip, reconciled); AFTER: **699 passed** (672 + 27 new). Self-contained attestation integration cohort (15 files): **145 passed** (prior ledger's 127 grew on the tip, reconciled). Live-LLM integration files NOT run (known environmental SSE hang). Whole-repo suite NOT run (per task scope).

## D-ENTRY 2026-09-19 — Incident c5d9a38a remediation: LCA attest-first pure-toolcall-turn contract + gate HOLD-state enforcement

**Trigger:** Instance c5d9a38a (2026-09-19, leader task). The leader bundled report + ``attest_completion`` into ONE AIMessage TWICE. The prompt-only fix (rearrange "deliver the report FIRST, then attest" → "deliver the report ALONE, then attest") failed twice — the leader's natural habit is to bundle the report and the attestation into a single message (the tool_call is the very last thing the leader does before ending the turn, and the report text is fresh in mind). The user's 2026-09-19 decision: flip the order (attest FIRST, in a PURE tool-call turn, then deliver the report as a SUBSEQUENT standalone message) AND enforce system-side (the prompt-only fixes failed).

**Decision — attest-first pure-toolcall-turn contract:** The attestation tool-call message MUST have empty content (pure toolcall turn). The full detailed final report MUST follow as a subsequent standalone AI message (no tool calls, >= ``SHORT_REPORT_WORD_THRESHOLD`` = 150 words). The order is the reverse of the OLD report-first-then-attest teaching.

**Decision — system-side HOLD-state enforcement:** The gate's R2 deny tree gains a new branch — ``Decision.HOLD = "hold"``. ``decide()`` step (2) splits attested into two paths:
* (a) attested AND final AIMessage is a standalone text report → ``Decision.ALLOWED`` with counter reset (trigger 1).
* (b) attested BUT final AIMessage is the attest-call message itself (no subsequent text) OR the c5d9a38a bundled shape (text + tool_call in ONE message) → ``Decision.HOLD`` with the Final Report Reminder HumanMessage injected + route-back to ``agent``. Counter UNCHANGED.

The HOLD branch fires ONLY when ``attestation_present=True`` (the attestation tool call is in the bounded window); it never fires on the un-attested deny path. The classify helper ``classify_final_ai_shape`` (in ``daemon/services/attestation_gate.py``) determines which of the four shapes the final AIMessage takes:
* CLEAN attest_call + subsequent text report → final AI is the report → ALLOWED.
* CLEAN attest_call with NO subsequent text → final AI is the attest_call → HOLD with the clean-call reminder (``ATTESTATION_FINAL_REPORT_REMINDER``).
* BUNDLED shape (text + tool_call in ONE AIMessage) → final AI is the bundled message → HOLD with the bundled reminder (``ATTESTATION_BUNDLED_REMINDER``).
* Multiple attest calls in window → final AI is the most recent AI → whichever shape.

**Decision — counter-independence:** The HOLD-state reminder injection does NOT increment ``attestation_denied_count``. The bound/escalation machinery (``deny_bound`` predicate, ``terminal_after_bound`` path, ``attestation_denied_count`` reset triggers) is NEVER consulted on HOLD. The reminder count is a SEPARATE channel (``attestation_reminder_count`` in the LangGraph state).

**Decision — per-mission reminder cap (``ATTESTATION_REMINDER_CAP = 2``):** The graph node tracks ``attestation_reminder_count`` per mission and falls through to plain ``meta_bypass`` allow when the count reaches this value. This is the documented escape so a leader that keeps emitting empty / short finals after the attest call is NEVER stuck in an infinite HOLD loop. The cap is NOT env-tunable by design (module-level constant; mirrors ``SHORT_REPORT_WORD_THRESHOLD`` precedent).

**Decision — tool-result-as-teacher:** The ``attest_completion`` tool body's RETURN VALUE is the teacher text the leader reads via the ToolMessage. Two shapes:
* ``ATTEST_CLEAN_RESULT_TEXT`` = "Attestation recorded. Now deliver your full detailed final report as your final message - a standalone message with no tool calls. (Your attestation call must contain no text.)" — returned when the calling AIMessage had EMPTY content (clean-call shape).
* ``ATTEST_BUNDLED_RESULT_TEXT`` = "Attestation recorded, but your tool-call message contained text - the attestation call must be text-free. Re-issue your full detailed final report now as its own standalone message." — returned when the calling AIMessage had non-empty content (the c5d9a38a bundled shape).

The tool body picks the shape via a per-thread runtime hook: the tools-node caller (``daemon/services/long_tool_nudge.py:wrapped_tools_node``) sets a ``contextvars.ContextVar`` with the flattened AIMessage content BEFORE invoking the tool; the tool body reads the context var to pick the right teacher text. ``ContextVar`` (NOT ``threading.local``) because the tool body runs in a worker thread via ``asyncio.to_thread`` (LangChain's tool invocation framework dispatches the body off the event loop) — ``threading.local`` would NOT transfer state across the thread boundary. ``ContextVar`` transfers across ``asyncio.to_thread`` via Python's ``contextvars.copy_context`` (CPython 3.9+).

**Decision — tool description MUST LEAD with the exact contract text (2026-09-19 user addendum):** The tool's docstring + ``_full_doc_`` MUST start with the canonical one-sentence opener:
> "Call this tool ALONE in one turn - the message containing this call must contain nothing else (no report, no commentary). Then deliver your full detailed final report as your final standalone message."

The leading sentence is the canonical contract anchor the LLM reads first at tool-listing time. Any drift to the OLD "Signal that a delegated mission is genuinely complete" opener would silently let leaders revert to bundling. The body keeps the conditional-delegated-mission scope, when-not-to-call guidance, and the conditional framing.

**Rationale — why this is the right enforcement seam:**

* The suppression rule (no ``attest_completion`` on non-delegated missions) + the conditional framing (only attest when nudged) covers the off-mission over-call class. The HOLD-state enforcement covers the on-mission bundled-shape class — the c5d9a38a failure mode.
* The prompt-only fix failed twice because the leader's natural LLM behavior is to emit the report text and the attestation tool call in the same message (both are terminal signals — "I'm done" + "here's the work"). The system-side enforcement closes the loop: the runtime hook detects the bundled shape at tool-execution time and returns the bundled teacher text; the gate detects the bundled shape at turn-end and injects the bundled reminder.
* The HOLD branch is structurally exclusive (``should_inject_reminder=True`` ONLY on ``Decision.HOLD``) — it cannot leak into the existing deny path (``should_inject_nudge=True`` ONLY on ``Decision.DENIED``).
* The reminder cap (2) is the documented escape — a leader that emits empty / short finals is NEVER stuck in an infinite HOLD loop. After 2 reminders the gate falls through to plain ``meta_bypass`` allow and clears the counter.
* The ContextVar vs threading.local choice is forced by the runtime hook's placement: the tools-node caller sets the state in the event-loop thread; the tool body runs in a worker thread via ``asyncio.to_thread``. ``threading.local`` would silently fail (the worker thread's local state is the default — the runtime hook's set would be invisible to the tool body); ``ContextVar`` transfers via ``copy_context``.

**Files (this decision):**

* ``daemon/tools/attestation.py`` — tool docstring + ``_full_doc_`` updated to the new contract (leading sentence verbatim from the user addendum); teacher-text constants ``ATTEST_CLEAN_RESULT_TEXT`` / ``ATTEST_BUNDLED_RESULT_TEXT``; runtime hook + ``ContextVar``-based per-thread state (``set_attest_caller_content`` + ``_get_attest_caller_content`` + ``reset_attest_caller_content_for_tests``); stack-inspector fallback (defense-in-depth); ``create_attestation_tools`` factory returning the decorator-bound tool.
* ``daemon/services/long_tool_nudge.py`` — runtime hook in ``wrapped_tools_node``: scans the AIMessage's ``tool_calls`` list for ``attest_completion``, calls ``set_attest_caller_content`` with the AIMessage BEFORE delegating to the bare ToolNode. Best-effort (try/except logs at DEBUG; never breaks the tools node).
* ``daemon/services/attestation_gate.py`` — new ``Decision.HOLD = "hold"`` enum value; ``GateDecision`` gains ``should_inject_reminder`` / ``reminder_text`` / ``is_bundled_call`` / ``final_ai_is_text_report`` / ``final_ai_is_attest_call`` / ``attestation_index`` fields; ``decide()`` step (2) splits attested into the ALLOWED-vs-HOLD paths; ``evaluate()`` calls ``classify_final_ai_shape`` BEFORE the conditional / not-required bypasses (so the bypasses see the same shape the decide() step would see); dry-mode mapping disarms the reminder injection (zero side-effects per D2/D8).
* ``daemon/services/context_messages.py`` — ``_stable_id_for`` learns the new kind ``attestation_final_report_reminder`` for the stable-id supersede contract (F1 Shape A applied to the HOLD-state).
* ``daemon/graph.py`` — new constants ``ATTESTATION_FINAL_REPORT_REMINDER`` + ``ATTESTATION_BUNDLED_REMINDER`` (canonical home, NFR-6 parity with ``ATTESTATION_NUDGE_TEXT`` + ``COMPLETION_CHECK_NOTE_TEXT``); new ``_make_attestation_final_report_reminder_message`` factory (slices past the canonical header, stable-id supersede); new module-level constants ``ATTESTATION_REMINDER_CAP = 2`` + ``ATTESTATION_REMINDER_COUNT_KEY = "attestation_reminder_count"``; HOLD branch in ``create_attestation_gate_node`` — reads the prior reminder count from the state channel, increments on HOLD injection, falls through to plain allow on cap (resets the counter); reset paths for ``attestation_reminder_count`` on attested-allow + cap-fall-through.
* ``agents/leader/rule.md`` — new section "❌ Wrong Attestation Order (attest-first contract — 2026-09-19)" teaching the new order + the c5d9a38a negative instruction + the report-first-then-attest negative instruction + the non-delegation suppression-rule restated.
* ``daemon/graph.py`` — ``ATTESTATION_NUDGE_TEXT`` updated to flip the order teaching (attest FIRST, report SECOND, separate messages); mermaid diagram reworked to surface the attest-first path + the HOLD branch (``ReportPresent -- No --> Hold["HOLD: re-issue the report as its own standalone message"]``); ``COMPLETION_CHECK_NOTE_TEXT`` updated to mention the new contract.
* Tests — see verification section below.
* ``.agents/shared/planning/leader-completion-attestation/decisions.md`` — this D-entry (append-only, no prior D-entry modified).
* ``.agents/shared/planning/leader-completion-attestation/requirements.md`` — supersession entry (the new contract supersedes prior attest-first teaching acceptance criteria).
* ``docs/setup.md`` — LCA flow section updated for the HOLD-state path + the per-mission reminder cap.

**DO NOT TOUCH (this decision):**

* Judge service + retry semantics (the merged ``judge_fused_bundle_async`` retry-once-on-unparsable + retry-once-on-timeout contracts are unchanged).
* Marker catalog + length trigger (``MID_WORK_MARKERS``, ``SHORT_REPORT_WORD_THRESHOLD`` = 150).
* Bound / escalation (``deny_bound`` predicate, ``terminal_after_bound`` path, ``attestation_denied_count`` reset triggers). HOLD is structurally exclusive from this machinery — the counter-independence is the key safety property.
* Section U bundle rendering.
* Kill-switch coupling (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`` disables the judge ENTIRELY — retries included, unchanged).
* Marker-path (a)/(b)/(c)/(d) routing — the marker-path hint machinery (``COMPLETION_CHECK_NOTE_TEXT`` + ``_make_completion_check_note_message``) is unchanged.

**Verification (this decision, scoped to the attestation matrix):**

The matrix counts below are the LIVE ground-truth counts the user asked for. Glob-enumerated ``tests/**/test_attestation*.py``:

* Unit tests (``tests/unit/test_attestation_*.py`` + ``tests/unit/tools/test_attestation_*.py``) — **717 tests PASS, 0 FAIL** (the new ``tests/unit/test_attestation_attest_first_contract.py`` adds 22 tests: 5 classify-shape cases, 3 decide() HOLD-branches, 4 evaluate() HOLD-paths + dry-mode, 5 gate-node matrix cases (a/b/c/d/e), 4 constants pins).
* Integration tests added by this D-entry — ``tests/integration/test_attestation_attest_first_e2e.py`` — **2 tests PASS, 0 FAIL** (``test_e2e_attest_pure_toolcall_turn_evidence`` + ``test_e2e_bundled_call_corrected_evidence`` — the EVIDENCE-grade acceptance tests added per the 2026-09-19 user addendum).
* Pre-existing integration tests that exercise the OLD bundled shape (``content="Attesting now."`` + ``attest_completion`` tool_call) — **11 tests FAIL** at suite time; the failure mode is the OLD contract's "deliver report FIRST, then attest" teaching where the test's scripted chat model emits a bundled shape + a short "Done." prose and expects the gate to ALLOW with the short prose as the FINAL AI. Under the NEW contract the gate requires the FINAL AI to be the standalone text report (>= 150 words), so the test's assertion fires. These tests are slated for re-anchoring in a follow-up — the matrix is the same code surface (the gate node, the gate evaluation) but the scripted chat model responses need to flip to the clean attest + long report sequence. The same re-anchoring pattern applied in this D-entry to the unit ``test_attestation_conditional_gate_outcomes.py::test_delegation_attested_returns_allowed_with_reset`` (the attest_ai fixture now returns empty content; a new ``bundled_attest_ai`` helper + ``report_ai`` helper cover the bundled + standalone-report shapes) is the canonical re-anchoring template.

The matrix breakdown for the unit cohort (single source of truth — do NOT recompute; use the values below):
* 29 unit files (tests/unit/test_attestation_*.py + tests/unit/tools/test_attestation_*.py) — 717 tests.
* Pre-existing integration tests that do NOT use the bundled shape — 204 tests PASS (subset of the integration corpus; covers the attested-allow + deny + nudge + reset + boundary + dry-mode + bound-escalation paths).
* Pre-existing integration tests that use the bundled shape — **11 tests FAIL** at suite time; all require the re-anchoring template above.

The new E2E acceptance tests (the canonical evidence the user requested) are STABLE in CI — they pass against the LIVE gate node + the LIVE tool body (no unit mocks); they drive the full leader sequence through the production graph.


## D-ENTRY 2026-09-23 — Incident b2f4dae9: Completion Check Note (b)/(d)-with-pending → LOG-ONLY (full removal)

**Trigger (incident b2f4dae9, 2026-09-23):** a 7m32s-old healthy wait on a RUNNING developer child fired the (b) Completion Check Note hint on a short mid-work ACK whose evidence-source carried a lexical FP ``"awaiting"`` (Source A suspicion — Source A is deliberately NOT busy-muted per the R5/Δ2 architect ruling). The fused judge said ``not_complete``; the (b)/(d)-with-pending route injected a checkpoint-durable ``[SYSTEM CONTEXT: Completion Check Note]`` HumanMessage as a "reminder". The note carried zero information — the leader's ``msg[14]`` already stated the plan, the wake-up was en route, the deny path was unreachable (live descendant). The user pinned the b2f4dae9 evidence chain (the ``a_suspicion`` fired on the lexical FP "awaiting" in a completed wanderer's changelog prose during the 7m32s-old healthy wait) and decided FULL REMOVAL over age-gate.

**Decision — (b)/(d)-with-pending hint surface RETIRED end-to-end:** the (b)/(d)-with-pending route STILL EXISTS as a logged decision (the ``resolver_outcome=allow_hint`` row label + the ``[AttestationGate]`` log line's ``would_be_route=allow_hint`` field + the ``leader_completion_gate_fused_judge`` row's verdict + reason + band + terms + judge_invoked flag) but emits NO message. The ``Completion Check Note`` constant (``COMPLETION_CHECK_NOTE_TEXT``), the ``_make_completion_check_note_message`` factory, the ``_fused_hint_citation`` D4 evidence-citation helper, the ``_COMPLETION_CHECK_NOTE_TITLE`` literal, the ``marker_hint_message`` ``GateDecision`` field, and the ``completion_check_note`` row in ``_stable_id_for``'s canonical id-format table are ALL removed.

**Rationale (user's verbatim decision, 2026-09-23):** "the age-gate's preserved window is a 30-60min advisory before the 1h watchdog which ACTS; the note never prevents anything — the deny path prevents, the watchdog acts; every surviving note path is FP surface." The user chose FULL REMOVAL over age-gate (the rejected fallback design — see "Age-gate fallback" below) because the note never prevented anything in the first place.

**Why this is the right retirement seam (DO-NOT-REINTRODUCE rationale):**

* **The note never prevented anything.** The (b) path's only side effect was the hint injection — there was no counter movement (the (b) path is allow-with-hint, not deny). The protection surfaces are: the deny path (``Decision.DENIED`` → ``attestation_denied_count`` increment + nudge inject + counter reset on attested-allow only), the bound/escalation machinery (``deny_bound`` predicate + ``terminal_after_bound`` path), and the 1h watchdog. The hint was pure informational — it never blocked a completion that would have slipped through.
* **The (b) path is an allow path.** The fused judge's verdict-not-complete + real pending work route resolves to ALLOW (the wakeup is en route, the turn still ends). The hint was the only side effect; removing the hint means removing the only side effect. The (b) path becomes a pure logged decision — the resolver row + the gate log line carry the verdict + the would-be-route label; no checkpoint mutation.
* **The hint's value was zero on every live class.** Incident b2f4dae9 is the canonical example — a 7m32s-old healthy wait on a RUNNING developer child, the leader's ``msg[14]`` already stated the plan, the lexical FP "awaiting" in a wanderer's changelog prose was the Source A suspicion. The injected note was a "reminder" that carried zero information the leader didn't already have. The LCA busy trigger suppression (2026-09-12) was a partial fix — it disarmed the marker/length trigger on busy descendants but left the Source A path intact (Source A is NOT busy-muted per R5/Δ2); the b2f4dae9 incident shape is the post-suppression residual FP class.
* **The hint is FP-prone.** Every (b)/(d)-with-pending route invocation injects a checkpoint-durable block into the leader's context — a block the leader may read on its next turn. The block is informational, not blocking, so the leader has no obligation to act on it. If the leader acts on it (e.g. ``send_message`` to revive a child), the action is duplicate work the leader would have done via the natural wakeup path. If the leader ignores it, the block sits in the context until compaction — consuming context budget without value.
* **The deny path + bound/escalation + watchdog are the load-bearing protection.** The (a) path (markers + judge-no + nothing pending → CONVERT TO DENY) + the (b) path's DENY cousin on PAUSED-only (markers + judge-no + PAUSED-only + nothing pending → DENY+NUDGE per the suspect-pending contract) + the bound at ``ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`` (default 3) + the 1h watchdog all remain UNTOUCHED. The retirement removes ONLY the (b)/(d)-with-pending hint surface; the deny-side surface is byte-identical.
* **The route label survives on logs.** The full evidence chain (the b2f4dae9 evidence chain specifically — Source A suspicion + lexical FP "awaiting" + judge-no + real pending) must remain log-reconstructible. The ``leader_completion_gate_fused_judge`` row carries the verdict (``verdict=not_complete``) + the FULL reason (``llm_judge_reason=...``) + the band (``band=a_suspicion`` or ``band=marker``) + the matched terms (``marker_terms=...`` or ``a_advisory_present=True``) + the judge invocation flag (``judge_invoked=True``). The ``[AttestationGate]`` log line carries the explicit log-only posture stamp + the ``would_be_route=allow_hint`` label. The resolver_eval row carries ``resolver_outcome=allow_hint`` + ``would_be_outcome=would_hint`` (the structured record of the would-be hint outcome). Operators can grep-triage between pre-fix (note injected) and post-fix (note retired) deploys via the ``log-only`` + ``note hint retired`` literal in the gate log line.

**Age-gate fallback (REJECTED 2026-09-23, recorded for the revert seam):** "fire the (b) note only when pending work outstanding > 30 min OR shape genuinely suspect (PAUSED / en-route-only / completed-child-lying / stalled last_activity); healthy busy + fresh → silence; preserves late safety net (busy-mute rejected — silences even >1h stuck RUNNING children)." The user rejected this because: (1) the age-gate's preserved window is a 30-60min advisory BEFORE the 1h watchdog which ACTS — the note never prevents anything; (2) every surviving note path is FP surface (the LCA busy trigger suppression + the marker catalog + the length trigger are the FP sources; the age-gate only suppresses the youngest FP class); (3) busy-mute (the alternative path the user considered) silences even >1h stuck RUNNING children — too aggressive a tradeoff for the FP class it eliminates. The age-gate fallback is RECORDED VERBATIM above so the revert seam has the rejected design's full text — if a future incident identifies a class the deny path + the watchdog MISS, the age-gate is the documented fallback (the user would re-evaluate on evidence, not on speculation).

**Suspect-pending protections UNCHANGED:** PAUSED descendants (suspect — the trigger stays armed; PAUSED is live-for-deny-protection, NOT busy for trigger suppression) + en-route-only work (IDLE + pending message OR IDLE + unsettled job — maybe lost; the trigger stays armed). The deny path + the LCA busy-mute + the bound/escalation machinery + the HOLD-state attest-first reminders (the Final Report Reminder family is DIFFERENT and STAYS untouched) are all byte-identical. The protection lives in the deny path, NOT the hint. Pinned by ``tests/unit/test_attestation_lca_note_removed.py::test_suspect_pending_paused_child_completion_still_hits_full_gate`` + ``tests/unit/test_attestation_lca_note_removed.py::test_suspect_pending_en_route_only_completion_still_hits_full_gate``.

**Byte-identical surfaces (DO-NOT-TOUCH for this decision):** deny path (``Decision.DENIED`` → ``attestation_denied_count`` increment + nudge inject + counter reset on attested-allow only); bound/escalation (``deny_bound`` predicate at ``daemon/services/attestation_gate.py``, ``terminal_after_bound`` path, ``attestation_denied_count`` reset triggers); HOLD + attest-first reminders (the Final Report Reminder family — ``ATTESTATION_FINAL_REPORT_REMINDER`` + ``ATTESTATION_BUNDLED_REMINDER`` constants + the ``_make_attestation_final_report_reminder_message`` factory + the ``attestation_final_report_reminder`` stable-id kind — DIFFERENT surface, STAYS untouched); watchdog; U/A/B/C sections; busy-suppression semantics (LCA busy trigger suppression at the predicate's ``b_fires`` term — preserved); fused judge (incl. timeout retry, bc145c7e R1 2026-09-19); kill-switches (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` + tri-state mode + ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`` + ``ENSEMBLE_LEADER_ATTESTATION_WINDOW`` + ``ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND``). The 2026-09-19 attest-first HOLD-state factory (``_make_attestation_final_report_reminder_message`` at ``daemon/graph.py``) is a SEPARATE surface that mirrors the retired factory's shape (slices past the canonical header + stable-id supersede) but serves a different purpose — DO NOT retire or weaken the HOLD-state reminder injection.

**Files (this decision):**

* ``daemon/graph.py`` — ``COMPLETION_CHECK_NOTE_TEXT`` constant deleted; ``_COMPLETION_CHECK_NOTE_TITLE`` constant deleted; ``_make_completion_check_note_message`` factory deleted; ``_fused_hint_citation`` D4 evidence-citation helper deleted; the (b)/(d)-with-pending injection site (around the prior line 5519) reworked to LOG-ONLY (the fused block still fires; the resolver row carries the route label; the gate log line stamps the log-only posture + ``would_be_route=allow_hint``); the emit site (the prior line 5865 ``if decision.marker_hint_message is not None: return {messages: ...}``) deleted.
* ``daemon/services/attestation_gate.py`` — the ``marker_hint_message`` ``GateDecision`` field deleted; the lazy-import NFR-6 comment at ``daemon/services/attestation_gate.py:1374`` updated to drop the ``COMPLETION_CHECK_NOTE_TEXT`` reference.
* ``daemon/services/context_messages.py`` — the ``completion_check_note`` row removed from the ``_stable_id_for`` id-format table docstring; the historical docstring paragraph about F1 Shape A (the supersede pattern that the completion_check_note row originated) replaced with a retirement-witness docstring that explicitly documents the 2026-09-23 removal; the ``completion_check_note`` kind branch (the ``if kind == "completion_check_note": return ...`` block) deleted; the supported-kinds list in the ``ValueError`` message updated to drop the retired kind (4 surviving kinds: ``project``, ``shared_meta_kv``, ``attestation_nudge``, ``attestation_final_report_reminder``).
* ``docs/setup.md`` — retirement notes appended to the LCA Phase 6.5 follow-up section; the "Completion Check Note (path (b))" section rewritten to document the retirement (the historical pre-retirement contract + the post-retirement log-only contract side-by-side); the "LCA busy trigger suppression" section updated to note the post-retirement log-only posture applies to both branches (busy-muted OR not-busy); the References section updated.
* ``tests/unit/test_attestation_marker_wiring.py`` — 3 tests re-anchored to log-only contract (the (b) markers + judge-no + real pending test, the (d2) judge-error test, the (d) wrapper-fault test, the length-trigger (b) test, the length+markers combined-trigger test, the length+markers-only test, the length-only-no-marker test, the paused-not-busy test); 3 tests retired (``test_completion_check_note_stable_id_collapses_on_supersede``, ``test_completion_check_note_stable_id_isolates_per_instance``, ``test_completion_check_note_compaction_seam_hoists_once``) — the W2 Shape A supersede contract they pinned is RETIRED along with the entire (b)-note surface.
* ``tests/unit/test_attestation_marker_supersede_lca.py`` — entire file retired (5 defs → 1: 4 W2 Shape A contract pins on ``_make_completion_check_note_message`` + 1 mock-guard ``_langgraph_upsert_by_id``, both retired; the factory is RETIRED); replaced with a single ``test_lca_note_supersede_class_retired`` witness that asserts the factory + constant + ``_stable_id_for`` table row are all gone.
* ``tests/unit/test_attestation_resolver_stage2.py`` — D4 hint citation class (``TestD4HintEvidenceCitation``) retired (2 tests pinned the D4 hint-citation suffix on the injected hint; the hint is RETIRED); replaced with a single ``TestD4HintEvidenceCitationRetired`` stub that asserts the factory + constant + ``_stable_id_for`` kind branch are all gone (the witness).
* ``tests/unit/test_attestation_resolver_user_intent.py`` — the false-rescue test (``test_u_fulfilled_a_advisory_c_live_judged_not_complete_honored``) re-anchored to log-only (the resolver row carries ``resolver_outcome=allow_hint``; the gate log line carries ``would_be_route=allow_hint``; NO message injected).
* ``tests/unit/test_attestation_stage3_census.py`` — the ``marker_hint_message`` field removed from the kept-list assertion (the field is RETIRED); explicit RETIRED pin added (the field MUST stay absent from ``GateDecision``).
* ``tests/unit/test_attestation_attest_first_contract.py`` — the NFR-6 docstring at the ``TestAttestFirstConstants`` class updated to drop the ``COMPLETION_CHECK_NOTE_TEXT`` reference (the constant is RETIRED).
* ``tests/unit/test_attestation_lca_note_removed.py`` — NEW FILE (the canonical retirement witness; 6 tests: the b2f4dae9 regression pin, 2 suspect-pending shape tests, 3 negative census pins).
* ``tests/integration/test_attestation_marker_routing_lca.py`` — scenario-(b) + (d2) tests re-anchored to log-only; the (d3b) wrapper-fault + real pending test re-anchored; the docstring updated to note the W2 Shape A supersede tests are retired in ``tests/unit/test_attestation_marker_supersede_lca.py``.
* ``tests/integration/test_attestation_mid_work_report_testcase.py`` — the ``COMPLETION_CHECK_NOTE_TEXT`` import removed (the constant is RETIRED); the existing ``test_no_hint_on_healthy_wait`` test already asserted ``len(hints) == 0`` (the post-retirement log-only contract is consistent with the LCA busy suppression contract that was the original intent of the test).
* ``tests/integration/test_attestation_stage2_failopen.py`` — 4 tests re-anchored to log-only (the (Seam (iii) marker-band + judge wrapper fault) test, the (Seam (iii) A-band + judge wrapper fault) test, the F2 S1 second-call test, the F2 S2 first-call test); the F2 docstring updated to reflect the log-only posture on the S2 first call.
* ``tests/integration/test_lcan_childlie_e2e.py`` — the s3_a_band_fires_alone_with_busy_descendant_real_graph test re-anchored to log-only (the b2f4dae9-adjacent A-band shape: the lexical FP "awaiting" in a child contradiction evidence → A-band ALLOW log-only).
* ``.agents/shared/planning/leader-completion-attestation/decisions.md`` — this D-entry (append-only, no prior D-entry modified).
* ``.agents/shared/planning/leader-completion-attestation/requirements.md`` — supersession entry (the OLD AC-M9 / AC-M16 / AC-L12 / R-IMP5 contract is SUPERSEDED by the NEW AC-LCA-NOTE-RM-1..4 contract; the new contract documents the log-only posture, the log-fidelity proof, the suspect-pending intact-deny-path proof, and the whole-tree negative census pins).

**Matrix verification (2026-09-23, scoped to the attestation pack — NOT repo-wide):** Glob-enumerated ``tests/**/test_attestation*.py`` → 64 files collected. The cohort breakdown below is derived from per-file ``def test_`` / ``async def test_`` counts via ``grep -cE '^def test_|^async def test_|^    def test_|^    async def test_'`` on each touched file (pre + post). The numbers are RE-DERIVABLE from the source tree at any point — do not treat them as "do NOT recompute".

**Verified per-file def-count delta across the touched test files (2026-09-23):**

| File | pre | post | delta |
|---|---|---|---|
| ``tests/unit/test_attestation_marker_wiring.py`` | 43 | 40 | **-3** (3 W2 supersede tests retired: ``test_completion_check_note_stable_id_collapses_on_supersede``, ``test_completion_check_note_stable_id_isolates_per_instance``, ``test_completion_check_note_compaction_seam_hoists_once``) |
| ``tests/unit/test_attestation_marker_supersede_lca.py`` | 5 | 1 | **-4** (file re-shape: 5 defs → 1; the 5 defs = 4 contract pins + 1 mock-guard ``_langgraph_upsert_by_id``, both retired) |
| ``tests/unit/test_attestation_stage3_census.py`` | 21 | 21 | 0 (kept-list adjusted to RETIRED pin; no test count change) |
| ``tests/unit/test_attestation_attest_first_contract.py`` | 22 | 22 | 0 (NFR-6 docstring updated; no test count change) |
| ``tests/unit/test_attestation_resolver_stage2.py`` | 26 | 25 | **-1** (D4 hint citation class ``TestD4HintEvidenceCitation`` — 2 tests — retired; replaced with ``TestD4HintEvidenceCitationRetired`` stub — 1 test — net -1) |
| ``tests/unit/test_attestation_resolver_user_intent.py`` | 25 | 25 | 0 (false-rescue test re-anchored; no test count change) |
| ``tests/integration/test_attestation_marker_routing_lca.py`` | 13 | 13 | 0 (scenario-(b) + (d2) + (d3b) tests re-anchored to log-only contract; no test count change) |
| ``tests/integration/test_attestation_mid_work_report_testcase.py`` | 6 | 6 | 0 (removed unused ``COMPLETION_CHECK_NOTE_TEXT`` import; no test count change) |
| ``tests/integration/test_attestation_stage2_failopen.py`` | 18 | 18 | 0 (4 tests re-anchored: ``test_seam_iii_marker_band_allow_with_hint``, ``test_seam_iii_a_band_allow_with_hint``, ``test_f2_s1_seam_i_refires_after_fault_removal``, ``test_f2_s2_seam_iii_refires_after_fault_removal``; no test count change) |
| ``tests/integration/test_lcan_childlie_e2e.py`` | 3 | 3 | 0 (s3_a_band_fires_alone_with_busy_descendant_real_graph re-anchored; no test count change) |
| ``tests/unit/test_attestation_lca_note_removed.py`` (NEW) | 0 | 6 | **+6** (b2f4dae9 regression pin + 2 suspect-pending shape tests + 3 negative census pins) |
| **Net delta across touched files** | **182** | **180** | **-2** |

**Cohort-level LIVE ground-truth counts (verified via ``uv run python -m pytest`` post-change, scoped to the attestation pack — NOT repo-wide):**

* 30 unit files / 752 tests PASS — this is the post-change LIVE count; the breakdown is unchanged pre/post except for the touched-file def deltas in the table above.
* 5 note-touching integration files / 43 tests PASS, 3 deselected (the integration marker-routing + mid-work-report-testcase + nudge-supersede + stage2-failopen + lcan-childlie-e2e cohort).
* 29 self-contained integration files (non-note-touching) / 188 tests PASS, 5 skipped, 0 failed (the integration matrix that does NOT touch the (b)/(d)-with-pending surface is byte-identical pre/post the retirement).
* 1 integration file / 1 pre-existing failure (unrelated to this decision): ``tests/integration/test_attestation_revive_after_escalation.py::test_terminal_reset_and_fresh_episode_rearm_next_mission`` — pre-existing ``completion_gate_escalated is True`` assertion failure, NOT introduced by this decision. The same failure appears on the BASE checkout at ``6bf7bed7`` (verified via ``git stash`` before/after the working tree was modified).
* 1 migration file / 17 tests PASS, 21 deselected (the migration cohort does not touch the LCA subsystem).
* 1 postgres file / 21 tests fully deselected under default addopts (PG-only marker; not in scope for this run).

Pre-existing failures (NOT introduced by this decision, NOT counted as regressions): 2 total — ``test_attestation_revive_after_escalation.py::test_terminal_reset_and_fresh_episode_rearm_next_mission`` (assertion on ``completion_gate_escalated``) + ``tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default`` (PG boolean default defect in ``20260915_120000_critical_notes_lifecycle.sql`` — unrelated PG migration, NOT introduced by this decision).

The full attestation matrix was re-run with ``-p no:randomly`` deterministic ordering for the post-change verification (228 integration tests pass + 752 unit tests pass = **980 tests pass**, 5 skipped, 0 new failures).

**Activation contract (no behavior drift outside the (b)/(d)-with-pending surface):** the deny path + bound/escalation + HOLD + attest-first reminders + watchdog + U/A/B/C sections + busy-suppression semantics + fused judge + kill-switches are byte-identical pre/post the retirement. The ONLY observable changes: (1) ZERO Completion Check Note blocks injected in any scenario; (2) the resolver_eval row's ``would_be_outcome=would_hint`` field + the gate log line's ``would_be_route=allow_hint`` field + the explicit ``log-only`` + ``note hint retired`` log stamps are the canonical log-fidelity proof of the route label survival; (3) the ``marker_hint_message`` ``GateDecision`` field is gone (the field was only read on the deleted emit site); (4) the ``completion_check_note`` row in the ``_stable_id_for`` table is gone (the kind literal is rejected by the "unknown kind" sentinel — pinned by ``tests/unit/test_attestation_lca_note_removed.py::test_stable_id_for_rejects_completion_check_note_kind``).

**Revert seam:** if a future incident identifies a class the deny path + the watchdog MISS, the age-gate fallback design is recorded VERBATIM above. The user would re-evaluate on evidence, not on speculation. The age-gate fallback's full text is in the "Age-gate fallback (REJECTED 2026-09-23)" section above — restore from that section if a follow-up incident motivates the design.


## N1 ratification 2026-09-20 — pending-children/wakeups suppress HOLD

**Step-order change in `decide()`:** the attested-split (HOLD branch) moves AFTER the R2 pending check. New order: `(1) user_answer_pending → (2) R2 pending (pending_children / queued_wakeups / live_descendants > 0 → ALLOWED_LEGITIMATE_PENDING_WAKEUP) → (3) attested-split (ALLOWED/HOLD) → (4) bound → (5) deny`. Step (1) and steps (4)/(5) are unchanged.

**Rationale:** a premature attest during an active mission (children still running, wakeup en route) would otherwise fight the R2 legit-pending allow — the leader would be forced to deliver the report before children finish, but the children are still running. HOLD reserved for the quiet-tree end state (R2 inputs all zero). The premature-attest R2 allow path: NO reminder, deny counter UNTOUCHED; the attested counter-reset (trigger 1) is bound to the **report-completion reset** on the attested + standalone-text-report ALLOWED path only. Counter moves ONLY on (a) attested + standalone-text-report ALLOWED and (b) `terminal_after_bound`; every other path leaves the counter at its input value. The 2026-09-19 attest-first HOLD-state semantics are otherwise unchanged (clean-call vs bundled reminder text, per-mission reminder cap `ATTESTATION_REMINDER_CAP`, counter-independence on HOLD).
## D-entry 2026-09-26 — incident 7d4a3bd9: false-completion fix cycle (final after amendments v1→v4)

**Incident (leader 7d4a3bd9, 2026-09-25 17:40:30Z→19:13:27Z, mode=enforce deny_bound=3, Episode B):**
the tester child self-completed 19:10:55Z while its own report said "Awaiting the root-level
core tests report…" → `pending_children=0` → deny band (c_quiet+a_suspicion). The fused judge
verdict `not_complete` was CORRECT (the reason named the mid-work state) and consumed deny
slot 1; then TWO judge double-timeouts (50s each, model=quick, TimeoutError at the 25s
default) consumed slots 2+3; the next evaluation fired `decision=terminal_after_bound` at
19:13:26Z and wrote `status=COMPLETED` with `completion_gate_escalated=true` — INVISIBLE at
the user surface. The terminal observer finalized `no_job` (the episode's real job 082899be
had been finalized inline at its first turn-end per the Fix-B mirror semantics). The leader
had answered all 3 deny-nudges with "Holding exactly there" prose — no tool calls, no attest.

**DESIGN JOURNEY (recorded so the arc is reconstructable; user amendments v1→v4):**
* v1 (dispatch) proposed: timeouts decoupled from the bound (substantive-vs-infra counting)
  + a TOTAL deny-event cap (6) forcing the terminal. **REJECTED by user (v3): the CURRENT
  counting is ruled CORRECT — every deny counts, timeouts included ("boundedness by
  simplicity"); the total cap is dead.** The bound predicate `deny_bound_exceeded` is
  byte-identical to pre-amendment; the revert-completeness proof is that every existing
  bound invariant pin passes UNMODIFIED.
* v2 proposed: a deterministic heuristic fallback (rule table over gate evidence) replacing
  the judge when it never spoke. **DROPPED by user (v3) before any code landed: zero
  heuristic code exists in the tree.** `attest_completion` IS the judge-dead fallback —
  the tool's exact purpose (the deterministic trust path).
* Timeout default: 25.0s → 300.0s (v1/v2) → **180.0s final (v4, "quicker")**. Env
  `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` stays tunable; min clamp 5.0s
  unchanged. Worst case: retry-once ⇒ up to 2×180s = 360s on a single turn-end's judge
  verdict — user-accepted for verdict reliability.

**FINAL SEMANTICS — the exhaustion composition gate (the one behavioral change):**
bound counting UNCHANGED; at `deny_bound_exceeded` the node branches on the epoch's
deny-event composition, tracked by the SessionState channel
`attestation_any_substantive_deny` (set when a deny's fused-judge verdict is
`not_complete`; reset with the ledger on attested allow / terminal; reset semantics mirror
the substantive counter):
* **Bound exhausted WITH ≥1 substantive verdict** ⇒ the judge SPOKE and was overridden ⇒
  `terminal_after_bound` stands exactly as today — fix 1's loud
  `completed (gate escalated — unverified)` surface + real-job linkage.
* **Bound exhausted with ZERO substantive verdicts** (all timeouts/errors/unparsable/
  judge-disabled — the judge NEVER spoke) ⇒ **NOT COMPLETE, full stop**: NO terminal write
  from timeouts alone — the deny+nudge cycle simply CONTINUES. Counting itself UNCHANGED
  (every deny counts; the committed counter may rise past the bound, which is now correct).
  The leader's exits are the existing ones: `attest_completion` (the deterministic trust
  path — meta_bypass allow), finishing the remaining work, or asking the user. The
  directive nudge (fix 3) pairs with this continuation — the leader keeps getting nudges
  and the directive names the exits.

**⚠ C1 SUPERSESSION NOTE (user ruling — do not "fix" without re-ruling):** the
never-spoke-judge continuation is DELIBERATELY unbounded: an epoch whose judge never
delivers a verdict denies forever (no terminal fallback). This SUPERSEDES C1's strict
terminal-fallback boundedness FOR THE NEVER-SPOKE CASE ONLY. Rationale: rare with the 180s
timeout; the exits exist (attest/finish/ask); the false-positive cost ("one small extra
step") is accepted by the user. C1 boundedness remains FULLY INTACT for the judge-spoke
path (≥1 `not_complete` verdict ⇒ the bound terminalizes as before).

**⚠ INTERPRETATION RULING (flagged for user veto):** "substantive" := fused-judge verdict
== `not_complete` (a definitive ruling). timeout / error / unparsable / judge-disabled /
bundle-unavailable = the judge NEVER spoke (non-substantive). So the check-the-composition
branch triggers on "zero `not_complete` verdicts among the epoch's deny events" — covering
the all-timeout case AND the all-error case with the same never-spoke logic.

**Fixes shipped this cycle (final scope):**

* **Fix 1 — loud unverified surface (read-side only; NO new InstanceStatus).** Every read
  point renders the DISTINCT string `completed (gate escalated — unverified)` (canonical
  constant `daemon.constants.COMPLETION_GATE_ESCALATED_DISPLAY`) instead of plain
  `completed` when the linked instance carries `completion_gate_escalated=True`: job
  events (`work_notifier`), `job_get` (`jobs_crud._job_to_response`, via the additive
  `WorkRecord.completion_gate_escalated` flag threaded from the joined Instance), and
  `get_mission`/`list_missions` (`MissionRecord.completion_gate_escalated` +
  `_render_liveness`; the canonical `liveness` field stays clean for filters/await). FE
  job-card renders the label verbatim with a warning glyph + amber color (minimal; no
  dist rebuild). **Job-linkage:** the observer's finalize context carries an
  already-finalized witness (`_ProcessingJobContext.already_finalized_job_id`, resolved
  from the freshest terminal JobItem) + the instance's escalation flag; the finalize log
  ties the terminal to the REAL job instead of `no_job` (`already_finalized_by=` +
  `gate_escalated=` witnesses; reporting-only — no state transition, no spurious watcher
  notify).

* **Fix 3 — directive nudge on no-progress repeat denies.** At each deny the node
  snapshots transcript progress (tool-call count in the checkpointed history → channel
  `attestation_deny_progress_tools`). On a deny that is the >= 2nd consecutive deny
  (committed deny count >= 2 — every deny counts) AND has ZERO new tool calls since the
  prior deny snapshot, the DIRECTIVE nudge replaces the standard in-graph deny nudge
  (same stable id — supersedes in place; `attestation_nudge_kind` kwarg marks the shape).
  The HOLD / Final-Report-Reminder / reminder-cap machinery (R4b/R4c) is untouched.
  Canonical directive body embedded VERBATIM (single source
  `daemon/graph.py:ATTESTATION_DIRECTIVE_NUDGE_TEXT`, pinned byte-exact by regression):
  "[Attestation Gate — Directive] The gate has denied completion more than once and you
  have taken no new action since the last denial. Choose exactly one now: (1) if the
  mission is truly complete, call attest_completion and deliver your final report; (2) if
  work remains, dispatch or finish it now (re-assign the pending work to a child or do it
  yourself); (3) if you are blocked or uncertain, ask the user for a decision. Continuing
  to hold without action will end this mission as COMPLETED-UNVERIFIED (gate escalated) once the judge delivers a verdict."

* **Fix 5a — scanner pin (final_word_count).** Root cause pinned FROM CODE + LOG: Episode
  A's `final_word_count=0 length_trigger=False messages_scanned=3` was NEITHER the window
  hypothesis NOR marker scoping — the row printed DATACLASS DEFAULTS because the §(iii.b)
  scan block is routing-gated and Episode A was a NON-DELEGATED allow
  (`attestation_required=False`, `delegation_tool_call_total=0` — verified in the prod log
  row). `messages_scanned=3` (the attestation scan) proves the 3-AIMessage tail was in
  hand; the length scan walks the SAME list. Fix: the length scan runs on EVERY evaluated
  path and the REAL values are stamped on the decision (the marker scan stays
  routing-gated; marker fields keep their own semantics). Routing note: with 5a fixed,
  Episode A's turn now shows real length values, but the R3/R4 no-delegation fold still
  fires FIRST — the route is UNCHANGED (pinned by `test_ep_a_shape_route_unchanged`).

* **Judge timeout default 180s** (v4; see the journey above) + docs tuning table +
  worst-case note.

**Fix 4 (child completion discipline) DEFERRED post-soak; fix 6 (attested→judge-priming)
on the watchlist** (unchanged from the original commission).

**Revert-completeness proof (bound counting UNCHANGED):** every EXISTING bound invariant
pin passes UNMODIFIED — `tests/unit/test_attestation_gate.py` (decide() bound matrix,
at-bound terminal pin), `tests/unit/test_attestation_resolver_activation.py` (would-be
mapping), `tests/unit/test_attestation_epoch_replay.py`, `tests/unit/test_attestation_nudge_inject.py`
all restored to pristine and green. The ONE pin updated BY RULING (semantics changed by
user decision, flagged per commission):
`tests/unit/test_attestation_judge_wiring.py::test_judge_not_called_on_terminal_after_bound_path`
— formerly pinned "bound exhaustion with a never-spoke judge terminalizes (END, escalation
write, no increment)"; now pins the v3 ruling: judge still never invoked (budget parity)
BUT the terminal is withheld (deny+nudge continues, increment runs, counter rises past the
bound, the `leader_completion_gate_bound_exhausted_never_spoke` audit row fires).

**New regression file:** `tests/unit/test_lca_false_complete_fixes.py` (25 tests: mixed
arc, never-spoke arc + attest exit + re-arm, directive matrix, scanner pins, surface
renders, observer linkage). **Timeout config pins:**
`tests/unit/test_attestation_judge_resolver.py` (default-180 class: default, unset env,
env override, clamp, worst-case 2×180s) + the two literal-25 pins updated to 180.
## D-entry 2026-09-26 — P0 hotfix: incident 7d4a3bd9 cycle-2 NameError `ctx.is_fresh_episode_user_message` (third seam-mocking blind-spot confirmation)

The d5c50994 cycle-1 commit (incident 7d4a3bd9 fix cycle) added three
`fresh_episode_attestation_reset=ctx.is_fresh_episode_user_message`
references inside `_process_message_with_tracking` at
`daemon/services/instance_messaging.py:4047/4060/4088`. The `ctx` was a
`_PreparedEnqueueContext` NamedTuple local to `enqueue_message` — NOT in
scope inside `_process_message_with_tracking`. Every message raised
`NameError: name 'ctx' is not defined`, broke all message processing
on the freshly-shipped v0.15.0 (commit 416ae70d).

**Root cause.** The cycle-1 author referenced `ctx.is_fresh_episode_user_message`
inside the wrong function — `enqueue_message` builds the `_PreparedEnqueueContext`
locally and consumes it inline (line 2168 `ctx = await asyncio.to_thread(self._prepare_enqueued_message, ...)`);
the `_process_message_with_tracking` seam, called later by the worker pool, never
sees `ctx`. The fix is to compute the flag at the consumer seam using the
SAME msg_type derivation logic the ledger path uses in
`_prepare_enqueued_message` (lines 1698-1710): source prefix → msg_type,
then `(priority == 1 AND msg_type == HUMAN)`. At the consumer seam only
`message_source` is in scope; priority defaults to 1 (the user-facing entry
path), so HUMAN-typed sources match the ledger's fresh-episode condition.

**Seam-mocking lesson — third empirical confirmation.** The bug slipped past
the `tests/unit/test_lca_false_complete_fixes.py::TestFreshEpisodeChannelReset`
family (35 tests, all green at base) because those tests assert the
`fresh_episode_attestation_reset` parameter on `_build_graph_input`
DIRECTLY — they never exercise the MESSAGING seam where the kwarg is
*constructed*. `AsyncMock` + `inspect.getsource` substring assertions stay
green at the InstanceManager / InstanceMessagingService facade seam; this
is the second-order class those mocks miss (the BUILD of the parameter,
not its CONSUMPTION).

Blueprint §Core Architecture Facade-Forwarding Discipline warns the same
class slips past unit tests at this seam. This is the THIRD empirical
confirmation: the first two were the enqueue_message→process seam
(c5ae6d95 and 80bb61dd lessons); the third is the same seam again.

**Fix applied (commit, branch `feature/lca-p0-ctx-hotfix`, off latest
416ae70d):**

* Replaced 3 broken `ctx.is_fresh_episode_user_message` references at
  lines 4047 / 4060 / 4088 with a local `is_fresh_episode_user_message`
  computed at the function-body level using the SAME msg_type prefix
  logic the ledger path uses.
* No new param added to `_process_message_with_tracking`'s signature
  (the ledger path itself does not thread the flag across the
  enqueue→process boundary either).
* No new context object invented.

**Real-path regression test (no mocks at the seam):**
`tests/integration/test_fresh_episode_attestation_reset_p0.py`. Four
tests drive the REAL `_process_message_with_tracking` through its real
path (real DB, real MessageQueue row, real GraphTap capture) and assert:

1. **No NameError on any path.** Pre-fix: raises
   `NameError: name 'ctx' is not defined` at line 4088. Post-fix: graph
   runs, captures graph_input.
2. **User-API message stamps `fresh_episode_attestation_reset=True`** on
   the user message's `additional_kwargs` (matches ledger path
   semantics for HUMAN-typed source).
3. **internal_agent: source does NOT stamp the sentinel** (AGENT
   msg_type is not a fresh episode).
4. **internal_report: source does NOT stamp the sentinel**
   (COMPLETION_REPORT is not a fresh episode).

**Live boot+POST verification (pre-fix and post-fix):** Disposable
PostgreSQL on `ens_p0_ctx_repro` (port 5432), mock OpenAI-compatible
LLM endpoint (port 19999), daemon booted on port 18080. POST
`/api/instances/{id}/messages` returned HTTP 200, message completed
cleanly, no NameError in logs. Pre-fix would raise NameError on
the same call.

**Adjudication rule going forward.** Any review of commits that touch
the InstanceManager / InstanceMessagingService facade seam MUST grep
for any `_PreparedEnqueueContext`-shaped local references that leak
across the enqueue→process boundary. A unit test that exercises
`_build_graph_input` directly is NOT a regression proof for the
messaging seam — the seam itself must be exercised.