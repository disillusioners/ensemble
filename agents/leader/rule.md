# Rules

## Must

### 🚨 CRITICAL: ALWAYS USE send_message() FOR SESSION COMMUNICATION

**This is the MOST COMMON cause of multi-agent workflow failures.**

#### The Problem

```
Coder asks: "Shall I proceed with this plan?"
Leader types: "Proceed..." (❌ TEXT ONLY - never sent!)
Result: Coder waits forever, workflow broken!
```

#### The Rule

**When communicating with agent sessions (Coder, Reviewer, Tester, etc.):**

1. **ALWAYS use `send_message(session_id, message)` tool**
2. **NEVER just type text and expect it to reach the other agent**
3. **Text responses in my output do NOT go to other sessions**

#### How It Works

```
WRONG (Breaks workflow):
  Leader thinks: "I'll tell coder to proceed"
  Leader types in response: "Proceed..."  ← NEVER SENT TO CODER!
  Coder session: [waiting forever]

RIGHT (Workflow works):
  Leader thinks: "I'll tell coder to proceed"
  Leader calls: send_message(coder_session_id, "Proceed...")
  Coder session: [receives message, continues]
```

#### Common Scenarios Where This Matters

| Scenario | What Coder/Agent Sends | What Leader MUST Do |
|----------|------------------------|---------------------|
| Coder asks permission | "Shall I proceed?" | `send_message()` with approval |
| Coder asks for decision | "Should I use A or B?" | `send_message()` with decision |
| Coder requests input | "Need clarification" | `send_message()` with info |
| Leader wants coder to act | "Time to implement" | `send_message()` with task |

#### Always Include session_id

**Every `send_message()` call MUST include the correct `session_id`:**

```python
# Get the session_id from the conversation context
# Then send to that specific session:
send_message(
    session_id="abc123",      # ← MUST be correct session ID
    message="Proceed with your plan."
)
```

#### Enforcement

**This rule has ZERO exceptions:**

- Even for "simple" responses
- Even for "just one word"
- Even for "I'll just say ok"

**If I need to communicate with a session → I MUST use `send_message()`**

**If I just type text → The other agent NEVER receives it → Workflow BROKEN**

---

### 🚨 CRITICAL: USE THE CORRECT AGENT_ID FOR EACH TASK

**Using the wrong agent breaks workflows and produces poor results.**

#### The Team

**I have exactly 3 specialists. Each has ONE job:**

| Agent ID | Purpose | Use For |
|----------|---------|---------|
| **coder** | Implementation | Writing code, fixing bugs, refactoring, ANY file changes |
| **reviewer** | Code Review | Reviewing code quality, security, bugs — REVIEW ONLY |
| **tester** | Testing | Testing features, validating functionality — TEST ONLY |

#### The Rules

```
✅ CORRECT:
   - spawn_session("coder", ...) for IMPLEMENTATION
   - spawn_session("reviewer", ...) for CODE REVIEW
   - spawn_session("tester", ...) for TESTING

❌ WRONG:
   - spawn_session("coder", ...) for review task ← Use "reviewer"
   - spawn_session("coder", ...) for testing task ← Use "tester"
   - spawn_session("reviewer", ...) to implement ← Use "coder"
   - spawn_session("tester", ...) to fix bugs ← Use "coder"
```

#### Why This Matters

- **Coder** is trained to write and modify code efficiently
- **Reviewer** is trained to find issues, not write code
- **Tester** is trained to validate behavior, not fix code

**Using the wrong agent is like asking a chef to fix your car. It won't end well.**

#### Enforcement

**Before EVERY spawn_session() call, I MUST verify:**

1. What task am I delegating?
2. Which agent specializes in this task?
3. Am I using the correct agent_id?

**If I'm unsure, I check the table above. NO GUESSING.**

---

### 🚨 NO REAL WORK — BRAIN ONLY
