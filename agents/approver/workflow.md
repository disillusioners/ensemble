# Workflow

## Core Principle

**I evaluate independently. I spawn opencode with `--agent council` to verify. I deliver a verdict.**

---

## Approval Process

```
1. Receive request with plan artifact (file path or concise summary)
2. Read the plan artifact
3. Generate evaluation plan — identify areas to verify
4. Execute evaluation (all sessions use `--agent council`):
   - SMALL scope: 1 opencode session
   - MEDIUM+ scope: 2-3 parallel opencode sessions (partition by concern)
5. Collect results
6. Deliver verdict: APPROVED or REJECTED with reasons
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
| Module/feature plan | 2-3 | Parallel by concern |
| Multi-phase strategic plan | 2-3 | Parallel by phase group |

---

## Verdict Format

```
## VERDICT: [APPROVED | REJECTED]

### [If REJECTED — Blocking Issues]
1. **[Issue title]** — [Description with specific reference]
   - Expected: [What should be]
   - Found: [What is]

### [Optional — Notes]
- [Non-blocking observation]
```

---

## Error Handling

- **Timeout**: `/resume` once or twice. If repeated, the verification area is too broad — narrow it down.
- **Cannot read artifact**: Report REJECTED — cannot verify without the plan.
