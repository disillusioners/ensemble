# Phase 1: Attestation Tool + Registration Seam + Leader Authz + Prompt Contract

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (revised in reconciliation pass)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase2-plan.md`](./phase2-plan.md), [`phase3-plan.md`](./phase3-plan.md), [`phase4-plan.md`](./phase4-plan.md), [`phase5-plan.md`](./phase5-plan.md), [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md), [`research-findings.md`](./research-findings.md)

---

## Objective

Ship a registered, opted-in, drift-tested attestation tool that the leader LLM can call, plus a prompt contract requiring the leader to call it before declaring done. This phase is **D1-independent** — it produces the artifact the gate (D1=B in-graph pre-END interception per R1) will scan for. It also includes the precondition compaction test flagged as severity=high in `technical-analysis.md`.

Entry criterion: D7 (tool semantics) and D3 (scope: leader-only) are decided by the architect; this phase implements those decisions. Defaults if unresolved: tool name `attest_completion`, no required args, idempotent, confirmation frame return shape, NOT in `PRIVILEGED_TOOL_CATEGORIES`, leader-only scope. **D6 (recovery source value) is DEFERRED-to-Phase-6** — the MVP deny path is the in-graph nudge, NOT `manager.enqueue_message`; D6 implementation lands with the recovery injector in Phase 6.

Exit criterion: `tests/unit/tools/test_upgrade_registration.py` passes with the new tool; `agents/leader/rule.md` `### Must` block + workflow.md mirror are in place; precondition compaction test characterizes the default-config safe zone. The prompt contract instructs the leader that the in-graph nudge ("The work is not yet finished — check current progress and continue.") is a continuation signal.

---

## Entry Criteria

- D7 (tool semantics) and D3 (scope: leader-only) are decided by the architect — this phase implements those decisions
- Phase 0 prerequisite: Phase 1 dependency on Phase 0 = none; this phase is the starting trunk
- Defaults if unresolved: tool name `attest_completion`, no required args, idempotent, confirmation frame return shape, NOT in `PRIVILEGED_TOOL_CATEGORIES`, leader-only scope
- **Note**: D6 (recovery source value) does NOT gate Phase 1 — the MVP deny path uses the in-graph nudge, NOT `manager.enqueue_message` (R1). D6 implementation lands with the Phase 6 recovery injector.

---

## Tasks

### 1.1 — Register the attestation tool category (10-step discipline)

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/tools/_tool_registry.py:106` (new `@register_tool_category("attestation")`); `daemon/tools/_tool_registry.py:23-78` (`DYNAMIC_TOOL_NAMES`); `daemon/tools/_tool_registry.py` (`KNOWN_TOOL_NAMES` regen); `daemon/tools/upgrade_tools.py:110-143` (10-step checklist) |
| **Description** | Follow the 10-step checklist in `daemon/tools/upgrade_tools.py:110-143`. Decorator-only registration is SILENTLY INVISIBLE — every step is mandatory. The new category name is `attestation`; `CATEGORY_MODULES` entry points at `daemon/tools/attestation.py`. |
| **Decision tags** | [D7] (name, args, idempotency, return shape) |
| **Test notes** | Drift test `tests/unit/tools/test_upgrade_registration.py` must pass; new test `tests/unit/tools/test_attestation_registration.py` asserts (a) `@register_tool_category` above `@tool`, (b) entry in `CATEGORY_MODULES`, (c) tool name in `KNOWN_TOOL_NAMES`, (d) `DYNAMIC_TOOL_NAMES` includes it. |

### 1.2 — Implement the attestation tool module

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/tools/attestation.py` (new) |
| **Description** | New file. Implements `attest_completion` per D7 resolution. Default shape (subject to architect): no required args; idempotent; returns `{"attested": true, "timestamp": "<iso>"}` confirmation frame. The tool body is a no-op aside from returning the success payload — the attestation is recorded by virtue of the tool call existing in `state.values['messages']`. If D7 picks structured args, add `summary: Optional[str]` and `mission_id: Optional[str]` (validated as non-empty if present). |
| **Decision tags** | [D7] (args, idempotency, return shape) |
| **Test notes** | Unit test invokes the tool directly; asserts return shape matches `{"attested": true, "timestamp": "<iso>"}` (or per D7). If structured args: arg validation tests for malformed input. |

### 1.3 — Add leader opt-in to `tools.allow`

| Aspect | Detail |
|---|---|
| **Files touched** | `agents/leader/meta.json:14-15` |
| **Description** | Add `"attestation"` to the `tools.allow` array. The existing 13 categories are: `instance`, `subtree_messages`, `subtree_status`, `self`, `project`, `help`, `image`, `knowledge`, `mcp`, `critical_notes`, `project_history`, `shared_meta_kv`, `question`. The new entry slots alphabetically (after `critical_notes`, before `image`... actually check order — likely alphabetical). Bump the meta.json version per project versioning discipline. |
| **Decision tags** | none (architectural decision: leader-only opt-in per C-4) |
| **Test notes** | Unit test loads `agents/leader/meta.json` via `get_version(id, tag) → get_resolved() fallback` (`daemon/tools/instance.py:4475-4477`); asserts `attestation` is in `tools.allow`. |

### 1.4 — Add prompt contract to `agents/leader/rule.md`

| Aspect | Detail |
|---|---|
| **Files touched** | `agents/leader/rule.md` (under `## Must`, add a new `### Must` block) |
| **Description** | House style: mandatory `### Must` block under `## Must` per `agents/leader/rule.md` convention. The contract draft text (subject to architect refinement) reads: <br>*"When your work for this mission is genuinely complete and you are about to be done, you MUST call the `attest_completion` tool. Do not declare done in plain text. If you receive a user message containing 'The work is not yet finished — check current progress and continue.', treat it as a real user instruction: review your current progress, complete the remaining work, and only then call `attest_completion`."* <br>**Source-of-message note**: in the MVP, this nudge is delivered in-graph by the gate node (same execution, checkpoint-durable; Phase 2 task 2.5 — the in-graph deny nudge wiring, per CR-1 alignment). In Phase 6 a post-soak backstop may also enqueue the same text via `manager.enqueue_message` with `source="attestation_recovery"` for the OS-2 cascade class; the leader does not need to distinguish the two — both render as user-authored and contain identical prose. |
| **Decision tags** | [D7] (tool name in contract text) |
| **Test notes** | Manual review + grep test asserts (a) `attest_completion` appears in a `### Must` block under `## Must`; (b) the nudge/recovery message text appears verbatim in the contract. |

### 1.5 — Mirror prompt contract in `agents/leader/workflow.md`

| Aspect | Detail |
|---|---|
| **Files touched** | `agents/leader/workflow.md` |
| **Description** | Mirror the contract at the workflow stage. Same text as rule.md but positioned where the leader sees it during dispatch-time instructions. The mirror ensures the contract appears in both prompt contexts. |
| **Decision tags** | [D7] (tool name in contract text) |
| **Test notes** | Manual review; grep test (same as 1.4). |

### 1.6 — Verify authz fail-closed for non-leader agents

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/tools/_auth.py` (no change — verification only); `daemon/tools/_tool_registry.py:101-103` (`PRIVILEGED_TOOL_CATEGORIES` — verify attestation is NOT in the list) |
| **Description** | Assert that non-leader agents (developer, reviewer, tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer, doc-writer) do NOT have `attestation` in their `tools.allow`. The 13 categories are listed per-agent; only leader gains the new category in v1. `PRIVILEGED_TOOL_CATEGORIES` stays at one entry (`system_upgrade`); attestation is NOT privileged (D7 sub-question resolved: NOT privileged). |
| **Decision tags** | [D7] (PRIVILEGED sub-question resolved as NOT) |
| **Test notes** | Unit test iterates all 13 non-leader agents; asserts `attestation` NOT in `tools.allow`. Integration test AC-9.2 verifies with `agents/developer/meta.json` as exemplar. |

### 1.7 — Compaction-spike precondition test

| Aspect | Detail |
|---|---|
| **Files touched** | `daemon/compaction.py` (no change — verification only); new test `tests/integration/test_attestation_compaction_precondition.py` |
| **Description** | Verify that under default config (`recent_message_window=10` groups, `min_recent_window=3` groups per `daemon/config.py:728-729`), the attestation tool call made in the last turn survives into the preserved tail. Verify the aggressive-path case (reduce tail to `min_recent_window=3`, attestation in group #4 from end) — characterize whether the scanner can still see the tool_call or whether the summary message hides it. This precondition is flagged severity=high in `technical-analysis.md` §CMP1 because it gates Candidate B and the D10(b) compaction decision. |
| **Decision tags** | [D1] (B path), [D10] (compaction mitigation choice) |
| **Test notes** | Integration test with synthetic 50-message state: (a) attestation in group #1 (most recent) → scanner sees it (default safe zone). (b) attestation in group #4 with reduced tail to 3 → characterize: does scanner see it via summary text? Brittle. (c) attestation in group #2 (just inside tail) → scanner sees it. Report results back to the architect before Phase 2 implementation begins. If (b) reveals a gap, D10(b1) aget_state pre-compaction becomes mandatory, NOT optional. |

---

## Coupling

- **Tight with:** Phase 2 (the scanner + gate read `tool_calls[i].name == "attest_completion"`; tool name resolution comes from this phase); Phase 5 (drift test + registration test).
- **Loose with:** Phase 6 (recovery injector backstop uses the same nudge text — same prose, same prompt contract reference).
- **Independent of:** Phase 3 (Phase 3 ledger / bound / escalation does not depend on the tool body); Phase 4 (config resolver does not depend on the tool).

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Tool name collision with existing tool | High (drift test fails, agent failure) | Pick a unique name; `attest_completion` is preferred per `decisions.md` §D7; drift test catches collision at registration time |
| 2 | Decorator-only registration (silent invisibility) | High (tool not visible at runtime) | Strict 10-step checklist enforcement; new `tests/unit/tools/test_attestation_registration.py` |
| 3 | `tools.allow` opt-in missing for leader | High (leader can't call tool) | Drift test enforces; manual review |
| 4 | Prompt contract not honored by leader LLM | Medium (false-positive deny every turn — bounded by R2 + counter reset) | Bounded retry + dry-run + prompt engineering (architect refines the contract text); R2 condition `pending_children == 0 AND queued/expected_wakeups == 0` ensures legitimate delegation turn-ends do NOT trigger denials |
| 5 | Compaction-spike reveals a gap (D10(b) gate) | High (D1=B path requires D10(b1) aget_state pre-compaction) | Phase 1 task 1.7 characterizes before Phase 2 commits to D1=B path |
| 6 | Prompt contract text drift between rule.md and workflow.md | Low (LLM sees one or the other; not catastrophic) | Mirror discipline: same text in both files; grep test asserts |
| 7 | `PRIVILEGED_TOOL_CATEGORIES` membership (D7 sub-question) | Low (security; opt-in via tools.allow is already sufficient) | Default: NOT privileged; if architect picks privileged, follow the precedent of `system_upgrade` membership |

---

## Rollback Story

This phase is fully reversible:

1. **Tool registration rollback:** remove `@register_tool_category("attestation")`, `CATEGORY_MODULES` entry, `DYNAMIC_TOOL_NAMES` entry, `KNOWN_TOOL_NAMES` regen. Drift test will fail at commit time if not regenerated; revert confirms cleanup.
2. **`tools.allow` rollback:** remove `"attestation"` from `agents/leader/meta.json:14-15`. The meta.json version bump means downstream instances inherit the rolled-back list on next dispatch.
3. **Prompt contract rollback:** delete the `### Must` block in `rule.md` and the mirror in `workflow.md`. No DB state to clean.
4. **Compaction test rollback:** delete `tests/integration/test_attestation_compaction_precondition.py`. No production code changed.
5. **In-graph nudge** (Phase 2) is unaffected — the nudge is a graph-node-emitted `HumanMessage` regardless of whether the tool is registered. The leader receives the nudge and either calls the tool (if registered) or proceeds with the continuation signal anyway. The gate's nudge path becomes a no-op end-to-end once the tool is unregistered, since the leader cannot call a tool it doesn't have and the nudge is just a continuation prompt. **Phase 6 recovery injection** (the `manager.enqueue_message` backstop) is also unaffected — it enqueues the same text regardless of tool state.

**Restart-read:** all changes are restart-required (per C-2). No live flip. The daemon must be restarted for any rollback to take effect.

---

## Exit Criterion

This phase is done when:

- [x] `tests/unit/tools/test_upgrade_registration.py` passes with the new attestation category
- [x] `tests/unit/tools/test_attestation_registration.py` (new) passes
- [x] `agents/leader/meta.json` has `attestation` in `tools.allow`; meta.json version bumped
- [x] `agents/leader/rule.md` has the `### Must` block under `## Must` with the contract text
- [x] `agents/leader/workflow.md` has the mirror contract
- [x] Unit test asserts all 13 non-leader agents do NOT have `attestation` in `tools.allow`
- [x] `tests/integration/test_attestation_compaction_precondition.py` (new) characterizes the default-config safe zone AND the aggressive-path case; report goes to the architect
- [x] Boot log (added in Phase 4) announces the new tool category as `present` (sanity)

The phase is the precondition for Phase 2; Phase 2 cannot start until the compaction-spike result is known (gates D10 mitigation choice for D1=B path).