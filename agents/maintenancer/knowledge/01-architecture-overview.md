# 01 — Architecture Overview

last-verified-against: v0.12.4
verify-against-source: pyproject.toml version (release tag, NOT rolling SHA — per D12/D17 + architect §6.3)

A condensed map of the ensemble daemon — load this when an investigation
needs to know which file owns a behavior.

## Facade: InstanceManager

`daemon/manager.py` is the facade coordinating lifecycle, repositories,
message and job queues, source adapters, MCP/OpenCode resources, tool
construction, and LangGraph assembly. New methods that take a kwarg must
thread it through the facade (`enqueue_message` etc.) and ship a
facade-forwarding test. The facade does not own state — it forwards.

## Execution graph + middleware slots

`daemon/graph.py` builds the LangGraph state graph with middleware slots
for context injection, report framing, tool throttling, loop detection
and repair, response validation, retry classification, language
checks, and context compaction. The loop breaker (default threshold
three) detects repeated tool-call patterns and replaces the looping
history with a repair summary. Mid-superstep `aupdate_state` is
SUPERSEDED by the in-flight task's own commit; durable mid-turn writes
use the return-carried pattern (`[REMOVE_ALL_MESSAGES sentinel,
*post-channel, ...new]`).

## Repositories

About 16 repositories are bound to a single shared SQLAlchemy engine.
Engine selection is in `daemon/manager.py` (~:446-460). On SQLite the
PG-only code paths no-op; on PG `create_all + _ensure_postgres_columns`
are the schema-evolution path (the migration runner is SQLite-only by
design — see `daemon/migrations/runner.py:486-491`).

## Checkpoint adapter

`daemon/checkpoint_adapter.py` normalizes LangGraph checkpoints. OpenCode
keeps its own SQLite layer for tool state. The CLE handler at
`daemon/graph.py:3606-3608` is a pre-existing hazard: it still uses
mid-flight `aupdate_state` which is superseded by the in-flight task —
DML/messages written there vanish on normal return.

## Prompt loader

`daemon/loader.py:373-385` documents the 11-section composition order:
soul → rule → skill.md (legacy) → per-skill sections → dynamic_tools →
tools.md → workflow → memory → recent memories → shared_knowledge →
project-experience. Each section comes from the agent's own files;
section ordering is fixed by the loader, not by file order on disk.

## Agent registry (import-time singleton)

`daemon/registry.py:1170-1175`: `get_registry()` builds a module-global
`_registry` once and runs `discover()`. Per-spawn resolution reads the
in-memory snapshot via `get_version(...) or get_resolved(...)`
(`daemon/tools/instance.py:4539-4541`), never the file. Therefore adding
a new agent directory requires a daemon restart before spawns resolve
its meta.

## Jobs and admission

Jobs expose a four-value `AdmissionState` (QUEUED / ACTIVE / DONE /
DEAD). Legacy `JobStatus` is a backward-compat shim
(`daemon/repositories/job_queue/models.py:107-126`) — production
admission writes use `AdmissionState`. Transitions live in
`daemon/services/job_state_machine.py`; lock-first concurrency is in
`daemon/services/job_queue_service.py` (`start_job_atomic_with_lock`,
single transaction for lock INSERT + status UPDATE).

## Cross-refs

- §02 jobs/missions/admission
- §04 known traps (create_all vs migrations runner)
- §05 repair runbooks (pause-first quiesce, migration recipe)
