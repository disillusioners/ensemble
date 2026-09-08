# Phase 2: Fix DEFECT 2 — Spawned-Child KV Mispartition

Date: 2026-09-07
Author: planner[v2] via plan-creation worker
Status: Draft
Worktree anchor: `agents-ensemble-wt-approver @ 2750c815` (defect verified here; re-grep anchors at kickoff)
Feature: kv-ambient-awareness-fix — child-first-turn `[SYSTEM CONTEXT: Related Project]` block must bind to the tree-root KV partition, not the child's own (empty) partition.

---

## Objective

A spawned child instance's **first runtime context assembly** (turn 1) must read the shared `shared_meta_kv` partition owned by the tree-root instance, so the persisted `[SYSTEM CONTEXT: Related Project]` block it carries to the checkpoint is populated with the parent's KV metadata (governor `council_manifest`, project-manager `pm_leader_instances`, and the five worktree-aware agent hints) instead of an empty own-partition block.

A single change at one hardcode site (the `parent_id` argument to `assemble_context_messages` at `daemon/services/instance_messaging.py:3629`) closes the defect — the existing graph-side build at `daemon/graph.py:674-684` already does the right thing and its discard at `:3873-3879` is by-design anti-double-inject, **not** a missed repair.

---

## Scope

### In Scope

- **Single touch point**: `daemon/services/instance_messaging.py:3620` (the hardcoded `_persistent_parent_id: str | None = None`) and the call site at `:3629`.
- **Comment/doc-truth update**: rewrite the misleading block-comment at `instance_messaging.py:3609-3619` to describe the actual contract after the fix (parent_id is the parent's instance id, child → tree-root resolution walks the chain). Per the doc-truth convention, stale comments that quote unreachable behavior must be corrected.
- **Comment update at graph.py:3873-3879**: no semantic change (the discard rationale remains intact and the graph-side build now becomes redundant-but-identical to the messaging-path build). Reword the comment to note that both paths now produce the same block under the same partition key, and the discard is preserved for the original anti-double-inject reason.
- **Kill-switch** (Shape B, see "Kill-switch design" below): service-module cached resolver + `_reset_*_for_tests()` helper + one-shot boot INFO log. Names `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` reserved at `daemon/constants.py` ahead of the resolver wiring.
- **Regression test**: new test that reproduces child-first-turn partitioning (mirrors the tool-path contract at `daemon/tools/shared_meta_kv_tools.py:109-122`).
- **Verification**: tool-path pins at `tests/unit/test_shared_meta_kv_tool.py` (`:84, :109, :133, :161, :194, :216` — context_key assertions) stay green; existing first-turn assembly tests stay green.

### Out of Scope

- **Stable-id scheme** (specified by sibling phase 1 worker). This phase 2 only references the scheme as a dependency and threads the id format through `_make_context_message` for the first-turn block. The scheme itself is not invented here.
- **DEFECT 1** (cadence/refreshing of the ambient block) — depends on phase 2 (refresh of a mispartitioned block is meaningless), but is planned separately.
- **DEFECT 3** (suppression for system-default projects) — depends on phase 2 (suppression's new KV host must bind the correct tree-root key), but is planned separately.
- **API GET /messages read path** (`persistence.py:852-947`) — already passes real `parent_id` at `:899/:921`. Verified correct today; no change.
- **Tool path** (`daemon/tools/shared_meta_kv_tools.py:109-122`) — already uses `get_tree_root_id` correctly. Verified correct today; no change.
- **graph.py:674-684** ContextSlot assemble — already passes `self._parent_id` correctly. The discard at `:3873-3879` is intentional; do NOT un-discard.
- **Agent prompt files** under `agents/{name}/` — daemon fix must NOT touch prompts (worktree-aware prompts ship-byte-identity fence per `.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34`). No agent prompt may describe the mechanism.
- **Other env flag precedents** that ship with default-OFF polarity (e.g. `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` reserved-unused, `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`) — different risk class; not relevant to this defect's polarity decision.

---

## Root Cause

A spawned child's first runtime context assembly hardcodes `parent_id=None`, so it computes the KV `context_key` as its OWN instance id instead of the tree-root partition where the parent writes. The correctly-configured graph-side rebuild is then DISCARDED.

### Mechanism — file:line + quoted conditions

1. **Hardcode** at `daemon/services/instance_messaging.py:3620`:
   ```python
   _persistent_parent_id: str | None = None
   ```
   The block-comment at `:3609-3619` claims:
   > "The orchestrator treats `parent_id=None` as 'tree-root instance' … children inherit the same persistent context as their root via the tree-root resolution inside the orchestrator."
   The comment describes a resolution the `None` input makes UNREACHABLE. At `:3629`:
   ```python
   parent_id=_persistent_parent_id,
   ```
   the literal `None` flows through.

2. **Short-circuit** at `daemon/services/context_messages.py:949-950`:
   ```python
   if parent_id is None:
       return instance_id
   ```
   `_resolve_tree_root_id` returns the child's own id → `context_key = child's own id` → `_fetch_kv_metadata` (`context_messages.py:964-997`) reads the child's own effectively-empty partition at `daemon/services/context_messages.py:1342-1345` (the `_fetch_kv_metadata` call site inside `assemble_context_messages`).

3. **Discarded correct repair** at `daemon/graph.py:3866-3879`:
   ```python
   _persistent_msgs, ephemeral_context_msgs = await context_slot.assemble(
       instance_id, user_query, project_id
   )
   # ``_persistent_msgs`` is intentionally discarded —
   # the messaging path already prepended those messages
   # to ``graph_input`` ...
   _ = _persistent_msgs
   ```
   `context_slot.assemble` (`graph.py:674-684`) is called with `parent_id=self._parent_id` (line 681), so the graph-side build computes the CORRECT tree-root partition. The discard is **by-design anti-double-inject** — the messaging path's first-turn output is already prepended to `graph_input` and arrives at this node via `list(messages)`. The fix MUST fix the **INPUT** (the `None`), not un-discard the graph-side build.

4. **Correct resolution already exists** at `daemon/repositories/instance/repository.py:431`:
   ```python
   def get_tree_root_id(self, instance_id: str) -> str | None:
       ...
       for _ in range(_MAX_TRAVERSAL_DEPTH):
           instance = db_session.get(Instance, current_id)
           if instance is None:
               return None
           if instance.parent_id is None:
               return current_id
           current_id = instance.parent_id
       return None
   ```
   The TOOL path uses it correctly at `daemon/tools/shared_meta_kv_tools.py:109-122`:
   ```python
   try:
       context_key = manager._instance_repository.get_tree_root_id(current_instance_id)
       if not context_key:
           context_key = current_instance_id
   except Exception as e:
       logger.warning(...)
       context_key = current_instance_id
   ```
   **Defect is confined to the runtime `[SYSTEM CONTEXT]` build.**

### Minimal-fix fact

The instance row is **already fetched** at `instance_messaging.py:3599-3601`:
```python
_proj_row = await asyncio.to_thread(
    self._manager._instance_repository.get, instance_id
)
if _proj_row is not None and _proj_row.instance_metadata:
    _persistent_project_id = (
        _proj_row.instance_metadata.get("project_id")
    )
```
The `_proj_row.parent_id` field can be threaded as `parent_id` (one extra attribute access; no extra repo round-trip), letting `_resolve_tree_root_id` walk the parent chain.

The API read path (`persistence.py:897-921`) is the model for the fix:
```python
instance_meta = ctx["instance_meta"]
project_id = getattr(instance_meta, "project_id", None)
parent_id = getattr(instance_meta, "parent_id", None)
...
persistent_msgs, ephemeral_msgs = await assemble_context_messages(
    ...
    parent_id=parent_id,
)
```
The graph-side build (`graph.py:681`) is the second model: `parent_id=self._parent_id`.

---

## Fix Approach

### Chosen design — thread real `parent_id` from the already-fetched instance row

**Decision: Option A** (thread real `parent_id` rather than Option B which would pre-resolve the root and pass it as a different kwarg).

At `instance_messaging.py:3620`, change:
```python
_persistent_parent_id: str | None = None
```
to:
```python
_persistent_parent_id: str | None = None
try:
    if _proj_row is not None:
        _persistent_parent_id = _proj_row.parent_id
except Exception:  # pragma: no cover - defensive
    _persistent_parent_id = None
```
(The existing `_proj_row` from the `project_id` block at `:3599-3607` is in scope. The block-comment above is rewritten to describe the real contract.)

The `_resolve_tree_root_id` ladder at `context_messages.py:920-961` then walks the chain via `get_tree_root_id(parent_id)` (line 953) and returns the tree root. Three call sites now agree on the contract:
- `graph.py:681` — `parent_id=self._parent_id`
- `persistence.py:899, 921` — `parent_id = getattr(instance_meta, "parent_id", None)`
- `instance_messaging.py` (this fix) — `parent_id = _proj_row.parent_id` (when `_proj_row is not None`)

### Alternatives considered

- **Option B**: pre-resolve the tree root at the messaging path by calling `get_tree_root_id(instance_id)` directly and pass the resolved root as a new `context_key` kwarg to `assemble_context_messages`. **Rejected** because:
  1. It requires a new public kwarg on `assemble_context_messages` (a 7-keyword call already in `graph.py:674-684` and `persistence.py:914-925`). The facade-forwarding discipline (`daemon/manager.py`) means every existing caller would need to be audited; high blast radius for a one-line fix.
  2. It duplicates the `_resolve_tree_root_id` ladder (parent_id=None → self fallback, exception → parent_id fallback) in two places.
  3. The exception-path semantics are simpler if `parent_id` is the canonical input: today's behavior (`parent_id=None` → own partition) is unchanged for roots; today's defect (child reads own partition) is closed because `parent_id` is now set.
- **Option C**: skip the `parent_id` lookup and call `get_tree_root_id(instance_id)` directly at `instance_messaging.py:3620` to compute `parent_id = tree_root_id`. **Rejected** because:
  1. `_resolve_tree_root_id` already does this walk; we'd compute the same value twice (one extra DB round-trip per first turn, across all spawned children).
  2. The exception-path contract (defensive `try/except` + fall back to `parent_id`) already exists in `_resolve_tree_root_id`; replicating it is error-prone.

### Exception-path behavior

| Scenario | Today's behavior (None input) | After fix (real parent_id) |
|---|---|---|
| Root instance (`_proj_row.parent_id is None`) | `context_key = instance_id` ✓ | `context_key = instance_id` ✓ (unchanged) |
| Child with valid parent_id | `context_key = child_id` ✗ (reads own empty partition) | `context_key = get_tree_root_id(parent_id)` ✓ (reads tree-root partition) |
| `_proj_row` is `None` (transient race / missing instance) | `context_key = instance_id` (correct for missing-row, defensive) | `context_key = instance_id` (correct for missing-row, defensive — the new `try/except` around `_proj_row.parent_id` returns `None`, identical to today) |
| `get_tree_root_id` raises (DB error) | n/a — `_resolve_tree_root_id` returns `instance_id` via the `if parent_id is None` short-circuit | `_resolve_tree_root_id` returns `parent_id` via the `except Exception` ladder at `:954-958` (defensive fall back — better than today's hard-coded own partition) |
| `get_tree_root_id` returns `None` (orphan chain, parent deleted) | n/a | `_resolve_tree_root_id` returns `parent_id` at `:961` (better than today's hard-coded own partition) |
| `_proj_row.parent_id` extraction raises | n/a (literal `None`) | `try/except` returns `None` → identical to today (defensive) |

**Net: no regression in any exception path.** Today's `None` default becomes a measured fallback after the explicit `_proj_row.parent_id` extraction. All defensive fallbacks land on a value that is at least as correct as today's.

### Compliance with shared stable-id scheme (phase1-plan.md §Shared prerequisite)

The first-turn block this fix emits flows through `_make_context_message` (`context_messages.py:85-111`), which mints a `uuid4` per call at line 109. Per the message-id invariant in the Core Architecture blueprint:
- Re-emitted blocks under new ids get **APPENDED** by `add_messages`, not replaced.
- Id-less messages are dropped by `MessageTapSlot` / get moving-timestamp fallbacks (`persistence.py:527-528`).

For DEFECT 2 specifically: the first-turn block **just needs to use the stable-id format** that phase1 specifies (so defect 1's supersede semantics land cleanly later). Precedents:
- Auto-load block stable-id supersede at `context_messages.py:1273-1279` (filtered rebuild under stable id so `add_messages` supersedes the stale block).
- API scheme `synthetic-context-{kind}-{instance_id}-{idx}` at `persistence.py:943`.

**Dependency**: phase 2 BLOCKS on phase 1's stable-id scheme spec being available at implementation kickoff. If phase1's scheme is not yet committed when phase 2 implementation begins, halt and flag to dispatcher. Do not invent a competing format.

### Comment/doc-truth updates

Per the prompt-writing convention (`PROCESS: pin destructive-copy by RENDER path`) and doc-truth discipline (stale comments quoting unreachable behavior must be corrected), update:

1. **`instance_messaging.py:3609-3619`** — rewrite to:
   > "Resolve `parent_id` for tree-root resolution. The instance row was just fetched above (`_proj_row = await asyncio.to_thread(self._manager._instance_repository.get, instance_id)`); mine its `parent_id` field so `_resolve_tree_root_id` can walk to the tree root via `get_tree_root_id(parent_id)`. A root instance (`_proj_row.parent_id is None`) → `_resolve_tree_root_id` short-circuits to `context_key = instance_id` (correct, root reads its own partition). A child instance (`_proj_row.parent_id` set) → `get_tree_root_id` walks the chain to the tree root and reads the parent's KV partition (matches the tool-path contract at `shared_meta_kv_tools.py:109-122`). Defensive `try/except` around the attribute access so a transient row-fetch error cannot strand the call at `parent_id=None` (which would silently re-introduce the defect — see architect c5ae6d95 §1a/§6)."

2. **`graph.py:3873-3879`** — reword (no semantic change) to:
   > "`_persistent_msgs` is intentionally discarded — the messaging path already prepended those messages to `graph_input` (and they now live in `state['messages']` from the checkpoint), so they arrive at this node via `list(messages)` below. Reading them again here would double-inject. Both paths (messaging-path first-turn build at `instance_messaging.py:3573-3657` and this graph-side rebuild) now compute the **same partition** under the **same stable-id format** specified by phase1, so the discard is purely about input de-duplication, not about partition mismatch."

No other doc updates required. The Core Architecture blueprint's `[SYSTEM CONTEXT]` block already describes the data flow correctly; no in-doc fix needed there.

---

## Kill-Switch Design

### Name

`ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT`

- Pattern matches registry discipline: feature flag name in CAPS_SNAKE_CASE.
- Must be reserved at `daemon/constants.py` (per `constants.py:594-624` registry discipline — reserved-unused names must NOT appear in `config.py`; the binding lands with the resolver wiring).
- Must NOT collide with reserved names listed in the registry section.

### Shape

**Shape B** — service-module cached resolver + `_reset_*_for_tests()` + one-shot boot INFO log.

Rationale:
- The defect is a pure correctness bug with **zero perf cost** to enabling. There is no knob to tune — either the partition is correct (ON) or wrong (OFF, today's behavior).
- Shape A (pydantic field + `_resolve_*` in `load_config`) is reserved for features with bool-vocab inversion concerns or per-deploy tunability (exemplar: `ENSEMBLE_PROACTIVE_COMPACTION`). This fix has neither.
- Shape B keeps the revert path minimal: operator sets `=0`, restarts, and ambient reverts to today's stale-partition behavior — clean escape hatch for incident response.

Implementation pattern (mirroring `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` at `daemon/services/job_recovery_service.py:233-334` and `ENSEMBLE_WC_WAKE_ENQUEUE` at `daemon/services/instance_messaging.py:110-197`):

```python
# daemon/services/instance_messaging.py (top-of-file)
_CONTEXT_PERSISTENT_KV_TREE_ROOT_ENV: str = (
    "ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT"
)

def _resolve_context_persistent_kv_tree_root_enabled() -> bool:
    """Cached resolver for the persistent-context tree-root partition fix.
    ...
    """
    cached = getattr(_CONTEXT_PERSISTENT_KV_TREE_ROOT_RESOLVER, "value", _UNSET)
    if cached is not _UNSET:
        return cached  # type: ignore[return-value]
    raw = os.environ.get(_CONTEXT_PERSISTENT_KV_TREE_ROOT_ENV, "1").strip().lower()
    enabled = raw not in ("0", "false", "no", "off", "")
    _CONTEXT_PERSISTENT_KV_TREE_ROOT_RESOLVER.value = enabled
    logger.info(
        f"[ContextPersistentKV] ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT "
        f"resolver: {enabled} (env={raw!r}, default ON)"
    )
    return enabled

def _reset_context_persistent_kv_tree_root_for_tests() -> None:
    _CONTEXT_PERSISTENT_KV_TREE_ROOT_RESOLVER.value = _UNSET
```

The `parent_id`-threading block at `:3597-3629` is wrapped in the flag:
```python
if _resolve_context_persistent_kv_tree_root_enabled():
    # New path: thread real parent_id from the already-fetched _proj_row
    try:
        if _proj_row is not None:
            _persistent_parent_id = _proj_row.parent_id
    except Exception:  # pragma: no cover - defensive
        _persistent_parent_id = None
else:
    # Pre-fix behavior, byte-identical pinned regression (see test plan)
    _persistent_parent_id = None
```

### Polarity — Default ON, =0 disables

| Precedent | Fix type | Polarity | Shape |
|---|---|---|---|
| `ENSEMBLE_PROACTIVE_COMPACTION` | Compaction ladder (perf + correctness) | Default ON, =0 disables | A |
| `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` | Autopromote correctness | Default ON, =0 disables | B |
| `ENSEMBLE_WC_WAKE_ENQUEUE` | WC→injection wake | Default OFF, operator flips ON after soak | B |
| `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` | Governor spawn-recursion | Default OFF (revert path) | B |

**Decision: Default ON, `=0` disables** — same polarity as `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` and `ENSEMBLE_PROACTIVE_COMPACTION`.

Rationale:
1. **Pure correctness bug, no perf cost**. Today's ambient = always stale (silent mispartition). ON → ambient always fresh + correct. There is no "feature flag for correctness" justification; OFF = "preserve today's bug" which is the wrong default for prod.
2. **Defect 3 (suppression) depends on it**. If defect 3 ships first without defect 2, its suppression is based on a wrong partition key → silent wrong behavior (suppressing the wrong block).
3. **Reversibility**. If the fix misfires, `=0` reverts to today's behavior in one restart. Operator has a clean escape path identical to the `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` revert.
4. **Match the closest precedent** — `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` is a correctness fix that also gates an autopromote correctness class with no perf knob; default ON with `=0` disable is the established pattern.
5. **`ENSEMBLE_WC_WAKE_ENQUEUE` is the wrong precedent** — that flag's default OFF is because it changes the failure mode (WC→injection wake changes which entity owns the bus callback); it has a non-trivial risk of misfire. This fix changes no failure mode; it changes which partition is read. ON is the right default.

### Boot log

One-shot INFO line at first resolver call:
```
[ContextPersistentKV] ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT resolver: True (env='1', default ON)
```
(Exemplar: `daemon/manager.py:797-806` and `daemon/api.py:535-540` for boot INFO pattern; `daemon/services/instance_messaging.py:179` for `ENSEMBLE_WC_WAKE_ENQUEUE` resolver INFO.)

### Restart-to-flip

Per the Shape B convention: setting `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT=0` and restarting the daemon reverts to today's behavior. No DB migration; no schema change.

---

## Test Strategy

### 1. NEW regression test — child first turn must read tree-root partition

**File**: `tests/unit/test_instance_messaging_first_turn_kv_partition.py` (NEW).

**Setup** (mirrors `test_governor_recursion_acceptance_walk.py:771-839` for real-service + real-spawn):
- Real `Config` + real `InstanceManager` + real `_instance_repository` (file-backed SQLite tmp_path + NullPool + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` per `test_job_driven_enqueue_work_id_facade.py:76-95`).
- Spawn a parent instance via `manager.spawn_instance` (real spawn path).
- Spawn a child instance via `manager.spawn_instance(..., parent_id=parent_id)`.
- Set a sentinel KV entry on the parent's tree-root partition via `repo.set(parent_id, "council_manifest", {...})`.
- Invoke the child's first turn via `send_message` (real messaging path).
- Capture `persistent_context_msgs` from the child's first-turn assembly (via a hook or by reading the persisted checkpoint).

**Assertions**:
- `len(persistent_context_msgs) >= 1` (the project context message).
- The serialized project message contains the sentinel KV payload (the parent's `council_manifest` value).
- It does NOT contain a fresh-empty project block (no mispartitioning).

**Worktree-based regression proof** (per "Worktree-Based Regression Proof" convention):
1. Implement the test.
2. Create a worktree at pre-fix commit (the current anchor `2750c815` BEFORE the fix lands): `git worktree add /tmp/wt-pre-fix 2750c815`.
3. Copy ONLY the new test file (`test_instance_messaging_first_turn_kv_partition.py`) into the worktree. Do NOT copy the fix.
4. Run the test in the pre-fix worktree → **must FAIL with exact symptom**: the persistent block carries an empty partition (no sentinel KV value), confirming the defect.
5. Restore the test in the post-fix branch → must PASS.
6. Document the pre-fix failure mode in the test docstring ("Regression proof: this test fails on commit 2750c815 with empty partition; passes after fix.").

### 2. flag-ON real-service test

**File**: `tests/integration/test_instance_messaging_tree_root_kv_flag.py` (NEW).

Exercises the resolver through the real service (not mocked), with `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT=1`:
- Spawn parent + child.
- Override `self._manager._instance_repository.get_tree_root_id` via monkeypatch to return a deterministic root id ("root-test-001") and assert the child's persistent block is built under that key.
- Reset the resolver cache via `_reset_context_persistent_kv_tree_root_for_tests()` per test setup (matches `_reset_wc_wake_enqueue_for_tests` pattern at `instance_messaging.py:192`).

### 3. flag-OFF byte-identical pin

**File**: `tests/unit/test_instance_messaging_kv_tree_root_pin.py` (NEW).

With `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT=0`, verify:
- The `_persistent_parent_id` value at the `assemble_context_messages` call site is `None`.
- The resulting `context_key` (via `_resolve_tree_root_id`) is `instance_id`.
- The persistent block is byte-identical to the pre-fix output for the same inputs.
- Pattern mirrors `tests/unit/services/test_proactive_compaction_fix_p1b.py:560-585` (paired shape p2:514-541).

### 4. Tool-path pins stay green

**No changes** to `tests/unit/test_shared_meta_kv_tool.py` — its context_key assertions at `:84, :109, :133, :161, :194, :216` exercise the tool path, which is unchanged. Run the full tool-path test as part of CI to confirm no regression.

### 5. Existing first-turn assembly tests stay green

- `tests/unit/test_context_messages.py:1054-1066` — orchestrator-level first-turn test with explicit `parent_id=`. Must stay green.
- Any other test under `tests/unit/test_context_messages.py` and `tests/unit/test_instance_messaging.py` that exercises first-turn context assembly.
- The fix changes the runtime path **only**; the orchestrator-level test pattern (explicit `parent_id=`) is unchanged.

### 6. DB recipe for write surfaces

File-backed SQLite `tmp_path` + `NullPool` + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` — exemplar `tests/integration/test_job_driven_enqueue_work_id_facade.py:76-95`, `tests/test_wc_wake_pure_hang.py:235-254`, `tests/test_n3_per_kind_filter_pin.py:76-96`.

**Do NOT** copy the in-memory `StaticPool` pattern from existing `shared_meta_kv` repo tests — that pattern masks DB-level bugs and would hide the partition-correctness contract (the fix is about which row is read, not how the repo is mocked).

### 7. Bug-exercising proof

Per the test convention: temporarily revert the parent_id threading (revert the `if _resolve_..._enabled(): ... else: ... = None` block to a hardcoded `_persistent_parent_id = None`) → new regression test fails with the exact symptom (empty partition read) → restore. Document the failure mode in the test docstring.

---

## Rollout

### Branch and anchor re-pin

1. At implementation kickoff, **re-grep all anchors** in the implicated file set against `main` (the externally-owned main checkout) and `latest` (the deploy target). The task notes 3 newer commits on the implicated file set since the `2750c815` worktree anchor:
   - `bb4e3e89` (injected-notes-absorb arc)
   - `4e1e6698`
   - `c2142c69`
   - `ENSEMBLE_INJECTED_NOTES_ABSORB` env flag
   These may have touched `instance_messaging.py:3573-3657` or `context_messages.py:1134-1360`. **Re-verify line numbers at kickoff**; the fix design is robust to line drift (only the `:3620` hardcode and `:3629` call site are touched), but the line numbers in this plan may be stale.

2. Branch from `latest` (not from the worktree anchor): `feature/fix-kv-ambient-child-partition` off `origin/latest`.

### Implementation order

Per the coupling-derived sequencing (see "Sequencing inputs"):
1. Wait for phase 1 (stable-id scheme) to be committed (this plan BLOCKS on phase 1's stable-id scheme spec being available).
2. Implement the parent_id threading + kill-switch wiring on the new branch.
3. Write the three new tests + verify tool-path pins stay green + verify existing first-turn tests stay green.
4. Run worktree-based regression proof (pre-fix worktree test fails; post-fix branch test passes).
5. Run full test suite.

### Restart-to-flip

Per Shape B: ship the fix with the resolver defaulting to ON; no operator action required at deploy. If a misfire is detected post-deploy, `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT=0` + restart reverts to today's behavior.

### Post-deploy verification

1. **Boot log check**: confirm `[ContextPersistentKV] ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT resolver: True (env='1', default ON)` appears once in the daemon log at startup.
2. **Smoke test** (manual, scripted into the rollout runbook): spawn a leader → spawn a worker child via `send_message`. In the child's first-turn checkpoint, inspect the `[SYSTEM CONTEXT: Related Project]` block — it must contain the leader's KV metadata (e.g. `council_manifest` if the leader has populated one), not an empty block.
3. **Regression check**: tool-path tests (`tests/unit/test_shared_meta_kv_tool.py`) and existing first-turn assembly tests stay green in CI.
4. **Negative test**: temporarily flip `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT=0` in a staging daemon → confirm the child's persistent block reverts to empty partition (byte-identical to pre-fix). Flip back to `1` for prod.

---

## Risks + Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | `get_tree_root_id` returns wrong root due to cycle self-parenting | High (children read wrong partition → silent stale block) | Low (depth cap at `_MAX_TRAVERSAL_DEPTH=256` per `repository.py:33`; visited set is the truthful result) | Pre-existing safety in the repo; no new fix needed. If a misfire is observed, the kill-switch reverts. |
| 2 | Race between spawn and first turn where `parent_id` changes | Low (a child re-parented mid-flight reads a different root) | Very low (parent_id is set at spawn and rarely mutated) | Mitigated by depth cap + the defensive `_resolve_tree_root_id` exception ladder. If observed, the kill-switch reverts. |
| 3 | Exception in the `_proj_row.parent_id` extraction at `:3599-3607` | Low (silent reversion to `None` → re-introduces the defect for that turn) | Low (the row is already fetched and the attribute access is plain attribute lookup on a known shape) | Wrapped in `try/except Exception` (matching the existing defensive pattern at `:3606-3607`). If the exception rate climbs, the kill-switch reverts prod-wide. |
| 4 | The discard at `graph.py:3873-3879` changes in a future commit and conflicts with this fix | Low (breaks the anti-double-inject contract → double-injected block) | Low (discard is intentional, doc-pinned, and reviewed in the worktree-aware-prompts work) | Comment at `:3873-3879` now notes the partition-equivalence contract; reviewers must preserve it. |
| 5 | Phase 1 stable-id scheme not yet specified at implementation kickoff | High (no compatible id format for the first-turn block) | Medium (phase 1 is a sibling worker; timing-dependent) | Halt and flag to dispatcher if phase1 not committed. Do not invent a competing format — reference phase1-plan.md §Shared prerequisite explicitly in the implementation kickoff checklist. |
| 6 | Kill-switch flag accidentally OFF by config change (operator sets `=0` and forgets) | Medium (ambient silently stale — same as today's defect) | Low (Shape B default ON; off is the explicit opt-out) | Boot INFO log line at startup makes the active mode visible. Post-deploy verification checklist confirms ON. |
| 7 | Pin-test rebase churn on the 3 newer commits (`bb4e3e89/4e1e6698/c2142c69`) | Medium (line numbers in this plan drift) | Medium (each commit may touch `instance_messaging.py`) | Re-grep all anchors at kickoff; fix design is robust to line drift (only `:3620` hardcode + `:3629` call site + the comment block are touched). Document actual final line numbers in the PR description. |
| 8 | Existing test using a manually-mocked `parent_id` path breaks under the new threading | Low (a test that asserts "parent_id is None" would now fail) | Very low (the only orchestrator-level test at `test_context_messages.py:1054-1066` passes explicit `parent_id=`; no test asserts the messaging-path hardcoded `None` is preserved) | Run full test suite before merge. If a test breaks, decide per-test: update test or update fix. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Child instance first turn reads tree-root KV partition | New `test_instance_messaging_first_turn_kv_partition.py` (real spawn + real send_message) | Sentinel KV value from parent's partition appears in child's persistent block; not in child's empty partition |
| 2 | Pre-fix worktree regression proof | Worktree at `2750c815` + only new test file copied in | Test fails with exact symptom (empty partition); documented in test docstring |
| 3 | Post-fix worktree passes | Branch + fix + new test file | Test passes |
| 4 | Tool-path pins stay green | `tests/unit/test_shared_meta_kv_tool.py:84, :109, :133, :161, :194, :216` | All 6 context_key assertions pass |
| 5 | Existing first-turn assembly tests stay green | `tests/unit/test_context_messages.py` (full file), `tests/unit/test_instance_messaging.py` (full file) | No regression |
| 6 | Kill-switch OFF reverts to today's byte-identical behavior | New `test_instance_messaging_kv_tree_root_pin.py` | `parent_id=None`, `context_key=instance_id`, persistent block byte-identical to pre-fix for same inputs |
| 7 | Boot log shows resolver mode | Daemon startup log | Single INFO line `[ContextPersistentKV] ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT resolver: True/False (env=..., default ON)` |
| 8 | No agent prompt file touched | `git diff agents/` after merge | Empty diff |
| 9 | Stable-id scheme from phase 1 is used by the first-turn block | Code review of `_make_context_message` call site for the persistent block in `instance_messaging.py:3573-3657` | First-turn block id matches phase1's format (no competing format invented) |
| 10 | Post-deploy smoke test passes | Manual scripted check: leader spawns worker child → worker's first-turn checkpoint contains leader's KV metadata | Block populated, not empty |

---

## Sequencing Inputs

This phase is the **first implementer** after the stable-id scheme prerequisite (phase 1).

### Inputs consumed

- **Phase 1 (stable-id scheme, sibling worker)**: The deterministic id format to be used by the first-turn block my fix emits. Specified in phase1-plan.md §Shared prerequisite. **BLOCKS phase 2 implementation** — if phase1 is not yet committed at kickoff, halt and flag to dispatcher. Reference (do not redefine): the format must support the supersede semantics that defect 1 (cadence) will land with later.
- **Defect verification** (architect c5ae6d95 §1a/§6, 2026-09-06): provides the verified root-cause analysis. Read in full before implementing.
- **Worktree-aware prompts verification summary** (`.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34`): the byte-identity fence that constrains phase 2 to NOT touch `agents/` prompt files.

### Outputs produced (consumed by later phases)

- **Phase 3 (defect 3 — suppression for system-default projects)**: depends on phase 2 because suppression's new KV host must bind to the correct tree-root key (`context_key = get_tree_root_id(parent_id)`). Without phase 2, suppression would bind to the wrong key (own partition for children) and silently suppress the wrong block.
- **Phase 1 (defect 1 — cadence/refreshing of the ambient block)**: depends on phase 2 because refreshing a mispartitioned block would only make wrong content fresher. Cadence is meaningless until the partition is correct.

### Coupling-derived implementation order

```
phase1 (stable-id scheme) → phase2 (YOU, partition correctness) → phase3 (suppression) → phase1-cadence (defect 1)
```

All three fix sites share the `assemble_context_messages` KV path (`context_messages.py:1319-1360`) and the runtime injection seam (`instance_messaging.py:3573-3657`). Sequencing reflects dependency, not priority.

### Branch and dependency hygiene

- Do not branch off the `2750c815` worktree — branch off `origin/latest` at kickoff and re-grep anchors. The worktree is read-only context; the main checkout is externally owned.
- Coordinate with phase 1 worker: confirm the stable-id scheme spec is committed BEFORE phase 2 implementation begins. If phase 1's spec lands mid-phase-2, halt and re-anchor the implementation against the committed spec.
- Do not land phase 2 if phase 1's stable-id scheme is not yet committed — the first-turn block my fix emits MUST use the stable-id format to support defect 1's supersede semantics later.

---

## Open Questions

1. **Phase 1 stable-id scheme spec**: when does phase 1 commit? (Dispatcher adjudication required if phase 2 implementation begins before phase 1 is committed.) The fix design references the scheme as a dependency; without it, the first-turn block id is uuid4 (current behavior at `context_messages.py:109`), which would still close the defect but would block defect 1's supersede semantics.
2. **System-default project behavior post-fix**: today the system-default project is suppressed from KV reads (`is_system_default` check at `context_messages.py:1331-1342`). For a system-default ROOT, `context_key = root_id` and `_fetch_kv_metadata` is skipped — correct. For a system-default CHILD (depth ≥ 1, parent is system-default root), the fix threads `parent_id = root_id` and `_resolve_tree_root_id` walks to the root → `context_key = root_id` → `_fetch_kv_metadata` is skipped (correct — system-default suppression is by project_id, not by partition). Confirm no edge case where a non-system-default child reads a system-default parent's KV partition. (Belief: no edge case — system-default is per-project, not per-partition.)
3. **Phase 3 timing**: when does phase 3 (suppression) land? If phase 3 lands before phase 2, its suppression must use `parent_id`-threaded `context_key` from the start — flag to phase 3 planner to confirm.
