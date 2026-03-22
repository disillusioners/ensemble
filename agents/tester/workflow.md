# Workflow

## Initial Project Setup

When starting with a new project:

1. **Check for `.agents/tester/`** — Does the project-specific directory exist?
2. **Read existing docs** — If README.md exists, read it to understand project testing context
3. **Initialize if needed** — Create `.agents/tester/` directory and README.md if missing

---

## Test Request Flow

1. **Understand** — What to test? What framework? Where to put tests?
2. **Explore** — 
   - Find existing tests, understand code structure
   - Read `.agents/tester/README.md` for project-specific context
   - Check `.agents/tester/GUIDE.md` for conventions
3. **Write** — Create tests following project conventions
4. **Run** — Execute tests, capture results
5. **Report** — Summarize: passed/failed/error, with actionable details
6. **Document** — Update `.agents/tester/` files with new knowledge

---

## Documentation Updates

After testing sessions, update relevant files in `.agents/tester/`:

### README.md (create/update when)
- Project structure changes
- New test frameworks introduced
- Testing process changes

### LESSONS.md (append when)
- Found tricky bugs
- Discovered edge cases
- Learned project-specific gotchas

### COVERAGE.md (update when)
- Coverage improves/declines significantly
- New areas need testing
- Critical paths identified

---

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

### Documentation Updated
- [x] README.md — added new test section
- [ ] LESSONS.md — no new lessons
```

---

## Decision Points

- **No `.agents/tester/` directory?** → Create it with README.md
- **No test file exists?** → Ask where to create it, document in README.md
- **Multiple test targets?** → Prioritize: critical paths > edge cases > happy paths
- **Flaky tests?** → Flag and suggest isolation, document in LESSONS.md
- **New testing knowledge?** → Write to appropriate `.agents/tester/` file
