# Phase 6: Type Consistency & Final Polish

## Objective
Normalize all remaining type annotation inconsistencies (`Optional[T]` → `T | None`, `Union[A, B]` → `A | B`) across the entire codebase, perform final cleanup, and verify the complete refactoring is clean and consistent.

## Coupling
- **Depends on**: Phases 1–5 (all prior phases must be complete)
- **Coupling type**: loose
- **Shared files with other phases**: Touches files from all prior phases for type annotation fixes only (no structural changes)
- **Shared APIs/interfaces**: None — only annotation changes
- **Why this coupling**: Must be last since it touches files modified by all prior phases

## Pre-flight Validation
```bash
git tag refactor-pre-phase6

# Count all Optional[T] occurrences (baseline)
grep -r "Optional\[" daemon/ --include="*.py" | grep -v __pycache__ | wc -l
# Expected: ~326+

# Count all Union[T] occurrences
grep -r "Union\[" daemon/ --include="*.py" | grep -v __pycache__ | wc -l
# Expected: 1

# Record files with Optional/Union usage
grep -rl "Optional\[" daemon/ --include="*.py" | grep -v __pycache__ | sort > /tmp/optional-files.txt
```

## Rollback Procedure
```bash
git checkout refactor-pre-phase6 -- daemon/
# Re-run tests
```

## Context
- Phases 1–5 completed: All structural changes done
- **Scope is larger than initially estimated**: ~326+ `Optional[T]` occurrences and 1 `Union[A, B]` usage
- `routers/schemas.py` alone has 38 `Optional[T]` occurrences (lines 17–437)
- `Optional[T]` is not broken code — it's a style consistency issue. This phase normalizes to modern `T | None` syntax.

## Known `Optional[T]` Locations by File

| File | Approx. Count | Notes |
|------|---------------|-------|
| `daemon/routers/schemas.py` | ~38 | Largest single file; Pydantic request/response models |
| `daemon/services/job_queue_service.py` | ~18 | Service class methods |
| `daemon/services/job_state_machine.py` | ~7 | State machine |
| `daemon/sources/adapters/scheduler.py` | ~15 | Scheduler adapter |
| `daemon/sources/adapters/telegram.py` | ~5 | Telegram adapter |
| `daemon/sources/registry.py` | ~6 | Source registry |
| `daemon/sources/cleanup.py` | ~1 | Cleanup |
| `daemon/config.py` | ~6 | Configuration |
| `daemon/models/` (split in Phase 2) | ~2 | Already in submodules |
| `daemon/graph.py` | ~2 | LangGraph setup |
| `daemon/tools/bash.py` | ~3 (+ 1 Union) | Bash tool |
| Other files | ~220+ | Various locations |
| **Total** | **~326+** | |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Convert `Optional[T]` → `T \| None` in `routers/schemas.py` | ~38 occurrences (lines 17–437). Remove `from typing import Optional` import if no longer needed. | `daemon/routers/schemas.py` |
| 2 | Convert `Optional[T]` → `T \| None` in `services/job_queue_service.py` | ~18 occurrences. | `daemon/services/job_queue_service.py` |
| 3 | Convert `Optional[T]` → `T \| None` in `services/job_state_machine.py` | ~7 occurrences. | `daemon/services/job_state_machine.py` |
| 4 | Convert `Optional[T]` → `T \| None` in sources adapters | `scheduler.py` (~15), `telegram.py` (~5), `registry.py` (~6), `cleanup.py` (~1). | `daemon/sources/` files |
| 5 | Convert `Optional[T]` → `T \| None` in config | ~6 occurrences. | `daemon/config.py` |
| 6 | Convert `Optional[T]` → `T \| None` in models (Phase 2 submodules) | ~2 occurrences remaining. | `daemon/models/` submodules |
| 7 | Convert `Optional[T]` → `T \| None` in other daemon files | All remaining files. Use `grep -rl "Optional\[" daemon/ --include="*.py"` to find. | Various |
| 8 | Convert `Union[A, B]` → `A \| B` in `tools/bash.py` | 1 occurrence at line 41. Remove `from typing import Union` if no longer needed. | `daemon/tools/bash.py` |
| 9 | Clean up stale `typing` imports | After conversion, remove `from typing import Optional` and `from typing import Union` from all files that no longer use them. Keep other typing imports (`TypeVar`, `Any`, `Callable`, etc.). | All modified files |
| 10 | Verify no stale imports | Run `ruff check --select F401,F811 daemon/` or manual review | All modified files |
| 11 | Verify consistent docstrings | All new modules/classes from Phases 1–5 have docstrings | All new files |
| 12 | Verify `__all__` exports | All new modules have appropriate `__all__` | All new modules |
| 13 | Run full test suite (final verification) | Complete test run confirming all phases are clean | — |
| 14 | Verify line counts | No file exceeds 600 lines (except manager.py facade ≤ 600 including module-level functions) | All modified files |
| 15 | Final import audit | All import paths are clean — no mixed relative/absolute styles | All modified files |

## Key Files
- All files modified in Phases 1–5 (type annotation pass)
- `daemon/routers/schemas.py` — Highest density of `Optional[T]` (38 occurrences)
- `daemon/services/job_queue_service.py` — Second highest (18 occurrences)
- `daemon/sources/adapters/scheduler.py` — Third highest (15 occurrences)

## Constraints
- Only type annotation and import changes — no structural or logic changes
- Must not break any tests
- **Python version compatibility**: The project must support Python 3.10+ for `T | None` syntax. Verify `pyproject.toml` or `setup.py` for minimum Python version before proceeding.
- **If Python 3.9 is supported**: Keep `Optional[T]` and do NOT convert. Add `from __future__ import annotations` instead (which enables the syntax without runtime changes).

## Verification Checklist

```bash
# 1. Full test suite
python -m pytest tests/ -v --tb=short

# 2. Import check — all major modules load
python -c "
from daemon.models import *
from daemon.api import create_app
from daemon.manager import InstanceManager, extract_project_keywords, format_project_context, MessageResult, AsyncMessageResult
from daemon.constants import *
from daemon.utils import parse_utc_datetime, validate_agent_id, find_near_instance
app = create_app()
print(f'App OK, routes: {len(app.routes)}')
print('All imports OK')
"

# 3. Type annotation audit — should return zero
grep -r "Optional\[" daemon/ --include="*.py" | grep -v __pycache__ | wc -l
# Expected: 0
grep -r "Union\[" daemon/ --include="*.py" | grep -v __pycache__ | wc -l
# Expected: 0

# 4. Line count check — no file > 600 lines
find daemon/ -name "*.py" -exec wc -l {} \; | sort -rn | head -20

# 5. Smoke test — app starts
python -c "
from daemon.api import create_app
app = create_app()
# Verify live_hub pattern still works
print(f'App created successfully')
"
```

## Deliverables
- [ ] Zero `Optional[T]` usages remaining in `daemon/` (all converted to `T | None`)
- [ ] Zero `Union[A, B]` usages remaining (all converted to `A | B`)
- [ ] Stale `typing.Optional` and `typing.Union` imports removed
- [ ] All new modules have docstrings and `__all__` exports
- [ ] No file exceeds 600 lines
- [ ] Full test suite passes (identical to baseline from Phase 1)
- [ ] All original functionality preserved
