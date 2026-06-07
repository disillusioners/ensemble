# OpenCode-Skill

OpenCode is an external agent system for code generation and execution. This opencode-skill provides instructions to interact with OpenCode's Orchestrator agent, allowing you to delegate complex coding tasks and retrieve results seamlessly.

## Prerequisites

**OpenCode running**: The OpenCode service must be running at `http://127.0.0.1:4095`.

## Context Before Delegation

Shared context is populated by the `explore()` tool. 

`external_opencode_send_message` tool scans the shared-context directory for files that match the **topics** of the outgoing task and prepends them to the prompt. The accuracy of that match — and therefore whether the remote opencode session receives the right context — depends on the `related_context_keywords` you pass.

**Always pass `related_context_keywords`** with 3-8 short topic phrases on every `external_opencode_send_message` call. The keywords are what the matcher scores against, so vague or missing keywords lead to a wrong (or empty) context preload.

### How to derive your keywords

When you are about to call `external_opencode_send_message`, look at the `message` you are sending and pick the **3-8 noun-phrases a human would use to file this task**. They must be concrete, topic-specific, and drawn from the message itself. Look the guide below:

- **Pull from the modules, components, and concepts named in the message** — backtick-quoted terms, CamelCase identifiers, file paths, and proper nouns are the strongest signal.
  - If your message is `"Refactor the `auth` flow to use OAuth"`, pass `["auth", "OAuth", "login flow"]`.
  - If your message is `"Update PaymentModule to handle refund edge cases"`, pass `["PaymentModule", "refund", "edge cases"]`.
- **Add the area the task belongs to** — the subsystem / feature / domain the work touches. This is what most shared-context files are filed under.
  - If your message is `"Add a /healthz endpoint that pings the DB"`, also include `"health check"`, `"endpoint"`, `"DB ping"`.
- **Stay short and topic-level** — 1-3 words per phrase. No full sentences, no verbs in isolation. The matcher scores by token overlap, so brevity and topic density beat prose.
- **Drop filler / generic words** — never include `"code"`, `"task"`, `"implement"`, `"the"`, `"function"`, `"feature"`. They dilute the signal without adding information.
- **Aim for 4-6 keywords** — too few (≤2) is ambiguous; too many (8+) lets noise dilute the score.
- **Tips** - keywords often is a mix of query keywords that you used in explore() to gather context, and new keywords that are specific to the task at hand.


## Tool Inventory

| Tool | Blocking? | Purpose |
|------|-----------|---------|
| `external_opencode_init_session` | No | Create/replace a named session |
| `external_opencode_send_message` | No | Send prompt (fire-and-forget) |
| `external_opencode_get_status` | No | Check state + response + questions |
| `external_opencode_wait_for_result` | Yes | Block until session completes (30s poll, max 10min) |
| `external_opencode_wait_any` | Yes | Block until ANY session completes |
| `external_opencode_answer_question` | No | Answer interactive questions |
| `external_opencode_resume_session` | No | Resume a timed-out session |
| `external_opencode_abort_session` | No | Abort and reset to IDLE |

## Tool Reference

All `external_opencode_send_message` calls return **immediately** (fire-and-forget). The Orchestrator continues processing in the background. Use `external_opencode_wait_for_result` / `external_opencode_wait_any` to retrieve results.

```python
# Create or replace a session (re-init auto-aborts the old one)
external_opencode_init_session(
    project="<PROJECT>",          # e.g. "myapp"
    session_name="<SESSION_NAME>", # e.g. "feature-login"
    working_dir="<WORKING_DIR>",   # absolute path to project root
)

# Fire-and-forget send (message can also be a command: "continue", "retry", "abort")
external_opencode_send_message(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    message="<MESSAGE>",
    related_context_keywords=["<TOPIC_PHRASE>", ...],  # 3-8 short topic phrases
)

# Non-blocking status check
external_opencode_get_status(project="<PROJECT>", session_name="<SESSION_NAME>")

# Block until one session completes (30s poll, max 10min)
external_opencode_wait_for_result(project="<PROJECT>", session_name="<SESSION_NAME>", timeout=600)

# Block until ANY of the given sessions completes
external_opencode_wait_any(sessions=[
    {"project": "<PROJECT>", "session_name": "<SESSION_NAME>"},
    ...
], timeout=600)

# Answer an interactive question (see "Interactive Questions" below)
external_opencode_answer_question(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    request_id="<REQUEST_ID>",
    answers=["<ANSWER>", ...],  # one entry per question
)

# Resume a timed-out session
external_opencode_resume_session(project="<PROJECT>", session_name="<SESSION_NAME>")

# Abort and reset to IDLE
external_opencode_abort_session(project="<PROJECT>", session_name="<SESSION_NAME>")
```

## Workflow: Single Session

End-to-end pattern for one task. Answer questions inline as they appear; do not poll for status unless you need to.

```python
# 1. Initialize
external_opencode_init_session(
    project="myapp",
    session_name="feature-login",
    working_dir="/Users/me/projects/my-app",
)

# 2. Send the task (include any gathered context in the message)
external_opencode_send_message(
    project="myapp",
    session_name="feature-login",
    message=(
        "Add a /login endpoint to the FastAPI app. "
        "Use the existing User model in app/models/user.py. "
        "Return a JWT in an httpOnly cookie. "
        "Write tests in tests/test_auth.py and run pytest before finishing."
    ),
    related_context_keywords=["auth", "login", "JWT", "User model", "FastAPI"],
)

# 3. Handle any questions, then wait for completion
external_opencode_get_status(project="myapp", session_name="feature-login")
# -> if status == WAITING_FOR_INPUT, call external_opencode_answer_question(...)

external_opencode_wait_for_result(
    project="myapp",
    session_name="feature-login",
    timeout=600,
)
```

The Orchestrator handles planning, execution, and cleanup automatically.

## Workflow: Parallel Sessions (Async)

Run up to **3 sessions in parallel** for independent tasks. `wait_any` returns as soon as one finishes, so you can slot a new task into the freed slot without waiting for the others.

> **⚠️ IMPORTANT: Only use for tasks with NO dependencies.** Parallel sessions must not rely on each other's output or modify the same files.

```python
# 1. Initialize all sessions first
for name in ("task-1", "task-2", "task-3"):
    external_opencode_init_session(
        project="myapp",
        session_name=name,
        working_dir="/Users/me/projects/my-app",
    )

# 2. Fire all messages (non-blocking, returns immediately)
external_opencode_send_message(
    project="myapp", session_name="task-1",
    message="Refactor app/db.py to use SQLModel and add an index on users.email.",
    related_context_keywords=["SQLModel", "db.py", "users.email", "index"],
)
external_opencode_send_message(
    project="myapp", session_name="task-2",
    message="Add a /healthz endpoint that pings the DB and returns 200/503.",
    related_context_keywords=["healthz", "endpoint", "DB ping", "health check"],
)
external_opencode_send_message(
    project="myapp", session_name="task-3",
    message="Upgrade pytest to >=8 and fix any failing tests in tests/.",
    related_context_keywords=["pytest", "upgrade", "test fixes", "tests"],
)

# 3. Wait for the first to complete, then handle its result
external_opencode_wait_any(sessions=[
    {"project": "myapp", "session_name": "task-1"},
    {"project": "myapp", "session_name": "task-2"},
    {"project": "myapp", "session_name": "task-3"},
], timeout=600)
# Suppose task-1 finished. Review it, then start task-4 in its slot.

# 4. Slot in a new task on the freed session slot
external_opencode_init_session(
    project="myapp", session_name="task-4", working_dir="/Users/me/projects/my-app",
)
external_opencode_send_message(
    project="myapp", session_name="task-4",
    message="Add a Makefile target `make seed` that loads fixtures from data/.",
    related_context_keywords=["Makefile", "make seed", "fixtures", "data"],
)

# 5. Wait for the next to complete (task-2, task-3, or task-4)
external_opencode_wait_any(sessions=[
    {"project": "myapp", "session_name": "task-2"},
    {"project": "myapp", "session_name": "task-3"},
    {"project": "myapp", "session_name": "task-4"},
], timeout=600)

# 6. Repeat step 4-5 until all sessions have reported a result.
```

## Interactive Questions

If the Orchestrator asks a question, `external_opencode_get_status` returns it with a `request_id`:

```text
status: WAITING_FOR_INPUT
request_id: req_abc123
question: Which linter should I use?
options: [...]
```

**CRITICAL INSTRUCTION**: When a question is received:
1.  **Suggest** the best answer to the user based on context.
2.  **Ask** the user for confirmation.
3.  **DO NOT** automatically answer unless the user explicitly tells you to "auto-answer" or "decide for me".

**To answer:**

```python
# Single question
external_opencode_answer_question(
    project="myapp",
    session_name="feature-login",
    request_id="req_abc123",
    answers=["ESLint"],
)

# Multiple questions in one request (one entry per question, in order)
external_opencode_answer_question(
    project="myapp",
    session_name="feature-login",
    request_id="req_abc123",
    answers=["ESLint", "Jest"],
)
```

## Error Handling

**"The operation timed out"**: Call `external_opencode_resume_session()` one or two times. If it times out repeatedly, the task is too large and should be split into smaller tasks.

```python
external_opencode_resume_session(project="<PROJECT>", session_name="<SESSION_NAME>")
```

**Session is busy**: Wait with `external_opencode_wait_for_result()` or abort with `external_opencode_abort_session()`.
