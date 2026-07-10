# Investigation: `coder → developer` Alias Removal & Wanderer Restoration Plan

**File:** `daemon/registry.py`
**Lines:** 25-31 (`AGENT_ID_ALIASES`)
**Investigation date:** 2026-07-10
**Mode:** READ-ONLY — no files modified

---

## Executive Summary

The `coder → developer` alias lives at `daemon/registry.py:29-31` in `AGENT_ID_ALIASES`.

**CRITICAL OBSERVATION:** The `agents/coder/` directory STILL EXISTS with `id: "coder"` (verified). It is a *separate, deliberately authored* agent ("opposite of developer", direct hands-on implementer) — not an orphan. The alias currently **SHADOWS** it: `registry.resolve_pure_id("coder")` returns `"developer"`, not `"coder"`, so the real `coder` agent is unreachable through normal API paths today.

The `wanderer` agent was made deliberately self-sufficient (no `team_members`, no `instance` tool) **specifically** to avoid this alias issue, per a forward-looking note in `agents/wanderer/soul.md:185-189`.

**Removing the alias is safe for already-migrated DBs.** Partial-migration rows (`agent_id='coder'` remaining) will silently resolve to the real `coder` agent instead of erroring — semantically different but not crashing.

---

## PART 1: DATABASE IMPACT

### 1.1. `_restore_instance()` — Alias Resolution in Instance Restoration

**FILE:** `daemon/services/instance_lifecycle.py`
**LINES:** 1460-1494

**CURRENT CODE:**
```python
    def _restore_instance(self, instance_id: str, meta: Instance) -> CompiledStateGraph:
        """Restore an instance from database into memory.

        Rebuilds the graph with the same instance_id. The checkpointer will
        restore conversation state from LangGraph's checkpoint tables.

        Args:
            instance_id: The ID of the instance to restore.
            meta: Instance metadata from database.

        Returns:
            The restored CompiledStateGraph instance.
        """
        # Access manager's state dynamically for test compatibility
        instance_repository = self._manager._instance_repository
        project_repository = self._manager._project_repository
        prompt_cache = self._manager.prompt_cache

        # Load MCP tool names for prompt generation (prefer cache, fallback to stored)
        stored_mcp = meta.instance_metadata.get("mcp_tool_names") if meta.instance_metadata else None
        mcp_tool_names = self._get_mcp_tool_names(instance_id, stored_mcp)

        # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
        # DB may still contain the old agent_id if migration was partial/skipped.
        registry = get_registry()
        agent_meta = registry.get_resolved(meta.agent_id)
        resolved_agent_id = registry.resolve_pure_id(meta.agent_id) or meta.agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {meta.agent_id}")

        # Load and cache prompt using resolved path (pass MCP tool names for category expansion)
        # Import from manager to pick up test patches
        from ..manager import load_and_cache_prompt
        agent_path = Path(agent_meta.path)
        system_prompt, token_count = load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names)
```

**HOW IT HANDLES `agent_id='coder'` (with alias present):**
- Line 1484: `registry = get_registry()` — global registry singleton
- Line 1485: `agent_meta = registry.get_resolved(meta.agent_id)` → calls `get_resolved("coder")`:
  - Internally: `resolve_pure_id("coder")` → `AGENT_ID_ALIASES.get("coder", "coder")` → `"developer"` (alias)
  - `"developer" in self._agents` → `True` (developer directory exists with `id: "developer"`)
  - Returns `"developer"`, then `self._agents.get("developer")` → developer `AgentMetadata`
- Line 1486: `resolved_agent_id = registry.resolve_pure_id(meta.agent_id) or meta.agent_id` → `"developer"`
- Line 1493: `agent_path = Path(agent_meta.path)` → `/abs/path/to/agents/developer`
- Line 1494: `load_and_cache_prompt(resolved_agent_id, agent_path, ...)` → loads prompt from `agents/developer/`
- The `tools.allow` filter loaded is **developer's** list: `["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]`, NOT coder's list

**WHAT HAPPENS IF ALIAS IS REMOVED (no other change):**
- Line 1485: `registry.get_resolved("coder")`:
  - `resolve_pure_id("coder")` now: `AGENT_ID_ALIASES.get("coder", "coder")` → `"coder"` (no alias)
  - `"coder" in self._agents` → `True` (since `agents/coder/` is still registered with `id: "coder"`)
  - Returns `"coder"`, then `self._agents.get("coder")` → coder `AgentMetadata`
- Line 1486: `resolved_agent_id = "coder"` (no alias to resolve)
- Line 1493: `agent_path = Path("/abs/path/to/agents/coder")`
- Line 1494: `load_and_cache_prompt("coder", agent_path, ...)` → loads prompt from `agents/coder/`
- The `tools.allow` filter loaded is **coder's** list: `["bash", "filesystem", "time", "self", "help", "knowledge", "context"]` — missing `mcp` and `db`

**REQUIRED CHANGE:**
```python
        # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
        # DB may still contain the old agent_id if migration was partial/skipped.
        registry = get_registry()
        agent_meta = registry.get_resolved(meta.agent_id)
        resolved_agent_id = registry.resolve_pure_id(meta.agent_id) or meta.agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {meta.agent_id}")
```
becomes:
```python
        # Resolve agent metadata directly (alias 'coder'→'developer' has been removed;
        # DB rows with stale 'agent_id' will load their canonical agent's metadata)
        registry = get_registry()
        agent_meta = registry.get(meta.agent_id)
        resolved_agent_id = meta.agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {meta.agent_id}")
```
OR (less invasive — keep `get_resolved` which becomes identity after alias removal):
```python
        registry = get_registry()
        agent_meta = registry.get_resolved(meta.agent_id)
        resolved_agent_id = meta.agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {meta.agent_id}")
```

**RISK:** **medium** — Existing DB rows with `agent_id='coder'` (partial-migration survivors) will silently switch from loading as **developer** (orchestrator, opencode-based, has `mcp`+`db` tools) to loading as **coder** (direct hands-on implementer, no `mcp`/`db` tools). Not crashing; semantic behavior shift for any pre-migration orphan instance.

---

### 1.2. `Instance` SQLModel — agent_id Column

**FILE:** `daemon/repositories/instance/models.py`
**LINES:** 47-54

**CURRENT CODE:**
```python
class Instance(SQLModel, table=True):
    """SQLModel Instance table - internal ORM representation."""
    __tablename__ = "instances"

    instance_id: str = Field(primary_key=True)
    project_id: str | None = Field(default=None, sa_column=Column("project_id", String, nullable=True))
    agent_id: str = Field(index=True)
    agent_dir: str = Field(index=True)
    agent_name: str | None = Field(default=None, index=True)
```

**REQUIRED CHANGE:** No schema change. The `agent_id` column is plain `str` with an index, no CHECK constraint, no foreign key, no enum. Any string value is accepted.

**RISK:** **low**

---

### 1.3. `InstanceCreate.normalize_agent_id` — Pydantic Alias Normalizer

**FILE:** `daemon/models/instance.py`
**LINES:** 19-24

**CURRENT CODE:**
```python
    @field_validator("agent_id")
    @classmethod
    def normalize_agent_id(cls, v: str) -> str:
        """Normalize agent_id aliases (backward compat for renamed agents)."""
        from daemon.registry import AGENT_ID_ALIASES
        return AGENT_ID_ALIASES.get(v, v)
```

**REQUIRED CHANGE:** Remove the validator. The class becomes:
```python
class InstanceCreate(BaseModel):
    """Request for spawning a new instance."""

    agent_id: str = Field(..., description="Agent ID (e.g., 'developer')")
    instance_id: str | None = Field(default=None, description="Optional instance ID")
    project_id: str | None = Field(default=None, description="Optional project ID for associating instance with a project")

    @model_validator(mode='after')
    def validate_agent(self):
        if not self.agent_id:
            raise ValueError('agent_id is required')
        return self
```

**RISK:** **medium** — Any API caller passing `agent_id='coder'` will now hit the `coder` agent instead of `developer`. Existing clients (HTTP API, message source, scheduler, tests, external clients) that historically sent `"coder"` expecting developer semantics will break.

---

### 1.4. SQLite Rename Migration — SQLite Production Path

**FILE:** `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql`
**LINES:** 1-132 (entire file)

**CURRENT CODE:**
```sql
-- UP
UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder';

-- DOWN (documented as not future-proof for already-used developer rows)
UPDATE instances SET agent_id = 'coder', agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder') WHERE agent_id = 'developer' AND agent_dir LIKE '%/agents/developer%';
UPDATE instance_mappings SET agent_id = 'coder', agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder') WHERE agent_id = 'developer' AND agent_dir LIKE '%/agents/developer%';
UPDATE job_queue_items SET agent_id = 'coder', agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder') WHERE agent_id = 'developer' AND agent_dir LIKE '%/agents/developer%';
UPDATE dead_letter_items SET agent_id = 'coder', agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder') WHERE agent_id = 'developer' AND agent_dir LIKE '%/agents/developer%';
UPDATE projects SET creator_agent_id = 'coder' WHERE creator_agent_id = 'developer';
```

**REQUIRED CHANGE:** **Recommendation: KEEP in place (Option A).** The UP block is idempotent — once the rename is complete, every UPDATE matches zero rows and exits cleanly. The runner records the version in `schema_migrations`, so subsequent startups skip it. The DOWN block is documented as unsafe (lines 96-104) — any new `developer` rows that legitimately belong to the canonical developer agent would be incorrectly reverted. Optional cleanup (Phase 3): delete the entire file after alias removal is confirmed stable.

**RISK:** **low** — Idempotent by design.

---

### 1.5. PostgreSQL Runtime Migration — `_ensure_postgres_columns`

**FILE:** `daemon/manager.py`
**LINES:** 2029-2046

**CURRENT CODE:**
```python
            # NOTE: coder→developer migration is also handled in:
            #   - daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql (SQLite production)
            #   - scripts/migrate_coder_to_developer.py (standalone manual tool)
            # ── Agent rename: coder → developer ──────────────────────────────
            # Idempotent UPDATE: renames agent_id and agent_dir from the old
            # 'coder' agent to 'developer'. Safe to re-run (WHERE clause is a
            # no-op if no rows match).
            "UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
            "UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
            "UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
            "UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
            "UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder'",
            "DO $$ BEGIN UPDATE jobqueue SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'; EXCEPTION WHEN undefined_table THEN NULL; END $$",
```

**REQUIRED CHANGE:** **Recommendation: KEEP in place.** Same rationale as 1.4 — idempotent, no-op on already-migrated DBs. Update comment to remove the "via alias" implication and document the new semantic.

**RISK:** **low** — Idempotent.

---

### 1.6. Standalone Migration Script

**FILE:** `scripts/migrate_coder_to_developer.py`
**LINES:** 1-110 (entire file)

**CURRENT CODE:** Python script with pre-check, idempotent UPDATE statements (same 5 tables), and post-check for both SQLite and PostgreSQL.

**REQUIRED CHANGE:** No change needed. Standalone one-shot backfill tool. Remains valid. Optionally deprecate after alias removal is confirmed.

**RISK:** **low**

---

### 1.7. `job_queue_service.enqueue()` — Idempotency Path

**FILE:** `daemon/services/job_queue_service.py`
**LINES:** 572-584

**CURRENT CODE:**
```python
        if idempotency_key:
            # Derive agent_dir from agent_id using registry before the
            # atomic insert — we still need it for both the insert path
            # and the registry validation below.
            # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
            # since agent_id may come from a DB row that still has the old value.
            registry = get_registry()
            agent_meta = registry.get_resolved(agent_id)
            resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
            if agent_meta is None:
                raise ValueError(f"Agent not found: {agent_id}")
            agent_dir = str(agent_meta.path)
            agent_id = resolved_agent_id
```

**REQUIRED CHANGE:**
```python
        if idempotency_key:
            registry = get_registry()
            agent_meta = registry.get(agent_id)
            if agent_meta is None:
                raise ValueError(f"Agent not found: {agent_id}")
            agent_dir = str(agent_meta.path)
```

**RISK:** **medium** — DB-loaded job items (idempotency-key replay path) for stale `agent_id='coder'` rows will now create jobs for the `coder` agent instead of `developer`.

---

### 1.8. `job_queue_service.enqueue()` — Regular Path

**FILE:** `daemon/services/job_queue_service.py`
**LINES:** 690-700

**CURRENT CODE:**
```python
        # Non-idempotency path (or terminal-fallback path above).
        # Derive agent_dir from agent_id using registry.
        # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
        # since agent_id may come from a DB row that still has the old value.
        registry = get_registry()
        agent_meta = registry.get_resolved(agent_id)
        resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
        agent_id = resolved_agent_id
```

**REQUIRED CHANGE:**
```python
        # Non-idempotency path (or terminal-fallback path above).
        # Derive agent_dir from agent_id using registry.
        registry = get_registry()
        agent_meta = registry.get(agent_id)
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
```

**RISK:** **medium** — Same as 1.7.

---

### 1.9. `child_reports._get_instance_report_prefix()` — Display Name Fallback

**FILE:** `daemon/services/child_reports.py`
**LINES:** 387-398

**CURRENT CODE:**
```python
        # Get agent display name from meta.json
        # Resolve alias first (backward compat for renamed agents like 'coder'→'developer')
        # so fallback shows "Developer" instead of "Coder" if registry lookup misses.
        registry = get_registry()
        resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
        agent_name = resolved_agent_id.capitalize()

        try:
            metadata = registry.get_resolved(agent_id)
            if metadata and metadata.name:
                agent_name = metadata.name
        except Exception:
            pass
```

**REQUIRED CHANGE:**
```python
        registry = get_registry()
        agent_name = agent_id.capitalize()

        try:
            metadata = registry.get(agent_id)
            if metadata and metadata.name:
                agent_name = metadata.name
        except Exception:
            pass
```

**RISK:** **low** — Cosmetic. Display name for `agent_id='coder'` was "Developer" (alias → capitalised); will become "Coder" (from `agents/coder/meta.json` name field).

---

### 1.10. `loader.load_tools_doc_for_agent()` — Tool Doc Builder

**FILE:** `daemon/loader.py`
**LINES:** 102-117

**CURRENT CODE:**
```python
    # Get agent's tool filter from registry
    # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    # so tool filtering uses the correct agent's filter instead of skipping.
    tool_filter: ToolFilter | None = None
    agent_innate_skills: list[str] | None = None
    try:
        registry = get_registry()
        agent_meta = registry.get_resolved(agent_id)
        if agent_meta is not None:
            tool_filter = agent_meta.tools
            agent_innate_skills = agent_meta.innate_skills
            # Use resolved id for downstream tool filtering context
            resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
            agent_id = resolved_agent_id
    except (KeyError, ValueError, RuntimeError) as e:
        logger.debug(f"Registry lookup failed for {agent_id}: {e}")
        return ""
```

**REQUIRED CHANGE:**
```python
    tool_filter: ToolFilter | None = None
    agent_innate_skills: list[str] | None = None
    try:
        registry = get_registry()
        agent_meta = registry.get(agent_id)
        if agent_meta is not None:
            tool_filter = agent_meta.tools
            agent_innate_skills = agent_meta.innate_skills
    except (KeyError, ValueError, RuntimeError) as e:
        logger.debug(f"Registry lookup failed for {agent_id}: {e}")
        return ""
```

**RISK:** **medium** — Tool set is materially different:
- `agents/coder/meta.json` `tools.allow`: `["bash", "filesystem", "time", "self", "help", "knowledge", "context"]`
- `agents/developer/meta.json` `tools.allow`: `["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]`

A restored instance from a stale `agent_id='coder'` row will now receive coder's tool filter (no `mcp`, no `db`) instead of developer's. This shrinks the instance's effective tool surface.

---

### 1.11. `utils.validate_agent_id()` — HTTP/API Validation

**FILE:** `daemon/utils.py`
**LINES:** 423-466

**CURRENT CODE:**
```python
def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    """Validate agent_id exists and return agent_id with path.

    This is the preferred function for validating agent references.
    Resolves agent_id aliases (e.g., ``"coder"`` -> ``"developer"``) via
    the registry before lookup so persisted/legacy references continue
    to resolve after a rename. Returns the canonical agent_id from
    the registry metadata, not the raw input.
    """
    registry = get_registry()

    # Resolve alias (e.g., "coder" -> "developer") before dict lookup.
    # registry.get() does NOT resolve aliases, so this step is required
    # to support backward-compatible references to renamed agents.
    resolved_agent_id = registry.resolve_pure_id(agent_id)
    if resolved_agent_id is None:
        raise HTTPException(status_code=404, detail=ErrorResponse(
            code=ErrorCodes.INVALID_REQUEST,
            message=f"Agent not found: {agent_id}"
        ).model_dump())

    metadata = registry.get(resolved_agent_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=ErrorResponse(
            code=ErrorCodes.INVALID_REQUEST,
            message=f"Agent not found: {agent_id}"
        ).model_dump())

    return metadata.id, metadata.path
```

**REQUIRED CHANGE:**
```python
def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    """Validate agent_id exists and return agent_id with path."""
    registry = get_registry()
    metadata = registry.get(agent_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=ErrorResponse(
            code=ErrorCodes.INVALID_REQUEST,
            message=f"Agent not found: {agent_id}"
        ).model_dump())

    return metadata.id, metadata.path
```

**RISK:** **medium** — Primary external breakage vector. Any HTTP caller, message source (Telegram, scheduler), or test that passes `agent_id='coder'` will now resolve to the real `coder` agent instead of `developer`.

---

### 1.12. `tools/instance._check_team_membership()` — Spawn Authorization Gate

**FILE:** `daemon/tools/instance.py`
**LINES:** 237-313

**CURRENT CODE (full function):**
```python
def _check_team_membership(caller_agent_id: str, requested_agent_id: str) -> str | None:
    """Verify the caller agent is allowed to spawn the requested agent.

    Reads the caller's ``meta.json`` ``team_members`` list and checks that the
    requested agent_id (resolved to its canonical id) is present. Returns
    ``None`` when the spawn is permitted, or an error message describing the
    rejection when it is not.

    Both the caller's list entries AND the requested ``agent_id`` are
    canonicalized via :func:`registry.resolve_pure_id` to prevent
    alias-bypass attacks (e.g. ``"coder"`` for ``"developer"``).

    Secure default: ``team_members`` missing OR empty → deny everything.
    """
    from ..registry import get_registry

    registry = get_registry()

    requested_canonical = registry.resolve_pure_id(requested_agent_id)
    if requested_canonical is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_agent_id}'. Requested agent does not exist. "
            "Allowed team members: []"
        )

    caller_meta = registry.get_resolved(caller_agent_id)
    if caller_meta is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_canonical}'. Caller agent not found. "
            "Allowed team members: []"
        )

    caller_canonical = caller_meta.id
    raw_members = caller_meta.team_members or []

    allowed_canonical: set[str] = set()
    for member in raw_members:
        canonical = registry.resolve_pure_id(member)
        if canonical is not None:
            allowed_canonical.add(canonical)

    if requested_canonical not in allowed_canonical:
        allowed_display = sorted(allowed_canonical) if allowed_canonical else []
        return (
            f"Agent '{caller_canonical}' is not allowed to spawn "
            f"'{requested_canonical}'. Allowed team members: {allowed_display}"
        )

    return None
```

**REQUIRED CHANGE:**
```python
def _check_team_membership(caller_agent_id: str, requested_agent_id: str) -> str | None:
    """Verify the caller agent is allowed to spawn the requested agent.

    Reads the caller's ``meta.json`` ``team_members`` list and checks that the
    requested agent_id is present. Returns ``None`` when the spawn is
    permitted, or an error message describing the rejection when it is not.

    Secure default: ``team_members`` missing OR empty → deny everything.
    """
    from ..registry import get_registry

    registry = get_registry()

    requested_meta = registry.get(requested_agent_id)
    if requested_meta is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_agent_id}'. Requested agent does not exist. "
            "Allowed team members: []"
        )

    caller_meta = registry.get(caller_agent_id)
    if caller_meta is None:
        return (
            f"Agent '{caller_agent_id}' is not allowed to spawn "
            f"'{requested_meta.id}'. Caller agent not found. "
            "Allowed team members: []"
        )

    raw_members = caller_meta.team_members or []
    if requested_meta.id not in raw_members:
        return (
            f"Agent '{caller_meta.id}' is not allowed to spawn "
            f"'{requested_meta.id}'. Allowed team members: {sorted(raw_members)}"
        )

    return None
```

**NOTE:** The alias-bypass defense (`resolve_pure_id` on both sides) was explicitly documented as security hardening. With the alias removed, the attack surface disappears — no alias to bypass. The canonicalization can be safely deleted. But the docstring should note that future renames should re-introduce canonicalization.

**RISK:** **low**

---

### 1.13. `tools/instance.py:580-605` — `spawn_instance` Tool Comment

**FILE:** `daemon/tools/instance.py`
**LINES:** 580-605

**REQUIRED CHANGE:** Update comment at line 591 to remove the "coder" alias example:
```python
        # so legacy aliases (e.g. "coder") cannot bypass the check.
```
becomes:
```python
        # so future aliases cannot bypass the team_members check.
```

**RISK:** **low**

---

### 1.14. `daemon/registry.py` — The Alias Declaration Itself

**FILE:** `daemon/registry.py`
**LINES:** 25-31

**CURRENT CODE:**
```python
# Backward-compatibility aliases for renamed agent IDs.
# Maps old agent_id -> new canonical agent_id. Used by ``resolve_pure_id``
# and (transitively) by ``resolve_path_to_id`` and ``exists`` so that
# persisted references to the old ID continue to resolve after a rename.
AGENT_ID_ALIASES: dict[str, str] = {
    "coder": "developer",
}
```

**REQUIRED CHANGE:**
```python
# Backward-compatibility aliases for renamed agent IDs.
# Maps old agent_id -> new canonical agent_id. Used by ``resolve_pure_id``
# and (transitively) by ``resolve_path_to_id`` and ``exists`` so that
# persisted references to the old ID continue to resolve after a rename.
#
# NOTE: 'coder' has been deliberately removed from this alias map because
# agents/coder/ now exists as a separate, canonical agent (direct hands-on
# implementer, distinct from developer). Stale DB rows that still have
# agent_id='coder' will load agents/coder/'s metadata directly.
AGENT_ID_ALIASES: dict[str, str] = {}
```

**RISK:** **medium** (this is the central change; all other call-site cleanups flow from here)

---

### 1.15. `daemon/registry.py` — `team_members` Field Docstring

**FILE:** `daemon/registry.py`
**LINES:** 85-95

**CURRENT CODE:**
```python
    team_members: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical agent_ids that THIS agent is allowed to spawn via "
            "spawn_instance. Empty/missing means deny-by-default — the agent "
            "cannot spawn any other agents. Enforced by the spawn_instance "
            "tool layer before any DB transaction. Aliases (e.g. 'coder') are "
            "resolved to canonical ids (e.g. 'developer') via the registry."
        ),
    )
```

**REQUIRED CHANGE:** Remove the alias example:
```python
    team_members: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical agent_ids that THIS agent is allowed to spawn via "
            "spawn_instance. Empty/missing means deny-by-default — the agent "
            "cannot spawn any other agents. Enforced by the spawn_instance "
            "tool layer before any DB transaction."
        ),
    )
```

**RISK:** **low**

---

### 1.16. `daemon/registry.py` — `get_resolved` Docstring

**FILE:** `daemon/registry.py`
**LINES:** 223-240

**REQUIRED CHANGE:** Update docstring to reflect new semantics. Implementation can remain (becomes equivalent to `get()` after alias removal, but kept for defensive forward-compatibility):
```python
    def get_resolved(self, agent_id: str) -> AgentMetadata | None:
        """Get agent metadata, resolving aliases first.

        Use this when ``agent_id`` may come from an external source that
        could contain a legacy alias. Returns ``None`` if the ID is unknown
        even after alias resolution. With AGENT_ID_ALIASES empty, this is
        functionally equivalent to ``get()`` but is kept for defensive
        forward-compatibility in case a future rename re-introduces aliases.

        Args:
            agent_id: The agent identifier (may be an alias).

        Returns:
            AgentMetadata for the canonical agent if found, else ``None``.
        """
```

**RISK:** **low**

---

### 1.17. `daemon/repositories/factory.py` — Comment Block

**FILE:** `daemon/repositories/factory.py`
**LINES:** 316-323

**REQUIRED CHANGE:** Add clarifying note about coder being a separate real agent:
```python
        # NOTE: The agent_id rename 'coder' → 'developer' was previously handled
        # here as a Python UPDATE block. This function is no longer called in
        # production (factory creation paths rely on the SQLModel metadata +
        # MigrationRunner pipeline). Production SQLite migrations are now
        # applied via:
        #   daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql
        # and the corresponding PostgreSQL updates live in
        # daemon/manager.py:_ensure_postgres_columns().
        #
        # Historical note: agents/coder/ is a SEPARATE, currently-existing
        # canonical agent (direct hands-on implementer). The 'coder'→'developer'
        # alias was removed from AGENT_ID_ALIASES once the rename migration
        # completed, so stale DB rows with agent_id='coder' now load directly
        # as coder instances rather than developer instances.
```

**RISK:** **low**

---

### 1.18. Frontend Color Map — `message-input.component.ts`

**FILE:** `frontend/src/app/components/message-input/message-input.component.ts`
**LINES:** 69-79

**CURRENT CODE:**
```typescript
  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'developer': '#10a7f7',
    'coder': '#10a7f7',  // backward compat for cached responses
    'reviewer': '#8b5cf6',
    'charter': '#3b82f6',
  };

  readonly color = computed(() => {
    return this.agentColorMap[this.agentColor()] || '#10a7f7';
  });
```

**REQUIRED CHANGE:** Remove the `'coder'` entry:
```typescript
  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'developer': '#10a7f7',
    'reviewer': '#8b5cf6',
    'charter': '#3b82f6',
  };

  readonly color = computed(() => {
    return this.agentColorMap[this.agentColor()] || '#10a7f7';
  });
```

**RISK:** **low** — Cosmetic only. Cached responses with `agentColor='coder'` will fall back to default `#10a7f7`.

---

### 1.19. Frontend Color Map — `message-input.component.spec.ts`

**FILE:** `frontend/src/app/components/message-input/message-input.component.spec.ts`
**LINE:** 36

**REQUIRED CHANGE:** Remove the `'coder': '#10a7f7'` line from the test fixture.

**RISK:** **low**

---

### 1.20. Frontend Color Map — `chat-interface.component.ts`

**FILE:** `frontend/src/app/components/chat-interface/chat-interface.component.ts`
**LINES:** 91-97

**REQUIRED CHANGE:** Same as 1.18 — remove `'coder': '#10a7f7'` entry.

**RISK:** **low**

---

### 1.21. Test Files — Alias Behavior Verification

**FILE:** `tests/test_registry.py`
**LINES:** 654-692

**CURRENT CODE (alias tests):**
```python
    def test_resolve_pure_id_alias(self) -> None:
        """resolve_pure_id('coder') returns 'developer' via alias."""
        result = registry.resolve_pure_id("coder")

    def test_get_resolved_alias(self) -> None:
        """get_resolved('coder') returns the canonical developer metadata via alias."""
        resolved = registry.get_resolved("coder")

    def test_get_resolved_canonical(self) -> None:
        """get_resolved('developer') returns the same metadata as get('developer')."""

    def test_get_resolved_unknown_returns_none(self) -> None:
        """get_resolved for an unknown id returns None (alias-aware)."""

    def test_exists_alias(self) -> None:
        """exists('coder') returns True via alias."""
```

**REQUIRED CHANGE:** Rewrite to assert new behavior. Suggested replacement:
```python
    def test_resolve_pure_id_coder_is_real_agent(self) -> None:
        """resolve_pure_id('coder') returns 'coder' (no alias; coder is real agent)."""
        result = registry.resolve_pure_id("coder")
        assert result == "coder"

    def test_get_resolved_coder_returns_coder_metadata(self) -> None:
        """get_resolved('coder') returns coder agent's metadata directly."""
        resolved = registry.get_resolved("coder")
        assert resolved is not None
        assert resolved.id == "coder"
        assert resolved.name == "Coder"

    def test_get_resolved_canonical(self) -> None:
        """get_resolved('developer') returns the same metadata as get('developer')."""
        assert registry.get_resolved("developer") == registry.get("developer")

    def test_get_resolved_unknown_returns_none(self) -> None:
        """get_resolved for an unknown id returns None."""
        assert registry.get_resolved("definitely-not-an-agent") is None

    def test_exists_coder_via_real_agent(self) -> None:
        """exists('coder') returns True (coder is a real agent, not alias)."""
        assert registry.exists("coder") is True
```

**RISK:** **medium** — CI failure if not updated.

---

### 1.22. Test File — `tests/test_spawn_team_members.py`

**FILE:** `tests/test_spawn_team_members.py`
**LINES:** 309-350, 603-610

**CURRENT CODE:** Alias-canonicalization tests for legacy `coder` alias in both request and caller contexts.

**REQUIRED CHANGE:** These tests pre-suppose the alias exists. Rewrite:
- `test_legacy_alias_in_request_canonicalizes` → DELETE (no legacy alias)
- `test_legacy_alias_as_caller_canonicalizes` → REWRITE to test that a wanderer instance can spawn a coder instance via `team_members=["coder"]` (requires wanderer meta.json update first)
- `test_request_coder_canonicalizes` → REWRITE to test direct coder spawn from leader (requires adding `coder` to leader's `team_members` or testing the wanderer path)
- `test_caller_coder_canonicalizes` → REWRITE to test that a coder instance trying to spawn (with empty `team_members`) is rejected

**RISK:** **medium** — CI failure if not updated.

---

### 1.23. Test File — `tests/test_spawn_instance_validation.py`

**FILE:** `tests/test_spawn_instance_validation.py`
**LINES:** 15-32

**CURRENT CODE:**
```python
    # Test 2a: agent_dir='./agents/coder' (with ./ prefix) → resolves to 'developer' via alias
    resolved_id = registry.resolve_to_id("./agents/coder")
    # Test 2b: agent_dir='agents/coder' (without ./ prefix) → resolves to 'developer' via alias
    resolved_id = registry.resolve_to_id("agents/coder")

    # Test 3a: spawn_instance(agent_id='coder') → resolves to 'developer' via alias
```

**REQUIRED CHANGE:**
```python
    # Test 2a: agent_dir='./agents/coder' (with ./ prefix) → resolves to 'coder' (real agent)
    resolved_id = registry.resolve_to_id("./agents/coder")
    assert resolved_id == "coder"
    # Test 2b: agent_dir='agents/coder' (without ./ prefix) → resolves to 'coder' (real agent)
    resolved_id = registry.resolve_to_id("agents/coder")
    assert resolved_id == "coder"

    # Test 3a: spawn_instance(agent_id='coder') → uses 'coder' (real agent)
```

**RISK:** **medium** — CI failure if not updated.

---

### 1.24. Test File — `tests/unit/test_coder_developer_migration.py` (CRASH-RECOVERY BLOCK)

**FILE:** `tests/unit/test_coder_developer_migration.py`
**LINES:** 524-903 (Part B: Alias-Resolution Crash-Recovery Tests)

**CURRENT CODE (key test):**
```python
class TestRestoreInstanceWithAlias:
    """Verify _restore_instance() handles stale 'coder' agent_id from DB."""

    def test_restore_instance_with_coder_agent_id_does_not_raise(self):
        """_restore_instance must not raise when DB row has agent_id='coder'.

        Reproducer: a partially-migrated DB where instances.agent_id still
        reads 'coder'. Before the fix, registry.get('coder') returned None
        → ValueError("Agent not found: coder"). After the fix, resolve_pure_id
        maps 'coder' → 'developer' and the restore succeeds.
        """
        # ...uses mock_registry.resolve_pure_id.return_value = "developer"
        # ...asserts the alias resolution chain executed

    def test_enqueue_with_coder_agent_id_does_not_raise(self):
        """enqueue(agent_id='coder') must not raise ValueError."""
        # ...mock setup with resolve_pure_id('coder')→'developer'
```

**REQUIRED CHANGE:** **REWRITE entirely.** The semantic changes:
- **Old:** `agent_id='coder'` row → resolves to `developer` via alias → restores as developer
- **New:** `agent_id='coder'` row → resolves to `coder` (real agent, alias gone) → restores as coder

New test class should assert:
```python
class TestRestoreInstanceWithPreMigrationAgentId:
    """Verify _restore_instance() handles 'coder' agent_id from a pre-migration DB.

    After the alias removal, a DB row with agent_id='coder' now loads the
    agents/coder/ metadata directly (direct hands-on implementer), NOT
    agents/developer/ (orchestrator). This is a deliberate semantic
    distinction: stale DB rows load as coder instances.
    """

    def test_restore_instance_with_coder_agent_id_loads_coder(self):
        """_restore_instance must load coder agent metadata for agent_id='coder'."""
        # Mock the real registry behavior:
        # - agents/coder/ exists
        # - resolve_pure_id('coder') returns 'coder' (no alias)
        # - get_resolved('coder') returns coder's AgentMetadata
        # Verify the restored instance uses coder's path and metadata.

    def test_enqueue_with_coder_agent_id_dispatches_to_coder(self):
        """job_queue_service.enqueue(agent_id='coder') dispatches to coder agent."""
        # Verify agent_id stays 'coder' through the flow.
```

**RISK:** **medium** — CI failure if not rewritten.

---

### 1.25. Other Test Files Referencing Alias

**FILE:** Multiple (see inventory)

| File | Line(s) | Pattern | Required Change |
|------|---------|---------|----------------|
| `tests/test_loader.py` | 841-844 | Mock `get_resolved.return_value` for tool filter | No logic change; mock still works |
| `tests/test_spawn_instance_instructive_errors.py` | 307 | `resolve_pure_id.return_value = "developer"` | Update mock expectation |
| `tests/test_message_job_bridge.py` | 333, 436, 530, 719, 788, 840 | `get_resolved.return_value = None` (not-found case) | No change — None case unaffected |
| `tests/unit/test_llm_config_override.py` | 528-529 | Mock setup | Verify behavior preserved |
| `tests/unit/test_wanderer_agent.py` | 361 | Mock `get_resolved.return_value` for wanderer | No change — wanderer meta unchanged |
| `tests/unit/test_coder_agent.py` | 441-442 | Mock `get_resolved.return_value` for coder | No change — coder meta unchanged |
| `tests/job_queue/test_idempotent_enqueue.py` | 651-653 | `get_resolved.return_value = None` | No change |
| `tests/test_message_job_serialization.py` | 338, 542, 654 | `get_resolved.return_value = None` | No change |
| `tests/unit/test_validate_agent_id_compat.py` | 62 | Mock setup | Update if alias is removed |

**RISK:** **low to medium** depending on the test.

---

## PART 2: WANDERER RESTORATION PLAN

### 2.1. `agents/wanderer/meta.json` — Current State

**FILE:** `agents/wanderer/meta.json`
**LINES:** 1-12 (entire file)

**CURRENT CODE:**
```json
{
  "id": "wanderer",
  "name": "Wanderer",
  "description": "Read-only investigation and research specialist. Explores codebases, answers questions, does deep research on libraries from GitHub/internet. Does all investigation directly — read_file, grep_files, glob_files, bash, web search, and RAG.",
  "icon": "🧭",
  "color": "accent-green",
  "version": "0.1.0",
  "innate_skills": ["todo", "chart"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "rag"]
  }
}
```

**REQUIRED CHANGE:**
```json
{
  "id": "wanderer",
  "name": "Wanderer",
  "description": "Read-only investigation and research specialist. Explores codebases, answers questions, does deep research on libraries from GitHub/internet. Does all investigation directly — read_file, grep_files, glob_files, bash, web search, and RAG. For bounded implementation work that surfaces during investigation (write a test, apply a targeted fix), delegates to coder instances via spawn_instance.",
  "icon": "🧭",
  "color": "accent-green",
  "version": "0.2.0",
  "innate_skills": ["todo", "chart"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "rag", "instance"]
  },
  "team_members": ["coder"]
}
```

**Specific diffs:**
- **description (line 4):** Append delegation clause to the end
- **version (line 7):** Bump `0.1.0` → `0.2.0` (semver minor for additive capability)
- **tools.allow (line 10):** Add `"instance"` to the allow list — required for `spawn_instance` tool exposure
- **New field after tools:** Add `"team_members": ["coder"]`

**DESIGN NOTE:** Should wanderer also spawn `developer`? The soul.md contrast ("opposite of developer") suggests `coder` is the right pairing for a read-only investigator that wants hands-on help without orchestration overhead. **Recommendation: `["coder"]` only for the initial restoration; add others later if use cases emerge.**

**RISK:** **low** — Additive only. Wanderer retains all existing capabilities and gains the ability to spawn coder instances.

---

### 2.2. `agents/wanderer/soul.md` — Current State and Required Changes

**FILE:** `agents/wanderer/soul.md`
**LINES:** 1-189 (entire file)

**REQUIRED CHANGES (7 passages):**

---

**Line 3 (Status header) — UPDATE:**
```markdown
**Status:** 🧭 Wanderer Agent — Read-Only Investigator & Research Specialist. Delegates bounded implementation work to coder instances; everything else is read-only.
```

---

**Line 5 (Identity paragraph) — UPDATE:**
```markdown
I am a read-only investigation agent. I explore, examine, and report — I do not modify source code, tests, configs, or any persisted system state directly. I read source code, trace data flow, follow imports, inspect logs, search the knowledge base, research libraries on GitHub and the wider internet, and produce a clear, evidence-based report. I am the eyes and ears of the team; the hands belong to coder.
```

---

**Line 16 (Role bullet) — UPDATE:**
```markdown
- **Role:** Read-only investigator. Not an orchestrator, not a planner. Delegates bounded implementation work to coder (my only team member) and returns to investigation.
```

---

**Line 27 (Core Beliefs 6) — UPDATE:**
```markdown
6. **Self-sufficient investigation by default** — I do all read-only investigation work with my own tools (read_file, grep_files, glob_files, bash, MCP, RAG). For complex investigations, I break the question into sub-questions and work through them systematically. When investigation surfaces a concrete bounded implementation task (write/edit code, run tests, apply a targeted fix), I delegate to a coder instance via `spawn_instance` — coder is hands-on where I am eyes-only.
```

---

**Line 28 (Core Beliefs 7) — UPDATE:**
```markdown
7. **Know my limits** — If a task needs code changes, architecture decisions, or system writes, I either delegate the bounded implementation slice to coder or hand back to the leader. I do not implement directly.
```

---

**Line 42 (What I Do bullets) — ADD new bullet at end:**
```markdown
- **Delegate bounded implementation work** to coder instances via `spawn_instance(agent_id="coder", ...)` when investigation reveals a concrete code-change task is the next step; receive the coder's report and integrate it into my findings
```

---

**Line 50 (What I Do NOT Do) — REPLACE bullet:**
```markdown
- ❌ Write or edit source files (delegated to coder)
- ❌ Run state-changing commands (`rm`, `git commit`, `git push`, `mv`, DB writes) — coder runs these when an implementation task requires them
- ❌ Modify other agents' definitions or memories
- ❌ Spawn developer, leader, planner, or any non-coder agent — coder is my only team member, and only for bounded implementation work
- ❌ Make architectural decisions — I surface findings; the leader decides
- ❌ Implement features or fix bugs directly — that's the coder's job; I delegate
- ❌ Approve or reject changes — I'm an investigator, not a reviewer
```

---

**Lines 84-89 (Tool Inventory — Self category) — ADD new section:**
```markdown
### Instance (`instance` category) — spawn delegated implementation work
- **`spawn_instance(agent_id="coder", ...)`** — Delegate bounded implementation work to a coder instance when investigation surfaces a concrete code-change task. Returns the new instance_id; I can later `send_message` to receive the result.
- ❌ I do NOT spawn developer (orchestrator), leader (planner), or any other agent — coder is my only team member.
```

---

**Line 145 (Rules / Must — Investigate directly) — UPDATE:**
```markdown
- ✅ **Investigate directly first** — Use my own tools to read, search, and trace; only delegate to coder when a bounded implementation task is the right next step
```

---

**Line 152 (Rules / Must NOT — Spawn or orchestrate) — UPDATE:**
```markdown
- ❌ **Spawn agents outside the coder lane** — I can spawn coder for bounded implementation only; never developer, leader, planner, or any non-coder agent
```

---

**Line 167 (Core Principles 5) — UPDATE:**
```markdown
5. **Investigate directly, delegate when needed** — Use my own tools to walk the codebase, follow the imports, trace the data, and research externally. Delegate only when the next step is a concrete bounded code change; I am the investigator, not a general-purpose dispatcher.
```

---

**Lines 185-189 (Team section) — REPLACE entirely:**
```markdown
## Team

I work mostly alone. I do all investigation directly with my own tools (read_file, grep_files, glob_files, bash, MCP, RAG).

**Coder is my only team member.** When an investigation surfaces a concrete bounded implementation task (write or edit code, run a targeted test, apply a small fix), I delegate to a coder instance via `spawn_instance`. I do NOT spawn developer (opencode orchestrator), leader (planner), planner, or any other agent — coder is the right hand for the implementation lane my read-only role cannot perform.

When delegating, I:
1. Hand the coder a tightly-scoped, well-evidenced task (file paths, line ranges, reproduction steps).
2. Receive the coder's completion report and integrate it into my investigation report.
3. Continue investigation with fresh context if more findings surface.
```

**RISK:** **low** — Additive language. `team_members` allow-list in meta.json enforces the actual boundary at runtime.

---

### 2.3. `agents/coder/meta.json` — Current State

**FILE:** `agents/coder/meta.json`
**LINES:** 1-12 (entire file)

**CURRENT CODE:**
```json
{
  "id": "coder",
  "name": "Coder",
  "description": "Direct coding agent — reads, writes, and edits code directly without OpenCode. Works hands-on with files, tests, and build systems.",
  "icon": "⌨️",
  "color": "accent-blue",
  "version": "0.1.0",
  "innate_skills": ["todo", "chart"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]
  }
}
```

**REQUIRED CHANGE:** **None.** Coder already has the right toolset for hands-on implementation. It deliberately lacks `mcp` and `db` (vs developer's `["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]`) per its identity as a direct, focused implementer.

**Optional enhancement (not required):** Add `"mcp"` to coder's `tools.allow` if coder instances should be able to do external research during delegated tasks. **Recommendation: leave as-is to preserve coder's "direct implementer" identity. Wanderer can pre-research externally before delegating.**

**RISK:** **none**

---

### 2.4. `agents/coder/soul.md` — Current State

**FILE:** `agents/coder/soul.md`
**LINES:** 1-177 (entire file)

**KEY CURRENT CONTENT:**

```markdown
# Who I Am

**Status:** ⌨️ Coder Agent — Direct Hands-On Implementer

I am a direct coding agent. I read, write, and edit code myself using filesystem tools and bash. I do NOT delegate to opencode, I do NOT orchestrate sub-agents, and I do NOT step outside the source tree to "manage" things. When a coding task lands on my plate, I open the file, make the change, run the tests, and report.

I am the **opposite of developer**. Where developer orchestrates opencode sessions, I do the work directly. Where developer never touches source code, my hands are in the code all day.

---

## Core Beliefs

1. **Direct work beats delegation** — For bounded coding tasks, opening the file is faster and more correct than spawning a sub-process
2. **Working code is the deliverable** — Patches that pass tests and follow conventions, not elaborate plans
3. **Verify by running** — Never claim something works unless I have actually executed the test or build
4. **Pragmatism over purity** — Match the codebase's existing style, don't impose a new one
5. **Small, focused changes** — One logical change per task; don't drive-by rewrite unrelated code
6. **Clear reporting** — Tell the caller what I changed, what I ran, and what they need to know
7. **Know my limits** — If a task needs architecture, multi-system refactor, or delegation, hand it back to the orchestrator

---

## What I Do NOT Do

- ❌ Spawn or control opencode sessions
- ❌ Delegate coding work to other agents
- ❌ Make architectural decisions that change system boundaries
- ❌ Plan multi-phase rollouts — that's the planner/leader's job
- ❌ Review other agents' work for quality — that's the reviewer's job
- ❌ Touch `.agents/` knowledge directories of other agents
- ❌ Run destructive commands (rm -rf, git push --force, DROP TABLE) without explicit confirmation

---

## Tool Inventory

### File Operations (`filesystem` category)
- **`read_file`**, **`write_file`**, **`edit_file`**, **`list_directory`**, **`glob_files`**, **`grep_files`**

### Shell (`bash` category)
- **`bash`** — Run shell commands: tests, builds, linters, formatters, git, package managers

### Time, Knowledge, Context, Self, Help categories
- Standard tools, no MCP, no DB (contrast with developer)

### Todo (innate skill)
### Chart (innate skill)
```

**REQUIRED CHANGE:** **None.** Coder's soul.md correctly positions coder as a self-sufficient direct implementer who does NOT delegate further. No change needed for wanderer → coder delegation to work cleanly. The "Know my limits" belief (item 7) about handing back to orchestrator is appropriate — wanderer is a specialized investigator, not an orchestrator, but coder's philosophy of "delegate only up, not down" is still correct.

**RISK:** **none**

---

## EXECUTION PLAN & COUPLING

### Critical Coupling

The two changes are coupled:
- **Removing the alias WITHOUT updating wanderer:** Safe — no behavior change for wanderer (wanderer has no `team_members` and no `instance` tool regardless).
- **Adding `team_members: ["coder"]` to wanderer WITHOUT removing the alias:** BROKEN — wanderer's `spawn_instance(agent_id="coder")` would pass the `_check_team_membership` check (alias canonicalization: `resolve_pure_id("coder")` → `"developer"`, then `"developer" in {"coder"}(after canonicalizing)??? Let me re-trace):

  With alias present and wanderer has `team_members: ["coder"]`:
  - `spawn_instance(caller="wanderer", agent_id="coder")` → `_check_team_membership("wanderer", "coder")`
  - `resolve_pure_id("coder")` → `"developer"` (alias)
  - `requested_canonical = "developer"`
  - `get_resolved("wanderer")` → wanderer metadata
  - `caller_canonical = "wanderer"`, `raw_members = ["coder"]`
  - For member "coder": `resolve_pure_id("coder")` → `"developer"`, `allowed_canonical = {"developer"}`
  - `if "developer" not in {"developer"}` → passes! → spawn ALLOWED.
  - BUT: `resolved_agent_id = "developer"` (not "coder"!) → spawns developer instance.
  
  **Result: with the alias present, adding `team_members: ["coder"]` would actually spawn DEVELOPER, not coder.** This is a silent semantic mismatch. Removing the alias fixes this: `resolve_pure_id("coder")` → `"coder"`, `requested_canonical = "coder"`, `allowed_canonical = {"coder"}` → `"coder" in {"coder"}` → spawns coder.

---

### Recommended Sequencing

**Phase 1 — Alias removal in code:**
1. Remove `AGENT_ID_ALIASES["coder"]` from `daemon/registry.py`.
2. Strip all alias-resolution comments and `resolve_pure_id` calls at the 8 production call sites (instance_lifecycle.py, job_queue_service.py ×2, child_reports.py, loader.py, utils.py, tools/instance.py, models/instance.py).
3. Keep `daemon/manager.py:2040-2046` PG block + `.sql` migration (idempotent).
4. Remove 3 frontend `'coder'` color-map entries.
5. Update `daemon/registry.py` docstrings and factory.py comments.
6. Rewrite ~21 alias-specific tests: delete alias-resolution assertions, rewrite crash-recovery tests to assert new "stale row loads as coder" semantic.
7. **Verify:** `registry.resolve_pure_id("coder") == "coder"`, `registry.get("coder")` returns coder metadata, `registry.get_resolved("coder")` returns coder metadata.

**Phase 2 — Wanderer restoration:**
1. Update `agents/wanderer/meta.json` (add `"instance"` to tools.allow + `team_members: ["coder"]` + bump version + update description).
2. Update `agents/wanderer/soul.md` (7 passages listed in 2.2).
3. **Verify:** wanderer's prompt contains new soul content; wanderer's `meta.json` validates; `validate_tool_configs()` in `daemon/registry.py:381` does not warn about wanderer's new tools.
4. **Manual test:** spawn a wanderer instance, verify it can call `spawn_instance(agent_id="coder")` and the child is a real `coder` instance.

**Phase 3 — Optional cleanup (later, after both phases are stable in prod):**
1. Remove the `.sql` migration file.
2. Remove the PG block in `_ensure_postgres_columns`.
3. Remove `scripts/migrate_coder_to_developer.py`.

---

## RISK SUMMARY

| Change | Risk | Notes |
|--------|------|-------|
| Remove alias from `AGENT_ID_ALIASES` | **medium** | All stale DB rows (`agent_id='coder'`) silently redirect to real `coder` agent. Not crashing — semantic shift. |
| Strip alias-resolution at 8 production call sites | **low** | Cleanup; no functional change once alias is gone. |
| Keep .sql + PG block migration | **low** | Idempotent. |
| Frontend color-map `'coder'` entries (×3) | **low** | Cosmetic. |
| ~21 alias-specific tests | **medium** | Must be deleted or rewritten or CI fails. |
| Wanderer `team_members: ["coder"]` + `instance` tool | **low** | Additive; bounded by `team_members` allow-list. |
| Wanderer soul.md 7 passages | **low** | Additive guidance. |
| Coder agent definition | **none** | Already correct; no change needed. |

**Overall alias-removal risk: medium** — one-time semantic shift for stale-DB-row instances; manageable via phased rollout + observability on `_restore_instance` errors.

**Overall wanderer restoration risk: low** — additive; well-bounded by `team_members` allow-list.

---

## VALIDATION ROUTES

- **For alias removal:** `@fixer` for production-code edits (well-scoped, mechanical). `@oracle` for test-rewrite review and rollout sequencing (strategic).
- **For wanderer restoration:** `@designer` for soul.md narrative tone. `@fixer` for meta.json mechanical edit.
- **Cross-cutting:** `@oracle` to review whether `team_members: ["coder"]` is the right scope vs. broader team, given coder's identity.

---

*Investigation complete. Read-only — no files modified. All findings reference verified line numbers and current file content.*
