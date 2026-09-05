# Research Findings: Leader Completion Attestation

> RECONCILED 2026-09-05 to the post-architecture design (architecture-recommendation.md + leader rulings R1/R2). Pre-architecture design details elsewhere in this digest are historical context; where they conflict with decisions.md, decisions.md wins.

Date: 2026-09-05
Author: planner[v2] via plan-creation worker
Sources: Two explorer reports + technical-analysis worker verification + this worker's compaction-spike verification
Status: Reference for the implementation plan; compiled into `plan-overview.md` decision points.

---

## Purpose

Assemble the architecture of the leader-completion path, the precedents the plan must honor, and the patterns / constraints the implementation must respect. This is the "what does the codebase look like today" companion to `requirements.md` (the "what does the user want") and `technical-analysis.md` (the "which integration points are candidates"). The plan `plan-overview.md` is the "what to build, in what order, with what risks".

---

## 1. Completion-Path Surface Map

The leader → COMPLETED transition currently fires from multiple surfaces. The plan must coexist with each, not replace them.

| Surface | Where the terminal write happens | Atomic guard | Touched by plan? |
|---|---|---|---|
| **child_reports atomic UPDATEs** | `daemon/services/child_reports.py:1983` `_process_child_completion_db_sync`; three UPDATE sites at `:2545`, `:2737`, `:2895` | `WHERE status NOT IN (PAUSED, COMPLETED, ERROR)` + rowcount == 0 → skip side effects | Pre-D1=A option (REJECTED in favor of D1=B). Gate function reusable as building block. |
| **job_feedback_observer Step 2** | `daemon/services/job_feedback_observer.py:3083` `_finalize_job_db_sync`; Step 2 unconditional terminal re-stamp at `:3703-3758` | None (unconditional re-stamp over non-terminal incl. just-revived RUNNING) | Pre-D1=E option (REJECTED in favor of D1=B). Defense-in-depth WHERE clause retained as a hardening layer under D1=B. |
| **In-graph `should_continue` END routing** | `daemon/graph.py:2462-2533` original; `:2707-2734` `create_should_continue` wrapper; wiring `:6463` | n/a (graph routing, not DB) | **Yes** — D1=B (in-graph interception) is the chosen design (leader ruling R1). The plan wraps this. |
| **Graph END → finalize → mission terminal** | `daemon/services/job_feedback_observer.py:3083` after graph commits END | Same as Step 2 above | Indirectly — graph END triggers finalize; gate either blocks END or blocks finalize |
| **error_reporting path** | `daemon/services/error_reporting.py:319` | n/a | Out of scope |
| **terminate_instance path** | `daemon/services/instance_lifecycle.py:3979-3988` | n/a (operator-initiated) | Out of scope per FR / G12 (operator termination bypasses gate) |
| **LangGraph graph END** | n/a (graph itself writes no DB status) | n/a | The plan does not modify this. |

### Critical line-citation correction

The technical-analysis worker verified the actual write sites against `git log -L` on the introducing commit. Line numbers drift as code is edited; **HEAD-truth citations** (verified 2026-09-05):

- **child_reports atomic UPDATEs:** `:2545`, `:2737`, `:2895` (was reported as `:2566/:2756/:2916` by earlier explorer comments — those are stale comment-line drift; the UPDATE statements themselves are at the corrected line numbers).
- **job_feedback_observer Step 2:** `:3703-3758` (unconditional `instance.status = terminal_status` when not already terminal).
- **Revive semantics:** `daemon/services/instance_messaging.py:1867-1909` (RUNNING for {IDLE, WC, COMPLETED, TERMINATED, ERROR, FAILED}; PAUSED excluded; single commit).
- **Origin stamp:** `daemon/services/instance_messaging.py:1685-1704` (was reported as `:1310-1319` in the historical / LESSONS note — drifted; current code is `:1685-1704`).
- **Compaction defaults:** `daemon/config.py:728-729` (`recent_message_window: int = 10`; `min_recent_window: int = 3`).
- **Default context limit:** `daemon/compaction.py:1090` = 700,000 (per `e14f09f9`).

All other citations in `technical-analysis.md` and `decisions.md` were verified against HEAD.

---

## 2. In-Graph END Interception Precedent (Candidate B)

There is **exactly one** existing in-graph END-interception pattern in the codebase. The plan uses this as the primary precedent for D1=B.

| Element | File:Line | What it does |
|---|---|---|
| Original `should_continue` | `daemon/graph.py:2462-2533` | Returns `END` when no `tool_calls`; routes to `tools` otherwise |
| `create_should_continue` wrapper | `daemon/graph.py:2707-2734` | Translates `END` → `"end_candidate"` and routes through a language_check node before allowing real END |
| `language_check` node | `daemon/graph.py:6463` (wiring) | Optional node, opt-in via `language_check_enabled=True` |
| `HumanMessage` reminder injection | `daemon/graph.py:2666-2685` | If language_check fails, injects a `HumanMessage` with `language_check_reminder=True` additional_kwargs marker and routes back to `agent` |
| `additional_kwargs` marker pattern | `daemon/graph.py:2666-2685` | Convention for in-state reminder/nudge messages that survive next graph tick |

**Why this matters for the plan:** the attestation gate, if it goes in-graph (D1=B), must compose with the language_check wrapper. Two `create_should_continue`-style wrappers compose mechanically but the conditional-edges table must be re-validated. The plan's wiring task must:

1. Verify that wrapping `create_should_continue` with another `create_attestation_should_continue` produces the correct edge table.
2. Confirm the order (language_check first → attestation second, or reverse) — language_check is cheap, attestation is one tool-call check; recommended order: language_check → attestation (cheapest fail first).

---

## 3. Loop-Breaker Precedent (Bounded-Retry Counter)

The plan's bounded-retry counter is modeled directly on the loop-breaker.

| Element | File:Line | What it does |
|---|---|---|
| `max_repairs` cap | `daemon/graph.py:1840-1847` | Threshold for loop-breaker; once exceeded, halt repairs and let loop continue |
| Counter auto-reset | `daemon/graph.py:1836-1837` | Auto-reset on certain state transitions |
| LoopDetector backwards scan | `daemon/graph.py:960-1054` (alias `:1037-1044`) | Walks backwards through messages looking for repeated tool-call patterns |
| `_loop_breaker_state` cleanup | `daemon/manager.py:3734`, `:3798`, `:8548` | Cleanup in 3 paths to avoid stale-trip on new instance |

**Implication for the plan:** the attestation denied-count counter uses **row-scoped DB columns** (`denied_count`, `completion_gate_escalated`) with **reset-on-allow** semantics (an `attest_completion` tool call succeeds → counter zeroed) plus **reset-on-terminal_after_bound** semantics (a terminal commit lands past the bound → counter zeroed). The in-memory `_loop_breaker_state` precedent (3 pop sites at `daemon/manager.py:3734`, `:3798`, `:8548`) does **not** apply to DB columns — DB rows persist across instance reuse, so the reset lives in the writer paths, not in a parallel cleanup pattern. See `decisions.md` D5 (RESOLVED) and `architecture-recommendation.md`.

---

## 4. Revive Semantics vs Observer Step 2 Race

The single most subtle race in this feature.

- **Revive:** `daemon/services/instance_messaging.py:1867-1909`. When `enqueue_message` targets an instance in {IDLE, WC, COMPLETED, TERMINATED, ERROR, FAILED}, it auto-transitions to RUNNING in a single commit. PAUSED is exempt.
- **Observer Step 2:** `daemon/services/job_feedback_observer.py:3703-3758`. Unconditional `instance.status = terminal_status` when not already terminal.

**Race scenario (D1=B, chosen per R1):** if the leader is COMPLETED (revive enqueued), the recovery message is enqueued. The observer Step 2 fires between enqueue and revive-commit. Result: observer stamps terminal over the just-revived RUNNING.

**Mitigation (D1=B, the chosen design per R1):** D1=B runs **before** the END commit (pre-END gate), so observer Step 2 cannot fire after the gate returns DENY. This is the "zero race with observer Step 2" property. The MVP deny path is an in-graph checkpoint-durable `HumanMessage` nudge (R1); durable `manager.enqueue_message` recovery is a post-soak backstop relocated to `phase6-fastfollow-plan.md`.

**Mitigation (D1=E, historical alternative — REJECTED in favor of D1=B):** the observer-path gate must check `instance.status` AFTER acquiring the session lock and use a defense-in-depth `WHERE status NOT IN (...)` clause to avoid clobbering a just-revived instance. The defense-in-depth UPDATE pattern from `child_reports.py:1983` is the precedent. Retained here as a hardening reference under D1=B.

**Mitigation (D1=A, historical alternative — REJECTED in favor of D1=B):** same defense-in-depth UPDATE pattern, applied to the conditional UPDATE itself. Retained here as a hardening reference under D1=B.

---

## 5. Recovery Message Authorship (Origin Stamping)

Two paths exist; both are fragile and well-known.

| Path | File:Line | Behavior | Side effects |
|---|---|---|---|
| **Default else-branch** | `daemon/services/instance_messaging.py:1685-1704` | Stamps `MessageType.HUMAN.value` for any source NOT matching `internal_*:` prefix | None (default path) |
| **`source="api"`** | `daemon/manager.py:3159-3197` | Arms user-origin window | SSE notification, message-id assignment, audit log entry |

**Known deferred defect:** the else-branch stamps HUMAN for ALL internal callers (cascade_resume, internal_invoke_and_wait, attestation_recovery). Anti-forgery rests on caller discipline. P2.2 plans a `USER_ORIGIN_SOURCES` whitelist.

**Decision required (D6):** the plan tags this with [D6] and asks the architect to choose:
- `"api"` — zero code change, default else-branch works. Side effects: SSE, audit, message-id assignment. Mixes with real user traffic.
- New prefix (e.g., `"attestation_recovery"`) — clean audit, distinct from user traffic. May need `_prepare_enqueued_message` whitelist update.
- P2.2 whitelist participation — cleanest, but depends on P2.2 landing first.

**Mandatory constraint (NFR-6):** recovery message text MUST be a server-authored constant, NEVER echo of any leader-supplied or user-supplied content. The proposed constant is:
> *"The work is not yet finished — check current progress and continue."*

**Mandatory constraint (C-6 / C-9):** recovery message MUST NOT use `[SYSTEM NOTE: ...]` data-frame convention (`daemon/graph.py:216-224`). The leader hallucinated precisely from system-framed reports; recovery must read as user-authored prose.

---

## 6. Tool Registration Discipline (10-Step Checklist)

Decorator-only registration is SILENTLY INVISIBLE. The 10-step checklist is mandatory.

| Step | File:Line | What |
|---|---|---|
| 1 | `daemon/tools/_tool_registry.py:454-493` | `@register_tool_category("attestation")` ABOVE `@tool` decorator |
| 2 | `daemon/tools/_tool_registry.py:454-493` | `CATEGORY_MODULES` entry pointing at the new module |
| 3 | `daemon/tools/_tool_registry.py:23-78` | `DYNAMIC_TOOL_NAMES` regen |
| 4 | `daemon/tools/_tool_registry.py` | `KNOWN_TOOL_NAMES` regen (drift test fails if skipped) |
| 5 | `daemon/tools/instance.py:1752` | `tools.extend(create_instance_tools(...))` in `create_instance_tools()` |
| 6 | `daemon/tools/_auth.py` | Fail-closed authorization check |
| 7 | `agents/leader/meta.json:14-15` | `tools.allow` opt-in for leader agent |
| 8 | `agents/leader/meta.json` | `version` bump (meta.json versioning discipline) |
| 9 | `tests/unit/tools/test_upgrade_registration.py` | Drift test must pass |
| 10 | `tests/unit/tools/test_attestation_registration.py` (new) | New dedicated test |

**Authz path:** `daemon/tools/_auth.py` fail-closed. Absent category = unavailable. Non-leader agents (developer, reviewer, tidier, etc.) do NOT see the attestation tool.

**Version-tag resolution:** `get_version(id, tag) → get_resolved() fallback` (`daemon/tools/instance.py:4475-4477`). All meta lookups MUST use `get_version()` with `get_resolved()` fallback (per `agents/ensemble` Version Tag Tool Resolution Fix pattern).

**Privileged category:** `daemon/tools/_tool_registry.py:101-103` `PRIVILEGED_TOOL_CATEGORIES` currently lists only `system_upgrade`. Whether attestation belongs here is D7's sub-question. **Recommendation: NOT privileged** — opt-in via `tools.allow` is sufficient and consistent with how every other leader tool is gated.

---

## 7. Mode-Config Pattern (Tri-State Env; D2 RESOLVED)

The new mode config is a single tri-state env, not a boolean kill-switch. Pattern C is the chosen resolver style (module env resolver + cached global + one-time boot log); the same pattern already powers WC-wake, so reuse is mechanical.

| Pattern | Where | Pros | Cons |
|---|---|---|---|
| **A** — pydantic `validation_alias` + explicit `load_config` resolver | `daemon/config.py:805-844`, `:2155-2215` | Typo-safe; canonical precedence (env > legacy alias > yaml > default) | More code |
| **B** — dual-read cfg AND env | `daemon/config.py:463-506` | Simpler | No typo-safety |
| **C** — module env resolver + cached global + one-time boot log | `daemon/services/instance_messaging.py:114-191` (WC-wake precedent) | One-time boot log; canonical for the new mode config | Cached global requires care |

**Recommendation (D2 RESOLVED):** Pattern C, matching the WC-wake precedent. Reasons:
1. WC-wake precedent established the boot-log + cached-global pattern; consistency matters.
2. The one-time boot log is required by FR-12 / NFR-9; Pattern C produces it natively.
3. Typo-safety is less critical when the env name is documented in the deploy runbook.

**Default value (D2, RESOLVED):** single tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE=off|dry|enforce`, default `dry`. The tri-state replaces the rejected boolean kill-switch — `off` IS the disable, `dry` records denied-count + would-deny decisions without enforcing, `enforce` blocks the END route on deny. See `decisions.md` D2 (RESOLVED). There is no separate `ENSEMBLE_LEADER_ATTESTATION_ENABLED` boolean env.

---

## 8. Sweep Patterns (Reference Patterns for Post-Soak Backstop)

The post-soak backstop (relocated to `phase6-fastfollow-plan.md` per architecture ruling) MAY model on these existing sweep services if a sweep-style design is chosen for the backstop; the MVP does not require a sweep.

| Service | File:Line | Pattern |
|---|---|---|
| `ReportDeliveryRecoveryService` | `daemon/services/report_delivery_recovery.py:207` | 5 lanes; per-lane kill-switch; sweep cadence |
| `WaitingChildrenWatchdog` | `daemon/services/waiting_children_watchdog.py:312` | Hourly nudge-only; provenance `system:watchdog`; scheduling via `api.py:577-620` |
| Per-lane kill-switch config | `daemon/config.py:1107-1185` | Each lane has an `enabled: bool` env-resolved toggle |
| Wiring | `daemon/manager.py:6093-6250` | Sweeps instantiated in manager constructor |

**Pre-existing scheduling infra** is reusable. The MVP is the in-graph HumanMessage nudge (leader ruling R1, `phase1-plan.md`); sweep-style backstop coverage lives in `phase6-fastfollow-plan.md` per architecture ruling.

---

## 9. Scanner Mechanics

The scanner is the **only** deterministic way to confirm a real tool was invoked (vs. the LLM claiming it was). It is pure and unit-testable.

| Element | File:Line | What |
|---|---|---|
| `aget_state` pattern | `daemon/services/instance_messaging.py:1258`, `:1292` | Read `state.values['messages']` from the LangGraph checkpoint |
| `AIMessage.tool_calls` shape | `daemon/graph.py:995-1005` | `[{"name": str, "args": dict, "id": str}, ...]` |
| LoopDetector backwards walk | `daemon/graph.py:1037-1044` | Walk from end of messages; first hit of pattern wins |

**Proposed scanner signature (D10-resolved):**
```python
def scan_for_attestation(messages: list[BaseMessage], window: int = 3, tool_name: str = "attest_completion") -> tuple[bool, list[dict]]:
    """Return (attested, diagnostic_detail).
    
    Window semantics: scan the LAST `window` AIMessages (regardless of intervening HumanMessage/ToolMessage).
    Tool-name match: any tool_calls[i].name == tool_name counts as attested.
    Diagnostic_detail: list of {index, tool_call_names, attestation_present} per AIMessage inspected.
    """
```

---

## 10. Inter-Report Gap Premature-Finalize (Out-of-Scope Bug Class)

The plan does NOT fix this. Documented here so the architect and reviewer know the boundary.

- **Symptom:** during the gap between processing one child report and the next arriving, the bus gate and pending-tasks gate can both pass — premature finalize window.
- **Different root cause from leader hallucination:** the bus gate + pending-tasks gate pass when there's no in-flight work AT THE MOMENT the gate fires, but a child report is en route. The leader is not hallucinating; the system is racing.
- **Why this plan doesn't fix it:** the user's bug report is specifically about leader hallucination; the inter-report gap is a separate completion-path vulnerability.
- **Detection:** if a leader completes successfully but a child report arrives seconds later, the mission may have been finalized prematurely. This is the SAME user-visible symptom as leader hallucination but different in mechanism.
- **Recommendation:** separate plan/feature for the inter-report gap; out of scope for `leader-completion-attestation`.

---

## 11. Compaction-vs-Tick Visibility (Severity=High Precondition)

Verified 2026-09-05 by this plan worker. The compaction cliff risk that the technical analysis flagged at severity=high is real but bounded under default configuration.

| Compaction setting | File:Line | Default |
|---|---|---|
| `recent_message_window` | `daemon/config.py:728` | **10** boundary groups preserved verbatim |
| `min_recent_window` | `daemon/config.py:729` | **3** groups (progressive reduction floor) |
| `DEFAULT_CONTEXT_LIMIT` | `daemon/compaction.py:1090` | **700,000** tokens |

**Behavior:** the preserved tail is **kept as-is** (verbatim HumanMessage/AIMessage/etc.), not summarized. The compaction doc envelope (line 376, line 917 "preserved verbatim") replaces only the older groups.

**Implication for the attestation scanner:**
- Default config (10-group tail, N=3 message window): the scanner sees post-compaction AIMessages for any attestation tool call made in the last ~10 groups. **Safe.**
- Aggressive context pressure (reduces tail to min_recent_window=3): if the attestation falls outside the 3-group floor AND the leader took an extra turn after attesting, the attestation gets folded into the summary message. **Scanner misses.** This is the severity=high cliff.
- Default 700k context limit makes the aggressive case unlikely unless the leader has thousands of turns.

**Mitigation options (D10):**
- **(b1) aget_state pre-compaction:** read state before compaction runs. Latency cost on hot path; reliability cost on concurrency.
- **(b2) Compaction preserves tool_call shape:** currently the preserved tail is verbatim, so this is already true for the tail. Only becomes a problem when context pressure reduces the tail.
- **(b3) Scan summary text:** brittle.

**Recommended plan posture:** trust default config in Phase 1 (rely on recent_message_window=10 covering N=3 scanner). Add an **integration test that simulates context pressure** (mock compaction reducing to min_recent_window=3, attestation in group #4) to characterize behavior. The architect picks the D10 mitigation in Phase 2 if the test reveals a gap.

---

## 12. JAFP (Job-As-Front-Primitive) — Post-Soak Recovery Backstop Must Not Mint a JobItem

Internal paths (send_message, cascade-resume, reports, recovery) use `enqueue_message` only, NOT `JobItem`. This is a hard rule. **Post-architecture note:** the MVP deny path is the in-graph checkpoint-durable `HumanMessage` nudge (leader ruling R1, captured in `phase1-plan.md`). The durable `manager.enqueue_message` recovery described below is the **post-soak backstop**, relocated to `phase6-fastfollow-plan.md` per architecture ruling. The JAFP discipline itself still applies to whichever path lands where.

- **Public entry points create JobItem** (4 entry points).
- **`enqueue_message`** writes `MessageQueue` + `Task` in a single transaction (`daemon/services/instance_messaging.py:1960-2073`).
- **Post-soak backstop — Recovery message → `manager.enqueue_message`** (`daemon/manager.py:6530-6626`) → `InstanceMessagingService.enqueue_message` (`daemon/services/instance_messaging.py:1960-2073`). NOT the MVP path; see banner above and `phase6-fastfollow-plan.md`.

**Verification:** integration test asserts NO `JobItem` row exists for recovery messages (AC-4.4) — applies to the backstop path under `phase6-fastfollow-plan.md`.

**Facade-forwarding discipline:** if a new kwarg is added to `InstanceMessagingService.enqueue_message` (e.g., `attestation_origin`), `manager.py:6530-6626` MUST forward it, AND a real-dispatch integration test asserting the intended exception type must be added. Precedent: `tests/unit/test_manager_enqueue_message_work_id_required.py` + `tests/integration/test_job_driven_enqueue_work_id_facade.py`.

---

## 13. Leader Prompt-Contract Precedent

The plan's prompt contract (Phase 1 task: add `### Must` block to `agents/leader/rule.md`) follows the existing must-call-tool precedent.

| Precedent | File | Mechanism |
|---|---|---|
| `planner/tidier` `skill_feedback` contract | agents/planner/rule.md, agents/tidier/rule.md | `### Must` block under `## Must` requiring the agent to call `skill_feedback` after consuming a skill |
| Leader workflow.md mirroring | `agents/leader/workflow.md` | Mirror of rule.md contract into the dispatch-time instructions |

**Recommendation for the attestation contract:** mirror the same pattern:
- `agents/leader/rule.md` gains a `### Must` block under `## Must`.
- `agents/leader/workflow.md` mirrors the contract at the workflow stage.
- Text draft (subject to architect refinement):
  > *"When your work for this mission is genuinely complete and you are about to be done, you MUST call the `attest_completion` tool. Do not declare done in plain text. If you receive a user message containing 'The work is not yet finished — check current progress and continue.', treat it as a real user instruction: review your current progress, complete the remaining work, and only then call `attest_completion`."*

---

## 14. WC-Wake Coexistence

The new mode config (`ENSEMBLE_LEADER_ATTESTATION_MODE`) must not interfere with the existing WC-wake kill-switch.

| Setting | File:Line | Default | Behavior |
|---|---|---|---|
| `ENSEMBLE_WC_WAKE_ENQUEUE` | `daemon/services/instance_messaging.py:114-191` | OFF | Operator flips ON after ≤2-week soak or on incident |
| `ENSEMBLE_LEADER_ATTESTATION_MODE` | (new) | tri-state `off\|dry\|enforce`, default `dry` (D2 RESOLVED) | Pattern C: module env resolver + cached global + one-time boot log |

**Coexistence:** orthogonal concerns. WC-wake is about routing lanes for wake-up injection; attestation is about completion gate. No shared state. No shared config surface. No shared boot log (each has its own one-time boot log line).

---

## 15. Blind Spots and Deferred Risks

The plan must acknowledge but not fix:

- **Inter-report gap premature-finalize** (separate bug class). The bus gate + pending-tasks gate can both pass on a hallucinated report between processing one child report and the next. Out of scope per `requirements.md` §OS-2.
- **Origin-stamping defect** (deferred per P2.2). Plan uses the default else-branch; P2.2's `USER_ORIGIN_SOURCES` whitelist is a separate plan. Plan does not fix the defect.
- **Per-mission / per-tree attempt counting** (out of scope per OS-4). Per-instance only.
- **Live-flip mode config** (out of scope per OS-5). Restart-only — the tri-state env is resolved once at boot per Pattern C.
- **Cross-instance attestation coordination** (out of scope per OS-8). Leaders are per-instance.

---

## 16. Test Infrastructure Reuse

The plan reuses the project's test conventions.

| Convention | Where | What the plan does |
|---|---|---|
| Worktree-based regression proof | `tests/integration/test_attestation_recovery_flow.py` | If the in-graph MVP (R1) regression, copy to pre-fix worktree; expect original END-bypass failure mode |
| File-backed SQLite | integration tests | `tmp_path` + `NullPool` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=10000` |
| Full-partition attribution | any pass-at-base test | Re-run FULL partition in context-matched scratch worktree at base |
| Mock-migration checklist | recovery path mocks | If escalation to `enforce=True`, grep for mocked `enqueue_message` returns; migrate mocks to real `work_id=task.work_id` |
| Test-blindness fix | recovery path | Real-dispatch integration test with DB read-back + kwargs-CONTENT assertions |

---

## References

- **Companion docs:** [`requirements.md`](./requirements.md), [`technical-analysis.md`](./technical-analysis.md), [`decisions.md`](./decisions.md)
- **Architecture blueprint:** `agents/ensemble` core architecture doc (Core Architecture section)
- **Tool registration:** `daemon/tools/_tool_registry.py:454-493`; `daemon/tools/upgrade_tools.py:110-143`; `tests/unit/tools/test_upgrade_registration.py`
- **Completion writes:** `daemon/services/child_reports.py:1983`, `:2545`, `:2737`, `:2895`
- **Observer finalize:** `daemon/services/job_feedback_observer.py:3083`, `:3703-3758`; `:259-277` gate_deferred; `:1698` re-arm
- **In-graph END interception:** `daemon/graph.py:2462-2533` should_continue; `:2707-2734` create_should_continue; `:6463` wiring; `:2666-2685` HumanMessage reminder; `:414-490` report-injection claim machine
- **Loop-breaker:** `daemon/graph.py:1037-1044`, `:1836-1847`; cleanup `daemon/manager.py:3734`, `:3798`, `:8548` (3 sites, per architect ruling D5)
- **Revive semantics:** `daemon/services/instance_messaging.py:1867-1909`
- **Recovery message path:** `daemon/manager.py:6530-6626` facade; `daemon/services/instance_messaging.py:1960-2073` service; `:1685-1704` source→HUMAN stamp; `:114-191` WC-wake env resolver + boot log
- **Mode-config resolver patterns:** `daemon/config.py:805-844`, `:2155-2215` (A); `:463-506` (B); `:1107-1185` (per-lane toggles); `daemon/services/instance_messaging.py:114-191` (C — chosen)
- **Tool-call mechanics:** `daemon/graph.py:995-1005` AIMessage.tool_calls; `:1037-1044` LoopDetector
- **`[SYSTEM NOTE: ...]` data-frame convention:** `daemon/graph.py:216-224` (MUST NOT be used for recovery)
- **Compaction:** `daemon/compaction.py:1090` DEFAULT_CONTEXT_LIMIT; `:1390-1410` group building; `:728-729` `daemon/config.py` recent_message_window + min_recent_window
- **JAFP:** `daemon/services/instance_messaging.py:1960` enqueue_message; `daemon/manager.py:6530-6626` facade
- **Facade-forwarding:** `tests/unit/test_manager_enqueue_message_work_id_required.py`; `tests/integration/test_job_driven_enqueue_work_id_facade.py`
- **Sweeps:** `daemon/services/report_delivery_recovery.py:207` 5-lane; `daemon/services/waiting_children_watchdog.py:312` hourly; `daemon/config.py:1107-1185` per-lane kills; `daemon/manager.py:6093-6250` wiring