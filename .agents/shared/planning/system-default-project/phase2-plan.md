# Phase 2: Normalization (Chokepoint + Boundaries)

## Objective

**[B1 FIX — CRITICAL]** Establish `enqueue()` as the single canonical normalization chokepoint that ALL callers — HTTP endpoints, agent tools, retry paths, and internal services — pass through. Additionally, add defense-in-depth normalization at every input boundary. This ensures that `project_id=None` or `""` is converted to the system default project's ID regardless of how a job enters the system, including internal callers like `retry_job()` and `instance_lifecycle.py` that bypass HTTP/tool boundaries.

---

## Coupling

- **Depends on**: Phase 1 (System Project & Queue Bootstrap)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/routers/schemas.py` (also touched by Phase 4), `daemon/routers/jobs_crud.py`, `daemon/services/job_queue_service.py` (also touched by Phase 3), `daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py`
- **Shared APIs/interfaces**: `normalize_project_id()` function — used by `enqueue()` and all input boundaries
- **Why this coupling**: Phase 2's normalization reads `constants.SYSTEM_DEFAULT_PROJECT_ID` which Phase 1 populates at startup. Phase 3's removal of `None`-project paths is only safe because Phase 2 guarantees no `None` reaches the service layer. These must be sequential.

## Context

### Previous Phase Completed

Phase 1 delivered:
- `SYSTEM_DEFAULT_PROJECT_NAME` and `SYSTEM_DEFAULT_PROJECT_ID` constants in `daemon/constants.py`
- `ensure_system_default_project()` in the project repository
- Startup hook that populates `SYSTEM_DEFAULT_PROJECT_ID` before services start

### Current State

- `JobCreateRequest.project_id: str | None` (schemas.py) — accepts `None`, no normalization.
- `jobs_crud.py create_job()` — passes `request.project_id` directly to `service.enqueue()`.
- `job_queue.py job_create` tool — passes `project_id` directly to `job_service.enqueue()`.
- **⚠️ `job_queue_service.py retry_job()` (line 539)** — passes original job's `project_id` directly to `enqueue()` with no normalization. Retrying an orphan creates a new orphan.
- **⚠️ `instance_lifecycle.py:104-106`** — converts `"null"/"none"/""` to `None` instead of the system project ID.
- **⚠️ `tools/instance.py:315-317`** — auto-inherits `project_id` from parent, can propagate `None`.
- `JobQueueService.enqueue()` signature accepts `project_id: str | None` — no normalization inside.
- No centralized normalization helper exists.

### Target State

- **Canonical chokepoint**: `normalize_project_id()` called at the top of `enqueue()` — this is the single point that guarantees normalization for ALL callers (HTTP, tools, retry, internal).
- **Defense-in-depth at boundaries**: Pydantic validator in `JobCreateRequest`, normalization in `jobs_crud.py`, `job_queue.py` tool, `instance_lifecycle.py`, `tools/instance.py`.
- Internal callers like `retry_job()` are covered by the `enqueue()` chokepoint — they don't need separate normalization.

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **2.1** | **Create `normalize_project_id()` utility** | New file. Reads `constants.SYSTEM_DEFAULT_PROJECT_ID`. Returns it for `None` or `""` input; returns input unchanged otherwise. Raises `RuntimeError` if `SYSTEM_DEFAULT_PROJECT_ID` is `None` (called before startup). | `daemon/services/project_normalizer.py` (new) |
| **2.2** | **🚨 [B1] Add normalization in `enqueue()` as canonical chokepoint** | At the very start of `JobQueueService.enqueue()`, call `project_id = normalize_project_id(project_id)`. This is the single guarantee that ALL callers — HTTP, tools, `retry_job()`, `instance_lifecycle`, recovery — get normalized project IDs. Add a comment: `# Canonical normalization: ensures ALL callers get system_default_project for None/empty`. | `daemon/services/job_queue_service.py` |
| 2.3 | Add Pydantic validator to `JobCreateRequest` | `@field_validator("project_id", mode="before")` — strips whitespace, replaces `None`/`""` with `normalize_project_id()`. Defense-in-depth: catches bad input early with clear error messages. | `daemon/routers/schemas.py` |
| 2.4 | Normalize in `create_job` endpoint | Call `normalize_project_id(request.project_id)` before `service.enqueue()`. Defense-in-depth. | `daemon/routers/jobs_crud.py` |
| 2.5 | Normalize in `job_create` tool | Call `normalize_project_id(project_id)` before `job_service.enqueue()`. Defense-in-depth. | `daemon/tools/job_queue.py` |
| **2.6** | **🚨 [R1] Normalize in `instance_lifecycle.py:104-106`** | Replace the current logic that converts `"null"/"none"/""` to `None`. Instead, convert these values to `SYSTEM_DEFAULT_PROJECT_ID` via `normalize_project_id()`. Also address `daemon/tools/instance.py:315-317` (auto-inherit from parent) — if parent has `None`, normalize to system project. | `daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py` |
| 2.7 | Add unit tests for `normalize_project_id()` | Test: `None` → system ID; `""` → system ID; valid UUID → unchanged; raises if system ID not set. | `tests/unit/test_project_normalizer.py` (new) |
| 2.8 | Add Pydantic model tests for `JobCreateRequest` | Test: `project_id=None` serializes to system ID; `project_id=""` serializes to system ID; `project_id="valid-uuid"` unchanged. | `tests/unit/test_schemas.py` (new or extend) |
| **2.9** | **🚨 [B1] Add test: retry of orphan job gets system project ID** | Create an orphan job (with `project_id=None`), then call `retry_job()`. Assert the new job's `project_id` equals `SYSTEM_DEFAULT_PROJECT_ID`. This proves the `enqueue()` chokepoint works for internal callers. | `tests/unit/test_job_queue_service.py` (extend) |
| 2.10 | Add integration test for `POST /jobs` with null project_id | Verify DB row has system project ID, not NULL. | `tests/integration/test_job_create.py` (new or extend) |

---

## Key Files

- `daemon/services/project_normalizer.py` — **New file** — `normalize_project_id()` function
- `daemon/services/job_queue_service.py` — **🚨 Add canonical normalization in `enqueue()`** (B1 fix)
- `daemon/routers/schemas.py` — Add `field_validator` on `JobCreateRequest.project_id`
- `daemon/routers/jobs_crud.py` — Normalize in `create_job()`
- `daemon/tools/job_queue.py` — Normalize in `job_create` tool
- `daemon/services/instance_lifecycle.py` — **🚨 Normalize `"null"/"none"/""` → system ID** (R1 fix)
- `daemon/tools/instance.py` — **🚨 Normalize parent `None` inheritance** (R1 fix)
- `tests/unit/test_project_normalizer.py` — **New file** — unit tests
- `tests/unit/test_schemas.py` — **New or extend** — model validator tests
- `tests/unit/test_job_queue_service.py` — **Extend** — retry normalization test
- `tests/integration/test_job_create.py` — **New or extend** — integration test

---

## Constraints

1. **`enqueue()` is the canonical chokepoint (B1).** The primary normalization MUST live inside `enqueue()`. Boundary normalization (schemas, routers, tools) is defense-in-depth — it catches bad input early with clear errors but must not be the sole line of defense. Internal callers like `retry_job()` bypass boundaries entirely.
2. **Fail-fast on uninitialized system project.** `normalize_project_id()` must raise `RuntimeError` if `SYSTEM_DEFAULT_PROJECT_ID` is `None` (meaning it was called before the daemon finished startup), rather than returning `None` and propagating the bug downstream.
3. **Preserve explicit project IDs.** Normalization must only replace `None` and `""`; any non-empty string is passed through unchanged. Do not validate that the project exists at normalization time — that is a downstream concern.
4. **No changes to `enqueue()` signature.** The method continues to accept `str | None` for backward compatibility. The normalization happens at the top of the method body, not in the type signature.
5. **Tool docstrings unchanged.** The `job_create` tool's documentation string may note that `project_id=None` defaults to the system project, but the type annotation in the LangChain tool should remain `str | None` for now.

---

## Deliverables

- [ ] `normalize_project_id()` utility in `daemon/services/project_normalizer.py`
- [ ] **🚨 Canonical normalization in `enqueue()` — the B1 fix** (job_queue_service.py)
- [ ] Pydantic validator in `JobCreateRequest` (schemas.py) — defense-in-depth
- [ ] Normalization in `jobs_crud.py create_job()` — defense-in-depth
- [ ] Normalization in `job_queue.py job_create` tool — defense-in-depth
- [ ] **🚨 Normalization in `instance_lifecycle.py` and `tools/instance.py` — the R1 fix**
- [ ] Unit tests for `normalize_project_id()`
- [ ] Pydantic model tests for `JobCreateRequest`
- [ ] **🚨 Test: retry of orphan job gets system project ID via `enqueue()` chokepoint**
- [ ] Integration test verifying DB row has non-NULL `project_id`
- [ ] All tests pass (`pytest tests/ -v`)
