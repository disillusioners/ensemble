# Phase 5: Test Suite Updates (Rev. 2)

> **Revision 2**: Fixes W4 (adds `test/packs/`), S3 (adds migration tests for dual-engine).

## Objective
Update all 107 test files + 1 `test/packs/` file that reference "coder" as an agent_id to use "developer" instead. Additionally, add dedicated migration tests (S3) to verify the DB migration works on both PostgreSQL and SQLite.

## Coupling
- **Depends on**: Phase 1 (agent directory renamed), Phase 2 (daemon source updated), Phase 3 (DB migration + alias)
- **Coupling type**: tight
- **Shared files with other phases**: Tests exercise the daemon + migration; must match the new agent_id
- **Shared APIs/interfaces**: Tests call `spawn_instance(agent_id="developer")`, query instances by agent_id, verify migration, etc.
- **Why this coupling**: Tests must pass against the renamed agent + migration; can't be updated until rename is complete

## Context
The test suite has **107 files** under `tests/` with **1,038 references** plus **1 file** under `test/packs/` with **3 references** (W4).

| Category | Pattern | Count (est.) | Action |
|----------|---------|-------------|--------|
| agent_id assignment | `agent_id="coder"` / `agent_id: str = "coder"` | ~400 | Replace with `"developer"` |
| agent_dir paths | `"./agents/coder"` / `"/agents/coder"` | ~100 | Replace with `"./agents/developer"` |
| String assertions | `assert ... == "coder"` | ~150 | Replace with `"developer"` |
| Dict/JSON test data | `"agent_id": "coder"` | ~200 | Replace with `"developer"` |
| Natural language (E2E) | `"spawn a coder"` | ~50 | Replace with `"spawn a developer"` |
| Test fixture creation | `create_agent_meta(dir, "coder")` | ~50 | Replace with `"developer"` |
| Comments/docstrings | `# spawn coder instance` | ~88 | Replace or leave (low priority) |

## Test File Breakdown by Category

### Unit Tests (19 files)
```
tests/unit/test_cascade_pause_resume.py
tests/unit/test_critical_notes_api.py
tests/unit/test_critical_notes_schema.py
tests/unit/test_devops_agent.py
tests/unit/test_hide_kb_instances.py
tests/unit/test_instance_children_junction_c10.py
tests/unit/test_instance_tree_loading.py
tests/unit/test_job_processor_status_guard.py
tests/unit/test_live_event_hub.py
tests/unit/test_mcp_cold_load_race.py
tests/unit/test_models_split.py
tests/unit/test_notification_broadcaster.py
tests/unit/test_notification_lifecycle_hook.py
tests/unit/test_notification_sse_endpoint.py
tests/unit/test_pause_flow_redesign.py
tests/unit/test_ready_message_completion_report.py
tests/unit/test_resume_flow_redesign.py
tests/unit/tools/test_inner_soul_persona_preservation.py
tests/unit/services/test_title_generation_trigger.py
```

### Integration Tests (9 files)
```
tests/integration/test_agent_bootstrap.py
tests/integration/test_cold_resume_ttl.py
tests/integration/test_completion_report.py
tests/integration/test_crash_recovery_paused.py
tests/integration/test_dlq_project_normalization.py
tests/integration/test_instance_title_e2e.py
tests/integration/test_job_create.py
tests/integration/test_message_queue_e2e.py
tests/integration/test_migration.py
```

### E2E Tests (3 files) — CRITICAL
```
tests/e2e/test_e2e_workflows.py     (25 refs — uses natural language)
tests/e2e/test_mcp_tools.py          (3 refs)
tests/e2e/test_mcp_tools_restore.py  (3 refs)
```

### `test/packs/` — W4 FIX
```
test/packs/stop_resume_spawn_e2e_test.py  (3 refs — lines 249, 252, 253)
```
This file is **outside** the `tests/` directory. It sends LLM prompts asking the leader to "spawn a coder instance". Must change to "developer".

| Line | Current | New |
|------|---------|-----|
| 249 | `# Step 3: Send message asking leader to spawn coder` | `# Step 3: Send message asking leader to spawn developer` |
| 252 | `"Hello! Please spawn a coder instance to do a simple task. "` | `"Hello! Please spawn a developer instance to do a simple task. "` |
| 253 | `"Use the spawn_instance tool to create a coder instance."` | `"Use the spawn_instance tool to create a developer instance."` |

### Job Queue Tests (28 files)
All in `tests/job_queue/` — mostly use `agent_id="coder"` as test data.

### Postgres Tests (3 files)
```
tests/postgres/test_inflight_flag_flip.py
tests/postgres/test_premature_completion_edge_cases.py
tests/postgres/test_premature_completion_regression.py
```

### Root Test Files (41 files)
Various test files in `tests/` root — includes `test_registry.py` (heavily tests agent resolution), `test_agents_api.py` (tests agent discovery), `test_models.py`, etc.

### Other (3+1 files)
```
tests/services/test_instance_lifecycle_h10_l14.py
tests/tools/test_send_message_status_guard.py
tests/tools/test_send_message_task_repo_guard.py
tests/migration/test_data_factory.py
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Batch replace in unit tests | Replace `"coder"` → `"developer"` and `"./agents/coder"` → `"./agents/developer"` across 19 unit test files | `tests/unit/**/*.py` |
| 2 | Batch replace in job queue tests | Same replacement across 28 job queue test files | `tests/job_queue/**/*.py` |
| 3 | Batch replace in integration tests | Same replacement across 9 integration test files | `tests/integration/**/*.py` |
| 4 | Batch replace in root test files | Same replacement across 41 root test files | `tests/*.py` |
| 5 | Update E2E tests (CAREFUL) | Replace natural language + agent_id references. These tests send LLM prompts like "spawn a coder" — must change to "spawn a developer" | `tests/e2e/*.py` |
| 6 | Update `test/packs/` E2E test (W4) | Update `stop_resume_spawn_e2e_test.py` — 3 refs on lines 249, 252, 253 | `test/packs/stop_resume_spawn_e2e_test.py` |
| 7 | Update postgres tests | Same replacement across 3 postgres test files | `tests/postgres/*.py` |
| 8 | Update other test dirs | services/, tools/, migration/ test files | `tests/{services,tools,migration}/*.py` |
| 9 | Update conftest.py | Check for "coder" in shared fixtures | `tests/conftest.py`, `tests/job_queue/conftest.py` |
| 10 | Update test helper files | Check mock helpers and test utilities | `tests/mock_pause_resume.py`, `tests/mock_test_job_queue_api.py`, `tests/resume_mock_test.py` |
| 11 | **Write migration tests (S3)** | Create dedicated tests for the coder→developer DB migration — see below | `tests/unit/test_coder_developer_migration.py` |
| 12 | **Write alias tests (S1)** | Create tests verifying `resolve_pure_id("coder")`, `resolve_path_to_id("./agents/coder")`, `exists("coder")` all resolve to "developer" | `tests/test_registry.py` (add tests) |
| 13 | Run full test suite | Run tests against PostgreSQL and verify all pass | — |

### Migration Tests (S3) — Detailed Requirements

Create `tests/unit/test_coder_developer_migration.py` with the following test cases:

#### Test 1: Migration updates agent_id in DB
```python
def test_migration_updates_coder_to_developer():
    """Insert row with agent_id='coder' → run migration → verify 'developer'."""
    # Setup: insert instance with agent_id='coder', agent_dir='./agents/coder'
    # Run: migration statements (same SQL as _ensure_postgres_columns / run_migrations)
    # Assert: agent_id == 'developer', agent_dir == './agents/developer'
```

#### Test 2: Migration is idempotent
```python
def test_migration_idempotent():
    """Run migration twice → verify no errors and correct final state."""
    # Setup: insert row with agent_id='coder'
    # Run migration twice
    # Assert: agent_id == 'developer' after first run, no error on second run
```

#### Test 3: Migration handles tables without 'coder' rows
```python
def test_migration_no_coder_rows():
    """Run migration on DB with no 'coder' rows → verify no errors."""
    # Setup: insert rows with agent_id='developer' and agent_id='leader'
    # Run migration
    # Assert: no rows changed, no errors
```

#### Test 4: Migration covers all 6 tables
```python
def test_migration_covers_all_tables():
    """Insert 'coder' rows in all 6 tables → run migration → verify all updated."""
    # Tables: instances, instance_mappings, job_queue_items,
    #         dead_letter_items, projects (creator_agent_id), jobqueue (if exists)
```

#### Test 5: Dual-engine (run on both PostgreSQL and SQLite)
```python
@pytest.mark.parametrize("engine_type", ["sqlite", "postgresql"])
def test_migration_dual_engine(engine_type):
    """Run migration on both SQLite and PostgreSQL → verify identical results."""
    # Skip PostgreSQL test if not available (like existing postgres/ tests)
```

### Alias Tests (S1) — Add to `tests/test_registry.py`

```python
def test_resolve_pure_id_alias():
    """resolve_pure_id('coder') returns 'developer' via alias."""

def test_resolve_path_to_id_alias():
    """resolve_path_to_id('./agents/coder') returns 'developer' via alias."""

def test_exists_alias():
    """exists('coder') returns True via alias."""

def test_instance_create_normalizes_alias():
    """InstanceCreate(agent_id='coder') normalizes to 'developer'."""
```

## Special Handling Required

### test_registry.py — Agent resolution tests
This file extensively tests `resolve_to_id()`, `resolve_pure_id()`, and path resolution. All references to `"coder"` as an agent_id must change to `"developer"`. Key test functions:
- `test_resolve_to_id` (line 253-282): Tests path resolution for `agents/coder` → must become `agents/developer`
- `test_get_agent` (line 195): Creates agent with `id="coder"` → must become `id="developer"`
- `test_exists` (line 217): Tests `registry.exists("coder")` → must become `"developer"`

### test_agents_api.py — Agent discovery tests
- Lines 32-35: Creates `agents/coder/` dir in test → must become `agents/developer/`
- Line 90: `assert agents[0]["id"] == "coder"` → must become `"developer"`
- Line 94: `assert agents[0]["agent_dir"] == "./agents/coder"` → must become `"./agents/developer"`

### E2E tests — Natural language prompts
`test_e2e_workflows.py` sends messages to the leader like:
```python
"ask coder to say hello, this is a test workflow, coder dont need do anything"
```
This must become:
```python
"ask developer to say hello, this is a test workflow, developer dont need do anything"
```
And the test that waits for the coder child:
```python
f"Leader {leader_id[:8]}... did not spawn a coder child"
```
Must become:
```python
f"Leader {leader_id[:8]}... did not spawn a developer child"
```

### test_inner_soul_persona_preservation.py — Identity classification
```python
def test_i_am_a_coder_specialist(self):
    result = _classify_request("I am a coder specialist")
```
⚠️ **Decision needed**: This test checks if "I am a coder" is classified as an identity statement. This is testing the NLP classification logic, not the agent_id. Options:
- **Option A**: Change to "I am a developer specialist" (consistent with rename)
- **Option B**: Leave as "coder" since it tests a generic NLP classification that should handle any identity claim
- **Recommended**: Option A for consistency, but add a test case for "developer" too

### test_memory_system.py — Soul prompt test data
```python
prompts = {"soul": "I am a coder"}
```
This is test data for a soul prompt. Change to `"I am a developer"`.

## Constraints
- Run tests against **PostgreSQL** (primary dev/test DB per critical notes)
- Also verify SQLite compatibility
- E2E tests require a running daemon with the LLM configured — may need to verify manually
- Some references to "coder" in comments are informational and low-priority, but should be updated for consistency
- **DO NOT** change references to `encoder` or `tiktoken` in test files (false positives)

## Deliverables
- [ ] All 107 test files + `test/packs/stop_resume_spawn_e2e_test.py` updated to use `"developer"` instead of `"coder"`
- [ ] `grep -rn "coder" tests/ test/packs/ --include="*.py" | grep -v encoder | grep -v tiktoken` returns 0 matches
- [ ] **Migration tests pass (S3)**: `pytest tests/unit/test_coder_developer_migration.py -x`
- [ ] **Alias tests pass (S1)**: `pytest tests/test_registry.py -k alias -x`
- [ ] Unit tests pass: `pytest tests/unit/ -x`
- [ ] Job queue tests pass: `pytest tests/job_queue/ -x`
- [ ] Integration tests pass: `pytest tests/integration/ -x`
- [ ] Root tests pass: `pytest tests/ -x --ignore=tests/e2e --ignore=tests/unit --ignore=tests/integration --ignore=tests/job_queue`
- [ ] E2E tests pass (if daemon + LLM available): `pytest tests/e2e/ -x`
- [ ] `test/packs/` E2E test passes: `pytest test/packs/stop_resume_spawn_e2e_test.py -x` (W4)
- [ ] Full test suite passes: `pytest tests/ -x`
