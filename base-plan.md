# plan.md — Persistent Multi-Session Agent Daemon (LangGraph)

## 1. Purpose

Build a **long-running daemon** that hosts conversational agents as independent sessions.

* Only **one LangGraph definition** exists.
* Each running session = one live agent instance.
* Agent behavior is defined by a **directory of markdown files** (not code).
* Sessions can spawn other sessions to collaborate (leader → teammates).
* All reasoning is driven by LLM + conversation.
* The runtime itself is domain-agnostic and tool-agnostic.

This system is closer to an **OS for reasoning processes** than an automation tool.

---

## 2. Core Concepts

### 2.1 Agent = Data, Not Code

An agent is created by loading a directory:

```
agents/
 ├── leader/
 ├── developer/
 ├── reviewer/
```

Each contains:

```
skill.md
workflow.md
rule.md
memory.md
```

These files become the system prompt layers that define personality and capability.

No agent classes exist.

---

### 2.2 Session = A Running Agent

A session is a live LangGraph state machine:

```
Session =
    Graph Instance
  + Agent Directory
  + Conversation History
  + Persistent State
```

Sessions are long-lived and resumable.

They are not re-created per request.

---

### 2.3 The Daemon Hosts Sessions

The daemon is responsible for lifecycle, not intelligence.

It exposes control APIs:

```
spawn_session(agent_dir, session_id)
send_message(session_id, message)
get_session_state(session_id)
list_sessions()
terminate_session(session_id)
```

The daemon never interprets meaning.
It only routes messages and maintains state.

---

## 3. Collaboration Model (Leader → Team)

Agents never “simulate” each other.

Instead, a leader session **creates real sessions**.

Example flow:

1. Leader decides delegation is needed.
2. Leader emits structured action:

   ```
   { "action": "spawn_session", "agent": "agents/developer", "id": "task_42" }
   ```
3. Daemon creates a new independent session.
4. Leader sends instructions via:

   ```
   { "action": "send_message", "session": "task_42", "message": "Implement retry logic." }
   ```
5. Worker responds independently.

All cooperation happens through the daemon, not shared prompts.

---

## 4. Role of Markdown Files

### `skill.md` — Capabilities

Describes what the agent *can* do and how to invoke tools.
No logic is enforced — only explained to the LLM.

### `workflow.md` — Methodology

Defines the agent’s thinking loop (e.g., plan → act → verify).

### `rule.md` — Constraints

Hard behavioral boundaries the agent must follow.

### `memory.md` — Long-Term Knowledge

Writable knowledge that persists across sessions and evolves slowly.

---

## 5. Conversation Is the Only Context

The runtime does NOT track:

* projects
* repositories
* environments
* tasks

All operational context must come from user or other sessions via chat.

This keeps the system fully generic.

---

## 6. LangGraph Responsibilities

LangGraph is used only as a **durable execution engine**:

* Maintain conversational state.
* Control reasoning → action → observation loop.
* Allow pause/resume at any time.
* Persist checkpoints.
* Recover after crashes.
* Execute structured actions safely.

We use LangGraph as a state machine, not an “agent framework.”

---

## 7. Single Graph Topology (Per Session)

Each session runs the same minimal loop:

```
[Receive Message]
        ↓
[LLM Reasoning]
        ↓
[Optional Action Execution]
        ↓
[Persist State]
        ↓
[Wait For Next Message]
```

No multi-agent graph.
No branching orchestration.
Scaling = more sessions.

---

## 8. Session Persistence Model

Each session has its own storage:

```
sessions/
 ├── leader_001/
 ├── task_42/
 ├── review_42/
```

Contains:

* conversation log
* LangGraph checkpoint
* runtime metadata

This allows independent restart and inspection.

---

## 9. System Boundaries (Important)

The runtime MUST NOT contain:

* domain logic
* coding assumptions
* project models
* workflow enforcement
* tool-specific code paths

All specialization must live inside agent directories.

---

## 10. Minimal Implementation Scope

We only build:

1. Session manager (create/load/save sessions)
2. Markdown loader → system prompt composer
3. LangGraph execution loop
4. Generic action executor bridge
5. Persistence layer (filesystem or DB)
6. Messaging interface (API / websocket / CLI client)

Nothing else.

---

## 11. Design Philosophy

We are not building many agents.

We are building:

> A persistent reasoning runtime where agents are **instantiated minds** loaded from disk and allowed to converse, act, and collaborate as independent processes.
