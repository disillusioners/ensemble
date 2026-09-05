# Plan Overview: Leader Completion Attestation

Date: 2026-09-05
Author: planner[v2] via plan-creation worker
Branch: `feature/leader-completion-attestation`
Companion docs: [`requirements.md`](./requirements.md), [`technical-analysis.md`](./technical-analysis.md), [`decisions.md`](./decisions.md), [`research-findings.md`](./research-findings.md)
Status: Draft — handed to architect for D1–D10 resolution before implementation.

---

## Objective

Prevent the leader instance from being marked COMPLETED while its work is still unfinished, by requiring an explicit attestation tool call before completion and recovering (via a durable, user-authored message) when the call is missing. The feature applies to all future leader missions and is bounded by a per-instance retry counter, a kill-switch, and a dry-run mode so operators can flip it on/off with one restart.

Single sentence: *A leader instance cannot transition to terminal status unless the most-recent AIMessages in its message stream include an `attest_completion` tool call; missing attestation triggers bounded durable recovery until the bound is reached, after which the instance completes with an escalation flag.*

---

## Scope

### In Scope

- **Attestation tool** with stable name (`attest_completion`, D7), no required args, returns a structured success payload, registered via the 10-step tool discipline, opted-in by `agents/leader/meta.json:14-15` `tools.allow`.
- **Scanner** over the last N AIMessages of `state.values['messages']` (N configurable; default 3 per D4) that returns whether the attestation tool call is present.
- **Gate** (per R1/R2): `decision ∈ canonical 5-value enum per Phase 4 task 4.5 (verbatim pointer — do not restate)` from inputs `(attestation-in-window, pending_children, queued_or_expected_wakeups, denied_count, bound, scope, mode)`. The gate DENIES ONLY when `attestation is missing` AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` — legitimate delegation turn-ends (children active / wakeups pending) ALLOW without attestation (kills nudge-flood). The gate is leader-only, fail-OPEN, and lives in-graph (D1=B; pre-END interception).
- **In-graph deny nudge** (THE deny path per R1): when the gate denies, a checkpoint-durable in-state `HumanMessage` is injected by the gate node and routed back to `agent` — same execution, no revive, no `manager.enqueue_message` on deny (C1b, C5 fork ruling per `architecture-recommendation.md` §3). The recovery-injection / `manager.enqueue_message` path is RELOCATED to **Phase 6 (fast-follow)** as a backstop, NOT in the MVP deny path.
- **Per-instance denied-count ledger** with bounded retry (default 3 per D5). Persists in instance row for durability across revives. Reset semantics: `attestation_denied_count` resets to 0 on every allow (otherwise a revived leader starts its next mission pre-burdened — architect addition).
- **Terminal fallback** when bound is exceeded: allow terminal + emit `gate_terminal_after_bound` event + set `completion_gate_escalated=true` flag on instance row (D5 option).
- **Mode config**: single tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce` (D2 RESOLVED), default **`dry`** at ship. Operators flip to `enforce` after ≤2-week soak on adjudicated dry-log false-positive rate. **Tri-state single-env design** (avoids the invalid `ENABLED=OFF, DRY_RUN=ON` state of a two-env design).
- **Window/bound knobs**: `ENSEMBLE_LEADER_ATTESTATION_WINDOW` (int, default 3) and `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` (int, default 3) — both restart-read, Pattern C.
- **Boot log** announcing the resolved effective values for the three knobs (one-line, Pattern C).
- **Structured logging** of every gate decision with the schema: `event=leader_completion_gate`, `decision` ∈ canonical 5-value enum per Phase 4 task 4.5 (verbatim pointer — do not restate), `instance_id`, `attestation_present`, `denied_count`, `gate_location`, `leader_prompt_version`, `pending_children`, `attest_seen_outside_window`, `mode` (O8 — additional fields for R2 gate diagnosis). Dry-mode lines carry scanner diagnostics (window truncated? summary-seen?) so dry→enforce promotion is adjudicated on data.
- **Prompt contract** in `agents/leader/rule.md` (canonical `### Must` block) and `agents/leader/workflow.md` (mirror) — instructs the leader to call `attest_completion` before declaring done and to treat the in-graph nudge as a real user instruction (since the nudge is in-graph the leader sees it as a continuation signal — same prose as the durable recovery text).
- **Test matrix**: unit (scanner window semantics incl. text-only-claim-doesn't-count, gate decision incl. R2 inputs, both-wiring-branches activation, fail-open on scanner exception, ledger, mode-resolver, dry-mode decision-logging, authz fail-closed, tool registration drift) + integration (full hallucination→deny-nudge→continue→attested-completion→finalize E2E; bound-exceeded escalation; must-not-break regression: normal attested completion, WC-wake lanes, report-injection claim machine, existing sweeps) + a parameterized activation test over `language_check_enabled ∈ {True, False}` (Phase-2 exit criterion — C2).

### Out of Scope

- **Origin-stamping defect fix** (deferred per `requirements.md` §OS-1; P2.2 plans a `USER_ORIGIN_SOURCES` whitelist separately). The feature relies on the existing default `else → HUMAN` stamp (`daemon/services/instance_messaging.py:1685-1704`).
- **Inter-report gap premature-finalize bug class** (deferred per §OS-2). Different root cause; not addressed by this feature.
- **Child-side hallucination prevention** (§OS-3). Different problem class.
- **Per-tree or per-mission attempt counting** (§OS-4). Per-instance only.
- **Live-flip kill-switch** (§OS-5). Restart-only is in scope.
- **Recovery message text customization** (§OS-6). A single constant text is in scope.
- **Replay of historical hallucination incidents** (§OS-7). Manual testing only at MVP.
- **Cross-instance attestation coordination** (§OS-8). Leaders are per-instance.
- **Auto-flips on the kill-switch** (WC-wake precedent: operator flip after ≤2-week soak or on incident).
- **Promoting attestation to `PRIVILEGED_TOOL_CATEGORIES`** (`daemon/tools/_tool_registry.py:101-103`). D7 sub-question; recommendation is NOT privileged.

### Module / File Surface Touched

- `agents/leader/meta.json:14-15` — `tools.allow` opt-in
- `agents/leader/rule.md` — `### Must` block contract
- `agents/leader/workflow.md` — mirror contract
- `daemon/tools/attestation.py` — new tool module
- `daemon/tools/_tool_registry.py` — `@register_tool_category`, `CATEGORY_MODULES`, `DYNAMIC_TOOL_NAMES`, `KNOWN_TOOL_NAMES` regen
- `daemon/tools/upgrade_tools.py:110-143` — 10-step checklist
- `daemon/services/attestation_scanner.py` — new pure-function scanner
- `daemon/services/attestation_gate.py` — new pure-function gate decision logic (R2 inputs)
- `daemon/services/attestation_resolver.py` — new mode / window / bound resolver (Pattern C)
- `daemon/repositories/instance/` — schema migration for `attestation_denied_count`, `completion_gate_escalated` columns
- `daemon/graph.py:2707-2734`, `:6445-6470` — wrapper composition under **independent attestation_enabled flag, both return paths of `create_should_continue(language_check_enabled)`** (C2)
- `daemon/manager.py` — `manager.count_pending_children(instance_id)` + `manager.get_queued_or_expected_wakeups(instance_id)` facade methods specified in Phase 2 task 2.3 for R2 inputs
- `daemon/config.py:805-844 / :463-506 / :114-191` — Pattern C config registration (single tri-state env + two knobs)
- `daemon/services/instance_messaging.py:1685-1704` — origin stamping (untouched; in-graph nudge doesn't go through this path)
- `tests/unit/test_attestation_*.py` — unit tests (multiple files)
- `tests/integration/test_attestation_*.py` — integration tests (multiple files)
- `docs/setup.md` — operator runbook update for the three env vars

**Phase 6 (fast-follow) file surface — RELOCATED, NOT in MVP:**

- `daemon/services/attestation_recovery.py` — durable `manager.enqueue_message` recovery injector (C fast-follow backstop)
- `daemon/services/attestation_recovery_sweep.py` — async sweep modeled on `ReportDeliveryRecoveryService` 5-lane
- `daemon/services/<new>_recovery.py` wiring — sweep + per-lane kill-switch
- `daemon/manager.py:6530-6626` — facade-forwarding + JAFP integration tests

---

## Architecture Summary (post-reconciliation — authoritative)

**R1 (RESOLVED)**: the deny path is **nudge-MVP** — an in-graph, checkpoint-durable `HumanMessage` nudge injected by the gate node on deny, routed back into `agent` in the same execution. NO `manager.enqueue_message` on deny, NO revive on deny (C1b — see `architecture-recommendation.md` §3, the C5 fork ruling; doing both would double-deliver and spuriously revive a COMPLETED instance).

**R2 (RESOLVED)**: the gate denies ONLY when `(attestation missing in window) AND (no pending children) AND (no queued/expected wakeups)`. Legitimate delegation turn-ends (children active / wakeups pending) ALLOW without attestation. In the original bug class, children are all TERMINAL (hallucinated "in progress"), so leader turn-end leads directly to instance COMPLETED — exactly the state the gate must catch. R2 kills nudge-flood by not denying during legitimate work. Gate needs subtree/pending state held by the manager — the access path is specified in Phase 2 task 2.3.

**D1=B (RESOLVED)**: in-graph pre-END interception (architect ruling per `architecture-recommendation.md` §1, §2 trade-off matrix). Chokepoint shared by all completion stampers; structurally race-free (denied turn never reaches observer Step 2 / child_reports / revive); exact `language_check` precedent (`daemon/graph.py:2589-2734`, wiring `:6445-6470`).

**D2 (RESOLVED)**: tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce`, default **`dry`** at ship. Promote to `enforce` after ≤2-week soak on adjudicated dry-log false-positive rate.

**C2 (critical)**: `create_should_continue(language_check_enabled)` returns the ORIGINAL `should_continue` UNCHANGED when `False` (verify: `graph.py:2718-2721`; live wiring for auto-language leaders `graph.py:6459-6484`). A single-branch gate is STRUCTURALLY INERT. Phase 2 wires an INDEPENDENT `attestation_enabled` flag active in BOTH return paths; parameterized activation test over `language_check_enabled ∈ {True, False}` is a Phase-2 EXIT CRITERION.

**C3 (critical)**: fail-OPEN — any exception in scanner/gate ⇒ `allow` + structured error log; `except Exception` at the attestation_denied_count ledger DB seam (W4 precedent's narrow set at `graph.py:2663-2688` does NOT cover SQLAlchemy `OperationalError`).

### Common Trunk (Phase 1–5)

1. **Attestation tool** (`daemon/tools/attestation.py`) — registered, opted-in by leader, drift-test enforced. **Phase 1.**
2. **Prompt contract** (`agents/leader/rule.md` + `agents/leader/workflow.md`) — instructs leader to call the tool; treats the in-graph nudge as a continuation signal. **Phase 1.**
3. **Scanner** (`daemon/services/attestation_scanner.py`) — pure function over `state.values['messages']`, scans last N AIMessages, returns `(attested, diagnostic_detail)`. **Phase 2.**
4. **Gate decision logic** (`daemon/services/attestation_gate.py`) — pure function over `(attestation, pending_children, queued_or_expected_wakeups, denied_count, bound, scope, mode) → decision ∈ canonical Decision enum per Phase 4 task 4.5 (verbatim pointer — do not restate). R2 inputs. **Phase 2.**
5. **In-graph deny nudge** — checkpoint-durable `HumanMessage` injected by the gate node; routes back to `agent`; same execution. NO `manager.enqueue_message` on deny. **Phase 2.**
6. **Both-wiring-branches composition** — independent `attestation_enabled` flag active in BOTH return paths of `create_should_continue(language_check_enabled)`. **Phase 2 (C2).**
7. **Attempt ledger** (instance-row columns `attestation_denied_count`, `completion_gate_escalated`) with **reset-on-allow** semantics. **Phase 3.**
8. **Mode / config resolver** (`daemon/services/attestation_resolver.py`) — Pattern C, restart-read, cached global, one-time boot log. Tri-state `MODE` env + window/bound knobs. **Phase 4.**
9. **Dry-run mode** — decision = log-only, no deny fires, no side effects (evaluation + decision logging only). **Phase 4.**
10. **Structured logging** with the full schema incl. R2 fields (`pending_children`, `attest_seen_outside_window`, `mode`). **Phase 4.**

### SUPERSEDED — D1-Parameterized Gate Workstream (archived for reference)

> **SUPERSEDED 2026-09-05 by architect council ruling on `architecture-recommendation.md`.** The plan originally parameterized the gate attachment site across five options (A/B/C/D/E) plus a hybrid, deferred to the architect for D1. D1 is now resolved to **B (in-graph pre-END interception)** as the MVP. Candidate C (async sweep) is RELOCATED to Phase 6 as a post-soak backstop. Options A/D/E are **DISQUALIFIED** by the trade-off matrix (`architecture-recommendation.md` §2 — A is bypassable across `_update_parent_on_child_complete` + `error_reporting.py:319`; E double footgun at `:1698` re-arm + `:3740-3752` bare ORM terminal write). The resolution and disqualifications are authoritative; the table below is preserved for traceability only — none of the MVP phases implement any of these options.

| ~~D1 option~~ | ~~One-line summary~~ | ~~File:Line~~ | ~~Phase~~ |
|---|---|---|---|
| ~~**A** — Pre-commit child_reports~~ | ~~Gate inside the atomic conditional UPDATE at `_process_child_completion_db_sync`~~ | ~~`daemon/services/child_reports.py:1983, :2545, :2737, :2895`~~ | ~~DISQUALIFIED — bypassable~~ |
| ~~**B** — In-graph `end_candidate` interception~~ | ~~Wrapper that translates END → end_candidate → attestation_gate node; mirror language_check precedent~~ | ~~`daemon/graph.py:2707-2734`, `:6463`~~ | ✅ **MVP — Phase 2** |
| ~~**C** — Async post-completion sweep~~ | ~~New lane modeled on `ReportDeliveryRecoveryService` 5-lane~~ | ~~`daemon/services/<new>_recovery.py`; wiring `daemon/manager.py:6093-6250`~~ | **Phase 6 (backstop)** |
| ~~**D** — Tool-as-trigger (inverted control)~~ | ~~Tool call drives completion via state flag; `should_continue` checks the flag~~ | ~~tool layer + `daemon/graph.py:2462-2533`~~ | ~~DISQUALIFIED — sticky flag survives revive~~ |
| ~~**E** — Observer-path gate at `_finalize_job` Step 2~~ | ~~Extend Step 2 with `gate_deferred`; re-arm at `:1698`~~ | ~~`daemon/services/job_feedback_observer.py:3083, :3703-3758, :1698`~~ | ~~DISQUALIFIED — defer-starvation + clobber~~ |
| ~~**B+C** — Hybrid~~ | ~~B primary, C backstop for B's misses~~ | ~~B components + C components~~ | ~~Resolved to B-MVP + C-Phase-6~~ |

See `technical-analysis.md` §"Comparison Matrix" for the trade-off matrix. See `decisions.md` §D1 for the resolved decision.

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Attestation Tool + Registration + Prompt Contract | Ship a registered, opted-in, drift-tested attestation tool with a leader prompt contract requiring its use. Includes the compaction-spike precondition test (D10(b) mitigation). | 7 | independent | pending |
| 2 | Scanner + Gate (R2 inputs) + Both-Branches Composition + In-Graph Nudge | Pure scanner + pure decision function (R2 inputs) + in-graph deny nudge + independent `attestation_enabled` flag in BOTH `create_should_continue` return paths + parameterized activation test. | 8 | tight with Phase 1 (tool name); tight with Phase 3 (ledger reads/writes); tight with Phase 4 (mode + window + bound feed gate) | pending |
| 3 | Ledger + Bound + Escalation (no recovery injector) | Instance-row columns `attestation_denied_count` + `completion_gate_escalated`, reset-on-allow semantics, bound enforcement, terminal fallback, `gate_terminal_after_bound` event. **No recovery injector — relocated to Phase 6.** | 6 | tight with Phase 2 (gate reads/writes counter); tight with Phase 4 (counter source for observability) | pending |
| 4 | Tri-State Mode Resolver + Config Surface + Observability + Dry-Run | Single tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce` (default `dry`) + window/bound knobs + Pattern C resolver + boot log + dry-run mode (allow + log only, zero side effects) + structured logging schema with R2 fields. Promotion metrics + operator runbook satisfy the instrumented dry-run (not a blocking pre-Phase-2 activity). | 6 | tight with Phase 2 (config feeds scanner window + gate inputs); tight with Phase 3 (counter source for observability) | pending |
| 5 | Test Matrix | Unit (scanner, gate incl. R2 inputs, fail-open, mode-resolver, dry-mode decision-logging, ledger, authz, registration) + integration (E2E hallucination→deny-nudge→continue→attested→finalize; bound-exceeded; must-not-break regression) + **parameterized activation test over `language_check_enabled ∈ {True, False}`** (C2 exit criterion). | 9 | tight with all prior phases | pending |
| 6 (fast-follow, post-soak) | Durable Enqueue Backstop + O5–O9 Pre-Flip Work | New `daemon/services/attestation_recovery.py` (durable `manager.enqueue_message` with `source="attestation_recovery"` per D6) + async sweep modeled on `ReportDeliveryRecoveryService` 5-lane + facade-forwarding + JAFP no-JobItem tests + O5–O9 pre-flip hardening. Ships only after adjudicated dry→enforce soak data. | TBD | tight with Phase 3 (counter feeds sweep); tight with Phase 4 (mode + kill-switch feed sweep) | pending |

**Total tasks across MVP phases 1–5: 36. Phase 6 tasks: deferred to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md).**

### Coupling Map

| Phase | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|---|---|---|---|---|---|---|
| **Phase 1** | — | tight (tool name + args feed scanner + gate) | independent | independent | tight (tool registration drift test) | independent |
| **Phase 2** | tight | — | tight (gate reads/writes counter) | tight (config feeds scanner window + gate inputs) | tight (scanner/gate unit tests; C2 parameterized activation test) | loose (gate→recovery seam) |
| **Phase 3** | independent | tight | — | tight (counter source for observability) | tight (ledger unit + integration tests) | tight (counter feeds sweep) |
| **Phase 4** | independent | tight | tight | — | tight (config / mode / dry tests) | tight (mode + kill-switch feed sweep) |
| **Phase 5** | tight | tight | tight | tight | — | loose (JAFP + facade-forwarding tests) |
| **Phase 6** | independent | loose | tight | tight | loose | — |

### Must-Not-Break Surfaces (per C-7)

The plan must preserve behavior on these surfaces whether the gate is `off`, `dry`, or `enforce`:

| Surface | File:Line | Verification |
|---|---|---|
| Normal attested completion | `daemon/services/child_reports.py:1983` + `daemon/services/job_feedback_observer.py:3703-3758` | Integration test AC-E2E-3 |
| Mission finalize | `daemon/services/job_feedback_observer.py:3083` | Integration test AC-E2E-5 |
| WC-wake routing lanes | `daemon/services/instance_messaging.py:114-191`; `ENSEMBLE_WC_WAKE_ENQUEUE` default OFF | Integration test AC-E2E-5 + boot log coexistence |
| Report-injection claim machine | `daemon/graph.py:414-490`, `:3622-3658` | Integration test AC-E2E-5 |
| Existing sweeps | `daemon/services/report_delivery_recovery.py:207`; `daemon/services/waiting_children_watchdog.py:312` | Integration test AC-E2E-5 |
| Legitimate delegation turn-end (children active / wakeups pending) | `daemon/services/child_reports.py` subtree state | Integration test AC-E2E-6 (R2 allow-without-attestation path) |

**Relocated to Phase 6 (NOT an MVP must-not-break surface):**

| ~~Revive semantics~~ | ~~`daemon/services/instance_messaging.py:1867-1909`~~ | ~~Phase 6 integration test AC-E2E-5~~ — moved because the MVP deny path does NOT use `manager.enqueue_message` and therefore does not trigger revive |
| ~~JAFP no-JobItem for internal messages~~ | ~~`daemon/services/instance_messaging.py:1960`~~ | ~~Phase 6 integration test AC-4.5 JAFP clause~~ — moved with the recovery injector |
| ~~Facade-forwarding discipline (recovery injection path)~~ | ~~`daemon/manager.py:6530-6626`~~ | ~~Phase 6 facade-forwarding test~~ — moved with the recovery injector |
| ~~Defer-starvation footgun~~ | ~~`daemon/services/job_feedback_observer.py:1698-1737` defer-early-return; `instance.status = terminal_status` Step-2 write at `:3753`~~ | ~~Moot in MVP (D1=E disqualified)~~ |

---

## Risks

Top risks, ordered by severity × likelihood. Full register in `technical-analysis.md` §"Risk Register".

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **C2 — Single-branch gate is structurally inert**: `create_should_continue(language_check_enabled)` returns the ORIGINAL `should_continue` UNCHANGED when `False` (verify `graph.py:2718-2721`; live wiring for auto-language leaders `graph.py:6459-6484`). Piggybacking on `language_check` wiring silently disables the gate for most instances. | High | Medium (silent failure mode) | Phase 2 wires an INDEPENDENT `attestation_enabled` flag active in BOTH return paths of `create_should_continue(language_check_enabled)`; explicit composition choice documented; **parameterized activation test over `language_check_enabled ∈ {True, False}` is a Phase-2 EXIT CRITERION**. |
| 2 | **C3 — Gate failure crashes every leader mission**: any unhandled exception in scanner/gate on the routing path errors the leader. | High | Medium (scanner touches LLM-shaped messages; resolver env-parse may fail on a future value) | Fail-OPEN wrapper spec: `try/except Exception` around scanner + gate call ⇒ `allow` + structured error log (`event=leader_completion_gate_error`); `except Exception` at the attestation_denied_count ledger DB seam (W4 precedent's narrow set at `graph.py:2663-2688` does NOT cover SQLAlchemy `OperationalError` — Phase 3 widens the exception class). Integration test: inject scanner exception, assert allow + error log. |
| 3 | ~~**Nudge-flood on legitimate delegation turn-ends**~~ — RESOLVED-by-R2. | (resolved) | (resolved) | R2 input condition `(attestation missing in window) AND (pending_children == 0) AND (queued_or_expected_wakeups == 0)` — legitimate delegation turn-ends (children active / wakeups pending) ALLOW without attestation. Original bug class: children all TERMINAL → leader turn-end leads to instance COMPLETED → gate must catch; the R2 condition catches exactly that case. |
| 4 | **C1b — Double-delivery via nudge + enqueue**: doing both the in-graph nudge AND `manager.enqueue_message` on one deny double-delivers; the enqueued task fires after the attested END and spuriously revives a COMPLETED instance. | High | High (architectural) | **Forbidden dual-delivery**: in MVP, deny path is in-graph nudge ONLY; `manager.enqueue_message` recovery is RELOCATED to Phase 6 and used only as a post-completion backstop (TOCTOU re-query required before enqueue). Self-grep guard forbids the dual-delivery pattern in MVP plan files (the pattern: `enqueue_message` called from a deny path). |
| 5 | **Compaction folds attestation tool_call before scanner reads** (D10(b)). Default config (`recent_message_window=10`, `min_recent_window=3`) is safe; aggressive context pressure may reduce the preserved tail to 3 groups. | High | Low (default config covers) — Medium (under context pressure) | Phase 1 task 1.7 compaction-spike precondition test; if test reveals a gap, `aget_state` pre-compaction fallback (D10(b1)) becomes mandatory. **O1 boot-assert call site: Phase 4 task 4.2** (resolver + boot log + `attestation_resolver.boot_assert_window_within_compaction_floor`). |
| 6 | **Stale pre-revive attestation watermark + diagnostic** (O3): a checkpoint pre-revive contains an attestation but post-revive the scanner re-checks fresh window state; if window semantics differ across compaction boundaries, false-positives may appear. | Medium | Low | Phase 4 log schema carries `attest_seen_outside_window: bool` to surface this diagnostic; if rate exceeds threshold in dry-log soak, O3 mitigation flips on (rebuild window from full history). |
| 7 | **Pause-mid-gate double-increment** (O4): a pause between gate deny and counter update could leave the counter unincremented while the leader saw a deny. | Medium | Low | Phase 3 spec: idempotent per-denial-epoch upsert (primary choice — `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1` keyed by `(instance_id, denial_epoch)`); secondary alternative is documented inflation (counter may exceed bound within a single pause-resume cycle but never causes a strand). Phase 3 picks primary and documents the decision. |
| 8 | **Reset-site enumeration + insta-escalation hazard** (O2): an `escalated→revive→next-mission` sequence could insta-escalate if `attestation_denied_count` is not reset on `terminal_after_bound`. | High | Medium | Phase 3 resets `attestation_denied_count` to 0 on `terminal_after_bound` (not just on allow-with-attest); documents reset triggers explicitly per the leader ruling (CLOSED-by-leader, 2026-09-05) — four triggers ONLY: (1) attested allow; (2) `terminal_after_bound` finalization; (3) revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode); (4) instance creation. Counter does NOT auto-reset on PAUSED → RUNNING (per leader ruling: pause/resume and checkpoint reload do NOT reset). The actual `_loop_breaker_state.pop` sites (3, at `manager.py:3734/:3798/:8548`) are NOT a precedent for row-scoped columns — column reset is via DB UPDATE not in-memory pop. |
| 9 | **Recovery source-prefix collision** (Phase 6 only): if source starts with `internal_*:`, the message gets COMPLETION_REPORT / ERROR_REPORT / AGENT type, not HUMAN. (Deferred to Phase 6; not an MVP risk.) | High (Phase 6) | Low (D6 well-defined: `"attestation_recovery"`) | Phase 6 unit test asserts source does NOT start with `internal_*:`; D6 source-value choice documented in runbook. Out-of-MVP scope. |
| 10 | **Sweep race with finalize** (Phase 6 only — observer-vs-revive race): post-completion sweep sees a just-revived RUNNING instance; recovery enqueues into a RUNNING leader, doubling the leader's work. | Medium (Phase 6) | Medium | Phase 6 (only) includes TOCTOU re-query right before enqueue; sweep skips RUNNING instances. Integration test: finalize + sweep tick + revive race. Out-of-MVP scope. **Note**: the pre-existing observer-vs-revive race (correction #3b in `architecture-recommendation.md` §6) exists independently of this feature; file separately; do NOT fix incidentally in this branch. |

---

## Success Criteria

Mapped to `requirements.md` ACs. Every AC must be testable. Full AC coverage is tracked per phase exit-criterion checklists; this table summarizes headline criteria only.

| # | Criterion | Test method | Pass threshold |
|---|---|---|---|
| 1 | Attestation tool exists, registered, opted-in by leader | Unit AC-1.1, AC-1.2, AC-9.1, AC-9.2, AC-9.3 | All unit tests pass; `KNOWN_TOOL_NAMES` regenerated; `tools.allow` opt-in verified |
| 2 | Scanner detects attestation in last N messages; window bounded | Unit AC-2.1, AC-2.2, AC-2.3, AC-2.4, AC-2.5 | All window-semantics unit tests pass; 1000-message state scanned in N=3 |
| 3 | Gate denies non-attested completion; no terminal write | Integration AC-3.1 | `attestation_denied_count` increments; in-graph `HumanMessage` nudge injected (durable via LangGraph checkpoint, no durable-recovery off-ramp on deny); terminal-status write skipped |
| 4 | Gate allows attested completion; no recovery | Integration AC-3.2 | Terminal-status write proceeds; no recovery; `allowed` log emitted |
| 5 | Recovery message is durable + user-authored | Integration AC-4.5 (durable-path AC, per the renumbering fix in this revision — MVP nudge ACs are AC-4.1 + AC-4.2 + AC-4.3 + AC-4.4 in `requirements.md:276-298`) | `MessageQueue`+`Task` rows in single txn; `HumanMessage` rendered; survives restart; NO `JobItem` row |
| 6 | Prompt contract in `agents/leader/rule.md` | Manual + grep AC-5.1, AC-5.2 | Tool name + recovery text both appear in `### Must` block under `## Must` |
| 7 | Bounded retry; bound-exceeded terminal fallback | Unit AC-6.1, AC-6.2, AC-6.3, AC-6.4 | Counter increments; bound enforced; escalation event emitted exactly once; counter resets on new mission |
| 8 | Configurable window + bound + kill-switch | Integration AC-7.1, AC-7.2, AC-7.3, AC-7.4, AC-7.5, AC-7.6 | Env vars resolve at boot; restart-read; boot log announces resolved values |
| 9 | Authorization is fail-closed | Unit AC-9.1, AC-9.2 | Leader has tool; non-leader lacks tool; drift test passes |
| 10 | Observability (every gate decision logged with full schema) | Integration AC-10.1, AC-10.2 | 1000/1000 decisions logged with full schema; escalation event unique per instance |
| 11 | Scope (leader-only v1) | Integration AC-11.1 | Non-leader parent is not gated; behavior byte-equivalent to baseline |
| 12 | **NFR-1**: Gate decision overhead ≤ P95 20 ms | Integration | Daemon log timing around gate; ≤ 20 ms P95 |
| 13 | **NFR-4**: Recovery survives crash between gate decision and durable write | Integration AC-4.5 (chaos clause — RELOCATED to Phase 6) | After simulated crash + restart, recovery delivered on next boot |
| 14 | **NFR-5**: Kill-switch restart-read | Integration AC-7.6 | Env change requires restart |
| 15 | **NFR-6**: Recovery text is a server-authored constant | Unit | Constant text hard-coded; injection attempts rejected |
| 16 | **NFR-10**: Feature OFF = byte-equivalent behavior to baseline | Integration AC-7.3, AC-E2E-4 | Identical logs + DB writes |
| 17 | **NFR-11**: Must-not-break regression — normal attested completion, mission finalize, revive, WC-wake, sweeps, report-injection claim machine | Integration AC-E2E-5 | All surfaces behave identically with ON and OFF |
| 18 | **NFR-12**: No defer-starvation footgun | Integration | Job finalizes after `gate_deferred=True` re-arm (D1=E only) |
| 19 | **NFR-13**: All knobs configurable (no hardcoded constants) | Code review + drift test | Grep for hardcoded N/bound returns zero hits in production code |
| 20 | **NFR-14**: Gate decision is a pure function | Unit | Pure function file exists; unit tests cover all branches |
| E2E-1 | Full hallucination → recovery → attested → complete | E2E AC-E2E-1 | 1× denied + 1× allowed log; terminal-status write succeeds on retry |
| E2E-2 | Bound-exceeded escalation | E2E AC-E2E-2 | `bound + 1`-th attempt → terminal + escalation + flag |
| E2E-3 | Normal attested completion unaffected | E2E AC-E2E-3 | Attested → terminal; no recovery; `allowed` only |
| E2E-4 | Kill-switch disables the feature | E2E AC-E2E-4 | OFF → no gate; byte-equivalent baseline |
| E2E-5 | Must-not-break surfaces | E2E AC-E2E-5 (parameterized) | All 6 surfaces behave identically ON vs OFF |

---

## Research Insights

`research-findings.md` compiles the full architecture digest. Key insights shaping this plan:

1. **In-graph END interception is precedented exactly once** (`language_check` at `daemon/graph.py:2707-2734`, wiring `:6445-6470`). D1=B is the strongest pattern fit; composition with language_check is mechanical BUT requires an independent `attestation_enabled` flag (C2 — see Risk #1).
2. **Gate failure mode**: the gate touches LLM-shaped messages; any unhandled exception errors the leader. Fail-OPEN is mandatory (W4 precedent `graph.py:2661-2664`); `except Exception` is required at the ledger DB seam (W4's narrow set does NOT cover `OperationalError`).
3. **Compaction default is safe** (`recent_message_window=10` groups, verbatim preserved tail, `daemon/config.py:728-729`). Risk only under aggressive context pressure reducing tail to `min_recent_window=3` groups. Phase 1 precondition test characterizes this.
4. **Origin-stamping default** (`daemon/services/instance_messaging.py:1685-1704`) is not in the MVP deny path. Phase 6 (backstop only) uses `manager.enqueue_message` with `source="attestation_recovery"` (D6 DEFERRED-to-Phase-6). MVP's in-graph nudge does not touch this path.
5. **Facade-forwarding duty** (Phase 6 only — recovery uses `manager.enqueue_message`): any new kwarg to `enqueue_message` requires a real-dispatch integration test asserting the intended exception type. Precedent at `tests/unit/test_manager_enqueue_message_work_id_required.py`.
6. **WC-wake precedent** for the mode resolver: module env resolver + cached global + one-time boot log (`daemon/services/instance_messaging.py:114-191`). Pattern C is the recommended resolver shape. Blank→`off` parsing mirrors WC-wake resolver.
7. **Line-citation correction**: `child_reports.py` atomic UPDATEs are at `:2545/:2737/:2895` (not `:2566/:2756/:2916` as the explorer comments said — those are stale comment-line drift; verified against HEAD).
8. **C5 fork ruling** (`architecture-recommendation.md` §3): the user-closed constraint "Recovery MUST use `manager.enqueue_message`" is interpreted as a durability requirement, not a delivery-channel requirement for the MVP deny path. The in-graph checkpoint-durable nudge satisfies C5's intent for the MVP (no RAM-only delivery; LangGraph checkpoints at node boundaries); `manager.enqueue_message` remains mandatory for the Phase 6 out-of-graph recovery path. Rejection forces END-then-enqueue-revive on every deny and reopens D1.
9. **R2 gate inputs require subtree/pending state held by the manager**: the gate node reads `pending_children` and `queued_or_expected_wakeups`. The access path is via `daemon/manager.py` facade methods (to be specified in Phase 2 task 2.3) backed by `daemon/repositories/instance/repository.py` — manager holds the live state; the gate node reaches it through a thin facade call. No new state field on the instance row.
10. **Row-scoped column reset, NOT in-memory pop**: `attestation_denied_count` is a DB column on the instance row. The `_loop_breaker_state.pop` precedent (in-memory dict cleanup at `daemon/manager.py:3734/:3798/:8548`) does NOT apply — DB columns survive revive and require DB UPDATE to reset. Reset-on-allow + reset-on-`terminal_after_bound` is the correct shape (architect addition to council verdict).
11. **Compaction-stale path fix location is `daemon/compaction.py`, NOT the prior stale path** (correction #5 in `architecture-recommendation.md` §6): the actual compaction module is `daemon/compaction.py` (line refs `:1090` correct at real path); **CLOSED-by-leader (residual-fix pass 2026-09-05)**: this correction is now applied across all plan-dir docs — zero prior stale-path occurrences remain plan-dir-wide.

---

## Open Decisions (handed to architect — non-blocking for Phase 1)

These mirror `decisions.md` decision records. Resolutions per `architecture-recommendation.md` are authoritative where listed; all load-bearing decisions (D1/D2/D3/D4/D5/D7/D8/D9/D10) are RESOLVED via leader confirming architect rulings (closed-by-leader per `decisions.md` legend). D5 counter-semantics sub-item closed 2026-09-05 — full ruling recorded under the D5 row.

| ID | Status | Resolution / One-liner | Plan impact |
|---|---|---|---|
| **R1** | ✅ RESOLVED | **nudge-MVP** (in-graph checkpoint-durable nudge is THE deny path; no `manager.enqueue_message` on deny; no revive on deny). C5 fork ruling applies. | Phases 2/5 (gate→nudge→route back); Phase 6 (durable backstop) |
| **R2** | ✅ RESOLVED | Gate denies ONLY when `(attestation missing) AND (pending_children == 0) AND (queued_or_expected_wakeups == 0)`. Legitimate delegation turn-ends ALLOW without attestation. | Phase 2 (gate inputs); Phase 4 (log schema fields) |
| **D1** | ✅ RESOLVED | **B** — in-graph pre-END interception (`create_should_continue` composition → `attestation_gate` node, own flag, both wiring branches). | Phase 2 (C2 composition) |
| **D2** | ✅ RESOLVED | Tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce`, default **`dry`** at ship; promote to `enforce` after ≤2-week soak. | Phase 4 (resolver + boot log) |
| **D3** | ✅ RESOLVED | Gate scope: **leader-only** (graph-build-time `agent_id == "leader"` check; non-leader graphs untouched). | Phase 1 (prompt contract opt-in); Phase 2 (gate scope flag) |
| **D4** | ✅ RESOLVED | Window N default 3, restart-read, Pattern C resolver. N must stay ≤ `min_recent_window` (O1 boot assert in Phase 4). | Phase 4 (resolver sets default + boot assert) |
| **D5** | ✅ RESOLVED | Bound 3 (env `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`); DB columns on instance row (`attestation_denied_count`, `completion_gate_escalated`); allow-completion + flag + structured `gate_terminal_after_bound` event + counter reset-on-allow + counter reset-on-`terminal_after_bound`. Counter semantics per leader ruling (CLOSED-by-leader 2026-09-05): `attestation_denied_count` is PER-MISSION (per-work-episode) — accumulates within a mission, in-graph deny-nudges NEVER reset it; resets on exactly four triggers (attested allow / `terminal_after_bound` finalization / revive-from-COMPLETED via NEW top-level user/mission message / instance creation); NOT reset on pause/resume or checkpoint reload (full verbatim in decisions.md D5). | Phase 3 (ledger + bound) |
| **D6** | DEFERRED-to-Phase-6 | Recovery source value: new explicit source `"attestation_recovery"` + explicit `msg_type → HUMAN` mapping branch + defensive exclusion from any user-origin window. **Implementation lands with Phase 6** (the recovery injector is out of MVP scope). | Phase 6 (recovery injector) |
| **D7** | ✅ RESOLVED | Tool: `attest_completion`, no-arg, idempotent (any call in window counts), short confirmation ToolMessage return, NOT privileged. | Phase 1 (tool + registration) |
| **D8** | RESOLVED (via D2) | Dry-run = tri-state `dry` mode (gate evaluates, logs canonical `dry_log` decision + scanner diagnostics, allows END, ZERO side effects). Always-on structured `leader_completion_gate` logging in every mode. The R2-deny predicate is computed in dry mode and surfaced as `dry_log_deny_predicate_total` (replaces the previous fuzzy counter name per CR-4). | Phase 4 (dry semantics + log schema) |
| **D9** | ✅ RESOLVED | Finalize ordering: moot for D1=B (recovery lands before finalize by construction — denied turn never ENDs, observer Step 2 never fires on it). OPEN only if a future A/E-shaped gate is reconsidered. | Phase 2 (gate) + Phase 3 (escalation) — moot in MVP |
| **D10** | ✅ RESOLVED | (a) ANY-in-last-N-AIMessages; (b) scan current post-compaction state — safe at default config, with N ≤ `min_recent_window` coupling enforced; (c) immune by construction. | Phase 1 task 1.7 (precondition test); Phase 2 (scanner); Phase 4 (O1 boot assert) |

---

## Plan Structure

The plan consists of 6 phase files plus this overview plus `research-findings.md`:

| File | Purpose |
|---|---|
| `plan-overview.md` | This file |
| `research-findings.md` | Architecture digest + line-citation corrections |
| `phase1-plan.md` | Attestation tool + registration seam + leader authz + prompt contract |
| `phase2-plan.md` | Scanner + gate (R2 inputs) + both-branches composition (C2) + in-graph deny nudge (R1) |
| `phase3-plan.md` | Ledger + bound + escalation (no recovery injector) |
| `phase4-plan.md` | Tri-state mode resolver + config surface + observability + dry-run |
| `phase5-plan.md` | Test matrix (unit + integration + E2E + C2 parameterized activation test) |
| `phase6-fastfollow-plan.md` | Durable enqueue backstop + async sweep + O5–O9 pre-flip work (post-soak) |

Each phase file contains: objective; entry criteria; tasks (file:line, decision tags, test notes); risks; exit criteria; rollback story.ks; exit criteria; rollback story.