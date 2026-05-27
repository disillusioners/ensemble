# Phase 3: Router Integration — Resume Endpoint Fix

## Objective

Update the resume endpoint in `daemon/routers/instances.py` to work with the new cascade function return values. The router must call `resume_processing_job()` for ALL resumed nodes, passing `silent=False` for the target and `silent=True` for all others.

## Coupling

- **Depends on**: Phase 2 (rewritten `resume_instance_cascade()` with new return format)
- **Coupling type**: tight — router directly calls cascade function and consumes its return dict
- **Shared files with other phases**: `daemon/routers/instances.py`
- **Shared APIs/interfaces**: The resume endpoint's return format must stay backward-compatible

## Context

### Current router code (already mostly correct)
The current `resume_instance` endpoint already:
1. Calls `resume_instance_cascade(instance_id)` → gets `resumed_ids`
2. Loops over `resumed_ids` calling `resume_processing_job()`
3. Target gets `silent=False`, others get `silent=True`

This is **mostly correct** but needs these changes:

### Required changes

1. **Use `target_id` from cascade result** — The cascade function now returns `target_id` in its dict. Use this instead of assuming `instance_id` is the target (it is, but being explicit is better).

2. **No changes to `resume_processing_job()` itself** — The method in `manager.py` is already correct. It handles:
   - Finding orphaned PROCESSING job
   - Creating fresh CancellationToken
   - Re-executing via `_process_message_with_tracking()`
   - When `silent=True`: passes `is_retry=True` → pure checkpoint resume
   - When `silent=False`: passes `is_retry=False` → checkpoint + new message

3. **No changes to pause endpoint** — The pause endpoint just calls `pause_instance_cascade()` and returns the result. The new return dict includes `target_id` which is harmless.

4. **Error handling** — If `resume_processing_job()` fails for some nodes but succeeds for others, return partial results. Don't fail the entire request.

## Key Files

- `daemon/routers/instances.py` — resume endpoint (lines ~262-279)
- `daemon/manager.py` — `resume_processing_job()` (lines ~1804-1883) — **NO CHANGES NEEDED**

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update resume endpoint to use `target_id` from cascade result | Change `is_target = rid == instance_id` to `is_target = rid == result["target_id"]`. Also log which node is the target vs silent. | instances.py |
| 2 | Verify error handling is robust | Ensure partial failures don't crash the endpoint. Current try/except per-node is correct — verify it still works. | instances.py |
| 3 | Verify pause endpoint works with new return format | The pause endpoint just forwards cascade result. The new dict has an extra `target_id` field — this is harmless but verify frontend doesn't break. | instances.py |
| 4 | Update API response documentation | Ensure the response model (if any Pydantic model exists) accepts the new `target_id` field. Currently returns raw dict so likely no change needed. | instances.py |

## Updated Resume Endpoint Code

```python
@router.post("/{instance_id}/resume")
async def resume_instance(
    instance_id: str,
    request: Request,
    body: ResumeRequest | None = None,
) -> dict:
    """Resume a paused instance and cascade to entire tree.
    
    Finds the root of the tree, resumes ALL nodes in the tree.
    Target instance gets the resume message; all others resume silently.
    """
    manager = _get_manager(request)

    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    message_text = (body.message.strip() if body and body.message else None) or "resume"

    # Cascade resume (now full-tree: root + all descendants)
    result = await manager.resume_instance_cascade(instance_id)
    target_id = result.get("target_id", instance_id)

    # Resume processing jobs for ALL resumed instances
    # Target: silent=False → checkpoint resume + new message injected
    # Others: silent=True → pure checkpoint resume, no new message
    resume_results = {}
    for rid in result["resumed_ids"]:
        try:
            is_target = rid == target_id
            job_result = await manager.resume_processing_job(
                rid,
                message=message_text if is_target else "resume",
                silent=not is_target,
            )
            resume_results[rid] = job_result
        except Exception as e:
            logger.warning(f"Failed to resume job for {rid[:8]}...: {e}")
            resume_results[rid] = {"error": str(e)}

    return {
        "resumed": True,
        "resumed_ids": result["resumed_ids"],
        "skipped_ids": result["skipped_ids"],
        "target_id": target_id,
        "resume_results": resume_results,
    }
```

### Pause endpoint (minimal change)

```python
@router.post("/{instance_id}/pause")
async def pause_instance(
    instance_id: str,
    request: Request,
) -> dict:
    """Pause an instance and its entire tree."""
    manager = _get_manager(request)

    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    result = await manager.pause_instance_cascade(instance_id)
    return {
        "paused": True,
        "paused_ids": result["paused_ids"],
        "skipped_ids": result["skipped_ids"],
        "target_id": result.get("target_id", instance_id),
    }
```

## What NOT to Change

| Component | Reason |
|-----------|--------|
| `resume_processing_job()` in manager.py | Already correct — handles silent flag, finds PROCESSING job, re-executes |
| `_process_message_with_tracking()` | Unchanged — the `is_retry` flag from `silent` already works |
| Frontend API calls | Resume endpoint URL and basic response shape unchanged |
| `ResumeRequest` model | Unchanged — just has optional `message` field |

## Constraints

- Response must remain backward-compatible (new `target_id` field is additive)
- The router does NOT need to know about `waiting_for` — that's handled in Phase 2
- The router does NOT need to know about tree traversal — that's handled in Phase 1-2

## Deliverables

- [ ] Resume endpoint uses `target_id` from cascade result
- [ ] Pause endpoint includes `target_id` in response
- [ ] `resume_processing_job()` called for every node in `resumed_ids`
- [ ] Error handling verified (partial failures don't crash)
- [ ] No changes to `resume_processing_job()` itself
