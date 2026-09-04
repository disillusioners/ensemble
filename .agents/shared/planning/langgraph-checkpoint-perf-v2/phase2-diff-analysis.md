# Phase 2 — Diff Analysis (T2.1 + T2.13)

> Date: 2026-09-04 (UTC)
> v1 source: `fa31a520` + fold `3c9478ba` (PR2 of feature/langgraph-checkpoint-perf)
> v2 source: branch `feature/langgraph-checkpoint-perf-v2 @ 87ad1018` (current HEAD; PR1 already landed at `901d96e5`); docs at `87ad1018` carry the merge of `2f80d45b -> 901d96e5 -> 87ad1018`
> Method: monolithic `git cherry-pick -x fa31a520` then `git cherry-pick -x 3c9478ba` per W6 provenance AC; `-x` auto-appends `(cherry picked from commit fa31a520...)` to each commit. Conflicts resolved per Files-Touched resolution rules + architect §1.2 corrected anchors.

## §astream-check (T2.0 gate — prep task)

```text
$ grep -rn "graph.astream\|graph.ainvoke" daemon/services/instance_messaging.py daemon/graph.py
daemon/services/instance_messaging.py:96:# -> ``graph.ainvoke`` bypass. These are correctness fixes that ship
daemon/services/instance_messaging.py:328:# ``_build_graph_input`` sites converge, before ``graph.astream``),
daemon/services/instance_messaging.py:369:            ``graph.astream``. **MUTATED IN PLACE** — placeholders
daemon/services/instance_messaging.py:481:    # structurally valid before ``graph.astream``. The D1 seam
daemon/services/instance_messaging.py:586:        ``graph.astream(graph_input, ...)``. With a non-empty
daemon/services/instance_messaging.py:1498:    # ``graph.ainvoke`` bypass — was DELETED (it never shipped in
daemon/services/instance_messaging.py:1777:            #    graph.astream, so reviving a terminated instance is the
daemon/services/instance_messaging.py:3685:    # between the clear and ``graph.astream`` loses the leftovers
daemon/services/instance_messaging.py:3929:                async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
```

| Metric | v1 baseline (fc908945) | v2 HEAD (87ad1018) | Drift |
|---|---|---|---|
| `astream`/`ainvoke` CALL SITES in instance_messaging.py (excludes comments/docstrings) | 2 (ainvoke :1087 + astream :3564) | **1** (astream :3929; the inline `ainvoke` at v1 :1087 was deleted by the v2 consolidation) | v2 LOST the inline `ainvoke` — ZERO risk to the tap-site 1:1 contract because (a) v1 itself classified that path as OOS / accepted-degradation per `decisions.md D19` + B1 (inline `{"messages": [message]}`; id-less; zero production callers; `state.ts` fallback applies), and (b) v1's message_tap.py docstring already enumerates it explicitly in the OOS list. |
| `astream`/`ainvoke` CALL SITES in daemon/graph.py | 0 | 0 | No drift. |

**T2.0 STOP gate: PASS.** v2 has exactly 1 astream call site at `instance_messaging.py:3929` (matches the load-bearing assumption: SINGLE astream site). The 4-tap-site contract mapping holds.

## Files in v1 PR2 (`fa31a520` + `3c9478ba`)

19 files changed (+3135/-7 in v1). The actual PR2 surface splits as follows:

### CLEAN ADDS — 5 paths (T2.2 — `git checkout`)

| v1 source | Path |
|---|---|
| `daemon/services/message_tap.py` | `daemon/services/message_tap.py` (MessageTapSlot + 4 SOURCE_* constants; sole-containment docstring truth + over-record note folded from `3c9478ba`) |
| `daemon/repositories/message_metadata/__init__.py` | package init |
| `daemon/repositories/message_metadata/models.py` | MessageMetadata SQLModel |
| `daemon/repositories/message_metadata/repository.py` | SYNC MessageMetadataRepository |
| `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` | SQLite migration |

### HOT FILE HUNKS (T2.4–T2.6 — `cherry-pick -x` + manual fix-up)

| v1 hunk | v2 anchor | Insertion site |
|---|---|---|
| `daemon/graph.py` — 2 kwargs on `create_agent_node` | `create_agent_node(..., context_slot: ...)` end | Append `message_tap_slot: "MessageTapSlot \| None"` + `compaction_tap_slot: "MessageTapSlot \| None"` after `context_slot`. |
| `daemon/graph.py` — `compaction_aupdate_reactive` tap | AFTER `await graph.aupdate_state(thread_config, {'compacted_at': result.compacted_at}, as_node='agent')` at `daemon/graph.py:3585` | Insert tap call BEFORE the `logger.info(f'[LLM] Reactive compaction complete:')` line. Architect §1.2 confirms v2 already has v2's `4db97e3c` streaming + `84fd8018` placeholder synthesis + `dd95caef` provenance stamping + `a80767b9` single-document sentinel recipe applied BEFORE this line — tap reads `result.replacement_messages` AFTER them. |
| `daemon/graph.py` — F2 single-return hoist | v2 dual-return `:3731-3732` (architect §1.2 — was v1 `:3386-3397`; v2's WC-wake + compaction rewrites pushed the block ~345 lines down) | Replace v2's `persisted: list[BaseMessage] = []` construction + `return {**watchover_state_reset, 'messages': persisted}` / `return {**watchover_state_reset, 'messages': [response]}` with v1's `outgoing: list[BaseMessage] = [response]` + the conditional extend + `tap_node_return` call + single `return {**watchover_state_reset, 'messages': outgoing}`. v2's preceding `pairing_synthesized_msgs.extend(_ensure_tool_result_pairing(...))` (`84fd8018`) sits ~50 lines above — preserved unchanged. |
| `daemon/graph.py` — `tap_node_return` at agent_node_return | The single `return {**watchover_state_reset, 'messages': outgoing}` after F2 hoist | Insert `if message_tap_slot is not None: await message_tap_slot.tap_node_return(outgoing, instance_id)` immediately before the return. |
| `daemon/graph.py` — 2 kwargs on `build_instance_graph` | `build_instance_graph(..., context_slot: ...)` end | Same append pattern. |
| `daemon/graph.py` — kwargs forwarded into `create_agent_node(...)` call | `graph.add_node("agent", create_agent_node(...))` block at v2 `:5871` | Append `message_tap_slot=message_tap_slot, compaction_tap_slot=compaction_tap_slot` after `context_slot=context_slot`. |
| `daemon/services/instance_messaging.py` — `from .message_tap import (...)` | After `from .messaging_types import AsyncMessageResult, LinkageContractError` at `daemon/services/instance_messaging.py:30` | Append 5-line import block (v1 import shape). |
| `daemon/services/instance_messaging.py` — `compaction_aupdate_messaging` tap | AFTER the second `await graph.aupdate_state(...)` (the `compacted_at` write) inside `_maybe_compact_context` at v2 `:1303`–`:1306` (architect §1.2 — was v1 `:821`; v2 shift ~485 lines) | Insert tap call BEFORE the `[Compaction]` log_parts assembly. v2's `dd95caef` provenance stamping + `a80767b9` single-document sentinel recipe (`build_sentinel_replacement` at `:1285`) precede this — tap reads `result.replacement_messages` AFTER them. |
| `daemon/services/instance_messaging.py` — `user_message_entry` tap | AFTER the D2 seam-drain block (cleanup_injection block + `# ── end D2 seam drain ──`) at v2 `:3808` (architect §1.2 — was v1 `:3425`; v2 shift ~335 lines); BEFORE the `context_messages_to_emit = list(persistent_context_msgs)` block at `:3810` | Insert tap call. This is strictly AFTER the FIFO drain (so the tap captures exactly what's about to flow into the graph START) and BEFORE the persistent-context pre-emit. v2's `dbf9ef44` `message_metadata` kwarg (the task-context propagation in `send_message`) is a DIFFERENT surface — it lives in `send_message`'s signature, NOT in this entry path, so the naming overlap is benign and per plan §"Risk 5" is documented in `message_tap.py` docstring. |
| `daemon/services/instance_lifecycle.py` — 2 import lines per `build_instance_graph` call site (4 imports total across 2 paths) | After the `from ..graph import InjectionSlot, ReportInjectionSlot, ToolThrottleSlot, LoopBreakerSlot, LoopRepairer, ContextSlot` line at `daemon/services/instance_lifecycle.py:1273` and `:3243` | Insert v1's 5-line `from ..services.message_tap import (...)` block. |
| `daemon/services/instance_lifecycle.py` — 2 MessageTapSlot kwargs per call (4 constructions total across 2 paths) | After the last slot kwarg before the closing `)` of `build_instance_graph(...)` at v2 `:1304`–`:1325` (spawn path) and `:3289`–`:3306` (restore path); architect §1.2 confirms HIGH (~515-line real churn since `58260f35`); 4 slot constructions need manual fix-up. | Append `message_tap_slot=MessageTapSlot(...)` + `compaction_tap_slot=MessageTapSlot(...)` after the LAST existing slot kwarg. |
| `daemon/manager.py` — import block additions (`create_message_metadata_repository`, `MessageMetadataRepository`) | After `InstanceUiPrefsRepository,` at `daemon/manager.py:53` | Append v1's 2-line addition. |
| `daemon/manager.py` — `self._message_metadata_repo = ...` constructor placement | After `self._report_injection_repo = ReportInjectionRepository(engine=self._engine)` at `daemon/manager.py:578` | Append v1's 13-line block. |
| `daemon/manager.py` — `message_metadata_repo` property | After `return self._db_connection_repository` (the END of the `_db_connection_repository` property block) at `daemon/manager.py:2022` (NOT near `:6642`, which is v2's UNRELATED `dbf9ef44` `message_metadata` kwarg — manager.py anchor collision architect §1.2 + Risk #2a) | Append v1's 15-line property block before `@property\n    def db_pool_manager`. |
| `daemon/manager.py` — PG `CREATE TABLE` + `CREATE INDEX` block in `_ensure_postgres_columns` | END of `statements = [...]` list in `_ensure_postgres_columns()` at `daemon/manager.py:5511` (just before the `]` closing the list, then `with self._engine.begin() as conn:` at `:5515`) | Append v1's 25-line block (2 statements + comments). |
| `daemon/repositories/__init__.py` — imports + `__all__` appends | After `from .dependency_bus.repository import DependencyWatcherRepository` at the model import block + `from .factory import (...)` factory import block + the `__all__` lists | Append v1's `MessageMetadata` + `MessageMetadataRepository` model + factory imports + `__all__` entries. **CRITICAL order: message_metadata model imported BEFORE `daemon.manager.py` calls `create_all`** (T2.3 acceptance + Risk #3). |
| `daemon/repositories/factory.py` — `create_message_metadata_repository` factory | After `def create_skill_bank_repository(...)` (LAST factory function in the file) | Append v1's 56-line factory function + `from .message_metadata.repository import MessageMetadataRepository` import at the import block. |

### ZERO-DELTA FILES (T2.1 — verify)

| File | Status | Rationale |
|---|---|---|
| `daemon/checkpoint_perf.py` | **ZERO DIFF** | Phase 1 already ported this file at the `fc908945` byte target (209 lines, includes `log_message_tap` at :120 + `log_blob_prune` at :168). v1's PR2 added `log_message_tap` (lines 117-152 of v1's diff), but fc908945's already-included `log_message_tap` is byte-equivalent to v1's PR2 addition (verified by content). |

## Conflict resolution matrix (per Files-Touched + plan text)

| File | Conflict type | Resolution rule |
|---|---|---|
| `daemon/services/message_tap.py` (clean add) | none | `git checkout fa31a520 -- daemon/services/message_tap.py` |
| `daemon/repositories/message_metadata/*.py` (3 clean adds) | none | `git checkout fa31a520 -- <each>` |
| `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` | none (verify id non-collision; latest v2 max = `20260819_000001`) | `git checkout fa31a520 -- <sql>` |
| `daemon/repositories/__init__.py` | LOW | `git cherry-pick -x fa31a520 -- daemon/repositories/__init__.py daemon/repositories/factory.py` (low-conflict — 4 lines + `__all__` appends; v2's existing imports preserved; manual fix-up if 3-way merge fails on imports) |
| `daemon/repositories/factory.py` | LOW | same commit, same `-x` provenance |
| `daemon/manager.py` | HIGH (architect §1.2) | 3-way merge expected to resolve; if not, manual fix-up per file: (a) import tail at `:53`; (b) `_message_metadata_repo` ctor at `:578`; (c) `message_metadata_repo` property at `:2022` (NOT `:6642` — that's v2's unrelated `dbf9ef44` kwarg per Risk #2a); (d) PG DDL block at end of `_ensure_postgres_columns` statements list (`:5511`). PG DDL block byte-identical to v1. |
| `daemon/graph.py` | HIGH (architect §1.2) | 3-way merge expected to resolve; if not, manual fix-up per file: (a) 2 kwargs on `create_agent_node` after `context_slot` (~`:2731`); (b) F2 single-return hoist replacing dual-return at `:3731-3732`; (c) `tap_node_return` call at agent_node_return site; (d) `compaction_aupdate_reactive` tap AFTER `:3585` (BEFORE the next `logger.info(f'[LLM] Reactive compaction complete:')`); (e) 2 kwargs on `build_instance_graph` after `context_slot` (~`:5757`); (f) `message_tap_slot=`, `compaction_tap_slot=` kwargs forwarded into `create_agent_node(...)` after `context_slot=context_slot` (~`:5887`). v2's `4db97e3c` + `84fd8018` + `dd95caef` + `a80767b9` effects MUST be preserved (read `result.replacement_messages` AFTER them; do NOT regress the dual-return to be silent about the `pairing_synthesized_msgs` assembly). |
| `daemon/services/instance_messaging.py` | HIGH (architect §1.2) | 3-way merge expected to resolve; if not, manual fix-up per file: (a) `from .message_tap import (...)` import block after `:30`; (b) `compaction_aupdate_messaging` tap after `:1306` (the second `aupdate_state` write) and before the `[Compaction]` log_parts assembly at `:1310`; (c) `user_message_entry` tap after `:3808` (`# ── end D2 seam drain ──`) and before `:3810` (`context_messages_to_emit = list(persistent_context_msgs)`). v2's `dbf9ef44` `message_metadata` kwarg is a DIFFERENT surface — DO NOT modify (Risk #5). |
| `daemon/services/instance_lifecycle.py` | HIGH (architect §1.2; ~515-line real churn) | 3-way merge may FAIL on each `build_instance_graph` call site (the slot-kwarg block sits inside the call which has churned). Manual fix-up: (a) import block after `from ..graph import InjectionSlot, ..., ContextSlot` at `:1273` and `:3243`; (b) `message_tap_slot=` + `compaction_tap_slot=` kwargs after the LAST existing slot kwarg in each `build_instance_graph(...)` call (both spawn and restore paths). 4 MessageTapSlot constructions total (2 per path). |

## Landings + provenance (W6 AC)

Each cherry-picked commit carries `(cherry picked from commit fa31a52089d87dd79959f2642ba65044ac9f3153)` (or `3c9478ba79b3f0c615f1ee6585fc0ebb5afc96b5`) appended by `git cherry-pick -x`.

| Commit | Subject | Provenance | Files | Staged-set verification (before commit) |
|---|---|---|---|---|
| C1 | feat(perf): PR2 — 5 clean adds (message_tap + message_metadata repo + migration) | `(cherry picked from commit fa31a520...)` | daemon/services/message_tap.py; daemon/repositories/message_metadata/__init__.py; daemon/repositories/message_metadata/models.py; daemon/repositories/message_metadata/repository.py; daemon/migrations/versions/20260825_000001_create_message_metadata.sql | `git status --short` MUST show ONLY the 5 paths under `??` → `A`. No `.agents/approver/active.md`, no `.agents/shared/planning/job-task-retrospective/decisions.md`, no `.agents/shared/planning/defer-gate-fix/`, no `.agents/tester/QUARANTINE.md`. |
| C2 | feat(perf): PR2 — repositories wiring (factory + `__init__.py` + `__all__`) | `(cherry picked from commit fa31a520...)` | daemon/repositories/__init__.py; daemon/repositories/factory.py | same |
| C3 | feat(perf): PR2 — manager wiring (repo + property + PG DDL) | `(cherry picked from commit fa31a520...)` | daemon/manager.py | same |
| C4 | feat(perf): PR2 — graph.py (2 kwargs + F2 hoist + 2 tap calls) | `(cherry picked from commit fa31a520...)` | daemon/graph.py | same |
| C5 | feat(perf): PR2 — instance_messaging (imports + 2 tap calls) | `(cherry picked from commit fa31a520...)` | daemon/services/instance_messaging.py | same |
| C6 | feat(perf): PR2 — instance_lifecycle (4 MessageTapSlot constructions + 4 imports) | `(cherry picked from commit fa31a520...)` | daemon/services/instance_lifecycle.py | same |
| C7 | test(perf): PR2 — ported tests (7 files) | `(cherry picked from commit fa31a520...)` | tests/integration/test_message_metadata_hook_placement.py; tests/integration/test_message_metadata_liveness.py; tests/unit/repositories/test_message_metadata_repository.py; tests/unit/repositories/test_message_metadata_paused_question_flow.py; tests/unit/repositories/test_message_metadata_revive_stability.py; tests/unit/repositories/test_message_tap_to_repo_liveness.py; tests/unit/services/test_message_tap_slot.py | same |
| C8 | docs(tap): PR2 — message_tap docstring fold (sole-containment truth + over-record note) | `(cherry picked from commit 3c9478ba...)` | daemon/services/message_tap.py | same |
| C9 | chore(gate): regen manifest at <v2-sha> | (gate regen; carries the 4 phase2 corpus docs + GATE_SUITES.txt regen) | tests/integration/gate_suites/GATE_SUITES.txt + .agents/shared/planning/langgraph-checkpoint-perf-v2/{phase2-diff-analysis.md,phase2-migration-verify.md,phase2-astream-check.md,phase2-results.md} | per `git status --short` final |

Explicit-path staging ONLY (`git add <each path>`); NEVER `git add -A` / `git add .` / `commit -a`.

## Drift-vs-baseline guards (post-port)

| Guard | Phase 0/1 baseline | Post-port expected | Mechanism |
|---|---|---|---|
| G1 `grep -rn "settled" docs/job-task-system.md` | 17 (per phase1-results.md erratum) | 17 (no edit to doc) | doc not touched |
| G2 `grep -n tap_node_return daemon/graph.py daemon/services/instance_messaging.py` | 0 (pre-Phase-2) | **EXACTLY 4** | T2.5 + T2.6 wiring; verified by AST gate `test_message_metadata_hook_placement.py` |
| G3 migration tail `20260*` | `20260819_000001_report_injections_deferred_marker.sql` | `20260825_000001_create_message_metadata.sql` | ONLY expected delta (Phase 2 introduces the new migration) |
| G4 `grep -rn atomic daemon/services/checkpoint_prune.py daemon/checkpoint_adapter.py` | exit 2 / exit 1 | unchanged | PR2 does not touch these paths |

## Native re-runs (the two Phase-0 SKIP-LOUDLY items)

- **T0.3 dialect-parity** = `test_message_metadata_repository.py` (16 tests). Must now COLLECT AND PASS.
- **T0.6 isolation** = `test_message_tap_slot.py` (20 tests) + `test_message_tap_to_repo_liveness.py`. Must now COLLECT AND PASS.

## RemoveMessage 6 sub-cases (architect §5 guardrail row + §8 N10 — must PASS by name)

The `tests/unit/services/test_message_tap_slot.py` suite MUST cover (Phase 2 verification MUST verify by name):
- (a) RemoveMessage marker on a HumanMessage → filtered BEFORE INSERT (no row written)
- (b) RemoveMessage marker on an AIMessage → filtered BEFORE INSERT (no row written)
- (c) RemoveMessage marker on a ToolMessage → filtered BEFORE INSERT (no row written)
- (d) RemoveMessage marker interleaved with normal messages → only normal messages inserted
- (e) RemoveMessage marker on the LAST message of a turn → no orphan row
- (f) bare `RemoveMessage(id=...)` without a sibling payload → filtered

## Carry-over 🟡 (v1 docstring fix verification)

- `message_tap.py:54-80` (post-fold) MUST no longer claim call sites wrap in try/except. The fold `3c9478ba` rewrites the "Failure mode" paragraph to declare the slot's internal `try / except Exception` is the SOLE containment layer. Verify by `grep -n "SOLE containment"` in the post-port `daemon/services/message_tap.py` and confirm the docstring matches the v1-folded shape.
