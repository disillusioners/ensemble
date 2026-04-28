# Explorer/Experiencer Architecture Investigation — 2026-04-28

## Overview
Comprehensive architecture investigation for refactoring planning. Three parallel sessions explored Explorer/Experiencer, Message/Job Queue, and RAG/KB systems.

## Explorer Subsystem

### Agent Files
- `agents/explorer/` — meta.json (model: "quick", tools: rag,filesystem,help,time), soul.md, rule.md, workflow.md, tools_note.md, knowledge.md
- Identity: knowledge retrieval specialist, speed-focused, read-only
- Recursion prevention: cannot call explore()/experience()
- Confidence-driven: HIGH→return immediately, MEDIUM/LOW→browse files

### explore() Tool
- Location: `daemon/tools/knowledge_tools.py:49-91`
- Parameters: query (str), mode (str="hybrid"), project_id (str|None)
- Uses `invoke_agent_and_wait()` — SYNCHRONOUS, blocks up to 300s
- Auto-detects project_id from instance context

## Experiencer Subsystem

### Agent Files
- `agents/experiencer/` — meta.json (model: "quick", tools: rag,help,time), soul.md, rule.md, workflow.md, tools_note.md, knowledge.md
- Identity: knowledge architect/curator
- No filesystem, no bash, no spawning — headless insert-only
- Deduplication required via rag_search_labels

### experience() Tool
- Location: `daemon/tools/knowledge_tools.py:93-143`
- Parameters: text (str), project_id (str|None)
- FIRE-AND-FORGET: spawns instance async, returns immediately with instance ID
- Key difference from explore(): non-blocking

## Inner Soul Redirect
- Location: `daemon/tools/inner_soul.py:183-254`
- `_should_redirect_to_rag()` checks: all targets are memory/memories + knowledge classification
- Redirects knowledge requests to experience() instead of file-based memory
- Self-modification (soul, user, workflow) preserved unchanged

## Message Flow
- Agent (LLM) → ToolNode (graph.py:538) → Tool execution → ToolMessage → LLM receives
- Key files: `daemon/graph.py`, `daemon/services/instance_messaging.py` (1009 lines), `daemon/services/child_reports.py` (669 lines), `daemon/services/completion_registry.py` (249 lines)
- invoked_as_tool=True → CompletionRegistry (synchronous) instead of parent notification

## Job Queue System
- 6 states: PENDING→PROCESSING→COMPLETED/FAILED→DEAD_LETTER
- 10 valid transitions in `services/job_state_machine.py`
- Single JobProcessor with event-driven wakeup (NOT thread pool)
- Key files: `services/job_queue_service.py` (1301 lines), `services/job_processor.py` (309 lines)
- JobFeedbackObserver: instance completion → job state transition

## RAG/KB System
- External LightRAG via HTTP REST API (NOT built-in)
- 15 RAG tools in `daemon/tools/rag_tools.py`
- Workspace isolation via LIGHTRAG-WORKSPACE header (project_id)
- No SQLite vector tables — all storage delegated to LightRAG
- Two-level tools: raw RAG (rag_*) + higher-level knowledge (explore/experience)

## Instance Hierarchy
- Instance model: parent_id, children (denormalized JSON), waiting_for counter
- InstanceHierarchy junction table for canonical parent→child
- spawn_instance in `services/instance_lifecycle.py:96-277`
- waiting_for incremented in send_message, decremented in child_reports
- Cascade completion: waiting_for==0 → parent completes → up the tree
