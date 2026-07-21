---
version: 1.0.1
category: execution
auto_load: false
include: [test-pack]
---

# Mock Test

You are the executor. You design, implement, and execute mock tests directly against external-service dependencies — no delegation to other agents.

## Port & Safety Rules

- **Mock ports**: ALWAYS > 10000 (1-9999 reserved for production/dev; 20000+ reserved)
- Before killing any port, verify it is not 8088 (ensemble self-system — see rule.md Port Safety). Use ports > 10000 for mock services.
- **Never call real external services** — mock tests run against fake services on local ports
- **Always document ports** in `.agents/tester/MOCK_TESTS.md`; use consistent ports per scenario
- **Kill processes on ports before/after** — both pre-test cleanup (avoid conflicts) and post-test cleanup (avoid leaks)

## What to Mock

| Real service | Mock with |
|---|---|
| External HTTP API (OpenAI, Slack, etc.) | Local HTTP server on port > 10000 returning canned responses |
| Database | SQLite/in-memory or local container |
| Message queue | In-process fake |
| Auth provider | Fake auth server with predictable tokens |
| File storage | Local temp dir |

**Decision rule:** if the test needs the real service's behavior to validate the system under test, mock the boundary; if the test needs the real service to validate the integration itself, use an integration test instead.

## Mock Test Specification Template

Document every mock test in `.agents/tester/MOCK_TESTS.md` using this template:

```markdown
## Mock Test: [Test Name]

### Metadata
- **Created**: [date]
- **Script**: [path/to/script.ext]
- **Language**: [Python/Go/Bash]
- **Status**: [PLANNED/IMPLEMENTED/ACTIVE/DEPRECATED]

### Configuration
- **Timeout**: [X seconds]
- **Service Port**: [port > 10000]
- **Mock Ports**: [list of ports > 10000]
- **Cleanup**: Kill processes on all ports before/after

### What It Tests
- [Feature/workflow being tested]
- [Critical paths covered]

### Mock Services Required
- [External service 1]: Mock on port [X]
- [External service 2]: Mock on port [Y]

### Test Scenarios
1. [Scenario 1]: [Expected behavior]
2. [Scenario 2]: [Expected behavior]
3. [Scenario 3]: [Expected behavior]

### Success Criteria
- [ ] All scenarios pass
- [ ] Response times within [X ms]
- [ ] No process leaks
- [ ] All ports freed after test

### Implementation Notes
- [Special considerations]
- [Dependencies]
- [Known issues]

### Last Run
- **Date**: [timestamp]
- **Session**: [executor session ID]
- **Result**: [PASS/FAIL]
- **Quick Fixes**: [List any quick fixes applied]
- **Report**: [link to RESULTS/ file]
```

## 5-Phase Mock Test Workflow

### Phase 1: Design Mock Test

1. Identify feature/workflow to test (must involve external dependency that needs mocking)
2. Read `.agents/tester/MOCK_TESTS.md` for existing tests (avoid port collisions)
3. Read `.agents/tester/rules/ensure.md` for quality requirements
4. Design mock test specification — what to test, required mock services, ports (> 10000), timeout, scenarios, expected results
5. Document specification in `.agents/tester/MOCK_TESTS.md` using the template above
6. Confirm ports are unused (`lsof -ti:<port>`); pick alternative if taken

### Phase 2: Create Mock Test Script

Write the test script directly (Python/Go/Bash) with explicit auto-kill timeout, pre-start port validation/cleanup, mock services on ports > 10000, the test scenarios, and exit-time cleanup of all processes.

### Phase 3: Execute Mock Test

Run the script directly, capture all output, report PASS/FAIL with details (on FAIL include error messages and logs). Apply quick fixes (< 20 lines, no architecture change) and retry; always verify post-run cleanup (processes killed, ports freed).

### Phase 4: Report & Document

1. Capture execution results directly
2. Write comprehensive test report to `.agents/tester/RESULTS/[date]-[test-name].md`
3. Update `.agents/tester/MOCK_TESTS.md` Last Run section (status, date, result)
4. Update `.agents/tester/LESSONS/` with findings and any quick fixes (e.g., `mock-test-[name]-findings.md`)
5. Update `.agents/tester/README.md` if procedures changed

### Phase 5: Validate ensure.md (after mock tests pass)

1. If mock tests pass, proceed to ensure.md validation
2. Follow the ensure-validation skill workflow
3. Document results in `.agents/tester/RESULTS/[date]-ensure-validation.md`

## Scenario Design Tips

- **Cover happy path + one failure per mock** — verify system works when all is well, plus one failure scenario per mock (500 / timeout / malformed data).
- **Keep timeout tight** — mock tests should finish in seconds, not minutes; if they don't, you're not mocking enough.
- **Avoid real-network calls** — even DNS lookups can hang; use `127.0.0.1` literals for all mock endpoints.