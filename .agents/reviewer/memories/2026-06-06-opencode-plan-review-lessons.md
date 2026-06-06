# 2026-06-06 — Plan Review Lessons: opencode-native-tools

## Key Insight: Pydantic camelCase JSON Mismatch
When porting Go APIs to Python Pydantic, Go structs use explicit `json:"camelCase"` tags but Python Pydantic defaults to snake_case serialization. Without `alias_generator=to_camel` or `Field(alias=...)`, the API will silently reject requests. This is the **most common** porting mistake in cross-language serialization.

**Fix pattern**:
```python
class ModelDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    provider_id: str = Field(default="litellm", alias="providerID")
    model_id: str = Field(default="coding", alias="modelID")
```

## Key Insight: Async Go-to-Python Concurrency Translation
Go's `goroutine + channel` pattern (`workerDoneChan chan workerResult`) translates to Python as `asyncio.Queue` (not `asyncio.Event` or `asyncio.Future`). The Go pattern of:
1. Set state optimistic (BUSY) BEFORE enqueue
2. Call OnStateChange callback OUTSIDE the lock (deadlock avoidance)
3. Worker writes to channel; main loop reads via `select`

maps to:
1. `async with self._lock: self._state = BUSY; ...` then release
2. After lock release, call `await self.on_state_change(snapshot)`
3. `await self._input_queue.put(request)` with `maxsize=N` (bounded for backpressure)

## Key Insight: State Machine as "File Layout" — Not Enough
A common failure mode in plan docs: define the file structure, type signatures, and factory wiring — but stub out the actual state machine methods as `...`. The state machine IS the hard part. Plans that have `async def submit_request(self, request) -> None: ...` instead of the actual implementation are review-blockers. Force the plan author to write the actual body, not the signature.

## Key Insight: `last_agent` Default Differs Between Systems
Go binary defaulted `last_agent` to `"sisyphus"` (a specific agent name in the OpenCode ecosystem). Python plan defaulted to `""` (empty string). This is a behavioral difference that affects agent lock overrides after the FIRST prompt. When porting stateful systems, check every default value, not just types.

## Key Insight: Time.Sleep for Remote Abort Propagation
Go's 3-second `time.Sleep` after `AbortSession` looks like a code smell, but it's intentional — the remote session needs time to process the abort before the local state is reset. The Python port should use `await asyncio.sleep(3.0)` and document the rationale. This pattern recurs in any system with non-instantaneous remote cleanup.

## Key Insight: "Hidden" Behavioral Features
The Go binary's `start-work` → `atlas` lock is not documented in any of the standard phases — it's a feature that lives in the server.go PROMPT handler and the registry. When reviewing a port, scan for hardcoded strings, agent-name lookups, and command-prefix handlers. These are the "magic" features that aren't visible from the type signatures.

## Key Insight: Migration vs create_all Tradeoff
The plan uses `SQLModel.metadata.create_all(engine)` (dialect-agnostic) AND a separate `.sql` migration file. The create_all runs on every factory call but is idempotent. The migration is useful for documentation and for SQLAlchemy-Alembic tracking. For SQLite, both work. For PostgreSQL, the migration file needs explicit recording in `schema_migrations` (which the plan doesn't show how to do for new tables on Postgres).

## Key Insight: "Test stubs" = Test plan
The phase 5 plan lists test NAMES but no test BODIES. The most important tests (abort race, callback-outside-lock invariant, atlas lock) are stubs. When reviewing test plans, push for explicit assertions — names alone don't prevent regressions.

## Review Methodology Notes
- For plan reviews of porting work, run TWO parallel sessions: (a) Go-to-target-language parity check, (b) Architecture/wiring review. The parity check catches "missing logic" — the wiring review catches "wrong integration."
- For high-concurrency refactors, escalate to `review-deep` (council mode) — the parallelism between sessions caused timeouts in this review (10min+) but produced the highest-signal findings.
- The `find_by_id` full-scan issue (no index on `id` column) only emerged from deep review — standard reviews didn't surface it.

## Project-Specific
- The `data/opencode_skill.json` separate-DB-file decision is a CRITICAL project requirement (per Critical Notes). The plan uses the shared engine by default — this needs explicit decision, not silent inheritance.
- The `is_rag_enabled()` gate in `create_instance_tools` is a placement trap — opencode tools must go OUTSIDE this block.
