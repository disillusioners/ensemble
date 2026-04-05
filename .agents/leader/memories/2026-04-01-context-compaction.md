# Context Compaction Implementation — 2026-04-01

## Summary
Implemented automatic context compaction for long sessions in agents-ensemble. When conversation history approaches the model's context window limit, older messages are summarized via LLM and replaced with a compact summary, preserving recent context.

## Architecture
- **Pre-invocation compaction** — runs in `manager.py` before `graph.astream()`, not a graph node
- **Boundary groups** — messages grouped into atomic units (AI + tool responses never split)
- **`RemoveMessage` sentinels** — correct way to replace messages in LangGraph (NOT raw replacement)
- **`SessionState(MessagesState)`** — custom state schema with `compacted_at` field for dedup
- **Progressive window reduction** + **emergency truncation** — guarantees termination even with few but very large groups
- **Chunked summarization** with hierarchical merge — handles arbitrarily large histories

## Key Files
| File | Purpose |
|------|---------|
| `daemon/compaction.py` | Full compaction engine: ContextCompactor, boundary groups, summarization, truncation |
| `daemon/config.py` | `CompactionConfig` with COMPACTION_ env prefix |
| `daemon/loader.py` | `estimate_messages_tokens()` |
| `daemon/graph.py` | `SessionState(MessagesState)` with `compacted_at` |
| `daemon/manager.py` | `_maybe_compact_context()` integration, retry-skip guard |
| `config.yaml` | `compaction:` section with defaults |
| `tests/unit/test_compaction.py` | 43 unit tests |
| `tests/integration/test_compaction_e2e.py` | 4 integration tests |

## Gotchas Discovered
1. `MessagesState.add_messages` is append-only — must use `RemoveMessage` sentinels, NOT raw message replacement
2. `aupdate_state({"compacted_at": ...})` silently drops keys not in state schema — needed custom `SessionState`
3. `prompt_cache` has `_cache` (private), not `cache` — use `.get()` public API
4. `compaction_type` is `str`, not an enum — no `.value` accessor

## Branch
`feature/context-compaction` pushed to origin, ready for PR.
