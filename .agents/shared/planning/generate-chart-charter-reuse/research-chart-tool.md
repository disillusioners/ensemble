# Research: generate_chart tool plumbing (facts only)

Repo: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` (read-only research)
Branch check: `git rev-parse` NOT executable from this agent (no bash). Read `.git/HEAD` directly: `ref: refs/heads/feature/generate-chart-charter-reuse` (main checkout of the repo). Per task note, branch content identical to `latest` either way — NOT independently diff-verified.
Research date: 2026-09-10

---

## 1. `daemon/tools/chart_tools.py` (158 lines, read fully)

### Tool signature (chart_tools.py:57-63)
```python
@register_tool_category("chart")
@tool
async def generate_chart(
    description: str,
    diagram_type: str = "flowchart",
    project_id: str | None = None,
) -> str:
```
- Factory: `create_chart_tools(manager, current_instance_id) -> list` (chart_tools.py:33); returns `[generate_chart]` (:158).
- `project_id` auto-injection: `_get_project_id()` (:45-55) reads `manager._instance_repository.get(current_instance_id).project_id` directly (comment at :48-49: `get_instance()` returns a CompiledStateGraph, not metadata).
- Extended doc attached as `generate_chart._full_doc_` (:121-156): documents blocking wait, default `timeout` = 600s, charter validates via `npx -y @mermaid-js/mermaid-cli`, and instructs the caller to paste the returned mermaid block verbatim without re-wrapping (:135-137).

### Structured prompt built for charter (chart_tools.py:88-96)
```
Create a {diagram_type} diagram.

Description: {description}
Project: {pid}          # line only if pid is truthy
```
Label-style header lines, explicitly mirroring the `explore()` style of `knowledge_tools.py` (:88-90). No history, no session reference, no prior-chart context — every call is a self-contained single message.

### Exact invoke call (chart_tools.py:101-110)
```python
result, child_instance_id = await invoke_agent_and_wait(
    manager=manager,
    agent_id="charter",
    message=chart_message,
    project_id=pid,
    parent_id=current_instance_id,
    instance_name=f"chart-{description[:30]}",
    timeout=600.0,
    return_instance_id=True,
)
```
- `child_instance_id` is unpacked but NEVER used afterwards (:101-119) — dropped on both success and error paths.

### Helper used: `invoke_agent_and_wait` (daemon/utils.py:588-740, read fully)

Signature (utils.py:588-599):
```python
async def invoke_agent_and_wait(
    manager, agent_id: str, message: str,
    project_id: str | None = None,
    instance_name: str | None = None,
    parent_id: str | None = None,
    images: list[str] | None = None,
    timeout: float = 300.0,
    return_instance_id: bool = False,
    model: str | None = None,
) -> str | tuple[str, str]:
```

Full flow:
1. **Deadlock-prevention semaphore**: lazy singleton `asyncio.Semaphore` capped at `WORKER_POOL_SIZE - 1` (min 1) so ≥1 worker thread stays free for the spawned agent (utils.py:554-569, :643-647).
2. **Pre-generates `instance_id = str(uuid.uuid4())`** (:650) — every invocation gets a brand-new UUID.
3. **Spawn** (synchronous, creates instance in DB): `manager.spawn_instance_with_mcp(agent_id, instance_id, parent_id, project_id, instance_name, invoked_as_tool=True)`; `model` kwarg forwarded only when not None (:656-670).
4. **Register** in CompletionRegistry IMMEDIATELY after spawn, before enqueue (buffered-completion handles the race) (:672-674).
5. **Enqueue message**: `manager.enqueue_message(instance_id, message, source=f"internal_invoke_and_wait:{parent_id or 'system'}", images=images)` (:676-682). Source prefix documented at daemon/constants.py:375.
6. **Re-register** if a checkpoint resume consumed the registration while unregistered (:684-686).
7. **Wait**: `result = await registry.wait_for(instance_id, timeout=timeout)` (:689).
   - `None` → timeout → best-effort fire-and-forget `_try_terminate_orphan` (`asyncio.ensure_future(manager.terminate_instance(...))`, :572-585) → returns `"Error: Agent timed out after {timeout}s. Instance {id[:8]}... may still be running."` (:691-697).
   - `result.is_error` → returns `"Error: Agent failed. {result.content}"` (:699-701).
   - Success → `result.content or ""` (:704).
8. **Exception path** (:706-735): ValueError matching "Spawn refused" + "governor" (governor recursion guard) → readable `Error: {e}`; all other exceptions → `_try_terminate_orphan` + `Error: {e}`.
9. **finally**: `registry.unregister(instance_id)` + semaphore release (:736-740).

Return semantics with `return_instance_id=True` (:652-654): ALWAYS a `(content, instance_id)` tuple including error/exception paths; on exception, instance_id is the pre-generated UUID (spawn never completed) (:653).

### Error surfacing at chart_tools.py:112-119
- `result is None` (hard timeout during cleanup) → `"Error: Charter agent timed out or failed. Try a simpler description."` (:117-118).
- Any `"Error: ..."` string from the helper passes through **verbatim** (no re-shaping) (:119).
- On success the tool returns the charter text unchanged.

---

## 2. Spawn chain: chart_tools → utils → manager → lifecycle

Chain (verified end-to-end):

1. **chart_tools.py:101** → `invoke_agent_and_wait(...)`
2. **daemon/utils.py:670** → `instance_id = await manager.spawn_instance_with_mcp(**spawn_kwargs)` where spawn_kwargs = `{agent_id, instance_id, parent_id, project_id, instance_name, invoked_as_tool=True}` (+ `model` only when provided) (:660-670)
3. **daemon/manager.py:6574** → `spawn_instance_with_mcp(self, *, instance_id, version_tag=None, agent_id=None, **kwargs) -> str` (keyword-only):
   - `await self.ensure_mcp_preloaded(instance_id, agent_id=..., version_tag=...)` (:6617-6621)
   - sync `self.spawn_instance(instance_id=..., version_tag=..., agent_id=..., **kwargs)` (:6629-6634); discards the `(instance_id, validated_model_override)` tuple's second element (:6624-6628)
   - on failure: MCP connection cleanup, re-raise (:6636-6643)
4. **daemon/manager.py:6439** → `spawn_instance(...)` — thin facade delegate to `self._lifecycle_service.spawn_instance(...)` (:6489-6499)
5. **daemon/services/instance_lifecycle.py:1317** → `InstanceLifecycleService.spawn_instance(agent_id, instance_id=None, parent_id=None, project_id=None, instance_name=None, invoked_as_tool=False, model=None, version_tag=None, source_type=None) -> tuple[str, str | None]` — the authoritative spawn.

### Kwargs available on this chain (instance_lifecycle.py:1317-1328)
| kwarg | Effect |
|---|---|
| `parent_id` | Hierarchy linkage; permanent on the child instance row (survives terminate-to-revive per core blueprint) |
| `project_id` | Normalized at :1410 (`normalize_project_id`); drives context injection |
| `instance_name` | Short name used in completion reports |
| `invoked_as_tool` | Stored as `instance_metadata["invoked_as_tool"] = True` (:1798-1799); suppresses parent completion-report notification (child_reports.py:2725) |
| `model` | Validated override (`_resolve_model_override`); persisted to DB `model_override` field; resolution priority chain documented at :1373-1392 (1. override → 2. `metadata.llm_models` weighted-random → 3. `metadata.llm_model` → 4. `config.llm.model`) |
| `version_tag` | Agent version; persisted as `Instance.agent_tag`; mismatch raises ValueError when explicit (:1347-1354) |
| `source_type` | Chat platform type; root instances only, stored in instance_metadata (:1355-1360) |

### NOT available on this chain (confirmed by signature read)
- **No `context` kwarg** — the message string is the only context carrier.
- **No `load_skill` kwarg**.
- **No session/token kwarg** for re-targeting an existing instance — spawn always creates a new instance; `invoke_agent_and_wait` never reuses (new UUID every call, utils.py:650).

---

## 3. Registration seam & agent availability

Three-step seam (all three confirmed):
1. **Decorator**: `@register_tool_category("chart")` on the tool factory product (chart_tools.py:57); `CATEGORY_NAME = "Chart"` + docstring (:23-30).
2. **CATEGORY_MODULES entry**: `"chart": "daemon.tools.chart_tools"` (daemon/tools/_tool_registry.py:496, dict starts :479).
3. **Import + list-extend in `create_instance_tools`**: `from .chart_tools import create_chart_tools` (daemon/tools/instance.py:197); `chart_tool_list = create_chart_tools(manager, current_instance_id)` + `tools.extend(chart_tool_list)` (instance.py:4316-4317).

### The instance.py:4312 "always available" comment — actual gating (clarified)
The comment (:4312-4315) says "always available ... matching the OpenCode pattern", NOT inside the `is_rag_enabled()` block. This means the tool is **always constructed** into the per-instance tool list. Actual usability gating is the tool-authorization layer:
- **Default-OPEN universe**: "chart" is NOT in `PRIVILEGED_TOOL_CATEGORIES` (exactly {system_upgrade, system-log, ens-db}), so an agent with EMPTY `tools.allow` gets the chart category by default.
- **Innate-skill auto-grant**: `INNATE_SKILL_TOOL_CATEGORIES = {"opencode": ["external_opencode"], "chart": ["chart"], "todo": ["todo"], "dynamic-skill": [...], "skill-evolution": [...], "question": ["question"]}` (instance.py:133-140) merges "chart" into the effective allow list of any agent declaring `innate_skills: ["chart"]` via `expand_allow_for_innate_skills` (:143+).
- **Explicit allow/deny**: agents with explicit `tools.allow` need "chart" in it; `deny` removes access.

### Which agents hold it (meta.json verified by grep, `innate_skills` contains "chart")
reviewer, developer, planner[v2], tidier[v2], approver, devops, doc-writer, governor, developer[v2], maintenancer, planner, ari, leader, reviewer[v2], approver[v2], architect, tidier, coder, wanderer (19 agents).
- `agents/project-manager/meta.json` has "chart" twice in the tools section (:48 and :68) — allow vs deny placement NOT verified (file not read in full).
- explorer, charter, and other agents not listed above do NOT declare innate "chart" per grep.

### Delegate-target authorization
`TOOL_REQUIRED_AGENTS = {"knowledge": ["explorer","kb-writer"], "chart": ["charter"], "image": ["image-reader"], "council": ["governor"]}` (daemon/tools/_auth.py:35-40): declaring category "chart" in `tools.allow` implicitly adds "charter" to the agent's effective `team_members` allow-set — single-source-of-truth via tools.allow (:25-34, :59-62).

---

## 4. Sibling delegate tools

| Tool | File:line of invoke | Pattern | Reuses instances? |
|---|---|---|---|
| `explore` | knowledge_tools.py:797-807 | `invoke_agent_and_wait(agent_id="explorer", parent_id=current_instance_id, instance_name=f"explore-{query[:30]}", timeout=300.0, return_instance_id=True, model=model_override)` — model_override resolved from explorer's `caller_model_overrides` map keyed by calling agent (~:770-788) | NO — fresh spawn every call |
| `experience` | knowledge_tools.py:917-1032 | **Does NOT use invoke_agent_and_wait.** Fire-and-forget: `_save_experience_result` to shared context dir via `asyncio.to_thread` (:959-965) + `_enqueue_experience_job` for kb-writer agent (:972-984) + blueprint pending-queue hook (:996-1030). Returns `"Knowledge recording started."` (:1032) | N/A (no sync wait at all) |
| `explain_image` | image_tools.py:513-523 | `invoke_agent_and_wait(agent_id="image-reader", parent_id=current_instance_id, instance_name=sanitized "image-{...}", images=[data_uri], timeout=300.0, return_instance_id=True)`; name sanitized `re.sub(r"[^A-Za-z0-9._-]+", "_", image)[:30]` (:504-508) | NO — fresh spawn every call |

- **No delegate instance reuse exists anywhere today.** `invoke_agent_and_wait` pre-generates a fresh UUID per call (utils.py:650) and always spawns.
- **Shared helper**: `invoke_agent_and_wait` IS the generic shared delegate helper (spawn → register → enqueue → wait). Per-tool code is only message construction + kwarg selection. There is no higher-level "delegate with reuse" abstraction.
- Contract tests: `tests/test_chart_tools.py` (delegation kwargs :117-128, None→Error :184-196, project_id flow :204-210); `tests/test_image_tools.py` Section 5 pins `invoke_agent_and_wait` backward-compatible signature (:730-783) and Section 6 pins explain_image kwargs (:798-909).

---

## 5. Existing tracking of spawned delegate instances

**Conclusion: NO durable caller→delegate-instance mapping exists.** Evidence:

- **CompletionRegistry** (`daemon/services/completion_registry.py`, read fully): in-memory module-level singleton (:248-261) holding `_events/_results/_buffered/_register_times` dicts (:46-49). `invoke_agent_and_wait` unregisters its entry in `finally` after every wait (utils.py:736-740); `cleanup_stale` prunes entries older than 3600s and clears `_buffered` when >100 (:204-245). Nothing persists past the tool call returns; nothing survives restart.
- **`invoked_as_tool` metadata flag** (the only persisted tool-spawn trace):
  - Set: `instance_metadata["invoked_as_tool"] = True` (instance_lifecycle.py:1798-1799), via chain from utils.py:666.
  - Consumed: child_reports.py:2725 — tool-invocation completions SKIP parent completion-report notification, but still update status and signal CompletionRegistry (:2723-2729).
  - It is a boolean flag on the child row — it does NOT record which caller/session, and is not queryable as a "delegate for caller X" index by itself.
- **Message source marking**: enqueue source string `"internal_invoke_and_wait:{parent_id or 'system'}"` (utils.py:680); prefix documented at daemon/constants.py:375.
- **Grep `"charter"` across daemon/**: only chart_tools.py:103 (the invoke) and _auth.py:37 (TOOL_REQUIRED_AGENTS). No registry, no manager dict, no session store, no CorrelationManager-style persistent state keyed to charter delegates.
- **What IS queryable (raw data availability, not an existing feature)**: instance rows persist `agent_id="charter"`, permanent `parent_id` (= caller), `instance_name="chart-{description[:30]}"`, and `instance_metadata.invoked_as_tool=true`. A parent_id+agent_id query could find prior delegates; nothing in the code does this today.
- **Revive machinery exists but is NOT on this path**: per core blueprint, `send_message` (daemon/services/instance_messaging.py) revives COMPLETED/TERMINATED/ERROR/FAILED instances reusing checkpoints. `invoke_agent_and_wait` bypasses send_message entirely (spawn_instance_with_mcp + enqueue_message), so revive never triggers for delegate calls.

---

## 6. Backward-compat surface

### Exact current tool signature
```python
generate_chart(description: str, diagram_type: str = "flowchart", project_id: str | None = None) -> str
```

### Return contract
- **Success**: the charter agent's FULL response text, verbatim — a single ` ```mermaid ` fenced code block (validated) plus a brief explanation. The tool performs **NO extraction** of the mermaid block; no parsing, no re-wrapping (chart_tools.py:119 returns `result` unchanged; `_full_doc_` instructs callers to paste verbatim without stripping the fence, :135-137, :152-155).
- **`None` content** (hard timeout during cleanup): `"Error: Charter agent timed out or failed. Try a simpler description."` (:117-118).
- **Helper error strings** pass through verbatim: `"Error: Agent timed out after 600.0s. Instance {id}... may still be running."` / `"Error: Agent failed. {content}"` / `"Error: {exception}"` / governor-guard refusal text (utils.py:694-697, :701, :732, :735).
- **RAG-off gate**: none — chart has no `is_rag_enabled()` gate (unlike experience, knowledge_tools.py:933-934).
- **Contract-pinning tests**: `tests/test_chart_tools.py:117-210` (delegation kwargs, None→Error collapse, project_id flow into message + helper call).

---

## Gaps / could NOT confirm
1. **`git rev-parse --abbrev-ref HEAD`** — no bash available; branch obtained via reading `.git/HEAD` (`feature/generate-chart-charter-reuse`). Branch-content identity with `latest` not diff-verified.
2. **project-manager meta.json "chart" placement** (:48 vs :68 — allow vs deny list) — file not read in full.
3. **Lifecycle spawn body beyond :1411** (max_children enforcement, hierarchy-row writes, checkpoint assembly) — signature + doc verified; internal mechanics only partially read (not needed for the reuse question, but noted for completeness).
4. Whether the `internal_invoke_and_wait:` source prefix has additional consumers beyond constants.py:375 documentation — only the definition site verified.
