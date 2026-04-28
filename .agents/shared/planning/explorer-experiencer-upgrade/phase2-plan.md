# Phase 2: Auto KB Update via Job Queue

## Objective
Modify the `explore()` tool in `knowledge_tools.py` to parse the `## Should Update KB` flag from Explorer's response and automatically create an experiencer job in the project's parallel queue when the flag is true.

## Coupling
- **Depends on**: None (independent of Phase 1)
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: Expects Explorer response to contain `## Should Update KB: true/false` (format defined in Phase 1)

## Context
The explore() tool currently:
1. Spawns Explorer agent via `invoke_agent_and_wait()` (synchronous, 300s timeout)
2. Returns the Explorer's response text as-is

We need to add:
1. Parse the response for `## Should Update KB: true/false`
2. If true AND project_id is available, create an experiencer job via JobQueueService
3. Job goes to `system_parallel_queue` (falls back to `system_fifo_queue` if parallel doesn't exist)
4. Return the Explorer response unchanged — job creation is fire-and-forget

### Key Architecture Reference

**Accessing JobQueueService from the tool:**
```python
# InstanceManager has _job_queue_service set via api.py:188
# Pattern used in daemon/tools/instance.py:288:
job_service = getattr(manager, '_job_queue_service', None)
```

**Queue resolution:**
- System queues are created per-project by `job_queue_mgmt_service.ensure_system_queues()`
- Queue names: `"system_fifo_queue"` (FIFO, concurrency=1) and `"system_parallel_queue"` (PARALLEL, concurrency=5)
- Queue repo: `job_service._queue_repo` (JobQueueRepository)
- Lookup: `queue_repo.get_by_name(project_id, "system_parallel_queue")`

**Job creation via JobQueueService.enqueue():**
```python
await job_service.enqueue(
    agent_id="experiencer",
    message=experiencer_message,
    source="explore:{source_instance_id}",
    project_id=project_id,
    priority=5,
    queue_id=queue_id,
    metadata={"triggered_by": "explorer", "original_query": query},
)
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add regex parsing utility | Add `_SHOULD_UPDATE_KB_PATTERN` regex and `_parse_should_update_kb(response)` function at module level in knowledge_tools.py | `daemon/tools/knowledge_tools.py` |
| 2 | Add experiencer job enqueue utility | Add `_enqueue_experiencer_job()` async function that resolves the parallel queue and creates a job via JobQueueService | `daemon/tools/knowledge_tools.py` |
| 3 | Integrate into explore() tool | After getting Explorer response, parse the flag. If true, fire-and-forget enqueue the experiencer job. Return response unchanged. | `daemon/tools/knowledge_tools.py` |
| 4 | Add necessary imports | Add `import re` and `import asyncio` (if not already present) | `daemon/tools/knowledge_tools.py` |

## Key Files
- `daemon/tools/knowledge_tools.py` — Knowledge tools (explore, experience)

## Detailed Change Specification

### Add imports (top of file)
```python
import re
# asyncio is already available but add explicit import if needed
```

### Add module-level regex pattern (after CATEGORY_DOC, before create_knowledge_tools)
```python
# Pattern to match "## Should Update KB: true" or "## Should Update KB: false"
_SHOULD_UPDATE_KB_PATTERN = re.compile(
    r"##\s+Should\s+Update\s+KB:\s*(true|false)",
    re.IGNORECASE,
)
```

### Add `_parse_should_update_kb()` function (after the regex)
```python
def _parse_should_update_kb(response: str) -> bool:
    """Parse the Explorer response for the should_update_kb flag.

    Args:
        response: The Explorer agent's response text.

    Returns:
        True if the response indicates knowledge should be updated, False otherwise.
    """
    match = _SHOULD_UPDATE_KB_PATTERN.search(response)
    if match:
        return match.group(1).lower() == "true"
    return False
```

### Add `_enqueue_experiencer_job()` function (after parser)
```python
async def _enqueue_experiencer_job(
    manager: "InstanceManager",
    query: str,
    explorer_response: str,
    project_id: str,
    source_instance_id: str,
) -> None:
    """Fire-and-forget: create a job for the experiencer agent to update KB.

    This function is designed to never raise — all errors are logged and swallowed.
    The caller (explore tool) should not be affected by KB update failures.

    Args:
        manager: The InstanceManager instance.
        query: The original query that was explored.
        explorer_response: The Explorer's response with findings.
        project_id: The project ID for job routing.
        source_instance_id: The instance that called explore().
    """
    try:
        job_service = getattr(manager, "_job_queue_service", None)
        if job_service is None:
            logger.warning("JobQueueService not available, skipping experiencer job")
            return

        # Resolve system_parallel_queue for this project
        queue = await asyncio.to_thread(
            job_service._queue_repo.get_by_name, project_id, "system_parallel_queue"
        )
        if queue is None:
            # Fall back to system_fifo_queue if parallel doesn't exist
            queue = await asyncio.to_thread(
                job_service._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            if queue is None:
                logger.warning(
                    "No system queue found for project %s, skipping experiencer job",
                    project_id,
                )
                return
            logger.debug("No parallel queue for %s, using FIFO queue", project_id)

        # Build message for experiencer with full context
        experiencer_message = (
            "Process new knowledge discovered during exploration.\n\n"
            f"Original Query: {query}\n\n"
            f"Explorer Findings:\n{explorer_response}\n\n"
            f"Project: {project_id}"
        )

        # Create the job
        await job_service.enqueue(
            agent_id="experiencer",
            message=experiencer_message,
            source=f"explore:{source_instance_id}",
            project_id=project_id,
            priority=5,
            queue_id=queue.queue_id,
            metadata={
                "triggered_by": "explorer",
                "original_query": query,
            },
        )
        logger.debug(
            "Enqueued experiencer job for project %s on queue %s",
            project_id, queue.queue_id,
        )

    except Exception as e:
        # Fire-and-forget: don't fail the explore response if job creation fails
        logger.warning("Failed to enqueue experiencer job: %s", e)
```

### Modify explore() tool — add after `result` check, before `return result`

Current code (lines 89-91):
```python
        if result is None:
            return "Explorer agent timed out or failed. Try a simpler query."
        return result
```

New code:
```python
        if result is None:
            return "Explorer agent timed out or failed. Try a simpler query."

        # Parse response for should_update_kb flag
        should_update_kb = _parse_should_update_kb(result)

        # Fire-and-forget: create job for experiencer if knowledge update needed
        if should_update_kb and pid:
            _enqueue_experiencer_job(
                manager=manager,
                query=query,
                explorer_response=result,
                project_id=pid,
                source_instance_id=current_instance_id,
            )

        return result
```

**Important design decisions:**
1. **`_enqueue_experiencer_job()` is NOT awaited** — it's fire-and-forget via background task scheduling. The `explore()` tool returns immediately with the Explorer's response. The job creation happens in the background. (Note: Since the function is async, it needs to be scheduled as a task. We should use `asyncio.create_task()` or similar.)

   **Correction**: Actually, we need to think about this carefully. `explore()` itself is async, so we CAN await it without blocking the caller significantly (the job creation is fast — just a DB write). But to truly make it non-blocking and ensure errors don't propagate, we should wrap it:

   ```python
   # Fire-and-forget: schedule the job creation as a background task
   asyncio.ensure_future(_enqueue_experiencer_job(
       manager=manager,
       query=query,
       explorer_response=result,
       project_id=pid,
       source_instance_id=current_instance_id,
   ))
   ```

   This way `explore()` returns immediately and the job creation happens asynchronously.

## Constraints
- The `experience()` tool must NOT be changed — it already works correctly
- The experiencer agent files must NOT be changed
- The explore() tool must return the exact same response as before — the caller sees no difference
- Job creation must be fire-and-forget — errors must be logged but never propagated to the caller
- If `project_id` is None, skip the job (no way to route it)
- If `_job_queue_service` is not available on the manager, skip silently with a warning log

## Deliverables
- [ ] `_parse_should_update_kb()` function added
- [ ] `_enqueue_experiencer_job()` function added
- [ ] explore() tool parses flag and triggers job creation
- [ ] All errors handled gracefully with logging
- [ ] No changes to experience() tool
