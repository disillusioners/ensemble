# Phase 1: Repository Tree Helpers

## Objective

Add three tree-traversal methods to `InstanceRepository` that enable the cascade functions to discover the full tree from any node.

## Coupling

- **Depends on**: None
- **Coupling type**: independent (foundation layer)
- **Shared files with other phases**: `daemon/repositories/instance/repository.py` (read by Phase 2)
- **Shared APIs/interfaces**: The 3 new public methods are called by Phase 2

## Context

Currently, the repository has `_load_children()` and `_enrich_instance()` but NO upward traversal (ancestors, root). The cascade functions need to:
1. Find the root from any node (for "operate on entire tree")
2. Get all descendants of the root (for "entire tree" enumeration)
3. Get ancestors of any node (for `waiting_for` propagation on resume)

## Key File

- `daemon/repositories/instance/repository.py`

### Existing relevant methods

```python
# Line 45-50: Loads child IDs from instance_hierarchy table
def _load_children(self, db_session, instance_id) -> list[str]

# Line 52-58: Enriches instance with children field
def _enrich_instance(self, db_session, instance) -> Instance | None

# Line 169-174: Gets direct children via parent_id field
def get_children(self, instance_id) -> list[Instance]

# Line 176-182: Gets parent by following parent_id
def get_parent(self, instance_id) -> Instance | None
```

### Data model facts

- `Instance.parent_id` — direct parent reference (str | None, indexed)
- `InstanceHierarchy` junction table — `(parent_id, child_id)` composite PK
- `_enrich_instance()` populates `children` from hierarchy table as `list[str]`
- Children in the model: `meta.children` after enrichment is `list[str]`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `get_tree_root_id(instance_id)` | Walk `parent_id` chain upward until `parent_id is None`. Return the root instance_id. Use a loop (not recursion) with depth guard (max 64). | repository.py |
| 2 | Add `get_tree_ids(root_id)` | BFS/DFS from root, collecting all descendant IDs via `instance_hierarchy` table. Returns `set[str]` containing root_id + all descendants. Use iterative approach with queue. | repository.py |
| 3 | Add `get_ancestor_ids(instance_id)` | Walk `parent_id` chain upward, collecting all ancestor IDs. Returns `list[str]` ordered from parent → root (nearest first). Does NOT include the instance itself. Use a loop with depth guard. | repository.py |
| 4 | Add tests for all 3 methods | Test single node (no parent/children), chain of 3 (grandparent → parent → child), multi-branch tree (root with 2 children, each with their own children) | tests/ |

## Method Signatures

```python
def get_tree_root_id(self, instance_id: str) -> str:
    """Find the root instance_id by walking parent_id chain upward.
    Returns the instance_id itself if it has no parent (is already root).
    Raises ValueError if instance not found or depth exceeds 64.
    """

def get_tree_ids(self, root_id: str) -> set[str]:
    """Get all instance IDs in the tree rooted at root_id.
    Returns set containing root_id + all descendants (transitive).
    Uses BFS via instance_hierarchy table.
    """

def get_ancestor_ids(self, instance_id: str) -> list[str]:
    """Walk parent_id chain upward, returning ancestor IDs.
    Returns list ordered from parent → root (nearest first).
    Returns empty list if instance has no parent.
    Does NOT include instance_id itself.
    """
```

## Implementation Notes

### `get_tree_root_id`
```python
def get_tree_root_id(self, instance_id: str) -> str:
    current_id = instance_id
    depth = 0
    while depth < 64:
        instance = self.get(current_id)
        if instance is None:
            raise ValueError(f"Instance {current_id} not found while finding root")
        if instance.parent_id is None:
            return current_id  # This is the root
        current_id = instance.parent_id
        depth += 1
    raise ValueError(f"Max depth exceeded finding root for {instance_id}")
```

### `get_tree_ids`
```python
def get_tree_ids(self, root_id: str) -> set[str]:
    result = {root_id}
    queue = [root_id]
    while queue:
        current_id = queue.pop(0)
        children = self._load_children_direct(current_id)  # needs a session
        for child_id in children:
            if child_id not in result:
                result.add(child_id)
                queue.append(child_id)
    return result
```
Note: `_load_children` requires a db_session. Either create a session inside `get_tree_ids` (like other public methods do), or extract the SQL query. Follow the existing pattern in the repository.

### `get_ancestor_ids`
```python
def get_ancestor_ids(self, instance_id: str) -> list[str]:
    ancestors = []
    current_id = instance_id
    depth = 0
    while depth < 64:
        instance = self.get(current_id)
        if instance is None:
            break
        if instance.parent_id is None:
            break
        ancestors.append(instance.parent_id)
        current_id = instance.parent_id
        depth += 1
    return ancestors
```

## Constraints

- All methods must follow existing repository patterns (sync, use sessions from session_factory)
- No external dependencies beyond what's already imported
- Depth guards to prevent infinite loops from corrupt data

## Deliverables

- [ ] `get_tree_root_id()` implemented and tested
- [ ] `get_tree_ids()` implemented and tested
- [ ] `get_ancestor_ids()` implemented and tested
- [ ] Unit tests covering single-node, chain, and multi-branch trees
