# Project Blueprint — Phase 2 & Phase 3 Implementation Plan

**Date:** 2026-08-02
**Author:** plan-creation worker (dispatched by planner[v2])
**Status:** Draft — implementation-level detail
**Scope:** Phase 2 (Injection Integration) + Phase 3 (CRUD API + Tool Registration) only.
**Upstream contract:** `.agents/shared/planning/project-blueprint/plan-overview.md` (locked, 832 lines)
**Adjacent work (NOT in this plan):** Phase 1 — DB schema + matching engine (owned by another worker; only consumed via class references below).

---

## Conventions used in this plan

* **File paths** are relative to the project root (`/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`).
* **Line numbers** cite the current source at the time of writing (post-`Critical Notes 2026-07-22`, pre-Blueprint).
* **Code shape** is pseudo-Python for the orchestrator edit (Phase 2) and exact signatures / schema fields for Phase 3.
* **Module references** — Phase 1 owns `daemon/services/blueprint_matcher.py`, `daemon/repositories/blueprint_repository.py`, and the `blueprints` / `blueprint_embeddings` / `blueprint_revisions` tables. We reference these by class name; we do NOT re-specify them.
* **Deferred** items are explicitly listed in the per-phase "Out of scope (this phase)" subsection so a later worker does not have to re-derive the boundary.

---

## Phase 2 — Injection Integration

**Objective (from plan §12 Phase 2 + §6):** Wire blueprint into the persistent block of `assemble_context_messages()`. Match at first-message receipt, freeze for instance lifetime, slot-1 reserved for `core.md`, opt-out via `blueprint_inactive`.

**Spec authority:** plan-overview §5 (matching), §6.1 (integration point), §6.2 (opt-out), §6.3 (message format), §6.4 (5-slot allocation), §6.5 (token budget), §5.4.1 (structured logging).

### 2.1 Touch surface (files to edit)

| File | Change | Why |
|---|---|---|
| `daemon/registry.py` | Add `blueprint_inactive: bool = False` to `AgentMetadata` (after `skill_search_interval`, before `context_injection`); add `blueprint_inactive=meta.get("blueprint_inactive", False)` in `discover()` at **both** the primary (line 515) and the retry-without-`llm_models` (line 561) AgentMetadata constructors. | Opt-out flag plumbing, parallel to existing `skill_injection` pattern (line 275 field, lines 531 & 577 loaders). Cites the C6 retry path. |
| `daemon/services/context_messages.py` | Add `CONTEXT_KIND_BLUEPRINT = "blueprint"` constant next to the existing `CONTEXT_KIND_*` constants (line 70-74); add `build_blueprint_message(matched: list[MatchedBlueprint], instance_id: str) -> list[HumanMessage]` builder near `build_skills_message` (line 807); add an `_assemble_blueprint_block` async helper near `_build_auto_load_block` (line 737); add the new code block between line 1287 and line 1289 in `assemble_context_messages`; export new symbols in `__all__` (line 1379). | Persistent-block integration seam and message builder. |
| `daemon/services/instance_messaging.py` | **No change required** — blueprint piggybacks on the existing `project_already_injected` flag (set at line 2337-2341 after successful first-turn injection). The orchestrator's own `project_already_injected` short-circuit at line 1188 already prevents re-running the persistent block on turn 2+. | Match-once gate reuses the existing flag. Rationale in §2.5. |
| `tests/unit/test_blueprint_injection.py` (new) | End-to-end test spec per §2.10. | Phase 2 exit criterion. |

**Why no change to `instance_messaging.py`:** the call site at line 2971 already passes `project_already_injected=project_already_injected` (read at line 2227 from `instance_metadata.get("project_injected")`). The orchestrator short-circuits on `project_already_injected=True` (line 1188) and skips the entire persistent block — including the new blueprint block we will insert below the `project_injected` gate. Piggybacking avoids a new metadata key, a new DB write, and a new flag-read in the hot path. This is a deliberate design simplification; a future enhancement could split the flags if blueprint matching ever needs to re-run independently of project context.

---

### 2.2 AgentMetadata field — `blueprint_inactive`

**Pattern citation:** parallels `skill_injection: bool = Field(default=False, ...)` at `daemon/registry.py:275-278` (field) and `daemon/registry.py:531` + `:577` (loaders).

**Field definition (insert after line 293, before `context_injection`):**

```python
    blueprint_inactive: bool = Field(
        default=False,
        description=(
            "Opt-out flag for the Project Blueprint injection. "
            "When True, the agent receives NO blueprint messages "
            "on first-message receipt. Default False (active) — "
            "blueprint is opt-OUT, not opt-in. Per plan-overview §6.2: "
            "utility agents (kb-writer, blueprinter) set this to True; "
            "all other agents leave it False (the default)."
        ),
    )
```

**Loader in `discover()` — primary constructor (`registry.py:515`)** — add the line between the existing `skill_search_interval=...` (line 532) and `context_injection=...` (line 533):

```python
                    skill_search_interval=meta.get("skill_search_interval", 1),
                    blueprint_inactive=meta.get("blueprint_inactive", False),  # ← NEW
                    context_injection=context_injection_arg,
```

**Loader in `discover()` — C6 retry-without-`llm_models` constructor (`registry.py:561`)** — mirror the same line between `skill_search_interval=...` (line 578) and `context_injection=...` (line 579). Both constructors MUST receive the field or the retry will drop the flag silently.

**End-to-end change list for `registry.py`:**

1. Add the `blueprint_inactive` field definition after line 293.
2. Add `blueprint_inactive=meta.get("blueprint_inactive", False)` to the primary constructor at line 515+ (between `skill_search_interval` and `context_injection`).
3. Add the same line to the retry constructor at line 561+.
4. Update the `json_schema_extra` example block (around line 359) with `"blueprint_inactive": false` so `agent-registry inspect` style tooling surfaces it. Optional but recommended.

**No change to the agent prompt material in this phase.** `agents/kb-writer/meta.json` and the future `agents/blueprinter/meta.json` will set `"blueprint_inactive": true` (per plan §6.2 table); that is a content edit, not a code edit, and is out of scope for Phase 2.

---

### 2.3 The new code block in `assemble_context_messages`

**Insertion point:** between the closing of the shared-context block (line 1287) and the opening of the auto-load block (line 1289). Per plan §6.1 — "Blueprint becomes a fifth source alongside these... position within the persistent block is alongside skills (after shared context, near skills)."

**Pseudo-code (this is plan-shape, not a literal patch — Phase 1 owns the matcher signature):**

```python
    # ── 2.5. Blueprint message — PERSISTENT (once-per-instance, opt-out) ───
    # Project Blueprint (plan-overview §6.1): a curated, multi-algorithm-
    # matched skeleton of the project's stable architectural knowledge.
    # Built once on the first user turn and checkpointed with the
    # user message so subsequent turns read it from state['messages']
    # for free (matches the persistent-block contract).
    #
    # Two gates:
    #   (a) project_already_injected must be False — we piggyback on the
    #       existing once-per-instance flag (set at
    #       instance_messaging.py:2337 after first-turn injection) so we
    #       do not need a new metadata key or a new flag-read. The
    #       existing short-circuit at line 1188 above already returns
    #       early for turn 2+; this block only runs on the first turn.
    #   (b) blueprint_inactive must be False — utility agents
    #       (kb-writer, blueprinter) opt out via meta.json (plan §6.2).
    #
    # Async I/O contract: assemble_context_messages() is ALREADY async, and
    # BlueprintMatcher.match() is an ``async def``. Await it DIRECTLY — do NOT
    # wrap in ``asyncio.to_thread(lambda: asyncio.run(...))``, which would
    # create a nested event loop and crash. Any sync DB calls inside
    # ``match()`` are individually wrapped in ``asyncio.to_thread(...)`` at
    # their own call sites. A matcher exception degrades to "no blueprints"
    # (the agent still gets core.md if it exists) so a transient DB error
    # never blocks the user message path.
    if not project_already_injected:
        blueprint_inactive = bool(
            getattr(agent_meta, "blueprint_inactive", False)
        )
        if not blueprint_inactive:
            try:
                # Phase 1 owns this class. Expected shape (NOT to be
                # re-specced here):
                #   BlueprintMatcher.match(
                #       project_id: str,
                #       query: str,
                #       max_area: int = 4,
                #       threshold: float | None = None,
                #   ) -> list[MatchedBlueprint]
                # where ``MatchedBlueprint`` is a dataclass with fields
                # id, name, kind, version, content, file_refs, score
                # (master-plan invariant C3).
                matcher = getattr(manager, "_blueprint_matcher", None)
                if matcher is None:
                    matched = []          # manager built without blueprint support
                else:
                    matched = await matcher.match(
                        project_id=project_id, query=user_query,
                    )
            except Exception as exc:
                logger.warning(
                    f"[ContextMessages] Blueprint matching failed for "
                    f"project {project_id}: {exc}"
                )
                matched = []

            # Structured logging per plan §5.4.1 — required from v1.
            # One log line per match (cheap; matching is one-shot).
            logger.info(
                "blueprint_match",
                extra={
                    "instance_id": instance_id,
                    "project_id": project_id,
                    "matched_count": len(matched),
                    "matched_ids": [b.id for b in matched[:5]],
                    "matched_names": [b.name for b in matched[:5]],
                    "top_score": getattr(matched[0], "score", 0.0) if matched else 0.0,
                    "query_source": "task_only",   # §2.6 — Option B v1
                },
            )

            bp_msgs = build_blueprint_message(
                matched=matched,
                instance_id=instance_id,
            )
            for msg in bp_msgs:
                persistent_msgs.append(msg)
```

**Matcher resolution:** the manager-attribute lookup uses `getattr(manager, "_blueprint_matcher", None)` directly inline (no separate `_match_blueprints` wrapper). If the attribute is missing (manager built without blueprint support — e.g. before Phase 1 ships), `matched` falls back to `[]`. Mirrors the `_run_skill_search` defensive-`getattr` pattern at `context_messages.py:1028-1030`.

**Order within `persistent_msgs` (after the insertion):** project → shared_context → **blueprint** (new) → auto_load_skills → skills. The blueprint block sits between shared-context and auto-load skills per plan §6.1.

---

### 2.4 `build_blueprint_message` — message builder

**Spec (per plan §6.3 + §6.4):**

| Slot | Lineage tag | Content | When emitted |
|---|---|---|---|
| 1 | `core` | `core.md` (kind=`core`) | Always if exists in the matcher result |
| 2 | `matched` | Best area match by score | Only if score ≥ threshold |
| 3 | `matched` | Second-best | Only if above threshold |
| 4 | `matched` | Third-best | Only if above threshold |
| 5 | `matched` | Fourth-best | Only if above threshold |

Empty slots are simply absent. If the matcher returns zero area matches AND no `core.md`, the function returns `[]` (no message emitted; the rest of the persistent block still goes through).

**Per-message format (single HumanMessage per slot, NOT one mega-message):**

```
[SYSTEM CONTEXT: Blueprint]
[BLUEPRINT core] core v3
<markdown body, escaped via escape_for_context_block()>
File references:
- daemon/services/context_messages.py @ assemble_context_messages (line ~120)
- docs/architecture/persistent-block.md
Source: blueprint:core v3 | lineage:core
```

For an area match, the header is `[BLUEPRINT matched]` and the footer is `lineage:matched`. Each message is its own `HumanMessage` so the agent can address blueprints individually in the message history and the UI can render them in separate cards.

**Stable message IDs (per plan §6.4 + the `auto_load:{iid}:{aid}` pattern at `context_messages.py:598`):**

* Slot 1: `blueprint:{instance_id}:core`
* Slot 2: `blueprint:{instance_id}:area-1`
* Slot 3: `blueprint:{instance_id}:area-2`
* Slot 4: `blueprint:{instance_id}:area-3`
* Slot 5: `blueprint:{instance_id}:area-4`

Stable IDs mean LangGraph's `add_messages` reducer REPLACES the slot on every rebuild (which only happens on the first turn — turn 2+ reads from checkpoint via the `project_already_injected` short-circuit). This matches the auto-load skills stable-id contract documented at `context_messages.py:564-571`.

**Builder signature:**

```python
def build_blueprint_message(
    matched: list[MatchedBlueprint],   # from Phase 1's BlueprintMatcher
    instance_id: str,
) -> list[HumanMessage]:
    """Render matched blueprints as persistent [SYSTEM CONTEXT: Blueprint] messages.
    ...
    Returns:
        List of 0-5 HumanMessages, one per slot. Empty list when the
        matcher returns nothing and core.md is absent.
    """
```

**Pseudocode body:**

```python
    if not matched:
        return []

    # Sort: core.md first, then area matches by score desc.
    core = next((b for b in matched if b.kind == "core"), None)
    areas = sorted(
        [b for b in matched if b.kind == "area"],
        key=lambda b: getattr(b, "score", 0.0),
        reverse=True,
    )[:4]  # hard cap at 4 area slots

    out: list[HumanMessage] = []
    slot = 0
    if core is not None:
        slot = 1
        out.append(_render_blueprint_slot(core, instance_id, slot, lineage="core"))
    for i, area in enumerate(areas, start=1):
        if slot >= 5:
            break
        slot = (core is not None) + i  # 2..5
        out.append(_render_blueprint_slot(area, instance_id, slot, lineage="matched"))
    return out


def _render_blueprint_slot(
    b: MatchedBlueprint, instance_id: str, slot: int, lineage: str
) -> HumanMessage:
    header = "[BLUEPRINT core]" if lineage == "core" else "[BLUEPRINT matched]"
    body = (
        f"{header} {b.name} v{b.version}\n"
        f"{escape_for_context_block(b.content)}\n"
    )
    if b.file_refs:
        body += "File references:\n"
        for ref in b.file_refs:
            body += f"- {ref}\n"
    body += f"Source: blueprint:{b.name} v{b.version} | lineage:{lineage}\n"

    slot_id_map = {1: "core", 2: "area-1", 3: "area-2", 4: "area-3", 5: "area-4"}
    msg_id = f"blueprint:{instance_id}:{slot_id_map[slot]}"
    return HumanMessage(
        content=f"{CONTEXT_PREFIX}Blueprint{CONTEXT_SUFFIX}{body}",
        id=msg_id,
        additional_kwargs={
            "injected_message": True,
            "context_kind": CONTEXT_KIND_BLUEPRINT,
            "blueprint_name": b.name,
            "blueprint_lineage": lineage,    # "core" | "matched"
            "blueprint_version": b.version,
            "blueprint_slot": slot,
        },
    )
```

**`additional_kwargs` rationale:** `context_kind` enables downstream consumers (compaction re-append, `GET /messages` API display, GET-UI blueprint panel) to identify and selectively re-render the block. `blueprint_name` / `blueprint_lineage` / `blueprint_version` / `blueprint_slot` are the source-lineage tags from plan §3.5 — recorded for analytics, not shown to the agent.

**`__all__` exports:** add `"build_blueprint_message"` and `"CONTEXT_KIND_BLUEPRINT"` to the `__all__` list at `context_messages.py:1379`.

---

### 2.5 Match-once gate — piggyback on `project_injected`

**Decision:** Blueprint matches ONLY when `project_already_injected=False`. The orchestrator's existing short-circuit at `context_messages.py:1188` returns early for every turn after the first; the new block sits inside the `if not project_already_injected:` branch (line 1315) and inherits the gate. No new metadata flag, no new DB write.

**Why piggyback rather than a separate `blueprint_injected` flag:**

1. The blueprint block IS part of the persistent block, which is built once per instance. The semantics are identical to the rest of the persistent block.
2. The existing flag is set at `instance_messaging.py:2337-2341` immediately after successful first-turn injection. Re-using it means blueprint matching has zero new DB writes on the hot path.
3. If blueprint matching ever needs to re-run (e.g. after a `<meta>` REPLACE that swaps a skill), the existing `auto_load_invalidated` rebuild path (lines 1199-1208) is the natural place to add an equivalent `blueprint_invalidated` flag — but that is a deferred enhancement (see §2.11).

**Match-once cost:** one BM25 + vector search per instance lifetime, capped at 5 messages. No re-match, no drift surprise. Plan §5.4 invariant is preserved.

---

### 2.6 Resolve the `build_blueprint_query` gap (option A vs B)

**The gap (per research):** plan §5.3.1 defines `build_blueprint_query(task_message, task_context, skill_content) -> str`, but at the orchestrator insertion point only `user_query` is in scope. `task_context` (the Tier 2A `send_message` context parameter) and `skill_content` (the dispatched skill body) are not threaded into `assemble_context_messages`.

**Two resolution options:**

| Option | Change scope | v1 cost | Recall benefit |
|---|---|---|---|
| **A — Thread new params** | Add `task_context: str \| None = None` and `skill_content: str \| None = None` kwargs to `assemble_context_messages`; update the call site at `instance_messaging.py:2971` to pass them (it already has `message` and can pull `task_context` from the inbound `send_message` body and `skill_content` from any `load_skill` dispatch record on the call). | 2 files, ~30 lines | Highest (plan §5.3.1 full design) |
| **B — user_query only, defer enrichment** | Leave `assemble_context_messages` unchanged. `BlueprintMatcher.match` receives only the user_query. Document the gap; capture the missing signals in `additional_kwargs` of the match log so a future worker can A/B test whether enrichment helps. | 0 changes to orchestrator signature | Lower (user_query is the dominant signal per plan §5.3) |

**Recommendation: Option B for v1.** Cite plan §5.3: "When enrichment signals are present, they are concatenated with the task message for matching purposes. **When absent, only the message text is used.**" The plan already accepts the absent case. `user_query` is by far the dominant matching signal; `task_context` and `skill_content` are enrichment that improves edge-case recall, not core recall. Threading them through the orchestrator signature is a permanent API change for an optimization that has not yet been measured (plan §5.4.1 logging is the prerequisite for the A/B test that would justify the change).

**Logging requirement (plan §5.4.1, must be present from v1):** add a `query_source` field to the `blueprint_match` log line. In Option B, the value is always `"task_only"`. The field exists so the v1 instrumented log can be compared against a future Option-A deployment (`"task+context"` / `"task+context+skill"`) without re-instrumenting.

**Documented enhancement path:** in the orchestrator's docstring, add a `.. note::` block citing plan §5.3.1 and noting that thread-through of `task_context` and `skill_content` is a deferred enhancement gated on Option-A's A/B comparison.

---

### 2.7 Opt-out flow — `blueprint_inactive`

**Read site (inside the new code block in §2.3):** `blueprint_inactive = bool(getattr(agent_meta, "blueprint_inactive", False))`. If True, the block returns no messages and the agent gets the rest of the persistent block (project / shared-context / auto-load / skills) but no blueprint.

**Polarity note:** this is the inverse of `skill_injection` (opt-in). `skill_injection: True` = receive skills; `blueprint_inactive: True` = skip blueprints. The plan §6.2 row for `kb-writer` and `blueprinter` is the only place this flag is set to True in the codebase.

**Default:** `False` (active). All agents get blueprints unless they opt out. Per plan §2.1 invariant 4: "Opt-out, not opt-in."

---

### 2.8 Stable message ID format

See §2.4 slot table. Format: `blueprint:{instance_id}:{slot_id}` where `slot_id ∈ {core, area-1, area-2, area-3, area-4}`. Mirrors the `auto_load:{instance_id}:{agent_id}` pattern at `context_messages.py:598`.

**Why slot-based, not name-based:** if a blueprint is renamed (e.g., `job-queue` → `job-system`), the slot identity keeps the checkpoint message id stable, so LangGraph's `add_messages` replaces the old block instead of orphaning a stale-named message. Plan §10.1 acknowledges renames happen.

**Why the instance-id is in the slot key:** a fresh instance always gets a fresh block (correct). Reusing a worker's old instance id is not a thing (instances are one-shot in the dispatcher pattern per plan §5.5).

---

### 2.9 Async I/O & error handling

* `BlueprintMatcher.match()` is an `async def`. Await it **directly** inside `assemble_context_messages()` (which is already async) — do NOT wrap in `asyncio.to_thread(lambda: asyncio.run(...))`, which creates a **nested event loop** and crashes. Sync DB calls inside `match()` are individually wrapped in `asyncio.to_thread(...)` at their own call sites (same pattern as the shared-context RAG call at `context_messages.py:1271-1276`).
* Catch `Exception` (not `BaseException`) so `CancelledError` propagates to the pause gate. Cite the BUG note from the project: "BUG: `except BaseException: pass` in finally drain blocks swallows CancelledError, breaking pause. Use `except Exception:`."
* On any matcher exception, log a warning, set `matched = []`, and continue. The user message path MUST NOT fail because of a blueprint DB hiccup.
* If `manager._blueprint_matcher` is absent (Phase 1 not yet wired), `matched` falls back to `[]` via the inline `getattr(manager, "_blueprint_matcher", None)` check — same defensive pattern as `_run_skill_search` at line 1028.

---

### 2.10 End-to-end test spec (Phase 2 exit criterion)

**File:** `tests/unit/test_blueprint_injection.py` (new).

**Test 1 — Fresh instance receives blueprint on first message:**

1. Create a project with one `core` blueprint and three `area` blueprints.
2. Seed `core.md` content + three area blueprints with distinct tags/trigger queries.
3. Spawn a fresh `developer` instance.
4. Send a first message whose tokens overlap with the trigger query of the highest-scoring area blueprint.
5. Assert: `assemble_context_messages` returns 4 `HumanMessage`s in the persistent block (1 core + 3 area), each tagged with `context_kind=CONTEXT_KIND_BLUEPRINT` and stable id `blueprint:{iid}:{slot}`.
6. Assert: the `blueprint_match` log line was emitted with `matched_count=4` and the expected `query_source="task_only"`.

**Test 2 — No re-injection on turn 2+:**

1. Continue the instance from Test 1.
2. Send a second message whose tokens DO NOT overlap with any blueprint.
3. Assert: `assemble_context_messages` short-circuits at the `project_already_injected=True` branch and returns no blueprint messages (the turn-2 path emits only skills).
4. Assert: the checkpointed `state['messages']` still carries the 4 blueprint messages from turn 1.

**Test 3 — Opt-out agent gets nothing:**

1. Use a `kb-writer` agent (`blueprint_inactive=True`).
2. Send a first message.
3. Assert: zero `context_kind=CONTEXT_KIND_BLUEPRINT` messages in the persistent block; the rest of the persistent block (project, shared-context, auto-load, skills) is intact.
4. Assert: no `blueprint_match` log line was emitted (we skip before the matcher call).

**Test 4 — Matcher exception degrades gracefully:**

1. Monkey-patch `manager._blueprint_matcher.match` to raise `RuntimeError`.
2. Send a first message.
3. Assert: a `WARNING [ContextMessages] Blueprint matching failed...` line is logged; the rest of the persistent block is built; no `context_kind=CONTEXT_KIND_BLUEPRINT` messages.

**Test 5 — Slot allocation respects the cap:**

1. Seed 6 `area` blueprints that all clear the threshold.
2. Send a first message that matches all 6.
3. Assert: exactly 5 blueprint messages are emitted (1 core + 4 area), not 7. Verify the four emitted area blueprints are the four highest-scoring (sorted desc, top-4).

**Test 6 — Empty project yields zero blueprint messages:**

1. Create a project with no blueprints.
2. Send a first message.
3. Assert: zero `context_kind=CONTEXT_KIND_BLUEPRINT` messages; the rest of the persistent block is intact.

---

### 2.11 Out of scope (this phase) — explicit deferrals

| Item | Why deferred | Where it should land |
|---|---|---|
| Threading `task_context` and `skill_content` into `assemble_context_messages` (Option A) | Need Option-B A/B data first to justify the signature change. | Post-Phase 6 evaluation, behind a metrics-driven trigger. |
| `blueprint_invalidated` flag for `<meta>`-REPLACE rebuilds (parallel to `auto_load_invalidated`) | Re-match on every REPLACE is wasteful; the persistent block is intentionally immutable. | Phase 6 (evaluation) or a future "blueprint refresh" feature. |
| Agent prompt material updates (e.g. `agents/kb-writer/meta.json` setting `blueprint_inactive: true`) | These are content edits, not code. They ride alongside the agent-prompt change for the `blueprinter` agent in Phase 4. | Phase 4 (Blueprinter agent definition). |
| Calibration of the threshold, BM25/vector fusion weights, trigger-query generation | Owned by the Phase 0 contract spike and Phase 6 evaluation. | Phase 0 + Phase 6. |
| LLM rerank fallback (plan §5.6) | Not in v1 scope; deferred to Phase 6 if recall is insufficient. | Phase 6. |
| A `blueprint_rollback` tool / endpoint | Out of plan §10.4 scope; revision history is enough for v1. | A future "blueprint UX" feature. |

---

### 2.12 Phase 2 exit criterion (per plan §12)

> End-to-end test confirms a fresh agent instance receives blueprint injection on first message, and no re-injection on subsequent turns.

Tested by Test 1 + Test 2 above. Additional coverage (Tests 3-6) is the "thorough" part of the exit criterion — opt-out, exception, slot-cap, empty-project paths.

---

## Phase 3 — CRUD API + Tool Registration

**Objective (from plan §12 Phase 3 + §8 + §9):** Backend REST API for blueprint management + agent-callable tool surface. Per-project scoping. Read tools unrestricted; write tools restricted to blueprinter agent and the UI API.

**Spec authority:** plan §8 (CRUD API), §9 (Tool API), §6.2 (opt-out only affects injection, not CRUD/UI).

### 3.1 Touch surface (files to create / edit)

| File | Change | Why |
|---|---|---|
| `daemon/routers/blueprints.py` (new) | FastAPI router: 6 endpoints. | CRUD surface. |
| `daemon/routers/__init__.py` | Add `from .blueprints import router as blueprints_router` + add to `__all__`. | Mount plumbing. |
| `daemon/api.py` | Add `blueprints_router` to the imports block (line 52-72) and to the `include_router` list (line 1482-1501). | Mount at `/api`. |
| `daemon/routers/schemas.py` | Add 5 Pydantic request/response schemas (see §3.3). | Type-safe API contracts. |
| `daemon/tools/blueprint.py` (new) | 5 LangChain tools with closure-injected `manager` + `current_instance_id`. | Agent-callable tool surface. |
| `daemon/tools/_tool_registry.py` | Add `"blueprint": "daemon.tools.blueprint"` to `CATEGORY_MODULES` (line 232-262). | 3-step registration, step 2. |
| `daemon/tools/instance.py` | Add the new tools to the `tools = [...]` list (line 1810+) by extending via `create_blueprint_tools(...)`. | 3-step registration, step 3 — the "or it doesn't exist" trap. |
| `daemon/services/blueprint_matcher.py` (Phase 1) | MATCH-ONLY service; Phase 3 consumes `manager._blueprint_matcher` for `blueprint_search` tool ONLY. No CRUD methods added here. |

**Why CRUD goes through the repository, not the matcher:** Phase 1 owns the `BlueprintRepository` class (the CRUD authority) and the `BlueprintMatcher` class (match-only). The REST API (`blueprints.py`) and agent read/write tools consume `manager._blueprint_repo` for ALL CRUD (get, list, create, update, soft_delete, list_revisions). Only `blueprint_search` consumes `manager._blueprint_matcher`. This keeps the matcher a pure matching concern with no CRUD methods — single responsibility. If Phase 1's repository is missing a CRUD method the API needs, that method is added to `BlueprintRepository`, not `BlueprintMatcher`.

**Repository reference (Phase 1 contract — assumed, NOT re-specced here):**

```python
class BlueprintRepository:                              # CRUD authority — Phase 1
    def get_by_id(self, blueprint_id: str) -> Blueprint | None: ...
    def get_by_name(self, project_id: str, name: str) -> Blueprint | None: ...
    def get_core(self, project_id: str) -> Blueprint | None: ...
    def list_by_project(self, project_id: str, kind: str | None = None, active_only: bool = True, search: str | None = None, limit: int = 100, offset: int = 0) -> list[Blueprint]: ...
    def create(self, project_id: str, name: str, kind: str, content: str, file_refs: list[...], tags: list[str], *, source: str = "manual", changed_by: str | None = None) -> Blueprint: ...
    def update(self, blueprint_id: str, *, name: str | None = None, content: str | None = None, file_refs: list[...] | None = None, tags: list[str] | None = None, source: str = "manual", changed_by: str | None = None, reason: str | None = None) -> Blueprint: ...
    def soft_delete(self, blueprint_id: str) -> None: ...
    def list_revisions(self, blueprint_id: str, limit: int = 50, offset: int = 0) -> list[BlueprintRevision]: ...

class BlueprintMatcher:                                  # MATCH-ONLY — Phase 1
    async def match(self, project_id: str, query: str, max_area: int = 4, threshold: float | None = None) -> list[MatchedBlueprint]: ...
```

**Consumption split:**

| Consumer | CRUD (get/list/create/update/delete/revisions) | Search |
|---|---|---|
| REST API (`blueprints.py`) | `manager._blueprint_repo` | `manager._blueprint_repo.list_by_project(search=...)` |
| `blueprint_get` / `blueprint_list` tools | `manager._blueprint_repo` | — |
| `blueprint_search` tool | — | `manager._blueprint_matcher` |
| `blueprint_create` / `blueprint_update` tools | `manager._blueprint_repo` | — |
| Phase 2 injection (`assemble_context_messages`) | — | `manager._blueprint_matcher.match()` |

Trigger queries + embeddings are generated server-side inside `create()` / `update()` per plan §5.2 (LLM generates 3-10 trigger queries; their embeddings are pre-computed and stored in `blueprint_embeddings`). The router does NOT accept trigger_queries from the client — that is an implementation detail.

---

### 3.2 Router — `daemon/routers/blueprints.py`

**Module-level setup (mirror `daemon/routers/projects.py:1-90`):**

```python
"""Project Blueprint CRUD API endpoints.

Mounted under /api/projects/{project_id}/blueprints. Per-plan §8:
- GET /                   List blueprints (filterable by kind, active_only, search)
- GET /{blueprint_id}     Single blueprint with full content + trigger_queries + file_refs
- POST /                  Create (server generates trigger_queries + embeddings)
- PUT /{blueprint_id}     Update (writes revision row, sets source=manual, bumps version)
- DELETE /{blueprint_id}  Soft delete (is_active=False)
- GET /{blueprint_id}/revisions  Paginated revision history

Read endpoints are unrestricted. Write endpoints (POST, PUT, DELETE) are
restricted to (a) the blueprinter agent and (b) UI-mediated user actions;
the current codebase has no central auth, so write restrictions are
implemented as project-scope checks (the project must exist) plus a
TODO marker for the future auth gate (plan §9.3).
"""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.blueprint import BlueprintRepository  # Phase 1 module
from daemon.services.blueprint_matcher import BlueprintMatcher  # match-only (not used by CRUD endpoints)

from .schemas import (
    BlueprintListItem, BlueprintDetail, BlueprintCreateRequest,
    BlueprintUpdateRequest, BlueprintRevisionResponse, BlueprintListResponse,
    BlueprintRevisionListResponse, BlueprintNotFoundResponse,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/projects/{project_id}/blueprints",
    tags=["blueprints"],
)


# Dependency wiring — matches the pattern at projects.py:25-89
# CRUD goes through BlueprintRepository; BlueprintMatcher is match-only (§3.1).
_blueprint_repo: BlueprintRepository | None = None
_project_repo: SQLModelProjectRepository | None = None


def set_blueprint_repository(repo: BlueprintRepository) -> None:
    """Set the BlueprintRepository instance (called during app startup)."""
    global _blueprint_repo
    _blueprint_repo = repo


def get_blueprint_repository() -> BlueprintRepository:
    if _blueprint_repo is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Blueprint repository not initialized"},
        )
    return _blueprint_repo


def get_project_repository() -> SQLModelProjectRepository:
    if _project_repo is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Project repository not initialized"},
        )
    return _project_repo


def _get_manager(request: Request) -> Any:
    return request.app.state.manager


def _require_project_exists(repo: SQLModelProjectRepository, project_id: str) -> None:
    """404 if the project_id is unknown. Shared by every endpoint."""
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Project '{project_id}' not found"},
        )
```

**Endpoint 1 — `GET /` (list):**

```python
@router.get(
    "",
    response_model=BlueprintListResponse,
    responses={200: {"description": "Blueprints list"}},
)
async def list_blueprints(
    project_id: str,
    kind: str | None = Query(default=None, description="Filter: 'core' | 'area'"),
    active_only: bool = Query(default=True),
    search: str | None = Query(default=None, description="Substring match on name/tags"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> BlueprintListResponse:
    _require_project_exists(project_repo, project_id)
    items = await asyncio.to_thread(
        repo.list_by_project, project_id, kind=kind, active_only=active_only,
        search=search, limit=limit, offset=offset,
    )
    return BlueprintListResponse(
        items=[_to_list_item(b) for b in items],
        total=len(items),  # Phase 1 contract: list returns the page; the
                           # router does a second count call for `total`
                           # in the production version. See §3.4 note.
    )
```

**Endpoint 2 — `GET /{blueprint_id}` (single):**

```python
@router.get(
    "/{blueprint_id}",
    response_model=BlueprintDetail,
    responses={
        200: {"description": "Blueprint detail"},
        404: {"model": BlueprintNotFoundResponse, "description": "Not found"},
    },
)
async def get_blueprint(
    project_id: str,
    blueprint_id: str,
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> BlueprintDetail:
    _require_project_exists(project_repo, project_id)
    bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    if bp is None or bp.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Blueprint '{blueprint_id}' not found in project '{project_id}'"},
        )
    return _to_detail(bp)
```

**Endpoint 3 — `POST /` (create):**

```python
@router.post(
    "",
    response_model=BlueprintDetail,
    status_code=201,
    responses={
        201: {"description": "Blueprint created"},
        400: {"description": "Validation error"},
        409: {"description": "Blueprint name already exists in project"},
    },
)
async def create_blueprint(
    project_id: str,
    body: BlueprintCreateRequest,
    request: Request,
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> BlueprintDetail:
    _require_project_exists(project_repo, project_id)
    # Manager pause check (same pattern as projects.py:226-227)
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        bp = await asyncio.to_thread(
            repo.create,
            project_id=project_id,
            name=body.name,
            kind=body.kind,
            content=body.content,
            file_refs=body.file_refs,
            tags=body.tags,
            source="manual",
            changed_by=body.changed_by,  # optional user identifier from request
        )
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail={"error": str(e)})
        raise HTTPException(status_code=400, detail={"error": str(e)})
    return _to_detail(bp)
```

**Endpoint 4 — `PUT /{blueprint_id}` (update):**

```python
@router.put(
    "/{blueprint_id}",
    response_model=BlueprintDetail,
    responses={
        200: {"description": "Blueprint updated"},
        400: {"description": "Validation error"},
        404: {"model": BlueprintNotFoundResponse, "description": "Not found"},
    },
)
async def update_blueprint(
    project_id: str,
    blueprint_id: str,
    body: BlueprintUpdateRequest,
    request: Request,
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> BlueprintDetail:
    _require_project_exists(project_repo, project_id)
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Project-scope guard: reject cross-project writes
    existing = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    if existing is None or existing.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Blueprint '{blueprint_id}' not found in project '{project_id}'"},
        )

    try:
        bp = await asyncio.to_thread(
            repo.update,
            blueprint_id,
            name=body.name,
            content=body.content,
            file_refs=body.file_refs,
            tags=body.tags,
            source="manual",  # plan §8.2: manual edits set source=manual
            changed_by=body.changed_by,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    return _to_detail(bp)
```

**Endpoint 5 — `DELETE /{blueprint_id}` (soft delete):**

```python
@router.delete(
    "/{blueprint_id}",
    status_code=204,
    responses={
        204: {"description": "Blueprint soft-deleted"},
        404: {"model": BlueprintNotFoundResponse, "description": "Not found"},
    },
)
async def delete_blueprint(
    project_id: str,
    blueprint_id: str,
    request: Request,
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> None:
    _require_project_exists(project_repo, project_id)
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    existing = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    if existing is None or existing.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Blueprint '{blueprint_id}' not found in project '{project_id}'"},
        )

    await asyncio.to_thread(repo.soft_delete, blueprint_id)
    return None
```

**Endpoint 6 — `GET /{blueprint_id}/revisions` (revision history):**

```python
@router.get(
    "/{blueprint_id}/revisions",
    response_model=BlueprintRevisionListResponse,
    responses={
        200: {"description": "Paginated revision list"},
        404: {"model": BlueprintNotFoundResponse, "description": "Not found"},
    },
)
async def list_blueprint_revisions(
    project_id: str,
    blueprint_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repo: BlueprintRepository = Depends(get_blueprint_repository),
    project_repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> BlueprintRevisionListResponse:
    _require_project_exists(project_repo, project_id)
    existing = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    if existing is None or existing.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Blueprint '{blueprint_id}' not found in project '{project_id}'"},
        )
    revs = await asyncio.to_thread(
        repo.list_revisions, blueprint_id, limit=limit, offset=offset
    )
    return BlueprintRevisionListResponse(
        items=[_to_revision(r) for r in revs],
        total=len(revs),  # see §3.4 note about count
    )
```

**Mount wiring:**

* In `daemon/routers/__init__.py` (line 22): add `from .blueprints import router as blueprints_router` and append `"blueprints_router"` to `__all__`.
* In `daemon/api.py` imports block (line 52-72): add `blueprints_router` to the existing tuple-style import (or use the same line-per-router style as the others).
* In `daemon/api.py` mount block (line 1482-1501): add `api_router.include_router(blueprints_router)  # /api/projects/{project_id}/blueprints`. Place it adjacent to `projects_router` (line 1491) so related routers stay grouped.
* In `daemon/api.py` `lifespan` startup (after the existing `set_project_repository(repo)` call): call `set_blueprint_repository(manager._blueprint_repo)`. The repository's lifecycle is owned by the manager (Phase 1 wires that). Pattern matches `set_skill_bank_repo(...)` and `set_job_queue_mgmt_service(...)`.

---

### 3.3 Pydantic schemas (in `daemon/routers/schemas.py`)

**Add at the end of `schemas.py` (after line 847):**

```python
# ==================== Project Blueprint Schemas ====================


class FileRef(BaseModel):
    """Structured file reference — the deepening path from a blueprint to code/docs."""
    path: str = Field(..., description="File path (relative to project root)")
    line: int | None = Field(default=None, ge=1, description="Optional 1-based line number")
    symbol: str | None = Field(default=None, description="Optional function/class/section name")
    note: str | None = Field(default=None, description="Optional human note about why this file matters")

    model_config = {
        "json_schema_extra": {
            "example": {
                "path": "daemon/services/context_messages.py",
                "line": 120,
                "symbol": "assemble_context_messages",
                "note": "The persistent-block orchestrator; lines 1287-1289 are the blueprint insertion point."
            }
        }
    }


class BlueprintListItem(BaseModel):
    """Summary view for the list endpoint — omits content + trigger_queries."""
    id: str = Field(..., description="Blueprint id")
    project_id: str = Field(..., description="Owning project id")
    name: str = Field(..., description="Short slug-style name (e.g., 'core', 'job-queue')")
    kind: str = Field(..., description="'core' or 'area'")
    version: int = Field(..., description="Monotonically increasing version")
    tags: list[str] = Field(default_factory=list, description="LLM-generated + user-editable tags")
    source: str = Field(..., description="'auto' or 'manual' — provenance")
    is_active: bool = Field(..., description="Soft-delete flag")
    created_at: str = Field(..., description="ISO-8601 timestamp")
    updated_at: str = Field(..., description="ISO-8601 timestamp")


class BlueprintDetail(BaseModel):
    """Full blueprint — used by GET /{id} and as POST/PUT response shape."""
    id: str
    project_id: str
    name: str
    kind: str
    version: int
    content: str = Field(..., description="The markdown body, 200-500 words")
    file_refs: list[FileRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    trigger_queries: list[str] = Field(
        default_factory=list,
        description="LLM-generated example queries (3-10). Server-managed — not editable via the API.",
    )
    source: str
    is_active: bool
    created_at: str
    updated_at: str


class BlueprintCreateRequest(BaseModel):
    """Body for POST /."""
    name: str = Field(..., min_length=1, max_length=120, description="Slug-style name")
    kind: str = Field(..., description="'core' or 'area'")
    content: str = Field(..., min_length=1, description="Markdown body, 200-500 words typical")
    file_refs: list[FileRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    changed_by: str | None = Field(default=None, description="Optional user/agent identifier for audit log")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "job-queue",
                "kind": "area",
                "content": "## Job Queue\n\nThe job queue is the single public work primitive...",
                "file_refs": [{"path": "daemon/services/job_queue_service.py", "symbol": "JobQueueService"}],
                "tags": ["scheduler", "concurrency"],
            }
        }
    }


class BlueprintUpdateRequest(BaseModel):
    """Body for PUT /{id} — all fields optional. None = no change. Manual edits."""
    name: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1)
    file_refs: list[FileRef] | None = None
    tags: list[str] | None = None
    changed_by: str | None = None
    reason: str | None = Field(default=None, description="Optional human note for the revision log")

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": "## Job Queue (revised)\n\n...",
                "tags": ["scheduler", "concurrency", "lifecycle"],
                "reason": "Added lifecycle detail after Phase 4 review."
            }
        }
    }


class BlueprintRevisionResponse(BaseModel):
    """One row from the blueprint_revisions history endpoint."""
    id: str
    blueprint_id: str
    version: int
    content_snapshot: str
    change_source: str = Field(..., description="'auto' | 'manual' | 'rollback'")
    changed_by: str | None = None
    reason: str | None = None
    created_at: str


class BlueprintListResponse(BaseModel):
    items: list[BlueprintListItem]
    total: int


class BlueprintRevisionListResponse(BaseModel):
    items: list[BlueprintRevisionResponse]
    total: int


class BlueprintNotFoundResponse(BaseModel):
    error: str
```

**No field for `embedding` on the response:** the embedding vector is an internal storage detail (pgvector type) and is never returned over the wire. The `trigger_queries` list is returned so the UI can show the user what queries the system thinks should match the blueprint (useful for debugging and for the management panel's "why does this match?" UX).

---

### 3.4 Helper conversions

**Add to `daemon/routers/blueprints.py` (near the bottom, before the endpoints or as a `_serializers.py` companion if the file grows):**

```python
def _to_list_item(bp: Any) -> BlueprintListItem:
    """Convert a Blueprint row model to BlueprintListItem (no content)."""
    d = bp.to_dict() if hasattr(bp, "to_dict") else bp
    return BlueprintListItem(**{k: d[k] for k in BlueprintListItem.model_fields if k in d})


def _to_detail(bp: Any) -> BlueprintDetail:
    d = bp.to_dict() if hasattr(bp, "to_dict") else bp
    return BlueprintDetail(**{k: d[k] for k in BlueprintDetail.model_fields if k in d})


def _to_revision(r: Any) -> BlueprintRevisionResponse:
    d = r.to_dict() if hasattr(r, "to_dict") else r
    return BlueprintRevisionResponse(**{k: d[k] for k in BlueprintRevisionResponse.model_fields if k in d})
```

**Note on `total` field:** the `total` in `BlueprintListResponse` reflects the current page length. For a true total (independent of pagination), Phase 1's `BlueprintRepository.list_by_project()` method needs a sibling `count()` that issues a `SELECT COUNT(*)` with the same filters. Flag this in the API docstring as a follow-up: `total` is page-length in v1; replace with a real count when `repository.count()` is added in Phase 1.

---

### 3.5 Tool module — `daemon/tools/blueprint.py`

**Module skeleton (mirror `daemon/tools/critical_notes.py:1-90` for the `create_*_tools(repo, current_instance_id, agent_id)` closure pattern, plus `daemon/tools/skill_evolution_tools.py:131-153` for the `create_*_tools(manager, current_instance_id)` pattern):**

```python
"""Project Blueprint tools for agent-callable read + write access.

Tools surface (plan §9.2):
- blueprint_get(name)                  Read — full content + file_refs
- blueprint_list(project_id?)          Read — name, kind, version, tags
- blueprint_search(query, limit?)      Read — BM25 query over trigger_queries
- blueprint_create(...)                Write — restricted to blueprinter + UI API
- blueprint_update(...)                Write — restricted to blueprinter + UI API

All async, all closure-injected (manager + current_instance_id).
Read tools return markdown. Write tools return dict (per plan §9).

Pattern parallels:
- skill_evolution_tools.py — async tools, closure capture, manager
  attribute lookup with soft-fail (the '⏳ not yet initialized' pattern)
- critical_notes.py — repository closure (SQLModelProjectRepository)
- rag_tools.py — per-tool Pydantic arg schema, StructuredTool return

Read tools (get/list/search) are unrestricted (plan §9.3). Write tools
(create/update) are restricted to the blueprinter agent and the UI API;
the runtime check is implemented as a project-scope check + a TODO
marker for the future auth gate.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Blueprint"
CATEGORY_DOC = """\
Project Blueprint management tools — read access is unrestricted;
write access is restricted to the blueprinter agent and the
UI-mediated user flow.

- blueprint_get — Fetch full content + file refs of one blueprint
- blueprint_list — List available blueprints (filterable)
- blueprint_search — BM25-style search over blueprint trigger queries
- blueprint_create — Create a new blueprint (restricted)
- blueprint_update — Update an existing blueprint (restricted)
"""


def _matcher(manager: "InstanceManager"):
    """Return the BlueprintMatcher instance, or None if not wired.
    Used for SEARCH ONLY (blueprint_search tool). CRUD uses _repo()."""
    return getattr(manager, "_blueprint_matcher", None)


def _repo(manager: "InstanceManager"):
    """Return the BlueprintRepository instance, or None if not wired.
    Used for ALL CRUD (get, list, create, update, delete)."""
    return getattr(manager, "_blueprint_repo", None)


def _is_writer_authorized(agent_id: str) -> bool:
    """Plan §9.3: only blueprinter can write via the tool path.

    The UI/API path is out-of-band (HTTP request, not a tool call) and
    is not gated by this check. A future auth gate will replace this
    function — leave a single seam so the swap is mechanical.
    """
    return agent_id in {"blueprinter"}  # set membership; easy to extend


def create_blueprint_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
    version_tag: str | None = None,
) -> list:
    """Create blueprint tools bound to a BlueprintMatcher."""
```

**Tool 1 — `blueprint_get` (read, unrestricted):**

```python
    @register_tool_category("blueprint")
    @tool
    async def blueprint_get(name: str) -> str:
        """Fetch full content + file refs of one blueprint by name.

        Returns the blueprint body as markdown. If multiple blueprints
        share the same name within the project (rare; should be unique
        per plan §10.1), returns the most-recently-updated one.
        """
        repo = _repo(manager)
        if repo is None:
            return "⏳ blueprint service not yet initialized."
        try:
            bp = await _acall(repo.get_by_name, name, project_id=None)
        except Exception as e:
            return f"ERROR: blueprint_get failed: {e}"
        if bp is None:
            return f"Blueprint '{name}' not found."
        return _render_blueprint_markdown(bp)
```

**Tool 2 — `blueprint_list` (read, unrestricted):**

```python
    @register_tool_category("blueprint")
    @tool
    async def blueprint_list(
        project_id: str | None = None,
        kind: str | None = None,
        active_only: bool = True,
    ) -> str:
        """List available blueprints for a project. Returns a markdown table.

        When ``project_id`` is None, returns blueprints from the
        current instance's project (resolved from instance metadata).
        """
        repo = _repo(manager)
        if repo is None:
            return "⏳ blueprint service not yet initialized."
        effective_project_id = project_id  # or resolve from current_instance_id
        try:
            items = await _acall(
                repo.list_by_project, effective_project_id,
                kind=kind, active_only=active_only, limit=100, offset=0,
            )
        except Exception as e:
            return f"ERROR: blueprint_list failed: {e}"
        if not items:
            return "No blueprints found."
        return _render_blueprint_table(items)
```

**Tool 3 — `blueprint_search` (read, unrestricted):**

```python
    @register_tool_category("blueprint")
    @tool
    async def blueprint_search(query: str, limit: int = 5) -> str:
        """BM25-style search over blueprint trigger queries.

        Returns the top-N matching blueprints as a markdown list with
        scores. The search uses the same multi-algorithm matcher that
        powers persistent injection (plan §5).
        """
        matcher = _matcher(manager)
        if matcher is None:
            return "⏳ blueprint service not yet initialized."
        if limit < 1 or limit > 20:
            return "limit must be between 1 and 20."
        try:
            results = await _acall(
                matcher.match, project_id=None,  # resolved internally
                query=query,
            )
        except Exception as e:
            return f"ERROR: blueprint_search failed: {e}"
        if not results:
            return f"No blueprints matched query: {query!r}"
        return _render_blueprint_search_results(query, results[:limit])
```

**Tool 4 — `blueprint_create` (write, restricted):**

```python
    @register_tool_category("blueprint")
    @tool
    async def blueprint_create(
        project_id: str,
        name: str,
        kind: str,
        content: str,
        file_refs: list[dict] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a new blueprint. Restricted to the blueprinter agent.

        The server generates trigger queries and embeddings — do not
        pass those from the tool caller. Returns a dict with the new
        blueprint's id and version.
        """
        if not _is_writer_authorized(agent_id):
            return {"error": f"Agent '{agent_id}' is not authorized to create blueprints."}
        repo = _repo(manager)
        if repo is None:
            return {"error": "blueprint service not yet initialized."}
        try:
            bp = await _acall(
                repo.create,
                project_id=project_id, name=name, kind=kind, content=content,
                file_refs=file_refs or [], tags=tags or [],
                source="auto", changed_by=agent_id,
            )
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"blueprint_create failed: {e}"}
        return {"id": bp.id, "version": bp.version, "name": bp.name}
```

**Tool 5 — `blueprint_update` (write, restricted):**

```python
    @register_tool_category("blueprint")
    @tool
    async def blueprint_update(
        blueprint_id: str,
        name: str | None = None,
        content: str | None = None,
        file_refs: list[dict] | None = None,
        tags: list[str] | None = None,
        reason: str | None = None,
    ) -> dict:
        """Update an existing blueprint. Restricted to the blueprinter agent.

        Manual edits set ``source=manual`` per plan §8.2 — the
        blueprinter may still update manual blueprints in the future
        but with a higher confidence threshold.
        """
        if not _is_writer_authorized(agent_id):
            return {"error": f"Agent '{agent_id}' is not authorized to update blueprints."}
        repo = _repo(manager)
        if repo is None:
            return {"error": "blueprint service not yet initialized."}
        try:
            bp = await _acall(
                repo.update,
                blueprint_id, name=name, content=content, file_refs=file_refs,
                tags=tags, source="auto", changed_by=agent_id, reason=reason,
            )
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"blueprint_update failed: {e}"}
        return {"id": bp.id, "version": bp.version, "name": bp.name}


    return [blueprint_get, blueprint_list, blueprint_search, blueprint_create, blueprint_update]
```

**Helper `_acall`** (private, mirrors `_invoke_service` from `skill_evolution_tools.py:71-128` but without the soft-fail stub — the blueprint service is owned by the manager, so its absence is a config error not a startup race):

```python
async def _acall(method, *args, **kwargs):
    """Call a possibly-sync repo/matcher method uniformly. Mirrors _invoke_service."""
    result = method(*args, **kwargs)
    if hasattr(result, "__await__"):
        result = await result
    return result
```

**Render helpers** (private, near the top of the module):

```python
def _render_blueprint_markdown(bp) -> str:
    out = f"## {bp.name} (v{bp.version}, kind={bp.kind})\n\n{bp.content}\n"
    if bp.file_refs:
        out += "\n### File references\n"
        for ref in bp.file_refs:
            out += f"- {ref['path']}"
            if ref.get("line"):
                out += f":{ref['line']}"
            if ref.get("symbol"):
                out += f" ({ref['symbol']})"
            if ref.get("note"):
                out += f" — {ref['note']}"
            out += "\n"
    return out


def _render_blueprint_table(items) -> str:
    rows = ["| name | kind | version | tags | updated |", "|------|------|---------|------|---------|"]
    for bp in items:
        rows.append(
            f"| {bp.name} | {bp.kind} | {bp.version} | {','.join(bp.tags)} | {bp.updated_at} |"
        )
    return "\n".join(rows)


def _render_blueprint_search_results(query, results) -> str:
    out = f"## Blueprint search: {query!r}\n\n"
    for r in results:
        score = getattr(r, "score", 0.0)
        out += f"- **{r.name}** (v{r.version}, kind={r.kind}, score={score:.2f})\n"
    return out
```

---

### 3.6 3-step tool registration

**Step 1 — `@register_tool_category("blueprint")` decorator:** already applied to every tool function in the module (§3.5). This stamps `_tool_category = "blueprint"` and the first-party provenance marker (`_tool_category_first_party = True`) on the function — see `_tool_registry.py:43-68`.

**Step 2 — `CATEGORY_MODULES` entry:** add to `daemon/tools/_tool_registry.py` `CATEGORY_MODULES` dict (line 232-262). Place adjacent to the `critical_notes` line for visibility:

```python
    "critical_notes": "daemon.tools.critical_notes",
    "project_history": "daemon.tools.project_history",
    # ... existing entries ...
    "blueprint": "daemon.tools.blueprint",  # ← NEW
```

**Step 3 — `tools = [...]` factory list inclusion in `daemon/tools/instance.py`:**

This is the step most likely to be forgotten — the decorators alone only stamp metadata, omission from the factory list means the tools are dead code (defined but never bound to instances). Mirror the `critical_notes_tools` block at `daemon/tools/instance.py:1847-1851`:

```python
    # Blueprint tools (project-scoped, opt-in by agents.allow "blueprint" category)
    blueprint_tool_list = create_blueprint_tools(
        manager, current_instance_id, agent_id=agent_id, version_tag=version_tag,
    )
    tools.extend(blueprint_tool_list)
```

Insertion point: after the `project_history_tools` block (line 1857) and before the `create_job_tools_if_available` block (line 1862). The position is non-load-bearing (anywhere in the list works), but adjacent to `project_history_tools` keeps project-scoped tools grouped.

**Per-agent opt-in (plan §6.2):** append `"blueprint"` to the `tools.allow` array in the following six agent `meta.json` files so their instances can call blueprint read/write tools:

| Agent file | `tools.allow` addition |
|---|---|
| `agents/developer/meta.json` | `"blueprint"` |
| `agents/tester/meta.json` | `"blueprint"` |
| `agents/explorer/meta.json` | `"blueprint"` |
| `agents/wanderer/meta.json` | `"blueprint"` |
| `agents/planner/meta.json` | `"blueprint"` |
| `agents/reviewer/meta.json` | `"blueprint"` |

Read tools (`blueprint_get`, `blueprint_list`, `blueprint_search`) are unrestricted for all agents. Write tools (`blueprint_create`, `blueprint_update`) are gated at runtime by `_is_writer_authorized(agent_id)` → only `agent_id == "blueprinter"` passes (see §3.5). Including the full `"blueprint"` category in every agent's tool list means the LLM can read blueprints; the two write tools return `{"error": "not authorized"}` for non-blueprinter agents. Utility agents (`kb-writer`, `blueprinter`) do NOT get the category added — `kb-writer` doesn't need it; `blueprinter` gets it via its own `meta.json` (Phase 4).

**Why "include in tool list, gate at runtime" rather than "exclude from tool list, no gate":** if the write tools are simply omitted from `developer`'s tool list, the LLM doesn't even see them in its function-call surface — clean. But the LLM might still want to **read** blueprints (e.g., `developer` calling `blueprint_get("job-queue")` to refresh its understanding mid-task). Excluding the entire category blocks read access too. The right design is: include the category, let the LLM see all 5 tools, gate the 2 write tools at runtime. The LLM's prompt material is then free to mention "blueprint tools are read-only for non-blueprinter agents" as a soft hint.

---

### 3.7 Authorization model summary

| Tool | Authorized to (per plan §9.3) | Enforced where |
|---|---|---|
| `blueprint_get` | All agents | Inclusion in tool list (no extra gate) |
| `blueprint_list` | All agents | Inclusion in tool list |
| `blueprint_search` | All agents | Inclusion in tool list |
| `blueprint_create` | Blueprinter only | Runtime check `_is_writer_authorized(agent_id)` returns `{"error": ...}` for non-blueprinter |
| `blueprint_update` | Blueprinter only | Runtime check `_is_writer_authorized(agent_id)` |
| HTTP `POST /` / `PUT /` / `DELETE /` | UI API (no agent context) | No per-endpoint auth in v1; TODO marker for future central auth (per plan §9.3) |

**TODO for future auth:** the runtime check at the tool level is sufficient for the current threat model (agents are spawned from `agents/<id>/meta.json` and the dispatch path controls who runs what). When the project adds central auth, the HTTP write endpoints will gain a `Depends(auth_dependency)` that checks the caller's session. The tool-level check stays as defense-in-depth.

---

### 3.8 End-to-end test spec (Phase 3 exit criterion)

**File:** `tests/integration/test_blueprint_crud.py` (new — integration, not unit, because it touches Postgres).

**Test 1 — Round-trip create → get → list → update → soft-delete:**

1. Create a project via the projects API.
2. POST `/api/projects/{project_id}/blueprints` with a body containing name=`test-core`, kind=`core`, content=`# Test\n\nBody.`, tags=`["test"]`. Expect 201 + a body with id, version=1, trigger_queries non-empty (server-generated).
3. GET `/api/projects/{project_id}/blueprints` and assert the item is present in the list.
4. GET `/api/projects/{project_id}/blueprints/{id}` and assert content + file_refs + tags + trigger_queries match.
5. PUT `/api/projects/{project_id}/blueprints/{id}` with `{"content": "# Test v2\n\nUpdated body.", "reason": "test"}`. Expect 200 + version=2 + source=`manual`.
6. GET `/api/projects/{project_id}/blueprints/{id}/revisions` and assert the list has 2 entries (versions 1 and 2).
7. DELETE `/api/projects/{project_id}/blueprints/{id}`. Expect 204.
8. GET `/api/projects/{project_id}/blueprints?active_only=true` and assert the item is absent; `?active_only=false` shows it.

**Test 2 — Project-scope guard rejects cross-project writes:**

1. Create projects A and B.
2. Create a blueprint in project A.
3. PUT `/api/projects/B/blueprints/{a_blueprint_id}` (wrong project). Expect 404.
4. DELETE `/api/projects/B/blueprints/{a_blueprint_id}` (wrong project). Expect 404.

**Test 3 — Write tools reject non-blueprinter agents:**

1. Spawn a `developer` instance with the new tool list.
2. Call `blueprint_create(project_id=..., name="x", kind="area", content="...")`.
3. Assert the tool returns `{"error": "Agent 'developer' is not authorized to create blueprints."}`.
4. Repeat for `blueprint_update(blueprint_id=...)` — same error.
5. Spawn a `blueprinter` instance. Repeat the calls. Assert 201/200 from the matcher (or an authorization-pass + the create succeeds).

**Test 4 — Read tools are unrestricted:**

1. Spawn a `developer` instance.
2. Call `blueprint_list(project_id=...)` — assert a markdown table is returned.
3. Call `blueprint_get(name="core")` — assert markdown body returned (or "not found" if no core blueprint).
4. Call `blueprint_search(query="architecture")` — assert a markdown list (or "no matches").

**Test 5 — Soft delete preserves revision history:**

1. Create a blueprint, update it twice, soft-delete it.
2. GET `/api/projects/{project_id}/blueprints/{id}/revisions` — assert 3 entries still present.
3. GET `/api/projects/{project_id}/blueprints/{id}` with `?include_inactive=true` (or equivalent) — assert the blueprint body is still readable (or document the policy: soft-deleted blueprints are not readable through the public API; only revisions are).

**Test 6 — 3-step registration is actually wired (smoke test):**

1. From a Python shell, call `get_tool_categories()` from `_tool_registry` and assert `"blueprint"` is in the returned dict.
2. Spawn a `developer` instance, fetch its tool list, assert the 5 blueprint tool names are present.
3. (Negative) Spawn an agent with `tools.deny = ["blueprint"]` in meta.json, assert the 5 tool names are absent.

---

### 3.9 Out of scope (this phase) — explicit deferrals

| Item | Why deferred | Where it should land |
|---|---|---|
| Central auth on HTTP write endpoints | No central auth exists in the codebase; the tool-level check is sufficient for v1. Plan §9.3 acknowledges this gap. | Future central auth feature. |
| `blueprint_delete` hard-delete / `blueprint_rollback` | Plan §10.4: rollback is via revision history; soft-delete is the only delete semantic in v1. | A future "blueprint UX" feature. |
| Real `total` count in `BlueprintListResponse` | Phase 1 doesn't ship a `count()` method; the v1 `total` is page-length. | Phase 1 (add `count()`) or a follow-up. |
| `blueprint_create` accepting `trigger_queries` from the client | Plan §5.2: trigger queries are server-generated from the content. Client-supplied would defeat the design. | Not deferred — explicitly forbidden in the schema. |
| Frontend UI (panel) | Plan §12 Phase 5 owns the UI. Phase 3 only ships the HTTP surface. | Phase 5. |
| The `_blueprint_matcher` manager-attribute wiring | Owned by Phase 1 (manager init). Phase 3 just consumes the attribute via `_matcher()` / `_repo()`. | Phase 1. |
| `blueprint_soft_delete` exposing the underlying `is_active=False` row to the agent | Soft-deleted blueprints are not visible via the public read path. Restoring = manual DB edit. | A future "blueprint restore" feature. |

---

### 3.10 Phase 3 exit criterion (per plan §12)

> All endpoints pass API tests; revision history is queryable.

Tested by Tests 1-6 above. The list/get/create/update/delete happy paths plus project-scope, auth, and registration-smoke tests cover the full surface.

---

## Cross-phase invariants

These MUST hold across both phases; a worker implementing either phase should verify both.

1. **Stable message IDs** (`blueprint:{iid}:{slot}`) match the auto-load skills pattern. A Phase-1 matcher returning blueprints in a different order must not change the message-id mapping — the slot index is fixed (1=core, 2-5=area by score).
2. **`context_kind=CONTEXT_KIND_BLUEPRINT`** is the single source of truth for downstream consumers (compaction re-append, `GET /messages` display, UI panel). Phase 2 sets it on the `HumanMessage.additional_kwargs`; Phase 3 UI consumes it.
3. **Pydantic schema field names** (Phase 3) match the Pydantic model field names returned by Phase 1's `Blueprint.to_dict()`. Coordinate via the `BlueprintDetail` schema; if Phase 1 names a field differently, the `_to_detail` helper is the seam to bridge.
4. **Opt-out is injection-only.** `blueprint_inactive=True` (Phase 2) skips the persistent block. The HTTP API and tool surface are unaffected — utility agents (`kb-writer`) can still read blueprints via the API/UI if a future feature needs that. Plan §6.2 is consistent with this: opt-out is about the persistent block, not the corpus.
5. **PostgreSQL only.** Both phases assume the manager's `_blueprint_matcher` is wired to a PostgreSQL backend. No SQLite fallback. Cite the project-wide pattern: "PostgreSQL is the PRIMARY dev/test DB."

---

## Implementation order recommendation

1. **Phase 1 ships first** (owned by another worker). Without `BlueprintMatcher` + `Blueprint` + `BlueprintRevision` models, Phase 2 has nothing to call and Phase 3 has nothing to expose. Do not start Phase 2 or Phase 3 in parallel with Phase 1.
2. **Phase 2 (injection) before Phase 3 (CRUD).** Reason: injection is the primary user-facing feature. Manual seeding via the CRUD API is needed before the blueprinter can take over (Phase 4), so Phase 3 should be available by the time blueprinter maintenance lands. The dependency is loose — Phase 2 doesn't need the CRUD API; Phase 3 doesn't need the injection path.
3. **The 3-step tool registration is the highest-risk step in Phase 3.** A worker implementing Phase 3 should test the tool-list inclusion (`daemon/tools/instance.py:1862+` insertion) BEFORE building the full tool module body — confirms the wiring works, then fill in the bodies.

---

## Appendix A — Annotated file change list

| File | LOC estimate | Risk |
|---|---|---|
| `daemon/registry.py` | +15 lines (1 field + 2 loader additions + optional example) | Low — pure additive, parallels `skill_injection` |
| `daemon/services/context_messages.py` | +120 lines (1 constant + 2 builders + 1 helper + 1 orchestrator block + `__all__` updates) | Medium — touches the hot path; structured logging must not regress the early-return short-circuit |
| `daemon/services/instance_messaging.py` | 0 lines | None — no change |
| `daemon/routers/blueprints.py` (new) | ~250 lines | Medium — 6 endpoints, all touch the DB |
| `daemon/routers/__init__.py` | +2 lines | Low — import + `__all__` |
| `daemon/api.py` | +3 lines (1 import + 1 include_router + 1 set_blueprint_repository in lifespan) | Low — pure additive |
| `daemon/routers/schemas.py` | +130 lines (5 schemas + 1 file_ref schema + 1 not_found + 2 list responses) | Low — pure schema, no logic |
| `daemon/tools/blueprint.py` (new) | ~250 lines | Medium — 5 tools + helpers, runtime auth check |
| `daemon/tools/_tool_registry.py` | +1 line (`CATEGORY_MODULES` entry) | Low — pure data |
| `daemon/tools/instance.py` | +5 lines (1 tools.extend block) | Low — pure additive, mirrors `critical_notes_tools` |
| `tests/unit/test_blueprint_injection.py` (new) | ~250 lines | — |
| `tests/integration/test_blueprint_crud.py` (new) | ~250 lines | — |
| `agents/developer/meta.json` | +1 token in `tools.allow` | Low — append `"blueprint"` to the array |
| `agents/tester/meta.json` | +1 token in `tools.allow` | Low |
| `agents/explorer/meta.json` | +1 token in `tools.allow` | Low |
| `agents/wanderer/meta.json` | +1 token in `tools.allow` | Low |
| `agents/planner/meta.json` | +1 token in `tools.allow` | Low |
| `agents/reviewer/meta.json` | +1 token in `tools.allow` | Low |

**Total additive LOC:** ~1300 (excluding tests). No deletions, no schema migrations on existing tables.

---

## Appendix B — Spec-gap resolutions (summary table)

| Gap | Resolution | Section |
|---|---|---|
| `task_context` and `skill_content` not in `assemble_context_messages` scope | Option B for v1: omit enrichment, log `query_source=task_only`, document enhancement path. | §2.6 |
| Match-once flag | Piggyback on existing `project_injected` flag. No new metadata key. | §2.5 |
| Slot identity vs blueprint name | Slot-based IDs (`blueprint:{iid}:core` / `area-N`) so renames don't orphan messages. | §2.4, §2.8 |
| Opt-out default | `blueprint_inactive: bool = False` — opt-out, not opt-in. | §2.2, §2.7 |
| Write-tool authorization (no central auth) | Runtime `_is_writer_authorized(agent_id) ∈ {"blueprinter"}` + TODO marker for future auth. | §3.7 |
| `total` field accuracy | Page-length in v1; replace with real `count()` when Phase 1 ships it. | §3.4 |
| `blueprint_rollback` | Out of scope per plan §10.4. | §2.11, §3.9 |
| `<meta>` REPLACE rebuild of blueprint block | Deferred; not in v1. | §2.11 |
| `repository.count()` for `total` | Deferred to Phase 1. | §3.9 |
| `_blueprint_matcher` manager-attribute wiring | Owned by Phase 1. | §3.9 |

---

*End of phase23-implementation.md*
