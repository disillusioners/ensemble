# Phase 1 Plan: LangGraph Checkpoint / Message Persistence Performance

| | |
|---|---|
| Date | 2026-08-25 (initial) · 2026-08-25 (Rev 2 — reviewer criticals applied) · 2026-08-25 (Rev 3 — narrow criticals: F1 entry-path miss, F2 dual-return at single site) · 2026-08-25 (Rev 4 — doc-only final pass: B1 ainvoke coverage + B2 site-count leftovers + B3 phantom mechanism) |
| Author | planner[v2] via plan-creation worker |
| Status | **Ready for Implementation — Rev 4 (doc-only final pass)** |
| Branch | feature/langgraph-checkpoint-perf |
| Workdir | `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` |
| Source docs (in order) | `~/Downloads/langgraph-checkpoint-performance-discussion.md` (§4, §9, §16–17, §29, §32, §33, §37) · `research-findings.md` (HIGH confidence) · `roadmap.md` (ranking + landing order) |
| Companion doc | `decisions.md` (same directory — Rev 4 doc-only final pass) |
| Rev 2 driver | Reviewer verdict `NEEDS_CHANGES` on 8 criticals (with binding leader decisions LD-D1 / LD-D2 / LD-OQ1 / LD-OQ2). All 8 confirmed RESOLVED in Rev 3. |
| Rev 3 driver | Re-review cycle 2 verdict `NEEDS_CHANGES` — NARROWLY. F1 (entry-path user-message tap missed) + F2 (graph.py:3396-3397 is actually two returns; AST "3 sites" gate would reject correct implementation) + 5 folds (F3 + 4). |
| Rev 4 driver | Re-review cycle 3 verdict — ENGINEERING SUBSTANCE APPROVED. Doc-only blockers: B1 (false ainvoke coverage claim + wrong citations at phase1-plan.md:225, 281, 950; decisions.md:30, 48), B2 ("3 sites" leftovers at phase1-plan.md:470, 477), B3 (phantom mechanism at phase1-plan.md:52, 348, 481; decisions.md:59). Plus leader-decision wording alignment on D2 (create_all) and `msgs_repo=None` (explicit degradation). |

---

## Objective

Eliminate the GET /messages checkpoint-history scan (PERF-1: kill `alist()`), land the durable message-metadata side table (Solution M) behind an idempotent repository-layer INSERT, instrument the read path with **observed-count** structured logs so the before/after delta + post-C1 invariant are provable (the `message_api_checkpoint_list_total == 0` invariant from §32 — by observation, not hardcoded), and add a **direct anti-join** reference-aware `checkpoint_blobs` prune that halts the verified unbounded-growth defect (§9 + §36) — all without touching the GET /messages response schema, the pause/resume/turn-reconciler semantics, the fail-closed authz pattern, or the saver's `aupdate_state`/`aput` mechanics.

*Testable completion sentence (Rev 3):* GET /messages no longer enumerates checkpoint history (verified by a spy/DB-trace regression test AND a real pre-Phase-1 frozen fixture captured in PR1); the response shape is byte-compatible; **all persisted messages — both user `HumanMessage` (entry-path tap) and LLM `AIMessage` (agent-node tap) on a plain turn — receive a `message_metadata` row**; the blob prune passes the doc's §9 required-tests checklist using a direct anti-join in dry-run mode for ≥1 retention cycle followed by ≥7 days destructive-enabled with no false positives AND a **mandatory real-saver integration test (write→prune→aget/resume reconstruction incl. `_DeltaSnapshot` chains + fail-safe + concurrent-aput safety) green before destructive enable**; pause/resume + turn-reconciler + interrupt-resume suites remain green; the `message_api_checkpoint_list_total` observed count collapses to 0 in production for ≥7 consecutive days post-C1.

---

## Scope

### In Scope

| Component | One-line goal |
|-----------|---------------|
| **C4** (Phase 0 lite) | Structured `[/Messages]` + `[CheckpointPerf]` logs emitting the **observed `alist_count`** (not hardcoded 0) + freeze-suite fixture capture from a real pre-Phase-1 run + integration test gate suites enumerated by filename |
| **C2** (Solution M) | `message_metadata(thread_id, message_id, created_at, seq)` side table + **SYNC** repository-layer idempotent upsert bridged through `asyncio.to_thread` + tap at the **entry path** (`_build_graph_input` at `instance_messaging.py:237-244`, covering the `astream` entry; **direct `ainvoke` is accepted-degradation OOS per B1**) capturing the user `HumanMessage` + tap at the **single agent-node return** (after the F2 mechanical refactor hoists the if/else to one `outgoing` list) + idempotent re-taps at `aupdate_state` compaction sites + `RemoveMessage`-filter `_extract_ids` + revive/compaction stability test + write-liveness gate test (asserts rows for BOTH user id AND AI id on a plain turn) |
| **C1** (PERF-1) | `get_instance_messages` reads timestamps from C2's table; **zero** `alist()` calls; response schema frozen via real pre-Phase-1 fixture; observed-count gate collapses to 0 |
| **C3** | **Direct anti-join** `checkpoint_blobs` prune (DELETE…NOT EXISTS against `checkpoint->'channel_versions'`) + fail-safe zero-refs skip + LOG ERROR + prod-layout verification + `checkpoint_ns` handled explicitly + **real-saver integration test (mandatory blocking gate)** + automated restore-rehearsal roundtrip |
| **Flag A** | **Import-level hard-fail test in the standard test suite** asserting no `langgraph.checkpoint.*` import in `daemon/routers/**` (LD-OQ2 — no new CI infra) |

### Out of Scope

- Cursor pagination (PERF-2 / Solution C) — Phase 2 (frontend coupling).
- Durable `agent_messages` / `agent_events` schema (PERF-3 / Solution A+B) — Phase 2 wave 1.
- Historical metadata backfill (PERF-4 / Solution N) — Phase 2 wave 1 (Flag B).
- `ShallowPostgresSaver` evaluation (PERF-5 / Solution D) — Phase 2.
- Bounded active message state (PERF-6 / Solution F+G) — Phase 3.
- Artifact / reference storage (PERF-7 / Solution J) — Phase 3.
- Checkpoint lifecycle / retention policy choice (PERF-8 / Solution E+T) — Phase 3.
- PG compression / network tuning (PERF-9 / Solution Q+R) — Phase 5.
- §33 full layering refactor (routers → MessageRepository / runtime → CheckpointRepository) — Phase 2+ (Phase 1 ships the import-level hard-fail test only).
- Saver connection concurrency pool — measured via C4, sized in Phase 3.
- Frontend changes — the GET /messages response schema is frozen in Phase 1.
- Solution H/I, Solution S, Solution U — deferred per `roadmap.md`.
- **Watchover tap (LD-D2)** — accepted known degradation; verify the serializer `type=='tool'` skip line (`daemon/persistence.py:359-361` — `serialize_message` skips `type=='tool'` from the response output, so tool-message timestamps being absent from `message_metadata` is invisible to users) and document. The Watchover subsystem remains a read-only guard; the C2 tap is NOT applied to messages synthesized under its `set_deferred_watchover_terminate` cascade. Re-evaluate in Phase 2.
- **Direct `ainvoke` invocation at `daemon/services/instance_messaging.py:1055`** — accepted known degradation per B1. The direct `ainvoke` site constructs `{"messages": [message]}` INLINE and does **NOT** call `_build_graph_input`; zero production callers; the input dict carries no message `id` (id-less input); the `state.ts` fallback at `persistence.py:368-370` applies (degrades to the latest-checkpoint timestamp, same handling as the watchover degradation above per LD-D2). The C2 entry-path tap covers the `astream` invocation path only (the production path). **No 5th tap is added** — mirrors the watchover handling; recorded in D19 + D1 + the message_tap docstring spec. The two formerly-cited lines (`graph.py:3385-3394` and `graph.py:1055`) are NOT the invocation sites — verified directly: `graph.py:3385-3394` is agent_node's return block; `graph.py:1055` is the LoopDetector scan. Re-evaluate in Phase 2 if a direct-`ainvoke` call site ships in production.
- **Custom ToolNode wrapper / ToolExecutor replacement** — explicitly REJECTED. ToolNode is registered raw at `daemon/graph.py:5546`; we do not wrap or replace it. Tool messages are **NEVER tapped** (no tools_node hook) and never receive a `message_metadata` row. Display invisibility comes from `serialize_message` skipping `type=='tool'` at `daemon/persistence.py:359-361` (B3 reword — the prior "next agent_node run" phrasing was a phantom mechanism).
- **`checkpoint_blob_refs` reference-table machinery** (table + `BlobRefExtractor` + `rebuild_refs_for_thread` + migration `20260825_000002`) — replaced by the direct anti-join (LD-D1). Net scope reduction.
- **Nudge + language_check nodes are id-less, never tapped** (B3 reword) — C2 tap fires only at the 4 approved sites (entry path + agent_node single-return + 2 compaction sites). `nudge` and `language_check` are constructed without LangChain message ids (or at most with `add_messages` defaults that don't carry), so they would never pass `_extract_ids`'s `getattr(m, "id", None)` truthy check, and they fall to `state.ts` if they ever surfaced in the response. They are NOT subject to any "lag" — there's no mechanism to lag; they simply never tap. Phase 2 PERF-3 may revisit if a need arises.
- **`RemoveMessage` markers** — filtered out in `_extract_ids` (`type == "remove"` → skip), not inserted as new-message rows.

---

## Hard Constraints (Rev 2 — restated + new binding entries)

1. **No frontend changes** — `GET /instances/{id}/messages` response schema stays byte-compatible (Angular untouched).
2. **Do not touch pause/resume / turn-reconciler semantics** — checkpoint-at-node-boundary, `is_retry=True` resume-from-checkpoint, `resume_target_turn_id` handles, and the 8 mirror tables are off-limits.
3. **Repository pattern** — no raw saver access from `routers/`; factory injection only.
4. **Checksummed / ordered migrations** — every new table / column follows the `YYYYMMDD_HHMMSS_name.sql` convention with `-- UP` / `-- DOWN` sections and `schema_migrations.checksum`. (C2 only — Rev 2 drops C3's extra migration; C3 needs NO new table.)
5. **Fail-closed authz untouched** — no new code path that weakens the existing per-instance authorization checks.
6. **NO naive `DELETE FROM checkpoint_blobs`** — blobs are versioned/shared across checkpoint reconstructions (§9); the prune is a direct anti-join on `checkpoint->'channel_versions'` (§36) with a fail-safe zero-refs skip.
7. **Phase 1 mergeable independently** — each component is a stand-alone PR that can be merged + reverted without breaking the others.
8. **Do not disturb `daemon/migrations/checkpoint_migrator.py`** — its `alist()` use is offline-only (export path), unrelated to the API read path (verified by grep; only `persistence.py:326` uses `alist` in production at the time of writing).
9. **Do not disturb `knowledge_tools` `aget` readers** — they read single checkpoints, not enumerated history.
10. **Pre-existing test-failure quarantine** — 5 failures in `tests/unit/tools/test_archive_lifecycle.py` are pre-existing (quarantined in `QUARANTINE.md`); not ours, do not count as regressions.
11. **PR4 (C3) BLOCKED on the real-saver integration test gate** — `tests/integration/checkpoint_prune_real_saver.py` must pass before PR4 merges AND before destructive enable. Test covers write→prune→aget/resume reconstruction (incl. `_DeltaSnapshot` chains), zero-refs fail-safe trigger, and concurrent-aput safety. Synthetic-marker tests alone do not satisfy this gate.
12. **`CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` env-flag enable requires the prod-layout verification** (LD-OQ1) — query prod `checkpoints` for the actual `(checkpoint_ns, channel, version)` layout and confirm `checkpoint->'channel_versions'` matches the assumed JSONB shape; do NOT enable destructively if shape diverges.
13. **No custom ToolNode wrapper / executor replacement** — rejected (Critical 4).
14. **No new CI infra (LD-OQ2)** — Flag A enforcement = import-level hard-fail test inside `tests/`, run under existing standard test gates.
15. **Fixture capture precedes any read-path change** — PR1 (C4) MUST capture the response-shape frozen fixture before PR3 (C1) changes any read-path code (Critical 5).
16. **Sync repository + `asyncio.to_thread` bridge** — `MessageMetadataRepository` is **synchronous** (matching the existing `daemon/repositories/factory.py` contract; engine is `sqlalchemy.Engine` per line 10); the tap bridges via `asyncio.to_thread(...)` (Critical 2).
17. **Watchover tap explicitly OOS** — known degradation accepted under LD-D2; document and move on.

---

## Component Specification

### C4 — Phase 0 Lite Instrumentation (lands FIRST; baseline + fixture capture)

**Files touched**

| File | Change |
|------|--------|
| `daemon/persistence.py` | Wrap `saver.aget(config)` (line 312) with `time_saver_op("aget", …)`; **wrap the `alist` call at lines 326-333 with `time_saver_op("alist", …)`** and **emit the observed `alist_count`** via `log_messages_api(...)` per request (carries `alist_count=<observed>`, NOT a hardcoded 0); `[/Messages]` log carries `(duration_ms, message_count, bytes_estimate, alist_count)`; existing `alist(…, limit=1000)` walk stays in place during C4 — C1 deletes it |
| `daemon/services/maintenance.py` | Wrap `_prune_per_thread_checkpoints` (lines 678-730) with `time.perf_counter()`-bracketed INFO log on entry/exit carrying thread count + total deleted |
| `daemon/checkpoint_perf.py` (new) | `log_saver_op(op, thread_id, duration_ms, deleted=0)`; `async time_saver_op(op, thread_id, coro)` (matches `time.perf_counter` pattern at `daemon/migrations/runner.py:302` and `daemon/services/blueprint_matcher.py:183`); `log_messages_api(instance_id, duration_ms, message_count, bytes_estimate, alist_count)` emits `alist_count=<observed>`; `invariant_check_no_alist()` for the ERROR log on accidental re-introduction |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` (new) | Verify all log sites emit observed fields |
| `tests/integration/test_messages_response_fixture_capture.py` (new, **Rev 2**) | **MANDATORY for PR1 merge**: spin up an in-process instance graph; run 4 conversations (id-less messages, multimodal HumanMessage with image content blocks, AIMessage with `tool_calls`, AIMessage with `thinking`); call `manager.get_messages(instance_id)`; dump the JSON response to `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json`. Run on the **pre-C1** code path (alist walk present). The fixture file becomes the contract that PR3 matches byte-for-byte. |
| `tests/integration/gate_suites/test_gate_suite_pause_resume.py` (new, **Rev 2**) | Enumerate-by-filename: assert the gate suites listed below all pass on PR1's branch. **Gate suites (must remain green throughout Phase 1)**: `tests/integration/test_pause_resume.py`, `tests/integration/test_resume_*.py`, `tests/integration/test_turn_reconciler*.py`, `tests/integration/test_interrupt_resume.py`, `tests/integration/test_human_approval_resume.py`, `tests/integration/test_is_retry_resume_from_checkpoint.py`, `tests/integration/test_8_mirror_tables.py`, `tests/integration/test_aupdate_state_idempotent.py`, `tests/integration/test_get_messages_lifecycle.py` (exact filenames verified during implementation; if a filename is missing, replace with the canonical path for that gate). PR1 contains a dry-run of this enumeration gate; PR2 + PR3 + PR4 re-run it in CI. |

**Function signatures (verbatim, Rev 2)**

```python
# daemon/checkpoint_perf.py
import logging
import time
from typing import Any, Awaitable

logger = logging.getLogger("daemon.checkpoint_perf")


def log_saver_op(op: str, thread_id: str, duration_ms: int, *, deleted: int = 0) -> None:
    """Single source of truth for ``[CheckpointPerf]`` structured-ish logs."""
    logger.info(
        f"[CheckpointPerf] op={op} thread={thread_id[:8] if thread_id else '?'} "
        f"duration_ms={duration_ms} deleted={deleted}"
    )


async def time_saver_op(op: str, thread_id: str, coro: Awaitable[Any]) -> Any:
    """Time a saver operation; emits ``[CheckpointPerf]`` and returns the result."""
    t0 = time.perf_counter()
    try:
        return await coro
    finally:
        elapsed = int((time.perf_counter() - t0) * 1000)
        log_saver_op(op, thread_id, elapsed)


def log_messages_api(
    instance_id: str,
    duration_ms: int,
    message_count: int,
    bytes_estimate: int,
    alist_count: int,  # OBSERVED; not a hardcoded constant
) -> None:
    """Emit the GET /messages single-line structured log carrying the observed alist count."""
    logger.info(
        f"[/Messages] instance={instance_id[:8] if instance_id else '?'} "
        f"duration_ms={duration_ms} messages={message_count} bytes={bytes_estimate} "
        f"alist_count={alist_count}"
    )


def invariant_check_no_alist() -> None:
    """ERROR log if invoked at all post-C1."""
    logger.error(
        f"[CheckpointPerf] INVARIANT VIOLATION: alist invoked on request path post-C1; "
        f"see roadmap §6 (Phase 1 gate #2). The expected value is 0 (by absence)."
    )
```

**Pseudo-code for `get_instance_messages` instrumentation (C4 wrapper only — alist stays)**:

```python
# daemon/persistence.py — top of get_instance_messages
t0 = time.perf_counter()
saver = (
    checkpointer.raw_saver
    if isinstance(checkpointer, CheckpointerAdapter)
    else checkpointer
)
config = {"configurable": {"thread_id": instance_id}}

# aget — TIMED + GUARDED
state = await time_saver_op("aget", instance_id, saver.aget(config))
if state is None:
    return []

# ... existing aget() result handling ...

# alist — TIMED + OBSERVED-COUNTED (Rev 2 — Critical 7)
t_alist = time.perf_counter()
alist_count = 0
async for checkpoint_tuple in saver.alist(config, limit=1000):
    # ... existing per-checkpoint walk body ...
    alist_count += 1  # observed count incremented per checkpoint tuple
# after the loop, emit:
duration_ms = int((time.perf_counter() - t0) * 1000)
bytes_estimate = sum(len(str(m.content).encode()) for m in messages)
log_messages_api(instance_id, duration_ms, len(messages), bytes_estimate, alist_count)
```

**Tests**

| Test | Assertion |
|------|-----------|
| `test_time_saver_op_logs_duration_ms` | `[CheckpointPerf] op=aget thread=... duration_ms=N` in captured log; returns coro result |
| `test_log_messages_api_emits_observed_count` | `alist_count=<n>` (observed) appears; the constant is NOT hardcoded |
| `test_invariant_check_no_alist_emits_error_on_call` | Calling with the alist removal flag emits ERROR |
| `test_get_instance_messages_logs_observed_alist_count` | Mock-saver alist walk yields N tuples; `alist_count=N` in log |
| `test_maintenance_prune_logged_with_duration` | `_prune_per_thread_checkpoints` exit log carries duration_ms + deleted count |
| `test_response_fixture_capture_round_trip` (**new, Rev 2**) | The fixture file is generated by `test_messages_response_fixture_capture` and matches the response of a fresh in-process conversation run on the pre-C1 code path |
| `test_gate_suite_enumeration_passes` (**new, Rev 2**) | Each gate suite filename returns exit 0 from pytest in a subprocess; failure on any name blocks PR1 merge |

**Risks + mitigations**

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Log volume from /messages spike (one INFO line per request) | Low | High | INFO-only with bracketed prefix; suppressible via `CHECKPOINT_PERF_LOGS=0` env |
| `perf_counter` precision insufficient | Low | Low | Coalesce to int milliseconds; document contract |
| Maintenance timing swallows real errors | Low | Low | Wrap timing in `try/finally` around the existing try/except inside `_prune_per_thread_checkpoints`; do not change error semantics |
| Fixture capture run depends on LLM (non-deterministic output) | Medium | Medium | Capture only message METADATA: ids, role, content-types, tool_call ids, message order; ignore free-text content. Field-level snapshot, not output-text snapshot. |
| Gate suite filename drift (the suite is renamed later) | Low | Medium | Use a single source of truth `tests/integration/gate_suites/GATE_SUITES.txt` that PR1 creates + subsequent PRs append to; document the contract |

**Rollback procedure**

1. Revert `daemon/persistence.py` (drop `_time_saver_op` wrap + `log_messages_api` call).
2. Delete `daemon/checkpoint_perf.py` + log-related tests; keep the **fixture file + gate-suite enumeration** (`test_messages_response_fixture_capture.py` and `test_gate_suite_enumeration_passes.py`) since they establish a baseline whether or not C1 merges.
3. Phase 1 gate unaffected: no schema touched.

**Exit criterion**

`[/Messages]` logs visible in dev/local with `alist_count` reflecting the real observation (typically 50–1000 in pre-C1); `[CheckpointPerf]` lines fire on every saver op; the **`/messages` frozen-response fixture is captured to disk**; the **gate-suite enumeration test** is green against the pre-Phase-1 branch; pre-C1 baseline metrics captured on ≥1 real instance.

---

### C2 — `message_metadata` Side Table + Sync Repository + Node-Return Tap (idempotent re-tap)

**Files touched**

| File | Change |
|------|--------|
| `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` (new) | `-- UP`: `CREATE TABLE IF NOT EXISTS message_metadata(thread_id, message_id, created_at, seq, PRIMARY KEY (thread_id, message_id))` + `CREATE INDEX IF NOT EXISTS ix_message_metadata_thread ON message_metadata(thread_id)`; `-- DOWN`: DROP INDEX + DROP TABLE. SQLite-only via MigrationRunner; PG via `SQLModel.metadata.create_all()` (fresh) + `daemon/manager.py::_ensure_postgres_columns()` (existing DBs) emitting byte-identical DDL — dual-driver comment header (mirrors `20260819_000001_report_injections_deferred_marker.sql`) |
| `daemon/repositories/message_metadata/__init__.py` (new) | Exports `MessageMetadata`, `MessageMetadataRepository` |
| `daemon/repositories/message_metadata/models.py` (new) | SQLAlchemy model: `MessageMetadata(thread_id TEXT NOT NULL, message_id TEXT NOT NULL, created_at TEXT NOT NULL, seq INTEGER, PRIMARY KEY (thread_id, message_id))`; index on `(thread_id,)` |
| `daemon/repositories/message_metadata/repository.py` (new) | **SYNC** methods (Critical 2): `def upsert_batch(self, thread_id: str, items: list[tuple[str, str, int|None]]) -> int` via `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing(index_elements=["thread_id","message_id"])` + SQLite equivalent; `def get_for_thread(self, thread_id: str) -> dict[str, tuple[str, int|None]]`. No `async def`. Engine comes from `daemon/repositories/factory.py` (sync `sqlalchemy.Engine`). |
| `daemon/manager.py` | Register `MessageMetadataRepository` against the existing sync factory (mirrors how other repos are wired); expose `manager.message_metadata_repo` |
| `daemon/graph.py` | Add `message_tap_slot` parameter to `create_agent_node` (line 2621+); **mechanical refactor** of the existing dual-return at `graph.py:3386-3397` (F2) so both branches funnel through a single `outgoing: list[BaseMessage]` and one `return`; fire `await message_tap_slot.tap_node_return(outgoing, thread_id)` ONCE just before the single `return {**watchover_state_reset, 'messages': outgoing}` — covers both the injected-branch (line 3396) AND the plain-turn branch (line 3397). ALSO fire `tap_node_return(result.replacement_messages, thread_id)` after the reactive-compaction `aupdate_state` at `graph.py:3248-3250`. **NO `tools_node` tap** (Critical 4); tool-message timestamps are never persisted to `message_metadata` because display skips `type=='tool'` at `daemon/persistence.py:359-361` |
| `daemon/services/instance_messaging.py` | (a) **Entry-path tap (Rev 3 / F1, Rev 4 B1 corrected)**: at `_build_graph_input` (`instance_messaging.py:237-244` per reviewer file:line), fire `await message_tap_slot.tap_node_return(graph_input_messages, thread_id)` after the per-turn context + user-message construction — captures the user `HumanMessage` that lands at graph START. **Coverage** (Rev 4 B1 corrected): the entry-path tap covers the `astream` invocation path which flows through `_build_graph_input`. The direct `ainvoke` invocation at `instance_messaging.py:1055` constructs `{"messages": [message]}` INLINE and does NOT call `_build_graph_input` — direct `ainvoke` is accepted-degradation OOS per B1 (zero production callers; id-less input dict; `state.ts` fallback applies; mirror the watchover handling); do NOT add a 5th tap. Without the entry-path tap, `astream`-invoked user messages never enter `message_metadata` and degrade to the `state.ts` fallback. (b) **Compaction re-tap**: After the `aupdate_state` call at lines 810-822, fire `await message_tap_slot.tap_node_return(result.replacement_messages, thread_id)`; this is an **IDEMPOTENT RE-TAP under ON CONFLICT DO NOTHING** — documented explicitly |
| `daemon/services/message_tap.py` (new) | `class MessageTapSlot: def __init__(self, repo, source): ...; async def tap_node_return(self, persisted_list, thread_id) -> int` — **filters `type=='remove'` (RemoveMessage markers must not be inserted as new-message rows — Critical 8 fold-in)**, dedups IDs, calls `asyncio.to_thread(self._repo.upsert_batch, …)`; failure path non-fatal. **Source label values (Rev 3)**: `"user_message_entry"` (entry path), `"agent_node_return"` (post-F2 refactor), `"compaction_aupdate_reactive"` (graph.py:3248-3250), `"compaction_aupdate_messaging"` (instance_messaging.py:810-822). |
| `daemon/checkpoint_perf.py` (from C4) | Add `log_message_tap(thread, count, source)` |
| `tests/unit/repositories/test_message_metadata_repository.py` (new) | SQLite + PG dual-driver upsert tests: insert → row exists; re-insert same key → no row change; multi-row batch; empty batch; thread not found |
| `tests/integration/test_message_metadata_liveness.py` (**new, Rev 2 — Critical 2 write-liveness gate**, **Rev 3 — strengthened for F1**) | End-to-end: spin up a real instance, send one turn; **await both taps** (entry path for user `HumanMessage`, agent_node for AIMessage); assert rows landed in `message_metadata` with the **USER message id** AND the **AIMessage id** AND non-null `created_at` for each. This test BLOCKS PR2 merge — proves the plumbing (sync repo + to_thread bridge) end-to-end AND catches the F1 entry-path omission. **Critical (Rev 3)**: rows must EXIST (not just fall back to state.ts non-null); the original Rev 2 assertion was too weak. |
| `tests/unit/services/test_message_tap_slot.py` (new) | Mock repo; verify `_extract_ids` filters `type=='remove'`; tap fires with non-remove IDs from the persisted list; failure path non-fatal |
| `tests/integration/test_message_metadata_revive_stability.py` (**new, Rev 2 — timestamp stability**) | Pause an instance, advance state, revive (`COMPLETED → RUNNING` path), fetch messages — assert timestamps are non-null and stable across the round-trip; verifies ON CONFLICT DO NOTHING re-tap semantics preserve first-appearance |
| `tests/integration/test_message_metadata_paused_question_flow.py` (**new, Rev 2 — Critical 8**) | Send a user message → agent invokes `question_pause_node` → user answers → agent resumes → fetch messages — assert every persisted message has non-null `created_at`, including the resume-turn AIMessage |
| `tests/integration/test_message_metadata_hook_placement.py` (new) | AST static scan verifies `message_tap_slot.tap_node_return(...)` is called at the **4 approved sites** (Rev 3): entry path (`_build_graph_input` in `instance_messaging.py:237-244`), agent_node single-return (post-F2 refactor of `graph.py:3386-3397`), reactive compaction (`graph.py:3248-3250`), messaging compaction (`instance_messaging.py:810-822`). Verifies NO `message_tap_slot` reference in any `tools_node`/`ToolNode` wrapping. |

**Migration content (`20260825_000001_create_message_metadata.sql`)** — follows the dual-driver comment header pattern from `20260819_000001_report_injections_deferred_marker.sql`:

```sql
-- Migration: Phase 1 C2 — message_metadata side table (Solution M)
-- Created: 2026-08-25
-- Author: planner[v2]
-- Description:
--   Side table for message timestamps + future sequence, fired by the
--   MessageTapSlot hook (Solution M; companion to PERF-1). Schema is the
--   minimal viable column set; the `seq` column is reserved nullable for
--   Phase 2 PERF-2 cursor pagination — adding it now avoids a future
--   ALTER on a populated table (option value, see decisions.md D5).
--
-- DUAL-DRIVER NOTES:
--   Applied by MigrationRunner ONLY when the engine dialect is SQLite.
--   Fresh PG databases receive the table from SQLModel.create_all()
--   (MessageMetadata SQLAlchemy model). Existing PG databases receive
--   CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS from
--   daemon/manager.py::_ensure_postgres_columns(). Index name MUST match.
--
-- CONSTRAINT: PRIMARY KEY (thread_id, message_id). Write path uses
-- INSERT ... ON CONFLICT DO NOTHING (PG) / INSERT OR IGNORE (SQLite) for
-- idempotency — see MessageMetadataRepository.upsert_batch.
--
-- REV 2 NOTE: hook reads NODE-RETURN persisted list, not post-LLM state;
-- see hook placement (decisions.md D1). Re-taps under ON CONFLICT
-- DO NOTHING are EXPECTED on revive + compaction, not anomalies.

-- UP
CREATE TABLE IF NOT EXISTS message_metadata (
    thread_id   TEXT    NOT NULL,
    message_id  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    seq         INTEGER,
    PRIMARY KEY (thread_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_message_metadata_thread
    ON message_metadata (thread_id);

-- DOWN
DROP INDEX IF EXISTS ix_message_metadata_thread;
DROP TABLE IF EXISTS message_metadata;
```

**Hook placement (Rev 3 — F1 entry path + F2 single-return; 4 approved sites)**:

- **Entry path (PRIMARY CALL SITE for user messages — Rev 3 / F1):** at `_build_graph_input` (`daemon/services/instance_messaging.py:237-244` per reviewer file:line), fire `await message_tap_slot.tap_node_return(graph_input_messages, thread_id)` immediately after the per-turn context + user-message construction. This is the place where the user's turn-start `HumanMessage` merges into `graph_input`. **Coverage** (Rev 4 B1 corrected): the entry-path tap covers the `astream` invocation path which DOES go through `_build_graph_input`. The direct `ainvoke` invocation at `daemon/services/instance_messaging.py:1055` constructs `{"messages": [message]}` INLINE and bypasses `_build_graph_input`; it is recorded as accepted-degradation OOS in D19 + Out-of-Scope (mirror watchover handling per LD-D2). Note: `daemon/graph.py:3385-3394` is the agent_node return block (the F2 refactor target) — it is NOT the `astream` invocation site. `daemon/graph.py:1055` is the LoopDetector scan — it is NOT the `ainvoke` invocation site. Without the entry-path tap, **`astream`-invoked user messages** degrade to the `state.ts` fallback (Critical 1 / F1) — never tapped, never gets a `message_metadata` row.

- **Agent_node single-return (post-F2 mechanical refactor of `daemon/graph.py:3386-3397`)**: the existing Rev-1/Rev-2 dual-return at `graph.py:3396-3397` is two `return` statements inside an if/else. The F2 binding fix is: **hoist both branches to a single `outgoing` variable + one `return`** — purely mechanical, no logic change. Target shape (pseudo-diff):

  ```python
  # graph.py:3386-3397 (CURRENT — pre-F2)
  if (injected_msgs or injected_report_msgs or pairing_synthesized_msgs):
      persisted: list[BaseMessage] = []
      persisted.extend(pairing_synthesized_msgs)
      persisted.extend(injected_msgs)
      persisted.extend(injected_report_msgs)
      persisted.append(response)
      return {**watchover_state_reset, 'messages': persisted}    # 3396
  return {**watchover_state_reset, 'messages': [response]}        # 3397

  # graph.py:3386-3397 (POST-F2 — single return + single tap)
  outgoing: list[BaseMessage] = [response]
  if (injected_msgs or injected_report_msgs or pairing_synthesized_msgs):
      outgoing = (
          list(pairing_synthesized_msgs)
          + list(injected_msgs)
          + list(injected_report_msgs)
          + outgoing  # response stays last
      )
  await message_tap_slot.tap_node_return(outgoing, thread_id)
  return {**watchover_state_reset, 'messages': outgoing}
  ```

  Captures: the LLM `response`, `ReportInjection` messages, tool-pairing placeholders, user-injection messages — across both branches. **NO tools_node tap** (Critical 4). The refactor is preferred (binding per F2); fall back to tapping at BOTH returns ONLY IF the refactor risks behavior drift.

- **Reactive-compaction `aupdate_state` (`graph.py:3248-3250`)**: after `aupdate_state(thread_config, {'messages': result.replacement_messages}, as_node='agent')` resolves, fire `tap_node_return(result.replacement_messages, thread_id)`. Idempotent re-tap under `ON CONFLICT DO NOTHING`.

- **Compaction-`aupdate_state` at `instance_messaging.py:810-822`**: same idempotent re-tap against `result.replacement_messages`.

- **Watchover pathway (OOS)**: messages synthesized under `set_deferred_watchover_terminate` are NOT tapped. Documented in `daemon/services/message_tap.py` docstring; LD-D2 acceptance.

- **Tool-message coverage (Rev 3 corrected mechanism)**: tool messages are NEVER tapped (no tools_node hook; Critical 4). They never receive a `message_metadata` row. This is invisible to users because `serialize_message` at `daemon/persistence.py:359-361` skips `type=='tool'` messages from the response output. Phase 2 PERF-3 may add either (a) a tools_node tap or (b) id-diff inference to capture them, IF a future need arises (deferred — OOS for Phase 1; F3 reword).

- **LoopRepair / `question_pause_node` / nudge / language_check**: tap never fires at these nodes.

- **`RemoveMessage` markers**: filtered inside `_extract_ids` (`type == "remove" → continue`) — never inserted.

**Pseudo-code (`MessageTapSlot.tap_node_return`, Rev 2 — SYNC repo + to_thread bridge)**:

```python
# daemon/services/message_tap.py
import asyncio
import datetime as dt
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MessageTapSlot:
    """Non-load-bearing post-node hook for message_metadata upserts.

    Source identifies the call site for observability:
      - "user_message_entry"           (instance_messaging.py:237-244 entry path)
      - "agent_node_return"           (graph.py:3386-3397, post-F2 single return)
      - "compaction_aupdate_reactive" (graph.py:3248-3250)
      - "compaction_aupdate_messaging" (instance_messaging.py:810-822)

    OOS / explicitly NOT tapped:
      - Watchover synthesis cascade (LD-D2; persistence.py:359-361 serializer skip handles display)
      - tools_node (no custom ToolNode wrapper; Critical 4; serializer skip handles display)
      - question_pause_node (no tap there)
      - nudge / language_check (id-less; never tapped; fall to state.ts if surfaced — no lag mechanism)
      - Direct ainvoke invocation at instance_messaging.py:1055 (B1; inline {"messages": [message]} bypasses _build_graph_input; zero production callers; id-less input; state.ts fallback applies; mirrors the watchover handling)
      - RemoveMessage markers (filtered inside _extract_ids)

    Rev 2 idempotency: every tap is an "INSERT ... ON CONFLICT DO NOTHING"
    against the PK (thread_id, message_id). Re-taps on revive and
    compaction collapse to a no-op at the constraint level. The first
    appearance wins; subsequent taps preserve first-appearance semantics.
    """

    def __init__(self, repo: "MessageMetadataRepository", source: str) -> None:
        self._repo = repo
        self._source = source

    @staticmethod
    def _extract_ids(persisted: list[Any]) -> list[str]:
        """Dedupe by message.id; skip RemoveMessage markers (type=='remove')."""
        seen: set[str] = set()
        ids: list[str] = []
        for m in persisted:
            if getattr(m, "type", None) == "remove":
                continue  # RemoveMessage marker — not a new persisted message
            mid = getattr(m, "id", None)
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        return ids

    async def tap_node_return(self, persisted_list: list[Any], thread_id: str) -> int:
        try:
            ids = self._extract_ids(persisted_list)
            if not ids:
                return 0
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            # Sync repo bridge (Critical 2): the repo's factory is
            # synchronous (daemon/repositories/factory.py:10 sqlalchemy.Engine).
            count = await asyncio.to_thread(
                self._repo.upsert_batch,
                thread_id,
                [(mid, now_iso, None) for mid in ids],
            )
            log_message_tap(thread_id, count, self._source)
            return count
        except Exception as exc:
            logger.warning(
                f"[MessageTap] non-fatal: source={self._source} "
                f"thread={thread_id[:8]} error={type(exc).__name__}: {exc}"
            )
            return 0
```

**Pseudo-code (`MessageMetadataRepository`, Rev 2 — SYNC only)**:

```python
# daemon/repositories/message_metadata/repository.py
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from daemon.repositories.message_metadata.models import MessageMetadata


class MessageMetadataRepository:
    def __init__(self, engine: "sqlalchemy.Engine") -> None:
        # engine is a sync sqlalchemy.Engine from daemon/repositories/factory.py
        self._engine = engine

    def upsert_batch(
        self,
        thread_id: str,
        items: list[tuple[str, str, int | None]],
    ) -> int:
        """SYNC. Idempotent batch upsert. Returns rows affected."""
        if not items:
            return 0
        rows = [
            {"thread_id": thread_id, "message_id": mid,
             "created_at": ts, "seq": seq}
            for (mid, ts, seq) in items
        ]
        with self._engine.begin() as conn:  # SYNC transaction
            dialect = conn.dialect.name  # "postgresql" | "sqlite"
            tbl = MessageMetadata.__table__
            if dialect == "postgresql":
                stmt = pg_insert(tbl).values(rows)
            else:
                stmt = sqlite_insert(tbl).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["thread_id", "message_id"]
            )
            result = conn.execute(stmt)
            return result.rowcount or 0

    def get_for_thread(
        self, thread_id: str
    ) -> dict[str, tuple[str, int | None]]:
        """SYNC. Per-thread batch lookup. Missing thread → empty dict."""
        with self._engine.connect() as conn:
            stmt = select(
                MessageMetadata.message_id,
                MessageMetadata.created_at,
                MessageMetadata.seq,
            ).where(MessageMetadata.thread_id == thread_id)
            rows = conn.execute(stmt).fetchall()
            return {r[0]: (r[1], r[2]) for r in rows}
```

**Tests**

| Test | Assertion |
|------|-----------|
| `test_upsert_batch_first_insert` (sync) | 3 rows inserted; result rowcount = 3 |
| `test_upsert_batch_idempotent` (sync) | Same `(thread_id, message_id)` twice → only 1 row; second rowcount = 0 |
| `test_upsert_batch_empty` (sync) | No execute; returns 0 |
| `test_get_for_thread_returns_dict` (sync) | Returns `{message_id: (created_at, seq)}`; missing thread → empty |
| `test_get_for_thread_sqlite_and_pg_equivalent` | Same fixture on both backends returns identical shape |
| `test_message_tap_extracts_unique_ids` | 5 messages with 2 duplicate IDs → upsert called with 3 unique |
| `test_message_tap_filters_remove_message_markers` (**Rev 2 fold-in**) | Persisted list contains a `RemoveMessage(type='remove', id='x')` → upsert NOT called for `x` |
| `test_message_tap_failure_is_non_fatal` | Mock repo raises → tap logs warning, returns 0, does NOT raise |
| **`test_message_metadata_liveness_round_trip`** (**Rev 2 BLOCKING, Rev 3 strengthened for F1**) | Spin up instance, send one plain turn, await both taps, assert ROWS LAND for both user `HumanMessage.id` AND `AIMessage.id` with non-null `created_at` for each. The original Rev 2 assertion (AI row only, non-null) is TOO WEAK — falls back to `state.ts` non-null trivially. Rev 3 asserts row existence for user + non-null for both. |
| **`test_message_metadata_first_appearance_ordering`** (**new, Rev 3 — F1 strongest**) | Run a plain turn; capture user-message `created_at` (via entry-path tap) and AIMessage `created_at` (via agent_node tap); assert `user.created_at < ai.created_at` (with a tolerance for sub-millisecond ordering). Proves both taps fire AND first-appearance semantics are correct. The strongest catch for F1: if entry-path tap is missing, only the AI row exists → assertion fails. |
| **`test_message_metadata_revive_stability`** (**Rev 2**) | Pause + advance + revive → timestamps non-null + first-appearance preserved |
| **`test_message_metadata_paused_question_flow`** (**Rev 2 BLOCKING, Critical 8**) | question_pause → user answer → resume → fetch → timestamps non-null for every persisted message |
| `test_hook_placement_ast_scan` | Parser confirms tap at the **4 sites** (entry, agent_node single-return, 2 compaction sites); verifies NO `message_tap_slot` reference inside any `ToolNode`/`tools_node` block |
| `test_invariant_zero_holds_pre_c1` | With C4 merged, `[/Messages]` log line carries `alist_count=N` (observed, not 0) |

**Risks + mitigations**

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Tap misses a message-creation path → silent timestamp gaps | Medium | Medium | AST static scan in CI confirms the **4 sites (entry, agent_node single-return, 2 compaction sites)**; `RemoveMessage` filter; a startup-reconcile hook if any path is missed |
| Sync repo + to_thread blocks the event loop on slow DB | Medium | Low | To_thread puts it on the default executor; IF profiling shows DB call >5 ms, wrap in `concurrent.futures.ThreadPoolExecutor(max_workers=N)` per-tap (deferred to Phase 3 + measurement) |
| Migration applied twice (idempotency) | Low | Low | `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`; MigrationRunner's per-statement duplicate-column handler |
| `RemoveMessage` marker inserted as a row | Low | Low | `_extract_ids` filters; AST test verifies filter is present |
| Tool-message timestamp invisibility (accepted; display never carries tool messages) | Low | Medium | Documented in `daemon/services/message_tap.py`; tool messages are NEVER tapped (no tools_node hook; Critical 4); display invisibility comes from `serialize_message` skipping `type=='tool'` at `daemon/persistence.py:359-361` (B3 reword — Rev 4: no phantom "next agent_node infers" mechanism); Phase 2 PERF-3 may add a tools_node tap if a need arises |
| Hook failure breaks the graph run | High (regression) | Low | `try/except` wrap the entire `tap_node_return`; never raises |
| IS_RETRY replay re-fires the tap with identical timestamps (create_at drifts) | Low | Medium | `isoformat(now)` is computed once per tap → re-tap can rewrite a newer `created_at`. Mitigate by: read existing `created_at` first via `get_for_thread` and re-use if present; alternative: pin `created_at` from the FIRST tap (currently NEW ISO every tap) — see Critical 8 stability test; D-s1 maps this |

**Rollback procedure**

1. Run the `-- DOWN` section of `20260825_000001_create_message_metadata.sql` via `MigrationRunner.rollback_migration(version="20260825_000001")`.
2. Revert `daemon/graph.py` + `daemon/services/instance_messaging.py` to remove the **4** `tap_node_return(...)` calls (entry path + single agent_node return + 2 compaction sites).
3. Delete `daemon/services/message_tap.py` + `daemon/repositories/message_metadata/*.py`.
4. C1 dependency: if C1 already merged, it reads from the (now-empty) repo and falls back to `state.get("ts")` (`persistence.py:368-370`) — no regression.

**Exit criterion**

- `MessageMetadataRepository` is SYNC (matches the factory contract).
- `asyncio.to_thread(self._repo.upsert_batch, …)` is the bridge at the tap site.
- `test_message_metadata_liveness_round_trip` is GREEN and asserts ROWS for both user id AND AI id (Rev 3 strengthened for F1).
- `test_message_metadata_first_appearance_ordering` is GREEN (catches F1 strongly: user.created_at < ai.created_at).
- `test_message_metadata_revive_stability` is GREEN (first-appearance preserved under ON CONFLICT DO NOTHING re-taps).
- `test_message_metadata_paused_question_flow` is GREEN (no silent timestamp loss on resume).
- Hook placement AST scan passes for the **4 sites** (Rev 3) AND verifies NO `message_tap_slot` reference in any `ToolNode`/`tools_node` block.
- `get_instance_messages` still works (with `state.ts` fallback until C1).

---

### C1 — Kill `alist()` in `get_instance_messages` (PERF-1)

**Files touched**

| File | Change |
|------|--------|
| `daemon/persistence.py` | Inside `get_instance_messages` (lines 254-540 approx): **delete** the `alist(config, limit=1000)` walk at lines 322-346; replace with `await asyncio.to_thread(msgs_repo.get_for_thread, instance_id)` (sync repo bridge, mirrors C2); populate `msg_timestamps = {mid: ts for mid, (ts, _seq) in metadata.items()}`; existing `state.get("ts")` fallback at line 368-370 stays. The C4 `time_saver_op` wrap on `aget` stays; the C4 wrap on `alist` is removed (no call to wrap). |
| `daemon/persistence.py` | Signature: `get_instance_messages(checkpointer, instance_id, manager=None, msgs_repo: MessageMetadataRepository | None = None)` — `None` is the **EXPLICIT-DEGRADATION** path (used when C1 ships without C2): with C1's alist-kill in place, `msgs_repo=None` means the alist walk is GONE and ALL message timestamps degrade to the `state.ts` fallback at `persistence.py:368-370`; the response shape stays correct, but every message shows the latest-checkpoint timestamp (the same `state.ts` degradation that applies to old threads without backfill — this is the operator-shim path that lets C1 ship alone if C2 slips). |
| `daemon/manager.py` | Plumb `_messaging_service.get_messages(...)` (`instance_messaging.py:3896`) → `msgs_repo=manager.message_metadata_repo`; `manager.get_messages` (`manager.py:9314`) threads through. |
| `daemon/checkpoint_perf.py` (from C4) | The `alist` observation log line naturally DISAPPEARS — that IS the invariant (Critical 7): collapse to zero by absence. `log_messages_api` keeps its `alist_count` parameter but the post-C1 call site never passes a non-zero value because the alist call is gone. Optionally keep an explicit `alist_count=0` argument to make the gate grep-friendly. |
| `tests/unit/persistence/test_get_instance_messages_no_alist.py` (new) | Mock saver; record every method call; assert `saver.alist` never invoked across 4 fixture variants (10/100/1000/10000 messages); the C4 observed-count assertion (`alist_count=0`) holds by absence. |
| `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` (new, **Rev 2**) | Loads `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` (captured by PR1); runs 4 conversations through the post-C1 `get_instance_messages`; asserts byte-identical response shape (key list + order + nested `tool_calls` structure) for every variant. The fixture file is captured in PR1 (Critical 5) and is the binding contract. |

**Change spec (pseudo-diff for `get_instance_messages`)**:

```diff
@@ daemon/persistence.py:254-346
-    # Get the current state from async checkpointer
-    state = await saver.aget(config)
+    # Get the current state from async checkpointer — TIMED + GUARDED (C4)
+    state = await time_saver_op("aget", instance_id, saver.aget(config))
     if state is None:
         return []

     # LangGraph stores messages in channel_values
     channel_values = state.get("channel_values", {})
     messages = channel_values.get("messages", [])
     if not messages:
         return []

-    # Collect all checkpoints with timestamps
-    # We need to iterate oldest-to-newest to track when messages first appeared
-    checkpoints_data: list[tuple[str | None, list[Any]]] = []
-
-    async for checkpoint_tuple in saver.alist(config, limit=1000):
-        ct = cast(CheckpointTuple, checkpoint_tuple)
-        checkpoint = ct.checkpoint
-        if not isinstance(checkpoint, dict):
-            continue
-        ts = checkpoint.get("ts")
-        checkpoint_messages = checkpoint.get("channel_values", {}).get("messages", [])
-        checkpoints_data.append((ts, checkpoint_messages))
-
-    # Reverse to get oldest-to-newest order
-    checkpoints_data.reverse()
-
-    # Track when each message first appeared
-    msg_timestamps: dict[str, str] = {}
-    for ts, checkpoint_messages in checkpoints_data:
-        if not ts:
-            continue
-        for msg in checkpoint_messages:
-            msg_id = getattr(msg, 'id', None)
-            if msg_id and msg_id not in msg_timestamps:
-                msg_timestamps[msg_id] = ts
+    # Phase 1 C1 (Rev 2): read timestamps from message_metadata side
+    # table (C2) instead of enumerating checkpoint history. Zero
+    # `alist()` calls on this path. Sync repo bridged through to_thread
+    # (Critical 2). Pre-Phase-1 alist walk + reconstruction is deleted.
+    metadata: dict[str, tuple[str, int | None]] = (
+        await asyncio.to_thread(msgs_repo.get_for_thread, instance_id)
+        if msgs_repo is not None else {}
+    )
+    msg_timestamps: dict[str, str] = {
+        mid: ts for mid, (ts, _seq) in metadata.items()
+    }
```

**Response-shape freeze (Rev 2 — fixture-driven)**:

The acceptance is no longer "freeze the dict-key list by hand" — it's "matches the **pre-captured fixture** byte-for-byte". The fixture (`tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json`) was captured in PR1 from a real in-process conversation run on the pre-C1 code path (Critical 5). The test loads it and runs post-C1 `get_instance_messages` against the same conversation shape; response must match.

**Tests**

| Test | Assertion |
|------|-----------|
| `test_zero_alist_calls_with_msgs_repo` | 4 fixture variants (10/100/1000/10000 + filled repo) — mock records every method call on `saver`; `alist` count = 0 |
| `test_zero_alist_calls_without_msgs_repo` | When `msgs_repo=None`, the C1 alist-kill path still applies (no alist calls); the function returns same shape with `state.ts` fallback for every message (EXPLICIT-DEGRADATION — operator shim for shipping C1 alone if C2 slips; mirrors the old-threads-without-backfill handling) |
| **`test_response_shape_byte_identical_to_captured_fixture`** (**Rev 2 BLOCKING**) | The 4 captured fixtures match post-C1 output key-by-key, order-included, recursively over `tool_calls`; the fixture file came from PR1's real-run capture |
| `test_response_shape_no_repo` | With `metadata_repo=None`, function returns same shape using `state.ts` fallback — fallback preserved |
| `test_invariance_alist_count_collapse_to_zero_by_absence` | After C1, the `[/Messages]` log line carries `alist_count=0` because the alist call + its observed-count wrapper are gone (Critical 7) |
| `test_synthesize_system_prompt_unchanged` | Synthetic system message path still works (manager-based reconstruction) — manager branch untouched |
| `test_invariant_check_no_alist_fires_on_regression` | Mock `time_saver_op("alist", ...)` → ERROR line |

**Risks + mitigations**

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Schema drift in `serialize_message` (developer adds a field) | High (frontend breaks) | Low | Frozen-shape regression test (fixture-driven) |
| `message_metadata` table empty (old threads without backfill) | Low | High until PERF-4 | `state.get("ts")` fallback at `persistence.py:368-370` already present |
| `msgs_repo` injection point breaks existing third-party callers | Low | Low | Accept `msgs_repo: MessageMetadataRepository \| None = None`; `None` ⇒ EXPLICIT-DEGRADATION path (alist walk is gone; all timestamps fall to `state.ts`; lets operator ship C1 alone if C2 slips) |
| To_thread on the request path adds tail latency | Low | Low | DB query is one indexed lookup; measured in C4 |
| Test-fixture drift if `serialize_message` evolves | Medium | Medium | CI gate: every PR that touches `daemon/utils.py`/`serialize_message` must regenerate the fixture; doc that contract on `tests/unit/persistence/README_FROZEN_FIXTURE.md` |

**Rollback procedure**

1. Revert `daemon/persistence.py` body to the pre-Phase-1 alist walk (with C4 instrumentation kept if PR1 still merged).
2. Revert `daemon/manager.py` injection + `instance_messaging.py:3896` plumbing.
3. The frozen fixture file stays — it's the regression baseline for both pre-C1 and post-C1 paths.

**Exit criterion**

Spy test proves zero alist calls; **fixture-based response-shape test** green against the PR1-captured fixture; C4 `[/Messages]` logs show the post-flip `alist_count` collapse (the line disappears from stdout/log stream → gate by absence; production grep shows the metric gone for ≥7 days).

---

### C3 — Reference-Aware `checkpoint_blobs` Prune via Direct Anti-Join (LD-D1 — Rev 2)

**Files touched**

| File | Change |
|------|--------|
| `daemon/services/checkpoint_prune.py` (new) | Single module owning the prune algorithm + the fail-safe. Includes the PG-SQL direct anti-join (parameterized per `(thread_id, checkpoint_ns)`) and the SQLite equivalent. |
| `daemon/checkpoint_adapter.py` | Add to both `SqliteCheckpointerAdapter` + `PostgresCheckpointerAdapter`: (a) `async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]` (Rev 3 NEW — returns ALL `(thread_id, checkpoint_ns, count)` pairs without the HAVING filter, used by C3 candidate enumeration); (b) `async def get_remaining_checkpoint_ids(self, thread_id, checkpoint_ns) -> list[str]` (existing `get_checkpoint_ids` is sufficient); (c) `async def count_refs_for_blob_thread(self, thread_id, checkpoint_ns) -> int` — for the **fail-safe pre-check** (returns the count of `(channel, version)` pairs present in `checkpoint->'channel_versions'` across remaining checkpoint rows; if 0 → SKIP + ERROR); (d) `async def delete_blobs_anti_join(self, thread_id, checkpoint_ns, dry_run: bool) -> tuple[int, int]` — runs the anti-join DELETE, returns `(deleted_count, bytes_freed)`; PG variant uses `c.checkpoint->'channel_versions'->>b.channel = b.version` against the JSONB column. SQLite variant reads `checkpoints.channel_values` via msgpack — research found that SQLite's `checkpoints.channel_values` IS where SQLite stores the equivalent of `channel_versions` for the sqlite-saver variant. **PG paths are the Rev 2 focus**; SQLite path is documented as "no-op on SQLite checkpoint_blobs table which doesn't exist for SQLite per research-findings §3 — handled by PostgresCheckpointerAdapter only". |
| `daemon/services/maintenance.py` | Add new method `_prune_unreferenced_blobs()`; call from `_run_loop` AFTER `_prune_per_thread_checkpoints` (same idle gate, same 15-min cadence). **Iterate candidate threads via the new `checkpoint_adapter.find_all_thread_ns_pairs()` method (Rev 3 correction)** — the existing `find_excess_checkpoint_groups` returns only groups where `HAVING COUNT(*) > max_per_thread` (per `daemon/checkpoint_adapter.py:220`), so passing `max_per_thread=1` would EXCLUDE threads with exactly 1 checkpoint. The new method returns ALL `(thread_id, checkpoint_ns)` pairs with their row counts — no HAVING filter. |
| `daemon/constants.py` | Add `CHECKPOINT_BLOB_PRUNE_DRY_RUN: bool = True` (default true; set false only via env); `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE: bool = False` (env-overridden; default off); `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD: int = 100_000` (safety cap) |
| `daemon/checkpoint_perf.py` (from C4) | Add `log_blob_prune(thread, dry_run, deleted, refs_seen, skipped_reason=None, observed_blob_count=0)` |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` (new) | **SQL fixture tests** (mirror the §9 mapping below): referenced blobs survive, unreferenced blobs die, mixed thread boundaries hold, fail-safe zero-refs skips with ERROR log, concurrent-aput safety |
| `tests/integration/checkpoint_prune_real_saver.py` (**new, Rev 2 BLOCKING — Critical 6**) | **Real-saver integration test**: use a real `AsyncPostgresSaver` (or `AsyncSqliteSaver` for local CI) against a real langgraph-checkpoint-postgres / langgraph-checkpoint-sqlite fixture; run a real instance through 3 turns to generate checkpoint + blob rows; execute the C3 prune; `aget`/`aget_state` and verify messages + tool outputs + `_DeltaSnapshot` chains reconstruct correctly; kill-safe test: process "killed" between nodes → next turn reconstructs; fail-safe test: zero-refs scenario produces ZERO deletes + ERROR log; concurrent-aput test: while prune runs, write a new checkpoint + new blob → new blob preserved. **This test BLOCKS PR4 merge AND `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` enable**. |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` (**new, Rev 2 fold-in**) | Automated restore-rehearsal roundtrip: backup `checkpoint_blobs` to a temp table; run destructive prune; restore from backup; byte-equality assertion on the row counts and key triples |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` (new) | Mock adapter; verify the fail-safe logic; verify iteration goes through `find_all_thread_ns_pairs()` (Rev 3 correction — NOT `find_excess_checkpoint_groups(max_per_thread=1)` which would skip single-checkpoint threads) |

**Anti-join SQL (PG — the canonical Rev 2 shape against the actual `checkpoint->'channel_versions'`)**:

```sql
-- Pre-check (fail-safe): are there ANY blob refs in remaining checkpoints?
-- SELECT 1 FROM checkpoints c
-- WHERE c.thread_id = $1
--   AND c.checkpoint_ns = $2
--   AND jsonb_typeof(c.checkpoint->'channel_versions') = 'object'
--   AND c.checkpoint->'channel_versions' != '{}'::jsonb
-- LIMIT 1;
-- If zero → SKIP this thread + log ERROR + continue.

-- Direct anti-join DELETE:
DELETE FROM checkpoint_blobs b
WHERE b.thread_id = $1
  AND b.checkpoint_ns = $2
  AND NOT EXISTS (
    SELECT 1
    FROM checkpoints c
    WHERE c.thread_id = b.thread_id
      AND c.checkpoint_ns = b.checkpoint_ns
      AND (c.checkpoint->'channel_versions'->>b.channel) = b.version
  );
```

The `(c.checkpoint->'channel_versions'->>b.channel) = b.version` selector: if the key `b.channel` does NOT exist in `c.checkpoint->'channel_versions'`, the `->>` returns NULL → `NULL = b.version` is NULL → row IS a delete candidate. That's intentional: a blob whose channel isn't referenced by ANY remaining checkpoint is unreferenced.

The `b.checkpoint_ns = c.checkpoint_ns` predicate handles non-empty `checkpoint_ns` (subgraph checkpoints); both sides are namespaced.

**Pseudo-code (`MaintenanceService._prune_unreferenced_blobs`, Rev 2)**:

```python
# daemon/services/maintenance.py
import os
import time

_DESTRUCTIVE = (
    os.environ.get("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "0") == "1"
    and os.environ.get("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "1") == "0"
)


async def _prune_unreferenced_blobs(self) -> None:
    """Phase 1 C3 (Rev 2) — direct anti-join checkpoint_blobs prune.

    Dry-run by default. Destructive only when BOTH env flags are set:
        CHECKPOINT_BLOB_PRUNE_DRY_RUN=0
        CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1

    Fail-safe: per-thread pre-check counts blob refs in
    ``checkpoint->'channel_versions'`` of remaining checkpoint rows; if
    zero → SKIP that thread + ERROR log (Critical 1).
    """
    try:
        destructive = _DESTRUCTIVE
        # Reuse the retention scan result (fold-in: avoid per-cycle scan)
        candidate_threads = await self._checkpointer.find_all_thread_ns_pairs()
        if not candidate_threads:
            logger.debug("No threads with checkpoints found for blob prune scan")
            return

        total_deleted = 0
        for thread_id, checkpoint_ns, _ in candidate_threads:
            try:
                t0 = time.perf_counter()
                # Pre-check: are there any blob refs at all?
                refs_seen = await self._checkpointer.count_refs_for_blob_thread(
                    thread_id, checkpoint_ns
                )
                if refs_seen == 0:
                    log_blob_prune(
                        thread_id, dry_run=not destructive,
                        deleted=0, refs_seen=0,
                        skipped_reason="ZERO_REFS_FAIL_SAFE",
                    )
                    continue  # Critical 1 — never delete on zero refs
                if refs_seen > CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD:
                    log_blob_prune(
                        thread_id, dry_run=not destructive,
                        deleted=0, refs_seen=refs_seen,
                        skipped_reason="MAX_REFS_EXCEEDED",
                    )
                    continue
                cnt, bytes_freed = await self._checkpointer.delete_blobs_anti_join(
                    thread_id, checkpoint_ns, dry_run=not destructive,
                )
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_blob_prune(
                    thread_id, dry_run=not destructive,
                    deleted=cnt, refs_seen=refs_seen,
                )
                total_deleted += cnt if destructive else 0
            except Exception as inner_exc:
                logger.warning(
                    f"[CheckpointPerf] blob_prune thread={thread_id[:8]} "
                    f"skipped, error={type(inner_exc).__name__}: {inner_exc}"
                )
                continue  # per-thread failure never breaks the cycle

        logger.info(
            f"[CheckpointPerf] blob_prune scanned={len(candidate_threads)} "
            f"threads destructive={destructive} total_deleted={total_deleted}"
        )
    except Exception as e:
        logger.error(f"Reference-aware blob prune failed: {e}")
```

**Pre-Enable Checklist (LD-OQ1 — mandatory before any destructive flip)**:

```
PRE-ENABLE CHECKLIST (execute in order, all green to flip destructive):

  [✓] 1. Read `langgraph-checkpoint-postgres` 3.1.0 schema in installed env.
        Confirm `checkpoint_blobs` columns are exactly
        (thread_id, checkpoint_ns, channel, version, type, blob).
        Source: `python -c "import langgraph.checkpoint.postgres.aio as m; print(m.__file__)"`
        then inspect `langgraph/checkpoint/postgres/base.py::CREATE_TABLES`.

  [✓] 2. Query PROD `checkpoints` table for one representative thread:
          SELECT jsonb_pretty(checkpoint->'channel_versions')
            FROM checkpoints
           WHERE thread_id = '<representative>'
           ORDER BY checkpoint_id DESC LIMIT 5;
        Confirm the top-level `channel_versions` is a JSONB object
        mapping channel name to a version string. If the shape differs
        (e.g., it's nested under another key, OR it uses `versions_seen`
        instead), DO NOT enable destructive — re-spec the anti-join
        against the actual key. (LD-OQ1 — do not assume.)

  [✓] 3. Run the dev/staging dry-run for ≥1 full retention cycle
        (~7 days at the default 15-minute cadence). Inspect logs for
        the `[CheckpointPerf] blob_prune ...` lines; reject the enable
        if `deleted=0` AND `refs_seen > 0` candidates appear (which
        would indicate the anti-join is over-deleting or never-deleting).

  [✓] 4. Run `tests/integration/checkpoint_prune_real_saver.py` GREEN.

  [✓] 5. Run `tests/integration/checkpoint_prune_restore_rehearsal.py`
        GREEN. Confirm the restore procedure: the backup table name
        location is `checkpoint_blobs_<YYYYMMDD>_backup` (operator
        chooses); the count-vs-baseline check is
        `SELECT COUNT(*) FROM checkpoint_blobs` after restore must
        equal the pre-prune count.

  [✓] 6. Snapshot prod `checkpoint_blobs` to
        `CREATE TABLE checkpoint_blobs_prune1_backup AS
           SELECT * FROM checkpoint_blobs;` Hold for ≥7 days.

  [✓] 7. Set env: `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0`,
        `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`. Restart daemon.

ROLLBACK if post-prune breakage appears:
  1. Unset both env flags; restart daemon. Prune reverts to no-op.
  2. INSERT INTO checkpoint_blobs SELECT * FROM checkpoint_blobs_prune1_backup
     ON CONFLICT DO NOTHING;
  3. Verify: SELECT COUNT(*) FROM checkpoint_blobs equals pre-prune count.
  4. Verify: `aget` an active instance → messages intact.
  5. Drop the backup table once restored.
```

**§9 Required Tests Checklist (Rev 2 — anti-join tests)**:

| §9 Item | Test file |
|---------|-----------|
| latest thread state still loads | `tests/integration/test_resume_*.py` (existing) + replay after destructive prune in `tests/integration/checkpoint_prune_real_saver.py` |
| interrupt resume still works | `tests/integration/test_interrupt_resume.py` + replay in real-saver test |
| pending writes still work | `tests/integration/test_pending_writes.py` (existing) + manual replay |
| subgraphs still work | existing tool_node tests + manual replay; `_DeltaSnapshot` chain coverage in real-saver test |
| fork / time-travel semantics are understood | documented; project does not exercise these (§9 acknowledgment) |
| **referenced blobs are not deleted** | `tests/unit/checkpoint_adapter/test_direct_anti_join.py::test_referenced_blobs_survive_*` + `tests/integration/checkpoint_prune_real_saver.py::test_real_saver_referenced_survives` |
| **deletion is safe under concurrent checkpoint writes** | `tests/unit/checkpoint_adapter/test_direct_anti_join.py::test_concurrent_aput_safety` + `tests/integration/checkpoint_prune_real_saver.py::test_real_saver_concurrent_aput` |
| **rollback procedure exists** | `docs/runbooks/checkpoint-blob-prune-restore.md` (Rev 2: backup destination + count-vs-baseline explicitly enumerated); automated roundtrip in `tests/integration/checkpoint_prune_restore_rehearsal.py` |
| **zero-refs fail-safe (Rev 2 add)** | `tests/unit/checkpoint_adapter/test_direct_anti_join.py::test_zero_refs_fail_safe_skips_and_logs_error` + `tests/integration/checkpoint_prune_real_saver.py::test_real_saver_zero_refs_skip` |

**Automated restore-rehearsal roundtrip test (fold-in)**:

```
def test_restore_rehearsal_roundtrip():
    conn = setup_real_saver_conn()  # real langgraph saver
    # 1. Drive 3 turns to populate checkpoint_blobs
    run_turns(conn, n=3)
    baseline_count = query_count(conn)
    backup_name = "checkpoint_blobs_rehearsal_backup"
    # 2. Backup to a known destination
    exec_sql(conn, f"CREATE TABLE {backup_name} AS SELECT * FROM checkpoint_blobs;")
    # 3. Destructive prune
    enable_destructive()
    run_prune(conn)
    post_prune_count = query_count(conn)
    assert post_prune_count <= baseline_count  # may have dropped unreferenced
    # 4. Restore from backup
    exec_sql(conn, f"INSERT INTO checkpoint_blobs SELECT * FROM {backup_name} ON CONFLICT DO NOTHING;")
    restored_count = query_count(conn)
    # 5. Byte-equality check
    assert restored_count == baseline_count, "restore must match baseline"
    exec_sql(conn, f"DROP TABLE {backup_name};")
```

**Risks + mitigations**

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| §9 blob-sharing accidentally drops a referenced blob | High (data loss) | Low | Direct anti-join; explicit `checkpoint_ns` predicate; fail-safe zero-refs skip; real-saver test BLOCKS merge |
| LangGraph serde format changes (the `channel_versions` shape diverges) | High (data loss if destructive enabled) | Low | LD-OQ1 prod-layout verification MANDATORY before enable; dry-run defaults to ON |
| Anti-join blows up on large `checkpoints` table | Medium | Low | `find_all_thread_ns_pairs()` (Rev 3 NEW adapter method) returns bounded candidate set; per-thread anti-join scans up to `CHECKPOINT_MAX_PER_THREAD` rows; `MAX_REFS_PER_THREAD` cap as a backstop |
| Long-running prune blocks maintenance loop | Low | Low | Per-thread try/except + continue; outer try/except catches all |
| Restore-rehearsal backup table grows large | Low | Low | Operator chooses naming + retention; backup table DROP'd after confirmed restore |
| SQLite path doesn't have `checkpoint_blobs` (research §3) — what does the adapter do? | Low | Low | `delete_blobs_anti_join` is **PG-only**; SQLite adapter returns `(0, 0)` and logs `dry_run=ON, no sqlite blobs table` once per cycle |

**Rollback procedure**

1. Unset both env flags (`CHECKPOINT_BLOB_PRUNE_DRY_RUN`, `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE`); restart daemon. Prune reverts to no-op.
2. Revert `daemon/services/maintenance.py` (`_prune_unreferenced_blobs` method) + `daemon/services/checkpoint_prune.py` deletions.
3. Revert `daemon/checkpoint_adapter.py` (`delete_blobs_anti_join` + `count_refs_for_blob_thread` additions).
4. If destructive was enabled and breakage observed: follow the restore section of the pre-enable checklist.

**Exit criterion**

- Direct anti-join DELETE written against the actual `checkpoint->'channel_versions'` JSONB shape (PG side; SQLite returns `(0,0)` with a one-time warning).
- Fail-safe: zero-refs → SKIP + ERROR (never zero-refs-means-delete-all).
- `tests/integration/checkpoint_prune_real_saver.py` is GREEN — write→prune→aget/resume reconstruction incl. `_DeltaSnapshot` chains + concurrent-aput + zero-refs fail-safe — BLOCKS PR4 merge AND destructive enable.
- `tests/integration/checkpoint_prune_restore_rehearsal.py` is GREEN (automated backup→prune→restore→byte-equality).
- Pre-enable checklist (LD-OQ1) executed end-to-end; prod-layout query confirms the anti-join shape matches reality.
- Dry-run ≥1 full retention cycle in dev before enabling destructive.

---

### Flag A — Import-Level Hard-Fail Test in `tests/` (no CI infra, LD-OQ2)

**Files touched**

| File | Change |
|------|--------|
| `tests/integration/test_no_saver_imports_in_routers.py` (new) | **Mandatory Phase 1 enforcement per LD-OQ2**: walks `daemon/routers/**/*.py`; asserts NO `from langgraph.checkpoint` import AND NO `import langgraph.checkpoint` AND NO `saver\.alist` call in any file. Fails the test suite if any pattern detected. Runs under the existing standard test gates — no new CI infra. |
| `tools/lint/checkpoint_perf_lint.py` (**OPTIONAL, Rev 2 scope-min**) | Standalone linter (was the original §33 spec). May be kept as a dev convenience (running locally for fast feedback) or dropped. The pytest-based `test_no_saver_imports_in_routers.py` IS the gate; this script is optional. |
| `tools/lint/allowlist.txt` | Per-line suppression; Phase 1 ships empty |

**Forbidden patterns (Phase 1 scope — `daemon/routers/**` only)**:

```text
1. `from langgraph.checkpoint ...`           (any direct import)
2. `import langgraph.checkpoint`            (any direct import)
3. `saver\.alist(`                          (any call)
4. `await saver\.alist(`                    (any call)
```

**Tests**

| Test | Assertion |
|------|-----------|
| `test_no_saver_imports_clean` | Walks `daemon/routers/**`, asserts ZERO forbidden patterns |
| `test_no_saver_imports_fails_on_synthetic_violation` | Creates a fixture router file with `from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver`; the test exits non-zero |
| `test_no_alist_calls_fails_on_synthetic_violation` | Fixture router has `await saver.alist(...)`; test exits non-zero |
| `test_allowlist_suppresses` | Adding a pattern to `allowlist.txt` suppresses the failure |

**Risks + mitigations**

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| False positive blocks legitimate code in routers | Low | Low | Allowlist escape hatch (currently empty) |
| Test silently skipped | High (regression) | Low | Standard test gates run the suite; CI failure on test exit non-zero |

**Rollback procedure**

1. Delete `tests/integration/test_no_saver_imports_in_routers.py`.

**Exit criterion**

`pytest tests/integration/test_no_saver_imports_in_routers.py` passes; any developer who adds a saver import or `saver.alist` call to a router sees a test failure.

---

## Sequencing (PR Breakdown, Rev 3)

Landing order: **C4 → C2 → C1 → C3**, with Flag A landing alongside C1 (it's the same enforcement surface and shares the test infra). PR1 grows by fixture capture; PR2 grows by the to_thread bridge + liveness test **+ entry-path tap (F1) + single-return agent_node refactor (F2) + strengthened first-appearance test**; PR3 unchanged in shape but gains fixture-based + pause-question integration tests; PR4 shrinks (no ref-table machinery) but gains the real-saver integration test mandate + the `find_all_thread_ns_pairs` adapter method (Rev 3 correction).

| PR | Component | Branch | Effort (revised, Rev 3) | Depends on | Independently mergeable? | Exit gates |
|----|-----------|--------|--------------------------|------------|--------------------------|------------|
| **1** | **C4** — instrumentation + frozen-fixture capture + gate-suite enumeration | `feat/c4-checkpoint-perf-instrumentation` | ~3 d | — | YES | `[CheckpointPerf] op=aget` visible; `[/Messages] alist_count=N` observed; **frozen fixture captured to disk**; **gate-suite enumeration test green**; baseline captured |
| **2** | **C2** — `message_metadata` + sync repo + to_thread bridge + entry-path tap (F1) + single-return agent_node refactor (F2) + 4-site hook | `feat/c2-message-metadata` | **~7 d** (Rev 3: Rev 2's ~6 d + ~0.5-1 d for F1 entry-path tap + F2 single-return refactor + strengthened first-appearance test + the `4 sites` AST gate) | PR#1 (uses C4 perf logger) | YES (additive table + hook) | Repo upsert sync + idempotent on both backends; **`liveness` test GREEN for BOTH user id AND AI id (Rev 3 strengthened for F1)**; **`first_appearance_ordering` test GREEN (`user.created_at < ai.created_at`)**; `RemoveMessage` filter test GREEN; **AST placement green for 4 sites (entry, agent_node single-return, 2 compactions); no tap in any ToolNode block**; `state.ts` fallback still works when row missing |
| **3** | **C1** — kill `alist()` in `get_instance_messages` + Flag A enforcement | `feat/c1-read-flip-and-flag-a` | ~4 d | PR#2 (works without it, completes with it) | YES (response shape frozen via PR1 fixture) | Spy test = ZERO alist; **fixture-driven response-shape** GREEN; revival + paused-question tests GREEN (sign-off Critical 8); `[/Messages] alist_count` collapses to absence in prod log |
| **4** | **C3** — direct anti-join blob prune + fail-safe + restore-rehearsal + `find_all_thread_ns_pairs` adapter method (Rev 3 correction) | `feat/c3-blob-prune` | **~5 d** (was 5 d in Rev 2; +~0.5 d for `find_all_thread_ns_pairs` method + tests; covered within existing 5 d) | PR#1 (perf logger), PR#2 (schema pattern NOT needed since C3 has no new table) | YES (separate concern: maintenance; gated by env flags) | **Real-saver integration test GREEN**; **automated restore-rehearsal GREEN**; direct anti-join unit tests GREEN; fail-safe zero-refs test GREEN; pre-enable checklist executed |
| **5** | **Phase 1 gate verification** (no code change; one PR for gate evidence + summary) | `feature/langgraph-checkpoint-perf` | **~1 d** | PRs 1–4 | n/a | All exit gates met; integration suite green; `message_api_checkpoint_list_total` observed count == 0 by absence in prod ≥7 days |

**Critical-path diagram (Rev 3)**:

```
PR1 (C4 + fixture capture)
   │
   ▼
PR2 (C2 + sync repo + to_thread + entry-path tap + agent_node single-return)
   │
   ├──► PR3 (C1 + fixture-driven shape test + Flag A) ──► [Phase 1 gate]
   │
   └──► PR4 (C3 direct anti-join + real-saver + find_all_thread_ns_pairs) ◄──┘
                                                                  │
                              [pre-enable checklist]             │
                                                                  ▼
                              [destructive flip] ─────────────────►
```

**Effort re-estimate summary (Rev 3)**:

| | Rev 1 | Rev 2 | Rev 3 | Δ vs Rev 2 | Why |
|---|-------|-------|-------|------------|-----|
| PR1 (C4) | 2 d | 3 d | 3 d | 0 | Unchanged from Rev 2 |
| PR2 (C2) | 5 d | 6 d | **7 d** | +1 | **F1 entry-path tap (at `_build_graph_input`) + F2 single-return refactor (in agent_node) + strengthened liveness test (rows for BOTH user + AI ids) + new `test_first_appearance_ordering`** |
| PR3 (C1) | 3 d | 4 d | 4 d | 0 | Unchanged from Rev 2 |
| PR4 (C3) | 6 d | 5 d | 5 d | 0 | Rev 3: +0.5 d for `find_all_thread_ns_pairs` adapter method + tests; –0.5 d from minor simplifications (no extra trigger logic). Net unchanged. |
| PR5 (gate) | 1 d | 1 d | 1 d | 0 | Unchanged |
| **Total** | **16 d ≈ 3.2 PW** | **18 d ≈ 3.6 PW** | **20 d ≈ 4.0 PW** | +2 d vs Rev 2 | **F1 + F2 fix-package**: ~+1 d PR2 entry-path tap + single-return refactor + strengthened tests. Aligns with overview totals. |

---

## Risk Summary (Rev 3 — top-level, cross-cutting)

| # | Risk | Impact | Likelihood | Phase | Mitigation |
|---|------|--------|------------|-------|------------|
| R1 | **C3 anti-join shape wrong against prod layout** | High (data loss) | Low | C3 | LD-OQ1 prod-layout verification mandatory pre-enable; anti-join SELECT pre-checked against actual `checkpoint->'channel_versions'` keys before any destructive flip |
| R2 | **C3 zero-refs misinterprets as "delete all"** | High (data loss) | Low | C3 | Fail-safe: zero refs → SKIP + ERROR; dual-tested in unit + real-saver integration |
| R3 | C2 sync-repo + to_thread bridge causes silent no-op | Medium | High if ignored | C2 | `test_message_metadata_liveness_round_trip` BLOCKS PR2 — proves end-to-end plumbing (Rev 3 strengthened: rows for BOTH user + AI ids) |
| R4 | C2 tap reads wrong input (`state_after.values` post-LLM rather than node-return) | Medium | **RESOLVED (Rev 3 / F2 mechanical refactor)** | C2 | F2 hoists the if/else to a single `outgoing` list; one tap before one `return`. Captures BOTH branches (injected + plain turn). AST gate enumerates the unified site. |
| R5 | Tool messages are NEVER tapped — display invisibility from serializer (accepted) | Low | **RESOLVED via F3 reword (Rev 3), Rev 4 B3 reaffirmed** | C2 | Rev 3 / Rev 4: tool messages NEVER tapped (no tools_node hook); invisible to users because `serialize_message` at `daemon/persistence.py:359-361` skips `type=='tool'` from the response output. No "next agent_node infers" mechanism; no "one-cycle lag" — there is no lag because there is no tap (display skips them outright). Phase 2 PERF-3 may add either (a) a tools_node tap or (b) id-diff inference to capture tool timestamps if a future need arises (deferred). |
| **R15 (Rev 3 / F1, Rev 4 B1 corrected)** | **User `HumanMessage` never enters `message_metadata`** — tap inventory missed `_build_graph_input`; user message is in NEITHER node-return NOR compaction paths → `astream`-invoked user messages degrade to `state.ts` fallback indefinitely. (Direct `ainvoke` at `instance_messaging.py:1055` is a SEPARATE accepted-degradation OOS per B1 — it bypasses `_build_graph_input` with inline `{"messages": [message]}` and falls to state.ts the same way; recorded in D19 + Out-of-Scope.) | High | High (caught in Rev 3) | C2 | F1 fix (astream path): entry-path tap at `_build_graph_input` (`daemon/services/instance_messaging.py:237-244`), covers the `astream` invocation which DOES go through `_build_graph_input`. The two formerly-cited lines (`graph.py:3385-3394` and `graph.py:1055`) are NOT the relevant invocation sites — verified directly: `graph.py:3385-3394` is agent_node return block, `graph.py:1055` is LoopDetector scan. Liveness test asserts ROW EXISTENCE for user id (not just non-null fallback). New `test_first_appearance_ordering`: `user.created_at < ai.created_at`. |
| **R16 (Rev 3 / F2)** | **"Single return at graph.py:3396-3397" was actually TWO returns** — line 3396 returns `persisted` (injected branch), line 3397 returns `[response]` (plain turn); tap at 3396 alone misses plain turns; AST "3 sites" gate would REJECT correct impl. | High (Rev 2 latent) | High | C2 | F2 preferred fix: mechanical refactor — hoist both branches into a single `outgoing: list[BaseMessage]` + one `return {**watchover_state_reset, 'messages': outgoing}` — purely mechanical, no logic change. Falls back to tapping at BOTH returns if refactor risks drift. AST gate enumerates FINAL site list. |
| R6 | C1 response-schema drift | High (frontend) | Low | C1 | Frozen fixture captured in PR1; fixture-driven shape test in PR3 |
| R7 | C3 deletes a referenced blob (§9 blob-sharing) | High (data loss) | Low | C3 | Direct anti-join + explicit `checkpoint_ns` predicate + real-saver test BLOCKS merge |
| R8 | Phase 1 disturbs pause/resume / turn-reconciler semantics | High | Low | All | No `aput`/`aupdate_state` semantic changes; only ADDITIVE wraps; gate suites enumerated by filename |
| R9 | Migration applied twice (idempotency) | Low | Low | C2 | `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`; MigrationRunner guards |
| R10 | C2 `seq` column unused → cost only if Phase 2 doesn't pick it up | Low | n/a | C2 | Acceptable; reserved for Phase 2 PERF-2; drop in Phase 2 if unused |
| R11 | `msgs_repo` injection via signature breaks third-party callers | Low | Low | C1 | Accept `\| None = None`; `None` ⇒ EXPLICIT-DEGRADATION path (alist walk is gone; all timestamps fall to `state.ts`; same handling as old threads without backfill) |
| R12 | `alist_count` collapse observed by absence — false green if log silenced | Low | Low | C4 | INFO log with bracketed prefix; gate suites enumerate `test_checkpoint_perf_logging` |
| R13 | Pre-existing 5 quarantined `test_archive_lifecycle` failures appear as regressions | Low | Medium | All | `QUARANTINE.md` honored; not blocking |
| R14 | C2 tap fires before C2 migration on existing DB | Medium | Low | C2 | `tap_node_return` is `try/except`-wrapped + WARNING log |
| **R17 (Rev 3 / non-blocking)** | **`find_excess_checkpoint_groups(max_per_thread=1)` reuse is invalid** — `HAVING COUNT(*) > 1` semantics at `daemon/checkpoint_adapter.py:220` excludes single-checkpoint threads. | Medium (would miss sub-2-thread) | Low (caught before merge) | C3 | New adapter method `find_all_thread_ns_pairs()` (no HAVING); `MaintenanceService._prune_unreferenced_blobs` iterates via this method. Existing `find_excess_checkpoint_groups` stays for retention. |
| **R18 (Rev 3 / non-blocking, Rev 4 leader-wording alignment)** | **`msgs_repo=None` wording contradiction** between signature line and tests — could mislead reviewer into thinking pre-Phase-1 behavior still survives in the `None` branch. | Low | Low | C1 | Single source of truth: signature line documents `None` ⇒ EXPLICIT-DEGRADATION path (C1's alist-kill stands; all timestamps fall to `state.ts`); restated identically in tests + risks. With C1 shipped, "preserves pre-Phase-1 behavior" is FALSE — the alist walk is gone, the response shape is preserved, but every timestamp degrades to `state.ts` (mirrors the old-threads-without-backfill handling). |

---

## Phase 1 Acceptance Criteria (Rev 3 — verbatim + mapped, F1/F2 strengthened)

| # | Criterion (verbatim) | Test / verification (Rev 2 binding) |
|---|----------------------|-------------------------------------|
| **A1** | GET /messages does ZERO alist calls on live path | `tests/unit/persistence/test_get_instance_messages_no_alist.py`; CI gate plus **observed `alist_count=0`** in prod log for ≥7 consecutive days (by absence after C1) |
| **A2** | Response schema unchanged | `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` loads the **PR1-captured `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json`** and asserts byte-equality; the fixture is the binding contract |
| **A3** | Unit tests for repository + get_instance_messages with mocked saver proving no alist | `tests/unit/repositories/test_message_metadata_repository.py` (sync!) + `tests/unit/persistence/test_get_instance_messages_no_alist.py` + **`tests/integration/test_message_metadata_liveness.py`** (real round-trip — BLOCKS PR2; **Rev 3 strengthened**: asserts ROW EXISTENCE for BOTH the user `HumanMessage.id` AND the `AIMessage.id` on a plain turn — not just non-null fallback) + **`tests/integration/test_message_metadata_first_appearance_ordering.py`** (Rev 3 NEW: `user.created_at < ai.created_at`; strongest catch for F1) |
| **A4** | Blob prune has tests proving referenced blobs survive + unreferenced die | `tests/unit/checkpoint_adapter/test_direct_anti_join.py` + **`tests/integration/checkpoint_prune_real_saver.py` BLOCKING** (write→prune→aget/resume incl. `_DeltaSnapshot` chains + zero-refs fail-safe + concurrent-aput safety) + `tests/integration/checkpoint_prune_restore_rehearsal.py` |
| **A5** | All existing tests pass | Gate suites enumerated by filename: pause-resume, turn-reconciler, interrupt-resume, human-approval-resume, is_retry resume-from-checkpoint, 8 mirror tables, aupdate_state idempotent, get_messages lifecycle (excluding the 5 quarantined `test_archive_lifecycle` failures) |

**Phase 1 gate (Rev 2 — from `roadmap.md §6`)** — must all be TRUE before Phase 2 begins:

1. PRs 1–5 merged to `latest` and pushed through the standard deploy ladder into prod; ≥1-week soak.
2. `[/Messages] alist_count=0` observed in production for ≥7 consecutive days (Critical 7: by absence of the alist call entirely; C4 observed the nonzero baseline pre-C1; C1 deletes the call; gate is the disappearance).
3. /messages P50/P95/P99 + response bytes + daemon RSS delta captured (C4 baseline vs post-C1), filed next to this plan as evidence. Frozen fixture captured in PR1 is the binding contract.
4. Post-C3 `checkpoint_blobs` size-per-thread chart flat or declining over ≥2 retention cycles; dry-run reports reviewed; **direct anti-join prod-layout verification (LD-OQ1)** passed; **real-saver integration test** GREEN; **automated restore-rehearsal** GREEN.
5. Pause/resume + interrupt-resume + human-approval + `is_retry` resume-from-checkpoint + 8-mirror-table + turn-reconciler suites ALL green (gate-suite enumeration test green).

---

## Open Questions (Rev 3 — F1/F2 resolved, residual flagged)

| # | Question | Resolution | Status |
|---|----------|------------|--------|
| ~~OQ-1~~ | Prod `checkpoint->'channel_versions'` shape verified? | **RESOLVED via LD-OQ1**: prod-layout verification goes INTO the C3 pre-enable checklist as a mandatory gate. SQL query against prod `checkpoints` confirms shape before destructive enable. | Resolved |
| ~~OQ-2~~ | Flag A enforcement mechanism (no CI infra)? | **RESOLVED via LD-OQ2**: import-level hard-fail test in `tests/integration/test_no_saver_imports_in_routers.py` runs under existing standard test gates; no new CI infra. Standalone linter optional. | Resolved |
| ~~OQ-5~~ | Tap captures the right messages on instance revive / paused-question flow? | **RESOLVED via Critical 8 tests**: `tests/integration/test_message_metadata_revive_stability.py` + `tests/integration/test_message_metadata_paused_question_flow.py` both assert non-null timestamps for all covered messages. REQUIRED for sign-off (blocking in PR2). | Resolved |
| **OQ-F1 (Rev 3, Rev 4 B1 corrected)** | Does the entry-path tap capture user `HumanMessage` on the `astream` path (and is the `ainvoke` path documented as accepted-degradation OOS)? | **RESOLVED via F1 fix + strengthened test**: entry-path tap at `_build_graph_input` (`instance_messaging.py:237-244`) covers the **`astream` invocation path only** — `astream` is the production path and DOES go through `_build_graph_input`. The **direct `ainvoke` invocation at `instance_messaging.py:1055` is accepted-degradation OOS per B1** (zero production callers; inline `{"messages": [message]}` dict bypasses `_build_graph_input`; id-less input; `state.ts` fallback applies; mirrors the watchover handling per LD-D2) — recorded in Out-of-Scope + D19 + the message_tap docstring spec. The F1 liveness test asserts ROW for the user id on a plain turn, plus the new `first_appearance_ordering` test (`user.created_at < ai.created_at`). If F1 had been deferred, post-C1 every `astream`-invoked user message would have silently degraded to `state.ts`. | Resolved |
| **OQ-F2 (Rev 3)** | Does the agent_node tap cover BOTH the injected branch AND the plain-turn branch? | **RESOLVED via F2 mechanical refactor**: hoist both branches into a single `outgoing` list + one `return` + one tap. Purely mechanical — no logic change. AST gate updated to enumerate the unified site. | Resolved |
| OQ-R1 | `seq` column usage timing — populate at read time in Phase 2, drop if unused then? | Default: ADD now (option value), DROP-COLUMN in Phase 2 if unused. (decisions.md D5) | Acceptable default |
| OQ-R2 | Who owns the C3 destructive-enable flip (env flag setter)? | Default: same operator who controls the deploy ladder; documented in pre-enable checklist. | Acceptable default |
| OQ-R3 | Will the freeze-fixture drift when `serialize_message` evolves? | Mitigation: `tests/unit/persistence/README_FROZEN_FIXTURE.md` documents the contract; PRs that touch `daemon/utils.py:serialize_message` must regenerate the fixture + bump a version field inside it; CI gate enforces | Default mitigation; per-PR |
| OQ-R4 | The `seq` column data race on concurrent inserts from different threads | Mitigated by `ON CONFLICT DO NOTHING` (race-free by construction); re-tap doesn't rewrite `seq` (NULL stays NULL until Phase 2 attaches a sequence) | Acceptable default |
| **OQ-R5 (Rev 3 / non-blocking)** | Will the mechanical F2 refactor (single-return) risk behavior drift? | The refactor is purely mechanical: same data flow (response + pairing + injections), just unified into one outgoing variable. Both code paths produce the same `messages` channel value (response alone vs response-with-extras). **If** any edge case is found during code review (e.g., a subtle difference in when `watchover_state_reset` is applied), fall back to F2's fallback: tap at BOTH returns. | Acceptable default with documented fallback |

---

## Appendix A — Files Touched Index (Rev 3 — F1 + F2 added)

| File | New / Modified | Component |
|------|---------------|-----------|
| `daemon/persistence.py` | M | C4 + C1 |
| `daemon/services/maintenance.py` | M | C4 + C3 |
| `daemon/checkpoint_adapter.py` | M | C3 (+ **Rev 3 NEW method `find_all_thread_ns_pairs()`** — no HAVING filter) |
| `daemon/manager.py` | M | C2 + C1 |
| `daemon/graph.py` | M | C2 — **(Rev 3 / F2)** single-return refactor of `graph.py:3386-3397` + reactive-compaction tap at `graph.py:3248-3250` |
| `daemon/services/instance_messaging.py` | M | C2 — **(Rev 3 / F1)** entry-path tap at `_build_graph_input` (`lines:237-244`, covers the `astream` invocation path) + idempotent re-tap at lines 810-822. The direct `ainvoke` invocation at `instance_messaging.py:1055` is accepted-degradation OOS per B1 (recorded in D19 + Out-of-Scope + message_tap docstring spec); no 5th tap. |
| `daemon/constants.py` | M | C3 |
| `daemon/checkpoint_perf.py` | **New** | C4 + consumed by C2/C3 |
| `daemon/services/message_tap.py` | **New** | C2 (Rev 3: 4 `source` labels — `"user_message_entry"`, `"agent_node_return"`, `"compaction_aupdate_reactive"`, `"compaction_aupdate_messaging"`) |
| `daemon/services/checkpoint_prune.py` | **New** | C3 |
| `daemon/repositories/message_metadata/__init__.py` | **New** | C2 |
| `daemon/repositories/message_metadata/models.py` | **New** | C2 |
| `daemon/repositories/message_metadata/repository.py` | **New (SYNC)** | C2 |
| `daemon/migrations/versions/20260825_000001_create_message_metadata.sql` | **New** | C2 |
| ~~`daemon/migrations/versions/20260825_000002_create_checkpoint_blob_refs.sql`~~ | **REMOVED (LD-D1)** | n/a |
| `tools/lint/checkpoint_perf_lint.py` | **New (optional)** | Flag A |
| `tools/lint/allowlist.txt` | **New** | Flag A |
| `docs/runbooks/checkpoint-blob-prune-restore.md` | **New** | C3 |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | **New** | C4 |
| `tests/integration/test_messages_response_fixture_capture.py` | **New** | C4 |
| `tests/integration/gate_suites/test_gate_suite_pause_resume.py` | **New** | C4 |
| `tests/unit/persistence/test_get_instance_messages_no_alist.py` | **New** | C1 |
| `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` | **New** | C1 |
| `tests/integration/test_no_saver_imports_in_routers.py` | **New (BLOCKING per LD-OQ2)** | Flag A |
| `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` | **New** | C4 capture / C1 contract |
| `tests/unit/repositories/test_message_metadata_repository.py` | **New** | C2 |
| `tests/integration/test_message_metadata_liveness.py` | **New (BLOCKING, Rev 3 strengthened for F1)** | C2 — asserts ROWS for BOTH user id AND AI id on plain turn |
| **`tests/integration/test_message_metadata_first_appearance_ordering.py`** | **NEW (Rev 3 / F1 strongest)** | C2 — `user.created_at < ai.created_at` |
| `tests/integration/test_message_metadata_revive_stability.py` | **New** | C2 |
| `tests/integration/test_message_metadata_paused_question_flow.py` | **New (BLOCKING)** | C2 |
| `tests/unit/services/test_message_tap_slot.py` | **New** | C2 |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | **New** | C3 |
| `tests/unit/checkpoint_adapter/test_find_all_thread_ns_pairs.py` | **NEW (Rev 3)** | C3 — tests the new adapter method (no HAVING filter) |
| `tests/integration/checkpoint_prune_real_saver.py` | **New (BLOCKING)** | C3 |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | **New** | C3 |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | **New (Rev 3 updated)** | C3 — uses the new `find_all_thread_ns_pairs()` iteration |
| `tests/unit/lint/test_checkpoint_perf_lint.py` | **New (optional)** | Flag A |
| `tests/integration/test_message_metadata_hook_placement.py` | **New (Rev 3 — 4 sites)** | C2 — verifies tap fires at the **4 approved sites** AND not in any ToolNode block |

**Removed (LD-D1)** — no longer exist:
- `daemon/services/checkpoint_blob_refs.py`
- `daemon/migrations/versions/20260825_000002_create_checkpoint_blob_refs.sql`

**Rejected (Critical 4)** — explicitly out of scope:
- Any custom `ToolNode` wrapper / executor replacement at `daemon/graph.py:5546`.

**Rev 3 F1 + F2 specifics**:
- `daemon/services/instance_messaging.py:237-244` — NEW entry-path tap at `_build_graph_input` (covers the `astream` invocation path; the direct `ainvoke` invocation at `instance_messaging.py:1055` is accepted-degradation OOS per B1 — recorded in D19 + Out-of-Scope + the message_tap docstring spec).
- `daemon/graph.py:3386-3397` — MECHANICAL refactor to single `outgoing: list[BaseMessage]` + one `tap_node_return(...)` + one `return`. AST gate enumerates the unified site.
