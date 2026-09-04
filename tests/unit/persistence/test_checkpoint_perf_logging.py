"""Unit tests for ``daemon.checkpoint_perf`` + the C4 instrumentation sites.

PR1 (C4) of the LangGraph Checkpoint / Message Persistence Performance plan.
The plan's test table (phase1-plan.md:183-191) specifies six test cases;
all six are implemented here.

The integration tests for ``get_instance_messages`` live in
``tests/integration/test_messages_response_fixture_capture.py`` (which
spins up a real LangGraph graph); the unit tests here cover the
mechanics with a mock saver so they stay fast and isolated.

NOTE: the conftest mocks ``langgraph.checkpoint.*`` globally for unit
tests. None of the modules these tests import depend on langgraph
directly (``daemon.checkpoint_perf`` is a leaf module, ``daemon.persistence``
only uses the saver through a duck-typed ``checkpointer`` arg with
``raw_saver`` access). The integration test that needs the real langgraph
restores the real modules via its own ``restore_langgraph_modules`` fixture.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon import checkpoint_perf
from daemon.checkpoint_perf import (
    invariant_check_no_alist,
    log_messages_api,
    log_saver_op,
    time_saver_op,
)


# ─────────────────────────────────────────────────────────────────────────────
# log_saver_op / time_saver_op
# ─────────────────────────────────────────────────────────────────────────────


class TestLogSaverOp:
    """The structured ``[CheckpointPerf]`` line carries observed fields only."""

    def test_log_saver_op_logs_duration_ms(self, caplog):
        """``op=aget thread=<8-char-prefix> duration_ms=N`` is emitted."""
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aget", "thread-abcdef1234567890", 42)
        # The plan mandates the thread_id be sliced to 8 chars.
        # "thread-abcdef1234567890" -> first 8 chars -> "thread-a".
        assert any(
            "[CheckpointPerf]" in rec.message
            and "op=aget" in rec.message
            and "thread=thread-a " in rec.message  # 8-char prefix + separator space
            and "duration_ms=42" in rec.message
            for rec in caplog.records
        ), f"Missing expected [CheckpointPerf] line in {[r.message for r in caplog.records]}"

    def test_log_saver_op_deleted_default_is_zero(self, caplog):
        """When ``deleted`` is omitted it defaults to 0 in the log line."""
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aput", "thread-aaaa", 5)
        assert any("deleted=0" in r.message for r in caplog.records)

    def test_log_saver_op_respects_env_suppression(self, caplog, monkeypatch):
        """``CHECKPOINT_PERF_LOGS=0`` suppresses the emit."""
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aget", "thread-aaaa", 42)
        assert not any("[CheckpointPerf]" in r.message for r in caplog.records)


class TestTimeSaverOp:
    """Async timing wrapper around any awaitable saver op."""

    @pytest.mark.asyncio
    async def test_time_saver_op_logs_duration_ms(self, caplog):
        """Wrapper emits one [CheckpointPerf] line with the measured duration."""
        async def coro():
            await asyncio.sleep(0.001)
            return "result"

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            result = await time_saver_op("aget", "thread-zzzz", coro())

        assert result == "result"
        matching = [r for r in caplog.records if "[CheckpointPerf]" in r.message]
        assert len(matching) == 1
        msg = matching[0].message
        assert "op=aget" in msg
        assert "thread=thread-z" in msg  # 8-char prefix (8th char is 'z' from 'thread-zzzz' = 'thread-z')
        assert "duration_ms=" in msg

    @pytest.mark.asyncio
    async def test_time_saver_op_logs_even_on_exception(self, caplog):
        """Wrapper logs even if the coro raises — duration_ms still emitted."""
        async def coro():
            raise RuntimeError("boom")

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            with pytest.raises(RuntimeError, match="boom"):
                await time_saver_op("aget", "thread-xxxx", coro())

        # Despite the exception the timing log was emitted.
        matching = [r for r in caplog.records if "[CheckpointPerf]" in r.message]
        assert len(matching) == 1
        assert "duration_ms=" in matching[0].message

    @pytest.mark.asyncio
    async def test_time_saver_op_returns_coroutine_result(self):
        """The coro result is returned untouched (no wrapping/transforming)."""
        sentinel = {"key": ["value"]}
        async def coro():
            return sentinel

        result = await time_saver_op("aget", "thread-aaaa", coro())
        assert result is sentinel


# ─────────────────────────────────────────────────────────────────────────────
# log_messages_api
# ─────────────────────────────────────────────────────────────────────────────


class TestLogMessagesApi:
    """The single-line ``[/Messages]`` log carries the OBSERVED alist_count."""

    def test_log_messages_api_emits_observed_count(self, caplog):
        """``alist_count=<observed>`` appears — the value is NOT hardcoded."""
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_messages_api(
                instance_id="instance-aabbccdd",
                duration_ms=12,
                message_count=3,
                bytes_estimate=456,
                alist_count=7,
            )
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1
        msg = matching[0].message
        assert "instance=instance" in msg  # 8-char prefix
        assert "duration_ms=12" in msg
        assert "messages=3" in msg
        assert "bytes=456" in msg
        # The OBSERVED value — this is the whole point of the
        # "alist_count=<observed> not hardcoded" requirement.
        assert "alist_count=7" in msg

    def test_log_messages_api_observed_count_zero_is_distinct_from_hardcoded(self, caplog):
        """``alist_count=0`` here is an OBSERVED zero (after C1), not hardcoded.

        The test asserts the log line is emitted with the passed value.
        It does NOT assert a constant — the caller (get_instance_messages)
        computes the count from the actual walk. A future regression that
        hardcodes ``0`` would be caught by the round-trip test against
        a real saver (see integration fixture capture test).
        """
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_messages_api("inst-1", 1, 0, 0, 0)
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1
        assert "alist_count=0" in matching[0].message

    def test_log_messages_api_suppressed_by_env(self, caplog, monkeypatch):
        """``CHECKPOINT_PERF_LOGS=0`` also suppresses the [/Messages] line."""
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "no")
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_messages_api("inst-1", 1, 1, 1, 1)
        assert not any("[/Messages]" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# invariant_check_no_alist
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariantCheckNoAlist:
    """The post-C1 ERROR log site (dead in PR1, consumed post-C1)."""

    def test_invariant_check_no_alist_emits_error_on_call(self, caplog):
        """Calling the function emits an ERROR-level log."""
        with caplog.at_level(logging.ERROR, logger="daemon.checkpoint_perf"):
            invariant_check_no_alist()
        matching = [r for r in caplog.records if "INVARIANT VIOLATION" in r.message]
        assert len(matching) == 1
        assert matching[0].levelno == logging.ERROR

    def test_invariant_check_no_alist_is_not_suppressed_by_env(self, caplog, monkeypatch):
        """Invariant violation is NEVER silenced — even with CHECKPOINT_PERF_LOGS=0."""
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        with caplog.at_level(logging.ERROR, logger="daemon.checkpoint_perf"):
            invariant_check_no_alist()
        matching = [r for r in caplog.records if "INVARIANT VIOLATION" in r.message]
        assert len(matching) == 1


# ─────────────────────────────────────────────────────────────────────────────
# get_instance_messages — observed alist_count is logged
# ─────────────────────────────────────────────────────────────────────────────


class _AlistAsyncIterator:
    """Async iterator yielding N synthetic checkpoint tuples for mock alist.

    Each yield is a MagicMock standing in for a ``CheckpointTuple`` — the
    production walk only reads ``.checkpoint`` (a dict with ``ts`` +
    ``channel_values.messages``). We construct real dicts for those so
    the iteration body runs unmodified.
    """

    def __init__(self, count: int, ts_base: str = "2026-08-25T00:00:00+00:00"):
        self.count = count
        self.index = 0
        self.ts_base = ts_base

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= self.count:
            raise StopAsyncIteration
        ct = MagicMock(name=f"CheckpointTuple-{self.index}")
        # ``cast(CheckpointTuple, checkpoint_tuple)`` accepts any object;
        # the body accesses ``.checkpoint`` (dict) directly.
        ct.checkpoint = {
            "ts": f"{self.ts_base}-{self.index:02d}",
            "channel_values": {"messages": []},
        }
        self.index += 1
        return ct


@pytest.mark.asyncio
class TestGetInstanceMessagesObservedAlistCount:
    """The observed ``alist_count`` from a real saver walk lands in the log."""

    async def test_get_instance_messages_logs_observed_alist_count(self, caplog):
        """Mock saver alist yields N tuples → log line shows alist_count=N."""
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-u1")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        # N=3 checkpoint tuples — must be the OBSERVED value the log reports.
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(3))

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            msgs = await get_instance_messages(mock_checkpointer, "test-obs-3")

        # Sanity: the messages path worked.
        assert len(msgs) == 1

        # The [/Messages] line carries alist_count=3 (the OBSERVED value).
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert "alist_count=3" in matching[0].message

    async def test_get_instance_messages_zero_messages_emits_log(self, caplog):
        """Empty channel still emits a single [/Messages] line for observability."""
        from daemon.persistence import get_instance_messages

        mock_checkpointer = MagicMock(name="Checkpointer")
        # state has no messages → early return path emits once with zeros.
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []},
            "ts": "2026-08-25T00:00:00+00:00",
        })

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            msgs = await get_instance_messages(mock_checkpointer, "test-empty")

        assert msgs == []
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1
        assert "messages=0" in matching[0].message
        assert "alist_count=0" in matching[0].message

    async def test_get_instance_messages_alist_op_emits_on_walk_exception(self, caplog):
        """W2 symmetry: a raising alist walk still emits the ``op=alist``
        timing line (try/finally around the walk), and the exception
        propagates unchanged to the caller.
        """
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        class _RaisingAlist(_AlistAsyncIterator):
            """Yields one tuple, then raises — simulates a saver failure
            mid-walk (before StopAsyncIteration)."""

            async def __anext__(self):
                if self.index == 1:
                    raise RuntimeError("alist walk boom")
                return await super().__anext__()

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-u1")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_RaisingAlist(3))

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            # The walk exception must NOT be swallowed by the finally block.
            with pytest.raises(RuntimeError, match="alist walk boom"):
                await get_instance_messages(mock_checkpointer, "test-alist-raise")

        # Despite the exception the op=alist timing line was emitted.
        alist_lines = [r for r in caplog.records if "op=alist" in r.message]
        assert len(alist_lines) == 1, [r.message for r in caplog.records]
        assert "duration_ms=" in alist_lines[0].message


# ─────────────────────────────────────────────────────────────────────────────
# _prune_per_thread_checkpoints — duration_ms + deleted in exit log
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMaintenancePruneLogging:
    """PR1 (C4) wrap on ``_prune_per_thread_checkpoints`` emits duration_ms."""

    async def test_maintenance_prune_logged_with_duration(self, caplog):
        """Exit log carries duration_ms + deleted count."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        checkpointer = MagicMock()
        instance_repo = MagicMock()
        checkpointer.find_excess_checkpoint_groups = AsyncMock(
            return_value=[("thread-aaaa", "", 100)]
        )

        job = CheckpointCleanupJob(PersistenceConfig(), checkpointer, instance_repo)

        # Stub the inner _prune_thread_checkpoints to return a known deletion
        # count without spinning up a real adapter.
        async def fake_prune(thread_id, checkpoint_ns, max_per_thread):
            return 50

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            with patch.object(job, "_prune_thread_checkpoints", side_effect=fake_prune):
                await job._prune_per_thread_checkpoints()

        # Two new lines expected: prune-entry + prune-exit.
        exit_lines = [r for r in caplog.records if "op=prune-exit" in r.message]
        assert len(exit_lines) == 1, [r.message for r in caplog.records]
        msg = exit_lines[0].message
        assert "threads=1" in msg  # one (thread, ns) pair was returned
        assert "deleted=50" in msg  # the fake prune returned 50 deletions
        assert "duration_ms=" in msg

    async def test_maintenance_prune_no_excess_threads(self, caplog):
        """No excess threads → exit log emits threads=0 deleted=0."""
        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        checkpointer = MagicMock()
        instance_repo = MagicMock()
        checkpointer.find_excess_checkpoint_groups = AsyncMock(return_value=[])

        job = CheckpointCleanupJob(PersistenceConfig(), checkpointer, instance_repo)

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            await job._prune_per_thread_checkpoints()

        exit_lines = [r for r in caplog.records if "op=prune-exit" in r.message]
        assert len(exit_lines) == 1, [r.message for r in caplog.records]
        msg = exit_lines[0].message
        assert "threads=0" in msg
        assert "deleted=0" in msg

    async def test_maintenance_prune_suppressed_by_env(self, caplog, monkeypatch):
        """W4: ``CHECKPOINT_PERF_LOGS=0`` suppresses ALL prune lines.

        The prune entry/exit lines previously wrote to the
        ``daemon.checkpoint_perf`` logger directly, bypassing the env
        gate. They now route through ``checkpoint_perf.log_prune``.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")

        checkpointer = MagicMock()
        instance_repo = MagicMock()
        checkpointer.find_excess_checkpoint_groups = AsyncMock(
            return_value=[("thread-aaaa", "", 100)]
        )

        job = CheckpointCleanupJob(PersistenceConfig(), checkpointer, instance_repo)

        async def fake_prune(thread_id, checkpoint_ns, max_per_thread):
            return 50

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            with patch.object(job, "_prune_thread_checkpoints", side_effect=fake_prune):
                await job._prune_per_thread_checkpoints()

        assert not any(
            "[CheckpointPerf]" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]


# ─────────────────────────────────────────────────────────────────────────────
# Fixture structural contract (on-disk artifact)
# ─────────────────────────────────────────────────────────────────────────────


class TestFixtureCaptureRoundTrip:
    """Structural validation of the committed fixture artifact (S2).

    The plan requires (phase1-plan.md:190):
      ``test_response_fixture_capture_round_trip`` — the fixture file is
      generated by ``test_messages_response_fixture_capture`` and matches
      a fresh in-process conversation run on the pre-C1 code path.

    The FRESH-RUN equality half of that round-trip lives in the
    integration test itself (which owns the harness and now asserts
    on-disk == fresh capture, all 4 variants — W1/W11). This unit-level
    companion only validates the on-disk artifact is structurally sound:
    a list of variant entries, each carrying a list of message dicts —
    so a future PR3 fixture-driven response-shape test can rely on it
    without running the slow LangGraph graph again.

    Structural equality is intentionally chosen over byte-equality
    (per the plan's risk table at line 200): LLM-driven fixture capture
    has non-deterministic free-text content; what matters is the SHAPE.

    Since the W1/W11 rework the fixture schema is v2:
    ``{"_meta": {…provenance + package versions…}, "variants": […]}``.
    """

    def _fixture_path(self):
        """Path to the captured fixture file (under this test's dir)."""
        from pathlib import Path

        return (
            Path(__file__).resolve().parent
            / "fixtures"
            / "get_instance_messages_pre_phase1.json"
        )

    def test_fixture_file_exists_and_is_valid_json(self):
        """The fixture file exists (FAILS when missing — S1) and parses.

        The fixture is a committed contract artifact, not a generated
        byproduct: its absence is a repo defect, not a skip condition.
        """
        import json

        path = self._fixture_path()
        assert path.exists(), (
            f"fixture missing at {path} — it is a committed contract "
            "artifact. Produce it deliberately with: REGENERATE_FIXTURE=1 "
            "uv run pytest tests/integration/test_messages_response_fixture_capture.py"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), (
            "fixture must be the v2 schema: {'_meta': …, 'variants': […]} "
            "(the message list per variant)"
        )
        assert len(data["variants"]) == 4, "fixture must carry exactly 4 variants"
        # S3 — provenance header records the library versions the fixture
        # was captured under.
        packages = data["_meta"]["packages"]
        for key in ("langgraph", "langgraph-checkpoint", "langchain-core"):
            assert key in packages, f"_meta.packages missing {key}: {packages}"

    def test_fixture_capture_round_trip_structural(self):
        """Structural soundness of the committed fixture (S2 naming).

        The fresh-run equality half of the round-trip lives in the
        integration producer test; see the class docstring.
        """
        import json

        path = self._fixture_path()
        assert path.exists(), (
            "fixture missing — it is a committed contract artifact; produce "
            "it with REGENERATE_FIXTURE=1 uv run pytest "
            "tests/integration/test_messages_response_fixture_capture.py"
        )
        # The actual round-trip assertion lives in the integration test
        # itself (which owns the harness). This unit-level assertion just
        # verifies the fixture is structurally valid: a list of variant
        # entries, each carrying a list of message dicts.
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and isinstance(data["variants"], list)
        for entry in data["variants"]:
            assert isinstance(entry, dict)
            assert "variant_id" in entry
            assert isinstance(entry.get("messages"), list)
            assert "observed_alist_count" in entry
            for msg in entry["messages"]:
                assert isinstance(msg, dict)
                # Every captured message carries a message_id (frontend
                # #anchor contract) — either the injected stable id or the
                # normalized "<generated-uuid>" sentinel.
                assert "message_id" in msg
                assert "role" in msg
                assert "content" in msg
                assert "instance_id" in msg
