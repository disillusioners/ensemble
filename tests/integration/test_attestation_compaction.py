"""Mode: enforce — compaction preserves the safe tail and characterizes the floor cliff."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from daemon.compaction import CompactionContext, ContextCompactor
from daemon.services.attestation_scanner import scan_for_attestation_detailed


def _compactor():
    config = MagicMock()
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 3
    config.min_recent_window = 3
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
    config.context_window_overrides = {"test": 200}
    config.context_window_default = 0
    compactor = ContextCompactor(
        config=config,
        llm_config={"model": "test", "base_url": "http://test", "api_key": "test", "temperature": 0},
    )

    async def summarize(_prompt, _ctx):
        return "summary"

    compactor._call_summarization_llm = summarize  # type: ignore[method-assign]
    return config, compactor


def _context(config, messages):
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name="test",
        config=config,
        llm_config={
            "model": "test",
            "base_url": "http://test",
            "api_key": "test",
            "temperature": 0,
        },
        last_compacted_at=None,
    )


def _messages(attestation_index: int):
    messages = [
        AIMessage(content="filler " + ("x " * 30), id=f"old-{i}")
        for i in range(6)
    ]
    messages.append(
        AIMessage(
            content="attestation",
            id="attestation",
            tool_calls=[{"name": "attest_completion", "args": {}, "id": "attest-call"}],
        )
    )
    messages.extend(
        AIMessage(content="filler " + ("y " * 30), id=f"new-{i}")
        for i in range(2)
    )
    # Move the attestation to the requested position; the fixture above already
    # places it at index 6 (safe) or we rebuild it explicitly for index 0.
    if attestation_index == 0:
        attestation = messages.pop(6)
        return [attestation, *messages]
    return messages


@pytest.mark.asyncio
async def test_compaction_safe_zone_preserves_attestation_in_tail():
    config, compactor = _compactor()
    result = await compactor.compact_state(_context(config, _messages(6)))
    assert result is not None
    scan = scan_for_attestation_detailed(result.replacement_messages, 3)
    assert scan.attested is True
    assert scan.messages_scanned == 3
    assert any(
        getattr(message, "id", "").startswith("compaction-global-")
        for message in result.replacement_messages
    )


@pytest.mark.asyncio
async def test_compaction_floor_cliff_moves_attestation_outside_window():
    config, compactor = _compactor()
    result = await compactor.compact_state(_context(config, _messages(0)))
    assert result is not None
    scan = scan_for_attestation_detailed(result.replacement_messages, 3)
    # The summary is visible, but the attestation is outside the preserved
    # floor; this characterizes the documented cliff and is not a new gate.
    assert scan.attested is False
    assert any(
        getattr(message, "id", "").startswith("compaction-global-")
        for message in result.replacement_messages
    )
    assert scan.messages_scanned == 3
