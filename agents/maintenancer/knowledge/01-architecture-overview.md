# 01 — Architecture Overview

last-verified-against: v0.12.4

## Facade + registry
`daemon/manager.py` coordinates lifecycle, repositories, message/job queues, source adapters, MCP/OpenCode, tool construction, LangGraph assembly. New kwargs thread through `enqueue_message` + ship a facade-forwarding test.
`daemon/registry.py:1170-1175`: `get_registry()` builds module-global `_registry` and runs `discover()`. Per-spawn reads the in-memory snapshot via `get_version(...) or get_resolved(...)` (`daemon/tools/instance.py:4542-4544`), never the file. New agent directory requires daemon restart.

## Execution graph
`daemon/graph.py` builds the LangGraph state graph with middleware slots (context injection, report framing, tool throttling, loop detection+repair, response validation, retry classification, language checks, context compaction). Loop breaker (threshold 3) replaces looping history with a repair summary. Mid-superstep `aupdate_state` is SUPERSEDED by the in-flight task's own commit; durable mid-turn writes use the return-carried pattern (`[REMOVE_ALL_MESSAGES sentinel, *post-channel, ...new]`).

## Repositories + engine
~16 repos share one SQLAlchemy engine. Engine selection at `daemon/manager.py:~446-460`. SQLite: PG paths no-op. PG: schema via `create_all + _ensure_postgres_columns` (runner SQLite-only — §04 (ii)).

## Checkpoint + CLE
`daemon/checkpoint_adapter.py` normalizes LangGraph checkpoints; OpenCode keeps its own SQLite. Reactive compaction / CLE handler in graph.py uses mid-flight `aupdate_state` superseded by the in-flight task — DML vanishes on normal return (T2-ext canary). See §04 (vi).

## Prompt loader
`daemon/loader.py:~373-385`: 11-section composition order — soul, rule, skill.md (legacy), per-skill sections, dynamic_tools, tools.md, workflow, memory, recent memories, shared_knowledge, project-experience.

## Jobs + admission (§02 full)
Jobs expose a four-value `AdmissionState` (QUEUED / ACTIVE / DONE / DEAD). Legacy `JobStatus` is a shim (`daemon/repositories/job_queue/models.py:107-126`) — production uses `AdmissionState`. Transitions in `daemon/services/job_state_machine.py`.
