# Todo Feature Testing Lessons

## Date: 2026-07-09
## Feature: Todo Innate Skill (`feature/todo-innate-skill`)

### API Naming Discrepancy
The RAG knowledge base and planning docs described TodoManager methods as
`create_list`, `update_item`, `get_list`, `clear_list` and mentioned `asyncio.Lock`.

The ACTUAL implementation uses:
- Method names: `create()`, `update()`, `get_all()`, `clear()`
- Lock: `threading.Lock` (not `asyncio.Lock`)

This is fine — methods are synchronous so threading.Lock is correct.
However, future planning docs should match actual API names.

### Innate Skills Tests Break on Addition
When a new innate skill is added to agents (e.g., "todo"), the test file
`tests/test_innate_skills_refactoring.py` has hardcoded expectations of which
agents have which innate skills. **Every time a new innate skill is added globally,
these test expectations MUST be updated.**

Quick fix applied (commit `f515a109`): Updated test expectations to include "todo"
in all agent test cases, renamed `test_giter_has_no_innate_skills` →
`test_giter_has_only_todo_innate_skill`.

### Full Test Suite Timing
The full `tests/` suite takes >20 minutes when run without parallelization.
Use directory-batched runs for regression analysis:
```bash
.venv/bin/python -m pytest tests/ --tb=no -q \
  --deselect tests/job_queue/test_job_repository_atomic_transition.py::TestStartJobAtomic::test_concurrent_start_only_one_succeeds
```

### Frontend Build
- Clean build in ~9 seconds
- 3 pre-existing budget warnings (bundle size, scss) — unrelated to todo
- `sse.service.ts` uses single signal for todos (single-chat-at-a-time design)

### terminate_instance Cleanup
Todo cleanup is at `instance_lifecycle.py:834-840` (step 2.6).
Best-effort, idempotent. Pause RETAINS todos for resume; terminate DISCARDS them.
