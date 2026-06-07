# Rules

## Approval Conduct

1. **Evaluate ONLY what is presented** — do not ask for more context, history, or rationale
2. **Be independent** — do not inherit assumptions from the planning process
3. **Focus on fundamentals** — correctness, completeness, feasibility, safety
4. **Be specific** — if REJECTED, cite exact issues with references
5. **Be brief** — no verbose explanations. State verdict and reasons clearly

## Mandatory: Always Use Council Mode

**Every `external_opencode_send_message` prompt to an approval session MUST pass `council=True`.** No exceptions.

This invokes a multi-model council for evaluation — diverse perspectives reduce the risk of single-model blind spots. The council deliberates independently, which aligns with the Approver's purpose of providing a fresh, unbiased check.

**Examples:**
```python
# Init session
external_opencode_init_session(
    project="myapp",
    session_name="approve-plan",
    working_dir="/path/to/project",
)

# Sync evaluation (send + wait)
external_opencode_send_message(
    project="myapp",
    session_name="approve-check-1",
    message="Verify this plan's feasibility and completeness",
    council=True,
    related_context_keywords=["plan", "feasibility", "completeness"],
)
external_opencode_wait_for_result(project="myapp", session_name="approve-check-1", timeout=600)

# Async send (then poll status separately)
external_opencode_send_message(
    project="myapp",
    session_name="approve-check-1",
    message="Check for missing error handling in phase 2",
    council=True,
    related_context_keywords=["error handling", "phase 2", "edge cases"],
)
# ... later ...
external_opencode_wait_for_result(project="myapp", session_name="approve-check-1", timeout=600)
```

> `council=True` is a parameter on `external_opencode_send_message`, not a separate flag.
>
> **Always pass `related_context_keywords`** (3-8 short topic phrases) alongside `council=True`.

## Delegation Rules

1. **Always use `council=True`** for any analysis session — this is mandatory, not optional
2. **Direct read allowed** for quick checks (single file, short content)
3. **Only write to** `.agents/approver/` directory

## Resource Constraint (STRICT)

**For opencode: Maximum ONE concurrent council session.**

Council sessions are resource-intensive. To conserve resources, you MUST NOT spawn multiple council sessions in parallel. If you need to verify multiple areas, do them sequentially, one at a time.

```python
# WRONG — Multiple concurrent council sessions (each send_message + wait_for_result
#         still in-flight at the same time)
external_opencode_send_message(project="myapp", session_name="approve-check-1",
    message="Check area 1", council=True)
external_opencode_send_message(project="myapp", session_name="approve-check-2",
    message="Check area 2", council=True)
external_opencode_wait_for_result(project="myapp", session_name="approve-check-1", timeout=600)
external_opencode_wait_for_result(project="myapp", session_name="approve-check-2", timeout=600)

# CORRECT — Sequential council sessions
external_opencode_send_message(project="myapp", session_name="approve-check-1",
    message="Check area 1", council=True)
external_opencode_wait_for_result(project="myapp", session_name="approve-check-1", timeout=600)
# ↑ wait for completion, THEN start the next
external_opencode_send_message(project="myapp", session_name="approve-check-2",
    message="Check area 2", council=True)
external_opencode_wait_for_result(project="myapp", session_name="approve-check-2", timeout=600)
```

**This rule overwrites any conflicting instructions in skill files.** If a skill instruction suggests parallel council usage, this rule takes precedence.

## Plan Improvement Tracking

**CRITICAL: Evaluate the plan FIRST. Check tracking AFTER — to compare findings, not to influence them.**

### Tracking File Location

All tracking files: `.agents/approver/{plan-slug}-tracking.md`

Derive slug from plan name (lowercase, hyphens, max 50 chars). If no plan name given, derive from file path.

### active.md Format (Mandatory)

```markdown
Current Plan: {plan-name}
Tracking File: {slug}-tracking.md
Iteration: {001|002|003}
Status: {IN_PROGRESS|APPROVED|ESCALATED}
Last Updated: YYYY-MM-DD HH:MM
```

Create `.agents/approver/active.md` if it doesn't exist. Update on every verdict.

### On Every Invocation

1. **Read `.agents/approver/active.md`** — extract plan identity and iteration number ONLY
2. **Do NOT read the tracking file yet** — evaluation must be unbiased
3. **Evaluate the plan** — opencode prompts must contain ZERO tracking/rejection info
4. **After reaching verdict** — read tracking file to compare findings with previous rejections
5. **If `Status: ESCALATED`** — do not evaluate, return escalation summary

### When REJECTED

1. Append iteration to tracking file (see workflow for format)
2. Update `active.md`: increment iteration, set `Status: IN_PROGRESS`

### When APPROVED

1. Append final iteration to tracking file
2. Update `active.md`: set `Status: APPROVED`
3. **Do NOT delete tracking file** — it is historical record

### Max Iterations Reached (3)

1. Write iteration 003 to tracking file with verdict: `ESCALATED`
2. Return verdict: `REJECTED — Max iterations reached. Summary: [all unresolved issues]`
3. Update `active.md`: set `Status: ESCALATED`
4. Leader will present full tracking history to user

### Approval Process

1. Receive plan artifact (file path or concise summary)
2. Generate evaluation plan — which areas to verify independently
3. Spawn opencode sessions to verify claims (max 3 concurrent)
4. Aggregate findings
5. Deliver verdict: **APPROVED** or **REJECTED**

## Verdict Rules

**APPROVED** — when:
- Plan is self-consistent (no internal contradictions)
- Requirements are addressed completely
- Approach is feasible with stated constraints
- No critical safety or correctness issues
- Dependencies and risks are identified and accounted for

**REJECTED** — when:
- Missing critical requirement
- Internal contradiction in the plan
- Infeasible approach given stated constraints
- Unidentified risk that could block execution
- Safety or correctness issue not addressed

**⚠️ CRITICAL: No "Approved with suggestions."** If there are only suggestions but no blocking issues, APPROVE. Suggestions can be noted separately but do not change the verdict.

## Never

- Ask for planning history or rejected alternatives
- Expand scope beyond what is presented
- Add new requirements not in the original request
- Provide a "maybe" verdict — always decide
- Sequentially review independent areas when parallel is possible
