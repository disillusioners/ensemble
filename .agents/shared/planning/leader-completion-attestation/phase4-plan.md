# Phase 4: Tri-State Mode Resolver + Config Surface + Observability + Dry-Run

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (revised in reconciliation pass)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase1-plan.md`](./phase1-plan.md), [`phase2-plan.md`](./phase2-plan.md), [`phase3-plan.md`](./phase3-plan.md), [`phase5-plan.md`](./phase5-plan.md), [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md), [`research-findings.md`](./research-findings.md)

---

## Objective

Ship the **single tri-state mode env** `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce` (default `dry` at ship — D2 RESOLVED), the window/bound knobs (`ENSEMBLE_LEADER_ATTESTATION_WINDOW`, `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`), the Pattern C resolver (module env + cached global + one-time boot log + O1 boot assert), the structured logging schema (with R2 fields `pending_children` and `attest_seen_outside_window` per O8), the dry-run mode (allow END, ZERO side effects — no deny fires, only evaluation + decision logging), and the promotion metrics + operator runbook that satisfy the instrumented dry-run. This phase is **D2 / D4 / D8 RESOLVED** for the defaults; the resolver and schema themselves are D-independent.

Entry criterion: D2 (RESOLVED — tri-state default `dry`), D4 (default N=3), D8 (RESOLVED via D2 — dry = allow + log only) are decided. Phase 2 (gate wiring + R2 inputs) is merged.

Exit criterion: AC-7.1, AC-7.2, AC-7.3, AC-7.4, AC-7.5 (mode=dry allows every END with full decision log), AC-7.6 (restart-read), AC-7.7 (boot log), AC-7.8 (boot assert) all pass; AC-10.1, AC-10.2 (observability + escalation event uniqueness) all pass; NFR-1 (P95 20 ms) verified; O1 boot assert verified; O8 log schema includes R2 fields.

---

## Entry Criteria

- Phase 2 (gate wiring + R2 inputs + both-branches composition) is merged
- D2, D4, D8 are RESOLVED (D2 RESOLVED — tri-state default `dry`; D8 RESOLVED via D2 — dry = allow + log only)
- Default behavior on unresolved: mode = `dry` (D2 RESOLVED); window N = 3; bound = 3; dry-run mode = evaluate + log only (zero side effects)

---

## Tasks

### 4.1 — Implement the resolver (Pattern C, restart-read, tri-state mode)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_resolver.py` (new); `daemon/services/instance_messaging.py:114-191` (mirror the WC-wake pattern) |
| **Description** | Pattern C: module env resolver + cached global + one-time boot log. Reads the three env vars at module import time, caches them in a module-level global, emits one boot log line announcing resolved effective values. Function signature: `get_config() -> AttestationConfig` (returns dataclass with `mode: Literal["off", "dry", "enforce"]`, `window: int`, `deny_bound: int`, `attestation_enabled: bool`). Env vars: `ENSEMBLE_LEADER_ATTESTATION_MODE` (tri-state, default `dry`; blank → `off`), `ENSEMBLE_LEADER_ATTESTATION_WINDOW` (int, default 3), `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` (int, default 3). **No live flip — restart required** (per C-2). **O1 boot assert (Phase 4 task 4.2)** validates `WINDOW ≤ min_recent_window` at boot time (warn if violated). Typo-safety: prefer Pattern A (`pydantic validation_alias`); recommend migrating from Pattern C to Pattern A if typo-safety becomes a concern. **Reconciliation across docs**: the same default values (`dry` / 3 / 3) appear in [`requirements.md`](./requirements.md), [`plan-overview.md`](./plan-overview.md), and [`architecture-recommendation.md`](./architecture-recommendation.md); a drift test asserts consistency. |
| **Decision tags** | [D2] (tri-state mode RESOLVED — default `dry`), [D4] (window N default + Pattern A vs B vs C), [D8] (RESOLVED via D2) |
| **Test notes** | Unit test `tests/unit/test_attestation_resolver.py` asserts: (a) mode env set to `dry`/`enforce`/`off` → resolver returns that mode; (b) mode env unset → resolver returns `dry` (D2 default); (c) mode env blank → resolver returns `off` (mirror WC-wake resolver shape); (d) mode env typo (e.g. `enabled`) → fails closed (if Pattern A) or fails open (if Pattern B/C); (e) window env set/unset → resolver returns configured value or 3; (f) restart-read: change env var, call `get_config()` in same process → returns cached value (no live flip). Integration test AC-7.6 verifies restart-read. |

### 4.2 — One-time boot log + O1 boot assert

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_resolver.py` (boot log line); `daemon/manager.py:InstanceManager.__init__` (boot log emission — sits next to the other Pattern C boot-log wrappers at lines 776–802) |
| **Description** | At module import time, emit one log line: `[INFO] attestation_resolver: mode=off\|dry\|enforce window=N bound=N attestation_enabled=true\|false`. Format mirrors the WC-wake boot log (`daemon/services/instance_messaging.py:114-191`). Operators grep this line to confirm config at startup. The line is emitted EXACTLY ONCE per startup (cached global ensures idempotence). **O1 — boot assert `WINDOW ≤ min_recent_window`**: at boot time, the resolver compares `window` against `min_recent_window` (the compaction floor — `daemon/compaction.py:1090` / `DEFAULT_CONTEXT_LIMIT` config). If `WINDOW > min_recent_window`, the boot log line carries a `WARN` prefix and an explanatory message ("`attestation_denied_count risk: WINDOW > min_recent_window; aggressive context pressure may fold the attestation tool_call`"). The assert is a boot-validation site (named: `attestation_resolver.boot_assert_window_within_compaction_floor`); it does NOT fail-closed (does not refuse to start) — it warns loudly so operators see the configuration risk. The default `WINDOW=3` matches the default `min_recent_window=3`, so the WARN does not fire under default config. |
| **Decision tags** | [O1] (boot assert) |
| **Test notes** | Integration test boots the daemon with each env var set/unset; asserts the boot log line contains the expected resolved values. `tests/integration/test_attestation_o1_boot_assert.py` boots with `WINDOW=5` (and `min_recent_window=3`) and asserts the WARN log line fires; boots with default `WINDOW=3` and asserts no WARN. AC-7.7 verifies the boot log announces resolved values; AC-7.8 verifies the O1 boot assert. |

### 4.3 — Wire the config into the scanner + gate (Phase 2 + 3)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_scanner.py`, `daemon/services/attestation_gate.py` (Phase 2 files); `daemon/repositories/instance/repository.py` (Phase 3 file) |
| **Description** | The scanner reads `resolver.window` (D4). The gate reads `resolver.mode` (D2 tri-state), `resolver.window` (D4), `resolver.deny_bound` (D5), `attestation_enabled: bool` (C2 — independent of `language_check_enabled`), and `scope_applicable: bool` (D3). The ledger operations (`increment_attestation_denied_count`, etc.) are skipped when `mode == "dry"` — NO counter change, NO nudge, NO flag change (zero side effects; only evaluation + log). **Nudge injection guard (N2)**: the in-graph nudge MUST fire ONLY when the gate returns `Decision.denied` (canonical enum per Phase 4 task 4.5). The nudge MUST NOT fire when the gate returns `Decision.terminal_after_bound` (escalation path — the instance is allowed to terminate; no continuation nudge); when the gate returns `Decision.allowed` or `Decision.allowed_legitimate_pending_wakeup` (allow path — no continuation needed); when the gate returns `Decision.dry_log` (dry mode — zero side effects). This guard is asserted by Phase 5 task 5.6 (which explicitly tests that `terminal_after_bound` does NOT inject a nudge) and by Phase 5 task 5.3 (which mocks `manager.enqueue_message.assert_not_called()`). The config is read at module import time (cached global) — restart required to change. |
| **Decision tags** | [D2], [D4], [D5] |
| **Test notes** | Unit test asserts: mode=`off` → gate returns `allow` regardless of scanner result (no counter change); mode=`dry` → gate evaluates + logs but does NOT change counter / nudge / flag; mode=`enforce` → full path active. Integration test verifies all knobs. |

### 4.4 — Dry-run mode semantics (allow END + ZERO side effects)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (Phase 2 file) |
| **Description** | **Per D2 / D8 (RESOLVED)**: when `mode == "dry"`, the gate EVALUATES (runs scanner, computes decision, reads R2 inputs) and LOGS the decision (`event=leader_completion_gate` with `decision=dry_log`) but does NOT execute the deny path. **Specifically**: (a) NO `increment_attestation_denied_count` (counter unchanged); (b) NO in-graph nudge injection (no `HumanMessage` added to `state.values['messages']`); (c) NO `completion_gate_escalated=true` (flag unchanged); (d) NO terminal denial (the gate returns `allow`, the original `should_continue` semantics are preserved, the leader turn ends normally). In dry mode, the gate is a **passive observer**: every gate decision is logged with full scanner diagnostics (window truncated? summary-seen? attestation present? pending children count? queued/expected wakeups count? deny-bound count?) but NO action is taken. **ZERO side effects** is the explicit semantic — there are no "deny-fires in dry". Operators use dry to adjudicate the false-positive rate before flipping to `enforce` after ≤2-week soak. **N2 nudge-injection guard (cross-reference task 4.3)**: dry mode emits `decision=dry_log`; the nudge injection branch is conditional on `decision == denied` only (NOT `terminal_after_bound`, NOT `dry_log`, NOT `allowed`, NOT `allowed_legitimate_pending_wakeup`). | |
| **Decision tags** | [D2] (tri-state default `dry`), [D8] (RESOLVED via D2 — allow + log only) |
| **Test notes** | Integration test AC-7.4 (mode=`enforce` → enforcement) + new dry-specific tests: `tests/integration/test_attestation_dry_mode.py` boots with `mode=dry`, runs a synthetic would-be-END with missing attestation, asserts (a) `event=leader_completion_gate` log emitted with `decision=dry_log` and full schema; (b) counter unchanged; (c) no nudge injected into `state.values['messages']`; (d) flag unchanged; (e) leader turn ends normally (terminal transition allowed). |
| **Test notes** | Integration test AC-7.4 (kill-switch ON → enforcement), AC-7.3 (kill-switch OFF → byte-equivalent baseline). Unit test asserts dry-run mode emits log but skips recovery. |

### 4.5 — Structured logging schema (R2 fields + O8 messages_scanned)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/services/attestation_gate.py` (Phase 2 file); new file `daemon/logging/event_signatures.py` or extend existing logger module |
| **Description** | **CANONICAL gate decision enum + log schema (single source of truth — referenced VERBATIM by phase2 task 2.2, phase3 task 3.3, phase5 tasks 5.2/5.4/5.5/5.16, requirements AC-3.3/AC-E2E-1b; DO NOT restate field-by-field elsewhere).** Every gate decision emits a structured log entry. The enum and schema are normative; every other task references this block, not a paraphrase.

**Canonical decision enum** (one of five values, every gate evaluation):
```
Decision ∈ {
  allowed,                              # meta-condition skip OR attested under enforce (terminal-status write proceeds)
  denied,                               # R2 deny under enforce (terminal-status write NOT performed; nudge injected)
  terminal_after_bound,                 # escalation path under enforce (allow terminal + completion_gate_escalated=true flag + counter reset)
  dry_log,                              # dry-mode evaluation: allow terminal + zero side effects (per D2/D8)
  allowed_legitimate_pending_wakeup,    # R2 allow under enforce — pending_children > 0 OR queued_or_expected_wakeups > 0 (nudge-flood kill per R2)
}
```

**Canonical schema fields** (every gate decision log entry; applies to all five enum values above; the structured logger emits one entry per gate evaluation, never omit any field):
```
event:                          "leader_completion_gate"
decision:                       <Decision enum above>
instance_id:                    str
attestation_present:            bool
denied_count:                   int
gate_location:                  str      # canonical value: "graph_end_candidate"
leader_prompt_version:          str      # from agents/leader/meta.json version field
pending_children:               int      # R2 input — sourced from bus.count_pending_for_target_sync(instance_id) behind NEW manager facade (phase2 task 2.3)
queued_or_expected_wakeups:     int      # R2 input — sourced from NEW helper over three next_retry_at tables + deferred/paused-held PENDING tasks (phase2 task 2.3)
attest_seen_outside_window:     bool     # O3 diagnostic — tool call present anywhere in full message list but NOT in last N
messages_scanned:               int      # O8 — actual scan depth; >0 confirms the scanner ran
scanned_window_size:            int      # the configured WINDOW (mirrors messages_scanned for log-volume predictability)
mode:                           Literal["off", "dry", "enforce"]
scanner_window_truncated:       bool     # diagnostic — was requested window larger than available AIMessage tail?
scanner_summary_seen:           bool     # diagnostic — was a compaction summary message encountered first? (D10(b) edge case)
```

**O8 messages_scanned>0**: every gate evaluation that ran (i.e. `attestation_enabled=True` AND `mode != "off"`) emits `messages_scanned: int > 0` — confirms the gate actually scanned something. If `messages_scanned == 0` for a non-empty message list, that's an anomaly (window truncated below the floor or `aget_state` returned empty); the log line surfaces it for diagnosis. (Phase 2 task 2.3 picks in-node `state["messages"]` for MVP — no `aget_state` thread-id-only discipline in MVP; that discipline is relocated to phase6.) **O8 unit assertion (replaces manual grep)**: add a unit test asserting the gate's config shape carries no `checkpoint_ns` key (the in-node pattern must NOT thread checkpoint_ns into the scanner config — this is the unit-level guard, not a grep). The escalation event (`event=leader_completion_gate_terminal_after_bound`) emits the same fields plus `last_denial_reason: str`. The C3 error logs (`event=leader_completion_gate_error`, `event=leader_completion_gate_db_error`) are emitted on the failure paths with the same schema fields plus `error_class: str`. The dry-mode observation (`event=leader_completion_gate` with `decision=dry_log`) emits the same fields with NO counter/nudge side effects. The structured logger uses the project's existing structured-logging facility (do not introduce a new logger). |
| **Decision tags** | [D8] (RESOLVED via D2 — dry log line format), [R2] (R2 fields), [O3] (O3 diagnostic field), [O8] (messages_scanned>0) |
| **Test notes** | Integration test AC-10.1 asserts 1000/1000 decisions logged with full schema (including R2 fields). AC-10.2 asserts escalation event is unique per instance (no double-fire). Unit test asserts log signature matches schema. O8 smoke test: synthetic empty-message scenario → assert `messages_scanned == 0` + WARN log line. |

### 4.6 — Operator runbook + promotion metrics

| Aspect | Detail |
|---|---|
| **Files touched** | `docs/setup.md` (extend); new section under "Kill-switches" or "Operational toggles" |
| **Description** | Document the three env vars + dry-run with deployment guidance: (a) ship default mode=`dry` (D2 RESOLVED); (b) flip mode=`enforce` after ≤2-week soak or on incident (WC-wake precedent); (c) recommended soak procedure: enable `dry` mode at ship, observe `event=leader_completion_gate` log lines with `decision=dry_log` (canonical enum per Phase 4 task 4.5) for false positives, then flip to `enforce`. Document the boot log line operators should look for, including the O1 WARN message ("`WINDOW > min_recent_window`"). Document the escalation flag and how to query for `completion_gate_escalated=true` instances postmortem. **Promotion metrics** (this task): a metrics emitter reports `dry_log_total` (canonical name), `dry_log_deny_predicate_total` (subdivision of `dry_log_total` with R2-deny predicate satisfied — i.e. would have denied under `enforce`; replaces the previous fuzzy counter name per CR-4), and `enforce_denied_total` — operators query these to adjudicate the dry→enforce flip. **The promotion metrics + operator runbook satisfy the instrumented dry-run** (the dry default at ship is the ship posture; metrics + runbook are the dry-run SOPs). This is NOT a pre-Phase-2 blocking activity — Phase 4 ships this; Phase 2 ships the gate with `mode=dry` already active. |
| **Decision tags** | [D2] (deploy plan + promotion metrics) |
| **Test notes** | Manual review. Drift test asserts the env var names + default values documented in `docs/setup.md` match the resolver implementation (`tests/integration/test_attestation_runbook_drift.py`). |

---

## Coupling

- **Tight with:** Phase 2 (scanner + gate read the resolver); Phase 3 (recovery reads the resolver for dry-run mode; observability reads the counter from Phase 3); Phase 5 (config + kill-switch tests).
- **Loose with:** Phase 1 (resolver + tool name share env var prefix convention but are independent).
- **Independent of:** none.

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | **Live flip assumed but not supported** (operators expect hot reload) | Medium (operator confusion) | Clear runbook documentation; restart-required messaging in `docs/setup.md` |
| 2 | **Cached global not invalidated on env var change** | Low (intended behavior — restart-read per C-2) | Document explicitly; integration test verifies |
| 3 | **Boot log line duplicated** (module imported twice in test process) | Low (log noise) | Idempotence guard in module; emit-once flag |
| 4 | **Dry-run mode silently ignored by gate code** (operators think it's enforcing when it isn't) | High | Phase 4 task 4.3 + 4.4: explicit `mode == "dry"` early-return at the gate; unit test asserts counter unchanged / no nudge / no flag change; integration test asserts `decision=dry_log` log line emitted + leader turn ends normally. |
| 5 | **Structured log schema drift** (developer adds field without updating schema) | Low (log search breaks) | Drift test on log signature; lint rule (recommend) |
| 6 | **Boot log line text drifts from runbook** | Low (operator confusion) | Runbook quotes the exact log line; grep test on docs/setup.md matches the code |
| 7 | **O1 boot assert ignored**: `WINDOW > min_recent_window` is silently accepted, leading to folded-attestation false-denies under context pressure | Medium | Phase 4 task 4.2: explicit boot-validation site `attestation_resolver.boot_assert_window_within_compaction_floor` emits a WARN log line (does not fail-closed). Integration test asserts WARN fires. |
| 8 | **O8 messages_scanned=0 anomaly unnoticed**: gate emits a `dry_log` decision with `messages_scanned=0` for a non-empty message list — indicates window truncated below the floor or `aget_state` returned empty | Low (smoke test for "gate is alive") | Phase 4 task 4.5: explicit `messages_scanned>0` assertion in the smoke test path; WARN log line on anomaly. |

---

## Rollback Story

This phase is reversible per resolver + observability:

1. **Resolver rollback:** remove `daemon/services/attestation_resolver.py`; gate (Phase 2) and ledger (Phase 3) revert to hardcoded defaults (`mode="enforce"`, `window=3`, `deny_bound=3`). The gate is now ALWAYS active regardless of env var. **Risk:** this is a partial deployment. Recommended rollback: roll back Phase 2 + Phase 3 callers to bypass the resolver and use hardcoded defaults; remove the resolver module.
2. **Boot log rollback:** remove the one-time log call + the O1 WARN emission; resolver becomes silent on import. Operators lose the audit trail at startup.
3. **Dry-run rollback:** remove the `mode == "dry"` early-return from the gate. The dry mode is ignored.
4. **Structured logging rollback:** revert to plain text logs. Log search across `event=leader_completion_gate` no longer works; observability degrades.
5. **Runbook rollback:** delete the new section in `docs/setup.md`. Operators lose documentation.

**Restart-read:** all changes require daemon restart. The resolver is the single config surface; rolling back the resolver means rolling back the entire feature to hardcoded behavior.

---

## Exit Criterion

This phase is done when:

- [ ] `tests/unit/test_attestation_resolver.py` passes (tri-state mode env resolution, blank→off parsing, restart-read)
- [ ] `tests/integration/test_attestation_o1_boot_assert.py` (new) passes (WARN fires when `WINDOW > min_recent_window`; no WARN under default config)
- [ ] Boot log line emitted on every daemon startup with the three resolved values (`mode`, `window`, `deny_bound`)
- [ ] `tests/integration/test_attestation_config.py` (Phase 5) passes (env var → config wiring)
- [ ] AC-7.1 (window N from resolver) verified
- [ ] AC-7.2 (window default = 3) verified
- [ ] AC-7.3 (mode=`off` → byte-equivalent baseline) verified
- [ ] AC-7.4 (mode=`enforce` → enforcement) verified
- [ ] AC-7.6 (restart-read: behavior does not change until restart) verified
- [ ] AC-7.7 (boot log line announces resolved values) verified
- [ ] AC-10.1 (every gate decision logged with full schema — incl. R2 fields + O8 `messages_scanned>0` + the canonical `Decision` enum from Phase 4 task 4.5) verified
- [ ] AC-10.2 (escalation event unique per instance) verified
- [ ] AC-10.3 (dry-mode would-have-denied schema — `decision=dry_log` with R2-deny predicate satisfied and R2 input fields present) verified
- [ ] AC-10.4 (gate_exception log entry on scanner exception — `event=leader_completion_gate_error` carries exception type, stack-trace summary, `instance_id`, `gate_location`, `error_class`; fail-open is a PATH, not a separate decision value) verified
- [ ] `tests/integration/test_attestation_dry_mode.py` (new) passes (dry mode: counter unchanged, no nudge, no flag change, leader turn ends normally, `decision=dry_log` log emitted per the canonical enum)
- [ ] `tests/integration/test_attestation_runbook_drift.py` (new) passes (env var names + default values in `docs/setup.md` match the resolver)
- [ ] Promotion metrics (`dry_log_total`, `dry_log_deny_predicate_total` — counts dry evals with R2-deny predicate satisfied, i.e. would have denied under `enforce`; replaces the previous fuzzy counter name; `enforce_denied_total`) emit and are queryable

The phase is the precondition for Phase 5 (the test matrix exercises the resolver across all ACs). The default mode at ship is `dry`; operators adjudicate the dry→enforce flip using the promotion metrics + runbook (NOT a pre-Phase-2 activity).