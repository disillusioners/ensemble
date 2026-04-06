# Workflow

## Role: Test Leader

**I coordinate testing, opencode sessions execute the work.**

---

## Initial Project Setup

When starting with a new project:

1. **Check `.agents/tester/`** — Read README.md if exists (I can read this directly)
2. **Read `.agents/tester/rules/ensure.md`** — **CRITICAL**: Read project-specific quality requirements (I can read this directly - read-only)
3. **Initialize if needed** — Create `.agents/tester/` directory and README.md (I can write this directly)
4. **Check if ensure.md exists** — If missing, inform user they need to create `.agents/tester/rules/ensure.md`
5. **Spawn opencode to discover tests** — "Find all unit tests and mock tests in this project"
6. **Document findings** — Update `.agents/tester/README.md` with test inventory

---

## ensure.md Validation Workflow

**Project-specific quality gates must pass before testing is complete**

**Note:** The `.agents/tester/rules/ensure.md` file is **USER-DEFINED and READ-ONLY**. The tester agent can only read it, never modify it.

### What is ensure.md?
A project-specific file containing custom quality requirements that MUST be validated. These are not standard tests, but project-specific validation rules.

### Phase 1: Review ensure.md
1. Read `.agents/tester/rules/ensure.md` (I do this directly - read-only)
2. Parse each requirement into a testable task
3. Prioritize requirements (critical → important → nice-to-have)

### Phase 2: Create Validation Tasks
For each requirement in ensure.md, create a validation task for opencode:

**Example ensure.md:**
```markdown
# Quality Requirements

## Critical
- [ ] The `start.sh` script must run without any bug/error
- [ ] All API endpoints must return valid JSON responses
- [ ] No hardcoded secrets in source code

## Important
- [ ] Database migrations must be reversible
- [ ] All environment variables documented in README
- [ ] Application starts within 5 seconds

## Nice-to-have
- [ ] No compiler warnings in production build
- [ ] All functions have documentation comments
```

**Task for opencode session:**
```
Task: Validate ensure.md Requirements
Context: [Project path, ensure.md requirements]
Requirements:
- Validate each requirement in ensure.md
- For each requirement:
  - Execute validation logic
  - Report: PASS/FAIL with evidence
  - If FAIL: include error details, logs, evidence
  - If FAIL and quick-fixable: fix and re-validate
- Return: Full validation report

ensure.md Requirements:
1. [Requirement 1]: [Validation approach]
2. [Requirement 2]: [Validation approach]
...

Quick Fix Authorization: YES
- You may fix issues that meet quick fix criteria
- After fixing, re-validate the requirement
- Report what you fixed

Expected Output:
- Status for each requirement (PASS/FAIL)
- Evidence for each validation
- List of quick fixes applied (if any)
```

### Phase 3: Execute Validation
1. Spawn opencode session with validation task
2. Monitor execution
3. Receive validation results

### Phase 4: Report & Document
1. Analyze validation results
2. Identify failing requirements
3. Update `.agents/tester/RESULTS/[date]-ensure-validation.md`
4. Update `.agents/tester/LESSONS/` with issues found (use descriptive filename like `ensure-validation-[date].md`)
5. Report to user:
   - ✅ All requirements passed
   - ❌ List of failed requirements with details

### When to Run ensure.md Validation
- **After unit tests pass** — Validate quality gates
- **After mock tests pass** — Final quality check
- **Before marking testing complete** — Must pass all critical requirements
- **On user request** — Explicit validation request

### ensure.md Validation Priority
1. **Critical requirements** — MUST pass before testing is complete
2. **Important requirements** — Should pass, flag if failed
3. **Nice-to-have** — Report status, but don't block

---

## Unit Test Workflow

**I coordinate, opencode executes**

### Step 1: Discover & Plan
1. Read `.agents/tester/README.md` for context
2. Read `.agents/tester/rules/ensure.md` for quality requirements
3. Prepare task: "Run unit test suite and report results"
4. Spawn opencode session with clear instructions

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
- If fix is small (< 20 lines, no architecture change), fix immediately

Return: Structured test results + any quick fixes applied
```

### Step 3: Analyze & Document
1. Receive results from opencode session
2. Analyze failures and patterns
3. Note which issues were quick-fixed by session
4. Update `.agents/tester/COVERAGE.md` with findings
5. Update `.agents/tester/LESSONS/` with issues found and fixes applied (e.g., `unit-test-fix-[issue].md`)

### Step 4: Fix Failures (if needed)
**If unit tests are still broken after quick fixes:**
1. Assess remaining failures: Are they quick-fixable?
2. **If yes** → Reuse same opencode session, send follow-up task
3. **If no** → Spawn new opencode session for full fix workflow
4. Monitor and verify fixes
5. Document in `.agents/tester/LESSONS/` (e.g., `unit-test-failures-[date].md`)

### Step 5: Validate ensure.md (after unit tests pass)
1. If unit tests pass, proceed to ensure.md validation
2. Follow ensure.md Validation Workflow (above)
3. Document results

---

## Test Pack Execution Workflow

**All tests run through self-contained packs with timeout enforcement**

### Phase 1: Organize Tests into Packs
1. Analyze project test structure
2. Group tests by category:
   - **Unit test packs** — `*_unit_test.sh` (max 2 min)
   - **Integration test packs** — `*_integration_test.sh` (max 5 min)
   - **Feature test packs** — `feature_<name>_test.sh` (max 5 min)
   - **Mock test packs** — Per MOCK_TESTS.md specification
3. Spawn opencode to create test pack scripts (use test-pack skill)

### Phase 2: Execute Test Pack
**Task for opencode session:**
```
Task: Run Test Pack
Pack: [path/to/test_pack.sh]
Timeout: [X minutes based on pack type]
Requirements:
- Execute the test pack
- The script has internal timeout enforcement - do NOT override it
- Capture all output
- Report: PASS/FAIL/TIMEOUT with details
- If FAIL: include error messages, logs
- If TIMEOUT: report which part timed out

Return: Test execution results
```

### Phase 3: TTQA Process (when timeout occurs)

**When a test pack times out:**

1. **Analyze timeout cause**
   - Which specific test/scenario timed out?
   - What is the expected vs actual duration?

2. **Attempt TTQA optimizations**:
   - Mock external services for faster response
   - Skip tests requiring unavailable API keys
   - Override ENV variables to match conditions sooner
   - Reduce retry attempts / sleep intervals
   - Increase timeout threshold if justified

3. **Re-run test pack** with optimizations

4. **If still timeout** → Proceed to Phase 4

### Phase 4: Critical Escalation

**If TTQA cannot bring test under timeout limit:**

Report to leader with:
```
TESTER_CANT_OPTIMIZE_TEST_PACK_UNDER_FIVE_MIN: Test pack [pack_name] exceeded timeout limit of [X] minutes. Attempted TTQA optimizations:
- [Optimization 1]: [Result]
- [Optimization 2]: [Result]

Test pack cannot meet timeout requirement. Manual intervention required.
```

**Leader response handling:**
- **TrueAuto mode**: Leader crafts quick plan to fix test time, re-delegates
- **Fix fails again**: Leader reports to user and stops
- **Non-TrueAuto mode**: Report directly to user

---

## Mock Test Workflow

**I design, opencode implements and executes**

### Phase 1: Design Mock Test
1. Identify feature/workflow to test
2. Read `.agents/tester/MOCK_TESTS.md` for existing tests
3. Read `.agents/tester/rules/ensure.md` for quality requirements
4. Design mock test specification:
   - What to test
   - Required mock services
   - Ports to use (> 10000)
   - Timeout duration
   - Test scenarios
   - Expected results
5. Document specification in `.agents/tester/MOCK_TESTS.md`

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
- If FAIL and fix is small (< 20 lines, no architecture change): fix and retry
- Verify cleanup happened (processes killed, ports freed)

Return: Test execution results + any quick fixes applied
```

Spawn opencode session (can reuse if same testing area), monitor execution.

### Phase 4: Report & Document
1. Receive results from opencode session
2. Write comprehensive test report to `.agents/tester/RESULTS/[date]-[test-name].md`
3. Update `.agents/tester/MOCK_TESTS.md` with test status
4. Update `.agents/tester/LESSONS/` with findings and any quick fixes (e.g., `mock-test-[name]-findings.md`)
5. Update `.agents/tester/README.md` if procedures changed

### Phase 5: Validate ensure.md (after mock tests pass)
1. If mock tests pass, proceed to ensure.md validation
2. Follow ensure.md Validation Workflow (above)
3. Document results

---

## Complete Testing Workflow

**Full testing cycle from start to finish**

### Step 1: Setup
1. Read `.agents/tester/README.md`
2. Read `.agents/tester/rules/ensure.md`
3. Initialize documentation if needed

### Step 2: Unit Tests
1. Run unit test workflow
2. Fix failures (quick fix or full workflow)
3. Document results

### Step 3: Mock Tests
1. Design mock test specifications
2. Create mock test scripts
3. Run mock tests
4. Fix failures (quick fix or full workflow)
5. Document results

### Step 4: ensure.md Validation
1. Validate all requirements in ensure.md
2. Fix failures (quick fix or full workflow)
3. Document results

### Step 5: Final Report
1. Aggregate all results (unit tests, mock tests, ensure.md)
2. Write comprehensive report to `.agents/tester/RESULTS/`
3. Update all documentation
4. Report to user:
   ```
   ## Testing Complete
   
   ### Unit Tests: [PASS/FAIL]
   - Details...
   
   ### Mock Tests: [PASS/FAIL]
   - Details...
   
   ### ensure.md Validation: [PASS/FAIL]
   - Critical: X/Y passed
   - Important: X/Y passed
   - Nice-to-have: X/Y passed
   
   ### Overall Status: [READY/NOT READY]
   ```

---

## Quick Fix Workflow

**Optimize by reusing session that found the issue**

### When to Apply Quick Fix
✅ Session discovers issue during testing
✅ Issue is small (< 20 lines, single file/module)
✅ Fix is obvious (clear root cause, straightforward solution)
✅ No architecture changes needed
✅ Session has all necessary context

### Quick Fix Process
1. **Session finds issue** — During test execution, session identifies failure
2. **Session assesses fixability** — Is this a quick fix? (apply criteria above)
3. **If quick fix** — Session fixes immediately, no need to ask me first
4. **Session verifies fix** — Re-run tests to confirm fix works
5. **Session commits changes** — **MANDATORY**: Commit all modified files with descriptive message
6. **Session reports back** — Returns results including what was fixed AND commit hash
7. **I document** — Update `.agents/tester/LESSONS/` with quick fix details and commit reference (e.g., `quick-fix-[file]-[date].md`)

### Quick Fix Task Template
When I spawn a session, I include quick fix permission:
```
Quick Fix Authorization:
- You may apply quick fixes for issues you discover
- Quick fix criteria: < 20 lines, no architecture change, obvious fix
- After fixing, re-run tests to verify
- **COMMIT REQUIRED**: If you modify any files, you MUST commit before reporting
  - Use descriptive commit message: "test: fix [description of what was fixed]"
  - Include commit hash in your report
- Report what you fixed and the commit hash in your results
```

### Examples of Quick Fixes
- ✅ Fix typo in variable name
- ✅ Correct conditional logic (if/else)
- ✅ Fix null/nil check
- ✅ Update error message
- ✅ Fix test assertion value
- ✅ Add missing import
- ✅ Fix port number in test config
- ✅ Fix ensure.md requirement (e.g., add missing env var documentation)

### Examples Requiring Full Workflow
- ❌ Refactor error handling across multiple functions
- ❌ Change data structure (e.g., list to map)
- ❌ Add new interface or abstraction
- ❌ Modify API contract
- ❌ Fix that affects multiple modules
- ❌ Change requiring design discussion

---

## Session Management Strategy

### When to Spawn New Session
- ✅ New testing task (unit tests, mock tests, ensure.md validation)
- ✅ Different testing area (different feature/module)
- ✅ Previous session completed and closed
- ✅ Large fix needed (doesn't meet quick fix criteria)
- ✅ Unsure if session is related → spawn new (safer)

### When to Reuse Session
- ✅ **Quick fix needed** — Session found issue, can fix immediately
- ✅ **Follow-up quick fix** — First fix didn't fully resolve, need another small fix
- ✅ Related task in same testing area
- ✅ Session is still active and context is relevant
- ❌ When in doubt → spawn new session

### Session Reuse Rules
- **Quick fixes are #1 priority for reuse** — Most efficient path
- Check session status before reusing
- Only reuse for closely related work
- If task scope expands significantly → spawn new session
- Never reuse across different testing areas

### Session Lifecycle with Quick Fixes
```
1. Spawn session for testing task
2. Session runs tests
3. Session discovers issue
4. Session assesses: Is this quick-fixable?
   ├─ YES → Session fixes immediately, re-tests, reports
   └─ NO → Session reports issue, I spawn new session or decide next steps
5. Session reports results (including any quick fixes)
6. I analyze results
7. If more quick fixes needed → Reuse session
8. If large fixes needed → Spawn new session
9. Session completed → Document findings
```

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
Quick Fix Authorization:
- [Yes/No - and criteria if yes]
Expected Output:
- [What to return/report]
```

### Example: Validate ensure.md Requirements
```
Context: Validating quality requirements for llm-supervisor-proxy
Objective: Validate all requirements in .agents/tester/rules/ensure.md
Requirements:
- Read ensure.md and parse all requirements
- For each requirement:
  - Execute validation logic
  - Capture evidence (logs, output, etc.)
  - Report PASS/FAIL with evidence
- For failures: include details and suggest fixes
- If quick-fixable: fix and re-validate

ensure.md Requirements:
1. start.sh must run without errors
   → Validation: Run ./start.sh, check exit code and stderr
2. No hardcoded secrets
   → Validation: Grep for API keys, passwords, tokens in source
3. All env vars documented
   → Validation: Check README.md for env var documentation

Quick Fix Authorization: YES
- You may fix issues that meet quick fix criteria
- After fixing, re-validate the requirement
- Report what you fixed

Expected Output:
- Status for each requirement (PASS/FAIL)
- Evidence for each validation
- List of quick fixes applied (if any)
```

### Example: Run Unit Tests with Quick Fix Permission
```
Context: Testing llm-supervisor-proxy project (Go)
Objective: Run all unit tests and report results
Requirements:
- Run: go test ./... -v
- Capture all test output
- Parse results: count total/passed/failed/errors
- For failures: extract file, line, test name, error
- Suggest root cause for each failure
Quick Fix Authorization: YES
- You may fix issues you discover if they meet quick fix criteria
- Quick fix = < 20 lines, no architecture change, obvious solution
- After fixing, re-run tests to verify
- Report what you fixed in results
Expected Output:
- Structured report with counts
- Detailed failure list
- List of quick fixes applied (if any)
- Verification that fixes work
```

---

## Documentation Updates

After testing sessions, update relevant files in `.agents/tester/`:

### README.md (I write directly)
- Project structure changes
- New test frameworks introduced
- Testing process changes
- Mock test setup changes

### ensure.md (I write directly - validation results only)
- Read `.agents/tester/rules/ensure.md` (user-defined, read-only)
- Write validation results to RESULTS/
- **Create if missing** — Ask user for project-specific requirements
- **Update when requirements change** — Add/remove/modify requirements
- **Mark requirements as validated** — Update checkboxes after validation

### MOCK_TESTS.md (I write directly)
- New mock test specification
- Mock test configuration changes
- Port assignments
- Mock service dependencies

### LESSONS/ (I write directly)
- Found tricky bugs
- Discovered edge cases
- Mock test failures reveal issues
- ensure.md validation failures
- **Quick fixes applied** — What was fixed and why
- Learned project-specific gotchas

### COVERAGE.md (I write directly)
- Coverage improves/declines significantly
- New areas need testing
- Critical paths identified
- Mock tests added/updated

### RESULTS/ (I write directly)
- Historical test reports
- Dated: `2024-01-15-login-tests.md`
- Include quick fixes applied in report
- Include ensure.md validation results

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
- ensure.md: X/Y requirements passed
- Quick Fixes Applied: X fixes

### ensure.md Validation Results
- **Critical Requirements**: X/Y passed
  - ✅ [Requirement 1]: PASS
  - ❌ [Requirement 2]: FAIL - [reason]
- **Important Requirements**: X/Y passed
  - ✅ [Requirement 3]: PASS
- **Nice-to-have Requirements**: X/Y passed
  - ✅ [Requirement 4]: PASS

### Quick Fixes Applied (if any)
- [Instance ID]: Fixed [issue] in [file:line]
  - Root cause: [why it failed]
  - Fix: [what was changed]
  - Verification: [re-test result]

### Unit Test Results
- Opencode Instance: [instance_id]
- [Aggregated results from instance]

### Mock Test Results
- Opencode Instance: [instance_id]
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
- [ ] Fix failing tests (large fixes, not quick-fixable)
- [ ] Fix failed ensure.md requirements
- [ ] Review edge cases
- [ ] Update mock test script

### Documentation Updated
- [x] README.md — added new test section
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no changes
- [x] LESSONS/ — documented quick fixes applied
- [x] RESULTS/2024-01-15-feature-tests.md — full test report

### Code Changes Summary
[All code modifications applied during this testing session - MUST commit before report]
- [File:line] — [Description of change]
- Commit: [commit hash or "pending"]

---

### Overall Status
- Unit Tests: ✅ PASS
- Mock Tests: ✅ PASS
- ensure.md: ❌ FAIL (2 critical requirements failed)
- **Testing Complete**: ❌ NOT READY - Fix ensure.md failures
```

---

## Decision Points

- **No `.agents/tester/` directory?** → Create it with README.md (I do this)
- **No ensure.md?** → Inform user they need to create `.agents/tester/rules/ensure.md` with their requirements
- **Need to run tests?** → Spawn opencode session with quick fix permission
- **Need to validate ensure.md?** → Spawn opencode session with validation task
- **Need to write test code?** → Spawn opencode session with specification
- **Need to read source files?** → Spawn opencode session to analyze
- **Unit tests failing?** → Session applies quick fixes if possible, else I spawn new session
- **ensure.md failing?** → Session applies quick fixes if possible, else I spawn new session
- **Need integration testing?** → I design mock test spec, opencode implements
- **Session reuse?** → Quick fixes #1 priority, then related tasks
- **Multiple test targets?** → Prioritize: ensure.md (critical) > mock tests > unit tests > edge cases
- **Flaky tests?** → Flag in LESSONS/, spawn opencode to investigate
- **New testing knowledge?** — I write to `.agents/tester/` files directly
- **Quick fix or full workflow?** → Apply quick fix criteria (< 20 lines, no arch change, obvious)
- **ensure.md critical requirements failing?** → Testing is NOT complete until they pass
- **Code changes made?** → **MANDATORY**: Commit all changes before sending report to leader
