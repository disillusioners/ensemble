# Phase 2: DEFECT 2 — Spawned-Child KV Mispartition — FIXED AT BASE; this phase VERIFIES AND PINS (C1′)

Date: 2026-09-07 (revision pass 2026-09-08 — C1 re-adjudication per decisions.md **D12**)
Author: planner[v2] via plan-creation worker; revision: plan worker per dispatcher adjudication
Status: Draft — revised (re-scope: verify-and-pin; design-of-record = the landed diff)
Worktree anchor: `agents-ensemble-wt-approver @ 2750c815` (defect verified here); **re-anchored by grep @ 9926bca0** — see decisions.md D13 for the verified-current anchor table
Feature: kv-ambient-awareness-fix — child-first-turn `[SYSTEM CONTEXT: Related Project]` block must bind to the tree-root KV partition, not the child's own (empty) partition.

> **RE-SCOPE HEADER (D12 — dispatcher adjudication, recorded verbatim in decisions.md).** Defect 2 is **ALREADY FIXED at base**: commit `80bb61dd` (merged via `36a46b01`) deleted the hardcoded `_persistent_parent_id=None` and threads `_proj_row.parent_id` (landed block: instance_messaging.py:3671-3686, call `:3695`), shipping `tests/services/test_instance_messaging_parent_resolution.py` (322 lines, 3 tests). Behavioral equivalence and exception-ladder parity are CONFIRMED; the flag-wrap is REJECTED as negative-value; `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` is **RETIRED** (no resolver, no boot log, no OFF pin; struck from C0's registry pre-reservation). This phase re-scopes to **C1′ VERIFY-AND-PIN**: (i) the equivalence verification is recorded (D12); (ii) a coverage audit of the landed tests closes the confirmed gaps with new pins — **tests-only, no daemon code**; (iii) the flag OFF-pin premise is dropped.

---

## Objective

A spawned child instance's **first runtime context assembly** (turn 1) reads the shared `shared_meta_kv` partition owned by the tree-root instance — this is now the LANDED behavior (80bb61dd), verified at base. C1′'s objective is to make that contract LOUD and REGRESSION-PROOF: pin the correct-partition behavior with tests that observe the actual block content through the real service, close the coverage gaps the landed tests leave, and thereby serve as the **verify gate for C2** (suppression binding to a wrong partition key is silently untestable — phase3-plan.md:460 Risk 5).

Historical root-cause statement (accurate history, kept): the messaging path hardcoded `parent_id=None` at `daemon/services/instance_messaging.py:3620` (line numbers @ 2750c815), so `_resolve_tree_root_id` (context_messages.py:949-950) computed the child's OWN id as the KV partition key, while the graph-side build at `daemon/graph.py:674-684` already did the right thing and its discard at `:4059-4065` (re-anchored; D13) is by-design anti-double-inject, **not** a missed repair.

---

## Scope

### In Scope (C1′ — tests-only)

- **Coverage audit** of `tests/services/test_instance_messaging_parent_resolution.py` (landed with 80bb61dd; 322 lines, 3 tests) against this phase's original test list — performed and recorded in decisions.md **D12**: thread-through pins exist (child→true parent_id, root→None, bug-exercising mispartition shape); three gaps CONFIRMED and closed below with new pins.
- **Gap pin (a) — real-service partition observation** (supersedes the original regression test 1's fix-proof role; now a pin, since the fix is landed): sentinel KV set on the tree-root partition must be visible in the child's first-turn block through the REAL messaging service with file-backed SQLite (recipe below). New test: `test_child_first_turn_kv_partition_real_service`.
- **Gap pin (b) — exception-ladder parity pins** (D12 CONFIRMED parity must be pinned, not just asserted): `_proj_row` fetch fails → `parent_id=None` (defensive, identical to pre-fix); `get_tree_root_id` raises → `_resolve_tree_root_id` returns `parent_id` (context_messages.py:954-959); `get_tree_root_id` returns None (orphan chain) → `parent_id` (:961). New tests: `test_parent_resolution_exception_ladder`.
- **Gap pin (c) — messaging-path vs tool-path partition consistency**: for one spawned child, the partition key the messaging path assembles under MUST equal the key the tool path reads (`daemon/tools/shared_meta_kv_tools.py:109-122`). New test: `test_partition_consistency_messaging_vs_tool`.
- **Existing pins stay green**: `tests/unit/test_shared_meta_kv_tool.py` (`:84, :109, :133, :161, :194, :216` — context_key assertions) and existing first-turn assembly tests.
- **No daemon code changes**: `git diff daemon/` must be EMPTY for the C1′ commit (tests-only). No agent prompt changes (`git diff agents/` empty).

### Out of Scope (updated by D12)

- **The kill-switch and all flag wiring** — RETIRED. `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` is struck from C0's `constants.py` registry pre-reservation and every flag table (avoid a reserved-unused entry per B.S.8). No resolver, no `_reset_*_for_tests`, no boot INFO line, no OFF pin. The kill-switch convention protects NEW behavior shipping to live prod; it does not mandate retro-wrapping landed correctness fixes (D12).
- **The parent_id threading fix itself** — LANDED at base (80bb61dd); design-of-record below. Re-implementing or modifying it is out of scope.
- **Stable-id scheme** (C0's prerequisite, phase1-plan.md §Shared prerequisite) — the partition pins observe block content, not id format; the id contract lands with C0/C3.
- **DEFECT 1** (cadence/refreshing of the ambient block) — phase1-plan.md body, C3.
- **DEFECT 3** (suppression for system-default projects) — phase3-plan.md, C2; binds against the partition correctness C1′ pins.
- **API GET /messages read path** (`daemon/persistence.py`) — already passes real `parent_id` (verified current: `:905` / `:927`; second site `:1096`). Verified correct; no change.
- **Tool path** (`daemon/tools/shared_meta_kv_tools.py:109-122`) — already uses `get_tree_root_id` correctly. Verified correct; no change.
- **graph.py:674-684** ContextSlot assemble — already passes `self._parent_id` correctly (line `:681`, verified current). The discard at `:4059-4065` (re-anchored; D13) is intentional; do NOT un-discard (W11 grep gate in the overview risk register R8).
- **Agent prompt files** under `agents/{name}/` — daemon-only diff; no agent prompt may describe the mechanism (worktree-aware prompts ship-byte-identity fence per `.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34`).
- **Other env flag precedents** that ship with default-OFF polarity (e.g. `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` reserved-unused, `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`) — different risk class; not relevant.

---

## Root Cause (historical — FIXED AT BASE by 80bb61dd; line numbers @ 2750c815 with current anchors per D13)

A spawned child's first runtime context assembly hardcoded `parent_id=None`, so it computed the KV `context_key` as its OWN instance id instead of the tree-root partition where the parent writes. The correctly-configured graph-side rebuild was then DISCARDED. **All anchors below are historical** (the hardcode and the misleading comment no longer exist at HEAD); the design-of-record section describes the landed state.

### Mechanism — file:line + quoted conditions (historical)

1. **Hardcode** at `daemon/services/instance_messaging.py:3620` (@ 2750c815):
   ```python
   _persistent_parent_id: str | None = None
   ```
   The block-comment at `:3609-3619` claimed:
   > "The orchestrator treats `parent_id=None` as 'tree-root instance' … children inherit the same persistent context as their root via the tree-root resolution inside the orchestrator."
   The comment described a resolution the `None` input made UNREACHABLE. At `:3629`:
   ```python
   parent_id=_persistent_parent_id,
   ```
   the literal `None` flowed through. **Landed state:** the hardcode and misleading comment are DELETED (80bb61dd); the doc-true comment + threading block now sit at `:3664-3686` (D13).

2. **Short-circuit** at `daemon/services/context_messages.py:949-950`:
   ```python
   if parent_id is None:
       return instance_id
   ```
   `_resolve_tree_root_id` returns the child's own id → `context_key = child's own id` → `_fetch_kv_metadata` (`context_messages.py:964-997`) reads the child's own effectively-empty partition at `daemon/services/context_messages.py:1342-1345` (the `_fetch_kv_metadata` call site inside `assemble_context_messages`).

3. **Discarded correct repair** at `daemon/graph.py:3866-3879` (@ 2750c815; current `:4059-4065`, D13):
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

### Minimal-fix fact (the premise the landed fix confirms)

The instance row is **already fetched** at `instance_messaging.py:3599-3601` (@ 2750c815; landed block `:3673-3675`):
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

The API read path (`persistence.py:897-921` @ 2750c815; current `:905/:927`, D13) is the model the landed fix followed:
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

## Design of Record — the LANDED diff (80bb61dd), verified at base

**Decision: Option A** (thread real `parent_id` from the already-fetched instance row) was implemented exactly as specified, with two refinements over the draft sketch:

1. **Single-fetch read with defensive getattr**: the landed code reads BOTH `project_id` and `parent_id` from the SAME `_proj_row` fetch — `getattr(_proj_row, "parent_id", None) or None` (instance_messaging.py `:3677-3678`), zero extra round-trips.
2. **Exception path sets BOTH fields to None** (`:3684-3686`): any failure in the row fetch leaves `_persistent_project_id` and `_persistent_parent_id` both `None`, so `_resolve_tree_root_id` (context_messages.py:949-961) short-circuits to the instance's own id — the pre-fix fallback, rated "at least as correct as today" in the exception table below (parity CONFIRMED per D12).

The misleading pre-fix comment block was deleted and replaced with a doc-true comment (`:3664-3670`) describing the tree-root walk. The three call sites now agree on the contract (verified current):
- `graph.py:681` — `parent_id=self._parent_id`
- `persistence.py:905 / :927` — `parent_id = getattr(instance_meta, "parent_id", None) or None` → `assemble_context_messages(parent_id=parent_id)`
- `instance_messaging.py:3671-3686 / :3695` (landed) — `parent_id = getattr(_proj_row, "parent_id", None) or None`

The landing commit's own three-surface audit matches this phase's original audit: messaging FIXED, graph.py ContextSlot already correct, persistence.py already correct (D12).

### Alternatives considered (historical — Option A was the one landed)

- **Option B**: pre-resolve the tree root at the messaging path by calling `get_tree_root_id(instance_id)` directly and pass the resolved root as a new `context_key` kwarg to `assemble_context_messages`. **Rejected** because:
  1. It requires a new public kwarg on `assemble_context_messages` (a 7-keyword call already in `graph.py:674-684` and `persistence.py:914-925`). The facade-forwarding discipline (`daemon/manager.py`) means every existing caller would need to be audited; high blast radius for a one-line fix.
  2. It duplicates the `_resolve_tree_root_id` ladder (parent_id=None → self fallback, exception → parent_id fallback) in two places.
  3. The exception-path semantics are simpler if `parent_id` is the canonical input: today's behavior (`parent_id=None` → own partition) is unchanged for roots; today's defect (child reads own partition) is closed because `parent_id` is now set.
- **Option C**: skip the `parent_id` lookup and call `get_tree_root_id(instance_id)` directly at `instance_messaging.py:3620` to compute `parent_id = tree_root_id`. **Rejected** because:
  1. `_resolve_tree_root_id` already does this walk; we'd compute the same value twice (one extra DB round-trip per first turn, across all spawned children).
  2. The exception-path contract (defensive `try/except` + fall back to `parent_id`) already exists in `_resolve_tree_root_id`; replicating it is error-prone.

### Exception-path behavior (parity CONFIRMED per D12; "landed" = the 80bb61dd behavior)

| Scenario | Pre-fix behavior (None input) | Landed behavior (real parent_id — 80bb61dd) |
|---|---|---|
| Root instance (`_proj_row.parent_id is None`) | `context_key = instance_id` ✓ | `context_key = instance_id` ✓ (unchanged) |
| Child with valid parent_id | `context_key = child_id` ✗ (reads own empty partition) | `context_key = get_tree_root_id(parent_id)` ✓ (reads tree-root partition) |
| `_proj_row` is `None` (transient race / missing instance) | `context_key = instance_id` (correct for missing-row, defensive) | `context_key = instance_id` (correct for missing-row, defensive — the landed `try/except` sets BOTH `_persistent_project_id` and `_persistent_parent_id` to `None`, `:3684-3686`) |
| `get_tree_root_id` raises (DB error) | n/a — `_resolve_tree_root_id` returns `instance_id` via the `if parent_id is None` short-circuit | `_resolve_tree_root_id` returns `parent_id` via the `except Exception` ladder at `:954-959` (defensive fall back — better than the pre-fix hard-coded own partition) |
| `get_tree_root_id` returns `None` (orphan chain, parent deleted) | n/a | `_resolve_tree_root_id` returns `parent_id` at `:961` (better than the pre-fix hard-coded own partition) |
| `_proj_row.parent_id` extraction raises | n/a (literal `None`) | `try/except` returns `None` → identical to pre-fix (defensive) |

**Net: no regression in any exception path (D12 parity CONFIRMED).** The `None` default became a measured fallback after the explicit `_proj_row.parent_id` extraction. All defensive fallbacks land on a value that is at least as correct as the pre-fix one. C1′ pins this ladder — see Test strategy gap pin (b).

### Compliance with shared stable-id scheme (phase1-plan.md §Shared prerequisite)

The first-turn block this fix emits flows through `_make_context_message` (`context_messages.py:85-111`), which mints a `uuid4` per call at line 109. Per the message-id invariant in the Core Architecture blueprint:
- Re-emitted blocks under new ids get **APPENDED** by `add_messages`, not replaced.
- Id-less messages are dropped by `MessageTapSlot` / get moving-timestamp fallbacks (`persistence.py:527-528`).

For DEFECT 2 specifically: the first-turn block **just needs to use the stable-id format** that C0 specifies (so defect 1's supersede semantics land cleanly later). Precedents:
- Auto-load block stable-id supersede at `context_messages.py:1273-1279` (filtered rebuild under stable id so `add_messages` supersedes the stale block).
- API scheme `synthetic-context-{kind}-{instance_id}-{idx}` at `persistence.py:943` (current `:949`, D13).

**Dependency (updated by D12):** the C1′ partition pins observe block CONTENT, not id format — C1′ does NOT block on the stable-id scheme landing. C2/C3 remain blocked on C0 as before.

### Comment/doc-truth updates — DONE by the landed diff

Per the prompt-writing convention (`PROCESS: pin destructive-copy by RENDER path`) and doc-truth discipline (stale comments quoting unreachable behavior must be corrected), 80bb61dd already executed both updates — verified at base:

1. **`instance_messaging.py:3609-3619`** (@ 2750c815) — the misleading comment was DELETED and replaced with the doc-true comment now at `:3664-3670`: it resolves `project_id` AND `parent_id` from the permanent `instances` row in one fetch, explains the `_resolve_tree_root_id` walk, and notes that root instances still pass `None` (parent_id column is None → returns own id; unchanged).
2. **`graph.py:3873-3879`** (@ 2750c815) — the landed discard comment at `:4059-4065` (D13) states: "`_persistent_msgs` is intentionally discarded — the messaging path already prepended those messages to `graph_input` ... Reading them again here would double-inject." The do-NOT-un-discard contract is enforced by the W11 PR-description grep gate (overview R8).

No other doc updates required. The Core Architecture blueprint's `[SYSTEM CONTEXT]` block already describes the data flow correctly; no in-doc fix needed there.

---

## Kill-Switch Design — RETIRED (D12)

`ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` is **RETIRED** by the C1 re-adjudication (decisions.md D12, dispatcher decision recorded verbatim there). Rationale, in brief:

- The fix is **landed, in base, pure-correctness, exception-safe** (80bb61dd, merged via 36a46b01). A post-hoc Shape B flag would add resolver + boot-log + restart-dependency + churn on the exact seam C2/C3 touch, **with nil revert demand** — there is no new behavior shipping to live prod to protect.
- The kill-switch convention protects NEW behavior shipping to live prod; it does not mandate retro-wrapping landed correctness fixes.
- Per B.S.8's own rationale (a reserved-unused entry contradicts the discipline), the name is **struck from C0's `constants.py:594-624` registry pre-reservation** and from every flag table in this artifact set. The remaining two flags (`ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`, `ENSEMBLE_AMBIENT_KV_FRESH`) are unchanged (D5/D8).

Consequences for this phase: **no resolver, no `_reset_*_for_tests`, no boot INFO line, no OFF pin, no restart-to-flip**. The original draft's flag sections (Shape B pattern, polarity table, boot log, negative staging test) are superseded; the deployed behavior is unconditional. If the landed fix ever needs an incident kill-switch, that is a NEW decision with a NEW name (D12 reversibility note).

---

## Test Strategy (C1′ — verify-and-pin; tests-only)

### 0. Coverage audit of the LANDED tests (recorded; D12 item ii)

`tests/services/test_instance_messaging_parent_resolution.py` (landed with 80bb61dd; 322 lines, 3 tests):

| Landed test | What it pins | Level |
|---|---|---|
| `test_child_instance_passes_true_parent_id_to_orchestrator` (:132) | Child's row `parent_id` is threaded into `assemble_context_messages(parent_id=...)` | kwargs-capture; `assemble_context_messages` itself patched; row is a `SimpleNamespace` mock |
| `test_root_instance_passes_none_to_orchestrator` (:181) | Root still passes `parent_id=None` (own-partition branch unchanged) | same |
| `test_fix_reverted_child_mispartitions_to_own_id` (:240) | Bug-exercising proof: the pre-fix force-None shape produces the exact mispartition symptom via `_resolve_tree_root_id`, plus the fix-shape cross-check (:311-322) | resolver-level |

**Confirmed gaps** (each closed by a new pin below — NO daemon code): (a) no test observes block CONTENT/partition through the real service (the mocks never execute real assembly, so "child reads the parent's partition" is inferred, not observed); (b) the exception ladder is unpinned (`_proj_row` fetch fails; `get_tree_root_id` raises; `get_tree_root_id` returns None); (c) messaging-path vs tool-path partition consistency is unasserted.

### 1. Gap pin (a) — `test_child_first_turn_kv_partition_real_service` (NEW; supersedes original regression test 1's proof role — this is now a PIN of landed behavior)

**File**: `tests/integration/test_instance_messaging_first_turn_kv_partition.py` (NEW).

**Setup** (mirrors `test_governor_recursion_acceptance_walk.py:771-839` for real-service + real-spawn):
- Real `Config` + real `InstanceManager` + real `_instance_repository` (file-backed SQLite tmp_path + NullPool + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` per `test_job_driven_enqueue_work_id_facade.py:76-95`) + **real `SharedMetaKVRepository`**.
- Spawn a parent instance via `manager.spawn_instance` (real spawn path).
- Spawn a child instance via `manager.spawn_instance(..., parent_id=parent_id)`.
- Set a sentinel KV entry on the tree-root partition via the real repo: `repo.set(tree_root_id, "council_manifest", {...})`.
- Invoke the child's first turn via `send_message` (real messaging path — no patching of `assemble_context_messages`).
- Read the child's persisted first-turn checkpoint (or hook the produced `persistent_context_msgs`).

**Assertions**:
- The child's first-turn context surface contains a project context message.
- Its serialized content contains the sentinel KV payload (the parent's `council_manifest` value) — the tree-root partition was actually READ.
- No second, own-partition block is produced (single project block; no empty-partition duplicate).

### 2. Gap pin (b) — `test_parent_resolution_exception_ladder` (NEW; pins the D12-CONFIRMED parity)

**File**: `tests/services/test_instance_messaging_parent_resolution.py` (extend; same harness).

- `_proj_row` fetch raises / returns `None` → `parent_id=None` reaches the orchestrator (defensive; identical to pre-fix). Landed shape: except-branch sets BOTH `_persistent_project_id` and `_persistent_parent_id` to None (`:3684-3686`).
- `get_tree_root_id(parent_id)` raises → `_resolve_tree_root_id` returns `parent_id` (context_messages.py:954-959).
- `get_tree_root_id(parent_id)` returns `None` (orphan chain) → `_resolve_tree_root_id` returns `parent_id` (:961).
- Net assertion per D12: every fallback lands on a value **at least as correct as** the pre-fix own-partition fallback.

### 3. Gap pin (c) — `test_partition_consistency_messaging_vs_tool` (NEW)

**File**: `tests/integration/test_instance_messaging_partition_consistency.py` (NEW).

For ONE spawned child (real service, real repo, DB recipe below): the partition key the messaging path's first-turn assembly reads under MUST equal the key the tool path returns for the same child (`daemon/tools/shared_meta_kv_tools.py:109-122` — `get_tree_root_id`-based). Asserts both paths resolve to the same tree-root `context_key` — the property that makes "ambient block content" and "explicit tool read" agree.

### 4. Tool-path pins stay green

**No changes** to `tests/unit/test_shared_meta_kv_tool.py` — its context_key assertions at `:84, :109, :133, :161, :194, :216` exercise the tool path, which is unchanged. Run the full tool-path test as part of CI to confirm no regression.

### 5. Existing first-turn assembly tests stay green

- `tests/unit/test_context_messages.py:1054-1066` — orchestrator-level first-turn test with explicit `parent_id=`. Must stay green.
- Any other test under `tests/unit/test_context_messages.py` and `tests/unit/test_instance_messaging.py` that exercises first-turn context assembly.
- The landed fix changed the runtime path **only**; the orchestrator-level test pattern (explicit `parent_id=`) is unchanged.

### 6. DB recipe for write surfaces

File-backed SQLite `tmp_path` + `NullPool` + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` — exemplar `tests/integration/test_job_driven_enqueue_work_id_facade.py:76-95`, `tests/test_wc_wake_pure_hang.py:235-254`, `tests/test_n3_per_kind_filter_pin.py:76-96`.

**Do NOT** copy the in-memory `StaticPool` pattern from existing `shared_meta_kv` repo tests — that pattern masks DB-level bugs and would hide the partition-correctness contract (the contract is about which row is read, not how the repo is mocked). Applies to pins 1 and 3; the MagicMock-waiver note in the original draft test 3 is DROPPED with the flag (D12) — pin 1 goes through the REAL service with a REAL `SharedMetaKVRepository` (W6-equivalent discipline adopted for C1′).

### 7. Bug-exercising proof — ALREADY LANDED

Performed by the landing commit: `TestMessagingParentResolutionBugExercising::test_fix_reverted_child_mispartitions_to_own_id` reverts the fix shape (force `parent_id=None`) and asserts the exact mispartition symptom, plus the fix-shape cross-check. No additional pre-fix worktree proof is required for C1′ — the branch's base already contains both the fix and its bug-exercising proof.

---

## Rollout

### Branch and anchor re-pin

1. At implementation kickoff, **re-grep all anchors** in the implicated file set. The drift set is **7 commits**, `2750c815..9eebf3ff` (decisions.md D13 — supersedes the stale 3-commit injected-notes list this draft carried): `d348ad4e` (tidier doc-truth/comment fixes), `80bb61dd` (the DEFECT 2 fix — already in base), `d6e30d9d` + `7a899517` + `e321bdb3` + `f965345a` + `53baef57` (LCA arc). D13's verified-current anchor table is the starting point.
2. Branch from `latest` (not from the worktree anchor): the initiative's single branch `feature/kv-ambient-awareness-fix` off `origin/latest` (D2 — one branch, sequenced commits; C1′ is the second commit).

### Implementation order (C1′ — tests-only)

1. After C0 (stable-id helper; the pins do not depend on it, but branch order is fixed — D2/D9).
2. Write the three gap pins (test-strategy items 1-3) — **no daemon code changes** (`git diff daemon/` must be empty for the C1′ commit).
3. Verify tool-path pins stay green + verify existing first-turn tests stay green.
4. Run full test suite.

### Restart-to-flip

**N/A — the fix is landed and unconditional** (no flag; D12). Nothing to flip. Deploy staging is the operator's choice per the overview's Pause-First runbook addendum (default-ON direct vs staged flip of the TWO surviving flags).

### Post-deploy verification

1. **Boot log check**: no DEFECT-2-specific line exists (no flag — D12). Verify only the two surviving flags' boot lines (overview Rollout step 4).
2. **Smoke test** (manual, scripted into the rollout runbook): spawn a leader → spawn a worker child via `send_message`. In the child's first-turn checkpoint, inspect the `[SYSTEM CONTEXT: Related Project]` block — it must contain the leader's KV metadata (e.g. `council_manifest` if the leader has populated one), not an empty block. This is the manual counterpart of C1′'s `test_child_first_turn_kv_partition_real_service`.
3. **Regression check**: tool-path tests (`tests/unit/test_shared_meta_kv_tool.py`) and existing first-turn assembly tests stay green in CI.
4. **Negative test**: none for this defect (no flag; D12). The landed bug-exercising test (`test_fix_reverted_child_mispartitions_to_own_id`) is the standing regression alarm.

---

## Risks + Mitigations (updated by D12)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | `get_tree_root_id` returns wrong root due to cycle self-parenting | High (children read wrong partition → silent stale block) | Low (depth cap at `_MAX_TRAVERSAL_DEPTH=256` per `repository.py:33`; visited set is the truthful result) | Pre-existing safety in the repo; no new fix needed. If a misfire is observed, the landed bug-exercising test + C1′'s partition pins fail loudly; remediation is a NEW fix decision (no flag exists — D12). |
| 2 | Race between spawn and first turn where `parent_id` changes | Low (a child re-parented mid-flight reads a different root) | Very low (parent_id is set at spawn and rarely mutated; `instances.parent_id` is permanent for the child row — survives terminate-to-revive) | Mitigated by depth cap + the defensive `_resolve_tree_root_id` exception ladder (pinned by C1′ gap pin b). |
| 3 | Exception in the `_proj_row` fetch (current `:3673-3686`; draft cite `:3599-3607`) | Low (silent reversion to `None` → re-introduces the defect for that turn) | Low (the row is already fetched and the attribute access is plain `getattr` on a known shape) | Landed `try/except Exception` sets BOTH fields to `None` — identical to the pre-fix fallback. **Now PINNED** by C1′ gap pin (b) instead of relying on a flag. |
| 4 | The discard at `graph.py:4059-4065` (draft cite `:3873-3879`) changes in a future commit and conflicts with the fix | Low (breaks the anti-double-inject contract → double-injected block) | Low (discard is intentional, doc-pinned, and reviewed in the worktree-aware-prompts work) | Landed comment documents the do-NOT-un-discard contract; **W11 grep gate** in every C2/C3 PR description (`grep -n "_ = _persistent_msgs" daemon/graph.py` + comment intact). |
| 5 | ~~Phase 1 stable-id scheme not yet specified at implementation kickoff~~ | — | — | **RESOLVED (D12):** C1′ is tests-only and its partition pins observe content, not id format — no stable-id dependency. C0→C2→C3 retain the dependency. |
| 6 | ~~Kill-switch flag accidentally OFF by config change~~ | — | — | **RESOLVED (D12):** no flag exists; the behavior is unconditional. |
| 7 | Pin-test anchor churn on the 7-commit drift (`2750c815..9eebf3ff` — D13) | Medium (line numbers in this plan drift) | Medium (LCA commits touched `graph.py`/`instance_messaging.py`) | Re-grep all anchors at kickoff; D13's verified table is the starting point; the pinned CONTRACTS (threading shape, ladder, discard) are stable even as lines move. |
| 8 | Existing test using a manually-mocked `parent_id` path breaks under the landed threading | Low | Very low (already absorbed: the landed suite passes at base; the only orchestrator-level test at `test_context_messages.py:1054-1066` passes explicit `parent_id=`) | Run full test suite before merge. If a test breaks, decide per-test. |

---

## Success Criteria (C1′)

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Coverage audit recorded | decisions.md D12 item (ii) + this phase's test strategy §0 | Audit present; 3 gaps enumerated with evidence |
| 2 | Child instance first turn reads tree-root KV partition (observed, not inferred) | New `test_child_first_turn_kv_partition_real_service` (real spawn + real send_message + real `SharedMetaKVRepository`, file-backed SQLite) | Sentinel KV value from tree-root partition appears in child's persistent block |
| 3 | Exception-ladder parity is pinned | New `test_parent_resolution_exception_ladder` | All three fallback shapes (`_proj_row` missing; `get_tree_root_id` raises; returns None) assert the documented fallback value |
| 4 | Messaging/tool partition consistency is pinned | New `test_partition_consistency_messaging_vs_tool` | Both paths resolve the same `context_key` for one spawned child |
| 5 | Tool-path pins stay green | `tests/unit/test_shared_meta_kv_tool.py:84, :109, :133, :161, :194, :216` | All 6 context_key assertions pass |
| 6 | Existing first-turn assembly tests stay green | `tests/unit/test_context_messages.py` (full file), `tests/unit/test_instance_messaging.py` (full file) | No regression |
| 7 | C1′ commit is tests-only | `git diff daemon/` for the C1′ commit | **Empty diff** (no daemon code; no flag wiring) |
| 8 | No agent prompt file touched | `git diff agents/` after merge | Empty diff |
| 9 | Landed fix unmodified | `git log -- daemon/services/instance_messaging.py` on the branch | No C1′ commit touches it; 80bb61dd remains the last fix commit |
| 10 | Post-deploy smoke test passes | Manual scripted check: leader spawns worker child → worker's first-turn checkpoint contains leader's KV metadata | Block populated, not empty |

---

## Sequencing Inputs (updated by D12)

This phase is the **first implementer** after the stable-id prerequisite (C0) — as a TESTS-ONLY verify gate.

### Inputs consumed

- **The LANDED fix** (80bb61dd, merged via 36a46b01): design-of-record. Verified at base; anchors per D13. C1′ pins its behavior; it must NOT be modified.
- **Dispatcher adjudication** (decisions.md D12, 2026-09-08): the C1 re-scope, recorded verbatim. Read in full before implementing.
- **Defect verification** (architect c5ae6d95 §1a/§6, 2026-09-06): the verified root-cause analysis (historical context).
- **Worktree-aware prompts verification summary** (`.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34`): the byte-identity fence that constrains this phase to NOT touch `agents/` prompt files.

### Outputs produced (consumed by later phases)

- **C2 (DEFECT 3 — suppression)**: C1′ is its VERIFY GATE — the correct-partition contract is pinned before C2 builds on it. Suppression's new KV host binds to the tree-root key (`context_key = get_tree_root_id(parent_id)`); without the pinned contract, a wrong-partition binding is silently untestable (phase3-plan.md:460 Risk 5).
- **C3 (DEFECT 1 — cadence)**: depends on the partition being correct — refreshing a mispartitioned block would only make wrong content fresher. Cadence is meaningless until the partition contract is pinned.

### Coupling-derived implementation order

```
C0 (stable-id scheme) → C1′ (YOU, verify-and-pin — tests-only) → C2 (suppression) → C3 (cadence, DEFECT 1)
```

All three fix sites share the `assemble_context_messages` KV path (`context_messages.py:1319-1360`) and the runtime injection seam (`instance_messaging.py:3637-3760`, D13). Sequencing reflects dependency, not priority.

### Branch and dependency hygiene

- Do not branch off the `2750c815` worktree — branch off `origin/latest` at kickoff and re-grep anchors (D13 table). The worktree is read-only context; the main checkout is externally owned.
- C1′ must NOT modify `daemon/services/instance_messaging.py` (the landed fix) — if a pin cannot be written without touching daemon code, halt and flag to the dispatcher.

---

## Open Questions (updated by D12)

1. ~~Phase 1 stable-id scheme spec timing~~ — **RESOLVED (D12):** C1′ has no stable-id dependency (content-observing pins); C2/C3 retain it.
2. **System-default project behavior post-fix**: the system-default project is suppressed from KV reads today (`is_system_default` check at `context_messages.py:1331-1345`). For a system-default ROOT, `context_key = root_id` and `_fetch_kv_metadata` is skipped — correct. For a system-default CHILD (depth ≥ 1, parent is system-default root), the landed fix threads `parent_id = root_id` and `_resolve_tree_root_id` walks to the root → `context_key = root_id` → `_fetch_kv_metadata` is skipped (correct — system-default suppression is by project_id, not by partition). Belief: no edge case where a non-system-default child reads a system-default parent's KV partition (system-default is per-project, not per-partition). **Kept as a code-review checkpoint** for C1′'s partition pins (see plan-overview Gaps).
3. ~~Phase 3 timing~~ — **RESOLVED:** branch sequencing (D2/D9) fixes the order C0 → C1′ → C2 → C3 on one branch; no cross-worker timing dependency remains.
