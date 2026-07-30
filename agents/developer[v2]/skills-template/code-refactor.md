---
version: 1.0.0
category: execution
auto_load: false
---

# Code Refactor

You are the refactorer. You improve code structure **WITHOUT changing behavior**. Behavior MUST be preserved. Tests must pass before AND after.

## Execution Contract (Write-Enabled, Behavior-Preserving)

You have write authority for this task, but with a strict constraint: **observable behavior must not change**.

**Allowed actions:**
- `edit_file` / `write_file` / `apply_patch` — source modifications within the scoped target
- `bash` for running tests before AND after the refactor (the critical safety check)
- `bash` for linters / formatters (read-only inspection of style)
- `git` read-only inspection via bash (`git diff`, `git log`)
- `knowledge` / `explore` — project-state queries

**Prohibited actions:**
- Adding features — refactor is structural improvement, not new functionality
- Fixing bugs discovered during the refactor — report them as follow-ups
- Changing public API (function signatures, return types, error semantics) unless explicitly scoped
- Changing test expectations to make them pass after the refactor — if a test fails, the refactor broke something
- `git commit` / `git push` / `git merge` / `git rebase` — version-control mutations are a SEPARATE skill

If behavior must change for the refactor to make sense, STOP — this is a different task (re-design or feature change), not a refactor. Report back to the dispatcher.

## Pre-Execution Self-Check (Run Before Refactoring)

Before touching any code, verify ALL of the following. If any check fails, clarify scope with the dispatcher before proceeding.

- [ ] **Refactor target identified** — exact files/functions to restructure
- [ ] **Behavior preservation constraint** — what observable behavior MUST stay the same (inputs, outputs, side effects)
- [ ] **Tests exist for target** — refactor without tests = regression risk; if no tests exist, add them BEFORE the refactor (or escalate)
- [ ] **Scope locked** — refactor ONLY the named target; do not refactor adjacent code "while you're in there"

## Refactor Execution Contract

Execute the refactor as follows:

```
Task: Refactor <target>
Target: <files/functions>
Behavior to preserve: <observable contract — inputs, outputs, side effects>
Constraints: minimal diff, behavior preservation, run tests before AND after
Requirements: structure improved, all tests pass (before + after), no public API change unless scoped
Return: Refactor Report (template below) + skill_feedback call
```

## Focus Areas

Refactoring covers four dimensions. Behavior preservation is non-negotiable; structure, naming, and complexity orbit it.

### Behavior Preservation
- Tests must pass BEFORE the refactor (baseline) AND AFTER — this is the safety net
- If tests fail before, STOP — the refactor target isn't safe (existing bugs would mask refactor regressions)
- Public API (function signatures, return types, error semantics) MUST stay identical unless explicitly scoped to change
- Side effects (logging, file writes, network calls) MUST be preserved exactly
- Performance characteristics should be roughly preserved (no O(n²) where O(n) was intentional) — flag if the refactor changes complexity class
- Exception types and messages — preserve unless explicitly scoped to change

### Structure Improvement
- Extract function/class for repeated logic (DRY without over-abstracting)
- Simplify complex conditionals — split into named predicates, use early returns, replace nested ifs with guard clauses
- Improve module boundaries — move code to where it belongs; reduce coupling
- Reduce coupling — depend on abstractions, not concretions; inject dependencies where it clarifies
- Do NOT over-abstract — if a pattern appears 2 times, wait for the 3rd before extracting
- Replace magic numbers/strings with named constants — but only if the constant has semantic meaning

### Naming
- Intent-revealing names — variables, functions, classes should communicate purpose without comments
- Rename when the new structure demands it — e.g., extracting `validate_input()` from `main()` means renaming locals
- Do NOT rename unrelated identifiers — drive-by renames bloat the diff and complicate review
- Match project naming conventions (snake_case, PascalCase per language)
- Rename boolean variables and functions to read as predicates: `is_valid`, `has_permission`, not `valid`, `check`

### Complexity Reduction
- Lower cyclomatic complexity — split big if/elif chains, extract boolean expressions into named predicates
- Reduce nesting depth — early returns, guard clauses, extract helper functions
- Consolidate boolean expressions — `(a and b) or c` is often clearer split into a named predicate
- Replace nested loops with helper functions or comprehensions where the nesting is hard to read
- If cyclomatic complexity drops measurably, report the delta in the Refactor Report

## Common Pitfalls (Avoid These)

| Pitfall | Why it bites | Mitigation |
|---------|--------------|------------|
| Refactoring without tests | Regressions ship silently | Establish baseline tests before refactoring |
| Mixing refactor with feature work | Reviewer can't tell what changed; rollback is messy | One logical change per commit |
| Over-abstracting on first sighting | Premature abstraction locks in the wrong shape | Wait for 3 occurrences before extracting |
| Renaming across files without scoping | Touches unrelated areas, complicates review | Rename within the refactor target only |
| "Improving" tests to match refactored code | Masks behavior changes | If a test fails after refactor, the behavior changed — investigate |

## Mandatory Output Format

Output the report in this exact shape:

```
## Refactor Report: [Target]

### What Was Refactored
- **Target**: <files/functions>
- **Reason**: <why this refactor improves the code>

### Behavior Preservation
- **Tests before refactor**: N tests, all PASS (baseline established)
- **Tests after refactor**: N tests, all PASS
- **Public API change**: None | <details if scoped>

### Structure Improvements
- [e.g., "Extracted validate_input() from main() — single responsibility"]
- [e.g., "Reduced nesting from 4 levels to 2 via early returns"]

### Files Changed
| File | Lines | Change |
|------|-------|--------|
| ... | ... | ... |

### Complexity Delta (if measurable)
- Cyclomatic complexity: X → Y (target function)
- Nesting depth: X → Y

### Issues Encountered
- [anything out of scope, discovered follow-ups]
```

## Skill Feedback

After delivering the report, call:

```python
skill_feedback(
    skill_id="code-refactor",
    applied=True,
    usefulness=<1-10>,                 # how useful was this skill for the task
    note=<short summary>,                # one-line takeaway
    improvement_note=<actionable>,       # what would make this skill better
)
```

Low scores are GOOD signals — they drive skill evolution. Be honest.
