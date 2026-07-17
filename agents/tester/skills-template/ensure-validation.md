---
version: 1.0.0
category: validation
auto_load: false
---

# ensure.md Validation

You are the executor for this validation workflow. Project-specific quality gates live in `.agents/tester/rules/ensure.md`. Every requirement in that file must be validated before testing is complete. You run validation checks directly: parse the requirements, map them to test packs, execute the packs yourself, detect contradictions, and report.

The ensure.md file is **user-owned and read-only** — never modify it. Surface issues to the user; let them edit.

## Validation Workflow (4 Phases)

### Phase 1: Review, Scope & Detect Contradictions

1. Read `.agents/tester/rules/ensure.md` (read-only; direct read is allowed)
2. Derive the change set from blast-radius signals (request wording, shared context, `git diff` you run yourself, PACKS.md mapping)
3. Determine which requirements are in-scope:
   - **Core** requirements: always relevant; scope by blast radius (only requirements touching changed code)
   - **Release Gate** requirements: only run when change is big/critical/architecture
4. Detect contradictions — for each requirement, check if its METHOD conflicts with the test-pack rules (see Contradiction Handling below)
5. Parse each in-scope requirement into a testable task
6. Resolve each requirement to its mapped pack in PACKS.md (or static check for grep-based requirements)
7. For contradicting requirements, validate the tester's way (scoped pack + dual-layer timeout) instead of the literal method
8. Prioritize: Critical → Important → Nice-to-have

### Phase 2: Prepare Direct Validation Tasks (Pack-Mapped)

For each in-scope requirement, prepare a direct validation task using this template:

```
Task: Validate ensure.md Requirement (pack-mapped)
Context: [Project path, requirement text, mapped pack]
Requirement: [requirement text]
Pack: [exact pack path from PACKS.md]   # exactly ONE pack, or "static check" for grep

CONSTRAINTS (do NOT violate):
- Run ONLY this pack (or static check). Wrap with `timeout 300`.
- Quarantined tests (QUARANTINE.md) are skipped; they do not fail this requirement.
- No `pytest -x` for suite runs.

Requirements:
- Execute the validation with the timeout wrapper.
- Report PASS/FAIL with evidence.
- If FAIL: include error details, logs, evidence.
- If FAIL and quick-fixable: fix and re-validate.

Quick Fix Authorization: YES

Expected Output:
- Status (PASS/FAIL) + evidence + quick fixes applied (if any)
```

Independent requirements → run their packs in parallel (one pack run per requirement).
Dependent requirements → sequential, with edge in the todo graph.

### Phase 3: Execute Validation Directly

1. `todo_graph_create` with one node per requirement (or per validation pack)
2. Execute the validation yourself — run independent packs in parallel when supported; dependent checks run sequentially with edges in the todo graph
3. Monitor execution; aggregate results
4. For each result: capture PASS/FAIL with evidence

### Phase 4: Report & Document

1. Analyze validation results across all in-scope requirements
2. Identify failing requirements (Critical, Important, Nice-to-have)
3. Write `.agents/tester/RESULTS/[date]-ensure-validation.md` noting Core vs Release Gate coverage
4. Write `.agents/tester/LESSONS/ensure-validation-[date].md` for any failures or contradictions found
5. Report to leader with:
   - ✅ all passed, or
   - ❌ list of failed requirements with details and any Improvement Notices

## Pack-Mapping Rules

Each requirement references a pack from PACKS.md. The mapping discipline:

| Requirement Type | Validation Method |
|------------------|-------------------|
| Module integrity | One unit test pack: `<module>_unit_test` |
| Integration seam | One integration pack: `<module>_integration_test` |
| Full user journey | One E2E pack: `<journey>_e2e_test` |
| Mocked service behavior | One mock test pack: per `MOCK_TESTS.md` |
| Static check (no test) | Grep / file inspection (still wrap in a pack shell) |
| Full suite | Run ALL non-integration packs (parallel, ≤ 5 min each) |

**Mapping process:**

1. Read the requirement text
2. Identify what it claims to verify (regression, contract, behavior, presence)
3. Look up the corresponding pack name in PACKS.md
4. If no pack exists for an ad-hoc requirement, create one (use the test-pack-execution skill) before validation
5. Use the pack path verbatim when invoking the pack

## Contradiction Handling

ensure.md is user-written. Sometimes the requirement's METHOD contradicts the tester's optimization rules. When that happens, honor the user's INTENT but validate the tester's way, and notify the user.

### What Counts as a Contradiction

A requirement contradicts tester rules if it mandates:

- A bare/unbounded `pytest` / `go test` / `npm test` command (no pack, no timeout wrapper)
- `pytest -x` (stop-on-first-failure) for a suite run
- A full-suite run for what is a scoped (blast-radius) change
- Raw test file paths instead of packs
- Sequential runs where packs are independent (should be parallel)
- Anything exceeding the 5-min cap without a documented override

### When a Contradiction Is Found

1. **Honor the intent, validate the tester's way** — run the validation as a scoped pack with the dual-layer timeout. Do NOT skip it; do NOT run the bare/contradicting command.
2. **Record the contradiction** — requirement text, the rule it contradicts, how the validation was actually performed.
3. **Notify the user in the final report** — include an "ensure.md Improvement Notices" section with:
   - The contradicting requirement text
   - The rule it contradicts
   - How it was validated instead
   - A suggested pack-mapped rewrite
4. **Never modify ensure.md** — it's user-owned; surface the issue.

### Example Contradiction

User's ensure.md says:
> "Run `pytest tests/` to verify no regressions."

This contradicts the rules because:
- It's a bare, unbounded pytest (no pack, no timeout)
- It runs the entire suite (not scoped by blast radius)

Validation approach: honor the intent (verify no regressions in the change set), but run the scoped packs from PACKS.md that map to the changed files, each wrapped in `timeout 300`.

Suggested rewrite for the user:
> "Run scoped regression packs `[<module>_unit_test, <module>_integration_test]` for the change set, each ≤ 5 min, run in parallel."

## Priority Levels

### Critical

- MUST pass before testing is complete
- Block on failure: report and do not mark testing done
- Examples: regression-free in change set, security checks, required invariants

### Important

- SHOULD pass; flag if failed but do not block
- Examples: coverage thresholds, lint cleanliness, doc completeness

### Nice-to-have

- Report status, but never block
- Examples: optional invariants, style preferences, aspirational coverage

### Release Gate (slow / full-suite)

- Only run on big/critical/architecture changes
- Examples: full non-integration suite, full E2E suite, long-running perf checks
- Run via packs (parallel, each ≤ 5 min) — never a single bare command

## When to Run ensure.md Validation

| Trigger | Action |
|---------|--------|
| After unit tests pass | Validate in-scope Critical requirements |
| After mock tests pass | Final quality check (full in-scope Critical) |
| Before marking testing complete | All in-scope Critical must pass |
| On user request | Validate as requested (scope accordingly) |
| Big/critical/architecture change | Also run Release Gate requirements |

## Validation Result Format

When reporting ensure.md status, use:

```
### ensure.md Validation Results
- **Critical Requirements**: X/Y passed
  - ✅ [Requirement 1]: PASS
  - ❌ [Requirement 2]: FAIL — [reason]
- **Important Requirements**: X/Y passed
  - ✅ [Requirement 3]: PASS
- **Nice-to-have Requirements**: X/Y passed
  - ✅ [Requirement 4]: PASS

### ensure.md Improvement Notices (include when contradictions found)
- ⚠️ Requirement "[text]" contradicts rule "[rule]" — validated via [pack] instead.
  Suggested rewrite: "[pack-mapped version]". ensure.md is user-owned; please update.
```

## Quarantine Awareness

ensure.md validation must be **quarantine-aware**: tests listed in `.agents/tester/QUARANTINE.md` are skipped and do NOT count as failures for the requirement they would have validated.

- If a requirement would fail only due to a quarantined test → requirement PASSES (the test is intentionally skipped)
- Document this in the requirement's validation evidence: "PASS — test X is quarantined per QUARANTINE.md"
- A requirement with multiple tests, some passing some quarantined → PASS if the non-quarantined tests pass

## Quick Fixes During Validation

Quick fixes (see quick-fix skill) apply to ensure.md validation too:

- You find a quick-fixable issue while validating a requirement
- You apply the fix, re-validate the requirement, and report both the fix and the new result
- Commit hash must accompany the fix
- Document in `LESSONS/` and reference in the requirement's validation report

If a failure is NOT quick-fixable (≥ 20 lines, architecture, multi-file):

- Report the failure with full evidence
- Do NOT block the report; let the leader decide next steps
- Suggest a remediation path (Test Architecture Fix for test-code; full fix workflow for production)