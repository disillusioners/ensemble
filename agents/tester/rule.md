# Rules

## Must

- Follow project's test conventions (naming, structure, location)
- Write self-contained tests (no external deps unless mocked)
- Report actual error messages, not summaries
- Suggest fixes when tests fail
- **Check `.agents/tester/README.md` before testing a project**
- **Create `.agents/tester/` directory if it doesn't exist**
- **Document testing procedures in `.agents/tester/README.md`**
- **Record lessons learned in `.agents/tester/LESSONS.md`**
- **Keep documentation concise and actionable**

### Unit Test Rules
- **Maintain unit tests** — Update when code changes break them
- **Run unit tests frequently** — After every code change
- **Add regression tests** — For every bug fixed

### Mock Test Rules (CRITICAL)
- **Create mock tests as scripts** — Python, Go, or Bash
- **Add explicit timeout** — Every mock test MUST have timeout with auto-kill
- **Auto-kill after timeout** — Prevent hanging tests
- **Validate conditions at start** — Check and kill processes on required ports
- **Use ports > 10000 only** — Never use normal service ports
- **Test real service** — Run actual service with mocked externals
- **Clean up after tests** — Kill all processes, free all ports
- **Document in MOCK_TESTS.md** — List all mock tests and their configs

## Must Not

- Skip failing tests silently
- Test implementation details over behavior
- Leave commented-out code
- Over-test trivial code (getters/setters)
- **Ignore existing `.agents/tester/` documentation**
- **Write redundant documentation — check if info already exists**
- **Store temporary or throwaway files in `.agents/tester/`** — only permanent knowledge

### Mock Test Restrictions
- **Never use production ports** — Always use ports > 10000
- **Never skip timeout** — All mock tests must have timeout protection
- **Never skip port validation** — Always check/kill conflicting processes at start
- **Never leave processes running** — Always cleanup after mock tests
- **Never test without mocks** — Mock tests must not call real external services

---

## File Organization in `.agents/tester/`

### Required Files
- **README.md** — Always maintain. Quick start for testing this project
- **MOCK_TESTS.md** — Inventory of all mock tests with configurations

### Optional Files (create as needed)
- **GUIDE.md** — Detailed testing guidelines
- **WORKFLOWS.md** — Step-by-step procedures
- **LESSONS.md** — Lessons learned and gotchas
- **COVERAGE.md** — Coverage tracking and goals
- **RESULTS/** — Directory for historical reports

### Naming Convention
- Use UPPERCASE.md for standard docs
- Use descriptive names for specific topics (e.g., `API_TESTING.md`)
- Date historical reports: `RESULTS/2024-01-15-login-tests.md`

---

## Mock Test Script Template

Every mock test script MUST follow this structure:

```bash
#!/bin/bash
# Mock Test: [Test Name]
# Description: [What this tests]
# Timeout: [X seconds]

set -e

# Configuration
TIMEOUT_SECONDS=[X]
MOCK_PORT=[>10000]
SERVICE_PORT=[>10000]

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    # Kill all processes on test ports
    lsof -ti:$MOCK_PORT | xargs kill -9 2>/dev/null || true
    lsof -ti:$SERVICE_PORT | xargs kill -9 2>/dev/null || true
}

# Set timeout with auto-kill
trap cleanup EXIT
timeout $TIMEOUT_SECONDS bash -c "
    # Phase 1: Validate and prepare environment
    cleanup
    
    # Phase 2: Start mock services
    # [Start mock external services on ports > 10000]
    
    # Phase 3: Start real service
    # [Start service pointing to mocks]
    
    # Phase 4: Run test scenarios
    # [Execute real workflows against service]
    
    # Phase 5: Validate results
    # [Check outputs, responses, state]
"

echo "Mock test completed successfully"
```

### Key Elements Required
1. ✅ Explicit TIMEOUT_SECONDS variable
2. ✅ timeout command wrapping test logic
3. ✅ cleanup function to kill processes
4. ✅ trap EXIT to ensure cleanup runs
5. ✅ Port validation and cleanup at start
6. ✅ All ports > 10000
7. ✅ Clear test phases
8. ✅ Success/failure reporting

---

## Port Management Rules

### Port Range Allocation
- **Ports 1-9999**: Production and development services
- **Ports 10000-19999**: Mock tests ONLY
- **Ports 20000+**: Reserved for future use

### Port Selection Guidelines
- Document chosen ports in `.agents/tester/MOCK_TESTS.md`
- Use consistent ports for same test scenarios
- Check port availability before starting test
- Always kill processes on ports before and after test
