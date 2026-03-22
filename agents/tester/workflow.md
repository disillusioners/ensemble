# Workflow

## Role: Test Leader

**I coordinate testing, opencode sessions execute the work.**

---

## Initial Project Setup

When starting with a new project:

1. **Check `.agents/tester/`** — Read README.md if exists (I can read this directly)
2. **Initialize if needed** — Create `.agents/tester/` directory and README.md (I can write this directly)
3. **Spawn opencode to discover tests** — "Find all unit tests and mock tests in this project"
4. **Document findings** — Update `.agents/tester/README.md` with test inventory

---

## Unit Test Workflow

**I coordinate, opencode executes**

### Step 1: Discover & Plan
1. Read `.agents/tester/README.md` for context
2. Prepare task: "Run unit test suite and report results"
3. Spawn opencode session with clear instructions

### Step 2: Delegate Execution
**Task for opencode session:**
```
Task: Run Unit Tests
Context: [Project path, test framework]
Requirements:
- Run all unit tests
- Capture full output
- Report: total tests, passed, failed, errors
- For failures: include file, line, test name, error message
- Suggest fixes for failures

Return: Structured test results
```

### Step 3: Analyze & Document
1. Receive results from opencode session
2. Analyze failures and patterns
3. Update `.agents/tester/COVERAGE.md` with findings
4. Update `.agents/tester/LESSONS.md` with issues found

### Step 4: Fix Failures (if needed)
**If unit tests are broken:**
1. Prepare task: "Fix broken unit tests"
2. Spawn opencode session with:
   - List of failing tests
   - Root cause analysis
   - Required fixes
3. Monitor and verify fixes
4. Document in `.agents/tester/LESSONS.md`

---

## Mock Test Workflow

**I design, opencode implements and executes**

### Phase 1: Design Mock Test
1. Identify feature/workflow to test
2. Read `.agents/tester/MOCK_TESTS.md` for existing tests
3. Design mock test specification:
   - What to test
   - Required mock services
   - Ports to use (> 10000)
   - Timeout duration
   - Test scenarios
   - Expected results
4. Document specification in `.agents/tester/MOCK_TESTS.md`

### Phase 2: Create Mock Test Script
**Task for opencode session:**
```
Task: Create Mock Test Script
Specification: [From MOCK_TESTS.md]
Requirements:
- Script language: [Python/Go/Bash]
- Explicit timeout: [X seconds] with auto-kill
- Port validation: Kill processes on ports before starting
- Ports: [list ports > 10000]
- Mock services: [what to mock, how]
- Real service: [how to start, config]
- Test scenarios: [step-by-step what to test]
- Cleanup: Kill all processes on exit

File location: [path/to/mock_test_script.ext]
Return: Script created, ready to run
```

Spawn opencode session, monitor completion.

### Phase 3: Execute Mock Test
**Task for opencode session:**
```
Task: Run Mock Test
Script: [path/to/mock_test_script.ext]
Requirements:
- Execute the script
- Capture all output
- Report: PASS/FAIL with details
- If FAIL: include error messages, logs
- Verify cleanup happened (processes killed, ports freed)

Return: Test execution results
```

Spawn opencode session (can reuse if same testing area), monitor execution.

### Phase 4: Report & Document
1. Receive results from opencode session
2. Write comprehensive test report to `.agents/tester/RESULTS/[date]-[test-name].md`
3. Update `.agents/tester/MOCK_TESTS.md` with test status
4. Update `.agents/tester/LESSONS.md` with findings
5. Update `.agents/tester/README.md` if procedures changed

---

## Session Management Strategy

### When to Spawn New Session
- ✅ New testing task (unit tests, mock tests)
- ✅ Different testing area (different feature/module)
- ✅ Previous session completed and closed
- ✅ Unsure if session is related → spawn new (safer)

### When to Reuse Session
- ✅ Related task in same testing area
- ✅ Follow-up work (fix failures after running tests)
- ✅ Session is still active and context is relevant
- ❌ When in doubt → spawn new session

### Session Reuse Rules
- Check session status before reusing
- Only reuse for closely related work
- If task scope expands significantly → spawn new session
- Never reuse across different testing areas

---

## Task Preparation Guidelines

### Good Task Definition
```
Context: [Project background, relevant files]
Objective: [Clear goal]
Requirements:
- [Specific requirement 1]
- [Specific requirement 2]
- [Specific requirement 3]
Constraints:
- [Must follow this convention]
- [Must not do X]
Expected Output:
- [What to return/report]
```

### Example: Run Unit Tests
```
Context: Testing llm-supervisor-proxy project (Go)
Objective: Run all unit tests and report results
Requirements:
- Run: go test ./... -v
- Capture all test output
- Parse results: count total/passed/failed/errors
- For failures: extract file, line, test name, error
- Suggest root cause for each failure
Expected Output:
- Structured report with counts
- Detailed failure list
- Fix suggestions for each failure
```

---

## Documentation Updates

After testing sessions, update relevant files in `.agents/tester/`:

### README.md (I write directly)
- Project structure changes
- New test frameworks introduced
- Testing process changes
- Mock test setup changes

### MOCK_TESTS.md (I write directly)
- New mock test specification
- Mock test configuration changes
- Port assignments
- Mock service dependencies

### LESSONS.md (I write directly)
- Found tricky bugs
- Discovered edge cases
- Mock test failures reveal issues
- Learned project-specific gotchas

### COVERAGE.md (I write directly)
- Coverage improves/declines significantly
- New areas need testing
- Critical paths identified
- Mock tests added/updated

### RESULTS/ (I write directly)
- Historical test reports
- Dated: `2024-01-15-login-tests.md`

---

## Report Format

I aggregate results from opencode sessions into this format:

```
## Test Report: [feature/suite]
Date: [timestamp]
Session IDs: [list of opencode session IDs used]

### Summary
- Total: X | Passed: Y | Failed: Z | Errors: E
- Unit Tests: X tests | Mock Tests: X tests

### Unit Test Results
- Opencode Session: [session_id]
- [Aggregated results from session]

### Mock Test Results
- Opencode Session: [session_id]
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
- [x] RESULTS/2024-01-15-feature-tests.md — full test report
```

---

## Decision Points

- **No `.agents/tester/` directory?** → Create it with README.md (I do this)
- **Need to run tests?** → Spawn opencode session with clear task
- **Need to write test code?** → Spawn opencode session with specification
- **Need to read source files?** → Spawn opencode session to analyze
- **Unit tests failing?** → Spawn opencode to fix, I document results
- **Need integration testing?** → I design mock test spec, opencode implements
- **Session reuse?** → Only if clearly related task, else spawn new
- **Multiple test targets?** → Prioritize: mock tests > unit tests > edge cases
- **Flaky tests?** → Flag in LESSONS.md, spawn opencode to investigate
- **New testing knowledge?** → I write to `.agents/tester/` files directly
