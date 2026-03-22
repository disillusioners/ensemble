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

### 🚨 NO REAL WORK — BRAIN ONLY