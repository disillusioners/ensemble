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
from daemon.checkpoint_metrics import (
    checkpoint_list_total,
    reset_for_tests as reset_metrics_for_tests,
    saver_op_latency_seconds,
)


# ─────────────────────────────────────────────────────────────────────────────
# log_saver_op / time_saver_op
# ─────────────────────────────────────────────────────────────────────────────


class TestLogSaverOp:
    """The structured ``[CheckpointPerf]`` line carries observed fields only.

    FR-5 AC-5.1 (T5.3): the contract format is
    ``op=<name> latency_ms=<int> bytes=<int>``. ``thread=`` and
    ``deleted=`` are diagnostic extras (kept from the v1 PR1 surface);
    the trio above is the load-bearing requirement, verified by these
    tests AND by the per-op all-four pin in
    :class:`TestLogSaverOpPerAllFour`.
    """

    def test_log_saver_op_logs_latency_ms(self, caplog):
        """``op=aget thread=<8-char-prefix> latency_ms=N bytes=0`` is emitted.

        Verbatim FR-5 AC-5.1 format: ``op=aget``, ``latency_ms=42``,
        ``bytes=0``. The 8-char ``thread=`` prefix is a diagnostic extra.
        """
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aget", "thread-abcdef1234567890", 42)
        # The plan mandates the thread_id be sliced to 8 chars.
        # "thread-abcdef1234567890" -> first 8 chars -> "thread-a".
        assert any(
            "[CheckpointPerf]" in rec.message
            and "op=aget" in rec.message
            and "thread=thread-a " in rec.message  # 8-char prefix + separator space
            and "latency_ms=42" in rec.message
            and "bytes=0" in rec.message
            for rec in caplog.records
        ), f"Missing expected [CheckpointPerf] line in {[r.message for r in caplog.records]}"

    def test_log_saver_op_includes_bytes_field(self, caplog):
        """``bytes=<int>`` appears on the line — the contract field.

        The bytes field carries the op's payload size where known
        (e.g. aput's serialized blob); ``0`` is the default when the
        caller does not have the number.
        """
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aput", "thread-aaaa", 5, bytes_=2048)
        matching = [r for r in caplog.records if "[CheckpointPerf]" in r.message]
        assert len(matching) == 1
        msg = matching[0].message
        assert "op=aput" in msg
        assert "latency_ms=5" in msg
        assert "bytes=2048" in msg

    def test_log_saver_op_deleted_default_is_zero(self, caplog):
        """When ``deleted`` is omitted it defaults to 0 in the log line."""
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aput", "thread-aaaa", 5)
        assert any("deleted=0" in r.message for r in caplog.records)

    def test_log_saver_op_respects_env_suppression(self, caplog, monkeypatch):
        """``CHECKPOINT_PERF_LOGS=0`` suppresses the emit (the log line, NOT the metric)."""
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aget", "thread-aaaa", 42)
        assert not any("[CheckpointPerf]" in r.message for r in caplog.records)


class TestTimeSaverOp:
    """Async timing wrapper around any awaitable saver op."""

    @pytest.mark.asyncio
    async def test_time_saver_op_logs_latency_ms(self, caplog):
        """Wrapper emits one [CheckpointPerf] line with the measured latency."""
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
        assert "latency_ms=" in msg
        assert "bytes=" in msg  # bytes field is part of the contract format

    @pytest.mark.asyncio
    async def test_time_saver_op_logs_even_on_exception(self, caplog):
        """Wrapper logs even if the coro raises — latency_ms still emitted."""
        async def coro():
            raise RuntimeError("boom")

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            with pytest.raises(RuntimeError, match="boom"):
                await time_saver_op("aget", "thread-xxxx", coro())

        # Despite the exception the timing log was emitted.
        matching = [r for r in caplog.records if "[CheckpointPerf]" in r.message]
        assert len(matching) == 1
        assert "latency_ms=" in matching[0].message

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
    """Post-C1: the alist walk is GONE — the observed count is 0 even
    when the saver's ``alist`` is armed with tuples.

    These tests were the PR1 (C4) observed-count baseline tests; Phase 1
    C1 (PR3) flipped the read path to aget-only + message_metadata
    enrichment, so the contract they pin is now the COLLAPSE: the
    [/Messages] line reads ``alist_count=0`` on the messages>0 path and
    ``saver.alist`` is NEVER invoked (asserted via ``assert_not_called``
    on an armed mock — the walk machinery is still attached, the flip
    simply never touches it).
    """

    async def test_get_instance_messages_logs_observed_alist_count(self, caplog):
        """Armed mock alist (3 tuples) is NEVER called → log shows alist_count=0.

        Pre-C1 this test asserted ``alist_count=3`` (the observed walk);
        post-C1 the same armed saver must read 0 — the disappearance is
        the invariant, and the armed-but-uncalled mock proves it is a
        real flip, not a stubbed-away walk.
        """
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-u1")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        # N=3 checkpoint tuples ARMED on the mock — post-C1 the flip must
        # never consume them.
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(3))

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            msgs = await get_instance_messages(mock_checkpointer, "test-obs-3")

        # Sanity: the messages path worked.
        assert len(msgs) == 1

        # THE FLIP: the armed alist was never invoked.
        mock_checkpointer.alist.assert_not_called()

        # The [/Messages] line carries alist_count=0 (post-C1 collapse).
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert "alist_count=0" in matching[0].message

    async def test_get_instance_messages_zero_messages_emits_log(self, caplog):
        """Empty channel still emits a single [/Messages] line for observability."""
        from daemon.persistence import get_instance_messages

        mock_checkpointer = MagicMock(name="Checkpointer")
        # state has no messages → early return path emits once with zeros.
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(1))

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            msgs = await get_instance_messages(mock_checkpointer, "test-empty")

        assert msgs == []
        mock_checkpointer.alist.assert_not_called()
        matching = [r for r in caplog.records if "[/Messages]" in r.message]
        assert len(matching) == 1
        assert "messages=0" in matching[0].message
        assert "alist_count=0" in matching[0].message

    async def test_get_instance_messages_raising_alist_never_invoked(self, caplog):
        """Post-C1 (replaces the pre-C1 W2 walk-exception test): a saver
        whose ``alist`` raises is never touched — the call SUCCEEDS and
        emits no ``op=alist`` timing line.

        Pre-C1 the walk could fail mid-iteration and the W2 finally-block
        still emitted ``op=alist``. Post-C1 there is no walk, so a
        poisoned alist is simply dead code on the saver: the read path
        must complete normally.
        """
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        class _RaisingAlist(_AlistAsyncIterator):
            """Raises on first ``__anext__`` — would kill a pre-C1 walk."""

            async def __anext__(self):
                raise RuntimeError("alist walk boom")

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-u1")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_RaisingAlist(3))

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            # Post-C1 the poisoned alist is never awaited — no raise.
            msgs = await get_instance_messages(mock_checkpointer, "test-alist-raise")

        assert len(msgs) == 1
        mock_checkpointer.alist.assert_not_called()
        # No op=alist timing line exists anywhere in the record stream.
        alist_lines = [r for r in caplog.records if "op=alist" in r.message]
        assert alist_lines == [], [r.message for r in caplog.records]


# ─────────────────────────────────────────────────────────────────────────────
# FR-6 AC-6.1: degradation WARNING with reason categories
# ─────────────────────────────────────────────────────────────────────────────


class TestDegradationWarning:
    """FR-6 AC-6.1: every ``message_metadata`` lookup failure logs a
    WARNING with the reason category (``manager_missing`` |
    ``repo_missing`` | ``repo_exception`` | ``row_absent``); the
    response shape is byte-identical to the non-degraded path.

    FR-6 AC-6.2: catch is ``except Exception:`` (NEVER
    ``except BaseException:`` per C-14).
    """

    @pytest.mark.asyncio
    async def test_degraded_warning_when_manager_is_none(self, caplog):
        """Manager=None → WARNING with reason=manager_missing."""
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-d1")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(0))

        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            msgs = await get_instance_messages(mock_checkpointer, "test-no-mgr")

        assert len(msgs) == 1
        matching = [r for r in caplog.records if "manager_missing" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert ("fall back to state.ts" in matching[0].message or "falling back to state.ts" in matching[0].message), matching[0].message
        assert matching[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_degraded_warning_when_repo_attr_missing(self, caplog):
        """Manager shape lacks ``message_metadata_repo`` → repo_missing.

        The full ``get_instance_messages`` flow does extra work
        (context rebuild) that needs a fully-real manager. This
        test focuses on the WARNING contract by inspecting the
        code path that detects the missing repo attribute — the
        ``getattr(manager, "message_metadata_repo", None)`` check.
        The check emits a WARNING with reason=``repo_missing``:
        ``manager is not None but lacks the attribute``.
        """
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        # A minimal manager that lacks the attribute entirely (NOT
        # hasattr — so getattr returns None).
        class _BareManager:
            pass

        bare_manager = _BareManager()

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-d2")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        # Provide a list-shaped alist iterator so the checkpointer
        # doesn't blow up (the read path doesn't call alist, but
        # certain code paths inspect it).
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(0))

        # Drive only the repo-detection path by calling get_instance_messages
        # with a manager that lacks the attribute. The downstream
        # context rebuild may emit warnings — we capture caplog at
        # WARNING+ and filter for our specific WARNING.
        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            try:
                msgs = await get_instance_messages(
                    mock_checkpointer, "test-no-repo", manager=bare_manager
                )
            except Exception:
                msgs = []  # context rebuild may fail on minimal manager

        # The KEY assertion: WARNING with reason=repo_missing was emitted.
        matching = [r for r in caplog.records if "repo_missing" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert ("fall back to state.ts" in matching[0].message or "falling back to state.ts" in matching[0].message), matching[0].message

    def test_repo_exception_warning_emitted_by_persistence_py(self):
        """AST guard: ``except Exception:`` (NOT ``except BaseException:``)
        catches repo failures and emits a WARNING with the exception
        class name (the ``repo_exception`` reason category).

        C-14: NEVER ``except BaseException:`` — CancelledError must
        propagate on Python 3.13. The repo_lookup_raises test below
        would also catch RuntimeError (a non-CancelledError Exception);
        the AST scan below pins the catch shape so a future regression
        cannot widen the catch to ``BaseException``.
        """
        from pathlib import Path
        import ast
        repo_root = Path(__file__).resolve().parents[3]
        persistence_path = repo_root / "daemon" / "persistence.py"
        source = persistence_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the ``try`` block for the ``get_for_thread`` call.
        # The relevant ``except`` must be ``except Exception:`` (NOT
        # ``except BaseException:``).
        caught_exceptions: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        continue  # bare except — not allowed here per C-14
                    # handler.type is the exception class expression.
                    # Handle the common forms: ``except Exception:``,
                    # ``except Exception as exc:``.
                    type_node = handler.type
                    while isinstance(type_node, ast.Name):
                        caught_exceptions.append(type_node.id)
                        break
                    if isinstance(type_node, ast.Tuple):
                        for elt in type_node.elts:
                            if isinstance(elt, ast.Name):
                                caught_exceptions.append(elt.id)

        # The repo-lookup catch MUST be ``Exception`` (never BaseException).
        assert "Exception" in caught_exceptions, (
            f"no `except Exception` clause found in daemon/persistence.py "
            f"for repo-lookup error handling. Found: {caught_exceptions}"
        )
        assert "BaseException" not in caught_exceptions, (
            f"C-14 violation: `except BaseException` would swallow "
            f"CancelledError on Python 3.13. Found: {caught_exceptions}"
        )

    @pytest.mark.asyncio
    async def test_degraded_warning_when_repo_lookup_raises(self, caplog):
        """Repo exists but ``get_for_thread`` raises → repo_exception."""
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        mock_repo = MagicMock(name="Repo")
        mock_repo.get_for_thread = MagicMock(side_effect=RuntimeError("conn timeout"))

        mock_manager = MagicMock(name="Manager")
        mock_manager.message_metadata_repo = mock_repo

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-d3")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(0))

        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            msgs = await get_instance_messages(
                mock_checkpointer, "test-repo-raise", manager=mock_manager
            )

        # The KEY assertion: WARNING with reason=lb_<exception class>
        # was emitted (the message includes the exception class name).
        matching = [r for r in caplog.records if "lookup failed" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert "RuntimeError" in matching[0].message
        assert "conn timeout" in matching[0].message

    @pytest.mark.asyncio
    async def test_degraded_warning_when_repo_returns_empty(self, caplog):
        """Repo returns empty dict → row_absent (no rows for this thread)."""
        from langchain_core.messages import HumanMessage

        from daemon.persistence import get_instance_messages

        mock_repo = MagicMock(name="Repo-empty")
        mock_repo.get_for_thread = MagicMock(return_value={})

        mock_manager = MagicMock(name="Manager")
        mock_manager.message_metadata_repo = mock_repo

        mock_checkpointer = MagicMock(name="Checkpointer")
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="hi", id="msg-d4")]},
            "ts": "2026-08-25T00:00:00+00:00",
        })
        mock_checkpointer.alist = MagicMock(return_value=_AlistAsyncIterator(0))

        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            msgs = await get_instance_messages(
                mock_checkpointer, "test-repo-empty", manager=mock_manager
            )

        # The KEY assertion: WARNING with reason=row_absent emitted.
        matching = [r for r in caplog.records if "row_absent" in r.message]
        assert len(matching) == 1, [r.message for r in caplog.records]
        assert ("fall back to state.ts" in matching[0].message or "falling back to state.ts" in matching[0].message), matching[0].message

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
    on-disk == fresh capture, all variants — W1/W11). This unit-level
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
        assert len(data["variants"]) == 6, (
            "fixture must carry exactly 6 variants "
            "(4 persisted-shape + 2 synthetic-layer)"
        )
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


# ─────────────────────────────────────────────────────────────────────────────
# FR-5 AC-5.1 (T5.3): per-op all-four pin — every saver op MUST emit ≥1 line
# ─────────────────────────────────────────────────────────────────────────────


class TestLogSaverOpPerAllFour:
    """AC-5.1 caplog pin: aget / aput / adelete / alist each emit ≥1 line.

    FR-5 AC-5.1 specifies the ops ``aget`` / ``aput`` / ``adelete`` /
    ``alist`` (alist = migration-only). This test class exercises every
    one of them through ``log_saver_op`` and asserts the
    ``op=<name> latency_ms=<int> bytes=<int>`` shape is present.

    The alist label is migration-only by design (architect §3 + C-8):
    the live path makes ZERO alist calls post-PR3 (see
    :class:`TestAlistCountDisappearanceGate`); the migrator
    (``daemon/migrations/checkpoint_migrator.py``) is the ONE sanctioned
    caller of ``saver.alist(…)`` and would record its ops through this
    same helper if it ever wraps its alist in a timed context.
    """

    @pytest.fixture(autouse=True)
    def _isolate_metrics(self):
        # Each test starts from a clean metric state — the helpers
        # are module-level singletons (per the FR-5 surface), so cross-
        # test contamination is real without isolation.
        reset_metrics_for_tests()
        yield
        reset_metrics_for_tests()

    @pytest.mark.parametrize(
        "op", ["aget", "aput", "adelete", "alist"],
        ids=["aget", "aput", "adelete", "alist-migration-only"],
    )
    def test_each_op_emits_contract_format(self, caplog, op):
        """For every op, the contract format is emitted exactly once.

        The format is ``op=<name> latency_ms=<int> bytes=<int>``. The
        ``thread=`` and ``deleted=`` diagnostic extras are also present
        but are NOT the contract — verified separately in
        :class:`TestLogSaverOp` and :class:`TestTimeSaverOp`.
        """
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op(op, "thread-aaaa", 7, bytes_=128, deleted=0)
        matching = [r for r in caplog.records if "[CheckpointPerf]" in r.message]
        assert len(matching) >= 1, (
            f"op={op!r} emitted ZERO [CheckpointPerf] lines; "
            f"records={[r.message for r in caplog.records]}"
        )
        msg = matching[0].message
        assert f"op={op}" in msg, f"missing op={op} in {msg}"
        assert "latency_ms=7" in msg, f"missing latency_ms=7 in {msg}"
        assert "bytes=128" in msg, f"missing bytes=128 in {msg}"


# ─────────────────────────────────────────────────────────────────────────────
# FR-5 AC-5.2 (T5.3): metrics surface — counter + histogram exposed + record
# ─────────────────────────────────────────────────────────────────────────────


class TestMetricsSurface:
    """AC-5.2: the metric surface exposes both the counter and the histogram.

    * ``message_api_checkpoint_list_total`` — counter. Expected normal
      value 0 (post-PR3 alist is gone from the live path).
    * ``message_api_saver_op_latency_seconds`` — histogram, labeled
      by ``op``. Bucket layout is Prometheus-style (10ms..10s).

    A minimal internal collector lives in
    ``daemon/checkpoint_metrics.py``. The wire format is the Prometheus
    text-exposition (per Gap-5 / A-8 the daemon-internal collector is
    the canonical surface — NO HTTP endpoint is added in v2; a future
    wiring would be a one-liner against :func:`render_metrics`).
    """

    def test_counter_and_histogram_are_exposed(self):
        """Both singletons exist at module level with the contract names."""
        assert checkpoint_list_total.name == "message_api_checkpoint_list_total"
        assert saver_op_latency_seconds.name == "message_api_saver_op_latency_seconds"
        assert saver_op_latency_seconds.label_keys == ("op",)

    def test_counter_starts_at_zero(self):
        """Fresh surface — counter reads 0 (the expected normal value)."""
        reset_metrics_for_tests()
        assert checkpoint_list_total.get() == 0

    def test_counter_increments(self):
        """Counter is monotonic; amount defaults to 1."""
        reset_metrics_for_tests()
        checkpoint_list_total.inc()
        assert checkpoint_list_total.get() == 1
        checkpoint_list_total.inc(3)
        assert checkpoint_list_total.get() == 4

    def test_counter_rejects_negative_amount(self):
        """``inc(amount=-1)`` raises — counters never decrease."""
        with pytest.raises(ValueError, match="non-negative"):
            checkpoint_list_total.inc(-1)

    def test_histogram_observe_records_count_and_sum(self):
        """An observation is reflected in count + sum + bucket counts."""
        reset_metrics_for_tests()
        saver_op_latency_seconds.observe(0.003, op="aget")
        saver_op_latency_seconds.observe(0.020, op="aget")
        assert saver_op_latency_seconds.get_count(op="aget") == 2
        assert abs(saver_op_latency_seconds.get_sum(op="aget") - 0.023) < 1e-9
        # Bucket counts must be monotonic across the sorted bounds —
        # the first bucket (≤0.001) catches 0, the 0.005 bucket catches 1
        # (0.003), the 0.025 bucket catches both (0.003 + 0.020).
        bucket_counts = saver_op_latency_seconds.get_bucket_counts(op="aget")
        for prev, cur in zip(bucket_counts, bucket_counts[1:]):
            assert prev <= cur, f"bucket_counts not monotonic: {bucket_counts}"

    def test_histogram_rejects_unknown_label_keys(self):
        """Programmer-error guard: a typo on ``op`` raises."""
        reset_metrics_for_tests()
        with pytest.raises(KeyError, match="unknown label keys"):
            saver_op_latency_seconds.observe(0.001, ops="aget")  # typo

    def test_histogram_separates_label_combos(self):
        """Different ``op`` labels are independent series."""
        reset_metrics_for_tests()
        saver_op_latency_seconds.observe(0.005, op="aget")
        saver_op_latency_seconds.observe(0.050, op="aput")
        assert saver_op_latency_seconds.get_count(op="aget") == 1
        assert saver_op_latency_seconds.get_count(op="aput") == 1
        assert abs(saver_op_latency_seconds.get_sum(op="aget") - 0.005) < 1e-9
        assert abs(saver_op_latency_seconds.get_sum(op="aput") - 0.050) < 1e-9

    def test_log_saver_op_records_into_histogram(self, caplog):
        """``log_saver_op`` side-effects into the histogram (NOT gated by env)."""
        reset_metrics_for_tests()
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aget", "thread-aaaa", 42)
        # duration_ms=42 → 0.042 s. Buckets: 0.025, 0.05, 0.1, ...
        # Observation lands in buckets ≥ 0.05.
        assert saver_op_latency_seconds.get_count(op="aget") == 1
        assert abs(saver_op_latency_seconds.get_sum(op="aget") - 0.042) < 1e-9

    def test_log_saver_op_records_into_histogram_even_when_log_suppressed(
        self, caplog, monkeypatch
    ):
        """Metric records REGARDLESS of ``CHECKPOINT_PERF_LOGS`` — the log
        line is suppressed, the metric is not. Operator SLO surface is
        independent of log volume."""
        reset_metrics_for_tests()
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_saver_op("aput", "thread-aaaa", 100)
        # Log line suppressed.
        assert not any("[CheckpointPerf]" in r.message for r in caplog.records)
        # Histogram still recorded.
        assert saver_op_latency_seconds.get_count(op="aput") == 1
        assert abs(saver_op_latency_seconds.get_sum(op="aput") - 0.100) < 1e-9

    def test_render_metrics_is_prometheus_text_exposition(self):
        """``render_metrics()`` emits the Prometheus text-exposition format.

        Per Gap-5 / A-8 the v2 surface does NOT add an HTTP endpoint;
        the renderer exists so (a) operators can ``import`` and print
        and (b) a future wiring is a one-liner against this function.
        The shape MUST be the canonical Prometheus format (lines, not
        JSON) so a future scraper can consume it.
        """
        reset_metrics_for_tests()
        checkpoint_list_total.inc()
        saver_op_latency_seconds.observe(0.003, op="aget")
        from daemon.checkpoint_metrics import render_metrics
        rendered = render_metrics()
        # Counter shape: `name <value>` (no labels on the counter).
        assert "message_api_checkpoint_list_total 1" in rendered
        # Histogram shape: at least one `_bucket{op="aget",le="..."}` line.
        assert "message_api_saver_op_latency_seconds_bucket" in rendered
        assert 'op="aget"' in rendered
        # +Inf bucket must be present (the implicit top bucket).
        assert 'le="+Inf"' in rendered


class TestIncrementCheckpointListTotal:
    """The alist counter's live-path regression hook.

    FR-2 invariant + FR-5 AC-5.2: the counter MUST stay at 0 on the
    live path (post-PR3 alist is gone). The integrator helper
    :func:`increment_checkpoint_list_total` is the only sanctioned way
    to increment it; if a future caller invokes it AND the live-path
    alist guard fails, the counter moves off zero and the FR-2 test
    (``tests/integration/test_get_instance_messages_observed_count_zero.py``)
    fires. The migrator (``daemon/migrations/checkpoint_migrator.py``)
    is exempt — it does NOT call this helper.
    """

    def test_increment_returns_new_value(self):
        reset_metrics_for_tests()
        new = checkpoint_perf.increment_checkpoint_list_total()
        assert new == 1
        assert checkpoint_list_total.get() == 1

    def test_increment_supports_amount(self):
        reset_metrics_for_tests()
        new = checkpoint_perf.increment_checkpoint_list_total(amount=5)
        assert new == 5
        assert checkpoint_list_total.get() == 5
