# Tidier Review Summary
**Needs Work** — 7 issues: 2 high, 3 medium, 2 low

## Scope
Phase 3 — API Router Extraction: monolithic `daemon/api.py` (2095→544 lines) split into 7 domain routers under `daemon/routers/`. Files reviewed:
- `daemon/routers/agents.py` (212 lines), `instances.py` (234), `messages.py` (196), `sources.py` (564), `mappings.py` (195), `schedules.py` (465), `webhooks.py` (164)
- `daemon/api.py` (544 lines, modified)
- `daemon/routers/__init__.py` (27 lines, modified)
- `tests/unit/test_vision.py` (import update)

---

## Findings

### 🔴 High

#### Code Smells: `_validate_instance_mode` duplicated across sources.py and schedules.py (~50 lines each)
- **Problem**: `sources.py:95-144` (50 lines) and `schedules.py:63-108` (46 lines) contain functionally identical `_validate_instance_mode()` functions. Both validate instance mode against `{"new_instance", "reuse_instance"}`, handle one_time forcing, and return the same config dict shape.
- **Impact**: Any future change to instance mode validation must be applied in two places. Already diverged slightly (sources version is 4 lines longer due to slightly different formatting/structure). This is the exact kind of duplication that the router extraction was meant to resolve, not propagate.
- **Fix**: Extract to `daemon/utils.py` (alongside `parse_utc_datetime`, `validate_agent_id` which are already shared) and import from both routers. This file is the natural home for cross-router validation logic.

#### Code Smells: Inconsistent service access patterns — 4 different approaches across 7 routers
- **Problem**: Routers use wildly different patterns to access the `InstanceManager`:

  | Router | Access Pattern | Type hints |
  |--------|---------------|------------|
  | `instances.py` | `request.app.state.manager` via `_get_manager(request)` | `-> Any` |
  | `messages.py` | `request.app.state.manager` with fallback to module-level `_manager` | `-> "InstanceManager"` |
  | `schedules.py` | Same hybrid as messages | `-> "InstanceManager"` |
  | `sources.py` | Module-level `_manager` via `get_manager()` + `_get_manager(request)` wrapper that ignores `request` | `-> Any` |
  | `mappings.py` | Module-level `_manager` via `_get_manager()` (no request param) | `-> InstanceManager` |
  | `webhooks.py` | Module-level `_manager` via `_get_manager(request)` that ignores request | `-> "InstanceManager"` |
  | `agents.py` | No manager needed | — |

  Additionally, `sources.py` has both `get_manager()` (no request) and `_get_manager(request)` (alias that ignores request param), plus separate `get_credential_manager()` / `_get_credential_manager(request)` pair. This is confusing.

- **Impact**: Developers adding a new endpoint must guess which pattern to follow. The fallback patterns in `messages.py` and `schedules.py` add dead code — `_manager` module-level is never used since `api.py` always sets `app.state.manager` first. The `request` parameter in `webhooks.py` and `sources.py`'s `_get_manager` is misleading — it's accepted but never used.
- **Fix**: Standardize to one pattern. Recommended: use `request.app.state.manager` consistently (the `instances.py` pattern), since `api.py` already stores everything on `app.state`. Remove all module-level `_manager` variables, `set_manager()` functions, and the `_setup_router_dependencies` wiring. Remove unused `request` parameters.

---

### 🟡 Medium

#### Code Smells: Inconsistent error response shapes across routers
- **Problem**: Some endpoints return structured `ErrorResponse` objects, others return plain strings. Examples:
  - `instances.py:203`: `detail="Instance not found"` (plain string)
  - `instances.py:146-149`: `detail=ErrorResponse(...).model_dump()` (structured)
  - `messages.py:153`: `detail="Server is shutting down"` (plain string)
  - `messages.py:158`: `detail=f"Instance not found: {instance_id}"` (plain string)
  - `messages.py:48`: `detail="Manager not initialized"` (plain string)
  - `schedules.py:59`: `detail="Manager not initialized"` (plain string)
  - `webhooks.py:37-38`: `detail=ErrorResponse(...).model_dump()` (structured)
  
  Same HTTP status (404, 503) produces different response shapes depending on which line throws it.

- **Impact**: API consumers cannot reliably parse error responses. A 404 from one endpoint returns `{"detail": "string"}` while the same 404 from another endpoint returns `{"detail": {"code": "...", "message": "..."}}`.
- **Fix**: Use `ErrorResponse(...).model_dump()` consistently for all HTTPException `detail` values across all routers. This was already the dominant pattern — the plain string cases appear to be oversights from the extraction.

#### Coding Style: `agents.py` has no `_get_manager` / service access — but `agents.py:72` (`create_agent`) and `agents.py:159` (`delete_agent`) interact with filesystem directly
- **Problem**: `agents.py` uses `BASE_DIR` computed at module level and accesses the filesystem directly. While this is fine functionally (agents don't need InstanceManager), the `BASE_DIR` computation duplicates logic that exists in `api.py:70-74`. If the base path logic ever changes, it must be updated in two places.
- **Impact**: Fragile coupling to path resolution. Minor now but will cause confusion later.
- **Fix**: Extract `BASE_DIR` computation to `daemon/constants.py` or `daemon/utils.py` and import from both files. This is a one-time cleanup.

#### File Size: `sources.py` at 564 lines is above the 500-line ideal threshold
- **Problem**: `sources.py` is 564 lines, exceeding the ≤500 ideal. The `start_source` function (lines 433-520, ~87 lines) and `stop_source` function (lines 523-564, ~42 lines) contain adapter creation logic that adds bulk.
- **Impact**: Approaching maintainability limits. The file handles CRUD, lifecycle actions (start/stop), testing, and scheduler validation.
- **Fix**: Acceptable for now given the domain coherence (all source-related). If it grows further, consider extracting adapter lifecycle (start/stop) to a helper module. No action needed immediately.

---

### 🟢 Low

#### Coding Style: `mappings.py:22` comment says "Create router with /sources prefix" but it's actually the mappings router
- **Problem**: Line 22 says `# Create router with /sources prefix` but the router handles mappings under `/sources/{id}/mappings`.
- **Impact**: Misleading comment — minor confusion when reading the file in isolation.
- **Fix**: Change to `# Create router with mappings endpoints under /sources prefix` or similar.

#### Coding Style: `agents.py` variable shadowing — `agents = []` shadows the module name
- **Problem**: `agents.py:34`: Inside `list_agents()`, the variable `agents = []` shadows the module name. This is a minor readability issue.
- **Impact**: Could confuse readers, and would cause issues if the function ever needed to reference the module (unlikely but possible).
- **Fix**: Rename to `agent_list` or `result` for clarity.

---

## Recommendations

1. **Priority 1**: Extract `_validate_instance_mode` to `daemon/utils.py` and import from both `sources.py` and `schedules.py`. This is a clean, low-risk refactor that removes 46-50 lines of duplication.

2. **Priority 2**: Standardize `_get_manager` to `request.app.state.manager` across all routers. This also allows removing `_setup_router_dependencies()` from `api.py` and all `set_manager()` functions from routers — significantly simplifying the wiring.

3. **Priority 3**: Fix inconsistent error response shapes (plain strings vs ErrorResponse) to ensure API contract consistency.
