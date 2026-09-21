# Lesson: py3.13-vs-PEP649 annotation rot — concrete root cause chain

Date: 2026-09-20
Status: PRE-EXISTING (identical at base 997c670d and every branch), needs separate sweep ticket

## Confirmed failing site
`daemon/tools/inner_soul.py:824`:
```python
def _update_memories(..., manager: "InstanceManager" | None = None) -> dict:
```
Python 3.13 (PEP 649 direction) evaluates the annotation at runtime; `"InstanceManager" | None` is `str | None` → `TypeError: unsupported operand type(s) for |: 'str' and 'NoneType'`. File lacks `from __future__ import annotations`.

## Blast radius (all pre-existing)
- **dev.sh boot** (ensure.md gate): api.py lifespan → manager.py:52 → tools/__init__.py:6 → instance.py:30 → inner_soul.py:824. App crashes in lifespan startup; uvicorn --reload reloader keeps the wrapper process alive so literal exit-124 looks like "ran fine" — check for a LISTENING port (8079), not the exit code.
- **Collection errors** (~20 modules repo-wide; 7 in tests/job_queue): NameError InstanceManager / Optional, or the same `str | None` TypeError. Same class of issue.
- **One functional-looking failure**: `test_soft_delete.py::test_delete_terminal_job_soft_deletes` → POST /jobs → `validate_agent_id` → `get_registry()` → `validate_tool_configs()` → lazy import chain → same TypeError swallowed by FastAPI as 400 "Invalid agent". First-API-call tests in a class fail, later ones pass (partial registry state) — classic ordering dependence.

## Fix recipe (for the sweep ticket)
Add `from __future__ import annotations` to affected modules (start: daemon/tools/inner_soul.py + the 7 collection-error test imports' targets). Sweep repo-wide for string forward-ref PEP-604 unions. After the sweep, re-run: dev.sh 30s gate + tests/job_queue collection.

## Tester impact
- Any FAIL verdict on this repo must be attributed vs base (worktree provenance-guarded) before counting.
- ensure.md gate is BLOCKED-unred on every branch until the sweep lands — treat as environment blocker, not change-caused.
