# Alias Removal Impact Map — `coder → developer` (Full Report)

> **Investigation Date**: 2026-07-10
> **Project**: agents-ensemble
> **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
> **Task**: Read-only code investigation — no files modified.

---

## ⚠️ Path Note

The task description referenced paths that **do not exist** in the current codebase. Actual locations used throughout this report:

| Task Description Path | Actual Path |
|---|---|
| `daemon/instance_lifecycle.py` | `daemon/services/instance_lifecycle.py` |
| `daemon/job_queue_service.py` | `daemon/services/job_queue_service.py` |
| `daemon/models.py` | `daemon/models/instance.py` |
| `daemon/tools/spawn_instance.py` | `daemon/tools/instance.py` |
| `daemon/agent_loader.py` | `daemon/loader.py` |

---

## PART 1: Registry Layer

### 1.1 The `AGENT_ID_ALIASES` Definition

```
FILE: daemon/registry.py
LINE(S): 25-31
CURRENT CODE:
    25:  # Backward-compatibility aliases for renamed agent IDs.
    26:  # Maps old agent_id -> new canonical agent_id. Used by ``resolve_pure_id``
    27:  # and (transitively) by ``resolve_path_to_id`` and ``exists`` so that
    28:  # persisted references to the old ID continue to resolve after a rename.
    29:  AGENT_ID_ALIASES: dict[str, str] = {
    30:      "coder": "developer",
    31:  }
```

**Required Change**: Delete the dict AND the 4-line comment block at lines 25-28.

**Import Dependency**: The constant is also imported directly by `daemon/models/instance.py:23`, so that import site must also be removed (the entire `normalize_agent_id` validator method at lines 19-24 should be deleted).

**Risk**: HIGH — this is the single source of truth. Every alias-aware path fails silently (returns `None`) without it. Removing it without first verifying that the data migration has run on every production DB will cause instance restoration and job enqueue to break for any database that still has `agent_id='coder'` rows.

---

### 1.2 Alias-Aware Functions in the Registry

#### 1.2.1 `resolve_pure_id()` — the core resolver

```
FILE: daemon/registry.py
LINE(S): 267-283
CURRENT CODE:
    267:     def resolve_pure_id(self, agent_id: str) -> str | None:
    268:         """Check if a string is a valid agent ID (with alias support).
    269: 
    270:         Args:
    271:             agent_id: The string to check
    272: 
    273:         Returns:
    274:             The canonical agent_id if valid (resolving aliases), None otherwise
    275:         """
    276:         # Check for alias first (backward compat for renamed agents)
    277:         canonical = AGENT_ID_ALIASES.get(agent_id, agent_id)
    278:         if canonical in self._agents:
    279:             return canonical
    280:         # Also check the original in case alias maps to something not yet loaded
    281:         if agent_id in self._agents:
    282:             return agent_id
    283:         return None
```

**What it does**: Maps `"coder"` → `"developer"` via `AGENT_ID_ALIASES.get(agent_id, agent_id)` (line 277), then checks if `"developer"` is a known agent (line 278). The fallback at lines 281-282 handles the case where the alias maps to something not yet loaded (e.g., a future rename in progress).

**What happens if alias is removed**:
- Line 277 becomes equivalent to `canonical = agent_id` (because `.get(agent_id, agent_id)` returns `agent_id` when the key isn't present in the now-empty dict).
- The function becomes a pure `return agent_id if agent_id in self._agents else None` check.
- The function name and docstring become misleading ("with alias support" no longer applies).
- `resolve_pure_id("coder")` returns `None` instead of `"developer"` — this is the *intended* behavior post-removal, but it cascades to every caller.

**Required Change**: Simplify body to:
```python
def resolve_pure_id(self, agent_id: str) -> str | None:
    """Check if a string is a valid agent ID.

    Args:
        agent_id: The string to check

    Returns:
        The agent_id if valid, None otherwise
    """
    return agent_id if agent_id in self._agents else None
```
Optionally rename to `resolve_id` to drop the "pure" qualifier.

**Risk**: LOW — mechanical change in a single file.

#### 1.2.2 `get_resolved()` — alias-aware metadata getter

```
FILE: daemon/registry.py
LINE(S): 223-240
CURRENT CODE:
    223:     def get_resolved(self, agent_id: str) -> AgentMetadata | None:
    224:         """Get agent metadata, resolving aliases first.
    225: 
    226:         Use this when ``agent_id`` may come from an external source (DB row,
    227:         API param, persisted metadata) that could contain a legacy alias
    228:         such as ``"coder"``. Returns ``None`` if the ID is unknown even
    229:         after alias resolution.
    230: 
    231:         Args:
    232:             agent_id: The agent identifier (may be an alias).
    233: 
    234:         Returns:
    235:             AgentMetadata for the canonical agent if found, else ``None``.
    236:         """
    237:         resolved = self.resolve_pure_id(agent_id)
    238:         if resolved is None:
    239:             return None
    240:         return self._agents.get(resolved)
```

**What it does**: Delegates to `resolve_pure_id` then returns the metadata for the canonical ID. **This is the most heavily called alias-aware function** — 8 call sites across production code.

**What happens if alias is removed**:
- Works unchanged as code. Returns `None` for `"coder"` instead of `developer`'s metadata.
- With alias gone, any call passing `"coder"` from a stale DB row returns `None`, which downstream code turns into `ValueError("Agent not found: ...")` or falls through to a permissive default.

**Required Change**: Update docstring to drop alias mentions. Body could be simplified to:
```python
return self._agents.get(agent_id)
```
(A 3-line function becomes 1 line.)

**Risk**: LOW as a code change (mechanical), but **HIGH as a behavior change** — this is the single function that most DB-row restoration paths depend on. All 8 downstream call sites will change behavior.

#### 1.2.3 `resolve_to_id()` — public path/ID resolver

```
FILE: daemon/registry.py
LINE(S): 242-265
CURRENT CODE:
    242:     def resolve_to_id(self, agent_dir_or_id: str) -> str | None:
    243:         """Resolve agent_dir or agent_id to canonical agent_id.
    244: 
    245:         Handles various path formats:
    246:           - "developer" → "developer"
    247:           - "./agents/developer" → "developer"
    248:           - "agents/developer" → "developer"
    249:           - "/absolute/path/to/agents/developer" → "developer"
    250: 
    251:         Args:
    252:             agent_dir_or_id: Agent ID or path to agent directory
    253: 
    254:         Returns:
    255:             Canonical agent_id if found, None otherwise
    256: 257:         """
    258:         if not agent_dir_or_id:
    259:             return None
    260: 
    261:         # Already just an ID - check if it exists
    262:         if agent_id := self.resolve_pure_id(agent_dir_or_id):
    263:             return agent_id
    264: 
    265:         # Try resolving as path
    266:         return self.resolve_path_to_id(agent_dir_or_id)
```

**What it does**: First tries `resolve_pure_id` (alias-aware) for pure IDs, then falls back to `resolve_path_to_id` for path strings.

**What happens if alias is removed**:
- Works unchanged. `resolve_pure_id` continues to function (just without alias remapping).
- For input `"coder"`: today returns `"developer"`; tomorrow returns `None`.
- For input `"./agents/coder"`: today returns `"developer"`; tomorrow returns `None` (because the directory `agents/coder/` does not exist — only `agents/developer/` does).
- Callers handle `None` via `or agent_id` fallback already.

**Required Change**: No code change required. Optional: update docstring path examples.

**Risk**: NONE — function semantics unchanged for canonical inputs.

#### 1.2.4 `resolve_path_to_id()` — path resolver (3 internal alias-aware call sites)

```
FILE: daemon/registry.py
LINE(S): 285-343
CURRENT CODE (only the 3 alias-aware sites shown):
    285:     def resolve_path_to_id(self, path_str: str) -> str | None:
    286:         """Resolve a path string to an agent ID.
    287: 
    288:         Args:
    289:             path_str: Path string (relative or absolute)
    290: 
    291:         Returns:
    292:             Canonical agent_id if the path points to a valid agent, None otherwise
    293:         """
    ...
    312:         if agent_parts_idx >= 0:
    313:             potential_id = parts[agent_parts_idx]
    314:             resolved = self.resolve_pure_id(potential_id)
    315:             if resolved is not None:
    316:                 return resolved
    317: 
    318:         # Try treating the last part as an agent_id
    319:         if parts[-1]:
    320:             resolved = self.resolve_pure_id(parts[-1])
    321:             if resolved is not None:
    322:                 return resolved
    ...
    336:                 if abs_path.parent == self._agents_dir:
    337:                     resolved = self.resolve_pure_id(abs_path.name)
    338:                     if resolved is not None:
    339:                         return resolved
```

**What it does**: Extracts the agent_id from a path like `./agents/coder`, `agents/coder`, or `/abs/agents/coder`, and asks `resolve_pure_id` to canonicalize. With the alias active, `./agents/coder` resolves to `"developer"`.

**What happens if alias is removed**:
- Works unchanged as code. `resolve_pure_id` still returns `None` or the canonical match.
- **Behavior change**: input `"./agents/coder"`, `"agents/coder"`, or any path ending in `/coder` will return `None` (because the directory `agents/coder/` does not exist — only `agents/developer/` does).
- Security: the path-traversal guard at lines 333-335 is unrelated to the alias.

**Required Change**: No code change required.

**Risk**: LOW — no code change; behavior changes only for path-form inputs with legacy alias names.

#### 1.2.5 `exists()` — alias-aware existence check

```
FILE: daemon/registry.py
LINE(S): 457-466
CURRENT CODE:
    457:     def exists(self, agent_id: str) -> bool:
    458:         """Check if agent exists (with alias support).
    459: 
    460:         Args:
    461:             agent_id: The agent identifier
    462: 
    463:         Returns:
    464:             True if agent exists (directly or via alias), False otherwise
    465:         """
    466:         return self.resolve_pure_id(agent_id) is not None
```

**What it does**: Thin wrapper that delegates to `resolve_pure_id`. Today: `exists("coder")` returns `True`.

**What happens if alias is removed**:
- Works unchanged. Returns `False` for `"coder"`.
- Docstring becomes outdated ("with alias support" / "directly or via alias").

**Required Change**: Update docstring to remove alias mentions. No code change.

**Risk**: LOW

---

### 1.3 `_check_team_membership()` — alias-bypass security gate

```
FILE: daemon/tools/instance.py
LINE(S): 237-313
CURRENT CODE (full function):
    237: def _check_team_membership(caller_agent_id: str, requested_agent_id: str) -> str | None:
    238:     """Verify the caller agent is allowed to spawn the requested agent.
    239: 
    240:     Reads the caller's ``meta.json`` ``team_members`` list and checks that the
    241:     requested agent_id (resolved to its canonical id) is present. Returns
    242:     ``None`` when the spawn is permitted, or an error message describing the
    243:     rejection when it is not.
    244: 
    245:     Both the caller's list entries AND the requested ``agent_id`` are
    246:     canonicalized via :func:`registry.resolve_pure_id` to prevent
    247:     alias-bypass attacks (e.g. ``"coder"`` for ``"developer"``).
    248: 
    249:     Secure default: ``team_members`` missing OR empty → deny everything.
    250: 
    251:     Args:
    252:         caller_agent_id: The agent_id of the instance invoking
    253:             ``spawn_instance`` (the parent instance's agent).
    254:         requested_agent_id: The agent_id the caller wants to spawn.
    255: 
    256:     Returns:
    257:         ``None`` when the spawn is authorized, otherwise a human-readable
    258:         error string suitable for the tool's existing error path.
    259:     """
    260:     # Import here to avoid circular import (registry imports utils indirectly).
    261:     from ..registry import get_registry
    262: 
    263:     registry = get_registry()
    264: 
    265:     # Canonicalize the REQUESTED id first — unknown agent → reject (will be
    266:     # reported as "not allowed" rather than "not found" since this is a
    267:     # permissions check). The downstream lifecycle service still raises a
    268:     # "not found" ValueError for unresolvable ids, which is the right
    269:     # primary signal for callers; the membership check is purely an
    270:     # authorization filter on top.
    271:     requested_canonical = registry.resolve_pure_id(requested_agent_id)
    272:     if requested_canonical is None:
    273:         return (
    274:             f"Agent '{caller_agent_id}' is not allowed to spawn "
    275:             f"'{requested_agent_id}'. Requested agent does not exist. "
    276:             "Allowed team members: []"
    277:         )
    278: 
    279:     # Look up the caller's metadata.
    280:     caller_meta = registry.get_resolved(caller_agent_id)
    281:     if caller_meta is None:
    282:         # Caller agent_id is unknown — this is a wiring/misconfiguration
    283:         # bug, but we fail closed (deny). The downstream lifecycle service
    284:         # will raise a "not found" ValueError for the caller as well.
    285:         return (
    286:             f"Agent '{caller_agent_id}' is not allowed to spawn "
    287:             f"'{requested_canonical}'. Caller agent not found. "
    288:             "Allowed team members: []"
    289:         )
    290: 
    291:     # Resolve caller-canonical id (resolves aliases like 'coder' →
    292:     # 'developer'); the canonical id is what team_members entries should
    293:     # match against.
    294:     caller_canonical = caller_meta.id
    295:     raw_members = caller_meta.team_members or []
    296: 
    297:     # Canonicalize each member so an attacker cannot bypass via aliases
    298:     # (e.g. caller = 'coder' with team_members = ['developer'] still
    299:     # works because 'developer' canonicalizes to itself).
    300:     allowed_canonical: set[str] = set()
    301:     for member in raw_members:
    302:         canonical = registry.resolve_pure_id(member)
    303:         if canonical is not None:
    304:             allowed_canonical.add(canonical)
    305: 
    306:     if requested_canonical not in allowed_canonical:
    307:         allowed_display = sorted(allowed_canonical) if allowed_canonical else []
    308:         return (
    309:             f"Agent '{caller_canonical}' is not allowed to spawn "
    310:             f"'{requested_canonical}'. Allowed team members: {allowed_display}"
    311:         )
    312: 
    313:     return None
```

**What it does**: Authorization check for `spawn_instance`. Verifies the caller agent is allowed to spawn the requested agent by checking the caller's `team_members` list in `meta.json`. Both the requested agent_id AND each entry in `team_members` are canonicalized via `resolve_pure_id` to prevent alias-bypass attacks — a malicious caller cannot bypass `team_members` by passing an alias like `"coder"` for `"developer"`.

**Alias-aware sites within this function**:
- Line 271: `requested_canonical = registry.resolve_pure_id(requested_agent_id)` — canonicalizes the requested agent
- Line 280: `caller_meta = registry.get_resolved(caller_agent_id)` — alias-aware caller lookup
- Line 302: `canonical = registry.resolve_pure_id(member)` — canonicalizes each team member entry

**What happens if alias is removed**:
- Works unchanged as code (calls become no-ops — `resolve_pure_id` returns the input unchanged).
- **Security posture improves**: the alias-bypass attack surface disappears because `"coder"` and `"developer"` become distinct strings.
- The canonicalize-everything loop at lines 301-304 becomes redundant (each `member` maps to itself).
- The function body shortens by ~6 lines.

**Required Changes**:
1. Line 280: Replace `registry.get_resolved(caller_agent_id)` with `registry.get(caller_agent_id)`.
2. Line 302: Replace `registry.resolve_pure_id(member)` with `member` (loop variable is already the canonical form).
3. Remove the alias-bypass comment blocks: lines 245-247, 291-292, 297-299, and the reference at `spawn_instance` docstring line 591.

**Risk**: LOW (function logic preserved; security actually improves; code shrinks).

---

### 1.4 Complete End-to-End Flow: `"coder"` Passed as `agent_id`

Today, every entry point follows one of these traces when given `"coder"`:

#### Path A: `resolve_to_id("coder")`

```
caller passes "coder"
        │
        ▼
registry.resolve_to_id("coder")                  [registry.py:261-263]
  └─ self.resolve_pure_id("coder")              [registry.py:262]
       ├─ canonical = AGENT_ID_ALIASES.get("coder", "coder")
       │  → "developer"
       ├─ if "developer" in self._agents: ✓
       └─ return "developer"
  └─ return "developer"
        │
        ▼
registry.get("developer") → AgentMetadata(id="developer", ...)
```

#### Path B: `get_resolved("coder")`

```
caller passes "coder"
        │
        ▼
registry.get_resolved("coder")                   [registry.py:237-240]
  ├─ self.resolve_pure_id("coder") → "developer"
  └─ return self._agents.get("developer")
        │
        ▼
AgentMetadata(id="developer", path=..., tools=..., team_members=[...])
```

#### Path C: `exists("coder")`

```
caller passes "coder"
        │
        ▼
registry.exists("coder")                         [registry.py:466]
  └─ self.resolve_pure_id("coder") is not None → True
```

#### Path D: `InstanceCreate(agent_id="coder")` (Pydantic validation)

```
POST {"agent_id": "coder"}
        │
        ▼
normalize_agent_id("coder")                      [models/instance.py:21-24]
  └─ AGENT_ID_ALIASES.get("coder", "coder") → "developer"
        │
        ▼
Stored as "developer" (canonical form) → lifecycle service
```

#### Path E: `resolve_to_id("./agents/coder")` (path form)

```
caller passes "./agents/coder"
        │
        ▼
registry.resolve_to_id("./agents/coder")         [registry.py:265]
  ├─ self.resolve_pure_id("./agents/coder") → None (not a pure ID)
  └─ self.resolve_path_to_id("./agents/coder")  [registry.py:285-343]
       ├─ normalized = "agents/coder"
       ├─ parts = ["agents", "coder"]
       ├─ potential_id = "coder" (parts[1] after "agents")
       └─ resolve_pure_id("coder") → "developer" → return "developer"
```

---

**After removal** (assuming data migration has been run), every path simplifies to:

| Function | Today | After Removal |
|---|---|---|
| `resolve_pure_id("coder")` | `"developer"` | `None` |
| `get_resolved("coder")` | `AgentMetadata(id="developer", ...)` | `None` |
| `exists("coder")` | `True` | `False` |
| `normalize_agent_id("coder")` | `"developer"` | `"coder"` |
| `resolve_to_id("coder")` | `"developer"` | `None` |
| `resolve_to_id("./agents/coder")` | `"developer"` | `None` |

**The critical invariant**: today's flow lets a DB row with `agent_id='coder'` continue to work even if the data migration never ran. **After removal, the data migration is mandatory** — any stale row turns into `ValueError("Agent not found: coder")`.

---

## PART 2: Production Code Dependencies (excluding `tests/`)

### 2.1 Direct `AGENT_ID_ALIASES` Usage

```
FILE: daemon/models/instance.py
LINE(S): 19-24
CURRENT CODE:
    19:     @field_validator("agent_id")
    20:     @classmethod
    21:     def normalize_agent_id(cls, v: str) -> str:
    22:         """Normalize agent_id aliases (backward compat for renamed agents)."""
    23:         from daemon.registry import AGENT_ID_ALIASES
    24:         return AGENT_ID_ALIASES.get(v, v)
```

**What it does**: Pydantic field validator on `InstanceCreate.agent_id`. Translates `"coder"` → `"developer"` at the API deserialization boundary. Any HTTP `POST` carrying `{"agent_id": "coder"}` has the value silently rewritten to `"developer"` before reaching the lifecycle service.

**Required Change**: Remove the entire validator — delete lines 19-24 (`@field_validator` decorator, `@classmethod`, method def, docstring, import, return). The `InstanceCreate` model will no longer normalize incoming agent IDs.

**Risk**: HIGH — public API surface. Any external caller that POSTs `{"agent_id": "coder"}` will, post-removal, see the value flow downstream as-is and hit `ValueError("Agent not found: coder")` in the lifecycle service.

---

### 2.2 `resolve_to_id` Callers

```
FILE: daemon/services/instance_lifecycle.py
LINE(S): 491-497
CURRENT CODE:
    491:         # Resolve agent
    492:         registry = get_registry()
    493:         resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id
    494:         metadata = registry.get(resolved_agent_id)
    495:         if metadata is None:
    496:             raise ValueError(f"Agent not found: {resolved_agent_id}")
```

**What it does**: `spawn_instance()` resolves `agent_id` (could be `"coder"` from a stale DB row, or `"developer"` from a fresh API call) before metadata lookup. The `or agent_id` fallback at line 493 means if `resolve_to_id` returns `None`, the original value is used.

**Required Change**: None required (works without alias via the `or` fallback). Optional simplification: `registry.get(agent_id)`.

**Behavior Change**: Stale `"coder"` rows from old DBs will hit the `if metadata is None` branch and raise `ValueError`. This is the intended post-removal behavior but requires data migration first.

**Risk**: MEDIUM — depends on whether every production DB has been migrated.

---

```
FILE: daemon/sources/adapters/scheduler.py
LINE(S): 734-737
CURRENT CODE:
    734:             registry = get_registry()
    735:             resolved_agent_id = registry.resolve_to_id(agent_id)
    736:             if resolved_agent_id:
    737:                 agent_id = resolved_agent_id
```

**What it does**: Scheduler-driven messages resolve agent_id before instance creation in `dispatch_inline()`.

**Required Change**: None required. Optional simplification: `if registry.get(agent_id): pass`.

**Behavior Change**: Scheduler jobs targeting a legacy `"coder"` agent from old DBs would land on the canonical `"developer"` today; tomorrow they'd look up `"coder"` directly and fail to find it.

**Risk**: MEDIUM

---

```
FILE: daemon/sources/mapper.py
LINE(S): 270-279
CURRENT CODE:
    270:         # Resolve agent_id to canonical form
    271:         registry = get_registry()
    272:         resolved_id = registry.resolve_to_id(agent_id)
    273:         effective_agent_id = resolved_id if resolved_id else agent_id
    274:         
    275:         # Get agent_dir from registry
    276:         agent_meta = registry.get(effective_agent_id)
    277:         if agent_meta is None:
    278:             raise ValueError(f"Agent not found: {effective_agent_id}")
```

**What it does**: `get_or_create_instance()` from external sources (telegram, slack adapters, etc.) resolves agent_id before creating an `instance_mappings` row.

**Required Change**: None required (works without alias via `or` fallback).

**Behavior Change**: An inbound message from a user with `agent_id='coder'` set up via an old DB row will now throw `ValueError("Agent not found: coder")` instead of silently remapping.

**Risk**: MEDIUM

---

### 2.3 `get_resolved` and `resolve_pure_id` Direct Callers

```
FILE: daemon/services/instance_lifecycle.py
LINE(S): 1482-1488
CURRENT CODE:
    1482:         # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    1483:         # DB may still contain the old agent_id if migration was partial/skipped.
    1484:         registry = get_registry()
    1485:         agent_meta = registry.get_resolved(meta.agent_id)
    1486:         resolved_agent_id = registry.resolve_pure_id(meta.agent_id) or meta.agent_id
    1487:         if agent_meta is None:
    1488:             raise ValueError(f"Agent not found: {meta.agent_id}")
```

**What it does**: `_restore_instance()` restores LangGraph state from a DB row. The `meta.agent_id` may be `"coder"` if the row predates the data migration. Both lines 1485 and 1486 are alias-aware. The function continues with `resolved_agent_id` passed to `load_and_cache_prompt` (line 1494) and `create_instance_tools` (line 1505).

**Required Change**: Simplify both calls to use `meta.agent_id` directly:
- Line 1485 → `agent_meta = registry.get(meta.agent_id)`
- Line 1486 → `resolved_agent_id = meta.agent_id` (or drop the variable entirely)

**Behavior Change**: Any DB with `agent_id='coder'` rows becomes unrestorable, period.

**Risk**: MEDIUM — comment at line 1483 explicitly states this site exists to handle "partial/skipped" migrations, confirming it's a critical fall-through path.

---

```
FILE: daemon/services/job_queue_service.py
LINE(S): 576-584
CURRENT CODE:
    576:             # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    577:             # since agent_id may come from a DB row that still has the old value.
    578:             registry = get_registry()
    579:             agent_meta = registry.get_resolved(agent_id)
    580:             resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
    581:             if agent_meta is None:
    582:                 raise ValueError(f"Agent not found: {agent_id}")
    583:             agent_dir = str(agent_meta.path)
    584:             agent_id = resolved_agent_id
```

**What it does**: `enqueue_job()` idempotency-key path. Resolves agent before storing the JobItem row.

**Required Change**: Simplify to `registry.get(agent_id)` + `agent_id` (no rebind needed).

**Risk**: MEDIUM — duplicate-pattern site for the idempotency-key path.

---

```
FILE: daemon/services/job_queue_service.py
LINE(S): 692-700
CURRENT CODE:
    692:         # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    693:         # since agent_id may come from a DB row that still has the old value.
    694:         registry = get_registry()
    695:         agent_meta = registry.get_resolved(agent_id)
    696:         resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
    697:             if agent_meta is None:
    698:                 raise ValueError(f"Agent not found: {agent_id}")
    699:             agent_dir = str(agent_meta.path)
    700:             agent_id = resolved_agent_id
```

**What it does**: Non-idempotency-key path of `enqueue_job()`. Identical pattern to the idempotency path.

**Required Change**: Same as above.

**Risk**: MEDIUM

---

```
FILE: daemon/services/instance_messaging.py
LINE(S): 1266-1281
CURRENT CODE:
    1266:                 agent_id_for_job = ctx.instance_agent_id or "default"
    1267:                 registry = get_registry()
    1268:                 agent_meta = registry.get_resolved(agent_id_for_job)
    1269:                 if agent_meta is None:
    1270:                     # Fall back to the instance's stored agent_id if
    1271:                     # registry lookup fails (e.g. unregistered agent).
    1272:                     # The JobItem still gets a usable row; downstream
    1273:                     # consumers can re-resolve.
    1274:                     agent_dir_value = ""
    1275:                     resolved_agent_id = agent_id_for_job
    1276:                 else:
    1277:                     agent_dir_value = str(agent_meta.path)
    1278:                     resolved_agent_id = (
    1279:                         registry.resolve_pure_id(agent_id_for_job)
    1280:                         or agent_id_for_job
    1281:                     )
```

**What it does**: `enqueue_message_job()` mirrors the agent_id resolution pattern from `JobQueueService.enqueue_job`. The fallback at line 1274 handles "agent not found" by leaving `agent_dir_value = ""`. The JobItem still gets a row; downstream consumers can re-resolve.

**Required Change**: None required (the fallback already exists). Could simplify the `else` branch to `registry.get`.

**Behavior Change**: Alias-removal simply means the fallback fires when `agent_id_for_job == "coder"` — a stale instance would have `agent_dir = ""` in its JobItem mirror.

**Risk**: LOW (graceful fallback already in place)

---

```
FILE: daemon/services/child_reports.py
LINE(S): 386-398
CURRENT CODE:
    386:         # Get agent display name from meta.json
    387:         # Resolve alias first (backward compat for renamed agents like 'coder'→'developer')
    388:         # so fallback shows "Developer" instead of "Coder" if registry lookup misses.
    389:         registry = get_registry()
    390:         resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
    391:         agent_name = resolved_agent_id.capitalize()
    392: 
    393:         try:
    394:             metadata = registry.get_resolved(agent_id)
    395:             if metadata and metadata.name:
    396:                 agent_name = metadata.name
    397:         except Exception:
    398:             pass
```

**What it does**: Builds the human-readable display prefix (e.g. `"Developer agent (id=xxx) has done"`) for child completion reports. Alias-aware so a stale DB row's report shows `"Developer"` instead of `"Coder"`.

**Required Change**: None required for behavior. Update comments.

**Behavior Change**: Stale rows produce reports with prefix `"Coder agent (...)"` instead of `"Developer agent (...)"`.

**Risk**: LOW (cosmetic / UX regression for stale instances only)

---

```
FILE: daemon/loader.py
LINE(S): 101-114
CURRENT CODE:
    101:     # Get agent's tool filter from registry
    102:     # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    103:     # so tool filtering uses the correct agent's filter instead of skipping.
    104:     tool_filter: ToolFilter | None = None
    105:     agent_innate_skills: list[str] | None = None
    106:     try:
    107:         registry = get_registry()
    108:         agent_meta = registry.get_resolved(agent_id)
    109:         if agent_meta is not None:
    110:             tool_filter = agent_meta.tools
    111:             agent_innate_skills = agent_meta.innate_skills
    112:             # Use resolved id for downstream tool filtering context
    113:             resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
    114:             agent_id = resolved_agent_id
```

**What it does**: `load_tools_doc_for_agent()` looks up tool filter + innate skills from registry metadata when building the system prompt.

**Required Change**: Simplify to `registry.get(agent_id)` + use `agent_id` directly. Update comment.

**Behavior Change**: Without alias, `get_resolved("coder")` returns `None` → tool filter is silently skipped → agent gets all tools (over-permissive for legacy agents).

**Risk**: LOW (only relevant for legacy `"coder"` instances, which should not exist after migration)

---

```
FILE: daemon/utils.py
LINE(S): 423-456 (function `validate_agent_id`)
CURRENT CODE (key sites):
    423: def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    424:     """Validate agent_id exists and return agent_id with path.
    425: 
    426:     This is the preferred function for validating agent references.
    427:     Resolves agent_id aliases (e.g., ``"coder"`` -> ``"developer"``) via
    428:     the registry before lookup so persisted/legacy references continue
    429:     to resolve after a rename. Returns the canonical agent_id from
    430:     the registry metadata, not the raw input.
    431: 
    432:     Args:
    433:         agent_id: The agent identifier to validate.
    434: 
    435:     Returns:
    436:         Tuple of (canonical_agent_id, resolved_absolute_path).
    437: 
    438:     Raises:
    439:         HTTPException: If agent is invalid or not found.
    440:     """
    441:     registry = get_registry()
    442: 
    443:     # Resolve alias (e.g., "coder" -> "developer") before dict lookup.
    444:     # registry.get() does NOT resolve aliases, so this step is required
    445:     # to support backward-compatible references to renamed agents.
    446:     resolved_agent_id = registry.resolve_pure_id(agent_id)
    447:     if resolved_agent_id is None:
    448:         raise HTTPException(
    449:             status_code=404,
    450:             detail=ErrorResponse(
    451:                 code=ErrorCodes.INVALID_REQUEST,
    452:                 message=f"Agent not found: {agent_id}"
    453:             ).model_dump()
    454:         )
    455: 
    456:     metadata = registry.get(resolved_agent_id)
```

**What it does**: Public API helper — validates `agent_id` and returns `(canonical_id, path)`. **Transitively called by HTTP routers** (see 2.4).

**Required Change**: Simplify to:
```python
def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    registry = get_registry()
    metadata = registry.get(agent_id)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    return agent_id, metadata.path
```
Update docstring to drop alias mentions.

**Risk**: MEDIUM — public API surface. After migration: callers passing `"developer"` are unaffected; callers passing `"coder"` get HTTP 404.

---

### 2.4 Tool-File Callers

```
FILE: daemon/tools/instance.py
LINE(S): 1077-1081 (function `_apply_tool_filter`)
CURRENT CODE:
    1077:     from ..registry import get_registry
    1078: 
    1079:     # Get agent metadata
    1080:     registry = get_registry()
    1081:     agent_meta = registry.get_resolved(agent_id)
```

**What it does**: Looks up the agent's `tools` filter configuration when creating per-instance tool lists. Alias-aware so legacy `"coder"` agent_id strings still find the canonical filter.

**Required Change**: Replace `get_resolved(agent_id)` with `get(agent_id)`.

**Behavior Change**: Legacy `"coder"` agents lose their tool filter and fall through to the default "all tools allowed" path.

**Risk**: LOW

---

```
FILE: daemon/tools/help.py
LINE(S): 47-54
CURRENT CODE:
    47:     from ..registry import get_registry
    48:     from .instance import resolve_tool_filter, expand_allow_for_innate_skills
    49: 
    50:     registry = get_registry()
    51:     agent_meta = registry.get_resolved(agent_id)
    52: 
    53:     if agent_meta is None or agent_meta.tools is None:
    54:         return None
```

**What it does**: Resolves the agent's tools filter for the `tool_help()` display — same pattern as `_apply_tool_filter`.

**Required Change**: Replace `get_resolved(agent_id)` with `get(agent_id)`.

**Risk**: LOW

---

```
FILE: daemon/tools/access_memory.py
LINE(S): 34-38
CURRENT CODE:
    34:     from ..registry import get_registry
    35: 
    36:     registry = get_registry()
    37:     agent_meta = registry.get_resolved(agent_id)
    38:     agent_path = agent_meta.path if agent_meta else Path(agent_id)
```

**What it does**: `access_memory` tool resolves agent_id → path to the agent's `memories/` directory. If lookup fails, falls back to `Path(agent_id)` (which won't exist on disk for a stale `"coder"` instance).

**Required Change**: Replace `get_resolved(agent_id)` with `get(agent_id)`.

**Behavior Change**: Stale `"coder"` instances calling `access_memory` would return an "agent path does not exist" error.

**Risk**: LOW (only relevant to legacy instances that should not exist after migration)

---

```
FILE: daemon/tools/inner_soul.py
LINE(S): 560-571
CURRENT CODE:
    560:     # Resolve agent_id to path for internal use
    561:     from ..registry import get_registry
    562:     registry = get_registry()
    563:     agent_meta = registry.get_resolved(agent_id)
    564:     agent_path = agent_meta.path if agent_meta else Path(agent_id)
    565:     ...
    567:     if agent_path:
    568:         growth_rules = _load_growth_rules(agent_path)
```

**What it does**: Same pattern as `access_memory` — resolves agent_id → agent directory, then loads growth rules for memory archival.

**Required Change**: Replace `get_resolved(agent_id)` with `get(agent_id)`.

**Risk**: LOW (same as access_memory)

---

### 2.5 Transitive Consumers of `validate_agent_id`

```
FILE: daemon/api.py
LINE(S): 79-80
CURRENT CODE:
    79: # Re-export validate_agent_id from utils for backward compatibility
    80: from daemon.utils import validate_agent_id as validate_agent_id  # noqa: F401
```

**What it does**: Re-exports `validate_agent_id` from utils for the legacy `daemon.api` namespace.

**Required Change**: No code change (the re-export is fine; underlying function changes are in utils).

**Risk**: NONE

---

```
FILE: daemon/routers/mappings.py
LINE(S): 17, 74
CURRENT CODE:
    17: from daemon.utils import parse_utc_datetime, validate_agent_id
    ...
    74:     resolved_agent_id, agent_path = validate_agent_id(mapping_create.agent_id)
```

**What it does**: The `POST /mappings` endpoint uses `validate_agent_id` to validate the `agent_id` field in the request body before creating a new `instance_mapping` row.

**Required Change**: No code change required.

**Behavior Change**: Requests with `agent_id='coder'` will return HTTP 404 instead of being silently remapped to `developer`.

**Risk**: MEDIUM — public HTTP API surface.

---

```
FILE: daemon/routers/jobs_crud.py
LINE(S): 20, 260
CURRENT CODE:
    20: from daemon.utils import create_service_dependency, validate_agent_id
    ...
    260:         resolved_agent_id, agent_path = validate_agent_id(body.agent_id)
```

**What it does**: The `POST /jobs` endpoint uses `validate_agent_id` on the request body.

**Required Change**: No code change required.

**Behavior Change**: Same as `routers/mappings.py`.

**Risk**: MEDIUM — public HTTP API surface.

---

### 2.6 Migration / Historical References (no active alias logic — these MUST REMAIN)

```
FILE: daemon/manager.py
LINE(S): 2020-2046 (inside `_ensure_postgres_columns`)
CURRENT CODE (excerpt):
    2029:             # NOTE: coder→developer migration is also handled in:
    2030:             #   - daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql (SQLite production)
    2031:             #   - scripts/migrate_coder_to_developer.py (standalone manual tool)
    2032:             # ── Agent rename: coder → developer ──────────────────────────────
    2033:             # Idempotent UPDATE: renames agent_id and agent_dir from the old
    2034:             # 'coder' agent to 'developer'. Safe to re-run (WHERE clause is a
    2035:             # no-op if no rows match). The .sql migration runner is a NO-OP
    2036:             # on PostgreSQL, so data migrations of this kind must live here to
    2037:             # take effect on existing production databases. Fresh databases
    2038:             # never see 'coder' values because the new model definitions
    2039:             # already reference 'developer'.
    2040:             "UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
    2041:             "UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
    2042:             "UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
    2043:             "UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
    2044:             "UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder'",
    2045:             # Legacy table (may not exist on fresh DBs — wrapped in exception handler)
    2046:             "DO $$ BEGIN UPDATE jobqueue SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'; EXCEPTION WHEN undefined_table THEN NULL; END $$",
```

**What it does**: PostgreSQL data migration that physically renames `agent_id='coder'` rows to `'developer'` across 5 live tables (`instances`, `instance_mappings`, `job_queue_items`, `dead_letter_items`, `projects`) plus the legacy `jobqueue` table (defensively wrapped in exception handler).

**Required Change**: **MUST NOT BE REMOVED** until well after the alias is gone. This is the runtime migration that ensures the data layer is clean. The UPDATEs are idempotent — no-op if no rows match. Once all production PostgreSQL databases have run through these UPDATEs, removing the alias becomes safe.

**Risk**: HIGH if removed prematurely (data layer breaks for any PG DB that still has stale rows)

---

```
FILE: daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql
LINE(S): 1-132 (entire file is a single SQL migration)
CURRENT CODE: Idempotent UPDATEs covering the same 5 tables as `manager.py`, with a DOWN block that reverses the rename. Idempotent via `WHERE agent_id = 'coder'` clauses on each UPDATE. Records version in `schema_migrations` after first apply, so subsequent startups skip the file. Comments explain the dual-driver design (SQLite uses this file; PostgreSQL uses `manager.py`).

Key content:
    -- UP
    UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
    UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
    UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
    UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder';
    UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder';

    -- DOWN (idempotent, but limited by agent_dir pattern matching)
    UPDATE instances SET agent_id = 'coder', agent_dir = REPLACE(agent_dir, '/agents/developer', '/agents/coder') WHERE agent_id = 'developer' AND agent_dir LIKE '%/agents/developer%';
    ...(same pattern for 4 other tables)
```

**Required Change**: **MUST REMAIN** for at least one release cycle after the alias is removed. Handles the (unlikely) case of a brand-new SQLite DB spun up between the alias-removal PR and the production migration completing.

**Risk**: HIGH if removed prematurely

---

```
FILE: scripts/migrate_coder_to_developer.py
LINE(S): 1-108+ (entire file is a standalone migration script)
CURRENT CODE: Standalone Python script for manual migration. Auto-detects SQLite vs PostgreSQL from the connection URL. Handles the legacy `jobqueue` table defensively (try/except). Backs up DB before mutation. Provides `SELECT COUNT(*) WHERE agent_id='coder'` pre-flight checks.
```

**Required Change**: Keep for at least one release cycle. Operators may need it for manual one-off migrations, particularly on PostgreSQL where the MigrationRunner only handles SQLite.

**Risk**: LOW (operational tool, not auto-invoked)

---

```
FILE: daemon/repositories/factory.py
LINE(S): 316-323
CURRENT CODE:
    316:         # NOTE: The agent_id rename 'coder' → 'developer' was previously handled
    317:         # here as a Python UPDATE block. This function is no longer called in
    318:         # production (factory creation paths rely on the SQLModel metadata +
    319:         # MigrationRunner pipeline). Production SQLite migrations are now
    320:         # applied via:
    321:         #   daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql
    322:         # and the corresponding PostgreSQL updates live in
    323:         # daemon/repositories/factory.py:run_migrations().
```

**What it does**: A multi-line comment block (NOT active code) describing where the migration logic moved. The actual Python UPDATEs are gone; only the documentation remains.

**Required Change**: No code change. (Optional: update doc if alias is fully removed.)

**Risk**: NONE

---

### 2.7 Inline Comments Referencing the Alias (non-executable)

| File | Line | Comment Text | Action |
|---|---|---|---|
| `daemon/registry.py` | 91-92 | AgentMetadata.team_members docstring: *"Aliases (e.g. 'coder') are resolved to canonical ids (e.g. 'developer') via the registry."* | Update |
| `daemon/registry.py` | 228 | `get_resolved` docstring mentioning `"coder"` | Update |
| `daemon/loader.py` | 102 | `# Resolve alias (backward compat...)` | Remove |
| `daemon/services/instance_lifecycle.py` | 1482 | `# Resolve alias (backward compat...)` | Remove |
| `daemon/services/job_queue_service.py` | 576, 692 | `# Resolve alias (backward compat...)` | Remove |
| `daemon/services/child_reports.py` | 387 | `# Resolve alias first (backward compat...)` | Remove |
| `daemon/tools/instance.py` | 247, 291-292, 297-299, 591 | Alias-bypass security comments | Update/Remove |

**Risk**: LOW (cosmetic / docstring hygiene)

---

## Summary Table: All Touched Production Files

| # | File | Line(s) | Pattern | Risk | Required Action |
|---|---|---|---|---|---|
| 1 | `daemon/registry.py` | 25-31, 91-92, 223-240, 267-283, 285-343, 457-466 | `AGENT_ID_ALIASES` definition + 5 alias-aware methods | **HIGH** | Delete dict; simplify 5 methods; update docs |
| 2 | `daemon/models/instance.py` | 19-24 | Direct `AGENT_ID_ALIASES` import + validator | **HIGH** | Delete `normalize_agent_id` validator entirely |
| 3 | `daemon/services/instance_lifecycle.py` | 493, 1485-1486 | `resolve_to_id`, `get_resolved`, `resolve_pure_id` | **MEDIUM** | Simplify `_restore_instance` (lines 1485-1486); `spawn_instance` (line 493) OK as-is |
| 4 | `daemon/services/job_queue_service.py` | 579-580, 695-696 | `get_resolved`, `resolve_pure_id` (×2 sites) | **MEDIUM** | Simplify both `enqueue()` paths |
| 5 | `daemon/services/instance_messaging.py` | 1268, 1279 | `get_resolved`, `resolve_pure_id` | **LOW** | None required (fallback already exists) |
| 6 | `daemon/services/child_reports.py` | 390, 394 | `resolve_pure_id`, `get_resolved` | **LOW** | Update comments only |
| 7 | `daemon/sources/adapters/scheduler.py` | 735 | `resolve_to_id` | **MEDIUM** | None required (alias-aware via `or` fallback) |
| 8 | `daemon/sources/mapper.py` | 272 | `resolve_to_id` | **MEDIUM** | None required (alias-aware via `or` fallback) |
| 9 | `daemon/loader.py` | 108, 113 | `get_resolved`, `resolve_pure_id` | **LOW** | Simplify to `registry.get` + update comment |
| 10 | `daemon/utils.py` | 446 | `resolve_pure_id` (`validate_agent_id`) | **MEDIUM** | Simplify function body to `registry.get` |
| 11 | `daemon/tools/instance.py` | 271, 280, 302, 1081 | `resolve_pure_id` (×2), `get_resolved` (×2) | **LOW** | Simplify `_check_team_membership` + `_apply_tool_filter` |
| 12 | `daemon/tools/help.py` | 51 | `get_resolved` | **LOW** | Swap to `registry.get` |
| 13 | `daemon/tools/access_memory.py` | 37 | `get_resolved` | **LOW** | Swap to `registry.get` |
| 14 | `daemon/tools/inner_soul.py` | 563 | `get_resolved` | **LOW** | Swap to `registry.get` |
| 15 | `daemon/api.py` | 80 | `validate_agent_id` re-export | **NONE** | No code change |
| 16 | `daemon/routers/mappings.py` | 17, 74 | `validate_agent_id` call | **MEDIUM** | None required (HTTP 404 for `"coder"` post-removal) |
| 17 | `daemon/routers/jobs_crud.py` | 20, 260 | `validate_agent_id` call | **MEDIUM** | None required (same) |
| 18 | `daemon/manager.py` | 2020-2046 | PG migration UPDATEs | **HIGH if removed** | **MUST REMAIN** until DBs are verified clean |
| 19 | `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql` | 1-132 | SQLite migration | **HIGH if removed** | **MUST REMAIN** for 1+ release cycle |
| 20 | `scripts/migrate_coder_to_developer.py` | 1-108+ | Standalone migration tool | **LOW** | Keep for ops escape hatch |
| 21 | `daemon/repositories/factory.py` | 316-323 | Comment only | **NONE** | No code change |

**Total unique production files: 21**
**Files requiring code changes: 10 (rows 1-10, 12-14)**
**Files requiring behavior verification before removal: 7 (rows 3-4, 7-8, 10, 16-17)**
**Files that must NOT be removed: 3 (rows 18-20)**
**Files requiring no changes: 4 (rows 5-6, 15, 21)**

---

## Recommended Removal Order

### Phase A — Pre-Removal Verification
1. Run `scripts/migrate_coder_to_developer.py` in dry-run/pre-flight mode against every production DB (PG + SQLite). Confirm zero rows match `WHERE agent_id='coder'`.
2. Confirm `daemon/manager.py:_ensure_postgres_columns` has run on all PostgreSQL databases on next process restart.

### Phase B — Code Changes (single PR)
1. Delete `AGENT_ID_ALIASES` from `daemon/registry.py` (lines 25-31).
2. Delete `normalize_agent_id` validator from `daemon/models/instance.py` (lines 19-24).
3. Simplify `resolve_pure_id` body in `daemon/registry.py` to plain dict lookup.
4. Update docstrings in `daemon/registry.py` (`get_resolved`, `exists`, `AgentMetadata.team_members`).
5. Simplify the `get_resolved` → `get` swap across all 8 tool-file call sites.
6. Simplify `_check_team_membership` to drop the alias-bypass loops (lines 280, 302).
7. Update outdated comments across all affected files.
8. Update `tests/test_registry.py` (delete alias unit tests), `tests/conftest.py` (update fixtures), and other test files with `"coder"` agent_id references.

### Phase C — Post-Removal Safety Net (1+ release cycle)
- Leave `daemon/manager.py` PG UPDATEs in place (idempotent, harmless).
- Leave `daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql` in place.
- Leave `scripts/migrate_coder_to_developer.py` as operator escape hatch.

### Phase D — Long-Term Cleanup
- After confirming no production DBs contain `agent_id='coder'` for ≥1 release cycle, optionally remove the migration files.

---

## Test-File Impact (reference only — out of scope for production impact map)

The `tests/` directory contains ~30+ mock-return-value sites and dedicated alias unit tests. Major hits:

- `tests/test_registry.py:645-688` — `TestAgentIdAliasBackwardCompatibility` class (DELETE)
- `tests/unit/test_coder_agent.py` — test data for `agents/coder/` directory (test data, not alias logic; may still be relevant if the directory is renamed)
- `tests/unit/test_coder_developer_migration.py` — data-migration regression tests (KEEP as historical record)
- `tests/test_spawn_instance_validation.py:15-20` — alias resolution in spawn validation tests
- `tests/conftest.py:388, 396` — fixtures using `"coder"` as `agent_id` (must migrate to `"developer"`)
- `tests/test_spawn_instance_instructive_errors.py:307-540` — mocks `resolve_pure_id`, `resolve_to_id` return values
- `tests/test_loader.py:841-844` — mocks `get_resolved` / `resolve_pure_id`
- `tests/test_message_job_bridge.py:333, 436, 530, 719, 788, 840` — `mock_registry.get_resolved.return_value = None`
- `tests/test_sources_system_fix.py:107, 279, 330, 379, 428, 486, 731, 776, 823, 882` — `mock_agent_registry.resolve_to_id` mocks
- `tests/integration/test_inner_soul.py:109`, `tests/integration/test_inner_soul_standalone.py:225, 334`, `tests/integration/conftest.py:158` — `mock_registry.resolve_to_id.return_value = "test_agent"`
- `tests/services/test_instance_lifecycle_h10_l14.py:845` — mock `resolve_to_id`
- `tests/unit/test_gaia_agent.py:147, 169, 188, 203, 362` — `registry.exists("gaia")` and `registry.get("gaia")` (unrelated to coder alias, but registry-coupled)
