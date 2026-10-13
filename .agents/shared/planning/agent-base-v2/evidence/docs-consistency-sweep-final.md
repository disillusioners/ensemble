# Phase E — FINAL docs-consistency sweep (post-D baseline; exec 2026-10-13T16:18:25Z)

## Stream (i) broad — grep -rn 'opencode' docs/ README.md AGENTS.md (case-sensitive)
docs/mcp-kb-usage.md:12:- **User-level:** `~/.config/opencode/opencode.json` — applies to all projects
docs/mcp-kb-usage.md:13:- **Project-level:** `.opencode.json` in your project root — project-specific
docs/mcp-kb-usage.md:34:  "$schema": "https://opencode.ai/config.json",
docs/mcp-kb-usage.md:46:### oh-my-opencode-slim Plugin
docs/mcp-kb-usage.md:48:Even with MCP servers configured in `opencode.json`, they **won't work** unless each agent in the slim plugin config explicitly includes the MCP server name in its `"mcps"` array.
docs/mcp-kb-usage.md:50:**Config file:** `~/.config/opencode/oh-my-opencode-slim.json`
docs/mcp-kb-usage.md:73:- The MCP server name in `"mcps"` must match the key used in `opencode.json`'s `"mcp"` section (e.g., `"ensemble-kb"`)
docs/test-suite-known-issues.md:43:| `tests/opencode/test_integration.py` (2) | Live OpenCode server. |
docs/plans/shared-context-metadata-message-injection.md:107:- **external opencode sessions** (`external_opencode.py` `_preload_shared_context`), and
docs/plans/decouple-execution-plan.md:107:| **A9** | Audit remaining 13 files | Grep for `waiting_for` reads in the 13 files not in A4–A7; gate any control-flow reads; document rebuild-only reads | `api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{error_reporting,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py` |
docs/plans/decouple-execution-plan.md:512:- `api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{error_reporting,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py` — audit + gate remaining `waiting_for` reads
docs/plans/decouple-execution-plan.md:574:- **Next step**: Initialize `plan-track` opencode session to monitor execution progress
docs/plans/cleanup-old-architecture.md:250:| **4.1** | Comprehensive grep-driven sweep + remove unconditional reads/resets | **`grep -rn "waiting_for" daemon/ --include="*.py"` = 324 matches across 19 files.** This is a COMPREHENSIVE sweep — not just the ~7 sites listed below. Files: `api.py`, `config.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/models.py`, `repositories/instance/repository.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/child_reports.py`, `services/correlation_manager.py`, `services/error_reporting.py`, `services/instance_lifecycle.py`, `services/job_feedback_observer.py`, `services/job_processor.py`, `services/job_queue_service.py`, `services/message_processing_pipeline.py`, `tools/instance.py`. Every reference must be audited. Many are comments/docstrings (safe to clean up), but **code references that read the column must be removed**. Specific unconditional read sites: `instance_lifecycle.py:650` (`child_ids = list(meta.children)` — cascade-to-children reads `meta.children` from the `children` column; **must replace with `instance_hierarchy` query**); `instance_lifecycle.py:1173, 1246-1247, 1558, 1565`; `job_feedback_observer.py:780, 830, 1993-2010` (the `SELECT waiting_for ... FOR UPDATE` gate). | 19 files |
docs/plans/decouple-review.md:59:- **55** `waiting_for` control-flow read sites (decrement/increment/`==0`/`>0`) across **18 files** (`api.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/{models,repository}.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/{child_reports,correlation_manager,error_reporting,instance_lifecycle,job_feedback_observer,job_processor,job_queue_service,message_job_handler,message_processing_pipeline}.py`, `tools/instance.py`).
docs/plans/skill-metrics-improvement.md:442:- `opencode` innate skill (for delegated code execution)
docs/bugs/agent-call-wrong-tool-spawn-session.md:4:  "agent_id": "opencode",
docs/bugs/agent-call-wrong-tool-spawn-session.md:8:This API call of agent is wrong (no agent_id name opencode, opencode is a skill), i need you improve this tool. Our tool response error and guide to help llm. For example: no agent_id called ‘opencode’ please use list_agent tool. Don’t make mistake between tool call via bash cli and spawn_instance
docs/bugs/child-completion-report-lost-under-concurrent-task-processing.review.md:367:- A live 10-min opencode_skill exploration with several bash tool calls: `started_at` is 10 min old, but the worker is alive and processing.
docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md:107:| 20 | ai | — | "Good context. The current opencode skill uses…" |
docs/bugs/job-feedback-observer-cross-instance-handoff.md:21:12:08:06 - external_opencode session v1-re-review finishes its task on root project dadc814a
docs/bugs/job-feedback-observer-cross-instance-handoff.md:22:12:08:11 - graph tool call: external_opencode_wait_for_result
docs/bugs/skill-feedback-no-usage-record-in-production.md:66:The only side-channel engine is `self._opencode_engine` (`manager.py:765`), which exists solely for the `opencode_sessions` table and never touches the skill/instants schema.
docs/optimization/workflow-context-load-optimization.md:9:Spawned workers/coders start with near-zero parent context, and the system re-runs an expensive 3-stage skill search on every single LLM turn — despite having the infrastructure to fix both already built (opencode preload, shared-context directory, heuristic_match flag).
docs/optimization/workflow-context-load-optimization.md:42:| **R3** | Internal `send_message` passes ZERO context (unlike opencode's `_preload_shared_context`) | Every child starts cold — re-reads files, re-explores architecture the parent already mapped | `instance.py:1555` — only raw text enqueued |
docs/optimization/workflow-context-load-optimization.md:128:The opencode dispatch path already solves the cold-start problem via `_preload_shared_context()` (`external_opencode.py:419-539`). It:
docs/optimization/workflow-context-load-optimization.md:138:- Add optional `related_context_keywords` parameter (matching opencode's signature)
docs/optimization/workflow-context-load-optimization.md:196:| **2A** | Port opencode preload to send_message | ~150 lines | ⭐⭐⭐⭐⭐ | 1-3 turns/child |
docs/optimization/workflow-context-load-optimization.md:206:**Phase 2 (next sprint):** 2A — the biggest bang-for-buck. Port the proven opencode preload pattern into internal dispatch.
docs/optimization/workflow-context-load-optimization.md:226:| opencode preload mechanism | `daemon/tools/external_opencode.py:419-539` |
docs/optimization/workflow-context-load-optimization.md:227:| opencode preload invocation | `daemon/tools/external_opencode.py:622-633` |
docs/optimization/workflow-context-load-optimization.md:314:| **2A** | **Port opencode preload to `send_message`** | ✅ CONFIRMED — the biggest gap; proven pattern exists | **HIGHEST** |
(rc=0)

## Stream (ii) — opencode session/API/coding-worker phrases
docs/plans/shared-context-metadata-message-injection.md:107:- **external opencode sessions** (`external_opencode.py` `_preload_shared_context`), and
docs/plans/decouple-execution-plan.md:574:- **Next step**: Initialize `plan-track` opencode session to monitor execution progress
docs/bugs/job-feedback-observer-cross-instance-handoff.md:21:12:08:06 - external_opencode session v1-re-review finishes its task on root project dadc814a
(rc=0)

## Stream (iii) — coding.worker
(rc=1)

## Case-INSENSITIVE supplementary (Phase D verified capitalized 'OpenCode' rows all fixed)
docs/mcp-kb-usage.md:5:## 1. OpenCode Integration
docs/mcp-kb-usage.md:9:OpenCode uses **JSON** config (not YAML). Add the MCP server to your config file.
docs/mcp-kb-usage.md:31:**Full example with typical OpenCode settings:**
docs/test-suite-known-issues.md:37:Depend on live LLM quota, a running OpenCode server, or `psycopg` state. Their
docs/plans/coder-selective-context-truncation.md:290:  "description": "Direct coding agent — reads, writes, and edits code directly without OpenCode. Works hands-on with files, tests, and build systems.",
docs/optimization/workflow-context-load-optimization.md:125:#### 2A. Port OpenCode Preload Pattern to Internal `send_message`
(capitalized-only residual rows above; empty=none)

## Case-insensitive total:
37

## Classification (vs Phase D baseline table — evidence/docs-consistency-sweep.md)

- Stream (i) = **31 hits**, stream (ii) = **3 hits** (subset), stream (iii) = **0** — byte-matches Phase D's post-edit counts (31/3/0). Zero drift since Phase D's commit.
- Every case-sensitive hit maps to an `evidence/r10-allowlist.md` row/class: D6 (`docs/mcp-kb-usage.md` ×7), HIST (`docs/test-suite-known-issues.md:43`, `docs/plans/*` ×7, `docs/bugs/*` ×7, `docs/optimization/*` ×9). **Zero unclassified. Zero mandatory-FIX leftovers.**
- Case-INSENSITIVE supplementary total = **37** (6 additional capitalized-only rows, ALL in allowlist-eligible classes):
  - `docs/mcp-kb-usage.md:5,:9,:31` — D6 (upstream sst/opencode product doc; allowlisted file).
  - `docs/test-suite-known-issues.md:37` — HIST (same historical record file as allowlisted :43).
  - `docs/plans/coder-selective-context-truncation.md:290` — HIST (docs/plans past-planning class; gate-excluded dir).
  - `docs/optimization/workflow-context-load-optimization.md:125` — HIST (file carries 9 allowlisted rows).
- **Residual `opencode` hits post-Phase-D: 31 case-sensitive (all classified per Phase D table) / 37 case-insensitive (all classified). Zero unclassified. Mission DoD #8: PASS.**
