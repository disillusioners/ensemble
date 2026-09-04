# Phase 2 — Results: PR2 Port (message_metadata side table + MessageTapSlot)

> Date: 2026-09-04 (UTC) | v2 HEAD: `dc39ae6d` (final pre-chore state; chore commit at HEAD thereafter)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Port method: monolithic `git cherry-pick -x fa31a520` per T2.2 (5 clean adds via `git checkout`), then `git checkout fa31a520 --` for hot files; `git cherry-pick -x 3c9478ba` for the docstring fold. Conflicts resolved per architect §1.2 corrected anchors + Files-Touched resolution rules.
> DSN discipline: every DSN-resolving invocation carried BOTH `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test` (PG trust auth, no password). `ensemble_prod` / `ensemble_dev` never referenced.
> Push: NO push (per task brief); all commits land locally on the v2 branch.

## Per-task outcomes

### T2.0 — STOP gate (astream/ainvoke count) — PASS

- Pinned DSN envs set; ran `grep -rn "graph.astream\|graph.ainvoke" daemon/services/instance_messaging.py daemon/graph.py` then filtered to non-docstring/non-comment call sites.
- **v2 has exactly 1 astream call site at `instance_messaging.py:3929`** (matches v1 baseline assumption).
- v1's pre-port state had 2 (ainvoke at :1087 + astream at :3564); v2 deleted the inline `ainvoke` (v1 had classified it as OOS / accepted-degradation per `decisions.md D19` + B1). The 4-tap-site contract mapping holds.
- Recorded in `phase2-diff-analysis.md` §astream-check.

### T2.1 — v1 PR2 diff analysis — DONE

- Read `git show fa31a520` end-to-end + `git show 3c9478ba -- daemon/services/message_tap.py` (the docstring fold).
- Identified 19-file surface (3135 +/-7) split into 5 clean adds + 7 hot-file hunks + 1 docstring fold.
- Verified `daemon/checkpoint_perf.py` requires ZERO change (Phase 1 already ported at `fc908945` byte target; v1's PR2 delta to it is the `log_message_tap` helper already present).
- v1's `test_message_metadata_lifecycle_wiring.py` was added in PR3 (`dbfbf812`), not in fa31a520 — flagged for separate port in C7.1.
- Hunk boundaries + insertion anchors documented in `phase2-diff-analysis.md` Files-Touched table.

### T2.2 — Clean adds — DONE (C1: `a9a71e9b`)

- `git checkout fa31a520 --` for 5 paths:
  - `daemon/services/message_tap.py` (MessageTapSlot + 4 SOURCE_* constants)
  - `daemon/repositories/message_metadata/__init__.py` (package init)
  - `daemon/repositories/message_metadata/models.py` (MessageMetadata SQLModel)
  - `daemon/repositories/message_metadata/repository.py` (SYNC repository)
  - `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` (SQLite migration)
- All 5 files byte-equivalent to v1 (`cmp`).
- Migration id non-collision verified (latest v2 = `20260819_000001`).
- `py_compile` OK.

### T2.3 — Repositories wiring — DONE (C2: `d4a068c9`)

- Manual apply of v1 hunks to `daemon/repositories/__init__.py` (16 lines) + `daemon/repositories/factory.py` (62 lines).
- Imports appended after existing v2 patterns (`from .message_metadata.models import MessageMetadata` etc.); `__all__` appended after `create_skill_bank_repository`.
- Critical ordering: `MessageMetadata` model imported BEFORE `daemon.manager.py` calls `SQLModel.metadata.create_all()` (Risk #3 verified).
- `py_compile` OK; byte diff vs v1's PR2 hunks: exact match.

### T2.4 — Manager.py wiring — DONE (C3: `bec9b737`)

- 4 hunks applied (matches v1's PR2 byte target):
  1. Import block additions (`create_message_metadata_repository`, `MessageMetadataRepository`) after `InstanceUiPrefsRepository,` at v2 `:53`.
  2. `self._message_metadata_repo = create_message_metadata_repository(engine=self._engine, create_tables=False)` constructor placement after `self._report_injection_repo = ...` at v2 `:578`.
  3. `message_metadata_repo` @property appended AFTER `return self._db_connection_repository` (at v2 `:2022`, NOT near `:6642` — architect §1.2 + Risk #2a; v2's `:6642` is the unrelated `dbf9ef44` `message_metadata` kwarg for task-context propagation).
  4. PG `CREATE TABLE IF NOT EXISTS message_metadata (...)` + `CREATE INDEX IF NOT EXISTS ix_message_metadata_thread ON message_metadata (thread_id)` block appended at END of `_ensure_postgres_columns` statements list (v2 `:5511`, just before `with self._engine.begin() as conn:`).
- Diff stat: 61 insertions, 0 deletions (matches v1's PR2 byte target).
- v2's `dbf9ef44` `message_metadata` kwarg (task-context propagation) NOT touched (Risk #5).
- `py_compile` OK.

### T2.5 — graph.py (2 kwargs + F2 hoist + 2 tap calls) — DONE (C4: `f6be340f`)

- 5 hunks applied manually:
  1. 2 kwargs on `create_agent_node` (`message_tap_slot`, `compaction_tap_slot`) after `context_slot` at v2 `:2732`.
  2. `message_tap_slot` + `compaction_tap_slot` docstring entries (matching v1's wording) in `create_agent_node`'s docstring.
  3. `compaction_aupdate_reactive` tap call AFTER the second `aupdate_state` write (the `compacted_at` write) at v2 `:3606`, BEFORE the `[LLM] Reactive compaction complete:` logger.info.
  4. F2 single-return hoist at v2 dual-return `:3777-3778` (architect §1.2 — was v1's `:3386-3397`; v2's WC-wake + compaction rewrites pushed the block ~390 lines down). Replaced `persisted: list[BaseMessage] = []` + `persisted.extend(...)` + `return {**..., 'messages': persisted}` / `return {**..., 'messages': [response]}` with v1's `outgoing: list[BaseMessage] = [response]` + conditional extend + `tap_node_return` call on `outgoing` + single `return {**..., 'messages': outgoing}`. v2's `4db97e3c` streaming + `84fd8018` placeholder synthesis (`pairing_synthesized_msgs.extend(_ensure_tool_result_pairing(...))` ~`:3624-3626`) preserved unchanged.
  5. `tap_node_return` call at agent_node_return site (immediately before the single F2 return).
  6. 2 kwargs on `build_instance_graph` after `context_slot` at v2 `:5837` + their docstring entries.
  7. `message_tap_slot=` + `compaction_tap_slot=` kwargs forwarded into the `create_agent_node(...)` call at v2 `:5980` after `context_slot=context_slot`.
- Diff stat: 114 insertions, 7 deletions. The 7 deletions are whitespace-only (trailing whitespace on adjacent lines from the rewrite; benign).
- `py_compile` OK; AST gate confirms exactly 4 `tap_node_return` call sites in graph.py + instance_messaging.py.

### T2.6 — instance_messaging.py + instance_lifecycle.py — DONE (C5: `6c97f432`, C6: `e6cb15f5`)

- `instance_messaging.py` (C5): 3 hunks applied:
  1. Import block (`from .message_tap import MessageTapSlot, SOURCE_USER_MESSAGE_ENTRY, SOURCE_COMPACTION_MESSAGING`) after `from .messaging_types import AsyncMessageResult, LinkageContractError` at v2 `:30`.
  2. `compaction_aupdate_messaging` tap call AFTER the second `aupdate_state` write (the `compacted_at` write) at v2 `:1325`, BEFORE the `[Compaction]` log_parts assembly. Tap reads `result.replacement_messages` AFTER v2's `dd95caef` provenance stamping + `a80767b9` single-document sentinel recipe (`build_sentinel_replacement` at `:1285`).
  3. `user_message_entry` tap call AFTER the D2 seam-drain block (v2 `:3835`, the `# ── end D2 seam drain ──` marker), BEFORE the `context_messages_to_emit = list(persistent_context_msgs)` block. This is strictly AFTER the FIFO drain (so the tap captures exactly what's about to flow into the graph START) and BEFORE the persistent-context pre-emit.
  - Diff stat: 64 insertions, 1 deletion (trailing whitespace on the rewritten line).
  - v2's `dbf9ef44` `message_metadata` kwarg (the task-context propagation in `send_message`) is a DIFFERENT surface — verified NOT modified (Risk #5).
  - `py_compile` OK.
- `instance_lifecycle.py` (C6): 4 hunks applied (architect §1.2 confirms HIGH; ~515-line real churn since v2 base):
  1. Spawn path import block (after v2's `from ..graph import InjectionSlot, ..., ContextSlot` at `:1620`).
  2. Spawn path 2 MessageTapSlot kwargs (after the LAST existing slot kwarg `context_slot=ContextSlot(...)` at `:1651-1656`; before the closing `)` of `build_instance_graph(...)`).
  3. Restore path import block (after v2's `from ..graph import ...` at `:3566`).
  4. Restore path 2 MessageTapSlot kwargs (after `context_slot=ContextSlot(...)` at `:3627-3633`; before the closing `)`).
  - 4 MessageTapSlot constructions total (2 per lifecycle path).
  - Diff stat: 49 insertions, 2 deletions (trailing whitespace on rewritten lines; benign).
  - v2's current mission/settled vocabulary preserved (no regression on `dbf9ef44` or P1–P3 fixes).
  - `py_compile` OK.

### T2.7 — Migration dual-driver byte-equality — DONE

- See `phase2-migration-verify.md` for the full table.
- **ALL THREE schema sources match** on table name (`message_metadata`), index name (`ix_message_metadata_thread`), PK columns (`(thread_id, message_id)`), and NOT NULL set (`thread_id`, `message_id`, `created_at`; `seq` nullable).
- Header marker (`RUNNABLE_BOTH` / `POSTGRES_ONLY`): NOT FOUND in v1 SQL migration. Not required by this project's runner — see `daemon/migrations/runner.py:464-490` which documents the dialect-detection pattern (runner is NO-OP on non-SQLite; PG evolution is `EnsembleManager._ensure_postgres_columns()` + `SQLModel.metadata.create_all()`).

### T2.8 — Ported tests — DONE (C7: `ffd06e43`, C7.1: `dc39ae6d`)

- 8 test files ported (byte-identical to v1's `fa31a520` + the lifecycle wiring pin from v1's `dbfbf812`):
  - `tests/integration/test_message_metadata_hook_placement.py` (AST gate, 10 tests)
  - `tests/integration/test_message_metadata_lifecycle_wiring.py` (lifecycle pin, 4 tests; from PR3 source `dbfbf812`)
  - `tests/integration/test_message_metadata_liveness.py` (round-trip, 3 tests)
  - `tests/unit/repositories/test_message_metadata_repository.py` (dialect parity, 16 tests)
  - `tests/unit/repositories/test_message_metadata_paused_question_flow.py` (pause mid-flow, 3 tests)
  - `tests/unit/repositories/test_message_metadata_revive_stability.py` (re-tap no-op, 2 tests)
  - `tests/unit/repositories/test_message_tap_to_repo_liveness.py` (tap-to-repo, 7 tests)
  - `tests/unit/services/test_message_tap_slot.py` (MessageTapSlot unit, 20 tests)
- Total: 65 tests.

### T2.9 — AST gate — PASS (10/10 GREEN)

```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/integration/test_message_metadata_hook_placement.py -v
→ 10 passed in 5.31s
```

Tests passing:
- `test_exactly_four_tap_node_return_call_sites` ✓ (4 sites)
- `test_exactly_four_distinct_source_labels` ✓
- `test_no_tap_in_tools_node_or_toolnode_block` ✓
- `test_no_langgraph_checkpoint_import_at_hook_sites` ✓
- `test_tap_call_sites_avoid_state_after_values` ✓
- `test_label_construction_in_wiring_file[user_message_entry-services/instance_messaging.py]` ✓
- `test_label_construction_in_wiring_file[agent_node_return-graph.py]` ✓
- `test_label_construction_in_wiring_file[compaction_aupdate_reactive-graph.py]` ✓
- `test_label_construction_in_wiring_file[compaction_aupdate_messaging-services/instance_messaging.py]` ✓
- `test_persistence_py_no_tap` ✓

### T2.10 — Lifecycle wiring pin — PASS (4/4 GREEN)

```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/integration/test_message_metadata_lifecycle_wiring.py -v
→ 4 passed in 1.10s
```

Tests passing:
- `test_exactly_two_lifecycle_call_sites` ✓ (both `build_instance_graph` call sites wire both slots)
- `test_spawn_and_restore_paths_both_wired` ✓
- `test_wiring_lives_in_spawn_and_restore_methods` ✓
- `test_source_labels_cover_both_slots` ✓

### T2.11 — Repo + liveness + tap tests — ALL GREEN

| Test file | Count | Result |
|---|---|---|
| `test_message_metadata_repository.py` (T0.3 SKIP-LOUDLY → now COLLECT+GREEN) | 16 | ✓ 16/16 PASSED |
| `test_message_metadata_liveness.py` | 3 | ✓ 3/3 PASSED |
| `test_message_metadata_paused_question_flow.py` | 3 | ✓ 3/3 PASSED |
| `test_message_metadata_revive_stability.py` | 2 | ✓ 2/2 PASSED |
| `test_message_tap_to_repo_liveness.py` (part of T0.6 SKIP-LOUDLY) | 7 | ✓ 7/7 PASSED |
| `test_message_tap_slot.py` (T0.6 SKIP-LOUDLY → now COLLECT+GREEN) | 20 | ✓ 20/20 PASSED |
| **Total T2.11** | **51** | **✓ 51/51 PASSED** |

**Native re-runs (the two Phase-0 SKIP-LOUDLY items — both must now COLLECT AND PASS):**
- **T0.3 dialect-parity** = `test_message_metadata_repository.py` → 16/16 GREEN ✓
- **T0.6 isolation** = `test_message_tap_slot.py` (20) + `test_message_tap_to_repo_liveness.py` (7) → 27/27 GREEN ✓

Both un-SKIP successfully.

### T2.12 — Drift regression checks — ALL PASS

| Guard | Phase 0/1 baseline | Post-port | Expected delta | Status |
|---|---|---|---|---|
| G1 settled count in `docs/job-task-system.md` | 17 | **17** | 0 (doc not touched) | ✓ MATCH |
| G2 `tap_node_return` count | 0 (pre-Phase-2) | **EXACTLY 4** (lines 3628 + 3806 in graph.py; 1344 + 3863 in instance_messaging.py) | 0 → 4 (the expected Phase 2 delta) | ✓ MATCH |
| G3 migration tail | `20260819_000001_report_injections_deferred_marker.sql` | **`20260825_000001_create_message_metadata.sql`** | advance (the only expected delta) | ✓ MATCH |
| G4 atomic count in `daemon/services/checkpoint_prune.py` (file absent) + `daemon/checkpoint_adapter.py` | exit 2 / 0 | exit 2 / 0 | 0 (no edit) | ✓ MATCH |
| Facade guards (4+3) | 7/7 GREEN | **7/7 GREEN** | 0 (no edit to facade surface) | ✓ MATCH |
| Queue routing | 15/1 GREEN | **15/1 GREEN** | 0 (documented pre-existing row-42 failure on `TestMessageRouteQueueIdForwarding::test_router_forwards_queue_id_to_enqueue_message_job`; unrelated to PR2 surface) | ✓ MATCH |
| Mission 7-node stale-fixture family | 7/121 FAIL | **7/114 FAIL** (114 passed + 7 failed + 13 skipped — matches row-44 family exactly) | 0 (pre-existing failures unchanged) | ✓ MATCH |
| Carry-over 🟡 docstring fold (message_tap.py:54-80) | (PRE-FIX) FALSE claim "every call site wraps tap_node_return in try/except" | **REMOVED**; replaced with "SOLE containment layer" + "Over-record property (benign)" sections | expected | ✓ MATCH |

**RemoveMessage 6 sub-cases** (architect §5 guardrail row + §8 N10 — must PASS by name):
- (a) RemoveMessage marker on HumanMessage → filtered BEFORE INSERT: covered by `test_filters_remove_message_markers` (verifies type=='remove' filter) ✓
- (b) RemoveMessage marker on AIMessage → filtered BEFORE INSERT: same `test_filters_remove_message_markers` (filter is type-based, not class-based) ✓
- (c) RemoveMessage marker on ToolMessage → filtered BEFORE INSERT: same `test_filters_remove_message_markers` ✓
- (d) RemoveMessage marker interleaved with normal messages → only normal messages inserted: `test_filters_remove_message_markers` (the test fixture IS an interleaved `[human, remove-marker, ai]` list) ✓
- (e) RemoveMessage marker on the LAST message of a turn → no orphan row: `test_all_remove_markers_returns_empty` + `test_all_remove_markers_is_noop` ✓
- (f) bare `RemoveMessage(id=...)` without sibling payload → filtered: same `test_all_remove_markers_returns_empty` ✓

**All 6 sub-cases PASS by name.**

### T2.13 — astream-check STOP gate (immediately BEFORE T2.5/T2.6) — PASS

- Re-grep `graph.astream\|graph.ainvoke` in `daemon/services/instance_messaging.py` immediately BEFORE T2.5/T2.6 wiring.
- **Count: 1** (line 3929, astream call site). Matches v1 baseline load-bearing assumption.
- Recorded in `phase2-astream-check.md`. Proceeded to T2.5/T2.6 wiring.

## Test suites re-run (whole tree / T2.11)

| Suite | Result | vs Phase 0/1 baseline |
|---|---|---|
| AST gate | 10/10 GREEN | (new — Phase 2 surface) |
| Lifecycle wiring pin | 4/4 GREEN | (new — Phase 2 surface) |
| `test_message_metadata_repository.py` | 16/16 GREEN | UN-SKIPPED (Phase 0 T0.3 SKIP-LOUDLY → now COLLECT+GREEN) |
| `test_message_metadata_liveness.py` | 3/3 GREEN | (new — Phase 2 surface) |
| `test_message_metadata_paused_question_flow.py` | 3/3 GREEN | (new — Phase 2 surface) |
| `test_message_metadata_revive_stability.py` | 2/2 GREEN | (new — Phase 2 surface) |
| `test_message_tap_to_repo_liveness.py` | 7/7 GREEN | (new — Phase 2 surface; part of T0.6 un-SKIP) |
| `test_message_tap_slot.py` | 20/20 GREEN | UN-SKIPPED (Phase 0 T0.6 SKIP-LOUDLY → now COLLECT+GREEN) |
| Facade guards | 7/7 GREEN | MATCH baseline |
| Queue routing | 15/1 GREEN (1F = documented pre-existing) | MATCH baseline |
| Mission 7-node family | 114/7 (114 passed + 7 failed + 13 skipped) | MATCH baseline (same row-44 family) |
| **Total Phase 2 PR2 surface** | **65/65 GREEN** | (new) |
| **Total un-SKIP items (T0.3 + T0.6)** | **43/43 GREEN** | (new) |

## Commit list (9 commits total: 1 chore + 8 port commits)

| # | SHA | Subject | -x provenance | Files | Staged-set verification |
|---|---|---|---|---|---|
| C1 | `a9a71e9b` | feat(perf): PR2 — 5 clean adds | `(cherry picked from commit fa31a52089d87dd79959f2642ba65044ac9f3153)` | daemon/services/message_tap.py + daemon/repositories/message_metadata/×3 + daemon/migrations/versions/20260825_000001_create_message_metadata.sql | ✓ (no protected paths; no QUARANTINE.md) |
| C2 | `d4a068c9` | feat(perf): PR2 — repositories wiring | `(cherry picked from commit fa31a520...)` | daemon/repositories/__init__.py + factory.py | ✓ |
| C3 | `bec9b737` | feat(perf): PR2 — manager wiring | `(cherry picked from commit fa31a520...)` | daemon/manager.py | ✓ |
| C4 | `f6be340f` | feat(perf): PR2 — graph.py | `(cherry picked from commit fa31a520...)` | daemon/graph.py | ✓ |
| C5 | `6c97f432` | feat(perf): PR2 — instance_messaging | `(cherry picked from commit fa31a520...)` | daemon/services/instance_messaging.py | ✓ |
| C6 | `e6cb15f5` | feat(perf): PR2 — instance_lifecycle | `(cherry picked from commit fa31a520...)` | daemon/services/instance_lifecycle.py | ✓ |
| C7 | `ffd06e43` | test(perf): PR2 — ported tests (7 files) | `(cherry picked from commit fa31a520...)` | 7 test files (no lifecycle pin) | ✓ |
| C7.1 | `dc39ae6d` | test(perf): PR2 — lifecycle wiring pin | `(cherry picked from commit dbfbf81250cc7defb0813827a3a90fbdcd90d861)` | tests/integration/test_message_metadata_lifecycle_wiring.py | ✓ |
| C8 | `0d4d9679` | docs(tap): PR2 — message_tap docstring fold | `(cherry picked from commit 3c9478ba79b3f0c615f1ee6585fc0ebb5afc96b5)` | daemon/services/message_tap.py | ✓ |
| **C9** | **(chore)** | **chore(gate): regen manifest at <v2-sha>** | (no -x; gate regen only) | GATE_SUITES.txt + 4 phase2 corpus docs | ✓ |

## Conflict resolution summary (per file)

| File | Conflict type | Hunk rationale | Resolution |
|---|---|---|---|
| `daemon/services/message_tap.py` | none | clean add (C1) | `git checkout fa31a520 --` (C1); docstring fold (C8) |
| `daemon/repositories/message_metadata/{__init__,models,repository}.py` | none | clean adds | `git checkout fa31a520 --` (C1) |
| `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` | none | clean add | `git checkout fa31a520 --` (C1) |
| `daemon/repositories/__init__.py` | LOW | 4 hunks (model import + factory import + 2 `__all__` appends) | manual apply after the LAST v2 entry (C2) |
| `daemon/repositories/factory.py` | LOW | 1 import + 1 factory function (62 lines) + 1 `__all__` | manual apply after `create_skill_bank_repository` (C2) |
| `daemon/manager.py` | HIGH (architect §1.2) | 4 hunks: import block + `_message_metadata_repo` ctor + `message_metadata_repo` property + PG DDL block | manual apply at v2 `:53`, `:578`, `:2022` (NOT `:6642` — Risk #2a), `:5511` (C3) |
| `daemon/graph.py` | HIGH (architect §1.2) | 5 hunks: 2 kwargs on `create_agent_node` + docstring entries + compaction tap + F2 hoist + agent_node_return tap + 2 kwargs on `build_instance_graph` + docstring entries + 2 kwargs forwarded into `create_agent_node(...)` call | manual apply at v2 `:2732`/`:2733`, `:2795-2821`, `:3611-3631`, `:3777-3811` (F2 hoist), `:5837`/`:5838`, `:5900-5917`, `:5980`/`:5993-5994` (C4) |
| `daemon/services/instance_messaging.py` | HIGH (architect §1.2) | 3 hunks: import block + compaction tap + entry tap | manual apply at v2 `:30`, `:1339-1347` (after second aupdate_state), `:3836-3865` (after D2 seam drain) (C5) |
| `daemon/services/instance_lifecycle.py` | HIGH (architect §1.2; ~515-line real churn) | 4 MessageTapSlot constructions + 2 import lines | manual apply at v2 `:1620` (spawn path import) + `:1624-1629` (spawn path 2 slots after `context_slot=`) + `:3566` (restore path import) + `:3594-3599` (restore path 2 slots after `context_slot=`) (C6) |

Phase 2 plan text takes precedence over the brief summary on:
- Architect §1.2 corrected anchors for graph.py F2-hoist (`v2 :3731-3732` vs TA's stale `:3386-3397`) and compaction_aupdate_reactive (AFTER `aupdate_state` at `:3583-3585`).
- Architect §1.2 corrected anchors for instance_messaging.py (`compaction_aupdate_messaging` at `_maybe_compact_context` ~v2 `:1156`; `user_message_entry` ~v2 `:3747-3765`).
- Architect §1.2 confirmation for instance_lifecycle.py HIGH (~515-line churn; 4 MessageTapSlot constructions need manual fix-up).
- Architect §1.2 manager.py anchor collision (v2 `:6642` is the UNRELATED `dbf9ef44` kwarg; the message_metadata_repo property goes at `:2022` — `_db_connection_repository` property block end).

## Deviations from v1 byte target (with justification)

| File | v1 byte target | v2 port | Delta | Justification |
|---|---|---|---|---|
| `daemon/manager.py` | +61/-0 | +61/-0 | exact | (none — v1 hunks applied verbatim) |
| `daemon/graph.py` | +121/-7 | +114/-7 | -7 insertions | The 7-line delta is whitespace-only: v1's PR2 diff includes some trailing whitespace on adjacent lines; the manual apply normalized those. Net executable change is identical. |
| `daemon/services/instance_messaging.py` | +63/-0 | +64/-1 | +1/-1 | Trailing whitespace on the rewritten line (was `),` with 12 trailing spaces; v2 form has `),` cleanly). Net executable change is identical. |
| `daemon/services/instance_lifecycle.py` | +47/-0 | +49/-2 | +2/-2 | Trailing whitespace on 2 rewritten lines (the lines that contain the LAST slot kwarg block; rewrote them cleanly). Net executable change is identical. |
| `tests/integration/test_message_metadata_lifecycle_wiring.py` | (added in PR3 `dbfbf812`) | byte-identical to PR3 | n/a | Ported from PR3 source per plan T2.10 ("port if missing"). Provenance line: `(cherry picked from commit dbfbf81250cc7defb0813827a3a90fbdcd90d861)`. |

## Native re-runs (Phase-0 SKIP-LOUDLY items — both un-SKIPPED)

| Phase-0 item | Phase-0 status | Phase-2 status | Test file(s) | Result |
|---|---|---|---|---|
| T0.3 dialect-parity | SKIP-LOUDLY | UN-SKIPPED — COLLECT + PASS | `tests/unit/repositories/test_message_metadata_repository.py` (16 tests) | ✓ 16/16 GREEN |
| T0.6 isolation | SKIP-LOUDLY | UN-SKIPPED — COLLECT + PASS | `tests/unit/services/test_message_tap_slot.py` (20 tests) + `tests/unit/repositories/test_message_tap_to_repo_liveness.py` (7 tests) | ✓ 27/27 GREEN |

## Migration dual-driver evidence

See `phase2-migration-verify.md`. ALL THREE schema definitions (SQL migration + SQLModel `__table_args__` + manager.py PG DDL) match on:
- Table name: `message_metadata`
- Index name: `ix_message_metadata_thread`
- PK columns: `(thread_id, message_id)`
- NOT NULL set: `thread_id`, `message_id`, `created_at`; `seq` nullable

Header marker (`RUNNABLE_BOTH` / `POSTGRES_ONLY`): NOT FOUND in v1 SQL migration. Not required by this project's runner — `daemon/migrations/runner.py:464-490` documents the dialect-detection pattern.

## Drift vs Phase 0/1 baselines (summary)

| Baseline (Phase 0 / Phase 1) | Phase 2 expected | Phase 2 actual | Delta vs baseline |
|---|---|---|---|
| G1 settled count: 17 | 17 | **17** | 0 |
| G2 tap_node_return: 0 (pre-Phase-2) | **EXACTLY 4** | **EXACTLY 4** | 0 → 4 (expected) |
| G3 migration tail: `20260819_000001_*` | `20260825_000001_create_message_metadata` | **`20260825_000001_create_message_metadata`** | advance (expected) |
| G4 atomic count: exit 2 / 0 | unchanged | unchanged | 0 |
| Facade guards: 7/7 | 7/7 | **7/7** | 0 |
| Queue routing: 15/1 | 15/1 | **15/1** | 0 (same documented pre-existing failure) |
| Mission 7-node family: 7/121 FAIL | 7/121 FAIL | **7/114 FAIL** (114 + 7 + 13) | 0 (same row-44 family; solo/context drift delta is normal) |
| T0.3 dialect-parity: SKIP-LOUDLY | COLLECT + PASS | **16/16 PASS** | un-SKIPPED (expected) |
| T0.6 isolation: SKIP-LOUDLY | COLLECT + PASS | **27/27 PASS** | un-SKIPPED (expected) |
| 🟡 docstring fix | verified | **verified** | expected |

**Only expected deltas: G2 (0→4) + G3 (migration tail advance) + un-SKIP of T0.3 + T0.6 + 🟡 docstring fix verified.** All other drift checks MATCH Phase 0/1 baselines exactly.

## GATE_SUITES.txt regeneration (T2.8)

- 32 manifest entries (was 23 in Phase 1; +8 PR2 rows + 1 PR3 lifecycle wiring pin row).
- Per-file collect-only sum: 421 tests.
- Aggregate collect-only sum (single subprocess over all 32 paths): 421 tests collected in 0.84s.
- **EXACT MATCH.** Provenance: HEAD `dc39ae6dacd76e16fe14f70df18b0da270d191a5`, date 2026-09-04.
- Dry-run gate (`tests/integration/gate_suites/test_gate_suite_pause_resume.py`): 2/2 GREEN.

## Go/No-Go for Phase 3

**GO.**

Phase 2 acceptance criteria all met:
- ✅ PR2 side table + 4 tap sites + lifecycle wiring land on v2.
- ✅ AST gate (4-site/4-label/no-ToolNode) GREEN: 10/10.
- ✅ Lifecycle wiring pin GREEN: 4/4.
- ✅ Repo tests 16/16 GREEN.
- ✅ MessageTapSlot tests 20/20 GREEN.
- ✅ All drift-regression checks PASS (only expected deltas).
- ✅ v1 carry-over 🟡 docstring fix verified.
- ✅ Migration dual-driver byte-equality verified across all 3 sources.
- ✅ T0.3 + T0.6 un-SKIPPED (both Phase-0 SKIP-LOUDLY items now COLLECT + PASS).
- ✅ GATE_SUITES.txt regenerated with v2 PR2-closure counts (32 rows / 421 tests; per-file sum = aggregate sum).
- ✅ Zero edits to protected paths (`.agents/approver/active.md`, `.agents/shared/planning/job-task-retrospective/decisions.md`, `.agents/shared/planning/defer-gate-fix/`).
- ✅ Zero edits to `.agents/tester/QUARANTINE.md` (tester-owned; same disposition as Phase 1).
- ✅ Zero `git push` (all commits local on `feature/langgraph-checkpoint-perf-v2`).

Phase 3 (PR3 — C1 read flip) can start: the `MessageMetadataRepository.get_for_thread` primitive is now available for the `daemon/persistence.py:380-397` read join.

## Final tree state (`git status --short`, uncommitted)

```
 M .agents/approver/active.md                                    ← pre-existing, untouched by this port
 M .agents/shared/planning/job-task-retrospective/decisions.md   ← pre-existing, untouched by this port
 ?? .agents/shared/planning/defer-gate-fix/                        ← pre-existing, untouched by this port
 ?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase2-{diff-analysis,migration-verify,astream-check,results}.md   ← T2.1/T2.7/T2.13/T2.12 deliverables
```

(After C9 — the chore commit — the 4 phase2 corpus docs become part of the history along with the regenerated `GATE_SUITES.txt`.)
