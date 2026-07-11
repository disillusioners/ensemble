# Workflow

## Role: Test Leader

**I coordinate testing, opencode sessions execute the work.**

## Workflow Overview

```mermaid
flowchart TD
    A([Test work requested]) --> BR{"Assess blast radius<br/>(even if full requested)"}
    BR -->|"small / isolated change"| SC["Scope down: relevant packs only · report reduction"]
    BR -->|"big / critical / architecture"| B["Plan full: list packs + estimate runtime"]
    SC --> C
    B --> C
    C[Split any pack estimated &gt; 5 min]
    C --> D["todo_graph_create: nodes=packs, edges=deps"]
    D --> E{Pre-send self-check each message}
    E -->|fail| E
    E -->|pass| F["Launch: 1 session per pack, parallel if independent"]

    F --> G1[Session A] & G2[Session B] & G3[Session C]
    G1 & G2 & G3 --> H["Opencode: run single pack · timeout 300"]
    H --> I{Result}
    I -->|PASS| J["todo_graph_update → done"]
    I -->|FAIL| K{Quick fix &lt; 20 lines?}
    K -->|yes| L[Fix + re-run this pack] --> H
    K -->|no| M[Spawn full-fix session] --> H
    I -->|TIMEOUT| N[TTQA optimizations] --> O[Re-run] --> I2{Result}
    I2 -->|PASS| J
    I2 -->|TIMEOUT| P["Test Architecture Fix · test-code only"] --> Q[Re-run] --> I3{Result}
    I3 -->|PASS| J
    I3 -->|TIMEOUT| R[Escalate TESTER_CANT_OPTIMIZE]

    J --> S[Aggregate all per-pack results]
    S --> T[Validate ensure.md]
    T --> U["Document: PACKS.md + LESSONS before/after"]
    U --> V([Report to user])
```

Key shape: **parallel fan-out** at launch (independent packs run concurrently), **per-pack fix loops** (quick fix → re-run; TTQA → Architecture Fix → escalate), and **maintenance is mandatory** before escalation.

---

## Planning Phase (Do This First!)

**Before spawning any sessions, plan how to execute the work.**

### Why Plan First?
- Avoids spawning too many/few sessions
- Enables parallel execution when appropriate
- Reduces total testing time
- Prevents wasted capacity

### Blast Radius Control (First Gate — Before Listing Packs)

**Even on an explicit "full test suite" request, assess the real scope of change first.** A huge suite — even parallelized — costs real time and resources. Don't run it unless the change warrants it.

**Derive the change set from any available signal** (no explicit phase context required):
1. Request details / user message wording
2. Shared context: `.agents/shared/planning/`, conventions, recent commits
3. Spawn opencode to inspect `git diff` / changed files / affected modules (I cannot run git directly)
4. PACKS.md pack-to-module mapping

**Decision:**

| Change shape | Action |
|--------------|--------|
| Small / isolated (few files, single module, no architecture impact) | **Reduce scope** to relevant packs only — even if "full" was requested. Report the reduction + reason. |
| Big / critical (cross-module, architecture refactor, release gate) | Full suite is justified → proceed to Split & Parallel. |
| Ambiguous / unknown | Default to scoped run of directly-affected packs; offer to expand. Don't default to "run everything". |
| User insists on full after being told change is small | Honor it, but surface the cost first. |

**Default:** the smallest scope that covers the change. When in doubt, scope down and offer to expand — not the reverse.

**Report template:** `"Full requested; change touches [X files / N modules] → running [packs], skipping [packs]. Full suite [warranted / not warranted]. Reason: [why]."`

### Planning Checklist

1. **Identify all work to do**
   - List all test packs that need execution
   - Note any dependencies between packs
   - Identify ensure.md validations needed

2. **Assess parallelism**
   - **Independent?** → Can run in parallel (different modules, no shared state)
   - **Dependent?** → Must run sequentially (shared resources, order matters)
   - **Parallelizable?** → 2+ independent groups of packs

3. **Determine execution strategy**

   | Scenario | Strategy |
   |----------|----------|
   | 1 independent pack | 1 session |
   | 2-3 small packs (same module) | 1 session (grouped) |
   | 3+ independent packs (different modules) | Multiple sessions in parallel |
   | Mixed dependencies | Parallel + sequential |

4. **Group packs into sessions**
   - Group by: module, test type, or execution environment
   - Keep unrelated packs in separate sessions
   - Consider quick fix context (reuse same module)

5. **Set execution order**
   - Order dependent packs
   - Launch independent groups simultaneously
   - Note which validations run after tests pass

6. **Materialize the plan as a todo graph** (do this right after planning)
   - Call `todo_graph_create(nodes=<packs>, edges=<dependencies>)` — one node per pack.
   - Prefer `todo_graph_*` over `todo_list_*` **because of parallelism**: the DAG expresses which packs run concurrently (no edge between them) vs. which must wait (edge = dependency). A flat list cannot represent fan-out/fan-in.
   - Independent packs → sibling nodes with no edge (run in parallel).
   - Dependent packs → edge from prerequisite to dependent (e.g., `api_mock_test` waits on `api_unit_test`).
   - Add a final aggregation/ensure.md node with edges from every pack so it only goes ready once packs finish.
   - As sessions launch/complete, keep the graph current with `todo_graph_update(node_id, status)` (`in_progress` → `done`). The response tells you the next-ready nodes.

### Planning Rules
- **Never skip planning** — Always analyze before spawning
- **Parallel when safe** — Independent packs benefit from parallelism
- **Group related packs** — Same module = same session (better context)
- **When in doubt, split** — Separate sessions are safer than mis-grouped ones
- **Plan for aggregations** — Know how you'll combine results from multiple sessions

### Execution Strategy Examples

**Example 1: Multiple independent unit test packs**
```
Packs: auth_unit_test, api_unit_test, db_unit_test
Plan: Spawn 3 sessions in parallel (one per pack)
Expected: 10 min total instead of 30 min sequential
```

**Example 2: Phase-scoped testing with some skipped**
```
Context: Changes in auth/ module only
Packs: auth_unit_test, api_unit_test, db_unit_test
Plan: Run auth_unit_test only (others irrelevant)
Sessions: 1 session for 1 pack
```

**Example 3: Unit tests + mock tests**
```
Packs: core_unit_test, api_unit_test
Mock tests: api_mock_test (needs unit tests first)
Plan: 
  - Session 1: core_unit_test + api_unit_test (parallel)
  - Session 2: api_mock_test (sequential, after Session 1)
Sessions: 2 (1 parallel group, 1 sequential)
```

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

**I coordinate, opencode executes. When working on a phase, prefer running only relevant unit test packs.**

### Step 1: Discover & Plan
1. Read `.agents/tester/README.md` for context
2. Read `.agents/tester/rules/ensure.md` for quality requirements
3. **If phase context provided**: Scope to relevant test packs only
4. **List the unit test packs to run** (one per module/scope). Estimate each pack's runtime; split any pack > 2 min (unit hard limit) into smaller packs.
5. **Plan sessions**: one opencode session per pack; independent packs in parallel.
6. Prepare the strict message (Step 2) per pack — never a single "run unit tests" message.

### Step 2: Delegate Execution (per pack)
**Use the "Run Single Test Pack" strict message template** (see Test Pack Execution Workflow). Send one message per pack, one session per pack. Never send a bare "run unit tests" / `go test ./...` / `pytest tests/` message.

```
Task: Run Single Test Pack
Pack: [exact path/to/<module>_unit_test.sh]   # exactly ONE pack
Estimated runtime: [X min, must be < 2 for unit]
... (rest of the strict template — see "Run Single Test Pack" section)
```

- Independent unit packs → launch in parallel (one session each).
- Run the Pre-Send Self-Check before each send.

### Step 3: Analyze & Document
1. Receive results from all opencode sessions
2. Aggregate per-pack PASS/FAIL/TIMEOUT; analyze failures and patterns
3. Note which issues were quick-fixed by sessions
4. Update `.agents/tester/COVERAGE.md` with findings
5. Update `.agents/tester/LESSONS/` with issues found and fixes applied (e.g., `unit-test-fix-[issue].md`)

### Step 4: Fix Failures (if needed)
**If unit tests are still broken after quick fixes:**
1. Assess remaining failures: Are they quick-fixable?
2. **If yes** → Reuse same opencode session, send follow-up task (still single-pack scoped)
3. **If no** → Spawn new opencode session for full fix workflow
4. Monitor and verify fixes
5. Document in `.agents/tester/LESSONS/` (e.g., `unit-test-failures-[date].md`)

### Step 5: Validate ensure.md (after unit tests pass)
1. If unit tests pass, proceed to ensure.md validation
2. Follow ensure.md Validation Workflow (above)
3. Document results

---

## Test Pack Execution Workflow

**All tests run through self-contained packs with timeout enforcement. When working on a phase, prefer running only relevant test packs.**

### ⚠️ Planning Step (Do This First!)

1. **List all packs to run** — Based on phase context or full test request
2. **Assess parallelism** — Which packs are independent?
3. **Group into sessions** — Related packs together, unrelated packs separate
4. **Determine spawn order** — Sequential for dependent, parallel for independent

**See Planning Phase (above) for full guidance.**

### Phase-Scoped Testing (Productivity Optimization)

**When leader provides phase context:**

- **Input format**: Leader provides a list of changed file paths or module names
- **Relevance heuristic**: Match file paths to pack names via naming convention (e.g., `src/auth/` → `auth_unit_test.sh`)
- **Ambiguity handling**: If <50% of packs are relevant, run scoped only; if ≥50% are relevant, run all packs
- **Scope reporting template**: `"Running: [packs]. Skipped: [packs]. Reason: [no changed files in X modules]."`

**Decision flow:**
1. Receive changed files from leader
2. Match each file to test packs by naming convention
3. Calculate relevance ratio (matched packs / total packs)
4. If <50%: run only relevant packs, report skipped packs
5. If ≥50%: run all packs (more efficient than skipping few)
6. Report scope to leader using the template above

**No context case:** Do NOT default to "run everything." Apply **Blast Radius Control (First Gate)** above — derive the change set from request/shared context/git diff, and reduce scope when the change is small, even on a "full suite" request.

### Check: Pack Existence Gate

1. **Check if test packs exist for this project.**
2. **YES** → Proceed to Execute Test Pack
3. **NO** → Proceed to Organize Tests into Packs, then Execute

### Organize Tests into Packs
1. Analyze project test structure
2. Group tests by category (see timeout limits in rule.md):
   - **Unit test packs** — `<module>_unit_test`
   - **Integration test packs** — `<module>_integration_test`
   - **E2E test packs** — `<module>_e2e_test`
   - **Mock test packs** — Per MOCK_TESTS.md specification
3. Spawn opencode to create test pack scripts (use test-pack skill)

### Pre-Send Self-Check (Run Before EVERY opencode Message)

Before sending any test-execution message to opencode, verify **all** of the following. If any check fails, fix the message before sending.

- [ ] **Single pack** — Message names exactly ONE pack path (no "run all tests", no bare `go test ./...` / `pytest tests/`)
- [ ] **Scope locked** — Message explicitly forbids running any other pack/test
- [ ] **Command-level timeout** — Message includes the 5-min wrapper (`timeout 300 ...` or `subprocess.run(..., timeout=300)`)
- [ ] **Script-internal timeout** — Pack script self-timeouts at ≤ 5 min (dual-layer confirmed)
- [ ] **Time estimate** — Pack estimated < 5 min (if not, split before sending)
- [ ] **PACKS.md valid** — Pack path exists and is registered in PACKS.md
- [ ] **Override documented** — If long-timeout case, overridden config/env is intentional and documented

### Run Single Test Pack — Strict Message Template (MANDATORY)

**This is the ONLY acceptable message format for running a test pack.** Never send a free-form "run the tests" message — that is what causes opencode to run the full suite at once.

```
Task: Run Single Test Pack
Pack: [exact path/to/<scope>_<type>_test.sh]   # exactly ONE pack
Estimated runtime: [X min, must be < 5]

CONSTRAINTS (do NOT violate):
- Run ONLY the pack at the path above. Do NOT run any other pack or test.
- Do NOT run broad commands like `go test ./...`, `pytest tests/`, `npm test`, `jest`.
- Wrap the run with a 5-min command-level timeout:
    bash:   `timeout 300 ./path/to/<scope>_<type>_test.sh`
    python: `subprocess.run([..., "<pack>"], timeout=300)`
- The pack script also has its own internal timer — do NOT disable or extend it.
- Do NOT "discover and run" extra tests to "be thorough".

Requirements:
- Execute the single pack with the timeout wrapper above.
- Capture all output.
- Report final status: PASS / FAIL / TIMEOUT  (exit 0 / 1 / 124).
- If FAIL: include file, line, test name, error message for each failure.
- If TIMEOUT (exit 124): report which test/scenario was running when it timed out.
- If a quick fix is possible (< 20 lines, no architecture change): fix, re-run, and report the fix + commit hash.

Return:
- RESULT: PASS|FAIL|TIMEOUT
- Failures (if any): [file:line] test — reason
- Quick fixes applied (if any): what + commit hash
- Actual runtime: [X min]
```

**Long-timeout case variant:** If the pack contains tests that inherently need long waits (retries/sleeps/polls), add an `ENV OVERRIDES` block to the same template so the pack still finishes < 5 min:
```
ENV OVERRIDES (intentional, documented in MOCK_TESTS.md):
- RETRY_COUNT=1            # was 5
- SLEEP_INTERVAL_MS=50     # was 1000
- MOCK_ENDPOINT=http://127.0.0.1:10080   # fast local mock, was real service
```
Do **not** raise the 5-min cap to fit a slow test. Override the config/env instead.

### Execute Test Pack
**If phase context provided:** Only run packs relevant to changed files. Report skipped packs.
Send the **Run Single Test Pack** message above (one per opencode session). Run the Pre-Send Self-Check before each send.

### Full Project Test: Split & Parallel Workflow

**When a full project test is requested** (no phase scope, or leader explicitly wants everything):

1. **List every pack** from PACKS.md and estimate each pack's runtime.
2. **Split any pack estimated > 5 min** into smaller packs until every pack is < 5 min.
3. **Group by independence:**
   - Independent packs → launch in **parallel** (one opencode session + one strict message per pack).
   - Dependent packs → run sequentially in the required order.
4. **Send each message** using the Run Single Test Pack template, after passing the Pre-Send Self-Check.
5. **Aggregate** PASS/FAIL/TIMEOUT from every session into one report. One pack's TIMEOUT does not block the others.
6. **For any TIMEOUT** → run the TTQA Loop on that single pack (do not re-run the whole project).

```
Example: 6 packs, all independent, ~3 min each
Plan: 6 parallel sessions, each gets one strict "Run Single Test Pack" message
Expected: ~3 min total (parallel) instead of ~18 min (sequential) or 1 opaque timeout (all-at-once)
```

### TTQA Loop (when timeout occurs)

**When a test pack times out:**

1. **Analyze timeout cause**
   - Which specific test/scenario timed out?
   - What is the expected vs actual duration?

2. **Attempt TTQA optimizations** (see rule.md for canonical list)

3. **Re-run test pack** with optimizations

4. **If still timeout** → Proceed to Test Architecture Fix (NOT straight to escalation)

### Test Architecture Fix Workflow

**When:** a pack cannot fit under its timeout, or test-architecture quality issues are found (bloated pack, slow setup, order-dependent tests, missing mocks, repeated real-service calls).

**Principle:** Fix right after finding — this is the tester's maintenance duty, not optional. Test-code architecture changes are the tester's job and are NOT blocked by the production "no architecture change" rule. TTQA is a patch for this run; this workflow is the permanent fix.

#### Step 1: Diagnose
- Which pack/test is slow? Root cause (real service call, repeated setup, sleeps, shared state, sheer size)?
- Estimate before/after runtime and target limit (≤ 5 min; unit ≤ 2 min).

#### Step 2: Choose fix path
- **Small test fix (< 20 lines, test code):** quick-fix path — delegate to opencode, fix, re-run, commit, report.
- **Larger test-architecture refactor (≥ 20 lines, test code only):** use the task below — still immediate, do not defer.

#### Step 3: Delegate Test Architecture Fix (opencode)
```
Task: Test Architecture Fix
Pack: [path/to/<scope>_<type>_test]   # the slow/bloated pack
Problem: [root cause — e.g., calls real DB, sleeps 2s/test, 400 tests in one pack]
Target: pack must finish < [2|5] min after fix.

CONSTRAINTS (do NOT violate):
- Modify TEST code only. Do NOT change production/source code behavior.
- Preserve coverage/equivalence — same scenarios validated, just faster/leaner.
- Each resulting pack keeps dual-layer timeout (command-level `timeout 300` + script-internal).
- Follow test-pack skill for any new/split scripts.

Approaches (apply as needed):
- Split into multiple smaller packs (and update PACKS.md)
- Mock slow external dependencies
- Reduce/parameterize sleeps, retries, waits (or move to overridden config/env)
- Share/remove redundant setup; isolate order-dependent tests
- Parallelize within a pack where safe

Return:
- What changed (before/after runtime, files touched)
- New PACKS.md entries if packs were split
- Re-run RESULT: PASS|FAIL|TIMEOUT with actual runtime
- Commit hash
```

#### Step 4: Verify & Document
- Re-run the fixed/split pack(s); confirm each is under its timeout.
- Update PACKS.md (new packs, last run, status).
- Write LESSONS/[descriptive].md: root cause, fix applied, before/after runtime.
- Update COVERAGE.md if structure changed.

### Escalation

**Only after a Test Architecture Fix has been attempted and verified insufficient:**

Report to leader with:
```
TESTER_CANT_OPTIMIZE_TEST_PACK: Test pack [pack_name] exceeded timeout limit of 5 minutes.
Attempted TTQA optimizations:
- [Optimization 1]: [Result]
Attempted Test Architecture Fix:
- [Fix 1]: [before → after runtime, still over limit]
Root cause that resists fixing: [reason]

Test pack cannot meet timeout requirement. Manual intervention required.
```

**Leader response handling:**
- **TrueAuto mode**: Leader crafts quick plan to fix test time, re-delegates
- **Fix fails again**: Leader reports to user and stops
- **Non-TrueAuto mode**: Report directly to user

---

## Flaky Test Handling

**Definition**: A flaky test passes and fails across multiple runs with no code changes.

**Detection**: If a test fails on run 1 but passes on run 2 with no fixes applied → mark as potentially flaky.

**Action**:
1. Run the test 3 times
2. If results show ≥1 pass AND ≥1 fail → flag as flaky:
   - Document in `.agents/tester/LESSONS/` (e.g., `flaky-test-[test-name].md`)
   - Skip in future test runs until resolved
   - Report to leader

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

**Full testing cycle from start to finish. When testing a phase, scope tests to changed code only.**

### Step 0: Plan (Do This First!)
1. **Identify all work** — List test packs, ensure.md validations, mock tests needed
2. **Assess parallelism** — Which tasks are independent?
3. **Group into sessions** — Related packs together, unrelated packs separate
4. **Determine spawn order** — Sequential for dependent, parallel for independent
5. **Create todo graph** — `todo_graph_create(nodes=<packs>, edges=<deps>)`; prefer `todo_graph_*` over `todo_list_*` because the DAG tracks parallel fan-out/fan-in. Update nodes as sessions complete.

### Step 1: Setup
1. Read `.agents/tester/README.md`
2. Read `.agents/tester/rules/ensure.md`
3. Initialize documentation if needed

### Step 2: Phase-Scoped Unit Tests
1. **If phase context provided**: Identify relevant test packs
2. Run unit test workflow (scoped or full based on context)
3. Fix failures (quick fix or full workflow)
4. Document results

### Step 3: Phase-Scoped Mock Tests
1. **If phase context provided**: Only mock tests relevant to phase
2. Design mock test specifications
3. Create mock test scripts
4. Run mock tests
5. Fix failures (quick fix or full workflow)
6. Document results

### Step 4: ensure.md Validation
1. Validate requirements in ensure.md (always full - quality gates)
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
Objective: Run ONE unit test pack (scoped to a single module)
Pack: ./tests/packs/auth_unit_test.sh   # exactly ONE pack — NOT `go test ./...`
Constraints:
- Run ONLY this pack. Do NOT run `go test ./...` or any other pack.
- Wrap with 5-min command-level timeout: `timeout 300 ./tests/packs/auth_unit_test.sh`
- Pack script has its own internal timer — do not disable it.
Requirements:
- Execute the single pack with the timeout wrapper above.
- Capture all test output.
- Report final status: PASS / FAIL / TIMEOUT (exit 0 / 1 / 124).
- For FAIL: extract file, line, test name, error per failure.
- Suggest root cause for each failure.
Quick Fix Authorization: YES
- You may fix issues you discover if they meet quick fix criteria
- Quick fix = < 20 lines, no architecture change, obvious solution
- After fixing, re-run THIS pack only to verify
- Report what you fixed + commit hash in results
Expected Output:
- RESULT: PASS|FAIL|TIMEOUT
- Structured report with counts
- Detailed failure list
- List of quick fixes applied (if any) + commit hash
- Actual runtime: [X min]
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

### Scope Decision (include whenever scope was reduced)
> Based on my intelligent decision, the full test suite was reduced to: [list packs run] because [reason — e.g., change touches only 3 files in 1 module; running the full suite would burn ~40 min across 24 packs for a non-architecture change]. Skipped: [list packs]. Full suite not warranted.
- If full suite WAS run: state "Full suite run — warranted: [reason — e.g., cross-module architecture refactor]."

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

- **Starting testing work?** → PLAN FIRST: Analyze work, assess parallelism, group packs into sessions
- **After planning?** → `todo_graph_create` (nodes=packs, edges=deps); prefer `todo_graph_*` over `todo_list_*` for parallelism
- **Need to run tests?** → Send ONE strict "Run Single Test Pack" message per pack (never "run the tests"); pass Pre-Send Self-Check first
- **Full project test requested?** → FIRST assess blast radius (derive change set from request/shared context/git diff). If change is small/isolated → reduce scope to relevant packs even though "full" was requested; report reduction. Only run the full suite if the change is big/critical/architecture — then Split & Parallel.
- **Pack estimated > 5 min?** → Split it further before spawning (do NOT raise the cap)
- **Test needs long waits/retries?** → Override config/env in a separate pack; never relax the 5-min cap
- **Pack timed out after TTQA?** → Run Test Architecture Fix (fix root cause) right after — do NOT escalate yet
- **Pack bloated/slow but didn't timeout?** → Still fix it (maintenance duty); don't wait for a timeout
- **Test-architecture fix needs > 20 lines?** → Use Test Architecture Fix workflow (test code only, not blocked by no-architecture-change rule)
- **Tempted to send `go test ./...` / `pytest tests/`?** → STOP. That is forbidden. Use the strict single-pack template.
- **No `.agents/tester/` directory?** → Create it with README.md (I do this)
- **No ensure.md?** → Inform user they need to create `.agents/tester/rules/ensure.md` with their requirements
- **Phase context provided?** → Scope tests to relevant packs only, report scope to leader
- **No phase context?** → Do NOT default to "run everything" — apply Blast Radius Control: derive the change set and reduce scope when small; full suite only if the change is big/critical
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
