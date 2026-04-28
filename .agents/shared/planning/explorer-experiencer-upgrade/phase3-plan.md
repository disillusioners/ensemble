# Phase 3: Tests

## Objective
Add and update tests to verify the new behavior: flag parsing, job enqueue on true, no job on false, graceful failure, and Explorer agent output format.

## Coupling
- **Depends on**: Phase 1 (output format), Phase 2 (tool behavior)
- **Coupling type**: loose
- **Shared files with other phases**: Tests import from `daemon/tools/knowledge_tools.py` (Phase 2)
- **Shared APIs/interfaces**: Tests verify the `## Should Update KB` format from Phase 1

## Context
Existing test file: `tests/unit/tools/test_knowledge_tools.py` (322 lines, 35 tests).
Tests use `mock_manager` fixture with `MagicMock` for InstanceManager and `AsyncMock` for async methods.
Tests patch `invoke_agent_and_wait` to control Explorer responses.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add tests for `_parse_should_update_kb()` | Test true, false, missing, case variations, whitespace variations | `tests/unit/tools/test_knowledge_tools.py` |
| 2 | Add test: explore() triggers job when should_update_kb=true | Mock response with `## Should Update KB: true`, verify enqueue called | `tests/unit/tools/test_knowledge_tools.py` |
| 3 | Add test: explore() does NOT trigger job when should_update_kb=false | Mock response with `## Should Update KB: false`, verify no enqueue | `tests/unit/tools/test_knowledge_tools.py` |
| 4 | Add test: explore() does NOT trigger job when flag missing | Old-style response without the flag, verify no enqueue | `tests/unit/tools/test_knowledge_tools.py` |
| 5 | Add test: explore() skips job when no project_id | should_update_kb=true but no project_id, verify graceful skip | `tests/unit/tools/test_knowledge_tools.py` |
| 6 | Add test: explore() skips job when JobQueueService unavailable | Manager without `_job_queue_service`, verify graceful skip | `tests/unit/tools/test_knowledge_tools.py` |
| 7 | Add test: explore() skips job when system queue not found | Queue repo returns None, verify graceful skip | `tests/unit/tools/test_knowledge_tools.py` |
| 8 | Update mock_manager fixture | Add `_job_queue_service` with mock `_queue_repo` and async `enqueue` | `tests/unit/tools/test_knowledge_tools.py` |
| 9 | Run full test suite | Verify all 35+ existing tests still pass | — |

## Key Files
- `tests/unit/tools/test_knowledge_tools.py` — Existing test file to extend

## Detailed Test Specifications

### Test: `_parse_should_update_kb()` unit tests

```python
class TestParseShouldUpdateKb:
    def test_returns_true_for_explicit_true(self):
        assert _parse_should_update_kb("## Should Update KB: true") is True

    def test_returns_true_case_insensitive(self):
        assert _parse_should_update_kb("## Should Update KB: True") is True
        assert _parse_should_update_kb("## should update kb: TRUE") is True

    def test_returns_false_for_explicit_false(self):
        assert _parse_should_update_kb("## Should Update KB: false") is False

    def test_returns_false_when_missing(self):
        assert _parse_should_update_kb("## Answer\nSome text\n## Confidence: HIGH") is False

    def test_returns_false_for_empty_string(self):
        assert _parse_should_update_kb("") is False

    def test_handles_extra_whitespace(self):
        assert _parse_should_update_kb("##  Should   Update   KB:  true ") is True
```

### Test: explore() triggers experiencer job

```python
@pytest.mark.asyncio
async def test_explore_enqueues_experiencer_job_on_should_update(self, configured_env, mock_manager):
    """When Explorer returns should_update_kb=true, an experiencer job is created."""
    # Setup mock job queue service
    mock_queue = MagicMock()
    mock_queue.queue_id = "parallel-queue-123"
    mock_manager._job_queue_service._queue_repo.get_by_name = MagicMock(return_value=mock_queue)
    mock_manager._job_queue_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-123"))

    explorer_response = "## Answer\nFound info from files.\n\n## Confidence: LOW\n\n## Should Update KB: true"

    with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
               new_callable=AsyncMock, return_value=explorer_response):
        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        explore_tool = next(t for t in tools if t.name == "explore")

        result = await explore_tool.ainvoke({"query": "What is X?"})

        # Response returned unchanged
        assert "## Answer" in result
        assert "Should Update KB: true" in result

        # Allow async task to complete
        await asyncio.sleep(0.1)

        # Verify job was created
        mock_manager._job_queue_service.enqueue.assert_called_once()
        call_kwargs = mock_manager._job_queue_service.enqueue.call_args.kwargs
        assert call_kwargs["agent_id"] == "experiencer"
        assert "What is X?" in call_kwargs["message"]
```

### Updated mock_manager fixture

```python
@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with configured return values."""
    manager = MagicMock()

    # Instance metadata
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

    # Spawn and enqueue (for experience tool)
    manager.spawn_instance = MagicMock(return_value="spawned-instance-abc123")
    manager.enqueue_message = AsyncMock()

    # Job queue service (for explore auto-KB-update)
    mock_queue_repo = MagicMock()
    mock_queue = MagicMock()
    mock_queue.queue_id = "system-parallel-queue-123"
    mock_queue_repo.get_by_name = MagicMock(return_value=mock_queue)

    mock_job_service = MagicMock()
    mock_job_service._queue_repo = mock_queue_repo
    mock_job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-123"))
    manager._job_queue_service = mock_job_service

    return manager
```

## Constraints
- All existing tests must continue to pass (backward compatible)
- New tests must not require actual RAG or job queue services (mocked)
- Tests follow the existing pattern in the file (patch invoke_agent_and_wait, use mock_manager)

## Deliverables
- [ ] All `_parse_should_update_kb()` unit tests pass
- [ ] All explore() job trigger tests pass (true, false, missing, no project, no service, no queue)
- [ ] All 35+ existing tests still pass
- [ ] Full test suite green
