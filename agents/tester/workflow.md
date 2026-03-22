# Workflow

## Test Request Flow

1. **Understand** — What to test? What framework? Where to put tests?
2. **Explore** — Find existing tests, understand code structure
3. **Write** — Create tests following project conventions
4. **Run** — Execute tests, capture results
5. **Report** — Summarize: passed/failed/error, with actionable details

## Report Format

```
## Test Report: [feature/suite]

### Summary
- Total: X | Passed: Y | Failed: Z | Errors: E

### Failures
[file:line] TestName — reason

### Errors
[file:line] — exception

### Action Needed
- [ ] Fix failing tests
- [ ] Review edge cases
```

## Decision Points

- **No test file exists?** → Ask where to create it
- **Multiple test targets?** → Prioritize: critical paths > edge cases > happy paths
- **Flaky tests?** → Flag and suggest isolation
