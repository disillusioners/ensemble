# CODE-CENSUS — ON-EVENT-LOOP WORK IN agents-ensemble

**Target tree:** `/home/nea/ensemble-src-wt-offloop-census` — detached `@ 01eb788b6` (**v0.31.10** == live daemon bytes).
**Scope:** ANALYSIS-ONLY. No source edits, no commits, no DB/HTTP probes. The live daemon was never contacted.
**Method:** AST census (Python `ast` walk) over `daemon/` + targeted `grep`/`sed` reads. Every line number below is a
**v0.31.10** line number read from the census tree, so they match live.
**Census date:** 2026-11-19.

## Confidence legend

| Tag | Meaning |
|---|---|
| **VR** | *verified-read* — I opened the file at the cited line and read the statement in context. |
| **GS** | *grep-suspect* — located by AST/grep pattern, statement shape inferred from the AST node; surrounding control flow not read line-by-line. |

## House trap — CONFIRMED

* **265 / 265 route handlers are `async def`** (`daemon/routers/*.py`, AST census of every `@router.*` / `@app.*`
  decorated function). **Zero** sync route handlers ⇒ FastAPI's threadpool is never used for route bodies.
* **274 `@tool` decorator uses** in `daemon/tools/` (reviewer/tester AST convention; **325** raw `@tool` lines across
  `daemon/` including prose — the two counting conventions are cited side-by-side). *This lane's own independent AST
  re-derivation of the decorator-use convention returns **271** (a 3-count convention difference from the reviewer's
  274; the substance — "the agent-tool surface is async" — is identical under either).* ⇒ the agent-tool surface is
  async too.
* Repositories are **~100 % synchronous SQLModel/Session** code ⇒ any direct repo/Session call inside an `async def`
  blocks the loop for the DB duration. **VR** (`daemon/repositories/instance/repository.py:650` `def get()` opens
  `SQLModelSession(self.engine)` at `:652` and executes inline.)

---

## 1. BRIDGE INVENTORY (full, as required by the commission)

### 1a. `db_offload.offload` — DEDICATED pool, 6 workers — **21 call sites / 12 files**

Executor: `daemon/services/db_offload.py:342` `ThreadPoolExecutor(max_workers=6, thread_name_prefix="db-offload")`,
gated by the custom `_Gate` (permit counter + no-barging waiter deque, `db_offload.py:117-243`). Configured at
`daemon/services/db_offload.py:configure()`; constant `DB_OFFLOAD_MAX_WORKERS_DEFAULT = 6` at
`daemon/constants.py:1817`. Re-entrancy is refused by thread identity (`db_offload.py:_reentrancy_check`).
**VR.**

| File | Sites |
|---|---|
| `daemon/tools/metrics_read.py` | 4 (`:1327` import; call sites in `_build_off_loop` wrapper) |
| `daemon/services/decision/kill.py` | 3 (`:762`, `:210`, `:216` — write/read/tier) |
| `daemon/services/decision/cost_store.py` | 3 (`:18` import; `_read_sync`, `get_overrides`) |
| `daemon/routers/instances.py` | 3 (`:350`, `:489`, `:565`) |
| `daemon/tools/nest_report_read.py` | 2 (`:336`) |
| `daemon/services/decision/overlay.py` | 1 (`:1072`) |
| `daemon/routers/work.py` | 1 (`:181` — `_fetch_and_serialize`) |
| `daemon/routers/queues.py` | 1 (`:668` — `resolver.resolve`) |
| `daemon/routers/nest_reports.py` | 1 (`:228`) |
| `daemon/routers/metrics.py` | 1 (`:472` — `_build_off_loop`) |
| `daemon/routers/messages.py` | 1 (`:210`) |

**Note (VR):** `daemon/services/anomaly_service.py:152` imports `offload as _db_offload` — the service routes *every*
count through the dedicated pool (module docstring `:40`, `:98`).

### 1b. `asyncio.to_thread` — SHARED DEFAULT executor — **1202 call sites**

`asyncio.to_thread` submits to the loop's **default** `ThreadPoolExecutor`
(`min(32, os.cpu_count()+4)` workers — Python default). The daemon **never** calls `asyncio.set_default_executor`
(verified by grep: only *other*, purpose-built executors exist — see 1c). ⇒ **every `to_thread` site competes for the
same small pool.** This is the **R-3 ceiling risk**: the pool is shared by DB reads, LLM `invoke` calls (which hold
the worker for the whole LLM round-trip), git subprocesses, file I/O and CPU work.

Highest site-density files (`grep -c "asyncio.to_thread("`, **VR**):

| File | sites | | File | sites |
|---|---|---|---|---|
| `daemon/services/job_queue_service.py` | 84 | | `daemon/services/dependency_bus.py` | 26 |
| `daemon/services/job_recovery_service.py` | 44 | | `daemon/routers/projects.py` | 26 |
| `daemon/services/instance_messaging.py` | 43 | | `daemon/services/scheduling_service.py` | 21 |
| `daemon/services/service_tool_manager.py` | 42 | | `daemon/routers/settings.py` | 21 |
| `daemon/services/skill_evolution_service.py` | 38 | | `daemon/services/task_processor.py` | 19 |
| `daemon/services/prompt_version_service.py` | 33 | | `daemon/services/child_reports.py` | 19 |
| `daemon/manager.py` | 31 | | `daemon/routers/mcp_servers.py` | 17 |
| `daemon/repositories/room/service.py` | 28 | | `daemon/tools/job_queue.py` | 15 |
| `daemon/services/job_queue_mgmt_service.py` | 27 | | `daemon/services/job_processor.py` | 15 |
| `daemon/services/mission_strand_healer.py` | 26 | | `daemon/services/context_messages.py` | 15 |

**LLM-hold hazard (VR):** `daemon/graph.py:12574` `asyncio.to_thread(llm.invoke, summary_messages)`,
`daemon/graph.py:2078` `asyncio.to_thread(...)` for the primary LLM call, `daemon/graph.py:10246/10275/9781/9839/3282`
`loop.run_in_executor(...)` for LLM invokes, `daemon/services/attestation_report_judge.py:411`,
`daemon/services/keyword_extraction.py:406`, `daemon/services/watcher_context_builder.py:268`,
`daemon/services/skill_evolution_service.py:2693`. **Each LLM round-trip holds a shared-default worker for its
entire duration** (30–300 s). With a **20**-worker default pool (host-computed: `min(32, cpu_count()+4)` = 16+4 = 20
on this 16-vCPU host), ~20 concurrent LLM calls saturate it ⇒ every DB
`to_thread` bridge in the daemon stalls behind them. This is the concrete R-3 ceiling mechanism.

### 1c. `run_in_executor` — **25 distinct real call sites** (executor arg decides)

*Count reconciliation:* 28 raw `run_in_executor(` grep hits at v0.31.10 − 3 prose/docstring mentions = **25 distinct
call sites**. The earlier count of 26 double-counted `daemon/mcp/warmup_pool.py:587`, which was listed under BOTH
buckets; the code shows `run_in_executor(SECRET_RESOLVE_EXECUTOR, …)` ⇒ **DEDICATED only** (verified at v0.31.10:
`daemon/mcp/warmup_pool.py:587-589` passes `SECRET_RESOLVE_EXECUTOR`, defined at `:116`).

| Executor | Sites |
|---|---|
| **SHARED DEFAULT** (`None`) | `daemon/manager.py:5046` (`_warm_loader_caches_sync`), `daemon/graph.py:3282`, `:9781`, `:9839`, `:10246`, `:10275` (all LLM `invoke`), `daemon/mcp/connection_manager.py:215`, `:496`, `daemon/sources/registry.py:601`, `daemon/tools/bash.py:1612`, `daemon/services/memoir_embedding_worker.py:470/480/554/639/683/730`, `daemon/services/memoir_poignancy_worker.py:282/427/503`, `daemon/services/memoir_reflection_worker.py:204/220`, `daemon/services/upgrade_journal_sweep.py:3141` — **VR** |
| **DEDICATED** | `daemon/mcp/stdio_wrapper.py:821` (`_PROC_SCAN_EXECUTOR`, defined `:130`), `daemon/mcp/warmup_pool.py:587` (`SECRET_RESOLVE_EXECUTOR`, defined `:116`), `daemon/services/question_pause_set.py:298` (`_MERGE_EXECUTOR`, `:137`) — **VR** |

### 1d. Other purpose-built (dedicated) executors — **VR**

`daemon/services/db_offload.py:342` (6) · `daemon/mcp/stdio_wrapper.py:130` (`_PROC_SCAN_EXECUTOR`) ·
`daemon/mcp/warmup_pool.py:116` (`SECRET_RESOLVE_EXECUTOR`) · `daemon/sources/registry.py:50` (4) ·
`daemon/tools/bash.py:155` (`_DURABLE_INSERT_EXECUTOR`) · `daemon/services/plane_sync_service.py:254` (2) ·
`daemon/services/memoir_poignancy_worker.py:138` (2) · `daemon/services/memoir_reflection_worker.py:70` (1) ·
`daemon/services/question_pause_set.py:137` (1) · `daemon/services/memoir_embedding_worker.py:216` ·
`daemon/services/upgrade_journal_sweep.py:384` (`_waitpid_executor`).

---

## 2. FAMILY 1 — UNBRIDGED SYNC-DB-ON-ASYNC (the core family)

**AST census result:** **215** direct repo/session calls sit inside an `async def` body with **no bridge wrapper**.
Bucket split: `services/` 79 · `tools/` 60 · `core (manager/api/graph)` 35 · `routers/` 18 · `mcp/` 14 ·
`sources/` 9. **13** of the 215 are MCP-client-session lifecycle calls (`session.start/stop/initialize/list_tools`),
not DB ⇒ **202 are DB-shaped**. **VR** (scanner + spot reads).

### F1.1 — ROUTERS (18 sites) — *every request that hits them blocks the loop for the DB duration*

| Site | Call | Route | Fix family |
|---|---|---|---|
| `daemon/routers/instances.py:511` | `manager._instance_ui_prefs_repo.get_all(instance_ids)` | `GET /api/instances` (list) | **bridge (db_offload)** — the surrounding `manager.list_instances` at `:489` is already on the dedicated pool, so this is a **half-bridged** route |
| `daemon/routers/instances.py:576` | `manager._instance_ui_prefs_repo.get()` | `GET /api/instances/{id}` | bridge (db_offload) |
| `daemon/routers/instances.py:2116` | `..._repo.upsert()` | `PUT /api/instances/{id}/ui-prefs` | bridge (db_offload) — **write** |
| `daemon/routers/instances.py:2157` | `..._repo.delete()` | `DELETE /api/instances/{id}/ui-prefs` | bridge (db_offload) — **write** |
| `daemon/routers/skills.py:661` | `repo.list(project_id=…, enabled_only=…)` | `GET /api/skills/triggers` | bridge (db_offload) |
| `daemon/routers/skills.py:692` | `repo.create(**body.model_dump(...))` | `POST /api/skills/triggers` | bridge — **write** |
| `daemon/routers/skills.py:724` | `repo.update(...)` | `PUT /api/skills/triggers/{id}` | bridge — **write** |
| `daemon/routers/skills.py:761` | `repo.delete(...)` | `DELETE /api/skills/triggers/{id}` | bridge — **write** |
| `daemon/routers/wedge_capture.py:180-181` | `repository.list_recent()` + `repository.row_to_dict(row)` per row | `GET /api/wedge-captures` | bridge (db_offload) + **batch** the per-row `row_to_dict` |
| `daemon/routers/wedge_capture.py:364`, `:415` | `repository.get()`, `repository.row_to_dict()` | `GET /api/wedge-captures/{id}` | bridge |
| `daemon/routers/schedules.py:140` | `manager._source_repository.get_latest_execution()` | `GET /api/schedules` | bridge |
| `daemon/routers/schedules.py:441` | same | `PUT /api/schedules/{id}` | bridge |
| `daemon/routers/answer_helper.py:278` | `manager._instance_repository.get_metadata_value()` | `POST /api/answer/...` | bridge |
| `daemon/routers/projects.py:942` | `manager._instance_repository.list()` | `DELETE /api/projects/{id}` | bridge — **bulk list on the loop** |
| `daemon/routers/mcp_servers.py:670`, `:689` | `session.list_tools()` / `session.stop()` | `POST /api/mcp-servers/{id}/test` | **GS** — MCP client session, not DB; still blocking-on-loop (network) |

**Highest-value target: `daemon/routers/instances.py:511`.** The route is *half* bridged — the heavy
`manager.list_instances` fetch runs on the dedicated db-offload pool (`:489-499`), then the **prefs batch SELECT runs
back on the loop** (`:511`), then N pydantic `InstanceInfo` models are constructed (`:514-541`) and FastAPI encodes the
body on the loop (`:545-552`). This is the `/api/instances` on-loop leg already cross-referenced by the
`instances-list-latency` ANALYSIS (steps 8-10). **Not re-derived here — cross-ref only.** The census adds: the
**prefs batch itself is a sync DB call on the loop**, so the fix family is *bridge (db_offload) for the prefs SELECT*,
not merely "batch".

### F1.2 — AGENT TOOLS (60 sites) — *every tool call blocks the loop for the DB duration*

`daemon/tools/job_queue.py` alone carries **32** unbridged sync-DB calls inside agent-tool coroutines:

| Tool (async fn) | Lines | Calls |
|---|---|---|
| `job_create` @1401 | 1537, 1542, 1574, 1576 | `watcher_repo.count_watches_for_instance`, `.add_watch` ×2, `.remove_watch` |
| `job_continue` @1986 | 2123 | `manager._instance_repository.get()` |
| `watch_job` @2578 | 2636, 2652, 2736 | `watcher_repo.count_watches_for_instance`, `.add_watch` ×2 |
| `unwatch_job` @2760 | 2791, 2793, 2799, 2811 | `task_repo.get_by_instance`, `watcher_repo.remove_watch` ×3 |
| `list_watched_jobs` @2822 | 2838 | `watcher_repo.get_watches_for_instance()` |
| `watch_jobs` @2891 | 2927, 2977, 2980, 3008 | `watcher_repo.count_watches_for_instance`, `.add_watch` ×3 |
| `job_messages` @3055 | 3083, 3087, 3104 | `_instance_repository.get`, `.get_tree_ids`, `.get` |
| `job_tree` @3176 | 3202, 3215, 3224 | `_instance_repository.get`, `.get_tree_ids`, `.get` |
| `job_progress` @3290 | 3315, 3374, 3378 | `_instance_repository.get`, `.get_tree_ids`, `.get` |
| `job_inject` @3405 | 3431 | `_instance_repository.get()` |
| `watch_mission` @4262 | 4339, 4344, 4361, 4405, 4413 | `watcher_repo.*` ×4 + `task_repo.get_by_instance` |

**VR** (source read at `daemon/tools/job_queue.py:1537-1576`, `:2838`, `:3083-3104`).

Other tool families (**VR**):

| Site | Call | Tool | Fix family |
|---|---|---|---|
| `daemon/tools/infra.py:242, 350, 393, 467, 559, 639, 705, 754, 806` | `repository.create_asset/get_asset/list_assets/search_assets/update_asset/delete_asset/register_type/list_types/get_history` | 9 infra asset/type/history tools | bridge (db_offload) |
| `daemon/tools/knowledge_tools.py:851, 856` | `manager._instance_repository.get_tree_root_id()`, `manager._project_repository.get()` | `explore()` @679 | bridge |
| `daemon/tools/knowledge_tools.py:901, 908` | same pair | `experience()` @874 | bridge |
| `daemon/tools/instance.py:1273`, `:1381` | `session.get(Instance, …)`, `session.commit()` | `_register_child_completion_watcher` @1220 | bridge — **write tx on the loop** |
| `daemon/tools/instance.py:4606` | `instance_repo.get()` | `convene_council_with_skill` @4528 | bridge |
| `daemon/tools/db_tools.py` (3), `daemon/tools/memoir.py` (5 — bridged via `:1358` `to_thread(_sync)`) | — | — | already-off / bridge |
| `daemon/tools/self_alarm.py` (2), `daemon/tools/skill_evolution_tools.py` (2) | repo calls | agent tools | bridge |

**`tools/knowledge_tools.py:851/901` are the notable pair** — the `explore()` and `experience()` tools are the
knowledge-base write path used by every agent that records findings; a repo round-trip per call on the loop.

### F1.3 — SERVICES (79 sites) — *background sweeps + lifecycle runs on the same loop as user traffic*

| Service / async fn | Lines | Notes | Fix family |
|---|---|---|---|
| `daemon/services/waiting_children_watchdog.py:1324, 1370, 1397, 1765, 1894, 1949` | `self._repo.list_waiting_children_parents()`, `.get()`, `.list_hung_children_for_parent()`, `.list_terminal_instance_ids()`, `.parents_with_non_terminal_children()`, `.get()` | `run_once()` @1274 — **periodic sweep** | bridge (db_offload); the last two are **unbounded list** queries |
| `daemon/services/child_reports.py:1306, 1320, 1339, 1349` | `session.exec(...)` ×3 + `session.get(Instance, …)` | `_should_send_completion_report()` @1267 — **runs on EVERY child completion** | bridge (db_offload) — **hot path** |
| `daemon/services/child_reports.py:1392, 1463, 1559` | `session.get`, `session.execute`, `session.exec` | `_update_parent_on_child_complete()` @1373 — **every child completion** | bridge — **hot path** |
| `daemon/services/child_reports.py:1699, 1713` | `session.add()` ×2 | `_create_completion_events()` @1667 | bridge — **write tx** |
| `daemon/services/child_reports.py:2225` | `session.exec()` | `_assistant_message_fresh()` @2201 | bridge |
| `daemon/services/child_reports.py:3124`, `:3215` | `session.exec()`, `session.close()` | `_root_completion_gate()` @2929 | bridge |
| `daemon/services/instance_lifecycle.py:3685, 3696, 3701, 3772` | `repo.get_tree_root_id()`, `repo.get_cascade_tree_ids()` ×2, `repo.get(node_id)` **in a loop over tree_ids** | `pause_instance_cascade()` @3573 — **house pause-first path** | bridge + **batch** the per-node `repo.get` |
| `daemon/services/instance_lifecycle.py:4346, 4365, 4371, 4394` | `repo.get_tree_root_id()`, `.get_cascade_tree_ids()`, `.get_ancestor_ids()`, `.get()` | `resume_instance_cascade()` @4312 | bridge |
| `daemon/services/instance_lifecycle.py:3422` | `instance_repository.get_cascade_tree_ids()` | `hard_delete_instance()` @3315 | bridge |
| `daemon/services/instance_lifecycle.py:440, 4611, 4679` | `_instance_repository.get()` ×3 | `_cancel_bus_watchers_for` @315, `get_instance` @4580, `_recover_watchover_pending_termination` @4617 | bridge |
| `daemon/services/instance_lifecycle.py:4783, 4788, 4855, 4860, 4882` | `.update()`, `.delete_metadata()`, `.update()`, `.set_metadata()`, `.delete_metadata()` | `_restore_instance()` @4702 | bridge — **write tx, 5 statements** |
| `daemon/services/maintenance.py:985, 1017, 1100, 1123` | `_runs_repo.insert()`, `.get_running()`, `.mark_terminal()` ×2 | `execute()` @881 — **maintenance run on the loop** | bridge |
| `daemon/services/maintenance.py:2465, 2473` | `_instance_repo.get()`, `.delete()` | `_cleanup_instance()` @2420 | bridge — **write** |
| `daemon/services/maintenance.py:2219`, `daemon/services/checkpoint_prune.py:612`, `:786` | `engine.dispose()` | maintenance / prune | **VR** — `engine.dispose()` closes the DB pool synchronously ⇒ **pool-wide stall** |
| `daemon/services/job_queue_service.py:556, 711, 844` | `_watcher_repo.get_all_active_watches()` | `reconcile_terminal_watches` @516, `_reconcile_terminal_watches_legacy` @698, `reconcile_held_watches_for_instance` @776 | bridge — **unbounded watch table read** |
| `daemon/services/decision/kill.py:526, 642, 759, 777, 837` | `self._store.get()` / `.set()` | kill-switch service — note `:210/:216/:762` already route through `offload`, so these five are the **unbridged remainder** | bridge (db_offload) |
| `daemon/services/mcps_gateway/service.py:788, 1485, 1940, 2319, 2417` | `_store.mark_offline()`, `.upsert()`, `.clear_session()`, `.get()`, `.bind_session()` | MCP gateway session store | bridge |
| `daemon/services/prompt_refresh.py:1342` | `instance_repo.get()` | `_refresh_impl()` @1337 | bridge |
| `daemon/services/prompt_refresh.py:1491` | `repo.get_effective_rows(agent_id, version_tag)` | same | bridge |
| `daemon/services/plane_sync_service.py:864, 896` | `_repo.get()`, `_repo.list_metadata_records()` | `sync_project()` @770 | bridge |
| `daemon/services/compact_executor.py:1125, 1130` | `task_repo.has_instance_busy()` ×2 | `_in_gate()` @1115 | bridge |
| `daemon/services/db_pool_manager.py:235, 250` | `_repository.get_by_name()`, `.get_credentials()` | `_get_or_create_pool()` @191 | bridge — **credential read on the loop** |
| `daemon/services/auth_session/sweeper.py` (3), `daemon/services/skill_store_service.py` (8 — bridged at `:213/:321/:350/:400/:449`), `daemon/services/custom_agent_lifecycle_service.py` (3), `daemon/services/context_messages.py` (2), `daemon/services/critical_notes_selection_orchestrator.py` (1), `daemon/services/skill_trigger_engine.py` (1), `daemon/services/snapshot_metrics_service.py` (1), `daemon/services/snapshot_settings_utils.py` (1), `daemon/services/editor_utils.py` (1), `daemon/services/mcp_service.py` (1), `daemon/services/job_recovery_service.py` (1), `daemon/services/memoir_embedding_worker.py` (1) | — | — | bridge (to_thread where already present) |

### F1.4 — MANAGER / API / GRAPH (35 sites) — *the manager holds the write transaction*

| Site | Call | Async fn | Fix family |
|---|---|---|---|
| `daemon/manager.py:12207, 12223, 12230` | `session.get(Instance, instance_id)`, `session.add(inst_row)`, `session.commit()` | `_revive_terminal_instance()` @12129 | bridge (db_offload) — **write tx**: `with self._session_scope() as session:` at `:12206` (WriteGuardSession, `manager.py:3305-3318`) |
| `daemon/manager.py:13455, 13479, 13503, 13507, 13571, 13601, 13623, 13627, 13642, 13665, 13670, 13686, 13706, 13733, 13749, 13754, 13771, 13841, 13853` | **19** `session.get/add/exec/execute/commit` statements across **two** `with self._session_scope() as session:` blocks (`:13453`, `:13566`) | `_reconcile_deferred_report_async()` @13380 | bridge (db_offload) — **largest single on-loop write transaction in the daemon** |
| `daemon/manager.py:8283` | `_instance_repository.list(status=status)` | `_cleanup_cached_instances()` @8247 | bridge |
| `daemon/manager.py:11674` | `_instance_repository.get(instance_id)` | `ensure_mcp_preloaded()` @11621 | bridge |
| `daemon/manager.py:16462` | `_instance_repository.update_instance()` | `_resume_processing_background()` @15989 | bridge — **write** |
| `daemon/manager.py:6084` | `with engine.begin():` | `_rehydrate_question_packs_at_boot()` @6053 | bridge — **boot path** |
| `daemon/manager.py:4846` | `_project_repository.get_metadata(...)` | `initialize()` @4509 | bridge — **boot path** |
| `daemon/manager.py:12169` | `await asyncio.to_thread(...)` | `_revive_terminal_instance` (pre-check) | **already-off** |
| `daemon/api.py` (4), `daemon/graph.py` (4), `daemon/sources/mapper.py` (3), `daemon/mcp/tool_adapter.py` (4), `daemon/mcp/connection_manager.py` (7 — MCP session lifecycle) | — | — | bridge |

**VR** — `daemon/manager.py:12206-12231` and `:13453-13853` were read in full; the session blocks contain
**no `await`** (see §7 "refuted"), so the whole transaction runs with the loop pinned.

---

## 3. FAMILY 2 — CPU-BOUND PURE-PYTHON ON THE LOOP (GIL ⇒ threads add no CPU throughput)

### F2.1 — tiktoken token counting inside `async def compact_state` — **VR, CONFIRMED**

`daemon/compaction.py:3113` `async def compact_state(self, …)` (body `:3113-3963`) contains **13 direct
`estimate_messages_tokens(...)` calls**, each of which runs `tiktoken.get_encoding("cl100k_base").encode(text)`
(`daemon/loader.py:1087-1099` `estimate_tokens`, `daemon/loader.py:1102+` `estimate_messages_tokens`):

`daemon/compaction.py:3190`, `:3194`, `:3255`, `:3300`, `:3315`, `:3320`, `:3358`, `:3403`, `:3440`, `:3517`, `:3539`,
`:3580`, `:3892` — plus `daemon/compaction.py:4018` and `:4078` in `async def _summarize_chunked()` (`:3965-4279`).

**Caller path (VR):** `daemon/graph.py:8410` `result = await compactor.compact_state(ctx, force=False)`,
`daemon/graph.py:10006`, `:10014`, `:10035` — i.e. **on the agent turn, on the main loop**.

**Amplifiers (VR):**
* `daemon/compaction.py:736-785` `_truncate_to_token_cap` — an **iterative-shrink loop of up to 20 tiktoken
  re-encodes** per call (`:765`, `:774`).
* `daemon/compaction.py:788-846` `_apply_ceiling_rule` — re-estimates the **whole section set** inside a per-section
  loop (`:808`, `:811`, `:834`, `:837`).
* `daemon/compaction.py:874-1068` `build_compaction_doc` (`:1015`) and `daemon/compaction.py:2046-2263`
  `_build_last_effort_replacement` (`:2173`, `:2177`).

**Fix family: `process-pool / C-accel` is the only real option** (tiktoken's Rust core releases the GIL only
partially; `asyncio.to_thread` does **not** add CPU throughput). The winning shapes are (a) **algorithmic** —
replace exact tiktoken counting with the cheap character heuristic already used *inside* the shrink loop
(`daemon/compaction.py:779-780`) so the 20-iteration loop stops re-encoding, and (b) **batch** — count once per
compaction instead of 13+ times. A **process pool** is the fallback if exact counts are contractually required.
This is the documented ~20 s lag class (observed 2026-10-30).

### F2.2 — `json.dumps` / `model_dump` on the loop — **GS**

AST census: **299** blocking-I/O/CPU calls sit directly inside `async def`. The `model_dump` majority
(`routers/instances.py` 22, `routers/schedules.py` 20, `routers/mcp_servers.py` 19, `routers/sources.py` 12,
`routers/messages.py` 11, `routers/projects.py` 10, `routers/tmp_images.py` 9, `routers/dlq.py` 8, …) are
**request-body** dumps — small, low severity. The `json.dumps` set is the one to look at:
`daemon/tools/instance_ops.py` **13** sites, `daemon/tools/system.py` **9**, `daemon/routers/jobs_streaming.py` **7**,
`daemon/tools/instance_read.py` **5**, `daemon/tools/room.py` **5**, `daemon/tools/ens_env_tools.py` **4**,
`daemon/tools/infra.py` **4**, `daemon/tools/nest_report_read.py` **4**. **GS** — payload size not measured; the
agent-tool `json.dumps` results become the tool message body, so a large tree/instance dump is a real CPU cost on
the loop during an agent turn.

### F2.3 — ormsgpack / checkpoint decode — **cross-ref only, NOT re-litigated**

Prior verdict stands: **offload was DECLINED** (the decode holds the GIL inside the lib lock). Algorithmic win only.
See §6 for the *loop* attribution.

---

## 4. FAMILY 3 — BLOCKING I/O ON THE LOOP

### F4.1 — `open()` / `shutil.rmtree()` in route handlers — **VR**

| Site | Call | Route | Fix family |
|---|---|---|---|
| `daemon/routers/agents.py:131` | `with open(new_agent_dir / "meta.json", "w", encoding="utf-8") as f:` | `POST /api/agents` (`create_agent` @65) | bridge (db_offload / to_thread) |
| `daemon/routers/agents.py:150` | `shutil.rmtree(new_agent_dir)` | same (rollback path) | bridge — **recursive delete of a directory tree on the loop** (unbounded latency) |

### F4.2 — file reads in async paths — **VR / GS**

| Site | Call | Context | Fix family |
|---|---|---|---|
| `daemon/services/git_diff_service.py:87` | `working_file.read_text(encoding="utf-8")` — **up to 1 MiB** (`st_size <= 1_048_576` guard at `:86`) | `get_file_diff()` @38 (async) — the *git* commands at `:30/:52/:56` **are** bridged via `to_thread`; the file read is not | bridge (to_thread) — **mixed bridge** |
| `daemon/services/prompt_refresh.py:1419` | `meta_path.read_text(encoding="utf-8")` + `json.loads(...)` | `_refresh_impl()` @1337 | bridge |
| `daemon/services/project_skill_source/service.py:1066` | `os.read(...)` | `_read_body_fresh()` @1006 (the sibling path at `:1082` **is** bridged) | bridge — **mixed bridge** |
| `daemon/services/project_skill_source/service.py:1097` | `hashlib.sha256(...)` | same | bridge (CPU+GIL) |
| `daemon/tools/bash.py:1412`, `:1413`, `:1422` | `open(stdout_path, "w+b")`, `open(stderr_path, "w+b")`, `open(stdin_path, "rb")` | `bash()` @1293 — temp-file handles for the child | low severity (local tmpfs), bridge optional |

### F4.3 — `subprocess` / `time.sleep` — **CLEAN**

AST census found **zero** `subprocess.run/Popen/call/check_output`, `time.sleep`, `requests.*`, `urllib.request.urlopen`,
`os.read/write` or `shutil.*` calls directly inside an `async def` body. Every hit found by raw grep
(`daemon/tools/service_spawner.py:196/342/443`, `daemon/tools/proc_tools.py:818/858`, `daemon/tools/snapshot_tools.py:382/391/400`,
`daemon/tools/upgrade_journal.py:3099/3472/3678/3873/3970/4198/4217`, `daemon/services/resource_monitor/proc_readers.py:484`,
`daemon/tools/comment_edit.py:183`, `daemon/tools/doc_write.py:180`, `daemon/tools/inner_soul.py:495`,
`daemon/tools/upgrade_journal.py:1475`) lives in a **sync helper** that is reached through a bridge
(e.g. `daemon/tools/proc_tools.py:2362-2367` `asyncio.to_thread`; `daemon/services/doc_commit_service.py:262/349/421/442/458`).
**VR** for the absence (AST census) and **GS** for each individual helper's bridge.

---

## 5. FAMILY 4 — R-2 BFS DEPTH-LOOP DWELL

### F5.1 — `InstanceRepository.get_tree_ids_permanent` — **per-NODE BFS, one SELECT per frontier node** — **VR**

`daemon/repositories/instance/repository.py:844-908`:

```
:869   root = db_session.get(Instance, root_id)
:876   for _ in range(_MAX_TRAVERSAL_DEPTH):        # cap = 256 (repository.py:56)
:880       for current_id in frontier:
:881-885      rows = db_session.exec(select(Instance.instance_id).where(Instance.parent_id == current_id)).all()
```

**Statement-count shape:** **1 SELECT per frontier node per level**, up to **256 levels** ⇒ worst case
`O(nodes)` round-trips, each with its own SQL planning + network wait, **all inside one open session**
(`with SQLModelSession(self.engine) as db_session:` at `:867` — the connection is pinned idle-in-transaction for the
whole traversal). **This is the R-2 dwell class.**

**On/off loop:** the repository method is **sync**; it is on-loop whenever its caller is on-loop. Callers:

| Caller | Line | On-loop? |
|---|---|---|
| `daemon/tools/instance.py:1974`, `:1976`, `:1996` — `_validate_subtree_target()` @1932 (a **SYNC** helper) | called from the async agent tools `subtree_messages` @5775 and `subtree_status` @6286 | **ON-LOOP** — **VR** |
| `daemon/tools/instance_read.py:362` (sync helper) | `repo.get_tree_ids_permanent(instance_id)` | **ON-LOOP** — **VR** |
| `daemon/tools/instance_read.py:828` | `asyncio.to_thread(repo.get_tree_ids_permanent, instance_id)` | already-off |
| `daemon/routers/rooms.py:411` | `asyncio.to_thread(manager.get_tree_ids_permanent, anchor)` | already-off |
| `daemon/manager.py:16683` | `def get_tree_ids_permanent(self, …)` → `self._instance_repository.get_tree_ids_permanent(...)` — **sync facade** | on-loop when called from an async ctx without a bridge |
| `daemon/manager.py:14302`, `:14620` | `repo.get_tree_ids_permanent(instance_id)` | **GS** — on-loop iff the enclosing fn is async |
| `daemon/services/answer_resolver.py:440` | `instance_repo.get_tree_ids_permanent(mission_root_id)` | **GS** — the docstring at `:421` says it "is called DIRECTLY (not through …)" |
| `daemon/services/attestation_resolver_activation.py:1512` | `manager.get_tree_ids_permanent(instance_id)` | **GS** |
| `daemon/services/mission_strand_healer.py:618`, `:1080`; `daemon/services/mission_live_guard.py:830`; `daemon/services/job_recovery_service.py:3419` | all inside `asyncio.to_thread(...)` | already-off |

**Fix family: `batch` (recursive-CTE / single `IN`-per-level query) + `bridge`.**
`daemon/repositories/instance/repository.py:1616-1662` already demonstrates the **batch** shape for a different
traversal (`list_with_descendants`): **one `WHERE parent_id IN (current_level_ids)` SELECT per LEVEL**, not per node
— the `IN`-clause is at `:1627-1629`
(`child_stmt = select(Instance).where(col(Instance.parent_id).in_(current_level_ids))`).
Porting that shape to `get_tree_ids_permanent` turns O(nodes) round-trips into O(levels). **VR**

### F5.2 — `get_cascade_tree_ids` → `get_tree_ids_permanent` — the R-2 chain that reaches the loop

`daemon/repositories/instance/repository.py:942-973` `get_cascade_tree_ids()` (mode switch
`ENSEMBLE_CASCADE_LINEAGE`, default `permanent`) delegates to `get_tree_ids_permanent` at `:973`.
⇒ `daemon/services/instance_lifecycle.py:3696` and `:3701` (`pause_instance_cascade`), `:4365`
(`resume_instance_cascade`), `:3422` (`hard_delete_instance`) each pull the **per-node BFS onto the loop**,
and `pause_instance_cascade` then adds an **N-per-node `repo.get(node_id)` loop** at `:3772`. **VR**

**Fix family: `batch` (CTE) + `bridge` (db_offload) + `batch` (the `repo.get` loop → one `IN` fetch).**

---

## 6. FAMILY 5 — CHECKPOINT READ AMPLIFICATION (cross-ref only — the checkpoint-amplification lane owns it)

**Blocking sites and WHICH loop they block:**

| Site | What | Loop |
|---|---|---|
| `daemon/checkpoint_adapter.py:611-631` `_call_with_retry` → `await method(*args, **kwargs)` (`:614`, `:631`) | the wrapped `AsyncPostgresSaver.aget` / `aget_tuple` call | **MAIN loop** for the DB round-trip (the await is async — the *wait* is not blocking) |
| `daemon/checkpoint_adapter.py:348-399` `_observe_read` → `:390` `_shared_payload_size(result)` → `bounded_deep_size(result)` (`daemon/checkpoint_adapter.py:194-222`, walk at `:218`) | **one O(payload) deep-size walk per read** | **MAIN loop** — the docstring at `:362` says verbatim: *"two O(payload) walks per read, **ON THE EVENT LOOP**"*; the M-2 fix collapsed two walks to one but did **not** move it off the loop |
| `daemon/checkpoint_adapter.py:797` (`aget`) and `:834` (`aget_tuple`) | the two call sites of `_observe_read` | **MAIN loop** |
| `daemon/checkpoint_adapter.py:611` — `await self._call_with_retry("aget", …)` | the read-cache miss path | **MAIN loop** |

**Which loop blocks for the *decode*:** the blob decode in the upstream lib is offloaded by the **lib itself** —
`langgraph/checkpoint/postgres/aio.py:286` `await asyncio.to_thread(self._dump_blobs, …)` and `:328`
`await asyncio.to_thread(self._dump_writes, …)` ⇒ the ormsgpack **encode** side runs on the **SHARED DEFAULT
executor**, not the main loop. **VR** (read of the installed package).

⇒ The 5.4-8.1 s sentinel `aget` timeouts therefore have **two distinct contributors**:
1. the **main loop** pays the O(payload) `_observe_read` walk per read (CPU, GIL-held, main loop); and
2. the **shared default executor** carries the blob decode — so a saturated default pool (see §1b: LLM calls hold
   workers for 30-300 s) delays the decode ⇒ the sentinel `aget_tuple` waits on the *executor*, not the DB.

**Cross-ref:** the readiness sentinel probe is `daemon/services/readiness.py:1249-1253` / `:1536`
(`"checkpoint_saver: sentinel checkpoint read (aget_tuple)"`) and `:1854-1879` (the sentinel cycle contract).
The checkpoint-amplification lane owns the fix; this census only attributes the loops.

---

## 7. SEEDS REFUTED AT v0.31.10 (anchors drift; each was re-grepped, not trusted)

| Brief seed | v0.31.10 truth | Verdict |
|---|---|---|
| `services/eligible_pending_sweep.py ~:284-290` `list_paused_or_terminal_instance_ids` unbridged | Actual call is at **`daemon/services/eligible_pending_sweep.py:299`**, and it **IS** bridged: `await asyncio.to_thread(self._instance_repository.list_paused_or_terminal_instance_ids, …)`. The sweep's other DB call is bridged at `:244`. | **REFUTED — already-off** (VR) |
| `routers/work.py` resolver path unbridged (vs `jobs` path bridged) | **`daemon/routers/work.py:181-192`** — `await _offload(_fetch_and_serialize)`; `resolver.list_work` + `r.to_dict()` both run on the **dedicated db-offload pool**. The jobs path (`daemon/services/job_queue_service.py:1841`) is likewise bridged via `asyncio.to_thread`. | **REFUTED — already-off** (VR) |
| `routers/queues.py ~:654` defer-block resolver unbridged | **`daemon/routers/queues.py:668`** — `snapshot = await _offload(resolver.resolve)`. The comment at `:650-667` documents the **wi-3f5bea95** relocation onto the db-offload executor. | **REFUTED — already-off** (VR) |
| `manager.py` sessions held **ACROSS awaits** `~:11515-11571` (dwell class) | **AST census: ZERO** `with <session>` blocks inside an `async def` contain an `await`. The three in-async session blocks — `daemon/manager.py:12206`, `:13453`, `:13566` — contain **no await** (the enclosing async fns `_revive_terminal_instance` @12129 and `_reconcile_deferred_report_async` @13380 were read in full). The line range `11515-11571` in v0.31.10 is the `shutdown_worker_pool` / `spawn_instance` facade region — no session scope there at all. | **REFUTED as a *mechanism*** — but the **site** is CONFIRMED: the same blocks are sync DB work on the loop (see F1.4). The dwell is *within* the transaction, not *across* an await. (VR) |
| `tools/instance.py ~:5861 _fetch_instance_messages` → `repositories/instance/repository.py ~:647` unbridged | `daemon/tools/instance.py:5861` `async def _fetch_instance_messages` calls **`await manager.get_messages(iid)`** (`daemon/manager.py:16645` → `self._messaging_service.get_messages(...)`, `daemon/services/instance_messaging.py:5970`) — an **async** chain, not a direct sync repo call. The cited repository line `daemon/repositories/instance/repository.py:653` is `db_session.get(Instance, instance_id)` inside `def get()` (@650) — reached through the async messaging service. | **REFUTED as a direct sync-repo-on-async site** (VR) — the *tree* walk around it (`_validate_subtree_target`, §5) is the real on-loop cost |
| `skill_search_service.py` `_embedding_rerank` unbridged | `daemon/services/skill_search_service.py:1358` `async def _embedding_rerank` — its DB/embedding calls are bridged: `:1428`/`:1433` `await asyncio.to_thread(...)`, `:1573`/`:1590` `await asyncio.to_thread(...)`. | **REFUTED — already-off** (VR) |
| `repositories/instance/repository.py ~:1627-1662` per-level BFS | The **batch** shape lives at `:1616-1662` (`list_with_descendants`, one `IN` SELECT per level). The **per-node** BFS the brief means is `get_tree_ids_permanent` at **`:844-908`** (anchor drifted by ~780 lines). | **CORRECTED** (VR) |
| `job_queue_service.py ~:1831` | Actual `list_work` bridge is at **`daemon/services/job_queue_service.py:1841`**. | minor anchor drift |

---

## 8. CROSS-REFERENCES (not re-derived)

* **`.agents/shared/planning/sonic-team/instances-list-latency/ANALYSIS.md`** — `/api/instances` on-loop steps 8-10:
  prefs batch `daemon/routers/instances.py:511` + N-model build `:514-541` + 6 MB encode `:545-552` ≈ 230 ms per 10 s poll.
  The census **confirms** step 8 is a *sync DB call on the loop* (F1.1), i.e. the fix family is *bridge + batch*, not
  only *batch*.
* **`Option E` (`/api/jobs` badge leg)** — `daemon/services/job_queue_service.py:1841` is bridged (`asyncio.to_thread`).
  The census found the **un-bridged sibling** in the same family: `daemon/services/job_queue_service.py:556`, `:711`,
  `:844` — `_watcher_repo.get_all_active_watches()` (unbounded watch-table read) → on-loop. **New finding.**
* **`to_thread` fix precedents for mapping** — `daemon/services/job_queue_service.py:1841`,
  `daemon/services/instance_messaging.py:6045`/`:6080`: all read. The `instance_lifecycle.py` precedent anchors have
  drifted: the file is **6 613 lines** at v0.31.10, so the earlier `~:10286-10298` citation was **dead** (no such
  range exists) and is dropped. The equivalent `asyncio.to_thread` bridges sit at
  `daemon/services/instance_lifecycle.py:2943`, `:3461`, `:3521`, `:3883`, `:4419`, `:4522`, `:4752`, `:4810`
  (all eight verified real at v0.31.10).
* **`db_offload.offload` semantics** — `daemon/services/db_offload.py` module docstring: the seam runs sync callables on
  a dedicated 6-worker pool; it is **NOT** a CPU-offload seam and it **does not** bound response encoding.
* **Encode-on-loop LESSON (house-documented)** — `daemon/routers/metrics.py:464-471` `_build_off_loop` docstring,
  verbatim: *"The claim is BUILD-OFF-LOOP ONLY — the offload returns a Python object and **FastAPI still encodes it on
  the loop**; the residual is bounded by the `max_points`/selector ceilings."* ⇒ **any route that offloads the *body
  build* but returns a large `response_model` still pays the encode on the loop.** The census did **not** re-measure
  the `/api/instances` 6 MB encode (cross-ref only).

---

## 9. SUMMARY — FIX FAMILY ROLL-UP

| Fix family | Sites | Representative |
|---|---|---|
| **bridge (db_offload / to_thread)** | **~202 DB-shaped on-loop sites** + 21 already-dedicated | `daemon/manager.py:13453-13853` (19 statements, one tx), `daemon/tools/job_queue.py` (32), `daemon/routers/instances.py:511`, `daemon/services/child_reports.py:1306-1349` |
| **batch** | 3 high-value | `daemon/repositories/instance/repository.py:844-908` (per-node BFS → per-level `IN`), `daemon/services/instance_lifecycle.py:3772` (per-node `repo.get` loop), `daemon/routers/wedge_capture.py:181` (per-row `row_to_dict`) |
| **process-pool / C-accel** | 1 family (13+ sites) | `daemon/compaction.py:3190…:3892` (tiktoken) — threads do **not** help (GIL) |
| **algorithmic** | 3 | compaction shrink-loop (`daemon/compaction.py:774-780`), checkpoint deep-size walk (`daemon/checkpoint_adapter.py:390`), `get_tree_ids_permanent` BFS |
| **already-off** | 4 refuted seeds + 21 db_offload + 1202 to_thread | `daemon/routers/work.py:181`, `daemon/routers/queues.py:668`, `daemon/services/eligible_pending_sweep.py:299`, `daemon/services/job_queue_service.py:1841` |

**Top three findings for the developer:**

1. **`daemon/manager.py:_reconcile_deferred_report_async` @13380 — 19 sync `session.*` statements across two
   `self._session_scope()` blocks (`:13453`, `:13566`), all on the main loop.** This is the single largest on-loop
   write transaction in the daemon. *(F1.4, VR)*
2. **`daemon/tools/job_queue.py` — 32 unbridged sync-DB calls across 11 agent-tool coroutines**; every `job_*` /
   `watch_*` tool invocation blocks the loop for the DB duration, on the agent-turn path. *(F1.2, VR)*
3. **`daemon/compaction.py:compact_state` @3113 — 13 tiktoken encodes on the main loop** during every agent turn,
   amplified by the 20-iteration re-encode loop in `_truncate_to_token_cap` (`:774`). Threads do **not** fix this
   (GIL). *(F2.1, VR)*

**Secondary:** `daemon/repositories/instance/repository.py:844-908` per-node BFS is reachable on the loop through
`daemon/tools/instance.py:1932-1996` `_validate_subtree_target` (the `subtree_messages` / `subtree_status` authz
chokepoint) — this is the R-2 dwell, and the batch shape already exists 780 lines away at `:1616-1662`.
