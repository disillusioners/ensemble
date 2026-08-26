# PR2 — C2 message_metadata side table + MessageTapSlot (External Gate)

Date: 2026-08-26 · Branch: feature/langgraph-checkpoint-perf · Range: 603c9eb8..c42a8bf5
Mode: 🔴 Deep-Review (council, 2 councilors: agentic + coding, skill=code-review)
Verdict: **APPROVED** — 0 🔴 / 2 🟡 / 7 🟢. Internal PASS 8/8 corroborated; council independently confirmed all 8 items.

## Architecture landed (reference for future reviews)
- `daemon/services/message_tap.py` — MessageTapSlot; **exactly 4 call sites / 4 labels**:
  `user_message_entry` (graph.py:3293 via _build_graph_input), `agent_node_return` (graph.py:3471),
  `compaction_aupdate_reactive` + `compaction_aupdate_messaging` (instance_messaging.py:851, :3454).
  Slot constructions live in instance_lifecycle.py:1323-1331 (spawn) / :3309-3317 (restore) for compaction pair;
  entry/agent_node slots built inline in instance_messaging.py (graph_input not in scope at manager wiring time).
- Containment model: **internal try/except Exception in message_tap.py:146-220 is the SOLE containment layer** —
  call sites are bare awaits BY DESIGN so CancelledError propagates (Python 3.13 BaseException promotion;
  pinned by test_cancelled_error_propagates). Do not "harden" call sites with except BaseException.
- Repo: sync-only, ON CONFLICT DO NOTHING (thread_id, message_id) → idempotent re-taps, first-write-wins,
  created_at immutable on conflict. asyncio.to_thread bridge (message_tap.py:194-198), fresh engine.begin() per call.
- Migration dual-driver: SQLite .sql (20260825_000001) + PG _ensure_postgres_columns (manager.py:5187-5217);
  index `ix_message_metadata_thread` three-way byte-identical (sql:31-34 == models.py:66-70 == manager.py:5213-5216).
- Read path (get_instance_messages alist walk) UNTOUCHED — PR3 flips it. `get_for_thread` has zero prod callers until PR3.
- AST gate: tests/integration/test_message_metadata_hook_placement.py enforces 4-site/4-label/no-ToolNode contract
  across daemon/**; checkpoint_perf.py = CHECKPOINT_PERF_LOGS-gated observability consumed inside slot try.

## Findings that matter (follow-ups)
1. 🟡 message_tap.py:54-80 docstring FALSELY claims call sites also wrap in try/except — rewrites the containment
   model; latent regression vector if someone "simplifies" the internal handler. Fix docstring with merge.
2. 🟡 lifecycle wiring (slot kwargs → build_instance_graph) NOT test-pinned — liveness test threads slot manually,
   AST gate only asserts construction sites exist in file. Drop kwargs → silent empty table in prod. Pin before PR3.
3. 🟢 over-record property: pause between tap-await and node return → side-table rows for un-checkpointed messages
   (never under-records; benign once PR3 joins metadata to checkpoint walk). Document in message_tap.py.

## Patterns learned
- Severity-split resolution: identical factual finding, 🟢 vs 🟡 across models → sided with the model whose reasoning
  tied the finding to a safety invariant (false defense-in-depth doc = latent regression vector, not prose).
- Unverifiable-by-construction items surfaced: no PG fixture (static parity only), no multi-thread stress, tests not
  re-run by read-only gate (provenance from GATE_SUITES.txt).
- Pre-adjudicated deviations honored — flagged scope extras (checkpoint_perf.py, repositories/__init__.py) instead.
