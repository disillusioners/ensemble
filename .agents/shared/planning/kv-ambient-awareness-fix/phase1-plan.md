# Phase 1: DEFECT 1 — KV Snapshot Cadence Fix
**kv-ambient-awareness-fix initiative — Phase 1 of 3**

Date: 2026-09-07
Author: plan-creation worker (v1)
Status: Draft

---

## Objective

Fix DEFECT 1 (KV snapshot cadence) so that the `[SYSTEM CONTEXT: Related Project]` KV block reflects fresh shared metadata on **every agent turn**, not just once at instance start. After the fix: ambient KV is reliable, the `project_injected` flag remains the turn-1 gate for project JSON / critical notes / history, and each seam (inject / report / revive — all except `is_retry` resume) threads fresh KV into the context block under a stable message id, causing LangGraph's `add_messages` reducer to replace the stale checkpoint entry in place. Compacted sessions remain safe (constant per-turn KV overhead; one stable-id entry, not growing).

---

## Scope

### In Scope
- Refactor `assemble_context_messages` to split the once-per-instance `[SYSTEM CONTEXT: Related Project]` block into two independent HumanMessages:
  1. **Stable project block** — project JSON, critical notes, history entries. Emitted once on turn 1 under stable id `project:{instance_id}`. Never rebuilt.
  2. **Live KV block** — the `kv_metadata` section. Emitted every turn under stable id `kv:{instance_id}` (stable across turns so `add_messages` replaces in place).
- Add a deterministic-id path to `_make_context_message` (precedent: `build_auto_load_skills_message` / `get_auto_load_skill_message_id` at `context_messages.py:683-693`).
- ~~Gate the `is_retry` short-circuit (`if not is_retry:`) to ALWAYS emit the KV block on retries~~ — **ERRATUM (W4/D15, 2026-09-08 revision): the frozen draft's opposite claim here was a contradiction. RATIFIED: `is_retry=True` turns NEVER refresh** — retries skip all of `assemble_context_messages` via the gate at instance_messaging.py:3637 (re-anchored; D13); a retry turn replays the checkpointed KV block, and the next non-retry turn refreshes it. Consistent with the seam table below and the test inventory.
- Kill-switch `ENSEMBLE_AMBIENT_KV_FRESH` (Shape B; default ON; `=0` disables → **cadence-only reversion (W3): the KV block still EXISTS — emitted on turn 1 exactly as with the flag ON — and is simply never refreshed on turns 2+; OFF does NOT remove the turn-1 block**). Registered in `constants.py:594-624`.
- New regression test reproducing the defect symptom: KV stays stale across inject / report / revive seams.
- New flag-ON real-service test wiring the kill-switch through the real `SharedMetaKVRepository`.
- **Turn-1-surface OFF pin (W3):** new test asserting that with the flag OFF, turn 1 STILL emits the KV block (the block's existence is not gated — only the refresh cadence is).
- Existing test pin audit: enumerate assertions that encode the once-per-instance contract and document their new expected outcomes.
- Re-anchor at `latest` before implementation (drift set: **7 commits, `2750c815..9eebf3ff`** — `d348ad4e`, `80bb61dd`, `d6e30d9d`, `7a899517`, `e321bdb3`, `f965345a`, `53baef57`; supersedes the stale 3-commit injected-notes list this draft carried; see decisions.md D13).

### Out of Scope
- DEFECT 2 (mispartition `_persistent_parent_id=None`) — Phase 2.
- DEFECT 3 (system-default project suppression) — Phase 3.
- Any changes to agent prompt files (`.agents/*/soul.md`, `.agents/*/workflow.md`, etc.) — the worktree-aware prompts worktree-aware-prompts/verification-summary.md:24-34 is immune by design; ambient becomes a reliable bonus for governor/project-manager consumers.
- The `project_injected` flag itself — it stays as the turn-1 gate for the project block. KV freshness is handled by a separate mechanism.
- The `auto_load_block_active` / `AUTO_LOAD_BLOCK_ACTIVE_KEY` path — untouched.
- Changes to the `shared_meta_kv` tool (`daemon/tools/shared_meta_kv_tools.py`) or the `SharedMetaKVRepository` — the write path is correct; only the read (assembly) cadence is wrong.
- Migration of `_finalize_job_db_sync` / `_terminate_instance_db_sync` (Phase 4b/4c deferred) — unrelated.

---

## Root Cause

### Mechanism (per verified investigation, architect c5ae6d95 §1a/§6)

The `[SYSTEM CONTEXT: Related Project]` HumanMessage is assembled once per instance lifetime by `assemble_context_messages` (context_messages.py:1134) via `build_project_context_message` (context_messages.py:411), which calls `_fetch_kv_metadata` (context_messages.py:964-997 → `repo.get_all_as_dict`) and renders the result into the block body. This block is emitted and checkpointed at the end of the first turn.

**The stale-on-revival condition** (`context_messages.py:1270-1314`):
```python
if project_already_injected:
    # On turn 2+ the project + shared-context + auto-load blocks
    # are already checkpointed — only the per-turn BM25 skill search
    # rebuilds.
    ...
    return (persistent_after_inject, [])
```
The `project_already_injected` boolean is the `project_injected` flag persisted in `instance_metadata` (DB row, survives restarts). Once True it never flips back. The entire project block (including KV section) is short-circuited on every subsequent turn.

**The `is_retry` gate** (`instance_messaging.py:3637`; re-anchored from the draft's `:3574` per D13):
```python
if not is_retry:
    ...  # assemble_context_messages called here
```
Retries intentionally bypass the assembly — fine. The defect is that non-retry turns (the vast majority) also bypass it after turn 1.

**Seam table** (which turns run the fresh path; derived from investigation):

| Seam | Call site | `is_retry`? | Reaches assembly? | Fix needed? |
|------|-----------|-------------|-------------------|-------------|
| agent_node (graph slot) | graph.py:648-684 | per dispatch | Yes — every turn | Yes |
| Message inject (HTTP / tool) | instance_messaging.py:3637-3760 (re-anchored; D13) | False (first) | Yes — turn 1 only | Yes |
| Child-report delivery | child_reports.py:466/3735 → _process_message_with_tracking | False | Yes — turn 1 only | Yes |
| Job-event context | manager.py:6790-6846 → normal message | False | Yes — turn 1 only | Yes |
| is_retry resume | instance_messaging.py:3637 | True | **No** | No (by design — D15 ratifies skip-on-retry) |
| Terminal revive | instance_messaging.py:1875-1945 | False | **No** (flag still True) | No (next non-retry turn fixes) |
| Pause / resume | instance_lifecycle.py:2799/3085 | True on resume | **No** | No (by design) |

The two seams that short-circuit are the same contract — `project_injected` flag.

### The Compounding Factor
`DEFAULT_CONTEXT_LIMIT = 700000` (compaction.py:1090) means the system is not under immediate pressure from the stale block. But as sessions grow, the stale once-per-instance KV block contributes its frozen bytes to every compaction trigger — the fix is correctness, not a performance regression.

### Relevant Code Anchors (at worktree 2750c815; must re-anchor at latest before implementation)

| Location | Role |
|----------|------|
| `context_messages.py:85-111` | `_make_context_message` — mints `uuid4` every call (the defect) |
| `context_messages.py:411-470` | `build_project_context_message` — renders project JSON + critical notes + KV + history into ONE HumanMessage |
| `context_messages.py:460` | `kv_section = _format_kv_metadata_section(kv_metadata or {})` — KV rendering call |
| `context_messages.py:683-693` | `get_auto_load_skill_message_id` — precedent stable-id format: `auto_load:{instance_id}:{agent_id}` |
| `context_messages.py:964-997` | `_fetch_kv_metadata` — reads `repo.get_all_as_dict(context_key)` |
| `context_messages.py:1270-1314` | `if project_already_injected:` — the once-per-instance short-circuit |
| `context_messages.py:1319-1360` | KV fetch inside the full-build path; skipped by short-circuit |
| `context_messages.py:1273-1279` | Comment documenting the stable-id / add_messages supersede pattern for auto-load |
| `instance_messaging.py:2865-2890` | `project_injected` flag capture (read once, cached) |
| `instance_messaging.py:2907-2949` | `project_injected` flag capture (read once, cached) — re-anchored per D13 |
| `instance_messaging.py:3050-3056` | `project_injected` flag stamp after first turn — re-anchored per D13 |
| `instance_messaging.py:3637` | `if not is_retry:` gate — retries skip all of `assemble_context_messages` — re-anchored per D13 |
| `graph.py:665-684` | `_is_project_already_injected` — re-reads flag per turn (already fresh per turn) |
| `persistence.py:949` | API synthetic-context stable id format: `synthetic-context-{context_kind}-{instance_id}-{idx}` (enumerate region `:937-949`; re-anchored per D13) |
| `compaction.py:1085-1090` | `DEFAULT_CONTEXT_LIMIT = 700000` — bound for per-turn cost analysis |
| `constants.py:594-624` | Kill-switch name registry |
| `tests/unit/test_context_messages.py:1634-1671` | `test_auto_load_skipped_when_project_already_injected` — pin to verify unchanged |
| `tests/unit/test_blueprint_injection.py:260-276` | `test_project_already_injected_skips_matcher` — pin to verify unchanged |

### Compaction Numerator Behavior (Under Fix)
- **Turn 1**: emits project block (stable id) + kv block (stable id) + skills (uuid4 → appended)  
- **Turn 2+**: emits kv block (stable id → replaces prior kv block via `add_messages`) + skills (uuid4 → appended)  
- **Net**: project block = 1 entry (constant), kv block = 1 entry constant (replaces in place), skills = grows (existing behavior)  
- **Compaction delta**: constant 1-entry overhead for kv block; no growth relative to current once-per-instance path. Compaction trigger is unchanged.

---

## § Shared Prerequisite — Stable-Id Scheme

**This section is the SHARED PREREQUISITE for all three phases (D2 mispartition → D3 suppression → D1 cadence). All three phases depend on `_make_context_message` accepting an explicit stable id. Implement this first; it unblocks D2 and D3 immediately.**

### Why It Is Required

`_make_context_message` (`context_messages.py:85-111`) currently mints `uuid4()` on every call:
```python
return HumanMessage(
    content=f"{CONTEXT_PREFIX}{title}{CONTEXT_SUFFIX}{content}",
    id=str(uuid.uuid4()),  # ← different every call
    additional_kwargs={"injected_message": True, "context_kind": kind},
)
```
When this function is called twice for the same block (turn 1 → turn 2, or child-report → report delivery), the two output HumanMessages have different ids. LangGraph's `add_messages` reducer appends them as distinct entries, not supersedes them. Result: the checkpoint grows with duplicate blocks instead of refreshing the content.

The `build_auto_load_skills_message` / `get_auto_load_skill_message_id` precedent (`context_messages.py:649-693`) already demonstrates the stable-id pattern for auto-load blocks: `auto_load:{instance_id}:{agent_id}`. The auto-load REPLACE sweep at `instance_messaging.py:3666` uses `RemoveMessage` targeting that stable id, and the filtered rebuild under the same id causes `add_messages` to supersede. The KV fix uses the same pattern.

### Required Change: Deterministic-Id Variant of `_make_context_message`

Add an optional `id_` parameter to `_make_context_message`:
```python
def _make_context_message(
    kind: str,
    title: str,
    content: str,
    id_: str | None = None,   # NEW: explicit stable id; None = uuid4 (back-compat)
) -> HumanMessage:
    return HumanMessage(
        content=f"{CONTEXT_PREFIX}{title}{CONTEXT_SUFFIX}{content}",
        id=id_ if id_ is not None else str(uuid.uuid4()),
        additional_kwargs={"injected_message": True, "context_kind": kind},
    )
```

### Stable Id Formats (Two New Registrations)

| Block | Stable id format | Registered in |
|-------|-----------------|---------------|
| Stable project block | `project:{instance_id}` | constants.py |
| Live KV block | `kv:{instance_id}` | constants.py |

Precedents: `auto_load:{instance_id}:{agent_id}` (context_messages.py:683-693), `synthetic-context-{context_kind}-{instance_id}-{idx}` (persistence.py:949; D13).

> **S16 (out of scope, documented):** extending the C0 id-mint to ALL block types (project / auto-load / synthetic) so every context block gains supersede semantics is explicitly OUT OF SCOPE for this initiative — C0 mints ids only for the project + KV blocks. Recorded as a **prod-P3 pin note**: post-deploy priority-3 follow-up ("extend stable-id mint to remaining block kinds"), to be planned separately if the block-growth symptom ever shows on auto-load/skills blocks.

### Supersede Semantics

On every non-retry turn after the fix:
1. `assemble_context_messages` emits `HumanMessage(id="kv:{instance_id}", ...)` alongside the project block
2. LangGraph's `add_messages` reducer sees `id="kv:{instance_id}"` and replaces any prior entry with the same id in `state['messages']`
3. Checkpoint stores exactly one kv block entry, with fresh content

### Compaction-Numerator Stability Proof

- **Before fix**: one project block (stable content, but `uuid4` id → re-emitted on every `assemble_context_messages` call → appended to state['messages'] → grows checkpoint, not replaces)
- **After fix**:  
  - Project block: `id="project:{instance_id}"` → first emit (turn 1) stored; subsequent emits replace in place → **constant 1 entry**
  - KV block: `id="kv:{instance_id}"` → every turn replaces prior → **constant 1 entry, fresh content**
  - Skills: `uuid4` → appended each turn (unchanged; existing behavior)
- **Compaction trigger unchanged**: threshold still counts all messages including injected `[SYSTEM CONTEXT]` blocks. Net addition per turn = 0 from kv fix (constant), +1 from skills (unchanged). No regression.

### Backward Compatibility

The `id_=None` default preserves all existing callers (auto-load, blueprint, skills, etc.) that don't pass an explicit id — they continue to receive uuid4. Only the new KV builder and the project block refactor pass explicit ids.

---

## Fix Approach

### Chosen Design: Split Block + Stable-Id Per-Turn KV

The fix refactors `assemble_context_messages` so the persistent part of the once-per-instance block is split into two independent HumanMessages emitted on different cadences:

**Stable project block** (once, turn 1, under `project:{instance_id}`):
- Contains: project JSON, critical notes, history entries
- Gated by: `not project_already_injected` (existing flag, unchanged semantics)
- New: explicitly minted under stable id `project:{instance_id}` so `add_messages` replaces in place on any future emit (defensive; currently no future emit, but stable id makes the supersede contract explicit)

**Live KV block** (every turn, under `kv:{instance_id}`):
- Contains: the `_format_kv_metadata_section(kv_metadata)` output only
- Gated by: `not is_retry` (retries intentionally skip — by design)
- Emitted: both on turn 1 (alongside stable project block) AND on every subsequent non-retry turn via the `project_already_injected=True` short-circuit branch
- Idempotent: same id every turn → `add_messages` replaces the prior entry in the checkpoint
- Separate builder: `_build_kv_context_message(context_key, manager)` returns `HumanMessage(id=f"kv:{instance_id}", ...)` or `None` (None when system-default project; mirrors the existing `if is_system_default: skip kv_fetch` guard)

**New `assemble_context_messages` signature change**: No change required — the new KV block is emitted from INSIDE the existing function. Two insertion points:

1. **Turn 1 path** (`not project_already_injected`): after building and appending the project block, call `_build_kv_context_message` and append to `persistent_msgs` (KV emitted once alongside project).
2. **Turn 2+ path** (`project_already_injected=True`): before the skill-search block, call `_build_kv_context_message` and append to `persistent_after_inject` (KV only on turn 2+; project block short-circuited as before).

**New function** `_build_kv_context_message`:
```python
async def _build_kv_context_message(
    context_key: str,
    manager: Any,
) -> HumanMessage | None:
    """Build the per-turn KV block under stable id 'kv:{instance_id}'.

    Reads shared_meta_kv via _fetch_kv_metadata. Returns None when
    kv_metadata is empty (no content to render) or on system-default
    project. The stable id ensures add_messages replaces the prior
    checkpoint entry every turn, keeping the checkpoint at constant size.
    """
    kv = await asyncio.to_thread(_fetch_kv_metadata, context_key, manager)
    if not kv:
        return None
    section = _format_kv_metadata_section(kv)
    return _make_context_message(
        kind=CONTEXT_KIND_PROJECT,   # same kind as project block; id differentiates
        title="Related Project",
        content=section,
        id_=f"kv:{context_key}",  # CANONICAL (D3): the full context_key IS the tree-root partition key.
        # ERRATUM (S19/D3, 2026-09-08 revision): the frozen draft carried
        # `kv:{context_key.split(':')[-1]}` ("extract instance_id from context_key") —
        # a WRONG-ID HAZARD; stricken. Supersede granularity must match data granularity.
    )
```

Note: The `context_kind` for the KV block should be `CONTEXT_KIND_PROJECT` (same as the project block) — both are the `[SYSTEM CONTEXT: Related Project]` section. The stable id is the differentiator. Alternatively, a new `CONTEXT_KIND_KV_METADATA` enum value could be added to further distinguish — but the stable id is sufficient and avoids schema changes.

**Refactor of `build_project_context_message`**:
The existing function renders the project JSON + critical notes + KV + history into one block. After the fix, it renders only the first three (project JSON, critical notes, history). Call site in turn 1 path:
```python
# Before: build_project_context_message(..., kv_metadata=kv_metadata, ...)
# After:
kv_metadata = await asyncio.to_thread(_fetch_kv_metadata, context_key, manager)
project_msg = build_project_context_message(
    project=project,
    critical_notes=critical_notes,
    kv_metadata=None,  # no longer embedded in project block
    history_entries=history_entries,
)
# Then emit kv block separately:
kv_msg = await _build_kv_context_message(context_key, manager)
if kv_msg:
    persistent_msgs.append(kv_msg)
```

### Alternatives Considered

| Alternative | Why Rejected |
|-------------|-------------|
| **A. Change-detector (hash/updatedAt compare)** | Adds state: hash-of-KV stored in instance metadata; race: writer commits between hash-read and block-emit → false negative (shows stale). Complexity disproportionate to benefit — turn boundaries are already the natural cadence; the per-turn cost is bounded. |
| **B. Cadence bound (every N turns)** | Arbitrary choice of N; doesn't match the seam cadence (every non-retry turn is already the natural boundary). Adds config surface for no clear benefit. |
| **C. Emit entire project block (project JSON + KV) every turn under stable id** | Wasteful: project JSON, critical notes, history entries are stable — rebuilding them every turn from DB adds unnecessary work. Split design isolates the mutable part (KV) cleanly. |
| **D. Split into project + kv as two separate blocks WITHOUT stable ids (uuid4 each turn)** | Would cause `add_messages` to APPEND both blocks every turn (uuid4 → no supersede), doubling the per-turn checkpoint growth. Stable ids are mandatory. |
| **E. Use RemoveMessage + rebuild (like auto-load REPLACE sweep)** | Works but adds a message mutation: RemoveMessage(this turn) + HumanMessage(next turn). Stable-id supersede is cleaner — one message emission per turn, replaces prior. RemoveMessage is the backstop for edge cases, not the primary path. |

### Refresh Policy Decision: Per-Turn (Unanimous)

The chosen policy is **per-turn refresh** for the KV block, with `is_retry` as the explicit exception (same exception that applies to the full `assemble_context_messages` call). Justification against `DEFAULT_CONTEXT_LIMIT=700000`:

- **Cost bound**: `SharedMetaKVRepository.get_all_as_dict` is a single SQL `SELECT WHERE context_key=?`. KV table schema: `meta_key VARCHAR(128), meta_value TEXT (≤4096), batch≤100`. Worst-case payload ≈ 100 × (128 + 4096) ≈ 422 KB — still < 1% of context window. In practice, typical KV is a few key-value pairs (sub-10 KB).
- **Precedent**: `_is_project_already_injected` at `graph.py:665-684` already re-reads the instance metadata flag every turn (one DB read). Our fix adds exactly one more DB read per turn (the `get_all_as_dict` call). Both reads are already wrapped in `asyncio.to_thread` (non-blocking).
- **Compaction safety**: constant 1-entry contribution to the numerator (stable id, replaces in place). No regression.
- **Correctness**: every agent turn (inject, report, revive) now sees fresh ambient KV. Matches the user's intent: "ambient shared_meta_kv is reliable."

---

## Kill-Switch Design

### Name
`ENSEMBLE_AMBIENT_KV_FRESH`

### Shape
**Shape B** — service-module cached resolver + `_reset_*_for_tests` + one-shot boot INFO.

Rationale: KV freshness is a routing/behavioral pivot (not an enforcement policy), similar to `ENSEMBLE_WC_WAKE_ENQUEUE` (instance_messaging.py:110-197) and `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` (job_recovery_service.py:230-334). Shape A (pydantic config field) is used for enforcement policies with YAML surface; this fix is a behavioral flag with no YAML surface. Shape B is the documented precedent for this class of kill-switch.

### Polarity: Default ON

`ENSEMBLE_AMBIENT_KV_FRESH=1` (or unset / blank / truthy) → per-turn KV refresh ENABLED (the fix behavior).  
`ENSEMBLE_AMBIENT_KV_FRESH=0` (or `false` / `no` / `off`) → KV refresh DISABLED → **cadence-only reversion (W3, canonical wording — identical at phase1-plan.md:24, :362, :414-423, and decisions.md D6): the KV block still EXISTS — it is emitted on turn 1 exactly as with the flag ON — and is simply NEVER REFRESHED on turns 2+.** OFF does not remove the turn-1 block and does not produce stale-but-refreshing behavior; it freezes the turn-1 snapshot.

**Precedent rationale**: D1 adds a **per-turn DB read**. Two precedent classes exist:
1. **Behavior-bug fixes default ON** (proactive compaction, defer-autopromote, governor recursion guard): `=0` disables the bug-fix and restores the old behavior. Operators who want the old behavior set `=0` — they accept staleness.
2. **Enforcement/route pivots default OFF + soak + pre-committed flip** (WC wake, D2.5-FLIP): `=0` means the feature is NOT active; the flip happens after soak.

D1's ON-default is the **behavior-bug fix** class: the per-turn read is correct behavior, and `=0` is the escape hatch for operators who want zero per-turn overhead (accepting staleness). This is the same tradeoff as `ENSEMBLE_PROACTIVE_COMPACTION=0` (disables the 80%/95% pre-triggers; operators accept compaction risk). The per-turn read cost is bounded (see Refresh Policy section), so the ON default is safe to ship.

### Resolver Signature
```python
# daemon/services/context_messages.py (new module-level resolver)
_ENSEMBLE_AMBIENT_KV_FRESH_ENV = "ENSEMBLE_AMBIENT_KV_FRESH"
_ENSEMBLE_AMBIENT_KV_FRESH: bool | None = None
_ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED: bool = False

def _resolve_ambient_kv_fresh() -> bool:
    """Resolve and cache the per-turn KV freshness kill-switch.

    Returns True (default) when ENSEMBLE_AMBIENT_KV_FRESH is unset/blank/
    truthy. False when explicitly set to a falsy value (0/false/no/off).
    Blank / unset → True (ON default, same as proactive_compaction).

    Caching: global _ENSEMBLE_AMBIENT_KV_FRESH boolean, set once.
    Flipping env mid-flight has no effect until restart.
    """
    global _ENSEMBLE_AMBIENT_KV_FRESH
    if _ENSEMBLE_AMBIENT_KV_FRESH is not None:
        return _ENSEMBLE_AMBIENT_KV_FRESH
    raw = os.environ.get(_ENSEMBLE_AMBIENT_KV_FRESH_ENV, "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        _ENSEMBLE_AMBIENT_KV_FRESH = False
    elif raw in ("1", "true", "yes", "on", ""):
        _ENSEMBLE_AMBIENT_KV_FRESH = True
    else:
        logger.warning(
            "%s=%r not recognized; falling back to ON (default). "
            "Valid falsy: 0/false/no/off.",
            _ENSEMBLE_AMBIENT_KV_FRESH_ENV, raw,
        )
        _ENSEMBLE_AMBIENT_KV_FRESH = True
    return _ENSEMBLE_AMBIENT_KV_FRESH

def _reset_ambient_kv_fresh_for_tests() -> None:
    """Clear cached kill-switch state for test isolation."""
    global _ENSEMBLE_AMBIENT_KV_FRESH, _ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED
    _ENSEMBLE_AMBIENT_KV_FRESH = None
    _ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED = False

def emit_ambient_kv_fresh_boot_log() -> None:
    """One-shot boot INFO naming the resolved state. Wired from manager.py."""
    global _ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED
    if _ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED:
        return
    _ENSEMBLE_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED = True
    enabled = _resolve_ambient_kv_fresh()
    logger.info(
        "Ambient KV freshness %s (env %s=%s); restart required to flip. "
        "Default ON — set =0 to revert to legacy cadence (turn-1 block kept, never refreshed).",
        "ENABLED (per-turn fresh)" if enabled else "DISABLED (cadence-only legacy: turn-1 snapshot, no refresh)",
        _ENSEMBLE_AMBIENT_KV_FRESH_ENV,
        os.environ.get(_ENSEMBLE_AMBIENT_KV_FRESH_ENV, "<unset>"),
    )
```

### Wire-Up
- Boot log: `emit_ambient_kv_fresh_boot_log()` wired in `manager.py` alongside `emit_wc_wake_enqueue_boot_log()` and `emit_defer_autopromote_boot_log()` (mirrors the Shape B pattern at `manager.py:797-806`). **Emit-AT-BOOT requirement (S13):** the boot INFO must be wired from `manager.py` boot, NOT lazily on the first `_resolve_ambient_kv_fresh()` call — a lazy emit makes quiet-daemon boot-log grep false-fail (no traffic since restart → line never printed → operator misreads the flag as OFF). Verification greppability is a design requirement.
- Registry: add `"ENSEMBLE_AMBIENT_KV_FRESH"` to `constants.py:594-624` reserved names. (Post-D12, C0 pre-reserves only the TWO surviving names; see decisions.md D5/D12.)

### Restart-to-Flip
`ENSEMBLE_AMBIENT_KV_FRESH=0` + daemon restart → legacy cadence (W3: turn-1 block kept, never refreshed). `ENSEMBLE_AMBIENT_KV_FRESH=1` (or unset) + restart → per-turn KV refresh (fix active).

---

## Test Strategy

### Test Inventory

| Test | Type | New/Existing | Key assertions |
|------|------|-------------|----------------|
| `test_kv_freshness_on_turn2_short_circuit` | Regression (new) | **New** | KV block appears in `assemble_context_messages` output when `project_already_injected=True`; content is fresh |
| `test_kv_stable_id_supersedes` | Regression (new) | **New** | Two calls with same `instance_id` return same message id; `add_messages` supersedes |
| `test_kv_block_absent_on_is_retry` | Regression (new) | **New** | KV block NOT emitted when `is_retry=True` (kill-switch OFF path) |
| `test_kv_freshness_killswitch_off` | Regression (new) | **New** | When kill-switch OFF, the turn-2+ refresh emission is absent — the turn-1 block REMAINS (cadence-only reversion, W3) |
| `test_kv_block_present_on_turn1_when_flag_off` | Turn-1-surface OFF pin (new, **W3**) | **New** | With kill-switch OFF, turn 1 STILL emits the KV block — OFF gates the cadence, not the block's existence |
| `test_ambient_kv_fresh_flag_on_real_service` | Flag-ON (new) | **New** | Real `SharedMetaKVRepository` + `InstanceManager` path; verify `get_all_as_dict` called each turn |
| `test_ambient_kv_fresh_identical_when_off` | Flag-OFF identical (new) | **New** | With kill-switch OFF, output matches baseline (no kv block in short-circuit path) |
| `test_auto_load_skipped_when_project_already_injected` | Existing pin | **Existing** | Auto-load block NOT in turn 2+ output. **Stays green** — fix doesn't touch auto-load |
| `test_project_already_injected_skips_matcher` | Existing pin | **Existing** | Blueprint matcher NOT called on turn 2+. **Stays green** — fix doesn't touch blueprint |
| `test_context_slot_resolves_fresh_flags` | Existing pin | **Existing** | Graph slot re-reads `project_already_injected` each turn. **Stays green** — flag-read unchanged |

### Seam Coverage (W5 — decision: documented subsumption mapping)

Chosen over a parametrized seam test matrix (inject/report/retry/revive × ON/OFF) — the seams share ONE assembly call site, so a cross-product matrix would re-test the same code path through four doors:

- **Subsumption**: every non-retry seam (agent_node dispatch, message inject at `instance_messaging.py:3637-3760`, child-report delivery via `_process_message_with_tracking`, job-event context) reaches `assemble_context_messages` through the same `if not is_retry:` gate (:3637) and the same `project_already_injected` short-circuit — so `test_kv_freshness_on_turn2_short_circuit` + `test_kv_stable_id_supersedes` SUBSUME refresh coverage for the inject/report/revive seams collectively (the seam table :65-77 documents which seams reach assembly; terminal revive relies on the next non-retry turn per that table).
- **Retry/resume skip**: `test_kv_block_absent_on_is_retry` covers the `is_retry=True` + pause/resume skip for all such seams (same gate).
- **Flag-OFF cadence**: `test_kv_freshness_killswitch_off` + `test_kv_block_present_on_turn1_when_flag_off` cover the OFF state for all seams (flag evaluated at the single builder call site).

If a future seam is added that bypasses the `:3637` gate, the CI grep gate on the gate's call site (R3-style) fails — the mapping is then re-derived.

### New Regression Tests (Pre-Fix Worktree Proof Required)

Per the Worktree-Based Regression Proof convention, new test files are copied into a git worktree at the pre-fix commit (`2750c815`), run against the original code, and **must fail with the exact defect symptom**. Then the fix is applied and the tests pass.

**Defect symptom to reproduce**: KV block content is identical across turn 1 and turn 2+ (stale).

```python
# tests/unit/services/test_kv_freshness_cadence.py
# Run at 2750c815: expect FAIL (kv block absent on turn 2+)
# Run after fix: expect PASS (kv block present + fresh on turn 2+)

class TestKVFreshnessCadence:
    """DEFECT 1: KV block is once-per-instance, not per-turn."""

    async def test_kv_freshness_on_turn2_short_circuit(self, ...):
        """Turn 2+ (project_already_injected=True) SHOULD emit a fresh KV block."""
        # Setup: mock repo returns {"topic": "auth", "priority": 2}
        # Turn 1:
        msgs1 = await assemble_context_messages(..., project_already_injected=False)
        # Turn 2:
        msgs2 = await assemble_context_messages(..., project_already_injected=True)
        # Assert: msgs2 contains a kv block with fresh content
        kv_msg = next((m for m in msgs2 if m.id.startswith("kv:"), None)
        assert kv_msg is not None, "KV block must appear on turn 2+"
        assert "topic" in kv_msg.content  # fresh content
        # At 2750c815: this assertion FAILS (kv_msg is None)
        # After fix: PASSES

    async def test_kv_stable_id_supersedes(self, ...):
        """Two calls with same instance_id return same stable id for kv block."""
        msgs_a = await assemble_context_messages(..., project_already_injected=True)
        msgs_b = await assemble_context_messages(..., project_already_injected=True)
        kv_a = next((m for m in msgs_a if m.id.startswith("kv:"), None)
        kv_b = next((m for m in msgs_b if m.id.startswith("kv:"), None)
        assert kv_a is not None and kv_b is not None
        assert kv_a.id == kv_b.id  # stable id
        # At 2750c815: FAILS (uuid4 → different ids)
        # After fix: PASSES

    async def test_kv_block_absent_on_is_retry(self, ...):
        """Retries MUST NOT emit KV block (is_retry gate)."""
        # This test should PASS at both 2750c815 and after fix
        # — the is_retry gate is preserved
        ...

    async def test_kv_block_absent_when_flag_off(self, ...):
        """Kill-switch OFF → cadence-only reversion (W3): no kv REFRESH emission
        on turn 2+; the turn-1 block remains in the checkpoint."""
        # Setup: ENSEMBLE_AMBIENT_KV_FRESH=0
        _reset_ambient_kv_fresh_for_tests()
        msgs = await assemble_context_messages(..., project_already_injected=True)
        kv_msgs = [m for m in msgs if m.id.startswith("kv:")]
        assert len(kv_msgs) == 0  # turn-2+ call: no refresh emission
        # At 2750c815: PASSES (no kv block at all — but for the wrong reason)
        # After fix with flag=0: PASSES (kv block explicitly unrefreshed)
        ...

    async def test_kv_block_present_on_turn1_when_flag_off(self, ...):
        """Turn-1-surface OFF pin (W3): with the kill-switch OFF, turn 1 STILL
        emits the KV block. OFF is a cadence-only reversion — it gates the
        refresh, not the block's existence."""
        # Setup: ENSEMBLE_AMBIENT_KV_FRESH=0
        _reset_ambient_kv_fresh_for_tests()
        msgs1 = await assemble_context_messages(..., project_already_injected=False)
        kv_msgs1 = [m for m in msgs1 if m.id.startswith("kv:")]
        assert len(kv_msgs1) == 1  # turn-1 block present even with flag OFF
        ...
```

### Flag-ON Real-Service Test

```python
class TestAmbientKVFreshFlagOnRealService:
    """ENSEMBLE_AMBIENT_KV_FRESH=ON through the real service stack."""

    async def test_flag_on_through_real_manager(self, tmp_path):
        """Real SharedMetaKVRepository via file-backed SQLite; verify fresh read each turn."""
        # DB recipe: file-backed SQLite + NullPool + WAL + busy_timeout
        # (mirror test_job_driven_enqueue_work_id_facade.py:76-95 pattern)
        ...
        # Turn 1: emit kv block
        # External writer updates KV via shared_meta_kv tool
        # Turn 2: assemble_context_messages → verify kv block reflects update
        ...
```

### DB Recipe for Write Surfaces

All tests that write to the shared_meta_kv surface use:
```python
engine = create_engine(
    f"sqlite:///{tmp_path / 'test.db'}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
# Set pragmas before any connection
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA busy_timeout=10000"))
    conn.commit()
```
**NOT `StaticPool`** (used by existing shared_meta_kv repo tests for read-only; incompatible with writes across threads).

### Existing Test Assertions That Flip

**These assertions are NOT expected to flip under the fix** — the fix is narrow to the KV block. The existing pins document the behavior that must stay unchanged:

| Test | Location | Assertion | After fix | Status |
|------|----------|-----------|-----------|--------|
| `test_auto_load_skipped_when_project_already_injected` | test_context_messages.py:1648 | `assert "auto_load_skills" not in kinds` | Still True (auto-load unchanged) | **GREEN** |
| `test_project_already_injected_skips_matcher` | test_blueprint_injection.py:271 | `assert _blueprint_messages(persistent) == []` | Still True (blueprint unchanged) | **GREEN** |
| `test_context_slot_resolves_fresh_flags` | test_context_messages.py (implicit) | Flag re-read per turn | Still True (flag-read unchanged) | **GREEN** |

**New assertions that ARE added by the fix** (not flipping existing ones, but new contracts):

| New assertion | Location | Expected outcome |
|---------------|----------|------------------|
| `kv_msg.id.startswith("kv:")` | new test | Stable id format |
| `kv_msg is not None` on turn 2+ | new test | Fresh kv block present |
| `kv_msg.id == prior_kv_msg.id` | new test | Stable id, supersedes |
| `kv_msg not in [m for m in msgs if is_retry]` | new test | No kv on retry |

### Bug-Exercising Proof

1. Revert the fix (cherry-pick or git checkout pre-fix files)
2. Run new regression tests → expect `test_kv_freshness_on_turn2_short_circuit` **FAILS** with `AssertionError: KV block must appear on turn 2+`
3. Re-apply fix → test **PASSES**

---

## Rollout

### Pre-Deployment: Re-Anchor at Latest

**Mandatory**: before branching for implementation, `git fetch origin && git checkout latest` in a new worktree. The implicated-file drift since `2750c815` is **7 commits** (`2750c815..9eebf3ff` — supersedes this draft's 3-commit injected-notes list; see decisions.md **D13**): `d348ad4e`, `80bb61dd`, `d6e30d9d`, `7a899517`, `e321bdb3`, `f965345a`, `53baef57`. Re-anchor all anchors in the implicated files. Key files to re-grep after checkout:
- `daemon/compaction.py`: `ENSEMBLE_INJECTED_NOTES_ABSORB` resolver, `_injected_note_absorbed_ids` caller
- `daemon/config.py`: `resolve_injected_notes_absorb()` boot validation call
- `daemon/services/context_messages.py`: full re-grep of all anchors listed in Root Cause table above

### Branch Strategy
Branch from `latest` on a new worktree: `feature/kv-freshness-cadenza` (or similar). Do NOT branch from the worktree-aware-prompts branch (`2750c815` worktree is externally owned).

### Deployment Steps

1. **Branch** from `latest` on a new worktree
2. **Implement** (per task list in Exit Criterion):
   - Add `_make_context_message` deterministic-id variant
   - Register stable id formats in `constants.py`
   - Implement `_build_kv_context_message`
   - Add `ENSEMBLE_AMBIENT_KV_FRESH` Shape B resolver + boot log
   - Wire boot log in `manager.py`
   - Refactor `assemble_context_messages`: turn 1 path (split block) + turn 2+ path (kv-only)
   - Add new regression tests + run at pre-fix commit to confirm FAIL
   - Apply fix + confirm tests PASS
3. **Run existing suites** to confirm no regression (pins stay green):
   - `pytest tests/unit/test_context_messages.py`
   - `pytest tests/unit/test_blueprint_injection.py`
   - `pytest tests/unit/test_shared_meta_kv_tool.py` (no changes, must stay green)
4. **Ship**: merge to `latest`, push, deploy
5. **Restart daemon** to activate kill-switch (default ON)
6. **Post-deploy verification** (operator walkthrough):
   - `grep "Ambient KV freshness ENABLED" data/logs/ensemble.log` — confirms boot log
   - `grep "ENSEMBLE_AMBIENT_KV_FRESH" .env` or unset (default ON) → fresh KV on every turn
   - External KV write via `shared_meta_kv` tool → next agent turn reflects updated value
   - Governor `council_manifest` inspection → shows fresh ambient data
   - Project-manager `pm_leader_instances` → shows fresh ambient data

### Kill-Switch Flip (If Needed)
```bash
# Instant revert path (accepts stale KV, no per-turn read):
echo "ENSEMBLE_AMBIENT_KV_FRESH=0" >> $INSTALL_DIR/.env
# Restart daemon
./scripts/upgrade/restart.sh
# Verify: grep "Ambient KV freshness DISABLED" data/logs/ensemble.log
```

---

## Risks + Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Compaction interaction**: if `_format_kv_metadata_section` output changes in format (e.g., key ordering), the kv block id is stable but content changes — checkpoint stores fresh content each turn. Compaction still sees it as one entry. | Low | Low | Stable-id replaces in place; compaction sees constant 1-entry contribution. Run `pytest tests/unit/test_compaction.py` after fix. |
| 2 | **Concurrency**: if two concurrent turns (from two message deliveries to the same instance) race `get_all_as_dict` vs a KV write, the worst case is one turn sees slightly stale data for one turn. | Low | Low | This is acceptable for ambient data (best-effort freshness). Production systems rarely have true concurrent turns for the same instance. |
| 3 | **The `_resolve_ambient_kv_fresh()` resolver is new**: if the env variable name collides with a future flag, the registry discipline in `constants.py:594-624` prevents silent override. | Low | Low | Register `"ENSEMBLE_AMBIENT_KV_FRESH"` in constants before shipping. |
| 4 | **Split block changes the API GET /messages output**: API re-runs assembly live (`persistence.py:914`). The API path calls `assemble_context_messages` and will now emit BOTH blocks. Frontend receives two `[SYSTEM CONTEXT: Related Project]` messages. | Medium | Medium | The API path (`persistence.py:914`) goes through the same `assemble_context_messages`. Both blocks will appear in GET /messages. The frontend may need to handle two blocks instead of one. Mitigation: document the new layout; coordinate with frontend team. Check `frontend/src/...` for context-message rendering before shipping. |
| 5 | **Re-anchor miss**: if newer commits (7-commit drift set, `2750c815..9eebf3ff` — D13) touch `context_messages.py` itself, anchors in this plan are stale. | Medium | Low | Mandatory re-anchor step before implementation. |
| 6 | **SharedMetaKVRepository raises**: `_fetch_kv_metadata` catches exceptions and returns `None` (existing behavior). The kv block will not be emitted if the repo raises — acceptable degradation (safe fallback). | Low | Low | Existing exception handling at `context_messages.py:984-991` is unchanged. |
| 7 | **Skills block grows unbounded** even without this fix; the fix adds a constant 1-entry overhead. | Low | Low | No change to skills behavior; compaction continues to trigger at 80%/95%. |
| 8 | **Kill-switch OFF freezes the KV block at its turn-1 snapshot**: operators who set `ENSEMBLE_AMBIENT_KV_FRESH=0` get the turn-1 block and NO refresh on turns 2+ (cadence-only reversion, W3 — the block's existence is unchanged, only the refresh cadence). Documented in boot log and `.env.example` so nobody expects per-turn freshness while OFF. | Low | Low | W3 wording is canonical across phase1-plan.md:24/:280/:362/:414-423 and decisions.md D6; turn-1-surface OFF pin (`test_kv_block_present_on_turn1_when_flag_off`) prevents accidental suppression regressions. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | KV block appears in context on turn 2+ | New regression test `test_kv_freshness_on_turn2_short_circuit` | PASS |
| 2 | KV block uses stable id `kv:{instance_id}` | New regression test `test_kv_stable_id_supersedes` | PASS; `kv_msg.id == prior_kv_msg.id` |
| 3 | KV block is absent on `is_retry` turns | New regression test `test_kv_block_absent_on_is_retry` | PASS |
| 4 | Kill-switch OFF = cadence-only reversion (no refresh emission on turn 2+; turn-1 block retained) | New regression tests `test_kv_block_absent_when_flag_off` + `test_kv_block_present_on_turn1_when_flag_off` (W3) | PASS |
| 5 | Existing auto-load pin stays green | `pytest tests/unit/test_context_messages.py::TestAutoLoadBlock::test_auto_load_skipped_when_project_already_injected` | PASS |
| 6 | Existing blueprint pin stays green | `pytest tests/unit/test_blueprint_injection.py::TestBlueprintInjection::test_project_already_injected_skips_matcher` | PASS |
| 7 | shared_meta_kv tool tests stay green | `pytest tests/unit/test_shared_meta_kv_tool.py` | PASS (all) |
| 8 | Per-turn overhead bounded | Manual: measure `get_all_as_dict` call count per session (log hook) | ≤ 1 extra call per non-retry turn |
| 9 | Boot log emitted | `grep "Ambient KV freshness" data/logs/ensemble.log` after restart | "Ambient KV freshness ENABLED" present |
| 10 | Governor `council_manifest` sees fresh data | Manual: write KV via tool, send message to governor, inspect context | KV value reflected in context |
| 11 | Worktree-aware prompts immune | No agent prompt file modified | `git diff agents/` shows zero changes |

---

## Sequencing Inputs

### Position in D2 → D3 → D1 Order

This plan is **Phase 1** of 3, implementing **DEFECT 1 (KV snapshot cadence)**. The full sequence:

```
D2 (mispartition) → D3 (suppression) → D1 (cadence) ← YOU ARE HERE
```

### Dependencies on D2 and D3

- **D2 (mispartition)**: Depends on the stable-id scheme in this plan. D2's `_persistent_parent_id` fix uses `_make_context_message(id_=...)` to emit the critical-notes block under a stable id — the same prerequisite.
- **D3 (suppression)**: Depends on the stable-id scheme AND the split block architecture. D3's system-default suppression refactors the project block handling in `assemble_context_messages` — the same turn-1/turn-2+ split makes suppression easier to implement cleanly.

### What §Shared Prerequisite Unblocks

The §Shared prerequisite (deterministic-id variant of `_make_context_message`) is **already required by D2 and D3** — implementing it first means D2 and D3 can branch from the same base without conflicts. The stable id registration in `constants.py` is shared. The Shape B kill-switch pattern is also shared (D2 may need a kill-switch; D3 definitely does not).

### Re-Anchor Step (Mandatory Before Implementation Kickoff)

```
git fetch origin
git checkout latest
git log --oneline -5  # confirm c2142c69 is present
grep -n "ENSEMBLE_INJECTED_NOTES_ABSORB\|injected_notes_absorb" daemon/compaction.py daemon/config.py
# All anchors in this plan must be re-verified at latest
```

All file:line references in this plan are anchored at worktree `2750c815`. The injected-notes-absorb arc touches `daemon/compaction.py` and `daemon/config.py` — neither is in the direct fix path for D1, but any shared code (e.g., `constants.py` if the registry is extended, or `manager.py` if the boot log wiring changes) must be checked.

### What This Plan Does NOT Touch (D2/D3 territory)

- `instance_messaging.py:2860-2900` — the `project_injected` flag capture / stamp (D2's `_persistent_parent_id=None` fix lives here)
- `child_reports.py:466/:3735` — child-report delivery path (D2 may add explicit parent_id propagation)
- `manager.py:6790-6846` — job-event context manager (D2 may add `parent_id` context)
- `context_messages.py:1319` — `_resolve_tree_root_id` call site (D2's mispartition fix changes what is passed here)

---

## Open Questions

| # | Question | Owner | Blocking? |
|---|----------|-------|-----------|
| 1 | Does the frontend (`frontend/src/...`) render `[SYSTEM CONTEXT: Related Project]` messages? If so, it will now see TWO such messages (project block + kv block). Does it deduplicate by title? | Frontend team | Medium — may need frontend change before ship |
| 2 | Should the KV block use a new `CONTEXT_KIND_KV_METADATA` enum value (distinct from `CONTEXT_KIND_PROJECT`) to allow FE to key on kind separately? Or is stable-id sufficient? | Frontend team | Low — stable id is sufficient, but kind split is cleaner |
| 3 | Is the per-turn `get_all_as_dict` call acceptable under prod load (1000+ concurrent instances)? Each call acquires the `_set_many_lock` (threading.RLock) in the repo. Under high concurrency this is a point of serialization. | Architect | Low — the lock is held only for the duration of the SELECT; typical KV reads are sub-ms |
