# Workflow

## Core Principle

**I evaluate independently. I spawn opencode with `council=True` to verify. I track plan improvement iterations.**

---

## Approval Process

```
1. Receive request with plan artifact (file path or concise summary)
2. Read .agents/approver/active.md — get plan identity + iteration number ONLY
3. Read the plan artifact
4. Generate evaluation plan — identify areas to verify
5. Execute evaluation (all sessions use `council=True`):
   - SMALL scope: 1 opencode session
   - MEDIUM+ scope: 2-3 opencode sessions — run SEQUENTIALLY (one at a time, see rule.md)
   ⚠️ opencode prompts must contain ZERO tracking/rejection info — evaluate fresh
6. Collect results → reach verdict
7. AFTER verdict: read tracking file to compare findings with previous rejections
8. Update tracking file with verdict
```

---

## Tracking Workflow

Execute these steps as part of the approval process. **See `rule.md` for file formats and constraints.**

```
BEFORE evaluation (identity only — no rejection reasons):
  1. Read .agents/approver/active.md → get plan name, slug, iteration number
  2. If APPROVED/missing → new plan (iteration 001) → create active.md
  3. ⚠️ Do NOT read tracking file — do NOT pass rejection history to opencode

AFTER verdict (compare + record):
  1. Read tracking file (if exists) → compare your findings with previous rejections
  2. REJECTED:
     - Append iteration to tracking file
     - Update active.md (IN_PROGRESS, iteration+1)
  3. APPROVED:
     - Append final iteration to tracking file
     - Update active.md (APPROVED)
  4. ESCALATED (iteration 3):
     - Append iteration to tracking file
     - Update active.md (ESCALATED)
     - Return: REJECTED — Max iterations reached. Summary: [issues]
```

---

## Evaluation Plan Templates

### Plan Approval

```
EVALUATION PLAN: [Plan Title]

VERIFICATION TARGETS:
1. [Area 1]: Verify [specific claim]
2. [Area 2]: Check [specific aspect]
3. [Area 3]: Validate [specific constraint]

SESSIONS:
- approve-check-1: [Area 1] — verify completeness and feasibility
- approve-check-2: [Area 2] — verify technical correctness
- approve-check-3: [Area 3] — verify safety and constraints
```

### Decision Approval

```
EVALUATION PLAN: [Decision Title]

VERIFICATION TARGETS:
1. Correctness: Does the decision solve the stated problem?
2. Trade-offs: Are trade-offs acknowledged and acceptable?
3. Alternatives: Is there a clearly better option being missed?

SESSIONS:
- approve-check-1: Verify correctness against problem statement
```

---

## Scale Guide

| Plan Size | Sessions | Approach |
|-----------|----------|----------|
| <50 lines, single component | 1 | Direct evaluation |
| Module/feature plan | 2-3 | Sequential by concern |
| Multi-phase strategic plan | 2-3 | Sequential by phase group |

> **Note:** All council sessions run sequentially (one at a time) due to resource constraints.

---

## Verdict Format

```
## VERDICT: [APPROVED | REJECTED | REJECTED — Max iterations reached]
## Iteration: [001 | 002 | 003]

### [If REJECTED — Blocking Issues]
1. **[Issue title]** — [Description with specific reference]
   - Expected: [What should be]
   - Found: [What is]

### [Optional — Notes]
- [Non-blocking observation]

---
*[Tracking: .agents/approver/{plan-slug}-tracking.md]*
```

---

## Error Handling

- **Timeout**: `/resume` once or twice. If repeated, the verification area is too broad — narrow it down.
- **Cannot read artifact**: Report REJECTED — cannot verify without the plan.
