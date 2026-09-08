# Phase 3: Fix DEFECT 3 — System-Default Project KV Suppression

Date: 2026-09-07 (worktree @ 2750c815)
Author: planner[v2] via plan-creation worker (defect 3 of `kv-ambient-awareness-fix`)
Status: Draft — ready for review
Inputs:
- Explorer re-verification @ 2750c815 (architect `c5ae6d95` §1a/§6)
- `daemon/services/context_messages.py:1319-1360` (assemble_context_messages KV path)
- `daemon/services/context_messages.py:411-475` (build_project_context_message)
- `daemon/services/context_messages.py:519-536` (build_project_scope_guide_message)
- `daemon/services/context_messages.py:85-111` (_make_context_message factory)
- `daemon/services/instance_messaging.py:3573-3657` (persistent_context_msgs seam)
- `tests/unit/test_context_messages.py:1199-1233` (the pin that must be flipped)
- Sibling plans: `phase1-plan.md` (stable-id scheme — Shared prerequisite), `phase2-plan.md` (defect 2 mispartition — must land BEFORE defect 3)

---

## Objective

Remove the unconditional KV suppression on the first turn of any instance whose `project_id` is the system-default project (`__system_default__`), so ambient KV metadata becomes visible to default-project consumers (councils, PM sessions, orphan-job workers) — matching the visibility non-default-project trees already enjoy. Ship behind a kill-switch (default ON) so the fix can be reverted instantly if the ambient surface breaks a default-project prompt.

Single testable sentence: *Under `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=1` (default), a first turn on the system-default project produces a `[SYSTEM CONTEXT: Shared Meta KV]` HumanMessage in the persistent context block whenever the tree-root partition has at least one KV row; under `=0` behavior is byte/value-identical to current.*

---

## Scope

### In Scope

1. **Configurable gate** on the system-default branch of `assemble_context_messages` (context_messages.py:1341-1345) — replace the unconditional skip with a `_resolve_kv_ambient_system_default_enabled()` gate (Shape A: pydantic `ContextMessagesConfig` field + explicit `_resolve_*` helper, mirroring `ENSEMBLE_PROACTIVE_COMPACTION` precedent at config.py:805-863 / :2147-2215).
2. **New KV host block** — emit a standalone `[SYSTEM CONTEXT: Shared Meta KV]` HumanMessage via a new `build_shared_meta_kv_message(kv_metadata)` builder using a new `CONTEXT_KIND_SHARED_META_KV` enum value; render only when the tree-root partition is non-empty (skip silently on empty/None — no host noise).
3. **Stable-id compliance** — the new builder must produce a deterministic per-tree-root id, per the `phase1-plan.md` Shared prerequisite (`synthetic-{kind}-{tree_root_id}-{idx}`-family; this plan is downstream of phase1, so the exact id format is locked in there — defect 3 inherits it).
4. **Boot log line** — single INFO line at module import resolution time stating resolved gate value (e.g. `[ContextMessages] kv_ambient_system_default_enabled=True`), placed alongside the resolver return so an operator can grep boot logs to confirm the live state. Restart-to-flip is intentional; the boot line is the verification surface.
5. **Registry reservation** — add the env name to `daemon/constants.py:594-624` registry block as `RESERVED` (per B.S.8 PARTIAL discipline; `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` precedent).
6. **Test surface (new + flipped)** — see *Test strategy* below.
7. **Touch points**: `daemon/services/context_messages.py` (gate + new builder + new enum), `daemon/config.py` (field + resolver), `daemon/constants.py` (registry entry + name constant), `tests/unit/test_context_messages.py` (flip pin + new OFF identical-pin + new ON through-real-service test).

### Out of Scope

- **Scope-guide content edits** (`build_project_scope_guide_message`, `_PROJECT_SCOPE_GUIDE_CONTENT`) — the scope guide remains the right UX for orphan-job workers and PM sessions; we add a sibling block, not a replacement.
- **`build_project_context_message` refactor** — the existing project-context KV section at :460 (`_format_kv_metadata_section`) continues to render for non-default projects. We are NOT splitting KV rendering across two builders for non-default projects.
- **Default-project `to_dict()` rendering** — the "unhelpful default-project JSON dump" rationale at :1347-1351 stays as a UX choice (no JSON dump) — only the KV suppression is removed.
- **API synthetic-id scheme changes** at `daemon/persistence.py:942-945` — that scheme is independent of the HumanMessage stable-id scheme (defect 1's cadence-fix scope). Only the read-stability of the new block's id is required.
- **`context_messages.py:964-997` `_fetch_kv_metadata` semantics** — already swallows repo exceptions + returns `None` for missing repo. Sufficient as-is; no defensive wrapping change needed.
- **Stable-id scheme itself** — `phase1-plan.md` owns this. Defect 3 inherits the deterministic-id contract; it does not introduce one.
- **Mispartition fix** (`phase2-plan.md` defect 2) — already lands before this phase. Defect 3 binds against the corrected tree-root key without modifying the resolver.
- **Agent prompt documentation** — per the no-regression surface (worktree-aware prompts merged `192dee4e`), no agent prompt file under `agents/` may describe the new ambient KV mechanism; ambient becomes reliable, explicit channels stay primary.
- **Tool-schema forms** — `action="set"` and other obsolete `shared_meta_kv` docstrings in `agents/governor/tools_note.md:100-115` are explicitly out of scope (pre-existing tech debt).
- **Cross-instance partition sharing** — KV rows are scoped to a tree-root key today; this plan does not introduce default-project cross-sharing semantics. Default-project trees continue to share one partition per root.

---

## Root Cause (file:line, quoted)

`daemon/services/context_messages.py`:

```python
# Lines 1331-1337 (detection)
is_system_default = (
    project_id == _constants.SYSTEM_DEFAULT_PROJECT_ID
    or (
        project is not None
        and getattr(project, "name", None) == SYSTEM_DEFAULT_PROJECT_NAME
    )
)

# Lines 1339-1345 (gate — THE BUG)
# KV metadata is only consumed by build_project_context_message;
# skip the DB read for system-default instances (scope guide path).
kv_metadata: dict[str, Any] | None = None
if not is_system_default:
    kv_metadata = await asyncio.to_thread(
        _fetch_kv_metadata, context_key, manager
    )

# Lines 1347-1360 (host branch — only fires for is_system_default)
if is_system_default:
    # Inject the scope guide instead of the unhelpful default-project
    # JSON dump.
    project_msg = build_project_scope_guide_message()
    persistent_msgs.append(project_msg)
else:
    project_msg = build_project_context_message(
        project=project, critical_notes=critical_notes,
        kv_metadata=kv_metadata, history_entries=history_entries,
    )
    if project_msg is not None:
        persistent_msgs.append(project_msg)
```

Two compounded defects:

1. **The fetch is skipped entirely on the default-project branch** (`if not is_system_default:` at :1342). The DB round-trip never happens; `kv_metadata` stays `None` for the entire default-project tree.
2. **No host outside `build_project_context_message`** (the only KV renderer is `_format_kv_metadata_section` at :460, called inside `build_project_context_message`). The scope-guide branch at :1347-1351 substitutes a *different* message (`build_project_scope_guide_message`, def at :519) — which does not take a `kv_metadata` parameter and has no KV section. So even if the gate were flipped unconditionally, there would be nowhere to render the KV.

The combined effect: **every instance whose `project_id` resolves to the system-default project gets a persistent context block with zero KV rows, on every first turn**, regardless of whether the tree-root partition actually has rows. Because children inherit `project_id` (`instance_messaging.py:2933-2935`), the entire default-project session tree is KV-silent — matching the operator critical note (worktree-aware-prompts / architecture-recommendation.md §6).

**Why the branch exists**: in-code rationale at :1339-1340 ("KV metadata is only consumed by build_project_context_message; skip the DB read for system-default instances"). Prior plan audit (`.agents/shared/planning/system-default-project/`) confirms the system-default project is a job-system construct (orphan-job home, fixing DeadLetterItem NOT NULL crashes) and contains zero prior reasoning about KV or context injection. The suppression is a presentation-side addition that, today, drops an entire class of ambient signal for default-project tasks.

**Structural fix**: a single-line gate flip is insufficient. The fix needs BOTH (a) a configurable gate + (b) a KV host. The KV host is the bigger design call — see *Fix approach*.

---

## Fix Approach

### KV host choice: STANDALONE BLOCK (chosen)

A new `build_shared_meta_kv_message(kv_metadata)` builder that emits one `[SYSTEM CONTEXT: Shared Meta KV]` HumanMessage, gated by the new env, rendered only when the partition is non-empty.

**Why standalone over appended-to-scope-guide:**

- **Clean stable-id semantics.** A standalone block has its own `context_kind` (`CONTEXT_KIND_SHARED_META_KV`) and its own per-tree-root deterministic id (per phase1 Shared prerequisite). Appending to the scope guide would couple two concerns — scope-guide (orchestration UX) and KV (ambient session state) — under one id, making future disable/enable swaps racy.
- **Symmetry with `_format_kv_metadata_section` model.** The non-default path renders KV inside `build_project_context_message`; the new block renders KV alone. Same data, different host. Avoids the inconsistency of "KV inside project JSON for real projects, KV inside scope-guide for default project" which would tie the scope guide's lifecycle to KV's lifecycle.
- **Empty-partition skip is straightforward.** Standalone block returns `None` when `kv_metadata` is empty/None — caller appends nothing, no empty-host noise. Appending to the scope guide would either always append (renders an empty "Shared Meta KV" header when the partition is empty) or require special-case scoping of the scope-guide's body — neither is clean.
- **Per-phase iteration safety.** Defect 1 (cadence refresh, sibling) will eventually need to refresh the ambient KV block across turns. A standalone block with its own kind is the natural supersede target (`RemoveMessage`-by-kind). Appending to the scope guide would couple refresh to scope-guide stability.

**Alternatives considered and rejected:**

- *Append KV section to `build_project_scope_guide_message` body* — couples UX-guide content to ambient-data lifecycle; empty-partition handling is awkward; future defect-1 refresh target is unclear.
- *Render KV inside `_PROJECT_SCOPE_GUIDE_CONTENT` block via a new section constant* — same as above; worse, it requires editing the static guide content string.

### Builder sketch (locked in by this plan, not the spec; the spec is the *what*)

`daemon/services/context_messages.py` (additive, near line 519):

```python
CONTEXT_KIND_SHARED_META_KV = "shared_meta_kv"

def build_shared_meta_kv_message(
    kv_metadata: dict[str, Any] | None,
    *,
    stable_id: str | None = None,  # supplied by caller from phase1 helper
) -> HumanMessage | None:
    """Build the ``[SYSTEM CONTEXT: Shared Meta KV]`` message.

    Mirrors the KV render path that ``build_project_context_message``
    uses for non-default projects, but as a standalone block so the
    system-default project path (which substitutes the scope guide
    instead of the project JSON dump) also surfaces ambient KV.
    Returns ``None`` when the partition is empty / ``None``.
    """
    if not kv_metadata:
        return None
    payload = json.dumps(kv_metadata, sort_keys=True, indent=2)
    # W10 (2026-09-08 revision): bound the serialized payload. The standalone
    # host must carry the SAME value-size discipline as the rest of the KV
    # surface: cap the serialized body at 32k; on overflow log a WARNING and
    # SKIP the block (skip-on-overflow — same observable outcome as an empty
    # partition, never a truncated/garbage block).
    if len(payload) > 32 * 1024:
        logger.warning(
            "[ContextMessages] shared_meta_kv payload exceeds 32k "
            "(%d bytes); skipping ambient KV block this turn",
            len(payload),
        )
        return None
    body = escape_for_context_block(payload)
    msg = _make_context_message(
        kind=CONTEXT_KIND_SHARED_META_KV,
        title="Shared Meta KV",
        content=body,
    )
    if stable_id is not None:
        msg.id = stable_id  # phase1 deterministic-id contract
    return msg
```

> **W10 companion note — freshness bounded by compaction cadence for compacted spans:** when a conversation has been compacted, injected blocks inside the compacted span were absorbed/re-emitted verbatim (injected blocks are non-selectable compaction material — blueprint compaction notes). For such spans the ambient KV block's refresh is bounded by the compaction cadence, not by this builder: the per-turn refresh (C3) supersedes the block only on turns where assembly re-emits it. Acknowledged as an inherent bound of the absorb design, not a defect of this fix.

The exact `stable_id` minting helper is provided by phase1's Shared prerequisite; defect 3 does not redefine it. If phase1 lands a `_make_stable_id(kind, tree_root_id, instance_id)` helper, defect 3 calls it; if phase1 lands per-kind `id=` directly inside `_make_context_message` (the simpler scheme), defect 3 benefits without code change.

### Gate design

`context_messages.py:1339-1345` becomes:

```python
# Default-ON; off-by-env. Resolver mirrored on ENSEMBLE_PROACTIVE_COMPACTION
# (config.py:2147-2215); see *Kill-switch design*.
kv_ambient_enabled = _resolve_kv_ambient_system_default_enabled()

kv_metadata: dict[str, Any] | None = None
if not is_system_default or kv_ambient_enabled:
    kv_metadata = await asyncio.to_thread(
        _fetch_kv_metadata, context_key, manager
    )
```

Then the host branch at :1347-1360 becomes:

```python
if is_system_default:
    # Always emit the scope guide (UX choice for orphan-job workers,
    # PM sessions).
    project_msg = build_project_scope_guide_message()
    persistent_msgs.append(project_msg)
    # Ambient KV (flag-gated, default ON) when partition is non-empty.
    if kv_ambient_enabled:
        kv_msg = build_shared_meta_kv_message(
            kv_metadata,
            stable_id=_stable_id_for("shared_meta_kv", context_key, instance_id),
        )
        if kv_msg is not None:
            persistent_msgs.append(kv_msg)
else:
    # Unchanged.
    project_msg = build_project_context_message(...)
    if project_msg is not None:
        persistent_msgs.append(project_msg)
```

(The `else:` branch is **byte-identical** to current — that's the OFF-identical pin.)

### Empty-partition behavior

**Decision**: when `kv_metadata` is `None` or empty dict, skip the block entirely (`build_shared_meta_kv_message` returns `None`; caller appends nothing). Pinned by:

- The builder's own `if not kv_metadata: return None` short-circuit.
- The new test `test_kv_ambient_block_skipped_when_partition_empty` (see Test strategy).

Rationale: a default-project session tree with no `shared_meta_kv` partition rows should look identical to today (scope-guide-only). No empty-host noise — matches the existing `_format_kv_metadata_section` empty-string contract at :460.

### Stable-id compliance

The new `build_shared_meta_kv_message` accepts an optional `stable_id` parameter. Phase1's Shared prerequisite (sibling plan) defines the deterministic-id scheme; defect 3 calls it. **Hard dependency on phase1**: this phase cannot ship until phase1's id-mint helper exists, because emitting the new block with a per-call `uuid4()` id would (a) violate the stable-id contract for injected messages (per blueprint: id-less / random-id messages break MessageTapSlot metadata, persistence.py:527-528 moving-timestamp fallback) and (b) cause repeated re-emissions to APPEND rather than replace, growing the persistent block unboundedly across re-emits.

The exact `stable_id` format is owned by phase1; defect 3 only requires that the id be deterministic-per-(kind, tree_root_id, instance_id) tuple.

---

## Kill-Switch Design

### Name

`ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`

Per registry convention `ENSEMBLE_<FEATURE>[_ENABLED]` (proactive compaction, defer-autopromote precedents); reserved at `daemon/constants.py:594-624` registry block as `RESERVED` per B.S.8 PARTIAL discipline (matches `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` precedent).

### Shape

**Shape A** — pydantic `ContextMessagesConfig` field + explicit `_resolve_kv_ambient_system_default_enabled()` resolver in `daemon/config.py`. Mirror of `ENSEMBLE_PROACTIVE_COMPACTION` at `daemon/config.py:805-863` (field + validator) and `:2147-2215` (resolver) and `:2351-2363` (wire-up).

Rationale for Shape A over Shape B (service-module cached resolver): consistent with the immediate peer (`ENSEMBLE_PROACTIVE_COMPACTION`) which solves the same class of bug (silent ambient-suppression; behavior-BUG fix default ON); consistent with the empty-string safe validator pattern (boot never crashes on bare `KEY=` lines); consistent with the bool-vocabulary `_PROACTIVE_TRUE_BOOLS`/`_PROACTIVE_FALSE_BOOLS` precedent at config.py:717-718 (`0`/`false`/`no`/`off` → False; `1`/`true`/`yes`/`on` → True).

Shape B (exemplar `ENSEMBLE_WC_WAKE_ENQUEUE` instance_messaging.py:110-197) is reserved for service-internal gates that have no clean pydantic home; defect 3's gate IS a daemon-config concern and belongs in `daemon/config.py`.

### Polarity + precedent rationale

**Default ON**, `=0` (or `=false`/`=no`/`=off`) disables.

Two precedent classes:

| Class | Precedents | Default | When to use |
|-------|------------|---------|-------------|
| **Behavior-BUG fixes** | `ENSEMBLE_PROACTIVE_COMPACTION`, `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`, `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` | ON (=0 disables) | When the un-fixed behavior is the bug and the fix is the safer state |
| **Enforcement pivots** | `ENSEMBLE_WC_WAKE_ENQUEUE`, `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` | OFF (soak then flip) | When the new behavior is a behavior change with risk of breaking working flows |

Defect 3 is the former: today, every default-project instance silently drops ambient KV — that is the bug. The fix is to make ambient KV visible to default-project trees just as it already is for real-project trees. There is no working default-project KV flow to break; the existing flow is broken.

Justification despite content-volume change: the system-default project is *by design* the home for orphan jobs + default-project instances (councils, PM sessions, worktree agents running under the default project). The consumers that gain visibility are exactly the consumers that today are KV-silent by accident. Risk of regression is low — adding KV to a prompt that today has none cannot break an existing consumer that depends on KV being absent (no such consumer exists; KV is opt-in via `shared_meta_kv` write tool calls).

Soak-then-flip is unnecessary; this is a fix-the-bug-on shipment, not a behavior-toggle.

### Boot log

Single INFO line, emitted at config-resolution time (alongside `_resolve_proactive_enabled` logging style at config.py:2351-2363):

```python
logger.info(
    "[ContextMessages] kv_ambient_system_default_enabled=%s "
    "(env ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED)",
    kv_ambient_enabled,
)
```

Operators verify the live state via boot log grep — same pattern as the `Creating PostgreSQL engine` boot marker for prod-truth verification. **Emit-AT-BOOT requirement (S13, 2026-09-08 revision):** this line is emitted at config-resolution time (boot), and must STAY there — a lazy first-call emit would make quiet-daemon boot-log grep false-fail (no traffic since restart → line never printed → operator misreads the flag as OFF). Reviewer gate: the C2 diff must not move the INFO line into a lazily-called resolver path.

### Restart-to-flip

Restart is required (env-driven config field). This is intentional: a hot-flip of ambient KV rendering would require per-instance `_reset_*_for_tests`-style cached-reset plumbing AND a per-instance re-emit of the persistent context block, which the runtime does not support today. The flip path is: edit `.env`, restart daemon, verify boot log, observe. Pinned at a single config field — no code-path mutation required.

---

## Test Strategy

### 1. THE TEST FLIP — `test_kv_metadata_not_fetched_for_system_default`

`tests/unit/test_context_messages.py:1199-1233`. Old assertion:

```python
assert manager._shared_meta_kv_repo.get_all_as_dict.call_count == 0, (
    "_fetch_kv_metadata must be skipped for system-default "
    "instances — the scope guide path does not consume KV "
    "metadata and the DB read is wasted I/O"
)
```

**New behavior** — split into TWO tests:

#### 1a. `test_kv_ambient_on_default_project_renders_block_when_partition_has_rows` (NEW, flag ON)

```python
def test_kv_ambient_on_default_project_renders_block_when_partition_has_rows(self):
    # Patch consts.SYSTEM_DEFAULT_PROJECT_ID + name fallback (precedent :1098-1109)
    # Patch _resolve_kv_ambient_system_default_enabled to return True (test-side flag).
    # Setup manager with KV repo whose get_all_as_dict returns {"key": "value"}.
    result = _flatten_context_result(self._run(
        assemble_context_messages(
            instance_id="inst-1", project_id="default-id", ...
        )
    ))
    # KV repo WAS called (this is the flip)
    assert manager._shared_meta_kv_repo.get_all_as_dict.call_count == 1
    # Scope guide AND KV block both rendered
    kinds = [m.additional_kwargs["context_kind"] for m in result]
    assert "project_scope_guide" in kinds
    assert "shared_meta_kv" in kinds
    # KV block content matches what was stored
    kv_msg = next(m for m in result if m.additional_kwargs["context_kind"] == "shared_meta_kv")
    assert "key" in kv_msg.content and "value" in kv_msg.content
    # Stable id (deterministic per phase1 contract — assertion shape owned by phase1)
    assert kv_msg.id == _expected_stable_id_for("shared_meta_kv", tree_root, instance_id)
```

#### 1b. `test_kv_ambient_disabled_keeps_old_no_fetch_behavior` (KEPT-AS-PIN, flag OFF)

```python
def test_kv_ambient_disabled_keeps_old_no_fetch_behavior(self):
    # Same setup as the old test, with flag forced OFF.
    self._run(assemble_context_messages(...))
    assert manager._shared_meta_kv_repo.get_all_as_dict.call_count == 0, (
        "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=0 must preserve "
        "the original skip-the-DB-read behavior — guard the revert path"
    )
```

This is the OFF-identical pin — byte/value-identical to the old assertion body, with the comment updated to reference the kill-switch.

### 2. NEW — `test_kv_ambient_skipped_when_partition_empty`

`tests/unit/test_context_messages.py` (new test). Default-project + flag ON + `get_all_as_dict` returns `{}`:

```python
def test_kv_ambient_skipped_when_partition_empty(self):
    # ... setup with KV repo returning empty dict ...
    result = _flatten_context_result(self._run(assemble_context_messages(...)))
    kinds = [m.additional_kwargs["context_kind"] for m in result]
    # Scope guide still renders (UX choice unchanged)
    assert "project_scope_guide" in kinds
    # KV block DOES NOT render (empty-partition skip)
    assert "shared_meta_kv" not in kinds
    # But the fetch DID happen (gate is ON; builder returned None)
    assert manager._shared_meta_kv_repo.get_all_as_dict.call_count == 1
```

### 3. NEW — `test_kv_ambient_real_service_flag_on_through_assembler` (W6: REAL service, REAL repo, file-backed SQLite)

`tests/integration/` (new test). **No MagicMock manager and no patched `_resolve_*_enabled`** — the flag-ON path must be exercised through the REAL service stack:

- Real `Config`/pydantic field (`kv_ambient_system_default_enabled: True`) passed through the real resolver.
- Real `InstanceManager` + **real `SharedMetaKVRepository`** on file-backed SQLite (`tmp_path` + NullPool + WAL + busy_timeout — the §6 recipe below, NOT StaticPool, NOT MagicMock).
- Seed the tree-root partition with a real `repo.set(...)`, run the assembler's default-project branch, and assert the `Shared Meta KV` block renders with the seeded row and the correct tree-root `context_key`.

Mirrors `tests/test_injection_api.py:372-411` real-router precedent and `tests/integration/test_job_driven_enqueue_work_id_facade.py:76-95` for the DB recipe.

```python
def test_kv_ambient_real_service_flag_on_through_assembler(self, tmp_path):
    # Real ContextMessagesConfig field = True (no test-side patching of the resolver)
    # Real manager + real SharedMetaKVRepository on file-backed SQLite (W6)
    # ... assemble through the real default-project branch; assert block + content ...
```

### 4. PINS THAT MUST STAY GREEN (scope-guide path unchanged)

`tests/unit/test_context_messages.py:1096-1197`:

- `test_scope_guide_when_system_default_project_id` (:1096)
- `test_scope_guide_when_project_name_is_default` (:1126)
- `test_normal_project_context_when_real_project` (:1146)
- `test_scope_guide_when_project_payload_is_none_but_id_is_default` (:1168)
- `test_scope_guide_in_first_position_of_persistent_block` (or whatever the first-position pin is named; the one that asserts `[0].additional_kwargs["context_kind"] == "project_scope_guide"` at :1119)

All four MUST stay green unmodified — the scope-guide path is unchanged. They verify the substitute-UX-message still fires before any sibling block; defect 3's KV block is a *second* persistent message, not a replacement.

### 5. NEW — `test_kv_block_position_after_scope_guide`

`tests/unit/test_context_messages.py` (new test). Asserts the assembly order — scope-guide first (UX), KV block second (ambient data):

```python
def test_kv_block_position_after_scope_guide(self):
    # ... default-project + flag ON + non-empty partition ...
    kinds = [m.additional_kwargs["context_kind"] for m in result]
    scope_idx = kinds.index("project_scope_guide")
    kv_idx = kinds.index("shared_meta_kv")
    assert scope_idx < kv_idx, "scope guide must precede KV block in persistent block"
```

### 5b. NEW — W7 cross-flag composition tests (named cells of the D4 2×2)

Two NEW cross-flag independence tests — flipping either flag alone must produce exactly its D4 table row, not a blend (see decisions.md D4 cell-pin table):

```python
def test_composition_c2_off_c3_on(self):
    """D4 cell OFF×ON: host flag =0, refresh flag ON → NO KV block on any turn.
    The refresh flag alone must NOT re-add a suppressed block."""
    # ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=0, ENSEMBLE_AMBIENT_KV_FRESH=1
    # default-project tree, non-empty partition
    # assert: no shared_meta_kv kind on turn 1 AND on turn 2+

def test_composition_c2_on_c3_off(self):
    """D4 cell ON×OFF: host flag ON, refresh flag =0 → KV block emitted on
    turn 1, NEVER refreshed on turns 2+ (cadence-only reversion, W3)."""
    # ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=1, ENSEMBLE_AMBIENT_KV_FRESH=0
    # default-project tree, non-empty partition
    # assert: shared_meta_kv present on turn 1; absent from turn-2+ refresh emissions
```

(The diagonal cells already have their per-phase tests: ON×ON = test 1a + phase1 refresh suite; OFF×OFF = test 1b + `test_kv_block_absent_when_flag_off` + `test_kv_block_present_on_turn1_when_flag_off`.)

### 5c. NEW — W9 synthetic-id enumerate-order pin

Pin for `persistence.py:937-949` (id mint at `:949`): for a fixed context-message list, each synthetic `message_id` suffix `{idx}` MUST equal the message's position in the `GET /messages` array (enumeration order = array order; no re-sort between enumerate and return). The FE merge layer keys on these ids — order drift breaks merge integrity.

### 6. NO-REGRESSION — worktree-aware prompt pins

`tests/unit/test_shared_meta_kv_tool.py` (:84, :109, :133, :161, :194, :216) — must stay green; tree-root reads in the tool surface are unchanged. Defect 3 modifies the ambient block, not the tool's tree-root resolution.

`tests/test_injection_api.py:401-411` (context= enqueue-only forcing) — must stay green.

`.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34` — prompt byte-identity fences guarded regions; no agent prompt file modified.

### 7. DB recipe (W6 — updated)

Test 3 (flag-ON through real service) writes to the `shared_meta_kv` surface and therefore uses the file-backed SQLite recipe: `tmp_path` + NullPool + `PRAGMA journal_mode=WAL` + `busy_timeout=10000` with a **real `SharedMetaKVRepository`** — same discipline as phase2's C1′ partition pin and phase1's flag-ON test. The original draft's MagicMock-manager waiver is DROPPED (W6): mocked KV repos mask the fetch/render contract this phase exists to prove. Tests 1a/1b/2 (unit-level gate/pin tests that only count `get_all_as_dict` calls) may keep the lightweight in-memory mock — they do not exercise writes.

### 8. Bug-exercising proof (Worktree-Based Regression Proof)

Per the test convention: copy the three NEW tests (1a, 2, 5) into a git worktree at a pre-fix commit (worktree pin will be set to `2750c815^` or earlier — the merge base of `feature/kv-ambient-awareness-fix` with worktree-aware prompts), run them, observe the exact original failure mode (`call_count == 0`, missing `shared_meta_kv` kind). Record the worktree SHA in the test docstring as the regression-pin reference.

---

## Rollout

### Branch & anchor

- **Branch**: `feature/kv-ambient-awareness-fix` (created by planner/dispatcher, not by this plan). Defect 3 ships as the third commit on this branch (after phase1 stable-id scheme + phase2 mispartition fix), atomic per defect.
- **Anchor re-pin**: at implementation kickoff, **re-verify all anchors** in the latest commit. The implicated-file drift since 2750c815 is **7 commits** (`2750c815..9eebf3ff` — decisions.md **D13**, superseding the stale 3-commit injected-notes list this draft carried): `d348ad4e` (tidier doc-truth/comment fixes), `80bb61dd` (the DEFECT 2 fix — already in base), `d6e30d9d` + `7a899517` + `e321bdb3` + `f965345a` + `53baef57` (LCA arc: judge punch-list, inline LLM report judge, enqueue-lane stamping, nudge embeds, conditional attestation). Re-grep:
  - `context_messages.py:1331-1360` — may have shifted; confirm system-default gate structure unchanged
  - `context_messages.py:519-536` — `build_project_scope_guide_message` signature unchanged
  - `context_messages.py:85-111` — `_make_context_message` factory unchanged
  - `constants.py:110-111` — system-default project identifiers unchanged
  - `constants.py:594-624` — registry block format unchanged
  - `tests/unit/test_context_messages.py:1199-1233` — old pin still present at that line
  - `config.py:805-863, :2147-2215` — `ENSEMBLE_PROACTIVE_COMPACTION` precedent intact

If any anchor shifted, this plan is to be updated to match new line numbers BEFORE implementation begins; line numbers in plan are references, not contracts.

### Implementation order (within phase 3 only)

1. Add `CONTEXT_KIND_SHARED_META_KV` enum at context_messages.py:73-79.
2. Add `build_shared_meta_kv_message` builder (near line 519, between scope-guide and shared-context builders).
3. Add gate flip at context_messages.py:1339-1345 (Shape A wiring).
4. Add KV-host branch at context_messages.py:1347-1360.
5. Add config field + resolver + wire-up in daemon/config.py.
6. Add `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` registry entry at daemon/constants.py:594-624.
7. Add boot log line at config resolution time.
8. Flip tests 1a/1b/2/3/5/5b/5c; verify 4 (scope-guide pins) and 6 (worktree-aware pins) stay green.
9. Run full `tests/unit/test_context_messages.py` + `tests/test_injection_api.py` + `tests/unit/test_shared_meta_kv_tool.py`.
10. Run full repo test suite.

### Restart

Config flag → restart required. Rollout checklist:

- [ ] Feature branch merged to `latest`
- [ ] Daemon restart
- [ ] Boot log shows `[ContextMessages] kv_ambient_system_default_enabled=True`
- [ ] Operator verify: spawn a default-project instance (e.g. PM session), confirm first-turn persistent context block contains BOTH `[SYSTEM CONTEXT: Project Scope Guide]` AND `[SYSTEM CONTEXT: Shared Meta KV]` blocks (when partition has rows)

### Post-deploy verification

- Spot-check a default-project session tree (council or PM) for the new KV block in `GET /messages`.
- Spot-check a real-project session tree for unchanged behavior (defect 3 only touches the default-project branch).
- Tail `data/logs/ensemble.log` for any `[ContextMessages]` warnings related to KV fetch failures on default-project instances.

### Kill-switch revert path

If ambient KV breaks a default-project prompt after deploy:

1. Set `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=0` in `.env`
2. Restart daemon
3. Boot log shows `kv_ambient_system_default_enabled=False`
4. Default-project trees return to scope-guide-only behavior (byte-identical to pre-fix)
5. Defect 1 (cadence refresh) and defect 2 (mispartition) continue to operate unaffected — neither depends on defect 3's host

---

## Risks + Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Stable-id scheme from phase1 not yet landed when defect 3 ships | High (id-less messages break MessageTapSlot metadata + persistence.py:527-528 moving-timestamp fallback) | Low (sequencing plan locks phase1 first) | Hard sequencing: phase1 ships → phase2 ships → defect 3 ships. The defect-3 implementation PR is rejected if phase1's `_make_stable_id` helper (or equivalent per-kind id contract) is absent. CI gate: `grep _make_stable_id` in context_messages.py or equivalent. |
| 2 | Default-project consumers break because ambient KV adds unexpected content to their prompts | High (visible behavior change) | Low (the consumers that gain KV are exactly the ones documented to be KV-silent by accident; no consumer depends on KV being absent — KV is opt-in via writes) | Soak window of ≥48h between merge and `=0` kill-switch flip if needed. Default ON means immediate visibility; the kill-switch is the revert path. Soak is implicit (default-on ships with revert-available). |
| 3 | `build_shared_meta_kv_message` produces unstable id → MessageTapSlot drops metadata rows + FE merge ordering breaks (per blueprint Message-id invariant) | High (FE-side metadata loss; silent failure) | Low if phase1 stable-id contract is honored | Stable-id is a hard requirement in the builder API (`stable_id` parameter is required when defect 3 ships, not optional). Test 1a asserts the id; failing that test is a release blocker. |
| 4 | KV block placement confuses downstream consumers expecting scope-guide at index [0] | Medium (downstream filtering breaks) | Low (no documented consumer pattern filters by index; consumers filter by `context_kind`) | Test 5 pins scope-guide-before-KV ordering. If a downstream consumer breaks, the fix is in the consumer (filter by kind, not by index). |
| 5 | Mispartition defect 2 not yet landed → defect 3 binds to a wrong tree-root key | High (defect 3 emits KV under the wrong partition, looks like defect 3 is broken when actually defect 2 is the problem) | Medium (sibling worker schedule slip) | Hard sequencing: defect 3 PR is blocked on phase2's mispartition fix being merged. CI check: the test for defect 3 (`test_kv_ambient_on_default_project_renders_block_when_partition_has_rows`) asserts the correct tree-root key via the existing `get_tree_root_id` patch path; if the partition lookup hits the wrong key, the test fails because no rows are returned. |
| 6 | Registry discipline violation (B.S.8): name added to config.py without `RESERVED` entry in constants.py:594-624 | Low (test catches it) | Low | Plan explicitly requires both: add name to config.py field validation_alias AND add to constants.py registry block. Reviewer grep for `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` in both files. |
| 7 | Shape-A resolver inconsistency: pydantic field validator passes a value that the resolver interprets differently (e.g. the empty-string safe pattern) | Medium (boot crash on bare `KEY=` line) | Low (precedent already handles this; pattern is well-tested) | Mirror the exact empty-string normalization at the new field validator; share the bool-vocabulary constants with `_resolve_kv_ambient_system_default_enabled` (same `_PROACTIVE_*_BOOLS` precedent). |
| 8 | Worktree-aware-prompt byte-identity fences tripped by tool doc updates | Low (the fences are on prompt files, not on the KV tool) | Very low (defect 3 does not modify agent prompt files; tool doc is in `daemon/tools/`, separate path) | Defect 3 makes no edits under `agents/`. Reviewer diff-check: `git diff agents/` is empty. |
| 9 | `json.dumps` in the builder fails on non-JSON-serializable values (e.g. datetime, custom objects in the partition) | Low (renders malformed KV block, swallows KV rather than crashing) | Low (the partition is supposed to store JSON-friendly values; the table schema enforces `meta_value ≤ 4096` text) | Wrap `json.dumps` in try/except → return None on serialization failure (mirroring `_fetch_kv_metadata` swallow-and-log at :988-997). Log WARNING, not ERROR — empty partition is the same outcome. |
| 10 | Latest-branch drift: **7 commits** (`2750c815..9eebf3ff` — decisions.md **D13**, superseding the stale 3-commit injected-notes list) shifted anchors | Medium (line numbers stale; semantic anchors may have changed) | Medium (drift is real and recent) | Re-pin all anchors at implementation kickoff (D13's verified anchor table is the starting point). Plan captures the semantic intent; line numbers are reference-only. If semantic intent breaks (e.g. a drifted commit already addressed defect 3 transitively), abort defect 3 and route to planner for re-scoping. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Default-project first turn emits a `[SYSTEM CONTEXT: Shared Meta KV]` block when partition has rows | New test 1a passes | 100% pass |
| 2 | Default-project first turn emits NO KV block when partition is empty | New test 2 passes | 100% pass |
| 3 | Scope-guide pins stay green (4 existing tests) | Re-run `tests/unit/test_context_messages.py` | 4/4 pass |
| 4 | Worktree-aware-prompt pins stay green (tool + enqueue + prompt byte-identity) | Re-run `tests/unit/test_shared_meta_kv_tool.py` + `tests/test_injection_api.py` + grep agent prompts for KV docstrings | All pass; grep empty |
| 5 | Flag-ON behavior is observable through real service (no test-side patching of the resolver) | New test 3 passes | 100% pass |
| 6 | Flag-OFF behavior is byte/value-identical to pre-fix | New test 1b passes (old assertion body, updated comment) | 100% pass |
| 7 | Registry discipline holds (B.S.8 PARTIAL test) | `tests/unit/test_reserved_env_registry.py` (or equivalent) passes | 100% pass |
| 8 | Boot log line is emitted and grep-able | Operator tail `data/logs/ensemble.log` for `[ContextMessages] kv_ambient_system_default_enabled=...` after restart | Line present, value matches expected |
| 9 | Stable-id contract honored (per phase1 prerequisite) | New test 1a's id assertion passes; MessageTapSlot metadata is not dropped for the new block | Test passes; manual FE spot-check confirms metadata survives roundtrip |
| 10 | Mispartition prerequisite (defect 2) honored | Defect 3 implementation binds to the corrected tree-root key from phase2 | Manual code review + integration test (defect 2's acceptance suite) |
| 11 | Kill-switch revert path works | Set `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=0`, restart, observe boot log + default-project first turn back to scope-guide-only | Observed in staging before prod |
| 12 | `agents/` prompt files unmodified | `git diff agents/` is empty | Empty diff |
| 13 | Full repo test suite passes | Run `pytest` (or project-standard runner) | 100% pass (modulo pre-existing quarantined failures; TestAccessMemoryArchive 5 are explicitly out of scope) |

---

## Sequencing Inputs

### You (defect 3) are AFTER defect 2 and BEFORE defect 1

```
phase1-plan.md §Shared prerequisite (stable-id = C0) ← Shared prerequisite, must land FIRST
       │
       ▼
phase2-plan.md (defect 2 mispartition = C1′ verify-and-pin) ← FIXED AT BASE (80bb61dd);
       │                                                       C1′ pins the contract BEFORE defect 3 (S12)
       ▼
phase3-plan.md (defect 3, THIS PLAN = C2)   ← YOU
       │
       ▼
phase1-plan.md body (defect 1 cadence = C3) ← Refreshing a suppressed block is moot
                                          until you un-suppress it
```

### What you depend on

- **phase1 stable-id scheme**: provides the `_make_stable_id` helper (or equivalent per-kind id contract) that defect 3's `build_shared_meta_kv_message` consumes via its `stable_id` parameter. Without phase1, defect 3 cannot emit the new block — re-emissions would APPEND rather than replace, growing the persistent block unboundedly, and id-less messages would be dropped by MessageTapSlot / fall through to the moving-timestamp fallback at persistence.py:527-528.
- **phase2 mispartition fix — FIXED AT BASE (80bb61dd), verified + pinned by C1′ (D12)**: establishes the correct tree-root key resolution at `assemble_context_messages`. Defect 3's gate calls `_fetch_kv_metadata(context_key, manager)` with the same `context_key`; the C1′ verify gate (tests-only: real-service partition pin + exception-ladder + messaging/tool consistency pins) is what makes it safe for defect 3 to bind against that key — a wrong-partition binding would fail C1′'s pins loudly instead of silently emitting an empty block. Hard ordering per S12: C0 → C1′ → C2 (this phase) → C3.
- **No other plan** in this initiative precedes defect 3. Defect 3 is the third of four steps; it does not depend on phase1's injection-site changes (those are defect 1's domain).

### What you unblock

- **C3 (defect 1 cadence refresh — D9 canonical mapping; the "phase4" label is retired)**: defect 1's job is to refresh the ambient KV block (and other ambient blocks) when stale. Refreshing a block that is suppressed-by-default in the entire default-project branch would have no observable effect for default-project trees. Defect 3 must un-suppress the block first; defect 1's refresh logic then has something to refresh in the default-project case.
- **Governor council_manifest restore visibility in default-project trees**: governor uses `shared_meta_kv` to persist council manifest state. Today, default-project governor sessions (which run under the default project) cannot see the manifest because the block is suppressed. Defect 3 unblocks the governor's documented workflow for default-project trees.
- **PM session + council operator UX**: PM sessions and councils (which run under the system-default project by design) gain ambient KV visibility. They become able to read project_change_scope, decision_log, and other leader-set ambient state via ambient — without needing an explicit `shared_meta_kv` tool call from their end.

### Coupling to non-initiatives

- **Worktree-aware prompts (merged `192dee4e`)**: defect 3 must NOT touch any agent prompt file under `agents/`. The pins in `tests/unit/test_shared_meta_kv_tool.py` and `tests/test_injection_api.py` are pinned-explicit; defect 3 does not modify these surfaces. No coupling, except the no-touch constraint.
- **Latest-branch drift (`2750c815..9eebf3ff`, 7 commits — decisions.md **D13**)**: `d348ad4e` (tidier doc-truth/comment fixes), `80bb61dd` (the DEFECT 2 fix — landed at base), and the LCA arc `d6e30d9d` + `7a899517` + `e321bdb3` + `f965345a` + `53baef57`. Re-pin anchors at implementation kickoff (Risk 10); semantic intent of this plan is unchanged, but line numbers and possibly some adjacent context may have shifted. (The superseded 3-commit injected-notes list `bb4e3e89/4e1e6698/c2142c69` is struck — `git log 2750c815..9eebf3ff` shows that arc touched only `compaction.py`/`config.py`, not the implicated seam files; see the D13 addendum.)
- **Pre-existing tech debt (5 quarantined test failures — TestAccessMemoryArchive)**: explicitly out of scope per the existing QUARANTINE.md discipline. Defect 3's test list must not chase these.
