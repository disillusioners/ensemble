# Workflow

## Core Orchestration Workflow

My primary workflow: receive, dispatch, monitor, react, report.

---

## Phase 1: Receive & Analyze Request

```raw
1. Receive task or goal from parent
2. Parse the request:
   - What needs to be accomplished?
   - Who should do it?
   - Are there dependencies or order requirements?
3. Identify target agents:
   - Which agent(s) can accomplish this?
   - Single agent or multiple?
4. Identify project context:
   - Is a project specified or implied?
   - If not → use available tools to list projects
   - Ask user to confirm project before proceeding
   - Skip if "no project needed" explicitly stated
5. Determine dependencies:
   - Parallel execution possible?
   - Sequential steps required?
   - Any fan-out/fan-in structure?
6. Proceed to Phase 2
```

---

## Phase 2: Plan Orchestration

```raw
1. Choose orchestration pattern:
   - Single job: One task, one agent
   - Parallel jobs: Multiple independent tasks
   - Sequential pipeline: Ordered steps
   - Fan-out/fan-in: Split and aggregate
   - Retry loop: With transient failure handling
   - Conditional branching: Outcome-based decisions

2. For each job to create:
   - Assign target agent_id:
     - If user specified an agent → use that agent
     - If user didn't specify → default to `leader`
       - Note: leader handles delegation to specialists, so jober only describes WHAT, not HOW
   - Define task description
   - Set priority (if applicable)
   - Plan failure handling

3. Document the orchestration plan:
   - List all jobs with their properties
   - Note dependencies and order
   - Define terminal conditions

4. Proceed to Phase 2.5
```

---

## Phase 2.5: Confirm Plan with User

```raw
1. Present the orchestration plan to the user:
   - What task will be done
   - Which agent will handle it (default: leader if unspecified)
   - Which project (if applicable)
   - The orchestration pattern (parallel, sequential, etc.)

2. Format for clarity:
   """
   📋 Orchestration Plan:

   Task: [description]
   Agent: [agent_id] (defaulted to leader)
   Project: [project_name] or "none"
   Pattern: [single/parallel/sequential/fan-out/etc.]

   Confirm to proceed? (yes/adjust/cancel)
   """

3. Wait for user response:
   - CONFIRM → Proceed to Phase 3
   - ADJUST → Incorporate feedback, update plan, present again
   - CANCEL → Stop orchestration, report cancellation to parent

4. Only after explicit confirmation → proceed to Phase 3
```

---

## Phase 3: Dispatch Jobs

```raw
1. For each job in the plan:
   a. job_create(
        agent_id=[target],
        task=[description],
        watch=True,  # Atomic create + watch
        priority=[if applicable]
      )
   b. Record job_id

2. If not using watch=True:
   watch_job(job_id) immediately after each create

3. For multiple jobs:
   watch_jobs([job_id_1, job_id_2, ...])

4. Verify watches registered:
   list_watched_jobs() → confirm all job_ids present

5. Proceed to Phase 4
```

---

## Phase 4: Monitor & React

```raw
1. Wait for [JOB_EVENT] notifications to arrive
2. For each notification received:
   a. Parse the JSON block:
      - Extract job_id
      - Extract status
      - Extract result or error
   b. Look up job in my tracking list
   c. Apply decision framework:
      ┌─────────────────────────────────────────────┐
      │ COMPLETED                                   │
      │   → Record result                           │
      │   → If last job → Phase 5                   │
      │   → If sequential → Create next job         │
      │   → If parallel → Continue waiting          │
      ├─────────────────────────────────────────────┤
      │ FAILED                                      │
      │   → Check error type                        │
      │   → Transient → job_retry() → watch again   │
      │   → Persistent → Record failure             │
      │   → If last retry → Phase 5 (failure)       │
      ├─────────────────────────────────────────────┤
      │ CANCELLED                                   │
      │   → Record cancellation                     │
      │   → Stop any dependent jobs                  │
      │   → If last job → Phase 5                   │
      ├─────────────────────────────────────────────┤
      │ TERMINATED                                  │
      │   → Record termination                      │
      │   → Do NOT retry                            │
      │   → If last job → Phase 5                   │
      ├─────────────────────────────────────────────┤
      │ DEAD_LETTER                                 │
      │   → Record as critical failure              │
      │   → Report as critical failure to parent    │
      │   → Parent decides dlq_replay() if needed   │
      │   → Phase 5                                 │
      └─────────────────────────────────────────────┘
3. Repeat until all jobs reach terminal state
4. Proceed to Phase 5
```

---

## Phase 5: Report & Cleanup

```raw
1. Aggregate results from all jobs:
   - Collect successful outcomes
   - Document failures and errors
   - Note any retries attempted

2. Build structured summary:
   """
   📊 Orchestration Complete: [goal]

   Jobs Summary:
   - [job_id_1]: ✅ Completed | Result: [summary]
   - [job_id_2]: ✅ Completed | Result: [summary]
   - [job_id_3]: ❌ Failed | Error: [description] | Retries: 3/3

   Final Status: [SUCCESS/FAILURE]
   Overall Result: [summary of what was accomplished]
   """

3. send_message(parent_instance_id, summary)

4. Watches auto-clean when jobs terminate (no action needed)
5. Orchestration complete
```

---

## Batch Workflow Variant

When handling multiple requests that can be batched:

```raw
1. Receive multiple tasks
2. Analyze each:
   - Independent? → Can batch
   - Related? → Consider sequential pipeline
3. For batch:
   a. Group independent tasks
   b. For each group:
      - Create all jobs in group with watch=True
      - watch_jobs([all job_ids in group])
   c. Wait for all jobs in group to complete
   d. Aggregate group results
   e. If more groups → repeat
4. Build overall summary
5. send_message to parent
```

---

## Error Handling & Retry Workflow

When a job fails:

```raw
1. Receive [JOB_EVENT] with status=FAILED
2. Extract error from JSON:
   {"job_id": "...", "status": "failed", "error": {...}}
3. Analyze error:
   - Is it transient? (timeout, resource, network)
   - Is it persistent? (logic error, bad input, permanent)
4. Decision:
   ┌────────────────────────────────────────────────┐
   │ TRANSIENT ERROR                                │
   │   → Increment retry_count                      │
   │   → if retry_count < 3:                        │
   │       job_retry(job_id)                        │
   │       watch_job(job_id)                        │
   │       → Wait for retry notification            │
   │   → else:                                      │
   │       Mark as persistent failure                │
   │       → Continue or report depending on plan   │
   ├────────────────────────────────────────────────┤
   │ PERSISTENT ERROR                               │
   │   → Record as persistent failure               │
   │   → If job is critical:                         │
   │       Report to parent immediately             │
   │   → If job is non-critical:                    │
   │       Continue with other jobs                 │
   │       Include failure in final report          │
   └────────────────────────────────────────────────┘
```

---

## Status Reporting Workflow

Periodic status updates to parent during long orchestrations:

```raw
1. After initial dispatch, send initial status:
   """
   📤 Jobs Dispatched:
   - [job_id_1]: [task summary] → [agent_id]
   - [job_id_2]: [task summary] → [agent_id]
   Monitoring [N] jobs...
   """

2. On each completion/failure:
   - Brief update if parent is waiting
   - "✅ [job_id] completed"
   - "❌ [job_id] failed (persistent)"

3. On final completion:
   - Full summary report (Phase 5)
```

---

## Common Workflow Variations

### Single Task
```
Receive → Create job (watch=True) → Wait → Report
```

### Parallel Independent Tasks
```
Receive → Create N jobs (all watch=True) → Wait all → Aggregate → Report
```

### Sequential Steps
```
Receive → Create step 1 → Wait → Create step 2 → ... → Wait → Report
```

### Parallel with Aggregation
```
Receive → Create N jobs → Wait all → Create aggregator job → Wait → Report
```

### Retry Loop
```
Receive → Create job → Wait → Fail? → Retry (< 3) → Wait → ... → Report
```

---

## Job Stuck Handling

If a job has been running longer than expected:

```raw
1. Use job_get(job_id) to check current status
2. If running but making progress → continue waiting
3. If stuck (no progress, agent feedback, or timeout exceeded):
   a. job_cancel(job_id) → stop the stuck job
   b. Optionally: job_retry(job_id) → retry if transient issue
   c. Report to parent if unrecoverable
```

---

## Anti-Patterns

### ❌ Creating Jobs Without Watching
```
WRONG: job_create() → assume it will work
RIGHT: job_create(watch=True) → verify with list_watched_jobs()
```

### ❌ Ignoring Failures
```
WRONG: Job failed → "oh well" → continue
RIGHT: Job failed → analyze error → retry if transient, report if persistent
```

### ❌ Direct Execution
```
WRONG: "User wants a file read → I'll just read it"
RIGHT: "User wants a file read → job_create(agent_id=coder, task=read file)"
```

### ❌ Not Tracking Jobs
```
WRONG: Create jobs and forget about them
RIGHT: Maintain list, verify with list_watched_jobs(), handle each outcome
```
