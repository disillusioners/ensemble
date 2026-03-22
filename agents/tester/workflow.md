# Workflow

## Initial Project Setup

When starting with a new project:

1. **Check for `.agents/tester/`** — Does the project-specific directory exist?
2. **Read existing docs** — If README.md exists, read it to understand project testing context
3. **Initialize if needed** — Create `.agents/tester/` directory and README.md if missing
4. **Identify test types** — Check for existing unit tests and mock tests

---

## Unit Test Workflow

**Primary Focus**: Maintain and update unit tests written by Coder agent

1. **Discover** — Find all unit test files
2. **Run** — Execute unit test suite
3. **Analyze Failures** — Identify which tests need updates
4. **Update Tests** — Fix broken tests due to code changes
5. **Report** — Summarize unit test status
6. **Document** — Update `.agents/tester/COVERAGE.md` with coverage changes

### Unit Test Maintenance Triggers
- Code refactoring breaks tests
- New features need test coverage
- Edge cases discovered
- Bug fixes need regression tests

---

## Mock Test Workflow

**Primary Focus**: Ensure features work end-to-end with real service

### Phase 1: Preparation
1. **Identify what to test** — Which feature/workflow needs mock testing?
2. **Find mock test scripts** — Check `.agents/tester/MOCK_TESTS.md` for existing tests
3. **Check dependencies** — What external services need mocking?

### Phase 2: Create/Update Mock Test Script
1. **Choose script language** — Python, Go, or Bash based on project
2. **Add timeout protection** — MUST have explicit timeout with auto-kill
3. **Add port validation** — Kill processes on required ports at start
4. **Configure mock ports** — Use ports > 10000 (never normal ports)
5. **Write test logic** — Real service calls with mocked responses

### Phase 3: Execute Mock Test
1. **Validate environment** — Check ports are free
2. **Start mock services** — Launch mocked externals on ports > 10000
3. **Start real service** — Point to mock services
4. **Run test scenarios** — Execute real workflows
5. **Capture results** — Record success/failure with details
6. **Cleanup** — Kill all processes, free ports

### Phase 4: Report & Document
1. **Report results** — Detailed mock test report
2. **Update MOCK_TESTS.md** — Add/update mock test inventory
3. **Document in LESSONS.md** — Record any issues discovered

---

## Documentation Updates

After testing sessions, update relevant files in `.agents/tester/`:

### README.md (create/update when)
- Project structure changes
- New test frameworks introduced
- Testing process changes
- Mock test setup changes

### MOCK_TESTS.md (create/update when)
- New mock test created
- Mock test configuration changes
- Port assignments change
- Mock service dependencies change

### LESSONS.md (append when)
- Found tricky bugs
- Discovered edge cases
- Mock test failures reveal issues
- Learned project-specific gotchas

### COVERAGE.md (update when)
- Coverage improves/declines significantly
- New areas need testing
- Critical paths identified
- Mock tests added/updated

---

## Report Format

```
## Test Report: [feature/suite]

### Summary
- Total: X | Passed: Y | Failed: Z | Errors: E
- Unit Tests: X tests | Mock Tests: X tests

### Unit Test Results
- [Details of unit test failures/successes]

### Mock Test Results
- Test Script: [script_name]
- Timeout: [X seconds]
- Ports Used: [list ports > 10000]
- Status: [PASS/FAIL]
- Details: [what was tested, what failed]

### Failures
[file:line] TestName — reason

### Errors
[file:line] — exception

### Action Needed
- [ ] Fix failing tests
- [ ] Review edge cases
- [ ] Update mock test script

### Documentation Updated
- [x] README.md — added new test section
- [ ] MOCK_TESTS.md — no changes
- [ ] LESSONS.md — documented mock test port conflict
```

---

## Decision Points

- **No `.agents/tester/` directory?** → Create it with README.md
- **No test file exists?** → Ask where to create it, document in README.md
- **Unit tests failing?** → Update tests to match code changes
- **Need integration testing?** → Create mock test script with timeout protection
- **Port conflicts?** → Use ports > 10000, kill conflicting processes at script start
- **Multiple test targets?** → Prioritize: mock tests > unit tests > edge cases
- **Flaky tests?** → Flag and suggest isolation, document in LESSONS.md
- **New testing knowledge?** → Write to appropriate `.agents/tester/` file
