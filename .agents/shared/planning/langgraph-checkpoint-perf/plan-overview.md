# Plan Overview: LangGraph Checkpoint / Message Persistence Performance

| | |
|---|---|
| Date | 2026-08-25 (Rev 4 — doc-only final pass; engineering substance approved) |
| Planner | planner[v2] (dispatcher) — synthesized from worker outputs |
| Branch | `feature/langgraph-checkpoint-perf` (created from latest) |
| Source analysis | `~/Downloads/langgraph-checkpoint-performance-discussion.md` (1777 lines; PERF-1..9, Solutions A–U) |
| User directive | "Focus on improve which easy, small blast radius and high impact first" — ranked by impact × ease ÷ blast-radius; only the top tier (Phase 1) implemented now |
| Review trail | Round 1: 8 criticals → Rev 2 (all resolved). Round 2: F1/F2 → Rev 3 (both fixed; 5 folds (F3 + 4)). Round 3: engineering APPROVED; doc-only B1/B2/B3 + 2 leader wordings → Rev 4. No further cycles — approver does fresh-eyes confirmation. |

## Documents in this Directory

| File | Author | Content |
|------|--------|---------|
| `research-findings.md` | explorer (HIGH confidence) | Integration points with file:line citations |
| `roadmap.md` | worker (roadmap-strategy) | 22-item ranking, milestones, critical path, Phase 2 gate, Phase 2+ sketches (wording aligned Rev 2) |
| `phase1-plan.md` | worker (plan-creation) | **Rev 4** — C4→C2→C1→C3 + import guard, per-component specs/tests/risks/rollbacks, 5-PR sequencing |
| `decisions.md` | worker (plan-creation) | **Rev 4** — D1–D10 revised, D11 removed, D14–D21, LD-D1/D2/OQ1/OQ2 recorded; D19 carries the direct-ainvoke accepted degradation; D-s6 fallback stub |

## Problem (verified)

GET `/instances/{id}/messages` walks up to 1,000 checkpoints via `saver.alist()` (`persistence.py:326-333`) to reconstruct timestamps — ~206 MB / ~42 s / 2.1 GB RSS on the measured thread. Independently: retention prunes checkpoints to 50/thread but never deletes `checkpoint_blobs` — unbounded growth.

## Phase 1 (Rev 4 — implement now)

| # | Component | What | Effort | Key mechanism |
|---|-----------|------|--------|---------------|
| PR1 | **C4** — instrumentation + fixture capture | `checkpoint_perf.py` observed-count gate (`alist_count=<observed>` → 0 after C1); frozen response fixture from real pre-Phase-1 run (id-less + multimodal); gate-suite manifest | ~3 d | fixture MUST precede any PR3 read-path change |
| PR2 | **C2** — Solution M side table | `message_metadata(thread_id, message_id, created_at, seq)`; **4-site tap** (see below); sync repository + `asyncio.to_thread` bridge; idempotent `ON CONFLICT DO NOTHING`; RemoveMessage filter | **~7 d** | unified liveness test (user + AI ids on a plain turn) + first-appearance ordering test |
| PR3 | **C1** — PERF-1 read flip | aget-only; side-table timestamps, absent row → `state.ts` fallback (null only if both absent); fixture-driven byte-shape test | ~4 d | import-level hard-fail test (no `langgraph.checkpoint.*` under `daemon/routers/**`) — existing test gates, no CI |
| PR4 | **C3** — blob prune | direct anti-join on `checkpoint->'channel_versions'` (ns-matched); zero-refs fail-safe skip + ERROR; `find_all_thread_ns_pairs` adapter method (D21); dry-run → destructive ladder; prod-layout pre-enable check | ~5 d | real-saver integration test BLOCKS merge + destructive enable; restore-rehearsal roundtrip |
| PR5 | Gate verification | Evidence capture vs `GATE_SUITES.txt` manifest | ~1 d | no code change |

**Total: 20 d (~4.0 PW)** — Rev 1: 16 d → Rev 2: 19 d → Rev 3: 20 d (+1 for F1/F2 fix package); Rev 4 doc-only, totals unchanged. Expected effect: ~206 MB → ~762 KB, ~42 s → sub-second.

## Final Tap-Site Enumeration (Rev 3 → affirmed Rev 4 — 4 sites, AST-gate-matched; entry site covers astream only)

| # | Site | Purpose | Source label |
|---|------|---------|--------------|
| 1 | `instance_messaging.py:237-244` (`_build_graph_input`) | User HumanMessage at graph START — covers the `astream` invocation path (**F1 fix**; the direct `ainvoke` invocation at `instance_messaging.py:1055` is accepted-degradation OOS per B1 — D19) | `user_message_entry` |
| 2 | `graph.py:3386-3397` (post-refactor single return) | LLM response + report-injection + tool-pairing + user-injection msgs, both branches (**F2 fix**) | `agent_node_return` |
| 3 | `graph.py:3248-3250` (reactive-compaction aupdate_state) | Idempotent re-tap of replacement messages | `compaction_aupdate_reactive` |
| 4 | `instance_messaging.py:810-822` (messaging-compaction aupdate_state) | Idempotent re-tap of replacement messages | `compaction_aupdate_messaging` |

AST gate: exactly 4 tap calls, 4 distinct labels (each once), zero taps in ToolNode blocks, zero `langgraph.checkpoint` imports at hook sites. **F2 fallback (D-s6/OQ-R5):** if the single-return refactor risks behavior drift → tap both returns; gate becomes 5 sites (pre-specced).

## Key Decisions (full detail: decisions.md)

- **D1/D19/D20**: 4-site tap inventory (entry + node-return + 2 compaction); single-return refactor chosen as purely mechanical, both-returns fallback documented
- **D2**: dual-driver migration `20260825_000001`; NEW tables arrive via boot-time `create_all` — dual-driver convention INTENTIONAL (wording finalized Rev 4)
- **D3**: `ON CONFLICT DO NOTHING` — first-appearance timestamps survive re-taps
- **D4**: anti-join on `checkpoint->'channel_versions'` + zero-refs fail-safe — never naive DELETE (§9)
- **D10/D18**: tool messages deliberately untapped in Phase 1; display invisibility is `serialize_message`'s `type=='tool'` skip (F3 reworded — phantom "next-run inference" mechanism removed); Phase 2 options documented
- **D21**: `find_all_thread_ns_pairs` (no HAVING filter) replaces the misused `find_excess_checkpoint_groups(max_per_thread=1)`

## Top Risks (Rev 4)

1. Blob-ref layout drift dev↔prod — prod-layout pre-enable gate (LD-OQ1) + zero-refs fail-safe
2. Response-schema drift breaking Angular — real frozen fixture, byte-equality
3. Tap wiring hole AST can't see (entry-path bypass) — runtime liveness covers it (AST + liveness are complementary layers)
4. F2 refactor behavior drift — bounded to one function; both-returns fallback pre-specced (D-s6)

## Acceptance (Phase 1, verbatim — Rev 4 mapping)

1. /messages ZERO alist calls (observed-count gate + import guard, existing test gates)
2. Response schema unchanged (real frozen fixture, byte-equality)
3. Repository + get_instance_messages mocked-saver tests proving no alist
4. Blob prune: referenced survive + unreferenced die — on a real saver (blocking)
5. All existing tests pass (5 quarantined `test_archive_lifecycle` failures excluded — pre-existing)

## Phase 2+ (sketched — roadmap.md)

Pagination (PERF-2, frontend), timestamp backfill (PERF-4, first Phase 2 item), PERF-3 store (+ tools_node tap / id-diff inference options for tool timestamps), ShallowPostgresSaver eval (PERF-5), conn concurrency, artifacts (PERF-7), rotation (deferred). Gate: roadmap §6.

## Residual Items (phase1-plan.md — implementer checklist)

Verify at implementation time: `serialize_message` tool-skip cite (persistence.py:361-363), `_build_graph_input` line range currency, F2 refactor review trigger for D-s6 fallback, `created_at` immutability assertion in liveness test, GATE_SUITES.txt rename discipline, optional prod-layout automation (Phase 2), sync-repo-in-async profiling (Phase 3).
).
