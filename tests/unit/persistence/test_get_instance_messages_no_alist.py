"""PR3 (C1) — zero-``alist`` proof + message_metadata enrichment tests.

Phase 1 C1 of the langgraph-checkpoint-perf plan (phase1-plan.md
§C1, lines 508-610). The read flip: ``get_instance_messages`` reads
timestamps from the C2 ``message_metadata`` side table (one indexed
``get_for_thread`` lookup bridged via ``asyncio.to_thread``) instead
of enumerating checkpoint history with ``alist(config, limit=1000)``.

This suite pins the plan's PR3 test table:

* ``test_zero_alist_calls_with_msgs_repo`` — 4 size variants
  (10/100/1000/10000 messages) with a FILLED repo; the mock saver
  records every method call and ``alist`` is never among them.
* ``test_zero_alist_calls_without_msgs_repo`` — the
  EXPLICIT-DEGRADATION path (``manager=None`` → no repo → every
  timestamp falls to the ``state.ts`` fallback) still makes ZERO
  alist calls.
* Timestamp population — tapped ids get the ``message_metadata``
  ``created_at``; untapped ids get ``state.ts``.
* Over-record tolerance — side-table rows for messages NOT in the
  latest checkpoint never join (the pause-between-tap-and-commit
  property from ``daemon/services/message_tap.py``).
* Repo failure — warned + degraded to ``state.ts``; GET /messages
  never fails because of the enrichment lookup.
* ``alist_count`` disappearance gate — the observed count on every
  messages>0 path is 0 (the pre-C1 baseline of ≥1 collapsed).
* Revive-then-fetch — timestamps stay non-null from the side table
  across a fetch → "revive" → fetch cycle (COMPLETED→RUNNING revive
  reuses the checkpoint; side-table rows are keyed by thread and
  survive by construction).

Marker gating: this file carries NO ``integration`` marker — it must
execute under the default ``addopts`` (``-m 'not integration and not
postgres'``). The collection count is verified in the recorded PR3
gate run.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.persistence import get_instance_messages

STATE_TS = "2026-08-26T12:00:00+00:00"
META_TS = "2026-08-26T01:00:00+00:00"


class _StubMetadataRepo:
    """Deterministic stand-in for ``MessageMetadataRepository``.

    Duck-typed to the surface ``get_instance_messages`` touches:
    ``get_for_thread(thread_id) -> dict[message_id, (created_at, seq)]``.
    Records every call so tests can assert the lookup happened (or
    didn't).
    """

    def __init__(self, rows: dict[str, tuple[str, int | None]] | None = None):
        self.rows = rows or {}
        self.calls: list[str] = []
        self.raise_on_call: Exception | None = None

    def get_for_thread(self, thread_id: str) -> dict[str, tuple[str, int | None]]:
        self.calls.append(thread_id)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return dict(self.rows)


def _make_messages(n: int) -> list[Any]:
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content=f"msg {i}", id=f"m-{i}") for i in range(n)]


def _make_saver(messages: list[Any]) -> MagicMock:
    """Mock saver with a WORKING aget and an ARMED-but-uncalled alist.

    ``alist`` is a MagicMock returning an async iterator that WOULD
    yield tuples — post-C1 the flip must never consume it, and the
    armed mock makes ``assert_not_called`` a real proof (the walk
    machinery is attached; the code path simply never reaches it).
    """
    saver = MagicMock(name="SpySaver")
    saver.aget = AsyncMock(return_value={
        "channel_values": {"messages": messages},
        "ts": STATE_TS,
    })

    class _ArmedAlist:
        def __init__(self, count: int = 5):
            self.count = count
            self.i = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.i >= self.count:
                raise StopAsyncIteration
            self.i += 1
            ct = MagicMock(name="CheckpointTuple")
            ct.checkpoint = {
                "ts": f"2026-08-25T00:00:0{self.i}:00+00:00",
                "channel_values": {"messages": []},
            }
            return ct

    saver.alist = MagicMock(return_value=_ArmedAlist())
    return saver


def _make_manager(repo: _StubMetadataRepo | None) -> SimpleNamespace:
    """Manager stub exposing only ``message_metadata_repo``."""
    return SimpleNamespace(message_metadata_repo=repo)


def _messages_api_line(caplog) -> str | None:
    """Return the (single) [/Messages] log line, if any."""
    lines = [r.message for r in caplog.records if "[/Messages]" in r.message]
    return lines[0] if lines else None


# ─────────────────────────────────────────────────────────────────────────────
# 1. The no-alist proof
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestZeroAlist:
    """ZERO ``alist`` calls on the flipped path — every size, every repo mode."""

    @pytest.mark.parametrize("n", [10, 100, 1000, 10000])
    async def test_zero_alist_calls_with_msgs_repo(self, n, caplog):
        """Filled repo + N persisted messages → aget-only read.

        The mock saver RECORDS every method call (``method_calls``);
        ``alist`` must never appear — not as a call, not as an awaited
        attribute fetch. The repo lookup must have happened exactly
        once (the enrichment is real, not skipped).
        """
        messages = _make_messages(n)
        rows = {f"m-{i}": (META_TS, i) for i in range(n)}
        repo = _StubMetadataRepo(rows)
        saver = _make_saver(messages)

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            out = await get_instance_messages(
                saver, "thr-flip", manager=_make_manager(repo)
            )

        assert len(out) == n
        saver.alist.assert_not_called()
        # Every method the saver saw: exactly one aget, nothing else.
        assert [c[0] for c in saver.method_calls] == ["aget"]
        # The enrichment lookup fired once for this thread.
        assert repo.calls == ["thr-flip"]
        # Converse (PR3 review): repo resolved — the degradation
        # warning must NOT fire on the armed/happy path.
        assert not [
            r.message for r in caplog.records
            if "message_metadata_repo missing/None" in r.message
        ]

    async def test_zero_alist_calls_without_msgs_repo(self):
        """``manager=None`` → EXPLICIT-DEGRADATION (plan §C1): no repo,
        no alist — every timestamp falls to the ``state.ts`` fallback,
        the operator shim that lets C1's alist-kill stand alone."""
        messages = _make_messages(25)
        saver = _make_saver(messages)

        out = await get_instance_messages(saver, "thr-norepo", manager=None)

        assert len(out) == 25
        saver.alist.assert_not_called()
        assert [c[0] for c in saver.method_calls] == ["aget"]
        # Degradation: state.ts for every message.
        assert all(m["created_at"] == STATE_TS for m in out)

    async def test_manager_without_repo_attribute_degrades(self, caplog):
        """A manager that does NOT expose ``message_metadata_repo``
        (pre-PR2 manager shape, or a stripped stub) degrades the same
        way — the getattr-None-guard is the accepted degradation, and
        (PR3 external review) it is WARNED, not silent."""
        messages = _make_messages(3)
        saver = _make_saver(messages)
        manager = SimpleNamespace()  # no message_metadata_repo

        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            out = await get_instance_messages(saver, "thr-attr", manager=manager)

        assert len(out) == 3
        saver.alist.assert_not_called()
        assert all(m["created_at"] == STATE_TS for m in out)
        # Degradation is warned exactly once: cause + fallback named,
        # instance identified ("thr-attr" is 8 chars — stable under
        # the [:8] truncation in the log formatter).
        warns = [
            r.message for r in caplog.records
            if "message_metadata_repo missing/None" in r.message
        ]
        assert len(warns) == 1, [r.message for r in caplog.records]
        assert "state.ts" in warns[0] and "thr-attr" in warns[0]

    async def test_empty_state_returns_empty_and_never_touches_alist(self):
        """No checkpoint at all (``aget → None``) → ``[]`` before any
        repo lookup; alist armed but never called (defensive empty-path
        proof)."""
        saver = MagicMock(name="SpySaver")
        saver.aget = AsyncMock(return_value=None)
        saver.alist = MagicMock()

        out = await get_instance_messages(saver, "thr-none", manager=_make_manager(_StubMetadataRepo()))

        assert out == []
        saver.alist.assert_not_called()

    async def test_empty_messages_channel_never_touches_alist(self):
        """Checkpoint exists but ``channel_values.messages`` is empty →
        early-return ``[]`` with the observability line; alist armed
        but never called."""
        saver = _make_saver([])

        out = await get_instance_messages(saver, "thr-empty-chan")

        assert out == []
        saver.alist.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Timestamp population + fallback chain
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTimestampPopulation:
    """Tapped ids → message_metadata created_at; untapped → state.ts."""

    async def test_tapped_ids_get_metadata_timestamps(self):
        """Half the messages are tapped: their created_at is the
        side-table value, DISTINCT from state.ts (proves the source)."""
        messages = _make_messages(6)
        rows = {f"m-{i}": (META_TS, i) for i in range(3)}
        repo = _StubMetadataRepo(rows)
        saver = _make_saver(messages)

        out = await get_instance_messages(saver, "thr-mixed", manager=_make_manager(repo))

        by_id = {m["message_id"]: m for m in out}
        for i in range(3):  # tapped → metadata ts
            assert by_id[f"m-{i}"]["created_at"] == META_TS
        for i in range(3, 6):  # untapped → state.ts fallback
            assert by_id[f"m-{i}"]["created_at"] == STATE_TS

    async def test_id_less_message_falls_to_state_ts(self):
        """D19: an id-less message has no tap row (serialize generates
        a fresh UUID) → state.ts fallback even with a filled repo."""
        from langchain_core.messages import HumanMessage

        saver = _make_saver([HumanMessage(content="no id here")])
        repo = _StubMetadataRepo({"irrelevant": (META_TS, 0)})

        out = await get_instance_messages(saver, "thr-idless", manager=_make_manager(repo))

        assert len(out) == 1
        assert out[0]["created_at"] == STATE_TS

    async def test_over_record_rows_never_join(self):
        """Over-record property (message_tap.py): side-table rows for
        messages NOT in the latest checkpoint (pause landed between
        the tap and the node's checkpoint commit) must simply never
        join — no phantom messages, no crash."""
        messages = _make_messages(4)
        ghost_rows = {
            "m-0": (META_TS, 0),
            "ghost-1": ("2026-08-26T02:00:00+00:00", 1),
            "ghost-2": ("2026-08-26T03:00:00+00:00", 2),
        }
        repo = _StubMetadataRepo(ghost_rows)
        saver = _make_saver(messages)

        out = await get_instance_messages(saver, "thr-ghost", manager=_make_manager(repo))

        ids = [m["message_id"] for m in out]
        assert len(out) == 4
        assert "ghost-1" not in ids and "ghost-2" not in ids
        by_id = {m["message_id"]: m for m in out}
        assert by_id["m-0"]["created_at"] == META_TS
        assert by_id["m-3"]["created_at"] == STATE_TS

    async def test_repo_failure_degrades_to_state_ts(self, caplog):
        """A raising side-table lookup is warned + swallowed; every
        timestamp falls to state.ts; the response still returns."""
        messages = _make_messages(3)
        repo = _StubMetadataRepo({})
        repo.raise_on_call = RuntimeError("side table down")
        saver = _make_saver(messages)

        with caplog.at_level(logging.WARNING, logger="daemon.persistence"):
            out = await get_instance_messages(saver, "thr-boom", manager=_make_manager(repo))

        assert len(out) == 3
        assert all(m["created_at"] == STATE_TS for m in out)
        assert any(
            "message_metadata lookup failed" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]

    async def test_revive_then_fetch_keeps_timestamps(self):
        """Revive stability (read side): COMPLETED→RUNNING revive reuses
        the checkpoint; the side table is keyed by thread_id, so a
        fetch after revive returns the SAME non-null metadata
        timestamps. Simulated as two sequential fetches against the
        same saver + repo (revive adds no checkpoint, no tap)."""
        messages = _make_messages(4)
        rows = {f"m-{i}": (META_TS, i) for i in range(4)}
        repo = _StubMetadataRepo(rows)
        saver = _make_saver(messages)

        first = await get_instance_messages(saver, "thr-revive", manager=_make_manager(repo))
        # "revive" — nothing changes for the read path; rows persist.
        second = await get_instance_messages(saver, "thr-revive", manager=_make_manager(repo))

        assert first == second
        assert all(m["created_at"] == META_TS for m in second)
        assert repo.calls == ["thr-revive", "thr-revive"]
        saver.alist.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3. alist_count disappearance gate (messages>0 paths only)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestAlistCountDisappearanceGate:
    """The observed alist_count must read 0 on ALL messages>0 paths.

    The empty paths legitimately emit 0-by-absence, which would make a
    naive ``== 0`` gate trivially satisfied — every assertion here is
    on a path with messages>0 in the response (the premise correction
    from the PR3 brief).
    """

    @pytest.mark.parametrize("n,with_repo", [(1, True), (7, True), (50, False)])
    async def test_observed_count_zero_on_messages_gt_zero(self, n, with_repo, caplog):
        messages = _make_messages(n)
        repo = _StubMetadataRepo({f"m-{i}": (META_TS, i) for i in range(n)}) if with_repo else None
        saver = _make_saver(messages)

        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            out = await get_instance_messages(
                saver,
                "thr-gate",
                manager=_make_manager(repo) if with_repo else None,
            )

        line = _messages_api_line(caplog)
        assert line is not None, "expected exactly one [/Messages] line"
        assert len(out) == n and n > 0  # the gate is non-trivial: messages>0
        assert "alist_count=0" in line
        # And the armed saver was in fact never walked.
        saver.alist.assert_not_called()
