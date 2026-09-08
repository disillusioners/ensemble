"""Unit tests for the W2 read-rebuild parent_id normalization fix.

Pin: ``daemon/persistence.py:899`` now normalizes legacy ``""``
parent rows to ``None`` before calling
``assemble_context_messages``. Without the fix, a legacy row with
``parent_id=""`` resolved ``context_key=""`` (an empty string) via
``_resolve_tree_root_id`` — a mispartition symptom analogous to the
messaging-path defect fixed in commit 1.

The messaging path (instance_messaging.py:3683-3692, my commit 1)
and the restore path (instance_lifecycle.py:3982-3998) already
normalize this exact shape; this test mirrors those normalization
contracts on the read-rebuild surface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.persistence import _build_context_dicts_for_response


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_messages_with_user_query(query: str) -> list:
    """Build a minimal checkpoint-messages list with a human turn.

    The read-rebuild helper scans ``reversed(messages)`` for the
    first ``type == 'human'`` message and uses its content as
    ``user_query`` — we need at least one such entry.
    """
    from langchain_core.messages import HumanMessage
    return [HumanMessage(content=query)]


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestReadRebuildParentIdNormalization:
    """Pin the W2 ``or None`` normalization on the read-rebuild path."""

    @pytest.mark.asyncio
    async def test_legacy_empty_parent_id_normalizes_to_none(self):
        """A row with ``parent_id=""`` must thread ``None`` to the orchestrator.

        Pin the W2 fix: legacy rows where ``instances.parent_id`` is
        an empty string (pre-2026-08 schema drift surface) would
        thread ``""`` to ``assemble_context_messages`` →
        ``_resolve_tree_root_id``. The orchestrator would then treat
        ``""`` as a non-None parent and call
        ``get_tree_root_id("")``, which walks an empty ancestor chain
        and resolves to ``""`` — an empty context partition, never
        the instance's own.

        With the fix, ``""`` collapses to ``None`` and the
        orchestrator returns the instance's own id (legacy root
        behavior).
        """
        captured: dict = {}

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])  # no synthetic context messages

        instance_meta = MagicMock()
        instance_meta.parent_id = ""  # legacy: empty string
        instance_meta.project_id = "proj-w2-1"
        instance_meta.instance_metadata = {"project_id": "proj-w2-1"}

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": MagicMock(),
        }

        manager = MagicMock()
        manager._instance_repository = MagicMock()

        messages = _make_messages_with_user_query("any user query")

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(side_effect=_capture),
        ):
            await _build_context_dicts_for_response(
                instance_id="legacy-empty-parent",
                ctx=ctx,
                manager=manager,
                messages=messages,
            )

        assert captured, "orchestrator was never called"
        # Pin: parent_id was normalized to None before the call.
        assert captured["kwargs"].get("parent_id") is None, (
            f"read-rebuild must normalize legacy empty-string parent_id to "
            f"None before calling assemble_context_messages; got "
            f"parent_id={captured['kwargs'].get('parent_id')!r}. "
            f"This is the W2 surface of the mispartition defect "
            f"(daemon/persistence.py:899)."
        )

    @pytest.mark.asyncio
    async def test_normal_parent_id_threads_unchanged(self):
        """Non-empty parent_id strings pass through unchanged.

        Companion pin: the normalization must not regress non-empty
        parent ids. A row with ``parent_id="caller-tree-root"``
        must still thread that exact string to the orchestrator.
        """
        captured: dict = {}

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])

        instance_meta = MagicMock()
        instance_meta.parent_id = "caller-tree-root"
        instance_meta.project_id = "proj-w2-2"
        instance_meta.instance_metadata = {"project_id": "proj-w2-2"}

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": MagicMock(),
        }

        manager = MagicMock()
        manager._instance_repository = MagicMock()

        messages = _make_messages_with_user_query("any user query")

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(side_effect=_capture),
        ):
            await _build_context_dicts_for_response(
                instance_id="normal-parent",
                ctx=ctx,
                manager=manager,
                messages=messages,
            )

        assert captured, "orchestrator was never called"
        assert captured["kwargs"].get("parent_id") == "caller-tree-root", (
            f"non-empty parent_id must thread unchanged; got "
            f"{captured['kwargs'].get('parent_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_none_parent_id_threads_unchanged(self):
        """A row with ``parent_id=None`` threads ``None`` (root unchanged).

        Root instance pin: a row with ``parent_id is None`` (the
        canonical root-instance shape) must still thread ``None``
        through to the orchestrator. The ``or None`` normalization
        is a no-op for the canonical None case — this test verifies
        that the canonical contract is preserved.
        """
        captured: dict = {}

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])

        instance_meta = MagicMock()
        instance_meta.parent_id = None
        instance_meta.project_id = "proj-w2-3"
        instance_meta.instance_metadata = {"project_id": "proj-w2-3"}

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": MagicMock(),
        }

        manager = MagicMock()
        manager._instance_repository = MagicMock()

        messages = _make_messages_with_user_query("any user query")

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(side_effect=_capture),
        ):
            await _build_context_dicts_for_response(
                instance_id="root-instance",
                ctx=ctx,
                manager=manager,
                messages=messages,
            )

        assert captured, "orchestrator was never called"
        assert captured["kwargs"].get("parent_id") is None, (
            f"root instance (parent_id=None) must still pass parent_id=None "
            f"to the orchestrator; got "
            f"{captured['kwargs'].get('parent_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_missing_parent_id_attribute_threads_none(self):
        """Row lacking the ``parent_id`` attribute → ``None`` (graceful default).

        Pre-2026-08 schema surface: rows that pre-date the
        ``parent_id`` column lack the attribute entirely.
        ``getattr(..., None)`` returns the default, and the ``or None``
        is a no-op. The orchestrator must receive ``None``.
        """
        captured: dict = {}

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])

        # Use SimpleNamespace without parent_id to simulate pre-2026-08 row.
        from types import SimpleNamespace
        instance_meta = SimpleNamespace(
            project_id="proj-w2-4",
            instance_metadata={"project_id": "proj-w2-4"},
        )

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": MagicMock(),
        }

        manager = MagicMock()
        manager._instance_repository = MagicMock()

        messages = _make_messages_with_user_query("any user query")

        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(side_effect=_capture),
        ):
            await _build_context_dicts_for_response(
                instance_id="legacy-no-parent-col",
                ctx=ctx,
                manager=manager,
                messages=messages,
            )

        assert captured, "orchestrator was never called"
        assert captured["kwargs"].get("parent_id") is None, (
            f"row missing parent_id attribute must resolve to None; "
            f"got {captured['kwargs'].get('parent_id')!r}"
        )