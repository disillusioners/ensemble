# Phase 2: Python Daemon Source (Rev. 2)

> **Revision 2**: Fixes W1 (file count corrected from 15 to 20).

## Objective
Update all 20 `daemon/` Python source files to replace `"coder"` string references with `"developer"` in docstrings, JSON schema examples, field descriptions, and any runtime logic.

## Coupling
- **Depends on**: Phase 1 (agent directory must exist at `agents/developer/`)
- **Coupling type**: tight
- **Shared files with other phases**: None directly (daemon source is self-contained)
- **Shared APIs/interfaces**: The Pydantic model examples and Field descriptions produce OpenAPI schema output
- **Why this coupling**: Daemon source examples reference `./agents/coder` paths; must match the renamed directory

## Context
After Phase 1, the agent directory is `agents/developer/` with `id="developer"`. The daemon's Pydantic models have `"coder"` hardcoded in:
- `Field(description=...)` strings
- `json_schema_extra` example dictionaries
- Docstring examples

These are **non-functional** (they're documentation/examples, not runtime logic), but they must be updated for consistency and to avoid confusing API consumers.

## Reference Analysis

There are **64 references** across **20 daemon files** (corrected from 15 — W1). They fall into categories:

### Category A: Field Descriptions (docstring-style)
```python
agent_id: str = Field(..., description="Agent ID (e.g., 'coder')")
```
→ Change to: `"Agent ID (e.g., 'developer')"`

### Category B: JSON Schema Examples
```python
"json_schema_extra": {"examples": [{"agent_id": "coder", ...}]}
```
→ Change all `"coder"` → `"developer"` and `"./agents/coder"` → `"./agents/developer"`

### Category C: Registry Docstrings
```python
# In registry.py resolve_to_id():
#   - "coder" → "coder"
#   - "./agents/coder" → "coder"
```
→ Change examples to use `"developer"`

### Category D: False Positives (DO NOT CHANGE)
- `daemon/loader.py:438`: `tiktoken.get_encoding("cl100k_base")` — "encoder" contains "coder"
- `daemon/manager.py:1276,1281,1341,1358,1361`: "encoder" / "tiktoken" references — DO NOT CHANGE
- `daemon/migrations/data_migrator.py:490`: "encoder" — DO NOT CHANGE

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update Pydantic models | Change all Field descriptions and JSON schema examples from "coder" to "developer" | `daemon/models/instance.py`, `daemon/models/agent.py`, `daemon/models/mapping.py`, `daemon/models/source.py` |
| 2 | Update router schemas | Change API schema examples and descriptions | `daemon/routers/schemas.py`, `daemon/routers/dlq.py` |
| 3 | Update tool definitions | Change tool docstrings and examples | `daemon/tools/instance.py`, `daemon/tools/inner_soul.py`, `daemon/tools/agent_mother.py`, `daemon/tools/job_queue.py` |
| 4 | Update registry docstrings | Change path resolution examples in `resolve_to_id()` docstring | `daemon/registry.py` |
| 5 | Update service docstrings | Change agent_id examples | `daemon/services/child_reports.py`, `daemon/services/notification_broadcaster.py`, `daemon/services/job_queue_service.py`, `daemon/services/instance_lifecycle.py` |
| 6 | Update loader docstrings | Change agent_id examples (EXCLUDE tiktoken/encoder references) | `daemon/loader.py` |
| 7 | Update manager docstrings | Change agent_id examples (EXCLUDE encoder references) | `daemon/manager.py` |
| 8 | Update repository docstrings | Change agent_id examples | `daemon/repositories/instance/repository.py`, `daemon/repositories/job_queue/repository.py`, `daemon/repositories/factory.py` |

## Key Files (Complete List with Line Numbers)

### daemon/models/instance.py
| Line | Current | New |
|------|---------|-----|
| 15 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 28 | `"agent_id": "coder"` (example) | `"agent_id": "developer"` |
| 39 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 55 | `"agent_id": "coder"` (example) | `"agent_id": "developer"` |
| 56 | `"agent_dir": "./agents/coder"` (example) | `"agent_dir": "./agents/developer"` |

### daemon/models/agent.py
| Line | Current | New |
|------|---------|-----|
| 19 | `"id": "coder"` (example) | `"id": "developer"` |
| 25 | `"agent_dir": "./agents/coder"` (example) | `"agent_dir": "./agents/developer"` |
| 42 | `"id": "coder"` (example) | `"id": "developer"` |
| 48 | `"agent_dir": "./agents/coder"` (example) | `"agent_dir": "./agents/developer"` |

### daemon/models/mapping.py
| Line | Current | New |
|------|---------|-----|
| 11 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 18 | `"agent_id": "coder"` (example) | `"agent_id": "developer"` |
| 32 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 45 | `"agent_id": "coder"` (example) | `"agent_id": "developer"` |
| 46 | `"agent_dir": "./agents/coder"` (example) | `"agent_dir": "./agents/developer"` |
| 69 | `"agent_dir": "./agents/coder"` (example) | `"agent_dir": "./agents/developer"` |

### daemon/models/source.py
| Line | Current | New |
|------|---------|-----|
| 54 | `"default_agent": "coder"` (example) | `"default_agent": "developer"` |
| 107 | `"config": {"polling_enabled": True, "default_agent": "coder"}` | `"default_agent": "developer"` |

### daemon/routers/schemas.py
| Line | Current | New |
|------|---------|-----|
| 15 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 42 | `"agent_id": "coder"` | `"agent_id": "developer"` |
| 59 | `description="Agent ID (e.g., 'coder')"` | `description="Agent ID (e.g., 'developer')"` |
| 87 | `"agent_id": "coder"` | `"agent_id": "developer"` |
| 88 | `"agent_dir": "/agents/coder"` | `"agent_dir": "/agents/developer"` |
| 117 | `"agent_dir": "/agents/coder"` | `"agent_dir": "/agents/developer"` |
| 415 | `"creator_agent_id": "coder"` | `"creator_agent_id": "developer"` |
| 513 | `"source_agent": "coder"` | `"source_agent": "developer"` |

### daemon/routers/dlq.py
| Line | Current | New |
|------|---------|-----|
| 79 | `"agent_id": "coder"` | `"agent_id": "developer"` |
| 80 | `"agent_dir": "/agents/coder"` | `"agent_dir": "/agents/developer"` |
| 110 | `"agent_id": "coder"` | `"agent_id": "developer"` |
| 111 | `"agent_dir": "/agents/coder"` | `"agent_dir": "/agents/developer"` |

### daemon/tools/instance.py
| Line | Current | New |
|------|---------|-----|
| 422 | `description="Agent ID (e.g., 'coder', 'leader')"` | `description="Agent ID (e.g., 'developer', 'leader')"` |
| 449 | `agent_id: The agent identifier (e.g., "coder").` | `agent_id: The agent identifier (e.g., "developer").` |
| 463 | `Field(description="Agent ID (e.g., 'coder', 'leader')")` | `Field(description="Agent ID (e.g., 'developer', 'leader')")` |
| 471 | `agent_id: Agent ID to spawn (e.g., 'coder', 'leader').` | `agent_id: Agent ID to spawn (e.g., 'developer', 'leader').` |

### daemon/tools/inner_soul.py
| Line | Current | New |
|------|---------|-----|
| 554 | `agent_id: The agent identifier (e.g., "coder")` | `agent_id: The agent identifier (e.g., "developer")` |

### daemon/tools/agent_mother.py
| Line | Current | New |
|------|---------|-----|
| 340 | `agent_name: The agent identifier (e.g., "coder", "leader", "_mother")` | `agent_name: The agent identifier (e.g., "developer", "leader", "_mother")` |
| 398 | `agent_name: The agent identifier (e.g., "coder", "leader", "_mother")` | `agent_name: The agent identifier (e.g., "developer", "leader", "_mother")` |

### daemon/tools/job_queue.py
| Line | Current | New |
|------|---------|-----|
| 37 | `agent_id: Agent ID to run the job (e.g., "coder", "leader").` | `agent_id: Agent ID to run the job (e.g., "developer", "leader").` |
| 51 | `agent_id="coder"` (example) | `agent_id="developer"` |
| 253 | `Field(description="Agent ID to run the job (e.g., 'coder', 'leader')")` | `Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")` |
| 266 | `Field(description="Agent ID to run the job (e.g., 'coder', 'leader')")` | `Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")` |

### daemon/registry.py
| Line | Current | New |
|------|---------|-----|
| 61 | `description="Unique agent identifier (e.g., 'coder')"` | `description="Unique agent identifier (e.g., 'developer')"` |
| 82 | `"id": "coder"` (example) | `"id": "developer"` |
| 88 | `"path": "/path/to/agents/coder"` (example) | `"path": "/path/to/agents/developer"` |
| 208 | `"coder" → "coder"` (docstring) | `"developer" → "developer"` |
| 209 | `"./agents/coder" → "coder"` (docstring) | `"./agents/developer" → "developer"` |
| 210 | `"agents/coder" → "coder"` (docstring) | `"agents/developer" → "developer"` |
| 211 | `"/absolute/path/to/agents/coder" → "coder"` (docstring) | `"/absolute/path/to/agents/developer" → "developer"` |
| 261 | `# Handle: agents/coder, ./agents/coder, agents/coder/` | `# Handle: agents/developer, ./agents/developer, agents/developer/` |

### daemon/services/*.py
| File | Line | Change |
|------|------|--------|
| `child_reports.py` | 497, 1042 | `(e.g., "coder", "leader")` → `(e.g., "developer", "leader")` |
| `notification_broadcaster.py` | 138 | `(e.g., "coder")` → `(e.g., "developer")` |
| `job_queue_service.py` | 331 | `(e.g., 'coder')` → `(e.g., 'developer')` |
| `instance_lifecycle.py` | 348 | `(e.g., "coder")` → `(e.g., "developer")` |

### daemon/loader.py
| Line | Current | New |
|------|---------|-----|
| 532 | `agent_id: The agent identifier (e.g., "coder").` | `agent_id: The agent identifier (e.g., "developer").` |
| 548 | `agent_id: The agent identifier (e.g., "coder").` | `agent_id: The agent identifier (e.g., "developer").` |
| 561 | `agent_id: The agent identifier (e.g., "coder").` | `agent_id: The agent identifier (e.g., "developer").` |
| 572 | `agent_id: The agent identifier (e.g., "coder").` | `agent_id: The agent identifier (e.g., "developer").` |

> ⚠️ **DO NOT change** lines 438-439: `tiktoken.get_encoding("cl100k_base")` and `encoder.encode(text)` — these are unrelated "encoder" references.

### daemon/manager.py
| Line | Current | New |
|------|---------|-----|
| 2009 | `agent_id: Agent ID (e.g., "coder").` | `agent_id: Agent ID (e.g., "developer").` |
| 2219 | `agent_id: The agent ID (e.g., "coder", "leader").` | `agent_id: The agent ID (e.g., "developer", "leader").` |
| 2381 | `agent_id: The agent ID (e.g., "coder", "leader").` | `agent_id: The agent ID (e.g., "developer", "leader").` |

> ⚠️ **DO NOT change** lines 1276, 1281, 1341, 1358, 1361: `encoder` / `tiktoken` references.

### daemon/repositories/*.py
| File | Line | Change |
|------|------|--------|
| `instance/repository.py` | 105 | `(e.g., 'coder')` → `(e.g., 'developer')` |
| `instance/repository.py` | 162 | `(e.g., 'coder', 'leader')` → `(e.g., 'developer', 'leader')` |
| `job_queue/repository.py` | 85 | `(e.g., 'coder')` → `(e.g., 'developer')` |
| `factory.py` | 234 | `'coder' from './agents/coder'` → `'developer' from './agents/developer'` |

> ⚠️ **DO NOT change** `factory.py` lines 191-312: `_add_agent_id_column` function — this is a generic migration utility that parses agent_dir dynamically. The comment on line 234 is just an example.

## Constraints
- These are ALL documentation/example changes — no runtime logic changes
- Do NOT change `encoder`, `tiktoken`, `cl100k_base` references (false positives)
- Do NOT change `factory.py:_add_agent_id_column` function body (generic utility)
- Preserve all other content in the affected lines

## Deliverables
- [ ] All Field descriptions use `'developer'` instead of `'coder'`
- [ ] All JSON schema examples use `"developer"` and `"./agents/developer"`
- [ ] All docstrings use `"developer"` as the example agent_id
- [ ] `grep -rn "coder" daemon/ --include="*.py" | grep -v encoder | grep -v tiktoken | grep -v cl100k` returns 0 matches
- [ ] OpenAPI docs at `/docs` show "developer" as the example agent_id
