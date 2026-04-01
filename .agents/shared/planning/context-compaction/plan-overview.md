# Plan Overview: Context Compaction

## Objective

Add automatic context compaction to the agents-ensemble daemon that detects when conversation history approaches the model's context window limit, summarizes older messages, and replaces them in-place using LangGraph's `RemoveMessage` sentinel pattern — ensuring the graph continues functioning correctly after compaction.

## Scope Assessment

**LARGE** — 4 phases spanning new module creation (compaction engine), graph integration, configuration system extension, and comprehensive testing. Affects core message processing pipeline.

## Context

- **Project**: agents-ensemble
- **Working Directory**: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- **Requested by**: Leader

## Phase Index

| Phase | Name | Objective | Dependencies | Est. Time |
|-------|------|-----------|-------------|-----------|
| 1 | Configuration & Token Estimation | Add `CompactionConfig` to config system, model context limits registry, `estimate_messages_tokens()` for LangChain messages | None | 2h |
| 2 | Compaction Engine | Build core compaction: boundary groups, progressive window reduction, chunked summarization with merge, `RemoveMessage` state replacement, emergency truncation, dedup guard | Phase 1 | 4h |
| 3 | Graph Integration | Wire compactor into `manager.py`: pre-invocation compaction with retry-skip guard, `SessionState` custom schema for dedup metadata, system prompt token budget | Phase 2 | 3h |
| 4 | Testing & Observability | Unit tests for all engine functions, integration tests for graph continuation after compaction (CRIT-4), crash recovery, structured logging | Phases 1-3 | 3h |

## Critical Design Decisions

1. **Pre-invocation compaction** in `manager.py` (not a graph node) — simpler, less invasive
2. **Boundary groups** — messages are grouped into atomic units (AI + tool responses) that are never split
3. **`RemoveMessage` sentinels** via `aupdate_state()` — verified experimentally as the correct way to replace messages in LangGraph state (CRIT-1)
4. **`SystemMessage` with `[Conversation Summary]` marker** for summaries (WARN-1) — avoids confusing the LLM
5. **Progressive window reduction** — shrinks preserved window when it alone exceeds threshold, down to `min_recent_window` (CRIT-2)
6. **Emergency truncation** — if even `min_recent_window` groups exceed threshold, truncate individual messages within groups as last resort
7. **Chunked summarization with hierarchical merge** — if compactable messages are too large for one LLM call, batch summarize then merge (CRIT-3)
8. **`SessionState(MessagesState)`** — custom state schema extending `MessagesState` with `compacted_at` field so dedup metadata persists in checkpoints
9. **Dedup via `compacted_at` timestamp** — stored in `SessionState`, prevents re-compaction within 60 seconds (WARN-2)
10. **Skip compaction on retry** — `is_retry=True` skips compaction entirely (WARN-5)

## Key Files (New/Modified)

| File | Action | Phase |
|------|--------|-------|
| `daemon/config.py` | Modify — add `CompactionConfig`, wire into `Config` and `load_config()` | 1 |
| `daemon/loader.py` | Modify — add `estimate_messages_tokens()` | 1 |
| `daemon/compaction.py` | **NEW** — model limits registry, `ContextCompactor`, all compaction logic | 1, 2 |
| `config.yaml` | Modify — add `compaction:` section | 1 |
| `daemon/manager.py` | Modify — compaction integration in `_process_message_with_tracking()` and `send_message()` | 3 |
| `daemon/graph.py` | Modify — use `SessionState` instead of `MessagesState` | 3 |
| `tests/unit/test_compaction.py` | **NEW** — unit tests | 4 |
| `tests/integration/test_compaction_e2e.py` | **NEW** — integration tests | 4 |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM summarization fails mid-compaction | high | Truncation fallback preserves recent window without summarization |
| Token estimation inaccuracy causes over/under compaction | medium | 80% threshold provides buffer; target_ratio=0.40 leaves room |
| `RemoveMessage` IDs not matching checkpoint messages | high | Messages from checkpoint always have IDs; verified experimentally |
| Infinite loop when all messages exceed threshold | critical | Progressive reduction + emergency truncation ensure termination |
| Dedup metadata lost across checkpoints | high | Custom `SessionState` schema with `compacted_at` field (REV-CRIT-1) |
| Re-compaction on every message after first compaction | medium | Dedup guard with 60s cooldown window |
| Chunked summarization partial failures | medium | Each batch is independent; merge handles 2-N partial summaries |
| Compaction breaks tool call integrity | critical | Boundary groups guarantee AI+Tool messages stay together |

## Success Criteria

- [ ] Compaction triggers when token usage exceeds configurable threshold
- [ ] Summary is coherent and preserves key decisions/facts/tool outcomes
- [ ] Graph continues to function correctly after compaction (tool calls, streaming, multi-turn)
- [ ] Crash recovery restores compacted state from checkpoint
- [ ] Tool call groups (AI + ToolMessages) are NEVER split during compaction
- [ ] Emergency truncation activates when even minimum window exceeds threshold
- [ ] Chunked summarization handles arbitrarily large conversation histories
- [ ] Dedup prevents re-compaction within 60 seconds
- [ ] Compaction failure never blocks message processing
- [ ] All existing tests pass (no regressions)

## Tracking

- Created: 2026-04-01
- Last Updated: 2026-04-01 (v3 — emergency restoration with review fixes)
- Status: active
- Revision: v3 (incorporates REV-CRIT-1, REV-CRIT-2, REV-CRIT-3, REV-WARN-1, REV-WARN-4)
