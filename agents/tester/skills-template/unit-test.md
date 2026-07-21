---
version: 1.0.1
category: execution
auto_load: false
include: [test-pack]
---

# Unit Test

Discover what unit tests exist in the codebase and analyze coverage gaps. You are the executor — read-only investigation only. You produce a report; you do not run tests, fix code, or coordinate sessions.

## Scope

This skill answers two questions:

1. **What unit tests exist** — which test files cover which source modules.
2. **Where are the coverage gaps** — which source files, functions, or branches lack unit tests, and what edge cases are missing.

You do not orchestrate test execution, hand work off to other sessions, apply fixes, or re-validate after changes. Those concerns belong to `test-pack-execution` and `test-strategy`.

## Step 1: Discover Existing Unit Tests

Map the test landscape before you judge coverage.

1. **Locate test directories** — scan the repo for conventional locations: `tests/unit/`, `tests/`, `<module>/__tests__/`, `*_test.go`, `*_test.py`, `*.spec.ts`, etc. Note non-obvious locations discovered via grep (e.g., colocated `*.test.tsx`, `*_test.py` next to source).
2. **Identify test files** — list every file whose name or path signals a test (filename matches, `test_` prefix, `Test` suffix, `describe(`/`it(` blocks, pytest decorators).
3. **Map tests to source modules** — for each test file, record which module/function it covers. Group by feature area, not by directory, so the report reads as "what is tested" rather than "what folders exist."
4. **Note test conventions** — record the framework (pytest, jest, vitest, go test, etc.), naming patterns, fixture style, and any shared setup. This is what later runs and fixes will depend on.

Output: a `Test Inventory` section in your report — file path → covered module(s) → framework.

## Step 2: Analyze Coverage Gaps

Compare what exists against what should exist.

1. **Identify untested source files** — for every module under the project's main source tree, check whether any test file references it (import, fixture, call). Flag files with zero coverage.
2. **Identify under-tested modules** — modules with one or two tests covering the happy path only. These have the most hidden risk.
3. **Spot missing edge cases** — look for the patterns that bite later:
   - Empty inputs, nil/None, zero values
   - Boundary values (off-by-one, max int, empty collections)
   - Error paths and exception branches
   - Validation rejection paths (invalid input, permission denied)
   - Concurrency / re-entrancy cases if relevant to the module
4. **Size the test packs** — apply the sizing heuristics below; flag any pack that already exceeds the unit-test runtime budget, since it will need splitting before execution.

Output: a `Coverage Gaps` section listing each gap with file:line where possible and the scenario missing.

## Step 3: Write the Report

Produce a single structured report. Use the Coverage Documentation Pattern in this skill as the template for what the report should look like. Do not write to `.agents/tester/COVERAGE.md` yourself — that update happens after execution, not during discovery.

The report goes back to the requester with three sections:

- **Test Inventory** — what exists, grouped by feature
- **Coverage Gaps** — what's missing, prioritized (untested file > under-tested file > missing edge case)
- **Pack Sizing Notes** — any existing pack > 2 min, suggestions for splitting

Keep it focused and actionable. The requester decides what to run, fix, or follow up on.

## Unit Pack Sizing Heuristics

Apply these when assessing test pack structure during discovery:

| Pack characteristic | Action |
|---|---|
| Estimated runtime < 2 min | OK as-is for unit-test scope |
| Estimated runtime ≥ 2 min | Split before launch — group by feature/module |
| Tests hitting real DB/network | Recommend mocking the boundary or splitting into integration pack |
| Tests sharing heavy setup | Flag for fixture extraction; verify cleanup runs (state leaks cause flakes) |
| Tests with > 5s sleeps each | Recommend overriding config/env to reduce waits; do not raise the cap |
| Tests with order dependencies | Flag order-dependent tests for isolation; order-coupled tests create flakes |

These are the same heuristics `test-pack-execution` uses at run time. Reporting a pack that violates them now saves a re-split later.

## Coverage Documentation Pattern

Structure the `Coverage Gaps` section of the report as:

```markdown
## [Date] — Unit Test Discovery

**Repository**: [repo / branch]
**Framework(s)**: [pytest, jest, ...]
**Test files found**: [count]
**Modules covered**: [count of N]

### Test Inventory
- [path/to/test_file]: covers [module/function], [scenario summary]

### Coverage Gaps
- [module/path] — entire file untested; recommend pack [pack_name]
- [module/path:line] — [missing edge case] — add to [pack_name]
- [module/path:line] — [error branch not exercised] — add to [pack_name]

### Pack Sizing Notes
- [pack_name]: estimated [X min] — exceeds 2 min budget, split before run
```

This format gives the requester what they need to decide the next step without re-doing the discovery.
