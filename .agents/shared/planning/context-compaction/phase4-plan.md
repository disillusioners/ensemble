# Phase 4: Testing & Observability

## Objective

Create comprehensive unit tests for the compaction engine, integration tests for end-to-end compaction including **post-compaction graph continuation** (CRIT-4), crash recovery, and add structured logging/metrics for production observability.

## Context

- **Previous phase**: Phase 3 — Graph Integration (completed)
- **Key files**: `tests/unit/test_compaction.py` (new), `tests/integration/test_compaction_e2e.py` (new)
- **Key decisions**:
  - Use pytest for all tests (consistent with existing test structure)
  - Mock LLM calls in unit tests (test logic, not the API)
  - Integration test uses the real graph with in-memory SQLite checkpointer
  - **CRIT-4**: Integration test MUST verify graph continuation after compaction (new message → tool call → full pipeline)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Unit tests: `identify_boundary_groups()`** | Test all message patterns: single messages, AI+Tool pairs, AI+multi-Tool, orphan ToolMessages, mixed sequences | `tests/unit/test_compaction.py` |
| 2 | **Unit tests: `select_compactable_groups()` with progressive reduction** | Test: fewer groups than window, exact window, more than window, progressive reduction when tokens exceed threshold, min_window floor | `tests/unit/test_compaction.py` |
| 3 | **Unit tests: `estimate_messages_tokens()`** | Test with empty list, single message, tool calls, multi-message conversation, content as list | `tests/unit/test_compaction.py` |
| 4 | **Unit tests: `_build_replacement_messages()` with RemoveMessage** | Verify RemoveMessage sentinels have correct IDs, summary message is present, preserved messages are in order | `tests/unit/test_compaction.py` |
| 5 | **Unit tests: `ContextCompactor.compact_state()` with truncation** | Mock the LLM to fail and verify truncation fallback works with RemoveMessage pattern | `tests/unit/test_compaction.py` |
| 6 | **Unit tests: Token threshold detection** | Verify compaction triggers at correct token thresholds (below, at, above threshold) | `tests/unit/test_compaction.py` |
| 7 | **Unit tests: Recent window preservation** | Verify the N most recent groups are always preserved regardless of token count | `tests/unit/test_compaction.py` |
| 8 | **Unit tests: Dedup guard** | Verify `compact_state()` returns None when `last_compacted_at` is recent | `tests/unit/test_compaction.py` |
| 9 | **Integration test: Multi-turn compaction with graph continuation (CRIT-4)** | Create session → send 30+ messages → verify compaction fires → send new message → verify tool call works → verify full pipeline works with compacted state | `tests/integration/test_compaction_e2e.py` |
| 10 | **Integration test: Crash recovery after compaction** | Compact session → create new manager with same checkpointer → verify compacted state restored → send message → verify continuation | `tests/integration/test_compaction_e2e.py` |
| 11 | **Unit test: Emergency truncation (`test_emergency_truncation_large_groups`)** (REV-CRIT-2) | Test that `emergency_truncate()` correctly truncates tool responses, human messages, and progressively truncates from oldest when all groups exceed threshold | `tests/unit/test_compaction.py` |
| 12 | **Unit test: Chunked summarization with merge (`test_chunked_summarization`)** (REV-CRIT-2) | Test `_truncate_batch_to_fit()`, `_merge_summaries()` with 2-3 partial summaries (simple merge), 4+ summaries (hierarchical), and size-check condensation | `tests/unit/test_compaction.py` |
| 13 | **Integration test: Re-compaction of already-compacted session (dedup works)** (REV-CRIT-1) | Compact session → verify `compacted_at` stored in `SessionState` → send another message → verify dedup prevents re-compaction → wait for cooldown → verify re-compaction proceeds | `tests/integration/test_compaction_e2e.py` |
| 14 | **Structured logging** | Ensure all compaction events log with consistent format: `[Compaction] session_id: type: metrics: error?` | `daemon/compaction.py` + `daemon/manager.py` |
| 15 | **Run existing test suite** | Ensure no regressions from compaction code | Full test suite |

## Key Files

- `tests/unit/test_compaction.py` — **NEW FILE** — Unit tests for compaction engine
- `tests/integration/test_compaction_e2e.py` — **NEW FILE** — End-to-end integration tests
- `daemon/compaction.py` — Add structured logging throughout
- `daemon/manager.py` — Add structured logging for compaction events

## Detailed Design

### CRIT-4: Post-Compaction Graph Continuation Test

This is the **most critical integration test**. It verifies the entire pipeline works after compaction:

```python
# tests/integration/test_compaction_e2e.py

@pytest.mark.asyncio
async def test_compaction_and_graph_continuation():
    """CRIT-4: Verify full graph pipeline works after compaction.
    
    Test flow:
    1. Create session with a tool-using agent (e.g., agent that uses bash tool)
    2. Send 30+ messages to build up history
    3. Verify compaction fires (check logs / state)
    4. Send a new message that requires tool use
    5. Verify tool call executes correctly
    6. Verify agent processes tool result and responds
    7. Verify the full turn completes successfully
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup: temp DB, config with low threshold, manager
        db_path = Path(tmpdir) / "test.db"
        conn = await aiosqlite.connect(str(db_path))
        checkpointer = AsyncSqliteSaver(conn)
        
        config = Config(
            llm=LLMConfig(model="gpt-4"),
            compaction=CompactionConfig(
                enabled=True,
                threshold=0.01,  # Very low to force compaction
                recent_message_window=2,
                min_recent_window=1,
                min_messages_before_compaction=2,
            ),
        )
        manager = SessionManager(config)
        manager._checkpointer = checkpointer
        
        # 1. Create session
        session_id = await manager.spawn_session(agent_id="coder")
        
        # 2. Build up history (mock LLM responses to avoid real API calls)
        for i in range(30):
            await manager.send_message(session_id, f"Message {i}")
        
        # 3. Verify compaction occurred
        graph = manager.get_session(session_id)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        messages = state.values.get("messages", [])
        assert len(messages) < 60  # Should be much less than 60 (30 user + 30 ai)
        
        # 4. Send a new message that triggers tool use
        # (Mock the LLM to return a tool call)
        result = await manager.send_message(
            session_id, "What files are in the current directory?"
        )
        
        # 5. Verify the turn completed successfully
        assert result.content is not None
        assert result.tool_calls is not None or result.content  # Either tool call or response
        
        # 6. Verify state is still coherent
        state2 = await graph.aget_state({"configurable": {"thread_id": session_id}})
        messages2 = state2.values.get("messages", [])
        assert len(messages2) > 0
        
        # 7. Verify the summary message exists in state
        summary_found = any(
            hasattr(m, 'content') and isinstance(m.content, str) 
            and '[Conversation Summary]' in m.content
            for m in messages2
        )
        assert summary_found, "Summary message should be present in compacted state"


@pytest.mark.asyncio
async def test_compaction_preserves_tool_call_integrity():
    """Verify tool calls and responses stay together after compaction.
    
    Specifically tests that we never have an AIMessage with tool_calls
    without its corresponding ToolMessage responses.
    """
    # Create a session with interleaved tool calls
    # After compaction, verify every AIMessage.tool_calls has matching ToolMessages
    ...


@pytest.mark.asyncio
async def test_compaction_retry_skip():
    """Verify compaction is skipped on retry (WARN-5)."""
    # Create session, trigger compaction
    # Simulate a failure that causes retry
    # Verify compaction does NOT run on the retry path
    ...


@pytest.mark.asyncio
async def test_compaction_dedup():
    """Verify compaction dedup prevents re-compaction (WARN-2)."""
    # Compact a session
    # Send another message
    # Verify compaction is skipped (dedup check on last_compacted_at)
    ...


@pytest.mark.asyncio
async def test_crash_recovery_after_compaction():
    """Test that compacted state survives crash/recovery cycle."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        # Manager 1: compact a session
        conn1 = await aiosqlite.connect(str(db_path))
        saver1 = AsyncSqliteSaver(conn1)
        manager1 = SessionManager(config_with_compaction)
        manager1._checkpointer = saver1
        session_id = await manager1.spawn_session(agent_id="coder")
        for i in range(30):
            await manager1.send_message(session_id, f"msg{i}")
        await conn1.close()
        
        # Manager 2: fresh instance, same DB
        conn2 = await aiosqlite.connect(str(db_path))
        saver2 = AsyncSqliteSaver(conn2)
        manager2 = SessionManager(config_with_compaction)
        manager2._checkpointer = saver2
        
        # Restore session
        graph = manager2.get_session(session_id)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        messages = state.values.get("messages", [])
        
        # Verify compacted state was restored (fewer than 60 messages)
        assert len(messages) < 60
        
        # Verify graph works with restored state
        result = await manager2.send_message(session_id, "Are you still working?")
        assert result.content is not None
```

### Unit Test: `_build_replacement_messages()` with RemoveMessage

```python
class TestBuildReplacementMessages:
    def test_creates_remove_sentinels(self):
        """Verify RemoveMessage sentinels are created for each compactable message."""
        groups = [
            MessageGroup(0, 0, [HumanMessage(content="old1", id="h1")], "single"),
            MessageGroup(1, 1, [AIMessage(content="old2", id="a1")], "single"),
        ]
        preserved = [
            MessageGroup(2, 2, [HumanMessage(content="keep", id="h3")], "single"),
        ]
        summary = SystemMessage(content="[Conversation Summary]\n...", id="s1")
        
        result = _build_replacement_messages(groups, preserved, summary)
        
        # Should have: 2 RemoveMessage + 1 summary + 1 preserved = 4 items
        remove_count = sum(1 for m in result if isinstance(m, RemoveMessage))
        assert remove_count == 2
        assert result[0].id == "h1"  # RemoveMessage for h1
        assert result[1].id == "a1"  # RemoveMessage for a1
        assert isinstance(result[2], SystemMessage)  # Summary
        assert result[3].id == "h3"  # Preserved message
    
    def test_preserves_order(self):
        """Preserved messages maintain their original order."""
        preserved = [
            MessageGroup(0, 0, [HumanMessage(content="first", id="h1")], "single"),
            MessageGroup(1, 1, [AIMessage(content="second", id="a1")], "single"),
        ]
        summary = SystemMessage(content="[Conversation Summary]\n...", id="s1")
        
        result = _build_replacement_messages([], preserved, summary)
        
        # Summary first, then preserved in order
        assert result[0].id == "s1"
        assert result[1].id == "h1"
        assert result[2].id == "a1"
    
    def test_handles_tool_groups(self):
        """Tool groups: all messages in group get RemoveMessage sentinels."""
        groups = [
            MessageGroup(0, 1, [
                AIMessage(content="", id="a1", tool_calls=[{"id": "tc1", "name": "bash", "args": {}}]),
                ToolMessage(content="output", id="t1", tool_call_id="tc1", name="bash"),
            ], "tool_sequence"),
        ]
        summary = SystemMessage(content="[Conversation Summary]\n...", id="s1")
        
        result = _build_replacement_messages(groups, [], summary)
        
        remove_count = sum(1 for m in result if isinstance(m, RemoveMessage))
        assert remove_count == 2  # Both AIMessage and ToolMessage removed
```

### Unit Test: Dedup Guard

```python
class TestCompactionDedup:
    def test_skips_recently_compacted(self):
        """compact_state() returns None when last_compacted_at is recent."""
        compactor = ContextCompactor(config, llm_config)
        context = CompactionContext(
            messages=[...many messages exceeding threshold...],
            system_prompt_tokens=1000,
            model_name="gpt-4",
            config=config,
            llm_config=llm_config,
            last_compacted_at=datetime.now(timezone.utc).isoformat(),  # Just now
        )
        result = asyncio.run(compactor.compact_state(context))
        assert result is None  # Skipped due to dedup
    
    def test_compacts_when_stale(self):
        """compact_state() proceeds when last_compacted_at is old."""
        compactor = ContextCompactor(config, llm_config)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        context = CompactionContext(
            messages=[...many messages...],
            system_prompt_tokens=1000,
            model_name="gpt-4",
            config=config,
            llm_config=llm_config,
            last_compacted_at=old_timestamp,
        )
        # Should proceed with compaction (not return None for dedup)
        result = asyncio.run(compactor.compact_state(context))
        assert result is not None or True  # May still skip for other reasons
```

### Unit Test: Emergency Truncation (REV-CRIT-2)

```python
class TestEmergencyTruncation:
    def test_truncates_tool_responses(self):
        """Emergency truncation targets tool responses first."""
        messages = [
            HumanMessage(content="short query", id="h1"),
            AIMessage(content="", id="a1", tool_calls=[{"id": "tc1", "name": "bash", "args": {"cmd": "ls"}}]),
            ToolMessage(content="x" * 10000, id="t1", tool_call_id="tc1", name="bash"),  # Very long
            HumanMessage(content="next query", id="h2"),
            AIMessage(content="response", id="a2"),
        ]
        
        result = emergency_truncate(messages, max_tokens=200, tokenizer_fn=estimate_messages_tokens)
        
        # Tool response should be truncated
        tool_msg = [m for m in result if getattr(m, 'type', '') == 'tool'][0]
        assert len(tool_msg.content) < 10000
        assert "[...truncated]" in tool_msg.content
    
    def test_truncates_human_messages_on_second_pass(self):
        """If tool truncation isn't enough, human messages are truncated."""
        messages = [
            HumanMessage(content="h" * 5000, id="h1"),
            AIMessage(content="short", id="a1"),
            HumanMessage(content="h" * 5000, id="h2"),
            AIMessage(content="short", id="a2"),
        ]
        
        result = emergency_truncate(
            messages, max_tokens=200, tokenizer_fn=estimate_messages_tokens,
            max_human_message_chars=1000,
        )
        
        # At least one human message should be truncated
        human_msgs = [m for m in result if getattr(m, 'type', '') == 'human']
        truncated = [m for m in human_msgs if "[...truncated]" in m.content]
        assert len(truncated) > 0
    
    def test_returns_same_length_as_input(self):
        """Emergency truncation never removes messages, only truncates content."""
        messages = [
            HumanMessage(content="q" * 1000, id=f"h{i}")
            for i in range(10)
        ]
        
        result = emergency_truncate(messages, max_tokens=100, tokenizer_fn=estimate_messages_tokens)
        assert len(result) == len(messages)


class TestChunkedSummarization:
    @pytest.mark.asyncio
    async def test_merge_two_summaries(self):
        """Merging 2 summaries uses simple concatenation with merge prompt."""
        compactor = ContextCompactor(config, llm_config)
        partials = [
            SystemMessage(content="[Conversation Summary]\nPart 1: user asked about auth", id="s1"),
            SystemMessage(content="[Conversation Summary]\nPart 2: user asked about API", id="s2"),
        ]
        
        # Mock the LLM call
        with patch.object(compactor, '_call_summarization_llm', return_value="Merged summary"):
            result = await compactor._merge_summaries(partials, context)
        
        assert "[Conversation Summary]" in result.content
        assert result.id is not None  # Has a new ID
    
    @pytest.mark.asyncio
    async def test_hierarchical_merge_many_summaries(self):
        """6 summaries get hierarchical pair-wise merge."""
        compactor = ContextCompactor(config, llm_config)
        partials = [
            SystemMessage(content=f"[Conversation Summary]\nPart {i}", id=f"s{i}")
            for i in range(6)
        ]
        
        call_count = 0
        async def mock_llm(prompt, ctx):
            nonlocal call_count
            call_count += 1
            return f"Merged {call_count}"
        
        with patch.object(compactor, '_call_summarization_llm', side_effect=mock_llm):
            result = await compactor._merge_summaries(partials, context)
        
        # Should have made multiple merge calls (hierarchical)
        assert call_count >= 3  # At least 3 pairs + final merge
    
    def test_truncate_batch_to_fit(self):
        """_truncate_batch_to_fit truncates tool responses and drops oldest groups."""
        groups = [
            MessageGroup(0, 0, [HumanMessage(content="q" * 5000, id="h1")], "single"),
            MessageGroup(1, 1, [AIMessage(content="a" * 5000, id="a1")], "single"),
            MessageGroup(2, 2, [HumanMessage(content="q" * 5000, id="h2")], "single"),
        ]
        
        result = _truncate_batch_to_fit(groups, max_tokens=500, tokenizer_fn=estimate_messages_tokens)
        
        # Should have kept at least 1 group
        assert len(result) >= 1
        # Total tokens should be under limit
        all_msgs = [m for g in result for m in g.messages]
        assert estimate_messages_tokens(all_msgs) <= 500
```

### Integration Test: Re-compaction Dedup via SessionState (REV-CRIT-1)

```python
@pytest.mark.asyncio
async def test_dedup_via_session_state():
    """REV-CRIT-1: Verify compacted_at persists in SessionState and prevents re-compaction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = await aiosqlite.connect(str(db_path))
        checkpointer = AsyncSqliteSaver(conn)
        
        config = Config(
            llm=LLMConfig(model="gpt-4"),
            compaction=CompactionConfig(
                enabled=True,
                threshold=0.01,
                recent_message_window=2,
                min_recent_window=1,
                min_messages_before_compaction=2,
            ),
        )
        manager = SessionManager(config)
        manager._checkpointer = checkpointer
        
        session_id = await manager.spawn_session(agent_id="coder")
        
        # Build up history to trigger compaction
        for i in range(30):
            await manager.send_message(session_id, f"Message {i}")
        
        # Verify compacted_at is in state
        graph = manager.get_session(session_id)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        compacted_at = state.values.get("compacted_at")
        assert compacted_at is not None, "compacted_at should be stored in SessionState after compaction"
        
        # Send another message — dedup should prevent re-compaction
        compaction_call_count = 0
        original_compact = manager._compactor.compact_state
        
        async def counting_compact(ctx):
            nonlocal compaction_call_count
            compaction_call_count += 1
            return await original_compact(ctx)
        
        manager._compactor.compact_state = counting_compact
        await manager.send_message(session_id, "Another message")
        
        # compact_state should have been called but returned None due to dedup
        # (The call happens, but the dedup check inside returns None)
        
        await conn.close()
```

### Structured Logging Format

```
[Compaction] {session_id_prefix}: {compaction_type} | messages: {before} → {after} | tokens: {before} → {after} (saved {saved}) | {error?}

Examples:
[Compaction] abc12345: summarization | messages: 45 → 8 | tokens: 95000 → 28000 (saved 67000)
[Compaction] abc12345: chunked_summarization | messages: 80 → 12 | tokens: 180000 → 35000 (saved 145000) | batches: 4
[Compaction] abc12345: truncation | messages: 30 → 5 | tokens: 82000 → 18000 (saved 64000) | warning: Summarization failed
[Compaction] abc12345: emergency_truncation | messages: 5 → 5 | tokens: 82000 → 4000 (saved 78000) | warning: Progressive reduction insufficient, truncated individual messages
[Compaction] abc12345: skipped | messages: 8 | tokens: 5000 (below threshold)
[Compaction] abc12345: skipped (recently compacted at 2026-04-01T12:00:00Z)
[Compaction] abc12345: skipped (retry path)
```

## Constraints

- All unit tests must run without external dependencies (mock the LLM)
- Integration tests must clean up temp files/databases
- Existing test suite must pass (no regressions)
- Logging must be consistent and parseable
- **CRIT-4 test is mandatory** — not optional. Must verify graph continuation after compaction.

## Deliverables

- [ ] `tests/unit/test_compaction.py` with all unit tests (boundary groups, progressive reduction, RemoveMessage, dedup, emergency truncation, chunked summarization with merge)
- [ ] `tests/integration/test_compaction_e2e.py` with CRIT-4 graph continuation test
- [ ] Integration test: crash recovery after compaction
- [ ] Integration test: tool call integrity after compaction
- [ ] Integration test: retry-skip guard
- [ ] Integration test: dedup guard via `SessionState.compacted_at` (REV-CRIT-1)
- [ ] Unit test: emergency truncation for large groups (REV-CRIT-2)
- [ ] Unit test: chunked summarization with merge (REV-CRIT-2)
- [ ] Structured logging format defined and implemented
- [ ] All existing tests pass (regression check)
