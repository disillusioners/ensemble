# Decisions: Governor Council-Manager — Architecture & Trade-offs

> **Revision 3 (2026-07-25):** D9 revised — degraded quorum (1 result → degraded notice, not fail) + tiered deadlines (30min soft / 1h hard cap with governor-extension judgment). See "Revision Log" at bottom.
> **Revision 2 (2026-07-25):** All 6 critical issues from review verified against source. Decisions D4, D7, D8, D9 added/updated.

---

## D1: Tool Approach — `spawn_councilor` (Option B) ✅ RECOMMENDED

### The Question

The governor must spawn councilors with an **explicit, validated model** to eliminate silent-fallback errors. Two approaches:

- **Option A**: Make `model` a REQUIRED parameter on the existing `spawn_instance` tool.
- **Option B**: Create a new dedicated `spawn_councilor` tool with `model` as REQUIRED.

### Option A — Modify `spawn_instance`

**How it would work:** Add `model` to a required-params variant or add a `strict_model: bool` flag.

| Pros | Cons |
|------|------|
| No new tool to maintain | `spawn_instance` is used by ALL agents (leader, wanderer, etc.) — making `model` required breaks every existing caller |
| Single code path | Adding a `strict_model` flag creates branching complexity inside a critical tool |
| — | Silent-fallback is an intentional contract of `spawn_instance` (other agents rely on the graceful degradation). Changing it is a breaking change. |
| — | `_format_model_fallback_notice` exists precisely because the system tolerates invalid models gracefully. Inverting this is invasive. |

**Verdict: REJECTED.** Breaks the silent-fallback contract that the rest of the system depends on. Too invasive for a general-purpose tool.

### Option B — New `spawn_councilor` Tool ✅

**How it works:** A new tool defined **inside `create_instance_tools()`** (per C5 fix — see D7 context). It reuses all `spawn_instance` internals (`manager.spawn_instance`, `_resolve_model_override`, `_format_model_fallback_notice`) but wraps them with **strict validation semantics**:

```python
@register_tool_category("council")
@tool(args_schema=SpawnCouncilorInput)
async def spawn_councilor(
    councilor_agent_id: str,   # REQUIRED — validated against AgentRegistry
    model: str,                # REQUIRED — validated against allowed_models, RAISES on invalid
    instance_name: str | None = None,
    project_id: str | None = None,
) -> str:
    ...
```

| Pros | Cons |
|------|------|
| REQUIRED params = compile-time guarantee the agent cannot forget the model | New tool to register + document |
| Strict validation (raises) = zero silent-fallback errors — meets the "reduce errors to zero" goal | Slight code duplication of the spawn wiring (~15 lines) |
| Zero impact on existing `spawn_instance` callers | — |
| Governor-specific semantics (councilor naming, council context) fit naturally | — |
| Reuses 100% of the lifecycle internals — no new validation/model-resolution logic | — |
| User explicitly stated preference for Option B | — |

**Verdict: ADOPTED.** Clean separation, strict guarantees, zero blast radius on existing tools.

### Implementation Detail (for Phase 2)

**⚠️ C5 CORRECTION:** The new tool MUST be defined **inside `create_instance_tools()`** (lines 679-1314 of `daemon/tools/instance.py`), as a closure — exactly like `spawn_instance` (lines 706-808), `send_message` (813-1000), etc. There is **no per-category factory dispatch**. A standalone `create_council_tools()` factory would never be invoked. See Phase 2 for the corrected wiring.

**⚠️ C3/C4 CORRECTIONS:** The validation must check return values (not rely on exceptions):
- `_check_team_membership()` returns `str | None` (never raises) → check `if err is not None: raise ValueError(err)`
- `resolve_to_id()` returns `str | None` (never raises) → check `if resolved is None: raise ValueError(...)`

---

## D2: Aggregation Strategy — LLM-Synthesize with Leader-Picks-Best ✅

*(Unchanged from Rev 1 — reviewer confirmed this is sound.)*

**The governor IS an LLM.** Its job is to read N councilor outputs and produce the best possible synthesis. This requires NO custom aggregation code — only clear instructions in `soul.md` and `workflow.md`.

**The synthesis process:**

1. **Collect** all councilor results.
2. **Compare** — identify areas of agreement (high confidence) and disagreement (needs resolution).
3. **Synthesize** — extract the strongest elements from each output into a unified answer.
4. **Quality gate** — if the synthesized answer is weaker than the single best councilor output, FALL BACK to that best output (rule.md).
5. **Refine (optional)** — if disagreement on factual points, send a targeted clarification to 1-2 specific councilors (max 2 rounds).

**⚠️ Suggestion #2 from review — runtime aggregation counter:** The "fallback to best single output" and "max 2 rounds" rules are prompt-only in markdown. Consider adding a lightweight runtime counter (e.g., a metadata key `governor_round` on the governor instance) that forces termination after N rounds regardless of LLM compliance. This is a **Phase 4 hardening task** — not blocking for initial implementation, but recommended for production reliability.

---

## D3: Models List Injection — New `append_allowed_models` Appender ✅

*(Core approach unchanged. C2 + C6 fixes applied below.)*

**Design:**
- New meta.json flag: `inject_allowed_models: true`
- New appender function: `append_allowed_models(system_prompt, agent_meta, manager) -> str`

**⚠️ C2 CORRECTION — config access path:** The attribute is `manager.config` (NO underscore) at `daemon/manager.py:481`. The `_config` underscore form only exists as a `@property` on `InstanceLifecycleService` (`instance_lifecycle.py:875-878`) that delegates to `self._manager.config`. The appender receives `manager` (an `InstanceManager`), so it MUST use `manager.config.llm.allowed_models`.

**⚠️ C6 CORRECTION — flag loading requires BOTH steps:**
1. Add `inject_allowed_models: bool = Field(default=False, ...)` to `AgentMetadata` model (`daemon/registry.py`, near `context_injection` at line 129)
2. Add `inject_allowed_models=meta.get("inject_allowed_models", False)` to the loader in `AgentRegistry.discover()` (`daemon/registry.py:270`, alongside `context_injection`)

`AgentMetadata` has `ConfigDict(extra="ignore")` (registry.py:138-140) and the loader passes `meta.get("field")` per-field (registry.py:254-272). Without BOTH changes, the flag is silently discarded.

---

## D4: Councilor Count — Max 4 (revised from 5) ✅

### Revision Rationale

**Original (Rev 1):** Max 5 councilors. **Revised:** Max 4.

**W3 finding (verified):** The WorkerPool has **4 workers** (`daemon/constants.py:48-50`, `daemon/services/worker_pool.py:986`). Spawning 5 councilors means the 5th **queues** until a worker frees up — increasing latency without concurrency benefit.

**Other limits (verified):**
- Per-parent children: 10 (`MAX_CHILDREN_PER_INSTANCE`, constants.py:17) — not a constraint at ≤4
- Total instances: 100 (`MAX_INSTANCES`, constants.py:16) — not a constraint
- LLM concurrency: 10 (`llm_concurrency`, config.py:191) — not a constraint

**Decision:** Max **4 councilors** — aligns with WorkerPool=4 so all councilors execute concurrently. This maximizes parallelism without queueing overhead.

**Edge case — fewer than 4 models available:** The governor spawns one councilor per available model (up to 4). If fewer than 2 models, warn the user (see rule.md).

---

## D5: Iteration Cap — Max 2 Refinement Rounds ✅

*(Unchanged from Rev 1.)*

| Round | Action | Limit |
|-------|--------|-------|
| Round 0 (mandatory) | Spawn councilors, forward request, collect | Cap: **4 councilors** (revised per D4) |
| Round 1 (optional) | Targeted clarification to ≤2 councilors | Only if factual disagreement |
| Round 2 (optional, final) | One more clarification to ≤2 councilors | Only if still unresolved |
| **STOP** | Produce final answer | **MANDATORY after Round 2** |

---

## D6: Governor Self-Containment — Intelligence in Markdown ✅

*(Unchanged from Rev 1.)*

The governor should be **self-contained as an agent**. Its intelligence comes from `soul.md`, `rule.md`, `workflow.md`, and `tools_note.md`. Custom code is limited to the strict-validation tool and the config-injection appender.

---

## D7: Error Propagation Mitigation — The C1 Problem ✅ NEW

### The Problem (Verified)

The dependency bus tracks `_parent_errored` (`dependency_bus.py:418`) — a **sticky per-parent flag**. When ANY child emits a terminal error event, `emit_terminal()` (line 645) or `emit_terminal_for_child_instance()` (line 824) sets `_parent_errored[parent_id] = True`. This flag is **never cleared by a subsequent child success** — once True, always True.

When the governor finalizes, `JobFeedbackObserver._process_event` calls `_apply_parent_error_override()` (`job_feedback_observer.py:148-151`):
```python
if bus is not None and bus.had_parent_error(instance_id):
    status = InstanceStatus.ERROR.value
    error = bus.parent_error_message(instance_id) or CHILD_AGENT_ERROR_FALLBACK
    return status, error
```

**Impact:** If even ONE of 4 councilors errors, the governor's terminal status is forced to ERROR — even if the other 3 succeeded and the governor synthesized a perfect answer. This **invalidates the council's fault-tolerance value proposition**.

### How Watchers Get Registered

Watchers are registered when `send_message` is called from parent to child. The `_send_message` tool constructs a `FollowUp` and calls `bus.watch(source_task_id, follow_up)`. This creates the parent-child dependency that makes error propagation fire.

### Mitigation Options Evaluated

| Option | How | Feasibility | Risk |
|--------|-----|-------------|------|
| **(a) Bus opt-out flag** | Add a `skip_parent_error_tracking` param to `spawn_instance`/`send_message`; when True, skip `bus.watch()` registration | **Medium** — requires changes to `send_message` FollowUp construction + `emit_terminal` error-tracking block. Cleanest but touches the bus. | If the governor still wants to know about child completions (for aggregation), skipping watchers entirely breaks the completion-report flow. |
| **(b) Non-dependent orchestration** | Governor spawns councilors WITHOUT the watcher dependency — use `parent_id` for tree structure but don't register bus watchers | **Hard** — the watcher is registered inside `send_message` automatically. Can't easily opt out without a flag. | Same as (a) — the governor needs completion reports to aggregate. |
| **(c) `COMPLETED_WITH_ERRORS` terminal status** | New non-error terminal status for parents where some children errored but the parent succeeded | **High effort** — new status enum value, touches status derivation, API, UI, tests everywhere. Over-engineered for v1. | Scope creep. |
| **(d) Governor clears parent-error flag before finalize** ✅ | Governor's workflow.md instructs: after successful synthesis, call a new `clear_parent_error` mechanism before delivering the final answer. The bus already has `clear_parent_error(parent_id)` (`dependency_bus.py:1487-1507`) — currently called only AFTER finalize by the observer. | **Low effort** — expose `clear_parent_error` as a governor-accessible mechanism, or have the governor call it via a thin tool/wrapper before finalizing. | TOCTOU: if the governor clears the flag but then a late child error arrives before finalize, the flag is re-set. Acceptable — the governor's synthesis already succeeded. |

### Chosen Approach: (d) — Clear Parent-Error Flag on Successful Synthesis

**Rationale:**
- `clear_parent_error()` already exists on the bus (`dependency_bus.py:1487-1507`). It pops both `_parent_errored[parent_id]` and `_parent_error_message[parent_id]`.
- The governor's workflow (in markdown) already has a "deliver final answer" step. We add: "before delivering, if synthesis succeeded, clear the parent-error flag."
- This requires either:
  - A thin tool `clear_councilor_errors` (governor-only), OR
  - The governor sets a metadata flag that the finalize path checks.

**Recommended implementation (pragmatic):** Add a `clear_councilor_errors` tool to the "council" category that calls `bus.clear_parent_error(current_instance_id)`. The governor calls it in workflow Step 6 (Deliver) BEFORE producing its final output, **but only if synthesis succeeded**.

**If synthesis failed** (all councilors errored), the governor does NOT clear the flag — the bus correctly reports ERROR.

**TOCTOU note:** If a councilor errors AFTER the governor clears the flag but BEFORE finalize, the flag is re-set. This is acceptable — the governor already has the results it needs for synthesis; a late error doesn't invalidate the work done.

**Phase placement:** This adds a task to Phase 2 (the `clear_councilor_errors` tool) and updates Phase 1 (workflow.md Step 6 + rule.md error handling).

---

## D8: Crash Recovery — Council Manifest ✅ NEW

### The Problem (W4)

If the daemon crashes mid-council (after spawning some councilors but before synthesis), there's no record of the council's state. On restart, the governor instance resumes with no knowledge of its councilors.

### Mitigation: Persist Council Manifest

The governor uses `shared_context_metadata` (the existing KV store) to persist a **council manifest** before the first spawn:

```json
{
  "council_manifest": {
    "councilor_agent_id": "developer",
    "request": "implement fibonacci",
    "councilors": [
      {"instance_id": "abc...", "model": "gpt-4o", "status": "dispatched"},
      {"instance_id": "def...", "model": "claude-3-5", "status": "dispatched"}
    ],
    "round": 0,
    "created_at": "2026-07-25T..."
  }
}
```

**Workflow integration (workflow.md):**
- **Step 1 (Convene):** Before spawning, write the manifest to shared_context_metadata.
- **Step 2 (Collect):** Update each councilor's status as reports arrive.
- **Step 6 (Deliver):** Clear the manifest on successful delivery.
- **On restore (crash recovery):** If the governor resumes and finds a stale manifest, it reads the councilor instance_ids, checks their status (via `get_instance_info`), collects available results, and proceeds with synthesis.

**This is encoded in workflow.md (markdown), not custom code.** The governor uses existing tools (`shared_context_metadata`, `get_instance_info`) to implement it.

---

## D9: Quorum + Tiered Deadline ✅ (Rev 3 — degraded quorum + tiered deadlines)

### The Problem (W5)

There's no mechanism to handle hung councilors. If a councilor never reports, the governor could wait indefinitely. The original Rev 2 spec used a strict 10-min deadline and required ≥2 results to synthesize — both too rigid for real workloads (some tasks are genuinely long-running; 1 result is still useful).

### Mitigation: Degraded Quorum + Tiered Deadlines (Rev 3)

**Quorum — degrade, don't fail (in rule.md):**

| Results received | Action |
|------------------|--------|
| **0** | Cannot synthesize → report failure |
| **1** | Synthesize from the single source, prepend a **degraded-confidence notice** to the output |
| **2+** | Normal multi-source synthesis (no notice) |

**The strict "min 2" gate is removed.** A single result is still valuable — degraded confidence is better than no answer. The degraded notice makes the confidence level explicit to the requester so they can judge whether to trust or retry.

**Tiered deadlines (in workflow.md + manifest):**

| Tier | Limit | Behavior |
|------|-------|----------|
| **Soft limit** | **30 minutes** per councilor (default) | At 30min, the governor **decides** whether to extend or terminate. Extension is allowed if the governor judges the councilor is making progress (see "Extension decision" below). |
| **Hard limit** | **1 hour** absolute cap | At 1h, terminate the councilor regardless of progress. Include any partial result available. **No extension possible past this point.** |

**Extension decision (how the governor decides to extend past 30min):**

The governor checks progress via available signals, then decides in markdown logic (no custom code):

1. **Check councilor status** — call `get_instance_info(instance_id)`. If status is `RUNNING` (not `ERROR`/`COMPLETE`), the councilor is alive and working.
2. **Check task nature** — the governor judges from the original request: is this clearly a long-running task (e.g., multi-file implementation, complex analysis)? If yes → lean toward extending.
3. **Default heuristic** — if unsure, extend ONCE (to the 1h hard cap) if the councilor is still `RUNNING`. Do not extend repeatedly.
4. **Never extend past 1h** — the hard cap is absolute.

**Where the extension is recorded:** Update the `deadline` field on the affected councilor's entry in the council manifest (`shared_context_metadata`). The manifest always reflects the *actual* current deadline, not the original estimate:

```json
{
  "instance_id": "abc...",
  "model": "gpt-4o",
  "status": "DISPATCHED",
  "deadline": "2026-07-25T11:15:00Z",   // original: T+30min
  "deadline_extended": true,              // ← set on extension
  "deadline_hard_cap": "2026-07-25T11:45:00Z"  // T+1h, never changes
}
```

**What happens at the 1h hard limit:**

1. Terminate the councilor (`terminate_instance`).
2. Attempt to capture any partial result — if the councilor produced intermediate output before termination, include it as a **partial result** with a `PARTIAL_TIMED_OUT` status.
3. A partial result counts toward the degraded-quorum count (1 partial = 1 degraded result). If combined with other complete results, it participates in synthesis with its limitations noted.

### Degraded-Confidence Notice Format

When synthesizing from a single result (or a partial result), prepend this notice to the final output:

```raw
⚠️ Confidence Notice: This answer was synthesized from a single councilor source
(model: <model>, status: <COMPLETED|PARTIAL_TIMED_OUT>). Multi-model consensus
was not achieved — confidence is reduced. Consider re-running for higher confidence.
---
<actual synthesized answer>
```

The notice is **prompt-only** — encoded in `workflow.md` and `soul.md`, not custom code. The governor includes it when the result count is 1.

### Design Q&A

**Q1: How does the governor "decide" to extend?**
The governor uses its own judgment (it is an LLM). It checks `get_instance_info` to confirm the councilor is still `RUNNING`, considers whether the task is inherently long-running, and decides in markdown logic. The default heuristic is "extend once if RUNNING and task is complex." No custom code — the decision lives in `rule.md`/`workflow.md`. See "Extension decision" above.

**Q2: Where is the extension recorded?**
In the council manifest (`shared_context_metadata`). The `deadline` field is updated to the new value, `deadline_extended` is set to `true`, and `deadline_hard_cap` (set at spawn time to T+1h) remains immutable. The manifest always reflects the current effective deadline. See the manifest schema above.

**Q3: What does the degraded notice look like?**
See "Degraded-Confidence Notice Format" above — a `⚠️ Confidence Notice:` block prepended to the output, stating the single-source status and reduced confidence, followed by the actual answer.

**Q4: What happens at 1h hard limit if only 1 partial result returned?**
The partial result counts as 1 degraded result. The governor synthesizes from it (with the degraded notice) and reports. If the partial result is empty/unusable, it's treated as 0 results → report failure. See "What happens at the 1h hard limit" above.

**This balances thoroughness against time** — the requirement states "don't take endless time finding things," but some tasks are genuinely long. The tiered approach (30min soft + 1h hard) gives the governor judgment while guaranteeing termination.

---

## D10: Model Canonicalization ✅ NEW (W7)

### The Problem

`_resolve_model_override()` does case-insensitive matching but **returns the caller's spelling** (the `candidate` variable), not the canonical name from `allowed_models`. This means `gpt-4o` and `GPT-4O` would create two councilors with the same underlying model — defeating the diversity goal.

### Mitigation

In `spawn_councilor`, after validation, **normalize to the canonical name** from `allowed_models`:

```python
validated_model = lifecycle._resolve_model_override(model)
if validated_model is None:
    raise ValueError(...)
# W7: normalize to canonical name from allowed_models
canonical = next(
    (m for m in allowed if m.lower() == validated_model.lower()),
    validated_model  # fallback to caller spelling if unrestricted
)
```

Then use `canonical` for both the spawn call AND dedup checking (prevent spawning two councilors with the same canonical model).

---

## Revision Log

| Rev | Date | Changes |
|-----|------|---------|
| 1 | 2026-07-25 | Initial plan |
| 2 | 2026-07-25 | C1-C6 verified against source. D4 revised (5→4 councilors). D7 added (error propagation mitigation). D8 added (crash recovery manifest). D9 added (quorum + deadline). D10 added (model canonicalization). C2/C3/C4/C5/C6 corrections applied to D1, D3. |
| 3 | 2026-07-25 | D9 revised: degraded quorum (1 result → degraded notice, strict min-2 removed) + tiered deadlines (30min soft with governor extension / 1h hard cap). Manifest schema extended with `deadline_extended` + `deadline_hard_cap`. Degraded-notice format defined. |
