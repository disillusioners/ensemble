# Phase 2: Rewrite Cascade Functions for Full-Tree Operation

## Objective

Rewrite `pause_instance_cascade()` and `resume_instance_cascade()` in `instance_lifecycle.py` to operate on the **entire tree** (root + all descendants) instead of target + children only. Add `waiting_for` upward propagation on resume from child.

## Coupling

- **Depends on**: Phase 1 (tree helpers: `get_tree_root_id`, `get_tree_ids`, `get_ancestor_ids`)
- **Coupling type**: tight — directly calls Phase 1 methods
- **Shared files with other phases**: `daemon/services/instance_lifecycle.py` (called by Phase 3 router)
- **Shared APIs/interfaces**: Both functions' signatures and return values are consumed by Phase 3

## Context

### Current behavior (BROKEN)
- `pause_instance_cascade()`: Recursive DFS children-first. Pauses target + descendants only. Parent and siblings keep running.
- `resume_instance_cascade()`: Recursive DFS children-first. Resumes target + descendants only. Ancestors, siblings, and their subtrees stay paused.

### Required behavior (NEW)
- `pause_instance_cascade()`: Find root from target. Collect entire tree. Pause ALL nodes (root + all descendants). `waiting_for = 0` for all.
- `resume_instance_cascade()`: Find root from target. Collect entire tree. Resume ALL nodes. If target ≠ root, ancestors of target get `waiting_for = 1`.

### Key insight: `_pause_single()` closure
The current `_pause_single()` is a nested closure inside `pause_instance_cascade()`. It handles:
1. Cancel active LLM requests (`_request_registry.cancel_by_instance`)
2. Kill graph task (`_graph_tasks.pop` + `cancel()`)
3. Update DB status to PAUSED, set `waiting_for=0` if > 0, set `paused_at`

This closure logic is CORRECT and should be **extracted to a standalone method** (not a closure) since the new approach iterates over a flat set instead of recursing.

### Key files to modify
- `daemon/services/instance_lifecycle.py` — lines 496-694 (both cascade functions)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Extract `_pause_single_node()` as a private method on the class | Move the closure body to `async def _pause_single_node(self, instance_id: str, meta: Instance | None = None) -> bool`. Keep exact same logic: cancel LLM requests, kill graph task, set PAUSED + waiting_for=0 + paused_at. | instance_lifecycle.py |
| 2 | Rewrite `pause_instance_cascade()` | New implementation: (a) call `repo.get_tree_root_id(instance_id)` → root_id, (b) call `repo.get_tree_ids(root_id)` → tree_ids set, (c) for each id in tree_ids: call `_pause_single_node()`, (d) stream status change for each paused node, (e) return `{"paused_ids": [...], "skipped_ids": [...]}`. Process children before parents (leaves-first) to avoid partial states. | instance_lifecycle.py |
| 3 | Extract `_resume_single_node()` as a private method on the class | New method: `async def _resume_single_node(self, instance_id: str, waiting_for: int = 0) -> bool`. Logic: get meta, skip if not PAUSED, update DB to RUNNING + clear paused_at + set waiting_for. Stream status change. Return True if resumed. | instance_lifecycle.py |
| 4 | Rewrite `resume_instance_cascade()` | New implementation: (a) call `repo.get_tree_root_id(instance_id)` → root_id, (b) call `repo.get_tree_ids(root_id)` → tree_ids set, (c) determine if target is root (target == root_id), (d) if target ≠ root: call `repo.get_ancestor_ids(instance_id)` → ancestor_ids, (e) for each id in tree_ids: if id in ancestor_ids → `waiting_for=1`, else → `waiting_for=0`, call `_resume_single_node()`, (f) return `{"resumed_ids": [...], "skipped_ids": [...], "target_id": instance_id}`. **IMPORTANT**: Add `target_id` to return dict so router knows which is the target. | instance_lifecycle.py |
| 5 | Update return value contract | Both functions must include `target_id` in return dict (the originally selected instance). This is needed by the router to know which node gets `silent=False`. | instance_lifecycle.py |

## Detailed Implementation

### `pause_instance_cascade()` (new)

```python
async def pause_instance_cascade(self, instance_id: str) -> dict:
    """Pause an instance and its ENTIRE tree (root + all descendants).
    
    Steps:
    1. Find root of the tree containing instance_id
    2. Collect all IDs in the tree
    3. Pause each node (leaves-first ordering)
    
    Returns dict with:
      - paused_ids: list of all instance IDs that were paused
      - skipped_ids: list of instance IDs that were skipped (already paused, not found)
      - target_id: the originally requested instance_id
    """
    repo = self._manager._instance_repository
    
    # 1. Find root
    root_id = repo.get_tree_root_id(instance_id)
    logger.info(f"Pause requested for {instance_id[:8]}..., found root {root_id[:8]}...")
    
    # 2. Collect entire tree
    tree_ids = repo.get_tree_ids(root_id)
    logger.info(f"Pausing entire tree ({len(tree_ids)} nodes)")
    
    # 3. Pause all nodes (process children before parents for clean state)
    paused_ids = []
    skipped_ids = []
    
    # Order: children-first (reverse topological)
    # Simple approach: process all, order doesn't critically matter since we cancel everything
    for node_id in tree_ids:
        try:
            if await self._pause_single_node(node_id):
                paused_ids.append(node_id)
                # Stream status change
                meta = self._manager._instance_repository.get(node_id)
                if meta:
                    await self._manager._live_hub.stream_status_change(
                        node_id, InstanceStatus.PAUSED.value, agent_id=meta.agent_id
                    )
            else:
                skipped_ids.append(node_id)
        except Exception as e:
            logger.error(f"Failed to pause {node_id[:8]}...: {e}")
            skipped_ids.append(node_id)
    
    return {
        "paused_ids": paused_ids,
        "skipped_ids": skipped_ids,
        "target_id": instance_id,
    }
```

### `_pause_single_node()` (extracted, converted to async method)

```python
async def _pause_single_node(self, instance_id: str) -> bool:
    """Pause a single instance. Returns True if paused, False if skipped."""
    meta = self._manager._instance_repository.get(instance_id)
    
    if meta is None:
        logger.warning(f"Instance {instance_id[:8]}... not found, skipping pause")
        return False
    
    if meta.status == InstanceStatus.PAUSED.value:
        logger.info(f"Instance {instance_id[:8]}... already paused, skipping")
        return False
    
    # 1. Cancel active LLM requests
    self._manager._request_registry.cancel_by_instance(
        instance_id, CancellationReason.USER_STOPPED
    )
    
    # 2. Kill graph task
    graph_task = self._manager._graph_tasks.pop(instance_id, None)
    if graph_task and not graph_task.done():
        graph_task.cancel()
        logger.info(f"Cancelled graph task for {instance_id[:8]}...")
    
    # 3. Update DB
    paused_at = datetime.utcnow().isoformat()
    update_kwargs = {
        "status": InstanceStatus.PAUSED.value,
        "paused_at": paused_at,
    }
    if meta.waiting_for and meta.waiting_for > 0:
        update_kwargs["waiting_for"] = 0
    
    self._manager._instance_repository.update(instance_id, **update_kwargs)
    logger.info(f"Paused instance {instance_id[:8]}...")
    return True
```

### `resume_instance_cascade()` (new)

```python
async def resume_instance_cascade(self, instance_id: str) -> dict:
    """Resume an instance and its ENTIRE tree (root + all descendants).
    
    Steps:
    1. Find root of the tree containing instance_id
    2. Collect all IDs in the tree
    3. Determine ancestor chain if target ≠ root (for waiting_for propagation)
    4. Resume each node with appropriate waiting_for value
    
    waiting_for semantics:
      - Resume from root: waiting_for = 0 for ALL nodes
      - Resume from child: ancestors of target get waiting_for = 1,
        all other nodes get waiting_for = 0
    
    Returns dict with:
      - resumed_ids: list of all instance IDs that were resumed
      - skipped_ids: list of instance IDs that were skipped
      - target_id: the originally requested instance_id
    """
    repo = self._manager._instance_repository
    
    # 1. Find root
    root_id = repo.get_tree_root_id(instance_id)
    logger.info(f"Resume requested for {instance_id[:8]}..., found root {root_id[:8]}...")
    
    # 2. Collect entire tree
    tree_ids = repo.get_tree_ids(root_id)
    logger.info(f"Resuming entire tree ({len(tree_ids)} nodes)")
    
    # 3. Determine waiting_for mapping
    ancestor_ids = set()
    if instance_id != root_id:
        ancestor_ids = set(repo.get_ancestor_ids(instance_id))
        logger.info(f"Target is non-root, {len(ancestor_ids)} ancestors get waiting_for=1")
    
    # 4. Resume all nodes
    resumed_ids = []
    skipped_ids = []
    
    for node_id in tree_ids:
        try:
            waiting_for = 1 if node_id in ancestor_ids else 0
            if await self._resume_single_node(node_id, waiting_for=waiting_for):
                resumed_ids.append(node_id)
            else:
                skipped_ids.append(node_id)
        except Exception as e:
            logger.error(f"Failed to resume {node_id[:8]}...: {e}")
            skipped_ids.append(node_id)
    
    return {
        "resumed_ids": resumed_ids,
        "skipped_ids": skipped_ids,
        "target_id": instance_id,
    }
```

### `_resume_single_node()` (new method)

```python
async def _resume_single_node(self, instance_id: str, waiting_for: int = 0) -> bool:
    """Resume a single instance. Returns True if resumed, False if skipped."""
    meta = self._manager._instance_repository.get(instance_id)
    
    if meta is None:
        logger.warning(f"Instance {instance_id[:8]}... not found, skipping resume")
        return False
    
    if meta.status != InstanceStatus.PAUSED.value:
        logger.info(f"Instance {instance_id[:8]}... not paused (status={meta.status}), skipping")
        return False
    
    # Update DB
    self._manager._instance_repository.update(
        instance_id,
        status=InstanceStatus.RUNNING.value,
        paused_at=None,
        waiting_for=waiting_for,
    )
    
    # Stream status change
    await self._manager._live_hub.stream_status_change(
        instance_id, InstanceStatus.RUNNING.value, agent_id=meta.agent_id
    )
    
    logger.info(f"Resumed instance {instance_id[:8]}... (waiting_for={waiting_for})")
    return True
```

## Edge Cases

| Case | Pause Behavior | Resume Behavior |
|------|---------------|-----------------|
| Single node (no parent, no children) | Pause just that node | Resume just that node, `waiting_for=0` |
| Node already paused | Skip (add to skipped_ids) | — |
| Node not paused | — | Skip (add to skipped_ids) |
| Node has status other than PAUSED/RUNNING | Pause it anyway (if not already paused) | Skip it (only resume PAUSED nodes) |
| Deep tree (5+ levels) | All nodes paused, `waiting_for=0` | Ancestors of target get `waiting_for=1` |
| Target is root | Normal full-tree pause | All nodes `waiting_for=0` |

## Constraints

- Remove recursive DFS approach entirely — use flat set iteration
- Remove `_visited` and `_depth` parameters (no longer needed)
- Remove the `_pause_single()` closure (extracted to method)
- Keep the depth guard in repo methods (Phase 1) for safety
- Both functions remain async (for `stream_status_change` calls)
- `_pause_single_node` must remain async because `graph_task.cancel()` may need event loop (though technically it's sync, keeping async is safer and consistent)

## Deliverables

- [ ] `_pause_single_node()` extracted as async method
- [ ] `pause_instance_cascade()` rewritten for full-tree operation
- [ ] `_resume_single_node()` created as new async method with `waiting_for` parameter
- [ ] `resume_instance_cascade()` rewritten for full-tree operation with `waiting_for` propagation
- [ ] Return values include `target_id` field
- [ ] Old recursive implementation completely removed
- [ ] No `_visited`/`_depth` parameters remain
