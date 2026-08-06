# Phase 3: Activation/Deactivation Lifecycle & API

## Objective

Implement the user-facing activation and deactivation lifecycle for watchover:
the `POST /instances/{id}/watchover` API endpoint, the pause → compaction →
set-flags → resume sequence, watchover_context construction via `ContextCompactor`,
the `SuspensionReason.WATCHOVER_SETUP` Python enum value (pure-Python, NO migration — `suspension_reason` is a TEXT/VARCHAR column, not a PostgreSQL enum type), and
`instance_metadata` flag management. After this phase, watchover can be turned
on/off programmatically via the API.

## Files to Create

| # | Path | Purpose |
|---|------|---------|
| ~~C3.1~~ | ~~`daemon/migrations/NNN_watchover_suspension_reason.sql`~~ | **DELETED — no SQL migration needed.** `SuspensionReason` is a Python `str, Enum` over a TEXT/VARCHAR column (`task/models.py:55-60`; migration `20260801_000001` line 35-45; PG ensure at `manager.py:3756-3765`). There is NO PostgreSQL native enum type to `ALTER`. Adding `WATCHOVER_SETUP` is a pure-Python enum member with zero migration cost. Alternatively, reuse `PAUSED_EXTERNAL` for phase 1 (architecture-recommendation.md S6). |
| C3.2 | `daemon/services/watchover_service.py` | New service encapsulating the activation/deactivation business logic: `activate_watchover(instance_id, requirement)`, `deactivate_watchover(instance_id)`, `_build_watchover_context(instance_id)` (calls ContextCompactor). |
| C3.3 | `test/test_watchover_lifecycle.py` | Unit tests for activation (pause→compaction→flags→resume), deactivation (flags cleared), context construction, flag persistence. |

## Files to Modify

| # | Path | What Changes |
|---|------|--------------|
| M3.1 | `daemon/routers/instances.py` (near `:527`, `:558`) | Add `POST /instances/{instance_id}/watchover` endpoint. Request body: `WatchoverRequest {enabled: bool, requirement: str}`. Calls `watchover_service.activate_watchover()` or `deactivate_watchover()`. **Reuse callout:** follows the existing pause/resume endpoint pattern (`instances.py:527`, `:558`). |
| M3.2 | `daemon/repositories/task/models.py:52-60` (`SuspensionReason`) | Add `WATCHOVER_SETUP = "watchover_setup"` enum value. |
| M3.3 | `daemon/manager.py` | Add methods used by the watchover service: `set_watchover_enabled(instance_id, enabled, requirement, context)` — writes to `instance_metadata` JSONB; `get_watchover_state(instance_id)` — reads flags + context. |
| M3.4 | `daemon/manager.py` (`build_instance_graph` invocation path) | Ensure the watchover flags in `instance_metadata` are available to the graph (already threaded via manager). No change needed if `is_watchover_enabled()` (Phase 1 T1.5) reads `instance_metadata`. |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T3.1 | Add `SuspensionReason.WATCHOVER_SETUP = "watchover_setup"` to `daemon/repositories/task/models.py:52-60`. | none | Enum value exists; existing code using SuspensionReason still works. |
| ~~T3.2~~ | ~~Create migration for `WATCHOVER_SETUP`~~ — **DELETED. No migration exists.** `suspension_reason` is TEXT/VARCHAR, not a PostgreSQL enum. Adding the Python enum member (T3.1) is zero-cost. See technical-analysis.md §C3 for the correction proof. | — | N/A (task eliminated). |
| T3.3 | Add `set_watchover_enabled(instance_id, enabled, requirement=None, context=None)` and `get_watchover_state(instance_id)` to `daemon/manager.py`. These read/write the `instance_metadata` JSONB (`instance/models.py:63-66`) keys: `watchover_enabled` (bool), `watchover_requirement` (str), `watchover_context` (str). | Phase 1 (T1.5 `is_watchover_enabled` reads the flag) | Unit test: set flags → read flags → correct values. Flags persist across manager restart (DB-backed). |
| T3.3b | Add `set_metadata_many` atomic multi-key helper (W-5, TD-7). The existing `set_metadata` writes one JSON key at a time (`instance/repository.py:782-845`) — four independent calls for watchover config can expose torn state on crash. Add an atomic multi-key patch that writes `{watchover_enabled, watchover_requirement, watchover_context, watchover_transition}` in a single dialect-aware UPDATE (reuse the `jsonb_set`/`json_set` pattern but for multiple keys). Use this in T3.5 activation and T3.6 deactivation. | T3.3 | Unit test: partial crash mid-`set_metadata_many` → either all keys or none written (no torn state). |
| T3.4 | Implement `_build_watchover_context(instance_id)` in `watchover_service.py`: fetch instance state via `compiled_graph.aget_state(config)` → build `CompactionContext` (`compaction.py:219-231`: messages, system_prompt_tokens, model_name, config, llm_config) → call `ContextCompactor.compact_state()` (`compaction.py:380-781`) → extract summary → combine summary + user requirement → return context string. **Add raw-tail fallback (TD-6):** if `compact_state()` returns `None` (fresh/short history), use the last N raw messages (default 10) as watchover_context instead. Ensures the watcher always has context, even on fresh instances (AC-EC.7). **Reuse callout:** `ContextCompactor.compact_state()` is graph-independent (`compaction.py:380-781`); `CompactionContext` dataclass at `compaction.py:219-231`. | T3.3 | `_build_watchover_context` returns a non-empty string combining the compaction summary + requirement. Unit test with a mock graph state + mock compactor; fallback: compact_state returns None → raw-tail used; context is non-empty. |
| T3.5 | Implement `activate_watchover(instance_id, requirement)` in `watchover_service.py`: (1) `manager.pause_instance_cascade(instance_id)` (`manager.py:5348`); (2) `context = self._build_watchover_context(instance_id)` (T3.4); (3) `manager.set_watchover_enabled(instance_id, enabled=True, requirement=requirement, context=context)` (T3.3); (4) `manager.resume_processing_job(instance_id)` (`manager.py:5382`). Set `suspension_reason=WATCHOVER_SETUP` during the pause window. **Reuse callout:** `pause_instance_cascade` (`manager.py:5348`), `resume_processing_job` (`manager.py:5382`). **Add try/except + rollback (W-8):** if compaction (step 2) or resume (step 4) fails after pause, roll back (clear any partially-set flags via `set_metadata_many` with `watchover_transition: "rollback"`) and re-raise so the operator can manually resume. The instance must not be left stuck paused with partial watchover state. | T3.3, T3.4 | End-to-end test: activate → instance pauses, compaction runs, flags set, instance resumes with watchover active. The graph now routes through `watchover_check` (Phase 1). |
| T3.5b | **Implement `wait_for_instance_quiescent` (TD-2).** Add `manager.wait_for_instance_quiescent(instance_id, timeout)` — waits for in-flight tool execution (worker threads) to complete before building watchover_context. `pause_instance_cascade` cancels the graph task but does NOT await tool-thread quiescence. This barrier delivers FR-28/NFR-15 (graph-boundary safe; note: in-flight limitation still documented per LD-4). Call it in T3.5 between pause (step 1) and compaction (step 2). | T3.5 | Unit test: tool executing in worker thread → activate watchover → wait_for_instance_quiescent blocks until thread completes → context built after quiescence. |
| T3.6 | Implement `deactivate_watchover(instance_id)` with FULL pause→disable→resume sequence per FR-14: (1) `manager.pause_instance_cascade(instance_id)` (`manager.py:5348`) — pause for safe state transition; (2) clear `watchover_enabled` flag + denial counter in graph state (keeps requirement/context for audit); (3) `manager.resume_processing_job(instance_id)` (`manager.py:5382`). The pause guard prevents a race between flag-clearing and an in-flight tool call. **Symmetric with activation (T3.5); follows FR-14 mandate.** | T3.3 | Unit test: deactivate → instance pauses, flag cleared, instance resumes; subsequent tool calls route directly to `tools` (passthrough). |
| T3.7 | Add `POST /instances/{instance_id}/watchover` endpoint to `daemon/routers/instances.py`. Request model: `WatchoverRequest(BaseModel) {enabled: bool, requirement: str \| None = None}`. If `enabled=True`, call `watchover_service.activate_watchover(instance_id, requirement)`. If `enabled=False`, call `deactivate_watchover(instance_id)`. Return success/error JSON. **Reuse callout:** follows pause/resume endpoint pattern (`instances.py:527`, `:558`). **Phase 1 descope (FR-27/TD-9): manager-internal only — no cross-session authorization.** The project has no existing instance-session-ownership primitive; full 403 cross-session rejection is deferred to phase 2. Document the descope in the endpoint docstring. | T3.5, T3.6 | API test: POST with `{enabled: true, requirement: "no destructive ops"}` → 200, watchover active; POST with `{enabled: false}` → 200, watchover inactive. Error cases: instance not found → 404; already enabled → 409. |
| T3.8 | Write `test/test_watchover_lifecycle.py`: tests for activation sequence (pause→compaction→flags→resume), deactivation (flags cleared), context construction, flag persistence across restart, and API endpoint integration. | T3.5, T3.6, T3.7 | All tests pass. |
| T3.9 | **Document in-flight tool-call limitation (W-9, CR-3, LD-4 ACCEPTED).** Add a documented limitation: "Watchover activation does NOT guarantee interception of tool calls that began executing before activation was requested. Synchronous tool threads are uncancellable; `pause_instance_cascade` cancels the graph task but cannot stop a tool already running in a worker thread. For maximum safety, activate watchover before starting autonomous work, or pause manually first." Do NOT claim NFR-15 is fully met — mark it "partially met (graph-boundary safe, not thread-safe)" in requirements traceability. | T3.5 | Documentation exists in the activation API docstring + a NOTE in the requirements.md NFR-15 row. |

## Coupling

- **Tight with: Phase 1** — depends on `is_watchover_enabled()` (T1.5) reading the flag this phase sets, and the `watchover_check` routing (T1.6, T1.7) being active.
- **Loose with: Phase 2** — Phase 2's evaluator reads `watchover_context` (set by T3.4); but Phase 2 can be tested with a manually-set context.
- **Tight with: Phase 4** — the frontend (Phase 4) calls this phase's API endpoint.

## Reuse Callouts

| Pattern | Source | Reused For |
|---------|--------|------------|
| `ContextCompactor.compact_state(context)` | `compaction.py:380-781` | `_build_watchover_context` — compaction during activation pause window |
| `CompactionContext` dataclass | `compaction.py:219-231` | Building the compaction input from graph state |
| `pause_instance_cascade` | `manager.py:5348` | Activation step 1: pause |
| `resume_processing_job` | `manager.py:5382` | Activation step 4: resume |
| Pause/resume router endpoints | `instances.py:527`, `:558` | `POST /watchover` endpoint structure |
| `instance_metadata` JSONB | `instance/models.py:63-66` | Flag storage (no migration needed) |
~~| PostgreSQL column-ensure / ALTER TYPE path | Constraint C-7 | `WATCHOVER_SETUP` enum migration |~~ — **REMOVED by Issue 8: no ALTER TYPE / migration exists (suspension_reason is TEXT/VARCHAR).**

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P3-R1 | Compaction during the activation pause window takes too long, leaving the instance paused for an unacceptable duration. | Medium | T3.4: compaction is already used for context management; measure latency in tests. If slow, consider a lightweight summary instead of full compaction for the watchover context. |
| P3-R2 | `resume_processing_job` fails after flags are set, leaving the instance stuck in a paused state with watchover enabled but never resuming. | High | T3.5: wrap the resume in a try/except; on failure, clear the watchover flags (rollback) and return an error so the operator can manually resume. |
| ~~P3-R3~~ | ~~PostgreSQL `ALTER TYPE` limitation~~ — **ELIMINATED.** No `ALTER TYPE` exists; `suspension_reason` is TEXT/VARCHAR. Risk is moot. | — | N/A. |
| P3-R4 | `instance_metadata` JSONB concurrent writes (activation while instance is processing) cause a lost update. | Medium | T3.5: activation pauses the instance first (step 1), so no processing is running when flags are written (step 3). The pause guarantees no concurrent graph task. |

## Exit Criterion

- `POST /instances/{id}/watchover` endpoint exists and works for activate/deactivate.
- Activation: instance pauses → compaction runs → flags set in `instance_metadata` → instance resumes.
- Deactivation: pause → flags cleared → resume → subsequent tool calls passthrough (full sequence per FR-14).
- `SuspensionReason.WATCHOVER_SETUP` exists and is used during the activation pause.
~~- PostgreSQL migration runs cleanly; SQLite fallback works.~~ — **REMOVED by Issue 8: no migration exists (suspension_reason is TEXT/VARCHAR, not a PostgreSQL enum).**
- `test/test_watchover_lifecycle.py` passes.
EXT/VARCHAR, not a PostgreSQL enum).**
- `test/test_watchover_lifecycle.py` passes.
