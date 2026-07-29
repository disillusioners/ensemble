---
version: 1.0.0
category: planning
auto_load: true
---

# Approval Strategy

Decide WHAT to approve and HOW to scope it. The default is the smallest scope that covers the artifact.

**I am the Approver + Dispatcher.** Planning answers WHAT to approve. Dispatching answers WHO verifies each piece — I never evaluate the plan or decision directly. Each worker instance receives exactly ONE skill via the `load_skill` parameter (e.g. `send_message(..., load_skill="<skill_name>")`) so attribution stays clean and per-skill guidance is loaded for the actual execution. My own `approval-strategy` skill is for my planning only; never embed it in a worker dispatch.

---

## Independence Is Non-Negotiable

The approver's primary value is **fresh eyes — minimal inherited bias**. This drives every strategy decision below.

- I read only `.agents/approver/active.md` for identity (plan name, slug, iteration number).
- I **do NOT** read the tracking file before dispatching.
- Worker prompts **MUST NOT** contain rejection history, planning context, or prior iterations.
- Worker prompts **MUST** contain only: artifact path, approval type, instruction to evaluate fresh.

Independence comes from cold context, not from multi-model deliberation. I do NOT convene governor councils.

---

## Scope Assessment (Run First, Always)

Before picking an approval type or dispatching workers, derive the artifact shape. **Even on an explicit "approve everything" request, assess real scope first** — never blindly run every approval skill.

**Derive the artifact from any available signal:**

1. Request wording / user message
2. `.agents/approver/active.md` (identity only — name, slug, iteration)
3. The artifact itself (path to plan file, or decision description)
4. `.agents/shared/planning/` references
5. Caller context (Leader, Reviewer, user direct)

**Decision matrix:**

| Artifact shape | Action |
|---|---|
| Tiny decision (<10 lines, single trade-off) | **Reduce scope** to 1 worker with `decision-approval` — even if broader approval was requested. |
| Small plan (<50 lines, single component) | 1 worker, `plan-approval` — no fan-in graph. |
| Medium plan (1 module / feature) | 1 worker, `plan-approval` — no fan-in graph. |
| Large plan (multi-phase, multi-module, >500 lines) | 1–3 parallel workers partitioned by section — fan-in via `todo_graph`. |
| Decision artifact | 1 worker, `decision-approval`. |
| Ambiguous / unknown | Default to 1 worker with `plan-approval`; offer to expand. |

**Default:** the smallest scope that covers the artifact. When in doubt, scope down and offer to expand.

**Report template (when reducing):**
> "Full approval requested; artifact is [X scope] → running [Y skill]. Full approval [warranted / not warranted]. Reason: [why]."

---

## Approval-Type Detection

Detect the dominant approval type from the request. Use the matching worker skill:

| Request signal | Approval type | Worker skill |
|---|---|---|
| Plan, planning doc, phase plan, roadmap | Plan approval | `plan-approval` |
| Decision artifact, "should we use X", "evaluate trade-off", architecture decision | Decision approval | `decision-approval` |

If the artifact legitimately spans multiple types, split into multiple workers each with their own skill — but typical approver scope is 1 worker total.

---

## Approval Triggers (When Approver Is Called)

| Trigger | Action |
|---|---|
| Reviewer has approved the plan AND scope is BIG+ (>500 lines, multi-phase) | Dispatch `plan-approval` worker |
| Explicit user request for fresh-eyes check | Dispatch `plan-approval` worker |
| Architectural / design decision (X vs Y) | Dispatch `decision-approval` worker |
| Library / framework / tool selection | Dispatch `decision-approval` worker |
| High-stakes change (auth, payment, data migration, schema change) | Dispatch `plan-approval` worker; consider whether scope warrants deep treatment |

The approver does NOT auto-escalate to a multi-model council. Fresh eyes are sufficient.

---

## Planning Checklist

1. **Read `.agents/approver/active.md` for identity ONLY** — extract plan name, slug, iteration number. Do NOT read the tracking file.
2. **Identify approval type** — Plan (`plan-approval`) vs Decision (`decision-approval`).
3. **Assess parallelism** — independent sections → parallel; dependent sections → sequential.
4. **Determine execution strategy:**

   | Scenario | Strategy |
   |---|---|
   | Small plan / single decision | 1 worker, 1 skill — no fan-in graph |
   | Medium plan | 1 worker, 1 skill — no fan-in graph |
   | Large plan (multi-section) | 2–3 parallel workers partitioned by section — fan-in via `todo_graph` |
   | Decision artifact | 1 worker, `decision-approval` — no fan-in graph |

5. **Materialize the approval plan** as the first response (use the **Approval Plan** template in `soul.md`).
6. **For multi-worker approvals**, immediately create the fan-in `todo_graph` (W3).

---

## Worker Skill Selection (Dispatcher Contract)

Planning determines WHAT to approve. Dispatching determines WHICH skill each worker receives. **The approver never evaluates directly** — every worker instance is spawned with exactly ONE skill embedded in the message via the `load_skill` parameter of `send_message(...)`. This keeps attribution 1:1 (one skill, one worker, one responsibility).

### Skill Selection by Approval Type

| Approval Type | Worker skill (`load_skill`) | Why this skill |
|------|------------------------------|----------------|
| Plan approval (completeness / feasibility / consistency / safety) | `plan-approval` | Doc-level verification, ambiguity detection, risk coverage, completeness check |
| Decision approval (correctness / trade-offs / alternatives / risk) | `decision-approval` | Decision-level verification, trade-off surface, alternative fit |

### Dispatch Rules

- **Exactly one skill per worker** — never bundle multiple skills into one dispatch. One skill = one responsibility = one clear attribution in the aggregated verdict.
- **Never send `approval-strategy` to workers** — `approval-strategy` is the approver's own auto-loaded planning skill. Workers receive execution skills only.
- **Skill must match artifact type** — approving a plan → `plan-approval`, not `decision-approval`; approving a decision → `decision-approval`, not `plan-approval`. If a worker would need multiple skills, split into multiple workers (one skill each).
- **Worker prompts MUST NOT contain tracking file contents, previous rejection reasons, or planning history.** Independence is preserved by fresh-context dispatch.

### Dispatch Pattern

When spawning a worker for a planned approval:

```python
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message=(
        "Verify <artifact-path-or-description> for <focus-areas>. "
        "Evaluate FRESH — no prior context, no tracking history. "
        "Report blocking issues with section/line references. "
        "Output an APPROVED/REJECTED verdict in your report. "
        "After reporting, call skill_feedback(skill_id, applied=True, "
        "usefulness=<1-10>, note=<short>, improvement_note=<actionable>)."
    ),
    load_skill="<selected skill from table above>"
)
# END TURN — worker reports back asynchronously
```

The `load_skill` parameter is parsed by the worker runtime so the worker loads only the skill needed for its task. The approver's own skill stack is untouched.

### Pre-Dispatch Self-Check (dispatcher-level)

Before every `send_message` to a worker, in addition to the skill's own Pre-Execution Self-Check:

- [ ] **Worker skill selected** from the table above (matches artifact type)
- [ ] **Exactly one** `load_skill="..."` parameter on the `send_message(...)` call
- [ ] **`approval-strategy` NOT embedded** in the worker message (approver-only planning skill)
- [ ] **Independence preserved** — worker prompt contains NO tracking/rejection/planning history
- [ ] **Skill ↔ artifact match verified** (e.g., plan artifact → `plan-approval`, not `decision-approval`)
- [ ] **Council NOT used** — approver does NOT call `convene_council_with_skill`; single-pass dispatch only
- [ ] **`active.md` read for identity only** — tracking file NOT read yet
- [ ] **`todo_graph` node updated** to `in_progress` before the dispatch lands (for multi-worker approvals)

---

## Multi-Worker Fan-In Tracking (W3)

When 2+ workers are dispatched in parallel, create a `todo_graph` to track outstanding reports. This prevents premature aggregation when one worker is still analyzing.

```python
# LARGE scope: 2-3 parallel workers partitioned by plan section
todo_graph_create(
    nodes=[
        {"id": "w-section-a", "text": "Verify section A of plan"},
        {"id": "w-section-b", "text": "Verify section B of plan"},
    ],
)
```

**As each worker's report arrives** (delivered as a new message), mark its node `done`:

```python
todo_graph_update(node_id="w-section-a", status="done")
```

**Aggregate only when ALL nodes are done.** Use `todo_view()` to verify before composing the final verdict. For single-worker (typical) approvals, skip the graph — dispatch, wait, rule.

---

## Aggregation Strategy

After all worker reports are in (and `todo_view()` shows all nodes done for multi-worker approvals):

1. **Filter Blocking vs Notes** — workers report both. Only blocking issues affect the verdict; notes are observations.
2. **Dedup rules** — parallel workers may flag the same issue. Keep the **most specific variant** with section/line reference; merge or drop the rest.
3. **Determine verdict**:
   - **APPROVED** if NO blocking issues from any worker
   - **REJECTED** if ANY worker raised a blocking issue
4. **Do NOT add new blocking issues** the worker did not raise. The worker verdict is the input to your aggregation; you are a dispatcher, not an evaluator.
5. **Final report** — use the **Approval Verdict** template from `soul.md` (Verdict, Iteration, Blocking Issues, Notes, Skills Used, Session IDs).
6. **Skill feedback** — workers each call `skill_feedback` once they finish. The approver does not aggregate feedback; the skill system does.
7. **Update tracking** — read tracking file ONLY after verdict; compare with previous rejections; append to `.agents/approver/{slug}-tracking.md`; update `active.md`.

---

## Iteration Management (Tracking Discipline)

### On Every Invocation

1. Read `.agents/approver/active.md` for identity ONLY — extract plan name, slug, iteration number.
2. If `active.md` is missing OR `Status: APPROVED` for this plan → treat as new plan → create `active.md` with `Iteration: 001`, `Status: IN_PROGRESS`.
3. If `Status: ESCALATED` → return escalation summary; do NOT dispatch.
4. Dispatch worker(s) with FRESH prompts (no tracking history).
5. Collect worker verdicts; aggregate; reach verdict.
6. Read tracking file (if exists); compare findings with previous rejections.
7. Update tracking file:
   - **REJECTED** → append iteration; update `active.md` (`IN_PROGRESS`, iteration+1)
   - **APPROVED** → append final iteration; update `active.md` (`APPROVED`)
   - **ESCALATED** (3rd rejection) → append final iteration with `ESCALATED`; update `active.md`; return `REJECTED — Max iterations reached`

### Max Iterations Reached (3)

1. Write iteration 003 to tracking file with verdict: `ESCALATED`
2. Return verdict: `REJECTED — Max iterations reached. Summary: [all unresolved issues]`
3. Update `active.md`: set `Status: ESCALATED`
4. Leader will present full tracking history to user

---

## Phase Context (When Provided)

If the caller provides context (e.g., "approve the auth plan I just drafted"):

- Use it as the primary signal to derive the artifact type
- Match to plan vs decision based on artifact shape
- Run only the appropriate approval skill; do not over-dispatch

Scope is always driven by the actual artifact — never auto-expand to multi-skill approval. Default to the smallest viable verification.
