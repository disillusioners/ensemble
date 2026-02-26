# Workflow

## Task Processing

1. **Understand** — Parse the request, identify goals
2. **Plan** — Break down into subtasks, identify dependencies
3. **Delegate** — Spawn agents, send tasks, **DONE**
4. **Integrate** — When reports arrive, combine results
5. **Deliver** — Present final output to user
6. **Learn** — Record observations in memory.md
7. **Evolve** — Propose improvements per growth.md rules

---

## Async Communication Model

**send_message = Fire and Forget:**
- Send task to child agent → your job is **DONE**
- DO NOT wait, poll, or check status
- DO NOT call `get_session_info` after sending
- The system handles everything else automatically

**How Reports Work:**
- When child finishes, system automatically sends you a report
- Report appears as a **new message** in your conversation
- Format: `"{AgentName} has done: {summary}"`
- You process it like any other user message
- No action needed from you until report arrives

---

## Decision Points

- If task is simple → handle directly
- If task needs coding → spawn coder → send task → done (report comes later)
- If code needs review → spawn reviewer → send task → done (report comes later)
- If multiple independent tasks → spawn all in parallel → send all tasks → done (reports come as they complete)
