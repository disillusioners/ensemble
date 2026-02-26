# agents/leader/

## Responsibility

The Leader agent is the **orchestrator/conductor** of the multi-agent system. It coordinates tasks across specialized agents, delegates work appropriately, and integrates results into cohesive deliverables. The Leader does not micromanage—it provides clear direction and context, then trusts specialist agents to execute their work independently.

**Core responsibilities:**
- Parse and understand user requests
- Break down complex tasks into subtasks
- Delegate to the right specialized agents (coder, reviewer)
- Integrate outputs from multiple agents into unified responses
- Manage the async workflow using fire-and-forget pattern
- Learn and evolve from experience

---

## Design

### Agent Configuration (meta.json)
| Property | Value |
|----------|-------|
| ID | leader |
| Name | Leader |
| Description | Coordinates tasks and manages workflow delegation |
| Icon | 👑 |
| Color | accent-amber |
| Version | 1.0.0 |

### Core Philosophy
- **"I delegate, then I'm done"** — After sending a task, the Leader moves on. It does not poll, check status, or wait.
- **Fire and Forget** — Reports arrive automatically as new messages in the conversation
- **Leverage** — Delegate to specialists who are better suited for specific tasks
- **Precision** — Ambiguity is the enemy; clear instructions with all necessary context

### Communication Model
- **Async messaging**: Tasks are sent to child agents and reports return automatically
- **Session-based**: Each child agent runs in a session identified by session_id
- **No polling**: The system delivers reports without the Leader checking for them

---

## Skills

### Coordination Skill (`skills/coordination/skill.md`)
The primary skill enabling the Leader to orchestrate work across agents.

**Key capabilities:**
1. **Agent reuse** — Check for existing idle agents before spawning new ones; maintains efficiency and context
2. **Delegation clarity** — Provide clear, specific instructions with all necessary context; agents only know what they're told
3. **Result integration** — Combine outputs from multiple agents into unified responses; resolve conflicts and fill gaps

---

## Integration Points

### Known Agents
| Agent | Role |
|-------|------|
| `coder` | Code implementation specialist |
| `reviewer` | Code review specialist |

### Workflow Integration
1. **Task Input** → User provides request
2. **Parse & Plan** → Break into subtasks, identify dependencies
3. **Delegate** → Spawn agents, send tasks (fire and forget)
4. **Integrate** → Receive automatic reports, combine results
5. **Deliver** → Present final output to user
6. **Learn** → Record observations in memory.md
7. **Evolve** → Propose improvements per growth.md

### Session Management
- Creates new agent sessions via `spawn_session`
- Sends tasks via `send_message` (fire and forget)
- Monitors active sessions via `list_sessions`
- Queries session details via `get_session_info` (rarely needed)
- Terminates sessions via `terminate_session` (only after receiving completion report)

---

## Key Files

| File | Purpose |
|------|---------|
| `soul.md` | Core identity — defines the Leader's philosophy as a conductor, not a micromanager |
| `tools.md` | Tool definitions — session management tools (spawn_session, send_message, list_sessions, get_session_info, terminate_session) |
| `rule.md` | Operational rules — must/must-not constraints for delegation |
| `workflow.md` | Task processing workflow — Understand → Plan → Delegate → Integrate → Deliver → Learn → Evolve |
| `meta.json` | Agent metadata — id, name, description, icon, color, version |
| `memory.md` | Persistent knowledge — known agents (coder, reviewer), project context |
| `user.md` | User information placeholder — filled as the Leader learns about the user |
| `growth.md` | Self-evolution guidance — uses `inner_soul` tool to remember and change |
| `skills/coordination/skill.md` | Coordination skill definition — agent reuse, delegation clarity, result integration |

---

## Rules Summary

### Must Do
- Confirm understanding before delegating
- Provide clear, specific instructions to agents
- Trust the async system — reports arrive automatically
- Process reports when they arrive

### Must NOT Do
- Poll child sessions with `get_session_info`
- Terminate child sessions after sending (they're still working)
- Wait, loop, or check status — send and move on
- Assume silence means failure
- Spawn more than 5 child sessions simultaneously
- Ignore errors from child agents
